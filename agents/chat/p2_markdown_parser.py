"""P2 domain knowledge markdown parser/normalizer for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from p2_markdown_review import P2MarkdownReviewInput


ParseStatus = Literal["passed", "needs_enrichment", "failed"]

CONFIDENCE_PATTERN = re.compile(r"(?:confidence|신뢰도|확신도)\s*[:=]\s*([01](?:\.\d+)?)", re.IGNORECASE)
SECTION_HEADING_PATTERN = re.compile(r"^##\s+(?P<title>.+)")
SOURCE_TOKEN_PATTERN = re.compile(r"\[(S\d+)\]", re.IGNORECASE)
SOURCE_LINE_PATTERN = re.compile(
    r"^\s*(?:[-*+]\s*)?\[?(S\d+)\]?\s*(?::|\-)?\s*(?P<text>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class P2MarkdownParseInput:
    """Raw P2 domain knowledge markdown artifact and context received by P1."""

    domain: str
    expected_domain: str
    content: str
    category: str = ""
    slot_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_s3_key: str | None = None
    version: str | None = None
    source_count: int = 0
    source_grade: str = "unknown"


@dataclass(frozen=True)
class EvidenceRef:
    """Evidence/source item extracted from a P2 markdown artifact."""

    ref_id: str
    text: str
    source_s3_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "text": self.text,
            "source_s3_key": self.source_s3_key,
        }


@dataclass(frozen=True)
class KnowledgeSection:
    """Domain knowledge section extracted from P2 markdown."""

    section_id: str
    title: str
    body: str
    source_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "body": self.body,
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True)
class P2MarkdownParseResult:
    """P1 normalized P2 domain knowledge artifact."""

    domain: str
    category: str
    label: str
    p2_confidence: float
    knowledge_sections: tuple[KnowledgeSection, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    frontmatter: dict[str, Any]
    warnings: tuple[str, ...]
    parse_status: ParseStatus
    source_s3_key: str | None = None
    version: str | None = None
    source_count: int = 0
    source_grade: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "category": self.category,
            "label": self.label,
            "p2_confidence": self.p2_confidence,
            "knowledge_sections": [section.to_dict() for section in self.knowledge_sections],
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "frontmatter": dict(self.frontmatter),
            "warnings": list(self.warnings),
            "parse_status": self.parse_status,
            "source_s3_key": self.source_s3_key,
            "version": self.version,
            "source_count": self.source_count,
            "source_grade": self.source_grade,
        }

    def to_review_input(self, content: str, expected_domain: str) -> P2MarkdownReviewInput:
        return P2MarkdownReviewInput(
            domain=self.domain,
            expected_domain=expected_domain,
            p2_confidence=self.p2_confidence,
            content=content,
            source_count=self.source_count,
            source_grade=self.source_grade,
        )

    def apply_to_slot_registry(
        self,
        slot_registry: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        normalized = {slot: dict(meta) for slot, meta in slot_registry.items()}
        summary = self.knowledge_summary()
        for slot, meta in normalized.items():
            label = str(meta.get("label", slot)).strip() or slot
            normalized[slot]["knowledge_question_hint"] = (
                f"{self.label or self.domain} 도메인 지식 기준으로 홈페이지에 담을 {label}을 알려주세요."
            )
            if summary:
                normalized[slot]["p2_knowledge_summary"] = summary
        return normalized

    def knowledge_summary(self) -> str:
        if not self.knowledge_sections:
            return ""
        titles = [section.title for section in self.knowledge_sections[:3] if section.title]
        return ", ".join(titles)


def parse_p2_markdown(parse_input: P2MarkdownParseInput) -> P2MarkdownParseResult:
    """Parse raw P2 domain knowledge markdown into the P1 normalized artifact shape."""

    validation_error = _validate_parse_input(parse_input)
    if validation_error:
        return _failed_result(parse_input, validation_error)

    frontmatter, body = _split_frontmatter(parse_input.content)
    domain = str(frontmatter.get("domain") or parse_input.domain).strip()
    category = str(frontmatter.get("category") or parse_input.category).strip()
    label = str(frontmatter.get("label") or domain).strip()
    warnings: list[str] = []

    if domain != parse_input.expected_domain.strip():
        warnings.append("domain_mismatch")
    if parse_input.category.strip() and category != parse_input.category.strip():
        warnings.append("category_mismatch")
    if not frontmatter:
        warnings.append("frontmatter_missing")

    sections = tuple(_extract_knowledge_sections(body))
    evidence_refs = tuple(_extract_evidence_refs(parse_input.content, parse_input.source_s3_key))
    if not sections:
        warnings.append("knowledge_sections_missing")
    if not evidence_refs:
        warnings.append("source_refs_missing")

    parse_status = _parse_status(warnings)
    confidence = _extract_confidence(parse_input.content)
    if confidence is None:
        confidence = _default_confidence(parse_status, sections, evidence_refs)

    return P2MarkdownParseResult(
        domain=domain,
        category=category,
        label=label,
        p2_confidence=confidence,
        knowledge_sections=sections,
        evidence_refs=evidence_refs,
        frontmatter=frontmatter,
        warnings=tuple(warnings) or ("parsed",),
        parse_status=parse_status,
        source_s3_key=parse_input.source_s3_key,
        version=parse_input.version,
        source_count=parse_input.source_count or len(evidence_refs),
        source_grade=parse_input.source_grade,
    )


def _validate_parse_input(parse_input: P2MarkdownParseInput) -> str | None:
    required_strings = {
        "domain": parse_input.domain,
        "expected_domain": parse_input.expected_domain,
        "content": parse_input.content,
    }
    missing = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        return "required_fields_missing:" + ",".join(missing)
    return None


def _failed_result(parse_input: P2MarkdownParseInput, reason: str) -> P2MarkdownParseResult:
    return P2MarkdownParseResult(
        domain=parse_input.domain.strip(),
        category=parse_input.category.strip(),
        label=parse_input.domain.strip(),
        p2_confidence=0.0,
        knowledge_sections=(),
        evidence_refs=(),
        frontmatter={},
        warnings=(reason,),
        parse_status="failed",
        source_s3_key=parse_input.source_s3_key,
        version=parse_input.version,
        source_count=parse_input.source_count,
        source_grade=parse_input.source_grade,
    )


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    lines = content.strip().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    frontmatter_lines: list[str] = []
    end_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
        frontmatter_lines.append(line)

    if end_index is None:
        return {}, content

    return _parse_frontmatter(frontmatter_lines), "\n".join(lines[end_index + 1 :])


def _parse_frontmatter(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- ") and current_list_key:
            metadata.setdefault(current_list_key, []).append(line.split("- ", 1)[1].strip())
            continue
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        current_list_key = None
        if value == "":
            metadata[key] = []
            current_list_key = key
            continue
        metadata[key] = _parse_scalar(value)
    return metadata


def _parse_scalar(value: str) -> Any:
    cleaned = value.strip().strip("'\"")
    if "," in cleaned and not cleaned.startswith("http"):
        return [item.strip() for item in cleaned.split(",") if item.strip()]
    try:
        return float(cleaned)
    except ValueError:
        return cleaned


def _extract_knowledge_sections(body: str) -> list[KnowledgeSection]:
    sections: list[KnowledgeSection] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for raw_line in body.splitlines():
        heading = SECTION_HEADING_PATTERN.match(raw_line.strip())
        if heading:
            _append_section(sections, current_title, current_lines)
            current_title = _clean_section_title(heading.group("title"))
            current_lines = []
            if _is_source_heading(current_title):
                current_title = None
            continue
        if current_title is not None:
            current_lines.append(raw_line.rstrip())

    _append_section(sections, current_title, current_lines)
    return sections


def _append_section(
    sections: list[KnowledgeSection],
    title: str | None,
    lines: list[str],
) -> None:
    if not title:
        return
    body = "\n".join(line for line in lines if line.strip()).strip()
    if not body:
        return
    sections.append(
        KnowledgeSection(
            section_id=f"section_{len(sections) + 1:03d}",
            title=title,
            body=body,
            source_refs=tuple(sorted(set(SOURCE_TOKEN_PATTERN.findall(f"{title}\n{body}")))),
        )
    )


def _clean_section_title(title: str) -> str:
    return re.sub(r"^\d+[.)]\s*", "", title).strip()


def _is_source_heading(title: str) -> bool:
    normalized = re.sub(r"[\s#_*`:\-]+", "", title.lower())
    return normalized in {"출처", "근거", "source", "sources", "references", "참고"}


def _extract_evidence_refs(content: str, source_s3_key: str | None) -> list[EvidenceRef]:
    refs: dict[str, EvidenceRef] = {}
    for raw_line in content.splitlines():
        match = SOURCE_LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        ref_id = match.group(1).upper()
        refs[ref_id] = EvidenceRef(
            ref_id=ref_id,
            text=match.group("text").strip(),
            source_s3_key=source_s3_key,
        )
    return [refs[key] for key in sorted(refs)]


def _extract_confidence(content: str) -> float | None:
    match = CONFIDENCE_PATTERN.search(content)
    if not match:
        return None
    value = float(match.group(1))
    return max(0.0, min(1.0, round(value, 2)))


def _parse_status(warnings: list[str]) -> ParseStatus:
    blocking = {"domain_mismatch", "category_mismatch", "knowledge_sections_missing"}
    if any(warning in blocking for warning in warnings):
        return "failed"
    if "frontmatter_missing" in warnings or "source_refs_missing" in warnings:
        return "needs_enrichment"
    return "passed"


def _default_confidence(
    parse_status: ParseStatus,
    sections: tuple[KnowledgeSection, ...],
    evidence_refs: tuple[EvidenceRef, ...],
) -> float:
    if parse_status == "failed":
        return 0.0
    if sections and evidence_refs:
        return 0.78
    return 0.72
