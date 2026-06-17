"""
P1 Chat Agent S3 adapter AWS smoke test.

This test writes a small object to the dev chat bucket, reads it back, and
deletes it. It requires AWS CLI-compatible credentials to be configured locally.

Usage:
    python3 agents/chat/test_s3_aws_smoke.py
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

from s3_artifact_store import ArtifactPayload, Boto3S3ArtifactStore  # noqa: E402


def main() -> int:
    store = Boto3S3ArtifactStore()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ref = store.build_artifact_ref(
        "chat_transcript",
        session_id=f"smoke_{timestamp}",
        version=1,
    )

    payload = ArtifactPayload(
        ref=ref,
        body={
            "test": "p1_s3_artifact_store_smoke",
            "created_at": timestamp,
        },
    )

    store.put_artifact(payload)
    stored = store.get_artifact(ref)
    store.delete_artifact(ref)

    if "p1_s3_artifact_store_smoke" not in stored:
        print("[FAIL] stored object body mismatch")
        return 1

    print("[OK] S3 write/read/delete smoke test")
    print(ref.uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
