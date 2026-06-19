"""
P1 Chat Agent HTTP turn DynamoDB message AWS smoke test.

This test invokes the chat_turn handler with storage_mode=aws, verifies that
user/assistant message rows are written to the dev DynamoDB table, and deletes
all smoke rows.

Usage:
    python3 agents/chat/test_chat_message_dynamodb_aws_smoke.py
"""

from __future__ import annotations

import os
import pathlib
import sys
from datetime import datetime, timezone


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

from chat_http_handler import handle_agentcore_payload  # noqa: E402
from chat_state_store import Boto3ChatStateStore  # noqa: E402


def main() -> int:
    store = Boto3ChatStateStore()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"smoke_message_{timestamp}"

    try:
        response = handle_agentcore_payload(
            {
                "sessionId": session_id,
                "inputText": "",
                "sessionAttributes": {
                    "action": "chat_turn",
                    "storage_mode": "aws",
                    "answered_slot": "core_services",
                    "answer": "기장 대리, 종합소득세 신고",
                    "intent": "on_topic",
                },
            }
        )

        metadata = response["metadata"]
        recent_messages = store.load_recent_messages(session_id, limit=2)
        if metadata["turn_status"] != "answer_accepted":
            print("[FAIL] chat_turn did not accept the on-topic answer")
            print(metadata)
            return 1
        if len(metadata.get("message_refs", [])) != 2:
            print("[FAIL] handler did not return two message refs")
            print(metadata)
            return 1
        if [message.role for message in recent_messages] != ["user", "assistant"]:
            print("[FAIL] DynamoDB recent messages mismatch")
            print([message.__dict__ for message in recent_messages])
            return 1

        off_topic_session_id = f"{session_id}_off_topic"
        off_topic = handle_agentcore_payload(
            {
                "sessionId": off_topic_session_id,
                "inputText": "",
                "sessionAttributes": {
                    "action": "chat_turn",
                    "storage_mode": "aws",
                    "answered_slot": "core_services",
                    "answer": "나 배고파. 점심 메뉴 추천해줘.",
                    "intent": "off_topic",
                },
            }
        )
        if off_topic["metadata"].get("message_refs") != []:
            print("[FAIL] off-topic turn should not store messages")
            print(off_topic["metadata"])
            return 1
        if store.load_recent_messages(off_topic_session_id):
            print("[FAIL] off-topic session should have no messages")
            return 1

        print("[OK] Chat turn DynamoDB message smoke")
        print({"session_id": session_id, "message_refs": metadata["message_refs"]})
        return 0
    finally:
        store.delete_session_items(session_id)
        store.delete_session_items(f"{session_id}_off_topic")


if __name__ == "__main__":
    raise SystemExit(main())
