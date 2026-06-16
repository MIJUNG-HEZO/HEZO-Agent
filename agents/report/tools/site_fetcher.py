"""사이트 콘텐츠를 로드하는 내부 도구 (llms.txt 우선)"""
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


def fetch_site_content(site_id: str) -> dict[str, Any]:
    """
    사이트 콘텐츠 로드 (llms.txt 우선, 없으면 render_spec fallback).
    반환: {site_id, llms_txt, render_spec, contract}
    """
    site_id = validate_site_id(site_id)
    s3 = get_s3()
    prefix = f"sites/{site_id}"

    # llms.txt 로드 (AI 크롤러 관점의 콘텐츠)
    llms_txt = ""
    try:
        resp = s3.get_object(Bucket=SITE_BUCKET, Key=f"{prefix}/llms.txt")
        llms_txt = resp["Body"].read().decode("utf-8")
        logger.info("llms.txt 로드: %d chars", len(llms_txt))
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("NoSuchKey", "404"):
            raise
        logger.warning("llms.txt 없음 — render_spec fallback 사용")

    render_spec = read_json(ARTIFACTS_BUCKET, f"{prefix}/render_spec.json")
    contract = read_json(ARTIFACTS_BUCKET, f"{prefix}/contract_final.json")

    return {
        "site_id": site_id,
        "llms_txt": llms_txt,
        "render_spec": render_spec,
        "contract": contract,
    }
