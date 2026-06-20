"""
구글 인덱싱 상태 추정.

Google Search Console API는 per-site OAuth가 필요해 MVP에서 사용 불가.
대신 아래 방법으로 인덱싱 상태를 추정한다:
  1. 발행 후 경과일 기반 확률 추정 (구글 인덱싱 통계 기반)
  2. sitemap.xml 접근성 확인 (인덱싱 가속 조건)
  3. GOOGLE_SEARCH_API_KEY 있으면 Custom Search API로 site: 쿼리 (선택)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "")
TIMEOUT = 10.0


def _estimate_by_days(days: int) -> tuple[str, int]:
    """
    발행 후 경과일 기반 인덱싱 확률 추정.
    구글 통계: 신규 사이트 중간값 4일, 95%가 14일 내 색인
    """
    if days >= 14:
        return "indexed", 95
    if days >= 7:
        return "likely_indexed", 75
    if days >= 3:
        return "pending", 40
    return "pending", 10


def _check_sitemap(domain_url: str) -> bool:
    try:
        resp = httpx.get(f"{domain_url}/sitemap.xml", timeout=TIMEOUT, follow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return False


def _check_via_custom_search(domain_url: str) -> bool | None:
    if not GOOGLE_SEARCH_API_KEY or not GOOGLE_SEARCH_ENGINE_ID:
        return None
    domain = domain_url.replace("https://", "").replace("http://", "").rstrip("/")
    try:
        resp = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_SEARCH_API_KEY,
                "cx": GOOGLE_SEARCH_ENGINE_ID,
                "q": f"site:{domain}",
                "num": 1,
            },
            timeout=TIMEOUT,
        )
        data = resp.json()
        total = int(data.get("searchInformation", {}).get("totalResults", "0"))
        logger.info("Google Custom Search site:%s → %d 결과", domain, total)
        return total > 0
    except Exception as exc:
        logger.warning("Google Custom Search 실패: %s", exc)
        return None


def check_google_indexing(domain_url: str, days_since_publish: int) -> dict[str, Any]:
    """
    구글 인덱싱 상태 추정.
    days_since_publish: 사이트 최초 발행 후 경과일
    """
    domain_url = domain_url.rstrip("/")
    sitemap_ok = _check_sitemap(domain_url)

    api_result = _check_via_custom_search(domain_url)

    if api_result is True:
        status, likelihood = "indexed", 99
        note = "Google Custom Search API로 인덱싱 확인됨"
    elif api_result is False:
        status, likelihood = "pending", max(10, days_since_publish * 5)
        note = "Google Custom Search API 기준 미인덱싱 (최대 14일 소요)"
    else:
        status, likelihood = _estimate_by_days(days_since_publish)
        note = f"발행 {days_since_publish}일 경과 기준 추정 (Google Search Console API 미연동)"

    if sitemap_ok and likelihood < 75:
        likelihood = min(likelihood + 15, 75)
        note += " / sitemap.xml 제출로 인덱싱 가속 가능"

    logger.info("인덱싱 상태: %s (%d%%) days=%d", status, likelihood, days_since_publish)
    return {
        "days_since_publish": days_since_publish,
        "indexing_status": status,
        "indexing_likelihood_pct": likelihood,
        "sitemap_accessible": sitemap_ok,
        "note": note,
    }
