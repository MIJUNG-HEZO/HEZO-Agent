"""
HEZO 관측 키트 (P5)

- 모든 에이전트가 import 해서 쓰는 공용 텔레메트리.
- 에이전트가 추가하는 코드는 딱 2줄: init 1번 + AI 호출마다 record 1번.
- 지금은 출력을 "콘솔(화면)"로 보냄 → AWS 없이 테스트 가능.
  나중에 출력만 OTLP(ADOT)로 한 줄 바꾸면 됨. (아래 # TODO 표시 위치)
"""

from __future__ import annotations

import json
import time

# 모델별 1K 토큰당 단가(USD). 비용 계산용 — 나중에 정확한 값으로 교체.
_PRICE_PER_1K = {
    "sonnet": {"in": 0.003, "out": 0.015},
    "opus": {"in": 0.015, "out": 0.075},
    "haiku": {"in": 0.0008, "out": 0.004},
}

# init_telemetry()로 한 번 세팅되는 현재 에이전트 이름.
_agent_name: str | None = None


def init_telemetry(agent_name: str) -> None:
    """에이전트가 켜질 때 1번 호출. 기록 도구를 세팅한다."""
    global _agent_name
    _agent_name = agent_name

    # TODO: 나중에 여기서 OTel meter/logger 세팅 (OTLP -> ADOT -> CloudWatch)
    _emit("telemetry.init", {"agent": agent_name})


def record_llm_usage(
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    ms: float | None = None,
) -> dict:
    """AI를 부를 때마다 1번 호출. 토큰·시간·비용을 기록한다."""
    cost = _estimate_cost(model, input_tokens, output_tokens)

    record = {
        "agent": agent_name,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "latency_ms": ms,
        "cost_usd": cost,
    }

    # TODO: 나중에 여기서 OTel 메트릭/로그로 내보내기 (지금은 콘솔)
    _emit("llm.usage", record)
    return record


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICE_PER_1K.get(model)
    if not price:
        return 0.0
    return round(
        input_tokens / 1000 * price["in"] + output_tokens / 1000 * price["out"], 6
    )


def _emit(event: str, payload: dict) -> None:
    """지금은 구조화 JSON 로그를 콘솔로 찍는다. (나중에 OTLP 전송으로 교체)"""
    line = {"event": event, "ts": time.time(), **payload}
    print(json.dumps(line, ensure_ascii=False))
