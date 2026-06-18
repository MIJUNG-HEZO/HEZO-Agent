"""HTTP payload handler for the HEZO chat agent."""

from __future__ import annotations

from typing import Any

from chat_graph import ChatGraphState, run_chat_graph
from chat_session_start import ChatSessionStartInput, start_chat_session
from chat_turn_handler import ChatTurnInput, handle_chat_turn
from p2_markdown_loader import P2MarkdownLoadInput, build_p2_markdown_ref
from s3_artifact_store import ArtifactPayload, InMemoryS3ArtifactStore


DEFAULT_CATEGORY = "services"
DEFAULT_DOMAIN = "tax_accounting"
DEFAULT_DOMAIN_LABEL = "세무/회계"
DEFAULT_TEMPLATE = "landing/13-tax-accounting"


def handle_agentcore_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Handle an AgentCore-style payload and return a normalized response."""

    session_attrs = _dict_value(payload.get("sessionAttributes"))
    action = str(session_attrs.get("action", "graph_smoke")).strip() or "graph_smoke"
    session_id = str(payload.get("sessionId") or session_attrs.get("session_id") or "session_001")

    if action == "session_start":
        metadata = _run_session_start(session_id, session_attrs)
    elif action == "chat_turn":
        metadata = _run_chat_turn(session_id, session_attrs)
    elif action == "graph_smoke":
        metadata = _run_graph_smoke(session_id, session_attrs)
    else:
        raise ValueError("action_invalid")

    return {
        "output": _build_output_text(action, metadata),
        "sessionState": {
            "sessionId": session_id,
            "action": action,
            "stage": _metadata_stage(metadata),
        },
        "metadata": metadata,
    }


def _run_session_start(session_id: str, session_attrs: dict[str, Any]) -> dict[str, Any]:
    request_input = _build_session_start_input(session_id, session_attrs)
    store = InMemoryS3ArtifactStore()
    load_input = P2MarkdownLoadInput(
        category=request_input.category,
        domain=request_input.domain,
        expected_domain=request_input.domain,
        slot_registry=request_input.slot_registry,
        source_s3_key=request_input.source_s3_key,
        version=request_input.version,
        source_count=request_input.source_count,
        source_grade=request_input.source_grade,
    )
    ref = build_p2_markdown_ref(load_input)
    store.put_artifact(
        ArtifactPayload(
            ref=ref,
            body=_sample_p2_markdown_content(
                domain=request_input.domain,
                category=request_input.category,
                domain_label=request_input.domain_label,
            ),
        )
    )
    return start_chat_session(request_input, store).to_dict()


def _run_chat_turn(session_id: str, session_attrs: dict[str, Any]) -> dict[str, Any]:
    slot_registry = _slot_registry(session_attrs)
    known_answers = _dict_value(
        session_attrs.get("known_answers"),
        default={"business_name": "한빛 세무회계"},
    )
    missing_slots = _tuple_value(
        session_attrs.get("missing_slots"),
        default=("core_services", "contact_method"),
    )
    answered_slot = str(session_attrs.get("answered_slot", "core_services"))
    answer = session_attrs.get("answer", "기장 대리, 종합소득세 신고, 법인세 신고")
    result = handle_chat_turn(
        ChatTurnInput(
            session_id=session_id,
            site_id=str(session_attrs.get("site_id", "site_001")),
            user_id=str(session_attrs.get("user_id", "user_001")),
            domain=str(session_attrs.get("domain", DEFAULT_DOMAIN)),
            domain_label=str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL)),
            slot_registry=slot_registry,
            known_answers=known_answers,
            missing_slots=missing_slots,
            answered_slot=answered_slot,
            answer=answer,
            p1_markdown_review_status=str(
                session_attrs.get("p1_markdown_review_status", "passed")
            ),
            p2_markdown_usable_for_questions=bool(
                session_attrs.get("p2_markdown_usable_for_questions", True)
            ),
            p2_knowledge_summary=str(
                session_attrs.get("p2_knowledge_summary", "핵심 서비스 범위, 상담 전환 정보")
            ),
        )
    )
    return result.to_dict()


def _run_graph_smoke(session_id: str, session_attrs: dict[str, Any]) -> dict[str, Any]:
    result = run_chat_graph(
        ChatGraphState(
            session_id=session_id,
            site_id=str(session_attrs.get("site_id", "site_001")),
            user_id=str(session_attrs.get("user_id", "user_001")),
            stage="domain_selection",
            category=str(session_attrs.get("category", DEFAULT_CATEGORY)),
            domain=str(session_attrs.get("domain", DEFAULT_DOMAIN)),
            domain_label=str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL)),
            selected_template=str(session_attrs.get("selected_template", DEFAULT_TEMPLATE)),
            slot_registry=_slot_registry(session_attrs),
            known_answers=_dict_value(
                session_attrs.get("known_answers"),
                default={"business_name": "한빛 세무회계"},
            ),
            missing_slots=_tuple_value(
                session_attrs.get("missing_slots"),
                default=("core_services", "contact_method"),
            ),
        )
    )
    return result.to_dict()


def _build_session_start_input(
    session_id: str,
    session_attrs: dict[str, Any],
) -> ChatSessionStartInput:
    category = str(session_attrs.get("category", DEFAULT_CATEGORY))
    domain = str(session_attrs.get("domain", DEFAULT_DOMAIN))
    return ChatSessionStartInput(
        session_id=session_id,
        site_id=str(session_attrs.get("site_id", "site_001")),
        user_id=str(session_attrs.get("user_id", "user_001")),
        category=category,
        domain=domain,
        domain_label=str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL)),
        selected_template=str(session_attrs.get("selected_template", DEFAULT_TEMPLATE)),
        slot_registry=_slot_registry(session_attrs),
        known_answers=_dict_value(
            session_attrs.get("known_answers"),
            default={"business_name": "한빛 세무회계"},
        ),
        missing_slots=_tuple_value(
            session_attrs.get("missing_slots"),
            default=("core_services", "contact_method"),
        ),
        source_s3_key=str(
            session_attrs.get("source_s3_key", f"industries/{category}/{domain}.md")
        ),
        version=str(session_attrs.get("version", "v001")),
        source_count=int(session_attrs.get("source_count", 2)),
        source_grade=str(session_attrs.get("source_grade", "mid")),
    )


def _slot_registry(session_attrs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = session_attrs.get("slot_registry")
    if isinstance(registry, dict) and registry:
        return registry
    return {
        "business_name": {
            "label": "업체명",
            "required": True,
            "question_hint": "사무소명은 무엇인가요?",
        },
        "core_services": {
            "label": "핵심 서비스",
            "required": True,
            "question_hint": "핵심 세무 서비스는 무엇인가요?",
        },
        "contact_method": {
            "label": "상담 방식",
            "required": True,
            "question_hint": "상담 문의는 어떤 방식으로 받나요?",
        },
    }


def _sample_p2_markdown_content(domain: str, category: str, domain_label: str) -> str:
    return "\n".join(
        [
            "---",
            f"domain: {domain}",
            f"category: {category}",
            "template_no: 13",
            f"label: {domain_label}",
            "confidence: 0.82",
            "volatility: low",
            "last_updated: 2026-06-18",
            "source_urls:",
            "  - https://example.com/source-1",
            "  - https://example.com/source-2",
            "---",
            "",
            f"# {domain_label} 도메인 지식",
            "",
            "## 1. 핵심 서비스 범위 [S1]",
            "세무/회계 홈페이지는 기장, 세무 신고, 상담 방식, 신뢰 요소를 명확히 전달해야 합니다.",
            "",
            "## 2. 상담 전환 정보 [S2]",
            "문의 방식과 상담 가능 시간을 쉽게 확인할 수 있어야 합니다.",
            "",
            "## 출처",
            "- [S1] 세무 서비스 안내 자료",
            "- [S2] 세무사무소 랜딩 페이지 공통 항목",
        ]
    )


def _build_output_text(action: str, metadata: dict[str, Any]) -> str:
    stage = _metadata_stage(metadata)
    if action == "session_start":
        return f"chat_session_start_complete — stage: {stage}"
    if action == "chat_turn":
        return f"chat_turn_complete — stage: {stage}"
    return f"chat_graph_smoke_complete — stage: {stage}"


def _metadata_stage(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("next_stage")
        or metadata.get("stage")
        or metadata.get("session_start_status")
        or metadata.get("turn_status")
        or "unknown"
    )


def _dict_value(value: Any, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {} if default is None else dict(default)


def _tuple_value(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return default
