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
- 로컬 smoke test

제외:

- 실제 LangGraph graph 구현
- Bedrock 호출
- 실제 P2 API 호출
- 실제 사용자 대화 API 라우터
- DynamoDB custom checkpointer
- S3 3개 물리 버킷 연동
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
```

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

## Local Smoke Test

```bash
python3 agents/chat/test_agent_local.py
```
