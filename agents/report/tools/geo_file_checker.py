"""GEO 파일 5종 접근성 및 품질 체크"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

AI_BOTS = ["GPTBot", "ClaudeBot", "PerplexityBot", "Yeti", "Googlebot"]
TIMEOUT = 10.0


def _get(url: str) -> tuple[int, str]:
    try:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "HEZO-ReportAgent/1.0"})
        return resp.status_code, resp.text
    except Exception as exc:
        logger.warning("GET 실패: %s — %s", url, exc)
        return 0, ""


def _check_llms_txt(domain_url: str) -> dict:
    status, body = _get(f"{domain_url}/llms.txt")
    ok = status == 200 and len(body) > 50
    has_core_pages = "## 핵심 페이지" in body or "## Core Pages" in body
    return {"ok": ok, "status_code": status, "has_core_pages_section": has_core_pages}


def _check_llms_full_txt(domain_url: str) -> dict:
    status, body = _get(f"{domain_url}/llms-full.txt")
    ok = status == 200 and len(body) > 50
    faq_count = len(re.findall(r"^Q:", body, re.MULTILINE))
    return {"ok": ok, "status_code": status, "faq_count": faq_count}


def _check_sitemap_xml(domain_url: str) -> dict:
    status, body = _get(f"{domain_url}/sitemap.xml")
    ok = status == 200 and "<urlset" in body
    has_llms_full = "llms-full.txt" in body
    return {"ok": ok, "status_code": status, "has_llms_full": has_llms_full}


def _check_robots_txt(domain_url: str) -> dict:
    status, body = _get(f"{domain_url}/robots.txt")
    ok = status == 200 and len(body) > 0
    bots: dict[str, bool] = {}
    for bot in AI_BOTS:
        # User-agent: BotName 뒤 Allow: / 패턴
        pattern = rf"User-agent:\s*{re.escape(bot)}.*?(?:Allow:\s*/|Disallow:\s*$)"
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        bots[bot] = bool(match and "Allow" in match.group())
    return {"ok": ok, "status_code": status, "bots": bots}


def _check_jsonld(domain_url: str) -> dict:
    status, body = _get(f"{domain_url}/")
    if status != 200 or not body:
        return {"ok": False, "types_found": [], "has_faq_page": False}
    soup = BeautifulSoup(body, "lxml")
    scripts = soup.find_all("script", type="application/ld+json")
    types_found: list[str] = []
    for script in scripts:
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                t = data.get("@type", "")
                if t:
                    types_found.append(t)
            elif isinstance(data, list):
                for item in data:
                    t = item.get("@type", "")
                    if t:
                        types_found.append(t)
        except (json.JSONDecodeError, AttributeError):
            continue
    has_faq = "FAQPage" in types_found
    return {"ok": bool(types_found), "types_found": types_found, "has_faq_page": has_faq}


def check_geo_files(domain_url: str) -> dict[str, Any]:
    """
    GEO 파일 5종 접근성 체크.
    domain_url 예: "https://hezo.doodo.cloud"
    """
    domain_url = domain_url.rstrip("/")
    logger.info("GEO 파일 체크 시작: %s", domain_url)

    result = {
        "llms_txt": _check_llms_txt(domain_url),
        "llms_full_txt": _check_llms_full_txt(domain_url),
        "sitemap_xml": _check_sitemap_xml(domain_url),
        "robots_txt": _check_robots_txt(domain_url),
        "jsonld": _check_jsonld(domain_url),
    }

    checks = [
        result["llms_txt"]["ok"],
        result["llms_txt"]["has_core_pages_section"],
        result["llms_full_txt"]["ok"],
        result["llms_full_txt"]["faq_count"] >= 3,
        result["sitemap_xml"]["ok"],
        result["sitemap_xml"]["has_llms_full"],
        result["robots_txt"]["ok"],
        all(result["robots_txt"]["bots"].values()),
        result["jsonld"]["ok"],
        result["jsonld"]["has_faq_page"],
    ]
    result["summary_score"] = round(sum(checks) / len(checks) * 100)

    logger.info("GEO 파일 체크 완료: score=%d", result["summary_score"])
    return result
