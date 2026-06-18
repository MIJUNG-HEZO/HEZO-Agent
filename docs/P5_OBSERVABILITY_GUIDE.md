# P5 옵저버빌리티 가이드 — libs/telemetry vs AgentCore Observability

> 작성 기준: 2026-06-18  
> 대상: P5 인프라 담당자  
> 배경: 현재 `latest` 이미지에 `libs/telemetry`가 삽입되어 있음. AgentCore Observability와의 역할 분리 및 운영 권장 방향을 정리한다.

---

## 1. 현재 상태 확인

ECR `hezo-generation-agent` 이미지 이력:

| 태그 | 푸시 시각 | 크기 | 비고 |
|---|---|---|---|
| `v8-prod` | 2026-06-16 15:56 | 75.25 MB | P4 최초 빌드 — `libs/telemetry` 없음 |
| **`latest`** | **2026-06-16 20:01** | **75.28 MB** | **P5 재빌드 — `libs/telemetry` 추가** |

`latest` 이미지에서 확인된 변경점:

```
/app/libs/telemetry/telemetry.py   ← P5가 추가
/app/libs/telemetry/__init__.py
/app/libs/telemetry/requirements.txt
```

`agents/generation/agent.py` 내 호출:

```python
from libs.telemetry import init_telemetry, record_llm_usage

init_telemetry("generation", region=REGION)   # 앱 기동 시 1회
record_llm_usage(...)                          # LLM 호출마다
```

---

## 2. libs/telemetry 란?

`libs/telemetry/telemetry.py`는 **boto3로 CloudWatch에 직접 메트릭을 전송**하는 자체 구현체다.  
OTEL 프로토콜이 아니며, AgentCore와의 ADOT/OTLP 호환 문제를 우회하기 위해 작성되었다.

### 수집 메트릭

| 메트릭 이름 | 단위 | 설명 |
|---|---|---|
| `llm.input_tokens` | Count | 요청 토큰 수 |
| `llm.output_tokens` | Count | 응답 토큰 수 |
| `llm.cost_usd` | None (USD) | 모델별 단가 기반 비용 추정 |
| `llm.latency_ms` | Milliseconds | LLM 호출 지연 |

CloudWatch 네임스페이스: `HEZO/Agents`  
차원(Dimension): `agent` × `model`

### 한계

- LLM 단가가 하드코딩되어 있어 Bedrock 공식 단가 변경 시 수동 업데이트 필요
- 인프라 지표(CPU, 메모리, 호출 수, 에러율)는 수집 안 됨

---

## 3. AgentCore Observability 란?

Amazon Bedrock AgentCore가 **컨테이너 외부에서** 자동으로 수집하는 빌트인 옵저버빌리티 기능.  
별도 요금 없음 (CloudWatch 데이터 전송 비용만 발생 — 개발·MVP 단계에서 사실상 0원).

### 자동 수집 메트릭 (코드 변경 없음)

| 메트릭 | 설명 |
|---|---|
| 호출 수 (Invocations) | 에이전트 호출 총 횟수 |
| 에러율 (Errors) | 서버사이드 오류 비율 |
| 스로틀링 (Throttles) | TPS 한도 초과 건수 |
| 지연 (Latency) | 요청~응답 end-to-end 시간 |
| CPU 사용량 | vCPU-hour 단위 |
| 메모리 사용량 | GB-hour 단위 |
| 세션 수 | 동시 세션 |

### 자동 수집 안 되는 것

HEZO 에이전트는 **boto3로 Bedrock을 직접 호출**한다. AgentCore는 컨테이너 내부 boto3 호출을 가로챌 수 없으므로 아래 항목은 자동 수집 불가:

- LLM 토큰 수 (입력/출력)
- 모델별 비용 추적
- 프롬프트 단위 LLM 지연

> **왜 boto3 직접 호출인가?**  
> P4 에이전트(생성·검증·리포트)는 LangChain/LangGraph 없이 순수 Python으로 설계되어 있다.  
> AgentCore Runtime은 컨테이너 실행 플랫폼이므로 LLM 호출은 컨테이너 내부 코드가 직접 담당한다.  
> 대화형 에이전트가 아닌 결정론적 파이프라인이라 LangChain 추상화 계층이 불필요하다.

---

## 4. 비교 정리

| 항목 | libs/telemetry | AgentCore Observability |
|---|---|---|
| **호출 수 / 에러율** | ❌ | ✅ 자동 |
| **CPU / 메모리** | ❌ | ✅ 자동 |
| **스로틀링** | ❌ | ✅ 자동 |
| **end-to-end 지연** | ❌ | ✅ 자동 |
| **LLM 토큰 수** | ✅ 수동 코드 | ❌ (boto3 직접 호출이라 불가) |
| **LLM 비용 추정** | ✅ 수동 코드 | ❌ |
| **모델별 분류** | ✅ (Dimension) | ❌ |
| **코드 변경 필요** | 있음 | **없음** |
| **비용** | CloudWatch Metrics 요금 | CloudWatch 데이터 전송 요금 |
| **OTEL 외부 도구 연동** | ❌ | ✅ (Datadog, Langfuse 등) |

---

## 5. 권장 운영 방향

### 두 가지를 병행한다 (상호 보완 관계)

```
AgentCore Observability  ─→  인프라 지표 (자동)
libs/telemetry           ─→  LLM 토큰·비용 지표 (수동)
```

둘 중 하나만 쓰면 맹점이 생긴다:
- AgentCore Observability만: LLM 토큰·비용 추적 불가
- libs/telemetry만: CPU·메모리·에러율·스로틀링 추적 불가

### 향후 개선 옵션 (운영 안정화 후 검토)

ADOT(AWS Distro for OpenTelemetry)를 컨테이너에 추가하면 boto3 Bedrock 호출까지 자동 계측 가능하다.  
단, 컨테이너 구성 변경이 필요하므로 MVP 이후 시점에 검토 권장.

---

## 6. AgentCore Observability 활성화 방법

### 6-1. 콘솔에서 확인

AWS 콘솔 → **Amazon Bedrock** → **AgentCore** → **Observability** 탭  
별도 설정 없이 런타임 메트릭 대시보드가 자동 제공된다.

### 6-2. CloudWatch에서 직접 조회

```bash
# 에이전트 호출 수 조회 (최근 1시간)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Bedrock/AgentCore \
  --metric-name Invocations \
  --dimensions Name=RuntimeId,Value=hezo_generation_agent-GPmRKmCFnL \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum \
  --region ap-northeast-2 \
  --profile rapa-cm1-21
```

### 6-3. libs/telemetry 메트릭 조회 (현재 운영 중)

```bash
# LLM 토큰 사용량 조회
aws cloudwatch get-metric-statistics \
  --namespace HEZO/Agents \
  --metric-name llm.input_tokens \
  --dimensions Name=agent,Value=generation Name=model,Value=sonnet \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum \
  --region ap-northeast-2 \
  --profile rapa-cm1-21
```

### 6-4. 현재 에이전트별 Runtime ID

| 에이전트 | Runtime ID | 상태 |
|---|---|---|
| 생성 에이전트 | `hezo_generation_agent-GPmRKmCFnL` | READY (v10) |
| 검증 에이전트 | `hezo_validation_agent-0b91p74jvm` | READY (v2) |

---

## 7. 체크리스트 (P5 액션)

- [ ] AWS 콘솔 AgentCore Observability 탭에서 생성·검증 에이전트 메트릭 확인
- [ ] CloudWatch 대시보드 `dashboard.json` (`infra/cloudwatch/dashboard.json`)에 AgentCore 빌트인 메트릭 위젯 추가
- [ ] `libs/telemetry/telemetry.py` 단가 테이블 Bedrock 공식 단가로 업데이트 확인
- [ ] 검증 에이전트 이미지 (`hezo-validation-agent:v1-prod`)에 `libs/telemetry` 연동 여부 확인 및 필요 시 추가

---

## 관련 파일

| 파일 | 역할 |
|---|---|
| `libs/telemetry/telemetry.py` | 자체 CloudWatch 메트릭 전송 모듈 |
| `agents/generation/agent.py` | `init_telemetry` / `record_llm_usage` 호출 위치 |
| `infra/cloudwatch/dashboard.json` | CloudWatch 대시보드 정의 |
| `infra/iam/agentcore-execution-policy.json` | `cloudwatch:PutMetricData` 권한 포함 여부 확인 필요 |
