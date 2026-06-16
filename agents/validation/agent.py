"""
검증 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

흐름:
  1. site_id 파싱
  2. 빌드 산출물 로드 (S3)
  3. Layer 1 (정보 보존, LLM — crawl_snapshot 있을 때만)
  4. Layer 2 (요구사항 정합성, 결정론적)
  5. Layer 3 (AI 친화 구조, BeautifulSoup)
  6. AI 가시성 점수 계산
  7. PASS / PASS_WITH_WARNINGS / FAIL_BLOCKING 판정
  8. validation_report.json → S3 저장
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.validation.evaluators.ai_visibility_scorer import calculate_ai_score
from agents.validation.rule_engine.layer1_info_preservation import check_layer1
from agents.validation.rule_engine.layer2_requirements import check_layer2
from agents.validation.rule_engine.layer3_ai_friendly import check_layer3
from agents.validation.tools.artifact_fetcher import fetch_artifacts
from agents.validation.tools.validation_saver import save_validation_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.validation")

app = FastAPI(title="HEZO Validation Agent")


def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


def run_validation(site_id: str) -> dict[str, Any]:
    logger.info("검증 에이전트 시작 — site_id=%s", site_id)

    artifacts = fetch_artifacts(site_id)
    contract = artifacts["contract"]
    render_spec = artifacts["render_spec"]
    crawl_snapshot = artifacts.get("crawl_snapshot")
    html_content = artifacts["html"]
    file_list = artifacts["file_list"]

    all_issues: list[dict] = []

    # Layer 1: 정보 보존 (LLM — crawl_snapshot 있을 때만)
    layer1_issues: list[dict] = []
    if crawl_snapshot:
        layer1_issues = check_layer1(contract, crawl_snapshot, html_content)
    all_issues.extend(layer1_issues)

    # Layer 2: 요구사항 정합성 (결정론적)
    layer2_issues = check_layer2(contract, render_spec)
    all_issues.extend(layer2_issues)

    # Layer 3: AI 친화 구조 (BeautifulSoup)
    layer3_issues = check_layer3(html_content, file_list)
    all_issues.extend(layer3_issues)

    # 점수 계산
    ai_score = calculate_ai_score(all_issues)

    # 판정
    blocking = [i for i in all_issues if i.get("level") == "blocking"]
    warnings = [i for i in all_issues if i.get("level") == "warning"]

    if blocking:
        status = "FAIL_BLOCKING"
    elif warnings:
        status = "PASS_WITH_WARNINGS"
    else:
        status = "PASS"

    report = {
        "site_id": site_id,
        "validation_status": status,
        "publish_ready": len(blocking) == 0,
        "ai_visibility_score": ai_score,
        "layers": {
            "layer1_info_preservation": {
                "status": "PASS" if not layer1_issues else "PASS_WITH_WARNINGS",
                "issues": layer1_issues,
                "skipped": not bool(crawl_snapshot),
            },
            "layer2_requirements": {
                "status": "PASS" if not any(i["level"] == "blocking" for i in layer2_issues) else "FAIL_BLOCKING",
                "issues": layer2_issues,
            },
            "layer3_ai_friendly": {
                "status": "PASS" if not layer3_issues else ("FAIL_BLOCKING" if blocking else "PASS_WITH_WARNINGS"),
                "issues": layer3_issues,
            },
        },
        "blocking_issues": blocking,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat() + "Z",
    }

    key = save_validation_report(site_id, report)

    logger.info(
        "검증 완료 — site_id=%s, status=%s, score=%d, blocking=%d, warnings=%d",
        site_id, status, ai_score, len(blocking), len(warnings),
    )

    return {
        "validation_status": status,
        "ai_visibility_score": ai_score,
        "publish_ready": len(blocking) == 0,
        "report_key": key,
        "blocking_count": len(blocking),
        "warning_count": len(warnings),
    }


@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    payload = await request.json()
    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("invoke 호출 — sessionId=%s", session_id)

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_validation(site_id)
        output_text = (
            f"validation_complete — site_id: {site_id}, "
            f"status: {result['validation_status']}, "
            f"score: {result['ai_visibility_score']}, "
            f"publish_ready: {result['publish_ready']}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})
    except Exception as exc:
        logger.exception("검증 에이전트 오류: %s", exc)
        return JSONResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-validation-agent"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
