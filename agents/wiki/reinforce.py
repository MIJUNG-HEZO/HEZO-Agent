"""HEZO Wiki (P2) 보강 A — P1 보완 md vs 기존 위키 비교·택1 (하이브리드: 룰 + LLM 검수).

P1이 우리 위키 md를 가져가 편집·보완한 '전체 md'를 pending/에 올린다. 보강 A는 그것을
기존 위키와 비교해 **더 나은 쪽을 새 버전으로** 채택한다(병합 아님, 택1). 트리거(S3이벤트/
SQS)는 얇은 핸들러가 pending_key만 뽑아 reinforce()를 호출 — 코어는 트리거 무관.

하이브리드 흐름:
  1. [룰] pending md 파싱(frontmatter→domain·category) + precheck(P1)·동일성 검사
  2. [LLM] review(P1)·review(기존) 둘 다 8항목 채점 (보강 강화 프롬프트로 — 원문 미제공 보완)
  3. P1 점수 > 기존 AND P1 게이트 통과 → P1 md 새 버전 저장 / 아니면 기존 유지
  4. 어느 경우든 pending 삭제

품질 보완(출처 원문 미제공): 강화 프롬프트로 "모델 지식 팩트체크 + 인용 규율 + 모순 감점".
동시성: 저장은 낙관적 동시성(CAS) — 읽은 latest_version이 그대로일 때만 commit, 그새 다른
쓰기(다른 보강·크롤)가 끼면 ConcurrencyConflict → 최신 재읽기·재채점·재시도(MAX_CAS_RETRY).
frontmatter·출처는 저장 시 코드가 재조립(P1 오타로 파서 깨짐 방지).
"""
from __future__ import annotations

import re

from agents.shared.s3_utils import get_s3

from agents.wiki import catalog
from agents.wiki.catalog import get_entry
from agents.wiki.constants import WIKI_BUCKET
from agents.wiki.generate import assemble_markdown, build_sources_block
from agents.wiki.index_store import ConcurrencyConflict, WikiIndexStore
from agents.wiki.llm import BedrockLLM
from agents.wiki.precheck import precheck
from agents.wiki.review import REVIEW_SYSTEM, review
from agents.wiki.search import source_grade
from agents.wiki.storage import get_industry, industry_exists, save_industry_versioned

MAX_CAS_RETRY = 3
_H2_SOURCE = re.compile(r"^##\s+출처\s*$", re.MULTILINE)
_SOURCE_LINE = re.compile(r"^\[S\d+\]\s*(.*)$")

# 출처 원문이 없을 때 사실성·근거성을 보완하는 강화 지시 (보강 A 전용 — ⑤b는 원문 있어 안 씀)
REINFORCE_REVIEW_SYSTEM = REVIEW_SYSTEM + (
    "\n\n[보강 검수 추가 지시 — 출처 원문 미제공]\n"
    "- 출처 원문이 없으니, 너의 도메인 지식을 적극 활용해 명백히 틀리거나 의심스러운 "
    "사실·수치·날짜를 사실정확성에서 강하게 감점하라. 확신이 없으면 보수적으로(낮게) 채점.\n"
    "- 구체 주장(수치·통계·날짜·고유명사)에 [Sn] 인용이 없으면 근거성을 감점하라.\n"
    "- 문서 내 앞뒤 모순·근거 없는 단정·과장 표현을 감점하라."
)


# ─── S3 입출력 (테스트 monkeypatch 지점) ─────────────────────────────────────
def _read_pending(key: str) -> str:
    resp = get_s3().get_object(Bucket=WIKI_BUCKET, Key=key)
    return resp["Body"].read().decode("utf-8")


def _delete_pending(key: str) -> None:
    get_s3().delete_object(Bucket=WIKI_BUCKET, Key=key)


# ─── 파싱 헬퍼 ───────────────────────────────────────────────────────────────
def parse_frontmatter(md: str) -> tuple[dict, str]:
    """md → (frontmatter dict, 본문). frontmatter 없으면 ({}, 원문)."""
    lines = md.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, md.strip()
    end = None
    fm_lines: list[str] = []
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            end = i
            break
        fm_lines.append(ln)
    if end is None:
        return {}, md.strip()
    meta: dict = {}
    for raw in fm_lines:
        s = raw.strip()
        if not s or ":" not in s:
            continue
        k, v = s.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, "\n".join(lines[end + 1:]).strip()


def split_body_sources(wiki_md: str) -> tuple[str, list[dict]]:
    """위키 md → (지식 본문, 출처 목록[{title,url,source_grade}] S1..Sm 순)."""
    _, body = parse_frontmatter(wiki_md)
    parts = _H2_SOURCE.split(body, maxsplit=1)
    main = parts[0].rstrip()
    sources: list[dict] = []
    if len(parts) > 1:
        for ln in parts[1].splitlines():
            m = _SOURCE_LINE.match(ln.strip())
            if not m:
                continue
            rest = m.group(1)
            title, url = (rest.rsplit(" — ", 1) if " — " in rest else ("", rest))
            url = url.strip()
            sources.append({"title": title.strip(), "url": url, "source_grade": source_grade(url)})
    return main, sources


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _score_md(category: str, domain: str, body: str, sources: list[dict], llm):
    """완성 md(본문+출처)를 review 8항목으로 채점 — 보강 강화 프롬프트, 출처 목록 기반."""
    block = build_sources_block(sources)
    return review(category, domain, body, sources, llm=llm, sources_block=block,
                  system_prompt=REINFORCE_REVIEW_SYSTEM)


def _res(stage, adopted, domain, *, score=None, version_id=None, reason="") -> dict:
    return {"stage": stage, "adopted": adopted, "domain": domain,
            "score": score, "version_id": version_id, "reason": reason}


# ─── 코어 ────────────────────────────────────────────────────────────────────
def reinforce(pending_key: str, *, llm: BedrockLLM | None = None, index: WikiIndexStore | None = None) -> dict:
    """pending_key의 P1 보완 md를 기존 위키와 비교 → 더 나은 쪽 채택(CAS). 트리거 무관."""
    raw = _read_pending(pending_key)
    fm, _ = parse_frontmatter(raw)
    domain = str(fm.get("domain", "")).strip()

    if domain not in catalog.all_domains():
        _delete_pending(pending_key)
        return _res("rejected_bad_domain", False, domain, reason=f"unknown domain: {domain!r}")
    category = get_entry(domain)["category"]
    label = get_entry(domain)["label"]
    llm = llm or BedrockLLM()
    index = index or WikiIndexStore()

    p1_body, p1_sources = split_body_sources(raw)

    # 1. 룰: precheck(P1) — 형식·인용유효성·광고
    pc = precheck(p1_body, p1_sources)
    if not pc.passed:
        _delete_pending(pending_key)
        return _res("rejected_precheck", False, domain, reason=str(pc.violations))

    # 2. LLM: P1 채점 (한 번만 — P1 내용은 안 바뀜)
    rev_p1 = _score_md(category, domain, p1_body, p1_sources, llm)
    if not rev_p1.ok or not rev_p1.passed:
        _delete_pending(pending_key)
        return _res("rejected_p1_review", False, domain,
                    score=(rev_p1.score if rev_p1.ok else None),
                    reason=rev_p1.reason or f"gate_failed:{rev_p1.gate_failed}")

    # 3~5. 비교·저장 (낙관적 동시성 — 충돌 시 최신 재읽기·재시도)
    for _ in range(MAX_CAS_RETRY):
        exists = industry_exists(category, domain)
        if exists:
            meta = index.get(domain) or {}
            expected = meta.get("latest_version")
            existing_body, existing_sources = split_body_sources(get_industry(category, domain))
            if _norm(p1_body) == _norm(existing_body):
                _delete_pending(pending_key)
                return _res("rejected_no_change", False, domain, reason="identical_to_existing")
            rev_ex = _score_md(category, domain, existing_body, existing_sources, llm)
            score_existing = rev_ex.score if rev_ex.ok else -1.0
        else:
            expected = None
            score_existing = -1.0

        if rev_p1.score <= score_existing:
            _delete_pending(pending_key)
            return _res("kept_existing", False, domain, score=rev_p1.score,
                        reason=f"p1({rev_p1.score})<=existing({score_existing})")

        md = assemble_markdown(domain, category, label, p1_body, p1_sources, confidence=rev_p1.score)
        try:
            saved = save_industry_versioned(
                category, domain, md, confidence=rev_p1.score,
                source_urls=[s["url"] for s in p1_sources], index=index,
                check_version=True, expected_version=expected,
            )
        except ConcurrencyConflict:
            continue  # 그새 다른 쓰기가 끼었음 → 최신 재읽기·재채점·재시도
        _delete_pending(pending_key)
        return _res("committed", True, domain, score=rev_p1.score, version_id=saved["version_id"])

    # 재시도 소진 (드문 고경합) — 이번 회차 포기
    _delete_pending(pending_key)
    return _res("rejected_conflict", False, domain, reason="cas_retry_exhausted")
