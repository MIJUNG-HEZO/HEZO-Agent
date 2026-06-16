"""HEZO Wiki (P2) 경로/버킷 상수 및 키 빌더.

S3(hezo-wiki 버킷) 저장 형식:
- 위키 지식(industries/, api_profiles/)은 **md**로 저장한다.
- 내부 관리 파일(_internal/processed, pending_industries)은 **json**으로 저장한다.

규칙:
- 버킷명·리전·프로필은 환경변수로 주입한다 (credential은 코드/깃에 두지 않고
  AWS 프로필로만 참조한다).
- 키 prefix는 코드 상수로 고정한다.

실제 boto3 S3 입출력은 후속 이슈(저장 계층)에서 구현한다. 이 모듈은 스켈레톤
범위로 상수와 키 빌더만 제공한다.
"""
from __future__ import annotations

import os

# ─── 환경변수 (실제 키 값은 절대 코드/깃에 두지 않음 — 프로필/환경변수만 참조) ──
WIKI_BUCKET = os.environ.get("WIKI_BUCKET", "hezo-wiki-dev")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
AWS_PROFILE = os.environ.get("AWS_PROFILE", "hezo-p2")

# ─── 위키 저장소 키 prefix ──────────────────────────────────────────────────
INDUSTRIES_PREFIX = "industries/"      # 업종 지식 (시드 3종 + 크롤링으로 채움)
API_PROFILES_PREFIX = "api_profiles/"  # API 명세 시드 (크롤링하지 않음)
INTERNAL_PREFIX = "_internal/"         # 내부 관리 파일

# ─── 내부 관리 파일 키 ──────────────────────────────────────────────────────
PROCESSED_KEY = INTERNAL_PREFIX + "processed.json"                    # 처리 기록(중복·실패 방지)
PENDING_INDUSTRIES_KEY = INTERNAL_PREFIX + "pending_industries.json"  # 신규 채우기 대기열


def _validate_name(name: str, kind: str) -> str:
    """경로 주입 방지: 빈 값·'/'·'..' 거부 (agents/shared/s3_utils.validate_site_id 패턴)."""
    name = name.strip()
    if not name or "/" in name or ".." in name:
        raise ValueError(f"invalid {kind}: {name!r}")
    return name


def industry_key(domain: str) -> str:
    """업종 지식 md S3 키. 예: industry_key('tax_accounting') -> 'industries/tax_accounting.md'"""
    return f"{INDUSTRIES_PREFIX}{_validate_name(domain, 'domain')}.md"


def api_profile_key(name: str) -> str:
    """API 명세 md S3 키. 예: api_profile_key('landing_tax') -> 'api_profiles/landing_tax.md'"""
    return f"{API_PROFILES_PREFIX}{_validate_name(name, 'api_profile')}.md"
