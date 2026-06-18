"""
리포트 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

트리거: EventBridge Scheduler (hezo-report-{site_id}, rate 7 days)
       SSM flag 없음 — Scheduler 등록 여부가 실행 제어.

흐름 (v2 에이전트 동작):
  1. site_id + domain_url 파싱
  2. 사이트 콘텐츠 로드 (llms.txt 우선)
  3. Claude Haiku로 업종 기반 질의셋 3~5개 생성
  4. [에이전트] 실제 멀티 LLM 병렬 쿼리 (Claude / ChatGPT / Perplexity)
  5. 인용률 집계 + DynamoDB 시계열 저장
  6. [에이전트] wiki 업데이트 트리거 (새 정보 감지 시 P2 재크롤 요청)
  7. llm_report.json → S3 저장
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.report.tools.report_saver import save_report
from agents.report.tools.site_fetcher import fetch_site_content
from agents.report.tools.llm_querier import run_benchmark
from agents.report.tools.wiki_updater import detect_stale_wiki, request_recrawl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.report")

REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001")
DYNAMODB_TABLE = os.environ.get("REPORT_SCORES_TABLE", "report_scores")

app = FastAPI(title="HEZO Report Agent")

_bedrock: Any = None
_dynamodb: Any = None


def get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb", region_name=REGION)
    return _dynamodb


def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# 질의셋 생성 (Claude Haiku — 업종 기반 실제 검색 쿼리 패턴)
# =============================================================================

def generate_queries(content: dict) -> list[str]:
    """업종·지역 기반 실제 AI 검색 질의 3~5개 생성"""
    slots = content["contract"].get("slots", {})
    business_type = slots.get("business_type", "")
    region = slots.get("address", "")
    business_name = slots.get("business_name", "")

    prompt = f"""'{business_name}'({region} {business_type}) 사업체에 관해
사용자가 ChatGPT/Claude/Perplexity에 실제로 물어볼 법한 검색 질의 5개를 생성하세요.

조건:
- 지역명 포함 (예: '강남 치과 임플란트 비용')
- 구체적인 질문 (비용, 절차, 비교 등)
- 실제 사용자 검색 패턴 반영

다음 JSON 배열만 출력:
["질의1", "질의2", "질의3", "질의4", "질의5"]"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = get_bedrock().invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    text = json.loads(resp["body"].read())["content"][0]["text"].strip()
    try:
        m = re.search(r"\[[\s\S]+\]", text)
        return json.loads(m.group() if m else text)
    except (json.JSONDecodeError, AttributeError):
        return [f"{business_type} {region} 추천", f"{business_type} 비용", f"{business_name} 서비스"]


# =============================================================================
# DynamoDB 시계열 점수 저장
# =============================================================================

def save_scores_to_dynamodb(site_id: str, scores: dict, queries: list[str]) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        get_dynamodb().put_item(
            TableName=DYNAMODB_TABLE,
            Item={
                "pk": {"S": f"SITE#{site_id}"},
                "sk": {"S": f"REPORT#{today}"},
                "scores": {"S": json.dumps(scores, ensure_ascii=False)},
                "query_set": {"S": json.dumps(queries, ensure_ascii=False)},
                "recorded_at": {"S": datetime.now(timezone.utc).isoformat() + "Z"},
            },
        )
        logger.info("DynamoDB report_scores 저장: SITE#%s REPORT#%s", site_id, today)
    except Exception as exc:
        logger.warning("DynamoDB 저장 실패 (non-critical): %s", exc)


# =============================================================================
# 핵심 에이전트 로직
# =============================================================================

def run_report(site_id: str) -> dict:
    logger.info("리포트 에이전트 시작 — site_id=%s", site_id)

    # 1. 사이트 콘텐츠 로드
    content = fetch_site_content(site_id)
    slots = content["contract"].get("slots", {})
    business_name = slots.get("business_name", "")
    business_type = slots.get("business_type", "")
    domain_url = content.get("domain_url", f"https://{site_id}.hezo.io")

    # 2. 질의셋 생성
    queries = generate_queries(content)
    logger.info("질의셋 생성 완료: %d개", len(queries))

    # 3. [에이전트] 실제 멀티 LLM 병렬 쿼리
    scores = run_benchmark(queries, site_url=domain_url, business_name=business_name)

    # 4. DynamoDB 시계열 저장
    save_scores_to_dynamodb(site_id, scores, queries)

    # 5. 평균 인용률 계산
    valid_rates = [
        v["citation_rate"] for v in scores.values()
        if isinstance(v.get("citation_rate"), float)
    ]
    avg_citation = round(sum(valid_rates) / len(valid_rates), 2) if valid_rates else 0.0
    overall_answerability = round(avg_citation * 10, 1)

    # 6. [에이전트] wiki 업데이트 트리거
    wiki_snapshot = content.get("wiki_snapshot")
    llm_responses_raw = {
        llm: data.get("responses", []) if isinstance(data, dict) else []
        for llm, data in scores.items()
    }
    stale_reason = detect_stale_wiki(llm_responses_raw, wiki_snapshot, business_type)
    wiki_updated = False
    if stale_reason:
        wiki_updated = request_recrawl(business_type, stale_reason, site_id)
        logger.info("wiki 재크롤 요청: domain=%s reason=%s", business_type, stale_reason)

    # 7. 리포트 저장
    report = {
        "site_id": site_id,
        "business_name": business_name,
        "business_type": business_type,
        "domain_url": domain_url,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "queries": queries,
        "llm_scores": scores,
        "avg_citation_rate": avg_citation,
        "overall_answerability": overall_answerability,
        "wiki_recrawl_triggered": wiki_updated,
        "wiki_recrawl_reason": stale_reason,
    }
    key = save_report(site_id, report)

    logger.info(
        "리포트 완료 — site_id=%s, avg_citation=%.0f%%, answerability=%.1f, wiki_updated=%s",
        site_id, avg_citation * 100, overall_answerability, wiki_updated,
    )

    return {
        "status": "complete",
        "site_id": site_id,
        "report_key": key,
        "overall_answerability": overall_answerability,
        "avg_citation_rate": avg_citation,
        "llm_scores": scores,
        "wiki_recrawl_triggered": wiki_updated,
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

async def _handle_invoke(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = __import__("json").loads(body) if body else {}
    except Exception:
        payload = {}

    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("invoke 호출 — sessionId=%s", session_id)

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_report(site_id)
        return JSONResponse({
            "output": (
                f"report_complete — site_id: {site_id}, "
                f"answerability: {result['overall_answerability']}, "
                f"avg_citation: {result['avg_citation_rate']:.0%}"
            ),
            "sessionState": {},
            "metadata": result,
        })
    except Exception as exc:
        logger.exception("리포트 에이전트 오류: %s", exc)
        return JSONResponse({"error": "REPORT_ERROR", "message": str(exc)}, status_code=500)


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.get("/ping")
async def ping() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-report-agent"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
