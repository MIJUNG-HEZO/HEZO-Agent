"""S3에서 빌드 산출물(HTML, 파일 목록)과 참조 문서를 로드하는 내부 도구"""
from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from agents.shared.s3_utils import (
    ARTIFACTS_BUCKET,
    SITE_BUCKET,
    get_s3,
    read_json,
    validate_site_id,
)
from agents.wiki.constants import WIKI_BUCKET, industry_key
from agents.validation.tools.wiki_parser import parse_wiki_md

logger = logging.getLogger(__name__)


def _load_wiki_snapshot(s3, domain: str) -> dict | None:
    """
    hezo-wiki 버킷에서 업종 MD를 읽어 wiki_snapshot으로 변환.
    키 경로: industries/{domain}.md  (industry_key() 헬퍼 사용)
    """
    if not domain:
        return None
    key = industry_key(domain)
    try:
        resp = s3.get_object(Bucket=WIKI_BUCKET, Key=key)
        md_content = resp["Body"].read().decode("utf-8")
        snapshot = parse_wiki_md(md_content)
        logger.info(
            "wiki_snapshot 로드: domain=%s topics=%d confidence=%.2f",
            domain, len(snapshot.get("topics", [])), snapshot.get("confidence", 0),
        )
        return snapshot
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            logger.info("hezo-wiki MD 없음: bucket=%s key=%s", WIKI_BUCKET, key)
            return None
        raise
    except Exception as exc:
        logger.warning("wiki_snapshot 파싱 실패 — 건너뜀: %s", exc)
        return None


def fetch_artifacts(site_id: str) -> dict[str, Any]:
    """
    검증에 필요한 모든 산출물 로드.
    반환:
      contract, render_spec,
      wiki_snapshot (hezo-wiki MD 파싱 결과, 없으면 None),
      html (index.html 문자열), file_list (dist/ 파일 키 목록)
    """
    site_id = validate_site_id(site_id)
    s3 = get_s3()
    prefix = f"sites/{site_id}"

    contract = read_json(ARTIFACTS_BUCKET, f"{prefix}/contract_final.json")
    render_spec = read_json(ARTIFACTS_BUCKET, f"{prefix}/render_spec.json")

    # contract의 business_type → hezo-wiki 업종 MD 로드
    domain = contract.get("slots", {}).get("business_type", "")
    wiki_snapshot = _load_wiki_snapshot(s3, domain)

    # dist/index.html 로드
    html_key = f"{prefix}/dist/index.html"
    html_content = ""
    try:
        resp = s3.get_object(Bucket=SITE_BUCKET, Key=html_key)
        html_content = resp["Body"].read().decode("utf-8")
        logger.info("index.html 로드: %d chars", len(html_content))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise
        logger.warning("dist/index.html 없음 — 빌드 산출물 미존재 가능")

    # dist/ 파일 목록
    file_list: list[str] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=SITE_BUCKET, Prefix=f"{prefix}/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                file_list.append(key.split("/")[-1])
    except Exception as exc:
        logger.warning("파일 목록 조회 실패: %s", exc)

    return {
        "site_id": site_id,
        "contract": contract,
        "render_spec": render_spec,
        "wiki_snapshot": wiki_snapshot,
        "html": html_content,
        "file_list": file_list,
    }
