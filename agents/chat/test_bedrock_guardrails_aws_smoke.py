"""
P1 Chat Agent Bedrock Guardrails AWS smoke test.

This test calls the dev Bedrock Guardrail once through ApplyGuardrail.
It requires AWS CLI-compatible credentials to be configured locally.

Usage:
    python3 agents/chat/test_bedrock_guardrails_aws_smoke.py
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

from bedrock_guardrails_adapter import (  # noqa: E402
    Boto3BedrockGuardrailsClient,
    GuardrailsApplyInput,
)


def main() -> int:
    client = Boto3BedrockGuardrailsClient()
    result = client.apply_guardrail(
        GuardrailsApplyInput(
            target="user_input",
            source="INPUT",
            content="HEZO 채팅 에이전트 AWS Guardrails smoke test입니다.",
            metadata={"test": "bedrock_guardrails_aws_smoke"},
        )
    )

    if result.status != "succeeded" or result.action != "NONE" or not result.store_allowed:
        print("[FAIL] Bedrock Guardrails smoke apply failed")
        print(result.to_dict())
        return 1

    print("[OK] Bedrock Guardrails smoke apply")
    print(result.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
