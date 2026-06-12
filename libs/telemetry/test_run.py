"""
가짜 호출로 텔레메트리가 도는지 확인하는 스모크 테스트.

실행:  python libs/telemetry/test_run.py
성공:  콘솔에 init 로그 + llm.usage 로그(JSON)가 찍히면 OK.
"""

from telemetry import init_telemetry, record_llm_usage

if __name__ == "__main__":
    init_telemetry("generation")
    record_llm_usage(
        "generation",
        "sonnet",
        input_tokens=500,
        output_tokens=1200,
        ms=120,
    )
