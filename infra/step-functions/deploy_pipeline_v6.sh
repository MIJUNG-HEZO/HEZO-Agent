#!/usr/bin/env bash
# HEZO 사이트 생성 벨트 v6 배포 (P5)
# - 하드코딩 0: 모든 값은 SSM Parameter Store에서 읽어 placeholder 치환
# - 생성·검증 = AgentCore 직접호출(ARN) / 빌드 = http(ALB) / 검증FAIL시 벨트 재시도
set -euo pipefail

REGION="ap-northeast-2"
ACCOUNT="492554570964"
SM_NAME="hezo-site-pipeline"
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/hezo-step-functions-role"
DEF_FILE="$(dirname "$0")/hezo_pipeline_v6.json"

ssm() { aws ssm get-parameter --name "$1" --region "$REGION" --query "Parameter.Value" --output text 2>/dev/null || echo ""; }

echo "── 1. SSM에서 값 읽기 (하드코딩 X) ──"
GEN_RAW="$(ssm hezo-generation-agent-endpoint-arn)"
VAL_RAW="$(ssm hezo-validation-agent-endpoint-arn)"
BUILD_EP="$(ssm hezo-build-agent-endpoint)"
CONN_ARN="$(ssm hezo-eventbridge-connection-arn)"

# AgentCore ARN을 [맨 runtime ARN] + [Qualifier(엔드포인트 이름)]로 분리.
# SSM값이 '.../runtime-endpoint/prod' 형태면 prod를 Qualifier로 떼어내고,
# 아니면 Qualifier=DEFAULT. (InvokeAgentRuntime은 둘을 따로 받아야 함)
split_arn() {  # $1=raw → 전역 _ARN, _QUAL 설정
  case "$1" in
    */runtime-endpoint/*) _ARN="${1%/runtime-endpoint/*}"; _QUAL="${1##*/runtime-endpoint/}" ;;
    *)                    _ARN="$1"; _QUAL="DEFAULT" ;;
  esac
}
split_arn "$GEN_RAW"; GEN_ARN="$_ARN"; GEN_QUAL="$_QUAL"
split_arn "$VAL_RAW"; VAL_ARN="$_ARN"; VAL_QUAL="$_QUAL"
echo "  생성 ARN : ${GEN_ARN:0:55}... (qual=$GEN_QUAL)"
echo "  검증 ARN : ${VAL_ARN:0:55}... (qual=$VAL_QUAL)"
echo "  빌드 EP  : $BUILD_EP"

# EventBridge Connection 없으면 생성 (빌드 http:invoke 인증용 — ALB는 무인증이라 더미 키)
if [ -z "$CONN_ARN" ]; then
  echo "── Connection 없음 → 생성 ──"
  CONN_ARN="$(aws events create-connection --region "$REGION" \
    --name hezo-pipeline-build-conn \
    --authorization-type API_KEY \
    --auth-parameters '{"ApiKeyAuthParameters":{"ApiKeyName":"x-hezo-internal","ApiKeyValue":"internal"}}' \
    --query ConnectionArn --output text)"
  aws ssm put-parameter --name hezo-eventbridge-connection-arn --value "$CONN_ARN" --type String --overwrite --region "$REGION" >/dev/null
  echo "  Connection 생성 + SSM 저장: ${CONN_ARN:0:60}..."
fi
echo "  Conn ARN : ${CONN_ARN:0:60}..."

[ -z "$GEN_ARN" ] && { echo "❌ 생성 ARN SSM 없음"; exit 1; }
[ -z "$VAL_ARN" ] && { echo "❌ 검증 ARN SSM 없음"; exit 1; }
[ -z "$BUILD_EP" ] && { echo "❌ 빌드 EP SSM 없음"; exit 1; }

echo "── 2. placeholder 치환 ──"
TMP="$(mktemp).json"
python3 - "$DEF_FILE" "$TMP" "$GEN_ARN" "$GEN_QUAL" "$VAL_ARN" "$VAL_QUAL" "$BUILD_EP" "$CONN_ARN" <<'PY'
import sys
src, dst, gen, gen_q, val, val_q, build, conn = sys.argv[1:]
c = open(src, encoding='utf-8').read()
c = c.replace('${GENERATION_AGENT_ARN}', gen)
c = c.replace('${GENERATION_AGENT_QUALIFIER}', gen_q)
c = c.replace('${VALIDATION_AGENT_ARN}', val)
c = c.replace('${VALIDATION_AGENT_QUALIFIER}', val_q)
c = c.replace('${BUILD_AGENT_ENDPOINT}', build)
c = c.replace('${EVENTBRIDGE_CONNECTION_ARN}', conn)
open(dst, 'w', encoding='utf-8').write(c)
print('  치환 완료')
PY

echo "── 3. 상태머신 생성/업데이트 ──"
SM_ARN="arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${SM_NAME}"
if aws stepfunctions describe-state-machine --state-machine-arn "$SM_ARN" --region "$REGION" >/dev/null 2>&1; then
  aws stepfunctions update-state-machine --state-machine-arn "$SM_ARN" \
    --definition "file://$TMP" --role-arn "$ROLE_ARN" --region "$REGION" --query "updateDate" --output text
  echo "✅ 업데이트: $SM_ARN"
else
  for i in 1 2 3 4 5; do
    R="$(aws stepfunctions create-state-machine --name "$SM_NAME" \
      --definition "file://$TMP" --role-arn "$ROLE_ARN" --type STANDARD \
      --region "$REGION" --query stateMachineArn --output text 2>&1)"
    [[ "$R" == arn:* ]] && { echo "✅ 생성: $R"; break; }
    echo "  재시도 $i (IAM 전파 대기)... $R"; sleep 5
  done
fi
rm -f "$TMP"
