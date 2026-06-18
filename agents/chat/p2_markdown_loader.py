"""P2 markdown S3 loader for the HEZO chat agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from p2_markdown_parser import P2MarkdownParseInput
from s3_artifact_store import (
    ArtifactRef,
    P2_MARKDOWNS_BUCKET,
    S3ArtifactStore,
    p2_markdown_key,
)


DEFAULT_P2_MARKDOWN_VERSION = "v001"


@dataclass(frozen=True)
class P2MarkdownLoadInput:
    """Context required to load a P2 markdown artifact from S3."""

    domain: str
    expected_domain: str
    slot_registry: dict[str, dict[str, Any]]
    version: str | None = None
    source_s3_key: str | None = None
    source_count: int = 0
    source_grade: str = "unknown"
    bucket: str = P2_MARKDOWNS_BUCKET
    required_slots: tuple[str, ...] = ()


@dataclass(frozen=True)
class P2MarkdownLoadResult:
    """Loaded P2 markdown artifact normalized for the parser stage."""

    ref: ArtifactRef
    content: str
    parse_input: P2MarkdownParseInput

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "content": self.content,
            "parse_input": {
                "domain": self.parse_input.domain,
                "expected_domain": self.parse_input.expected_domain,
                "source_s3_key": self.parse_input.source_s3_key,
                "version": self.parse_input.version,
                "source_count": self.parse_input.source_count,
                "source_grade": self.parse_input.source_grade,
                "required_slots": list(self.parse_input.required_slots),
            },
        }


def load_p2_markdown_from_s3(
    load_input: P2MarkdownLoadInput,
    store: S3ArtifactStore,
) -> P2MarkdownLoadResult:
    """Load a P2 markdown object and convert it into parser input."""

    _validate_load_input(load_input)
    ref = _build_p2_markdown_ref(load_input)
    content = store.get_artifact(ref)
    if not content.strip():
        raise ValueError("p2_markdown_body_empty")

    return P2MarkdownLoadResult(
        ref=ref,
        content=content,
        parse_input=P2MarkdownParseInput(
            domain=load_input.domain.strip(),
            expected_domain=load_input.expected_domain.strip(),
            content=content,
            slot_registry=load_input.slot_registry,
            source_s3_key=ref.key,
            version=load_input.version or _version_from_key(ref.key),
            source_count=load_input.source_count,
            source_grade=load_input.source_grade,
            required_slots=load_input.required_slots,
        ),
    )


def build_p2_markdown_ref(load_input: P2MarkdownLoadInput) -> ArtifactRef:
    """Build the S3 ref used to load a P2 markdown artifact."""

    _validate_load_input(load_input)
    return _build_p2_markdown_ref(load_input)


def _validate_load_input(load_input: P2MarkdownLoadInput) -> None:
    required_strings = {
        "domain": load_input.domain,
        "expected_domain": load_input.expected_domain,
        "bucket": load_input.bucket,
    }
    missing = [
        field_name
        for field_name, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError("required_fields_missing:" + ",".join(missing))
    if not load_input.slot_registry:
        raise ValueError("slot_registry_empty")
    if not load_input.source_s3_key and load_input.version is not None and not load_input.version.strip():
        raise ValueError("version_missing")


def _build_p2_markdown_ref(load_input: P2MarkdownLoadInput) -> ArtifactRef:
    key = (
        load_input.source_s3_key.strip()
        if load_input.source_s3_key and load_input.source_s3_key.strip()
        else p2_markdown_key(
            load_input.domain,
            load_input.version or DEFAULT_P2_MARKDOWN_VERSION,
        )
    )
    return ArtifactRef(
        bucket=load_input.bucket.strip(),
        key=key,
        artifact_kind="p2_markdown",
        content_type="text/markdown; charset=utf-8",
    )


def _version_from_key(key: str) -> str | None:
    filename = key.rsplit("/", 1)[-1]
    if not filename.endswith(".md"):
        return None
    version = filename[:-3]
    return version or None
