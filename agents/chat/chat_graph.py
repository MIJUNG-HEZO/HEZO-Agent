"""Deterministic chat graph skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from bedrock_guardrails_adapter import (
    GuardrailsApplyInput,
    MockBedrockGuardrailsClient,
)
from chat_state_store import ChatCheckpoint, InMemoryChatStateStore
from contract_compile import ContractDraftInput, compile_contract_draft
from contract_quality_check import ContractQualityInput, check_contract_quality
from p2_markdown_loader import P2MarkdownLoadInput, load_p2_markdown_from_s3
from p2_markdown_parser import P2MarkdownParseInput, parse_p2_markdown
from p2_markdown_request import P2MarkdownRequestInput, build_p2_markdown_request_payload
from p2_markdown_review import P2MarkdownReviewInput, review_p2_markdown
from proactive_questioning import ProactiveQuestionInput, build_proactive_question_candidates
from s3_artifact_store import ArtifactPayload, InMemoryS3ArtifactStore
from slot_answer_state import SlotAnswerInput, apply_slot_answer


GraphNode = Callable[["ChatGraphState"], "ChatGraphState"]

CHAT_GRAPH_NODE_ORDER = (
    "p2_markdown_request",
    "p2_markdown_load",
    "p2_markdown_parse",
    "p2_markdown_review",
    "proactive_questioning",
    "slot_answer_state",
    "contract_compile",
    "contract_quality_check",
    "bedrock_guardrails",
    "chat_state_checkpoint",
    "s3_artifact_storage",
)


@dataclass(frozen=True)
class ChatGraphState:
    """State shape passed through the P1 chat graph skeleton."""

    session_id: str
    site_id: str
    user_id: str
    stage: str
    domain: str
    domain_label: str
    selected_template: str
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    category: str = "services"
    p2_markdown_request: dict[str, Any] = field(default_factory=dict)
    p2_markdown_load: dict[str, Any] = field(default_factory=dict)
    p2_markdown_parse: dict[str, Any] = field(default_factory=dict)
    p2_markdown_review: dict[str, Any] = field(default_factory=dict)
    question_candidates: tuple[dict[str, Any], ...] = ()
    contract_draft: dict[str, Any] = field(default_factory=dict)
    quality_check: dict[str, Any] = field(default_factory=dict)
    guardrail_result: dict[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[dict[str, Any], ...] = ()
    checkpoint_ref: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "site_id": self.site_id,
            "user_id": self.user_id,
            "stage": self.stage,
            "category": self.category,
            "domain": self.domain,
            "domain_label": self.domain_label,
            "selected_template": self.selected_template,
            "slot_registry": self.slot_registry,
            "known_answers": self.known_answers,
            "missing_slots": list(self.missing_slots),
            "p2_markdown_request": self.p2_markdown_request,
            "p2_markdown_load": self.p2_markdown_load,
            "p2_markdown_parse": self.p2_markdown_parse,
            "p2_markdown_review": self.p2_markdown_review,
            "question_candidates": list(self.question_candidates),
            "contract_draft": self.contract_draft,
            "quality_check": self.quality_check,
            "guardrail_result": self.guardrail_result,
            "artifact_refs": list(self.artifact_refs),
            "checkpoint_ref": self.checkpoint_ref,
            "reasons": list(self.reasons),
        }


def run_chat_graph(initial_state: ChatGraphState) -> ChatGraphState:
    """Run the deterministic graph skeleton in fixed stage order."""

    state = initial_state
    for node in (
        p2_markdown_request_node,
        p2_markdown_load_node,
        p2_markdown_parse_node,
        p2_markdown_review_node,
        proactive_questioning_node,
        slot_answer_state_node,
        contract_compile_node,
        contract_quality_check_node,
        bedrock_guardrails_node,
        chat_state_checkpoint_node,
        s3_artifact_storage_node,
    ):
        state = node(state)
    return state


def p2_markdown_request_node(state: ChatGraphState) -> ChatGraphState:
    payload = build_p2_markdown_request_payload(
        P2MarkdownRequestInput(
            site_id=state.site_id,
            user_id=state.user_id,
            category=state.category,
            domain=state.domain,
            domain_label=state.domain_label,
            selected_template=state.selected_template,
            slot_registry=state.slot_registry,
            known_answers=state.known_answers,
            missing_slots=state.missing_slots,
        )
    ).to_dict()
    return _replace(
        state,
        stage="p2_markdown_request",
        p2_markdown_request=payload,
        reasons=state.reasons + ("p2_markdown_request_built",),
    )


def p2_markdown_load_node(state: ChatGraphState) -> ChatGraphState:
    store = InMemoryS3ArtifactStore()
    load_input = P2MarkdownLoadInput(
        category=state.category,
        domain=state.domain,
        expected_domain=state.domain,
        slot_registry=state.slot_registry,
        source_s3_key=f"industries/{state.category}/{state.domain}.md",
        version="v001",
        source_count=2,
        source_grade="mid",
    )
    ref = store.build_artifact_ref("p2_markdown", category=state.category, domain=state.domain)
    store.put_artifact(
        ArtifactPayload(
            ref=ref,
            body=_mock_p2_markdown_content(state),
        )
    )
    result = load_p2_markdown_from_s3(load_input, store)
    return _replace(
        state,
        stage="p2_markdown_load",
        p2_markdown_load=result.to_dict(),
        reasons=state.reasons + ("p2_markdown_loaded",),
    )


def p2_markdown_parse_node(state: ChatGraphState) -> ChatGraphState:
    result = parse_p2_markdown(
        P2MarkdownParseInput(
            domain=state.domain,
            category=state.category,
            expected_domain=state.domain,
            content=str(state.p2_markdown_load.get("content", "")),
            slot_registry=state.slot_registry,
            source_s3_key=str(
                state.p2_markdown_load.get("ref", {}).get("key", "")
            ),
            version=str(
                state.p2_markdown_load.get("parse_input", {}).get("version", "v001")
            ),
            source_count=int(
                state.p2_markdown_load.get("parse_input", {}).get("source_count", 0)
            ),
            source_grade=str(
                state.p2_markdown_load.get("parse_input", {}).get("source_grade", "unknown")
            ),
        )
    )
    return _replace(
        state,
        stage="p2_markdown_parse",
        slot_registry=result.apply_to_slot_registry(state.slot_registry),
        p2_markdown_parse=result.to_dict(),
        reasons=state.reasons + ("p2_markdown_parsed",),
    )


def p2_markdown_review_node(state: ChatGraphState) -> ChatGraphState:
    result = review_p2_markdown(
        P2MarkdownReviewInput(
            domain=state.domain,
            expected_domain=state.domain,
            p2_confidence=float(state.p2_markdown_parse.get("p2_confidence", 0.0)),
            content=str(state.p2_markdown_load.get("content", "")),
            source_count=int(state.p2_markdown_parse.get("source_count", 0)),
            source_grade=str(state.p2_markdown_parse.get("source_grade", "unknown")),
        )
    ).to_state()
    return _replace(
        state,
        stage="p2_markdown_review",
        p2_markdown_review=result,
        reasons=state.reasons + ("p2_markdown_reviewed",),
    )


def _mock_p2_markdown_content(state: ChatGraphState) -> str:
    return "\n".join(
        [
            "---",
            f"domain: {state.domain}",
            f"category: {state.category}",
            "template_no: 13",
            f"label: {state.domain_label}",
            "confidence: 0.82",
            "volatility: low",
            "last_updated: 2026-06-18",
            "source_urls:",
            "  - https://example.com/source-1",
            "  - https://example.com/source-2",
            "---",
            "",
            f"# {state.domain_label} 도메인 지식",
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


def proactive_questioning_node(state: ChatGraphState) -> ChatGraphState:
    candidates = build_proactive_question_candidates(
        ProactiveQuestionInput(
            domain=state.domain,
            domain_label=state.domain_label,
            p1_markdown_review_status=str(
                state.p2_markdown_review.get("p1_markdown_review_status", "failed")
            ),
            p2_markdown_usable_for_questions=bool(
                state.p2_markdown_review.get("p2_markdown_usable_for_questions", False)
            ),
            slot_registry=state.slot_registry,
            known_answers=state.known_answers,
            missing_slots=state.missing_slots,
            p2_knowledge_summary=", ".join(
                str(section.get("title", ""))
                for section in state.p2_markdown_parse.get("knowledge_sections", [])
                if section.get("title")
            ),
            max_questions=3,
        )
    )
    return _replace(
        state,
        stage="proactive_questioning",
        question_candidates=tuple(candidate.to_dict() for candidate in candidates),
        reasons=state.reasons + ("question_candidates_built",),
    )


def slot_answer_state_node(state: ChatGraphState) -> ChatGraphState:
    if not state.question_candidates:
        return _replace(
            state,
            stage="slot_answer_state",
            reasons=state.reasons + ("slot_answer_skipped",),
        )

    first_question = state.question_candidates[0]
    answered_slot = str(first_question["slot"])
    result = apply_slot_answer(
        SlotAnswerInput(
            slot_registry=state.slot_registry,
            known_answers=state.known_answers,
            missing_slots=state.missing_slots,
            answered_slot=answered_slot,
            answer=f"{state.slot_registry[answered_slot]['label']} 예시 답변",
        )
    )
    return _replace(
        state,
        stage="slot_answer_state",
        known_answers=result.known_answers,
        missing_slots=result.missing_slots,
        reasons=state.reasons + result.reasons,
    )


def contract_compile_node(state: ChatGraphState) -> ChatGraphState:
    result = compile_contract_draft(
        ContractDraftInput(
            site_id=state.site_id,
            user_id=state.user_id,
            domain=state.domain,
            domain_label=state.domain_label,
            selected_template=state.selected_template,
            slot_registry=state.slot_registry,
            known_answers=state.known_answers,
            missing_slots=state.missing_slots,
        )
    )
    return _replace(
        state,
        stage="contract_compile",
        contract_draft=result.draft,
        reasons=state.reasons + ("contract_draft_compiled",),
    )


def contract_quality_check_node(state: ChatGraphState) -> ChatGraphState:
    result = check_contract_quality(
        ContractQualityInput(
            draft=state.contract_draft,
            minimum_filled_slots=1,
        )
    ).to_dict()
    return _replace(
        state,
        stage="contract_quality_check",
        quality_check=result,
        reasons=state.reasons + tuple(result["reasons"]),
    )


def bedrock_guardrails_node(state: ChatGraphState) -> ChatGraphState:
    result = MockBedrockGuardrailsClient().apply_guardrail(
        GuardrailsApplyInput(
            target="contract_draft",
            source="OUTPUT",
            content=state.contract_draft,
            metadata={"session_id": state.session_id, "site_id": state.site_id},
        )
    ).to_dict()
    return _replace(
        state,
        stage="bedrock_guardrails",
        guardrail_result=result,
        reasons=state.reasons + tuple(result["reasons"]),
    )


def chat_state_checkpoint_node(state: ChatGraphState) -> ChatGraphState:
    store = InMemoryChatStateStore()
    checkpoint = store.save_checkpoint(
        ChatCheckpoint(
            session_id=state.session_id,
            stage=state.stage,
            version=1,
            state=state.to_dict(),
        )
    )
    return _replace(
        state,
        stage="chat_state_checkpoint",
        checkpoint_ref={"pk": checkpoint.pk, "sk": checkpoint.sk},
        reasons=state.reasons + ("chat_state_checkpoint_saved",),
    )


def s3_artifact_storage_node(state: ChatGraphState) -> ChatGraphState:
    store = InMemoryS3ArtifactStore()
    artifact_ref = store.build_artifact_ref(
        "contract_draft",
        site_id=state.site_id,
        version=1,
    )
    stored_ref = store.put_artifact(
        ArtifactPayload(
            ref=artifact_ref,
            body=state.contract_draft,
            guardrail_action=str(state.guardrail_result.get("action", "NONE")),
            store_allowed=bool(state.guardrail_result.get("store_allowed", True)),
        )
    )
    return _replace(
        state,
        stage="s3_artifact_storage",
        artifact_refs=state.artifact_refs + (stored_ref.to_dict(),),
        reasons=state.reasons + ("contract_draft_artifact_saved",),
    )


def _replace(state: ChatGraphState, **changes: Any) -> ChatGraphState:
    data = state.__dict__.copy()
    data.update(changes)
    return ChatGraphState(**data)
