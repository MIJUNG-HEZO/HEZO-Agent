"""S3 artifact storage adapter skeleton for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Literal, Protocol


ArtifactKind = Literal[
    "chat_transcript",
    "p2_markdown",
    "contract_draft",
    "contract_final",
    "guardrail_report",
]

CHAT_TRANSCRIPTS_BUCKET = "dev-hezo-chat-transcripts"
P2_MARKDOWNS_BUCKET = "dev-hezo-p2-markdowns"
CONTRACTS_BUCKET = "dev-hezo-p4-contracts"


@dataclass(frozen=True)
class ArtifactRef:
    """S3 artifact reference passed between chat stages."""

    bucket: str
    key: str
    artifact_kind: ArtifactKind
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)

    def uri(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "key": self.key,
            "uri": self.uri(),
            "artifact_kind": self.artifact_kind,
            "content_type": self.content_type,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ArtifactPayload:
    """Artifact payload stored by the future S3 adapter."""

    ref: ArtifactRef
    body: str | dict[str, Any] | list[Any]
    guardrail_action: str = "NONE"
    store_allowed: bool = True


class S3ArtifactStore(Protocol):
    """Repository boundary for future S3-backed artifact storage."""

    def build_artifact_ref(self, artifact_kind: ArtifactKind, **kwargs: Any) -> ArtifactRef:
        ...

    def put_artifact(self, payload: ArtifactPayload) -> ArtifactRef:
        ...

    def get_artifact(self, ref: ArtifactRef) -> str:
        ...


class InMemoryS3ArtifactStore:
    """In-memory implementation used by local smoke tests."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], str] = {}

    def build_artifact_ref(self, artifact_kind: ArtifactKind, **kwargs: Any) -> ArtifactRef:
        if artifact_kind == "chat_transcript":
            return ArtifactRef(
                bucket=CHAT_TRANSCRIPTS_BUCKET,
                key=chat_transcript_key(
                    str(kwargs.get("session_id", "")),
                    int(kwargs.get("version", 0)),
                ),
                artifact_kind=artifact_kind,
                content_type="application/json",
            )
        if artifact_kind == "p2_markdown":
            return ArtifactRef(
                bucket=P2_MARKDOWNS_BUCKET,
                key=p2_markdown_key(
                    str(kwargs.get("domain", "")),
                    str(kwargs.get("version", "")),
                ),
                artifact_kind=artifact_kind,
                content_type="text/markdown; charset=utf-8",
            )
        if artifact_kind == "contract_draft":
            return ArtifactRef(
                bucket=CONTRACTS_BUCKET,
                key=contract_draft_key(
                    str(kwargs.get("site_id", "")),
                    int(kwargs.get("version", 0)),
                ),
                artifact_kind=artifact_kind,
                content_type="application/json",
            )
        if artifact_kind == "contract_final":
            return ArtifactRef(
                bucket=CONTRACTS_BUCKET,
                key=contract_final_key(str(kwargs.get("site_id", ""))),
                artifact_kind=artifact_kind,
                content_type="application/json",
            )
        if artifact_kind == "guardrail_report":
            return ArtifactRef(
                bucket=CHAT_TRANSCRIPTS_BUCKET,
                key=guardrail_report_key(
                    str(kwargs.get("session_id", "")),
                    str(kwargs.get("target", "")),
                    str(kwargs.get("timestamp", "")),
                ),
                artifact_kind=artifact_kind,
                content_type="application/json",
            )
        raise ValueError("artifact_kind_invalid")

    def put_artifact(self, payload: ArtifactPayload) -> ArtifactRef:
        if not payload.store_allowed:
            raise ValueError("artifact_store_blocked_by_guardrail")
        if payload.guardrail_action != "NONE":
            raise ValueError("artifact_guardrail_action_not_clear")

        self._objects[(payload.ref.bucket, payload.ref.key)] = _serialize_body(payload.body)
        return payload.ref

    def get_artifact(self, ref: ArtifactRef) -> str:
        _require_text("bucket", ref.bucket)
        _require_text("key", ref.key)
        try:
            return self._objects[(ref.bucket, ref.key)]
        except KeyError as error:
            raise ValueError("artifact_not_found") from error


def chat_transcript_key(session_id: str, version: int) -> str:
    _require_text("session_id", session_id)
    _require_positive_version(version, "transcript_version")
    return f"sessions/{session_id.strip()}/transcripts/{version:06d}.json"


def p2_markdown_key(domain: str, version: str) -> str:
    _require_text("domain", domain)
    _require_text("version", version)
    return f"domains/{domain.strip()}/question_guides/{version.strip()}.md"


def contract_draft_key(site_id: str, version: int) -> str:
    _require_text("site_id", site_id)
    _require_positive_version(version, "contract_version")
    return f"sites/{site_id.strip()}/contracts/draft/{version:06d}.json"


def contract_final_key(site_id: str) -> str:
    _require_text("site_id", site_id)
    return f"sites/{site_id.strip()}/contract_final.json"


def guardrail_report_key(session_id: str, target: str, timestamp: str) -> str:
    _require_text("session_id", session_id)
    _require_text("target", target)
    _require_text("timestamp", timestamp)
    return f"sessions/{session_id.strip()}/guardrails/{target.strip()}/{timestamp.strip()}.json"


def _serialize_body(body: str | dict[str, Any] | list[Any]) -> str:
    if isinstance(body, str):
        if not body.strip():
            raise ValueError("artifact_body_empty")
        return body
    if isinstance(body, (dict, list)):
        if not body:
            raise ValueError("artifact_body_empty")
        return json.dumps(body, ensure_ascii=False, sort_keys=True)
    raise ValueError("artifact_body_type_invalid")


def _require_text(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name}_missing")


def _require_positive_version(version: int, field_name: str) -> None:
    if version <= 0:
        raise ValueError(f"{field_name}_must_be_positive")
