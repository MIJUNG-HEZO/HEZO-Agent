"""Contract draft quality checker for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


QualityStatus = Literal["contract_final_ready", "needs_enrichment"]


@dataclass(frozen=True)
class ContractQualityInput:
    """Contract draft and local quality thresholds."""

    draft: dict[str, Any]
    required_slot_threshold: float = 1.0
    minimum_filled_slots: int = 2


@dataclass(frozen=True)
class ContractQualityResult:
    """Quality result used before contract final handoff."""

    quality_status: QualityStatus
    contract_final_ready: bool
    generation_ready: bool
    quality_score: float
    missing_required_slots: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_status": self.quality_status,
            "contract_final_ready": self.contract_final_ready,
            "generation_ready": self.generation_ready,
            "quality_score": self.quality_score,
            "missing_required_slots": list(self.missing_required_slots),
            "reasons": list(self.reasons),
        }


def check_contract_quality(quality_input: ContractQualityInput) -> ContractQualityResult:
    """Check whether a compiled contract draft can be saved as contract_final."""

    _validate_quality_input(quality_input)

    slots = quality_input.draft.get("slots", {})
    required_slots = [
        slot for slot, slot_data in slots.items() if bool(slot_data.get("required", False))
    ]
    filled_slots = [
        slot for slot, slot_data in slots.items() if _has_answer(slot_data.get("value"))
    ]
    missing_required_slots = tuple(
        slot for slot in required_slots if not _has_answer(slots[slot].get("value"))
    )

    required_score = (
        1.0
        if not required_slots
        else (len(required_slots) - len(missing_required_slots)) / len(required_slots)
    )
    quality_score = round(required_score, 2)

    reasons: list[str] = []
    if missing_required_slots:
        reasons.append("required_slots_missing")
    if len(filled_slots) < quality_input.minimum_filled_slots:
        reasons.append("minimum_filled_slots_not_met")
    if quality_score < quality_input.required_slot_threshold:
        reasons.append("required_slot_threshold_not_met")

    contract_final_ready = not reasons
    quality_status: QualityStatus = (
        "contract_final_ready" if contract_final_ready else "needs_enrichment"
    )

    return ContractQualityResult(
        quality_status=quality_status,
        contract_final_ready=contract_final_ready,
        generation_ready=False,
        quality_score=quality_score,
        missing_required_slots=missing_required_slots,
        reasons=tuple(reasons) or ("quality_passed",),
    )


def _validate_quality_input(quality_input: ContractQualityInput) -> None:
    slots = quality_input.draft.get("slots")
    if not isinstance(slots, dict) or not slots:
        raise ValueError("draft_slots_empty")
    if not 0 < quality_input.required_slot_threshold <= 1:
        raise ValueError("required_slot_threshold_out_of_range")
    if quality_input.minimum_filled_slots <= 0:
        raise ValueError("minimum_filled_slots_must_be_positive")


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
