"""
P2 wiki MD → Layer 1 검증용 wiki_snapshot 변환기

MD 구조:
  --- (YAML frontmatter: domain, label, confidence, volatility, last_updated)
  ## 1. 토픽 제목
  ### 서브섹션
  - 내용...
  ## 2. 다음 토픽
  ...

청킹 전략:
  H2 헤딩 기준으로 분할 → 토픽 단위 (세무/회계: 11개)
  각 토픽에서 추출:
    - bold 텍스트 (**...**)       → 핵심 용어·제도명
    - 숫자+% 패턴               → 세율·비율 등 수치
    - 한국식 금액 패턴           → 기준 금액
    - 날짜·기간 패턴             → 신고기한 등
  → key_terms 리스트 (중복 제거, 최대 8개)
"""
from __future__ import annotations

import re
from typing import Any


# ─── frontmatter 파서 (PyYAML 의존성 없이) ───────────────────────────────────

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """'---\\n...\\n---\\n' 형식 YAML frontmatter 수동 파싱."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    meta: dict[str, Any] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not k:
            continue
        # 숫자형 변환 시도
        try:
            meta[k] = float(v) if "." in v else int(v)
        except (ValueError, TypeError):
            meta[k] = v

    return meta, parts[2]


# ─── 핵심 용어 추출 ───────────────────────────────────────────────────────────

_RE_BOLD = re.compile(r"\*\*([^*\n]{1,30})\*\*")
_RE_PCT = re.compile(r"\d+\.?\d*%")
_RE_AMOUNT = re.compile(r"[\d,]+(?:만원|억원|조원)")
_RE_PERIOD = re.compile(r"\d+월\s*\d+일|\d+개월|\d+년\s*\d+월|\d+일\s*이내")


def _extract_key_terms(text: str, max_terms: int = 8) -> list[str]:
    bold = _RE_BOLD.findall(text)
    pcts = _RE_PCT.findall(text)
    amounts = _RE_AMOUNT.findall(text)
    periods = _RE_PERIOD.findall(text)

    seen: set[str] = set()
    result: list[str] = []
    for term in bold + pcts + amounts + periods:
        t = term.strip()
        if t and t not in seen:
            seen.add(t)
            result.append(t)
            if len(result) >= max_terms:
                break
    return result


# ─── 공개 API ─────────────────────────────────────────────────────────────────

def parse_wiki_md(md_content: str) -> dict[str, Any]:
    """
    P2 wiki MD를 Layer 1 검증용 wiki_snapshot으로 변환.

    반환 스키마:
    {
      "domain": str,
      "label": str,
      "confidence": float,
      "volatility": str,
      "last_updated": str,
      "topics": [
        {
          "title": str,           # H2 헤딩 (번호 포함)
          "summary": str,         # 섹션 첫 단락 (200자)
          "key_terms": [str],     # 핵심 용어·수치 (최대 8개)
        },
        ...
      ]
    }
    """
    frontmatter, body = _parse_frontmatter(md_content.strip())

    topics: list[dict[str, Any]] = []

    # H2 기준 분할: '## ' 으로 시작하는 줄
    raw_sections = re.split(r"\n(?=## )", body)

    for section in raw_sections:
        section = section.strip()
        if not section.startswith("## "):
            continue

        lines = section.splitlines()
        # 헤딩에서 번호·# 제거 → "## 1. 사업자 유형 구분" → "사업자 유형 구분"
        raw_title = lines[0].lstrip("# ").strip()
        title = re.sub(r"^\d+\.\s*", "", raw_title).strip()

        content = "\n".join(lines[1:])

        # 첫 단락 (마크다운 테이블·빈 줄 제외)
        first_para = ""
        for para in content.split("\n\n"):
            clean = para.strip()
            if clean and not clean.startswith("|") and not clean.startswith("-"):
                first_para = clean[:200]
                break

        topics.append({
            "title": title,
            "summary": first_para,
            "key_terms": _extract_key_terms(content),
        })

    return {
        "domain": str(frontmatter.get("domain", "")),
        "label": str(frontmatter.get("label", "")),
        "confidence": float(frontmatter.get("confidence", 1.0)),
        "volatility": str(frontmatter.get("volatility", "low")),
        "last_updated": str(frontmatter.get("last_updated", "")),
        "topics": topics,
    }
