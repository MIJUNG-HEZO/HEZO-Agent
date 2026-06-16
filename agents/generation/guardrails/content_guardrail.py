"""콘텐츠 가드레일 — 필수 항목 누락 및 금지 콘텐츠 차단"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_BLOCKED_KEYWORDS = [
    "허위광고", "과장광고", "불법", "사기",
]

_REQUIRED_PAGES_FIELDS = ["title_h1", "seo", "blocks"]


class GuardrailViolation(ValueError):
    """가드레일 위반 — 에이전트가 생성을 중단해야 할 때 발생"""
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def check_guardrails(render_spec: dict[str, Any]) -> None:
    """
    render_spec에 대해 가드레일 검사 실행.
    위반 시 GuardrailViolation 발생.
    통과 시 None 반환.
    """
    # 1. 필수 최상위 필드
    for field in ("site_id", "pages"):
        if not render_spec.get(field):
            raise GuardrailViolation("MISSING_REQUIRED_FIELD", f"render_spec.{field} 없음")

    # 2. 페이지별 필수 필드
    for i, page in enumerate(render_spec.get("pages", [])):
        for field in _REQUIRED_PAGES_FIELDS:
            if not page.get(field):
                raise GuardrailViolation(
                    "MISSING_PAGE_FIELD",
                    f"pages[{i}].{field} 없음",
                )

    # 3. 금지 키워드 검사 (전체 텍스트 스캔)
    spec_text = str(render_spec).lower()
    for kw in _BLOCKED_KEYWORDS:
        if kw in spec_text:
            raise GuardrailViolation("BLOCKED_KEYWORD", f"금지 키워드 포함: {kw!r}")

    logger.info("가드레일 통과 - site_id=%s", render_spec.get("site_id"))
