"""
생성 에이전트 — Amazon Bedrock AgentCore Runtime 진입점

흐름 (v2 에이전트 동작):
  1. site_id 파싱
  2. contract_final.json 로드 + generation_ready 게이트 확인
  3. validation_feedback.json 로드 (재시도 경로용, 없으면 None)
  4. Claude Sonnet → render_spec 생성
  5. 규칙 기반 평가 (evaluate_render_spec)
  6. [에이전트] LLM 자체 평가 — "AI 검색 인용 가능성" 점수화
  7. 미달 섹션만 재생성 (전체 재생성 아님, 최대 2회)
  8. 가드레일 검사
  9. render_spec.json → S3 저장
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
from agents.generation.tools.feedback_loader import load_feedback
from agents.generation.tools.render_spec_saver import save_render_spec
from libs.telemetry import init_telemetry, record_llm_usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("hezo.generation")

REGION = os.environ.get("AWS_DEFAULT_REGION", os.environ.get("REGION", "ap-northeast-2"))
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")
QUALITY_THRESHOLD = int(os.environ.get("QUALITY_THRESHOLD", "70"))
LLM_EVAL_THRESHOLD = int(os.environ.get("LLM_EVAL_THRESHOLD", "70"))

init_telemetry("generation", region=REGION)
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))

app = FastAPI(title="HEZO Generation Agent")

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
    if site_id := session_attrs.get("site_id"):
        return site_id.strip()
    m = re.search(r"site_id[=:\s]+([a-zA-Z0-9_\-]+)", input_text)
    if m:
        return m.group(1).strip()
    raise ValueError(f"site_id를 찾을 수 없음 — inputText: {input_text!r}")


# =============================================================================
# Claude 호출 — render_spec 전체 생성
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
        },
        {
          "@context": "https://schema.org",
          "@type": "Service",
          "serviceType": "<대표 서비스명>",
          "provider": { "@type": "<업종 Schema 타입>", "name": "<업체명>" },
          "description": "<서비스 설명>",
          "areaServed": { "@type": "City", "name": "<지역>" }
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
    "llms_txt": "# <업체명>\\n> <업종> | <지역>\\n\\n## 핵심 페이지\\n- [홈](/) : <업체 한 줄 설명>\\n- [서비스 안내](/#services) : <서비스 한 줄 요약>\\n- [자주 묻는 질문](/#faq) : 비용·기간·절차 안내\\n- [상담 신청](/#contact) : 무료 상담\\n\\n## 서비스\\n- <서비스1>\\n- <서비스2>\\n\\n## 연락처\\n- 전화: <전화번호>\\n- 영업시간: <영업시간>",
    "llms_full_txt": "# <업체명>\\n> <업종> | <지역>\\n\\n<업체 소개 2~3문장>\\n\\n## 핵심 서비스\\n- **<서비스1>**: <구체적 설명>\\n- **<서비스2>**: <구체적 설명>\\n\\n## 고객 고통점 해결\\n- <문제1>: <해결책>\\n\\n## FAQ\\n- Q: <실제 사용자가 AI 검색에 물어볼 질문1>\\n  A: <구체적 수치·비용·기간 포함 답변>\\n- Q: <질문2>\\n  A: <답변2>\\n- Q: <질문3>\\n  A: <답변3>\\n\\n## 연락처\\n- 전화: <전화번호>\\n- 영업시간: <영업시간>\\n\\n## 타겟 고객\\n- <고객군1>\\n- <고객군2>",
    "sitemap_pages": [
      { "path": "/", "priority": 1.0, "changefreq": "monthly" },
      { "path": "/llms-full.txt", "priority": 0.8, "changefreq": "monthly" }
    ],
    "robots_rules": [
      "User-agent: GPTBot", "Allow: /",
      "User-agent: ClaudeBot", "Allow: /",
      "User-agent: PerplexityBot", "Allow: /",
      "User-agent: Yeti", "Allow: /",
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
- llms_txt 필수 생성 (## 핵심 페이지 링크 섹션 포함)
- llms_full_txt에 ## FAQ 섹션 필수 (Q:/A: 형식, 3개 이상)
- robots_rules에 GPTBot/ClaudeBot/PerplexityBot/Yeti Allow 필수
- FAQPage JSON-LD 필수
- Service JSON-LD 필수 (대표 서비스 1개 이상)
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
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    elapsed = (time.monotonic() - start) * 1000

    result = json.loads(resp["body"].read())
    _usage = result.get("usage", {})
    record_llm_usage(
        "generation", "sonnet",
        _usage.get("input_tokens", 0), _usage.get("output_tokens", 0), ms=elapsed,
    )
    logger.info("Claude 호출 완료: %.0f ms", elapsed)

    text = result["content"][0]["text"].strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)

    return json.loads(text)


# =============================================================================
# [에이전트] LLM 자체 평가 — AI 인용 가능성 판단
# =============================================================================

def llm_self_eval(render_spec: dict, contract: dict) -> dict:
    """
    Claude가 스스로 생성한 render_spec의 AI 검색 인용 가능성 평가.
    반환: {score: 0~100, weak_sections: [...], reason: str}
    """
    slots = contract.get("slots", {})
    business_type = slots.get("business_type", "")
    region = slots.get("address", "")

    page = render_spec.get("pages", [{}])[0]
    faq_items: list[dict] = []
    quick_answer = ""
    for block in page.get("blocks", []):
        if block.get("type") == "FAQ":
            faq_items = block.get("items", [])
        elif block.get("type") == "QuickAnswer":
            quick_answer = block.get("text", "")

    prompt = f"""다음 홈페이지 콘텐츠가 '{business_type} {region} 추천' AI 검색 질의에서 인용될 가능성을 평가하세요.

[QuickAnswer]
{quick_answer}

[FAQ {len(faq_items)}개]
{json.dumps(faq_items[:5], ensure_ascii=False, indent=2)}

[SEO 키워드]
{json.dumps(page.get('seo', {}).get('target_keywords', []), ensure_ascii=False)}

평가 기준 (각 20점):
1. 구체적 수치·비용·기간 포함 여부
2. FAQ가 실제 사용자 질문 형태인지
3. QuickAnswer가 핵심 정보를 압축 전달하는지
4. 업종 핵심 키워드 자연스럽게 포함했는지
5. 경쟁사 대비 차별화 포인트 존재 여부

반드시 다음 JSON만 출력하세요:
{{
  "score": <0~100 정수>,
  "weak_sections": <["FAQ", "QuickAnswer", "SEO", "JSONLD"] 중 미달 항목 목록, 없으면 []>,
  "reason": "<미달 이유 한 문장, 통과 시 빈 문자열>"
}}"""

    bedrock = get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = bedrock.invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()

    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        text = m.group()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM 자체 평가 JSON 파싱 실패 — 기본값 사용")
        return {"score": 75, "weak_sections": [], "reason": ""}


def regenerate_weak_sections(
    render_spec: dict, weak_sections: list[str], contract: dict, reason: str
) -> dict:
    """
    미달 섹션만 Claude로 재생성 (전체 재생성 아님).
    반환: 수정된 render_spec
    """
    import copy
    patched = copy.deepcopy(render_spec)
    slots = contract.get("slots", {})
    business_type = slots.get("business_type", "")
    business_name = slots.get("business_name", "")
    region = slots.get("address", "")

    page = patched.get("pages", [{}])[0]

    if "FAQ" in weak_sections:
        prompt = f"""'{business_name}'({business_type}, {region}) 홈페이지 FAQ 7개를 개선하여 재작성하세요.
개선 방향: {reason}
- 구체적 수치(비용·기간) 포함
- 실제 사용자가 AI 검색에 물어볼 법한 질문 형태

다음 JSON 배열만 출력:
[{{"q": "질문", "a": "구체적 답변"}}]"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        })
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        text = json.loads(resp["body"].read())["content"][0]["text"].strip()
        try:
            m = re.search(r"\[[\s\S]+\]", text)
            new_faq = json.loads(m.group() if m else text)
            for block in page.get("blocks", []):
                if block.get("type") == "FAQ":
                    block["items"] = new_faq
                    break
            # FAQPage JSON-LD도 업데이트
            for jld in page.get("jsonld", []):
                if jld.get("@type") == "FAQPage":
                    jld["mainEntity"] = [
                        {"@type": "Question", "name": item["q"],
                         "acceptedAnswer": {"@type": "Answer", "text": item["a"]}}
                        for item in new_faq
                    ]
                    break
            logger.info("FAQ 섹션 재생성 완료: %d개", len(new_faq))
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("FAQ 재생성 파싱 실패: %s", exc)

    if "QuickAnswer" in weak_sections:
        prompt = f"""'{business_name}'({business_type}, {region})의 QuickAnswer를 재작성하세요.
조건: 50~120자, AI 검색 인용 최적화, 핵심 서비스·특징 압축.
개선 방향: {reason}
문자열만 출력 (따옴표 없이):"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": prompt}],
        })
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        new_qa = json.loads(resp["body"].read())["content"][0]["text"].strip().strip('"')
        for block in page.get("blocks", []):
            if block.get("type") == "QuickAnswer":
                block["text"] = new_qa[:120]
                break
        logger.info("QuickAnswer 재생성 완료")

    if "SEO" in weak_sections:
        seo = page.get("seo", {})
        prompt = f"""'{business_name}'({business_type}, {region}) 홈페이지 SEO 메타데이터를 개선하세요.
개선 방향: {reason}

현재 키워드: {seo.get('target_keywords', [])}

다음 JSON만 출력:
{{
  "title": "<60자 이내 SEO 타이틀>",
  "description": "<160자 이내 메타 디스크립션>",
  "target_keywords": ["<키워드 5개>"]
}}"""

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        })
        resp = get_bedrock().invoke_model(modelId=MODEL_ID, body=body,
                                          contentType="application/json", accept="application/json")
        text = json.loads(resp["body"].read())["content"][0]["text"].strip()
        try:
            m = re.search(r"\{[\s\S]+\}", text)
            new_seo = json.loads(m.group() if m else text)
            page.setdefault("seo", {}).update(new_seo)
            logger.info("SEO 섹션 재생성 완료")
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.warning("SEO 재생성 파싱 실패: %s", exc)

    return patched


# =============================================================================
# 핵심 에이전트 로직
# =============================================================================

def run_generation(site_id: str) -> dict:
    logger.info("생성 에이전트 시작 — site_id=%s", site_id)

    # 1. Contract 로드
    loaded = load_contract(site_id)
    contract = loaded["contract"]
    crawl_snapshot = loaded.get("crawl_snapshot")

    # 2. generation_ready 게이트
    if not contract.get("gates", {}).get("generation_ready", False):
        logger.warning("generation_ready=false — 생성 건너뜀")
        return {"status": "skipped", "reason": "generation_ready=false", "site_id": site_id}

    # 3. validation_feedback 로드 (재시도 경로 — 없으면 None)
    feedback = load_feedback(site_id)
    initial_hints: list[str] | None = None
    if feedback:
        initial_hints = [f"{i.get('code')}: {i.get('detail')}" for i in feedback.get("patch_hints", [])]
        logger.info("validation_feedback 로드 — hint %d건", len(initial_hints))

    # 4. Claude 호출 + 규칙 기반 평가 루프
    render_spec: dict | None = None
    eval_result: dict = {}
    issues_hint: list[str] | None = initial_hints

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
        logger.info("규칙 평가: score=%d, issues=%d", eval_result["score"], eval_result["issue_count"])

        if eval_result["passed"]:
            break

        if attempt < MAX_RETRIES:
            issues_hint = eval_result["issues"]
            logger.warning("규칙 점수 미달 (score=%d) — 재시도", eval_result["score"])
        else:
            logger.warning("최대 재시도 도달 — score=%d 로 진행", eval_result["score"])

    if render_spec is None:
        raise RuntimeError("render_spec 생성 실패")

    # 5. [에이전트] LLM 자체 평가 — AI 인용 가능성 판단
    llm_eval = llm_self_eval(render_spec, contract)
    logger.info("LLM 자체 평가: score=%d, weak=%s", llm_eval["score"], llm_eval["weak_sections"])

    if llm_eval["score"] < LLM_EVAL_THRESHOLD and llm_eval["weak_sections"]:
        logger.info("LLM 자체 평가 미달 — 미달 섹션만 재생성: %s", llm_eval["weak_sections"])
        render_spec = regenerate_weak_sections(
            render_spec, llm_eval["weak_sections"], contract, llm_eval["reason"]
        )

    # 6. 가드레일
    check_guardrails(render_spec)

    # 7. S3 저장
    save_result = save_render_spec(site_id, render_spec)
    logger.info(
        "생성 완료 — site_id=%s, rule_score=%d, llm_score=%d, s3_key=%s",
        site_id, eval_result.get("score", 0), llm_eval["score"], save_result["s3_key"],
    )

    return {
        "status": "complete",
        "site_id": site_id,
        "render_spec_key": save_result["s3_key"],
        "eval_score": eval_result.get("score", 0),
        "llm_eval_score": llm_eval["score"],
        "weak_sections_patched": llm_eval["weak_sections"],
        "page_count": save_result["page_count"],
        "saved_at": save_result["saved_at"],
    }


# =============================================================================
# AgentCore Runtime HTTP 핸들러
# =============================================================================

async def _handle_invoke(request: Request) -> JSONResponse:
    body = await request.body()
    logger.info("요청 경로: %s %s", request.method, request.url.path)

    try:
        payload = __import__("json").loads(body) if body else {}
    except Exception:
        payload = {}

    session_id = payload.get("sessionId", "")
    input_text = payload.get("inputText", "")
    session_attrs = payload.get("sessionAttributes", {})
    # Step Functions HTTP Task / 직접 호출 시 payload root에 site_id가 올 수 있음
    if payload.get("site_id") and not session_attrs.get("site_id"):
        session_attrs = {**session_attrs, "site_id": payload["site_id"]}

    logger.info("invoke 호출 — sessionId=%s body_keys=%s", session_id, list(payload.keys()))

    try:
        site_id = parse_site_id(input_text, session_attrs)
        result = run_generation(site_id)
        output_text = (
            f"render_spec_saved — site_id: {site_id}, "
            f"rule_score: {result.get('eval_score', 0)}, "
            f"llm_score: {result.get('llm_eval_score', 0)}"
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
    return JSONResponse({"status": "ok", "agent": "hezo-generation-agent"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def catch_all(path: str, request: Request) -> JSONResponse:
    body = await request.body()
    logger.warning("알 수 없는 경로: %s %s", request.method, request.url.path)
    if request.method == "POST":
        return await _handle_invoke(request)
    return JSONResponse({"path": path, "method": request.method}, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
