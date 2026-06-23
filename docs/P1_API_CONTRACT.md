# P1 Chat Agent HTTP/API 계약

- 대상: `agents/chat` (P1 챗봇 에이전트)
- 소비자: HEZO-backend P1 프록시(`/api/v1/sites/{site_id}/chat`), 프론트
- 상태: 백엔드 PR [HEZO-backend#81](https://github.com/MIJUNG-HEZO/HEZO-backend/pull/81)(#80)과 정합 — 본 문서로 고정
- 관련 이슈: #196

이 문서는 P1 에이전트가 받는 **요청 envelope**와 돌려주는 **응답 shape**를 고정한다.
계약 변경은 백엔드/프론트 동시 영향이므로 반드시 합의 후 본 문서를 함께 갱신한다.

---

## 1. 호출 방식

P1은 AWS Bedrock AgentCore Runtime 위에서 실행되며, 백엔드는 boto3
`bedrock-agentcore.invoke_agent_runtime()`으로 호출한다.

```text
agentRuntimeArn = arn:aws:bedrock-agentcore:ap-northeast-2:492554570964:runtime/hezo_chat_agent_dev-oGeowE4jgN
payload         = json.dumps(<요청 객체>)
contentType     = application/json
accept          = application/json
```

응답 본문은 스트림 body로 오며, 백엔드는 `json.loads(resp["body"].read())`로 파싱한다.

> 로컬/컨테이너에서는 동일 페이로드를 `POST /invocations`(FastAPI)로도 받는다.
> 엔드포인트: `/invoke`, `/invocations`, `/` (모두 동일 핸들러), 헬스: `GET /ping`, `GET /health`.

---

## 2. 요청 envelope

모든 실제 입력은 **`sessionAttributes` 안**에 담는다. (top-level flat 아님)

```jsonc
{
  "sessionId": "<세션 ID>",          // P1이 session_id로 사용 (필수)
  "inputText": "<사용자 발화>",       // AgentCore 표준 필드, P1 로직은 sessionAttributes.answer 사용
  "sessionAttributes": {
    "action": "chat_turn",            // "session_start" | "chat_turn" | "graph_smoke"
    // action별 필드는 아래 참조
  }
}
```

- `session_id`는 `payload.sessionId` → (없으면) `sessionAttributes.session_id` → (없으면) `"session_001"` 순으로 결정된다.
- `sessionAttributes` 값은 문자열로 전달될 수 있으며, P1은 필요한 필드를 파싱한다(`known_answers`는 JSON 문자열 허용).

---

## 3. action: `chat_turn` (메인 대화 턴)

### 3.1 요청 `sessionAttributes`

| 필드 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `action` | string | ✅ | `"chat_turn"` |
| `site_id` | string | ✅ | 사이트 ID (contract_final 저장 키) |
| `user_id` | string | ✅ | 사용자 ID |
| `answer` | string | ✅ | 이번 턴 사용자 답변 (= `inputText`와 동일) |
| `answered_slot` | string | ✅ | 직전 에이전트가 물은 슬롯 키 (첫 턴은 `""`) |
| `known_answers` | string(JSON) | ✅ | 누적 슬롯 값. 프론트/백엔드가 매 턴 전달. 빈 값은 `"{}"` |
| `domain` | string | ✅ | 도메인 키 (예: `tax-accounting`). 미상이면 `"general"` |
| `domain_label` | string | △ | 업종 한글 레이블 (예: `세무/회계`) |
| `category` | string | ✅ | `"landing"` \| `"blog"` \| `"store"` |
| `selected_template` | string | ✅ | 템플릿 ID (예: `landing/13-tax-accounting`). 슬롯 레지스트리 결정 |
| `storage_mode` | string | ✅ | `"aws"`여야 위키 로드·S3 저장·companion 추출·DynamoDB 동작. 그 외는 in-memory(목) |

> **중요**: `storage_mode`가 `"aws"`가 아니면 P2 위키 주입·Haiku companion 추출·contract_final 저장이 전부 스킵된다. 실서비스 호출은 반드시 `"aws"`.

### 3.2 known_answers 라운드트립

- 턴 간 슬롯 상태의 **단일 진실 공급원은 프론트/백엔드의 `known_answers`**다.
- P1은 `known_answers`가 전달되면 그것을 기준으로 `missing_slots`를 재계산한다
  (전달되면 DynamoDB 체크포인트 복원 경로는 사용하지 않음).
- 응답의 `metadata.known_answers`를 다음 턴 요청에 그대로 다시 넣는 방식으로 누적한다.

### 3.3 응답

```jsonc
{
  "output": "<사용자에게 보여줄 어시스턴트 메시지>",   // = metadata.assistant_reply (실패 시 rule-based 폴백)
  "sessionState": {
    "sessionId": "<세션 ID>",
    "action": "chat_turn",
    "stage": "<next_stage 또는 turn_status>"
  },
  "metadata": {
    "turn_status": "answer_accepted",
    "next_stage": "proactive_questioning",
    "known_answers": { "business_name": "한빛세무회계", "business_region": "서울 강남구" },
    "missing_slots": ["tax_services", "phone"],
    "question_candidates": [
      { "slot": "tax_services", "question": "제공하는 세무 서비스 3가지와 주요 고객층을 알려주세요." }
    ],
    "assistant_reply": "...",
    "reply_status": "succeeded"
    // 그 외 내부 필드 다수 (intent_guard, slot_answer, message_refs, recent_messages 등)
  }
}
```

### 3.4 백엔드 응답 매핑 (PR #81 기준)

| 백엔드 `ChatResponse` | P1 응답 경로 |
|---|---|
| `assistant_message` | `output` |
| `turn_status` | `metadata.turn_status` |
| `next_stage` | `metadata.next_stage` |
| `slot_filled` | `metadata.known_answers` |
| `missing_slots` | `metadata.missing_slots` |
| `current_slot` | `metadata.question_candidates[0].slot` (없으면 `""`) |

`current_slot`은 프론트가 **다음 턴의 `answered_slot`**으로 되돌려보낸다.

### 3.5 enum

`turn_status`:

| 값 | 의미 |
|---|---|
| `answer_accepted` | 답변 수용, 다음 슬롯 질문 진행 |
| `answer_rejected` | off_topic/ambiguous로 거부, 같은 슬롯 재질문 |
| `ready_for_contract_compile` | 필수 슬롯 충족, contract 단계로 |

`next_stage`:

| 값 | 의미 |
|---|---|
| `proactive_questioning` | 다음 슬롯 적극적 질의 |
| `retry_answer` | 같은 질문 재시도 |
| `contract_compile` | 슬롯 수집 완료 → contract_final 저장 트리거 |

### 3.6 부수 효과 (`next_stage == "contract_compile"` AND `storage_mode == "aws"`)

해당 턴에서 P1은 다음을 S3에 저장한다.

| 산출물 | 경로 |
|---|---|
| 최종 contract | `s3://hezo-artifacts/sites/{site_id}/contract_final.json` |
| 채팅 transcript(MD) | `s3://hezo-chat/sites/{site_id}/chat_{session_id}.md` |
| P2 보강 MD(룰셋 통과 시) | `s3://hezo-wiki-staging/pending/{category}/{domain}.md` |

> `contract_final.json`은 P4가 소비한다. (스키마·게이트는 후속 작업에서 P4와 정합 — 본 문서 범위 밖)

---

## 4. action: `session_start` (보조)

세션 초기화 + P2 위키 markdown 사전 로드/검수용. 응답은 `start_chat_session(...).to_dict()`
(세션 메타·P2 검수 상태)를 `metadata`로 반환한다. MVP 메인 흐름은 `chat_turn`이 담당하며,
프론트가 `chat_turn`만으로 신규 세션을 시작해도 동작한다(known_answers 빈 값 → 전체 슬롯 missing).

요청 `sessionAttributes` 주요 필드: `action="session_start"`, `site_id`, `user_id`,
`category`, `domain`, `domain_label`, `selected_template`, `storage_mode`.

---

## 5. action: `graph_smoke` (내부 스모크)

`run_chat_graph` 결정적 파이프라인 스모크 테스트용. **운영 트래픽에서 사용하지 않는다.**
`sessionAttributes.action`이 없으면 기본값이 `graph_smoke`이므로, 실호출은 반드시
`action`을 명시한다.

---

## 6. 세션 연속성 메모

- 백엔드는 현재 `invoke_agent_runtime`에 `runtimeSessionId`를 전달하지 않는다.
  → AgentCore 레벨 마이크로VM 세션 친화성은 없으나, P1이 매 턴 `known_answers`로
  상태를 재구성하므로 기능상 연속성은 유지된다.
- DynamoDB 체크포인트 기반 복원 경로는 현재 `known_answers`가 매 턴 전달되어 사실상
  미사용. TTL/메시지 저장 범위와 함께 별도 점검(task 7)에서 재정의한다.

---

## 7. 변경 관리

- 본 계약 변경은 P1 ↔ backend ↔ 프론트 동시 영향. 변경 시 본 문서 + 백엔드
  `app/schemas/chat.py` + `chat_with_p1()`를 함께 갱신하고 PR을 교차 링크한다.
- 필드 추가는 하위호환(옵셔널 + 기본값)을 우선한다.
