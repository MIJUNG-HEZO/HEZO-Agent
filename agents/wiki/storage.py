"""HEZO Wiki (P2) S3 저장 계층.

위키 파일을 S3에 넣고 빼는 얇은 래퍼. 실제 boto3 입출력은 팀 공용 유틸
`agents/shared/s3_utils.py`를 재사용하고, 이 모듈은 P2 키 규칙(constants.py)과
저장 형식(md/json)을 입혀 준다.

저장 형식:
- 위키 지식(industries/, api_profiles/) = **md** (text/markdown)
- 내부 관리 파일(_internal/processed, pending_industries) = **json**

credential은 코드/깃에 두지 않는다. 버킷·리전·프로필은 환경변수(constants.py),
자격증명은 AWS 프로필(hezo-p2)로만 참조한다.
"""
from __future__ import annotations

from typing import Any

from agents.shared.s3_utils import (
    get_s3,
    key_exists,
    read_json,
    write_json,
    write_text,
)

from agents.wiki.constants import (
    INDUSTRIES_PREFIX,
    PENDING_INDUSTRIES_KEY,
    PROCESSED_KEY,
    WIKI_BUCKET,
    api_profile_key,
    industry_key,
)

_MARKDOWN_CONTENT_TYPE = "text/markdown"


def _read_text(key: str) -> str:
    """S3에서 텍스트(md) 읽기. s3_utils엔 read_text가 없어 클라이언트만 재사용한다."""
    resp = get_s3().get_object(Bucket=WIKI_BUCKET, Key=key)
    return resp["Body"].read().decode("utf-8")


# ─── 업종 지식 (industries/{domain}.md) ────────────────────────────────────
def put_industry(domain: str, markdown: str) -> int:
    """업종 지식 md를 저장. 저장 바이트 수 반환."""
    key = industry_key(domain)
    write_text(WIKI_BUCKET, key, markdown, _MARKDOWN_CONTENT_TYPE)
    return len(markdown.encode("utf-8"))


def get_industry(domain: str) -> str:
    """업종 지식 md를 읽음."""
    return _read_text(industry_key(domain))


def industry_exists(domain: str) -> bool:
    """업종 지식 파일 존재 여부."""
    return key_exists(WIKI_BUCKET, industry_key(domain))


def list_industries() -> list[str]:
    """저장된 업종 도메인 목록(파일명에서 .md 제거)."""
    s3 = get_s3()
    domains: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=WIKI_BUCKET, Prefix=INDUSTRIES_PREFIX):
        for obj in page.get("Contents", []):
            name = obj["Key"][len(INDUSTRIES_PREFIX):]
            if name.endswith(".md"):
                domains.append(name[:-3])
    return domains


# ─── API 명세 (api_profiles/{name}.md, 크롤링하지 않음) ────────────────────
def put_api_profile(name: str, markdown: str) -> int:
    """API 명세 md를 저장. 저장 바이트 수 반환."""
    key = api_profile_key(name)
    write_text(WIKI_BUCKET, key, markdown, _MARKDOWN_CONTENT_TYPE)
    return len(markdown.encode("utf-8"))


def get_api_profile(name: str) -> str:
    """API 명세 md를 읽음."""
    return _read_text(api_profile_key(name))


# ─── 내부 관리 파일 (_internal/*.json) ─────────────────────────────────────
def read_processed() -> dict[str, Any]:
    """처리 기록(중복·실패 방지) 읽기. 없으면 빈 dict."""
    if not key_exists(WIKI_BUCKET, PROCESSED_KEY):
        return {}
    return read_json(WIKI_BUCKET, PROCESSED_KEY)


def write_processed(data: dict[str, Any]) -> int:
    """처리 기록 저장. 저장 바이트 수 반환."""
    return write_json(WIKI_BUCKET, PROCESSED_KEY, data)


def read_pending() -> dict[str, Any]:
    """신규 채우기 대기열 읽기. 없으면 빈 dict."""
    if not key_exists(WIKI_BUCKET, PENDING_INDUSTRIES_KEY):
        return {}
    return read_json(WIKI_BUCKET, PENDING_INDUSTRIES_KEY)


def write_pending(data: dict[str, Any]) -> int:
    """신규 채우기 대기열 저장. 저장 바이트 수 반환."""
    return write_json(WIKI_BUCKET, PENDING_INDUSTRIES_KEY, data)
