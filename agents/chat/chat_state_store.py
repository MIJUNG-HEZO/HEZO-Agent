"""Chat state/checkpoint store skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


MessageRole = Literal["user", "assistant", "system"]

SESSION_PK_PREFIX = "SESSION#"
META_SK = "META"
MESSAGE_SK_PREFIX = "MESSAGE#"
CHECKPOINT_SK_PREFIX = "CHECKPOINT#"
CONTRACT_SK_PREFIX = "CONTRACT#"
GUARDRAIL_SK_PREFIX = "GUARDRAIL#"


@dataclass(frozen=True)
class SessionMetadata:
    """Session metadata persisted as the session root item."""

    session_id: str
    user_id: str
    site_id: str
    stage: str
    domain: str | None = None


@dataclass(frozen=True)
class ChatMessage:
    """Chat message item stored under a session partition."""

    session_id: str
    message_id: str
    role: MessageRole
    content: str
    created_at: str


@dataclass(frozen=True)
class ChatCheckpoint:
    """LangGraph-compatible checkpoint skeleton."""

    session_id: str
    stage: str
    version: int
    state: dict[str, Any]


@dataclass(frozen=True)
class GuardrailSummary:
    """Guardrail result summary stored with the session state."""

    session_id: str
    target: str
    action: str
    store_allowed: bool
    reasons: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class StoredItem:
    """DynamoDB single-table item shape used by the mock store."""

    pk: str
    sk: str
    item_type: str
    data: dict[str, Any] = field(default_factory=dict)


class ChatStateStore(Protocol):
    """Repository boundary for future DynamoDB-backed chat state storage."""

    def save_session_metadata(self, metadata: SessionMetadata) -> StoredItem:
        ...

    def append_message(self, message: ChatMessage) -> StoredItem:
        ...

    def save_checkpoint(self, checkpoint: ChatCheckpoint) -> StoredItem:
        ...

    def load_latest_checkpoint(self, session_id: str) -> ChatCheckpoint | None:
        ...

    def save_guardrail_result(self, summary: GuardrailSummary) -> StoredItem:
        ...


class InMemoryChatStateStore:
    """In-memory implementation used by local smoke tests."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], StoredItem] = {}

    def save_session_metadata(self, metadata: SessionMetadata) -> StoredItem:
        _require_text("session_id", metadata.session_id)
        _require_text("user_id", metadata.user_id)
        _require_text("site_id", metadata.site_id)
        _require_text("stage", metadata.stage)

        item = StoredItem(
            pk=session_pk(metadata.session_id),
            sk=META_SK,
            item_type="session_metadata",
            data={
                "session_id": metadata.session_id,
                "user_id": metadata.user_id,
                "site_id": metadata.site_id,
                "stage": metadata.stage,
                "domain": metadata.domain,
            },
        )
        return self._put(item)

    def append_message(self, message: ChatMessage) -> StoredItem:
        _require_text("session_id", message.session_id)
        _require_text("message_id", message.message_id)
        _require_text("content", message.content)
        _require_text("created_at", message.created_at)
        if message.role not in {"user", "assistant", "system"}:
            raise ValueError("message_role_invalid")

        item = StoredItem(
            pk=session_pk(message.session_id),
            sk=message_sk(message.created_at, message.message_id),
            item_type="message",
            data={
                "session_id": message.session_id,
                "message_id": message.message_id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            },
        )
        return self._put(item)

    def save_checkpoint(self, checkpoint: ChatCheckpoint) -> StoredItem:
        _require_text("session_id", checkpoint.session_id)
        _require_text("stage", checkpoint.stage)
        if checkpoint.version <= 0:
            raise ValueError("checkpoint_version_must_be_positive")
        if not checkpoint.state:
            raise ValueError("checkpoint_state_empty")

        item = StoredItem(
            pk=session_pk(checkpoint.session_id),
            sk=checkpoint_sk(checkpoint.stage, checkpoint.version),
            item_type="checkpoint",
            data={
                "session_id": checkpoint.session_id,
                "stage": checkpoint.stage,
                "version": checkpoint.version,
                "state": dict(checkpoint.state),
            },
        )
        return self._put(item)

    def load_latest_checkpoint(self, session_id: str) -> ChatCheckpoint | None:
        _require_text("session_id", session_id)
        pk = session_pk(session_id)
        checkpoints = [
            item
            for (item_pk, item_sk), item in self._items.items()
            if item_pk == pk and item_sk.startswith(CHECKPOINT_SK_PREFIX)
        ]
        if not checkpoints:
            return None

        latest = max(checkpoints, key=lambda item: int(item.data["version"]))
        return ChatCheckpoint(
            session_id=str(latest.data["session_id"]),
            stage=str(latest.data["stage"]),
            version=int(latest.data["version"]),
            state=dict(latest.data["state"]),
        )

    def save_guardrail_result(self, summary: GuardrailSummary) -> StoredItem:
        _require_text("session_id", summary.session_id)
        _require_text("target", summary.target)
        _require_text("action", summary.action)
        _require_text("created_at", summary.created_at)

        item = StoredItem(
            pk=session_pk(summary.session_id),
            sk=guardrail_sk(summary.created_at, summary.target),
            item_type="guardrail_summary",
            data={
                "session_id": summary.session_id,
                "target": summary.target,
                "action": summary.action,
                "store_allowed": summary.store_allowed,
                "reasons": list(summary.reasons),
                "created_at": summary.created_at,
            },
        )
        return self._put(item)

    def list_items(self, session_id: str) -> list[StoredItem]:
        """Return stored items for local assertions."""

        _require_text("session_id", session_id)
        pk = session_pk(session_id)
        return [item for (item_pk, _), item in self._items.items() if item_pk == pk]

    def _put(self, item: StoredItem) -> StoredItem:
        self._items[(item.pk, item.sk)] = item
        return item


def session_pk(session_id: str) -> str:
    _require_text("session_id", session_id)
    return f"{SESSION_PK_PREFIX}{session_id.strip()}"


def message_sk(created_at: str, message_id: str) -> str:
    _require_text("created_at", created_at)
    _require_text("message_id", message_id)
    return f"{MESSAGE_SK_PREFIX}{created_at.strip()}#{message_id.strip()}"


def checkpoint_sk(stage: str, version: int) -> str:
    _require_text("stage", stage)
    if version <= 0:
        raise ValueError("checkpoint_version_must_be_positive")
    return f"{CHECKPOINT_SK_PREFIX}{stage.strip()}#{version:06d}"


def contract_sk(version: int) -> str:
    if version <= 0:
        raise ValueError("contract_version_must_be_positive")
    return f"{CONTRACT_SK_PREFIX}{version:06d}"


def guardrail_sk(created_at: str, target: str) -> str:
    _require_text("created_at", created_at)
    _require_text("target", target)
    return f"{GUARDRAIL_SK_PREFIX}{created_at.strip()}#{target.strip()}"


def _require_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}_missing")
