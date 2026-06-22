"""Bedrock Guardrails adapters for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
from typing import Any, Literal, Protocol


GuardrailTarget = Literal["user_input", "p2_markdown", "contract_draft", "llm_output"]
GuardrailSource = Literal["INPUT", "OUTPUT"]
GuardrailAction = Literal["NONE", "GUARDRAIL_INTERVENED"]
GuardrailStatus = Literal["succeeded", "failed"]

FALLBACK_GUARDRAIL_ID = "q8dcjc2um846"
FALLBACK_GUARDRAIL_VERSION = "DRAFT"
DEFAULT_GUARDRAIL_ID = os.environ.get("HEZO_BEDROCK_GUARDRAIL_ID", FALLBACK_GUARDRAIL_ID)
DEFAULT_GUARDRAIL_VERSION = os.environ.get(
    "HEZO_BEDROCK_GUARDRAIL_VERSION",
    FALLBACK_GUARDRAIL_VERSION,
)

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
    # 전화번호·이메일·이름·주소는 홈페이지 제작 챗봇의 수집 대상 — 차단 제외
    "resident_registration_number_detected": re.compile(r"\d{6}[-\s]?[1-4]\d{6}"),
}


def _default_guardrail_id() -> str:
    return os.environ.get("HEZO_BEDROCK_GUARDRAIL_ID", FALLBACK_GUARDRAIL_ID)


def _default_guardrail_version() -> str:
    return os.environ.get("HEZO_BEDROCK_GUARDRAIL_VERSION", FALLBACK_GUARDRAIL_VERSION)


@dataclass(frozen=True)
class GuardrailsApplyInput:
    """Input boundary for future Bedrock ApplyGuardrail calls."""

    target: GuardrailTarget
    content: str | dict[str, Any]
    source: GuardrailSource
    guardrail_id: str = field(default_factory=_default_guardrail_id)
    guardrail_version: str = field(default_factory=_default_guardrail_version)
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


class Boto3BedrockGuardrailsClient:
    """AWS Bedrock Runtime Guardrails client using ApplyGuardrail."""

    def __init__(self, client: Any | None = None, region_name: str | None = None) -> None:
        if client is not None:
            self._client = client
            return

        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("boto3_required_for_bedrock_guardrails_client") from error

        session_kwargs: dict[str, str] = {}
        profile_name = os.environ.get("AWS_PROFILE")
        if profile_name:
            session_kwargs["profile_name"] = profile_name
        session = boto3.Session(**session_kwargs)
        self._client = session.client(
            "bedrock-runtime",
            region_name=region_name or os.environ.get("AWS_REGION"),
        )

    def apply_guardrail(self, guardrail_input: GuardrailsApplyInput) -> GuardrailsApplyResult:
        validation_error = _validate_guardrail_input(guardrail_input)
        if validation_error:
            return _failed_result(guardrail_input, validation_error)

        started = time.perf_counter()
        try:
            response = self._client.apply_guardrail(
                guardrailIdentifier=guardrail_input.guardrail_id,
                guardrailVersion=guardrail_input.guardrail_version,
                source=guardrail_input.source,
                content=[
                    {
                        "text": {
                            "text": _content_to_text(guardrail_input.content),
                        },
                    }
                ],
            )
        except Exception as error:
            return _failed_result(
                guardrail_input,
                _bedrock_error_reason(error),
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        action = _normalize_action(response.get("action"))
        assessments = tuple(_normalize_bedrock_assessments(response.get("assessments", []), action))
        if not assessments:
            assessments = (
                GuardrailAssessment(
                    policy="bedrock_guardrail",
                    reason="guardrail_passed",
                    blocked=False,
                ),
            )

        blocking_reasons = tuple(
            assessment.reason for assessment in assessments if assessment.blocked
        )
        reasons = blocking_reasons or ("guardrail_passed",)
        return GuardrailsApplyResult(
            status="succeeded",
            target=guardrail_input.target,
            source=guardrail_input.source,
            action=action,
            store_allowed=action == "NONE",
            masked_output=_extract_guardrail_output_text(response),
            reasons=reasons,
            assessments=assessments,
            latency_ms=latency_ms,
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


def _normalize_action(action: Any) -> GuardrailAction:
    if action == "GUARDRAIL_INTERVENED":
        return "GUARDRAIL_INTERVENED"
    return "NONE"


def _extract_guardrail_output_text(response: dict[str, Any]) -> str | None:
    output_texts: list[str] = []
    for output in response.get("outputs", []):
        if isinstance(output, dict):
            text = output.get("text")
            if isinstance(text, str) and text.strip():
                output_texts.append(text.strip())
    if not output_texts:
        return None
    return "\n".join(output_texts)


def _normalize_bedrock_assessments(
    assessments: list[dict[str, Any]],
    action: GuardrailAction,
) -> list[GuardrailAssessment]:
    normalized: list[GuardrailAssessment] = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        normalized.extend(_content_policy_assessments(assessment))
        normalized.extend(_sensitive_information_assessments(assessment))
        normalized.extend(_topic_policy_assessments(assessment))
        normalized.extend(_word_policy_assessments(assessment))
        normalized.extend(_prompt_attack_assessments(assessment))
        normalized.extend(_contextual_grounding_assessments(assessment))

    if action == "GUARDRAIL_INTERVENED" and not any(item.blocked for item in normalized):
        normalized.append(
            GuardrailAssessment(
                policy="bedrock_guardrail",
                reason="guardrail_intervened",
                blocked=True,
            )
        )
    return normalized


def _content_policy_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("contentPolicy", {})
    filters = policy.get("filters", []) if isinstance(policy, dict) else []
    return [
        GuardrailAssessment(
            policy="content_policy",
            reason=str(item.get("type", "content_filter")).lower(),
            blocked=_is_blocked_action(item.get("action")),
        )
        for item in filters
        if isinstance(item, dict)
    ]


def _sensitive_information_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("sensitiveInformationPolicy", {})
    if not isinstance(policy, dict):
        return []

    normalized: list[GuardrailAssessment] = []
    for item in policy.get("piiEntities", []):
        if isinstance(item, dict):
            normalized.append(
                GuardrailAssessment(
                    policy="sensitive_information",
                    reason=str(item.get("type", "pii_entity")).lower(),
                    blocked=_is_blocked_action(item.get("action")),
                )
            )
    for item in policy.get("regexes", []):
        if isinstance(item, dict):
            normalized.append(
                GuardrailAssessment(
                    policy="sensitive_information",
                    reason=str(item.get("name", "regex")).lower(),
                    blocked=_is_blocked_action(item.get("action")),
                )
            )
    return normalized


def _topic_policy_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("topicPolicy", {})
    topics = policy.get("topics", []) if isinstance(policy, dict) else []
    return [
        GuardrailAssessment(
            policy="topic_policy",
            reason=str(item.get("name", item.get("type", "topic"))).lower(),
            blocked=_is_blocked_action(item.get("action")),
        )
        for item in topics
        if isinstance(item, dict)
    ]


def _word_policy_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("wordPolicy", {})
    if not isinstance(policy, dict):
        return []

    normalized: list[GuardrailAssessment] = []
    for item in policy.get("customWords", []):
        if isinstance(item, dict):
            normalized.append(
                GuardrailAssessment(
                    policy="word_policy",
                    reason=str(item.get("match", "custom_word")).lower(),
                    blocked=_is_blocked_action(item.get("action")),
                )
            )
    for item in policy.get("managedWordLists", []):
        if isinstance(item, dict):
            normalized.append(
                GuardrailAssessment(
                    policy="word_policy",
                    reason=str(item.get("type", "managed_word_list")).lower(),
                    blocked=_is_blocked_action(item.get("action")),
                )
            )
    return normalized


def _prompt_attack_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("promptAttackPolicy", {})
    filters = policy.get("filters", []) if isinstance(policy, dict) else []
    return [
        GuardrailAssessment(
            policy="prompt_attack",
            reason=str(item.get("type", "prompt_attack")).lower(),
            blocked=_is_blocked_action(item.get("action")),
        )
        for item in filters
        if isinstance(item, dict)
    ]


def _contextual_grounding_assessments(assessment: dict[str, Any]) -> list[GuardrailAssessment]:
    policy = assessment.get("contextualGroundingPolicy", {})
    filters = policy.get("filters", []) if isinstance(policy, dict) else []
    return [
        GuardrailAssessment(
            policy="contextual_grounding",
            reason=str(item.get("type", "contextual_grounding")).lower(),
            blocked=_is_blocked_action(item.get("action")),
        )
        for item in filters
        if isinstance(item, dict)
    ]


def _is_blocked_action(action: Any) -> bool:
    return action in {"BLOCKED", "ANONYMIZED"}


def _bedrock_error_reason(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        error_info = response.get("Error", {})
        code = error_info.get("Code")
        if code:
            message = str(error_info.get("Message", "")).strip()
            if message:
                normalized_message = "_".join(message.lower().split())[:80]
                return f"bedrock_{str(code).lower()}_{normalized_message}"
            return f"bedrock_{str(code).lower()}"
    return "bedrock_apply_guardrail_failed"
