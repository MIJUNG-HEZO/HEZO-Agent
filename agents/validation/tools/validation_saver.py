"""validation_report.json을 S3에 저장하는 내부 도구"""
from __future__ import annotations

import logging

from agents.shared.s3_utils import ARTIFACTS_BUCKET, write_json, validate_site_id

logger = logging.getLogger(__name__)


def save_validation_report(site_id: str, report: dict) -> str:
    """
    validation_report.json → hezo-artifacts/sites/{site_id}/validation_report.json
    반환: s3_key
    """
    site_id = validate_site_id(site_id)
    key = f"sites/{site_id}/validation_report.json"
    write_json(ARTIFACTS_BUCKET, key, report,
               metadata={"site-id": site_id, "saved-by": "hezo-validation-agent"})
    logger.info("validation_report.json 저장: s3://hezo-artifacts/%s", key)
    return key
