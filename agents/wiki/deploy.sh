#!/usr/bin/env bash
# HEZO 위키 Lambda 4종 재배포 (crawl/generate/pick/reinforce)
# - 공용 컨테이너 이미지 1개를 빌드 → ECR push → 4개 함수 update-function-code
# - 핸들러(ImageConfig.Command)는 기존 설정 유지됨 (update-function-code는 안 건드림)
# 사용: bash agents/wiki/deploy.sh
set -euo pipefail

REGION="ap-northeast-2"
ACCOUNT="492554570964"
REPO="hezo-wiki-lambda"
ECR="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"
TAG="latest"
FUNCS=(hezo-wiki-crawl hezo-wiki-generate hezo-wiki-pick hezo-wiki-reinforce)

# Dockerfile은 repo 루트를 빌드 컨텍스트로 씀 (agents/, libs/ 복사하므로)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "── 1. ECR 로그인 ──"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

echo "── 2. 이미지 빌드 (linux/amd64, provenance off) ──"
docker build --platform linux/amd64 --provenance=false \
  -f agents/wiki/Dockerfile -t "${ECR}:${TAG}" .

echo "── 3. ECR push ──"
docker push "${ECR}:${TAG}"
DIGEST="$(aws ecr describe-images --region "$REGION" --repository-name "$REPO" \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)"
echo "  digest: $DIGEST"

echo "── 4. Lambda 4종 새 이미지로 업데이트 ──"
for fn in "${FUNCS[@]}"; do
  aws lambda update-function-code --region "$REGION" --function-name "$fn" \
    --image-uri "${ECR}@${DIGEST}" --query "[FunctionName,LastUpdateStatus]" --output text 2>&1
done

echo "── 5. 업데이트 완료 대기 ──"
for fn in "${FUNCS[@]}"; do
  aws lambda wait function-updated --region "$REGION" --function-name "$fn" && echo "  ✅ $fn"
done
echo "✅ 위키 Lambda 4종 재배포 완료"
