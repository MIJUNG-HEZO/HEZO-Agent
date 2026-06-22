"""HEZO Wiki (P2) 생성물 룰베이스 사전검사 (결정적, LLM 없음).

LLM 의미 채점(review) 전에 돌리는 결정적 게이트. 구조·인용 정합성·금지어 같은 '객관적'
문제를 룰로 먼저 잡아 통과한 것만 LLM 채점으로 보낸다. 이점:
- 환각 인용([S9]인데 출처는 6개) 등 LLM 채점이 잘 못 잡는 정합성 오류를 100% 차단
- 명백한 구조/금지어 위반은 LLM 콜 없이 즉시 거부(비용 절약)
- 룰(객관)·LLM(의미)이 상호보완 — 2단 게이트

검사 대상은 생성 직후의 '본문'(frontmatter·출처 섹션 붙기 전, 순수 H2 지식 섹션)이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

MIN_BODY_CHARS = 400          # 본문 최소 길이
MAX_BODY_CHARS = 8000         # 본문 최대 길이(장황 방지)
MIN_CITATIONS = 3             # [Sn] 인용 최소 개수
MIN_SECTIONS = 5              # H2 지식 섹션 최소 개수(골격 7 중)
MAX_INSUFFICIENT_RATIO = 0.5  # "자료 부족" 섹션 비율 상한

# 광고·홍보 금지어 (중립성 하드 컷) — 지식이 아니라 마케팅 카피의 신호
BANNED_PHRASES = (
    # 직접 호객 CTA (명백한 마케팅 — 지식 본문에 올 일 없음)
    "지금 전화", "무료 상담", "무료상담", "지금 문의", "지금 신청", "상담 신청",
    "전화 주세요", "문의 주세요", "방문 예약", "카톡 상담",
    # 판촉 문구 — '패턴'으로 (단어 '할인'은 '할인마트'·'할인점' 같은 정상 업태어를
    # 오탐하므로 미사용). 미묘한 판촉 톤은 LLM 검수 neutrality 항목이 잡는다.
    "할인 이벤트", "할인 행사", "특가 세일", "특가 행사", "선착순 마감", "이벤트 중",
)

INSUFFICIENT_MARK = "자료 부족"
_H2 = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_CITE = re.compile(r"\[S(\d+)\]")


@dataclass(frozen=True)
class PrecheckResult:
    passed: bool
    violations: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _split_sections(body: str) -> list[tuple[str, str]]:
    """본문을 (H2 제목, 섹션 본문) 목록으로 분리."""
    parts = re.split(r"^##\s+(.+)$", body, flags=re.MULTILINE)
    out: list[tuple[str, str]] = []
    rest = iter(parts[1:])  # parts[0]은 첫 H2 이전 텍스트(보통 빈 문자열)
    for title, section_body in zip(rest, rest):
        out.append((title.strip(), section_body.strip()))
    return out


def precheck(body: str, selected: list[dict]) -> PrecheckResult:
    """생성 본문을 룰로 검사. 위반 0개면 passed=True."""
    violations: list[str] = []
    n_sources = len(selected)
    sections = [(t, b) for t, b in _split_sections(body) if t != "출처"]
    cites = _CITE.findall(body)
    cite_nums = {int(n) for n in cites}
    insufficient = sum(1 for _, b in sections if INSUFFICIENT_MARK in b)
    chars = len(body.strip())

    # 1) 섹션 완전성
    if len(sections) < MIN_SECTIONS:
        violations.append(f"sections_too_few:{len(sections)}<{MIN_SECTIONS}")

    # 2) 인용 존재
    if len(cites) < MIN_CITATIONS:
        violations.append(f"citations_too_few:{len(cites)}<{MIN_CITATIONS}")

    # 3) 인용 유효성 — 출처 범위 밖 번호 = 환각 인용
    out_of_range = sorted(n for n in cite_nums if n < 1 or n > n_sources)
    if out_of_range:
        violations.append(f"citation_out_of_range:{out_of_range}(sources={n_sources})")

    # 4) 자료 부족 비율
    if sections and insufficient / len(sections) > MAX_INSUFFICIENT_RATIO:
        violations.append(f"insufficient_ratio:{insufficient}/{len(sections)}")

    # 5) 길이
    if chars < MIN_BODY_CHARS:
        violations.append(f"body_too_short:{chars}<{MIN_BODY_CHARS}")
    elif chars > MAX_BODY_CHARS:
        violations.append(f"body_too_long:{chars}>{MAX_BODY_CHARS}")

    # 6) 광고 금지어
    hits = [p for p in BANNED_PHRASES if p in body]
    if hits:
        violations.append(f"banned_phrases:{hits}")

    stats = {
        "sections": len(sections),
        "citations": len(cites),
        "insufficient": insufficient,
        "chars": chars,
        "sources": n_sources,
    }
    return PrecheckResult(passed=not violations, violations=violations, stats=stats)
