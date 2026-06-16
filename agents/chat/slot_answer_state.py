"""Slot answer state updater for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


AnswerStatus = Literal["accepted", "rejected"]


@dataclass(frozen=True)
class SlotAnswerInput:
    """User answer and current slot state."""

    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    answered_slot: str
    answer: Any


@dataclass(frozen=True)
class SlotAnswerStateResult:
    """Updated slot state after applying a user answer."""

    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    answered_slot: str
    answer_status: AnswerStatus
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "known_answers": self.known_answers,
            "missing_slots": list(self.missing_slots),
            "answered_slot": self.answered_slot,
            "answer_status": self.answer_status,
            "reasons": list(self.reasons),
        }


def apply_slot_answer(answer_input: SlotAnswerInput) -> SlotAnswerStateResult:
    """Apply a user answer to known_answers and missing_slots."""

    slot = answer_input.answered_slot.strip() if isinstance(answer_input.answered_slot, str) else ""
    if not slot:
        return _rejected(answer_input, "answered_slot_missing")

    if not answer_input.slot_registry:
        return _rejected(answer_input, "slot_registry_empty")

    if slot not in answer_input.slot_registry:
        return _rejected(answer_input, "unknown_slot")

    if not _has_answer(answer_input.answer):
        return _rejected(answer_input, "answer_empty")

    updated_answers = dict(answer_input.known_answers)
    updated_answers[slot] = answer_input.answer
    updated_missing_slots = tuple(
        missing_slot for missing_slot in answer_input.missing_slots if missing_slot != slot
    )

    return SlotAnswerStateResult(
        known_answers=updated_answers,
        missing_slots=updated_missing_slots,
        answered_slot=slot,
        answer_status="accepted",
        reasons=("answer_applied",),
    )


def _rejected(answer_input: SlotAnswerInput, reason: str) -> SlotAnswerStateResult:
    answered_slot = (
        answer_input.answered_slot.strip()
        if isinstance(answer_input.answered_slot, str)
        else ""
    )
    return SlotAnswerStateResult(
        known_answers=dict(answer_input.known_answers),
        missing_slots=tuple(answer_input.missing_slots),
        answered_slot=answered_slot,
        answer_status="rejected",
        reasons=(reason,),
    )


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
