#!/usr/bin/env bash
# Read-only verification for P1 Chat Agent dev AWS infra.

set -u

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-}"
TABLE_NAME="${HEZO_AGENT_DYNAMODB_TABLE:-hezo_agent_chat}"
CHAT_BUCKET="${HEZO_CHAT_BUCKET:-hezo-chat}"
P2_MARKDOWNS_BUCKET="${HEZO_P2_MARKDOWNS_BUCKET:-hezo-wiki}"
CONTRACTS_BUCKET="${HEZO_CONTRACTS_BUCKET:-hezo-artifacts}"
MODEL_ID="${HEZO_BEDROCK_MODEL_ID:-anthropic.claude-sonnet-4-5-20251001}"
GUARDRAIL_ID="${HEZO_BEDROCK_GUARDRAIL_ID:-hezo-dev-guardrail}"
ECR_REPOSITORY="${HEZO_ECR_REPOSITORY:-hezo-chat-agent}"

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
  if aws bedrock list-guardrails "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "Bedrock guardrails API 접근 가능: $GUARDRAIL_ID 존재 여부는 콘솔 또는 상세 조회로 확인"
  else
    warn "Bedrock guardrails API 확인 실패: CLI 버전/권한/리전 지원 여부를 확인하세요"
  fi
}

check_ecr() {
  if aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
    ok "ECR repository: $ECR_REPOSITORY"
  else
    missing "ECR repository: $ECR_REPOSITORY"
  fi
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

  printf '\n검증 완료: MISSING 항목은 후속 AWS 생성 이슈에서 생성해야 합니다.\n'
}

main "$@"
