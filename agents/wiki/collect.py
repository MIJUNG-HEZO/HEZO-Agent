"""HEZO Wiki (P2) 수집 오케스트레이션 (순수 로직, 런타임 무관).

도메인 1개에 대해: 검색(CSE) → 크롤(httpx+trafilatura) → raw 원문 S3 저장.
`event`/`context`를 모르는 순수 함수 — 람다 핸들러는 이 `collect()`를 호출만 한다
(ECS 전환 시에도 그대로 재사용). 출력은 S3 raw/로 핸드오프(생성 단계 전달).
"""
from __future__ import annotations

from datetime import datetime, timezone

from agents.shared.s3_utils import write_json

from agents.wiki.constants import WIKI_BUCKET, raw_key
from agents.wiki.fetch import fetch_many
from agents.wiki.search import search_sources


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def save_raw(category: str, domain: str, docs: list[dict], *, date: str | None = None) -> str:
    """수집 원문(json)을 S3 raw/에 저장(생성 단계 전달용). raw 키 반환."""
    date = date or _today()
    key = raw_key(category, domain, date)
    write_json(WIKI_BUCKET, key, {"category": category, "domain": domain, "date": date, "docs": docs})
    return key


def collect(category: str, domain: str, *, num: int = 10, date: str | None = None) -> dict:
    """검색 → 크롤 → raw 저장. 요약 dict 반환(본문은 S3 raw/에).

    반환: {raw_key, searched, fetched, kept, needs_headless_count, needs_headless_urls}
    """
    sources = search_sources(domain, num=num)
    docs = fetch_many([s["url"] for s in sources])

    # 검색 단계의 source_grade를 추출 문서에 머지
    grade = {s["url"]: s["source_grade"] for s in sources}
    for doc in docs:
        doc["source_grade"] = grade.get(doc["url"], "low")

    kept = [d for d in docs if d.get("ok")]
    needs_headless = [d["url"] for d in docs if d.get("needs_headless")]
    rk = save_raw(category, domain, docs, date=date)

    return {
        "raw_key": rk,
        "searched": len(sources),
        "fetched": len(docs),
        "kept": len(kept),
        "needs_headless_count": len(needs_headless),
        "needs_headless_urls": needs_headless,
    }
