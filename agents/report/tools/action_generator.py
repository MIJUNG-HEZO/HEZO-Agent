"""
5가지 체크 결과를 바탕으로 Claude Haiku가 개선 액션 아이템 3~5개 생성.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import boto3

from libs.telemetry.telemetry import record_llm_usage

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

_bedrock: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    return _bedrock


def _build_summary(
    geo_file: dict,
    bot_visits: dict,
    indexing: dict,
    performance: dict,
) -> str:
    visits = bot_visits.get("visits", {})
    ai_bots = ["GPTBot", "ClaudeBot", "PerplexityBot", "Yeti"]
    bot_summary = ", ".join(f"{b}:{visits.get(b, 0)}회" for b in ai_bots + ["Googlebot"])

    geo_file_issues = []
    if not geo_file.get("llms_txt", {}).get("ok"):
        geo_file_issues.append("llms.txt 접근 불가")
    faq_cnt = geo_file.get("llms_full_txt", {}).get("faq_count", 0)
    if faq_cnt < 3:
        geo_file_issues.append(f"llms-full.txt FAQ {faq_cnt}개 (부족)")
    robots = geo_file.get("robots_txt", {}).get("bots", {})
    missing_bots = [b for b, allowed in robots.items() if not allowed]
    if missing_bots:
        geo_file_issues.append(f"robots.txt {', '.join(missing_bots)} 미허용")
    if not geo_file.get("jsonld", {}).get("has_faq_page"):
        geo_file_issues.append("JSON-LD FAQPage 스키마 없음")

    ssl_days = performance.get("ssl_days_remaining")
    if ssl_days is None:
        ssl_status = "확인 불가"
    elif ssl_days <= 30:
        ssl_status = f"⚠️ {ssl_days}일 남음 (갱신 필요)"
    elif ssl_days <= 90:
        ssl_status = f"주의 — {ssl_days}일 남음"
    else:
        ssl_status = f"정상 ({ssl_days}일 남음)"

    return f"""[사이트 현황 리포트]
GEO 파일 점수: {geo_file.get('summary_score', 0)}/100
AI 봇 방문 (7일): {bot_summary if bot_visits.get('configured') else '정보 없음 (CloudFront 로그 미설정)'}
구글 인덱싱: {indexing.get('indexing_status', 'unknown')} ({indexing.get('days_since_publish', 0)}일 경과, {indexing.get('indexing_likelihood_pct', 0)}% 추정)
사이트 성능: 응답 {performance.get('response_ms', 0)}ms, 모바일 점수 {performance.get('mobile_score', 'N/A')}, 등급 {performance.get('performance_grade', 'N/A')}
SSL 인증서: {ssl_status}

GEO 파일 문제:
{chr(10).join(f'- {i}' for i in geo_file_issues) or '- 없음'}""".strip()


def generate_action_items(
    geo_file: dict,
    bot_visits: dict,
    indexing: dict,
    performance: dict,
) -> list[dict[str, str]]:
    """Claude Haiku로 우선순위별 개선 액션 3~5개 생성"""
    summary = _build_summary(geo_file, bot_visits, indexing, performance)

    prompt = f"""다음은 AI 최적화 홈페이지의 현황 리포트입니다.

{summary}

위 현황을 바탕으로 고객이 지금 당장 실행할 수 있는 개선 액션 3~5개를 생성하세요.

규칙:
- priority: "red"(즉시 필수), "yellow"(권장), "green"(잘 되고 있음)
- content: 구체적인 한 줄 행동 지침 (한국어)
- green은 1개 이상 포함

다음 JSON 배열만 출력:
[
  {{"priority": "red", "content": "구체적인 액션"}},
  {{"priority": "yellow", "content": "구체적인 액션"}},
  {{"priority": "green", "content": "잘 되고 있는 항목"}}
]"""

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

    usage = result.get("usage", {})
    record_llm_usage("report", "haiku",
                     usage.get("input_tokens", 0),
                     usage.get("output_tokens", 0),
                     ms=elapsed)

    text = result["content"][0]["text"].strip()
    try:
        m = re.search(r"\[[\s\S]+\]", text)
        items = json.loads(m.group() if m else text)
        valid = [i for i in items if "priority" in i and "content" in i]
        logger.info("액션 아이템 생성 완료: %d개", len(valid))
        return valid
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("액션 아이템 파싱 실패: %s — raw: %s", exc, text[:200])
        return [{"priority": "yellow", "content": "리포트 분석 중 오류 발생 — 다음 주 재시도"}]
