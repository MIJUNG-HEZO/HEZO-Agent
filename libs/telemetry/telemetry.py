"""
HEZO 관측 키트 (P5)

- 모든 에이전트가 import 해서 쓰는 공용 텔레메트리.
- 에이전트가 추가하는 코드는 딱 2줄: init 1번 + AI 호출마다 record 1번.
- 전송 방식: **boto3로 CloudWatch에 직접 전송** (put_metric_data).
    → ADOT/OTLP 불필요. AgentCore 컨테이너 안에서 바로 CloudWatch로.
- 출력 2갈래:
    ① 콘솔(화면)        : 항상 찍음. 디버깅용.
    ② CloudWatch 메트릭 : 에이전트별 토큰·비용·지연 (cloudwatch=True일 때)

필요 권한: 에이전트 실행 역할에 `cloudwatch:PutMetricData`.
"""

from __future__ import annotations

import json
import time

# CloudWatch 네임스페이스 (대시보드가 이걸 봄)
_NAMESPACE = "HEZO/Agents"

# 모델별 1K 토큰당 단가(USD). 비용 계산용 — 나중에 실제 Bedrock 단가로 교체.
_PRICE_PER_1K = {
    "sonnet": {"in": 0.003, "out": 0.015},
    "opus": {"in": 0.015, "out": 0.075},
    "haiku": {"in": 0.0008, "out": 0.004},
}

# init_telemetry()로 세팅되는 상태.
_agent_name: str | None = None
_cw_client = None          # boto3 CloudWatch 클라이언트 (cloudwatch=True일 때)
_console = True            # 콘솔 출력 여부


def init_telemetry(agent_name: str, region: str = "ap-northeast-2",
                   cloudwatch: bool = True, console: bool = True,
                   **_ignored) -> None:
    """에이전트가 켜질 때 1번 호출.

    - cloudwatch=True : CloudWatch(HEZO/Agents)로 메트릭 직접 전송 (boto3).
    - cloudwatch=False: 콘솔에만 (AWS 없이 로컬 테스트).
    - **_ignored: 옛 호출(otlp=, endpoint=)을 받아도 안 깨지게 흡수.
    """
    global _agent_name, _cw_client, _console
    _agent_name = agent_name
    _console = console

    if cloudwatch:
        try:
            import boto3
            _cw_client = boto3.client("cloudwatch", region_name=region)
        except Exception as exc:  # boto3 없음/자격증명 없음 → 콘솔만
            _cw_client = None
            print(f"[telemetry] CloudWatch 비활성화: {exc}")

    _emit("telemetry.init", {"agent": agent_name, "cloudwatch": _cw_client is not None})


def record_llm_usage(
    agent_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    ms: float | None = None,
) -> dict:
    """AI를 부를 때마다 1번 호출. 에이전트별 토큰·비용·지연을 기록한다."""
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

    # ① 콘솔
    _emit("llm.usage", record)

    # ② CloudWatch 직접 전송 (에이전트·모델 차원으로)
    _put_metrics(agent_name, model, input_tokens, output_tokens, cost, ms)

    return record


def shutdown_telemetry() -> None:
    """put_metric_data는 즉시 전송이라 flush 불필요. 호환용으로 남겨둠."""
    return None


def _put_metrics(agent_name, model, input_tokens, output_tokens, cost, ms):
    """boto3로 CloudWatch에 메트릭 직접 전송. 실패해도 에이전트 안 죽게(fire-and-forget)."""
    if _cw_client is None:
        return

    dims = [
        {"Name": "agent", "Value": agent_name},
        {"Name": "model", "Value": model},
    ]
    metric_data = [
        {"MetricName": "llm.input_tokens", "Value": float(input_tokens),
         "Unit": "Count", "Dimensions": dims},
        {"MetricName": "llm.output_tokens", "Value": float(output_tokens),
         "Unit": "Count", "Dimensions": dims},
        {"MetricName": "llm.cost_usd", "Value": float(cost),
         "Unit": "None", "Dimensions": dims},
    ]
    if ms is not None:
        metric_data.append(
            {"MetricName": "llm.latency_ms", "Value": float(ms),
             "Unit": "Milliseconds", "Dimensions": dims}
        )

    try:
        _cw_client.put_metric_data(Namespace=_NAMESPACE, MetricData=metric_data)
    except Exception as exc:
        print(f"[telemetry] CloudWatch 전송 실패(무시): {exc}")


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICE_PER_1K.get(model)
    if not price:
        return 0.0
    return round(
        input_tokens / 1000 * price["in"] + output_tokens / 1000 * price["out"], 6
    )


def _emit(event: str, payload: dict) -> None:
    """구조화 JSON 로그를 콘솔로 찍는다 (로그는 AgentCore가 CloudWatch Logs로 자동 수집)."""
    if not _console:
        return
    line = {"event": event, "ts": time.time(), **payload}
    print(json.dumps(line, ensure_ascii=False))
