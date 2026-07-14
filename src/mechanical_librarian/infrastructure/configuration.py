"""Strict, bounded config-v1 source loading and transport validation."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    ConfigurationImportPlan,
    plan_projected_import,
    project_configuration,
)
from mechanical_librarian.domain.identifiers import (
    CANONICAL_UUID4_PATTERN,
    LIBRARY_ID_PATTERN,
    is_canonical_uuid4,
)


_MAX_CONFIGURATION_BYTES = 1_048_576
_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    hide_input_in_errors=True,
)
IdentifierText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
    ),
]
LibraryIdentifierText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=LIBRARY_ID_PATTERN,
    ),
]
VersionText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    ),
]
BoundedText64 = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=64)
]
EndpointText = Annotated[
    str,
    StringConstraints(strict=True, min_length=8, max_length=256, pattern=r"^https?://"),
]
SecretReferenceText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256)
]
AbsolutePathText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=1024, pattern=r"^/")
]
ReadCapability = Literal[
    "inventory-read",
    "metadata-read",
    "media-file-read",
    "notifications",
    "pagination",
    "concurrency-tokens",
    "cover-read",
]
EvidenceFormat = Literal["epub", "m4b", "mp3"]


class ConnectionInput(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"adapter": {"const": "audiobookshelf"}},
                        "required": ["adapter"],
                    },
                    "then": {
                        "properties": {
                            "enabled_libraries": {
                                "items": {"pattern": CANONICAL_UUID4_PATTERN}
                            }
                        }
                    },
                }
            ]
        },
    )

    id: IdentifierText | None = None
    adapter: IdentifierText
    endpoint: EndpointText
    secret_reference: SecretReferenceText
    enabled_libraries: Annotated[
        list[LibraryIdentifierText],
        Field(min_length=1, max_length=64, json_schema_extra={"uniqueItems": True}),
    ]
    capability_overrides: Annotated[
        list[ReadCapability],
        Field(max_length=7, json_schema_extra={"uniqueItems": True}),
    ] = []

    @model_validator(mode="after")
    def validate_adapter_library_ids(self) -> Self:
        if self.adapter == "audiobookshelf" and any(
            not is_canonical_uuid4(value) for value in self.enabled_libraries
        ):
            raise ValueError("Audiobookshelf library identifiers are invalid")
        return self


class MediaMountInput(BaseModel):
    model_config = _MODEL_CONFIG

    name: IdentifierText
    library_type: Literal["ebook", "audiobook"]
    container_path: AbsolutePathText
    service_path: AbsolutePathText
    read_only: Literal[True] = True


class ResourceLimitsInput(BaseModel):
    model_config = _MODEL_CONFIG

    parser_concurrency: Annotated[StrictInt, Field(ge=1, le=8)] = 1
    parser_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3_600)] = 60
    temporary_bytes: Annotated[StrictInt, Field(ge=1_048_576, le=17_179_869_184)] = (
        1_073_741_824
    )


class InventoryInput(BaseModel):
    model_config = _MODEL_CONFIG

    page_size: Annotated[StrictInt, Field(ge=1, le=500)] = 100
    max_pages: Annotated[StrictInt, Field(ge=2, le=10_000)] = 512
    max_items: Annotated[StrictInt, Field(ge=2, le=10_000)] = 10_000
    max_response_bytes: Annotated[StrictInt, Field(ge=1_024, le=67_108_864)] = (
        67_108_864
    )
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3_600)] = 300
    stability_delay_seconds: Annotated[StrictInt, Field(ge=0, le=86_400)] = 0
    poll_generation: Annotated[StrictInt, Field(ge=0, le=1_000_000)] = 0
    max_attempts: Annotated[StrictInt, Field(ge=1, le=16)] = 3
    max_elapsed_seconds: Annotated[StrictInt, Field(ge=1, le=86_400)] = 900
    initial_backoff_seconds: Annotated[StrictInt, Field(ge=1, le=3_600)] = 5
    max_backoff_seconds: Annotated[StrictInt, Field(ge=1, le=86_400)] = 300
    connect_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 10
    read_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3_600)] = 30
    max_in_flight_requests: Literal[1] = 1

    @model_validator(mode="after")
    def validate_poll_backoff(self) -> Self:
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("inventory poll backoff is invalid")
        return self


class EpubPackageInput(BaseModel):
    model_config = _MODEL_CONFIG

    profile: Literal["epub-package-limits-v1"] = "epub-package-limits-v1"
    source_archive_bytes: Annotated[StrictInt, Field(ge=1, le=536_870_912)] = 67_108_864
    archive_entries: Annotated[StrictInt, Field(ge=1, le=8_192)] = 2_048
    compressed_bytes_per_member: Annotated[
        StrictInt, Field(ge=1, le=536_870_912)
    ] = 67_108_864
    expanded_bytes_per_member: Annotated[
        StrictInt, Field(ge=1, le=536_870_912)
    ] = 67_108_864
    expanded_bytes_per_archive: Annotated[
        StrictInt, Field(ge=1, le=2_147_483_648)
    ] = 536_870_912
    compression_ratio_per_member: Annotated[StrictInt, Field(ge=1, le=1_000)] = 200
    container_xml_bytes: Annotated[StrictInt, Field(ge=1, le=1_048_576)] = 65_536
    opf_package_bytes: Annotated[StrictInt, Field(ge=1, le=16_777_216)] = 4_194_304
    xml_nodes_per_document: Annotated[StrictInt, Field(ge=1, le=200_000)] = 50_000
    xml_attributes_per_document: Annotated[
        StrictInt, Field(ge=1, le=400_000)
    ] = 100_000
    attributes_per_element: Annotated[StrictInt, Field(ge=1, le=256)] = 64
    xml_depth: Annotated[StrictInt, Field(ge=1, le=128)] = 64
    decoded_xml_text_characters: Annotated[
        StrictInt, Field(ge=1, le=4_194_304)
    ] = 262_144
    metadata_nodes: Annotated[StrictInt, Field(ge=1, le=2_048)] = 512
    manifest_items: Annotated[StrictInt, Field(ge=1, le=8_192)] = 4_096
    spine_items: Annotated[StrictInt, Field(ge=1, le=8_192)] = 4_096
    guide_references: Annotated[StrictInt, Field(ge=1, le=512)] = 128
    prefix_mappings: Annotated[StrictInt, Field(ge=1, le=256)] = 64


class EvidenceInput(BaseModel):
    model_config = _MODEL_CONFIG

    enabled_formats: Annotated[
        list[EvidenceFormat],
        Field(min_length=1, max_length=3, json_schema_extra={"uniqueItems": True}),
    ] = ["epub", "m4b", "mp3"]
    maximum_document_bytes: Annotated[
        StrictInt, Field(ge=1_048_576, le=536_870_912)
    ] = 67_108_864
    maximum_text_characters: Annotated[StrictInt, Field(ge=1_024, le=4_194_304)] = (
        262_144
    )
    epub_package: EpubPackageInput = EpubPackageInput()


class PolicyInput(BaseModel):
    model_config = _MODEL_CONFIG

    policy_version: VersionText = "policy-v1"


class RetentionInput(BaseModel):
    model_config = _MODEL_CONFIG

    evidence_days: Annotated[StrictInt, Field(ge=1, le=3_650)] = 90
    job_log_days: Annotated[StrictInt, Field(ge=1, le=365)] = 30
    maximum_artifact_bytes: Annotated[
        StrictInt, Field(ge=1_048_576, le=17_179_869_184)
    ] = 1_073_741_824


class ConfigurationInput(BaseModel):
    """The single strict transport schema used by every supported config source."""

    model_config = _MODEL_CONFIG

    schema_version: Literal["config-v1"]
    instance_name: BoundedText64
    connections: Annotated[list[ConnectionInput], Field(min_length=1, max_length=64)]
    media_mounts: Annotated[list[MediaMountInput], Field(max_length=64)] = []
    limits: ResourceLimitsInput = ResourceLimitsInput()
    inventory: InventoryInput = InventoryInput()
    evidence: EvidenceInput = EvidenceInput()
    providers: Annotated[
        list[IdentifierText],
        Field(max_length=64, json_schema_extra={"uniqueItems": True}),
    ] = []
    reviewers: Annotated[
        list[IdentifierText],
        Field(max_length=64, json_schema_extra={"uniqueItems": True}),
    ] = []
    policy: PolicyInput = PolicyInput()
    notifications: Annotated[
        list[IdentifierText],
        Field(max_length=64, json_schema_extra={"uniqueItems": True}),
    ] = []
    backups_enabled: StrictBool = True
    retention: RetentionInput = RetentionInput()
    destructive_actions_enabled: Literal[False] = False


def validate_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Validate untrusted transport data and project immutable application values."""

    try:
        validated = ConfigurationInput.model_validate(source)
        return project_configuration(validated.model_dump(mode="python"))
    except ConfigurationError:
        raise
    except (TypeError, ValueError, ValidationError):
        raise ConfigurationError("configuration is invalid") from None


def load_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Compatibility name for the single supported strict mapping boundary."""

    return validate_configuration(source)


def plan_import(
    current_source: Mapping[str, Any], proposed_source: Mapping[str, Any]
) -> ConfigurationImportPlan:
    """Strictly validate both sources through one path before comparing values."""

    return plan_projected_import(
        validate_configuration(current_source),
        validate_configuration(proposed_source),
    )


def load_configuration_file(path: Path) -> ApplicationConfiguration:
    """Load one bounded regular UTF-8 JSON file without exposing path or values."""

    try:
        payload = _read_bounded_regular_file(path)
        source = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(source, dict):
            raise ConfigurationError("configuration document is invalid")
        return validate_configuration(source)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


def configuration_schema_json() -> str:
    """Return the deterministic generated config-v1 JSON Schema artifact."""

    return (
        json.dumps(
            ConfigurationInput.model_json_schema(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _read_bounded_regular_file(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_size > _MAX_CONFIGURATION_BYTES
        ):
            raise ConfigurationError("configuration document is invalid")
        chunks: list[bytes] = []
        remaining = _MAX_CONFIGURATION_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if not payload or len(payload) > _MAX_CONFIGURATION_BYTES:
            raise ConfigurationError("configuration document is invalid")
        return payload
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(
                "configuration document contains a duplicate field"
            )
        result[key] = value
    return result


__all__ = [
    "ConfigurationInput",
    "configuration_schema_json",
    "load_configuration",
    "load_configuration_file",
    "plan_import",
    "validate_configuration",
]
