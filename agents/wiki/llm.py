"""HEZO Wiki (P2) Bedrock LLM 래퍼 — converse 단발 호출 (순수 로직, 테스트 주입).

생성·검수에서 쓰는 단일 호출 헬퍼. 팀 어댑터(agents/chat/bedrock_claude_adapter.py)와
같은 converse 패턴을 따르되, 타 팀(P1) 파일에 의존하지 않도록 wiki 자체 래퍼로 둔다.
테스트는 client(또는 LLM 객체)를 주입해 boto3 없이 검증한다.

모델 id는 배포 환경변수로 주입한다(프로덕션은 sonnet-4-6 글로벌 추론 프로파일).
키/자격증명은 코드/깃에 두지 않고 AWS 프로필·환경변수로만 참조한다.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any

# 추론 프로파일을 우선한다(ap-northeast-2 cross-region 호출은 프로파일 id 필요 — 팀 chat 어댑터와 동일).
# 프로덕션은 env로 sonnet-4-6 프로파일을 덮어쓴다.
DEFAULT_MODEL_ID = os.environ.get(
    "HEZO_BEDROCK_INFERENCE_PROFILE_ID",
    os.environ.get("HEZO_BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
)
READ_TIMEOUT = 300  # 생성 콜이 길 수 있어 read_timeout 여유


@dataclass(frozen=True)
class LLMResult:
    """converse 호출 정규화 결과."""

    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    ok: bool
    reason: str = ""
    stop_reason: str = ""  # converse stopReason: end_turn / max_tokens(=잘림) 등


class BedrockLLM:
    """Bedrock Runtime converse 단발 호출기 (client 주입 가능)."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        model_id: str | None = None,
        region_name: str | None = None,
    ) -> None:
        self.model_id = model_id or DEFAULT_MODEL_ID
        if client is not None:
            self._client = client
            return
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.config import Config  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("boto3_required_for_wiki_llm") from error

        session_kwargs: dict[str, str] = {}
        profile = os.environ.get("AWS_PROFILE")
        if profile:
            session_kwargs["profile_name"] = profile
        session = boto3.Session(**session_kwargs)
        self._client = session.client(
            "bedrock-runtime",
            region_name=region_name
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2"),
            config=Config(read_timeout=READ_TIMEOUT),
        )

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> LLMResult:
        """system+user 1턴 converse 호출. 실패 시 ok=False(이유 포함)."""
        started = time.perf_counter()
        try:
            resp = self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )
        except Exception as error:
            return LLMResult("", 0, 0, int((time.perf_counter() - started) * 1000), False, _error_reason(error))

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage = resp.get("usage", {})
        return LLMResult(
            text=_extract_text(resp),
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            latency_ms=latency_ms,
            ok=True,
            stop_reason=str(resp.get("stopReason", "")),
        )


def _extract_text(response: dict[str, Any]) -> str:
    content = response.get("output", {}).get("message", {}).get("content", [])
    texts = [block.get("text", "") for block in content if isinstance(block, dict)]
    return "\n".join(t for t in texts if t).strip()


def _error_reason(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code:
            return f"bedrock_{str(code).lower()}"
    return "bedrock_invocation_failed"
