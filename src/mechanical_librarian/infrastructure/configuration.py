"""Strict bounded JSON configuration boundary for the report command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)


_MAX_CONFIG_BYTES = 262_144
Name = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
BoundedAbsolutePath = Annotated[
    StrictStr,
    Field(min_length=1, max_length=1024, pattern=r"^/"),
]
ContactEmail = Annotated[
    StrictStr,
    Field(
        min_length=3,
        max_length=254,
        pattern=(
            r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9]"
            r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
        ),
    ),
]


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConnectionInput(_Closed):
    name: Name
    adapter: Literal["audiobookshelf"]
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    secret_file: BoundedAbsolutePath


class ProviderInput(_Closed):
    name: Name
    adapter: Literal["hardcover", "open_library"]
    enabled: StrictBool
    cover_enabled: StrictBool
    secret_file: BoundedAbsolutePath | None
    contact_email: ContactEmail | None = None


class MountInput(_Closed):
    name: Name
    container_path: BoundedAbsolutePath
    service_path: BoundedAbsolutePath
    read_only: Literal[True]


class LibraryInput(_Closed):
    name: Name
    connection: Name
    library_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    mount: Name | None
    provider_connections: Annotated[list[Name], Field(max_length=32)]


class LimitsInput(_Closed):
    target_response_bytes: Annotated[StrictInt, Field(ge=1024, le=4_194_304)] = 1_048_576
    provider_response_bytes: Annotated[StrictInt, Field(ge=1024, le=262_144)] = 65_536
    source_archive_bytes: Annotated[StrictInt, Field(ge=1024, le=268_435_456)] = 268_435_456
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=60)] = 30


class ConfigurationInput(_Closed):
    schema_version: Literal["config-v4"]
    connections: Annotated[list[ConnectionInput], Field(min_length=1, max_length=32)]
    libraries: Annotated[list[LibraryInput], Field(min_length=1, max_length=128)]
    mounts: Annotated[list[MountInput], Field(max_length=32)]
    providers: Annotated[list[ProviderInput], Field(max_length=32)]
    limits: LimitsInput = LimitsInput()


def load_configuration_file(path: Path) -> ApplicationConfiguration:
    try:
        payload = _read_regular(path)
        source = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
        )
        value = ConfigurationInput.model_validate(source)
        return ApplicationConfiguration(
            tuple(sorted((TargetConnectionConfiguration(**item.model_dump()) for item in value.connections), key=lambda item: item.name)),
            tuple(sorted((LibraryConfiguration(**{**item.model_dump(), "media_kind": "book", "provider_connections": tuple(sorted(item.provider_connections))}) for item in value.libraries), key=lambda item: item.name)),
            tuple(sorted((MediaMountConfiguration(**item.model_dump()) for item in value.mounts), key=lambda item: item.name)),
            tuple(sorted((ProviderConnectionConfiguration(**item.model_dump()) for item in value.providers), key=lambda item: item.name)),
            ReportLimits(**value.limits.model_dump()),
            value.schema_version,
        )
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


def configuration_schema_json() -> str:
    return json.dumps(ConfigurationInput.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_regular(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or not 1 <= information.st_size <= _MAX_CONFIG_BYTES:
            raise ConfigurationError("configuration document is invalid")
        payload = os.read(descriptor, information.st_size + 1)
        if len(payload) != information.st_size:
            raise ConfigurationError("configuration document is invalid")
        return payload
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError("configuration document contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ConfigurationError("configuration document is invalid")


__all__ = ["ConfigurationInput", "configuration_schema_json", "load_configuration_file"]
