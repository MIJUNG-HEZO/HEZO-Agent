"""render_spec 품질 평가기 — jsonschema 검증 + AI 가시성 점수 계산"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# SEO 길이 기준
_SEO_TITLE_MIN = 30
_SEO_TITLE_MAX = 60
_SEO_DESC_MIN = 80
_SEO_DESC_MAX = 160

# AI 가시성 기준
_FAQ_MIN = 5
_H2_MIN = 5
_LLMS_TXT_MIN_LEN = 350
_QUICKANSWER_MIN = 50
_QUICKANSWER_MAX = 120
_KEYWORDS_MIN = 3

# AI 크롤러 봇
_REQUIRED_BOTS = ("GPTBot", "ClaudeBot", "PerplexityBot", "Yeti")


def evaluate_render_spec(
    render_spec: dict[str, Any],
    threshold: int = 70,
) -> dict[str, Any]:
    """
    render_spec을 평가하고 결과 반환.
    반환: {score: int(0~100), issues: list[str], passed: bool, issue_count: int}
    """
    issues: list[str] = []
    score = 100

    # ── 1. 필수 최상위 필드 ────────────────────────────────────────────────
    for field in ("site_id", "template_id", "pages"):
        if field not in render_spec:
            issues.append(f"필수 필드 누락: {field}")
            score -= 15

    pages = render_spec.get("pages", [])
    if not pages:
        issues.append("pages 배열이 비어 있음")
        score -= 20
    else:
        page = pages[0]

        # ── 2. H1 ─────────────────────────────────────────────────────────
        h1 = page.get("title_h1", "")
        if not h1:
            issues.append("pages[0].title_h1 없음")
            score -= 10
        elif len(h1) < 10:
            issues.append(f"title_h1이 너무 짧음 ({len(h1)}자, 최소 10자)")
            score -= 5

        # ── 3. h2_list ────────────────────────────────────────────────────
        h2_list = page.get("h2_list", [])
        if len(h2_list) < _H2_MIN:
            issues.append(f"h2_list {len(h2_list)}개 (최소 {_H2_MIN}개 필요)")
            score -= 5

        # ── 4. SEO ────────────────────────────────────────────────────────
        seo = page.get("seo", {})
        for seo_field in ("title", "description", "canonical"):
            if not seo.get(seo_field):
                issues.append(f"SEO 필드 누락: seo.{seo_field}")
                score -= 5

        title = seo.get("title", "")
        if title and not (_SEO_TITLE_MIN <= len(title) <= _SEO_TITLE_MAX):
            issues.append(
                f"seo.title 길이 {len(title)}자 (기준 {_SEO_TITLE_MIN}~{_SEO_TITLE_MAX}자)"
            )
            score -= 7

        desc = seo.get("description", "")
        if desc and not (_SEO_DESC_MIN <= len(desc) <= _SEO_DESC_MAX):
            issues.append(
                f"seo.description 길이 {len(desc)}자 (기준 {_SEO_DESC_MIN}~{_SEO_DESC_MAX}자)"
            )
            score -= 5

        canonical = seo.get("canonical", "")
        if canonical and not canonical.startswith("https://"):
            issues.append("seo.canonical이 https://로 시작하지 않음")
            score -= 3

        keywords = seo.get("target_keywords", [])
        if len(keywords) < _KEYWORDS_MIN:
            issues.append(f"target_keywords {len(keywords)}개 (최소 {_KEYWORDS_MIN}개 필요)")
            score -= 3

        # ── 5. JSON-LD ────────────────────────────────────────────────────
        jsonld = page.get("jsonld", [])
        if not jsonld:
            issues.append("JSON-LD 없음")
            score -= 10
        else:
            types = [j.get("@type", "") for j in jsonld]
            if "FAQPage" not in types:
                issues.append("FAQPage JSON-LD 없음")
                score -= 5
            else:
                faq_page = next(j for j in jsonld if j.get("@type") == "FAQPage")
                entities = faq_page.get("mainEntity", [])
                if len(entities) < _FAQ_MIN:
                    issues.append(
                        f"FAQPage.mainEntity {len(entities)}개 (최소 {_FAQ_MIN}개 필요)"
                    )
                    score -= 10

        # ── 6. blocks ────────────────────────────────────────────────────
        blocks = page.get("blocks", [])
        block_types = [b.get("type") for b in blocks]

        if "QuickAnswer" not in block_types:
            issues.append("QuickAnswer 블록 없음")
            score -= 5
        else:
            qa_block = next(b for b in blocks if b.get("type") == "QuickAnswer")
            qa_text = qa_block.get("text", "")
            if not qa_text:
                issues.append("QuickAnswer.text가 비어 있음")
                score -= 5
            elif not (_QUICKANSWER_MIN <= len(qa_text) <= _QUICKANSWER_MAX):
                issues.append(
                    f"QuickAnswer.text 길이 {len(qa_text)}자 (기준 {_QUICKANSWER_MIN}~{_QUICKANSWER_MAX}자)"
                )
                score -= 5

        if "FAQ" not in block_types:
            issues.append("FAQ 블록 없음")
            score -= 5
        else:
            faq_block = next(b for b in blocks if b.get("type") == "FAQ")
            faq_items = faq_block.get("items", [])
            if len(faq_items) < _FAQ_MIN:
                issues.append(
                    f"FAQ 블록 items {len(faq_items)}개 (최소 {_FAQ_MIN}개 필요)"
                )
                score -= 15

    # ── 7. supplementary_files ────────────────────────────────────────────
    supp = render_spec.get("supplementary_files", {})

    llms_txt = supp.get("llms_txt", "")
    if not llms_txt:
        issues.append("supplementary_files.llms_txt 없음")
        score -= 10
    else:
        if len(llms_txt) < _LLMS_TXT_MIN_LEN:
            issues.append(f"llms_txt 너무 짧음 ({len(llms_txt)}자, 최소 {_LLMS_TXT_MIN_LEN}자)")
            score -= 5
        if "## 핵심 페이지" not in llms_txt:
            issues.append("llms_txt에 '## 핵심 페이지' 링크 섹션 없음 (BLOCKING) — [홈](/), [서비스 안내](/#services) 등 페이지 링크 섹션 필수")
            score -= 20
        elif not re.search(r"\[.+\]\(/.+\)", llms_txt):
            issues.append("llms_txt '## 핵심 페이지' 섹션에 내부 링크([텍스트](/path)) 없음")
            score -= 10

    llms_full = supp.get("llms_full_txt", "")
    if not llms_full:
        issues.append("supplementary_files.llms_full_txt 없음")
        score -= 15
    else:
        if "## FAQ" not in llms_full:
            issues.append("llms_full_txt에 '## FAQ' 섹션 없음 (BLOCKING) — Q: 질문\\n  A: 답변 형식으로 3개 이상 필수")
            score -= 20
        else:
            qa_count = len(re.findall(r"^\s*Q\s*:", llms_full, re.MULTILINE))
            if qa_count < 3:
                issues.append(f"llms_full_txt FAQ Q:/A: 항목 {qa_count}개 (최소 3개 필요)")
                score -= 10

    robots = supp.get("robots_rules", [])
    if not robots:
        issues.append("supplementary_files.robots_rules 없음")
        score -= 5
    else:
        bots_text = " ".join(robots)
        for bot in _REQUIRED_BOTS:
            if bot not in bots_text:
                issues.append(f"robots_rules에 {bot} Allow 없음")
                score -= 3

    score = max(0, score)

    if issues:
        logger.warning("render_spec 평가 이슈 %d개: %s", len(issues), issues[:5])
    else:
        logger.info("render_spec 평가 통과 (score=%d)", score)

    return {
        "score": score,
        "issues": issues,
        "passed": score >= threshold,
        "issue_count": len(issues),
    }
