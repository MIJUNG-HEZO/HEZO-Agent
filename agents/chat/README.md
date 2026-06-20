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

## MVP 개발 범위

이번 범위는 로컬 순수 로직 smoke를 기본으로 유지하되, AWS dev 리소스가 필요한 경계는 별도 AWS smoke test로 검증합니다.

포함:

- 기본 agent config
- mock state 구조
- P2 markdown 요청 payload 생성 순수 로직
- P2 markdown 수신 검수 필드
- P2 markdown 수신 검수 순수 로직
- 적극적 질의 후보 생성 순수 로직
- 대화 1턴 답변 반영/다음 stage 판단 순수 로직
- 사용자 답변 slot state 반영 순수 로직
- Contract JSON draft compile 순수 로직
- Contract draft quality check 순수 로직
- 저장 전 Guardrails adapter 스켈레톤
- DynamoDB chat state/checkpoint 저장소 스켈레톤
- S3 artifact storage adapter 스켈레톤
- Bedrock Claude invocation adapter 스켈레톤
- Bedrock Guardrails ApplyGuardrail adapter 스켈레톤
- Bedrock Claude intent classifier 경계
- LangGraph chat graph 스켈레톤
- AgentCore 호환 HTTP wrapper
- 로컬 smoke test
- Bedrock Claude/Guardrails/intent classifier AWS smoke test

제외:

- 실제 LangGraph `StateGraph` 구현
- 실제 P2 API 호출
- 실제 Backend 사용자 대화 API 라우터 연동
- 실제 DynamoDB custom checkpointer
- 실제 S3 3개 물리 버킷 연동
- AgentCore Runtime 배포

## Stage 흐름

```text
domain_selection
-> chat_session_start
-> p2_markdown_request
-> p2_markdown_load
-> p2_markdown_parse
-> p2_markdown_review
-> proactive_questioning
-> chat_turn_handler
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
- `category`, `domain`, `domain_label`
- `selected_template`
- `known_answers`
- `missing_slots`
- `request_reason`

출력 기준:

```json
{
  "payload_version": "v0.2",
  "target_artifact": "industry_domain_knowledge_markdown",
  "category": "landing",
  "domain": "tax_accounting",
  "domain_label": "세무/회계",
  "selected_template": "landing/13-tax-accounting",
  "request_reason": "initial_domain_selected",
  "missing_slots": ["core_services", "contact_method"]
}
```

검증 기준:

- 필수 식별자 누락 시 실패
- `category` 누락 시 실패
- `domain` 누락 시 실패
- `missing_slots`가 비어 있어도 payload 생성 가능

이번 범위에서는 실제 P2 API 호출, S3 저장, Bedrock/LangGraph 호출을 포함하지 않습니다.

## Chat Session Start Pipeline

`chat_session_start.py`는 도메인 선택 이후 첫 채팅 턴에 필요한 내부 파이프라인을 묶습니다.

처리 순서:

```text
P2 markdown S3 load
-> P2 markdown parse
-> P1 markdown review
-> proactive question candidates
-> LLM 보완 필요 여부 판단
```

입력 기준:

- `session_id`, `site_id`, `user_id`
- `category`, `domain`, `domain_label`, `selected_template`
- `slot_registry`, `known_answers`, `missing_slots`
- `source_s3_key`, `version`

출력 기준:

```json
{
  "status": "ready_for_user_question",
  "next_stage": "proactive_questioning",
  "llm_required": false,
  "question_candidates": [
    {
      "slot": "core_services",
      "question": "핵심 세무 서비스는 무엇인가요?",
      "source": "p2_markdown"
    }
  ]
}
```

판단 기준:

- P2 markdown parse/review가 모두 `passed`이고 P2 기반 질문 후보가 충분하면 LLM 호출을 생략합니다.
- P2 markdown이 일부 부족하거나 fallback 질문이 섞이면 `llm_required=true`로 표시합니다.
- P2 markdown review가 실패하면 `next_stage=p2_retry`로 정규화합니다.

이번 범위에서는 실제 HTTP API 라우터, 사용자 세션 저장, AgentCore Runtime 배포, Claude 호출 실행을 포함하지 않습니다.

## P2 Markdown S3 Loader

`p2_markdown_loader.py`는 P2가 S3에 저장한 markdown artifact를 조회하고 parser 입력으로 변환합니다.

입력 기준:

- `category`, `domain`, `expected_domain`
- `source_s3_key`
- `source_count`, `source_grade`
- `bucket`

key 결정 기준:

- `source_s3_key`가 있으면 해당 key를 우선 사용
- `source_s3_key`가 없으면 `industries/{category}/{domain}.md` 규칙 사용

출력 기준:

```json
{
  "ref": {
    "bucket": "hezo-wiki",
    "key": "industries/landing/tax_accounting.md",
    "artifact_kind": "p2_markdown"
  },
  "parse_input": {
    "category": "landing",
    "domain": "tax_accounting",
    "expected_domain": "tax_accounting",
    "source_s3_key": "industries/landing/tax_accounting.md",
    "version": "v001"
  }
}
```

이번 범위에서는 도메인 선택 API 연결, 사용자 세션 graph 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_p2_markdown_s3_aws_smoke.py
```

## P2 Markdown Parser / Normalizer

`p2_markdown_parser.py`는 P2가 S3에 저장한 domain knowledge markdown 원문을 P1 내부 표준 구조로 변환합니다.

입력 기준:

- `category`, `domain`, `expected_domain`
- `content`
- `source_s3_key`
- `version`
- `source_count`, `source_grade`

출력 기준:

```json
{
  "domain": "tax_accounting",
  "category": "landing",
  "label": "세무/회계",
  "p2_confidence": 0.82,
  "parse_status": "passed",
  "knowledge_sections": [
    {
      "section_id": "section_001",
      "title": "핵심 서비스 범위 [S1]",
      "source_refs": ["S1"]
    }
  ],
  "evidence_refs": [
    {
      "ref_id": "S1",
      "text": "국세청 세무 서비스 안내 페이지"
    }
  ],
  "warnings": ["parsed"]
}
```

파싱 기준:

- frontmatter의 `domain`, `category`, `label`, `confidence`, `source_urls`를 분리
- `## N. 주제 ... [S?]` 섹션을 `knowledge_sections`로 분리
- `[S?]` 출처 목록을 `evidence_refs`로 분리
- confidence metadata가 있으면 `p2_confidence`로 반영
- 출처가 부족하면 `needs_enrichment`
- 빈 markdown, domain/category mismatch, 지식 섹션을 추출할 수 없는 markdown은 `failed`

연결 기준:

- `source_count`, `source_grade`는 `review_p2_markdown()` 입력으로 전달 가능
- `knowledge_sections`는 P1이 `slot_registry`와 조합해 적극적 질의 질문 후보를 생성할 때 사용

이번 범위에서는 Bedrock/LangGraph 호출을 포함하지 않습니다.

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

## Chat Turn Handler

`chat_turn_handler.py`는 사용자 답변 1턴을 처리하는 orchestration 경계입니다.

처리 순서:

```text
사용자 답변 수신
-> LLM intent classifier 적용
-> slot_answer_state 적용
-> missing_slots 갱신
-> 다음 질문 후보 생성 또는 contract_compile 진입 판단
```

입력 기준:

- `session_id`, `site_id`, `user_id`
- `domain`, `domain_label`
- `slot_registry`, `known_answers`, `missing_slots`
- `answered_slot`, `answer`
- `p1_markdown_review_status`, `p2_markdown_usable_for_questions`
- `p2_knowledge_summary`
- `intent_classifier`

출력 기준:

```json
{
  "turn_status": "answer_accepted",
  "next_stage": "proactive_questioning",
  "intent_guard": {
    "intent": "on_topic",
    "confidence": 1.0,
    "classification_source": "llm",
    "store_allowed": true
  },
  "store_allowed": true,
  "known_answers": {
    "business_name": "한빛 세무회계",
    "core_services": "기장 대리, 종합소득세 신고, 법인세 신고"
  },
  "missing_slots": ["contact_method"],
  "question_candidates": [
    {
      "slot": "contact_method",
      "source": "p2_markdown"
    }
  ]
}
```

판단 기준:

- `chat_intent_guard.py`는 키워드 블랙리스트가 아니라 LLM intent classifier 경계입니다.
- 매 턴 현재 질문, answered slot, domain context를 함께 전달해 `on_topic`, `off_topic`, `ambiguous`를 분류합니다.
- `on_topic`만 `known_answers`, `missing_slots`, Contract JSON 후보에 반영할 수 있습니다.
- `off_topic`, `ambiguous`, classifier 미설정/실패 입력은 `store_allowed=false`로 fail-closed 처리하고 slot/Contract에 반영하지 않습니다.
- 오프토픽 입력 후에는 기존 `missing_slots` 기준 질문 후보를 다시 반환합니다.
- 답변이 유효하면 `known_answers`와 `missing_slots`를 갱신합니다.
- 남은 필수 slot이 있으면 `next_stage=proactive_questioning`으로 다음 질문 후보를 반환합니다.
- 모든 필수 slot이 채워지면 `next_stage=contract_compile`로 전환합니다.
- 빈 답변이나 알 수 없는 slot은 `next_stage=retry_answer`로 정규화합니다.

이번 범위에서는 실제 HTTP API 라우터, DynamoDB message/checkpoint 저장, LangGraph node 연결을 포함하지 않습니다.

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
- `save_session_metadata`, `append_message`, `load_recent_messages`, `save_checkpoint`, `load_latest_checkpoint`, `save_guardrail_result` repository 경계 제공
- 로컬 smoke test에서는 `InMemoryChatStateStore`로 저장/조회 검증
- AWS dev smoke test에서는 `Boto3ChatStateStore`로 `hezo_agent_chat` write/read/delete 검증
- chat graph는 `ChatStateStore` 주입을 통해 session metadata와 checkpoint를 저장
- HTTP `chat_turn`은 `storage_mode=aws`에서 user/assistant message를 DynamoDB에 저장하고 최근 메시지를 조회
- off-topic/ambiguous/needs_classification 등 `store_allowed=false` 턴은 message history에 저장하지 않음
- 실제 DynamoDB table, boto3 client, IAM 기준은 `infra/chat` 문서를 따른다.
- TTL, GSI 설정은 후속 운영 이슈에서 처리

이번 범위에서는 실제 `langgraph` package의 custom checkpointer 구현과 AgentCore Runtime 연결을 포함하지 않습니다.
현재 graph checkpoint stage는 repository 경계(`ChatStateStore`)를 통해 DynamoDB 저장소로 전환 가능합니다.
현재 message history는 다음 턴 context 조회용 최신 N개 저장/조회에 한정하며, 장기 transcript S3 저장은 별도 후속 범위입니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_dynamodb_aws_smoke.py
python3 agents/chat/test_chat_graph_dynamodb_aws_smoke.py
python3 agents/chat/test_chat_message_dynamodb_aws_smoke.py
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
hezo-wiki-staging
hezo-artifacts
```

key 설계:

```text
sessions/{session_id}/transcripts/{version}.json
industries/{category}/{domain}.md
pending/{category}/{domain}.md
sites/{site_id}/contracts/draft/{version}.json
sites/{site_id}/contract_final.json
sessions/{session_id}/guardrails/{target}/{timestamp}.json
```

P2 markdown load 정책:

- `source_s3_key`가 명시되면 해당 key를 우선 사용합니다.
- `source_s3_key`가 없으면 dev fallback으로 `industries/{category}/{domain}.md`를 사용합니다.
- P2 팀의 최종 prefix가 확정되면 호출 payload의 `source_s3_key`만 조정하고 loader 계약은 유지합니다.

P1 enriched markdown 저장 정책:

- P2 원본 markdown은 덮어쓰지 않습니다.
- 사용자 대화와 보강 결과를 반영한 보강 markdown은 `hezo-wiki-staging/pending/{category}/{domain}.md`에 저장합니다.
- Contract JSON은 `hezo-artifacts`에 저장하고, P1 보강 markdown 산출물은 `hezo-wiki-staging`에 저장해 책임을 분리합니다.

P4 Contract 저장 정책:

- Contract draft는 `hezo-artifacts/sites/{site_id}/contracts/draft/{version}.json`에 저장합니다.
- Contract final은 quality check가 `preview_ready=true`일 때 `hezo-artifacts/sites/{site_id}/contract_final.json`에 저장합니다.

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
- graph S3 pipeline smoke test에서는 `hezo-wiki` P2 markdown load, `hezo-wiki-staging` enriched markdown 저장, `hezo-artifacts` Contract draft/final 저장을 검증
- `store_allowed=false` 또는 `guardrail_action != NONE`이면 저장을 거부
- 실제 S3 bucket, boto3 client, IAM 기준은 `infra/chat` 문서를 따른다.
- KMS/SSE, lifecycle policy 고도화는 후속 운영 이슈에서 처리

이번 범위에서는 AgentCore Runtime 연결을 포함하지 않습니다.

AWS smoke test:

```bash
python3 -m pip install -r agents/chat/requirements.txt
python3 agents/chat/test_s3_aws_smoke.py
python3 agents/chat/test_p2_markdown_s3_aws_smoke.py
python3 agents/chat/test_chat_graph_s3_pipeline_aws_smoke.py
```

## Bedrock Claude Invocation

`bedrock_claude_adapter.py`는 Bedrock Claude 호출 입력/출력 경계를 정의합니다.

호출 대상:

- `question_enrichment`
- `contract_enrichment`
- `assistant_reply`
- `intent_classification`

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
python3 agents/chat/test_chat_intent_classifier_aws_smoke.py
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
-> p2_markdown_load
-> p2_markdown_parse
-> p2_markdown_review
-> proactive_questioning
-> chat_turn_handler
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
    "metadata": {
      "pk": "SESSION#session_001",
      "sk": "META"
    },
    "checkpoint": {
      "pk": "SESSION#session_001",
      "sk": "CHECKPOINT#bedrock_guardrails#000001"
    }
  },
  "artifact_refs": [
    {
      "uri": "s3://dev-hezo-p4-contracts/sites/site_001/contracts/draft/000001.json"
    }
  ]
}
```

이번 범위에서는 실제 LangGraph `StateGraph` 런타임과 AgentCore Runtime 연결을 포함하지 않습니다.
S3 artifact 경계는 `S3ArtifactStore` 주입으로 분리되어 있으며, 로컬 smoke는 in-memory store, AWS smoke는 `Boto3S3ArtifactStore`를 사용합니다.
DynamoDB checkpoint 경계는 `ChatStateStore` 주입으로 분리되어 있으며, 로컬 smoke는 in-memory store, AWS smoke는 `Boto3ChatStateStore`를 사용합니다.

## Chat HTTP Wrapper

`agent.py`는 P1 Chat Agent를 AgentCore/로컬 HTTP 호출 규격으로 노출하는 얇은 FastAPI wrapper입니다.

엔드포인트:

- `POST /invoke`
- `POST /invocations`
- `POST /`
- `GET /ping`
- `GET /health`

요청 기준:

```json
{
  "sessionId": "session_001",
  "inputText": "",
  "sessionAttributes": {
    "action": "graph_smoke"
  }
}
```

지원 action:

- `session_start`: `start_chat_session()` 호출
- `chat_turn`: `handle_chat_turn()` 호출
- `graph_smoke`: `run_chat_graph()` 호출

응답 기준:

```json
{
  "output": "chat_graph_smoke_complete — stage: s3_artifact_storage",
  "sessionState": {
    "sessionId": "session_001",
    "action": "graph_smoke",
    "stage": "s3_artifact_storage"
  },
  "metadata": {}
}
```

이번 범위에서는 실제 Backend 사용자 대화 API 라우터, AgentCore Runtime 배포를 포함하지 않습니다.

## Docker Runtime

`agents/chat/Dockerfile`은 AgentCore/ECR 배포 전 P1 Chat Agent의 컨테이너 실행 단위를 고정합니다.

Build:

```bash
docker build -f agents/chat/Dockerfile -t hezo-chat-agent:local .
```

Run:

```bash
docker run --rm -p 8080:8080 hezo-chat-agent:local
```

Health check:

```bash
curl http://localhost:8080/ping
curl http://localhost:8080/health
```

Invocation smoke:

```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "sessionId": "session_001",
    "inputText": "",
    "sessionAttributes": {
      "action": "graph_smoke"
    }
  }'
```

기본 env:

- `AWS_DEFAULT_REGION=ap-northeast-2`
- `AWS_REGION=ap-northeast-2`
- `MODEL_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0`
- `HEZO_BEDROCK_MODEL_ID=anthropic.claude-sonnet-4-5-20250929-v1:0`
- `HEZO_BEDROCK_INFERENCE_PROFILE_ID=global.anthropic.claude-sonnet-4-5-20250929-v1:0`
- `HEZO_CHAT_BUCKET=hezo-chat`
- `HEZO_P2_MARKDOWNS_BUCKET=hezo-wiki`
- `HEZO_ENRICHED_MARKDOWNS_BUCKET=hezo-wiki-staging`
- `HEZO_CONTRACTS_BUCKET=hezo-artifacts`
- `HEZO_AGENT_DYNAMODB_TABLE=hezo_agent_chat`
- `HEZO_BEDROCK_GUARDRAIL_ID=q8dcjc2um846`
- `HEZO_BEDROCK_GUARDRAIL_VERSION=DRAFT`

이번 범위에서는 실제 ECR push, AgentCore Runtime 생성/갱신을 포함하지 않습니다.

## Local Smoke Test

```bash
python3 agents/chat/test_agent_local.py
```
