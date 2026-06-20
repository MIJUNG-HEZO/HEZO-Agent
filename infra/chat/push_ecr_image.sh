#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
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
PLATFORM="${DOCKER_PLATFORM:-linux/arm64}"
DOCKERFILE="$REPO_ROOT/agents/chat/Dockerfile"

usage() {
    cat <<USAGE
Usage:
  bash infra/chat/push_ecr_image.sh [--tag TAG] [--repository NAME] [--platform PLATFORM]

Defaults:
  --tag         ${TAG}
  --repository  ${REPOSITORY}
  --platform    ${PLATFORM}

Environment:
  AWS_PROFILE, AWS_REGION, HEZO_ECR_REPOSITORY, IMAGE_TAG, DOCKER_PLATFORM
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
        --platform)
            PLATFORM="${2:?--platform requires a value}"
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

need_command aws
need_command docker

if [ ! -f "$DOCKERFILE" ]; then
    echo "[ERROR] Dockerfile not found: $DOCKERFILE" >&2
    exit 1
fi

ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws --profile "$PROFILE" sts get-caller-identity --query Account --output text)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE_URI="${REGISTRY}/${REPOSITORY}:${TAG}"

echo "[INFO] AWS profile: $PROFILE"
echo "[INFO] AWS region:  $REGION"
echo "[INFO] ECR repo:    $REPOSITORY"
echo "[INFO] Image tag:   $TAG"
echo "[INFO] Platform:    $PLATFORM"

aws_cmd ecr describe-repositories --repository-names "$REPOSITORY" >/dev/null
echo "[OK] ECR repository exists: $REPOSITORY"

aws_cmd ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
echo "[OK] Docker logged in to ECR: $REGISTRY"

docker buildx build \
    --platform "$PLATFORM" \
    -f "$DOCKERFILE" \
    -t "$IMAGE_URI" \
    --push \
    "$REPO_ROOT"

echo "[OK] Pushed chat agent image"
echo "IMAGE_URI=$IMAGE_URI"
