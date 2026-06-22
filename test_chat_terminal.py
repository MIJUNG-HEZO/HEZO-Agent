"""터미널에서 챗봇 AgentCore를 직접 대화 테스트하는 스크립트."""

import boto3
import json
import uuid
import sys

AGENTCORE_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:492554570964:runtime/hezo_chat_agent_dev-oGeowE4jgN"
REGION = "ap-northeast-2"

client = boto3.client("bedrock-agentcore", region_name=REGION)

session_id = str(uuid.uuid4()) + "-terminal"
known_answers: dict = {}
current_slot = ""
turn = 0

print("=" * 60)
print("HEZO 챗봇 터미널 테스트")
print(f"세션 ID: {session_id}")
print("종료: Ctrl+C 또는 'exit' 입력")
print("=" * 60)
print()

# 초기 인사 메시지
print("챗봇: 안녕하세요! 홈페이지 제작을 시작할게요. 먼저 업체명(상호명)을 알려주세요.")
print()

while True:
    try:
        user_input = input("나: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n종료합니다.")
        break

    if user_input.lower() in ("exit", "quit", "종료"):
        print("종료합니다.")
        break

    if not user_input:
        continue

    turn += 1
    payload = {
        "session_id": session_id,
        "user_message": user_input,
        "answered_slot": current_slot,
        "known_answers": json.dumps(known_answers, ensure_ascii=False),
        "domain": "tax-accounting",
        "domain_label": "세무/회계",
        "category": "landing",
        "template_id": "landing/13-tax-accounting",
        "storage_mode": "aws",
        "use_aws": "true",
    }

    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_ARN,
            qualifier="DEFAULT",
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode(),
        )
        body_key = next((k for k in resp if hasattr(resp.get(k), "read")), None)
        if not body_key:
            print(f"챗봇: [오류] 응답 키를 찾을 수 없음. 키 목록: {list(resp.keys())}")
            continue

        result = json.loads(resp[body_key].read())
        meta = result.get("metadata", {})

        # 어시스턴트 응답 추출
        assistant_reply = meta.get("assistant_reply") or ""

        # 슬롯 상태 업데이트
        chat_turn = meta.get("chat_turn", {})
        new_answers = chat_turn.get("known_answers") or meta.get("known_answers") or {}
        if new_answers:
            known_answers.update(new_answers)

        candidates = meta.get("question_candidates") or []
        if candidates:
            current_slot = candidates[0].get("slot", "")
            fallback_question = candidates[0].get("question", "")
        else:
            current_slot = ""
            fallback_question = ""

        next_stage = chat_turn.get("next_stage", "")

        # 출력
        if assistant_reply:
            print(f"\n챗봇: {assistant_reply}\n")
        elif fallback_question:
            print(f"\n챗봇: {fallback_question}\n")
        else:
            print(f"\n챗봇: [응답 없음 — output: {result.get('output', '')}]\n")

        # 디버그 (슬롯 현황)
        print(f"  [슬롯] {known_answers}  |  다음: {current_slot}  |  stage: {next_stage}")
        print()

        if next_stage == "contract_compile":
            print("=" * 60)
            print("✅ 모든 슬롯 수집 완료! Contract 생성 단계로 이동합니다.")
            print(f"최종 슬롯: {json.dumps(known_answers, ensure_ascii=False, indent=2)}")
            break

    except Exception as e:
        print(f"\n챗봇: [오류] {e}\n")
