#!/usr/bin/env bash
# =============================================================================
# HEZO Step Functions 상태 머신 배포 스크립트 v4.0
#
# 변경사항 (v4.0):
#   - hezo_pipeline.json v4.0 반영: 고객사 CloudFormation IAC 스텝 추가
#     CreateCustomerStack → UpdateCustomerStack → GetStackOutputs →
#     StoreCustomerDomain → CloudFrontInvalidation → RegisterReportSchedule
#   - 신규 SSM 파라미터: CFN_TEMPLATE_URL, HEZO_HOSTED_ZONE_ID,
#                         WILDCARD_CERT_ARN, REPORT_STATE_MACHINE_ARN, SCHEDULER_ROLE_ARN
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
echo "║  HEZO Step Functions 배포 v4.0                      ║"
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
VALIDATION_AGENT_ENDPOINT=$(ssm_get "hezo-validation-agent-endpoint" "PLACEHOLDER_VALIDATION_ENDPOINT")
BUILD_AGENT_ENDPOINT=$(ssm_get "hezo-build-agent-endpoint" "PLACEHOLDER_BUILD_ENDPOINT")
EVENTBRIDGE_CONNECTION_ARN=$(ssm_get "hezo-eventbridge-connection-arn" "PLACEHOLDER_CONNECTION_ARN")

# 고객사 CloudFormation IAC 관련 파라미터
CFN_TEMPLATE_URL=$(ssm_get "hezo-cfn-template-url" "PLACEHOLDER_CFN_TEMPLATE_URL")
HEZO_HOSTED_ZONE_ID=$(ssm_get "hezo-hosted-zone-id" "PLACEHOLDER_HOSTED_ZONE_ID")
WILDCARD_CERT_ARN=$(ssm_get "hezo-wildcard-cert-arn" "PLACEHOLDER_WILDCARD_CERT_ARN")
REPORT_STATE_MACHINE_ARN=$(ssm_get "hezo-report-state-machine-arn" "PLACEHOLDER_REPORT_SM_ARN")
SCHEDULER_ROLE_ARN=$(ssm_get "hezo-scheduler-role-arn" "PLACEHOLDER_SCHEDULER_ROLE_ARN")

# hezo-customer-backend 연동 파라미터
HEZO_SITES_BUCKET_DOMAIN=$(ssm_get "hezo-sites-bucket-domain" "hezo-sites.s3.ap-northeast-2.amazonaws.com")
CUSTOMER_BACKEND_ECR_IMAGE=$(ssm_get "hezo-customer-backend-ecr-image" "PLACEHOLDER_CUSTOMER_BACKEND_ECR_IMAGE")

if [[ "$GENERATION_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-generation-agent-endpoint SSM 없음 — placeholder 사용"
    warn "  등록: aws ssm put-parameter --name hezo-generation-agent-endpoint --value <URL> --type String --overwrite"
else
    success "Generation Agent: $GENERATION_AGENT_ENDPOINT"
fi

if [[ "$VALIDATION_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-validation-agent-endpoint SSM 없음 — placeholder 사용"
    warn "  등록: aws ssm put-parameter --name hezo-validation-agent-endpoint --value <URL> --type String --overwrite"
else
    success "Validation Agent: $VALIDATION_AGENT_ENDPOINT"
fi

if [[ "$BUILD_AGENT_ENDPOINT" == PLACEHOLDER* ]]; then
    warn "hezo-build-agent-endpoint SSM 없음 — placeholder 사용"
    warn "  등록: aws ssm put-parameter --name hezo-build-agent-endpoint --value <URL> --type String --overwrite"
else
    success "Build Agent: $BUILD_AGENT_ENDPOINT"
fi

if [[ "$EVENTBRIDGE_CONNECTION_ARN" == PLACEHOLDER* ]]; then
    warn "hezo-eventbridge-connection-arn SSM 없음"
    warn "  실행: bash deploy_state_machine.sh --setup-connection"
else
    success "EventBridge Connection: $EVENTBRIDGE_CONNECTION_ARN"
fi

if [[ "$CFN_TEMPLATE_URL" == PLACEHOLDER* ]]; then
    warn "hezo-cfn-template-url SSM 없음 (고객사 CloudFormation 템플릿 S3 URL)"
    warn "  등록: aws s3 cp infra/cloudformation/customer-infra.yaml s3://hezo-cfn-templates/"
    warn "        aws ssm put-parameter --name hezo-cfn-template-url --value https://hezo-cfn-templates.s3.ap-northeast-2.amazonaws.com/customer-infra.yaml --type String --overwrite"
else
    success "CFn Template: $CFN_TEMPLATE_URL"
fi

if [[ "$HEZO_HOSTED_ZONE_ID" == PLACEHOLDER* ]]; then
    warn "hezo-hosted-zone-id SSM 없음 (Route 53 Hosted Zone ID)"
    warn "  등록: aws ssm put-parameter --name hezo-hosted-zone-id --value <ZONE_ID> --type String --overwrite"
else
    success "Hosted Zone ID: $HEZO_HOSTED_ZONE_ID"
fi

if [[ "$WILDCARD_CERT_ARN" == PLACEHOLDER* ]]; then
    warn "hezo-wildcard-cert-arn SSM 없음 (기본 ACM 인증서 ARN — us-east-1, 단 실행 입력 $.cert_arn 우선)"
    warn "  등록: aws ssm put-parameter --name hezo-wildcard-cert-arn --value <CERT_ARN> --type String --overwrite"
else
    success "Wildcard Cert: $WILDCARD_CERT_ARN"
fi

if [[ "$SCHEDULER_ROLE_ARN" == PLACEHOLDER* ]]; then
    warn "hezo-scheduler-role-arn SSM 없음 (EventBridge Scheduler 실행 역할)"
    warn "  등록: aws ssm put-parameter --name hezo-scheduler-role-arn --value <ROLE_ARN> --type String --overwrite"
else
    success "Scheduler Role: $SCHEDULER_ROLE_ARN"
fi

success "Sites Bucket Domain: $HEZO_SITES_BUCKET_DOMAIN"

if [[ "$CUSTOMER_BACKEND_ECR_IMAGE" == PLACEHOLDER* ]]; then
    warn "hezo-customer-backend-ecr-image SSM 없음 (hezo-customer-backend ECR 이미지 URI)"
    warn "  등록: aws ssm put-parameter --name hezo-customer-backend-ecr-image --value 492554570964.dkr.ecr.ap-northeast-2.amazonaws.com/hezo-customer-backend:latest --type String --overwrite"
else
    success "Customer Backend ECR: $CUSTOMER_BACKEND_ECR_IMAGE"
fi

# =============================================================================
# 3. 상태 머신 정의 파일 플레이스홀더 치환
# =============================================================================
info "플레이스홀더 치환 중..."

DEFINITION_TEMP=$(python3 -c "import tempfile; tf=tempfile.NamedTemporaryFile(suffix='.json',delete=False); print(tf.name)")
trap "rm -f '$DEFINITION_TEMP'" EXIT

python3 - "$DEFINITION_FILE" "$DEFINITION_TEMP" \
    "$ACCOUNT_ID" \
    "$GENERATION_AGENT_ENDPOINT" \
    "$VALIDATION_AGENT_ENDPOINT" \
    "$BUILD_AGENT_ENDPOINT" \
    "$EVENTBRIDGE_CONNECTION_ARN" \
    "$CFN_TEMPLATE_URL" \
    "$HEZO_HOSTED_ZONE_ID" \
    "$WILDCARD_CERT_ARN" \
    "$REPORT_STATE_MACHINE_ARN" \
    "$SCHEDULER_ROLE_ARN" \
    "$HEZO_SITES_BUCKET_DOMAIN" \
    "$CUSTOMER_BACKEND_ECR_IMAGE" <<'PYEOF'
import sys, json

(src, dst, account, gen_ep, val_ep, build_ep, conn_arn,
 cfn_url, hosted_zone_id, wildcard_cert, report_sm_arn, scheduler_role,
 sites_bucket_domain, customer_backend_ecr) = sys.argv[1:]

content = open(src, encoding='utf-8').read()
content = content.replace('${AWS_ACCOUNT_ID}',                account)
content = content.replace('${GENERATION_AGENT_ENDPOINT}',     gen_ep)
content = content.replace('${VALIDATION_AGENT_ENDPOINT}',     val_ep)
content = content.replace('${BUILD_AGENT_ENDPOINT}',          build_ep)
content = content.replace('${EVENTBRIDGE_CONNECTION_ARN}',    conn_arn)
content = content.replace('${CFN_TEMPLATE_URL}',              cfn_url)
content = content.replace('${HEZO_HOSTED_ZONE_ID}',           hosted_zone_id)
content = content.replace('${WILDCARD_CERT_ARN}',             wildcard_cert)
content = content.replace('${REPORT_STATE_MACHINE_ARN}',      report_sm_arn)
content = content.replace('${SCHEDULER_ROLE_ARN}',            scheduler_role)
content = content.replace('${HEZO_SITES_BUCKET_DOMAIN}',      sites_bucket_domain)
content = content.replace('${CUSTOMER_BACKEND_ECR_IMAGE}',    customer_backend_ecr)

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
echo "  상태 머신 ARN      : $STATE_MACHINE_ARN"
echo "  Generation Agent   : $GENERATION_AGENT_ENDPOINT"
echo "  Validation Agent   : $VALIDATION_AGENT_ENDPOINT"
echo "  Build Agent        : $BUILD_AGENT_ENDPOINT"
echo "  EventBridge Conn   : $EVENTBRIDGE_CONNECTION_ARN"
echo "  CFn Template URL   : $CFN_TEMPLATE_URL"
echo "  Hosted Zone ID     : $HEZO_HOSTED_ZONE_ID"
echo "  Wildcard Cert ARN  : $WILDCARD_CERT_ARN"
echo "  Scheduler Role ARN : $SCHEDULER_ROLE_ARN"
echo "  Sites Bucket Domain: $HEZO_SITES_BUCKET_DOMAIN"
echo "  Customer Backend   : $CUSTOMER_BACKEND_ECR_IMAGE"
echo
echo "  [M8 고객사 CloudFormation 사전 등록 순서]"
echo "  1. hezo-sites 버킷 정책 1회 업데이트 (계정 내 모든 CF 배포가 접근 가능하도록):"
echo "     infra/cloudformation/setup-sites-bucket-policy.sh 실행"
echo "  2. 템플릿 버킷 + 업로드:"
echo "     aws s3 mb s3://hezo-cfn-templates --region ap-northeast-2"
echo "     aws s3 cp infra/cloudformation/customer-infra.yaml s3://hezo-cfn-templates/"
echo "  3. SSM 등록:"
echo "     aws ssm put-parameter --name hezo-cfn-template-url     --value https://hezo-cfn-templates.s3.ap-northeast-2.amazonaws.com/customer-infra.yaml --type String --overwrite"
echo "     aws ssm put-parameter --name hezo-hosted-zone-id       --value <ROUTE53_ZONE_ID> --type String --overwrite"
echo "     aws ssm put-parameter --name hezo-wildcard-cert-arn    --value <ACM_ARN_US_EAST_1> --type String --overwrite"
echo "     aws ssm put-parameter --name hezo-scheduler-role-arn   --value <SCHEDULER_ROLE_ARN> --type String --overwrite"
echo
echo "  [파이프라인 테스트 실행]"
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn '$STATE_MACHINE_ARN' \\"
echo "    --input '{\"site_id\": \"site_tax_13_001\", \"template_type\": \"tax-accounting\", \"template_category\": \"landing\"}' \\"
echo "    --region $REGION"
echo
echo "  [백엔드 publish 엔드포인트 수정 필요]"
echo "  POST /sites/{id}/publish 의 Step Functions input에 template_type, template_category 추가 필요"
echo "  (현재: {site_id} 만 전달 → CreateCustomerStack 에서 \$.template_type 참조 실패)"
