"""HEZO Wiki (P2) 위키 본문 생성 — Bedrock Sonnet 1콜 (순수 로직, 런타임 무관).

raw 원문(docs)을 근거로 도메인 지식 md '본문'을 생성한다. frontmatter와 '## 출처'
섹션은 코드에서 결정적으로 붙이고(P1 파서 계약·URL 정확성 보장), LLM은 H2 지식 섹션만
작성하며 모든 사실 주장에 [Sn] 인라인 인용을 단다(근거성). 마케팅/질문 생성은 금지(P1 몫).

출처는 등급(high>mid>low) 우선 정렬해 상위 N개만 프롬프트에 넣는다(품질·토큰 관리).
커머스 도메인처럼 high 출처가 없으면 LLM이 라벨로 도메인 성격을 판단해 출처 경중을
조정하도록 시스템 프롬프트에서 지시한다(수동 유형 분류 없이 처리).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agents.wiki.catalog import get_entry
from agents.wiki.llm import BedrockLLM

MAX_SOURCES = 8           # 프롬프트에 넣는 출처 상한
SOURCE_EXCERPT_CHARS = 1500  # 출처당 본문 발췌 상한(토큰 관리)
# 생성 본문 토큰 천장(실제 쓴 만큼만 과금). precheck 본문상한 8000자와 정합 + 헤드룸.
# 4000은 7섹션 위키엔 부족해 잘림(generation_truncated)이 잦았다(#169 실측 ~33%). 런어웨이는
# precheck(8000자)가 잡으므로 천장을 넉넉히 둬 정상 위키가 안 잘리게 한다.
MAX_OUTPUT_TOKENS = 8000
_GRADE_RANK = {"high": 0, "mid": 1, "low": 2}

# 60개 도메인 공통 H2 골격 (P1 파서가 ## 섹션을 지식 섹션으로 추출)
SECTIONS = [
    "개요",
    "핵심 개념·용어",
    "비즈니스 형태·서비스",
    "타깃 고객",
    "차별화·경쟁 포인트",
    "규제·인증·유의사항",
    "트렌드·시장 동향",
]

GENERATION_SYSTEM = (
    "당신은 HEZO의 도메인 지식 편집자입니다. 제공된 출처만 근거로 한국어 백과사전식 "
    "도메인 지식을 작성합니다. 마케팅·홍보 문구, 특정 업체 추천, 사용자에게 묻는 질문은 "
    "절대 쓰지 않습니다(질문 생성은 다른 단계의 역할).\n"
    "규칙:\n"
    "1) 제공된 [Sn] 출처에 있는 사실만 쓰고, 모든 사실 주장 끝에 근거 [Sn]을 표기한다.\n"
    "2) 출처에 없으면 지어내지 말고 해당 섹션에 '자료 부족'이라고만 적는다.\n"
    "3) high 등급 출처를 우선 근거로 삼는다. 정부/협회 출처가 없는 도메인(예: 소비재·취미)은 "
    "전문 매체·위키를 권위로 보고, 쇼핑·광고성(low) 출처는 사실만 추출하고 광고 톤을 제거한다.\n"
    "4) 아래 H2 골격을 그 순서대로 모두 작성한다. frontmatter와 '## 출처' 섹션은 쓰지 않는다(시스템이 붙인다).\n"
    "5) 중립적이고 구체적으로(수치·전문용어 포함) 쓴다."
)


def select_sources(docs: list[dict]) -> list[dict]:
    """추출 성공(ok)·본문 있는 문서만, 등급 우선 정렬해 상위 MAX_SOURCES개."""
    usable = [d for d in docs if d.get("ok") and (d.get("text") or "").strip()]
    usable.sort(key=lambda d: _GRADE_RANK.get(d.get("source_grade", "low"), 2))
    return usable[:MAX_SOURCES]


def build_sources_block(selected: list[dict]) -> str:
    """선택 문서를 [S1]..[SN] 번호 블록으로. 생성·검수가 같은 번호를 공유한다."""
    parts: list[str] = []
    for i, d in enumerate(selected, start=1):
        excerpt = (d.get("text") or "")[:SOURCE_EXCERPT_CHARS]
        parts.append(
            f"[S{i}] (등급:{d.get('source_grade', 'low')}) {d.get('title', '')}\n"
            f"URL: {d.get('url', '')}\n{excerpt}"
        )
    return "\n\n".join(parts)


def build_generation_user(label: str, category: str, sources_block: str) -> str:
    skeleton = "\n".join(f"## {s}" for s in SECTIONS)
    return (
        f"도메인: {label}\n템플릿 유형: {category}\n\n"
        f"다음 출처를 근거로 '{label}' 도메인 지식을 작성하세요.\n\n"
        f"=== 출처 ===\n{sources_block}\n\n"
        f"=== 작성할 H2 골격(이 순서대로 모두) ===\n{skeleton}\n"
    )


@dataclass(frozen=True)
class GenerationResult:
    ok: bool
    body: str
    selected: list[dict]
    usage: dict = field(default_factory=dict)
    reason: str = ""


def generate(category: str, domain: str, docs: list[dict], *, llm: BedrockLLM | None = None) -> GenerationResult:
    """출처(docs) → 위키 본문(H2 섹션) 생성. frontmatter/출처는 assemble에서 붙인다."""
    label = get_entry(domain)["label"]
    selected = select_sources(docs)
    if not selected:
        return GenerationResult(False, "", [], reason="no_usable_sources")

    user = build_generation_user(label, category, build_sources_block(selected))
    llm = llm or BedrockLLM()
    # max_tokens는 천장(실제 쓴 만큼만 과금) — 본문이 중간에 안 잘리도록 넉넉히(MAX_OUTPUT_TOKENS).
    res = llm.complete(GENERATION_SYSTEM, user, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2)
    if not res.ok or not res.text.strip():
        return GenerationResult(False, "", selected, reason=res.reason or "empty_generation")
    # 토큰 한도로 본문이 잘렸으면(stopReason=max_tokens) 미완성 → 실패 처리(백오프 재시도).
    # 글자 추측이 아니라 converse stopReason으로 확실히 판정.
    if res.stop_reason == "max_tokens":
        return GenerationResult(False, "", selected, reason="generation_truncated")
    return GenerationResult(
        True,
        res.text.strip(),
        selected,
        usage={"input": res.input_tokens, "output": res.output_tokens},
    )


def assemble_markdown(
    domain: str,
    category: str,
    label: str,
    body: str,
    selected: list[dict],
    *,
    confidence: float,
) -> str:
    """frontmatter + 본문 + '## 출처'를 합쳐 P1 파서가 읽는 최종 md를 만든다.

    frontmatter(domain·category·label·confidence)와 출처([Sn] url)는 코드가 결정적으로
    쓴다 — 파서의 domain/category 일치 검증과 근거 ref 추출을 보장하기 위해서다.
    """
    front = [
        "---",
        f"domain: {domain}",
        f"category: {category}",
        f"label: {label}",
        f"confidence: {confidence:.2f}",
        f"source_count: {len(selected)}",
        "---",
        "",
    ]
    sources = ["", "## 출처"]
    for i, d in enumerate(selected, start=1):
        title = (d.get("title") or "").strip() or d.get("url", "")
        sources.append(f"[S{i}] {title} — {d.get('url', '')}")
    return "\n".join(front) + body.strip() + "\n" + "\n".join(sources) + "\n"
