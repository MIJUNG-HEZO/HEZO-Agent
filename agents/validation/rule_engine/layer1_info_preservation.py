"""
Layer 1: 도메인 토픽 커버리지 검증 — hezo-wiki MD vs 생성 HTML (LLM 판단)

목적:
  생성 에이전트는 contract(고객 입력)만 보고 콘텐츠를 만들므로 편향이 생길 수 있음.
  P2가 외부 공공데이터를 크롤링·정제한 업종 표준 지식 MD를 기준선으로 삼아,
  생성된 사이트가 해당 업종의 핵심 도메인 토픽을 충분히 커버하는지 LLM으로 검증.

입력:
  wiki_snapshot: parse_wiki_md() 결과
    {domain, label, topics: [{title, summary, key_terms}]}
  html_content: 생성된 index.html 전문

비교 방식:
  1. wiki MD → H2 기준 토픽 단위로 청킹
  2. 각 토픽의 title + key_terms(최대 8개)를 LLM 프롬프트에 주입 (전문 X → 비용 절감)
  3. LLM이 HTML과 비교해 누락 토픽 판단
  4. missing_topics → warning 이슈 반환
     (구조적 blocking은 Layer 3 담당, Layer 1은 콘텐츠 완성도)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3

from libs.telemetry import record_llm_usage

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-6")

_bedrock: Any = None


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        from botocore.config import Config
        _bedrock = boto3.client(
            "bedrock-runtime",
            region_name=REGION,
            config=Config(read_timeout=600, connect_timeout=10, retries={"max_attempts": 0}),
        )
    return _bedrock


def _build_topics_brief(wiki_snapshot: dict) -> list[dict]:
    """LLM 프롬프트에 넣을 토픽 요약 — title + key_terms만 (full body 제외)."""
    return [
        {
            "title": t["title"],
            "key_terms": t["key_terms"][:6],  # 토큰 절약
        }
        for t in wiki_snapshot.get("topics", [])
    ]


def check_layer1(
    contract: dict,
    wiki_snapshot: dict,
    html_content: str,
) -> list[dict]:
    """
    hezo-wiki 도메인 토픽 목록 vs 생성 HTML 커버리지 검증.

    파라미터:
      contract     : contract_final.json
      wiki_snapshot: parse_wiki_md() 반환값 (없으면 빈 dict)
      html_content : 생성된 index.html

    반환: 이슈 목록 [{level, code, detail}]  (전부 warning)
    """
    if not wiki_snapshot or not wiki_snapshot.get("topics"):
        logger.info("Layer 1 skip: wiki_snapshot 없음")
        return []

    if not html_content:
        return []

    label = wiki_snapshot.get("label", wiki_snapshot.get("domain", "해당 업종"))
    topics_brief = _build_topics_brief(wiki_snapshot)
    html_snippet = html_content[:4000]

    prompt = f"""당신은 업종별 홈페이지 콘텐츠 전문가입니다.

아래는 [{label}] 업종 도메인 지식 위키의 핵심 토픽 목록입니다.
각 항목은 해당 업종 사이트를 방문하는 고객이나 AI 검색엔진이 알아야 할 정보 영역입니다.

=== 도메인 위키 토픽 목록 ===
{json.dumps(topics_brief, ensure_ascii=False, indent=2)}

=== 생성된 홈페이지 HTML (앞 4000자) ===
{html_snippet}

평가 지침:
- 홈페이지가 [{label}] 업종 사이트로서 위 토픽들을 얼마나 커버하는지 판단하세요.
- 모든 토픽을 다룰 필요는 없습니다. 랜딩 페이지 특성상 핵심 서비스·세율·신고기한 등 방문자 의사결정에 직결되는 정보가 있어야 합니다.
- key_terms에 있는 구체적 수치/날짜가 HTML 어딘가에 나타나면 해당 토픽이 커버된 것으로 봅니다.
- 핵심 토픽이 아예 언급되지 않았거나 중요 수치가 전혀 없으면 누락으로 표시하세요.

다음 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "covered_topics": ["커버된 토픽 제목 목록"],
  "missing_topics": [
    {{
      "topic": "누락된 토픽 제목",
      "reason": "방문자나 AI가 이 정보 없이 겪을 문제 (1~2문장)"
    }}
  ],
  "coverage_rate": 0.0
}}"""

    try:
        bedrock = _get_bedrock()
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
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
            "validation", "sonnet",
            _usage.get("input_tokens", 0),
            _usage.get("output_tokens", 0),
            ms=elapsed,
        )

        text = result["content"][0]["text"].strip()
        check = json.loads(text)
    except Exception as exc:
        logger.warning("Layer 1 LLM 호출 실패 — 건너뜀: %s", exc)
        return []

    issues: list[dict] = []
    coverage_rate = float(check.get("coverage_rate", 1.0))

    for item in check.get("missing_topics", []):
        issues.append({
            "level": "warning",
            "code": "LAYER1_MISSING_TOPIC",
            "detail": f"[{item.get('topic', '?')}] {item.get('reason', '')}",
        })

    logger.info(
        "Layer 1 완료: covered=%d missing=%d coverage=%.0f%%",
        len(check.get("covered_topics", [])),
        len(issues),
        coverage_rate * 100,
    )
    return issues
