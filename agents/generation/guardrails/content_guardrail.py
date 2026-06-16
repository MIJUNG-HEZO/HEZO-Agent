"""콘텐츠 가드레일 — 필수 항목 누락 및 금지 콘텐츠 차단"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── 금지 키워드 ────────────────────────────────────────────────────────────────
# 의료/건강 — 의료법·약사법상 허위·과장 광고 표현
_BLOCKED_MEDICAL = [
    "완치 보장", "100% 완치", "부작용 없음", "부작용이 없", "기적의 치료",
    "즉시 완치", "즉각 효과", "무조건 낫는",
]

# 법률/세무 — 변호사법·세무사법상 승소·절세 보장 표현
_BLOCKED_LEGAL = [
    "승소 보장", "무조건 이겨", "탈세 도움", "세금 0원 보장",
    "100% 승소", "무죄 보장", "합법적 탈세",
]

# 일반 허위·과장 광고
_BLOCKED_GENERAL = [
    "허위광고", "과장광고", "불법", "사기",
    "무조건 최저가", "세계 최고", "국내 유일",
    "돈 돌려드림", "원금 보장", "수익 보장",
    "투자 보장", "손실 없음",
]

_BLOCKED_KEYWORDS: list[str] = _BLOCKED_MEDICAL + _BLOCKED_LEGAL + _BLOCKED_GENERAL

# ── 형식 패턴 ──────────────────────────────────────────────────────────────────
_PHONE_RE = re.compile(r"^0\d{1,2}-\d{3,4}-\d{4}$")

_REQUIRED_TOP_FIELDS = ("site_id", "pages")
_REQUIRED_PAGE_FIELDS = ("title_h1", "seo", "blocks")


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
    for field in _REQUIRED_TOP_FIELDS:
        if not render_spec.get(field):
            raise GuardrailViolation("MISSING_REQUIRED_FIELD", f"render_spec.{field} 없음")

    # 2. 페이지별 필수 필드
    for i, page in enumerate(render_spec.get("pages", [])):
        for field in _REQUIRED_PAGE_FIELDS:
            if not page.get(field):
                raise GuardrailViolation(
                    "MISSING_PAGE_FIELD",
                    f"pages[{i}].{field} 없음",
                )

        # 3. QuickAnswer 빈 문자열 차단
        for block in page.get("blocks", []):
            if block.get("type") == "QuickAnswer":
                if not block.get("text", "").strip():
                    raise GuardrailViolation(
                        "EMPTY_QUICKANSWER",
                        "QuickAnswer.text가 비어 있음",
                    )

        # 4. canonical/og URL https:// 시작 여부
        seo = page.get("seo", {})
        canonical = seo.get("canonical", "")
        if canonical and not canonical.startswith("https://"):
            raise GuardrailViolation(
                "INVALID_URL_SCHEME",
                f"seo.canonical이 https://로 시작하지 않음: {canonical!r}",
            )
        og_url = seo.get("og", {}).get("url", "")
        if og_url and not og_url.startswith("https://"):
            raise GuardrailViolation(
                "INVALID_URL_SCHEME",
                f"seo.og.url이 https://로 시작하지 않음: {og_url!r}",
            )

    # 5. Contact 블록 전화번호 형식 검사
    for page in render_spec.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") == "Contact":
                phone = block.get("phone", "")
                if phone and not _PHONE_RE.match(phone):
                    raise GuardrailViolation(
                        "INVALID_PHONE_FORMAT",
                        f"Contact.phone 형식 오류: {phone!r} (기대: 010-XXXX-XXXX)",
                    )

    # 6. 금지 키워드 검사 (전체 텍스트 스캔)
    spec_text = str(render_spec)
    spec_text_lower = spec_text.lower()
    for kw in _BLOCKED_KEYWORDS:
        if kw.lower() in spec_text_lower:
            raise GuardrailViolation("BLOCKED_KEYWORD", f"금지 키워드 포함: {kw!r}")

    logger.info("가드레일 통과 — site_id=%s", render_spec.get("site_id"))
