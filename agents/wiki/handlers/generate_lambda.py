"""생성·검수·저장 람다 핸들러 (얇은 어댑터) — generate_and_store() 호출만 한다.

이벤트(category·domain·raw_key)를 받아 순수 로직 generate_and_store()로 넘기고 결과를
반환할 뿐, 생성·검수·저장 로직은 0줄이다. ECS 전환 시 entrypoint도 같은 함수를 호출한다.

event 예: {"category": "landing", "domain": "tax_accounting",
           "raw_key": "raw/landing/tax_accounting/2026-06-18.json"}
"""
from __future__ import annotations

from typing import Any

from agents.wiki.pipeline import generate_and_store


def handler(event: dict[str, Any], context: Any = None) -> dict:
    return generate_and_store(
        event["category"],
        event["domain"],
        event["raw_key"],
    )
