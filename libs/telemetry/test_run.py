"""
가짜 호출로 텔레메트리가 도는지 확인하는 스모크 테스트.

실행:  python libs/telemetry/test_run.py
성공:  콘솔에 init/llm.usage JSON 로그가 찍히고,
       ADOT(4317)가 떠 있으면 CloudWatch로도 메트릭·로그가 나간다.

지금은 에이전트가 없어서, PRD 이름(chatbot/generation/validation/report)
4종을 가짜로 흉내 낸다. 진짜 에이전트가 붙으면 이 파일 대신
각 에이전트가 같은 함수를 부르게 된다.
"""

from telemetry import init_telemetry, record_llm_usage, shutdown_telemetry

# (에이전트, 모델, 입력토큰, 출력토큰, 지연ms) — 가짜 샘플
FAKE_CALLS = [
    ("chatbot",    "haiku",  300,  200,  80),
    ("generation", "sonnet", 500, 1200, 120),
    ("validation", "sonnet", 400,  300,  90),
    ("report",     "opus",  2000, 3000, 350),
]

if __name__ == "__main__":
    init_telemetry("test_run")  # otlp=True (기본) → 4317로도 쏨

    for agent, model, tin, tout, ms in FAKE_CALLS:
        record_llm_usage(agent, model, tin, tout, ms=ms)

    shutdown_telemetry()  # 안 보낸 메트릭·로그 강제 flush 후 종료
