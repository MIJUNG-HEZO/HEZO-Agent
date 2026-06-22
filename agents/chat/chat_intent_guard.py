"""LLM intent classification boundary for P1 chat turns."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, Protocol

from bedrock_claude_adapter import (
    ClaudeInvocationInput,
    ClaudeInvoker,
    ClaudeMessage,
)


ChatIntent = Literal["on_topic", "off_topic", "ambiguous", "needs_classification"]
ClassificationSource = Literal["llm", "static", "not_configured"]


@dataclass(frozen=True)
class ChatIntentClassifierInput:
    """Input used to decide whether a user turn can be treated as a slot answer."""

    message: Any
    current_question: str
    answered_slot: str
    domain: str
    domain_label: str
    slot_registry: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ChatIntentClassifierResult:
    """Intent classifier result consumed before slot mutation/storage."""

    intent: ChatIntent
    confidence: float
    store_allowed: bool
    classification_source: ClassificationSource
    reasons: tuple[str, ...]
    normalized_answer_candidate: Any = None
    redirect_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "store_allowed": self.store_allowed,
            "classification_source": self.classification_source,
            "reasons": list(self.reasons),
            "normalized_answer_candidate": self.normalized_answer_candidate,
            "redirect_message": self.redirect_message,
        }


class ChatIntentClassifier(Protocol):
    """Classifier boundary. Production should use an LLM-backed implementation."""

    def classify(
        self,
        classifier_input: ChatIntentClassifierInput,
    ) -> ChatIntentClassifierResult:
        ...


@dataclass(frozen=True)
class StaticChatIntentClassifier:
    """Deterministic classifier for local smoke tests."""

    intent: ChatIntent = "on_topic"
    confidence: float = 1.0
    reason: str = "static_intent"

    def classify(
        self,
        classifier_input: ChatIntentClassifierInput,
    ) -> ChatIntentClassifierResult:
        store_allowed = self.intent == "on_topic"
        return ChatIntentClassifierResult(
            intent=self.intent,
            confidence=self.confidence,
            store_allowed=store_allowed,
            classification_source="static",
            reasons=(self.reason,),
            normalized_answer_candidate=(
                classifier_input.message if self.intent == "on_topic" else None
            ),
            redirect_message=(
                None
                if self.intent == "on_topic"
                else "홈페이지 제작과 관련된 답변만 반영할 수 있어요. 다시 질문으로 돌아갈게요."
            ),
        )


class ClaudeChatIntentClassifier:
    """LLM-backed classifier using the existing Claude invoker boundary."""

    def __init__(self, invoker: ClaudeInvoker) -> None:
        self._invoker = invoker

    def classify(
        self,
        classifier_input: ChatIntentClassifierInput,
    ) -> ChatIntentClassifierResult:
        invocation = self._invoker.invoke(
            ClaudeInvocationInput(
                use_case="intent_classification",
                system_prompt=_INTENT_CLASSIFIER_PROMPT,
                messages=(
                    ClaudeMessage(
                        role="user",
                        content=json.dumps(
                            _classifier_payload(classifier_input),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                ),
                context={"node": "classify_intent"},
                max_tokens=512,
                temperature=0,
            )
        )
        if invocation.status != "succeeded":
            return needs_classification_result(
                "intent_classifier_failed:" + ",".join(invocation.reasons)
            )

        return _parse_classifier_text(invocation.text)


def classify_chat_intent(
    classifier_input: ChatIntentClassifierInput,
    classifier: ChatIntentClassifier | None,
) -> ChatIntentClassifierResult:
    """Classify chat intent, failing closed when no classifier is configured."""

    if classifier is None:
        return needs_classification_result("intent_classifier_not_configured")
    return classifier.classify(classifier_input)


def needs_classification_result(reason: str) -> ChatIntentClassifierResult:
    return ChatIntentClassifierResult(
        intent="needs_classification",
        confidence=0.0,
        store_allowed=False,
        classification_source="not_configured",
        reasons=(reason,),
        normalized_answer_candidate=None,
        redirect_message="입력 의도를 확인한 뒤 다시 진행할게요.",
    )


def _classifier_payload(classifier_input: ChatIntentClassifierInput) -> dict[str, Any]:
    slot = classifier_input.slot_registry.get(classifier_input.answered_slot, {})
    return {
        "user_message": classifier_input.message,
        "current_question": classifier_input.current_question,
        "answered_slot": classifier_input.answered_slot,
        "answered_slot_label": slot.get("label"),
        "answered_slot_question_hint": slot.get("question_hint"),
        "domain": classifier_input.domain,
        "domain_label": classifier_input.domain_label,
        "allowed_task": "HEZO 홈페이지 제작을 위한 현재 질문 답변인지 판단",
    }


def _parse_classifier_text(text: str) -> ChatIntentClassifierResult:
    try:
        payload = json.loads(_extract_json_object(text))
    except json.JSONDecodeError:
        return needs_classification_result("intent_classifier_invalid_json")

    intent = payload.get("intent")
    if intent not in {"on_topic", "off_topic", "ambiguous"}:
        return needs_classification_result("intent_classifier_invalid_intent")

    confidence = float(payload.get("confidence", 0.0))
    store_allowed = intent == "on_topic"
    return ChatIntentClassifierResult(
        intent=intent,
        confidence=confidence,
        store_allowed=store_allowed,
        classification_source="llm",
        reasons=tuple(payload.get("reasons", ["intent_classified_by_llm"])),
        normalized_answer_candidate=payload.get("normalized_answer_candidate"),
        redirect_message=payload.get("redirect_message"),
    )


_INTENT_CLASSIFIER_PROMPT = """
당신은 HEZO P1 Chat Agent의 intent classifier입니다.
사용자 입력이 현재 홈페이지 제작 질문에 대한 답변인지 분류하세요.

반드시 JSON만 출력하세요.
스키마:
{
  "intent": "on_topic" | "off_topic" | "ambiguous",
  "confidence": 0.0-1.0,
  "reasons": ["..."],
  "normalized_answer_candidate": "on_topic일 때만 답변 후보, 아니면 null",
  "redirect_message": "off_topic/ambiguous일 때 사용자에게 보여줄 짧은 문장 (한국어, 30자 이내)"
}

판단 기준:
- on_topic: 현재 answered_slot에 대한 답변이거나 홈페이지에 반영 가능한 비즈니스 정보.
  다소 짧거나 구체적이지 않아도 비즈니스와 관련되면 on_topic.
  예) 핵심 서비스 질문에 "최저가 보장", "도매 유통", "커스텀 케이크 제작" → on_topic
- off_topic: 홈페이지 제작과 완전히 무관한 내용 (잡담, 정치, 날씨, 주식 등).
  또는 현재 slot과 명백히 다른 slot의 답변 (예: 핵심 서비스 질문에 전화번호·이메일 입력).
- ambiguous: 의미 파악이 불가능한 입력 (단순 "응", "ㅇ", 숫자 단독, 특수문자만 등).

비즈니스 관련 내용은 가능한 on_topic으로 판단하세요.
off_topic/ambiguous는 저장하면 안 됩니다.
""".strip()


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [
            line
            for line in stripped.splitlines()
            if not line.strip().startswith("```")
        ]
        stripped = "\n".join(lines).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and start < end:
        return stripped[start : end + 1]
    return stripped
