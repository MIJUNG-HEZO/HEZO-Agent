#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/env.example}"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-default}"
REPOSITORY="${HEZO_ECR_REPOSITORY:-hezo-chat-agent}"
TAG="${IMAGE_TAG:-latest}"
ROLE_NAME="${HEZO_AGENTCORE_ROLE_NAME:-hezo-agentcore-execution-role}"
RUNTIME_RAW="${HEZO_AGENTCORE_RUNTIME_NAME:-${HEZO_AGENTCORE_RUNTIME:-hezo-chat-agent-dev}}"
NETWORK_MODE="${HEZO_AGENTCORE_NETWORK_MODE:-PUBLIC}"
PROTOCOL="${HEZO_AGENTCORE_PROTOCOL:-HTTP}"
IDLE_TIMEOUT="${HEZO_AGENTCORE_IDLE_TIMEOUT:-900}"
MAX_LIFETIME="${HEZO_AGENTCORE_MAX_LIFETIME:-28800}"
WAIT_SECONDS="${HEZO_AGENTCORE_WAIT_SECONDS:-300}"
WAIT_INTERVAL="${HEZO_AGENTCORE_WAIT_INTERVAL:-5}"
DESCRIPTION="${HEZO_AGENTCORE_DESCRIPTION:-HEZO P1 Chat Agent dev runtime}"

usage() {
    cat <<USAGE
Usage:
  bash infra/chat/deploy_agentcore_runtime.sh [--tag TAG] [--repository NAME] [--runtime-name NAME] [--role-name NAME]

Defaults:
  --tag           ${TAG}
  --repository    ${REPOSITORY}
  --runtime-name  ${RUNTIME_RAW}
  --role-name     ${ROLE_NAME}

Environment:
  AWS_PROFILE, AWS_REGION, IMAGE_TAG
  HEZO_ECR_REPOSITORY
  HEZO_AGENTCORE_RUNTIME or HEZO_AGENTCORE_RUNTIME_NAME
  HEZO_AGENTCORE_ROLE_NAME
  HEZO_AGENTCORE_NETWORK_MODE, HEZO_AGENTCORE_PROTOCOL
  HEZO_AGENTCORE_IDLE_TIMEOUT, HEZO_AGENTCORE_MAX_LIFETIME
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag)
            TAG="${2:?--tag requires a value}"
            shift 2
            ;;
        --repository)
            REPOSITORY="${2:?--repository requires a value}"
            shift 2
            ;;
        --runtime-name)
            RUNTIME_RAW="${2:?--runtime-name requires a value}"
            shift 2
            ;;
        --role-name)
            ROLE_NAME="${2:?--role-name requires a value}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

need_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "[ERROR] Missing command: $1" >&2
        exit 1
    }
}

aws_cmd() {
    aws --profile "$PROFILE" --region "$REGION" "$@"
}

aws_global_cmd() {
    aws --profile "$PROFILE" "$@"
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

json_env() {
    python3 - "$REGION" <<'PYENV'
import json
import os
import sys

region = sys.argv[1]

keys = [
    "HEZO_ENV",
    "HEZO_AGENT_DYNAMODB_TABLE",
    "HEZO_CHAT_BUCKET",
    "HEZO_P2_MARKDOWNS_BUCKET",
    "HEZO_CONTRACTS_BUCKET",
    "HEZO_BEDROCK_MODEL_ID",
    "HEZO_BEDROCK_INFERENCE_PROFILE_ID",
    "HEZO_BEDROCK_GUARDRAIL_NAME",
    "HEZO_BEDROCK_GUARDRAIL_ID",
    "HEZO_BEDROCK_GUARDRAIL_ARN",
    "HEZO_BEDROCK_GUARDRAIL_VERSION",
]

env = {
    "AWS_DEFAULT_REGION": region,
    "AWS_REGION": region,
}

for key in keys:
    value = os.environ.get(key)
    if value:
        env[key] = value

model = env.get("HEZO_BEDROCK_INFERENCE_PROFILE_ID") or env.get("HEZO_BEDROCK_MODEL_ID")
if model:
    env["MODEL_ID"] = model

print(json.dumps(env, ensure_ascii=True, separators=(",", ":")))
PYENV
}

wait_for_runtime() {
    local runtime_id="$1"
    local max_attempts=$((WAIT_SECONDS / WAIT_INTERVAL))
    local attempt=0

    echo "[INFO] Runtime READY 대기: 최대 ${WAIT_SECONDS}s"

    while [ "$attempt" -le "$max_attempts" ]; do
        local status
        status="$(aws_cmd bedrock-agentcore-control get-agent-runtime \
            --agent-runtime-id "$runtime_id" \
            --query "status" \
            --output text 2>/dev/null || echo "UNKNOWN")"

        case "$status" in
            READY)
                echo "[OK] AgentCore Runtime READY: $runtime_id"
                return 0
                ;;
            FAILED|CREATE_FAILED|UPDATE_FAILED)
                echo "[ERROR] AgentCore Runtime failed: $status" >&2
                return 1
                ;;
            *)
                echo "[INFO] Runtime status: $status (${attempt}/${max_attempts})"
                sleep "$WAIT_INTERVAL"
                ;;
        esac

        attempt=$((attempt + 1))
    done

    echo "[WARN] Runtime이 제한 시간 안에 READY가 되지 않았습니다. 콘솔 또는 get-agent-runtime으로 상태를 확인하세요." >&2
    return 0
}

need_command aws
need_command python3

if ! aws_cmd bedrock-agentcore-control help >/dev/null 2>&1; then
    echo "[ERROR] aws bedrock-agentcore-control 명령을 사용할 수 없습니다. AWS CLI 버전을 확인하세요." >&2
    exit 1
fi

ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws_global_cmd sts get-caller-identity --query Account --output text)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPOSITORY}:${TAG}"
RUNTIME_NAME="$(normalize_runtime_name "$RUNTIME_RAW")"
ROLE_ARN="$(aws_global_cmd iam get-role --role-name "$ROLE_NAME" --query "Role.Arn" --output text)"
ENV_JSON="$(json_env)"
ARTIFACT_JSON="{\"containerConfiguration\":{\"containerUri\":\"${IMAGE_URI}\"}}"
NETWORK_JSON="{\"networkMode\":\"${NETWORK_MODE}\"}"
PROTOCOL_JSON="{\"serverProtocol\":\"${PROTOCOL}\"}"
LIFECYCLE_JSON="{\"idleRuntimeSessionTimeout\":${IDLE_TIMEOUT},\"maxLifetime\":${MAX_LIFETIME}}"

echo "[INFO] AWS profile:  $PROFILE"
echo "[INFO] AWS region:   $REGION"
echo "[INFO] ECR image:    $IMAGE_URI"
echo "[INFO] Runtime raw:  $RUNTIME_RAW"
echo "[INFO] Runtime name: $RUNTIME_NAME"
echo "[INFO] Role ARN:     $ROLE_ARN"

aws_cmd ecr describe-images \
    --repository-name "$REPOSITORY" \
    --image-ids imageTag="$TAG" >/dev/null
echo "[OK] ECR image exists: $IMAGE_URI"

existing_id="$(aws_cmd bedrock-agentcore-control list-agent-runtimes \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null || echo "None")"

if [ -z "$existing_id" ] || [ "$existing_id" = "None" ]; then
    echo "[INFO] AgentCore Runtime 생성 중: $RUNTIME_NAME"
    runtime_id="$(aws_cmd bedrock-agentcore-control create-agent-runtime \
        --agent-runtime-name "$RUNTIME_NAME" \
        --agent-runtime-artifact "$ARTIFACT_JSON" \
        --role-arn "$ROLE_ARN" \
        --network-configuration "$NETWORK_JSON" \
        --protocol-configuration "$PROTOCOL_JSON" \
        --lifecycle-configuration "$LIFECYCLE_JSON" \
        --environment-variables "$ENV_JSON" \
        --description "$DESCRIPTION" \
        --query "agentRuntimeId" \
        --output text)"
    echo "[OK] AgentCore Runtime created: $runtime_id"
else
    runtime_id="$existing_id"
    echo "[INFO] AgentCore Runtime 업데이트 중: $runtime_id"
    aws_cmd bedrock-agentcore-control update-agent-runtime \
        --agent-runtime-id "$runtime_id" \
        --agent-runtime-artifact "$ARTIFACT_JSON" \
        --role-arn "$ROLE_ARN" \
        --network-configuration "$NETWORK_JSON" \
        --protocol-configuration "$PROTOCOL_JSON" \
        --lifecycle-configuration "$LIFECYCLE_JSON" \
        --environment-variables "$ENV_JSON" >/dev/null
    echo "[OK] AgentCore Runtime updated: $runtime_id"
fi

wait_for_runtime "$runtime_id"

runtime_json="$(aws_cmd bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$runtime_id" \
    --output json)"

runtime_arn="$(printf '%s' "$runtime_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("agentRuntimeArn", ""))')"
runtime_status="$(printf '%s' "$runtime_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"

echo "[OK] AgentCore Runtime deploy result"
echo "RUNTIME_NAME=$RUNTIME_NAME"
echo "RUNTIME_ID=$runtime_id"
echo "RUNTIME_ARN=$runtime_arn"
echo "RUNTIME_STATUS=$runtime_status"
echo "IMAGE_URI=$IMAGE_URI"
