"""HEZO Wiki (P2) 본문 추출 — httpx + trafilatura (순수 로직, 런타임 무관).

URL → 정제된 본문 텍스트. httpx로 정적 HTML을 받아 trafilatura로 본문 추출.
헤드리스(브라우저)는 미사용 — 정적 본문이면 충분.

추출 실패 판정(빈 본문 / 최소 길이 미달 / 한국어 비율↓) 시 `needs_headless=True`
플래그만 부여한다. 실제 헤드리스(ECS) 폴백은 미구현 — 폴백률 실측 후 도입할
"훅 자리"일 뿐이다. (수집 로직은 람다 핸들러와 분리되어 ECS 전환 시 재사용)
"""
from __future__ import annotations

import re

DEFAULT_TIMEOUT = 15.0
MIN_LEN = 200            # 본문 최소 길이(문자)
MIN_KOREAN_RATIO = 0.2   # 한국어(한글) 최소 비율
_USER_AGENT = "Mozilla/5.0 (compatible; HEZO-WikiBot/1.0)"

_HANGUL = re.compile(r"[가-힣]")
_WORD = re.compile(r"\w", re.UNICODE)


def korean_ratio(text: str) -> float:
    """텍스트의 한글 문자 비율 (0~1)."""
    if not text:
        return 0.0
    hangul = len(_HANGUL.findall(text))
    letters = len(_WORD.findall(text)) or 1
    return hangul / letters


def fetch_clean(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    min_len: int = MIN_LEN,
    min_korean_ratio: float = MIN_KOREAN_RATIO,
) -> dict | None:
    """URL 본문 추출. HTTP 실패 시 None(스킵), 성공 시 dict(추출 품질 플래그 포함).

    반환 dict: {url, title, text, chars, korean_ratio, published_at, ok, needs_headless}
    - ok: 길이·한국어 비율 충족 → 생성 재료로 사용 가능
    - needs_headless: 추출 빈약 → 헤드리스 폴백 후보(미구현, 훅)
    """
    import httpx  # 지연 import (람다 import 비용·테스트 격리)
    import trafilatura

    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except Exception:
        return None  # 차단·타임아웃·4xx/5xx → 해당 URL 스킵(전체 중단 X)

    html = resp.text
    text = (trafilatura.extract(html, favor_precision=True, include_comments=False, include_tables=True) or "").strip()

    title = ""
    published_at = None
    try:
        meta = trafilatura.extract_metadata(html)
        if meta:
            title = meta.title or ""
            published_at = meta.date
    except Exception:
        pass

    kr = korean_ratio(text)
    ok = len(text) >= min_len and kr >= min_korean_ratio
    return {
        "url": url,
        "title": title,
        "text": text,
        "chars": len(text),
        "korean_ratio": round(kr, 3),
        "published_at": published_at,
        "ok": ok,
        "needs_headless": not ok,  # 추출 실패 → 폴백 후보(미구현)
    }


def fetch_many(urls: list[str], **kwargs) -> list[dict]:
    """여러 URL 순차 추출. HTTP 실패 URL은 스킵."""
    out: list[dict] = []
    for url in urls:
        doc = fetch_clean(url, **kwargs)
        if doc is not None:
            out.append(doc)
    return out
