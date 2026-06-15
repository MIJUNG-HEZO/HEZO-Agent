#!/usr/bin/env bash
# =============================================================================
# HEZO Generation Agent 배포 스크립트
# Lambda 함수 패키징/배포 → Bedrock Agent 생성 → 액션 그룹 등록 → 별칭 생성
# 사용법: bash deploy.sh [--skip-lambda] [--agent-only]
# =============================================================================

set -euo pipefail

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

# ─── 플래그 파싱 ────────────────────────────────────────────────────────────
SKIP_LAMBDA=false
AGENT_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --skip-lambda) SKIP_LAMBDA=true ;;
        --agent-only)  AGENT_ONLY=true; SKIP_LAMBDA=true ;;
    esac
done

# ─── 설정값 ─────────────────────────────────────────────────────────────────
REGION="ap-northeast-2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ACTION_GROUPS_DIR="${SCRIPT_DIR}/action_groups"
SHARED_DIR="${SCRIPT_DIR}/../shared"

# Lambda 함수명 (hezo-p4-notify 제거 — DynamoDB 직접 통합으로 교체)
LAMBDA_CONTRACT_LOADER="hezo-p4-contract-loader"
LAMBDA_RENDER_SPEC_SAVER="hezo-p4-render-spec-saver"
LAMBDA_CONTRACT_UPLOADER="hezo-p4-upload-contract"

# Bedrock Agent 설정
AGENT_NAME="hezo-generation-agent"
FOUNDATION_MODEL="anthropic.claude-sonnet-4-5-20251001"
AGENT_ALIAS_NAME="stable"

ARTIFACTS_BUCKET="hezo-artifacts"

echo ""
echo "========================================================"
echo "  HEZO Generation Agent 배포"
echo "  리전: ${REGION}"
echo "========================================================"
echo ""

# =============================================================================
# 사전 조건 확인
# =============================================================================
info "사전 조건 확인 중..."

command -v aws  &>/dev/null || die "AWS CLI가 없습니다."
command -v zip  &>/dev/null || die "zip 유틸리티가 없습니다. (brew install zip 또는 apt install zip)"
command -v python3 &>/dev/null || die "python3가 없습니다."

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || \
    die "AWS 자격증명이 유효하지 않습니다."
success "AWS 계정 ID: ${ACCOUNT_ID}"

LAMBDA_ROLE_ARN=$(aws iam get-role \
    --role-name "hezo-lambda-execution-role" \
    --query "Role.Arn" \
    --output text 2>/dev/null) || \
    die "IAM 역할 hezo-lambda-execution-role 없음. infra/aws_setup.sh를 먼저 실행하세요."
success "Lambda IAM 역할 ARN: ${LAMBDA_ROLE_ARN}"

BEDROCK_AGENT_ROLE_ARN=$(aws iam get-role \
    --role-name "hezo-bedrock-agent-role" \
    --query "Role.Arn" \
    --output text 2>/dev/null) || \
    die "IAM 역할 hezo-bedrock-agent-role 없음."
success "Bedrock Agent IAM 역할 ARN: ${BEDROCK_AGENT_ROLE_ARN}"
echo ""

# =============================================================================
# Lambda 함수 패키징 및 배포 헬퍼
# =============================================================================

# 빌드 임시 디렉터리
BUILD_DIR=$(mktemp -d /tmp/hezo_lambda_build_XXXXXX)
trap 'rm -rf "$BUILD_DIR"' EXIT

package_and_deploy_lambda() {
    local function_name="$1"
    local source_file="$2"
    local handler="$3"
    local description="$4"
    local env_vars="${5:-}"  # 선택적: Key=Val,Key2=Val2 형식

    local zip_path="${BUILD_DIR}/${function_name}.zip"
    local package_dir="${BUILD_DIR}/${function_name}_pkg"

    info "Lambda 패키징: ${function_name}"
    mkdir -p "$package_dir"

    # 메인 소스 파일 복사
    cp "$source_file" "${package_dir}/$(basename "$source_file")"

    # 공유 모듈 복사 (agents/shared/)
    if [ -d "$SHARED_DIR" ]; then
        mkdir -p "${package_dir}/agents/shared"
        cp "${SHARED_DIR}"/*.py "${package_dir}/agents/shared/" 2>/dev/null || true
        # agents/__init__.py 생성 (패키지 인식용)
        touch "${package_dir}/agents/__init__.py"
        touch "${package_dir}/agents/shared/__init__.py"
    fi

    # boto3는 Lambda 런타임에 포함되어 있으므로 별도 설치 불필요
    # 추가 의존성이 있는 경우 여기서 pip install --target 으로 설치

    # ZIP 패키지 생성
    (cd "$package_dir" && zip -r "$zip_path" . -x "*.pyc" "*/__pycache__/*" "*/.*") > /dev/null
    local zip_size
    zip_size=$(du -sh "$zip_path" | cut -f1)
    success "  ZIP 패키지 생성: ${zip_path} (${zip_size})"

    # Lambda 함수 존재 여부 확인
    if aws lambda get-function --function-name "$function_name" --region "$REGION" > /dev/null 2>&1; then
        # 코드 업데이트
        info "  기존 Lambda 함수 코드 업데이트: ${function_name}"
        aws lambda update-function-code \
            --function-name "$function_name" \
            --zip-file "fileb://${zip_path}" \
            --region "$REGION" \
            --output text > /dev/null

        # 설정 업데이트
        aws lambda update-function-configuration \
            --function-name "$function_name" \
            --handler "$handler" \
            --runtime "python3.12" \
            --description "$description" \
            --timeout 30 \
            --memory-size 256 \
            --region "$REGION" \
            --output text > /dev/null

    else
        # 신규 생성
        info "  Lambda 함수 신규 생성: ${function_name}"
        local create_args=(
            --function-name "$function_name"
            --zip-file "fileb://${zip_path}"
            --handler "$handler"
            --runtime "python3.12"
            --role "$LAMBDA_ROLE_ARN"
            --description "$description"
            --timeout 30
            --memory-size 256
            --region "$REGION"
            --tags "Project=HEZO,ManagedBy=deploy.sh"
            --output text
        )

        if [ -n "$env_vars" ]; then
            create_args+=(--environment "Variables={${env_vars}}")
        fi

        aws lambda create-function "${create_args[@]}" > /dev/null
    fi

    # 환경변수 업데이트 (신규/기존 모두)
    if [ -n "$env_vars" ]; then
        aws lambda update-function-configuration \
            --function-name "$function_name" \
            --environment "Variables={${env_vars}}" \
            --region "$REGION" \
            --output text > /dev/null
    fi

    # 배포 완료 대기
    aws lambda wait function-updated \
        --function-name "$function_name" \
        --region "$REGION"

    success "  Lambda 배포 완료: ${function_name}"
}

# Lambda에 Bedrock Agent 호출 권한 추가
add_bedrock_invoke_permission() {
    local function_name="$1"

    # 이미 권한이 있으면 스킵
    if aws lambda get-policy \
        --function-name "$function_name" \
        --region "$REGION" \
        --output text 2>/dev/null | grep -q "bedrock.amazonaws.com"; then
        warn "  Bedrock 호출 권한 이미 존재: ${function_name}"
        return
    fi

    aws lambda add-permission \
        --function-name "$function_name" \
        --statement-id "AllowBedrockAgentInvoke" \
        --action "lambda:InvokeFunction" \
        --principal "bedrock.amazonaws.com" \
        --source-account "$ACCOUNT_ID" \
        --region "$REGION" \
        --output text > /dev/null
    success "  Bedrock 호출 권한 추가: ${function_name}"
}

# =============================================================================
# 1. Lambda 함수 배포
# =============================================================================
if [ "$SKIP_LAMBDA" = false ]; then
    info "━━━ 1단계: Lambda 함수 배포 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    ENV_COMMON="ARTIFACTS_BUCKET=${ARTIFACTS_BUCKET},AWS_REGION=${REGION}"

    package_and_deploy_lambda \
        "$LAMBDA_CONTRACT_LOADER" \
        "${ACTION_GROUPS_DIR}/contract_loader.py" \
        "contract_loader.lambda_handler" \
        "HEZO Bedrock Agent Action Group - ContractLoader (S3에서 계약 JSON 읽기)" \
        "$ENV_COMMON"
    add_bedrock_invoke_permission "$LAMBDA_CONTRACT_LOADER"
    echo ""

    package_and_deploy_lambda \
        "$LAMBDA_RENDER_SPEC_SAVER" \
        "${ACTION_GROUPS_DIR}/render_spec_saver.py" \
        "render_spec_saver.lambda_handler" \
        "HEZO Bedrock Agent Action Group - RenderSpecSaver (render_spec.json S3 저장)" \
        "$ENV_COMMON"
    add_bedrock_invoke_permission "$LAMBDA_RENDER_SPEC_SAVER"
    echo ""

    package_and_deploy_lambda \
        "$LAMBDA_CONTRACT_UPLOADER" \
        "${ACTION_GROUPS_DIR}/contract_uploader.py" \
        "contract_uploader.lambda_handler" \
        "HEZO Step Functions Task - contract_uploader (계약 JSON S3 업로드)" \
        "$ENV_COMMON"
    echo ""

    success "모든 Lambda 함수 배포 완료"
    echo ""
fi

# =============================================================================
# 2. Bedrock Agent 생성 또는 업데이트
# =============================================================================
info "━━━ 2단계: Bedrock Agent 생성/업데이트 ━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 에이전트 지시문 (agent_config.yaml의 AgentInstructions 와 동일)
# 여기서는 간략화된 버전 - 실제로는 agent_config.yaml 에서 읽어오는 것을 권장
AGENT_INSTRUCTION=$(cat <<'INSTRUCTION'
당신은 HEZO의 홈페이지 생성 AI 에이전트입니다. 주어진 site_id에 대한 contract_final.json을 읽고, AI 검색 엔진(ChatGPT, Claude, Perplexity, Naver Cue)에 최적화된 완전한 render_spec.json을 생성하는 것이 당신의 역할입니다.

## 작업 순서
1. ContractLoader 도구의 /get-contract 를 호출하여 contract_final.json을 읽으세요.
2. ContractLoader 도구의 /get-crawl-snapshot 을 호출하여 크롤 스냅샷을 읽으세요(없어도 계속 진행).
3. 아래 항목을 포함한 render_spec.json을 생성하세요:
   - SEO 메타데이터 (title 50-60자, description 120-155자, canonical, og, twitter)
   - Schema.org JSON-LD (업종에 맞는 타입: MedicalBusiness/Restaurant/SoftwareApplication/EducationalOrganization/LocalBusiness 등)
   - FAQ 5-7개 (AEO 최적화 자연어 질문형 H2, 답변 50-150자)
   - QuickAnswer 블록 (AI 검색 스니펫 인용용 2-3문장)
   - H1 (페이지당 정확히 1개), H2 목록 (섹션 제목 3-5개 + FAQ H2)
   - 이미지 alt 텍스트 (한국어, 키워드 포함)
   - llms.txt (# BrandName > tagline 형식)
   - llms-full.txt (상세 브랜드/서비스 정보)
   - sitemap_pages (/, /about, /services, /faq, /contact 등)
   - robots_rules (GPTBot, ClaudeBot, PerplexityBot, NaverBot 명시적 허용)
4. RenderSpecSaver 도구의 /save-render-spec 을 호출하여 render_spec을 저장하세요.
5. 저장 완료 후 "render_spec_saved: site_id=[site_id]" 형식으로 응답하세요.

중요: 한국어로 작성하고, 실제 사업자 정보를 기반으로 하며, JSON-LD는 유효한 JSON이어야 합니다.
INSTRUCTION
)

# 기존 에이전트 조회
EXISTING_AGENT_ID=$(aws bedrock-agent list-agents \
    --region "$REGION" \
    --query "agentSummaries[?agentName=='${AGENT_NAME}'].agentId | [0]" \
    --output text 2>/dev/null)

if [ "$EXISTING_AGENT_ID" = "None" ] || [ -z "$EXISTING_AGENT_ID" ]; then
    # ── 신규 에이전트 생성 ───────────────────────────────────────────────────
    info "Bedrock Agent 신규 생성: ${AGENT_NAME}"

    CREATE_RESULT=$(aws bedrock-agent create-agent \
        --agent-name "$AGENT_NAME" \
        --agent-resource-role-arn "$BEDROCK_AGENT_ROLE_ARN" \
        --foundation-model "$FOUNDATION_MODEL" \
        --instruction "$AGENT_INSTRUCTION" \
        --idle-session-ttl-in-seconds 600 \
        --description "Contract JSON을 받아 render_spec.json을 생성하는 HEZO 생성 에이전트" \
        --region "$REGION" \
        --output json)

    AGENT_ID=$(echo "$CREATE_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['agent']['agentId'])")
    success "Bedrock Agent 생성 완료: ${AGENT_ID}"

else
    # ── 기존 에이전트 지시문 업데이트 ──────────────────────────────────────
    AGENT_ID="$EXISTING_AGENT_ID"
    info "기존 Bedrock Agent 업데이트: ${AGENT_ID}"

    aws bedrock-agent update-agent \
        --agent-id "$AGENT_ID" \
        --agent-name "$AGENT_NAME" \
        --agent-resource-role-arn "$BEDROCK_AGENT_ROLE_ARN" \
        --foundation-model "$FOUNDATION_MODEL" \
        --instruction "$AGENT_INSTRUCTION" \
        --idle-session-ttl-in-seconds 600 \
        --region "$REGION" \
        --output text > /dev/null

    success "Bedrock Agent 업데이트 완료: ${AGENT_ID}"
fi
echo ""

# =============================================================================
# 3. 액션 그룹 생성/업데이트
# =============================================================================
info "━━━ 3단계: 액션 그룹 생성/업데이트 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ContractLoader 액션 그룹 OpenAPI 스키마
CONTRACT_LOADER_SCHEMA=$(cat <<'SCHEMA'
{
  "openapi": "3.0.0",
  "info": { "title": "ContractLoader API", "version": "1.0" },
  "paths": {
    "/get-contract": {
      "get": {
        "summary": "S3에서 contract_final.json 읽기",
        "operationId": "getContract",
        "parameters": [
          { "name": "site_id", "in": "query", "required": true, "schema": { "type": "string" }, "description": "사이트 ID" }
        ],
        "responses": {
          "200": { "description": "계약 JSON 반환", "content": { "application/json": { "schema": { "type": "object" } } } },
          "404": { "description": "계약 JSON 없음" }
        }
      }
    },
    "/get-crawl-snapshot": {
      "get": {
        "summary": "S3에서 crawl_snapshot.json 읽기 (선택적)",
        "operationId": "getCrawlSnapshot",
        "parameters": [
          { "name": "site_id", "in": "query", "required": true, "schema": { "type": "string" } }
        ],
        "responses": {
          "200": { "description": "스냅샷 반환 (없으면 snapshot_available: false)", "content": { "application/json": { "schema": { "type": "object" } } } }
        }
      }
    }
  }
}
SCHEMA
)

# RenderSpecSaver 액션 그룹 OpenAPI 스키마
RENDER_SPEC_SAVER_SCHEMA=$(cat <<'SCHEMA'
{
  "openapi": "3.0.0",
  "info": { "title": "RenderSpecSaver API", "version": "1.0" },
  "paths": {
    "/save-render-spec": {
      "post": {
        "summary": "render_spec.json을 S3에 저장",
        "operationId": "saveRenderSpec",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["site_id", "render_spec"],
                "properties": {
                  "site_id": { "type": "string", "description": "사이트 ID" },
                  "render_spec": { "type": "object", "description": "render_spec.json 오브젝트" }
                }
              }
            }
          }
        },
        "responses": {
          "200": { "description": "저장 성공", "content": { "application/json": { "schema": { "type": "object" } } } }
        }
      }
    }
  }
}
SCHEMA
)

LAMBDA_CONTRACT_LOADER_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${LAMBDA_CONTRACT_LOADER}"
LAMBDA_RENDER_SPEC_SAVER_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${LAMBDA_RENDER_SPEC_SAVER}"

# 액션 그룹 생성 헬퍼
create_or_update_action_group() {
    local ag_name="$1"
    local lambda_arn="$2"
    local schema_json="$3"

    info "  액션 그룹 처리: ${ag_name}"

    # 기존 액션 그룹 ID 조회
    EXISTING_AG_ID=$(aws bedrock-agent list-agent-action-groups \
        --agent-id "$AGENT_ID" \
        --agent-version "DRAFT" \
        --region "$REGION" \
        --query "actionGroupSummaries[?actionGroupName=='${ag_name}'].actionGroupId | [0]" \
        --output text 2>/dev/null)

    local INLINE_SCHEMA_JSON
    INLINE_SCHEMA_JSON=$(echo "$schema_json" | python3 -c "import sys,json; print(json.dumps({'inlinePayload': sys.stdin.read()}))")

    if [ "$EXISTING_AG_ID" = "None" ] || [ -z "$EXISTING_AG_ID" ]; then
        aws bedrock-agent create-agent-action-group \
            --agent-id "$AGENT_ID" \
            --agent-version "DRAFT" \
            --action-group-name "$ag_name" \
            --action-group-state "ENABLED" \
            --action-group-executor "{\"lambda\": \"${lambda_arn}\"}" \
            --api-schema "{\"payload\": $(echo "$schema_json" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")}" \
            --region "$REGION" \
            --output text > /dev/null
        success "    액션 그룹 생성 완료: ${ag_name}"
    else
        aws bedrock-agent update-agent-action-group \
            --agent-id "$AGENT_ID" \
            --agent-version "DRAFT" \
            --action-group-id "$EXISTING_AG_ID" \
            --action-group-name "$ag_name" \
            --action-group-state "ENABLED" \
            --action-group-executor "{\"lambda\": \"${lambda_arn}\"}" \
            --api-schema "{\"payload\": $(echo "$schema_json" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read()))")}" \
            --region "$REGION" \
            --output text > /dev/null
        success "    액션 그룹 업데이트 완료: ${ag_name}"
    fi
}

create_or_update_action_group "ContractLoader"   "$LAMBDA_CONTRACT_LOADER_ARN"  "$CONTRACT_LOADER_SCHEMA"
create_or_update_action_group "RenderSpecSaver"  "$LAMBDA_RENDER_SPEC_SAVER_ARN" "$RENDER_SPEC_SAVER_SCHEMA"
echo ""

# =============================================================================
# 4. 에이전트 준비(Prepare) 실행
# =============================================================================
info "━━━ 4단계: 에이전트 준비(Prepare) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

info "  Bedrock Agent Prepare 실행 중 (에이전트 컴파일)..."
aws bedrock-agent prepare-agent \
    --agent-id "$AGENT_ID" \
    --region "$REGION" \
    --output text > /dev/null

# 에이전트 준비 완료 대기 (최대 3분)
info "  에이전트 준비 완료 대기 중 (최대 3분)..."
MAX_WAIT=180
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    AGENT_STATUS=$(aws bedrock-agent get-agent \
        --agent-id "$AGENT_ID" \
        --region "$REGION" \
        --query "agent.agentStatus" \
        --output text)

    if [ "$AGENT_STATUS" = "PREPARED" ]; then
        success "  에이전트 준비 완료 (상태: ${AGENT_STATUS})"
        break
    elif [ "$AGENT_STATUS" = "FAILED" ]; then
        die "에이전트 준비 실패 (상태: ${AGENT_STATUS}). AWS 콘솔에서 오류를 확인하세요."
    fi

    info "  현재 상태: ${AGENT_STATUS}... (${WAITED}초 경과)"
    sleep 10
    WAITED=$((WAITED + 10))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    warn "에이전트 준비 타임아웃. 현재 상태를 확인하고 수동으로 별칭을 생성하세요."
fi
echo ""

# =============================================================================
# 5. 에이전트 별칭 생성 (stable)
# =============================================================================
info "━━━ 5단계: 에이전트 별칭 생성 (${AGENT_ALIAS_NAME}) ━━━━━━━━━━━━━━━━"
echo ""

# 최신 에이전트 버전 조회
AGENT_VERSION=$(aws bedrock-agent list-agent-versions \
    --agent-id "$AGENT_ID" \
    --region "$REGION" \
    --query "agentVersionSummaries | sort_by(@, &creationDateTime) | [-1].agentVersion" \
    --output text 2>/dev/null)

if [ -z "$AGENT_VERSION" ] || [ "$AGENT_VERSION" = "None" ]; then
    # DRAFT 버전 사용 (아직 정식 버전 없는 경우)
    AGENT_VERSION="DRAFT"
    warn "에이전트 정식 버전 없음. DRAFT 버전으로 별칭 생성."
fi

info "  대상 에이전트 버전: ${AGENT_VERSION}"

# 기존 별칭 조회
EXISTING_ALIAS_ID=$(aws bedrock-agent list-agent-aliases \
    --agent-id "$AGENT_ID" \
    --region "$REGION" \
    --query "agentAliasSummaries[?agentAliasName=='${AGENT_ALIAS_NAME}'].agentAliasId | [0]" \
    --output text 2>/dev/null)

ROUTING_CONFIG="[{\"agentVersion\": \"${AGENT_VERSION}\"}]"

if [ "$EXISTING_ALIAS_ID" = "None" ] || [ -z "$EXISTING_ALIAS_ID" ]; then
    info "  별칭 '${AGENT_ALIAS_NAME}' 신규 생성..."
    ALIAS_RESULT=$(aws bedrock-agent create-agent-alias \
        --agent-id "$AGENT_ID" \
        --agent-alias-name "$AGENT_ALIAS_NAME" \
        --description "프로덕션 안정 버전 별칭. Step Functions에서 이 별칭을 사용." \
        --routing-configuration "$ROUTING_CONFIG" \
        --region "$REGION" \
        --output json)

    AGENT_ALIAS_ID=$(echo "$ALIAS_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['agentAlias']['agentAliasId'])")
    success "  별칭 생성 완료: ${AGENT_ALIAS_NAME} (ID: ${AGENT_ALIAS_ID})"
else
    AGENT_ALIAS_ID="$EXISTING_ALIAS_ID"
    info "  기존 별칭 업데이트: ${AGENT_ALIAS_NAME} (ID: ${AGENT_ALIAS_ID})"
    aws bedrock-agent update-agent-alias \
        --agent-id "$AGENT_ID" \
        --agent-alias-id "$AGENT_ALIAS_ID" \
        --agent-alias-name "$AGENT_ALIAS_NAME" \
        --routing-configuration "$ROUTING_CONFIG" \
        --region "$REGION" \
        --output text > /dev/null
    success "  별칭 업데이트 완료: ${AGENT_ALIAS_NAME}"
fi
echo ""

# =============================================================================
# 6. SSM Parameter Store에 에이전트 ID 저장
# =============================================================================
info "━━━ 6단계: SSM Parameter Store 업데이트 ━━━━━━━━━━━━━━━━━━━━━━━━━━"

aws ssm put-parameter \
    --name "/hezo/bedrock-agent-id" \
    --value "$AGENT_ID" \
    --type "String" \
    --description "HEZO Generation Agent Bedrock Agent ID" \
    --overwrite \
    --region "$REGION" \
    --output text > /dev/null
success "SSM 저장: /hezo/bedrock-agent-id = ${AGENT_ID}"

aws ssm put-parameter \
    --name "/hezo/bedrock-agent-alias-id" \
    --value "$AGENT_ALIAS_ID" \
    --type "String" \
    --description "HEZO Generation Agent Bedrock Agent Alias ID (stable)" \
    --overwrite \
    --region "$REGION" \
    --output text > /dev/null
success "SSM 저장: /hezo/bedrock-agent-alias-id = ${AGENT_ALIAS_ID}"
echo ""

# =============================================================================
# 7. 배포 요약
# =============================================================================
echo "========================================================"
echo "  HEZO Generation Agent 배포 완료"
echo "========================================================"
echo ""
echo "  [Bedrock Agent]"
echo "  Agent ID       : ${AGENT_ID}"
echo "  Agent Alias ID : ${AGENT_ALIAS_ID} (이름: ${AGENT_ALIAS_NAME})"
echo "  Foundation Model: ${FOUNDATION_MODEL}"
echo "  리전           : ${REGION}"
echo ""
echo "  [Lambda 함수]"
echo "  ${LAMBDA_CONTRACT_LOADER}"
echo "  ${LAMBDA_RENDER_SPEC_SAVER}"
echo "  ${LAMBDA_CONTRACT_UPLOADER}"
echo "  (hezo-p4-notify 없음 — Step Functions DynamoDB 직접 통합)"
echo ""
echo "  [다음 단계]"
echo "  infra/step_functions/deploy_state_machine.sh 를 실행하여"
echo "  Step Functions 상태 머신을 배포하세요."
echo ""
echo "  테스트:"
echo "  aws bedrock-agent-runtime invoke-agent \\"
echo "    --agent-id '${AGENT_ID}' \\"
echo "    --agent-alias-id '${AGENT_ALIAS_ID}' \\"
echo "    --session-id 'test-session-001' \\"
echo "    --input-text 'site_id=test-site-001 에 대한 render_spec.json을 생성하세요.' \\"
echo "    --region ${REGION}"
echo "========================================================"
