#!/usr/bin/env bash
# =============================================================================
# HEZO P4/P3 — AgentCore Runtime 배포 스크립트 (v3.0)
#
# 변경사항 (v3.0):
#   - bedrock-agentcore → bedrock-agentcore-control (컨트롤 플레인 CLI 수정)
#   - P3 빌드 에이전트 추가 (generation|build|validation|report|all)
#   - deploy_agentcore: 런타임 생성 후 엔드포인트 생성 + READY 대기
#   - 엔드포인트 ARN → SSM hezo-{agent}-agent-endpoint-arn 저장
#
# 사용법:
#   bash deploy.sh --agent generation        # generation 에이전트만
#   bash deploy.sh --agent build             # build 에이전트만
#   bash deploy.sh --agent all               # 전체 (generation, build)
#   bash deploy.sh --setup-iam               # IAM 역할만 설정
#   bash deploy.sh --setup-ecr               # ECR 리포지터리만 생성
#   bash deploy.sh --test generation         # 배포된 에이전트 CLI 테스트
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-rapa-cm1-21}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-}"

ARTIFACTS_BUCKET="hezo-artifacts"
SITE_BUCKET="hezo-sites"
ROLE_NAME="hezo-agentcore-execution-role"
# MVP: generation + build 만 배포 (validation/report는 P2 완료 후)
AGENTS=(generation build)

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
error()   { echo "[ERROR] $*" >&2; exit 1; }
step()    { echo; echo "── STEP $* ──────────────────────────────────────────"; }

aws_cmd() { aws --profile "$PROFILE" "$@"; }

# Git Bash 경로 → Windows 경로 변환
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
    identity=$(aws_cmd sts get-caller-identity --output json 2>/dev/null) || \
        error "AWS 인증 실패. 프로파일 '$PROFILE'을 확인하세요."

    if [ -z "$ACCOUNT_ID" ]; then
        ACCOUNT_ID=$(echo "$identity" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
    fi
    local caller
    caller=$(echo "$identity" | python3 -c "import sys,json; print(json.load(sys.stdin)['UserId'])")
    success "AWS 인증: $caller (Account: $ACCOUNT_ID)"

    # bedrock-agentcore-control 지원 여부 확인
    if ! aws_cmd bedrock-agentcore-control help >/dev/null 2>&1; then
        error "aws bedrock-agentcore-control 미지원 — AWS CLI 최신 버전 필요 (v2.17+)"
    fi
    success "bedrock-agentcore-control CLI 확인 완료"
}

# =============================================================================
# STEP 1: IAM 역할 설정
# =============================================================================
setup_iam() {
    step "1: IAM 역할 설정 ($ROLE_NAME)"

    local trust_file; trust_file=$(fix_path "$REPO_ROOT/infra/iam/agentcore-execution-trust.json")
    local policy_file; policy_file=$(fix_path "$REPO_ROOT/infra/iam/agentcore-execution-policy.json")

    # ${AWS_ACCOUNT_ID} 플레이스홀더 치환
    local tmp_policy tmp_trust
    tmp_policy=$(python3 -c "
import sys, tempfile
content = open(sys.argv[1], encoding='utf-8').read().replace('\${AWS_ACCOUNT_ID}', sys.argv[2])
tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
tf.write(content); tf.close(); print(tf.name)
" "$policy_file" "$ACCOUNT_ID")
    tmp_trust=$(python3 -c "
import sys, tempfile
content = open(sys.argv[1], encoding='utf-8').read().replace('\${AWS_ACCOUNT_ID}', sys.argv[2])
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

    # AgentCore가 ECR에서 이미지를 pull할 수 있는 관리형 정책 추가
    aws_cmd iam attach-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-arn "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly" \
        2>/dev/null || info "ECR ReadOnly 정책 이미 연결됨"
}

# =============================================================================
# STEP 2: ECR 리포지터리 생성
# =============================================================================
setup_ecr() {
    step "2: ECR 리포지터리 생성"
    local target="${1:-all}"
    local repos=()
    if [ "$target" = "all" ]; then
        repos=("${AGENTS[@]}")
    else
        repos=("$target")
    fi

    for agent in "${repos[@]}"; do
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

    info "ECR 로그인 중..."
    aws_cmd ecr get-login-password --region "$REGION" | \
        docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

    info "Docker 이미지 빌드 중: $repo"
    docker build -f "$dockerfile" -t "${repo}:latest" -t "${ecr_uri}:latest" "$context"

    info "ECR 푸시 중..."
    docker push "${ecr_uri}:latest"
    success "ECR 푸시 완료: ${ecr_uri}:latest"

    aws_cmd ssm put-parameter --name "hezo-${agent}-agent-image-uri" \
        --value "${ecr_uri}:latest" --type String --overwrite --region "$REGION" >/dev/null
    success "SSM 저장: hezo-${agent}-agent-image-uri"
}

# =============================================================================
# STEP 4: AgentCore Runtime 생성 또는 업데이트
# =============================================================================
deploy_agentcore() {
    local agent="$1"
    step "4: AgentCore Runtime 배포 ($agent)"

    local repo="hezo-${agent}-agent"
    local ecr_uri="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${repo}:latest"
    local role_arn="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

    # 에이전트별 환경변수
    local model_id
    case "$agent" in
        report)    model_id="global.anthropic.claude-haiku-4-5-20251001" ;;
        build)     model_id="" ;;  # 빌드 에이전트는 LLM 불필요
        *)         model_id="global.anthropic.claude-sonnet-4-6" ;;
    esac

    local env_json
    env_json=$(python3 - "$agent" "$model_id" "$ARTIFACTS_BUCKET" "$SITE_BUCKET" <<'PYENV'
import sys, json
agent, model_id, ab, sb = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = {
    "ARTIFACTS_BUCKET": ab,
    "SITE_BUCKET": sb,
    "AWS_DEFAULT_REGION": "ap-northeast-2"
}
if model_id:
    env["MODEL_ID"] = model_id
if agent == "report":
    env["SSM_FLAG_KEY"] = "hezo-report-enabled"
print(json.dumps(env))
PYENV
)

    # 기존 런타임 조회
    local existing_id
    existing_id=$(aws_cmd bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
        --query "agentRuntimes[?agentRuntimeName=='${repo}'].agentRuntimeId | [0]" \
        --output text 2>/dev/null || echo "None")

    local runtime_id
    if [ -z "$existing_id" ] || [ "$existing_id" = "None" ]; then
        info "AgentCore Runtime 생성 중: $repo"
        runtime_id=$(aws_cmd bedrock-agentcore-control create-agent-runtime \
            --agent-runtime-name "$repo" \
            --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ecr_uri}\"}}" \
            --role-arn "$role_arn" \
            --network-configuration "{\"networkMode\":\"PUBLIC\"}" \
            --environment-variables "$env_json" \
            --region "$REGION" \
            --query "agentRuntimeId" --output text)
        success "AgentCore Runtime 생성: ID=$runtime_id"
    else
        info "AgentCore Runtime 업데이트 중: $existing_id"
        aws_cmd bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-id "$existing_id" \
            --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ecr_uri}\"}}" \
            --environment-variables "$env_json" \
            --region "$REGION" >/dev/null
        runtime_id="$existing_id"
        success "AgentCore Runtime 업데이트: ID=$runtime_id"
    fi

    aws_cmd ssm put-parameter --name "hezo-${agent}-agent-id" \
        --value "$runtime_id" --type String --overwrite --region "$REGION" >/dev/null

    # 런타임 READY 대기 (최대 5분)
    wait_for_runtime "$runtime_id" "$agent"

    # 엔드포인트 생성 또는 조회
    create_or_get_endpoint "$runtime_id" "$agent"
}

# =============================================================================
# STEP 4a: AgentCore Runtime READY 대기
# =============================================================================
wait_for_runtime() {
    local runtime_id="$1"
    local agent="$2"
    info "Runtime 활성화 대기 중 (최대 5분)..."

    local max_wait=60  # 5초 간격 × 60 = 5분
    local i=0
    while [ $i -lt $max_wait ]; do
        local status
        status=$(aws_cmd bedrock-agentcore-control get-agent-runtime \
            --agent-runtime-id "$runtime_id" \
            --region "$REGION" \
            --query "status" --output text 2>/dev/null || echo "UNKNOWN")

        case "$status" in
            "READY")
                success "Runtime READY: $agent ($runtime_id)"
                return 0
                ;;
            "FAILED"|"CREATE_FAILED")
                error "Runtime 활성화 실패: $status — 콘솔 로그 확인 필요"
                ;;
            *)
                info "  현재 상태: $status ... ($((i * 5))s 경과)"
                sleep 5
                ;;
        esac
        i=$((i + 1))
    done
    warn "5분 내 READY 상태 미달 — 콘솔에서 상태 확인 필요"
}

# =============================================================================
# STEP 4b: 엔드포인트 생성
# =============================================================================
create_or_get_endpoint() {
    local runtime_id="$1"
    local agent="$2"
    local repo="hezo-${agent}-agent"
    local endpoint_name="${repo}-ep"

    # 기존 엔드포인트 조회
    local existing_ep_id
    existing_ep_id=$(aws_cmd bedrock-agentcore-control list-agent-runtime-endpoints \
        --agent-runtime-id "$runtime_id" \
        --region "$REGION" \
        --query "runtimeEndpoints[?name=='${endpoint_name}'].id | [0]" \
        --output text 2>/dev/null || echo "None")

    if [ -z "$existing_ep_id" ] || [ "$existing_ep_id" = "None" ]; then
        info "엔드포인트 생성 중: $endpoint_name"
        local ep_result
        ep_result=$(aws_cmd bedrock-agentcore-control create-agent-runtime-endpoint \
            --agent-runtime-id "$runtime_id" \
            --name "$endpoint_name" \
            --region "$REGION" \
            --output json 2>/dev/null || echo "{}")

        local ep_id
        ep_id=$(echo "$ep_result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('agentEndpointId') or d.get('id', 'unknown'))" 2>/dev/null || echo "unknown")
        success "엔드포인트 생성: $ep_id"
    else
        info "엔드포인트 이미 존재: $existing_ep_id"
        local ep_id="$existing_ep_id"
    fi

    # 엔드포인트 ARN 조회 및 SSM 저장
    local ep_arn
    ep_arn=$(aws_cmd bedrock-agentcore-control list-agent-runtime-endpoints \
        --agent-runtime-id "$runtime_id" \
        --region "$REGION" \
        --query "runtimeEndpoints[?name=='${endpoint_name}'].agentRuntimeEndpointArn | [0]" \
        --output text 2>/dev/null || echo "")

    if [ -n "$ep_arn" ] && [ "$ep_arn" != "None" ]; then
        aws_cmd ssm put-parameter --name "hezo-${agent}-agent-endpoint-arn" \
            --value "$ep_arn" --type String --overwrite --region "$REGION" >/dev/null
        success "SSM 저장: hezo-${agent}-agent-endpoint-arn = $ep_arn"
    fi

    # 엔드포인트 READY 대기 (최대 3분)
    wait_for_endpoint "$runtime_id" "$endpoint_name" "$agent"
}

# =============================================================================
# STEP 4c: 엔드포인트 READY 대기
# =============================================================================
wait_for_endpoint() {
    local runtime_id="$1"
    local endpoint_name="$2"
    local agent="$3"
    info "엔드포인트 활성화 대기 중 (최대 3분)..."

    local max_wait=36  # 5초 간격 × 36 = 3분
    local i=0
    while [ $i -lt $max_wait ]; do
        local status
        status=$(aws_cmd bedrock-agentcore-control list-agent-runtime-endpoints \
            --agent-runtime-id "$runtime_id" \
            --region "$REGION" \
            --query "runtimeEndpoints[?name=='${endpoint_name}'].status | [0]" \
            --output text 2>/dev/null || echo "UNKNOWN")

        case "$status" in
            "READY")
                success "엔드포인트 READY: $agent"
                return 0
                ;;
            "CREATE_FAILED"|"FAILED")
                error "엔드포인트 활성화 실패: $status"
                ;;
            *)
                info "  엔드포인트 상태: $status ... ($((i * 5))s 경과)"
                sleep 5
                ;;
        esac
        i=$((i + 1))
    done
    warn "3분 내 엔드포인트 READY 미달 — 콘솔 확인 필요"
}

# =============================================================================
# STEP 5: S3 & SSM 인프라 초기화
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
    success "인프라 초기화 완료"
}

# =============================================================================
# 테스트: CLI로 직접 invoke
# =============================================================================
test_agent() {
    local agent="$1"
    step "TEST: $agent 에이전트 직접 invoke"

    local runtime_id
    runtime_id=$(aws_cmd ssm get-parameter --name "hezo-${agent}-agent-id" \
        --query "Parameter.Value" --output text --region "$REGION" 2>/dev/null || echo "")
    [ -z "$runtime_id" ] && error "SSM에 hezo-${agent}-agent-id 없음. 먼저 배포하세요."

    local runtime_arn="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:agentRuntime/${runtime_id}"
    info "Runtime ARN: $runtime_arn"

    local payload='{"sessionId":"cli-test-001","inputText":"site_id=site_tax_13_001","sessionAttributes":{"site_id":"site_tax_13_001","pipeline_run_id":"cli-test-001"}}'
    info "페이로드: $payload"

    local tmpout; tmpout=$(mktemp)
    aws_cmd bedrock-agentcore invoke-agent-runtime \
        --agent-runtime-arn "$runtime_arn" \
        --payload "$payload" \
        --content-type "application/json" \
        --accept "application/json" \
        --region "$REGION" \
        "$tmpout" 2>&1 && echo "--- invoke 응답 ---" && cat "$tmpout" || \
        warn "invoke 실패 — 위 오류를 확인하세요"
    rm -f "$tmpout"
}

# =============================================================================
# 메인
# =============================================================================
TARGET_AGENT="generation"  # 기본값: generation만
RUN_SETUP_IAM=false
RUN_SETUP_ECR=false
RUN_TEST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)     TARGET_AGENT="$2"; shift 2 ;;
        --setup-iam) RUN_SETUP_IAM=true; shift ;;
        --setup-ecr) RUN_SETUP_ECR=true; shift ;;
        --test)      RUN_TEST="${2:-generation}"; shift 2 ;;
        *) warn "알 수 없는 옵션: $1"; shift ;;
    esac
done

echo "╔══════════════════════════════════════════════════════╗"
echo "║  HEZO AgentCore Runtime 배포 v3.0                   ║"
echo "╚══════════════════════════════════════════════════════╝"

preflight

# 테스트 전용
if [ -n "$RUN_TEST" ]; then
    [ -z "$ACCOUNT_ID" ] && ACCOUNT_ID=$(aws_cmd sts get-caller-identity --query Account --output text)
    test_agent "$RUN_TEST"
    exit 0
fi

$RUN_SETUP_IAM && { setup_iam; exit 0; }
$RUN_SETUP_ECR && { setup_ecr "$TARGET_AGENT"; exit 0; }

# 전체 배포 플로우
setup_infra
setup_iam
setup_ecr "$TARGET_AGENT"

if [ "$TARGET_AGENT" = "all" ]; then
    for agent in "${AGENTS[@]}"; do
        build_and_push "$agent"
        deploy_agentcore "$agent"
    done
else
    build_and_push "$TARGET_AGENT"
    deploy_agentcore "$TARGET_AGENT"
fi

echo
echo "╔══════════════════════════════════════════════════════╗"
echo "║  배포 완료                                           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  SSM hezo-${TARGET_AGENT}-agent-id"
echo "  SSM hezo-${TARGET_AGENT}-agent-endpoint-arn"
echo
echo "  [CLI 테스트]"
echo "  bash deploy.sh --test ${TARGET_AGENT}"
echo
echo "  [S3 결과 확인]"
echo "  aws s3 ls s3://hezo-artifacts/sites/site_tax_13_001/ --profile $PROFILE"
