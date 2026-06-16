"""P2 markdown review logic for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReviewStatus = Literal["passed", "needs_enrichment", "failed"]

CONFIDENCE_CUTOFF = 0.70
REVIEW_SCORE_CUTOFF = 0.70

INJECTION_PATTERNS = [
    "이전 지시 무시",
    "앞의 지시 무시",
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "act as",
]

EXAGGERATION_PATTERNS = [
    "100% 보장",
    "무조건",
    "최고",
    "1위",
    "완벽 보장",
]


@dataclass(frozen=True)
class P2MarkdownReviewInput:
    """Metadata received with a P2 markdown artifact."""

    domain: str
    expected_domain: str
    p2_confidence: float
    content: str
    required_slot_questions: dict[str, str] = field(default_factory=dict)
    required_slots: tuple[str, ...] = ()
    source_count: int = 0
    source_grade: str = "unknown"


@dataclass(frozen=True)
class P2MarkdownReviewResult:
    """P1 review result used by chat agent state."""

    p1_markdown_review_status: ReviewStatus
    p1_markdown_review_score: float
    p2_markdown_usable_for_questions: bool
    reasons: tuple[str, ...]

    def to_state(self) -> dict[str, object]:
        return {
            "p1_markdown_review_status": self.p1_markdown_review_status,
            "p1_markdown_review_score": self.p1_markdown_review_score,
            "p2_markdown_usable_for_questions": self.p2_markdown_usable_for_questions,
            "reasons": list(self.reasons),
        }


def review_p2_markdown(review_input: P2MarkdownReviewInput) -> P2MarkdownReviewResult:
    """Review whether received P2 markdown can be used for proactive questions."""

    reasons: list[str] = []
    score = 1.0

    content_lower = review_input.content.lower()
    if _contains_any(content_lower, INJECTION_PATTERNS):
        return P2MarkdownReviewResult(
            p1_markdown_review_status="failed",
            p1_markdown_review_score=0.10,
            p2_markdown_usable_for_questions=False,
            reasons=("injection_suspected",),
        )

    if review_input.domain != review_input.expected_domain:
        score -= 0.35
        reasons.append("domain_mismatch")

    if review_input.p2_confidence < CONFIDENCE_CUTOFF:
        return P2MarkdownReviewResult(
            p1_markdown_review_status="failed",
            p1_markdown_review_score=round(review_input.p2_confidence, 2),
            p2_markdown_usable_for_questions=False,
            reasons=("p2_confidence_below_cutoff",),
        )

    missing_slots = _missing_required_slots(review_input)
    if missing_slots:
        score -= min(0.30, 0.10 * len(missing_slots))
        reasons.append("required_slot_questions_missing:" + ",".join(missing_slots))

    if _contains_any(content_lower, EXAGGERATION_PATTERNS):
        score -= 0.10
        reasons.append("exaggeration_risk")

    if review_input.source_count <= 0:
        score -= 0.10
        reasons.append("source_count_missing")

    score = max(0.0, min(1.0, round(score, 2)))

    if score >= REVIEW_SCORE_CUTOFF and not reasons:
        status: ReviewStatus = "passed"
    elif score >= REVIEW_SCORE_CUTOFF:
        status = "needs_enrichment"
    elif _has_blocking_reason(reasons):
        status = "failed"
    else:
        status = "needs_enrichment"

    return P2MarkdownReviewResult(
        p1_markdown_review_status=status,
        p1_markdown_review_score=score,
        p2_markdown_usable_for_questions=status != "failed",
        reasons=tuple(reasons) or ("passed",),
    )


def _contains_any(content_lower: str, patterns: list[str]) -> bool:
    return any(pattern.lower() in content_lower for pattern in patterns)


def _missing_required_slots(review_input: P2MarkdownReviewInput) -> list[str]:
    return [
        slot
        for slot in review_input.required_slots
        if not review_input.required_slot_questions.get(slot)
    ]


def _has_blocking_reason(reasons: list[str]) -> bool:
    return "domain_mismatch" in reasons or "p2_confidence_below_cutoff" in reasons
