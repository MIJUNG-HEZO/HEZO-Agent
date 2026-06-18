"""HEZO Wiki (P2) 출처 검색 — Serper.dev (Google 검색결과) (순수 로직, 런타임 무관).

도메인 → 신뢰 출처 URL 목록. 람다/ECS 어디서든 호출 가능한 순수 함수.
키는 환경변수(SERPER_API_KEY)로만 — 코드/깃에 두지 않는다.

검색 제공자는 search.py에만 갇혀 있다(로직 분리). 제공자를 바꾸려면 _call_serper만
다른 함수(Tavily/네이버/CSE 화이트리스트 등)로 갈아끼우면 되고, fetch/collect는 그대로다.
(CSE 전체 웹 검색은 2026-03 구글 정책으로 신규 폐지 → Serper.dev로 구글 결과를 받는다)

출처 등급(source_grade): 정부·공공(.go.kr/.or.kr)=high / 신뢰 위키·백과=mid / 그 외=low.
(도메인 유형별 정교화는 후속 — 규제 도메인은 go.kr, 커머스·라이프는 미디어·협회 중심)
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from agents.wiki.catalog import get_entry

SERPER_ENDPOINT = "https://google.serper.dev/search"
_MID_HOSTS = ("wikipedia.org", "namu.wiki")


def build_query(domain: str) -> str:
    """검색어 = 도메인 라벨 그대로. ("정보 가이드" 등 접미사는 책·강의 페이지를
    끌어와 권위 출처를 밀어내므로 제거 — 실측으로 확인. 60개 자동 적용)"""
    return get_entry(domain)["label"]


def source_grade(url: str) -> str:
    """URL 호스트로 출처 등급 태깅 (high/mid/low)."""
    host = urlparse(url).netloc.lower()
    if host.endswith(".go.kr") or host.endswith(".or.kr"):
        return "high"
    if any(m in host for m in _MID_HOSTS):
        return "mid"
    return "low"


def _call_serper(query: str, num: int) -> dict:
    """Serper.dev 호출(구글 검색결과). 키 없으면 명확히 실패. (테스트는 이 함수를 monkeypatch)"""
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise RuntimeError("serper_credentials_missing: SERPER_API_KEY")
    import httpx  # 람다 import 비용 회피 위해 지연 import

    resp = httpx.post(
        SERPER_ENDPOINT,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": min(max(num, 1), 20), "gl": "kr", "hl": "ko"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def search_sources(domain: str, *, num: int = 10) -> list[dict]:
    """도메인 출처 URL 검색 → [{url, title, snippet, source_grade}] (dedupe)."""
    data = _call_serper(build_query(domain), num)
    out: list[dict] = []
    seen: set[str] = set()
    for item in data.get("organic", []):
        url = item.get("link")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "url": url,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "source_grade": source_grade(url),
            }
        )
    return out
