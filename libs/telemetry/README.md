# telemetry (P5 관측 키트)

모든 에이전트가 공용으로 import 하는 텔레메트리. 에이전트가 넣는 코드는 딱 2줄.

```python
# 파일 맨 위
from libs.telemetry import init_telemetry, record_llm_usage

# 에이전트(Lambda) 시작 시 1번
init_telemetry("generation")

# AI(Bedrock) 호출하고 나서, 호출마다 1번
record_llm_usage(
    "generation",          # 에이전트 이름
    "sonnet",              # 모델
    input_tokens,          # ← 응답에서 꺼낸 입력 토큰 (P4가 자기 구조로)
    output_tokens,         # ← 출력 토큰
    ms=elapsed_ms,         # ← 걸린 시간 (선택)
)  # 호출마다 1번
```

## 지금 상태 (틀만)

- 출력 = **콘솔(JSON 로그)**. AWS 없이 바로 테스트 가능.
- 나중에 `telemetry.py`의 `# TODO` 위치만 OTLP(ADOT) 전송으로 바꾸면 됨.
  에이전트 코드는 안 건드림.

## 돌려보기

```bash
python libs/telemetry/test_run.py
```

init 로그 + `llm.usage` JSON 로그가 콘솔에 찍히면 성공.

## TODO (진짜로 붙일 때)

- [ ] `pip install opentelemetry-sdk opentelemetry-exporter-otlp`
- [ ] `_emit()` → OTel 메트릭/로그 전송으로 교체
- [ ] 모델별 단가(`_PRICE_PER_1K`) 정확한 값으로 갱신
- [ ] 비-AI 메트릭(빌드/검증/파이프라인 시간) 추가
