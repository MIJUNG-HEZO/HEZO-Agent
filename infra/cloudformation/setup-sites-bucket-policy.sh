#!/usr/bin/env bash
# =============================================================================
# hezo-sites 버킷 정책 1회 설정 스크립트
#
# 목적: 계정 내 모든 CloudFront 배포(고객사별)가 hezo-sites 버킷을 OAC로 접근 가능하도록
#       버킷 정책에 wildcard statement 추가.
#       이 스크립트는 최초 1회만 실행. 이후 고객 스택이 몇 개가 생겨도 재실행 불필요.
#
# 사용법: bash infra/cloudformation/setup-sites-bucket-policy.sh
# =============================================================================
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
PROFILE="${AWS_PROFILE:-rapa-cm1-21}"
BUCKET="hezo-sites"

aws_cmd() { aws --profile "$PROFILE" "$@"; }

ACCOUNT_ID=$(aws_cmd sts get-caller-identity --query Account --output text)
echo "[INFO] AWS 계정: $ACCOUNT_ID"
echo "[INFO] 버킷: $BUCKET"

POLICY=$(python3 - "$ACCOUNT_ID" "$BUCKET" <<'PYEOF'
import json, sys
account, bucket = sys.argv[1], sys.argv[2]
policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowHEZOCloudFrontOAC",
            "Effect": "Allow",
            "Principal": {"Service": "cloudfront.amazonaws.com"},
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket}/*",
            "Condition": {
                "StringLike": {
                    "AWS:SourceArn": f"arn:aws:cloudfront::{account}:distribution/*"
                }
            }
        }
    ]
}
print(json.dumps(policy))
PYEOF
)

aws_cmd s3api put-bucket-policy \
    --bucket "$BUCKET" \
    --policy "$POLICY" \
    --region "$REGION"

echo "[OK] hezo-sites 버킷 정책 설정 완료"
echo "     이제 계정 내 모든 CloudFront(고객사별) 배포가 OAC로 hezo-sites에 접근 가능합니다."
