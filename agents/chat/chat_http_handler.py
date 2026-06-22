"""HTTP payload handler for the HEZO chat agent."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from bedrock_claude_adapter import (
    Boto3BedrockClaudeInvoker,
    ClaudeInvocationInput,
    ClaudeMessage,
)
from bedrock_guardrails_adapter import Boto3BedrockGuardrailsClient
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

_HAIKU_MODEL_ID = "anthropic.claude-haiku-4-5-20251001"
_WIKI_BUCKET = os.environ.get("HEZO_P2_MARKDOWNS_BUCKET", "hezo-wiki")

# domain 값 → hezo-wiki S3 키 매핑 (프론트 TEMPLATE_DOMAIN의 domain 필드 기준)
_DOMAIN_WIKI_KEY: dict[str, str] = {
    # landing
    "tax-accounting":  "industries/landing/tax_accounting.md",
    "tax_accounting":  "industries/landing/tax_accounting.md",
    "medical-clinic":  "industries/landing/lifting_clinic.md",
    "lifting-clinic":  "industries/landing/lifting_clinic.md",
    "saas-product":    "industries/landing/saas_product.md",
    "pet-hospital":    "industries/landing/pet_hospital.md",
    "mind-counseling": "industries/landing/mind_counseling.md",
    "app-launch":      "industries/landing/app_launch.md",
    # blog
    "career":          "industries/blog/career.md",
    "photo-diary":     "industries/blog/photo_diary.md",
    "wellness":        "industries/blog/wellness.md",
    "food-travel":     "industries/blog/photo_diary.md",  # 최근접 파일
    # store
    "book-curation":   "industries/store/book_curation.md",
    "booking-service": "industries/store/booking_service.md",
    "beauty-salon":    "industries/store/booking_service.md",  # 최근접
    "jewelry":         "industries/store/jewelry.md",
    "wine-market":     "industries/store/wine_market.md",
}

# template_id → domain key (domain 직접 매핑 실패 시 fallback)
_TEMPLATE_WIKI_DOMAIN: dict[str, str] = {
    "01-clinic-landing":   "medical-clinic",
    "05-lifting-clinic":   "lifting-clinic",
    "03-saas-product":     "saas-product",
    "13-tax-accounting":   "tax-accounting",
    "17-career-notebook":  "career",
    "05-booking-service":  "booking-service",
    "06-oops-nail":        "beauty-salon",
    "10-wine-market":      "wine-market",
}


def _wiki_s3_key(domain: str, template_id: str) -> str | None:
    """domain 또는 template_id로 wiki MD S3 key 결정."""
    if domain in _DOMAIN_WIKI_KEY:
        return _DOMAIN_WIKI_KEY[domain]
    # template_id fallback
    fallback_domain = _TEMPLATE_WIKI_DOMAIN.get(template_id)
    if fallback_domain:
        return _DOMAIN_WIKI_KEY.get(fallback_domain)
    # 언더스코어/하이픈 정규화 후 재시도
    norm = domain.replace("-", "_").lower()
    for key, s3_key in _DOMAIN_WIKI_KEY.items():
        if norm == key.replace("-", "_"):
            return s3_key
    return None


def _load_wiki_content(domain: str, template_id: str) -> str:
    """
    hezo-wiki S3에서 도메인 wiki MD 읽기.
    - frontmatter (--- ... ---) 제거
    - 시스템 프롬프트 토큰 절약: 최대 1500자
    """
    s3_key = _wiki_s3_key(domain, template_id)
    if not s3_key:
        return ""
    try:
        import boto3  # noqa: PLC0415
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
        obj = s3.get_object(Bucket=_WIKI_BUCKET, Key=s3_key)
        raw = obj["Body"].read().decode("utf-8")
        # YAML frontmatter 제거 (--- ... --- 사이 블록)
        content = re.sub(r"^---[\s\S]*?---\s*\n", "", raw, count=1)
        return content[:1500]
    except Exception:
        return ""

# 그룹 리더 슬롯 → 동반 추출 슬롯: {slot_key: extraction_hint}
_SLOT_COMPANION_MAP: dict[str, dict[str, str]] = {
    "business_name": {
        "business_region": "지역 (시·구·동 단위, 예: 서울 강남)",
    },
    "core_services": {
        "target_audience": "주요 고객층 (예: 30-40대 직장인, 소상공인, null이면 생략)",
    },
    "phone": {
        "kakao_channel": "카카오 채널 ID (@로 시작, 없으면 null)",
    },
}


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
    use_aws = _use_aws(session_attrs)

    # 세션 복원: caller가 known_answers를 전달하지 않으면 DynamoDB 체크포인트에서 로드
    known_answers, missing_slots = _restore_session_state(
        session_id, session_attrs, state_store, slot_registry
    )

    answered_slot = str(session_attrs.get("answered_slot", ""))
    answer = session_attrs.get("answer", "")
    domain = str(session_attrs.get("domain", DEFAULT_DOMAIN))
    template_id = str(session_attrs.get("selected_template", DEFAULT_TEMPLATE))

    # ── P2 wiki 로드 (템플릿 선택 직후 첫 턴부터 도메인 지식 주입) ──────────
    wiki_content = _load_wiki_content(domain, template_id) if use_aws else ""

    # 대화 히스토리 로드 (LLM 호출 전 — 현재 턴 메시지 저장 전이므로 이전 대화만 포함)
    recent_messages = state_store.load_recent_messages(session_id, limit=10)

    result = handle_chat_turn(
        ChatTurnInput(
            session_id=session_id,
            site_id=str(session_attrs.get("site_id", "site_001")),
            user_id=str(session_attrs.get("user_id", "user_001")),
            domain=domain,
            domain_label=str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL)),
            slot_registry=slot_registry,
            known_answers=known_answers,
            missing_slots=missing_slots,
            answered_slot=answered_slot,
            answer=answer,
            p1_markdown_review_status=str(
                session_attrs.get("p1_markdown_review_status", "passed")
            ),
            p2_markdown_usable_for_questions=bool(wiki_content),
            p2_knowledge_summary="wiki_loaded" if wiki_content else "",
            intent_classifier=_intent_classifier(session_attrs),
        )
    )
    metadata = result.to_dict()

    # ── 3-Turn 루프 방지: ambiguous 거부만 강제 수락 ────────────────────────
    # off_topic은 intent classifier가 명백한 거부 이유 제공 → 사용자에게 이유 안내 후 재질문
    # ambiguous(단순 "응", 숫자만 등)는 intent 분류가 불확실하므로 force-accept
    _intent = (result.intent_guard.intent if result.intent_guard else None)
    _force_accepted = (
        result.turn_status == "answer_rejected"  # ambiguous 경로만 (off_topic_rejected 제외)
        and _intent == "ambiguous"
        and bool(answered_slot)
        and answered_slot in slot_registry
        and bool(answer)
        and len(str(answer).strip()) > 4  # 너무 짧은 입력은 제외
    )
    if _force_accepted:
        _forced_known = {**result.known_answers, answered_slot: str(answer).strip()}
        _forced_missing = tuple(s for s in result.missing_slots if s != answered_slot)
        metadata.update({
            "known_answers": _forced_known,
            "missing_slots": list(_forced_missing),
            "next_stage": "contract_compile" if not _forced_missing else "proactive_questioning",
            "turn_status": "ready_for_contract_compile" if not _forced_missing else "answer_accepted",
            "intent_guard": None,
            "store_allowed": True,
        })
    # ─────────────────────────────────────────────────────────────────────────

    # ── 동반 슬롯 추출 (3-Turn Progressive) ─────────────────────────────────
    # 슬롯 리더 답변이 accepted 됐을 때, 같은 답변에서 동반 슬롯 값을 Haiku로 추출
    final_known = _forced_known if _force_accepted else result.known_answers
    final_missing = _forced_missing if _force_accepted else result.missing_slots

    companions = _SLOT_COMPANION_MAP.get(answered_slot, {})
    _effective_turn_status = metadata.get("turn_status", result.turn_status)
    if (
        companions
        and _effective_turn_status in ("answer_accepted", "ready_for_contract_compile")
        and use_aws
        and answer
    ):
        extracted = _extract_companion_slots(
            answer=str(answer),
            companions=companions,
        )
        if extracted:
            final_known = {**final_known, **extracted}
            final_missing = tuple(s for s in final_missing if s not in extracted)
            metadata["known_answers"] = final_known
            metadata["missing_slots"] = list(final_missing)
            if not final_missing:
                metadata["next_stage"] = "contract_compile"
                metadata["turn_status"] = "ready_for_contract_compile"
    # ─────────────────────────────────────────────────────────────────────────

    # LLM 어시스턴트 응답 생성
    domain_label = str(session_attrs.get("domain_label", DEFAULT_DOMAIN_LABEL))
    candidates = metadata.get("question_candidates") or []
    next_question = candidates[0]["question"] if candidates else ""

    reply = run_guarded_claude_reply(
        GuardedClaudeReplyInput(
            user_message=str(answer),
            system_prompt=_build_system_prompt(
                domain_label=domain_label,
                slot_registry=slot_registry,
                known_answers=final_known,
                missing_slots=final_missing,
                next_question=next_question,
                next_stage=str(metadata.get("next_stage", result.next_stage)),
                intent_guard=result.intent_guard.to_dict() if result.intent_guard else None,
                wiki_content=wiki_content,
            ),
            session_id=session_id,
            site_id=str(session_attrs.get("site_id", "site_001")),
            user_id=str(session_attrs.get("user_id", "user_001")),
            conversation_history=_build_conversation_history(recent_messages),
            max_tokens=512,
        ),
        claude_invoker=Boto3BedrockClaudeInvoker() if use_aws else None,
        guardrails_client=Boto3BedrockGuardrailsClient() if use_aws else None,
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
            "label": "업체명 · 지역",
            "required": True,
            "question_hint": (
                "업체 이름과 운영 지역을 함께 알려주세요. "
                "(예: '서울 강남에서 해조세무회계를 운영합니다')"
            ),
        },
        "core_services": {
            "label": "핵심 서비스",
            "required": True,
            "question_hint": (
                "주력 서비스나 상품을 알려주세요. "
                "주요 고객층도 함께 말씀해 주시면 맞춤 구성이 가능해요."
            ),
        },
        "phone": {
            "label": "연락처",
            "required": True,
            "question_hint": (
                "전화번호와 카카오 채널 ID를 알려주세요. "
                "카카오채널이 없으시면 '없음'이라고 해주세요."
            ),
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


def _extract_companion_slots(
    answer: str,
    companions: dict[str, str],
) -> dict[str, Any]:
    """Bedrock Haiku로 동반 슬롯 값을 추출. 실패 시 빈 dict 반환."""
    try:
        invoker = Boto3BedrockClaudeInvoker()
        fields = "\n".join(f'- "{k}": {desc}' for k, desc in companions.items())
        example = json.dumps(
            {k: f"<{desc.split('(')[0].strip()}>" for k, desc in companions.items()},
            ensure_ascii=False,
        )
        result = invoker.invoke(
            ClaudeInvocationInput(
                use_case="slot_extraction",
                system_prompt="텍스트에서 정보를 추출하는 JSON-only 추출기. 없는 값은 null.",
                messages=(
                    ClaudeMessage(
                        role="user",
                        content=(
                            f"다음 텍스트에서 추출하세요:\n{fields}\n\n"
                            f"텍스트: {answer}\n\n"
                            f"JSON만 반환 (예: {example})"
                        ),
                    ),
                ),
                model_id=_HAIKU_MODEL_ID,
                max_tokens=120,
            )
        )
        if result.status != "succeeded":
            return {}
        raw = result.text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json\n").strip()
        data = json.loads(raw)
        return {
            k: str(v).strip()
            for k, v in data.items()
            if v and str(v).strip() not in ("null", "None", "없음", "", "모름")
        }
    except Exception:
        return {}


def _build_system_prompt(
    *,
    domain_label: str,
    slot_registry: dict[str, dict[str, Any]],
    known_answers: dict[str, Any],
    missing_slots: tuple[str, ...],
    next_question: str,
    next_stage: str,
    intent_guard: dict[str, Any] | None,
    wiki_content: str = "",
) -> str:
    """P1 어시스턴트 Claude 시스템 프롬프트 생성 (3-Turn Progressive)."""
    _COMPANION_LABELS = {
        "business_region": "지역",
        "target_audience": "주요 고객",
        "kakao_channel": "카카오채널",
    }
    ALL_LABELS = {**{k: v["label"] for k, v in slot_registry.items()}, **_COMPANION_LABELS}

    filled_lines = [
        f"- {ALL_LABELS.get(k, k)}: {v}"
        for k, v in known_answers.items()
        if v
    ]
    filled_summary = "\n".join(filled_lines) if filled_lines else "없음"

    missing_labels = [ALL_LABELS.get(s, s) for s in missing_slots if s in slot_registry]
    missing_summary = ", ".join(missing_labels) if missing_labels else "없음"

    if next_stage == "contract_compile":
        task_instruction = (
            "모든 필수 정보 수집이 완료되었습니다. "
            "사용자에게 감사 인사와 함께 홈페이지 제작을 곧 시작한다고 따뜻하게 안내해주세요."
        )
    elif intent_guard and intent_guard.get("intent") in {"off_topic", "ambiguous"}:
        intent_type = intent_guard.get("intent", "")
        redirect = intent_guard.get("redirect_message", "")
        if intent_type == "off_topic":
            reason_prefix = "말씀해 주신 내용이 현재 질문과 맞지 않는 것 같아요."
        else:  # ambiguous
            reason_prefix = "말씀해 주신 내용이 너무 짧거나 모호해서 홈페이지에 반영하기 어렵습니다."
        task_instruction = (
            f"먼저 아래 [거부 이유]를 사용자에게 명확하게 전달한 뒤, "
            f"[다음 질문]을 다시 물어보세요.\n"
            f"[거부 이유]: {reason_prefix}"
            + (f" {redirect}" if redirect else "")
            + f"\n[다음 질문]: {next_question}"
        )
    elif next_stage == "retry_answer":
        task_instruction = f"답변을 다시 받아야 합니다. 같은 내용을 다시 질문해주세요.\n질문: {next_question}"
    else:
        task_instruction = (
            f"사용자의 답변을 1문장으로 자연스럽게 인정한 뒤, "
            f"아래 [다음 질문]을 문자 그대로 전달하세요.\n"
            f"[다음 질문]: {next_question}"
        )

    wiki_section = (
        f"\n[{domain_label} 도메인 지식 — P2 wiki]\n{wiki_content}\n"
        if wiki_content
        else ""
    )

    return f"""당신은 HEZO 홈페이지 제작 어시스턴트입니다.
총 3번의 대화로 홈페이지 제작에 필요한 정보를 수집합니다.

[고객 업종]
{domain_label}
{wiki_section}
[수집된 정보]
{filled_summary}

[아직 필요한 정보]
{missing_summary}

[지시사항]
{task_instruction}

절대 규칙:
1. [다음 질문] 문장을 수정하거나 도메인 예시를 추가하지 마세요. 그대로 전달하세요.
2. 200자 이내로 간결하게 답변하세요.
3. [수집된 정보]에 있는 항목은 절대 다시 묻지 마세요.
4. [아직 필요한 정보]가 "없음"이면 완료 안내만 하고 추가 질문하지 마세요."""


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
