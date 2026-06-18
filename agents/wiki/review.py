"""HEZO Wiki (P2) 위키 검수 — Bedrock Sonnet 1콜, 8항목 가중 채점 (순수 로직).

생성과 별도 콜(self-bias 방지)로 본문을 채점한다. 채점기에 원문(출처)도 함께 줘서
'원문 대조' 채점을 시킨다. LLM은 8항목을 0~5점으로만 내고, 합성점수·게이트·컷 판정은
코드에서 결정적으로 한다(LLM에 합산을 맡기지 않는다).

- 합성점수 = Σ(가중치 × 점수/5), 0~1.
- 게이트: 사실정확성·근거성 중 하나라도 3점 미만이면 합성점수와 무관하게 미달
  ("예쁘지만 틀린 위키" 원천 차단).
- 컷: 합성점수 0.70 이상이고 게이트 통과면 합격.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from agents.wiki.catalog import get_entry
from agents.wiki.generate import build_sources_block
from agents.wiki.llm import BedrockLLM

# (key, 한글명, 가중치, 게이트여부)
RUBRIC = [
    ("factual_accuracy", "사실 정확성", 0.20, True),
    ("groundedness", "근거성", 0.15, True),
    ("coverage", "커버리지", 0.15, False),
    ("domain_relevance", "도메인 적합성", 0.12, False),
    ("specificity", "구체성", 0.12, False),
    ("structure", "구조·파싱성", 0.10, False),
    ("neutrality", "중립성", 0.08, False),
    ("freshness", "최신성", 0.08, False),
]
GATE_KEYS = [k for k, _, _, gate in RUBRIC if gate]
GATE_MIN = 3.0   # 0~5 척도
CUT = 0.70       # 합성점수 컷
MAX_SCORE = 5.0

REVIEW_SYSTEM = (
    "당신은 HEZO 도메인 지식 위키의 엄격한 검수자입니다. 주어진 본문을 함께 제공된 출처와 "
    "대조하여 아래 8개 항목을 각각 0~5점으로 채점합니다. 후하게 주지 말고, 출처에 근거가 "
    "없는 주장·광고성 표현·주제 이탈은 감점하세요.\n"
    "항목 정의:\n"
    "- factual_accuracy: 사실이 출처와 일치하고 오류가 없는가\n"
    "- groundedness: 모든 핵심 주장이 출처([Sn])로 추적 가능하고 지어낸 내용이 없는가\n"
    "- coverage: 도메인 핵심 facet(정의·서비스·고객·용어·규제·트렌드)을 포괄하는가\n"
    "- domain_relevance: 주제 이탈 없이 해당 도메인 지식인가\n"
    "- specificity: 모호한 일반론이 아니라 구체 사실·수치·전문용어가 있는가\n"
    "- structure: 섹션 구조가 일관되고 읽기 좋은가\n"
    "- neutrality: 마케팅·홍보·특정 업체 추천이 배제됐는가\n"
    "- freshness: 오래된 정보가 아니라 최신성이 있는가\n"
    "반드시 아래 JSON만 출력하세요(설명 문장 금지):\n"
    '{"scores":{"factual_accuracy":0,"groundedness":0,"coverage":0,"domain_relevance":0,'
    '"specificity":0,"structure":0,"neutrality":0,"freshness":0},"notes":"간단 사유"}'
)


def build_review_user(label: str, body: str, sources_block: str) -> str:
    return (
        f"도메인: {label}\n\n"
        f"=== 출처 ===\n{sources_block}\n\n"
        f"=== 검수 대상 본문 ===\n{body}\n"
    )


@dataclass(frozen=True)
class ReviewResult:
    ok: bool
    passed: bool
    score: float
    items: dict = field(default_factory=dict)
    gate_failed: list = field(default_factory=list)
    notes: str = ""
    usage: dict = field(default_factory=dict)
    reason: str = ""


def _extract_json(text: str) -> dict | None:
    """본문에서 첫 '{'~마지막 '}' 구간을 JSON으로 파싱(설명문 섞여도 견고)."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None


def compute(scores: dict) -> tuple[float, bool, list[str]]:
    """점수 dict → (합성점수, 합격여부, 게이트 미달 항목). 코드에서 결정적으로 계산."""
    composite = sum(
        weight * (min(max(float(scores.get(k, 0)), 0.0), MAX_SCORE) / MAX_SCORE)
        for k, _, weight, _ in RUBRIC
    )
    gate_failed = [k for k in GATE_KEYS if float(scores.get(k, 0)) < GATE_MIN]
    passed = composite >= CUT and not gate_failed
    return round(composite, 3), passed, gate_failed


def review(
    category: str,
    domain: str,
    body: str,
    selected: list[dict],
    *,
    llm: BedrockLLM | None = None,
    sources_block: str | None = None,
) -> ReviewResult:
    """본문을 출처와 대조해 8항목 채점 → 합성점수·게이트·컷 판정."""
    label = get_entry(domain)["label"]
    block = sources_block if sources_block is not None else build_sources_block(selected)
    llm = llm or BedrockLLM()
    res = llm.complete(REVIEW_SYSTEM, build_review_user(label, body, block), max_tokens=600, temperature=0.0)
    if not res.ok:
        return ReviewResult(False, False, 0.0, reason=res.reason or "review_call_failed")

    data = _extract_json(res.text)
    if not data or "scores" not in data:
        return ReviewResult(False, False, 0.0, reason="review_json_unparseable")

    scores = {k: float(data["scores"].get(k, 0)) for k, _, _, _ in RUBRIC}
    composite, passed, gate_failed = compute(scores)
    return ReviewResult(
        ok=True,
        passed=passed,
        score=composite,
        items=scores,
        gate_failed=gate_failed,
        notes=str(data.get("notes", "")),
        usage={"input": res.input_tokens, "output": res.output_tokens},
    )
