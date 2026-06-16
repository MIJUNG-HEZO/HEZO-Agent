"""AI 가시성 점수 계산기 (0~100)"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 이슈 유형별 감점
_DEDUCTIONS = {
    # blocking
    "NO_H1": 20,
    "MULTIPLE_H1": 15,
    "NO_JSONLD": 20,
    "NO_FAQ_PAGE_JSONLD": 15,
    "NO_LLMS_TXT": 20,
    "NO_HTML": 30,
    "NO_TITLE_TAG": 15,
    "GENERATION_NOT_READY": 10,
    "UNSUPPORTED_FEATURE": 10,
    # warning
    "LOW_ALT_COVERAGE": 5,
    "NO_META_DESCRIPTION": 5,
    "NO_CANONICAL": 5,
    "NO_SITEMAP_XML": 5,
    "NO_ROBOTS_TXT": 5,
    "NO_QUICK_ANSWER": 5,
    "INSUFFICIENT_FAQ": 5,
    "MISSING_REQUIRED_SECTION": 3,
    "LAYER1_MISSING_INFO": 5,
    "LAYER1_DISTORTED_INFO": 8,
}


def calculate_ai_score(issues: list[dict]) -> int:
    """이슈 목록을 기반으로 AI 가시성 점수(0~100) 계산"""
    score = 100
    for issue in issues:
        code = issue.get("code", "")
        deduction = _DEDUCTIONS.get(code, 3)
        score -= deduction

    score = max(0, score)
    logger.info("AI 가시성 점수: %d (이슈 %d개)", score, len(issues))
    return score
