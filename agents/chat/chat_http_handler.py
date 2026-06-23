"""HTTP payload handler for the HEZO chat agent."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("hezo.chat")

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
from chat_p2_supplement import try_submit_p2_supplement
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
from template_slot_registries import (
    ALL_COMPANION_LABELS,
    get_companion_map,
    get_slot_registry,
)


DEFAULT_CATEGORY = "landing"
DEFAULT_DOMAIN = "tax_accounting"
DEFAULT_DOMAIN_LABEL = "세무/회계"
DEFAULT_TEMPLATE = "landing/13-tax-accounting"

_HAIKU_MODEL_ID = os.environ.get(
    "HEZO_BEDROCK_HAIKU_MODEL_ID",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
)
_WIKI_BUCKET = os.environ.get("HEZO_P2_MARKDOWNS_BUCKET", "hezo-wiki")
_ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "hezo-artifacts")

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

# _SLOT_COMPANION_MAP은 template_slot_registries.get_companion_map()으로 대체됨


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

    companions = get_companion_map(template_id).get(answered_slot, {})
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
                template_id=template_id,
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

    logger.info(
        "chat_turn_result session=%s answered_slot=%s intent=%s "
        "turn_status=%s next_stage=%s missing_slots=%s force_accepted=%s",
        session_id,
        answered_slot,
        (result.intent_guard.intent if result.intent_guard else "none"),
        metadata.get("turn_status"),
        metadata.get("next_stage"),
        metadata.get("missing_slots"),
        _force_accepted,
    )

    # 슬롯 수집 완료 → 3종 S3 저장 (contract_final, 채팅 transcript, P2 보강 A)
    if metadata.get("next_stage") == "contract_compile" and use_aws:
        _site_id = str(session_attrs.get("site_id", ""))
        _user_id = str(session_attrs.get("user_id", ""))
        _category = str(session_attrs.get("category", "landing"))
        _domain_label = str(session_attrs.get("domain_label", ""))
        _template_id = str(session_attrs.get("selected_template", ""))
        _known = dict(metadata.get("known_answers", {}))

        # ✅ 모든 slot의 companion을 수집해서 _known에 추가 (contract_final에 포함)
        _companion_map = get_companion_map(_template_id)
        for slot_key, companion_fields in _companion_map.items():
            slot_value = _known.get(slot_key, "")
            if slot_value:  # 해당 slot이 채워졌을 때만 companion 추출
                extracted = _extract_companion_slots(
                    answer=str(slot_value),
                    companions=companion_fields,
                )
                _known.update(extracted)  # companion 결과를 _known에 병합

        _save_contract_final(
            site_id=_site_id,
            user_id=_user_id,
            domain=domain,
            domain_label=_domain_label,
            template_id=_template_id,
            category=_category,
            slot_registry=slot_registry,
            known_answers=_known,
        )

        # 채팅 전체 대화를 MD로 hezo-chat에 저장
        all_messages = state_store.load_recent_messages(session_id, limit=30)
        _save_chat_transcript(
            site_id=_site_id,
            session_id=session_id,
            domain=domain,
            domain_label=_domain_label,
            template_id=_template_id,
            category=_category,
            known_answers=_known,
            messages=list(all_messages),
        )

        # P2 wiki 보강 A: 룰셋 게이트 통과 시 staging 저장
        try_submit_p2_supplement(
            site_id=_site_id,
            domain=domain,
            domain_label=_domain_label,
            category=_category,
            known_answers=_known,
            template_id=_template_id,
        )

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
    selected_template = str(session_attrs.get("selected_template", ""))
    return get_slot_registry(selected_template)


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


def _save_contract_final(
    *,
    site_id: str,
    user_id: str,
    domain: str,
    domain_label: str,
    template_id: str,
    category: str,
    slot_registry: dict[str, dict[str, Any]],
    known_answers: dict[str, Any],
) -> None:
    """슬롯 수집 완료 후 contract_final.json을 hezo-artifacts S3에 저장한다."""
    if not site_id:
        logger.warning("contract_final 저장 건너뜀: site_id 없음")
        return

    slot_status = {}
    slots = {}
    evidence = {}
    for key, meta in slot_registry.items():
        val = known_answers.get(key)
        filled = bool(val) if not isinstance(val, str) else bool(val.strip())
        slots[key] = val if filled else None
        slot_status[key] = "filled" if filled else "empty"
        if filled:
            evidence[key] = {"source": "user", "confirmed": True}

    # companion 슬롯(slot_registry에 없는 것)도 포함
    for key, val in known_answers.items():
        if key not in slots:
            slots[key] = val
            slot_status[key] = "filled" if val else "empty"
            if val:
                evidence[key] = {"source": "user", "confirmed": True}

    # generation_ready = P4 생성 진입 게이트. 필수 슬롯이 모두 채워졌을 때만 true.
    # (preview_ready는 P3 프리뷰 영역이라 P1 contract에서 관리하지 않음)
    missing_required = [
        key
        for key, meta in slot_registry.items()
        if meta.get("required", True) and slot_status.get(key) != "filled"
    ]
    generation_ready = not missing_required

    contract = {
        "schema_version": "1.0.0",
        "ids": {"site_id": site_id, "user_id": user_id},
        "template": {"template_id": template_id, "template_category": category},
        "slots": slots,
        "slot_status": slot_status,
        "evidence": evidence,
        "gates": {
            "generation_ready": generation_ready,
            "missing_required": missing_required,
        },
        "meta": {"domain": domain, "domain_label": domain_label},
    }

    if not generation_ready:
        logger.warning(
            "contract_final generation_ready=false site=%s missing_required=%s",
            site_id, missing_required,
        )

    try:
        import boto3  # noqa: PLC0415
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
        s3.put_object(
            Bucket=_ARTIFACTS_BUCKET,
            Key=f"sites/{site_id}/contract_final.json",
            Body=json.dumps(contract, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info("contract_final.json 저장 완료: s3://%s/sites/%s/contract_final.json", _ARTIFACTS_BUCKET, site_id)
    except Exception as exc:
        logger.error("contract_final.json 저장 실패 site=%s: %s", site_id, exc)


_CHAT_BUCKET = os.environ.get("CHAT_BUCKET", "hezo-chat")


def _save_chat_transcript(
    *,
    site_id: str,
    session_id: str,
    domain: str,
    domain_label: str,
    template_id: str,
    category: str,
    known_answers: dict[str, Any],
    messages: list[Any],
) -> None:
    """채팅 전체 대화를 MD로 hezo-chat/sites/{site_id}/chat_{session_id}.md에 저장."""
    if not site_id:
        return

    lines: list[str] = [
        "---",
        f"site_id: {site_id}",
        f"session_id: {session_id}",
        f"domain: {domain}",
        f"domain_label: {domain_label}",
        f"template_id: {template_id}",
        f"category: {category}",
        f"created_at: {_utc_timestamp()}",
        "---",
        "",
        f"# HEZO 챗봇 대화 기록 — {domain_label}",
        "",
        "## 수집된 슬롯",
        "",
    ]
    for k, v in known_answers.items():
        if v:
            lines.append(f"- **{k}**: {v}")
    lines += ["", "## 대화 내용", ""]
    for msg in messages:
        role = getattr(msg, "role", None) or (msg.get("role") if isinstance(msg, dict) else "")
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else "")
        if not content:
            continue
        label = "사용자" if role == "user" else "HEZO AI"
        lines.append(f"**{label}**: {content}")
        lines.append("")

    md = "\n".join(lines)
    safe_session = session_id.replace(":", "_").replace("/", "_")
    key = f"sites/{site_id}/chat_{safe_session}.md"
    try:
        import boto3  # noqa: PLC0415
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
        s3.put_object(
            Bucket=_CHAT_BUCKET,
            Key=key,
            Body=md.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
        logger.info("채팅 transcript 저장: s3://%s/%s (%d chars)", _CHAT_BUCKET, key, len(md))
    except Exception as exc:
        logger.error("채팅 transcript 저장 실패 site=%s: %s", site_id, exc)


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
        # known_answers에 이미 있는 슬롯은 missing에서 제외 (backend가 missing_slots를 안 보내도 정확히 계산)
        all_required = tuple(slot_registry.keys())
        missing_slots = tuple(s for s in all_required if s not in raw_answers)
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
    template_id: str = "",
) -> str:
    """P1 어시스턴트 Claude 시스템 프롬프트 생성 (3-Turn Progressive)."""
    ALL_LABELS = {**{k: v["label"] for k, v in slot_registry.items()}, **ALL_COMPANION_LABELS}

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
        # 첫 번째 턴인 경우 (answered_slot이 empty) 템플릿 요구사항 안내 추가
        # 임시 비활성화: template_guidance를 빈 문자열로 설정
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

    # Template-specific 요구사항 안내
    template_requirement = ""
    if "wine" in template_id:
        template_requirement = (
            "\n[이 템플릿의 필수 요구사항]\n"
            "홈페이지 품질을 위해 다음 정보가 반드시 필요합니다:\n"
            "- wine_lineup: 정확히 4가지 와인을 '이름/종류/가격/특징' 형식으로 입력해주세요.\n"
            "  예시: '이탈리아 키안티/레드/55,000원/스테이크와 어울림, 프랑스 샤르도네/화이트/48,000원/버터 향, ...'\n"
            "  ※ 반드시 슬래시(/)와 쉼표(,)를 사용해야 합니다.\n"
            "- featured_wine: 대표 추천 와인 1개 (wine_lineup 중에서 선택)\n"
            "- business_name: 실제 와인샵 이름 (테스트 입력 제외)\n"
            "고객님께서 요청하신 정보가 위 기준에 맞도록 정중하게 안내해주시기 바랍니다."
        )
    elif "tax" in template_id:
        template_requirement = (
            "\n[이 템플릿의 필수 요구사항]\n"
            "홈페이지 품질을 위해 다음 정보가 반드시 필요합니다:\n"
            "- tax_services: 반드시 3개의 서로 다른 세무 서비스\n"
            "- target_clients: 주요 고객층 (개인사업자, 법인 등)\n"
            "- success_case: 실제 절세 사례 1개\n"
            "고객님께서 요청하신 정보가 위 기준에 맞도록 정중하게 안내해주시기 바랍니다."
        )
    elif "career" in template_id:
        template_requirement = (
            "\n[이 템플릿의 필수 요구사항]\n"
            "홈페이지 품질을 위해 다음 정보가 반드시 필요합니다:\n"
            "- author_info: 블로그 작성자의 경력 소개 (직급, 경력년수, 주요 성과)\n"
            "- portfolio_projects: 3개 이상의 포트폴리오 프로젝트\n"
            "- learning_activities: 학습 활동 또는 개발 경험\n"
            "고객님께서 요청하신 정보가 위 기준에 맞도록 정중하게 안내해주시기 바랍니다."
        )

    return f"""당신은 HEZO 홈페이지 제작 어시스턴트입니다.
총 3번의 대화로 홈페이지 제작에 필요한 정보를 수집합니다.

[고객 업종]
{domain_label}
{wiki_section}{template_requirement}

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
4. [아직 필요한 정보]가 "없음"이면 완료 안내만 하고 추가 질문하지 마세요.
5. 와인 템플릿에서 wine_lineup이 '이름/종류/가격/특징' 형식을 벗어나면, 공손하게 형식을 맞춰 다시 입력해달라고 요청하세요."""


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
