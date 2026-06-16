"""Layer 2: 요구사항 정합성 검증 — 결정론적 Python 규칙 (LLM 불사용)"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UNSUPPORTED_FEATURES = {"live_chat", "payment_gateway", "member_login", "booking_engine"}


def check_layer2(contract: dict, render_spec: dict) -> list[dict]:
    """
    Contract의 요구사항과 render_spec의 정합성을 결정론적으로 검증.
    반환: 이슈 목록 [{level, code, detail}]
    """
    issues: list[dict] = []

    # 템플릿 ID 일치 확인
    contract_template = contract.get("template", {}).get("template_id", "")
    spec_template = render_spec.get("template_id", "")
    if contract_template and spec_template and contract_template != spec_template:
        issues.append({
            "level": "warning",
            "code": "TEMPLATE_ID_MISMATCH",
            "detail": f"template_id 불일치: contract={contract_template}, render_spec={spec_template}",
        })

    # 요청된 섹션 존재 여부
    required_sections = contract.get("slots", {}).get("required_sections", [])
    pages = render_spec.get("pages", [])
    if pages:
        block_types = {b.get("type", "") for b in pages[0].get("blocks", [])}
        for section in required_sections:
            section_lower = section.lower()
            matched = any(section_lower in bt.lower() for bt in block_types)
            if not matched:
                issues.append({
                    "level": "warning",
                    "code": "MISSING_REQUIRED_SECTION",
                    "detail": f"요청된 섹션 없음: {section}",
                })

    # 미지원 기능 체크
    features = contract.get("slots", {}).get("features", [])
    for feature in features:
        if feature in _UNSUPPORTED_FEATURES:
            issues.append({
                "level": "blocking",
                "code": "UNSUPPORTED_FEATURE",
                "detail": f"미지원 기능 요청: {feature}",
            })

    # generation_ready 게이트 확인
    if not contract.get("gates", {}).get("generation_ready", True):
        issues.append({
            "level": "blocking",
            "code": "GENERATION_NOT_READY",
            "detail": "contract.gates.generation_ready = false",
        })

    return issues
