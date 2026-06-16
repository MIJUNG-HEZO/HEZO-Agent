# telemetry (P5 관측 키트)

모든 에이전트가 공용으로 import 하는 텔레메트리.

## ⚠️ 함수 2개를 "둘 다" 불러야 합니다

| 함수 | 언제 | 안 부르면 |
|---|---|---|
| `init_telemetry(...)` | 에이전트(Lambda) 시작 시 **1번** | 세팅 안 됨 |
| `record_llm_usage(...)` | **AI 호출할 때마다** | ❌ **토큰·비용이 하나도 안 쌓임** |

> `init_telemetry`만 부르고 `record_llm_usage`를 안 부르면, "켜지긴 했는데 기록은 하나도 안 되는" 상태가 됩니다. **반드시 둘 다** 부르세요.

## 사용법 (복붙)

```python
# 1. 파일 맨 위 — import
from libs.telemetry import init_telemetry, record_llm_usage

# 2. 에이전트(Lambda) 시작 시 1번
init_telemetry("generation", otlp=True)   # otlp=True 면 CloudWatch로도 전송

# 3. AI(Bedrock) 호출하고 나서, ★호출마다★ 반드시 1번
resp = bedrock.invoke_model(...)          # 또는 AgentCore 호출
record_llm_usage(
    "generation",          # 에이전트 이름 (init과 동일하게)
    "sonnet",              # 모델
    input_tokens,          # ← 응답에서 꺼낸 입력 토큰 (각자 응답 구조로)
    output_tokens,         # ← 출력 토큰
    ms=elapsed_ms,         # ← 걸린 시간 (선택)
)
```

→ 이 셋(import + init + **record**)이 모두 있어야 토큰·비용·지연이 기록됩니다.

## otlp 옵션 (CloudWatch 전송 여부)

```python
init_telemetry("generation", otlp=True)   # 콘솔 + CloudWatch(ADOT 필요)
init_telemetry("generation", otlp=False)  # 콘솔만 (ADOT 없이 로컬 테스트용)
```
- **ADOT가 떠 있는 환경(실행/배포)** → `otlp=True`
- ADOT 없이 로컬에서 잠깐 테스트만 → `otlp=False`
- `otlp=False`면 콘솔에만 찍히고 **CloudWatch엔 안 갑니다** (대시보드에 안 뜸).

## Lambda 배포 시

`libs/telemetry/` 폴더를 Lambda 패키지에 **같이 포함**해야 import 됩니다.
(`agents/generation/deploy.sh`의 libs 복사 부분 참고)

## 돌려보기 (로컬 검증)

```bash
python libs/telemetry/test_run.py
```
init 로그 + `llm.usage` JSON 로그(토큰·비용 포함)가 콘솔에 찍히면 성공.

## 함수 시그니처 (고정 — 변경 시 P5와 합의)

```python
init_telemetry(agent_name: str, otlp: bool = True, endpoint: str = "localhost:4317")
record_llm_usage(agent_name: str, model: str, input_tokens: int,
                 output_tokens: int, ms: float | None = None) -> dict
shutdown_telemetry()   # 짧은 스크립트/배치 끝에서 강제 flush
```

## TODO (P5)

- [ ] 모델별 단가(`_PRICE_PER_1K`) 실제 Bedrock 단가로 갱신
- [ ] 비-AI 메트릭(빌드/검증/파이프라인 시간) 헬퍼 추가
- [ ] 메트릭 이름 PRD §6.3 카탈로그(`agent.*`)로 정렬 검토
