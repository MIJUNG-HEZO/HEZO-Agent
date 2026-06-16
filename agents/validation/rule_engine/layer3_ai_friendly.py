"""Layer 3: AI 친화 구조 검증 — BeautifulSoup 기반 (LLM 불사용)"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _parse_html(html_content: str):
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html_content, "lxml")
    except ImportError:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html_content, "html.parser")


def check_layer3(html_content: str, file_list: list[str]) -> list[dict]:
    """
    HTML의 AI 친화 구조를 검증.
    반환: 이슈 목록 [{level, code, detail}]
    """
    issues: list[dict] = []

    # HTML이 없으면 빌드 자체가 실패한 것
    if not html_content:
        issues.append({"level": "blocking", "code": "NO_HTML", "detail": "index.html 없음 — 빌드 실패"})
        return issues

    soup = _parse_html(html_content)

    # ── H1 ───────────────────────────────────────────────────────────────────
    h1_tags = soup.find_all("h1")
    if not h1_tags:
        issues.append({"level": "blocking", "code": "NO_H1", "detail": "H1 없음"})
    elif len(h1_tags) > 1:
        issues.append({
            "level": "blocking",
            "code": "MULTIPLE_H1",
            "detail": f"H1 {len(h1_tags)}개 — 페이지당 정확히 1개 필요",
        })

    # ── JSON-LD ───────────────────────────────────────────────────────────────
    jsonld_tags = soup.find_all("script", {"type": "application/ld+json"})
    if not jsonld_tags:
        issues.append({"level": "blocking", "code": "NO_JSONLD", "detail": "JSON-LD (application/ld+json) 없음"})
    else:
        has_faq_page = False
        for tag in jsonld_tags:
            try:
                data = json.loads(tag.string or "")
                if isinstance(data, dict) and data.get("@type") == "FAQPage":
                    has_faq_page = True
                    faq_items = data.get("mainEntity", [])
                    if len(faq_items) < 5:
                        issues.append({
                            "level": "warning",
                            "code": "INSUFFICIENT_FAQ",
                            "detail": f"FAQPage 항목 {len(faq_items)}개 — 5개 이상 권장",
                        })
            except (json.JSONDecodeError, TypeError):
                issues.append({"level": "warning", "code": "INVALID_JSONLD", "detail": "JSON-LD 파싱 불가"})
        if not has_faq_page:
            issues.append({"level": "blocking", "code": "NO_FAQ_PAGE_JSONLD", "detail": "FAQPage JSON-LD 없음"})

    # ── QuickAnswer ────────────────────────────────────────────────────────────
    qa_elem = soup.find(attrs={"data-hezo": "quick-answer"})
    if not qa_elem:
        issues.append({"level": "warning", "code": "NO_QUICK_ANSWER", "detail": "data-hezo='quick-answer' 요소 없음"})

    # ── 필수 파일 ─────────────────────────────────────────────────────────────
    required_files = {
        "llms.txt": "blocking",
        "sitemap.xml": "warning",
        "robots.txt": "warning",
    }
    for fname, level in required_files.items():
        if fname not in file_list:
            issues.append({
                "level": level,
                "code": f"NO_{fname.replace('.', '_').upper()}",
                "detail": f"{fname} 없음",
            })

    # robots.txt AI 봇 허용 확인
    robots_content = ""
    if "robots.txt" in file_list:
        # file_list는 파일명만 있으므로 실제 내용 체크는 별도
        pass  # artifact_fetcher에서 추가 로드 가능 — 현재는 skip

    # ── 이미지 alt ────────────────────────────────────────────────────────────
    imgs = soup.find_all("img")
    if imgs:
        missing_alt = [img for img in imgs if not img.get("alt")]
        ratio = len(missing_alt) / len(imgs)
        if ratio > 0.5:
            issues.append({
                "level": "warning",
                "code": "LOW_ALT_COVERAGE",
                "detail": f"이미지 {len(imgs)}개 중 {len(missing_alt)}개 alt 없음 ({ratio:.0%})",
            })

    # ── 메타 태그 ─────────────────────────────────────────────────────────────
    title_tag = soup.find("title")
    if not title_tag or not title_tag.text.strip():
        issues.append({"level": "blocking", "code": "NO_TITLE_TAG", "detail": "<title> 태그 없음"})

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content"):
        issues.append({"level": "warning", "code": "NO_META_DESCRIPTION", "detail": "meta description 없음"})

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if not canonical or not canonical.get("href"):
        issues.append({"level": "warning", "code": "NO_CANONICAL", "detail": "canonical link 없음"})

    return issues
