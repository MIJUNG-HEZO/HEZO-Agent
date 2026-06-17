"""Bedrock Guardrails adapter skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Literal, Protocol


GuardrailTarget = Literal["user_input", "p2_markdown", "contract_draft", "llm_output"]
GuardrailSource = Literal["INPUT", "OUTPUT"]
GuardrailAction = Literal["NONE", "GUARDRAIL_INTERVENED"]
GuardrailStatus = Literal["succeeded", "failed"]

DEFAULT_GUARDRAIL_ID = "hezo-dev-guardrail"
DEFAULT_GUARDRAIL_VERSION = "DRAFT"

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
class GuardrailsApplyInput:
    """Input boundary for future Bedrock ApplyGuardrail calls."""

    target: GuardrailTarget
    content: str | dict[str, Any]
    source: GuardrailSource
    guardrail_id: str = DEFAULT_GUARDRAIL_ID
    guardrail_version: str = DEFAULT_GUARDRAIL_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailAssessment:
    """Normalized assessment item returned by the adapter."""

    policy: str
    reason: str
    blocked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "reason": self.reason,
            "blocked": self.blocked,
        }


@dataclass(frozen=True)
class GuardrailsApplyResult:
    """Normalized Bedrock Guardrails result."""

    status: GuardrailStatus
    target: GuardrailTarget
    source: GuardrailSource
    action: GuardrailAction
    store_allowed: bool
    masked_output: str | None
    reasons: tuple[str, ...]
    assessments: tuple[GuardrailAssessment, ...]
    latency_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "target": self.target,
            "source": self.source,
            "action": self.action,
            "store_allowed": self.store_allowed,
            "masked_output": self.masked_output,
            "reasons": list(self.reasons),
            "assessments": [assessment.to_dict() for assessment in self.assessments],
            "latency_ms": self.latency_ms,
        }


class GuardrailsClient(Protocol):
    """Client boundary for a future boto3 Bedrock Runtime implementation."""

    def apply_guardrail(self, guardrail_input: GuardrailsApplyInput) -> GuardrailsApplyResult:
        ...


class MockBedrockGuardrailsClient:
    """Deterministic local Guardrails client used by smoke tests."""

    def apply_guardrail(self, guardrail_input: GuardrailsApplyInput) -> GuardrailsApplyResult:
        validation_error = _validate_guardrail_input(guardrail_input)
        if validation_error:
            return _failed_result(guardrail_input, validation_error)

        content_text = _content_to_text(guardrail_input.content)
        assessments = tuple(_assess_content(content_text))
        blocking_reasons = tuple(
            assessment.reason for assessment in assessments if assessment.blocked
        )

        if blocking_reasons:
            return GuardrailsApplyResult(
                status="succeeded",
                target=guardrail_input.target,
                source=guardrail_input.source,
                action="GUARDRAIL_INTERVENED",
                store_allowed=False,
                masked_output=None,
                reasons=blocking_reasons,
                assessments=assessments,
                latency_ms=18,
            )

        return GuardrailsApplyResult(
            status="succeeded",
            target=guardrail_input.target,
            source=guardrail_input.source,
            action="NONE",
            store_allowed=True,
            masked_output=None,
            reasons=("guardrail_passed",),
            assessments=assessments,
            latency_ms=18,
        )


def _validate_guardrail_input(guardrail_input: GuardrailsApplyInput) -> str | None:
    if guardrail_input.target not in {"user_input", "p2_markdown", "contract_draft", "llm_output"}:
        return "guardrail_target_invalid"
    if guardrail_input.source not in {"INPUT", "OUTPUT"}:
        return "guardrail_source_invalid"
    if not isinstance(guardrail_input.guardrail_id, str) or not guardrail_input.guardrail_id.strip():
        return "guardrail_id_missing"
    if not isinstance(guardrail_input.guardrail_version, str) or not guardrail_input.guardrail_version.strip():
        return "guardrail_version_missing"

    content_text = _content_to_text(guardrail_input.content)
    if not content_text.strip():
        return "guardrail_content_empty"

    return None


def _assess_content(content_text: str) -> list[GuardrailAssessment]:
    assessments: list[GuardrailAssessment] = []

    if _detect_prompt_injection(content_text):
        assessments.append(
            GuardrailAssessment(
                policy="prompt_attack",
                reason="prompt_injection_suspected",
                blocked=True,
            )
        )

    for reason, pattern in PII_PATTERNS.items():
        if pattern.search(content_text):
            assessments.append(
                GuardrailAssessment(
                    policy="sensitive_information",
                    reason=reason,
                    blocked=True,
                )
            )

    if not assessments:
        assessments.append(
            GuardrailAssessment(
                policy="mock_guardrail",
                reason="guardrail_passed",
                blocked=False,
            )
        )

    return assessments


def _failed_result(
    guardrail_input: GuardrailsApplyInput,
    reason: str,
) -> GuardrailsApplyResult:
    return GuardrailsApplyResult(
        status="failed",
        target=guardrail_input.target,
        source=guardrail_input.source,
        action="GUARDRAIL_INTERVENED",
        store_allowed=False,
        masked_output=None,
        reasons=(reason,),
        assessments=(),
        latency_ms=0,
    )


def _content_to_text(content: str | dict[str, Any]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def _detect_prompt_injection(content_text: str) -> bool:
    lowered = content_text.lower()
    return any(pattern.lower() in lowered for pattern in PROMPT_INJECTION_PATTERNS)
