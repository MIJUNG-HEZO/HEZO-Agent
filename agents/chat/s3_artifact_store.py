"""S3 artifact storage adapters for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Literal, Protocol


ArtifactKind = Literal[
    "chat_transcript",
    "p2_markdown",
    "contract_draft",
    "contract_final",
    "guardrail_report",
]

CHAT_BUCKET = os.environ.get("HEZO_CHAT_BUCKET", "hezo-chat")
CHAT_TRANSCRIPTS_BUCKET = CHAT_BUCKET
P2_MARKDOWNS_BUCKET = os.environ.get("HEZO_P2_MARKDOWNS_BUCKET", "hezo-wiki")
CONTRACTS_BUCKET = os.environ.get("HEZO_CONTRACTS_BUCKET", "hezo-artifacts")
P2_MARKDOWN_CATEGORIES = ("landing", "blog", "store")


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

    def delete_artifact(self, ref: ArtifactRef) -> None:
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
                    str(kwargs.get("category", "")),
                    str(kwargs.get("domain", "")),
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

    def delete_artifact(self, ref: ArtifactRef) -> None:
        _require_text("bucket", ref.bucket)
        _require_text("key", ref.key)
        self._objects.pop((ref.bucket, ref.key), None)


class Boto3S3ArtifactStore:
    """AWS S3 implementation used by dev/integration smoke tests."""

    def __init__(self, client: Any | None = None, region_name: str | None = None) -> None:
        if client is not None:
            self._client = client
            return

        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("boto3_required_for_s3_artifact_store") from error

        session_kwargs: dict[str, str] = {}
        profile_name = os.environ.get("AWS_PROFILE")
        if profile_name:
            session_kwargs["profile_name"] = profile_name
        session = boto3.Session(**session_kwargs)
        self._client = session.client("s3", region_name=region_name or os.environ.get("AWS_REGION"))

    def build_artifact_ref(self, artifact_kind: ArtifactKind, **kwargs: Any) -> ArtifactRef:
        return InMemoryS3ArtifactStore().build_artifact_ref(artifact_kind, **kwargs)

    def put_artifact(self, payload: ArtifactPayload) -> ArtifactRef:
        if not payload.store_allowed:
            raise ValueError("artifact_store_blocked_by_guardrail")
        if payload.guardrail_action != "NONE":
            raise ValueError("artifact_guardrail_action_not_clear")

        body = _serialize_body(payload.body)
        self._client.put_object(
            Bucket=payload.ref.bucket,
            Key=payload.ref.key,
            Body=body.encode("utf-8"),
            ContentType=payload.ref.content_type,
            Metadata=dict(payload.ref.metadata),
        )
        return payload.ref

    def get_artifact(self, ref: ArtifactRef) -> str:
        _require_text("bucket", ref.bucket)
        _require_text("key", ref.key)
        try:
            response = self._client.get_object(Bucket=ref.bucket, Key=ref.key)
        except Exception as error:
            if _is_s3_not_found_error(error):
                raise ValueError("artifact_not_found") from error
            raise

        body = response["Body"].read()
        if isinstance(body, bytes):
            return body.decode("utf-8")
        return str(body)

    def delete_artifact(self, ref: ArtifactRef) -> None:
        _require_text("bucket", ref.bucket)
        _require_text("key", ref.key)
        self._client.delete_object(Bucket=ref.bucket, Key=ref.key)


def chat_transcript_key(session_id: str, version: int) -> str:
    _require_text("session_id", session_id)
    _require_positive_version(version, "transcript_version")
    return f"sessions/{session_id.strip()}/transcripts/{version:06d}.json"


def p2_markdown_key(category: str, domain: str) -> str:
    category = _require_p2_markdown_category(category)
    _require_text("domain", domain)
    return f"industries/{category}/{domain.strip()}.md"


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


def _require_p2_markdown_category(category: str) -> str:
    _require_text("category", category)
    normalized = category.strip()
    if normalized not in P2_MARKDOWN_CATEGORIES:
        raise ValueError("p2_markdown_category_invalid")
    return normalized


def _require_positive_version(version: int, field_name: str) -> None:
    if version <= 0:
        raise ValueError(f"{field_name}_must_be_positive")


def _is_s3_not_found_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    error_code = response.get("Error", {}).get("Code")
    return error_code in {"NoSuchKey", "404", "NotFound"}
