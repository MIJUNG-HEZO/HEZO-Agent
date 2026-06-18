"""Chat session start pipeline for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from p2_markdown_loader import P2MarkdownLoadInput, P2MarkdownLoadResult, load_p2_markdown_from_s3
from p2_markdown_parser import P2MarkdownParseResult, parse_p2_markdown
from p2_markdown_review import P2MarkdownReviewInput, P2MarkdownReviewResult, review_p2_markdown
from proactive_questioning import (
    ProactiveQuestionCandidate,
    ProactiveQuestionInput,
    build_proactive_question_candidates,
)
from s3_artifact_store import P2_MARKDOWNS_BUCKET, S3ArtifactStore


SessionStartStatus = Literal["ready_for_user_question", "needs_llm_enrichment", "failed"]
NextStage = Literal["proactive_questioning", "llm_enrichment", "p2_retry"]


@dataclass(frozen=True)
class ChatSessionStartInput:
    """Input required to start a P1 chat session after domain selection."""

    session_id: str
    site_id: str
    user_id: str
    domain: str
    domain_label: str
    selected_template: str
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    source_s3_key: str | None = None
    version: str | None = "v001"
    source_count: int = 0
    source_grade: str = "unknown"
    bucket: str = P2_MARKDOWNS_BUCKET
    max_questions: int = 3


@dataclass(frozen=True)
class ChatSessionStartResult:
    """Normalized result returned by the chat session start pipeline."""

    status: SessionStartStatus
    next_stage: NextStage
    llm_required: bool
    p2_markdown_load: P2MarkdownLoadResult
    p2_markdown_parse: P2MarkdownParseResult
    p2_markdown_review: P2MarkdownReviewResult
    question_candidates: tuple[ProactiveQuestionCandidate, ...]
    slot_registry: dict[str, dict[str, Any]]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "next_stage": self.next_stage,
            "llm_required": self.llm_required,
            "p2_markdown_load": self.p2_markdown_load.to_dict(),
            "p2_markdown_parse": self.p2_markdown_parse.to_dict(),
            "p2_markdown_review": self.p2_markdown_review.to_state(),
            "question_candidates": [
                candidate.to_dict() for candidate in self.question_candidates
            ],
            "slot_registry": self.slot_registry,
            "reasons": list(self.reasons),
        }


def start_chat_session(
    session_input: ChatSessionStartInput,
    store: S3ArtifactStore,
) -> ChatSessionStartResult:
    """Load P2 markdown and build initial proactive questions for a chat session."""

    _validate_session_input(session_input)

    load_result = load_p2_markdown_from_s3(
        P2MarkdownLoadInput(
            domain=session_input.domain,
            expected_domain=session_input.domain,
            slot_registry=session_input.slot_registry,
            version=session_input.version,
            source_s3_key=session_input.source_s3_key,
            source_count=session_input.source_count,
            source_grade=session_input.source_grade,
            bucket=session_input.bucket,
            required_slots=session_input.missing_slots,
        ),
        store,
    )
    parse_result = parse_p2_markdown(load_result.parse_input)
    enriched_slot_registry = parse_result.apply_to_slot_registry(session_input.slot_registry)
    review_result = review_p2_markdown(
        P2MarkdownReviewInput(
            domain=parse_result.domain,
            expected_domain=session_input.domain,
            p2_confidence=parse_result.p2_confidence,
            content=load_result.content,
            required_slot_questions=parse_result.required_slot_questions,
            required_slots=session_input.missing_slots,
            source_count=parse_result.source_count,
            source_grade=parse_result.source_grade,
        )
    )
    question_candidates = tuple(
        build_proactive_question_candidates(
            ProactiveQuestionInput(
                domain=session_input.domain,
                domain_label=session_input.domain_label,
                p1_markdown_review_status=review_result.p1_markdown_review_status,
                p2_markdown_usable_for_questions=review_result.p2_markdown_usable_for_questions,
                slot_registry=enriched_slot_registry,
                known_answers=session_input.known_answers,
                missing_slots=session_input.missing_slots,
                max_questions=session_input.max_questions,
            )
        )
    )
    status, next_stage, llm_required, reasons = _session_routing(
        parse_result=parse_result,
        review_result=review_result,
        question_candidates=question_candidates,
    )

    return ChatSessionStartResult(
        status=status,
        next_stage=next_stage,
        llm_required=llm_required,
        p2_markdown_load=load_result,
        p2_markdown_parse=parse_result,
        p2_markdown_review=review_result,
        question_candidates=question_candidates,
        slot_registry=enriched_slot_registry,
        reasons=reasons,
    )


def _validate_session_input(session_input: ChatSessionStartInput) -> None:
    required_strings = {
        "session_id": session_input.session_id,
        "site_id": session_input.site_id,
        "user_id": session_input.user_id,
        "domain": session_input.domain,
        "domain_label": session_input.domain_label,
        "selected_template": session_input.selected_template,
        "bucket": session_input.bucket,
    }
    missing = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError("required_fields_missing:" + ",".join(missing))
    if not session_input.slot_registry:
        raise ValueError("slot_registry_empty")
    if session_input.max_questions <= 0:
        raise ValueError("max_questions_must_be_positive")


def _session_routing(
    *,
    parse_result: P2MarkdownParseResult,
    review_result: P2MarkdownReviewResult,
    question_candidates: tuple[ProactiveQuestionCandidate, ...],
) -> tuple[SessionStartStatus, NextStage, bool, tuple[str, ...]]:
    reasons: list[str] = [
        f"parse_status:{parse_result.parse_status}",
        f"review_status:{review_result.p1_markdown_review_status}",
    ]
    fallback_slots = tuple(
        candidate.slot for candidate in question_candidates if candidate.fallback
    )
    if fallback_slots:
        reasons.append("fallback_questions_used:" + ",".join(fallback_slots))
    if not question_candidates:
        reasons.append("question_candidates_empty")

    if review_result.p1_markdown_review_status == "failed":
        return "failed", "p2_retry", True, tuple(reasons)

    llm_required = (
        parse_result.parse_status != "passed"
        or review_result.p1_markdown_review_status != "passed"
        or bool(fallback_slots)
        or not question_candidates
    )
    if llm_required:
        return "needs_llm_enrichment", "llm_enrichment", True, tuple(reasons)
    return "ready_for_user_question", "proactive_questioning", False, tuple(reasons)
