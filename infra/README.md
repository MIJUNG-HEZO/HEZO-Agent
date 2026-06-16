# infra (P5)

P5가 만드는 AWS 인프라 자리. **지금은 틀만**, 내용은 작업 순서대로 채움.

## 디렉토리

| 폴더 | 들어갈 것 | 작업 |
|---|---|---|
| `adot/` | ADOT Collector `config.yaml` (otlp 받기 → CloudWatch 보내기) | 작업 2 |
| `cloudwatch/` | 대시보드 / 알람 정의 (지연·토큰·에러율·비용) | 작업 2 |
| `step-functions/` | 벨트 상태머신 정의 + DynamoDB `pipeline_state` | 작업 3 |
| `chat/` | P1 Chat Agent dev AWS 리소스 기준 / 환경변수 / read-only 검증 | P1 |

## 작업 순서

1. 관측 키트 (`libs/telemetry/`) ← 진행 중
2. ADOT + CloudWatch (`adot/`, `cloudwatch/`)
3. Step Functions 골격 + DynamoDB (`step-functions/`)

P1 Chat Agent AWS dev infra 기준은 `chat/README.md`를 따른다.

> 원칙: "구멍 뚫어놓고 나중에 진짜를 끼운다." 지금은 전부 가짜(mock)로 흐름부터.
> 자세한 계획은 레포 루트의 `P5_실행계획.md` 참고.
