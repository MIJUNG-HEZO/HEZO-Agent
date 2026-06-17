"""
P1 Chat Agent guarded Claude AWS smoke test.

This test runs Guardrail INPUT -> Claude -> Guardrail OUTPUT once.
It requires AWS CLI-compatible credentials to be configured locally.

Usage:
    python3 agents/chat/test_guarded_claude_aws_smoke.py
"""

from __future__ import annotations

import os
import pathlib
import sys


CHAT_DIR = pathlib.Path(__file__).parent
REPO_ROOT = CHAT_DIR.parents[1]
sys.path.insert(0, str(CHAT_DIR))


def _load_env_example() -> None:
    env_file = REPO_ROOT / "infra" / "chat" / "env.example"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env_example()

from bedrock_claude_adapter import Boto3BedrockClaudeInvoker  # noqa: E402
from bedrock_guardrails_adapter import Boto3BedrockGuardrailsClient  # noqa: E402
from guarded_claude_flow import GuardedClaudeReplyInput, run_guarded_claude_reply  # noqa: E402


def main() -> int:
    result = run_guarded_claude_reply(
        GuardedClaudeReplyInput(
            user_message="HEZO 채팅 에이전트 guarded flow smoke test입니다. OK라고만 답하세요.",
            system_prompt="당신은 HEZO P1 채팅 에이전트입니다. 사용자의 요청에 한 단어로만 답하세요.",
            session_id="smoke_session_001",
            site_id="smoke_site_001",
            user_id="smoke_user_001",
            context={"test": "guarded_claude_aws_smoke"},
            max_tokens=32,
            temperature=0,
        ),
        guardrails_client=Boto3BedrockGuardrailsClient(),
        claude_invoker=Boto3BedrockClaudeInvoker(),
    )

    if result.status != "succeeded" or result.stage != "completed" or not result.final_text.strip():
        print("[FAIL] Guarded Claude AWS smoke flow failed")
        print(result.to_dict())
        return 1

    print("[OK] Guarded Claude AWS smoke flow")
    print(result.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
