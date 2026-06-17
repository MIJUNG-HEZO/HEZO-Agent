# P1 Chat Agent AWS Dev Infra

P1 Chat Agent가 실제 AWS 서비스에 연결되기 전에 dev 환경 기준을 고정한다.

이번 문서는 리소스 생성 스크립트가 아니라, 팀원이 같은 이름과 같은 환경변수로
로컬 검증과 후속 배포 작업을 진행하기 위한 기준 문서다.

## 범위

포함:

- Chat Agent dev 리소스 이름 기준
- 로컬/팀원 공통 환경변수 기준
- AWS 리소스 생성 순서
- 생성 후 read-only 검증 순서

제외:

- 실제 AWS 리소스 생성
- AgentCore Runtime 배포
- ECR 이미지 빌드/푸시
- Bedrock Guardrail 정책 상세 튜닝
- DynamoDB/S3 운영 정책 최종 확정

## 리소스 기준

| 구분 | 이름 | 용도 |
|---|---|---|
| DynamoDB | `hezo_agent_chat` | 세션 메타데이터, 메시지, 체크포인트, guardrail audit 저장 |
| S3 | `hezo-chat` | P1 채팅 transcript / guardrail audit artifact 저장 |
| S3 | `hezo-wiki` | P2 wiki/markdown artifact 참조 bucket |
| S3 | `hezo-artifacts` | P4 contract/artifact 참조 bucket |
| Bedrock model | `anthropic.claude-sonnet-4-5-20251001` | 질문 보완, Contract 보완 등 Claude 호출 |
| Bedrock Guardrail | `hezo-dev-guardrail` / `DRAFT` | 사용자 입력, P2 markdown, Contract, LLM 출력 안전 검사 |
| ECR | `hezo-chat-agent` | AgentCore 배포용 container image 저장 |
| AgentCore Runtime | `hezo-chat-agent-dev` | P1 Chat Agent runtime |

## 환경변수

`infra/chat/env.example`을 복사해서 로컬 `.env` 또는 shell profile에 맞게 설정한다.

```bash
AWS_PROFILE=rapa-cm1-21
AWS_REGION=ap-northeast-2
HEZO_ENV=dev

HEZO_AGENT_DYNAMODB_TABLE=hezo_agent_chat
HEZO_CHAT_BUCKET=hezo-chat
HEZO_P2_MARKDOWNS_BUCKET=hezo-wiki
HEZO_CONTRACTS_BUCKET=hezo-artifacts

HEZO_BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20251001
HEZO_BEDROCK_GUARDRAIL_ID=hezo-dev-guardrail
HEZO_BEDROCK_GUARDRAIL_VERSION=DRAFT

HEZO_ECR_REPOSITORY=hezo-chat-agent
HEZO_AGENTCORE_RUNTIME=hezo-chat-agent-dev
```

## 생성 순서

1. AWS 계정, 리전, 프로파일 확인
2. IAM role/policy baseline 확인
3. DynamoDB `hezo_agent_chat` 생성
4. P1 chat 전용 S3 bucket `hezo-chat` 생성 및 public access block, encryption, versioning 설정
5. Bedrock model access 활성화 확인
6. Bedrock Guardrail `hezo-dev-guardrail` 생성
7. ECR repository `hezo-chat-agent` 생성
8. AgentCore Runtime `hezo-chat-agent-dev` 생성
9. P2/P4 bucket 참조값 확인
10. `infra/chat/verify_dev_infra.sh`로 read-only 검증

CloudWatch dashboard, alarm, log group, ADOT, metric 수집은 P5 담당 범위다.
P1에서는 CloudWatch 생성/설정/수집 작업을 하지 않는다.

## DynamoDB 기준

Chat state/checkpoint adapter는 single-table 구조를 전제로 한다.

| Key | 예시 | 설명 |
|---|---|---|
| PK | `SESSION#session_001` | 세션 단위 partition |
| SK | `META` | 세션 메타데이터 |
| SK | `MESSAGE#2026-06-16T10:00:00Z#msg_001` | 대화 메시지 |
| SK | `CHECKPOINT#contract_quality_check#000001` | 단계별 체크포인트 |
| SK | `GUARDRAIL#2026-06-16T10:00:01Z#contract_draft` | Guardrail 감사 기록 |

초기 dev table은 아래 속성 기준으로 시작한다.

- Partition key: `pk` (String)
- Sort key: `sk` (String)
- Billing mode: on-demand
- Point-in-time recovery: 후속 운영 이슈에서 결정

## S3 Key 기준

```text
s3://hezo-chat/sessions/{session_id}/transcripts/{seq}.json
s3://hezo-chat/sessions/{session_id}/guardrails/{target}/{created_at}.json
s3://hezo-wiki/{p2_prefix_tbd}
s3://hezo-artifacts/{p4_contract_prefix_tbd}
```

`hezo-wiki`, `hezo-artifacts` 내부 prefix는 P2/P4 코드 또는 팀원 합의 확인 후 확정한다.

## 검증

리소스가 생성된 뒤 아래 명령으로 존재 여부만 확인한다.

```bash
source infra/chat/env.example
bash infra/chat/verify_dev_infra.sh
```

스크립트는 read-only AWS CLI 호출만 수행한다. 없는 리소스는 `MISSING`으로 표시하고,
생성이나 수정은 하지 않는다.

## 후속 작업

- AWS dev 리소스 실제 생성 이슈
- Bedrock Claude adapter에 boto3 client 연결
- Bedrock Guardrails adapter에 `ApplyGuardrail` 연결
- DynamoDB state/checkpoint store 실제 구현
- S3 artifact store 실제 구현
- AgentCore Runtime + ECR 배포 파이프라인
