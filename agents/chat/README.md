# HEZO Chat Agent

P1 채팅 에이전트는 사용자 대화에서 도메인을 확정하고, P2 markdown을 요청/검수한 뒤 적극적 질의를 통해 Contract JSON 초안을 만드는 Agent입니다.

## 책임 범위

- 도메인/업종 확정
- P2 markdown 요청 payload 생성
- P2 markdown 수신 검수
- 적극적 질의 흐름 관리
- slot 기반 Contract JSON 초안 구성
- Contract 품질 상태 판단
- LLM 보완 필요 여부 표시

## MVP 스켈레톤 범위

이번 스켈레톤은 실제 AWS/Bedrock/LangGraph 연동을 포함하지 않습니다.

포함:

- 기본 agent config
- mock state 구조
- P2 markdown 요청 payload 생성 순수 로직
- P2 markdown 수신 검수 필드
- P2 markdown 수신 검수 순수 로직
- 적극적 질의 후보 생성 순수 로직
- 사용자 답변 slot state 반영 순수 로직
- Contract JSON draft compile 순수 로직
- Contract draft quality check 순수 로직
- 저장 전 Guardrails adapter 스켈레톤
- DynamoDB chat state/checkpoint 저장소 스켈레톤
- S3 artifact storage adapter 스켈레톤
- Bedrock Claude invocation adapter 스켈레톤
- Bedrock Guardrails ApplyGuardrail adapter 스켈레톤
- LangGraph chat graph 스켈레톤
- 로컬 smoke test

제외:

- 실제 LangGraph `StateGraph` 구현
- 실제 Bedrock 호출
- 실제 P2 API 호출
- 실제 사용자 대화 API 라우터
- 실제 Bedrock Guardrails 호출
- 실제 DynamoDB custom checkpointer
- 실제 S3 3개 물리 버킷 연동
- AgentCore Runtime 배포

## Stage 흐름

```text
domain_selection
-> p2_markdown_request
-> p2_markdown_review
-> proactive_questioning
-> slot_answer_state
-> contract_compile
-> contract_quality_check
-> storage_guardrails
-> chat_state_checkpoint
-> s3_artifact_storage
-> bedrock_claude_invocation
-> bedrock_guardrails_apply
-> chat_graph
```

## Rule-based Logic and Guardrails

채팅 에이전트는 코드 룰베이스와 Guardrails adapter를 분리합니다.

- 코드 룰베이스: HEZO workflow, slot 상태, Contract draft, preview ready 같은 비즈니스 규칙
- Guardrails adapter: 사용자 입력, P2 markdown, Contract draft, LLM 출력의 안전/보안 검사

이번 스켈레톤의 Guardrails adapter는 실제 AWS Bedrock Guardrails 호출 없이 로컬 mock 규칙으로 동작합니다. 실제 `ApplyGuardrail` 호출, guardrail id/version 설정, boto3 client 연결은 후속 이슈에서 다룹니다.

## P2 Markdown Request Payload

`p2_markdown_request.py`는 P2에게 markdown 생성을 요청하기 전, P1 내부 표준 payload를 만듭니다.

입력 기준:

- `site_id`, `user_id`
- `domain`, `domain_label`
- `selected_template`
- `slot_registry`
- `known_answers`
- `missing_slots`
- `request_reason`

출력 기준:

```json
{
  "payload_version": "v0.1",
  "target_artifact": "domain_question_guide_markdown",
  "domain": "tax_accounting",
  "domain_label": "세무/회계",
  "selected_template": "landing/13-tax-accounting",
  "request_reason": "initial_domain_selected",
  "missing_slots": ["core_services", "contact_method"]
}
```

검증 기준:

- 필수 식별자 누락 시 실패
- `domain` 누락 시 실패
- `slot_registry`가 비어 있으면 실패
- `missing_slots`가 비어 있어도 payload 생성 가능

이번 범위에서는 실제 P2 API 호출, S3 저장, Bedrock/LangGraph 호출을 포함하지 않습니다.

## P2 Markdown Review State

```json
{
  "p2_confidence": 0.78,
  "p1_markdown_review_status": "passed",
  "p1_markdown_review_score": 0.82,
  "p2_markdown_usable_for_questions": true
}
```

검수 상태:

- `passed`: 적극적 질의에 사용 가능
- `needs_enrichment`: 일부 사용 가능, 부족한 질문은 LangGraph/LLM 보완 필요
- `failed`: 적극적 질의 재료로 사용하지 않고 P2 재요청 또는 fallback 질문 사용

## P2 Markdown Review Logic

`p2_markdown_review.py`는 이미 수신한 P2 markdown metadata를 기준으로 P1 검수 결과를 산출합니다.

검수 기준:

- 도메인 일치 여부
- P2 confidence 컷 0.70 이상 여부
- 인젝션/명령형 조작 문구 포함 여부
- 필수 slot 질문 후보 충분성
- 과장/단정 표현 위험 여부

검증 케이스:

- 정상 통과
- confidence 부족
- domain 불일치
- 인젝션 의심 문구
- 필수 slot 질문 부족

## Proactive Question Candidates

`proactive_questioning.py`는 검수된 P2 markdown 상태와 `slot_registry`를 기준으로 사용자에게 물어볼 질문 후보를 생성합니다.

입력 기준:

- `domain`, `domain_label`
- `p1_markdown_review_status`
- `p2_markdown_usable_for_questions`
- `slot_registry`
- `known_answers`
- `missing_slots`
- `max_questions`

출력 기준:

```json
[
  {
    "slot": "core_services",
    "question": "핵심 세무 서비스는 무엇인가요?",
    "priority": 1,
    "source": "p2_markdown",
    "fallback": false,
    "required": true
  }
]
```

생성 기준:

- P2 markdown이 사용 가능하면 `question_hint` 기반 질문 생성
- P2 markdown이 사용 불가하면 fallback 질문 생성
- 이미 답변된 slot은 질문 후보에서 제외
- 필수 slot을 선택 slot보다 우선
- `max_questions`를 초과하지 않음

이번 범위에서는 실제 LangGraph node 연결, Bedrock/LLM 질문 보완, 사용자 답변 저장, Contract JSON 반영을 포함하지 않습니다.

## Slot Answer State

`slot_answer_state.py`는 적극적 질의에 대한 사용자 답변을 현재 slot state에 반영합니다.

입력 기준:

- `slot_registry`
- `known_answers`
- `missing_slots`
- `answered_slot`
- `answer`

출력 기준:

```json
{
  "answered_slot": "core_services",
  "answer_status": "accepted",
  "known_answers": {
    "business_name": "한빛 세무회계",
    "core_services": "기장 대리, 종합소득세 신고, 법인세 신고"
  },
  "missing_slots": ["contact_method"],
  "reasons": ["answer_applied"]
}
```

반영 기준:

- 유효한 slot 답변은 `known_answers`에 반영
- 답변된 slot은 `missing_slots`에서 제거
- 빈 문자열/공백 답변은 거부
- 존재하지 않는 slot은 거부
- 이미 답변된 slot도 유효한 새 답변이면 업데이트 가능
- 비어 있지 않은 list/dict 구조화 답변은 허용

이번 범위에서는 실제 사용자 대화 API 라우터, LangGraph node 연결, DynamoDB checkpoint 저장, Contract JSON 반영을 포함하지 않습니다.

## Contract Draft Compile

`contract_compile.py`는 최신 slot state를 내부 Contract JSON draft 형태로 조립합니다.

입력 기준:

- `site_id`, `user_id`
- `domain`, `domain_label`
- `selected_template`
- `slot_registry`
- `known_answers`
- `missing_slots`
- `contract_version`

출력 기준:

```json
{
  "contract_status": "draft",
  "quality_status": "needs_enrichment",
  "missing_required_slots": ["contact_method"],
  "filled_slots": ["business_name", "core_services"],
  "draft": {
    "contract_version": 1,
    "domain": "tax_accounting",
    "domain_label": "세무/회계",
    "selected_template": "landing/13-tax-accounting",
    "slots": {
      "core_services": {
        "label": "핵심 서비스",
        "required": true,
        "value": "기장 대리, 종합소득세 신고, 법인세 신고",
        "filled": true
      }
    }
  }
}
```

조립 기준:

- `slot_registry`에 정의된 slot만 draft에 포함
- 답변이 있는 slot은 `filled=true`, 답변이 없으면 `filled=false`
- 답변이 없는 slot의 `value`는 `null`
- 필수 slot이 모두 채워지면 `quality_status=ready_for_quality_check`
- 필수 slot이 남아 있으면 `quality_status=needs_enrichment`

이번 범위에서는 Contract JSON 최종 schema validation, P4 API/S3 전달, LangGraph node 연결, Bedrock/LLM 보완을 포함하지 않습니다.

## Contract Quality Check

`contract_quality_check.py`는 Contract draft가 preview 단계로 넘어갈 수 있는지 로컬 규칙으로 판정합니다.

입력 기준:

- Contract draft dict
- `required_slot_threshold`
- `minimum_filled_slots`

출력 기준:

```json
{
  "quality_status": "needs_enrichment",
  "preview_ready": false,
  "generation_ready": false,
  "quality_score": 0.67,
  "missing_required_slots": ["contact_method"],
  "reasons": ["required_slots_missing"]
}
```

판정 기준:

- 필수 slot이 누락되면 `quality_status=needs_enrichment`
- 채워진 slot 개수가 `minimum_filled_slots`보다 적으면 `needs_enrichment`
- 필수 slot 충족률이 threshold 이상이고 최소 slot 개수를 만족하면 `ready_for_preview`
- preview 가능 상태일 때만 `preview_ready=true`
- `generation_ready`는 schema validation/P4 adapter 이후 단계에서만 true로 전환
- 공백 문자열, 빈 list, 빈 dict 값은 미충족으로 처리

이번 범위에서는 JSON Schema validation, P4 API/S3 전달, LangGraph node 연결, Bedrock/LLM 보완을 포함하지 않습니다.

## Storage Guardrails

`storage_guardrails.py`는 S3 저장 전 content를 검사하는 adapter 스켈레톤입니다.

검사 대상:

- `user_input`
- `p2_markdown`
- `contract_draft`
- `llm_output`

출력 기준:

```json
{
  "target": "contract_draft",
  "action": "NONE",
  "store_allowed": true,
  "masked_output": null,
  "reasons": ["guardrail_passed"]
}
```

차단 기준:

- prompt injection 의심 문구 포함
- 이메일/전화번호/주민등록번호 형태의 PII 의심 패턴 포함
- 차단 시 `action=GUARDRAIL_INTERVENED`, `store_allowed=false`
- dict 형태의 Contract draft는 JSON string으로 직렬화해 검사

이번 범위에서는 실제 AWS Bedrock Guardrails `ApplyGuardrail` 호출, guardrail id/version env 설정, S3 저장 adapter 연결을 포함하지 않습니다.

## Chat State Checkpoint Store

`chat_state_store.py`는 DynamoDB single-table 설계를 기준으로 세션 상태와 체크포인트 저장 경계를 정의합니다.

저장 대상:

- session metadata
- chat message
- checkpoint state
- guardrail summary
- contract draft reference/state

키 설계:

```text
PK = SESSION#{session_id}
SK = META
SK = MESSAGE#{created_at}#{message_id}
SK = CHECKPOINT#{stage}#{version}
SK = CONTRACT#{version}
SK = GUARDRAIL#{created_at}#{target}
```

출력 기준:

```json
{
  "pk": "SESSION#session_001",
  "sk": "CHECKPOINT#contract_quality_check#000001",
  "item_type": "checkpoint",
  "data": {
    "session_id": "session_001",
    "stage": "contract_quality_check",
    "version": 1
  }
}
```

저장 기준:

- DynamoDB PK/SK 생성 규칙을 코드 상수로 고정
- `save_session_metadata`, `append_message`, `save_checkpoint`, `load_latest_checkpoint`, `save_guardrail_result` repository 경계 제공
- 로컬 smoke test에서는 `InMemoryChatStateStore`로 저장/조회 검증
- AWS dev smoke test에서는 `Boto3ChatStateStore`로 `hezo_agent_chat` write/read/delete 검증
- 실제 DynamoDB table, boto3 client, IAM 기준은 `infra/chat` 문서를 따른다.
- TTL, GSI 설정은 후속 운영 이슈에서 처리
- LangGraph custom checkpointer 연결은 후속 이슈에서 처리

이번 범위에서는 LangGraph custom checkpointer, AgentCore Runtime 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_dynamodb_aws_smoke.py
```

## S3 Artifact Storage

`s3_artifact_store.py`는 S3 bucket/key 설계를 기준으로 원문/대용량 artifact 저장 경계를 정의합니다.

저장 대상:

- chat transcript
- P2 markdown
- Contract draft
- Contract final
- guardrail report

물리 bucket 기준:

```text
hezo-chat
hezo-wiki
hezo-artifacts
```

key 설계:

```text
sessions/{session_id}/transcripts/{version}.json
domains/{domain}/question_guides/{version}.md
sites/{site_id}/contracts/draft/{version}.json
sites/{site_id}/contract_final.json
sessions/{session_id}/guardrails/{target}/{timestamp}.json
```

출력 기준:

```json
{
  "bucket": "hezo-artifacts",
  "key": "sites/site_001/contracts/draft/000001.json",
  "uri": "s3://hezo-artifacts/sites/site_001/contracts/draft/000001.json",
  "artifact_kind": "contract_draft",
  "content_type": "application/json"
}
```

저장 기준:

- bucket/key 생성 규칙을 코드 상수로 고정
- `build_artifact_ref`, `put_artifact`, `get_artifact` repository 경계 제공
- 로컬 smoke test에서는 `InMemoryS3ArtifactStore`로 저장/조회 검증
- AWS dev smoke test에서는 `Boto3S3ArtifactStore`로 `hezo-chat` write/read/delete 검증
- `store_allowed=false` 또는 `guardrail_action != NONE`이면 저장을 거부
- 실제 S3 bucket, boto3 client, IAM 기준은 `infra/chat` 문서를 따른다.
- KMS/SSE, lifecycle policy 고도화는 후속 운영 이슈에서 처리

이번 범위에서는 AgentCore Runtime 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_s3_aws_smoke.py
```

## Bedrock Claude Invocation

`bedrock_claude_adapter.py`는 Bedrock Claude 호출 입력/출력 경계를 정의합니다.

호출 대상:

- `question_enrichment`
- `contract_enrichment`
- `assistant_reply`

입력 기준:

- `use_case`
- `system_prompt`
- `messages`
- `context`
- `model_id`
- `max_tokens`
- `temperature`

출력 기준:

```json
{
  "status": "succeeded",
  "text": "부족한 슬롯을 확인하기 위한 보완 질문 후보를 생성했습니다.",
  "model_id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
  "usage": {
    "input_tokens": 12,
    "output_tokens": 7,
    "total_tokens": 19
  },
  "latency_ms": 25,
  "reasons": ["mock_invocation_succeeded", "question_enrichment"]
}
```

호출 기준:

- Claude는 HEZO 비즈니스 규칙의 source of truth가 아니라 Rule Engine 결과를 보완하는 역할로 제한
- 빈 메시지, 빈 system prompt, 잘못된 use case는 실패 결과로 정규화
- prompt injection 의심 문구가 포함된 입력은 mock 단계에서도 실패 처리
- LLM 출력은 저장 전 Guardrails adapter의 검사 대상
- AWS dev smoke test에서는 `Boto3BedrockClaudeInvoker`로 Bedrock Runtime Converse API 호출 검증
- 서울 리전 dev 호출은 `HEZO_BEDROCK_INFERENCE_PROFILE_ID`를 우선 사용하고, 값이 없으면 `HEZO_BEDROCK_MODEL_ID`로 fallback
- retry/backoff, streaming은 후속 이슈에서 처리

이번 범위에서는 AgentCore Runtime 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_bedrock_claude_aws_smoke.py
```

## Bedrock Guardrails ApplyGuardrail

`bedrock_guardrails_adapter.py`는 Bedrock Guardrails `ApplyGuardrail` 호출 입력/출력 경계를 정의합니다.

검사 대상:

- `user_input`
- `p2_markdown`
- `contract_draft`
- `llm_output`

입력 기준:

- `target`
- `content`
- `source`
- `guardrail_id`
- `guardrail_version`
- `metadata`

출력 기준:

```json
{
  "status": "succeeded",
  "target": "llm_output",
  "source": "OUTPUT",
  "action": "NONE",
  "store_allowed": true,
  "masked_output": null,
  "reasons": ["guardrail_passed"],
  "assessments": [
    {
      "policy": "mock_guardrail",
      "reason": "guardrail_passed",
      "blocked": false
    }
  ],
  "latency_ms": 18
}
```

검사 기준:

- safe content는 `action=NONE`, `store_allowed=true`
- prompt injection 의심 문구는 `GUARDRAIL_INTERVENED`
- 이메일/전화번호/주민등록번호 형태의 PII 의심 패턴은 `GUARDRAIL_INTERVENED`
- guardrail id/version 누락, 빈 content는 실패 결과로 정규화
- 기존 `storage_guardrails.py`와 호환되도록 `action`, `store_allowed`, `reasons` 구조 유지
- AWS dev smoke test에서는 `Boto3BedrockGuardrailsClient`로 Bedrock Runtime `ApplyGuardrail` API 호출 검증
- `GUARDRAIL_INTERVENED` 응답은 저장을 막기 위해 `store_allowed=false`로 정규화

이번 범위에서는 guardrail 생성/정책 설정을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_bedrock_guardrails_aws_smoke.py
```

## Guarded Claude Reply Flow

`guarded_claude_flow.py`는 사용자 입력을 Claude에 전달하기 전후로 Guardrails를 적용하는 요청 단위 흐름을 정의합니다.

흐름:

```text
user_input
-> Guardrail INPUT
-> Claude assistant_reply
-> Guardrail OUTPUT
-> final_text
```

분기 기준:

- 입력 Guardrail이 `GUARDRAIL_INTERVENED` 또는 `failed`이면 Claude를 호출하지 않음
- Claude 호출이 실패하면 출력 Guardrail을 수행하지 않고 실패 결과로 정규화
- 출력 Guardrail이 `GUARDRAIL_INTERVENED`이면 masked output 또는 기본 차단 문구를 `final_text`로 반환
- 모든 단계가 통과하면 Claude 응답 text를 `final_text`로 반환

출력 기준:

```json
{
  "status": "succeeded",
  "stage": "completed",
  "final_text": "OK",
  "input_guardrail": {
    "action": "NONE",
    "store_allowed": true
  },
  "claude_result": {
    "status": "succeeded"
  },
  "output_guardrail": {
    "action": "NONE",
    "store_allowed": true
  },
  "reasons": [
    "input_guardrail_passed",
    "claude_invocation_succeeded",
    "output_guardrail_passed"
  ]
}
```

이번 범위에서는 실제 LangGraph `StateGraph` 런타임 연결, DynamoDB/S3 저장 연결, 사용자 대화 API 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_guarded_claude_aws_smoke.py
```

## Chat Graph Skeleton

`chat_graph.py`는 P1 채팅 에이전트 stage들을 deterministic graph 순서로 연결하는 스켈레톤입니다.

이번 범위에서는 실제 `langgraph` package 의존이나 `StateGraph` 런타임 연결을 포함하지 않고, 후속 전환을 위한 state shape와 node boundary를 먼저 고정합니다.

graph node 순서:

```text
p2_markdown_request
-> p2_markdown_review
-> proactive_questioning
-> slot_answer_state
-> contract_compile
-> contract_quality_check
-> bedrock_guardrails
-> chat_state_checkpoint
-> s3_artifact_storage
```

state 기준:

- `session_id`
- `domain`
- `slot_registry`
- `known_answers`
- `missing_slots`
- `contract_draft`
- `quality_check`
- `guardrail_result`
- `checkpoint_ref`
- `artifact_refs`

출력 기준:

```json
{
  "stage": "s3_artifact_storage",
  "contract_draft": {
    "site_id": "site_001",
    "domain": "tax_accounting"
  },
  "quality_check": {
    "quality_status": "needs_enrichment"
  },
  "guardrail_result": {
    "action": "NONE",
    "store_allowed": true
  },
  "checkpoint_ref": {
    "pk": "SESSION#session_001",
    "sk": "CHECKPOINT#bedrock_guardrails#000001"
  },
  "artifact_refs": [
    {
      "uri": "s3://dev-hezo-p4-contracts/sites/site_001/contracts/draft/000001.json"
    }
  ]
}
```

이번 범위에서는 실제 LangGraph `StateGraph`, DynamoDB/S3/Boto3 호출, Bedrock 호출, AgentCore Runtime 연결을 포함하지 않습니다.

## Local Smoke Test

```bash
python3 agents/chat/test_agent_local.py
```
