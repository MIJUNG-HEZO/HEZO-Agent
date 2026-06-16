# HEZO Chat Agent

P1 채팅 에이전트는 사용자 대화에서 도메인을 확정하고, P2 markdown을 검수한 뒤 적극적 질의를 통해 Contract JSON 초안을 만드는 Agent입니다.

## 책임 범위

- 도메인/업종 확정
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
- P2 markdown 수신 검수 필드
- 로컬 smoke test

제외:

- 실제 LangGraph graph 구현
- Bedrock 호출
- DynamoDB custom checkpointer
- S3 3개 물리 버킷 연동
- AgentCore Runtime 배포

## Stage 흐름

```text
domain_selection
-> p2_markdown_review
-> proactive_questioning
-> contract_compile
-> contract_quality_check
```

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

## Local Smoke Test

```bash
python3 agents/chat/test_agent_local.py
```
