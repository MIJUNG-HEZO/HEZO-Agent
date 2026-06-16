#!/usr/bin/env bash
# =============================================================================
# HEZO Step Functions 상태 머신 배포 스크립트
# 사용법: bash deploy_state_machine.sh [--update]
# =============================================================================

set -euo pipefail

# Git Bash(MSYS/MinGW)에서 /로 시작하는 인자를 Windows 경로로 변환하는 것을 방지
export MSYS_NO_PATHCONV=1

# ─── .env 로드 (HEZO-Agent 레포 루트 기준) ──────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ -f "${REPO_ROOT}/.env" ]; then
    set -a
    source <(sed 's/\r//' "${REPO_ROOT}/.env")
    set +a
    echo "[ENV] .env 로드 완료: ${REPO_ROOT}/.env"
else
    echo "[ENV] .env 없음 — 환경변수 또는 ~/.aws/config 사용"
fi

# ─── 색상 출력 헬퍼 ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ─── 설정값 (.env 값 우선, 없으면 기본값) ──────────────────────────────────
REGION="${AWS_REGION:-ap-northeast-2}"
STATE_MACHINE_NAME="hezo-homepage-pipeline"
DEFINITION_FILE="$(dirname "$0")/hezo_pipeline.json"
ROLE_NAME="hezo-step-functions-role"

# Bedrock Agent 정보 (agents/generation/deploy.sh 실행 후 SSM에 저장된 값 사용)
BEDROCK_AGENT_ID=""
BEDROCK_AGENT_ALIAS_ID=""

# AWS_PROFILE이 설정돼 있으면 모든 aws 명령에 --profile 추가
AWS_PROFILE_OPT=""
if [ -n "${AWS_PROFILE:-}" ]; then
    AWS_PROFILE_OPT="--profile ${AWS_PROFILE}"
    echo "[ENV] AWS 프로파일: ${AWS_PROFILE}"
fi

echo ""
echo "========================================================"
echo "  HEZO Step Functions 상태 머신 배포"
echo "========================================================"
echo ""

# =============================================================================
# 1. 사전 조건 확인
# =============================================================================
info "사전 조건 확인 중..."

if ! command -v aws &>/dev/null; then
    die "AWS CLI가 설치되어 있지 않습니다."
fi

# 계정 ID 조회
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || \
    die "AWS 자격증명이 유효하지 않습니다."
success "AWS 계정 ID: ${ACCOUNT_ID}"

# Step Functions 역할 ARN 조회
ROLE_ARN=$(aws iam get-role \
    --role-name "$ROLE_NAME" \
    --query "Role.Arn" \
    --output text 2>/dev/null) || \
    die "IAM 역할 ${ROLE_NAME}을 찾을 수 없습니다. aws_setup.sh를 먼저 실행하세요."
success "Step Functions IAM 역할 ARN: ${ROLE_ARN}"

# Bedrock Agent ID를 SSM에서 조회 (agents/generation/deploy.sh 실행 후 저장됨)
info "SSM에서 Bedrock Agent 정보 조회 중..."
BEDROCK_AGENT_ID=$(aws ssm get-parameter \
    --name "hezo-bedrock-agent-id" \
    --query "Parameter.Value" \
    --output text \
    --region "$REGION" ${AWS_PROFILE_OPT} 2>/dev/null) || {
    warn "SSM hezo-bedrock-agent-id 를 찾을 수 없습니다."
    warn "agents/generation/deploy.sh 를 먼저 실행하거나 BEDROCK_AGENT_ID 환경변수를 설정하세요."
    BEDROCK_AGENT_ID="${BEDROCK_AGENT_ID:-PLACEHOLDER_AGENT_ID}"
}

BEDROCK_AGENT_ALIAS_ID=$(aws ssm get-parameter \
    --name "hezo-bedrock-agent-alias-id" \
    --query "Parameter.Value" \
    --output text \
    --region "$REGION" ${AWS_PROFILE_OPT} 2>/dev/null) || {
    warn "SSM hezo-bedrock-agent-alias-id 를 찾을 수 없습니다."
    BEDROCK_AGENT_ALIAS_ID="${BEDROCK_AGENT_ALIAS_ID:-PLACEHOLDER_ALIAS_ID}"
}

success "Bedrock Agent ID: ${BEDROCK_AGENT_ID}"
success "Bedrock Agent Alias ID: ${BEDROCK_AGENT_ALIAS_ID}"
echo ""

# =============================================================================
# 2. 상태 머신 정의 파일에 실제 값 주입
# =============================================================================
info "상태 머신 정의 파일 처리 중: ${DEFINITION_FILE}"

DEFINITION_TEMP=$(mktemp /tmp/hezo_pipeline_XXXXXX.json)
trap 'rm -f "$DEFINITION_TEMP"' EXIT

# 플레이스홀더 치환
sed \
    -e "s/\${AWS_ACCOUNT_ID}/${ACCOUNT_ID}/g" \
    -e "s/\${BEDROCK_AGENT_ID}/${BEDROCK_AGENT_ID}/g" \
    -e "s/\${BEDROCK_AGENT_ALIAS_ID}/${BEDROCK_AGENT_ALIAS_ID}/g" \
    "$DEFINITION_FILE" > "$DEFINITION_TEMP"

success "플레이스홀더 치환 완료 → ${DEFINITION_TEMP}"

# JSON 유효성 검증
if python3 -c "import json,sys; json.load(open('$DEFINITION_TEMP'))" 2>/dev/null; then
    success "JSON 구문 검증 통과"
else
    die "상태 머신 정의 JSON 구문 오류. 파일을 확인하세요: ${DEFINITION_TEMP}"
fi
echo ""

# =============================================================================
# 3. CloudWatch 로그 그룹 생성 (Step Functions 실행 로그용)
# =============================================================================
LOG_GROUP="/hezo/step-functions/${STATE_MACHINE_NAME}"
info "CloudWatch 로그 그룹 확인: ${LOG_GROUP}"

if aws logs describe-log-groups \
    --log-group-name-prefix "$LOG_GROUP" \
    --region "$REGION" \
    --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName" \
    --output text | grep -q "$LOG_GROUP" 2>/dev/null; then
    warn "로그 그룹 이미 존재: ${LOG_GROUP}"
else
    aws logs create-log-group \
        --log-group-name "$LOG_GROUP" \
        --region "$REGION"
    aws logs put-retention-policy \
        --log-group-name "$LOG_GROUP" \
        --retention-in-days 90 \
        --region "$REGION"
    success "로그 그룹 생성: ${LOG_GROUP} (보존: 90일)"
fi

LOG_GROUP_ARN="arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${LOG_GROUP}:*"
echo ""

# =============================================================================
# 4. 상태 머신 생성 또는 업데이트
# =============================================================================

# 기존 상태 머신 ARN 조회
EXISTING_ARN=$(aws stepfunctions list-state-machines \
    --region "$REGION" \
    --query "stateMachines[?name=='${STATE_MACHINE_NAME}'].stateMachineArn" \
    --output text 2>/dev/null)

LOGGING_CONFIGURATION=$(cat <<EOF
{
    "level": "ALL",
    "includeExecutionData": true,
    "destinations": [
        {
            "cloudWatchLogsLogGroup": {
                "logGroupArn": "${LOG_GROUP_ARN}"
            }
        }
    ]
}
EOF
)

TRACING_CONFIGURATION='{"enabled": true}'

if [ -n "$EXISTING_ARN" ] && [ "$EXISTING_ARN" != "None" ]; then
    # ── 업데이트 모드 ────────────────────────────────────────────────────────
    info "기존 상태 머신 업데이트: ${EXISTING_ARN}"

    aws stepfunctions update-state-machine \
        --state-machine-arn "$EXISTING_ARN" \
        --definition "file://${DEFINITION_TEMP}" \
        --role-arn "$ROLE_ARN" \
        --logging-configuration "$LOGGING_CONFIGURATION" \
        --tracing-configuration "$TRACING_CONFIGURATION" \
        --region "$REGION" \
        --output json > /tmp/hezo_sfn_update_result.json

    STATE_MACHINE_ARN="$EXISTING_ARN"
    success "상태 머신 업데이트 완료"
else
    # ── 신규 생성 모드 ───────────────────────────────────────────────────────
    info "새 상태 머신 생성: ${STATE_MACHINE_NAME}"

    CREATE_RESULT=$(aws stepfunctions create-state-machine \
        --name "$STATE_MACHINE_NAME" \
        --definition "file://${DEFINITION_TEMP}" \
        --role-arn "$ROLE_ARN" \
        --type "STANDARD" \
        --logging-configuration "$LOGGING_CONFIGURATION" \
        --tracing-configuration "$TRACING_CONFIGURATION" \
        --tags "project=HEZO" "managedBy=deploy_state_machine.sh" \
        --region "$REGION" \
        --output json)

    STATE_MACHINE_ARN=$(echo "$CREATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['stateMachineArn'])")
    success "상태 머신 생성 완료"
fi

echo ""

# =============================================================================
# 5. SSM에 상태 머신 ARN 저장
# =============================================================================
info "SSM에 상태 머신 ARN 저장 중..."

# 플랫 이름 사용 (조직 SCP가 계층형 /hezo/... 경로를 차단함)
aws ssm put-parameter \
    --name "hezo-step-functions-arn" \
    --value "$STATE_MACHINE_ARN" \
    --type "String" \
    --overwrite \
    --region "$REGION" \
    ${AWS_PROFILE_OPT} \
    --output text > /dev/null

success "SSM 저장 완료: hezo-step-functions-arn"
echo ""

# =============================================================================
# 6. 배포 검증 (단순 실행 테스트 - dry run)
# =============================================================================
info "배포 검증 중 (상태 머신 설명 조회)..."

DESCRIBE_RESULT=$(aws stepfunctions describe-state-machine \
    --state-machine-arn "$STATE_MACHINE_ARN" \
    --region "$REGION" \
    --output json)

SM_STATUS=$(echo "$DESCRIBE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
SM_CREATION=$(echo "$DESCRIBE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['creationDate'])")

success "상태 머신 상태: ${SM_STATUS}"
success "생성/최종수정 시각: ${SM_CREATION}"
echo ""

# =============================================================================
# 7. 배포 요약
# =============================================================================
echo "========================================================"
echo "  Step Functions 배포 완료"
echo "========================================================"
echo "  상태 머신 이름  : ${STATE_MACHINE_NAME}"
echo "  상태 머신 ARN   : ${STATE_MACHINE_ARN}"
echo "  리전            : ${REGION}"
echo "  IAM 역할        : ${ROLE_ARN}"
echo "  Bedrock Agent ID: ${BEDROCK_AGENT_ID}"
echo "  로그 그룹       : ${LOG_GROUP}"
echo ""
echo "  테스트 실행 예시:"
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn '${STATE_MACHINE_ARN}' \\"
echo "    --input '{\"site_id\": \"test-site-001\", \"contract_json\": {\"site_id\": \"test-site-001\"}}' \\"
echo "    --region ${REGION}"
echo "========================================================"
