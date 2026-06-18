#!/usr/bin/env bash
# =============================================================================
# HEZO 위키(P2) DynamoDB 색인 테이블 생성 스크립트
# 팀 인프라 패턴(infra/aws_setup.sh의 create-table)을 그대로 따른다.
# 위키 메타(상태·최신버전·만료)를 담는 P2 전용 색인 테이블을 만든다. (본문 없음)
#
# 사용법:
#   bash agents/wiki/setup_wiki_index.sh
#
# 환경변수(선택):
#   WIKI_INDEX_TABLE  테이블명 (기본: hezo_wiki_index)
#   AWS_REGION        리전     (기본: ap-northeast-2)
#   AWS_PROFILE       프로필   (기본: hezo-p2)
# =============================================================================

set -euo pipefail
export MSYS_NO_PATHCONV=1

WIKI_INDEX_TABLE="${WIKI_INDEX_TABLE:-hezo_wiki_index}"
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
echo "  HEZO 위키(P2) DynamoDB 색인 테이블 생성"
echo "    테이블: ${WIKI_INDEX_TABLE}"
echo "    리전  : ${REGION}"
echo "    프로필: ${AWS_PROFILE}"
echo "========================================================"
echo ""

# 멱등: 이미 있으면 스킵
if aws dynamodb describe-table --table-name "$WIKI_INDEX_TABLE" --region "$REGION" ${AWS_PROFILE_OPT} > /dev/null 2>&1; then
    warn "DynamoDB 테이블 ${WIKI_INDEX_TABLE} 이미 존재합니다. 생성 스킵."
else
    info "DynamoDB 테이블 생성: ${WIKI_INDEX_TABLE} (PK=domain, GSI=due-index)"
    aws dynamodb create-table \
        --table-name "$WIKI_INDEX_TABLE" \
        --attribute-definitions \
            "AttributeName=domain,AttributeType=S" \
            "AttributeName=status,AttributeType=S" \
            "AttributeName=next_refresh_at,AttributeType=N" \
        --key-schema "AttributeName=domain,KeyType=HASH" \
        --global-secondary-indexes '[{
            "IndexName": "due-index",
            "KeySchema": [
                {"AttributeName": "status", "KeyType": "HASH"},
                {"AttributeName": "next_refresh_at", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        }]' \
        --billing-mode PAY_PER_REQUEST \
        --region "$REGION" ${AWS_PROFILE_OPT} \
        --tags "Key=Project,Value=HEZO" \
        --output text > /dev/null
    success "테이블 생성 완료: ${WIKI_INDEX_TABLE}"
    info "  테이블 활성화 대기 중..."
    aws dynamodb wait table-exists --table-name "$WIKI_INDEX_TABLE" --region "$REGION" ${AWS_PROFILE_OPT}
    success "  테이블 활성화 완료"
fi

echo ""
echo "========================================================"
echo "  완료: ${WIKI_INDEX_TABLE}"
echo "    PK         = domain"
echo "    GSI        = due-index (status, next_refresh_at)"
echo "    billing    = PAY_PER_REQUEST"
echo "    본문 없음(메타만): status·latest_version·confidence·next_refresh_at 등"
echo "========================================================"
