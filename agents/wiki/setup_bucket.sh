#!/usr/bin/env bash
# =============================================================================
# HEZO 위키(P2) S3 버킷 생성 스크립트
# 팀 인프라 패턴(infra/aws_setup.sh의 create_s3_bucket)을 그대로 따른다.
# 위키 지식(md)과 내부 관리 파일(json)을 저장할 P2 전용 버킷을 만든다.
#
# 사용법:
#   bash agents/wiki/setup_bucket.sh
#
# 환경변수(선택):
#   WIKI_BUCKET   버킷명 (기본: hezo-wiki)
#   AWS_REGION    리전   (기본: ap-northeast-2)
#   AWS_PROFILE   프로필 (기본: hezo-p2)
# =============================================================================

set -euo pipefail

# Git Bash(MSYS/MinGW)에서 /로 시작하는 인자를 Windows 경로로 변환하는 것을 방지
export MSYS_NO_PATHCONV=1

# ─── 설정값 (환경변수 우선, 없으면 기본값) ──────────────────────────────────
WIKI_BUCKET="${WIKI_BUCKET:-hezo-wiki}"
REGION="${AWS_REGION:-ap-northeast-2}"

# AWS_PROFILE이 설정돼 있으면 모든 aws 명령에 --profile 추가 (기본 hezo-p2)
AWS_PROFILE="${AWS_PROFILE:-hezo-p2}"
AWS_PROFILE_OPT="--profile ${AWS_PROFILE}"

# ─── 색상 출력 헬퍼 ─────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ─── 자격증명 확인 ──────────────────────────────────────────────────────────
if ! command -v aws &>/dev/null; then
    die "AWS CLI가 설치되어 있지 않습니다. https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html 참조"
fi

aws sts get-caller-identity ${AWS_PROFILE_OPT} --output text > /dev/null 2>&1 || \
    die "AWS 자격증명이 유효하지 않습니다. 'aws configure --profile ${AWS_PROFILE}'로 설정하세요."

echo ""
echo "========================================================"
echo "  HEZO 위키(P2) S3 버킷 생성"
echo "    버킷 : ${WIKI_BUCKET}"
echo "    리전 : ${REGION}"
echo "    프로필: ${AWS_PROFILE}"
echo "========================================================"
echo ""

# ─── 버킷 생성 (팀 create_s3_bucket 패턴) ──────────────────────────────────
create_wiki_bucket() {
    local bucket_name="$1"

    info "S3 버킷 생성: ${bucket_name} (P2 위키 지식 md + 내부 관리 json)"

    # 버킷 존재 여부 확인 (멱등)
    if aws s3api head-bucket --bucket "$bucket_name" --region "$REGION" ${AWS_PROFILE_OPT} 2>/dev/null; then
        warn "버킷 ${bucket_name} 이미 존재합니다. 설정만 업데이트합니다."
    else
        aws s3api create-bucket \
            --bucket "$bucket_name" \
            --region "$REGION" \
            --create-bucket-configuration LocationConstraint="$REGION" \
            ${AWS_PROFILE_OPT} \
            --output text > /dev/null
        success "버킷 생성 완료: ${bucket_name}"
    fi

    # 퍼블릭 액세스 차단
    aws s3api put-public-access-block \
        --bucket "$bucket_name" \
        --public-access-block-configuration \
            "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
        --region "$REGION" ${AWS_PROFILE_OPT}
    success "  퍼블릭 액세스 차단 설정 완료"

    # 버전 관리 활성화 (비교 merge 시 이전 버전 보존)
    aws s3api put-bucket-versioning \
        --bucket "$bucket_name" \
        --versioning-configuration Status=Enabled \
        --region "$REGION" ${AWS_PROFILE_OPT}
    success "  버전 관리 활성화 완료"

    # 서버 측 암호화 (AES-256) 기본 설정
    aws s3api put-bucket-encryption \
        --bucket "$bucket_name" \
        --server-side-encryption-configuration '{
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": "AES256"
                },
                "BucketKeyEnabled": true
            }]
        }' \
        --region "$REGION" ${AWS_PROFILE_OPT}
    success "  서버 측 암호화(AES-256) 설정 완료"
}

create_wiki_bucket "$WIKI_BUCKET"

echo ""
echo "========================================================"
echo "  완료: s3://${WIKI_BUCKET}"
echo "    industries/   업종 지식 (md)"
echo "    api_profiles/ API 명세 (md, 크롤링 X)"
echo "    _internal/    처리기록·대기열 (json)"
echo "========================================================"
