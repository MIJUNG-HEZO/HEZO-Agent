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
RUNTIME_RAW="${HEZO_AGENTCORE_RUNTIME_NAME:-${HEZO_AGENTCORE_RUNTIME:-hezo-chat-agent-dev}}"
ACTION="${HEZO_AGENTCORE_INVOKE_ACTION:-graph_smoke}"
SESSION_ID="${HEZO_AGENTCORE_INVOKE_SESSION_ID:-agentcore-smoke-session-001}"
SITE_ID="${HEZO_AGENTCORE_INVOKE_SITE_ID:-site_smoke_001}"
USER_ID="${HEZO_AGENTCORE_INVOKE_USER_ID:-user_smoke_001}"
STORAGE_MODE="${HEZO_AGENTCORE_INVOKE_STORAGE_MODE:-memory}"
ANSWER="${HEZO_AGENTCORE_INVOKE_ANSWER:-기장 대리와 종합소득세 신고를 핵심 서비스로 제공합니다.}"

usage() {
    cat <<USAGE
Usage:
  bash infra/chat/invoke_agentcore_runtime.sh [--action ACTION] [--session-id ID] [--storage-mode memory|aws]

Defaults:
  --action        ${ACTION}
  --session-id    ${SESSION_ID}
  --storage-mode  ${STORAGE_MODE}

Supported actions:
  graph_smoke, session_start, chat_turn

Environment:
  AWS_PROFILE, AWS_REGION
  HEZO_AGENTCORE_RUNTIME or HEZO_AGENTCORE_RUNTIME_NAME
  HEZO_AGENTCORE_INVOKE_ACTION, HEZO_AGENTCORE_INVOKE_SESSION_ID
  HEZO_AGENTCORE_INVOKE_SITE_ID, HEZO_AGENTCORE_INVOKE_USER_ID
  HEZO_AGENTCORE_INVOKE_STORAGE_MODE, HEZO_AGENTCORE_INVOKE_ANSWER
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --action)
            ACTION="${2:?--action requires a value}"
            shift 2
            ;;
        --session-id)
            SESSION_ID="${2:?--session-id requires a value}"
            shift 2
            ;;
        --storage-mode)
            STORAGE_MODE="${2:?--storage-mode requires a value}"
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

normalize_runtime_name() {
    local raw="$1"
    local normalized
    normalized="$(printf '%s' "$raw" | tr '-' '_' | tr -cd '[:alnum:]_')"

    if [[ ! "$normalized" =~ ^[A-Za-z] ]]; then
        normalized="hezo_${normalized}"
    fi

    printf '%.48s' "$normalized"
}

payload_json() {
    python3 - "$ACTION" "$SESSION_ID" "$SITE_ID" "$USER_ID" "$STORAGE_MODE" "$ANSWER" <<'PYPAYLOAD'
import json
import sys

action, session_id, site_id, user_id, storage_mode, answer = sys.argv[1:]

payload = {
    "sessionId": session_id,
    "inputText": answer if action == "chat_turn" else "",
    "sessionAttributes": {
        "action": action,
        "site_id": site_id,
        "user_id": user_id,
        "storage_mode": storage_mode,
        "category": "services",
        "domain": "tax_accounting",
        "domain_label": "세무/회계",
        "selected_template": "landing/13-tax-accounting",
    },
}

if action == "chat_turn":
    payload["sessionAttributes"].update(
        {
            "answered_slot": "core_services",
            "answer": answer,
            "known_answers": {"business_name": "한빛 세무회계"},
            "missing_slots": ["core_services", "contact_method"],
            "intent": "on_topic",
        }
    )

print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
PYPAYLOAD
}

print_response_summary() {
    local response_file="$1"
    python3 - "$response_file" <<'PYSUMMARY'
import json
import sys

path = sys.argv[1]
raw = open(path, "rb").read()
text = raw.decode("utf-8", errors="replace")

try:
    data = json.loads(text)
except json.JSONDecodeError:
    print("[WARN] Response is not a single JSON object. Raw response:")
    print(text)
    sys.exit(0)

print("[OK] AgentCore invoke response")
print(json.dumps(data, ensure_ascii=False, indent=2))

if "error" in data:
    sys.exit(1)

output = data.get("output")
state = data.get("sessionState") if isinstance(data.get("sessionState"), dict) else {}
if not output or not state.get("stage"):
    print("[ERROR] Response shape is missing output or sessionState.stage", file=sys.stderr)
    sys.exit(1)
PYSUMMARY
}

need_command aws
need_command python3

if ! aws_cmd bedrock-agentcore-control help >/dev/null 2>&1; then
    echo "[ERROR] aws bedrock-agentcore-control 명령을 사용할 수 없습니다." >&2
    exit 1
fi

if ! aws_cmd bedrock-agentcore help >/dev/null 2>&1; then
    echo "[ERROR] aws bedrock-agentcore 명령을 사용할 수 없습니다." >&2
    exit 1
fi

RUNTIME_NAME="$(normalize_runtime_name "$RUNTIME_RAW")"
RUNTIME_ID="$(aws_cmd bedrock-agentcore-control list-agent-runtimes \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null || echo "None")"

if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
    echo "[ERROR] AgentCore Runtime not found: $RUNTIME_NAME" >&2
    exit 1
fi

RUNTIME_JSON="$(aws_cmd bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --output json)"
RUNTIME_ARN="$(printf '%s' "$RUNTIME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("agentRuntimeArn", ""))')"
RUNTIME_STATUS="$(printf '%s' "$RUNTIME_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"

if [ "$RUNTIME_STATUS" != "READY" ]; then
    echo "[ERROR] AgentCore Runtime is not READY: $RUNTIME_NAME ($RUNTIME_ID/$RUNTIME_STATUS)" >&2
    exit 1
fi

PAYLOAD="$(payload_json)"
RESPONSE_FILE="$(mktemp)"
trap 'rm -f "$RESPONSE_FILE"' EXIT

echo "[INFO] AWS profile:  $PROFILE"
echo "[INFO] AWS region:   $REGION"
echo "[INFO] Runtime name: $RUNTIME_NAME"
echo "[INFO] Runtime ID:   $RUNTIME_ID"
echo "[INFO] Runtime ARN:  $RUNTIME_ARN"
echo "[INFO] Action:       $ACTION"
echo "[INFO] Session ID:   $SESSION_ID"

aws_cmd bedrock-agentcore invoke-agent-runtime \
    --agent-runtime-arn "$RUNTIME_ARN" \
    --payload "$PAYLOAD" \
    --content-type "application/json" \
    --accept "application/json" \
    --cli-binary-format raw-in-base64-out \
    "$RESPONSE_FILE" >/dev/null

print_response_summary "$RESPONSE_FILE"
