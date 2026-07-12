"""Strict, value-free config-v1 transport boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    load_configuration,
)


_MAX_CONFIGURATION_BYTES = 1_048_576
_MODEL_CONFIG = ConfigDict(
    extra="forbid",
    frozen=True,
    strict=True,
    hide_input_in_errors=True,
)


class ConnectionInput(BaseModel):
    model_config = _MODEL_CONFIG

    id: StrictStr | None = None
    adapter: StrictStr
    endpoint: StrictStr
    secret_reference: StrictStr
    enabled_libraries: list[StrictStr]
    capability_overrides: list[StrictStr] = []


class MediaMountInput(BaseModel):
    model_config = _MODEL_CONFIG

    name: StrictStr
    library_type: StrictStr
    container_path: StrictStr
    service_path: StrictStr
    read_only: StrictBool = True


class ResourceLimitsInput(BaseModel):
    model_config = _MODEL_CONFIG

    parser_concurrency: StrictInt = 1
    parser_timeout_seconds: StrictInt = 60
    temporary_bytes: StrictInt = 1_073_741_824


class EvidenceInput(BaseModel):
    model_config = _MODEL_CONFIG

    enabled_formats: list[StrictStr] = ["epub", "m4b", "mp3"]
    maximum_document_bytes: StrictInt = 67_108_864
    maximum_text_characters: StrictInt = 262_144


class PolicyInput(BaseModel):
    model_config = _MODEL_CONFIG

    policy_version: StrictStr = "policy-v1"


class RetentionInput(BaseModel):
    model_config = _MODEL_CONFIG

    evidence_days: StrictInt = 90
    job_log_days: StrictInt = 30
    maximum_artifact_bytes: StrictInt = 1_073_741_824


class ConfigurationInput(BaseModel):
    """The single strict transport schema used by every supported config source."""

    model_config = _MODEL_CONFIG

    schema_version: StrictStr
    instance_name: StrictStr
    connections: list[ConnectionInput]
    media_mounts: list[MediaMountInput] = []
    limits: ResourceLimitsInput = ResourceLimitsInput()
    evidence: EvidenceInput = EvidenceInput()
    providers: list[StrictStr] = []
    reviewers: list[StrictStr] = []
    policy: PolicyInput = PolicyInput()
    notifications: list[StrictStr] = []
    backups_enabled: StrictBool = True
    retention: RetentionInput = RetentionInput()
    destructive_actions_enabled: StrictBool = False


def validate_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Validate untrusted transport data and project immutable application values."""

    try:
        validated = ConfigurationInput.model_validate(source)
        return load_configuration(validated.model_dump(mode="python"))
    except ConfigurationError:
        raise
    except (TypeError, ValueError, ValidationError):
        raise ConfigurationError("configuration is invalid") from None


def load_configuration_file(path: Path) -> ApplicationConfiguration:
    """Load one bounded UTF-8 JSON document without exposing its path or values."""

    try:
        payload = path.read_bytes()
        if not payload or len(payload) > _MAX_CONFIGURATION_BYTES:
            raise ConfigurationError("configuration document is invalid")
        source = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
        if not isinstance(source, dict):
            raise ConfigurationError("configuration document is invalid")
        return validate_configuration(source)
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


def configuration_schema_json() -> str:
    """Return the deterministic generated config-v1 JSON Schema artifact."""

    return json.dumps(
        ConfigurationInput.model_json_schema(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("configuration document contains a duplicate field")
        result[key] = value
    return result


__all__ = [
    "ConfigurationInput",
    "configuration_schema_json",
    "load_configuration_file",
    "validate_configuration",
]
