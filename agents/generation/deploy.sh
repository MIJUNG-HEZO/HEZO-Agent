#!/usr/bin/env bash
# =============================================================================
# HEZO P4 — AgentCore Runtime 배포 스크립트 (v2.0)
#
# 배포 대상: 생성·검증·리포트 에이전트 (AgentCore Runtime 커스텀 Python)
# 전제조건:
#   - AWS CLI v2 (aws bedrock-agentcore 커맨드 지원)
#   - Docker
#   - AWS 프로파일: rapa-cm1-21 (hezo-dev-donggyun)
#
# 사용법:
#   bash deploy.sh [--agent generation|validation|report|all]
#   bash deploy.sh --setup-iam     # IAM 역할만 설정
#   bash deploy.sh --setup-ecr     # ECR 리포지터리만 생성
# =============================================================================

set -euo pipefail
MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-rapa-cm1-21}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# 계정 ID는 환경변수 또는 STS에서 동적 조회 (하드코딩 금지)
ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"

ARTIFACTS_BUCKET="hezo-artifacts"
SITE_BUCKET="hezo-sites"
ROLE_NAME="hezo-agentcore-execution-role"
AGENTS=(generation validation report)

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
error()   { echo "[ERROR] $*" >&2; exit 1; }
step()    { echo; echo "── STEP $* ──────────────────────────────────────────"; }

aws_cmd() { aws --profile "$PROFILE" "$@"; }

fix_path() {
    python3 -c "
import sys, re
p = sys.argv[1]
m = re.match(r'^/([a-zA-Z])/(.*)', p)
print(m.group(1).upper() + ':/' + m.group(2) if m else p)
" "$1"
}

# =============================================================================
# STEP 0: 사전 확인
# =============================================================================
preflight() {
    step "0: 사전 확인"
    command -v docker  >/dev/null 2>&1 || error "Docker가 없습니다."
    command -v aws     >/dev/null 2>&1 || error "AWS CLI가 없습니다."
    command -v python3 >/dev/null 2>&1 || error "Python3가 없습니다."

    local identity
    identity=$(aws_cmd sts get-caller-identity --output json 2>/dev/null || echo "ERROR")
    [ "$identity" = "ERROR" ] && error "AWS 인증 실패. 프로파일 '$PROFILE'을 확인하세요."

    if [ -z "$ACCOUNT_ID" ]; then
        ACCOUNT_ID=$(echo "$identity" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
    fi
    local caller
    caller=$(echo "$identity" | python3 -c "import sys,json; print(json.load(sys.stdin)['UserId'])")
    success "AWS 인증: $caller (Account: $ACCOUNT_ID)"

    if ! aws_cmd bedrock-agentcore help >/dev/null 2>&1; then
        warn "aws bedrock-agentcore 명령어 미지원 — 최신 AWS CLI 필요 또는 콘솔 배포"
    fi
}

# =============================================================================
# STEP 1: IAM 역할 설정
# =============================================================================
setup_iam() {
    step "1: IAM 역할 설정 ($ROLE_NAME)"

    local trust_file; trust_file=$(fix_path "$REPO_ROOT/infra/iam/agentcore-execution-trust.json")
    local policy_file; policy_file=$(fix_path "$REPO_ROOT/infra/iam/agentcore-execution-policy.json")

    # ${AWS_ACCOUNT_ID} 플레이스홀더 치환 후 임시 파일 생성
    local tmp_policy; tmp_policy=$(python3 -c "
import sys, re, tempfile, os
content = open(sys.argv[1], encoding='utf-8').read()
content = content.replace('\${AWS_ACCOUNT_ID}', sys.argv[2])
tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
tf.write(content); tf.close(); print(tf.name)
" "$policy_file" "$ACCOUNT_ID")
    local tmp_trust; tmp_trust=$(python3 -c "
import sys, re, tempfile, os
content = open(sys.argv[1], encoding='utf-8').read()
content = content.replace('\${AWS_ACCOUNT_ID}', sys.argv[2])
tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
tf.write(content); tf.close(); print(tf.name)
" "$trust_file" "$ACCOUNT_ID")
    trap "rm -f '$tmp_policy' '$tmp_trust'" EXIT

    if aws_cmd iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
        info "역할 이미 존재: $ROLE_NAME"
    else
        aws_cmd iam create-role \
            --role-name "$ROLE_NAME" \
            --assume-role-policy-document "file://$tmp_trust" \
            --description "HEZO AgentCore Runtime Execution Role" \
            >/dev/null
        success "역할 생성: $ROLE_NAME"
    fi

    aws_cmd iam put-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-name "hezo-agentcore-inline-policy" \
        --policy-document "file://$tmp_policy"
    success "IAM 정책 적용 완료"
}

# =============================================================================
# STEP 2: ECR 리포지터리 생성
# =============================================================================
setup_ecr() {
    step "2: ECR 리포지터리 생성"
    for agent in "${AGENTS[@]}"; do
        local repo="hezo-${agent}-agent"
        if aws_cmd ecr describe-repositories --repository-names "$repo" --region "$REGION" >/dev/null 2>&1; then
            info "이미 존재: $repo"
        else
            aws_cmd ecr create-repository --repository-name "$repo" --region "$REGION" \
                --image-scanning-configuration scanOnPush=true >/dev/null
            success "ECR 리포지터리 생성: $repo"
        fi
    done
}

# =============================================================================
# STEP 3: Docker 빌드 & ECR 푸시
# =============================================================================
build_and_push() {
    local agent="$1"
    step "3: Docker 빌드 & 푸시 ($agent)"

    local repo="hezo-${agent}-agent"
    local ecr_uri="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${repo}"
    local dockerfile; dockerfile=$(fix_path "$REPO_ROOT/agents/${agent}/Dockerfile")
    local context; context=$(fix_path "$REPO_ROOT")

    aws_cmd ecr get-login-password --region "$REGION" | \
        docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

    docker build -f "$dockerfile" -t "${repo}:latest" -t "${ecr_uri}:latest" "$context"
    docker push "${ecr_uri}:latest"
    success "ECR 푸시 완료: $ecr_uri"

    aws_cmd ssm put-parameter --name "hezo-${agent}-agent-image-uri" \
        --value "${ecr_uri}:latest" --type String --overwrite --region "$REGION" >/dev/null
}

# =============================================================================
# STEP 4: AgentCore Runtime 배포
# =============================================================================
deploy_agentcore() {
    local agent="$1"
    step "4: AgentCore Runtime 배포 ($agent)"

    local repo="hezo-${agent}-agent"
    local ecr_uri="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${repo}:latest"
    local role_arn="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
    local model_id; case "$agent" in report) model_id="global.anthropic.claude-haiku-4-5-20251001" ;; *) model_id="global.anthropic.claude-sonnet-4-6" ;; esac

    local env_json
    env_json=$(python3 - "$agent" "$model_id" "$ARTIFACTS_BUCKET" "$SITE_BUCKET" <<'PYENV'
import sys, json
agent, model_id, ab, sb = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = {"ARTIFACTS_BUCKET": ab, "SITE_BUCKET": sb, "MODEL_ID": model_id, "AWS_DEFAULT_REGION": "ap-northeast-2"}
if agent == "report": env["SSM_FLAG_KEY"] = "hezo-report-enabled"
print(json.dumps(env))
PYENV
)

    local existing_id
    existing_id=$(aws_cmd bedrock-agentcore list-agent-runtimes --region "$REGION" \
        --query "agentRuntimes[?agentRuntimeName=='${repo}'].agentRuntimeId | [0]" \
        --output text 2>/dev/null || echo "None")

    if [ -z "$existing_id" ] || [ "$existing_id" = "None" ]; then
        local result
        result=$(aws_cmd bedrock-agentcore create-agent-runtime \
            --agent-runtime-name "$repo" \
            --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ecr_uri}\"}}" \
            --execution-role-arn "$role_arn" \
            --network-configuration "{\"networkMode\":\"PUBLIC\"}" \
            --environment-variables "$env_json" \
            --region "$REGION" --query "agentRuntimeId" --output text 2>/dev/null || echo "")

        if [ -z "$result" ] || [ "$result" = "None" ]; then
            warn "AgentCore Runtime CLI 생성 실패 — 콘솔에서 수동 배포 필요"
            warn "  콘솔: https://console.aws.amazon.com/bedrock/home?region=${REGION}#/agentcore"
            warn "  Name: $repo  |  Image: $ecr_uri  |  Role: $role_arn"
            return 0
        fi
        success "AgentCore Runtime 생성: ID=$result"
        aws_cmd ssm put-parameter --name "hezo-${agent}-agent-id" --value "$result" \
            --type String --overwrite --region "$REGION" >/dev/null
    else
        aws_cmd bedrock-agentcore update-agent-runtime \
            --agent-runtime-id "$existing_id" \
            --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ecr_uri}\"}}" \
            --region "$REGION" >/dev/null 2>/dev/null || warn "업데이트 실패 — 콘솔 확인 필요"
        success "AgentCore Runtime 업데이트: ID=$existing_id"
    fi
}

# =============================================================================
# STEP 5: 인프라 초기화
# =============================================================================
setup_infra() {
    step "5: S3 & SSM 초기화"
    for bucket in "$ARTIFACTS_BUCKET" "$SITE_BUCKET"; do
        if aws_cmd s3 ls "s3://${bucket}" --region "$REGION" >/dev/null 2>&1; then
            info "S3 버킷 이미 존재: $bucket"
        else
            aws_cmd s3 mb "s3://${bucket}" --region "$REGION"
            aws_cmd s3api put-bucket-versioning --bucket "$bucket" \
                --versioning-configuration Status=Enabled --region "$REGION"
            success "S3 버킷 생성: $bucket"
        fi
    done

    for pd in "hezo-report-enabled:false" "hezo-generation-agent-id:pending" \
              "hezo-validation-agent-id:pending" "hezo-report-agent-id:pending"; do
        local k="${pd%%:*}" v="${pd##*:}"
        aws_cmd ssm get-parameter --name "$k" --region "$REGION" >/dev/null 2>&1 || \
            aws_cmd ssm put-parameter --name "$k" --value "$v" --type String \
                --region "$REGION" >/dev/null && info "SSM: $k = $v"
    done
    success "인프라 초기화 완료"
}

# =============================================================================
# 메인
# =============================================================================
TARGET_AGENT="all"; RUN_SETUP_IAM=false; RUN_SETUP_ECR=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent) TARGET_AGENT="$2"; shift 2 ;;
        --setup-iam) RUN_SETUP_IAM=true; shift ;;
        --setup-ecr) RUN_SETUP_ECR=true; shift ;;
        *) warn "알 수 없는 옵션: $1"; shift ;;
    esac
done

echo "╔══════════════════════════════════════════════════════╗"
echo "║  HEZO P4 AgentCore Runtime 배포 v2.0                ║"
echo "╚══════════════════════════════════════════════════════╝"

preflight
$RUN_SETUP_IAM && { setup_iam; exit 0; }
$RUN_SETUP_ECR && { setup_ecr; exit 0; }

setup_infra; setup_iam; setup_ecr

if [ "$TARGET_AGENT" = "all" ]; then
    for agent in "${AGENTS[@]}"; do build_and_push "$agent"; deploy_agentcore "$agent"; done
else
    build_and_push "$TARGET_AGENT"; deploy_agentcore "$TARGET_AGENT"
fi

echo; echo "배포 완료 — AgentCore Runtime ID: SSM hezo-{generation,validation,report}-agent-id"
