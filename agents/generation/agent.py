"""
생성 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

AgentCore Runtime HTTP 핸들러:
  POST /invoke  { sessionId, inputText, sessionAttributes }
  → { output, sessionState }

흐름:
  1. site_id 파싱 (inputText 또는 sessionAttributes)
  2. contract_final.json 로드 (S3)
  3. generation_ready 게이트 확인
  4. Claude Sonnet 호출 → render_spec JSON 생성
  5. 평가기 (최대 2회 재시도)
  6. 가드레일 검사
  7. render_spec.json → S3 저장 (GEO 파일 4종은 P3 렌더링 워커가 생성)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agents.generation.evaluators.render_spec_evaluator import evaluate_render_spec
from agents.generation.guardrails.content_guardrail import (
    GuardrailViolation,
    check_guardrails,
)
from agents.generation.tools.contract_loader import load_contract
from agents.generation.tools.render_spec_saver import save_render_spec

# ─── 로깅 ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.generation")

# ─── 환경변수 ────────────────────────────────────────────────────────────────
REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
QUALITY_THRESHOLD = int(os.environ.get("QUALITY_THRESHOLD", "70"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))

# ─── FastAPI 앱 ───────────────────────────────────────────────────────────────
app = FastAPI(title="HEZO Generation Agent")

# ─── Bedrock 클라이언트 ───────────────────────────────────────────────────────
_bedrock: Any = None


def get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


# =============================================================================
# site_id 파싱
# =============================================================================

def parse_site_id(input_text: str, session_attrs: dict) -> str:
    """inputText 또는 sessionAttributes에서 site_id 추출"""
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# Claude 호출 — render_spec 생성
# =============================================================================

_SYSTEM_PROMPT = """당신은 HEZO의 AI 친화 홈페이지 생성 전문가입니다.

주어진 Contract JSON을 분석하여 render_spec.json을 생성하세요.

## 출력 형식
반드시 순수 JSON만 출력하세요. 마크다운 코드 블록(```json)이나 설명 텍스트를 포함하지 마세요.
JSON 시작 전 또는 후에 어떤 텍스트도 추가하지 마세요.

## render_spec 구조
{
  "schema_version": "1.0.0",
  "site_id": "<contract의 ids.site_id>",
  "template_id": "<contract의 template.template_id>",
  "pages": [
    {
      "path": "/",
      "title_h1": "<H1 — 페이지당 정확히 1개>",
      "h2_list": ["<FAQ 질문형 H2 5~7개>"],
      "seo": {
        "title": "<SEO 타이틀 60자 이내>",
        "description": "<메타 디스크립션 160자 이내>",
        "canonical": "<https://{template_slug}.hezo.io/>",
        "target_keywords": ["<키워드 3~5개>"],
        "og": {
          "title": "<OG 타이틀>",
          "description": "<OG 설명>",
          "image": "<https://{slug}.hezo.io/og-thumb.jpg>",
          "type": "website",
          "url": "<canonical URL>"
        },
        "twitter": {
          "card": "summary_large_image",
          "title": "<트위터 타이틀>",
          "description": "<트위터 설명>"
        }
      },
      "jsonld": [
        {
          "@context": "https://schema.org",
          "@type": "<업종별 Schema.org 타입>",
          "name": "<업체명>",
          "description": "<업체 설명>",
          "address": { "@type": "PostalAddress", "addressLocality": "<지역>", "addressCountry": "KR" },
          "telephone": "<전화번호>",
          "openingHours": "<영업시간>"
        },
        {
          "@context": "https://schema.org",
          "@type": "FAQPage",
          "mainEntity": [
            { "@type": "Question", "name": "<질문>", "acceptedAnswer": { "@type": "Answer", "text": "<답변>" } }
          ]
        }
      ],
      "blocks": [
        { "type": "Hero", "h1": "<H1>", "subtext": "<부제목>", "cta_text": "<CTA>", "cta_href": "#contact" },
        { "type": "Services", "items": [{ "name": "<서비스명>", "desc": "<서비스 설명>", "label": "<약어>" }] },
        { "type": "FAQ", "module_key": "<업종>", "items": [{ "q": "<질문>", "a": "<답변>" }] },
        { "type": "QuickAnswer", "text": "<업체 한줄 요약 — 50~120자>" },
        { "type": "Contact", "phone": "<전화>", "kakao": "<카카오>", "hours": "<영업시간>" }
      ]
    }
  ],
  "supplementary_files": {
    "llms_txt": "# <업체명>\\n> <업종> | <지역>\\n\\n## 서비스\\n- <서비스1>\\n...",
    "llms_full_txt": "<상세 설명 전체>",
    "sitemap_pages": [{ "path": "/", "priority": 1.0, "changefreq": "monthly" }],
    "robots_rules": [
      "User-agent: GPTBot", "Allow: /",
      "User-agent: ClaudeBot", "Allow: /",
      "User-agent: PerplexityBot", "Allow: /",
      "User-agent: *", "Allow: /",
      "Sitemap: https://<slug>.hezo.io/sitemap.xml"
    ]
  },
  "build_manifest": {
    "s3_artifact_bucket": "hezo-artifacts",
    "s3_site_bucket": "hezo-sites",
    "s3_key_prefix": "sites/<site_id>/"
  }
}

## 업종 → Schema.org 타입
tax_accounting → Accountant
medical_clinic → MedicalClinic
dental_clinic  → Dentist
law_firm       → LegalService
restaurant     → FoodEstablishment
fitness        → SportsActivityLocation
salon/nail     → BeautySalon
real_estate    → RealEstateAgent
education      → EducationalOrganization
기타           → LocalBusiness

## BLOCKING 조건 (반드시 준수)
- H1: 페이지당 정확히 1개
- FAQ: 최소 5개 (h2_list와 jsonld.FAQPage.mainEntity 모두 5개 이상)
- llms_txt 필수 생성
- robots_rules에 GPTBot/ClaudeBot/PerplexityBot Allow 필수
- FAQPage JSON-LD 필수
"""


def call_claude(contract: dict, crawl_snapshot: dict | None, issues_hint: list[str] | None = None) -> dict:
    """Claude Sonnet 호출 → render_spec dict 반환"""
    user_content = f"Contract JSON:\n{json.dumps(contract, ensure_ascii=False, indent=2)}"
    if crawl_snapshot:
        snap_str = json.dumps(crawl_snapshot, ensure_ascii=False)[:4000]
        user_content += f"\n\nCrawl Snapshot (참고용):\n{snap_str}"
    if issues_hint:
        user_content += f"\n\n이전 생성에서 발견된 이슈 — 이 부분을 개선하세요:\n" + "\n".join(f"- {i}" for i in issues_hint)

    bedrock = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 8192,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_content}],
    })

    start = time.monotonic()
    resp = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000
    logger.info("Claude 호출 완료: %.0f ms", elapsed)

    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()

    # JSON 추출 (마크다운 코드 블록 처리)
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)

    return json.loads(text)


# =============================================================================
# 핵심 에이전트 로직
# =============================================================================

def run_generation(site_id: str) -> dict:
    """생성 에이전트 메인 로직"""
    logger.info("생성 에이전트 시작 — site_id=%s", site_id)

    # 1. Contract 로드
    loaded = load_contract(site_id)
    contract = loaded["contract"]
    crawl_snapshot = loaded.get("crawl_snapshot")

    # 2. generation_ready 게이트
    if not contract.get("gates", {}).get("generation_ready", False):
        logger.warning("generation_ready=false — 생성 건너뜀")
        return {"status": "skipped", "reason": "generation_ready=false", "site_id": site_id}

    # 3. Claude 호출 + 평가 루프
    render_spec: dict | None = None
    eval_result: dict = {}
    issues_hint: list[str] | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info("Claude 호출 시도 %d/%d", attempt, MAX_RETRIES)
        try:
            render_spec = call_claude(contract, crawl_snapshot, issues_hint)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Claude 응답 파싱 실패 (시도 %d): %s", attempt, exc)
            if attempt == MAX_RETRIES:
                raise
            issues_hint = [f"이전 응답이 유효한 JSON이 아니었음: {exc}"]
            continue

        eval_result = evaluate_render_spec(render_spec, threshold=QUALITY_THRESHOLD)
        logger.info("평가 결과: score=%d, issues=%d", eval_result["score"], eval_result["issue_count"])

        if eval_result["passed"]:
            break

        if attempt < MAX_RETRIES:
            issues_hint = eval_result["issues"]
            logger.warning("품질 임계값 미달 (score=%d) — 재시도", eval_result["score"])
        else:
            logger.warning("최대 재시도 도달 — score=%d 로 진행", eval_result["score"])

    if render_spec is None:
        raise RuntimeError("render_spec 생성 실패")

    # 4. 가드레일
    check_guardrails(render_spec)

    # 5. S3 저장
    save_result = save_render_spec(site_id, render_spec)
    logger.info(
        "생성 완료 — site_id=%s, score=%d, s3_key=%s",
        site_id, eval_result.get("score", 0), save_result["s3_key"],
    )

    return {
        "status": "complete",
        "site_id": site_id,
        "render_spec_key": save_result["s3_key"],
        "eval_score": eval_result.get("score", 0),
        "page_count": save_result["page_count"],
        "saved_at": save_result["saved_at"],
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

async def _handle_invoke(request: Request) -> JSONResponse:
    """AgentCore Runtime 공통 핸들러"""
    body = await request.body()
    logger.info("요청 경로: %s %s (body_len=%d)", request.method, request.url.path, len(body))

    try:
        payload = __import__("json").loads(body) if body else {}
    except Exception:
        payload = {}

    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})

    logger.info("invoke 호출 — sessionId=%s, inputText=%r", session_id, input_text[:120] if input_text else "")

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_generation(site_id)
        output_text = (
            f"render_spec_saved — site_id: {site_id}, eval_score: {result.get('eval_score', 0)}"
            if result.get("status") == "complete"
            else f"generation_skipped — {result.get('reason', '')}"
        )
        return JSONResponse({"output": output_text, "sessionState": {}, "metadata": result})

    except GuardrailViolation as exc:
        logger.error("가드레일 위반: %s — %s", exc.code, exc.detail)
        return JSONResponse({"error": exc.code, "message": exc.detail}, status_code=422)
    except Exception as exc:
        logger.exception("생성 에이전트 오류: %s", exc)
        return JSONResponse({"error": "GENERATION_ERROR", "message": str(exc)}, status_code=500)


# AgentCore Runtime이 호출하는 경로 후보 모두 등록
@app.post("/invoke")
async def invoke(request: Request) -> JSONResponse:
    logger.info("invoke 호출 — 경로: /invoke")
    return await _handle_invoke(request)


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    logger.info("invoke 호출 — 경로: /invocations")
    return await _handle_invoke(request)


@app.post("/")
async def invoke_root(request: Request) -> JSONResponse:
    logger.info("invoke 호출 — 경로: /")
    return await _handle_invoke(request)


# AgentCore Runtime 디버그용: 어떤 경로가 호출됐는지 확인
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    body = await request.body()
    logger.warning("알 수 없는 경로: %s %s (body_len=%d)", request.method, request.url.path, len(body))
    if request.method == "POST":
        return await _handle_invoke(request)
    return JSONResponse({"path": path, "method": request.method}, status_code=200)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "agent": "hezo-generation-agent"})


# ─── 로컬 실행 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
