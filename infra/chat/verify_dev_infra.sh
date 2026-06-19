#!/usr/bin/env bash
# Read-only verification for P1 Chat Agent dev AWS infra.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${SCRIPT_DIR}/env.example" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${SCRIPT_DIR}/env.example"
  set +a
fi

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-}"
TABLE_NAME="${HEZO_AGENT_DYNAMODB_TABLE:-hezo_agent_chat}"
CHAT_BUCKET="${HEZO_CHAT_BUCKET:-hezo-chat}"
P2_MARKDOWNS_BUCKET="${HEZO_P2_MARKDOWNS_BUCKET:-hezo-wiki}"
CONTRACTS_BUCKET="${HEZO_CONTRACTS_BUCKET:-hezo-artifacts}"
MODEL_ID="${HEZO_BEDROCK_MODEL_ID:-anthropic.claude-sonnet-4-5-20250929-v1:0}"
GUARDRAIL_NAME="${HEZO_BEDROCK_GUARDRAIL_NAME:-hezo-dev-guardrail}"
GUARDRAIL_ID="${HEZO_BEDROCK_GUARDRAIL_ID:-q8dcjc2um846}"
GUARDRAIL_VERSION="${HEZO_BEDROCK_GUARDRAIL_VERSION:-DRAFT}"
ECR_REPOSITORY="${HEZO_ECR_REPOSITORY:-hezo-chat-agent}"
AGENTCORE_RUNTIME_RAW="${HEZO_AGENTCORE_RUNTIME_NAME:-${HEZO_AGENTCORE_RUNTIME:-hezo-chat-agent-dev}}"

AWS_ARGS=(--region "$REGION")
if [ -n "$PROFILE" ]; then
  AWS_ARGS+=(--profile "$PROFILE")
fi

ok() {
  printf '[OK] %s\n' "$1"
}

missing() {
  printf '[MISSING] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

normalize_runtime_name() {
  local raw="$1"
  local normalized
  normalized="$(printf '%s' "$raw" | tr '-' '_' | tr -cd '[:alnum:]_')"

  if [[ ! "$normalized" =~ ^[A-Za-z] ]]; then
    normalized="hezo_${normalized}"
  fi

  printf '%.48s' "$normalized"
}

check_aws_cli() {
  if ! command -v aws >/dev/null 2>&1; then
    printf '[ERROR] aws CLI가 설치되어 있지 않습니다.\n' >&2
    exit 1
  fi
  ok "aws CLI 확인"
}

check_identity() {
  if aws sts get-caller-identity "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "AWS identity 확인"
  else
    missing "AWS identity 확인 실패: profile/credential/region을 확인하세요"
  fi
}

check_dynamodb() {
  if aws dynamodb describe-table --table-name "$TABLE_NAME" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "DynamoDB table: $TABLE_NAME"
  else
    missing "DynamoDB table: $TABLE_NAME"
  fi
}

check_bucket() {
  local bucket="$1"
  if aws s3api head-bucket --bucket "$bucket" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "S3 bucket: $bucket"
  else
    missing "S3 bucket: $bucket"
  fi
}

check_bedrock_model() {
  if aws bedrock get-foundation-model --model-identifier "$MODEL_ID" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "Bedrock model access: $MODEL_ID"
  else
    missing "Bedrock model access: $MODEL_ID"
  fi
}

check_guardrail() {
  if aws bedrock get-guardrail \
    --guardrail-identifier "$GUARDRAIL_ID" \
    --guardrail-version "$GUARDRAIL_VERSION" \
    "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "Bedrock guardrail: $GUARDRAIL_NAME ($GUARDRAIL_ID/$GUARDRAIL_VERSION)"
  else
    warn "Bedrock guardrail 확인 실패: $GUARDRAIL_NAME ($GUARDRAIL_ID/$GUARDRAIL_VERSION)"
  fi
}

check_ecr() {
  if aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "ECR repository: $ECR_REPOSITORY"
  else
    missing "ECR repository: $ECR_REPOSITORY"
  fi
}

check_agentcore_runtime() {
  local runtime_name
  runtime_name="$(normalize_runtime_name "$AGENTCORE_RUNTIME_RAW")"

  if ! aws bedrock-agentcore-control help >/dev/null 2>&1; then
    warn "AgentCore Runtime 확인 실패: bedrock-agentcore-control CLI 미지원"
    return
  fi

  local runtime_id
  runtime_id="$(aws bedrock-agentcore-control list-agent-runtimes "${AWS_ARGS[@]}" \
    --query "agentRuntimes[?agentRuntimeName=='${runtime_name}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null || echo "None")"

  if [ -z "$runtime_id" ] || [ "$runtime_id" = "None" ]; then
    missing "AgentCore Runtime: $runtime_name"
    return
  fi

  local status
  status="$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$runtime_id" \
    "${AWS_ARGS[@]}" \
    --query "status" \
    --output text 2>/dev/null || echo "UNKNOWN")"

  ok "AgentCore Runtime: $runtime_name ($runtime_id/$status)"
}

main() {
  printf 'HEZO P1 Chat Agent dev infra verification\n'
  printf 'region=%s profile=%s\n\n' "$REGION" "${PROFILE:-default}"

  check_aws_cli
  check_identity
  check_dynamodb
  check_bucket "$CHAT_BUCKET"
  check_bucket "$P2_MARKDOWNS_BUCKET"
  check_bucket "$CONTRACTS_BUCKET"
  check_bedrock_model
  check_guardrail
  check_ecr
  check_agentcore_runtime

  printf '\n검증 완료: MISSING 항목은 후속 AWS 생성 이슈에서 생성해야 합니다.\n'
}

main "$@"
