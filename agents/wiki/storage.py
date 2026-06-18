"""HEZO Wiki (P2) S3 저장 계층 (nested).

업종 지식 본문을 S3에 넣고 빼는 얇은 래퍼. 실제 boto3 입출력은 팀 공용 유틸
`agents/shared/s3_utils.py`를 재사용하고, 이 모듈은 P2 키 규칙(constants.py)과
저장 형식(md)을 입혀 준다.

- 본문(industries/) = **md** (text/markdown), nested 키 `industries/{category}/{domain}.md`.
- 읽기 인터페이스 = `get_industry(category, domain)` (P1/P4가 Contract의 category·domain으로 호출).
- 버킷은 버전드 ON이라 put 시 새 버전이 쌓인다. `save_industry_versioned`가 그
  VersionId를 받아 `index_store.commit()`으로 DDB(최신버전·상태·만료)에 기록한다.

credential은 코드/깃에 두지 않는다. 버킷·리전·프로필은 환경변수(constants.py),
자격증명은 AWS 프로필(hezo-p2)로만 참조한다.
"""
from __future__ import annotations

from agents.shared.s3_utils import get_s3, key_exists, write_text

from agents.wiki.constants import INDUSTRIES_PREFIX, WIKI_BUCKET, industry_key
from agents.wiki.index_store import WikiIndexStore

_MARKDOWN_CONTENT_TYPE = "text/markdown"


def _read_text(key: str) -> str:
    """S3에서 텍스트(md) 읽기. s3_utils엔 read_text가 없어 클라이언트만 재사용한다."""
    resp = get_s3().get_object(Bucket=WIKI_BUCKET, Key=key)
    return resp["Body"].read().decode("utf-8")


# ─── 업종 지식 (industries/{category}/{domain}.md) ─────────────────────────
def put_industry(category: str, domain: str, markdown: str) -> int:
    """업종 지식 md를 저장(버킷 버전드 → 새 버전 append). 저장 바이트 수 반환."""
    key = industry_key(category, domain)
    write_text(WIKI_BUCKET, key, markdown, _MARKDOWN_CONTENT_TYPE)
    return len(markdown.encode("utf-8"))


def save_industry_versioned(
    category: str,
    domain: str,
    markdown: str,
    *,
    confidence: float,
    source_urls: list[str],
    index: WikiIndexStore | None = None,
) -> dict:
    """완성 md를 S3에 새 버전으로 저장하고, 그 VersionId로 DDB 메타를 갱신한다.

    흐름: S3 put_object(버전드 → VersionId 반환) → index.commit(latest_version=...,
    status=done, confidence, source_urls, next_refresh_at=now+TTL).

    저장(본문)과 메타(DDB)를 한 흐름으로 잇는 핵심 함수. VersionId 캡처를 위해
    공용 write_text(반환값 없음) 대신 put_object를 직접 호출한다(s3_utils 미변경).

    반환: {"version_id", "bytes", "committed"}.
    """
    index = index if index is not None else WikiIndexStore()
    key = industry_key(category, domain)
    body = markdown.encode("utf-8")
    resp = get_s3().put_object(
        Bucket=WIKI_BUCKET,
        Key=key,
        Body=body,
        ContentType=f"{_MARKDOWN_CONTENT_TYPE}; charset=utf-8",
    )
    version_id = resp.get("VersionId")  # 버킷 버전드 OFF면 None
    committed = index.commit(
        domain,
        latest_version=version_id,
        confidence=confidence,
        source_urls=source_urls,
    )
    return {"version_id": version_id, "bytes": len(body), "committed": committed}


def get_industry(category: str, domain: str) -> str:
    """업종 지식 md를 읽음(최신 버전). P1/P4 읽기 인터페이스."""
    return _read_text(industry_key(category, domain))


def industry_exists(category: str, domain: str) -> bool:
    """업종 지식 파일 존재 여부."""
    return key_exists(WIKI_BUCKET, industry_key(category, domain))


def list_industries() -> list[tuple[str, str]]:
    """저장된 (category, domain) 목록. industries/{category}/{domain}.md 키를 파싱."""
    s3 = get_s3()
    out: list[tuple[str, str]] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=WIKI_BUCKET, Prefix=INDUSTRIES_PREFIX):
        for obj in page.get("Contents", []):
            rel = obj["Key"][len(INDUSTRIES_PREFIX):]  # "landing/tax_accounting.md"
            if rel.endswith(".md") and "/" in rel:
                category, filename = rel.split("/", 1)
                out.append((category, filename[:-3]))
    return out
