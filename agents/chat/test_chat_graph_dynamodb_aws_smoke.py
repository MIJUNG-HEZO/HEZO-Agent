"""
P1 Chat Agent graph DynamoDB checkpoint AWS smoke test.

This test runs the deterministic chat graph with Boto3ChatStateStore, verifies
that session metadata and checkpoint rows are written to the dev DynamoDB table,
loads the latest checkpoint back, and deletes all smoke rows.

Usage:
    python3 agents/chat/test_chat_graph_dynamodb_aws_smoke.py
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

from chat_graph import ChatGraphState, run_chat_graph  # noqa: E402
from chat_state_store import Boto3ChatStateStore  # noqa: E402


SLOT_REGISTRY = {
    "business_name": {
        "label": "업체명",
        "required": True,
        "question_hint": "업체명은 무엇인가요?",
    },
    "core_services": {
        "label": "핵심 서비스",
        "required": True,
        "question_hint": "핵심 서비스는 무엇인가요?",
    },
    "contact_method": {
        "label": "상담 방식",
        "required": True,
        "question_hint": "상담 방식은 무엇인가요?",
    },
}


def main() -> int:
    store = Boto3ChatStateStore()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"smoke_graph_ddb_{timestamp}"

    try:
        final_state = run_chat_graph(
            ChatGraphState(
                session_id=session_id,
                site_id=f"smoke_site_{timestamp}",
                user_id="smoke_user_001",
                stage="domain_selection",
                category="landing",
                domain="tax_accounting",
                domain_label="세무/회계",
                selected_template="landing/13-tax-accounting",
                slot_registry=SLOT_REGISTRY,
                known_answers={"business_name": "한빛 세무회계"},
                missing_slots=("core_services", "contact_method"),
            ),
            state_store=store,
        )

        checkpoint_ref = final_state.checkpoint_ref.get("checkpoint", {})
        metadata_ref = final_state.checkpoint_ref.get("metadata", {})
        if metadata_ref.get("sk") != "META":
            print("[FAIL] graph did not save session metadata ref")
            print(final_state.to_dict())
            return 1
        if not str(checkpoint_ref.get("sk", "")).startswith("CHECKPOINT#"):
            print("[FAIL] graph did not save checkpoint ref")
            print(final_state.to_dict())
            return 1

        latest = store.load_latest_checkpoint(session_id)
        if latest is None:
            print("[FAIL] latest checkpoint was not saved")
            return 1
        if latest.stage != "bedrock_guardrails":
            print(f"[FAIL] latest checkpoint stage mismatch: {latest.stage}")
            return 1
        if latest.state.get("stage") != "bedrock_guardrails":
            print("[FAIL] latest checkpoint state mismatch")
            print(latest.state)
            return 1

        items = store.list_items(session_id)
        item_types = {item.item_type for item in items}
        if {"session_metadata", "checkpoint"} - item_types:
            print(f"[FAIL] expected metadata/checkpoint items, got {item_types}")
            return 1

        print("[OK] Chat graph DynamoDB checkpoint smoke")
        print({"session_id": session_id, "checkpoint_ref": final_state.checkpoint_ref})
        return 0
    finally:
        store.delete_session_items(session_id)


if __name__ == "__main__":
    raise SystemExit(main())
