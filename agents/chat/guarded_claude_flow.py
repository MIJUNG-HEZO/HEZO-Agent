"""Guardrail-gated Claude reply flow for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from bedrock_claude_adapter import (
    ClaudeInvocationInput,
    ClaudeInvocationResult,
    ClaudeInvoker,
    ClaudeMessage,
    MockClaudeInvoker,
)
from bedrock_guardrails_adapter import (
    GuardrailsApplyInput,
    GuardrailsApplyResult,
    GuardrailsClient,
    MockBedrockGuardrailsClient,
)


GuardedReplyStatus = Literal["succeeded", "blocked", "failed"]
GuardedReplyStage = Literal[
    "input_guardrail",
    "claude_invocation",
    "output_guardrail",
    "completed",
]

DEFAULT_BLOCKED_MESSAGE = "죄송합니다. 이 요청은 안전 정책에 따라 처리할 수 없습니다."


@dataclass(frozen=True)
class GuardedClaudeReplyInput:
    """Input for a guarded user-input -> Claude -> output flow."""

    user_message: str
    system_prompt: str
    session_id: str
    site_id: str
    user_id: str
    conversation_history: tuple[ClaudeMessage, ...] = ()
    context: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 512
    temperature: float = 0.2


@dataclass(frozen=True)
class GuardedClaudeReplyResult:
    """Normalized result for the guarded Claude reply flow."""

    status: GuardedReplyStatus
    stage: GuardedReplyStage
    final_text: str
    input_guardrail: GuardrailsApplyResult
    claude_result: ClaudeInvocationResult | None
    output_guardrail: GuardrailsApplyResult | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stage": self.stage,
            "final_text": self.final_text,
            "input_guardrail": self.input_guardrail.to_dict(),
            "claude_result": self.claude_result.to_dict() if self.claude_result else None,
            "output_guardrail": self.output_guardrail.to_dict() if self.output_guardrail else None,
            "reasons": list(self.reasons),
        }


def run_guarded_claude_reply(
    flow_input: GuardedClaudeReplyInput,
    *,
    guardrails_client: GuardrailsClient | None = None,
    claude_invoker: ClaudeInvoker | None = None,
) -> GuardedClaudeReplyResult:
    """Run Guardrail INPUT -> Claude -> Guardrail OUTPUT reply flow."""

    guardrails_client = guardrails_client or MockBedrockGuardrailsClient()
    claude_invoker = claude_invoker or MockClaudeInvoker()

    input_guardrail = guardrails_client.apply_guardrail(
        GuardrailsApplyInput(
            target="user_input",
            source="INPUT",
            content=flow_input.user_message,
            metadata=_metadata(flow_input),
        )
    )
    if _guardrail_blocks(input_guardrail):
        return GuardedClaudeReplyResult(
            status="blocked",
            stage="input_guardrail",
            final_text=_blocked_text(input_guardrail),
            input_guardrail=input_guardrail,
            claude_result=None,
            output_guardrail=None,
            reasons=("input_guardrail_blocked",) + input_guardrail.reasons,
        )
    if input_guardrail.status == "failed":
        return GuardedClaudeReplyResult(
            status="failed",
            stage="input_guardrail",
            final_text="",
            input_guardrail=input_guardrail,
            claude_result=None,
            output_guardrail=None,
            reasons=("input_guardrail_failed",) + input_guardrail.reasons,
        )

    claude_result = claude_invoker.invoke(_claude_input(flow_input))
    if claude_result.status == "failed":
        return GuardedClaudeReplyResult(
            status="failed",
            stage="claude_invocation",
            final_text="",
            input_guardrail=input_guardrail,
            claude_result=claude_result,
            output_guardrail=None,
            reasons=("claude_invocation_failed",) + claude_result.reasons,
        )

    output_guardrail = guardrails_client.apply_guardrail(
        GuardrailsApplyInput(
            target="llm_output",
            source="OUTPUT",
            content=claude_result.text,
            metadata=_metadata(flow_input),
        )
    )
    if _guardrail_blocks(output_guardrail):
        return GuardedClaudeReplyResult(
            status="blocked",
            stage="output_guardrail",
            final_text=_blocked_text(output_guardrail),
            input_guardrail=input_guardrail,
            claude_result=claude_result,
            output_guardrail=output_guardrail,
            reasons=("output_guardrail_blocked",) + output_guardrail.reasons,
        )
    if output_guardrail.status == "failed":
        return GuardedClaudeReplyResult(
            status="failed",
            stage="output_guardrail",
            final_text="",
            input_guardrail=input_guardrail,
            claude_result=claude_result,
            output_guardrail=output_guardrail,
            reasons=("output_guardrail_failed",) + output_guardrail.reasons,
        )

    return GuardedClaudeReplyResult(
        status="succeeded",
        stage="completed",
        final_text=claude_result.text,
        input_guardrail=input_guardrail,
        claude_result=claude_result,
        output_guardrail=output_guardrail,
        reasons=(
            "input_guardrail_passed",
            "claude_invocation_succeeded",
            "output_guardrail_passed",
        ),
    )


def _metadata(flow_input: GuardedClaudeReplyInput) -> dict[str, Any]:
    return {
        "session_id": flow_input.session_id,
        "site_id": flow_input.site_id,
        "user_id": flow_input.user_id,
        **flow_input.context,
    }


def _claude_input(flow_input: GuardedClaudeReplyInput) -> ClaudeInvocationInput:
    return ClaudeInvocationInput(
        use_case="assistant_reply",
        system_prompt=flow_input.system_prompt,
        messages=flow_input.conversation_history
        + (ClaudeMessage(role="user", content=flow_input.user_message),),
        context=_metadata(flow_input),
        max_tokens=flow_input.max_tokens,
        temperature=flow_input.temperature,
    )


def _guardrail_blocks(result: GuardrailsApplyResult) -> bool:
    return result.status == "succeeded" and result.action == "GUARDRAIL_INTERVENED"


def _blocked_text(result: GuardrailsApplyResult) -> str:
    if result.masked_output and result.masked_output.strip():
        return result.masked_output.strip()
    return DEFAULT_BLOCKED_MESSAGE
