"""
리포트 에이전트 — Amazon Bedrock AgentCore Runtime 진입점 (MVP v2)

트리거: EventBridge Scheduler (hezo-report-{site_id}, rate 7 days)

MVP 흐름 (5단계 즉각 지표):
  1. GEO 파일 접근성 체크 (llms.txt, llms-full.txt, sitemap.xml, robots.txt, JSON-LD)
  2. AI 봇 크롤 감지 (CloudFront S3 로그)
  3. 구글 인덱싱 상태 추정
  4. 사이트 성능 측정 (응답시간 + PageSpeed)
  5. GEO 구조 점수 (룰 기반 0-100)
  → Claude Haiku 액션 아이템 생성
  → HTML 리포트 렌더링 + S3 저장 + DynamoDB 저장

추후 구현 (v1.1~):
  _legacy_generate_queries() — 업종 기반 LLM 질의셋 생성 (Haiku)
  _legacy_save_scores_to_dynamodb() — LLM 인용률 시계열 저장
  llm_querier.run_benchmark() — 멀티 LLM 병렬 쿼리
  wiki_updater.detect_stale_wiki() / request_recrawl() — wiki 재크롤 트리거
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

from agents.report.tools.site_fetcher import fetch_site_content
from agents.report.tools.report_saver import save_report
from agents.report.tools.geo_file_checker import check_geo_files
from agents.report.tools.bot_crawl_analyzer import analyze_bot_visits
from agents.report.tools.google_index_checker import check_google_indexing
from agents.report.tools.performance_checker import check_performance
from agents.report.tools.action_generator import generate_action_items
from agents.report.tools.report_renderer import render_html_report
from libs.telemetry import init_telemetry, record_llm_usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.report")

REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
DYNAMODB_TABLE = os.environ.get("REPORT_SCORES_TABLE", "report_scores")
PIPELINE_STATE_TABLE = os.environ.get("PIPELINE_STATE_TABLE", "hezo_pipeline_state")
REPORTS_BUCKET = os.environ.get("REPORTS_BUCKET", "hezo-reports")

init_telemetry("report", region=REGION)

app = FastAPI(title="HEZO Report Agent")

_bedrock: Any = None
_dynamodb: Any = None
_s3: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb", region_name=REGION)
    return _dynamodb


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# 보조: DynamoDB에서 발행일·CloudFront distribution ID 조회
# =============================================================================

def _get_days_since_publish(site_id: str) -> int:
    try:
        resp = _get_dynamodb().get_item(
            TableName=PIPELINE_STATE_TABLE,
            Key={"site_id": {"S": site_id}},
            ProjectionExpression="updated_at",
        )
        updated_at = resp.get("Item", {}).get("updated_at", {}).get("S", "")
        if updated_at:
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            return (datetime.now(timezone.utc) - dt).days
    except Exception as exc:
        logger.warning("발행일 조회 실패: %s", exc)
    return 0


def _get_cf_distribution_id(site_id: str) -> str:
    try:
        resp = _get_dynamodb().get_item(
            TableName=PIPELINE_STATE_TABLE,
            Key={"site_id": {"S": site_id}},
            ProjectionExpression="cf_distribution_id",
        )
        return resp.get("Item", {}).get("cf_distribution_id", {}).get("S", "")
    except Exception:
        return ""


def _get_previous_score(site_id: str) -> int | None:
    try:
        resp = _get_dynamodb().query(
            TableName=DYNAMODB_TABLE,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
            ExpressionAttributeValues={
                ":pk": {"S": f"SITE#{site_id}"},
                ":sk_prefix": {"S": "REPORT#"},
            },
            ScanIndexForward=False,
            Limit=2,
        )
        items = resp.get("Items", [])
        if len(items) >= 2:
            return int(items[1].get("overall_score", {}).get("N", "0"))
    except Exception as exc:
        logger.warning("이전 점수 조회 실패: %s", exc)
    return None


# =============================================================================
# 보조: report_scores DynamoDB 저장 + HTML S3 저장
# =============================================================================

def _save_scores_to_dynamodb(site_id: str, report: dict, html_key: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        _get_dynamodb().put_item(
            TableName=DYNAMODB_TABLE,
            Item={
                "pk": {"S": f"SITE#{site_id}"},
                "sk": {"S": f"REPORT#{today}"},
                "overall_score": {"N": str(report["overall_score"])},
                "delta": {"N": str(report.get("delta", 0))},
                "geo_file_score": {"N": str(report["geo_file_check"].get("summary_score", 0))},
                "performance_grade": {"S": report["performance"].get("performance_grade", "F")},
                "ssl_days_remaining": {"N": str(report["performance"].get("ssl_days_remaining") or 0)},
                "indexing_status": {"S": report["indexing"].get("indexing_status", "unknown")},
                "action_items": {"S": json.dumps(report["action_items"], ensure_ascii=False)},
                "report_html_key": {"S": html_key},
                "recorded_at": {"S": datetime.now(timezone.utc).isoformat() + "Z"},
            },
        )
        logger.info("DynamoDB report_scores 저장: SITE#%s REPORT#%s", site_id, today)
    except Exception as exc:
        logger.warning("DynamoDB 저장 실패: %s", exc)


def _save_html_to_s3(site_id: str, html: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"{site_id}/{today}/weekly_report.html"
    _get_s3().put_object(
        Bucket=REPORTS_BUCKET,
        Key=key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
    )
    logger.info("HTML 리포트 저장: s3://%s/%s", REPORTS_BUCKET, key)
    return key


# =============================================================================
# 추후 구현 — LLM 인용률 측정 (v1.1~)
# 인용률은 신규 사이트 6개월+ 후 의미 있으므로 보존만 하고 미사용
# =============================================================================

def _legacy_generate_queries(content: dict) -> list[str]:
    """업종·지역 기반 실제 AI 검색 질의 3~5개 생성 (추후 구현)"""
    slots = content["contract"].get("slots", {})
    business_type = slots.get("business_type", "")
    region = slots.get("address", "")
    business_name = slots.get("business_name", "")

    prompt = f"""'{business_name}'({region} {business_type}) 사업체에 관해
사용자가 ChatGPT/Claude/Perplexity에 실제로 물어볼 법한 검색 질의 5개를 생성하세요.
조건: 지역명 포함, 구체적 질문 (비용/절차/비교), 실제 사용자 검색 패턴 반영
다음 JSON 배열만 출력: ["질의1", "질의2", "질의3", "질의4", "질의5"]"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    start = time.monotonic()
    resp = _get_bedrock().invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000
    result = json.loads(resp["body"].read())

    _usage = result.get("usage", {})
    record_llm_usage("report", "haiku",
                     _usage.get("input_tokens", 0), _usage.get("output_tokens", 0), ms=elapsed)

    text = result["content"][0]["text"].strip()
    try:
        m = re.search(r"\[[\s\S]+\]", text)
        return json.loads(m.group() if m else text)
    except (json.JSONDecodeError, AttributeError):
        return [f"{business_type} {region} 추천", f"{business_type} 비용", f"{business_name} 서비스"]


def _legacy_save_citation_scores(site_id: str, scores: dict, queries: list[str]) -> None:
    """LLM 인용률 시계열 저장 (추후 구현)"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        _get_dynamodb().put_item(
            TableName=DYNAMODB_TABLE,
            Item={
                "pk": {"S": f"SITE#{site_id}"},
                "sk": {"S": f"CITATION#{today}"},
                "scores": {"S": json.dumps(scores, ensure_ascii=False)},
                "query_set": {"S": json.dumps(queries, ensure_ascii=False)},
                "recorded_at": {"S": datetime.now(timezone.utc).isoformat() + "Z"},
            },
        )
    except Exception as exc:
        logger.warning("인용률 저장 실패: %s", exc)


# =============================================================================
# 핵심 에이전트 로직 — MVP 5단계 흐름
# =============================================================================

def run_report(site_id: str) -> dict:
    logger.info("리포트 에이전트 시작 (MVP v2) — site_id=%s", site_id)

    content = fetch_site_content(site_id)
    domain_url = content.get("domain_url", "")
    slots = content["contract"].get("slots", {})
    business_name = slots.get("business_name", site_id)

    days_since_publish = _get_days_since_publish(site_id)
    cf_distribution_id = _get_cf_distribution_id(site_id)
    prev_score = _get_previous_score(site_id)

    logger.info("도메인: %s, 발행 %d일 경과", domain_url, days_since_publish)

    # ── 5단계 측정 ────────────────────────────────────────────────────────────
    logger.info("[1/4] GEO 파일 접근성 체크")
    geo_file_check = check_geo_files(domain_url)

    logger.info("[2/4] AI 봇 크롤 감지")
    bot_visits = analyze_bot_visits(cf_distribution_id)

    logger.info("[3/4] 구글 인덱싱 상태 추정")
    indexing = check_google_indexing(domain_url, days_since_publish)

    logger.info("[4/4] 사이트 성능 측정 (SSL 포함)")
    performance = check_performance(domain_url)

    # ── 종합 점수 (가중 평균) ─────────────────────────────────────────────────
    # geo_structure 제외 (검증 에이전트 통과 사이트는 항상 고점 → 자기참조 지표 무의미)
    # bot: Googlebot 대신 AI 봇(GPTBot/ClaudeBot/PerplexityBot/Yeti) 기준
    geo_file_score = geo_file_check.get("summary_score", 0)
    perf_score = {"A": 100, "B": 75, "C": 50, "F": 20}.get(
        performance.get("performance_grade", "F"), 50
    )
    index_score = indexing.get("indexing_likelihood_pct", 0)

    visits = bot_visits.get("visits", {})
    ai_bot_visits = sum(visits.get(b, 0) for b in ["GPTBot", "ClaudeBot", "PerplexityBot", "Yeti"])
    if ai_bot_visits > 0:
        bot_score = 100
    elif visits.get("Googlebot", 0) > 0:
        bot_score = 60
    else:
        bot_score = 20

    overall_score = round(
        geo_file_score * 0.40 +
        perf_score * 0.30 +
        index_score * 0.20 +
        bot_score * 0.10
    )
    delta = overall_score - prev_score if prev_score is not None else 0

    # ── 액션 아이템 ───────────────────────────────────────────────────────────
    logger.info("액션 아이템 생성 (Claude Haiku)")
    action_items = generate_action_items(
        geo_file_check, bot_visits, indexing, performance
    )

    # ── 리포트 조립 ───────────────────────────────────────────────────────────
    report = {
        "site_id": site_id,
        "business_name": business_name,
        "domain_url": domain_url,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "overall_score": overall_score,
        "delta": delta,
        "geo_file_check": geo_file_check,
        "bot_visits": bot_visits,
        "indexing": indexing,
        "performance": performance,
        "action_items": action_items,
    }

    # ── 저장 ──────────────────────────────────────────────────────────────────
    json_key = save_report(site_id, report)
    html = render_html_report(report)
    html_key = _save_html_to_s3(site_id, html)
    _save_scores_to_dynamodb(site_id, report, html_key)

    logger.info("리포트 완료 — site_id=%s, score=%d, delta=%+d", site_id, overall_score, delta)
    return {
        "status": "complete",
        "site_id": site_id,
        "overall_score": overall_score,
        "delta": delta,
        "report_json_key": json_key,
        "report_html_key": html_key,
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

async def _handle_invoke(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body) if body else {}
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
                f"score: {result['overall_score']}, delta: {result['delta']:+d}"
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
    return JSONResponse({"status": "ok", "agent": "hezo-report-agent-mvp"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
