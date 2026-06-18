"""validation_report.json / validation_feedback.json을 S3에 저장하는 내부 도구"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

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


def save_validation_feedback(site_id: str, blocking_issues: list[dict], attempt: int) -> str:
    """
    FAIL_BLOCKING 시 validation_feedback.json 저장.
    생성 에이전트(Step Functions 재시도 경로) 또는 감사 로그용.
    반환: s3_key
    """
    site_id = validate_site_id(site_id)
    feedback = {
        "site_id": site_id,
        "blocking_issues": blocking_issues,
        "patch_hints": [
            {"code": i.get("code"), "detail": i.get("detail")}
            for i in blocking_issues
        ],
        "attempt": attempt,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }
    key = f"sites/{site_id}/validation_feedback.json"
    write_json(ARTIFACTS_BUCKET, key, feedback,
               metadata={"site-id": site_id, "saved-by": "hezo-validation-agent"})
    logger.info("validation_feedback.json 저장: s3://hezo-artifacts/%s", key)
    return key
