#!/usr/bin/env bash
# P3 ECS Fargate 빌드 워커 배포 스크립트
# 용도: ECR 이미지 빌드·푸시 + ECS 클러스터·서비스·ALB 프로비저닝 + SSM 등록
# 사용법:
#   bash infra/build/deploy_ecs_service.sh            # 전체 (빌드 포함)
#   bash infra/build/deploy_ecs_service.sh --skip-build  # 이미지 빌드 생략 (기존 이미지 재사용)

set -euo pipefail
# Windows Git Bash에서 /path 형태가 Windows 경로로 변환되는 것을 방지
export MSYS_NO_PATHCONV=1

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
REGION="${AWS_REGION:-ap-northeast-2}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

ECR_REPO="hezo-build-worker"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_URI="${REGISTRY}/${ECR_REPO}:${IMAGE_TAG}"

CLUSTER="hezo-cluster"
SERVICE="hezo-build-worker-svc"
TASK_FAMILY="hezo-build-worker"
CONTAINER_NAME="build-worker"
CONTAINER_PORT=8080

ALB_NAME="hezo-build-worker-alb"
TG_NAME="hezo-build-worker-tg"
SG_NAME="hezo-build-worker-sg"
ALB_SG_NAME="hezo-build-alb-sg"

# Default VPC 사용 (HEZO 내부 공유 인프라)
VPC_ID="vpc-0df8fb75e35e9968e"
SUBNETS="subnet-04b2e260d68ca2f93,subnet-0e805053510160ef7,subnet-021c8757ce9e7addd"

ECS_EXECUTION_ROLE="hezo-ecs-execution-role"
ECS_TASK_ROLE="hezo-ecs-task-role"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SKIP_BUILD=false
for arg in "$@"; do
  [ "$arg" = "--skip-build" ] && SKIP_BUILD=true
done

log()  { echo "[INFO]  $*" >&2; }
ok()   { echo "[OK]    $*" >&2; }
warn() { echo "[WARN]  $*" >&2; }
err()  { echo "[ERROR] $*" >&2; }

aws_cmd() { aws --region "$REGION" "$@"; }

# ──────────────────────────────────────────────
# 1. ECR 레포 생성
# ──────────────────────────────────────────────
log "ECR 레포 확인: ${ECR_REPO}"
if ! aws_cmd ecr describe-repositories --repository-names "${ECR_REPO}" >/dev/null 2>&1; then
  aws_cmd ecr create-repository \
    --repository-name "${ECR_REPO}" \
    --image-scanning-configuration scanOnPush=true \
    --tags Key=hezo:component,Value=p3-build-worker >/dev/null
  ok "ECR 레포 생성: ${ECR_REPO}"
else
  ok "ECR 레포 이미 존재: ${ECR_REPO}"
fi

# ──────────────────────────────────────────────
# 2. 이미지 빌드 & 푸시
# ──────────────────────────────────────────────
if [ "$SKIP_BUILD" = false ]; then
  log "ECR 로그인"
  aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "${REGISTRY}"

  log "Docker 이미지 빌드 (arm64): ${IMAGE_URI}"
  docker buildx build \
    --platform linux/arm64 \
    -f "${REPO_ROOT}/agents/build/Dockerfile" \
    -t "${IMAGE_URI}" \
    --push \
    "${REPO_ROOT}"
  ok "이미지 빌드 & 푸시 완료: ${IMAGE_URI}"
else
  warn "--skip-build: 기존 이미지 재사용"
fi

# ──────────────────────────────────────────────
# 3. IAM 역할 생성
# ──────────────────────────────────────────────
create_role_if_not_exists() {
  local role="$1" assume_policy="$2"
  if ! aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    aws iam create-role \
      --role-name "$role" \
      --assume-role-policy-document "$assume_policy" \
      --tags Key=hezo:component,Value=p3-build-worker >/dev/null
    log "IAM 역할 생성: $role"
  else
    log "IAM 역할 이미 존재: $role"
  fi
}

ECS_ASSUME='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

create_role_if_not_exists "${ECS_EXECUTION_ROLE}" "${ECS_ASSUME}"
aws iam attach-role-policy \
  --role-name "${ECS_EXECUTION_ROLE}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy 2>/dev/null || true
# ECR 이미지 풀 + CloudWatch Logs 쓰기
aws iam put-role-policy \
  --role-name "${ECS_EXECUTION_ROLE}" \
  --policy-name HezoECSExecutionExtra \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"ecr:GetAuthorizationToken\",\"ecr:BatchGetImage\",\"ecr:GetDownloadUrlForLayer\"],\"Resource\":\"*\"}
    ]}" 2>/dev/null || true
ok "IAM execution role 준비: ${ECS_EXECUTION_ROLE}"

create_role_if_not_exists "${ECS_TASK_ROLE}" "${ECS_ASSUME}"
aws iam put-role-policy \
  --role-name "${ECS_TASK_ROLE}" \
  --policy-name HezoECSTaskS3 \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:HeadObject\"],\"Resource\":\"arn:aws:s3:::hezo-artifacts/*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":\"arn:aws:s3:::hezo-sites/*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],\"Resource\":[\"arn:aws:s3:::hezo-artifacts\",\"arn:aws:s3:::hezo-sites\"]},
      {\"Effect\":\"Allow\",\"Action\":[\"logs:CreateLogGroup\",\"logs:CreateLogStream\",\"logs:PutLogEvents\"],\"Resource\":\"*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"cloudwatch:PutMetricData\"],\"Resource\":\"*\"}
    ]}" 2>/dev/null || true
ok "IAM task role 준비: ${ECS_TASK_ROLE}"

# ──────────────────────────────────────────────
# 4. CloudWatch 로그 그룹
# ──────────────────────────────────────────────
aws_cmd logs create-log-group --log-group-name /hezo/build-worker 2>/dev/null || true
aws_cmd logs put-retention-policy --log-group-name /hezo/build-worker --retention-in-days 30 2>/dev/null || true
ok "CloudWatch 로그 그룹: /hezo/build-worker"

# ──────────────────────────────────────────────
# 5. ECS 클러스터 생성 (없으면)
# ──────────────────────────────────────────────
CLUSTER_STATUS="$(aws_cmd ecs describe-clusters --clusters "${CLUSTER}" \
  --query "clusters[0].status" --output text 2>/dev/null || echo "MISSING")"

if [ "$CLUSTER_STATUS" = "INACTIVE" ] || [ "$CLUSTER_STATUS" = "MISSING" ]; then
  aws_cmd ecs create-cluster \
    --cluster-name "${CLUSTER}" \
    --capacity-providers FARGATE FARGATE_SPOT \
    --tags key=hezo:component,value=shared-infra >/dev/null
  ok "ECS 클러스터 생성: ${CLUSTER}"
else
  ok "ECS 클러스터 이미 ACTIVE: ${CLUSTER}"
fi

# ──────────────────────────────────────────────
# 6. 보안그룹 (ALB용 + ECS 태스크용)
# ──────────────────────────────────────────────
get_or_create_sg() {
  local name="$1" desc="$2"
  local sg_id
  sg_id="$(aws_cmd ec2 describe-security-groups \
    --filters "Name=group-name,Values=${name}" "Name=vpc-id,Values=${VPC_ID}" \
    --query "SecurityGroups[0].GroupId" --output text 2>/dev/null)"
  if [ "$sg_id" = "None" ] || [ -z "$sg_id" ]; then
    sg_id="$(aws_cmd ec2 create-security-group \
      --group-name "$name" --description "$desc" --vpc-id "${VPC_ID}" \
      --query "GroupId" --output text)"
    log "SG 생성: ${name} → ${sg_id}"
  else
    log "SG 이미 존재: ${name} → ${sg_id}"
  fi
  echo "$sg_id"
}

ALB_SG_ID="$(get_or_create_sg "${ALB_SG_NAME}" "HEZO P3 Build Worker ALB")"
ECS_SG_ID="$(get_or_create_sg "${SG_NAME}" "HEZO P3 Build Worker ECS Tasks")"

# ALB SG: 80 인바운드 허용
aws_cmd ec2 authorize-security-group-ingress \
  --group-id "${ALB_SG_ID}" --protocol tcp --port 80 --cidr 0.0.0.0/0 2>/dev/null || true

# ECS SG: ALB에서만 8080 허용
aws_cmd ec2 authorize-security-group-ingress \
  --group-id "${ECS_SG_ID}" --protocol tcp --port 8080 \
  --source-group "${ALB_SG_ID}" 2>/dev/null || true

ok "보안그룹 준비: ALB=${ALB_SG_ID}, ECS=${ECS_SG_ID}"

# ──────────────────────────────────────────────
# 7. ALB 생성
# ──────────────────────────────────────────────
SUBNET_LIST="${SUBNETS//,/ }"

ALB_ARN="$(aws_cmd elbv2 describe-load-balancers \
  --names "${ALB_NAME}" --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || echo "None")"

if [ "$ALB_ARN" = "None" ] || [ -z "$ALB_ARN" ]; then
  ALB_ARN="$(aws_cmd elbv2 create-load-balancer \
    --name "${ALB_NAME}" \
    --subnets ${SUBNET_LIST} \
    --security-groups "${ALB_SG_ID}" \
    --scheme internet-facing \
    --type application \
    --ip-address-type ipv4 \
    --tags Key=hezo:component,Value=p3-build-worker \
    --query "LoadBalancers[0].LoadBalancerArn" --output text)"
  ok "ALB 생성: ${ALB_ARN}"
else
  ok "ALB 이미 존재: ${ALB_ARN}"
fi

ALB_DNS="$(aws_cmd elbv2 describe-load-balancers \
  --load-balancer-arns "${ALB_ARN}" \
  --query "LoadBalancers[0].DNSName" --output text)"

# ──────────────────────────────────────────────
# 8. 타겟그룹 생성
# ──────────────────────────────────────────────
TG_ARN="$(aws_cmd elbv2 describe-target-groups \
  --names "${TG_NAME}" --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || echo "None")"

if [ "$TG_ARN" = "None" ] || [ -z "$TG_ARN" ]; then
  TG_ARN="$(aws_cmd elbv2 create-target-group \
    --name "${TG_NAME}" \
    --protocol HTTP \
    --port "${CONTAINER_PORT}" \
    --vpc-id "${VPC_ID}" \
    --target-type ip \
    --health-check-path /ping \
    --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 \
    --unhealthy-threshold-count 3 \
    --query "TargetGroups[0].TargetGroupArn" --output text)"
  ok "타겟그룹 생성: ${TG_ARN}"
else
  ok "타겟그룹 이미 존재: ${TG_ARN}"
fi

# ──────────────────────────────────────────────
# 9. 리스너 생성 (HTTP:80)
# ──────────────────────────────────────────────
LISTENER_ARN="$(aws_cmd elbv2 describe-listeners \
  --load-balancer-arn "${ALB_ARN}" \
  --query "Listeners[?Port==\`80\`].ListenerArn | [0]" --output text 2>/dev/null || echo "None")"

if [ "$LISTENER_ARN" = "None" ] || [ -z "$LISTENER_ARN" ]; then
  aws_cmd elbv2 create-listener \
    --load-balancer-arn "${ALB_ARN}" \
    --protocol HTTP --port 80 \
    --default-actions Type=forward,TargetGroupArn="${TG_ARN}" >/dev/null
  ok "리스너 생성: HTTP:80 → ${TG_ARN}"
else
  ok "리스너 이미 존재"
fi

# ──────────────────────────────────────────────
# 10. 태스크 정의 등록
# ──────────────────────────────────────────────
log "태스크 정의 등록: ${TASK_FAMILY}"
TASK_DEF_JSON="$(cat "${SCRIPT_DIR}/task-definition.json")"
TASK_DEF_ARN="$(aws_cmd ecs register-task-definition \
  --cli-input-json "${TASK_DEF_JSON}" \
  --query "taskDefinition.taskDefinitionArn" --output text)"
ok "태스크 정의 등록: ${TASK_DEF_ARN}"

# ──────────────────────────────────────────────
# 11. ECS 서비스 생성 or 업데이트
# ──────────────────────────────────────────────
NETWORK_CONFIG="awsvpcConfiguration={subnets=[$(echo ${SUBNETS} | tr ',' ',')],securityGroups=[${ECS_SG_ID}],assignPublicIp=ENABLED}"

SERVICE_STATUS="$(aws_cmd ecs describe-services \
  --cluster "${CLUSTER}" --services "${SERVICE}" \
  --query "services[0].status" --output text 2>/dev/null || echo "MISSING")"

if [ "$SERVICE_STATUS" = "ACTIVE" ]; then
  log "ECS 서비스 업데이트: ${SERVICE}"
  aws_cmd ecs update-service \
    --cluster "${CLUSTER}" \
    --service "${SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --force-new-deployment >/dev/null
  ok "ECS 서비스 업데이트 완료"
else
  log "ECS 서비스 생성: ${SERVICE}"
  aws_cmd ecs create-service \
    --cluster "${CLUSTER}" \
    --service-name "${SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "${NETWORK_CONFIG}" \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=${CONTAINER_NAME},containerPort=${CONTAINER_PORT}" \
    --health-check-grace-period-seconds 60 \
    --tags key=hezo:component,value=p3-build-worker \
    --enable-execute-command >/dev/null
  ok "ECS 서비스 생성 완료"
fi

# ──────────────────────────────────────────────
# 12. 서비스 Stable 대기
# ──────────────────────────────────────────────
log "ECS 서비스 stable 대기 (최대 5분)..."
aws_cmd ecs wait services-stable \
  --cluster "${CLUSTER}" \
  --services "${SERVICE}" || warn "Stable 대기 타임아웃 — 서비스 콘솔에서 상태 확인 필요"
ok "ECS 서비스 STABLE"

# ──────────────────────────────────────────────
# 13. SSM 파라미터 등록
# ──────────────────────────────────────────────
BUILD_ENDPOINT="http://${ALB_DNS}"
aws_cmd ssm put-parameter \
  --name "hezo-build-agent-endpoint" \
  --value "${BUILD_ENDPOINT}" \
  --type String \
  --overwrite >/dev/null
ok "SSM 등록: hezo-build-agent-endpoint = ${BUILD_ENDPOINT}"

# ──────────────────────────────────────────────
# 완료 요약
# ──────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo " P3 ECS Fargate 빌드 워커 배포 완료"
echo "═══════════════════════════════════════"
echo " 클러스터   : ${CLUSTER}"
echo " 서비스     : ${SERVICE}"
echo " 태스크 정의: ${TASK_FAMILY}"
echo " ALB        : ${ALB_DNS}"
echo " 엔드포인트 : ${BUILD_ENDPOINT}"
echo " SSM 키     : hezo-build-agent-endpoint"
echo ""
echo " 헬스체크   : curl ${BUILD_ENDPOINT}/ping"
echo " 빌드 호출  : curl -X POST ${BUILD_ENDPOINT}/invocations -d '{\"sessionAttributes\":{\"site_id\":\"site_xxx\",\"mode\":\"publish\"}}'"
echo "═══════════════════════════════════════"
