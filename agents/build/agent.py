"""
P3 빌드 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

흐름:
  1. site_id 파싱
  2. render_spec.json + template_category 로드 (S3)
  3. 로컬 HTML 템플릿 파일 로드
  4. 서버사이드 렌더링 (데이터 baked-in)
  5. dist/index.html + static/*.css → S3 업로드
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.build.renderer.html_renderer import render
from agents.build.renderer.template_map import resolve_template_filename
from agents.build.tools.render_spec_loader import load_render_spec
from agents.build.tools.site_uploader import upload_site

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.build")

app = FastAPI(title="HEZO Build Agent")

_TEMPLATES_ROOT = Path(__file__).parent / "templates"

# CSS 참조 파일명 목록
_KNOWN_CSS = ["template-expansion.css", "blog-premium.css", "templates.css", "templates-v2.css"]


# =============================================================================
# 템플릿 로더
# =============================================================================

def load_template(template_id: str, template_category: str) -> str:
    """로컬 templates/ 디렉터리에서 HTML 템플릿 로드"""
    category = template_category.lower().strip()
    # "multi" → "blog" 정규화
    if category == "multi":
        category = "blog"

    resolved = resolve_template_filename(template_id, category)
    path = _TEMPLATES_ROOT / category / f"{resolved}.html"

    if not path.exists():
        raise FileNotFoundError(f"템플릿을 찾을 수 없음: {path}")

    if resolved != template_id:
        logger.info("템플릿 매핑: %s → %s", template_id, resolved)

    return path.read_text(encoding="utf-8")


def detect_required_css(template_html: str) -> list[str]:
    """템플릿 HTML에서 참조하는 static CSS 파일명 목록 추출"""
    soup = BeautifulSoup(template_html, "html.parser")
    needed: list[str] = []
    for link in soup.find_all("link", rel="stylesheet"):
        href = link.get("href", "")
        for css in _KNOWN_CSS:
            if css in href and css not in needed:
                needed.append(css)
    return needed


# =============================================================================
# site_id 파싱
# =============================================================================

def parse_site_id(input_text: str, session_attrs: dict) -> str:
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# 핵심 빌드 로직
# =============================================================================

def run_build(site_id: str) -> dict:
    logger.info("빌드 에이전트 시작 — site_id=%s", site_id)

    # 1. render_spec + template_category 로드
    loaded = load_render_spec(site_id)
    render_spec = loaded["render_spec"]
    template_category = loaded["template_category"]
    template_id = render_spec.get("template_id", "")

    logger.info("template_id=%s, category=%s", template_id, template_category)

    # 2. HTML 템플릿 로드
    template_html = load_template(template_id, template_category)

    # 3. CSS 파일 목록 확인 (렌더링 전, 원본 기준)
    css_needed = detect_required_css(template_html)

    # 4. 서버사이드 렌더링
    rendered_html = render(template_html, render_spec)
    logger.info("렌더링 완료 — HTML 크기: %d bytes", len(rendered_html.encode()))

    # 5. S3 업로드
    upload_result = upload_site(site_id, rendered_html, css_needed)

    logger.info(
        "빌드 완료 — site_id=%s, 파일 수: %d, CSS: %s",
        site_id, len(upload_result["uploaded_files"]), css_needed,
    )

    return {
        "status": "complete",
        "site_id": site_id,
        "template_id": template_id,
        "template_category": template_category,
        "uploaded_files": upload_result["uploaded_files"],
        "css_files": css_needed,
        "dist_prefix": upload_result["dist_prefix"],
        "html_size_bytes": len(rendered_html.encode()),
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    payload = await request.json()
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("invoke 호출 — inputText=%r", input_text[:120])

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_build(site_id)
        output_text = (
            f"build_complete — site_id: {site_id}, "
            f"files: {len(result['uploaded_files'])}, "
            f"template: {result['template_id']}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})

    except Exception as exc:
        logger.exception("빌드 에이전트 오류: %s", exc)
        return JSONResponse({"error": "BUILD_ERROR", "message": str(exc)}, status_code=500)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-build-agent"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
