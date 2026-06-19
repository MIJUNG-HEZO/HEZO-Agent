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
- Bedrock Guardrail 정책 상세 튜닝
- DynamoDB/S3 운영 정책 최종 확정

## 리소스 기준

| 구분 | 이름 | 용도 |
|---|---|---|
| DynamoDB | `hezo_agent_chat` | 세션 메타데이터, 메시지, 체크포인트, guardrail audit 저장 |
| S3 | `hezo-chat` | P1 채팅 transcript / guardrail audit artifact 저장 |
| S3 | `hezo-wiki` | P2 wiki/markdown artifact 참조 bucket |
| S3 | `hezo-artifacts` | P4 contract/artifact 참조 bucket |
| Bedrock model | `anthropic.claude-sonnet-4-5-20250929-v1:0` | 질문 보완, Contract 보완 등 Claude 호출 |
| Bedrock Guardrail | `hezo-dev-guardrail` / `q8dcjc2um846` / `DRAFT` | 사용자 입력, P2 markdown, Contract, LLM 출력 안전 검사 |
| ECR | `hezo-chat-agent` | AgentCore 배포용 container image 저장 |
| AgentCore Runtime | `hezo_chat_agent_dev` | P1 Chat Agent runtime |

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

HEZO_BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0
HEZO_BEDROCK_GUARDRAIL_NAME=hezo-dev-guardrail
HEZO_BEDROCK_GUARDRAIL_ID=q8dcjc2um846
HEZO_BEDROCK_GUARDRAIL_ARN=arn:aws:bedrock:ap-northeast-2:492554570964:guardrail/q8dcjc2um846
HEZO_BEDROCK_GUARDRAIL_VERSION=DRAFT

HEZO_ECR_REPOSITORY=hezo-chat-agent
HEZO_AGENTCORE_RUNTIME=hezo-chat-agent-dev
HEZO_AGENTCORE_RUNTIME_NAME=hezo_chat_agent_dev
HEZO_AGENTCORE_ROLE_NAME=hezo-agentcore-execution-role
HEZO_AGENTCORE_NETWORK_MODE=PUBLIC
HEZO_AGENTCORE_PROTOCOL=HTTP
```

## 생성 순서

1. AWS 계정, 리전, 프로파일 확인
2. IAM role/policy baseline 확인
3. DynamoDB `hezo_agent_chat` 생성
4. P1 chat 전용 S3 bucket `hezo-chat` 생성 및 public access block, encryption, versioning 설정
5. Bedrock model access 활성화 확인
6. Bedrock Guardrail `hezo-dev-guardrail` 생성
7. ECR repository `hezo-chat-agent` 생성
8. AgentCore Runtime `hezo_chat_agent_dev` 생성
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
s3://hezo-wiki/industries/{category}/{domain}.md
s3://hezo-artifacts/sites/{site_id}/contracts/draft/{version}.json
s3://hezo-artifacts/sites/{site_id}/contract_final.json
```

P2 markdown은 `source_s3_key`가 명시되면 해당 key를 우선 사용한다.
P2 내부 prefix가 최종 확정되기 전까지 dev fallback은 `industries/{category}/{domain}.md`이다.
P4 Contract artifact는 draft/final prefix를 위 기준으로 고정한다.

## 검증

리소스가 생성된 뒤 아래 명령으로 존재 여부만 확인한다.

```bash
source infra/chat/env.example
bash infra/chat/verify_dev_infra.sh
python3 agents/chat/test_s3_aws_smoke.py
python3 agents/chat/test_p2_markdown_s3_aws_smoke.py
python3 agents/chat/test_chat_graph_s3_pipeline_aws_smoke.py
python3 agents/chat/test_dynamodb_aws_smoke.py
python3 agents/chat/test_chat_graph_dynamodb_aws_smoke.py
python3 agents/chat/test_chat_message_dynamodb_aws_smoke.py
```

`verify_dev_infra.sh`는 read-only AWS CLI 호출만 수행한다. 없는 리소스는 `MISSING`으로 표시하고,
생성이나 수정은 하지 않는다. `test_*_aws_smoke.py`는 dev bucket 또는 DynamoDB table에 임시 데이터를 쓰고 검증 후 삭제한다.

## ECR 이미지 빌드/푸시

Docker runtime smoke가 통과한 뒤 P1 Chat Agent 이미지를 dev ECR repository에 푸시한다.

```bash
source infra/chat/env.example
bash infra/chat/push_ecr_image.sh
```

태그를 명시하려면:

```bash
bash infra/chat/push_ecr_image.sh --tag dev-20260619
```

스크립트 기준:

- `HEZO_ECR_REPOSITORY` 기본값: `hezo-chat-agent`
- Dockerfile: `agents/chat/Dockerfile`
- Platform: `linux/arm64`
- push 성공 시 `IMAGE_URI={account}.dkr.ecr.{region}.amazonaws.com/hezo-chat-agent:{tag}` 출력

## AgentCore Runtime 생성/갱신

ECR에 push된 P1 Chat Agent 이미지를 기준으로 AgentCore Runtime을 생성하거나 갱신한다.

```bash
source infra/chat/env.example
bash infra/chat/deploy_agentcore_runtime.sh
```

태그를 명시하려면:

```bash
bash infra/chat/deploy_agentcore_runtime.sh --tag dev-20260619
```

스크립트 기준:

- ECR image: `{account}.dkr.ecr.{region}.amazonaws.com/hezo-chat-agent:{tag}`
- Runtime name: `hezo_chat_agent_dev`
- Execution role: `hezo-agentcore-execution-role`
- Network: `PUBLIC`
- Protocol: `HTTP`
- Runtime 환경변수: DynamoDB, S3, Bedrock model, Guardrail 설정값

AgentCore Runtime 이름은 AWS 제약상 하이픈을 사용할 수 없어서 `HEZO_AGENTCORE_RUNTIME_NAME`에는 언더스코어 이름을 사용한다.
`HEZO_AGENTCORE_RUNTIME`에 하이픈 이름만 있어도 스크립트가 자동으로 언더스코어 형태로 정규화한다.

배포 후 출력되는 값을 확인한다.

```text
RUNTIME_NAME=hezo_chat_agent_dev
RUNTIME_ID=...
RUNTIME_ARN=...
RUNTIME_STATUS=READY
IMAGE_URI=...
```

## 후속 작업

- Backend/Frontend에서 호출할 HTTP 계약 확정
- AgentCore Runtime invoke smoke 테스트
- 운영 전 IAM 최소 권한 재점검
