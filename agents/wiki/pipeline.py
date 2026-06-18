"""HEZO Wiki (P2) 생성·검수·저장 파이프라인 (순수 로직, 런타임 무관).

raw_key의 수집 원문을 읽어 생성 → 검수 → (통과)저장 / (미달)거부까지 한 흐름으로 잇는다.
람다 핸들러는 이 generate_and_store()를 호출만 한다(ECS 전환 시 그대로 재사용).

- 통과(≥0.70 & 게이트): assemble_markdown으로 최종 md 조립 → save_industry_versioned
  (S3 버전드 + DDB commit). 신뢰도 = 검수 합성점수.
- 미달: index.reject(domain) (attempts++, S3 미변경 → 다음 회차 재시도, 버전드라 손실 없음).

category는 입력을 믿지 않고 카탈로그(get_entry)를 진실원본으로 써서 키·파서 일치를 보장한다.
"""
from __future__ import annotations

from agents.shared.s3_utils import read_json

from agents.wiki.catalog import get_entry
from agents.wiki.constants import WIKI_BUCKET
from agents.wiki.generate import assemble_markdown, generate
from agents.wiki.index_store import WikiIndexStore
from agents.wiki.precheck import precheck
from agents.wiki.review import review
from agents.wiki.storage import save_industry_versioned


def generate_and_store(category: str, domain: str, raw_key: str, *, llm=None, index=None) -> dict:
    """raw_key 원문 → 생성·검수 → 통과 시 저장/거부. 요약 dict 반환."""
    entry = get_entry(domain)
    label = entry["label"]
    category = entry["category"]  # 진실원본(입력 category 불신) — 키·파서 일치 보장

    payload = read_json(WIKI_BUCKET, raw_key)
    docs = payload.get("docs", [])

    gen = generate(category, domain, docs, llm=llm)
    if not gen.ok:
        return {"domain": domain, "stage": "generate_failed", "passed": False, "reason": gen.reason}

    # 룰베이스 사전검사(결정적) — 통과해야 LLM 채점으로. 실패 시 LLM 콜 없이 즉시 거부.
    pc = precheck(gen.body, gen.selected)
    if not pc.passed:
        attempts = (index or WikiIndexStore()).reject(domain)
        return {
            "domain": domain,
            "stage": "precheck_failed",
            "passed": False,
            "violations": pc.violations,
            "stats": pc.stats,
            "attempts": attempts,
        }

    rev = review(category, domain, gen.body, gen.selected, llm=llm)
    if not rev.ok:
        return {"domain": domain, "stage": "review_failed", "passed": False, "reason": rev.reason}

    if not rev.passed:
        attempts = (index or WikiIndexStore()).reject(domain)
        return {
            "domain": domain,
            "stage": "rejected",
            "passed": False,
            "score": rev.score,
            "gate_failed": rev.gate_failed,
            "attempts": attempts,
        }

    md = assemble_markdown(domain, category, label, gen.body, gen.selected, confidence=rev.score)
    source_urls = [d.get("url", "") for d in gen.selected]
    saved = save_industry_versioned(
        category, domain, md, confidence=rev.score, source_urls=source_urls, index=index
    )
    return {
        "domain": domain,
        "stage": "committed",
        "passed": True,
        "score": rev.score,
        "version_id": saved["version_id"],
        "bytes": saved["bytes"],
        "committed": saved["committed"],
    }
