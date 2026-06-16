"""S3에서 Contract JSON과 크롤 스냅샷을 로드하는 내부 도구"""
from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError

from agents.shared.s3_utils import ARTIFACTS_BUCKET, read_json, validate_site_id

logger = logging.getLogger(__name__)


def load_contract(site_id: str) -> dict[str, Any]:
    """
    S3에서 contract_final.json 과 crawl_snapshot.json(선택)을 로드.
    반환: {"contract": {...}, "crawl_snapshot": {...} | None}
    """
    site_id = validate_site_id(site_id)

    contract = read_json(ARTIFACTS_BUCKET, f"sites/{site_id}/contract_final.json")
    logger.info("contract_final.json 로드 완료 - site_id=%s, keys=%s", site_id, list(contract.keys()))

    crawl_snapshot: dict | None = None
    try:
        crawl_snapshot = read_json(ARTIFACTS_BUCKET, f"sites/{site_id}/crawl_snapshot.json")
        logger.info("crawl_snapshot.json 로드 완료 - site_id=%s", site_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.info("crawl_snapshot.json 없음 (선택적) - site_id=%s", site_id)
        else:
            raise

    return {"contract": contract, "crawl_snapshot": crawl_snapshot}
