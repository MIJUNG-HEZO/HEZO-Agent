"""공유 S3 유틸리티"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "hezo-artifacts")
SITE_BUCKET = os.environ.get("SITE_BUCKET", "hezo-sites")
REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))

_s3: Any = None


def get_s3() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def read_json(bucket: str, key: str) -> dict:
    s3 = get_s3()
    logger.info("S3 읽기: s3://%s/%s", bucket, key)
    resp = s3.get_object(Bucket=bucket, Key=key)
    return json.loads(resp["Body"].read().decode("utf-8"))


def write_json(bucket: str, key: str, data: dict, metadata: dict | None = None) -> int:
    s3 = get_s3()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": "application/json; charset=utf-8",
    }
    if metadata:
        kwargs["Metadata"] = metadata
    s3.put_object(**kwargs)
    logger.info("S3 저장 완료: s3://%s/%s (%d bytes)", bucket, key, len(body))
    return len(body)


def write_text(bucket: str, key: str, text: str, content_type: str = "text/plain") -> None:
    s3 = get_s3()
    body = text.encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=f"{content_type}; charset=utf-8",
    )
    logger.info("S3 텍스트 저장: s3://%s/%s", bucket, key)


def key_exists(bucket: str, key: str) -> bool:
    try:
        get_s3().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def validate_site_id(site_id: str) -> str:
    site_id = site_id.strip()
    if not site_id or "/" in site_id or ".." in site_id:
        raise ValueError(f"유효하지 않은 site_id: {site_id!r}")
    return site_id
