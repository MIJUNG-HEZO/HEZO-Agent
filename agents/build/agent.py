"""
P3 빌드 워커 — HTTP 서비스 진입점 (Step Functions HTTP Task / Backend API 호환)

엔드포인트:
  POST /invoke        — 일반 호출
  POST /invocations   — AgentCore Runtime 호환
  GET  /ping          — 헬스체크
  GET  /health        — 헬스체크 (상세)

요청 형식 (AgentCore 표준):
  {
    "sessionId": "...",
    "inputText": "site_id=xxx mode=publish",
    "sessionAttributes": {
      "site_id": "xxx",
      "mode": "publish"        # 생략 시 "publish"
    }
  }

응답 (Step Functions ResultSelector 대상):
  {
    "output": "build_complete — site_id: xxx, mode: publish, files: 12",
    "sessionState": {},
    "metadata": { ...run() 반환값... }
  }
"""
from __future__ import annotations

import logging
import os
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.build.build_worker import run as build_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.build")

app = FastAPI(title="HEZO P3 Build Worker")


def _parse_params(input_text: str, session_attrs: dict) -> tuple[str, str]:
    """(site_id, mode) 파싱. mode 기본값 'publish'."""
    site_id = session_attrs.get("site_id", "")
    mode = session_attrs.get("mode", "")

    if not site_id:
        m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
        if m:
            site_id = m.group(1).strip()

    if not mode:
        m = re.search(r"mode[=:\s]+(preview|publish)", input_text)
        mode = m.group(1) if m else "publish"

    if not site_id:
        raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")

    return site_id, mode


async def _handle_invoke(request: Request) -> JSONResponse:
    payload = await request.json()
    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("빌드 워커 호출 — sessionId=%s", session_id)

    try:
        site_id, mode = _parse_params(input_text, session_attrs)
        result = build_run(site_id, mode=mode)

        file_count = len(result.get("uploaded_files", []))
        geo_count = len(result.get("geo_files", []))
        output_text = (
            f"build_complete — site_id: {site_id}, mode: {mode}, "
            f"files: {file_count}, geo: {geo_count}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})

    except Exception as exc:
        logger.exception("빌드 워커 오류: %s", exc)
        return JSONResponse({"error": "BUILD_ERROR", "message": str(exc)}, status_code=500)


@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.post("/")
async def invoke_root(request: Request) -> JSONResponse:
    return await _handle_invoke(request)


@app.get("/ping")
async def ping() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-p3-build-worker"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    if request.method == "POST":
        return await _handle_invoke(request)
    return JSONResponse({"path": path, "method": request.method}, status_code=200)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
