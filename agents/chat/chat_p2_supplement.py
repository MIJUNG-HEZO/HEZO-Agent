"""P1 → P2 보강: 기존 위키를 Claude로 보강한 풀 markdown 생성 + precheck 게이트.

흐름 (P2 reinforce 채택 룰셋 준수):
  load_existing_wiki(category, domain)            # hezo-wiki 원본 로드
    → build_enriched_wiki_md(...)                 # Claude 위키 보강(문장마다 [Sn], 교과서적 사실, 확장)
    → precheck(md, original_body_len)             # 6룰 + 기존보다 큼
      → 통과 → hezo-wiki-staging/pending/{category}/{domain}_{site_id}.md 저장
      → 실패 → 버림

핵심 채택 조건: 문장마다 인용 + 확실한 사실 + 기존보다 풍부.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from bedrock_claude_adapter import (
    Boto3BedrockClaudeInvoker,
    ClaudeInvocationInput,
    ClaudeMessage,
)

logger = logging.getLogger("hezo.chat.p2_supplement")

# ── 상수 ───────────────────────────────────────────────────────────────────────
_WIKI_BUCKET = os.environ.get("HEZO_P2_MARKDOWNS_BUCKET", "hezo-wiki")
_STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "hezo-wiki-staging")
_AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))

# ⑥ 광고/홍보/CTA 금지 문구 (P2 reinforce precheck 기준)
_BANNED_PHRASES: tuple[str, ...] = (
    "지금 전화", "무료 상담", "상담 신청", "방문 예약",
    "할인 이벤트", "특가 세일", "선착순 마감",
)

_MIN_BODY_LEN = 400
_MAX_BODY_LEN = 8000

_CITE_RE = re.compile(r"\[S(\d+)\]")
_SRC_LINE_RE = re.compile(r"^\[S(\d+)\]\s+.+?[—–\-]\s*https?://\S+", re.M)
_H2_RE = re.compile(r"^##\s+(.+)$", re.M)
_FRONTMATTER_RE = re.compile(r"^---[\s\S]*?---\s*", re.M)


# ── precheck 결과 ──────────────────────────────────────────────────────────────
@dataclass
class PrecheckResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)


# ── 도메인 정규화 ──────────────────────────────────────────────────────────────
def normalize_domain(domain: str) -> str:
    """카탈로그 키(언더스코어)로 정규화. 하이픈이면 rejected_bad_domain 되므로 변환."""
    return str(domain or "").strip().lower().replace("-", "_")


# ── 본문 추출 ──────────────────────────────────────────────────────────────────
def _body(md: str) -> str:
    return _FRONTMATTER_RE.sub("", md, count=1)


def count_know_sections(md: str) -> int:
    """'## 지식 섹션' 개수('## 출처' 제외) — reinforce는 섹션 수로 shrink를 판정."""
    return len([h for h in _H2_RE.findall(_body(md)) if "출처" not in h])


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n", "", t)
        t = re.sub(r"\n```\s*$", "", t)
    return t.strip()


# ── precheck (6룰 + 기존보다 큼) ────────────────────────────────────────────────
def precheck(md: str, original_body_len: int = 0, original_section_count: int = 0) -> PrecheckResult:
    body = _body(md)
    h2 = _H2_RE.findall(body)
    know = [h for h in h2 if "출처" not in h]
    cites = [int(n) for n in _CITE_RE.findall(body)]
    n_src = len(_SRC_LINE_RE.findall(body))
    checks = {
        "①섹션>=5": len(know) >= 5,
        "②인용>=3": len(cites) >= 3,
        "③인용번호<=출처수": bool(cites) and max(cites) <= n_src,
        "④본문400~8000": _MIN_BODY_LEN <= len(body) <= _MAX_BODY_LEN,
        "⑤출처>=3형식": n_src >= 3,
        "⑥광고문구없음": not any(p in body for p in _BANNED_PHRASES),
        # reinforce는 섹션 수로 shrink 판정 → 기존보다 섹션 많고 본문도 길어야 채택됨
        "⑦기존보다섹션많음": len(know) > original_section_count,
        "⑧기존보다본문김": len(body) > original_body_len,
    }
    stats = {
        "sections": len(know), "cites": len(cites), "sources": n_src,
        "body_len": len(body), "original_body_len": original_body_len,
        "original_section_count": original_section_count,
    }
    return PrecheckResult(all(checks.values()), checks, stats)


# ── 기존 위키 로드 ─────────────────────────────────────────────────────────────
def load_existing_wiki(category: str, domain: str) -> str | None:
    """hezo-wiki/industries/{category}/{domain}.md 로드 (없으면 None)."""
    key = f"industries/{category}/{domain}.md"
    try:
        import boto3  # noqa: PLC0415
        s3 = boto3.client("s3", region_name=_AWS_REGION)
        obj = s3.get_object(Bucket=_WIKI_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception as exc:
        logger.info("기존 위키 없음/로드 실패 key=%s: %s", key, exc)
        return None


# ── Claude 위키 보강 ───────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """당신은 업종 도메인 지식 위키를 보강·확장하는 전문 편집자입니다.
입력으로 받은 기존 위키 markdown을 누락·얕은 부분을 채워 더 풍부한 전체 markdown으로 재작성합니다.

반드시 지킬 규칙:
1. 모든 사실 문장 끝에 [Sn] 형식 인용을 붙인다. 인용 없는 주장은 쓰지 않는다.
2. 교과서적으로 확실하고 검증 가능한 사실만 사용한다. 의심스러운 수치·날짜·통계·과장 표현은 금지한다.
3. frontmatter(--- ... ---)의 domain, category 값은 그대로 유지한다.
4. '## ' 지식 섹션('## 출처' 제외)은 사용자 메시지에 지정된 최소 개수 이상으로 구성한다. 기존 위키의 모든 토픽을 보존하고 새 토픽을 추가해 섹션 수를 늘린다(채택은 기존보다 섹션이 많아야 통과).
5. 각 섹션은 200~400자로 간결히 쓰고, 본문(frontmatter 제외) 전체는 절대 8000자를 넘기지 않는다.
6. 광고/홍보/CTA 문구를 쓰지 않는다 (예: 지금 전화, 무료 상담, 상담 신청, 방문 예약, 할인 이벤트, 특가 세일, 선착순 마감).
7. 결과는 기존 위키보다 정보량이 많아야 한다(확장). 줄이지 않는다.
8. 문서는 **반드시 '## 출처' 섹션으로 끝맺는다.** 각 출처를 `[S1] 제목 — URL` 형식(구분자는 em-dash —)으로 3개 이상 나열한다. 본문에서 인용한 모든 [Sn] 번호는 출처 개수를 넘지 않는다. 기존 위키의 출처를 보존·확장한다.
9. 출력은 완성된 markdown 전체만. 설명 문장이나 코드블록 표시(```)는 넣지 않는다.

분량 배분: 본문을 간결히 유지해 토큰이 부족해지지 않게 하고, 마지막 '## 출처'까지 반드시 완성하라."""


def build_enriched_wiki_md(
    *,
    existing_wiki: str,
    hints: str = "",
    min_sections: int = 6,
    invoker: Any | None = None,
    max_tokens: int = 12000,
) -> str | None:
    """Claude로 기존 위키를 보강한 전체 markdown 생성. 실패 시 None."""
    inv = invoker or Boto3BedrockClaudeInvoker()
    hint_block = (
        f"\n\n[참고 — 실제 사업자 인터뷰 힌트: 사실로 단정하지 말고 어떤 영역을 보강할지 방향 참고만]\n{hints}\n"
        if hints.strip() else ""
    )
    user_content = (
        f"[기존 위키 markdown]\n{existing_wiki}\n{hint_block}\n"
        f"위 위키를 규칙에 맞게 보강한 전체 markdown을 출력하세요. "
        f"반드시 '## ' 지식 섹션('## 출처' 제외)을 {min_sections}개 이상으로 구성하세요(기존보다 많아야 채택됨). "
        f"각 섹션은 간결히 쓰고 본문 8000자를 넘기지 마세요. 마지막은 반드시 '## 출처'로 끝맺으세요."
    )
    try:
        result = inv.invoke(
            ClaudeInvocationInput(
                use_case="wiki_enrichment",
                system_prompt=_SYSTEM_PROMPT,
                messages=(ClaudeMessage(role="user", content=user_content),),
                max_tokens=max_tokens,
            )
        )
    except Exception as exc:
        logger.error("위키 보강 Claude 호출 예외: %s", exc)
        return None
    if result.status != "succeeded" or not result.text.strip():
        logger.warning("위키 보강 Claude 실패: status=%s reasons=%s", result.status, result.reasons)
        return None
    return _strip_code_fence(result.text)


# ── S3 저장 ────────────────────────────────────────────────────────────────────
def save_supplement_to_staging(*, category: str, domain: str, site_id: str, markdown: str) -> str:
    """hezo-wiki-staging/pending/{category}/{domain}_{site_id}.md 저장. domain은 언더스코어."""
    key = f"pending/{category}/{domain}_{site_id}.md"
    import boto3  # noqa: PLC0415
    s3 = boto3.client("s3", region_name=_AWS_REGION)
    s3.put_object(
        Bucket=_STAGING_BUCKET, Key=key,
        Body=markdown.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    logger.info("P2 보강 저장 완료: s3://%s/%s", _STAGING_BUCKET, key)
    return key


# ── 힌트 구성 ──────────────────────────────────────────────────────────────────
def _build_hints(known_answers: dict[str, Any]) -> str:
    keys = (
        "core_services", "tax_services", "wine_lineup", "author_info",
        "target_audience", "target_clients", "business_region",
    )
    parts = [f"- {k}: {known_answers[k]}" for k in keys if str(known_answers.get(k) or "").strip()]
    return "\n".join(parts)


# ── 진입점 (chat_http_handler에서 호출) ─────────────────────────────────────────
def try_submit_p2_supplement(
    *,
    site_id: str,
    domain: str,
    domain_label: str,
    category: str,
    known_answers: dict[str, Any],
    template_id: str = "",
    invoker: Any | None = None,
) -> None:
    """기존 위키 로드 → Claude 보강 → precheck 통과 시 staging 저장."""
    if not domain or not category:
        logger.debug("P2 보강 건너뜀: domain 또는 category 없음")
        return

    domain_us = normalize_domain(domain)
    existing = load_existing_wiki(category, domain_us)
    if not existing:
        logger.info("P2 보강 건너뜀: 기존 위키 없음 category=%s domain=%s", category, domain_us)
        return

    original_body_len = len(_body(existing))
    original_sections = count_know_sections(existing)
    enriched = build_enriched_wiki_md(
        existing_wiki=existing,
        hints=_build_hints(known_answers),
        min_sections=original_sections + 3,  # 기존보다 섹션 많게(shrink 방지)
        invoker=invoker,
    )
    if not enriched:
        return

    chk = precheck(
        enriched,
        original_body_len=original_body_len,
        original_section_count=original_sections,
    )
    logger.info(
        "P2 보강 precheck site=%s domain=%s passed=%s checks=%s stats=%s",
        site_id, domain_us, chk.passed, chk.checks, chk.stats,
    )
    if not chk.passed:
        return

    try:
        save_supplement_to_staging(
            category=category, domain=domain_us, site_id=site_id, markdown=enriched,
        )
    except Exception as exc:
        logger.error("P2 보강 S3 저장 실패 site=%s domain=%s: %s", site_id, domain_us, exc)
