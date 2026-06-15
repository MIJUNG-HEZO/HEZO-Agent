"""
HEZO 관측 키트 (P5)

- 모든 에이전트가 import 해서 쓰는 공용 텔레메트리.
- 에이전트가 추가하는 코드는 딱 2줄: init 1번 + AI 호출마다 record 1번.
- 출력 2갈래:
    ① 콘솔(화면)  : 항상 찍음. 디버깅용.
    ② OTLP(4317)  : ADOT로 쏨 → CloudWatch. (otlp=True일 때)
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

# init_telemetry()로 세팅되는 상태.
_agent_name: str | None = None
_meter_provider = None          # OTLP 끌 때 flush 하려고 들고 있음
_instruments: dict = {}         # 토큰/비용/지연 측정 도구들
_logger_provider = None         # 로그 장치 (끌 때 flush 하려고 들고 있음)
_py_logger = None               # _emit 이 로그 쏠 때 쓰는 파이썬 로거


def init_telemetry(agent_name: str, otlp: bool = True,
                   endpoint: str = "localhost:4317") -> None:
    """에이전트가 켜질 때 1번 호출. 기록 도구를 세팅한다.

    otlp=True 면 측정값을 4317(ADOT)로도 보낸다. False면 콘솔만.
    """
    global _agent_name
    _agent_name = agent_name

    if otlp:
        _setup_otlp(endpoint)

    _emit("telemetry.init", {"agent": agent_name, "otlp": otlp})


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

    # ① 콘솔
    _emit("llm.usage", record)

    # ② OTLP(메트릭) — 도구가 세팅돼 있으면 4317로 쏨
    if _instruments:
        attrs = {"agent": agent_name, "model": model}
        _instruments["input_tokens"].add(input_tokens, attrs)
        _instruments["output_tokens"].add(output_tokens, attrs)
        _instruments["cost_usd"].add(cost, attrs)
        if ms is not None:
            _instruments["latency_ms"].record(ms, attrs)

    return record


def shutdown_telemetry() -> None:
    """프로그램 끝날 때 호출. 아직 안 보낸 메트릭을 강제로 밀어낸다(flush).

    test_run 처럼 금방 끝나는 스크립트는 이걸 안 부르면 데이터가
    미처 안 나가고 종료될 수 있다.
    """
    if _meter_provider is not None:
        _meter_provider.force_flush()
        _meter_provider.shutdown()
    if _logger_provider is not None:
        _logger_provider.force_flush()
        _logger_provider.shutdown()


def _setup_otlp(endpoint: str) -> None:
    """OTLP 송신 세팅: 4317로 보내는 메트릭 장치 + 로그 장치 만들기."""
    _setup_otlp_metrics(endpoint)
    _setup_otlp_logs(endpoint)


def _setup_otlp_metrics(endpoint: str) -> None:
    """메트릭 장치: 4317로 보내는 미터 + 측정 도구."""
    global _meter_provider, _instruments

    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import (
        Counter,
        Histogram,
        ObservableCounter,
        ObservableGauge,
        ObservableUpDownCounter,
        UpDownCounter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality,
        PeriodicExportingMetricReader,
    )

    # awsemf(CloudWatch)는 델타(변화량) 방식을 권장.
    # 누적(cumulative)이면 첫 데이터가 비교 대상이 없어 0으로 기록되는 문제가 있음.
    _delta = AggregationTemporality.DELTA
    _cumulative = AggregationTemporality.CUMULATIVE
    exporter = OTLPMetricExporter(
        endpoint=endpoint,
        insecure=True,
        preferred_temporality={
            Counter: _delta,
            UpDownCounter: _cumulative,
            Histogram: _delta,
            ObservableCounter: _delta,
            ObservableUpDownCounter: _cumulative,
            ObservableGauge: _cumulative,
        },
    )
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=5000)
    _meter_provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(_meter_provider)

    meter = metrics.get_meter("hezo.telemetry")
    _instruments = {
        "input_tokens": meter.create_counter("llm.input_tokens"),
        "output_tokens": meter.create_counter("llm.output_tokens"),
        "cost_usd": meter.create_counter("llm.cost_usd"),
        "latency_ms": meter.create_histogram("llm.latency_ms"),
    }


def _setup_otlp_logs(endpoint: str) -> None:
    """로그 장치: 4317로 보내는 로거 + 파이썬 logging 연결."""
    global _logger_provider, _py_logger

    import logging

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter,
    )
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    _logger_provider = LoggerProvider()
    set_logger_provider(_logger_provider)
    _logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True))
    )

    # 파이썬 기본 logging 에 OTel 핸들러를 달아서, 이 로거로 찍으면 4317로 감.
    handler = LoggingHandler(level=logging.INFO, logger_provider=_logger_provider)
    _py_logger = logging.getLogger("hezo.telemetry")
    _py_logger.setLevel(logging.INFO)
    _py_logger.addHandler(handler)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = _PRICE_PER_1K.get(model)
    if not price:
        return 0.0
    return round(
        input_tokens / 1000 * price["in"] + output_tokens / 1000 * price["out"], 6
    )


def _emit(event: str, payload: dict) -> None:
    """구조화 JSON 로그를 ① 콘솔 ② OTLP(4317)로 보낸다."""
    line = {"event": event, "ts": time.time(), **payload}
    text = json.dumps(line, ensure_ascii=False)

    # ① 콘솔
    print(text)

    # ② OTLP 로그 — 로거가 세팅돼 있으면 4317로도 (→ ADOT → CloudWatch Logs)
    if _py_logger is not None:
        _py_logger.info(text)
