"""Bedrock Claude invocation adapter skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


ClaudeUseCase = Literal["question_enrichment", "contract_enrichment", "assistant_reply"]
InvocationStatus = Literal["succeeded", "failed"]

DEFAULT_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.2

INJECTION_PATTERNS = [
    "이전 지시 무시",
    "앞의 지시 무시",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "print your instructions",
]


@dataclass(frozen=True)
class ClaudeMessage:
    """Message item passed to Claude."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ClaudeInvocationInput:
    """Input boundary for future Bedrock Runtime calls."""

    use_case: ClaudeUseCase
    system_prompt: str
    messages: tuple[ClaudeMessage, ...]
    context: dict[str, Any] = field(default_factory=dict)
    model_id: str = DEFAULT_MODEL_ID
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE


@dataclass(frozen=True)
class ClaudeUsage:
    """Token usage metadata returned by the adapter."""

    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
        }


@dataclass(frozen=True)
class ClaudeInvocationResult:
    """Normalized Claude invocation result."""

    status: InvocationStatus
    text: str
    model_id: str
    usage: ClaudeUsage
    latency_ms: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "model_id": self.model_id,
            "usage": self.usage.to_dict(),
            "latency_ms": self.latency_ms,
            "reasons": list(self.reasons),
        }


class ClaudeInvoker(Protocol):
    """Invoker boundary for a future boto3 Bedrock Runtime implementation."""

    def invoke(self, invocation_input: ClaudeInvocationInput) -> ClaudeInvocationResult:
        ...


class MockClaudeInvoker:
    """Deterministic local Claude invoker used by smoke tests."""

    def invoke(self, invocation_input: ClaudeInvocationInput) -> ClaudeInvocationResult:
        validation_error = _validate_invocation_input(invocation_input)
        if validation_error:
            return _failed_result(invocation_input.model_id, validation_error)

        response_text = _mock_response_text(invocation_input)
        return ClaudeInvocationResult(
            status="succeeded",
            text=response_text,
            model_id=invocation_input.model_id,
            usage=ClaudeUsage(
                input_tokens=_estimate_input_tokens(invocation_input),
                output_tokens=max(1, len(response_text.split())),
            ),
            latency_ms=25,
            reasons=("mock_invocation_succeeded", invocation_input.use_case),
        )


def _validate_invocation_input(invocation_input: ClaudeInvocationInput) -> str | None:
    if invocation_input.use_case not in {
        "question_enrichment",
        "contract_enrichment",
        "assistant_reply",
    }:
        return "use_case_invalid"
    if not isinstance(invocation_input.system_prompt, str) or not invocation_input.system_prompt.strip():
        return "system_prompt_missing"
    if not invocation_input.messages:
        return "messages_empty"
    if not invocation_input.model_id.strip():
        return "model_id_missing"
    if invocation_input.max_tokens <= 0:
        return "max_tokens_must_be_positive"
    if not 0 <= invocation_input.temperature <= 1:
        return "temperature_out_of_range"

    for message in invocation_input.messages:
        if message.role not in {"user", "assistant"}:
            return "message_role_invalid"
        if not isinstance(message.content, str) or not message.content.strip():
            return "message_content_empty"
        if _contains_injection_pattern(message.content):
            return "prompt_injection_suspected"

    return None


def _mock_response_text(invocation_input: ClaudeInvocationInput) -> str:
    if invocation_input.use_case == "question_enrichment":
        return "부족한 슬롯을 확인하기 위한 보완 질문 후보를 생성했습니다."
    if invocation_input.use_case == "contract_enrichment":
        return "Contract draft 보완에 필요한 누락 정보와 약한 근거를 정리했습니다."
    return "요청 내용을 확인했습니다. 필요한 정보를 순서대로 도와드리겠습니다."


def _failed_result(model_id: str, reason: str) -> ClaudeInvocationResult:
    return ClaudeInvocationResult(
        status="failed",
        text="",
        model_id=model_id,
        usage=ClaudeUsage(input_tokens=0, output_tokens=0),
        latency_ms=0,
        reasons=(reason,),
    )


def _contains_injection_pattern(content: str) -> bool:
    lowered = content.lower()
    return any(pattern.lower() in lowered for pattern in INJECTION_PATTERNS)


def _estimate_input_tokens(invocation_input: ClaudeInvocationInput) -> int:
    joined = " ".join([invocation_input.system_prompt] + [m.content for m in invocation_input.messages])
    return max(1, len(joined.split()))
