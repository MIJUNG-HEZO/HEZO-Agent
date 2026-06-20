"""
CloudFront S3 액세스 로그에서 AI 봇 방문 감지.

선행 조건: customer-infra.yaml CloudFormation에서 CloudFront 로그 활성화 필요.
로그 S3 경로: {CF_LOG_BUCKET}/{cf_distribution_id}/
로그 미설정 시 configured=False 반환 (에러 없음).
"""
from __future__ import annotations

import gzip
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
CF_LOG_BUCKET = os.environ.get("CF_LOG_BUCKET", "hezo-cloudfront-logs")

BOT_PATTERNS: dict[str, str] = {
    "GPTBot": r"GPTBot",
    "ClaudeBot": r"ClaudeBot",
    "PerplexityBot": r"PerplexityBot",
    "Yeti": r"Yeti/",
    "Googlebot": r"Googlebot",
}

_s3: Any = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _list_log_keys(distribution_id: str, since_date: datetime) -> list[str]:
    prefix = f"{distribution_id}/"
    try:
        resp = _get_s3().list_objects_v2(Bucket=CF_LOG_BUCKET, Prefix=prefix)
        keys = []
        for obj in resp.get("Contents", []):
            if obj["LastModified"].replace(tzinfo=timezone.utc) >= since_date:
                keys.append(obj["Key"])
        return keys
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NoSuchBucket", "AccessDenied", "404"):
            return []
        raise


def _parse_log_file(key: str) -> list[str]:
    try:
        resp = _get_s3().get_object(Bucket=CF_LOG_BUCKET, Key=key)
        raw = resp["Body"].read()
        if key.endswith(".gz"):
            raw = gzip.decompress(raw)
        lines = raw.decode("utf-8", errors="ignore").splitlines()
        user_agents = []
        for line in lines:
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            # CloudFront 로그: cs(User-Agent)는 10번째 컬럼 (0-indexed)
            if len(parts) > 10:
                user_agents.append(parts[10])
        return user_agents
    except Exception as exc:
        logger.warning("로그 파일 파싱 실패: %s — %s", key, exc)
        return []


def analyze_bot_visits(cf_distribution_id: str) -> dict[str, Any]:
    """
    지난 7일간 AI 봇 방문 횟수 집계.
    cf_distribution_id 예: "E20FCOEPMP0R4A"
    """
    if not cf_distribution_id:
        return {
            "configured": False,
            "visits": {b: 0 for b in BOT_PATTERNS},
            "last_visit_dates": {b: None for b in BOT_PATTERNS},
            "period_days": 7,
        }

    since = datetime.now(timezone.utc) - timedelta(days=7)
    log_keys = _list_log_keys(cf_distribution_id, since)

    if not log_keys:
        logger.info("CloudFront 로그 없음 (미설정 또는 기간 내 로그 없음): dist=%s", cf_distribution_id)
        return {
            "configured": False,
            "visits": {b: 0 for b in BOT_PATTERNS},
            "last_visit_dates": {b: None for b in BOT_PATTERNS},
            "period_days": 7,
        }

    visits: dict[str, int] = defaultdict(int)
    last_dates: dict[str, str | None] = {b: None for b in BOT_PATTERNS}

    for key in log_keys:
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", key)
        log_date = date_match.group(1) if date_match else None

        user_agents = _parse_log_file(key)
        for ua in user_agents:
            for bot_name, pattern in BOT_PATTERNS.items():
                if re.search(pattern, ua, re.IGNORECASE):
                    visits[bot_name] += 1
                    if log_date and (last_dates[bot_name] is None or log_date > last_dates[bot_name]):
                        last_dates[bot_name] = log_date

    result = {
        "configured": True,
        "visits": {b: visits.get(b, 0) for b in BOT_PATTERNS},
        "last_visit_dates": last_dates,
        "period_days": 7,
    }
    logger.info("봇 방문 분석 완료: %s", result["visits"])
    return result
