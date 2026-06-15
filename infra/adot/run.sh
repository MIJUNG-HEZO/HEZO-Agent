#!/usr/bin/env bash
# ADOT Collector 로컬 실행 = "트럭 시동 버튼"
# 실행:  bash infra/adot/run.sh
#
# 보안: AWS 키를 여기 직접 안 박는다. 호스트의 ~/.aws 를 읽기전용으로 마운트해서 쓴다.
#       (레포 규칙: credential 커밋 금지 → 키는 파일에 안 들어감)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker run --rm \
  -p 4317:4317 \
  -v "$SCRIPT_DIR/config.yaml:/etc/otel/config.yaml:ro" \
  -v "$HOME/.aws:/aws-config:ro" \
  -e AWS_SHARED_CREDENTIALS_FILE=/aws-config/credentials \
  -e AWS_CONFIG_FILE=/aws-config/config \
  -e AWS_REGION=ap-northeast-2 \
  public.ecr.aws/aws-observability/aws-otel-collector \
  --config /etc/otel/config.yaml
