"""
P1 Chat Agent intent classifier AWS smoke test.

This test invokes Claude Sonnet 4.5 through Bedrock Runtime Converse API
and verifies that the chat intent classifier can gate slot storage.

Usage:
    python3 agents/chat/test_chat_intent_classifier_aws_smoke.py
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
from chat_intent_guard import (  # noqa: E402
    ChatIntentClassifierInput,
    ClaudeChatIntentClassifier,
)


SLOT_REGISTRY = {
    "business_name": {
        "label": "업체명",
        "required": True,
        "question_hint": "업체명이 뭔가요?",
    },
    "core_services": {
        "label": "핵심 서비스",
        "required": True,
        "question_hint": "핵심 서비스는 무엇인가요?",
    },
}


def main() -> int:
    classifier = ClaudeChatIntentClassifier(Boto3BedrockClaudeInvoker())

    on_topic = classifier.classify(
        ChatIntentClassifierInput(
            message="기장 대리와 종합소득세 신고를 주로 합니다.",
            current_question="핵심 서비스는 무엇인가요?",
            answered_slot="core_services",
            domain="tax_accounting",
            domain_label="세무/회계",
            slot_registry=SLOT_REGISTRY,
        )
    )
    off_topic = classifier.classify(
        ChatIntentClassifierInput(
            message="나 배고파. 점심 메뉴 추천해줘.",
            current_question="핵심 서비스는 무엇인가요?",
            answered_slot="core_services",
            domain="tax_accounting",
            domain_label="세무/회계",
            slot_registry=SLOT_REGISTRY,
        )
    )

    if on_topic.intent != "on_topic" or on_topic.store_allowed is not True:
        print("[FAIL] on-topic intent classification failed")
        print(on_topic.to_dict())
        return 1

    if off_topic.intent == "on_topic" or off_topic.store_allowed is not False:
        print("[FAIL] off-topic intent classification failed")
        print(off_topic.to_dict())
        return 1

    print("[OK] Bedrock intent classifier smoke")
    print({"on_topic": on_topic.to_dict(), "off_topic": off_topic.to_dict()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
