"""render_spec 품질 평가기 — jsonschema 검증 + AI 가시성 점수 계산"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent.parent / "render_spec_schema.json"
_schema: dict | None = None


def _load_schema() -> dict:
    global _schema
    if _schema is None:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            _schema = json.load(f)
    return _schema


def evaluate_render_spec(render_spec: dict[str, Any]) -> dict[str, Any]:
    """
    render_spec을 평가하고 결과 반환.
    반환: {score: int(0~100), issues: list[str], passed: bool}
    """
    issues: list[str] = []
    score = 100

    # 1. 필수 최상위 필드
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

        # H1
        if not page.get("title_h1"):
            issues.append("pages[0].title_h1 없음")
            score -= 10

        # SEO
        seo = page.get("seo", {})
        for seo_field in ("title", "description", "canonical"):
            if not seo.get(seo_field):
                issues.append(f"SEO 필드 누락: seo.{seo_field}")
                score -= 5

        # JSON-LD
        jsonld = page.get("jsonld", [])
        if not jsonld:
            issues.append("JSON-LD 없음")
            score -= 10
        else:
            types = [j.get("@type", "") for j in jsonld]
            if "FAQPage" not in types:
                issues.append("FAQPage JSON-LD 없음")
                score -= 5

        # blocks
        blocks = page.get("blocks", [])
        block_types = [b.get("type") for b in blocks]
        if "QuickAnswer" not in block_types:
            issues.append("QuickAnswer 블록 없음")
            score -= 5
        if "FAQ" not in block_types:
            issues.append("FAQ 블록 없음")
            score -= 5

    # supplementary_files
    supp = render_spec.get("supplementary_files", {})
    if not supp.get("llms_txt"):
        issues.append("supplementary_files.llms_txt 없음")
        score -= 10
    if not supp.get("robots_rules"):
        issues.append("supplementary_files.robots_rules 없음")
        score -= 5

    robots = supp.get("robots_rules", [])
    bots = " ".join(robots)
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        if bot not in bots:
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
        "passed": score >= 50,
        "issue_count": len(issues),
    }
