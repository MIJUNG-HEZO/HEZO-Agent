"""
P1 Chat Agent DynamoDB adapter AWS smoke test.

This test writes a small session state set to the dev DynamoDB table, reads it
back, and deletes it. It requires AWS CLI-compatible credentials to be
configured locally.

Usage:
    python3 agents/chat/test_dynamodb_aws_smoke.py
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

from chat_state_store import (  # noqa: E402
    Boto3ChatStateStore,
    ChatCheckpoint,
    ChatMessage,
    GuardrailSummary,
    SessionMetadata,
)


def main() -> int:
    store = Boto3ChatStateStore()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"smoke_{timestamp}"

    try:
        store.save_session_metadata(
            SessionMetadata(
                session_id=session_id,
                user_id="smoke_user",
                site_id="smoke_site",
                stage="contract_quality_check",
                domain="tax_accounting",
            )
        )
        store.append_message(
            ChatMessage(
                session_id=session_id,
                message_id="msg_001",
                role="user",
                content="DynamoDB smoke test",
                created_at=timestamp,
            )
        )
        store.append_message(
            ChatMessage(
                session_id=session_id,
                message_id="msg_002",
                role="assistant",
                content="DynamoDB smoke reply",
                created_at=f"{timestamp}~assistant",
            )
        )
        store.save_checkpoint(
            ChatCheckpoint(
                session_id=session_id,
                stage="contract_quality_check",
                version=1,
                state={"quality_status": "smoke"},
            )
        )
        store.save_guardrail_result(
            GuardrailSummary(
                session_id=session_id,
                target="contract_draft",
                action="NONE",
                store_allowed=True,
                reasons=("smoke_guardrail_passed",),
                created_at=timestamp,
            )
        )

        latest = store.load_latest_checkpoint(session_id)
        recent_messages = store.load_recent_messages(session_id, limit=2)
        items = store.list_items(session_id)
        if latest is None or latest.state.get("quality_status") != "smoke":
            print("[FAIL] latest checkpoint mismatch")
            return 1
        if [message.message_id for message in recent_messages] != ["msg_001", "msg_002"]:
            print("[FAIL] recent message query mismatch")
            return 1
        if len(items) != 5:
            print(f"[FAIL] expected 5 items, got {len(items)}")
            return 1

        print("[OK] DynamoDB write/read/delete smoke test")
        print(f"session_id={session_id}")
        return 0
    finally:
        store.delete_session_items(session_id)


if __name__ == "__main__":
    raise SystemExit(main())
