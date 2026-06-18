"""llm_report.json을 hezo-reports 버킷에 저장하는 내부 도구"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from agents.shared.s3_utils import validate_site_id

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "hezo-reports")

_s3: Any = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def save_report(site_id: str, report: dict) -> str:
    """
    llm_report.json → hezo-reports/{site_id}/{YYYY-MM-DD}/weekly_report.json

    마스터 플랜 S3 구조:
      hezo-reports/{site_id}/{YYYY-MM-DD}/
        weekly_report.pdf   (미래 — PDF 생성 시)
        weekly_report.html  (미래 — HTML 렌더링 시)
        weekly_report.json  (현재 구현)

    반환: s3_key
    """
    site_id = validate_site_id(site_id)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{site_id}/{today}/weekly_report.json"

    body = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
    _get_s3().put_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        Metadata={"site-id": site_id, "saved-by": "hezo-report-agent"},
    )
    logger.info("weekly_report.json 저장: s3://%s/%s (%d bytes)",
                REPORTS_BUCKET, key, len(body))
    return key
