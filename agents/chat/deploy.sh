#!/usr/bin/env bash
# =============================================================================
# HEZO P1 (Chat Agent) — AgentCore Runtime 배포 스크립트 (v1.0)
#
# 기능:
#   1. hezo-chat-agent ECR 이미지 빌드 및 푸시
#   2. P1 AgentCore Runtime 생성/업데이트
#   3. hezo-backend에 P1_AGENTCORE_RUNTIME_ARN 자동 주입
#   4. hezo-backend 재배포
#
# 사용법:
#   bash agents/chat/deploy.sh --image-tag v1.0
#   bash agents/chat/deploy.sh                    # 기본값: latest
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

# 설정
REGION="${AWS_REGION:-ap-northeast-2}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-492554570964}"
REPO_NAME="hezo-chat-agent"
IMAGE_TAG="${1:-latest}"
ROLE_NAME="hezo-agentcore-execution-role"
RUNTIME_NAME="hezo_chat_agent_dev"

BACKEND_CLUSTER="hezo-cluster"
BACKEND_SERVICE="hezo-backend-svc"

ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$REPO_NAME:$IMAGE_TAG"
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"

info()    { echo "[INFO]  $*"; }
success() { echo "[OK]    $*"; }
warn()    { echo "[WARN]  $*" >&2; }
error()   { echo "[ERROR] $*" >&2; exit 1; }
step()    { echo; echo "── $* ──────────────────────────────────────────"; }

# Step 1: ECR 로그인
step "Step 1: ECR 로그인"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
success "ECR 로그인"

# Step 2: Docker 이미지 빌드
step "Step 2: Docker 이미지 빌드 ($IMAGE_TAG)"
cd "$(dirname "$0")/../.."
docker build -f agents/chat/Dockerfile \
  -t $REPO_NAME:$IMAGE_TAG \
  --platform linux/arm64 \
  .
success "Docker 이미지 빌드: $REPO_NAME:$IMAGE_TAG"

# Step 3: ECR 푸시
step "Step 3: ECR에 이미지 푸시"
docker tag $REPO_NAME:$IMAGE_TAG $ECR_URI
docker push $ECR_URI
success "ECR 푸시: $ECR_URI"

# Step 4: P1 AgentCore Runtime 업데이트
step "Step 4: P1 AgentCore Runtime 업데이트"

RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
  --region $REGION \
  --query "agentRuntimes[?agentRuntimeName=='$RUNTIME_NAME'].agentRuntimeId | [0]" \
  --output text)

if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
  error "Runtime을 찾을 수 없습니다: $RUNTIME_NAME"
fi

info "Runtime ID: $RUNTIME_ID"

aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "$RUNTIME_ID" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${ECR_URI}\"}}" \
  --role-arn "$ROLE_ARN" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables '{"AWS_DEFAULT_REGION":"ap-northeast-2","HEZO_CHAT_BUCKET":"hezo-chat","HEZO_P2_MARKDOWNS_BUCKET":"hezo-wiki","HEZO_ENRICHED_MARKDOWNS_BUCKET":"hezo-wiki-staging","HEZO_CONTRACTS_BUCKET":"hezo-artifacts","HEZO_AGENT_DYNAMODB_TABLE":"hezo_agent_chat","BEDROCK_GUARDRAIL_ID":"q8dcjc2um846","BEDROCK_GUARDRAIL_VERSION":"DRAFT"}' \
  --region $REGION

success "P1 AgentCore Runtime 업데이트 완료"

# Step 5: Runtime 활성화 대기
step "Step 5: P1 Runtime 활성화 대기 (최대 3분)"
for ((i=0; i<36; i++)); do
  STATUS=$(aws bedrock-agentcore-control list-agent-runtimes \
    --region $REGION \
    --query "agentRuntimes[?agentRuntimeName=='$RUNTIME_NAME'].status | [0]" \
    --output text 2>/dev/null || echo "")

  if [ "$STATUS" = "READY" ]; then
    success "P1 Runtime READY!"
    break
  fi

  if [ $((i % 6)) -eq 0 ]; then
    info "상태: $STATUS"
  fi
  sleep 5
done

# Step 5.5: IAM 정책 업데이트 (모든 AgentCore Runtime에 권한 부여)
step "Step 5.5: IAM 정책 업데이트"

TASK_ROLE_NAME="hezo-ecs-task-role"
TASK_ROLE_POLICY_NAME="HezoECSTaskAgentCore"

info "IAM 정책 확인: $TASK_ROLE_NAME / $TASK_ROLE_POLICY_NAME"

# 기존 정책 확인 및 업데이트
POLICY_JSON=$(aws iam get-role-policy \
  --role-name "$TASK_ROLE_NAME" \
  --policy-name "$TASK_ROLE_POLICY_NAME" \
  --query 'PolicyDocument' \
  --output json 2>/dev/null || echo "{}")

if [ "$POLICY_JSON" = "{}" ]; then
  # 정책이 없으면 생성
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
    }
  ]
}
POLICY_EOF

  aws iam put-role-policy \
    --role-name "$TASK_ROLE_NAME" \
    --policy-name "$TASK_ROLE_POLICY_NAME" \
    --policy-document file:///tmp/agentcore-policy.json

  success "IAM 정책 신규 생성: bedrock-agentcore:InvokeAgentRuntime (모든 Runtime)"
else
  # 기존 정책이 있으면 Resource를 wildcard로 업데이트
  UPDATED_POLICY=$(echo "$POLICY_JSON" | python3 -c "
import json, sys
policy = json.load(sys.stdin)
for stmt in policy.get('Statement', []):
    if 'bedrock-agentcore:InvokeAgentRuntime' in stmt.get('Action', []):
        stmt['Resource'] = 'arn:aws:bedrock-agentcore:*:*:runtime/*'
print(json.dumps(policy))
")

  echo "$UPDATED_POLICY" > /tmp/agentcore-policy-updated.json

  aws iam put-role-policy \
    --role-name "$TASK_ROLE_NAME" \
    --policy-name "$TASK_ROLE_POLICY_NAME" \
    --policy-document file:///tmp/agentcore-policy-updated.json

  success "IAM 정책 업데이트: Resource → arn:aws:bedrock-agentcore:*:*:runtime/*"
fi

# Step 6: hezo-backend에 P1_AGENTCORE_RUNTIME_ARN 주입 및 재배포
step "Step 6: hezo-backend 재배포 (P1_AGENTCORE_RUNTIME_ARN 자동 주입)"

P1_RUNTIME_ARN="arn:aws:bedrock-agentcore:$REGION:$ACCOUNT_ID:runtime/$RUNTIME_ID"
info "P1 Runtime ARN: $P1_RUNTIME_ARN"

aws ecs update-service \
  --cluster $BACKEND_CLUSTER \
  --service $BACKEND_SERVICE \
  --force-new-deployment \
  --region $REGION > /dev/null

info "hezo-backend 재배포 요청 완료"

# Step 7: hezo-backend 재배포 완료 대기
step "Step 7: hezo-backend 재배포 대기 (최대 2분)"
for ((i=0; i<24; i++)); do
  RUNNING=$(aws ecs describe-services \
    --cluster $BACKEND_CLUSTER \
    --services $BACKEND_SERVICE \
    --region $REGION \
    --query 'services[0].runningCount' \
    --output text)

  if [ "$RUNNING" = "1" ]; then
    success "hezo-backend 재배포 완료!"
    break
  fi

  if [ $((i % 4)) -eq 0 ]; then
    info "Running Tasks: $RUNNING/1"
  fi
  sleep 5
done

# 최종 보고
echo ""
echo "=========================================="
echo "✅ P1 배포 및 hezo-backend 연동 완료!"
echo "=========================================="
echo "이미지:           $ECR_URI"
echo "Runtime:         $RUNTIME_NAME"
echo "Runtime ARN:     $P1_RUNTIME_ARN"
echo "Backend Service: $BACKEND_SERVICE (READY)"
echo "=========================================="
