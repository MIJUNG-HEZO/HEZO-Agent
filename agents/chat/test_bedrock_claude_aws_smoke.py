"""
P1 Chat Agent Bedrock Claude AWS smoke test.

This test invokes Claude Sonnet 4.5 once through Bedrock Runtime Converse API.
It requires AWS CLI-compatible credentials to be configured locally.

Usage:
    python3 agents/chat/test_bedrock_claude_aws_smoke.py
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

from bedrock_claude_adapter import (  # noqa: E402
    Boto3BedrockClaudeInvoker,
    ClaudeInvocationInput,
    ClaudeMessage,
)


def main() -> int:
    invoker = Boto3BedrockClaudeInvoker()
    result = invoker.invoke(
        ClaudeInvocationInput(
            use_case="assistant_reply",
            system_prompt="당신은 HEZO P1 채팅 에이전트입니다. 한 문장으로만 답하세요.",
            messages=(ClaudeMessage(role="user", content="테스트 응답으로 OK만 말해줘."),),
            max_tokens=32,
            temperature=0,
        )
    )

    if result.status != "succeeded" or not result.text.strip():
        print("[FAIL] Bedrock Claude smoke invocation failed")
        print(result.to_dict())
        return 1

    print("[OK] Bedrock Claude smoke invocation")
    print(result.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
