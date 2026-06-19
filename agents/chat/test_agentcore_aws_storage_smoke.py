"""
P1 Chat Agent AgentCore Runtime AWS storage smoke test.

This test invokes the deployed AgentCore Runtime with storage_mode=aws,
verifies that DynamoDB checkpoint rows and S3 contract artifacts are written,
and deletes all smoke data.

Usage:
    python3 agents/chat/test_agentcore_aws_storage_smoke.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any


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

from chat_state_store import Boto3ChatStateStore  # noqa: E402
from p2_markdown_loader import P2MarkdownLoadInput, build_p2_markdown_ref  # noqa: E402
from s3_artifact_store import Boto3S3ArtifactStore  # noqa: E402


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"agentcore_aws_storage_smoke_{timestamp}"
    site_id = f"agentcore_aws_storage_site_{timestamp}"
    p2_source_key = f"smoke/agentcore_aws_storage/{timestamp}.md"

    state_store = Boto3ChatStateStore()
    artifact_store = Boto3S3ArtifactStore()
    p2_ref = build_p2_markdown_ref(
        P2MarkdownLoadInput(
            category="services",
            domain="tax_accounting",
            expected_domain="tax_accounting",
            slot_registry=_slot_registry(),
            source_s3_key=p2_source_key,
            source_count=2,
            source_grade="mid",
        )
    )
    cleanup_refs = [
        p2_ref,
        artifact_store.build_artifact_ref(
            "contract_draft",
            site_id=site_id,
            version=1,
        ),
        artifact_store.build_artifact_ref(
            "contract_final",
            site_id=site_id,
            version=1,
        ),
    ]

    try:
        response = _invoke_agentcore(
            session_id=session_id,
            site_id=site_id,
            source_s3_key=p2_source_key,
        )

        metadata = response.get("metadata")
        if not isinstance(metadata, dict):
            print("[FAIL] response metadata missing")
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 1

        checkpoint_ref = metadata.get("checkpoint_ref")
        if not isinstance(checkpoint_ref, dict):
            print("[FAIL] response checkpoint_ref missing")
            print(json.dumps(response, ensure_ascii=False, indent=2))
            return 1

        items = state_store.list_items(session_id)
        item_types = {item.item_type for item in items}
        if {"session_metadata", "checkpoint"} - item_types:
            print(f"[FAIL] DynamoDB expected metadata/checkpoint items, got {item_types}")
            return 1

        draft_ref = artifact_store.build_artifact_ref(
            "contract_draft",
            site_id=site_id,
            version=1,
        )
        draft_body = artifact_store.get_artifact(draft_ref)
        if site_id not in draft_body or "tax_accounting" not in draft_body:
            print("[FAIL] S3 contract draft artifact body mismatch")
            print(draft_body)
            return 1

        print("[OK] AgentCore AWS storage smoke")
        print(
            json.dumps(
                {
                    "session_id": session_id,
                    "site_id": site_id,
                    "runtime_stage": response.get("sessionState", {}).get("stage"),
                    "dynamodb_item_types": sorted(item_types),
                    "contract_draft_uri": draft_ref.uri(),
                    "p2_source_uri": p2_ref.uri(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        state_store.delete_session_items(session_id)
        for ref in cleanup_refs:
            artifact_store.delete_artifact(ref)


def _invoke_agentcore(
    *,
    session_id: str,
    site_id: str,
    source_s3_key: str,
) -> dict[str, Any]:
    runtime_arn = _runtime_arn()
    payload = {
        "sessionId": session_id,
        "inputText": "",
        "sessionAttributes": {
            "action": "graph_smoke",
            "site_id": site_id,
            "user_id": "agentcore_aws_storage_user_001",
            "storage_mode": "aws",
            "category": "services",
            "domain": "tax_accounting",
            "domain_label": "세무/회계",
            "selected_template": "landing/13-tax-accounting",
            "source_s3_key": source_s3_key,
            "seed_mock_p2_markdown": True,
        },
    }
    payload_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    with tempfile.NamedTemporaryFile() as output:
        _run_aws(
            [
                "bedrock-agentcore",
                "invoke-agent-runtime",
                "--agent-runtime-arn",
                runtime_arn,
                "--payload",
                payload_text,
                "--content-type",
                "application/json",
                "--accept",
                "application/json",
                "--cli-binary-format",
                "raw-in-base64-out",
                output.name,
            ]
        )
        output.seek(0)
        raw = output.read().decode("utf-8")

    try:
        response = json.loads(raw)
    except json.JSONDecodeError:
        print("[FAIL] AgentCore response was not JSON")
        print(raw)
        raise

    if "error" in response:
        print("[FAIL] AgentCore returned error")
        print(json.dumps(response, ensure_ascii=False, indent=2))
        raise RuntimeError("agentcore_invoke_error")

    return response


def _runtime_arn() -> str:
    runtime_name = _normalize_runtime_name(
        os.environ.get(
            "HEZO_AGENTCORE_RUNTIME_NAME",
            os.environ.get("HEZO_AGENTCORE_RUNTIME", "hezo-chat-agent-dev"),
        )
    )
    runtime_id = _run_aws(
        [
            "bedrock-agentcore-control",
            "list-agent-runtimes",
            "--query",
            f"agentRuntimes[?agentRuntimeName=='{runtime_name}'].agentRuntimeId | [0]",
            "--output",
            "text",
        ]
    ).stdout.strip()
    if not runtime_id or runtime_id == "None":
        raise RuntimeError(f"agentcore_runtime_not_found:{runtime_name}")

    runtime_json = _run_aws(
        [
            "bedrock-agentcore-control",
            "get-agent-runtime",
            "--agent-runtime-id",
            runtime_id,
            "--output",
            "json",
        ]
    ).stdout
    runtime = json.loads(runtime_json)
    if runtime.get("status") != "READY":
        raise RuntimeError(f"agentcore_runtime_not_ready:{runtime.get('status')}")
    arn = str(runtime.get("agentRuntimeArn", ""))
    if not arn:
        raise RuntimeError("agentcore_runtime_arn_missing")
    return arn


def _run_aws(args: list[str]) -> subprocess.CompletedProcess[str]:
    command = ["aws"]
    profile = os.environ.get("AWS_PROFILE")
    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    if profile:
        command.extend(["--profile", profile])
    command.extend(["--region", region, *args])
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print("[FAIL] aws command failed", file=sys.stderr)
        print(" ".join(command), file=sys.stderr)
        if result.stdout:
            print("[stdout]", file=sys.stderr)
            print(result.stdout, file=sys.stderr)
        if result.stderr:
            print("[stderr]", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
        result.check_returncode()
    return result


def _normalize_runtime_name(raw: str) -> str:
    normalized = "".join(ch for ch in raw.replace("-", "_") if ch.isalnum() or ch == "_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"hezo_{normalized}"
    return normalized[:48]


def _slot_registry() -> dict[str, dict[str, Any]]:
    return {
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


if __name__ == "__main__":
    raise SystemExit(main())
