"""
Layer 1: 정보 보존 검증 — 크롤 스냅샷 vs 생성 HTML 의미 비교 (LLM 사용)
크롤 스냅샷이 없으면 건너뜀.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

from libs.telemetry import record_llm_usage

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")

_bedrock: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def check_layer1(
    contract: dict,
    crawl_snapshot: dict,
    html_content: str,
) -> list[dict]:
    """
    크롤 스냅샷의 핵심 정보가 생성된 HTML에 보존됐는지 LLM으로 검증.
    반환: 이슈 목록 [{level, code, detail}]
    """
    if not crawl_snapshot or not html_content:
        return []

    # 크롤 스냅샷에서 핵심 정보 추출 (비용 절감용 트림)
    snap_summary = {
        "business_name": crawl_snapshot.get("business_name", ""),
        "phone": crawl_snapshot.get("phone", ""),
        "address": crawl_snapshot.get("address", ""),
        "services": crawl_snapshot.get("services", [])[:5],
        "key_facts": crawl_snapshot.get("key_facts", [])[:5],
    }
    html_snippet = html_content[:3000]

    prompt = f"""다음 원본 비즈니스 정보와 생성된 HTML을 비교하여,
핵심 정보(업체명, 연락처, 주요 서비스)가 HTML에 올바르게 포함되어 있는지 확인하세요.

원본 정보:
{json.dumps(snap_summary, ensure_ascii=False, indent=2)}

생성된 HTML (앞부분):
{html_snippet}

다음 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "missing_info": ["누락된 핵심 정보 설명"],
  "distorted_info": ["왜곡된 정보 설명"],
  "all_preserved": true/false
}}"""

    try:
        bedrock = _get_bedrock()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        })
        start = time.monotonic()
        resp = bedrock.invoke_model(
            modelId=MODEL_ID, body=body,
            contentType="application/json", accept="application/json",
        )
        elapsed = (time.monotonic() - start) * 1000
        result = json.loads(resp["body"].read())

        _usage = result.get("usage", {})
        record_llm_usage(
            "validation", "sonnet",
            _usage.get("input_tokens", 0),
            _usage.get("output_tokens", 0),
            ms=elapsed,
        )

        text = result["content"][0]["text"].strip()
        check = json.loads(text)
    except Exception as exc:
        logger.warning("Layer 1 LLM 호출 실패 — 건너뜀: %s", exc)
        return []

    issues: list[dict] = []
    for item in check.get("missing_info", []):
        issues.append({"level": "warning", "code": "LAYER1_MISSING_INFO", "detail": item})
    for item in check.get("distorted_info", []):
        issues.append({"level": "warning", "code": "LAYER1_DISTORTED_INFO", "detail": item})

    return issues
