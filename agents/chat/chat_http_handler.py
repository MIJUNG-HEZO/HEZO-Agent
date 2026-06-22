"""HTTP payload handler for the HEZO chat agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bedrock_claude_adapter import Boto3BedrockClaudeInvoker, ClaudeMessage
from chat_graph import ChatGraphState, run_chat_graph
from guarded_claude_flow import GuardedClaudeReplyInput, run_guarded_claude_reply
from chat_intent_guard import (
    ChatIntent,
    ChatIntentClassifier,
    ClaudeChatIntentClassifier,
    StaticChatIntentClassifier,
)
from chat_session_start import ChatSessionStartInput, start_chat_session
from chat_state_store import (
    Boto3ChatStateStore,
    ChatMessage,
    ChatStateStore,
    InMemoryChatStateStore,
)
from chat_turn_handler import ChatTurnInput, handle_chat_turn
from p2_markdown_loader import P2MarkdownLoadInput, build_p2_markdown_ref
from s3_artifact_store import (
    ArtifactPayload,
    Boto3S3ArtifactStore,
    InMemoryS3ArtifactStore,
    S3ArtifactStore,
)


DEFAULT_CATEGORY = "landing"
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
    store = _s3_store(session_attrs)
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
    if _seed_mock_p2_markdown(session_attrs):
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
    state_store = _state_store(session_attrs)

    # 세션 복원: caller가 known_answers를 전달하지 않으면 DynamoDB 체크포인트에서 로드
    known_answers, missing_slots = _restore_session_state(
        session_id, session_attrs, state_store, slot_registry
    )

    answered_slot = str(session_attrs.get("answered_slot", ""))
    answer = session_attrs.get("answer", "")

    # 대화 히스토리 로드 (LLM 호출 전 — 현재 턴 메시지 저장 전이므로 이전 대화만 포함)
    recent_messages = state_store.load_recent_messages(session_id, limit=10)

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
            intent_classifier=_intent_classifier(session_attrs),
        )
    )
    metadata = result.to_dict()

    # LLM 어시스턴트 응답 생성 (재시도 요청이 아닐 때만)
    domain_label = str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL))
    next_question = (
        result.question_candidates[0].to_dict()["question"]
        if result.question_candidates
        else ""
    )
    reply = run_guarded_claude_reply(
        GuardedClaudeReplyInput(
            user_message=str(answer),
            system_prompt=_build_system_prompt(
                domain_label=domain_label,
                slot_registry=slot_registry,
                known_answers=result.known_answers,
                missing_slots=result.missing_slots,
                next_question=next_question,
                next_stage=result.next_stage,
                intent_guard=result.intent_guard.to_dict() if result.intent_guard else None,
            ),
            session_id=session_id,
            site_id=str(session_attrs.get("site_id", "site_001")),
            user_id=str(session_attrs.get("user_id", "user_001")),
            conversation_history=_build_conversation_history(recent_messages),
            max_tokens=512,
        ),
        claude_invoker=Boto3BedrockClaudeInvoker() if _use_aws(session_attrs) else None,
    )
    metadata["assistant_reply"] = reply.final_text
    metadata["reply_status"] = reply.status

    message_refs = _persist_chat_turn_messages(
        session_id=session_id,
        answer=answer,
        assistant_reply=reply.final_text if reply.status == "succeeded" else None,
        metadata=metadata,
        store=state_store,
    )
    metadata["message_refs"] = message_refs
    metadata["recent_messages"] = [
        _chat_message_to_dict(message)
        for message in state_store.load_recent_messages(session_id, limit=6)
    ]
    return metadata


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
            p2_source_s3_key=_optional_text(session_attrs.get("source_s3_key")),
            p2_version=_optional_text(session_attrs.get("version")) or "v001",
            p2_source_count=int(session_attrs.get("source_count", 2)),
            p2_source_grade=str(session_attrs.get("source_grade", "mid")),
            slot_registry=_slot_registry(session_attrs),
            known_answers=_dict_value(
                session_attrs.get("known_answers"),
                default={"business_name": "한빛 세무회계"},
            ),
            missing_slots=_tuple_value(
                session_attrs.get("missing_slots"),
                default=("core_services", "contact_method"),
            ),
        ),
        artifact_store=_s3_store(session_attrs),
        state_store=_state_store(session_attrs),
        seed_mock_p2_markdown=_seed_mock_p2_markdown(session_attrs),
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
    if action == "chat_turn":
        reply = metadata.get("assistant_reply")
        if reply:
            return str(reply)
        # LLM 실패 시 rule-based 폴백
        fallback = _fallback_assistant_content(metadata)
        return fallback if fallback else f"chat_turn_complete — stage: {stage}"
    if action == "session_start":
        return f"chat_session_start_complete — stage: {stage}"
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


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _s3_store(session_attrs: dict[str, Any]) -> S3ArtifactStore:
    if str(session_attrs.get("storage_mode", "memory")).lower() == "aws":
        return Boto3S3ArtifactStore()
    return InMemoryS3ArtifactStore()


def _state_store(session_attrs: dict[str, Any]) -> ChatStateStore:
    if str(session_attrs.get("storage_mode", "memory")).lower() == "aws":
        return Boto3ChatStateStore()
    return InMemoryChatStateStore()


def _persist_chat_turn_messages(
    *,
    session_id: str,
    answer: Any,
    assistant_reply: str | None,
    metadata: dict[str, Any],
    store: ChatStateStore,
) -> list[dict[str, str]]:
    if metadata.get("store_allowed") is not True:
        return []

    now = _utc_timestamp()
    refs: list[dict[str, str]] = []
    user_content = _message_content(answer)
    if user_content is not None:
        refs.append(
            _stored_ref(
                store.append_message(
                    ChatMessage(
                        session_id=session_id,
                        message_id="01_user",
                        role="user",
                        content=user_content,
                        created_at=now,
                    )
                )
            )
        )

    # LLM 응답이 있으면 우선 사용, 없으면 rule-based 폴백
    final_assistant = assistant_reply or _fallback_assistant_content(metadata)
    if final_assistant is not None:
        refs.append(
            _stored_ref(
                store.append_message(
                    ChatMessage(
                        session_id=session_id,
                        message_id="02_assistant",
                        role="assistant",
                        content=final_assistant,
                        created_at=now,
                    )
                )
            )
        )

    return refs


def _fallback_assistant_content(metadata: dict[str, Any]) -> str | None:
    """LLM 응답 실패 시 rule-based 폴백 메시지."""
    candidates = metadata.get("question_candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            return _message_content(first.get("question"))

    if metadata.get("next_stage") == "contract_compile":
        return "필수 정보가 모두 수집되어 Contract JSON 생성 단계로 이동합니다."

    intent_guard = metadata.get("intent_guard")
    if isinstance(intent_guard, dict):
        return _message_content(intent_guard.get("redirect_message"))

    return None


def _message_content(value: Any) -> str | None:
    if not isinstance(value, str):
        value = str(value)
    stripped = value.strip()
    return stripped or None


def _stored_ref(item: Any) -> dict[str, str]:
    return {"pk": str(item.pk), "sk": str(item.sk), "item_type": str(item.item_type)}


def _chat_message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {
        "session_id": message.session_id,
        "message_id": message.message_id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _use_aws(session_attrs: dict[str, Any]) -> bool:
    return str(session_attrs.get("storage_mode", "memory")).lower() == "aws"


def _restore_session_state(
    session_id: str,
    session_attrs: dict[str, Any],
    state_store: ChatStateStore,
    slot_registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """caller 제공 값 → DynamoDB 체크포인트 → 기본값 순서로 세션 상태 복원."""
    raw_answers = session_attrs.get("known_answers")
    # sessionAttributes 값은 str이므로 JSON 파싱 시도
    if isinstance(raw_answers, str) and raw_answers.strip().startswith("{"):
        import json as _json
        try:
            raw_answers = _json.loads(raw_answers)
        except Exception:
            raw_answers = None
    if isinstance(raw_answers, dict):
        missing_slots = _tuple_value(
            session_attrs.get("missing_slots"),
            default=tuple(slot_registry.keys()),
        )
        return raw_answers, missing_slots

    try:
        checkpoint = state_store.load_latest_checkpoint(session_id)
    except Exception:
        checkpoint = None

    if checkpoint is not None:
        state = checkpoint.state
        known_answers = _dict_value(state.get("known_answers"))
        missing_slots = _tuple_value(
            state.get("missing_slots", []),
            default=tuple(slot_registry.keys()),
        )
        return known_answers, missing_slots

    # 신규 세션
    return {}, tuple(slot_registry.keys())


def _build_conversation_history(messages: list[ChatMessage]) -> tuple[ClaudeMessage, ...]:
    """최근 메시지를 Claude용 대화 히스토리로 변환. 토큰 버짓 초과 시 오래된 메시지 제거."""
    MAX_CHARS = 6000  # ~1500 tokens (4 chars ≈ 1 token)
    valid = [m for m in messages if m.role in {"user", "assistant"}]

    # 토큰 버짓 역방향 적용
    selected: list[ChatMessage] = []
    total_chars = 0
    for msg in reversed(valid):
        if total_chars + len(msg.content) > MAX_CHARS:
            break
        selected.insert(0, msg)
        total_chars += len(msg.content)

    # Bedrock Converse API: user 메시지로 시작해야 함
    while selected and selected[0].role != "user":
        selected.pop(0)

    return tuple(ClaudeMessage(role=m.role, content=m.content) for m in selected)  # type: ignore[arg-type]


def _build_system_prompt(
    *,
    domain_label: str,
    slot_registry: dict[str, dict[str, Any]],
    known_answers: dict[str, Any],
    missing_slots: tuple[str, ...],
    next_question: str,
    next_stage: str,
    intent_guard: dict[str, Any] | None,
) -> str:
    """P1 어시스턴트 Claude 시스템 프롬프트 생성."""
    filled_lines = [
        f"- {slot_registry[k]['label']}: {v}"
        for k, v in known_answers.items()
        if k in slot_registry and v
    ]
    filled_summary = "\n".join(filled_lines) if filled_lines else "없음"

    missing_labels = [
        slot_registry[s]["label"]
        for s in missing_slots
        if s in slot_registry
    ]
    missing_summary = ", ".join(missing_labels) if missing_labels else "없음"

    if next_stage == "contract_compile":
        task_instruction = (
            "모든 필수 정보 수집이 완료되었습니다. "
            "사용자에게 수집 완료를 알리고 홈페이지 제작을 시작한다고 따뜻하게 안내해주세요."
        )
    elif intent_guard and intent_guard.get("intent") in {"off_topic", "ambiguous"}:
        redirect = intent_guard.get("redirect_message", "")
        task_instruction = (
            f"사용자의 답변이 주제와 맞지 않습니다. "
            f"부드럽게 원래 주제로 돌아와 달라고 안내하고 다시 질문해주세요.\n"
            f"제안 메시지: {redirect}\n"
            f"다음 질문: {next_question}"
        )
    elif next_stage == "retry_answer":
        task_instruction = f"답변을 다시 받아야 합니다. 같은 내용을 다시 질문해주세요.\n질문: {next_question}"
    else:
        task_instruction = (
            f"사용자의 답변을 1~2문장으로 자연스럽게 인정한 뒤 "
            f"다음 질문으로 이어가주세요.\n다음 질문: {next_question}"
        )

    return f"""당신은 HEZO 홈페이지 제작 어시스턴트입니다.
고객과 자연스러운 대화를 통해 홈페이지 제작에 필요한 정보를 수집합니다.

[고객 업종]
{domain_label}

[수집된 정보]
{filled_summary}

[아직 필요한 정보]
{missing_summary}

[지시사항]
{task_instruction}

규칙:
- 친근하고 전문적인 어조를 유지하세요
- 200자 이내로 간결하게 답변하세요
- 한 번에 하나의 질문만 하세요"""


def _seed_mock_p2_markdown(session_attrs: dict[str, Any]) -> bool:
    if "seed_mock_p2_markdown" in session_attrs:
        return bool(session_attrs.get("seed_mock_p2_markdown"))
    return str(session_attrs.get("storage_mode", "memory")).lower() != "aws"


def _intent_override(session_attrs: dict[str, Any]) -> ChatIntent:
    intent = str(session_attrs.get("intent", "on_topic"))
    if intent in {"on_topic", "off_topic", "ambiguous", "needs_classification"}:
        return intent  # type: ignore[return-value]
    return "on_topic"


def _intent_classifier(session_attrs: dict[str, Any]) -> ChatIntentClassifier:
    if "intent" in session_attrs:
        return StaticChatIntentClassifier(
            intent=_intent_override(session_attrs),
            reason="http_static_intent_override",
        )
    return ClaudeChatIntentClassifier(Boto3BedrockClaudeInvoker())
