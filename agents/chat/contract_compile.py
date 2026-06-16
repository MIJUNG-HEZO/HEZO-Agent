"""Contract JSON draft compiler for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ContractStatus = Literal["draft"]
QualityStatus = Literal["ready_for_quality_check", "needs_enrichment"]


@dataclass(frozen=True)
class ContractDraftInput:
    """Slot state used to compile an internal Contract JSON draft."""

    site_id: str
    user_id: str
    domain: str
    domain_label: str
    selected_template: str
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    contract_version: int = 1


@dataclass(frozen=True)
class ContractDraftResult:
    """Compiled Contract JSON draft and quality metadata."""

    contract_status: ContractStatus
    quality_status: QualityStatus
    draft: dict[str, Any]
    missing_required_slots: tuple[str, ...]
    filled_slots: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_status": self.contract_status,
            "quality_status": self.quality_status,
            "draft": self.draft,
            "missing_required_slots": list(self.missing_required_slots),
            "filled_slots": list(self.filled_slots),
        }


def compile_contract_draft(compile_input: ContractDraftInput) -> ContractDraftResult:
    """Compile known slot answers into an internal Contract JSON draft."""

    _validate_compile_input(compile_input)

    slots: dict[str, dict[str, Any]] = {}
    filled_slots: list[str] = []
    missing_required_slots: list[str] = []

    for slot, slot_meta in compile_input.slot_registry.items():
        value = compile_input.known_answers.get(slot)
        filled = _has_answer(value)
        if filled:
            filled_slots.append(slot)
        elif bool(slot_meta.get("required", False)):
            missing_required_slots.append(slot)

        slots[slot] = {
            "label": str(slot_meta.get("label", slot)),
            "required": bool(slot_meta.get("required", False)),
            "value": value if filled else None,
            "filled": filled,
        }

    quality_status: QualityStatus = (
        "ready_for_quality_check" if not missing_required_slots else "needs_enrichment"
    )

    draft = {
        "contract_version": compile_input.contract_version,
        "site_id": compile_input.site_id.strip(),
        "user_id": compile_input.user_id.strip(),
        "domain": compile_input.domain.strip(),
        "domain_label": compile_input.domain_label.strip(),
        "selected_template": compile_input.selected_template.strip(),
        "slots": slots,
        "missing_slots": [
            slot for slot in compile_input.missing_slots if slot in compile_input.slot_registry
        ],
    }

    return ContractDraftResult(
        contract_status="draft",
        quality_status=quality_status,
        draft=draft,
        missing_required_slots=tuple(missing_required_slots),
        filled_slots=tuple(filled_slots),
    )


def _validate_compile_input(compile_input: ContractDraftInput) -> None:
    required_strings = {
        "site_id": compile_input.site_id,
        "user_id": compile_input.user_id,
        "domain": compile_input.domain,
        "domain_label": compile_input.domain_label,
        "selected_template": compile_input.selected_template,
    }

    missing_fields = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing_fields:
        raise ValueError("required_fields_missing:" + ",".join(missing_fields))

    if not compile_input.slot_registry:
        raise ValueError("slot_registry_empty")

    if compile_input.contract_version <= 0:
        raise ValueError("contract_version_must_be_positive")


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True
