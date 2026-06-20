"""
HTML + GEO 파일 파싱으로 룰 기반 GEO 구조 점수 산출 (0~100).
외부 API 없음 — BeautifulSoup + httpx만 사용.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
TIMEOUT = 10.0


def _fetch(url: str) -> str:
    try:
        resp = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                         headers={"User-Agent": "HEZO-ReportAgent/1.0"})
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return ""


def _check_html_structure(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1")
    h1_exists = h1 is not None

    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc_content = (meta_desc.get("content", "") if meta_desc else "").strip()
    desc_len = len(desc_content)
    meta_desc_ok = 50 <= desc_len <= 160

    scripts = soup.find_all("script", type="application/ld+json")
    jsonld_types: list[str] = []
    for s in scripts:
        try:
            data = json.loads(s.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                t = item.get("@type", "")
                if t:
                    jsonld_types.append(t)
        except (json.JSONDecodeError, AttributeError):
            continue

    return {
        "h1_exists": h1_exists,
        "meta_description_ok": meta_desc_ok,
        "meta_description_len": desc_len,
        "jsonld_types": jsonld_types,
    }


def _check_llms_full(text: str) -> dict:
    if not text:
        return {"faq_count": 0, "faq_avg_length": 0}
    qa_pairs = re.findall(r"^Q:(.+?)(?=\nQ:|\Z)", text, re.MULTILINE | re.DOTALL)
    faq_count = len(qa_pairs)
    if faq_count == 0:
        return {"faq_count": 0, "faq_avg_length": 0}
    a_lengths = []
    for qa in qa_pairs:
        a_match = re.search(r"A:(.+?)$", qa, re.MULTILINE | re.DOTALL)
        if a_match:
            a_lengths.append(len(a_match.group(1).strip()))
    avg_len = round(sum(a_lengths) / len(a_lengths)) if a_lengths else 0
    return {"faq_count": faq_count, "faq_avg_length": avg_len}


def _check_llms_txt(text: str) -> dict:
    if not text:
        return {"llms_txt_link_count": 0}
    links = re.findall(r"\[.+?\]\(.+?\)", text)
    return {"llms_txt_link_count": len(links)}


def _calculate_score(details: dict) -> tuple[int, list[str]]:
    score = 100
    issues: list[str] = []

    if not details["h1_exists"]:
        score -= 15
        issues.append("H1 태그 없음 (-15점)")

    if not details["meta_description_ok"]:
        score -= 10
        if details["meta_description_len"] == 0:
            issues.append("meta description 미설정 (-10점)")
        else:
            issues.append(f"meta description 길이 부적절 ({details['meta_description_len']}자, 권장 50~160자) (-10점)")

    jsonld_types = details["jsonld_types"]
    required_types = {"LocalBusiness", "FAQPage", "Service"}
    missing = required_types - set(jsonld_types)
    if missing:
        deduct = len(missing) * 7
        score -= deduct
        issues.append(f"JSON-LD 타입 누락: {', '.join(sorted(missing))} (-{deduct}점)")

    faq_count = details["faq_count"]
    if faq_count < 3:
        score -= 15
        issues.append(f"FAQ 개수 부족 ({faq_count}개, 권장 5개 이상) (-15점)")
    elif faq_count < 5:
        score -= 7
        issues.append(f"FAQ 개수 보완 필요 ({faq_count}개, 권장 5개 이상) (-7점)")

    avg_len = details["faq_avg_length"]
    if avg_len < 50:
        score -= 10
        issues.append(f"FAQ 답변 너무 짧음 (평균 {avg_len}자, 권장 100자 이상) (-10점)")
    elif avg_len < 100:
        score -= 5
        issues.append(f"FAQ 답변 보완 권장 (평균 {avg_len}자) (-5점)")

    link_count = details["llms_txt_link_count"]
    if link_count < 1:
        score -= 8
        issues.append("llms.txt 핵심 페이지 링크 없음 (-8점)")
    elif link_count < 3:
        score -= 3
        issues.append(f"llms.txt 핵심 페이지 링크 부족 ({link_count}개, 권장 3개 이상) (-3점)")

    return max(0, score), issues


def score_geo_structure(domain_url: str) -> dict[str, Any]:
    """GEO 구조 룰 기반 점수 산출"""
    domain_url = domain_url.rstrip("/")
    logger.info("GEO 구조 점수 산출 시작: %s", domain_url)

    html = _fetch(f"{domain_url}/")
    llms_full = _fetch(f"{domain_url}/llms-full.txt")
    llms_txt = _fetch(f"{domain_url}/llms.txt")

    html_info = _check_html_structure(html)
    faq_info = _check_llms_full(llms_full)
    link_info = _check_llms_txt(llms_txt)

    details = {**html_info, **faq_info, **link_info}
    score, issues = _calculate_score(details)

    logger.info("GEO 구조 점수: %d, 이슈 %d개", score, len(issues))
    return {"score": score, "details": details, "issues": issues}
