"""P2 markdown artifact parser/normalizer for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal

from p2_markdown_review import P2MarkdownReviewInput


ParseStatus = Literal["passed", "needs_enrichment", "failed"]

CONFIDENCE_PATTERN = re.compile(r"(?:confidence|신뢰도|확신도)\s*[:=]\s*([01](?:\.\d+)?)", re.IGNORECASE)
QUESTION_MARKERS = ("?", "？", "무엇인가요", "어떤", "알려주세요", "있나요")
EVIDENCE_HEADINGS = ("근거", "출처", "source", "evidence", "references", "참고")


@dataclass(frozen=True)
class P2MarkdownParseInput:
    """Raw P2 markdown artifact and context received by P1."""

    domain: str
    expected_domain: str
    content: str
    slot_registry: dict[str, dict[str, Any]]
    source_s3_key: str | None = None
    version: str | None = None
    source_count: int = 0
    source_grade: str = "unknown"
    required_slots: tuple[str, ...] = ()


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
class P2MarkdownParseResult:
    """P1 normalized P2 markdown artifact."""

    domain: str
    p2_confidence: float
    required_slot_questions: dict[str, str]
    slot_question_hints: dict[str, str]
    evidence_refs: tuple[EvidenceRef, ...]
    warnings: tuple[str, ...]
    parse_status: ParseStatus
    source_s3_key: str | None = None
    version: str | None = None
    source_count: int = 0
    source_grade: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "p2_confidence": self.p2_confidence,
            "required_slot_questions": dict(self.required_slot_questions),
            "slot_question_hints": dict(self.slot_question_hints),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
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
            required_slot_questions=self.required_slot_questions,
            required_slots=tuple(self.required_slot_questions.keys()),
            source_count=self.source_count,
            source_grade=self.source_grade,
        )

    def apply_to_slot_registry(
        self,
        slot_registry: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        normalized = {slot: dict(meta) for slot, meta in slot_registry.items()}
        for slot, question in self.slot_question_hints.items():
            if slot in normalized and question.strip():
                normalized[slot]["question_hint"] = question.strip()
        return normalized


def parse_p2_markdown(parse_input: P2MarkdownParseInput) -> P2MarkdownParseResult:
    """Parse raw P2 markdown into the P1 normalized artifact shape."""

    validation_error = _validate_parse_input(parse_input)
    if validation_error:
        return _failed_result(parse_input, validation_error)

    warnings: list[str] = []
    if parse_input.domain.strip() != parse_input.expected_domain.strip():
        warnings.append("domain_mismatch")

    slot_question_hints = _extract_slot_questions(parse_input)
    evidence_refs = tuple(_extract_evidence_refs(parse_input.content, parse_input.source_s3_key))
    required_slots = _required_slots(parse_input)
    missing_required_slots = tuple(
        slot for slot in required_slots if not slot_question_hints.get(slot)
    )
    warnings.extend(f"required_slot_question_missing:{slot}" for slot in missing_required_slots)

    if not slot_question_hints and not evidence_refs:
        warnings.append("malformed_markdown")

    parse_status = _parse_status(warnings)
    confidence = _extract_confidence(parse_input.content)
    if confidence is None:
        confidence = _default_confidence(slot_question_hints, parse_status)

    return P2MarkdownParseResult(
        domain=parse_input.domain.strip(),
        p2_confidence=confidence,
        required_slot_questions={
            slot: slot_question_hints[slot]
            for slot in required_slots
            if slot in slot_question_hints
        },
        slot_question_hints=slot_question_hints,
        evidence_refs=evidence_refs,
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
    if not parse_input.slot_registry:
        return "slot_registry_empty"
    return None


def _failed_result(parse_input: P2MarkdownParseInput, reason: str) -> P2MarkdownParseResult:
    return P2MarkdownParseResult(
        domain=parse_input.domain.strip(),
        p2_confidence=0.0,
        required_slot_questions={},
        slot_question_hints={},
        evidence_refs=(),
        warnings=(reason,),
        parse_status="failed",
        source_s3_key=parse_input.source_s3_key,
        version=parse_input.version,
        source_count=parse_input.source_count,
        source_grade=parse_input.source_grade,
    )


def _extract_slot_questions(parse_input: P2MarkdownParseInput) -> dict[str, str]:
    slot_questions: dict[str, str] = {}
    current_slot: str | None = None

    for raw_line in parse_input.content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading_slot = _slot_from_heading(line, parse_input.slot_registry)
        if heading_slot:
            current_slot = heading_slot
            continue

        candidate_line = _strip_markdown_prefix(line)
        direct_slot = _slot_from_line(candidate_line, parse_input.slot_registry)
        target_slot = direct_slot or current_slot
        if not target_slot or target_slot in slot_questions:
            continue
        if not _looks_like_question(candidate_line):
            continue

        question = _clean_question(candidate_line, target_slot, parse_input.slot_registry)
        if question:
            slot_questions[target_slot] = question

    return slot_questions


def _extract_evidence_refs(content: str, source_s3_key: str | None) -> list[EvidenceRef]:
    evidence_refs: list[EvidenceRef] = []
    in_evidence_section = False

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            in_evidence_section = _is_evidence_heading(line)
            continue
        if not in_evidence_section:
            continue

        evidence_text = _strip_markdown_prefix(line)
        if evidence_text:
            evidence_refs.append(
                EvidenceRef(
                    ref_id=f"evidence_{len(evidence_refs) + 1:03d}",
                    text=evidence_text,
                    source_s3_key=source_s3_key,
                )
            )

    return evidence_refs


def _slot_from_heading(line: str, slot_registry: dict[str, dict[str, Any]]) -> str | None:
    if not line.startswith("#"):
        return None
    normalized = _normalize_text(line)
    return _find_slot(normalized, slot_registry)


def _slot_from_line(line: str, slot_registry: dict[str, dict[str, Any]]) -> str | None:
    normalized = _normalize_text(line)
    return _find_slot(normalized, slot_registry)


def _find_slot(normalized_text: str, slot_registry: dict[str, dict[str, Any]]) -> str | None:
    for slot, meta in slot_registry.items():
        slot_token = _normalize_text(slot)
        label_token = _normalize_text(str(meta.get("label", "")))
        if slot_token and slot_token in normalized_text:
            return slot
        if label_token and label_token in normalized_text:
            return slot
    return None


def _strip_markdown_prefix(line: str) -> str:
    cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", line)
    return cleaned.strip()


def _looks_like_question(line: str) -> bool:
    lowered = line.lower()
    return any(marker.lower() in lowered for marker in QUESTION_MARKERS)


def _clean_question(line: str, slot: str, slot_registry: dict[str, dict[str, Any]]) -> str:
    question = re.sub(r"^\[?[A-Za-z0-9_\-]+\]?\s*[:\-]\s*", "", line).strip()
    label = str(slot_registry[slot].get("label", "")).strip()
    for token in (slot, label):
        if token:
            question = re.sub(rf"^\*?\*?{re.escape(token)}\*?\*?\s*[:\-]\s*", "", question).strip()
    return question


def _is_evidence_heading(line: str) -> bool:
    normalized = _normalize_text(line)
    return any(_normalize_text(marker) in normalized for marker in EVIDENCE_HEADINGS)


def _extract_confidence(content: str) -> float | None:
    match = CONFIDENCE_PATTERN.search(content)
    if not match:
        return None
    value = float(match.group(1))
    return max(0.0, min(1.0, round(value, 2)))


def _required_slots(parse_input: P2MarkdownParseInput) -> tuple[str, ...]:
    if parse_input.required_slots:
        return parse_input.required_slots
    return tuple(
        slot
        for slot, meta in parse_input.slot_registry.items()
        if bool(meta.get("required", False))
    )


def _parse_status(warnings: list[str]) -> ParseStatus:
    if any(warning in {"domain_mismatch", "malformed_markdown"} for warning in warnings):
        return "failed"
    if any(warning.startswith("required_slot_question_missing:") for warning in warnings):
        return "needs_enrichment"
    return "passed"


def _default_confidence(
    slot_question_hints: dict[str, str],
    parse_status: ParseStatus,
) -> float:
    if parse_status == "failed":
        return 0.0
    if parse_status == "needs_enrichment":
        return 0.72
    if slot_question_hints:
        return 0.78
    return 0.0


def _normalize_text(value: str) -> str:
    return re.sub(r"[\s_*`#\[\]():\-]+", "", value.lower())
