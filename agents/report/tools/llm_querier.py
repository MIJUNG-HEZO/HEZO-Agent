"""
실제 멀티 LLM 병렬 쿼리 도구.

지원 LLM:
  - Claude (Bedrock, 항상 사용)
  - ChatGPT / OpenAI (OPENAI_API_KEY 환경변수 필요)
  - Perplexity (PERPLEXITY_API_KEY 환경변수 필요)

API 키 없는 LLM은 건너뛰고 결과에서 null 처리.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import boto3

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MODEL_ID_HAIKU = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
PERPLEXITY_API_KEY = os.environ.get("PERPLEXITY_API_KEY", "")

_bedrock: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def _check_citation(response_text: str, site_url: str, business_name: str) -> bool:
    """응답에 우리 사이트 URL 또는 업체명이 언급됐는지 확인"""
    text_lower = response_text.lower()
    name_lower = business_name.lower()
    url_domain = site_url.replace("https://", "").replace("http://", "").split("/")[0].lower()
    return url_domain in text_lower or name_lower in text_lower


# ── Claude (Bedrock) ──────────────────────────────────────────────────────────

def _query_claude(query: str, context: str) -> str:
    """Claude Haiku로 실제 질의"""
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": "당신은 사용자의 지역 비즈니스 검색 질의에 답하는 AI 어시스턴트입니다.",
        "messages": [{"role": "user", "content": query}],
    })
    resp = _get_bedrock().invoke_model(
        modelId=MODEL_ID_HAIKU, body=body,
        contentType="application/json", accept="application/json",
    )
    result = json.loads(resp["body"].read())
    return result["content"][0]["text"]


# ── OpenAI (ChatGPT) ──────────────────────────────────────────────────────────

async def _query_openai_async(query: str) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "사용자의 지역 비즈니스 검색에 답하세요."},
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": 512,
                },
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("OpenAI 쿼리 실패: %s", exc)
        return None


# ── Perplexity ────────────────────────────────────────────────────────────────

async def _query_perplexity_async(query: str) -> str | None:
    if not PERPLEXITY_API_KEY:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}"},
                json={
                    "model": "sonar",
                    "messages": [{"role": "user", "content": query}],
                    "max_tokens": 512,
                },
            )
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("Perplexity 쿼리 실패: %s", exc)
        return None


# ── 통합 실행 ─────────────────────────────────────────────────────────────────

async def _run_all_async(queries: list[str]) -> dict[str, list[str | None]]:
    results: dict[str, list[str | None]] = {"claude": [], "chatgpt": [], "perplexity": []}

    for query in queries:
        # Claude는 동기 클라이언트, 비동기로 래핑
        claude_resp = await asyncio.to_thread(_query_claude, query, "")
        openai_resp = await _query_openai_async(query)
        perplexity_resp = await _query_perplexity_async(query)

        results["claude"].append(claude_resp)
        results["chatgpt"].append(openai_resp)
        results["perplexity"].append(perplexity_resp)

    return results


def run_benchmark(queries: list[str], site_url: str, business_name: str) -> dict:
    """
    멀티 LLM 병렬 쿼리 실행 후 인용률 집계.
    반환: {llm_name: {citation_rate, responses}}
    """
    logger.info("멀티 LLM 벤치마크 시작: %d 질의, site=%s", len(queries), site_url)

    raw = asyncio.run(_run_all_async(queries))

    scores: dict[str, Any] = {}
    for llm_name, responses in raw.items():
        valid = [r for r in responses if r is not None]
        if not valid:
            scores[llm_name] = {"citation_rate": None, "skipped": True}
            continue

        cited = sum(1 for r in valid if _check_citation(r, site_url, business_name))
        scores[llm_name] = {
            "citation_rate": round(cited / len(valid), 2),
            "cited_count": cited,
            "total_queries": len(valid),
            "skipped": False,
            "responses": valid,  # wiki_updater.detect_stale_wiki 신선도 판단용
        }
        logger.info("LLM=%s 인용률: %.0f%% (%d/%d)",
                    llm_name, scores[llm_name]["citation_rate"] * 100, cited, len(valid))

    return scores
