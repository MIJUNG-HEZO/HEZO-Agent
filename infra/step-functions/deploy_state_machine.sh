#!/usr/bin/env bash
# =============================================================================
# HEZO Step Functions 상태 머신 배포 스크립트 v2.0
#
# 변경사항 (v2.0):
#   - 구 Managed Bedrock Agent / Lambda 참조 제거
#   - AgentCore Runtime HTTP 엔드포인트 기반으로 교체
#   - 플레이스홀더: GENERATION_AGENT_ENDPOINT, BUILD_AGENT_ENDPOINT, EVENTBRIDGE_CONNECTION_ARN
#
# 사용법:
#   bash deploy_state_machine.sh            # 생성 또는 업데이트
#   bash deploy_state_machine.sh --setup-connection  # EventBridge Connection 먼저 생성
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -f "${REPO_ROOT}/.env" ]; then
    set -a; source <(sed 's/\r//' "${REPO_ROOT}/.env"); set +a
fi

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-rapa-cm1-21}"
STATE_MACHINE_NAME="hezo-homepage-pipeline"
DEFINITION_FILE="$(dirname "$0")/hezo_pipeline.json"
ROLE_NAME="hezo-step-functions-role"

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
error()   { echo "[ERROR] $*" >&2; exit 1; }

aws_cmd() { aws --profile "$PROFILE" "$@"; }

ssm_get() {
    local name="$1" default="${2:-}"
    aws_cmd ssm get-parameter --name "$name" --query "Parameter.Value" \
        --output text --region "$REGION" 2>/dev/null || echo "$default"
}

# =============================================================================
# EventBridge Connection 생성 (--setup-connection 플래그)
# HTTP Task 인증에 필요. 에이전트 엔드포인트가 퍼블릭 + 무인증이면 NONE 타입 사용.
# =============================================================================
setup_connection() {
    local conn_name="hezo-agent-connection"
    local existing
    existing=$(aws_cmd events list-connections \
        --name-prefix "$conn_name" \
        --region "$REGION" \
        --query "Connections[?Name=='${conn_name}'].ConnectionArn | [0]" \
        --output text 2>/dev/null || echo "")

    if [ -n "$existing" ] && [ "$existing" != "None" ]; then
        info "EventBridge Connection 이미 존재: $existing"
        echo "$existing"
        return
    fi

    local conn_arn
    conn_arn=$(aws_cmd events create-connection \
        --name "$conn_name" \
        --authorization-type "API_KEY" \
        --auth-parameters "ApiKeyAuthParameters={ApiKeyName=X-Hezo-Auth,ApiKeyValue=hezo-internal}" \
        --region "$REGION" \
        --query "ConnectionArn" --output text)

    aws_cmd ssm put-parameter \
        --name "hezo-eventbridge-connection-arn" \
        --value "$conn_arn" \
        --type String --overwrite \
        --region "$REGION" > /dev/null
    success "EventBridge Connection 생성: $conn_arn"
    echo "$conn_arn"
}

if [ "${1:-}" = "--setup-connection" ]; then
    setup_connection
    exit 0
fi

# =============================================================================
# 1. 사전 조건 확인
# =============================================================================
echo "╔══════════════════════════════════════════════════════╗"
echo "║  HEZO Step Functions 배포 v2.0                      ║"
echo "╚══════════════════════════════════════════════════════╝"

command -v aws >/dev/null 2>&1 || error "AWS CLI 미설치"

ACCOUNT_ID=$(aws_cmd sts get-caller-identity --query Account --output text) || error "AWS 인증 실패"
success "AWS 계정: $ACCOUNT_ID"

ROLE_ARN=$(aws_cmd iam get-role --role-name "$ROLE_NAME" \
    --query "Role.Arn" --output text 2>/dev/null) || \
    error "IAM 역할 ${ROLE_NAME} 없음 — IAM 설정 먼저 필요"
success "Step Functions IAM 역할: $ROLE_ARN"

# =============================================================================
# 2. 에이전트 엔드포인트 조회 (SSM → 없으면 경고 후 placeholder 사용)
# =============================================================================
info "SSM에서 에이전트 엔드포인트 조회 중..."

GENERATION_AGENT_ENDPOINT=$(ssm_get "hezo-generation-agent-endpoint" "PLACEHOLDER_GENERATION_ENDPOINT")
BUILD_AGENT_ENDPOINT=$(ssm_get "hezo-build-agent-endpoint" "PLACEHOLDER_BUILD_ENDPOINT")
VALIDATION_AGENT_ENDPOINT=$(ssm_get "hezo-validation-agent-endpoint" "PLACEHOLDER_VALIDATION_ENDPOINT")
EVENTBRIDGE_CONNECTION_ARN=$(ssm_get "hezo-eventbridge-connection-arn" "PLACEHOLDER_CONNECTION_ARN")

if [[ "$GENERATION_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-generation-agent-endpoint SSM 없음 — placeholder 사용"
    warn "  AgentCore Runtime 배포 후: aws ssm put-parameter --name hezo-generation-agent-endpoint --value <URL>"
else
    success "Generation Agent: $GENERATION_AGENT_ENDPOINT"
fi

if [[ "$BUILD_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-build-agent-endpoint SSM 없음 — placeholder 사용"
else
    success "Build Agent: $BUILD_AGENT_ENDPOINT"
fi

if [[ "$VALIDATION_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-validation-agent-endpoint SSM 없음 — placeholder 사용"
    warn "  AgentCore Runtime 배포 후: aws ssm put-parameter --name hezo-validation-agent-endpoint --value <URL>"
else
    success "Validation Agent: $VALIDATION_AGENT_ENDPOINT"
fi

if [[ "$EVENTBRIDGE_CONNECTION_ARN" == PLACEHOLDER* ]]; then
    warn "hezo-eventbridge-connection-arn SSM 없음"
    warn "  실행: bash deploy_state_machine.sh --setup-connection"
else
    success "EventBridge Connection: $EVENTBRIDGE_CONNECTION_ARN"
fi

# =============================================================================
# 3. 상태 머신 정의 파일 플레이스홀더 치환
# =============================================================================
info "플레이스홀더 치환 중..."

DEFINITION_TEMP=$(python3 -c "import tempfile; tf=tempfile.NamedTemporaryFile(suffix='.json',delete=False); print(tf.name)")
trap "rm -f '$DEFINITION_TEMP'" EXIT

python3 - "$DEFINITION_FILE" "$DEFINITION_TEMP" \
    "$ACCOUNT_ID" "$GENERATION_AGENT_ENDPOINT" "$BUILD_AGENT_ENDPOINT" "$VALIDATION_AGENT_ENDPOINT" "$EVENTBRIDGE_CONNECTION_ARN" <<'PYEOF'
import sys, json

src, dst, account, gen_ep, build_ep, val_ep, conn_arn = sys.argv[1:]
content = open(src, encoding='utf-8').read()
content = content.replace('${AWS_ACCOUNT_ID}', account)
content = content.replace('${GENERATION_AGENT_ENDPOINT}', gen_ep)
content = content.replace('${BUILD_AGENT_ENDPOINT}', build_ep)
content = content.replace('${VALIDATION_AGENT_ENDPOINT}', val_ep)
content = content.replace('${EVENTBRIDGE_CONNECTION_ARN}', conn_arn)

# JSON 유효성 검증
json.loads(content)
open(dst, 'w', encoding='utf-8').write(content)
print("JSON 유효성 검증 통과")
PYEOF

success "플레이스홀더 치환 완료"

# =============================================================================
# 4. CloudWatch 로그 그룹
# =============================================================================
LOG_GROUP="/hezo/step-functions/${STATE_MACHINE_NAME}"
if ! aws_cmd logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$REGION" \
        --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName" \
        --output text 2>/dev/null | grep -q "$LOG_GROUP"; then
    aws_cmd logs create-log-group --log-group-name "$LOG_GROUP" --region "$REGION"
    aws_cmd logs put-retention-policy --log-group-name "$LOG_GROUP" \
        --retention-in-days 90 --region "$REGION"
    success "CloudWatch 로그 그룹 생성: $LOG_GROUP"
else
    info "로그 그룹 이미 존재: $LOG_GROUP"
fi

LOG_GROUP_ARN="arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${LOG_GROUP}:*"

LOGGING_CONFIG=$(python3 -c "
import json
print(json.dumps({
  'level': 'ALL',
  'includeExecutionData': True,
  'destinations': [{'cloudWatchLogsLogGroup': {'logGroupArn': '$LOG_GROUP_ARN'}}]
}))")

# =============================================================================
# 5. 상태 머신 생성 또는 업데이트
# =============================================================================
EXISTING_ARN=$(aws_cmd stepfunctions list-state-machines \
    --region "$REGION" \
    --query "stateMachines[?name=='${STATE_MACHINE_NAME}'].stateMachineArn" \
    --output text 2>/dev/null || echo "")

if [ -n "$EXISTING_ARN" ] && [ "$EXISTING_ARN" != "None" ]; then
    info "기존 상태 머신 업데이트: $EXISTING_ARN"
    aws_cmd stepfunctions update-state-machine \
        --state-machine-arn "$EXISTING_ARN" \
        --definition "file://${DEFINITION_TEMP}" \
        --role-arn "$ROLE_ARN" \
        --logging-configuration "$LOGGING_CONFIG" \
        --region "$REGION" --output json > /dev/null
    STATE_MACHINE_ARN="$EXISTING_ARN"
    success "상태 머신 업데이트 완료"
else
    info "새 상태 머신 생성: $STATE_MACHINE_NAME"
    CREATE_RESULT=$(aws_cmd stepfunctions create-state-machine \
        --name "$STATE_MACHINE_NAME" \
        --definition "file://${DEFINITION_TEMP}" \
        --role-arn "$ROLE_ARN" \
        --type "STANDARD" \
        --logging-configuration "$LOGGING_CONFIG" \
        --tags "project=HEZO" \
        --region "$REGION" --output json)
    STATE_MACHINE_ARN=$(echo "$CREATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['stateMachineArn'])")
    success "상태 머신 생성 완료"
fi

# =============================================================================
# 6. SSM에 상태 머신 ARN 저장
# =============================================================================
aws_cmd ssm put-parameter \
    --name "hezo-step-functions-arn" \
    --value "$STATE_MACHINE_ARN" \
    --type String --overwrite \
    --region "$REGION" > /dev/null
success "SSM 저장: hezo-step-functions-arn"

# =============================================================================
# 7. 배포 요약
# =============================================================================
echo
echo "╔══════════════════════════════════════════════════════╗"
echo "║  배포 완료                                           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  상태 머신 ARN     : $STATE_MACHINE_ARN"
echo "  Generation Agent  : $GENERATION_AGENT_ENDPOINT"
echo "  Build Agent       : $BUILD_AGENT_ENDPOINT"
echo "  Validation Agent  : $VALIDATION_AGENT_ENDPOINT"
echo "  EventBridge Conn  : $EVENTBRIDGE_CONNECTION_ARN"
echo
echo "  [에이전트 엔드포인트 등록 방법]"
echo "  aws ssm put-parameter --name hezo-generation-agent-endpoint  --value <URL> --type String --overwrite"
echo "  aws ssm put-parameter --name hezo-build-agent-endpoint       --value <URL> --type String --overwrite"
echo "  aws ssm put-parameter --name hezo-validation-agent-endpoint  --value <URL> --type String --overwrite"
echo
echo "  [파이프라인 테스트 실행]"
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn '$STATE_MACHINE_ARN' \\"
echo "    --input '{\"site_id\": \"site_tax_13_001\"}' \\"
echo "    --region $REGION"
