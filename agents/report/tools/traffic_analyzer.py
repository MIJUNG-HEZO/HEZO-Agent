"""
CloudFront S3 액세스 로그에서 AI 플랫폼 레퍼러 기반 실제 사용자 유입 감지.

봇 UA(Bot/Crawler/Spider 포함)는 제외하고 cs(Referer) 필드에서
AI 플랫폼 도메인을 감지해 실제 사람 방문만 집계.
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

AI_REFERRERS: dict[str, str] = {
    "Perplexity": r"perplexity\.ai",
    "ChatGPT":    r"chatgpt\.com",
    "Claude":     r"claude\.ai",
    "Copilot":    r"copilot\.microsoft\.com|bing\.com",
}

_BOT_UA = re.compile(r"bot|crawler|spider|slurp|facebookexternalhit", re.IGNORECASE)

_s3: Any = None


def _get_s3() -> Any:
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _list_log_keys(distribution_id: str, since_date: datetime) -> list[str]:
    prefix = f"{distribution_id}/"
    try:
        resp = _get_s3().list_objects_v2(Bucket=CF_LOG_BUCKET, Prefix=prefix)
        return [
            obj["Key"]
            for obj in resp.get("Contents", [])
            if obj["LastModified"].replace(tzinfo=timezone.utc) >= since_date
        ]
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NoSuchBucket", "AccessDenied", "404"):
            return []
        raise


def _parse_referrer_visits(key: str) -> list[tuple[str, str]]:
    """봇 UA 제외한 (referrer, user_agent) 쌍 목록 반환."""
    try:
        resp = _get_s3().get_object(Bucket=CF_LOG_BUCKET, Key=key)
        raw = resp["Body"].read()
        if key.endswith(".gz"):
            raw = gzip.decompress(raw)
        lines = raw.decode("utf-8", errors="ignore").splitlines()
        pairs = []
        for line in lines:
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) > 10:
                referer = parts[9]
                ua = parts[10]
                if _BOT_UA.search(ua):
                    continue
                pairs.append((referer, ua))
        return pairs
    except Exception as exc:
        logger.warning("로그 파싱 실패: %s — %s", key, exc)
        return []


def analyze_traffic(cf_distribution_id: str) -> dict[str, Any]:
    """
    지난 7일간 AI 플랫폼 레퍼러 기반 실제 사용자 유입 집계.
    cf_distribution_id 예: "E38E4K9DA2XEDN"
    """
    empty = {
        "configured": False,
        "visits": {p: 0 for p in AI_REFERRERS},
        "total_ai_traffic": 0,
        "period_days": 7,
    }

    if not cf_distribution_id:
        return empty

    since = datetime.now(timezone.utc) - timedelta(days=7)
    log_keys = _list_log_keys(cf_distribution_id, since)

    if not log_keys:
        logger.info("CloudFront 로그 없음: dist=%s", cf_distribution_id)
        return empty

    visits: dict[str, int] = defaultdict(int)
    for key in log_keys:
        for referer, _ in _parse_referrer_visits(key):
            for platform, pattern in AI_REFERRERS.items():
                if re.search(pattern, referer, re.IGNORECASE):
                    visits[platform] += 1

    result = {
        "configured": True,
        "visits": {p: visits.get(p, 0) for p in AI_REFERRERS},
        "total_ai_traffic": sum(visits.get(p, 0) for p in AI_REFERRERS),
        "period_days": 7,
    }
    logger.info("AI 트래픽 분석 완료: %s", result["visits"])
    return result
