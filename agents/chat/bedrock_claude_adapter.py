"""Bedrock Claude invocation adapters for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import time
from typing import Any, Literal, Protocol


ClaudeUseCase = Literal[
    "question_enrichment",
    "contract_enrichment",
    "assistant_reply",
    "intent_classification",
]
InvocationStatus = Literal["succeeded", "failed"]

FOUNDATION_MODEL_ID = "anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_MODEL_ID = os.environ.get(
    "HEZO_BEDROCK_INFERENCE_PROFILE_ID",
    os.environ.get("HEZO_BEDROCK_MODEL_ID", FOUNDATION_MODEL_ID),
)
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.2

# 관측(P5): LLM 호출마다 비용·토큰·지연을 CloudWatch(HEZO/Agents)로 전송.
# libs 미탑재(일부 테스트)면 no-op으로 떨어져 chat 동작에는 영향 없음.
try:
    from libs.telemetry import init_telemetry, record_llm_usage

    init_telemetry("chat", region=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"))
except Exception:  # noqa: BLE001 — libs 없음/자격증명 없음 → 비용기록 없이 동작
    def record_llm_usage(*_a: Any, **_k: Any) -> None:  # type: ignore[misc]
        return None


def _short_model(model_id: str) -> str:
    """모델 id → telemetry 단가표 키(sonnet/haiku/opus)."""
    m = model_id.lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    return "sonnet"

INJECTION_PATTERNS = [
    "이전 지시 무시",
    "앞의 지시 무시",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "print your instructions",
]


def _default_model_id() -> str:
    return os.environ.get(
        "HEZO_BEDROCK_INFERENCE_PROFILE_ID",
        os.environ.get("HEZO_BEDROCK_MODEL_ID", FOUNDATION_MODEL_ID),
    )


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
    model_id: str = field(default_factory=_default_model_id)
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


class Boto3BedrockClaudeInvoker:
    """AWS Bedrock Runtime Claude invoker using the Converse API."""

    def __init__(self, client: Any | None = None, region_name: str | None = None) -> None:
        if client is not None:
            self._client = client
            return

        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("boto3_required_for_bedrock_claude_invoker") from error

        session_kwargs: dict[str, str] = {}
        profile_name = os.environ.get("AWS_PROFILE")
        if profile_name:
            session_kwargs["profile_name"] = profile_name
        session = boto3.Session(**session_kwargs)
        self._client = session.client(
            "bedrock-runtime",
            region_name=region_name or os.environ.get("AWS_REGION"),
        )

    def invoke(self, invocation_input: ClaudeInvocationInput) -> ClaudeInvocationResult:
        validation_error = _validate_invocation_input(invocation_input)
        if validation_error:
            return _failed_result(invocation_input.model_id, validation_error)

        started = time.perf_counter()
        try:
            response = self._client.converse(
                modelId=invocation_input.model_id,
                system=[{"text": invocation_input.system_prompt}],
                messages=[
                    {
                        "role": message.role,
                        "content": [{"text": message.content}],
                    }
                    for message in invocation_input.messages
                ],
                inferenceConfig={
                    "maxTokens": invocation_input.max_tokens,
                    "temperature": invocation_input.temperature,
                },
            )
        except Exception as error:
            return _failed_result(
                invocation_input.model_id,
                _bedrock_error_reason(error),
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        output_text = _extract_converse_text(response)
        usage = response.get("usage", {})
        metrics = response.get("metrics", {})
        in_tok = int(usage.get("inputTokens", 0))
        out_tok = int(usage.get("outputTokens", 0))
        final_latency = int(metrics.get("latencyMs", latency_ms))

        # 관측(P5): 비용·토큰·지연 기록 (chat 에이전트로 분류)
        record_llm_usage("chat", _short_model(invocation_input.model_id), in_tok, out_tok, ms=final_latency)

        return ClaudeInvocationResult(
            status="succeeded",
            text=output_text,
            model_id=invocation_input.model_id,
            usage=ClaudeUsage(input_tokens=in_tok, output_tokens=out_tok),
            latency_ms=final_latency,
            reasons=("bedrock_invocation_succeeded", invocation_input.use_case),
        )


def _validate_invocation_input(invocation_input: ClaudeInvocationInput) -> str | None:
    if invocation_input.use_case not in {
        "question_enrichment",
        "contract_enrichment",
        "assistant_reply",
        "intent_classification",
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
    if invocation_input.use_case == "intent_classification":
        return (
            '{"intent":"on_topic","confidence":1.0,'
            '"reasons":["mock_intent_classification"],'
            '"normalized_answer_candidate":"요청 내용을 확인했습니다.",'
            '"redirect_message":null}'
        )
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


def _extract_converse_text(response: dict[str, Any]) -> str:
    content = response.get("output", {}).get("message", {}).get("content", [])
    texts = [block.get("text", "") for block in content if isinstance(block, dict)]
    return "\n".join(text for text in texts if text).strip()


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
    return "bedrock_invocation_failed"
