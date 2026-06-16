"""
리포트 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

기본 OFF (SSM hezo-report-enabled=false).
외부 LLM 관점에서 사이트 answerability·discoverability 관찰.
배포 차단 경로 밖 — 비동기·비차단.

흐름:
  1. SSM flag 확인 (비활성화 시 즉시 반환)
  2. site_id 파싱
  3. 사이트 콘텐츠 로드 (llms.txt 우선)
  4. Claude Haiku로 평가 질문 3개 생성 + 시뮬레이션
  5. llm_report.json → S3 저장
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.report")

REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
# 리포트 에이전트는 비용 절감을 위해 Haiku 사용
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001")
SSM_FLAG_KEY = os.environ.get("SSM_FLAG_KEY", "hezo-report-enabled")

app = FastAPI(title="HEZO Report Agent")

_bedrock: Any = None
_ssm: Any = None


def get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def get_ssm():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", region_name=REGION)
    return _ssm


def is_report_enabled() -> bool:
    """SSM 파라미터로 리포트 기능 활성화 여부 확인"""
    try:
        resp = get_ssm().get_parameter(Name=SSM_FLAG_KEY)
        value = resp["Parameter"]["Value"].lower()
        return value in ("true", "1", "yes", "enabled")
    except Exception as exc:
        logger.warning("SSM flag 조회 실패 — 기본값 false 사용: %s", exc)
        return False


def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


def generate_report(content: dict) -> dict:
    """Claude Haiku로 LLM 관점 평가 질문 3개 생성 + 시뮬레이션"""
    slots = content["contract"].get("slots", {})
    business_name = slots.get("business_name", "")
    business_type = slots.get("business_type", "")
    region = slots.get("business_region", "")
    llms_txt = content["llms_txt"] or str(content["render_spec"])[:3000]

    prompt = f"""다음은 '{business_name}'({region} {business_type})의 사이트 콘텐츠입니다:

{llms_txt[:4000]}

이 비즈니스에 관해 사용자가 AI 검색(ChatGPT, Claude, Perplexity)에 물어볼 법한
질문 3개를 만들고, 위 콘텐츠만을 참고하여 각각 답변하세요.

다음 JSON 형식으로만 응답하세요:
{{
  "business_name": "{business_name}",
  "evaluated_at": "<ISO timestamp>",
  "queries": [
    {{
      "query": "<질문>",
      "answer": "<위 콘텐츠 기반 답변>",
      "answerability_score": <0~10 — 콘텐츠만으로 답변 가능한 정도>,
      "missing_context": "<답변하기 위해 필요했지만 없는 정보>"
    }}
  ],
  "overall_answerability": <0~10>,
  "recommendation": "<AI 검색 최적화를 위한 한 줄 권고사항>"
}}"""

    bedrock = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    })

    start = time.monotonic()
    resp = bedrock.invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000
    logger.info("Claude Haiku 호출 완료: %.0f ms", elapsed)

    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()

    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)

    report = json.loads(text)
    report["site_id"] = content["site_id"]
    report["generated_at"] = datetime.now(timezone.utc).isoformat() + "Z"
    return report


def run_report(site_id: str) -> dict:
    logger.info("리포트 에이전트 시작 — site_id=%s", site_id)

    content = fetch_site_content(site_id)
    report = generate_report(content)
    key = save_report(site_id, report)

    overall = report.get("overall_answerability", 0)
    logger.info("리포트 완료 — site_id=%s, answerability=%s, key=%s", site_id, overall, key)

    return {"status": "complete", "site_id": site_id, "report_key": key, "overall_answerability": overall}


@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    payload = await request.json()
    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("invoke 호출 — sessionId=%s", session_id)

    # SSM flag 확인
    if not is_report_enabled():
        logger.info("report_enabled=false — 리포트 건너뜀")
        return JSONResponse({
            "output": "report_skipped — report_enabled=false",
            "sessionState": {},
            "metadata": {"status": "skipped", "reason": "report_enabled=false"},
        })

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_report(site_id)
        return JSONResponse({
            "output": f"report_complete — site_id: {site_id}, answerability: {result['overall_answerability']}",
            "sessionState": {},
            "metadata": result,
        })
    except Exception as exc:
        logger.exception("리포트 에이전트 오류: %s", exc)
        return JSONResponse({"error": "REPORT_ERROR", "message": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-report-agent"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
