"""P1 → P2 보강 A: 채팅 수집 정보 룰셋 게이트 + supplement MD 빌더.

흐름:
  evaluate_supplement(known_answers) → SupplementCheckResult
    → passed=False  → 버림
    → passed=True   → build_supplement_md() → hezo-wiki-staging/pending/ 저장
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("hezo.chat.p2_supplement")

# ── 상수 ───────────────────────────────────────────────────────────────────────
_STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "hezo-wiki-staging")
_AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))

_BLACKLIST: frozenset[str] = frozenset([
    "이거임", "테스트", "test", "aaa", "bbb", "xxx", "yyy", "zzz",
    "홍길동", "없음", "모름", "몰라", "ㅇㅇ", "ㅇ", "ㄴ", "ㄱ",
    "asdf", "qwer", "zxcv", "1234", "0000", "abcd", "abcde",
    "sample", "dummy", "temp", "임시", "예시",
])

_SERVICE_VERBS: frozenset[str] = frozenset([
    "제공", "판매", "운영", "상담", "제작", "배송", "시공", "교육",
    "개발", "관리", "수리", "설치", "기획", "컨설팅", "진료", "치료",
    "대여", "임대", "수입", "도매", "소매", "직수입", "유통",
])

_KOREAN_RE = re.compile(r"[가-힣]")
_REPEAT_RE = re.compile(r"(.)\1{4,}")  # 동일 문자 5개 이상 연속


# ── 룰셋 결과 ──────────────────────────────────────────────────────────────────
@dataclass
class SupplementCheckResult:
    passed: bool
    quality_score: int        # Q 충족 개수 (0~4)
    confidence: float         # P2 reinforce Lambda 전달용
    reasons: list[str] = field(default_factory=list)


# ── 룰셋 평가 ──────────────────────────────────────────────────────────────────
def evaluate_supplement(known_answers: dict[str, Any], template_id: str = "") -> SupplementCheckResult:
    """
    MUST(R1~R4) + QUALITY(Q1~Q4) 룰셋 평가 (template-specific).

    통과 조건: MUST 전부 ✅ AND Q 2개 이상
    반환: passed=True 면 저장, False 면 버림.
    """
    reasons: list[str] = []
    q = 0

    # ── template-specific 룰셋 분기 ──
    if "wine-market" in str(template_id):
        # wine-market: wine_lineup 검사 (core_services 대신)
        field = str(known_answers.get("wine_lineup") or "").strip()
        field_name = "wine_lineup"
    elif "tax-accounting" in str(template_id):
        # tax-accounting: tax_services 검사
        field = str(known_answers.get("tax_services") or "").strip()
        field_name = "tax_services"
    elif "career-notebook" in str(template_id):
        # career-notebook: author_info 검사
        field = str(known_answers.get("author_info") or "").strip()
        field_name = "author_info"
    else:
        # generic: core_services 검사
        field = str(known_answers.get("core_services") or "").strip()
        field_name = "core_services"

    target = str(known_answers.get("target_audience") or "").strip()
    region = str(known_answers.get("business_region") or "").strip()

    # ── MUST ──────────────────────────────────────────────────────────────────
    # R1: 메인 필드 15자 이상
    if len(field) < 15:
        reasons.append(f"R1_fail: {field_name} {len(field)}자 < 15자")
        return SupplementCheckResult(False, 0, 0.0, reasons)

    # R2: 블랙리스트 패턴
    field_lower = field.lower()
    for bl in _BLACKLIST:
        if bl.lower() in field_lower:
            reasons.append(f"R2_fail: 블랙리스트 패턴 '{bl}' 감지")
            return SupplementCheckResult(False, 0, 0.0, reasons)

    # R3: 한국어 포함
    if not _KOREAN_RE.search(field):
        reasons.append("R3_fail: 한국어 없음")
        return SupplementCheckResult(False, 0, 0.0, reasons)

    # R4: 반복 문자 패턴
    if _REPEAT_RE.search(field):
        reasons.append("R4_fail: 반복 문자 패턴 감지")
        return SupplementCheckResult(False, 0, 0.0, reasons)

    # ── QUALITY ───────────────────────────────────────────────────────────────
    if len(field) >= 25:
        q += 1
        reasons.append(f"Q1_pass: {field_name} 25자 이상")

    if len(target) >= 5:
        q += 1
        reasons.append("Q2_pass: target_audience 존재")

    if region:
        q += 1
        reasons.append("Q3_pass: business_region 존재")

    for verb in _SERVICE_VERBS:
        if verb in field:
            q += 1
            reasons.append(f"Q4_pass: 서비스 동사 '{verb}' 포함")
            break

    if q < 2:
        reasons.append(f"quality_fail: Q={q} < 2 (최소 2개 필요)")
        return SupplementCheckResult(False, q, 0.0, reasons)

    confidence = {2: 0.62, 3: 0.72, 4: 0.82}.get(q, 0.62)
    reasons.append(f"passed: Q={q} confidence={confidence}")
    return SupplementCheckResult(True, q, confidence, reasons)


# ── MD 빌더 ────────────────────────────────────────────────────────────────────
def build_supplement_md(
    *,
    domain: str,
    domain_label: str,
    category: str,
    known_answers: dict[str, Any],
    confidence: float,
    site_id: str = "",
) -> str:
    """룰셋 통과한 슬롯 정보로 supplement MD 생성."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    core   = str(known_answers.get("core_services")   or "").strip()
    region = str(known_answers.get("business_region") or "").strip()
    target = str(known_answers.get("target_audience") or "").strip()
    hours  = str(known_answers.get("business_hours")  or "").strip()

    lines: list[str] = [
        "---",
        f"domain: {domain}",
        f"category: {category}",
        f"label: {domain_label}",
        f"confidence: {confidence}",
        "volatility: medium",
        f"last_updated: {today}",
        "source_urls: []",
        "---",
        "",
        f"# {domain_label} 실사업자 사례 보강 [S1]",
        "",
        "## 실제 운영 사례",
        "",
        (
            f"HEZO 플랫폼 {domain_label} 업종 사용자 인터뷰에서 수집된 "
            "실제 운영 정보입니다. 일반 업종 지식 보강 목적으로 제출되었습니다."
        ),
        "",
    ]

    if core:
        lines.append(f"- **주요 서비스·특징**: {core}")
    if region:
        lines.append(f"- **운영 지역**: {region}")
    if target:
        lines.append(f"- **주요 고객층**: {target}")
    # 기본값이 아닐 때만 포함
    if hours and hours not in ("평일 09:00-18:00", ""):
        lines.append(f"- **운영 시간 패턴**: {hours}")

    lines += [
        "",
        "## 출처",
        f"- [S1] HEZO 챗봇 사용자 인터뷰 ({today})"
        + (f" · site_id={site_id}" if site_id else ""),
    ]
    return "\n".join(lines)


# ── S3 저장 ────────────────────────────────────────────────────────────────────
def save_supplement_to_staging(
    *,
    domain: str,
    category: str,
    site_id: str,
    markdown: str,
) -> None:
    """hezo-wiki-staging/pending/{category}/{domain}_{site_id}.md 에 저장.

    site_id로 파일명을 유니크하게 유지해 복수 사용자 제출이 서로 덮어쓰지 않도록 한다.
    P2 reinforce Lambda는 ObjectCreated 이벤트로 파일 1개씩 독립 처리.
    """
    key = f"pending/{category}/{domain}_{site_id}.md"
    import boto3  # noqa: PLC0415
    s3 = boto3.client("s3", region_name=_AWS_REGION)
    s3.put_object(
        Bucket=_STAGING_BUCKET,
        Key=key,
        Body=markdown.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    logger.info("P2 보강 A 저장 완료: s3://%s/%s", _STAGING_BUCKET, key)


# ── 진입점 (chat_http_handler에서 호출) ─────────────────────────────────────────
def try_submit_p2_supplement(
    *,
    site_id: str,
    domain: str,
    domain_label: str,
    category: str,
    known_answers: dict[str, Any],
    template_id: str = "",
) -> None:
    """룰셋 평가 → 통과 시 staging 저장, 실패 시 버림."""
    if not domain or not category:
        logger.debug("P2 보강 A 건너뜀: domain 또는 category 없음")
        return

    result = evaluate_supplement(known_answers, template_id=template_id)
    logger.info(
        "P2 보강 A 룰셋 평가 site=%s domain=%s template=%s passed=%s Q=%d confidence=%s reasons=%s",
        site_id, domain, template_id, result.passed, result.quality_score,
        result.confidence, result.reasons,
    )

    if not result.passed:
        return

    md = build_supplement_md(
        domain=domain,
        domain_label=domain_label,
        category=category,
        known_answers=known_answers,
        confidence=result.confidence,
        site_id=site_id,
    )

    try:
        save_supplement_to_staging(domain=domain, category=category, site_id=site_id, markdown=md)
    except Exception as exc:
        logger.error("P2 보강 A S3 저장 실패 site=%s domain=%s: %s", site_id, domain, exc)
