#!/usr/bin/env bash
# =============================================================================
# HEZO AgentCore IAM Policy 자동 업데이트
#
# 모든 에이전트 배포 후 실행:
#   bash update-agent-iam-policy.sh
#
# 기능:
#   - hezo-ecs-task-role에 bedrock-agentcore:InvokeAgentRuntime 권한 추가
#   - Resource를 wildcard로 설정 (모든 Runtime에 권한 부여)
# =============================================================================

set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-492554570964}"
TASK_ROLE_NAME="hezo-ecs-task-role"
TASK_ROLE_POLICY_NAME="HezoECSTaskAgentCore"

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
error()   { echo "[ERROR] $*" >&2; exit 1; }

# ─────────────────────────────────────────────────────────────────────────
# IAM 정책 확인 및 업데이트
# ─────────────────────────────────────────────────────────────────────────

info "IAM 정책 확인: $TASK_ROLE_NAME / $TASK_ROLE_POLICY_NAME"

# 기존 정책 확인
POLICY_JSON=$(aws iam get-role-policy \
  --role-name "$TASK_ROLE_NAME" \
  --policy-name "$TASK_ROLE_POLICY_NAME" \
  --query 'PolicyDocument' \
  --output json 2>/dev/null || echo "{}")

if [ "$POLICY_JSON" = "{}" ]; then
  # 정책이 없으면 신규 생성
  cat > /tmp/agentcore-policy.json << 'POLICY_EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime"
      ],
      "Resource": "arn:aws:bedrock-agentcore:*:*:runtime/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:*:*:foundation-model/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-runtime:InvokeModel",
        "bedrock-runtime:InvokeModelWithResponseStream"
      ],
      "Resource": "arn:aws:bedrock:*:*:foundation-model/*"
    }
  ]
}
POLICY_EOF

  aws iam put-role-policy \
    --role-name "$TASK_ROLE_NAME" \
    --policy-name "$TASK_ROLE_POLICY_NAME" \
    --policy-document file:///tmp/agentcore-policy.json

  success "✓ IAM 정책 신규 생성"
else
  # 기존 정책 업데이트 (Resource를 wildcard로)
  UPDATED_POLICY=$(echo "$POLICY_JSON" | python3 << 'PYTHON_EOF'
import json, sys
policy = json.load(sys.stdin)
for stmt in policy.get('Statement', []):
    actions = stmt.get('Action', [])
    if isinstance(actions, str):
        actions = [actions]
    if 'bedrock-agentcore:InvokeAgentRuntime' in actions:
        stmt['Resource'] = 'arn:aws:bedrock-agentcore:*:*:runtime/*'
print(json.dumps(policy))
PYTHON_EOF
)

  echo "$UPDATED_POLICY" > /tmp/agentcore-policy-updated.json

  aws iam put-role-policy \
    --role-name "$TASK_ROLE_NAME" \
    --policy-name "$TASK_ROLE_POLICY_NAME" \
    --policy-document file:///tmp/agentcore-policy-updated.json

  success "✓ IAM 정책 업데이트 완료"
fi

# 확인
info "IAM 정책 최종 확인..."
aws iam get-role-policy \
  --role-name "$TASK_ROLE_NAME" \
  --policy-name "$TASK_ROLE_POLICY_NAME" \
  --query 'PolicyDocument.Statement[?Action[0]==`bedrock-agentcore:InvokeAgentRuntime`].Resource' \
  --output text | grep -q "runtime/\*" && \
  success "✓ bedrock-agentcore:InvokeAgentRuntime → arn:aws:bedrock-agentcore:*:*:runtime/*" || \
  warn "정책 확인 실패"

echo ""
echo "=========================================="
echo "✅ IAM 정책 업데이트 완료"
echo "=========================================="
echo "Role:   $TASK_ROLE_NAME"
echo "Policy: $TASK_ROLE_POLICY_NAME"
echo "=========================================="
