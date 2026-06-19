#!/usr/bin/env bash
# =============================================================================
# HEZO 위키(P2) S3 버킷 생성 스크립트 — 2버킷 (영구 / 임시)
# 버전드가 버킷 단위라 영구(industries, 버전 필요)↔임시(raw·pending, 불필요)를 분리한다.
# 팀 인프라 패턴(infra/aws_setup.sh의 create_s3_bucket)을 따른다. 멱등(재실행 안전).
#
#   🟢 WIKI_BUCKET(hezo-wiki, 버전드 ON)      → industries/ 영구 위키. P1이 읽음
#        Lifecycle: noncurrent(옛) 버전 30일 후 만료 (현재 버전 영구)
#   🟠 STAGING_BUCKET(hezo-wiki-staging, 버전드 OFF) → raw/·pending/ 임시
#        Lifecycle: raw/ 7일 후 삭제 · pending/ 3일 후 삭제
#
# 사용법:
#   bash agents/wiki/setup_bucket.sh
#
# 환경변수(선택):
#   WIKI_BUCKET     영구 버킷명 (기본: hezo-wiki)
#   STAGING_BUCKET  임시 버킷명 (기본: hezo-wiki-staging)
#   AWS_REGION      리전        (기본: ap-northeast-2)
#   AWS_PROFILE     프로필      (기본: hezo-p2)
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash에서 / 인자를 Windows 경로로 바꾸는 것 방지

WIKI_BUCKET="${WIKI_BUCKET:-hezo-wiki}"
STAGING_BUCKET="${STAGING_BUCKET:-hezo-wiki-staging}"
REGION="${AWS_REGION:-ap-northeast-2}"
AWS_PROFILE="${AWS_PROFILE:-hezo-p2}"
AWS_PROFILE_OPT="--profile ${AWS_PROFILE}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

command -v aws &>/dev/null || die "AWS CLI가 설치되어 있지 않습니다."
aws sts get-caller-identity ${AWS_PROFILE_OPT} --output text > /dev/null 2>&1 || \
    die "AWS 자격증명이 유효하지 않습니다. 'aws configure --profile ${AWS_PROFILE}'로 설정하세요."

echo ""
echo "========================================================"
echo "  HEZO 위키(P2) S3 2버킷 생성"
echo "    🟢 영구: ${WIKI_BUCKET} (버전드 ON)"
echo "    🟠 임시: ${STAGING_BUCKET} (버전드 OFF)"
echo "    리전: ${REGION} / 프로필: ${AWS_PROFILE}"
echo "========================================================"
echo ""

# ─── 공통: 버킷 생성(멱등) + 퍼블릭 차단 + AES256 ───────────────────────────
create_bucket_base() {
    local bucket_name="$1"
    if aws s3api head-bucket --bucket "$bucket_name" --region "$REGION" ${AWS_PROFILE_OPT} 2>/dev/null; then
        warn "버킷 ${bucket_name} 이미 존재. 설정만 업데이트."
    else
        aws s3api create-bucket \
            --bucket "$bucket_name" --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION" \
            ${AWS_PROFILE_OPT} --output text > /dev/null
        success "버킷 생성: ${bucket_name}"
    fi
    aws s3api put-public-access-block --bucket "$bucket_name" \
        --public-access-block-configuration \
        "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
        --region "$REGION" ${AWS_PROFILE_OPT}
    aws s3api put-bucket-encryption --bucket "$bucket_name" \
        --server-side-encryption-configuration \
        '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}' \
        --region "$REGION" ${AWS_PROFILE_OPT}
    success "  퍼블릭 차단 + AES256 완료"
}

# ─── 🟢 영구 버킷 (industries) ──────────────────────────────────────────────
create_bucket_base "$WIKI_BUCKET"
aws s3api put-bucket-versioning --bucket "$WIKI_BUCKET" \
    --versioning-configuration Status=Enabled --region "$REGION" ${AWS_PROFILE_OPT}
success "  버전드 ON (industries 이력·롤백)"
# 옛(noncurrent) 버전 30일 후 만료 (현재 버전은 영구). 미완료 업로드 7일 후 정리.
aws s3api put-bucket-lifecycle-configuration --bucket "$WIKI_BUCKET" \
    --lifecycle-configuration '{"Rules":[
        {"ID":"expire-noncurrent-versions","Status":"Enabled","Filter":{},
         "NoncurrentVersionExpiration":{"NoncurrentDays":30},
         "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}
    ]}' \
    --region "$REGION" ${AWS_PROFILE_OPT}
success "  Lifecycle: noncurrent 버전 30일 후 만료 (현재 버전 영구)"

# ─── 🟠 임시 버킷 (raw·pending) ─────────────────────────────────────────────
create_bucket_base "$STAGING_BUCKET"
# 버전드는 켜지 않음(임시). raw/ 7일·pending/ 3일 후 삭제.
aws s3api put-bucket-lifecycle-configuration --bucket "$STAGING_BUCKET" \
    --lifecycle-configuration '{"Rules":[
        {"ID":"expire-raw","Status":"Enabled","Filter":{"Prefix":"raw/"},"Expiration":{"Days":7},
         "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":1}},
        {"ID":"expire-pending","Status":"Enabled","Filter":{"Prefix":"pending/"},"Expiration":{"Days":3}}
    ]}' \
    --region "$REGION" ${AWS_PROFILE_OPT}
success "  버전드 OFF + Lifecycle: raw/ 7일·pending/ 3일 후 삭제"

echo ""
echo "========================================================"
echo "  완료"
echo "    🟢 s3://${WIKI_BUCKET}   (버전드 ON)"
echo "         industries/{category}/{domain}.md   영구 위키, P1 읽음"
echo "    🟠 s3://${STAGING_BUCKET} (버전드 OFF, Lifecycle 삭제)"
echo "         raw/{category}/{domain}/{date}.json  크롤 원문 (7일)"
echo "         pending/{category}/{domain}.md        P1 보완 (3일/처리 후)"
echo "========================================================"
