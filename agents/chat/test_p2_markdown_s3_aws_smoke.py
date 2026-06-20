"""
P1 Chat Agent P2 markdown S3 loader AWS smoke test.

This test writes a temporary P2 markdown artifact to the dev P2 markdown
bucket, reads it through the loader, parses it, and deletes it.

Usage:
    python3 agents/chat/test_p2_markdown_s3_aws_smoke.py
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

from p2_markdown_loader import P2MarkdownLoadInput, build_p2_markdown_ref, load_p2_markdown_from_s3  # noqa: E402
from p2_markdown_parser import parse_p2_markdown  # noqa: E402
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
    source_s3_key = f"smoke/p2_markdown_loader/{timestamp}.md"
    load_input = P2MarkdownLoadInput(
        category="landing",
        domain="tax_accounting",
        expected_domain="tax_accounting",
        slot_registry=SLOT_REGISTRY,
        source_s3_key=source_s3_key,
        source_count=2,
        source_grade="mid",
    )
    ref = build_p2_markdown_ref(load_input)

    store.put_artifact(
        ArtifactPayload(
            ref=ref,
            body="""
---
domain: tax_accounting
category: landing
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
- [S1] P2 markdown S3 loader smoke source
- [S2] HEZO dev smoke test
""",
        )
    )

    try:
        loaded = load_p2_markdown_from_s3(load_input, store)
        parsed = parse_p2_markdown(loaded.parse_input)
    finally:
        store.delete_artifact(ref)

    if parsed.parse_status != "passed":
        print("[FAIL] P2 markdown S3 loader smoke parse failed")
        print(parsed.to_dict())
        return 1

    print("[OK] P2 markdown S3 loader smoke")
    print(loaded.ref.uri())
    print(parsed.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
