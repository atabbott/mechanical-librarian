"""Secret-safe, explicit configuration values for the 0.4 report command."""

from __future__ import annotations

import re
from dataclasses import dataclass


_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ConfigurationError(ValueError):
    """A fixed, value-free configuration failure."""


def _name(value: object, label: str) -> None:
    if type(value) is not str or _NAME.fullmatch(value) is None:
        raise ConfigurationError(f"{label} is invalid")


def _absolute_path(value: object, label: str) -> None:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 1024
        or not value.startswith("/")
    ):
        raise ConfigurationError(f"{label} is invalid")


@dataclass(frozen=True, repr=False, slots=True)
class TargetConnectionConfiguration:
    name: str
    endpoint: str
    secret_file: str
    adapter: str = "audiobookshelf"

    def __post_init__(self) -> None:
        _name(self.name, "target connection name")
        if self.adapter != "audiobookshelf" or type(self.endpoint) is not str:
            raise ConfigurationError("target connection is invalid")
        _absolute_path(self.secret_file, "target secret file")

    def __repr__(self) -> str:
        return "TargetConnectionConfiguration(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class ProviderConnectionConfiguration:
    name: str
    enabled: bool
    cover_enabled: bool
    secret_file: str | None
    adapter: str = "hardcover"

    def __post_init__(self) -> None:
        _name(self.name, "provider connection name")
        if self.adapter != "hardcover" or type(self.enabled) is not bool or type(self.cover_enabled) is not bool:
            raise ConfigurationError("provider connection is invalid")
        if self.enabled != (self.secret_file is not None):
            raise ConfigurationError("provider enablement and secret configuration disagree")
        if self.cover_enabled and not self.enabled:
            raise ConfigurationError("cover enablement requires provider enablement")
        if self.secret_file is not None:
            _absolute_path(self.secret_file, "provider secret file")

    def __repr__(self) -> str:
        return "ProviderConnectionConfiguration(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class MediaMountConfiguration:
    name: str
    container_path: str
    service_path: str
    read_only: bool

    def __post_init__(self) -> None:
        _name(self.name, "media mount name")
        if type(self.container_path) is not str or not self.container_path.startswith("/") or type(self.service_path) is not str or not self.service_path.startswith("/") or self.read_only is not True:
            raise ConfigurationError("media mount is invalid")

    def __repr__(self) -> str:
        return "MediaMountConfiguration(<redacted>)"


@dataclass(frozen=True, slots=True)
class LibraryConfiguration:
    name: str
    connection: str
    library_id: str
    media_kind: str
    mount: str | None
    provider_connections: tuple[str, ...]

    def __post_init__(self) -> None:
        _name(self.name, "library name")
        _name(self.connection, "library connection")
        if type(self.library_id) is not str or not 1 <= len(self.library_id) <= 128 or self.media_kind not in {"ebook", "audiobook"}:
            raise ConfigurationError("library is invalid")
        if self.mount is not None:
            _name(self.mount, "library mount")
        if type(self.provider_connections) is not tuple or any(type(value) is not str for value in self.provider_connections) or len(set(self.provider_connections)) != len(self.provider_connections):
            raise ConfigurationError("library provider references are duplicated or invalid")
        for value in self.provider_connections:
            _name(value, "library provider reference")


@dataclass(frozen=True, slots=True)
class ReportLimits:
    target_response_bytes: int = 1_048_576
    provider_response_bytes: int = 65_536
    source_archive_bytes: int = 268_435_456
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if (
            type(self.target_response_bytes) is not int or not 1_024 <= self.target_response_bytes <= 4_194_304
            or type(self.provider_response_bytes) is not int or not 1_024 <= self.provider_response_bytes <= 262_144
            or type(self.source_archive_bytes) is not int or not 1_024 <= self.source_archive_bytes <= 268_435_456
            or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60
        ):
            raise ConfigurationError("report limits are invalid")


@dataclass(frozen=True, repr=False, slots=True)
class ApplicationConfiguration:
    connections: tuple[TargetConnectionConfiguration, ...]
    libraries: tuple[LibraryConfiguration, ...]
    mounts: tuple[MediaMountConfiguration, ...]
    providers: tuple[ProviderConnectionConfiguration, ...]
    limits: ReportLimits = ReportLimits()
    schema_version: str = "config-v2"

    def __post_init__(self) -> None:
        if self.schema_version != "config-v2":
            raise ConfigurationError("configuration schema is unsupported")
        typed = (
            (self.connections, TargetConnectionConfiguration, "target connections"),
            (self.libraries, LibraryConfiguration, "libraries"),
            (self.mounts, MediaMountConfiguration, "media mounts"),
            (self.providers, ProviderConnectionConfiguration, "provider connections"),
        )
        for values, expected, label in typed:
            if type(values) is not tuple or any(type(value) is not expected for value in values):
                raise ConfigurationError(f"{label} are invalid")
            names = tuple(value.name for value in values)
            if names != tuple(sorted(set(names))):
                raise ConfigurationError(f"{label} are not in canonical unique order")
        connection_names = {value.name for value in self.connections}
        mount_names = {value.name for value in self.mounts}
        provider_names = {value.name for value in self.providers}
        providers_by_name = {value.name: value for value in self.providers}
        for library in self.libraries:
            if library.connection not in connection_names or (library.mount is not None and library.mount not in mount_names) or not set(library.provider_connections).issubset(provider_names):
                raise ConfigurationError("library references are invalid")
            if library.media_kind == "ebook" and library.mount is None:
                raise ConfigurationError("ebook library requires a read-only media mount")
            if sum(
                providers_by_name[name].cover_enabled
                for name in library.provider_connections
            ) > 1:
                raise ConfigurationError("library permits only one cover-enabled provider")

    def __repr__(self) -> str:
        return "ApplicationConfiguration(<redacted>)"

    def select(self, connection: str, library: str) -> tuple[TargetConnectionConfiguration, LibraryConfiguration, MediaMountConfiguration | None, tuple[ProviderConnectionConfiguration, ...]]:
        matches = tuple(value for value in self.libraries if value.name == library and value.connection == connection)
        if len(matches) != 1:
            raise ConfigurationError("requested connection or library is unavailable")
        selected = matches[0]
        target = next(value for value in self.connections if value.name == connection)
        mount = None if selected.mount is None else next(value for value in self.mounts if value.name == selected.mount)
        providers_by_name = {value.name: value for value in self.providers}
        providers = tuple(providers_by_name[name] for name in sorted(selected.provider_connections))
        return target, selected, mount, providers


__all__ = [
    "ApplicationConfiguration", "ConfigurationError", "LibraryConfiguration",
    "MediaMountConfiguration", "ProviderConnectionConfiguration", "ReportLimits",
    "TargetConnectionConfiguration",
]
