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

logger = logging.getLogger(__name__)


def fetch_artifacts(site_id: str) -> dict[str, Any]:
    """
    검증에 필요한 모든 산출물 로드.
    반환:
      contract, render_spec, crawl_snapshot (선택),
      html (index.html 문자열), file_list (dist/ 파일 키 목록)
    """
    site_id = validate_site_id(site_id)
    s3 = get_s3()
    prefix = f"sites/{site_id}"

    contract = read_json(ARTIFACTS_BUCKET, f"{prefix}/contract_final.json")
    render_spec = read_json(ARTIFACTS_BUCKET, f"{prefix}/render_spec.json")

    crawl_snapshot: dict | None = None
    try:
        crawl_snapshot = read_json(ARTIFACTS_BUCKET, f"{prefix}/crawl_snapshot.json")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise
        logger.info("crawl_snapshot.json 없음 (선택적) — site_id=%s", site_id)

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
        "crawl_snapshot": crawl_snapshot,
        "html": html_content,
        "file_list": file_list,
    }
