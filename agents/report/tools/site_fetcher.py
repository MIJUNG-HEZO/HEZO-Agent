"""사이트 콘텐츠를 로드하는 내부 도구 (llms.txt 우선)"""
from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from agents.shared.s3_utils import (
    ARTIFACTS_BUCKET,
    SITE_BUCKET,
    get_s3,
    read_json,
    validate_site_id,
)
from agents.wiki.constants import WIKI_BUCKET, industry_key
from agents.validation.tools.wiki_parser import parse_wiki_md

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
PIPELINE_STATE_TABLE = os.environ.get("PIPELINE_STATE_TABLE", "pipeline_state")

_dynamodb: Any = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb", region_name=REGION)
    return _dynamodb


def _load_domain_url(site_id: str) -> str:
    """DynamoDB pipeline_state에서 실제 배포 도메인 조회."""
    try:
        resp = _get_dynamodb().get_item(
            TableName=PIPELINE_STATE_TABLE,
            Key={"site_id": {"S": site_id}},
            ProjectionExpression="domain_url",
        )
        url = resp.get("Item", {}).get("domain_url", {}).get("S", "")
        if url:
            logger.info("domain_url 로드: %s", url)
            return url
    except Exception as exc:
        logger.warning("DynamoDB domain_url 조회 실패: %s", exc)
    fallback = f"https://{site_id}.hezo.io"
    logger.info("domain_url fallback 사용: %s", fallback)
    return fallback


def _load_wiki_snapshot(category: str, domain: str) -> dict | None:
    """hezo-wiki에서 업종 MD를 읽어 wiki_snapshot 반환. (키: industries/{category}/{domain}.md)"""
    if not domain or not category:
        return None
    key = industry_key(category, domain)
    try:
        resp = get_s3().get_object(Bucket=WIKI_BUCKET, Key=key)
        md_content = resp["Body"].read().decode("utf-8")
        snapshot = parse_wiki_md(md_content)
        logger.info("wiki_snapshot 로드: domain=%s, topics=%d",
                    domain, len(snapshot.get("topics", [])))
        return snapshot
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            logger.info("hezo-wiki MD 없음: %s", key)
            return None
        raise
    except Exception as exc:
        logger.warning("wiki_snapshot 파싱 실패: %s", exc)
        return None


def fetch_site_content(site_id: str) -> dict[str, Any]:
    """
    사이트 콘텐츠 로드 (llms.txt 우선, 없으면 render_spec fallback).
    반환: {site_id, llms_txt, render_spec, contract, domain_url, wiki_snapshot}
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

    # 도메인 URL (DynamoDB pipeline_state에서 실제 배포 URL 조회)
    domain_url = _load_domain_url(site_id)

    # wiki_snapshot (업종 지식 신선도 판단용) — validation과 동일하게 category + industry 키 사용
    domain = contract.get("slots", {}).get("industry", "")
    category = contract.get("template", {}).get("category", "landing")
    wiki_snapshot = _load_wiki_snapshot(category, domain)

    return {
        "site_id": site_id,
        "llms_txt": llms_txt,
        "render_spec": render_spec,
        "contract": contract,
        "domain_url": domain_url,
        "wiki_snapshot": wiki_snapshot,
    }
