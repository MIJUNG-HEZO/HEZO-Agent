"""
검증 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

흐름 (v2 에이전트 동작):
  1. site_id 파싱
  2. 빌드 산출물 + hezo-wiki MD 로드
  3. 레이어 실행 (비용 역순: Layer 3 → 2 → 1)
  4. PASS → 리포트 저장 → 완료
  5. FAIL_BLOCKING → render_spec 직접 패치 → P3 재빌드 → 재검증 (최대 3회 내부 루프)
  6. 3회 초과 시 FAIL 확정 → validation_feedback.json 저장
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
from agents.validation.tools.p3_rebuilder import trigger_and_wait as p3_rebuild
from agents.validation.tools.render_spec_patcher import patch as patch_render_spec
from agents.validation.tools.validation_saver import (
    save_validation_feedback,
    save_validation_report,
)
from libs.telemetry import init_telemetry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.validation")

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MAX_PATCH_ATTEMPTS = int(os.environ.get("MAX_PATCH_ATTEMPTS", "3"))

# ─── 관측 (P5 telemetry) — 에이전트별 토큰·비용을 CloudWatch로 직접 전송 ───
init_telemetry("validation", region=REGION)

app = FastAPI(title="HEZO Validation Agent")


def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


def _run_layers(artifacts: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Layer 3 → 2 → 1 순서로 실행 (비용 역순).
    반환: (layer3_issues, layer2_issues, layer1_issues)
    """
    contract = artifacts["contract"]
    render_spec = artifacts["render_spec"]
    wiki_snapshot = artifacts.get("wiki_snapshot")
    html_content = artifacts["html"]
    file_list = artifacts["file_list"]

    # Layer 3: BeautifulSoup 구조 검증 (비용 0, 가장 빠름)
    layer3_issues = check_layer3(html_content, file_list)
    logger.info("Layer 3 완료: %d 이슈", len(layer3_issues))

    # Layer 2: 요구사항 정합성 (결정론적, 비용 0)
    layer2_issues = check_layer2(contract, render_spec)
    logger.info("Layer 2 완료: %d 이슈", len(layer2_issues))

    # Layer 1: wiki 커버리지 (Bedrock 호출 — Layer 3·2 통과 후에만)
    layer1_issues: list[dict] = []
    l3_blocking = [i for i in layer3_issues if i.get("level") == "blocking"]
    l2_blocking = [i for i in layer2_issues if i.get("level") == "blocking"]

    if not l3_blocking and not l2_blocking and wiki_snapshot:
        layer1_issues = check_layer1(contract, wiki_snapshot, html_content)
        logger.info("Layer 1 완료: %d 이슈", len(layer1_issues))
    elif not wiki_snapshot:
        logger.info("Layer 1 건너뜀 — wiki 없음")
    else:
        logger.info("Layer 1 건너뜀 — Layer 3·2 blocking 존재 (Bedrock 비용 절감)")

    return layer3_issues, layer2_issues, layer1_issues


def run_validation(site_id: str) -> dict[str, Any]:
    logger.info("검증 에이전트 시작 — site_id=%s", site_id)

    for attempt in range(1, MAX_PATCH_ATTEMPTS + 1):
        logger.info("검증 시도 %d/%d — site_id=%s", attempt, MAX_PATCH_ATTEMPTS, site_id)

        # 산출물 로드 (패치 후 재빌드 시 최신 HTML 반영)
        artifacts = fetch_artifacts(site_id)
        contract = artifacts["contract"]
        render_spec = artifacts["render_spec"]

        layer3_issues, layer2_issues, layer1_issues = _run_layers(artifacts)

        all_issues = layer3_issues + layer2_issues + layer1_issues
        blocking = [i for i in all_issues if i.get("level") == "blocking"]
        warnings = [i for i in all_issues if i.get("level") == "warning"]

        ai_score = calculate_ai_score(all_issues)

        # ── PASS ──────────────────────────────────────────────────────────────
        if not blocking:
            status = "PASS_WITH_WARNINGS" if warnings else "PASS"
            report = _build_report(
                site_id, status, ai_score, blocking, warnings,
                layer3_issues, layer2_issues, layer1_issues,
                attempt=attempt,
            )
            key = save_validation_report(site_id, report)
            logger.info("검증 PASS — site_id=%s, status=%s, score=%d, attempt=%d",
                        site_id, status, ai_score, attempt)
            return {
                "validation_status": status,
                "ai_visibility_score": ai_score,
                "publish_ready": True,
                "report_key": key,
                "blocking_count": 0,
                "warning_count": len(warnings),
                "attempts": attempt,
            }

        # ── FAIL_BLOCKING ─────────────────────────────────────────────────────
        logger.warning("FAIL_BLOCKING: %d건 — attempt=%d", len(blocking), attempt)

        if attempt >= MAX_PATCH_ATTEMPTS:
            # 3회 초과 → 최종 실패
            report = _build_report(
                site_id, "FAIL_BLOCKING", ai_score, blocking, warnings,
                layer3_issues, layer2_issues, layer1_issues, attempt=attempt,
            )
            save_validation_report(site_id, report)
            feedback_key = save_validation_feedback(site_id, blocking, attempt)
            logger.error("검증 최종 실패 (attempt=%d) — site_id=%s", attempt, site_id)
            return {
                "validation_status": "FAIL_BLOCKING",
                "ai_visibility_score": ai_score,
                "publish_ready": False,
                "report_key": None,
                "feedback_key": feedback_key,
                "blocking_count": len(blocking),
                "warning_count": len(warnings),
                "attempts": attempt,
            }

        # ── 패치 + P3 재빌드 ──────────────────────────────────────────────────
        logger.info("render_spec 직접 패치 시작 — blocking=%d건", len(blocking))
        try:
            patch_render_spec(site_id, render_spec, blocking, contract)
        except Exception as exc:
            logger.error("render_spec 패치 실패: %s — 다음 시도로 넘어감", exc)
            continue

        logger.info("P3 재빌드 트리거 — site_id=%s", site_id)
        rebuild_ok = p3_rebuild(site_id, mode="publish")
        if not rebuild_ok:
            logger.error("P3 재빌드 실패 — attempt=%d", attempt)
            continue

        logger.info("P3 재빌드 완료 — attempt=%d, 재검증 시작", attempt)
        # 루프 상단으로 돌아가 재검증

    # 루프 종료 (이론상 도달하지 않음)
    return {
        "validation_status": "FAIL_BLOCKING",
        "ai_visibility_score": 0,
        "publish_ready": False,
        "blocking_count": -1,
        "warning_count": 0,
        "attempts": MAX_PATCH_ATTEMPTS,
    }


def _build_report(
    site_id: str, status: str, ai_score: int,
    blocking: list, warnings: list,
    layer3: list, layer2: list, layer1: list,
    attempt: int,
) -> dict:
    return {
        "site_id": site_id,
        "validation_status": status,
        "publish_ready": len(blocking) == 0,
        "ai_visibility_score": ai_score,
        "attempts": attempt,
        "layers": {
            "layer3_ai_friendly": {
                "status": "PASS" if not any(i["level"] == "blocking" for i in layer3) else "FAIL_BLOCKING",
                "issues": layer3,
                "order": 1,
            },
            "layer2_requirements": {
                "status": "PASS" if not any(i["level"] == "blocking" for i in layer2) else "FAIL_BLOCKING",
                "issues": layer2,
                "order": 2,
            },
            "layer1_info_preservation": {
                "status": "PASS" if not layer1 else "PASS_WITH_WARNINGS",
                "issues": layer1,
                "skipped": not bool(layer1),
                "order": 3,
            },
        },
        "blocking_issues": blocking,
        "warnings": warnings,
        "checked_at": datetime.now(timezone.utc).isoformat() + "Z",
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
        result = run_validation(site_id)
        output_text = (
            f"validation_complete — site_id: {site_id}, "
            f"status: {result['validation_status']}, "
            f"score: {result.get('ai_visibility_score', 0)}, "
            f"attempts: {result.get('attempts', 1)}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})
    except Exception as exc:
        logger.exception("검증 에이전트 오류: %s", exc)
        return JSONResponse({"error": "VALIDATION_ERROR", "message": str(exc)}, status_code=500)


# Bug Fix: AgentCore 표준 경로 /invocations + /ping 추가
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
    return JSONResponse({"status": "ok", "agent": "hezo-validation-agent"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
