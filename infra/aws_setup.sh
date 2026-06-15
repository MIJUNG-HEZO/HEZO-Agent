#!/usr/bin/env bash
# =============================================================================
# HEZO AWS 인프라 초기 설정 스크립트
# 실행 전 AWS CLI가 설치되어 있고 적절한 자격증명이 설정되어 있어야 합니다.
# 사용법: bash aws_setup.sh
# =============================================================================

set -euo pipefail

# ─── 색상 출력 헬퍼 ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ─── 설정값 ─────────────────────────────────────────────────────────────────
REGION="ap-northeast-2"
ARTIFACTS_BUCKET="hezo-artifacts"
SITES_BUCKET="hezo-sites"
IAM_DIR="$(dirname "$0")/iam"

# Bedrock 모델 ID (ap-northeast-2 지원 여부 확인용)
SONNET_MODEL="anthropic.claude-sonnet-4-5-20251001"
HAIKU_MODEL="anthropic.claude-haiku-4-5-20251001"

# IAM 역할명
LAMBDA_ROLE="hezo-lambda-execution-role"
STEP_FUNCTIONS_ROLE="hezo-step-functions-role"
BEDROCK_AGENT_ROLE="hezo-bedrock-agent-role"

# 리소스 추적 (요약 출력용)
CREATED_RESOURCES=()

echo ""
echo "========================================================"
echo "  HEZO AWS 인프라 초기 설정 (리전: ${REGION})"
echo "========================================================"
echo ""

# =============================================================================
# 1. AWS CLI 및 자격증명 확인
# =============================================================================
info "AWS CLI 버전 및 자격증명 확인 중..."

if ! command -v aws &>/dev/null; then
    die "AWS CLI가 설치되어 있지 않습니다. https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html 참조"
fi

AWS_VERSION=$(aws --version 2>&1 | head -1)
success "AWS CLI: ${AWS_VERSION}"

# 자격증명 확인
CALLER_IDENTITY=$(aws sts get-caller-identity --output json 2>/dev/null) || \
    die "AWS 자격증명이 유효하지 않습니다. 'aws configure' 또는 환경변수(AWS_ACCESS_KEY_ID 등)를 설정하세요."

ACCOUNT_ID=$(echo "$CALLER_IDENTITY" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
CALLER_ARN=$(echo "$CALLER_IDENTITY" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'])")

success "계정 ID: ${ACCOUNT_ID}"
success "호출자 ARN: ${CALLER_ARN}"
echo ""

# =============================================================================
# 2. Bedrock 모델 액세스 확인 (ap-northeast-2)
# =============================================================================
info "Bedrock 모델 액세스 확인 중 (리전: ${REGION})..."

check_bedrock_model() {
    local model_id="$1"
    local result
    result=$(aws bedrock get-foundation-model \
        --model-identifier "$model_id" \
        --region "$REGION" \
        --output json 2>/dev/null) || {
        warn "모델 ${model_id} 접근 불가 또는 미활성화. AWS 콘솔 → Bedrock → Model access 에서 활성화하세요."
        return 1
    }
    local status
    status=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('modelDetails',{}).get('modelLifecycle',{}).get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    success "모델 ${model_id} - 상태: ${status}"
}

check_bedrock_model "$SONNET_MODEL" || true
check_bedrock_model "$HAIKU_MODEL"  || true
echo ""

# =============================================================================
# 3. S3 버킷 생성
# =============================================================================
create_s3_bucket() {
    local bucket_name="$1"
    local description="$2"

    info "S3 버킷 생성: ${bucket_name} (${description})"

    # 버킷 존재 여부 확인
    if aws s3api head-bucket --bucket "$bucket_name" --region "$REGION" 2>/dev/null; then
        warn "버킷 ${bucket_name} 이미 존재합니다. 설정만 업데이트합니다."
    else
        aws s3api create-bucket \
            --bucket "$bucket_name" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION" \
            --output text > /dev/null
        success "버킷 생성 완료: ${bucket_name}"
        CREATED_RESOURCES+=("S3 Bucket: ${bucket_name}")
    fi

    # 퍼블릭 액세스 차단
    aws s3api put-public-access-block \
        --bucket "$bucket_name" \
        --public-access-block-configuration \
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
        --region "$REGION"
    success "  퍼블릭 액세스 차단 설정 완료"

    # 버전 관리 활성화
    aws s3api put-bucket-versioning \
        --bucket "$bucket_name" \
        --versioning-configuration Status=Enabled \
        --region "$REGION"
    success "  버전 관리 활성화 완료"

    # 서버 측 암호화 (AES-256) 기본 설정
    aws s3api put-bucket-encryption \
        --bucket "$bucket_name" \
        --server-side-encryption-configuration '{
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                },
                "BucketKeyEnabled": true
            }]
        }' \
        --region "$REGION"
    success "  서버 측 암호화(AES-256) 설정 완료"
}

create_s3_bucket "$ARTIFACTS_BUCKET" "계약 JSON 및 렌더 스펙 저장소"
echo ""
create_s3_bucket "$SITES_BUCKET" "생성된 정적 사이트 파일 저장소"
echo ""

# =============================================================================
# 4. IAM 역할 생성
# =============================================================================

# IAM 역할 존재 여부 확인 후 생성 또는 업데이트
create_or_update_role() {
    local role_name="$1"
    local trust_policy_file="$2"
    local description="$3"

    info "IAM 역할 처리: ${role_name}"

    if aws iam get-role --role-name "$role_name" --output text > /dev/null 2>&1; then
        warn "  역할 ${role_name} 이미 존재합니다. 신뢰 정책만 업데이트합니다."
        aws iam update-assume-role-policy \
            --role-name "$role_name" \
            --policy-document "file://${trust_policy_file}" > /dev/null
        success "  신뢰 정책 업데이트 완료"
    else
        aws iam create-role \
            --role-name "$role_name" \
            --assume-role-policy-document "file://${trust_policy_file}" \
            --description "$description" \
            --tags "Key=Project,Value=HEZO" "Key=ManagedBy,Value=aws_setup.sh" \
            --output text > /dev/null
        success "  역할 생성 완료: ${role_name}"
        CREATED_RESOURCES+=("IAM Role: ${role_name}")
    fi
}

# ─── 4-1. Lambda 실행 역할 ─────────────────────────────────────────────────
create_or_update_role \
    "$LAMBDA_ROLE" \
    "${IAM_DIR}/lambda-trust.json" \
    "HEZO Lambda 함수 실행 역할"

info "  Lambda 역할 인라인 정책 연결 중..."
aws iam put-role-policy \
    --role-name "$LAMBDA_ROLE" \
    --policy-name "hezo-lambda-inline-policy" \
    --policy-document "file://${IAM_DIR}/lambda-policy.json"
success "  Lambda 인라인 정책 연결 완료"

# AWSLambdaBasicExecutionRole 관리형 정책 연결 (CloudWatch Logs 기본)
aws iam attach-role-policy \
    --role-name "$LAMBDA_ROLE" \
    --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole" \
    2>/dev/null || warn "  AWSLambdaBasicExecutionRole 이미 연결되어 있습니다."
echo ""

# ─── 4-2. Step Functions 역할 ──────────────────────────────────────────────
create_or_update_role \
    "$STEP_FUNCTIONS_ROLE" \
    "${IAM_DIR}/step-functions-trust.json" \
    "HEZO Step Functions 파이프라인 실행 역할"

info "  Step Functions 역할 인라인 정책 연결 중..."
aws iam put-role-policy \
    --role-name "$STEP_FUNCTIONS_ROLE" \
    --policy-name "hezo-step-functions-inline-policy" \
    --policy-document "file://${IAM_DIR}/step-functions-policy.json"
success "  Step Functions 인라인 정책 연결 완료"
echo ""

# ─── 4-3. Bedrock Agent 역할 ──────────────────────────────────────────────
create_or_update_role \
    "$BEDROCK_AGENT_ROLE" \
    "${IAM_DIR}/bedrock-agent-trust.json" \
    "HEZO Bedrock Agent 실행 역할"

info "  Bedrock Agent 역할 인라인 정책 연결 중..."
aws iam put-role-policy \
    --role-name "$BEDROCK_AGENT_ROLE" \
    --policy-name "hezo-bedrock-agent-inline-policy" \
    --policy-document "file://${IAM_DIR}/bedrock-agent-policy.json"
success "  Bedrock Agent 인라인 정책 연결 완료"
echo ""

# IAM 전파 대기 (역할 생성 후 즉시 사용 시 오류 방지)
info "IAM 역할 전파 대기 (10초)..."
sleep 10
success "IAM 역할 전파 완료"
echo ""

# =============================================================================
# 5. SSM Parameter Store 파라미터 생성
# =============================================================================
info "SSM Parameter Store 파라미터 생성 중..."

put_ssm_param() {
    local name="$1"
    local value="$2"
    local description="$3"

    aws ssm put-parameter \
        --name "$name" \
        --value "$value" \
        --type "String" \
        --description "$description" \
        --overwrite \
        --region "$REGION" \
        --tags "Key=Project,Value=HEZO" \
        --output text > /dev/null
    success "  SSM 파라미터 설정: ${name} = ${value}"
    CREATED_RESOURCES+=("SSM Parameter: ${name}")
}

put_ssm_param "/hezo/artifacts-bucket" "$ARTIFACTS_BUCKET"  "HEZO 아티팩트 S3 버킷명"
put_ssm_param "/hezo/sites-bucket"     "$SITES_BUCKET"      "HEZO 사이트 S3 버킷명"
put_ssm_param "/hezo/region"           "$REGION"            "HEZO 서비스 AWS 리전"
put_ssm_param "/hezo/account-id"       "$ACCOUNT_ID"        "AWS 계정 ID"
echo ""

# =============================================================================
# 6. DynamoDB 테이블 생성 (파이프라인 상태 추적용)
# =============================================================================
info "DynamoDB 테이블 생성: hezo_pipeline_state"

if aws dynamodb describe-table --table-name "hezo_pipeline_state" --region "$REGION" > /dev/null 2>&1; then
    warn "DynamoDB 테이블 hezo_pipeline_state 이미 존재합니다."
else
    aws dynamodb create-table \
        --table-name "hezo_pipeline_state" \
        --attribute-definitions "AttributeName=site_id,AttributeType=S" \
        --key-schema "AttributeName=site_id,KeyType=HASH" \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" \
        --tags "Key=Project,Value=HEZO" \
        --output text > /dev/null
    success "DynamoDB 테이블 생성 완료: hezo_pipeline_state"
    CREATED_RESOURCES+=("DynamoDB Table: hezo_pipeline_state")

    # 테이블 활성화 대기
    info "  테이블 활성화 대기 중..."
    aws dynamodb wait table-exists --table-name "hezo_pipeline_state" --region "$REGION"
    success "  테이블 활성화 완료"
fi
echo ""

# =============================================================================
# 7. CloudWatch 로그 그룹 생성
# =============================================================================
info "CloudWatch 로그 그룹 생성 중..."

create_log_group() {
    local log_group="$1"
    local retention_days="$2"

    if aws logs describe-log-groups \
        --log-group-name-prefix "$log_group" \
        --region "$REGION" \
        --query "logGroups[?logGroupName=='${log_group}']" \
        --output text | grep -q "$log_group" 2>/dev/null; then
        warn "  로그 그룹 이미 존재: ${log_group}"
    else
        aws logs create-log-group \
            --log-group-name "$log_group" \
            --region "$REGION" \
            --tags "Project=HEZO"
        aws logs put-retention-policy \
            --log-group-name "$log_group" \
            --retention-in-days "$retention_days" \
            --region "$REGION"
        success "  로그 그룹 생성: ${log_group} (보존: ${retention_days}일)"
        CREATED_RESOURCES+=("CloudWatch Log Group: ${log_group}")
    fi
}

create_log_group "/hezo/lambda/contract-loader"      30
create_log_group "/hezo/lambda/render-spec-saver"    30
create_log_group "/hezo/lambda/contract-uploader"    30
create_log_group "/hezo/lambda/pipeline-notifier"    30
create_log_group "/hezo/step-functions/pipeline"     90
echo ""

# =============================================================================
# 8. 생성 리소스 요약 출력
# =============================================================================
echo "========================================================"
echo "  생성/업데이트 리소스 요약"
echo "========================================================"
if [ ${#CREATED_RESOURCES[@]} -eq 0 ]; then
    warn "새로 생성된 리소스가 없습니다 (모두 이미 존재)."
else
    for resource in "${CREATED_RESOURCES[@]}"; do
        success "  - ${resource}"
    done
fi
echo ""
echo "  AWS 계정 ID  : ${ACCOUNT_ID}"
echo "  리전         : ${REGION}"
echo "  아티팩트 버킷: s3://${ARTIFACTS_BUCKET}"
echo "  사이트 버킷  : s3://${SITES_BUCKET}"
echo ""
echo "  다음 단계:"
echo "  1. agents/generation/deploy.sh 실행 → Lambda 함수 배포"
echo "  2. infra/step_functions/deploy_state_machine.sh 실행 → Step Functions 생성"
echo "  3. AWS 콘솔 → Bedrock → Model access 에서 모델 활성화 확인"
echo "========================================================"
