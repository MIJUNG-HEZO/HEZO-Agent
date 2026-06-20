"""HEZO Wiki (P2) 경로/버킷 상수 및 키 빌더.

S3 = **2버킷** (버전드가 버킷 단위라 영구↔임시 분리, PRD §3.1):
- 🟢 WIKI_BUCKET(hezo-wiki, 버전드 ON)  → industries/{category}/{domain}.md  영구 위키 본문. P1이 읽음
- 🟠 STAGING_BUCKET(hezo-wiki-staging, 버전드 OFF, Lifecycle 삭제) → 임시:
    - raw/{category}/{domain}/{date}.json  크롤 원문 (크롤→생성 핸드오프)
    - pending/{category}/{domain}.md       P1 보완 (보강 A 입력, 처리 후 삭제)

원칙:
- 본문 읽기/쓰기 = 항상 S3 / "최신·상태·시드·만료" 메타 = 항상 DynamoDB(hezo_wiki_index).
- 영구(industries)=WIKI_BUCKET / 임시(raw·pending)=STAGING_BUCKET. 키(경로)는 동일, 버킷만 다름.
- 버킷명·리전·프로필은 환경변수로 주입 (credential은 코드/깃에 두지 않고 AWS 프로필만 참조).

상세 설계: 바탕화면 `HEZO_P2_위키저장소_PRD_v2_확정.md`.
"""
from __future__ import annotations

import os

# ─── 환경변수 (실제 키 값은 절대 코드/깃에 두지 않음 — 프로필/환경변수만 참조) ──
WIKI_BUCKET = os.environ.get("WIKI_BUCKET", "hezo-wiki")              # 🟢 영구(industries), 버전드 ON, P1 읽음
STAGING_BUCKET = os.environ.get("STAGING_BUCKET", "hezo-wiki-staging")  # 🟠 임시(raw·pending), 버전드 OFF
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
AWS_PROFILE = os.environ.get("AWS_PROFILE", "")

# DynamoDB 색인 테이블 (본문 없음, 메타만) — 후속 이슈(index_store)에서 사용
WIKI_INDEX_TABLE = os.environ.get("WIKI_INDEX_TABLE", "hezo_wiki_index")

# ─── 카테고리 (60 템플릿 = landing/blog/store 각 20) ────────────────────────
CATEGORIES = ("landing", "blog", "store")

# ─── 위키 저장소 키 prefix ──────────────────────────────────────────────────
INDUSTRIES_PREFIX = "industries/"  # 업종 지식 본문 (nested, 버전드)
PENDING_PREFIX = "pending/"        # P1 보완 md 임시 (보강 A 입력)
RAW_PREFIX = "raw/"                # 크롤 원문 임시 (Lifecycle 자동삭제)


def _validate_name(name: str, kind: str) -> str:
    """경로 주입 방지: 빈 값·'/'·'..' 거부 (agents/shared/s3_utils.validate_site_id 패턴)."""
    name = name.strip()
    if not name or "/" in name or ".." in name:
        raise ValueError(f"invalid {kind}: {name!r}")
    return name


def _validate_category(category: str) -> str:
    """카테고리는 landing/blog/store 중 하나여야 한다."""
    category = category.strip()
    if category not in CATEGORIES:
        raise ValueError(f"invalid category: {category!r} (expected one of {CATEGORIES})")
    return category


def industry_key(category: str, domain: str) -> str:
    """업종 지식 md S3 키 (nested).

    예: industry_key('store', 'wine_market') -> 'industries/store/wine_market.md'
    """
    cat = _validate_category(category)
    return f"{INDUSTRIES_PREFIX}{cat}/{_validate_name(domain, 'domain')}.md"


def pending_key(category: str, domain: str) -> str:
    """P1 보완 md 임시 S3 키 (보강 A 입력, 비교 후 삭제).

    예: pending_key('landing', 'tax_accounting') -> 'pending/landing/tax_accounting.md'
    """
    cat = _validate_category(category)
    return f"{PENDING_PREFIX}{cat}/{_validate_name(domain, 'domain')}.md"


def raw_key(category: str, domain: str, date: str) -> str:
    """크롤 원문 json 임시 S3 키 (크롤 람다→생성 람다 전달, Lifecycle 자동삭제).

    예: raw_key('store', 'wine_market', '2026-06-18')
        -> 'raw/store/wine_market/2026-06-18.json'
    """
    cat = _validate_category(category)
    domain = _validate_name(domain, "domain")
    return f"{RAW_PREFIX}{cat}/{domain}/{_validate_name(date, 'date')}.json"
