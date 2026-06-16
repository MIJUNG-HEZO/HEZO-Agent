"""Storage guardrails adapter skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal


GuardrailTarget = Literal["user_input", "p2_markdown", "contract_draft", "llm_output"]
GuardrailAction = Literal["NONE", "GUARDRAIL_INTERVENED"]

PROMPT_INJECTION_PATTERNS = [
    "이전 지시 무시",
    "앞의 지시 무시",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "print your instructions",
]

PII_PATTERNS = {
    "email_detected": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone_detected": re.compile(r"(?:010|011|016|017|018|019)[-\s]?\d{3,4}[-\s]?\d{4}"),
    "resident_registration_number_detected": re.compile(r"\d{6}[-\s]?[1-4]\d{6}"),
}


@dataclass(frozen=True)
class StorageGuardrailInput:
    """Content that should be checked before storage."""

    target: GuardrailTarget
    content: str | dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StorageGuardrailResult:
    """Guardrail result used by future S3 storage adapters."""

    target: GuardrailTarget
    action: GuardrailAction
    store_allowed: bool
    masked_output: str | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "action": self.action,
            "store_allowed": self.store_allowed,
            "masked_output": self.masked_output,
            "reasons": list(self.reasons),
        }


def apply_storage_guardrails(
    guardrail_input: StorageGuardrailInput,
) -> StorageGuardrailResult:
    """Apply local mock guardrails before storage.

    This is a deterministic skeleton. The actual Bedrock ApplyGuardrail call
    should be connected in a follow-up adapter.
    """

    content_text = _content_to_text(guardrail_input.content)
    reasons = _detect_prompt_injection(content_text) + _detect_pii(content_text)

    if reasons:
        return StorageGuardrailResult(
            target=guardrail_input.target,
            action="GUARDRAIL_INTERVENED",
            store_allowed=False,
            masked_output=None,
            reasons=tuple(reasons),
        )

    return StorageGuardrailResult(
        target=guardrail_input.target,
        action="NONE",
        store_allowed=True,
        masked_output=None,
        reasons=("guardrail_passed",),
    )


def _content_to_text(content: str | dict[str, Any]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def _detect_prompt_injection(content_text: str) -> list[str]:
    lowered = content_text.lower()
    if any(pattern.lower() in lowered for pattern in PROMPT_INJECTION_PATTERNS):
        return ["prompt_injection_suspected"]
    return []


def _detect_pii(content_text: str) -> list[str]:
    return [
        reason
        for reason, pattern in PII_PATTERNS.items()
        if pattern.search(content_text)
    ]
