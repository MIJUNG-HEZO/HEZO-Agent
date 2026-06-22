"""크롤 람다 핸들러 (얇은 어댑터) — collect() 호출만 한다.

이벤트(category·domain)를 받아 순수 로직 collect()로 넘기고 결과를 반환할 뿐,
검색·크롤·저장 로직은 0줄이다. ECS 전환 시 entrypoint도 같은 collect()를 호출한다.

event 예: {"category": "landing", "domain": "tax_accounting", "num": 10}
"""
from __future__ import annotations

from typing import Any

from agents.wiki.collect import collect


def handler(event: dict[str, Any], context: Any = None) -> dict:
    return collect(
        event["category"],
        event["domain"],
        num=int(event.get("num", 20)),
    )
