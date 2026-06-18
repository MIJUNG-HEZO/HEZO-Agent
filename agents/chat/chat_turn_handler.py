"""Chat turn handler for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from proactive_questioning import (
    ProactiveQuestionCandidate,
    ProactiveQuestionInput,
    ReviewStatus,
    build_proactive_question_candidates,
)
from slot_answer_state import SlotAnswerStateResult, SlotAnswerInput, apply_slot_answer


TurnStatus = Literal["answer_accepted", "answer_rejected", "ready_for_contract_compile"]
NextStage = Literal["proactive_questioning", "contract_compile", "retry_answer"]


@dataclass(frozen=True)
class ChatTurnInput:
    """Input required to process one user answer turn."""

    session_id: str
    site_id: str
    user_id: str
    domain: str
    domain_label: str
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    answered_slot: str
    answer: Any
    p1_markdown_review_status: ReviewStatus = "passed"
    p2_markdown_usable_for_questions: bool = True
    p2_knowledge_summary: str = ""
    max_questions: int = 3


@dataclass(frozen=True)
class ChatTurnResult:
    """Normalized result returned after processing one user answer turn."""

    turn_status: TurnStatus
    next_stage: NextStage
    slot_answer: SlotAnswerStateResult
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    question_candidates: tuple[ProactiveQuestionCandidate, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_status": self.turn_status,
            "next_stage": self.next_stage,
            "slot_answer": self.slot_answer.to_dict(),
            "known_answers": self.known_answers,
            "missing_slots": list(self.missing_slots),
            "question_candidates": [
                candidate.to_dict() for candidate in self.question_candidates
            ],
            "reasons": list(self.reasons),
        }


def handle_chat_turn(turn_input: ChatTurnInput) -> ChatTurnResult:
    """Apply one user answer and decide the next P1 chat stage."""

    _validate_turn_input(turn_input)
    answer_result = apply_slot_answer(
        SlotAnswerInput(
            slot_registry=turn_input.slot_registry,
            known_answers=turn_input.known_answers,
            missing_slots=turn_input.missing_slots,
            answered_slot=turn_input.answered_slot,
            answer=turn_input.answer,
        )
    )
    if answer_result.answer_status == "rejected":
        return ChatTurnResult(
            turn_status="answer_rejected",
            next_stage="retry_answer",
            slot_answer=answer_result,
            known_answers=answer_result.known_answers,
            missing_slots=answer_result.missing_slots,
            question_candidates=(),
            reasons=answer_result.reasons,
        )

    if not answer_result.missing_slots:
        return ChatTurnResult(
            turn_status="ready_for_contract_compile",
            next_stage="contract_compile",
            slot_answer=answer_result,
            known_answers=answer_result.known_answers,
            missing_slots=(),
            question_candidates=(),
            reasons=answer_result.reasons + ("required_slots_filled",),
        )

    question_candidates = tuple(
        build_proactive_question_candidates(
            ProactiveQuestionInput(
                domain=turn_input.domain,
                domain_label=turn_input.domain_label,
                p1_markdown_review_status=turn_input.p1_markdown_review_status,
                p2_markdown_usable_for_questions=turn_input.p2_markdown_usable_for_questions,
                slot_registry=turn_input.slot_registry,
                known_answers=answer_result.known_answers,
                missing_slots=answer_result.missing_slots,
                p2_knowledge_summary=turn_input.p2_knowledge_summary,
                max_questions=turn_input.max_questions,
            )
        )
    )
    return ChatTurnResult(
        turn_status="answer_accepted",
        next_stage="proactive_questioning",
        slot_answer=answer_result,
        known_answers=answer_result.known_answers,
        missing_slots=answer_result.missing_slots,
        question_candidates=question_candidates,
        reasons=answer_result.reasons + ("next_question_candidates_built",),
    )


def _validate_turn_input(turn_input: ChatTurnInput) -> None:
    required_strings = {
        "session_id": turn_input.session_id,
        "site_id": turn_input.site_id,
        "user_id": turn_input.user_id,
        "domain": turn_input.domain,
        "domain_label": turn_input.domain_label,
    }
    missing = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError("required_fields_missing:" + ",".join(missing))
    if turn_input.max_questions <= 0:
        raise ValueError("max_questions_must_be_positive")
