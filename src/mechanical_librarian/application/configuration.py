"""Versioned, secret-free configuration projection and validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from mechanical_librarian.domain.identifiers import (
    ConnectionId,
    DomainValueError,
    PolicyVersion,
    SchemaVersion,
)
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

    def audit_event(self) -> "ConfigurationChangeEvent":
        """Project a value-free typed fact suitable for later audit persistence."""

        return ConfigurationChangeEvent(SchemaVersion("config-change-v1"), self.changes)


@dataclass(frozen=True)
class ConfigurationChangeEvent:
    schema_version: SchemaVersion
    changes: tuple[ConfigurationChange, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not SchemaVersion
            or self.schema_version != SchemaVersion("config-change-v1")
            or type(self.changes) is not tuple
            or any(type(change) is not ConfigurationChange for change in self.changes)
        ):
            raise ConfigurationError("configuration change event is malformed")


@dataclass(frozen=True, repr=False)
class ConnectionConfiguration:
    adapter: str
    endpoint: ServiceEndpoint
    secret: SecretReference
    enabled_libraries: tuple[str, ...]
    capability_overrides: tuple[str, ...] = ()
    connection_id: ConnectionId | None = None

    def __post_init__(self) -> None:
        _text(self.adapter, "connection adapter", 64)
        if (
            type(self.endpoint) is not ServiceEndpoint
            or type(self.secret) is not SecretReference
        ):
            raise ConfigurationError(
                "connection endpoint or secret reference is malformed"
            )
        if (
            type(self.enabled_libraries) is not tuple
            or not self.enabled_libraries
            or self.enabled_libraries != tuple(sorted(self.enabled_libraries))
        ):
            raise ConfigurationError(
                "enabled libraries must be a canonical non-empty tuple"
            )
        if (
            _text_tuple(self.enabled_libraries, "enabled libraries", required=True)
            != self.enabled_libraries
        ):
            raise ConfigurationError("enabled libraries are malformed")
        if type(self.capability_overrides) is not tuple:
            raise ConfigurationError("capability overrides must be a canonical tuple")
        if (
            _capability_overrides(self.capability_overrides)
            != self.capability_overrides
        ):
            raise ConfigurationError("capability overrides must be a canonical tuple")
        if (
            self.connection_id is not None
            and type(self.connection_id) is not ConnectionId
        ):
            raise ConfigurationError("connection ID is malformed")

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
        if not self.container_path.startswith("/") or not self.service_path.startswith(
            "/"
        ):
            raise ConfigurationError(
                "media mount paths must be absolute container/service paths"
            )

    def __repr__(self) -> str:
        return "MediaMountConfiguration(<redacted>)"


@dataclass(frozen=True)
class ResourceLimits:
    parser_concurrency: int = 1
    parser_timeout_seconds: int = 60
    temporary_bytes: int = 1_073_741_824

    def __post_init__(self) -> None:
        if (
            type(self.parser_concurrency) is not int
            or not 1 <= self.parser_concurrency <= 8
        ):
            raise ConfigurationError("parser concurrency must be between 1 and 8")
        if (
            type(self.parser_timeout_seconds) is not int
            or not 1 <= self.parser_timeout_seconds <= 3_600
        ):
            raise ConfigurationError(
                "parser timeout must be between 1 and 3600 seconds"
            )
        if (
            type(self.temporary_bytes) is not int
            or not 1_048_576 <= self.temporary_bytes <= 17_179_869_184
        ):
            raise ConfigurationError("temporary storage limit is outside safe bounds")


@dataclass(frozen=True)
class EvidenceConfiguration:
    enabled_formats: tuple[str, ...] = ("epub", "m4b", "mp3")
    maximum_document_bytes: int = 67_108_864
    maximum_text_characters: int = 262_144

    def __post_init__(self) -> None:
        if (
            type(self.enabled_formats) is not tuple
            or not self.enabled_formats
            or self.enabled_formats != tuple(sorted(self.enabled_formats))
            or _text_tuple(self.enabled_formats, "evidence formats", required=True)
            != self.enabled_formats
        ):
            raise ConfigurationError(
                "evidence formats must be a canonical non-empty tuple"
            )
        if not all(value in {"epub", "m4b", "mp3"} for value in self.enabled_formats):
            raise ConfigurationError("evidence format is unsupported")
        if (
            type(self.maximum_document_bytes) is not int
            or not 1_048_576 <= self.maximum_document_bytes <= 536_870_912
        ):
            raise ConfigurationError(
                "evidence document byte limit is outside safe bounds"
            )
        if (
            type(self.maximum_text_characters) is not int
            or not 1_024 <= self.maximum_text_characters <= 4_194_304
        ):
            raise ConfigurationError("evidence text limit is outside safe bounds")


@dataclass(frozen=True)
class RetentionConfiguration:
    evidence_days: int = 90
    job_log_days: int = 30
    maximum_artifact_bytes: int = 1_073_741_824

    def __post_init__(self) -> None:
        if type(self.evidence_days) is not int or not 1 <= self.evidence_days <= 3_650:
            raise ConfigurationError("evidence retention is outside safe bounds")
        if type(self.job_log_days) is not int or not 1 <= self.job_log_days <= 365:
            raise ConfigurationError("job-log retention is outside safe bounds")
        if (
            type(self.maximum_artifact_bytes) is not int
            or not 1_048_576 <= self.maximum_artifact_bytes <= 17_179_869_184
        ):
            raise ConfigurationError("artifact retention limit is outside safe bounds")


@dataclass(frozen=True)
class PolicyConfiguration:
    policy_version: str = "policy-v1"

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str:
            raise ConfigurationError("policy version is malformed")
        try:
            PolicyVersion(self.policy_version)
        except DomainValueError:
            raise ConfigurationError("policy version is malformed") from None


@dataclass(frozen=True)
class InventoryConfiguration:
    page_size: int = 100
    max_pages: int = 512
    max_items: int = 10_000
    max_response_bytes: int = 67_108_864
    timeout_seconds: int = 300
    stability_delay_seconds: int = 0

    def __post_init__(self) -> None:
        bounds = (
            (self.page_size, 1, 500),
            (self.max_pages, 2, 10_000),
            (self.max_items, 2, 10_000),
            (self.max_response_bytes, 1_024, 67_108_864),
            (self.timeout_seconds, 1, 3_600),
            (self.stability_delay_seconds, 0, 86_400),
        )
        if any(
            type(value) is not int or not minimum <= value <= maximum
            for value, minimum, maximum in bounds
        ):
            raise ConfigurationError("inventory bounds are invalid")


@dataclass(frozen=True, repr=False)
class ApplicationConfiguration:
    schema_version: SchemaVersion
    instance_name: str
    connections: tuple[ConnectionConfiguration, ...]
    media_mounts: tuple[MediaMountConfiguration, ...]
    limits: ResourceLimits
    inventory: InventoryConfiguration = field(default_factory=InventoryConfiguration)
    evidence: EvidenceConfiguration = field(default_factory=EvidenceConfiguration)
    providers: tuple[str, ...] = ()
    reviewers: tuple[str, ...] = ()
    policy: PolicyConfiguration = PolicyConfiguration()
    notifications: tuple[str, ...] = ()
    backups_enabled: bool = True
    retention: RetentionConfiguration = field(default_factory=RetentionConfiguration)
    destructive_actions_enabled: bool = False

    def __post_init__(self) -> None:
        if type(
            self.schema_version
        ) is not SchemaVersion or self.schema_version != SchemaVersion("config-v1"):
            raise ConfigurationError("unsupported configuration schema version")
        _text(self.instance_name, "instance name", 64)
        if (
            type(self.connections) is not tuple
            or not self.connections
            or any(
                type(value) is not ConnectionConfiguration for value in self.connections
            )
        ):
            raise ConfigurationError("configuration requires typed connections")
        if type(self.media_mounts) is not tuple or any(
            type(value) is not MediaMountConfiguration for value in self.media_mounts
        ):
            raise ConfigurationError("configuration requires typed media mounts")
        if (
            type(self.limits) is not ResourceLimits
            or type(self.inventory) is not InventoryConfiguration
            or type(self.evidence) is not EvidenceConfiguration
            or type(self.policy) is not PolicyConfiguration
            or type(self.retention) is not RetentionConfiguration
        ):
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
        if (
            type(self.destructive_actions_enabled) is not bool
            or self.destructive_actions_enabled
        ):
            raise ConfigurationError(
                "destructive actions are not configurable in this release"
            )

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
                    "id": (
                        None
                        if connection.connection_id is None
                        else connection.connection_id.value
                    ),
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
            "inventory": {
                "page_size": self.inventory.page_size,
                "max_pages": self.inventory.max_pages,
                "max_items": self.inventory.max_items,
                "max_response_bytes": self.inventory.max_response_bytes,
                "timeout_seconds": self.inventory.timeout_seconds,
                "stability_delay_seconds": self.inventory.stability_delay_seconds,
            },
            "evidence": {
                "enabled_formats": list(self.evidence.enabled_formats),
                "maximum_document_bytes": self.evidence.maximum_document_bytes,
                "maximum_text_characters": self.evidence.maximum_text_characters,
            },
            "providers": list(self.providers),
            "reviewers": list(self.reviewers),
            "policy": {"policy_version": self.policy.policy_version},
            "notifications": list(self.notifications),
            "backups_enabled": self.backups_enabled,
            "retention": {
                "evidence_days": self.retention.evidence_days,
                "job_log_days": self.retention.job_log_days,
                "maximum_artifact_bytes": self.retention.maximum_artifact_bytes,
            },
            "destructive_actions_enabled": False,
        }


_CONFIGURATION_FIELDS = frozenset(
    {
        "schema_version",
        "instance_name",
        "connections",
        "media_mounts",
        "limits",
        "inventory",
        "evidence",
        "providers",
        "reviewers",
        "policy",
        "notifications",
        "backups_enabled",
        "retention",
        "destructive_actions_enabled",
    }
)
_CONNECTION_FIELDS = frozenset(
    {
        "id",
        "adapter",
        "endpoint",
        "secret_reference",
        "enabled_libraries",
        "capability_overrides",
    }
)
_MOUNT_FIELDS = frozenset(
    {"name", "library_type", "container_path", "service_path", "read_only"}
)
_LIMIT_FIELDS = frozenset(
    {"parser_concurrency", "parser_timeout_seconds", "temporary_bytes"}
)
_INVENTORY_FIELDS = frozenset(
    {
        "page_size",
        "max_pages",
        "max_items",
        "max_response_bytes",
        "timeout_seconds",
        "stability_delay_seconds",
    }
)
_POLICY_FIELDS = frozenset({"policy_version"})
_EVIDENCE_FIELDS = frozenset(
    {"enabled_formats", "maximum_document_bytes", "maximum_text_characters"}
)
_RETENTION_FIELDS = frozenset(
    {"evidence_days", "job_log_days", "maximum_artifact_bytes"}
)
_READ_ONLY_CAPABILITY_OVERRIDES = frozenset(
    {
        "inventory-read",
        "metadata-read",
        "media-file-read",
        "notifications",
        "pagination",
        "concurrency-tokens",
        "cover-read",
    }
)


def project_configuration(source: Mapping[str, Any]) -> ApplicationConfiguration:
    """Project one already transport-validated mapping into immutable values."""

    try:
        _only(source, _CONFIGURATION_FIELDS, "configuration")
        schema_version = SchemaVersion(_required_text(source, "schema_version", 32))
        if schema_version != SchemaVersion("config-v1"):
            raise ConfigurationError("unsupported configuration schema version")
        instance_name = _required_text(source, "instance_name", 64)
        connections = tuple(
            _connection(value)
            for value in _sequence(
                source.get("connections"), "connections", required=True
            )
        )
        mounts = tuple(
            _mount(value)
            for value in _sequence(source.get("media_mounts", ()), "media mounts")
        )
        limits_source = source.get("limits", {})
        _only(limits_source, _LIMIT_FIELDS, "limits")
        limits = ResourceLimits(**limits_source)
        inventory_source = source.get("inventory", {})
        _only(inventory_source, _INVENTORY_FIELDS, "inventory")
        inventory = InventoryConfiguration(**inventory_source)
        evidence_source = source.get("evidence", {})
        _only(evidence_source, _EVIDENCE_FIELDS, "evidence")
        evidence = EvidenceConfiguration(
            enabled_formats=_set_text_tuple(
                evidence_source.get("enabled_formats", ("epub", "m4b", "mp3")),
                "evidence formats",
                required=True,
            ),
            maximum_document_bytes=evidence_source.get(
                "maximum_document_bytes", 67_108_864
            ),
            maximum_text_characters=evidence_source.get(
                "maximum_text_characters", 262_144
            ),
        )
        policy_source = source.get("policy", {})
        _only(policy_source, _POLICY_FIELDS, "policy")
        policy = PolicyConfiguration(
            policy_version=_text(
                policy_source.get("policy_version", "policy-v1"), "policy version", 64
            )
        )
        backups_enabled = _boolean(
            source.get("backups_enabled", True), "backups enabled"
        )
        destructive = _boolean(
            source.get("destructive_actions_enabled", False),
            "destructive actions enabled",
        )
        if destructive:
            raise ConfigurationError(
                "destructive actions are not configurable in this release"
            )
        retention_source = source.get("retention", {})
        _only(retention_source, _RETENTION_FIELDS, "retention")
        retention = RetentionConfiguration(**retention_source)
        return ApplicationConfiguration(
            schema_version=schema_version,
            instance_name=instance_name,
            connections=connections,
            media_mounts=mounts,
            limits=limits,
            inventory=inventory,
            evidence=evidence,
            providers=_text_tuple(source.get("providers", ()), "providers"),
            reviewers=_text_tuple(source.get("reviewers", ()), "reviewers"),
            policy=policy,
            notifications=_text_tuple(source.get("notifications", ()), "notifications"),
            backups_enabled=backups_enabled,
            retention=retention,
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


def _connection_projection(
    attribute: str,
) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: tuple(
        getattr(connection, attribute) for connection in configuration.connections
    )


def _mount_projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: tuple(
        getattr(mount, attribute) for mount in configuration.media_mounts
    )


def _limit_projection(attribute: str) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration.limits, attribute)


def _inventory_projection(
    attribute: str,
) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration.inventory, attribute)


def _evidence_projection(
    attribute: str,
) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration.evidence, attribute)


def _retention_projection(
    attribute: str,
) -> Callable[[ApplicationConfiguration], object]:
    return lambda configuration: getattr(configuration.retention, attribute)


_IMPACT_MATRIX: tuple[
    tuple[str, Callable[[ApplicationConfiguration], object], tuple[ChangeImpact, ...]],
    ...,
] = (
    ("instance.instance_name", _projection("instance_name"), (_R,)),
    ("connections.id", _connection_projection("connection_id"), (_R, _I, _X, _C, _V)),
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
    ("inventory.page_size", _inventory_projection("page_size"), (_R, _I)),
    ("inventory.max_pages", _inventory_projection("max_pages"), (_R, _I)),
    ("inventory.max_items", _inventory_projection("max_items"), (_R, _I)),
    (
        "inventory.max_response_bytes",
        _inventory_projection("max_response_bytes"),
        (_R, _I),
    ),
    ("inventory.timeout_seconds", _inventory_projection("timeout_seconds"), (_R, _I)),
    (
        "inventory.stability_delay_seconds",
        _inventory_projection("stability_delay_seconds"),
        (_R, _I),
    ),
    (
        "evidence.enabled_formats",
        _evidence_projection("enabled_formats"),
        (_R, _X, _E, _C, _V),
    ),
    (
        "evidence.maximum_document_bytes",
        _evidence_projection("maximum_document_bytes"),
        (_R, _E),
    ),
    (
        "evidence.maximum_text_characters",
        _evidence_projection("maximum_text_characters"),
        (_R, _E),
    ),
    ("providers", _projection("providers"), (_R, _C, _V)),
    ("reviewers", _projection("reviewers"), (_R, _V)),
    (
        "policy.policy_version",
        lambda configuration: configuration.policy.policy_version,
        (_R, _V),
    ),
    ("notifications", _projection("notifications"), (_R, _N)),
    ("backups_enabled", _projection("backups_enabled"), (_R, _B, _V)),
    ("retention.evidence_days", _retention_projection("evidence_days"), (_R,)),
    ("retention.job_log_days", _retention_projection("job_log_days"), (_R,)),
    (
        "retention.maximum_artifact_bytes",
        _retention_projection("maximum_artifact_bytes"),
        (_R,),
    ),
)


def plan_projected_import(
    current: ApplicationConfiguration, proposed: ApplicationConfiguration
) -> ConfigurationImportPlan:
    """Compare two strictly validated immutable configurations."""

    if (
        type(current) is not ApplicationConfiguration
        or type(proposed) is not ApplicationConfiguration
    ):
        raise ConfigurationError("configuration import requires validated projections")
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
        capability_overrides=_capability_overrides(
            value.get("capability_overrides", ())
        ),
        connection_id=(
            None
            if value.get("id") is None
            else ConnectionId(_required_text(value, "id", 64))
        ),
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
        raise ConfigurationError(
            "media mount paths must be absolute container/service paths"
        )
    return MediaMountConfiguration(
        name, library_type, container_path, service_path, True
    )


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
        or any(
            ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F
            for character in value
        )
    ):
        raise ConfigurationError(f"{label} is malformed")
    return value


def _sequence(value: object, label: str, *, required: bool = False) -> Sequence[object]:
    if type(value) not in {list, tuple} or (required and not value):
        raise ConfigurationError(f"{label} must be a bounded sequence")
    if len(value) > 64:
        raise ConfigurationError(f"{label} exceeds its size limit")
    return value


def _text_tuple(
    value: object, label: str, *, required: bool = False
) -> tuple[str, ...]:
    values = tuple(
        _text(item, label) for item in _sequence(value, label, required=required)
    )
    if len(set(values)) != len(values):
        raise ConfigurationError(f"{label} must not contain duplicates")
    return values


def _set_text_tuple(
    value: object, label: str, *, required: bool = False
) -> tuple[str, ...]:
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
