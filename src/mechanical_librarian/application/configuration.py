"""Versioned, secret-free configuration projection and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from mechanical_librarian.domain.identifiers import SchemaVersion
from mechanical_librarian.domain.inventory import SecretReference, ServiceEndpoint


class ConfigurationError(ValueError):
    """A safe validation error; it never includes configuration values."""


@dataclass(frozen=True)
class ConnectionConfiguration:
    adapter: str
    endpoint: ServiceEndpoint
    secret: SecretReference
    enabled_libraries: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.adapter or not self.enabled_libraries:
            raise ConfigurationError("connection requires an adapter and enabled libraries")


@dataclass(frozen=True)
class MediaMountConfiguration:
    name: str
    library_type: str
    container_path: str
    service_path: str
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.name or not self.library_type or not self.read_only:
            raise ConfigurationError("media mounts must be named, typed, and read-only")
        if not self.container_path.startswith("/") or not self.service_path.startswith("/"):
            raise ConfigurationError("media mount paths must be absolute container/service paths")


@dataclass(frozen=True)
class ResourceLimits:
    parser_concurrency: int = 1
    parser_timeout_seconds: int = 60
    temporary_bytes: int = 1_073_741_824

    def __post_init__(self) -> None:
        if not 1 <= self.parser_concurrency <= 8:
            raise ConfigurationError("parser concurrency must be between 1 and 8")
        if not 1 <= self.parser_timeout_seconds <= 3_600:
            raise ConfigurationError("parser timeout must be between 1 and 3600 seconds")
        if not 1_048_576 <= self.temporary_bytes <= 17_179_869_184:
            raise ConfigurationError("temporary storage limit is outside safe bounds")


@dataclass(frozen=True)
class ApplicationConfiguration:
    schema_version: SchemaVersion
    instance_name: str
    connections: tuple[ConnectionConfiguration, ...]
    media_mounts: tuple[MediaMountConfiguration, ...]
    limits: ResourceLimits
    providers: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    notifications: tuple[str, ...] = ()
    backups_enabled: bool = True
    destructive_actions_enabled: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SchemaVersion("config-v1"):
            raise ConfigurationError("unsupported configuration schema version")
        if not self.instance_name or len(self.instance_name) > 64:
            raise ConfigurationError("instance name must be 1-64 characters")
        if not self.connections:
            raise ConfigurationError("at least one library connection is required")
        if self.destructive_actions_enabled:
            raise ConfigurationError("destructive actions are not configurable in this release")

    def export(self) -> dict[str, object]:
        """Return a deterministic diagnostic/export view without secrets or paths."""

        return {
            "schema_version": self.schema_version.value,
            "instance_name": self.instance_name,
            "connections": [
                {
                    "adapter": connection.adapter,
                    "secret_configured": True,
                    "enabled_libraries": list(connection.enabled_libraries),
                }
                for connection in self.connections
            ],
            "media_mounts": [
                {"name": mount.name, "library_type": mount.library_type, "read_only": mount.read_only}
                for mount in self.media_mounts
            ],
            "limits": {"parser_concurrency": self.limits.parser_concurrency, "parser_timeout_seconds": self.limits.parser_timeout_seconds, "temporary_bytes": self.limits.temporary_bytes},
            "providers": list(self.providers),
            "reviewers": list(self.reviewers),
            "notifications": list(self.notifications),
            "backups_enabled": self.backups_enabled,
            "destructive_actions_enabled": False,
        }


def load_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Validate an untrusted mapping; file/env/CLI transports call this function."""

    _only(source, {"schema_version", "instance_name", "connections", "media_mounts", "limits", "providers", "reviewers", "notifications", "backups_enabled", "destructive_actions_enabled"}, "configuration")
    try:
        connections = tuple(_connection(value) for value in source["connections"])
        mounts = tuple(_mount(value) for value in source.get("media_mounts", ()))
        limits = ResourceLimits(**source.get("limits", {}))
        return ApplicationConfiguration(
            schema_version=SchemaVersion(source["schema_version"]), instance_name=source["instance_name"],
            connections=connections, media_mounts=mounts, limits=limits,
            providers=tuple(source.get("providers", ())), reviewers=tuple(source.get("reviewers", ())),
            notifications=tuple(source.get("notifications", ())), backups_enabled=source.get("backups_enabled", True),
            destructive_actions_enabled=source.get("destructive_actions_enabled", False),
        )
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError("configuration is malformed") from error


def _connection(value: Mapping[str, Any]) -> ConnectionConfiguration:
    _only(value, {"adapter", "endpoint", "secret_reference", "enabled_libraries"}, "connection")
    return ConnectionConfiguration(value["adapter"], ServiceEndpoint(value["endpoint"]), SecretReference(value["secret_reference"]), tuple(value["enabled_libraries"]))


def _mount(value: Mapping[str, Any]) -> MediaMountConfiguration:
    _only(value, {"name", "library_type", "container_path", "service_path", "read_only"}, "media mount")
    return MediaMountConfiguration(**value)


def _only(value: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigurationError(f"unknown {location} field")
