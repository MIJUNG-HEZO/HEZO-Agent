"""
P1 Chat Agent graph S3 pipeline AWS smoke test.

This test writes a temporary P2 markdown object to hezo-wiki, runs the
deterministic chat graph with Boto3S3ArtifactStore, verifies that Contract
draft/final artifacts are saved to hezo-artifacts, and deletes all smoke
objects.

Usage:
    python3 agents/chat/test_chat_graph_s3_pipeline_aws_smoke.py
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
from p2_markdown_loader import P2MarkdownLoadInput, build_p2_markdown_ref  # noqa: E402
from s3_artifact_store import ArtifactPayload, Boto3S3ArtifactStore  # noqa: E402


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
    store = Boto3S3ArtifactStore()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    site_id = f"smoke_site_{timestamp}"
    category = "smoke"
    domain = f"tax_accounting_{timestamp.lower()}"
    source_s3_key = f"smoke/p2_p4_graph_pipeline/{timestamp}.md"
    p2_ref = build_p2_markdown_ref(
        P2MarkdownLoadInput(
            category=category,
            domain=domain,
            expected_domain=domain,
            slot_registry=SLOT_REGISTRY,
            source_s3_key=source_s3_key,
            source_count=2,
            source_grade="mid",
        )
    )
    store.put_artifact(
        ArtifactPayload(
            ref=p2_ref,
            body=f"""
---
domain: {domain}
category: {category}
template_no: 13
label: 세무/회계
confidence: 0.82
volatility: low
last_updated: 2026-06-18
source_urls:
  - https://example.com/smoke-1
  - https://example.com/smoke-2
---

# 세무/회계 도메인 지식

## 1. 핵심 서비스 범위 [S1]
세무/회계 홈페이지는 핵심 서비스와 상담 전환 정보를 명확히 제공해야 합니다.

## 2. 상담 전환 정보 [S2]
문의 방식과 상담 가능 시간을 쉽게 확인할 수 있어야 합니다.

## 출처
- [S1] P2/P4 graph pipeline smoke source
- [S2] HEZO dev smoke test
""",
        )
    )

    artifact_refs = []
    try:
        final_state = run_chat_graph(
            ChatGraphState(
                session_id=f"smoke_session_{timestamp}",
                site_id=site_id,
                user_id="smoke_user_001",
                stage="domain_selection",
                category=category,
                domain=domain,
                domain_label="세무/회계",
                selected_template="landing/13-tax-accounting",
                p2_source_s3_key=source_s3_key,
                p2_source_count=2,
                p2_source_grade="mid",
                slot_registry=SLOT_REGISTRY,
                known_answers={
                    "business_name": "한빛 세무회계",
                    "contact_method": "전화 상담",
                },
                missing_slots=("core_services",),
            ),
            artifact_store=store,
            seed_mock_p2_markdown=False,
        )
        artifact_refs = final_state.artifact_refs
        artifact_kinds = {artifact["artifact_kind"] for artifact in artifact_refs}
        if {"enriched_markdown", "contract_draft", "contract_final"} - artifact_kinds:
            print("[FAIL] graph did not save enriched markdown and draft/final contract artifacts")
            print(final_state.to_dict())
            return 1

        for artifact in artifact_refs:
            stored = store.get_artifact(
                _build_artifact_ref_for_smoke(
                    store,
                    artifact["artifact_kind"],
                    site_id,
                    category,
                    domain,
                )
            )
            if "tax_accounting" not in stored:
                print("[FAIL] stored graph artifact body mismatch")
                print(artifact)
                return 1
    finally:
        store.delete_artifact(p2_ref)
        for artifact in artifact_refs:
            store.delete_artifact(
                _build_artifact_ref_for_smoke(
                    store,
                    artifact["artifact_kind"],
                    site_id,
                    category,
                    domain,
                )
            )

    print("[OK] P2/P4 graph S3 pipeline smoke")
    print({"p2_ref": p2_ref.uri(), "artifact_refs": artifact_refs})
    return 0


def _build_artifact_ref_for_smoke(
    store: Boto3S3ArtifactStore,
    artifact_kind: str,
    site_id: str,
    category: str,
    domain: str,
):
    if artifact_kind == "enriched_markdown":
        return store.build_artifact_ref(
            artifact_kind,
            category=category,
            domain=domain,
        )
    return store.build_artifact_ref(
        artifact_kind,
        site_id=site_id,
        version=1,
    )


if __name__ == "__main__":
    raise SystemExit(main())
