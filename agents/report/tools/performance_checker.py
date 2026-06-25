"""
사이트 응답속도 + Google PageSpeed Insights API 성능 측정 + SSL 만료일 체크.
PageSpeed API는 키 없이도 호출 가능 (일 25회 제한, 키 있으면 일 25000회).
"""
from __future__ import annotations

import logging
import os
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
TIMEOUT = 15.0
PAGESPEED_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _measure_response_time(domain_url: str) -> tuple[bool, int, int]:
    try:
        start = time.monotonic()
        resp = httpx.get(domain_url, timeout=TIMEOUT, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return True, resp.status_code, elapsed_ms
    except httpx.TimeoutException:
        return False, 0, int(TIMEOUT * 1000)
    except Exception as exc:
        logger.warning("응답시간 측정 실패: %s", exc)
        return False, 0, 0


def _get_pagespeed(domain_url: str, strategy: str) -> dict | None:
    params: dict[str, str] = {"url": domain_url, "strategy": strategy}
    if GOOGLE_API_KEY:
        params["key"] = GOOGLE_API_KEY
    try:
        resp = httpx.get(PAGESPEED_URL, params=params, timeout=30.0)
        if resp.status_code != 200:
            logger.warning("PageSpeed API 오류: %d", resp.status_code)
            return None
        return resp.json()
    except Exception as exc:
        logger.warning("PageSpeed API 실패: %s", exc)
        return None


def _extract_score(data: dict) -> int | None:
    try:
        return round(data["lighthouseResult"]["categories"]["performance"]["score"] * 100)
    except (KeyError, TypeError):
        return None


def _extract_lcp(data: dict) -> int | None:
    try:
        lcp_ms = data["lighthouseResult"]["audits"]["largest-contentful-paint"]["numericValue"]
        return int(lcp_ms)
    except (KeyError, TypeError):
        return None


def _extract_cls(data: dict) -> float | None:
    try:
        return float(data["lighthouseResult"]["audits"]["cumulative-layout-shift"]["numericValue"])
    except (KeyError, TypeError):
        return None


def _check_ssl(domain_url: str) -> dict:
    try:
        parsed = urlparse(domain_url)
        hostname = parsed.hostname
        if not hostname or parsed.scheme != "https":
            return {"ssl_ok": False, "ssl_days_remaining": None, "ssl_expires": None}
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, 443), timeout=10) as raw_sock:
            with ctx.wrap_socket(raw_sock, server_hostname=hostname) as sock:
                cert = sock.getpeercert()
        expire_str = cert["notAfter"]  # "Dec 31 23:59:59 2026 GMT"
        expire_dt = datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_remaining = (expire_dt - datetime.now(timezone.utc)).days
        return {
            "ssl_ok": True,
            "ssl_days_remaining": days_remaining,
            "ssl_expires": expire_dt.strftime("%Y-%m-%d"),
        }
    except Exception as exc:
        logger.warning("SSL 체크 실패: %s", exc)
        return {"ssl_ok": False, "ssl_days_remaining": None, "ssl_expires": None}


def _grade(mobile_score: int | None, response_ms: int) -> str:
    score = mobile_score or 0
    if score >= 90 and response_ms <= 500:
        return "A"
    if score >= 70 and response_ms <= 1500:
        return "B"
    if score >= 50:
        return "C"
    return "F"


def check_performance(domain_url: str) -> dict[str, Any]:
    """사이트 가용성 + 성능 점수 측정"""
    domain_url = domain_url.rstrip("/")
    logger.info("성능 체크 시작: %s", domain_url)

    is_up, status_code, response_ms = _measure_response_time(domain_url)
    ssl_info = _check_ssl(domain_url)

    mobile_data = _get_pagespeed(domain_url, "mobile") if is_up else None
    desktop_data = _get_pagespeed(domain_url, "desktop") if is_up else None

    mobile_score = _extract_score(mobile_data) if mobile_data else None
    desktop_score = _extract_score(desktop_data) if desktop_data else None
    lcp_ms = _extract_lcp(mobile_data) if mobile_data else None
    cls = _extract_cls(mobile_data) if mobile_data else None

    result = {
        "is_up": is_up,
        "response_ms": response_ms,
        "status_code": status_code,
        "mobile_score": mobile_score,
        "desktop_score": desktop_score,
        "lcp_ms": lcp_ms,
        "cls": cls,
        "performance_grade": _grade(mobile_score, response_ms),
        "ssl_ok": ssl_info["ssl_ok"],
        "ssl_days_remaining": ssl_info["ssl_days_remaining"],
        "ssl_expires": ssl_info["ssl_expires"],
    }
    logger.info("성능 체크 완료: grade=%s, mobile=%s, response=%dms, ssl_days=%s",
                result["performance_grade"], mobile_score, response_ms, ssl_info["ssl_days_remaining"])
    return result
