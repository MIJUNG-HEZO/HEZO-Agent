"""P2 markdown request payload builder for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


RequestReason = Literal["initial_domain_selected", "review_failed", "manual_retry"]

PAYLOAD_VERSION = "v0.1"
TARGET_ARTIFACT = "domain_question_guide_markdown"


@dataclass(frozen=True)
class P2MarkdownRequestInput:
    """Context required before requesting a P2 markdown artifact."""

    site_id: str
    user_id: str
    domain: str
    domain_label: str
    selected_template: str
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any] = field(default_factory=dict)
    missing_slots: tuple[str, ...] = ()
    request_reason: RequestReason = "initial_domain_selected"


@dataclass(frozen=True)
class P2MarkdownRequestPayload:
    """Internal payload shape passed to the future P2 adapter."""

    payload_version: str
    target_artifact: str
    site_id: str
    user_id: str
    domain: str
    domain_label: str
    selected_template: str
    request_reason: RequestReason
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    instructions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "payload_version": self.payload_version,
            "target_artifact": self.target_artifact,
            "site_id": self.site_id,
            "user_id": self.user_id,
            "domain": self.domain,
            "domain_label": self.domain_label,
            "selected_template": self.selected_template,
            "request_reason": self.request_reason,
            "slot_registry": self.slot_registry,
            "known_answers": self.known_answers,
            "missing_slots": list(self.missing_slots),
            "instructions": list(self.instructions),
        }


def build_p2_markdown_request_payload(
    request_input: P2MarkdownRequestInput,
) -> P2MarkdownRequestPayload:
    """Build the local standard payload used to request P2 markdown."""

    _validate_request_input(request_input)

    return P2MarkdownRequestPayload(
        payload_version=PAYLOAD_VERSION,
        target_artifact=TARGET_ARTIFACT,
        site_id=request_input.site_id.strip(),
        user_id=request_input.user_id.strip(),
        domain=request_input.domain.strip(),
        domain_label=request_input.domain_label.strip(),
        selected_template=request_input.selected_template.strip(),
        request_reason=request_input.request_reason,
        slot_registry=request_input.slot_registry,
        known_answers=request_input.known_answers,
        missing_slots=tuple(request_input.missing_slots),
        instructions=(
            "Use the domain and slot registry as the source of truth.",
            "Return markdown that helps P1 ask proactive follow-up questions.",
            "Do not fabricate unverifiable business facts.",
            "Mark weak or missing information explicitly.",
        ),
    )


def _validate_request_input(request_input: P2MarkdownRequestInput) -> None:
    required_strings = {
        "site_id": request_input.site_id,
        "user_id": request_input.user_id,
        "domain": request_input.domain,
        "domain_label": request_input.domain_label,
        "selected_template": request_input.selected_template,
    }

    missing_fields = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing_fields:
        raise ValueError("required_fields_missing:" + ",".join(missing_fields))

    if not request_input.slot_registry:
        raise ValueError("slot_registry_empty")
