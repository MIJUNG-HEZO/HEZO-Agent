"""Proactive question candidate builder for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


QuestionSource = Literal["p2_markdown", "fallback"]
ReviewStatus = Literal["passed", "needs_enrichment", "failed"]


@dataclass(frozen=True)
class ProactiveQuestionInput:
    """Context used to build proactive question candidates."""

    domain: str
    domain_label: str
    p1_markdown_review_status: ReviewStatus
    p2_markdown_usable_for_questions: bool
    slot_registry: dict[str, dict[str, Any]]
    known_answers: dict[str, Any]
    missing_slots: tuple[str, ...]
    p2_knowledge_summary: str = ""
    max_questions: int = 3


@dataclass(frozen=True)
class ProactiveQuestionCandidate:
    """Question candidate shown to the user in the proactive questioning stage."""

    slot: str
    question: str
    priority: int
    source: QuestionSource
    fallback: bool
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "question": self.question,
            "priority": self.priority,
            "source": self.source,
            "fallback": self.fallback,
            "required": self.required,
        }


def build_proactive_question_candidates(
    question_input: ProactiveQuestionInput,
) -> list[ProactiveQuestionCandidate]:
    """Build deterministic proactive question candidates without LLM calls."""

    _validate_question_input(question_input)

    usable_p2_markdown = (
        question_input.p2_markdown_usable_for_questions
        and question_input.p1_markdown_review_status in {"passed", "needs_enrichment"}
    )
    target_slots = _target_slots(question_input)

    ordered_slots = sorted(
        target_slots,
        key=lambda slot: (
            not bool(question_input.slot_registry[slot].get("required", False)),
            target_slots.index(slot),
        ),
    )

    candidates: list[ProactiveQuestionCandidate] = []
    for slot in ordered_slots[: question_input.max_questions]:
        slot_meta = question_input.slot_registry[slot]
        knowledge_hint = str(slot_meta.get("knowledge_question_hint", "")).strip()
        if usable_p2_markdown and (question_input.p2_knowledge_summary.strip() or knowledge_hint):
            question = knowledge_hint or _knowledge_question(question_input.domain_label, slot_meta)
            source: QuestionSource = "p2_markdown"
            fallback = False
        else:
            question = _fallback_question(question_input.domain_label, slot_meta)
            source = "fallback"
            fallback = True

        candidates.append(
            ProactiveQuestionCandidate(
                slot=slot,
                question=question,
                priority=len(candidates) + 1,
                source=source,
                fallback=fallback,
                required=bool(slot_meta.get("required", False)),
            )
        )

    return candidates


def _validate_question_input(question_input: ProactiveQuestionInput) -> None:
    required_strings = {
        "domain": question_input.domain,
        "domain_label": question_input.domain_label,
    }
    missing_fields = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing_fields:
        raise ValueError("required_fields_missing:" + ",".join(missing_fields))

    if not question_input.slot_registry:
        raise ValueError("slot_registry_empty")

    if question_input.max_questions <= 0:
        raise ValueError("max_questions_must_be_positive")


def _target_slots(question_input: ProactiveQuestionInput) -> list[str]:
    candidate_slots = (
        list(question_input.missing_slots)
        if question_input.missing_slots
        else list(question_input.slot_registry.keys())
    )

    return [
        slot
        for slot in candidate_slots
        if slot in question_input.slot_registry and not _has_answer(question_input.known_answers.get(slot))
    ]


def _has_answer(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _fallback_question(domain_label: str, slot_meta: dict[str, Any]) -> str:
    label = str(slot_meta.get("label", "")).strip() or "필수 정보"
    if bool(slot_meta.get("required", False)):
        return f"{domain_label} 홈페이지에 꼭 들어가야 할 {label}을 알려주세요."
    return f"{domain_label} 홈페이지에 추가로 보여주고 싶은 {label}이 있나요?"


def _knowledge_question(domain_label: str, slot_meta: dict[str, Any]) -> str:
    label = str(slot_meta.get("label", "")).strip() or "필수 정보"
    return f"{domain_label} 도메인 지식 기준으로 홈페이지에 담을 {label}을 알려주세요."
