"""
채팅 에이전트 로컬 smoke test.

실제 LangGraph, Bedrock, DynamoDB, S3 연동 없이
P1 채팅 에이전트 스켈레톤의 stage/config/mock state를 검증한다.

사용법:
    python3 agents/chat/test_agent_local.py
"""

from __future__ import annotations

import pathlib
import re
import sys


CONFIG_FILE = pathlib.Path(__file__).parent / "agent_config.yaml"

REQUIRED_STAGES = [
    "domain_selection",
    "p2_markdown_review",
    "proactive_questioning",
    "contract_compile",
    "contract_quality_check",
]

REQUIRED_REVIEW_FIELDS = [
    "p2_confidence",
    "p1_markdown_review_status",
    "p1_markdown_review_score",
    "p2_markdown_usable_for_questions",
]


def _read_config_text() -> str:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] config 파일 없음: {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    return CONFIG_FILE.read_text(encoding="utf-8")


def _assert_required_tokens(config_text: str, tokens: list[str], label: str) -> list[str]:
    errors: list[str] = []
    for token in tokens:
        if token not in config_text:
            errors.append(f"{label} 누락: {token}")
    return errors


def _extract_number(config_text: str, field: str) -> float | None:
    pattern = rf"{re.escape(field)}:\s*([0-9]+(?:\.[0-9]+)?)"
    match = re.search(pattern, config_text)
    if not match:
        return None
    return float(match.group(1))


def _validate_review_policy(config_text: str) -> list[str]:
    errors: list[str] = []

    p2_confidence = _extract_number(config_text, "p2_confidence")
    p1_score = _extract_number(config_text, "p1_markdown_review_score")

    if p2_confidence is None:
        errors.append("p2_confidence 값이 없습니다.")
    elif p2_confidence < 0.70:
        errors.append(f"p2_confidence={p2_confidence} 입니다. P2 컷 0.70 이상이어야 합니다.")

    if p1_score is None:
        errors.append("p1_markdown_review_score 값이 없습니다.")
    elif p1_score < 0.70:
        errors.append(f"p1_markdown_review_score={p1_score} 입니다. 임시 컷 0.70 이상이어야 합니다.")

    if "p1_markdown_review_status: passed" not in config_text:
        errors.append("mock state는 passed 검수 상태를 포함해야 합니다.")

    if "p2_markdown_usable_for_questions: true" not in config_text:
        errors.append("mock state는 적극적 질의 사용 가능 상태여야 합니다.")

    return errors


def main() -> None:
    config_text = _read_config_text()

    print(f"\n{'=' * 60}")
    print("  HEZO Chat Agent 스켈레톤 로컬 검증")
    print(f"  Config: {CONFIG_FILE}")
    print(f"{'=' * 60}\n")

    errors: list[str] = []

    print("[1] stage 정의 검증")
    stage_errors = _assert_required_tokens(config_text, REQUIRED_STAGES, "stage")
    if stage_errors:
        errors.extend(stage_errors)
        for error in stage_errors:
            print(f"  [FAIL] {error}")
    else:
        print("  [OK] 필수 stage 정의 확인")

    print("\n[2] P2 markdown review 필드 검증")
    field_errors = _assert_required_tokens(config_text, REQUIRED_REVIEW_FIELDS, "review field")
    if field_errors:
        errors.extend(field_errors)
        for error in field_errors:
            print(f"  [FAIL] {error}")
    else:
        print("  [OK] P2 markdown review 필드 확인")

    print("\n[3] review policy mock 값 검증")
    policy_errors = _validate_review_policy(config_text)
    if policy_errors:
        errors.extend(policy_errors)
        for error in policy_errors:
            print(f"  [FAIL] {error}")
    else:
        print("  [OK] review policy mock 값 확인")

    print(f"\n{'=' * 60}")
    if errors:
        print(f"  결과: FAIL ({len(errors)}개 오류)")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)

    print("  결과: PASS — 채팅 에이전트 스켈레톤 검증 완료")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
