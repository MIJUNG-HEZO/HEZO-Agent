"""validation_feedback.json을 S3에서 로드하는 내부 도구 (재시도 시 사용)"""
from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from agents.shared.s3_utils import ARTIFACTS_BUCKET, read_json, validate_site_id

logger = logging.getLogger(__name__)


def load_feedback(site_id: str) -> dict | None:
    """
    hezo-artifacts/sites/{site_id}/validation_feedback.json 로드.
    파일이 없으면 None 반환 (첫 실행 시 정상).
    반환: {"blocking_issues": [...], "patch_hints": [...]} | None
    """
    site_id = validate_site_id(site_id)
    key = f"sites/{site_id}/validation_feedback.json"
    try:
        data = read_json(ARTIFACTS_BUCKET, key)
        logger.info("validation_feedback.json 로드 — site_id=%s, blocking=%d",
                    site_id, len(data.get("blocking_issues", [])))
        return data
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.info("validation_feedback.json 없음 (첫 실행) — site_id=%s", site_id)
            return None
        raise
