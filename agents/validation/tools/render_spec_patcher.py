"""
검증 실패 시 render_spec.json을 직접 패치하는 도구.

blocking issue 코드별 전략:
  구조적 이슈 (규칙 기반): NO_H1, MULTIPLE_H1, NO_TITLE_TAG, NO_LLMS_TXT
  콘텐츠 이슈 (LLM 생성): NO_JSONLD, NO_FAQ_PAGE_JSONLD, INSUFFICIENT_FAQ
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any

import boto3

from agents.shared.s3_utils import ARTIFACTS_BUCKET, write_json, validate_site_id

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")

_bedrock: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def _call_bedrock(prompt: str, max_tokens: int = 1024) -> str:
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = _get_bedrock().invoke_model(
        modelId=MODEL_ID, body=body,
        contentType="application/json", accept="application/json",
    )
    result = json.loads(resp["body"].read())
    text = result["content"][0]["text"].strip()
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    return m.group(1) if m else text


def _generate_faq_jsonld(slots: dict, existing_faq: list[dict]) -> dict:
    """FAQPage JSON-LD를 LLM으로 생성"""
    business_name = slots.get("business_name", "")
    business_type = slots.get("business_type", "")

    if existing_faq:
        faq_source = json.dumps(existing_faq[:7], ensure_ascii=False)
        prompt = f"""다음 FAQ 항목으로 Schema.org FAQPage JSON-LD를 생성하세요.
업체: {business_name} ({business_type})

FAQ 항목:
{faq_source}

순수 JSON만 출력 (마크다운 블록 제외):"""
    else:
        prompt = f"""'{business_name}'({business_type}) 홈페이지의 FAQPage JSON-LD를 생성하세요.
업종 특화 질문 5개 이상 포함.

순수 JSON만 출력:"""

    text = _call_bedrock(prompt, max_tokens=2048)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": f"{business_type} 서비스 문의",
                    "acceptedAnswer": {"@type": "Answer", "text": "자세한 내용은 전화 문의 바랍니다."},
                },
            ],
        }


def _generate_faq_items(slots: dict, count: int = 5) -> list[dict]:
    """FAQ 블록 items를 LLM으로 생성"""
    business_type = slots.get("business_type", "")
    business_name = slots.get("business_name", "")
    prompt = f"""'{business_name}'({business_type}) 홈페이지 FAQ {count}개를 생성하세요.
실제 사용자가 AI 검색에서 물어볼 법한 구체적 질문으로 작성.

다음 JSON 배열만 출력:
[{{"q": "질문", "a": "답변"}}]"""

    text = _call_bedrock(prompt, max_tokens=1024)
    try:
        m = re.search(r"\[[\s\S]+\]", text)
        return json.loads(m.group() if m else text)
    except (json.JSONDecodeError, AttributeError):
        return [{"q": f"{business_type} 이용 방법이 궁금합니다", "a": "전화 또는 방문 상담을 통해 안내드립니다."}]


def patch(site_id: str, render_spec: dict, blocking_issues: list[dict], contract: dict) -> dict:
    """
    blocking_issues를 분석해 render_spec을 직접 패치 후 S3에 저장.
    반환: 패치된 render_spec dict
    """
    site_id = validate_site_id(site_id)
    patched = copy.deepcopy(render_spec)
    slots = contract.get("slots", {})

    if not patched.get("pages"):
        logger.warning("render_spec.pages 없음 — 패치 불가")
        return patched

    page = patched["pages"][0]
    applied: list[str] = []

    for issue in blocking_issues:
        code = issue.get("code", "")
        logger.info("패치 시도: %s", code)

        # ── H1 ─────────────────────────────────────────────────────────────────
        if code == "NO_H1":
            page["title_h1"] = f"{slots.get('business_name', '비즈니스')} — {slots.get('business_type', '전문 서비스')}"
            applied.append(code)

        elif code == "MULTIPLE_H1":
            # H1을 하나만 남기도록 blocks 중 첫 Hero의 h1 기준 유지, 나머지 표시 제거 안내
            logger.warning("MULTIPLE_H1: 템플릿 레벨 문제 — render_spec 수정으로 해결 불가, 건너뜀")

        # ── JSON-LD ────────────────────────────────────────────────────────────
        elif code in ("NO_JSONLD", "NO_FAQ_PAGE_JSONLD"):
            existing_faq: list[dict] = []
            for block in page.get("blocks", []):
                if block.get("type") == "FAQ":
                    existing_faq = block.get("items", [])

            faq_jsonld = _generate_faq_jsonld(slots, existing_faq)

            if code == "NO_JSONLD":
                page["jsonld"] = [faq_jsonld]
            else:
                existing_jsonld = page.get("jsonld", [])
                existing_jsonld = [j for j in existing_jsonld if j.get("@type") != "FAQPage"]
                existing_jsonld.append(faq_jsonld)
                page["jsonld"] = existing_jsonld
            applied.append(code)

        # ── FAQ 부족 ───────────────────────────────────────────────────────────
        elif code == "INSUFFICIENT_FAQ":
            for block in page.get("blocks", []):
                if block.get("type") == "FAQ":
                    current_count = len(block.get("items", []))
                    needed = max(5 - current_count, 2)
                    extra = _generate_faq_items(slots, count=needed)
                    block.setdefault("items", []).extend(extra)
                    applied.append(code)
                    break

        # ── SEO 제목 누락 ──────────────────────────────────────────────────────
        elif code == "NO_TITLE_TAG":
            if not page.get("seo", {}).get("title"):
                page.setdefault("seo", {})["title"] = (
                    f"{slots.get('business_name', '')} | {slots.get('business_type', '')} 전문"
                )[:60]
                applied.append(code)

        # ── llms.txt 누락 ──────────────────────────────────────────────────────
        elif code == "NO_LLMS_TXT":
            name = slots.get("business_name", "")
            btype = slots.get("business_type", "")
            addr = slots.get("address", "")
            supp = patched.setdefault("supplementary_files", {})
            if not supp.get("llms_txt"):
                supp["llms_txt"] = (
                    f"# {name}\n> {btype} | {addr}\n\n"
                    "## 서비스\n이 페이지는 자동 생성된 홈페이지입니다."
                )
                applied.append(code)

    logger.info("패치 적용: %s", applied)

    key = f"sites/{site_id}/render_spec.json"
    write_json(ARTIFACTS_BUCKET, key, patched,
               metadata={"site-id": site_id, "patched-by": "hezo-validation-agent",
                         "applied-patches": ",".join(applied)})
    logger.info("패치된 render_spec.json 저장: s3://hezo-artifacts/%s", key)
    return patched
