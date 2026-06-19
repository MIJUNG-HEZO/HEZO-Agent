#!/usr/bin/env bash
# =============================================================================
# HEZO Step Functions 배포 스크립트 v5.0
#
# 변경사항 (v5.0):
#   - A12 IaC 분리 반영: 콘텐츠 파이프라인(hezo_pipeline.json v5.0)과
#     IaC 파이프라인(hezo_iac_pipeline.json) 분리 배포
#   - 콘텐츠 파이프라인에서 CFN/IaC 관련 SSM 파라미터 제거
#   - IaC 파이프라인: site-published EventBridge Rule로 자동 트리거
#
# 사용법:
#   bash deploy_state_machine.sh                # 콘텐츠 + IaC 양쪽 배포
#   bash deploy_state_machine.sh --content-only # 콘텐츠 파이프라인만 배포
#   bash deploy_state_machine.sh --iac-only     # IaC 파이프라인만 배포
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

CONTENT_SM_NAME="hezo-homepage-pipeline"
IAC_SM_NAME="hezo-iac-pipeline"

CONTENT_DEFINITION_FILE="$(dirname "$0")/hezo_pipeline.json"
IAC_DEFINITION_FILE="$(dirname "$0")/hezo_iac_pipeline.json"

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

# =============================================================================
# CloudWatch 로그 그룹 생성 (없으면)
# =============================================================================
ensure_log_group() {
    local log_group="$1"
    if ! aws_cmd logs describe-log-groups --log-group-name-prefix "$log_group" --region "$REGION" \
            --query "logGroups[?logGroupName=='${log_group}'].logGroupName" \
            --output text 2>/dev/null | grep -q "$log_group"; then
        aws_cmd logs create-log-group --log-group-name "$log_group" --region "$REGION"
        aws_cmd logs put-retention-policy --log-group-name "$log_group" \
            --retention-in-days 90 --region "$REGION"
        success "CloudWatch 로그 그룹 생성: $log_group"
    else
        info "로그 그룹 이미 존재: $log_group"
    fi
}

# =============================================================================
# 상태 머신 생성 또는 업데이트
# =============================================================================
deploy_state_machine() {
    local sm_name="$1"
    local definition_file="$2"
    local role_arn="$3"
    local account_id="$4"

    local temp_file
    temp_file=$(python3 -c "import tempfile; tf=tempfile.NamedTemporaryFile(suffix='.json',delete=False); print(tf.name)")
    trap "rm -f '$temp_file'" RETURN

    python3 - "$definition_file" "$temp_file" \
        "$account_id" \
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

json.loads(content)
open(dst, 'w', encoding='utf-8').write(content)
print("JSON 유효성 검증 통과")
PYEOF

    local log_group="/hezo/step-functions/${sm_name}"
    ensure_log_group "$log_group"
    local log_group_arn="arn:aws:logs:${REGION}:${account_id}:log-group:${log_group}:*"
    local logging_config
    logging_config=$(python3 -c "
import json
print(json.dumps({
  'level': 'ALL',
  'includeExecutionData': True,
  'destinations': [{'cloudWatchLogsLogGroup': {'logGroupArn': '$log_group_arn'}}]
}))")

    local existing_arn
    existing_arn=$(aws_cmd stepfunctions list-state-machines \
        --region "$REGION" \
        --query "stateMachines[?name=='${sm_name}'].stateMachineArn" \
        --output text 2>/dev/null || echo "")

    local sm_arn
    if [ -n "$existing_arn" ] && [ "$existing_arn" != "None" ]; then
        info "기존 상태 머신 업데이트: $existing_arn"
        aws_cmd stepfunctions update-state-machine \
            --state-machine-arn "$existing_arn" \
            --definition "file://${temp_file}" \
            --role-arn "$role_arn" \
            --logging-configuration "$logging_config" \
            --region "$REGION" --output json > /dev/null
        sm_arn="$existing_arn"
        success "상태 머신 업데이트 완료: $sm_name"
    else
        info "새 상태 머신 생성: $sm_name"
        local create_result
        create_result=$(aws_cmd stepfunctions create-state-machine \
            --name "$sm_name" \
            --definition "file://${temp_file}" \
            --role-arn "$role_arn" \
            --type "STANDARD" \
            --logging-configuration "$logging_config" \
            --tags "project=HEZO" \
            --region "$REGION" --output json)
        sm_arn=$(echo "$create_result" | python3 -c "import sys,json; print(json.load(sys.stdin)['stateMachineArn'])")
        success "상태 머신 생성 완료: $sm_name"
    fi

    echo "$sm_arn"
}

# =============================================================================
# EventBridge Rule 생성 — site-published → IaC 파이프라인
# =============================================================================
setup_iac_event_rule() {
    local iac_sm_arn="$1"
    local rule_name="hezo-site-published-iac"

    local rule_arn
    rule_arn=$(aws_cmd events put-rule \
        --name "$rule_name" \
        --event-pattern '{"source":["hezo.pipeline"],"detail-type":["site-published"]}' \
        --state "ENABLED" \
        --description "site-published 이벤트 → IaC 파이프라인 트리거" \
        --region "$REGION" \
        --query "RuleArn" --output text)
    success "EventBridge Rule: $rule_arn"

    local sfn_role_arn
    sfn_role_arn=$(aws_cmd iam get-role --role-name "$ROLE_NAME" \
        --query "Role.Arn" --output text)

    aws_cmd events put-targets \
        --rule "$rule_name" \
        --targets "Id=hezo-iac-pipeline,Arn=${iac_sm_arn},RoleArn=${sfn_role_arn}" \
        --region "$REGION" > /dev/null
    success "EventBridge Target → $IAC_SM_NAME"
}

# =============================================================================
# 플래그 파싱
# =============================================================================
DEPLOY_CONTENT=true
DEPLOY_IAC=true

case "${1:-}" in
    --setup-connection)
        setup_connection
        exit 0
        ;;
    --content-only)
        DEPLOY_IAC=false
        ;;
    --iac-only)
        DEPLOY_CONTENT=false
        ;;
esac

# =============================================================================
# 1. 사전 조건 확인
# =============================================================================
echo "╔══════════════════════════════════════════════════════╗"
echo "║  HEZO Step Functions 배포 v5.0                      ║"
echo "╚══════════════════════════════════════════════════════╝"

command -v aws >/dev/null 2>&1 || error "AWS CLI 미설치"

ACCOUNT_ID=$(aws_cmd sts get-caller-identity --query Account --output text) || error "AWS 인증 실패"
success "AWS 계정: $ACCOUNT_ID"

ROLE_ARN=$(aws_cmd iam get-role --role-name "$ROLE_NAME" \
    --query "Role.Arn" --output text 2>/dev/null) || \
    error "IAM 역할 ${ROLE_NAME} 없음 — IAM 설정 먼저 필요"
success "Step Functions IAM 역할: $ROLE_ARN"

# =============================================================================
# 2. SSM 파라미터 조회
# =============================================================================
info "SSM에서 파라미터 조회 중..."

GENERATION_AGENT_ENDPOINT=$(ssm_get "hezo-generation-agent-endpoint" "PLACEHOLDER_GENERATION_ENDPOINT")
VALIDATION_AGENT_ENDPOINT=$(ssm_get "hezo-validation-agent-endpoint" "PLACEHOLDER_VALIDATION_ENDPOINT")
BUILD_AGENT_ENDPOINT=$(ssm_get "hezo-build-agent-endpoint" "PLACEHOLDER_BUILD_ENDPOINT")
EVENTBRIDGE_CONNECTION_ARN=$(ssm_get "hezo-eventbridge-connection-arn" "PLACEHOLDER_CONNECTION_ARN")

CFN_TEMPLATE_URL=$(ssm_get "hezo-cfn-template-url" "PLACEHOLDER_CFN_TEMPLATE_URL")
HEZO_HOSTED_ZONE_ID=$(ssm_get "hezo-hosted-zone-id" "PLACEHOLDER_HOSTED_ZONE_ID")
WILDCARD_CERT_ARN=$(ssm_get "hezo-wildcard-cert-arn" "PLACEHOLDER_WILDCARD_CERT_ARN")
REPORT_STATE_MACHINE_ARN=$(ssm_get "hezo-report-state-machine-arn" "PLACEHOLDER_REPORT_SM_ARN")
SCHEDULER_ROLE_ARN=$(ssm_get "hezo-scheduler-role-arn" "PLACEHOLDER_SCHEDULER_ROLE_ARN")
HEZO_SITES_BUCKET_DOMAIN=$(ssm_get "hezo-sites-bucket-domain" "hezo-sites.s3.ap-northeast-2.amazonaws.com")
CUSTOMER_BACKEND_ECR_IMAGE=$(ssm_get "hezo-customer-backend-ecr-image" "PLACEHOLDER_CUSTOMER_BACKEND_ECR_IMAGE")

[[ "$GENERATION_AGENT_ENDPOINT" == PLACEHOLDER* ]] && warn "hezo-generation-agent-endpoint SSM 없음" || success "Generation Agent: $GENERATION_AGENT_ENDPOINT"
[[ "$VALIDATION_AGENT_ENDPOINT" == PLACEHOLDER* ]] && warn "hezo-validation-agent-endpoint SSM 없음" || success "Validation Agent: $VALIDATION_AGENT_ENDPOINT"
[[ "$BUILD_AGENT_ENDPOINT" == PLACEHOLDER* ]]       && warn "hezo-build-agent-endpoint SSM 없음" || success "Build Agent: $BUILD_AGENT_ENDPOINT"
[[ "$EVENTBRIDGE_CONNECTION_ARN" == PLACEHOLDER* ]] && warn "hezo-eventbridge-connection-arn SSM 없음 — --setup-connection 실행" || success "EventBridge Connection: $EVENTBRIDGE_CONNECTION_ARN"
[[ "$CFN_TEMPLATE_URL" == PLACEHOLDER* ]]           && warn "hezo-cfn-template-url SSM 없음 (IaC용)" || success "CFn Template: $CFN_TEMPLATE_URL"
[[ "$HEZO_HOSTED_ZONE_ID" == PLACEHOLDER* ]]        && warn "hezo-hosted-zone-id SSM 없음 (IaC용)" || success "Hosted Zone ID: $HEZO_HOSTED_ZONE_ID"
[[ "$WILDCARD_CERT_ARN" == PLACEHOLDER* ]]          && warn "hezo-wildcard-cert-arn SSM 없음 (IaC용)" || success "Wildcard Cert: $WILDCARD_CERT_ARN"
[[ "$SCHEDULER_ROLE_ARN" == PLACEHOLDER* ]]         && warn "hezo-scheduler-role-arn SSM 없음 (IaC용)" || success "Scheduler Role: $SCHEDULER_ROLE_ARN"
success "Sites Bucket Domain: $HEZO_SITES_BUCKET_DOMAIN"
[[ "$CUSTOMER_BACKEND_ECR_IMAGE" == PLACEHOLDER* ]] && warn "hezo-customer-backend-ecr-image SSM 없음 (IaC용)" || success "Customer Backend ECR: $CUSTOMER_BACKEND_ECR_IMAGE"

# =============================================================================
# 3. 콘텐츠 파이프라인 배포
# =============================================================================
CONTENT_SM_ARN=""
if [ "$DEPLOY_CONTENT" = true ]; then
    echo
    info "--- 콘텐츠 파이프라인 배포 ($CONTENT_SM_NAME) ---"
    CONTENT_SM_ARN=$(deploy_state_machine "$CONTENT_SM_NAME" "$CONTENT_DEFINITION_FILE" "$ROLE_ARN" "$ACCOUNT_ID")

    aws_cmd ssm put-parameter \
        --name "hezo-step-functions-arn" \
        --value "$CONTENT_SM_ARN" \
        --type String --overwrite \
        --region "$REGION" > /dev/null
    success "SSM 저장: hezo-step-functions-arn"
fi

# =============================================================================
# 4. IaC 파이프라인 배포 + EventBridge Rule 연결
# =============================================================================
IAC_SM_ARN=""
if [ "$DEPLOY_IAC" = true ]; then
    echo
    info "--- IaC 파이프라인 배포 ($IAC_SM_NAME) ---"
    IAC_SM_ARN=$(deploy_state_machine "$IAC_SM_NAME" "$IAC_DEFINITION_FILE" "$ROLE_ARN" "$ACCOUNT_ID")

    aws_cmd ssm put-parameter \
        --name "hezo-iac-step-functions-arn" \
        --value "$IAC_SM_ARN" \
        --type String --overwrite \
        --region "$REGION" > /dev/null
    success "SSM 저장: hezo-iac-step-functions-arn"

    setup_iac_event_rule "$IAC_SM_ARN"
fi

# =============================================================================
# 5. 배포 요약
# =============================================================================
echo
echo "╔══════════════════════════════════════════════════════╗"
echo "║  배포 완료                                           ║"
echo "╚══════════════════════════════════════════════════════╝"
[ -n "$CONTENT_SM_ARN" ] && echo "  콘텐츠 파이프라인 ARN : $CONTENT_SM_ARN"
[ -n "$IAC_SM_ARN" ]     && echo "  IaC 파이프라인 ARN    : $IAC_SM_ARN"
echo
if [ -n "$CONTENT_SM_ARN" ]; then
echo "  [파이프라인 테스트 실행]"
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn '$CONTENT_SM_ARN' \\"
echo "    --input '{\"site_id\": \"site_tax_13_001\", \"template_type\": \"tax-accounting\", \"template_category\": \"landing\"}' \\"
echo "    --region $REGION"
fi
echo
echo "  [IaC 파이프라인 사전 등록 순서 (M8)]"
echo "  1. aws s3 mb s3://hezo-cfn-templates --region ap-northeast-2"
echo "  2. aws s3 cp infra/cloudformation/customer-infra.yaml s3://hezo-cfn-templates/"
echo "  3. aws ssm put-parameter --name hezo-cfn-template-url     --value https://hezo-cfn-templates.s3.ap-northeast-2.amazonaws.com/customer-infra.yaml --type String --overwrite"
echo "  4. aws ssm put-parameter --name hezo-hosted-zone-id       --value <ROUTE53_ZONE_ID> --type String --overwrite"
echo "  5. aws ssm put-parameter --name hezo-wildcard-cert-arn    --value <ACM_ARN_US_EAST_1> --type String --overwrite"
echo "  6. aws ssm put-parameter --name hezo-scheduler-role-arn   --value <SCHEDULER_ROLE_ARN> --type String --overwrite"
echo "  7. bash deploy_state_machine.sh --iac-only"
