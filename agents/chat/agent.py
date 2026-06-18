"""
P1 Chat Agent — HTTP service entrypoint.

Endpoints:
  POST /invoke
  POST /invocations
  POST /
  GET  /ping
  GET  /health
"""
from __future__ import annotations

import logging
import os
import pathlib
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

CURRENT_DIR = pathlib.Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from chat_http_handler import handle_agentcore_payload  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.chat")

app = FastAPI(title="HEZO P1 Chat Agent")


async def _handle_invoke(request: Request) -> JSONResponse:
    payload = await request.json()
    session_id = payload.get("sessionId", "")
    action = payload.get("sessionAttributes", {}).get("action", "graph_smoke")
    logger.info("chat agent invoked — sessionId=%s action=%s", session_id, action)

    try:
        return JSONResponse(handle_agentcore_payload(payload))
    except ValueError as exc:
        logger.exception("chat agent validation error: %s", exc)
        return JSONResponse({"error": "CHAT_AGENT_BAD_REQUEST", "message": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("chat agent error: %s", exc)
        return JSONResponse({"error": "CHAT_AGENT_ERROR", "message": str(exc)}, status_code=500)


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
    return JSONResponse({"status": "ok", "agent": "hezo-p1-chat-agent"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    if request.method == "POST":
        return await _handle_invoke(request)
    return JSONResponse({"path": path, "method": request.method}, status_code=200)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
