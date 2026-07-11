"""Versioned, secret-free configuration projection and validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from mechanical_librarian.domain.identifiers import SchemaVersion
from mechanical_librarian.domain.inventory import SecretReference, ServiceEndpoint


class ConfigurationError(ValueError):
    """A safe validation error; it never includes configuration values."""


class ChangeImpact(StrEnum):
    """Closed, public-safe effects that a configuration change can require."""

    RESTART_REQUIRED = "restart_required"
    INVENTORY_RECONCILIATION_REQUIRED = "inventory_reconciliation_required"
    EVIDENCE_INVALIDATION_REQUIRED = "evidence_invalidation_required"
    EVIDENCE_REEVALUATION_REQUIRED = "evidence_reevaluation_required"
    CANDIDATE_REEVALUATION_REQUIRED = "candidate_reevaluation_required"
    REVIEW_REEVALUATION_REQUIRED = "review_reevaluation_required"
    NOTIFICATION_RECONFIGURATION_REQUIRED = "notification_reconfiguration_required"
    BACKUP_READINESS_REVALIDATION_REQUIRED = "backup_readiness_revalidation_required"


@dataclass(frozen=True)
class ConfigurationChange:
    """A changed public schema path and its deterministic impacts."""

    path: str
    impacts: tuple[ChangeImpact, ...]


@dataclass(frozen=True)
class ConfigurationImportPlan:
    """A value-free dry-run plan; it retains no source configuration."""

    changes: tuple[ConfigurationChange, ...]

    @property
    def changed_sections(self) -> tuple[str, ...]:
        sections: list[str] = []
        for change in self.changes:
            section = change.path.split(".", 1)[0]
            if section not in sections:
                sections.append(section)
        return tuple(sections)

    @property
    def impacts(self) -> tuple[ChangeImpact, ...]:
        present = {impact for change in self.changes for impact in change.impacts}
        return tuple(impact for impact in ChangeImpact if impact in present)

    @property
    def requires_restart(self) -> bool:
        return ChangeImpact.RESTART_REQUIRED in self.impacts

    @property
    def reextract_evidence(self) -> bool:
        return ChangeImpact.EVIDENCE_INVALIDATION_REQUIRED in self.impacts

    @property
    def requires_inventory_reconciliation(self) -> bool:
        return ChangeImpact.INVENTORY_RECONCILIATION_REQUIRED in self.impacts

    @property
    def reevaluates_evidence(self) -> bool:
        return ChangeImpact.EVIDENCE_REEVALUATION_REQUIRED in self.impacts

    @property
    def is_noop(self) -> bool:
        return not self.changes


@dataclass(frozen=True, repr=False)
class ConnectionConfiguration:
    adapter: str
    endpoint: ServiceEndpoint
    secret: SecretReference
    enabled_libraries: tuple[str, ...]
    capability_overrides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.adapter, "connection adapter", 64)
        if type(self.endpoint) is not ServiceEndpoint or type(self.secret) is not SecretReference:
            raise ConfigurationError("connection endpoint or secret reference is malformed")
        if (
            type(self.enabled_libraries) is not tuple
            or not self.enabled_libraries
            or self.enabled_libraries != tuple(sorted(self.enabled_libraries))
        ):
            raise ConfigurationError("enabled libraries must be a canonical non-empty tuple")
        if _text_tuple(self.enabled_libraries, "enabled libraries", required=True) != self.enabled_libraries:
            raise ConfigurationError("enabled libraries are malformed")
        if type(self.capability_overrides) is not tuple:
            raise ConfigurationError("capability overrides must be a canonical tuple")
        if _capability_overrides(self.capability_overrides) != self.capability_overrides:
            raise ConfigurationError("capability overrides must be a canonical tuple")

    def __repr__(self) -> str:
        return "ConnectionConfiguration(<redacted>)"


@dataclass(frozen=True, repr=False)
class MediaMountConfiguration:
    name: str
    library_type: str
    container_path: str
    service_path: str
    read_only: bool = True

    def __post_init__(self) -> None:
        _text(self.name, "media mount name", 64)
        _text(self.library_type, "media mount library type", 64)
        _text(self.container_path, "media mount container path", 1024)
        _text(self.service_path, "media mount service path", 1024)
        if type(self.read_only) is not bool or not self.read_only:
            raise ConfigurationError("media mounts must be read-only")
        if not self.container_path.startswith("/") or not self.service_path.startswith("/"):
            raise ConfigurationError("media mount paths must be absolute container/service paths")

    def __repr__(self) -> str:
        return "MediaMountConfiguration(<redacted>)"


@dataclass(frozen=True)
class ResourceLimits:
    parser_concurrency: int = 1
    parser_timeout_seconds: int = 60
    temporary_bytes: int = 1_073_741_824

    def __post_init__(self) -> None:
        if type(self.parser_concurrency) is not int or not 1 <= self.parser_concurrency <= 8:
            raise ConfigurationError("parser concurrency must be between 1 and 8")
        if (
            type(self.parser_timeout_seconds) is not int
            or not 1 <= self.parser_timeout_seconds <= 3_600
        ):
            raise ConfigurationError("parser timeout must be between 1 and 3600 seconds")
        if (
            type(self.temporary_bytes) is not int
            or not 1_048_576 <= self.temporary_bytes <= 17_179_869_184
        ):
            raise ConfigurationError("temporary storage limit is outside safe bounds")


@dataclass(frozen=True)
class PolicyConfiguration:
    policy_version: str = "policy-v1"

    def __post_init__(self) -> None:
        if (
            type(self.policy_version) is not str
            or not self.policy_version
            or self.policy_version.strip() != self.policy_version
            or len(self.policy_version) > 64
            or any(
                ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
                for character in self.policy_version
            )
        ):
            raise ConfigurationError("policy version is malformed")


@dataclass(frozen=True, repr=False)
class ApplicationConfiguration:
    schema_version: SchemaVersion
    instance_name: str
    connections: tuple[ConnectionConfiguration, ...]
    media_mounts: tuple[MediaMountConfiguration, ...]
    limits: ResourceLimits
    providers: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    policy: PolicyConfiguration = PolicyConfiguration()
    notifications: tuple[str, ...] = ()
    backups_enabled: bool = True
    destructive_actions_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not SchemaVersion or self.schema_version != SchemaVersion(
            "config-v1"
        ):
            raise ConfigurationError("unsupported configuration schema version")
        _text(self.instance_name, "instance name", 64)
        if (
            type(self.connections) is not tuple
            or not self.connections
            or any(type(value) is not ConnectionConfiguration for value in self.connections)
        ):
            raise ConfigurationError("configuration requires typed connections")
        if type(self.media_mounts) is not tuple or any(
            type(value) is not MediaMountConfiguration for value in self.media_mounts
        ):
            raise ConfigurationError("configuration requires typed media mounts")
        if type(self.limits) is not ResourceLimits or type(self.policy) is not PolicyConfiguration:
            raise ConfigurationError("configuration limits or policy are malformed")
        for values, label in (
            (self.providers, "providers"),
            (self.reviewers, "reviewers"),
            (self.notifications, "notifications"),
        ):
            if type(values) is not tuple or _text_tuple(values, label) != values:
                raise ConfigurationError(f"{label} must be an immutable typed tuple")
        if type(self.backups_enabled) is not bool:
            raise ConfigurationError("backups enabled must be a boolean")
        if type(self.destructive_actions_enabled) is not bool or self.destructive_actions_enabled:
            raise ConfigurationError("destructive actions are not configurable in this release")

    def __repr__(self) -> str:
        return "ApplicationConfiguration(<redacted>)"

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
                    "capability_overrides": list(connection.capability_overrides),
                }
                for connection in self.connections
            ],
            "media_mounts": [
                {
                    "name": mount.name,
                    "library_type": mount.library_type,
                    "read_only": mount.read_only,
                }
                for mount in self.media_mounts
            ],
            "limits": {
                "parser_concurrency": self.limits.parser_concurrency,
                "parser_timeout_seconds": self.limits.parser_timeout_seconds,
                "temporary_bytes": self.limits.temporary_bytes,
            },
            "providers": list(self.providers),
            "reviewers": list(self.reviewers),
            "policy": {"policy_version": self.policy.policy_version},
            "notifications": list(self.notifications),
            "backups_enabled": self.backups_enabled,
            "destructive_actions_enabled": False,
        }


_CONFIGURATION_FIELDS = frozenset({
    "schema_version",
    "instance_name",
    "connections",
    "media_mounts",
    "limits",
    "providers",
    "reviewers",
    "policy",
    "notifications",
    "backups_enabled",
    "destructive_actions_enabled",
})
_CONNECTION_FIELDS = frozenset({
    "adapter",
    "endpoint",
    "secret_reference",
    "enabled_libraries",
    "capability_overrides",
})
_MOUNT_FIELDS = frozenset(
    {"name", "library_type", "container_path", "service_path", "read_only"}
)
_LIMIT_FIELDS = frozenset(
    {"parser_concurrency", "parser_timeout_seconds", "temporary_bytes"}
)
_POLICY_FIELDS = frozenset({"policy_version"})
_READ_ONLY_CAPABILITY_OVERRIDES = frozenset({
    "inventory-read",
    "metadata-read",
    "media-file-read",
    "notifications",
    "pagination",
    "concurrency-tokens",
    "cover-read",
})


def load_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Validate an untrusted mapping; every transport calls this function."""

    try:
        _only(source, _CONFIGURATION_FIELDS, "configuration")
        schema_version = SchemaVersion(_required_text(source, "schema_version", 32))
        if schema_version != SchemaVersion("config-v1"):
            raise ConfigurationError("unsupported configuration schema version")
        instance_name = _required_text(source, "instance_name", 64)
        connections = tuple(
            _connection(value)
            for value in _sequence(source.get("connections"), "connections", required=True)
        )
        mounts = tuple(
            _mount(value) for value in _sequence(source.get("media_mounts", ()), "media mounts")
        )
        limits_source = source.get("limits", {})
        _only(limits_source, _LIMIT_FIELDS, "limits")
        limits = ResourceLimits(**limits_source)
        policy_source = source.get("policy", {})
        _only(policy_source, _POLICY_FIELDS, "policy")
        policy = PolicyConfiguration(
            policy_version=_text(policy_source.get("policy_version", "policy-v1"), "policy version", 64)
        )
        backups_enabled = _boolean(source.get("backups_enabled", True), "backups enabled")
        destructive = _boolean(
            source.get("destructive_actions_enabled", False), "destructive actions enabled"
        )
        if destructive:
            raise ConfigurationError("destructive actions are not configurable in this release")
        return ApplicationConfiguration(
            schema_version=schema_version,
            instance_name=instance_name,
            connections=connections,
            media_mounts=mounts,
            limits=limits,
            providers=_text_tuple(source.get("providers", ()), "providers"),
            reviewers=_text_tuple(source.get("reviewers", ()), "reviewers"),
            policy=policy,
            notifications=_text_tuple(source.get("notifications", ()), "notifications"),
            backups_enabled=backups_enabled,
            destructive_actions_enabled=False,
        )
    except ConfigurationError:
        raise
    except (KeyError, TypeError, ValueError):
        raise ConfigurationError("configuration is malformed") from None


_R = ChangeImpact.RESTART_REQUIRED
_I = ChangeImpact.INVENTORY_RECONCILIATION_REQUIRED
_X = ChangeImpact.EVIDENCE_INVALIDATION_REQUIRED
_E = ChangeImpact.EVIDENCE_REEVALUATION_REQUIRED
_C = ChangeImpact.CANDIDATE_REEVALUATION_REQUIRED
_V = ChangeImpact.REVIEW_REEVALUATION_REQUIRED
_N = ChangeImpact.NOTIFICATION_RECONFIGURATION_REQUIRED
_B = ChangeImpact.BACKUP_READINESS_REVALIDATION_REQUIRED


def _projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration, attribute)


def _connection_projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: tuple(
        getattr(connection, attribute) for connection in configuration.connections
    )


def _mount_projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: tuple(
        getattr(mount, attribute) for mount in configuration.media_mounts
    )


def _limit_projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration.limits, attribute)


_IMPACT_MATRIX: tuple[
    tuple[str, Callable[[ApplicationConfiguration], object], tuple[ChangeImpact, ...]], ...
] = (
    ("instance.instance_name", _projection("instance_name"), (_R,)),
    ("connections.adapter", _connection_projection("adapter"), (_R, _I, _X, _C, _V)),
    ("connections.endpoint", _connection_projection("endpoint"), (_R, _I, _X, _C, _V)),
    (
        "connections.secret_reference",
        _connection_projection("secret"),
        (_R, _I, _X, _C, _V),
    ),
    (
        "connections.enabled_libraries",
        _connection_projection("enabled_libraries"),
        (_R, _I, _X, _C, _V),
    ),
    (
        "connections.capability_overrides",
        _connection_projection("capability_overrides"),
        (_R, _I, _E, _C, _V),
    ),
    ("media_mounts.name", _mount_projection("name"), (_R, _I, _X, _C, _V)),
    (
        "media_mounts.library_type",
        _mount_projection("library_type"),
        (_R, _I, _X, _C, _V),
    ),
    (
        "media_mounts.container_path",
        _mount_projection("container_path"),
        (_R, _I, _X, _C, _V),
    ),
    (
        "media_mounts.service_path",
        _mount_projection("service_path"),
        (_R, _I, _X, _C, _V),
    ),
    ("limits.parser_concurrency", _limit_projection("parser_concurrency"), (_R,)),
    (
        "limits.parser_timeout_seconds",
        _limit_projection("parser_timeout_seconds"),
        (_R, _E),
    ),
    ("limits.temporary_bytes", _limit_projection("temporary_bytes"), (_R, _E)),
    ("providers", _projection("providers"), (_R, _C, _V)),
    ("reviewers", _projection("reviewers"), (_R, _V)),
    ("policy.policy_version", lambda configuration: configuration.policy.policy_version, (_R, _V)),
    ("notifications", _projection("notifications"), (_R, _N)),
    ("backups_enabled", _projection("backups_enabled"), (_R, _B, _V)),
)


def plan_import(
    current_source: Mapping[str, Any], proposed_source: Mapping[str, Any]
) -> ConfigurationImportPlan:
    """Validate both sources and compare them without persisting or applying."""

    current = load_configuration(current_source)
    proposed = load_configuration(proposed_source)
    changes = tuple(
        ConfigurationChange(path, impacts)
        for path, project, impacts in _IMPACT_MATRIX
        if project(current) != project(proposed)
    )
    return ConfigurationImportPlan(changes)


def _connection(value: object) -> ConnectionConfiguration:
    _only(value, _CONNECTION_FIELDS, "connection")
    return ConnectionConfiguration(
        adapter=_required_text(value, "adapter", 64),
        endpoint=ServiceEndpoint(_required_text(value, "endpoint", 256)),
        secret=SecretReference(_required_text(value, "secret_reference", 256)),
        enabled_libraries=_set_text_tuple(
            value.get("enabled_libraries"), "enabled libraries", required=True
        ),
        capability_overrides=_capability_overrides(value.get("capability_overrides", ())),
    )


def _mount(value: object) -> MediaMountConfiguration:
    _only(value, _MOUNT_FIELDS, "media mount")
    name = _required_text(value, "name", 64)
    library_type = _required_text(value, "library_type", 64)
    container_path = _required_text(value, "container_path", 1024)
    service_path = _required_text(value, "service_path", 1024)
    read_only = _boolean(value.get("read_only", True), "media mount read only")
    if not read_only:
        raise ConfigurationError("media mounts must be read-only")
    if not container_path.startswith("/") or not service_path.startswith("/"):
        raise ConfigurationError("media mount paths must be absolute container/service paths")
    return MediaMountConfiguration(name, library_type, container_path, service_path, True)


def _only(value: object, allowed: frozenset[str], location: str) -> None:
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{location} must be a mapping")
    if any(type(key) is not str for key in value):
        raise ConfigurationError(f"unknown {location} field")
    if set(value) - allowed:
        raise ConfigurationError(f"unknown {location} field")


def _required_text(source: Mapping[str, Any], key: str, maximum: int) -> str:
    if key not in source:
        raise ConfigurationError("configuration is malformed")
    return _text(source[key], key.replace("_", " "), maximum)


def _text(value: object, label: str, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in value)
    ):
        raise ConfigurationError(f"{label} is malformed")
    return value


def _sequence(value: object, label: str, *, required: bool = False) -> Sequence[object]:
    if type(value) not in {list, tuple} or (required and not value):
        raise ConfigurationError(f"{label} must be a bounded sequence")
    if len(value) > 64:
        raise ConfigurationError(f"{label} exceeds its size limit")
    return value


def _text_tuple(value: object, label: str, *, required: bool = False) -> tuple[str, ...]:
    values = tuple(_text(item, label) for item in _sequence(value, label, required=required))
    if len(set(values)) != len(values):
        raise ConfigurationError(f"{label} must not contain duplicates")
    return values


def _set_text_tuple(value: object, label: str, *, required: bool = False) -> tuple[str, ...]:
    return tuple(sorted(_text_tuple(value, label, required=required)))


def _capability_overrides(value: object) -> tuple[str, ...]:
    overrides = _set_text_tuple(value, "capability overrides")
    if any(override not in _READ_ONLY_CAPABILITY_OVERRIDES for override in overrides):
        raise ConfigurationError("capability override is unsupported")
    return overrides


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ConfigurationError(f"{label} must be a boolean")
    return value
