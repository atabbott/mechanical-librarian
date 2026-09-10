"""Closed, redacted owner-settings contract."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Final, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.validation import (
    OWNER_SESSION_LIFETIME_DEFAULT,
    OWNER_SESSION_LIFETIME_MAXIMUM,
    OWNER_SESSION_LIFETIME_MINIMUM,
    credential,
)

Name = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Email = Annotated[StrictStr, Field(min_length=3, max_length=254)]
Marketplace = Literal["us", "uk", "de", "fr", "au", "in", "ca", "jp", "it", "es"]
# The same closed set the report vocabulary and the configuration boundary
# name, in the same US-first order. A test holds the three together, because a
# `Literal` cannot be built from the tuple and the settings contract needs the
# enumeration in its published JSON schema.
_MARKETPLACE_DEFAULT: list[Marketplace] = list(get_args(Marketplace))
_CONTACT_EMAIL = re.compile(
    r"^(?=.{1,64}@)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


class OwnerSettingsError(ValueError):
    """Fixed value-free owner-settings failure."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TargetSettings(_Closed):
    name: Name
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    adapter: Literal["audiobookshelf"] = "audiobookshelf"


class HardcoverProviderSettings(_Closed):
    name: Name
    adapter: Literal["hardcover"]
    enabled: StrictBool
    cover_enabled: StrictBool = False


class GoogleBooksProviderSettings(_Closed):
    name: Name
    adapter: Literal["google_books"]
    enabled: StrictBool
    cover_enabled: Literal[False] = False


class OpenLibraryProviderSettings(_Closed):
    name: Name
    adapter: Literal["open_library"]
    enabled: StrictBool
    cover_enabled: StrictBool = False
    contact_email: Email | None = None


class InventaireProviderSettings(_Closed):
    name: Name
    adapter: Literal["inventaire"]
    enabled: StrictBool
    cover_enabled: StrictBool = False


# ADR-0061 admits every audiobook provider keyless: no credential exists for
# one even as an optional upgrade, so none of the four below carries a secret
# or contact field of any kind. Covers are admitted on the two that state an
# asset of their own and refused on the two that restate Audible's, exactly as
# the configuration boundary grants them.
class AudibleCatalogProviderSettings(_Closed):
    name: Name
    adapter: Literal["audible_catalog"]
    enabled: StrictBool
    cover_enabled: StrictBool = False
    # The order one ASIN lookup resolves in. The owner reads it here; the
    # configuration file is the only place it is written.
    marketplaces: Annotated[
        list[Marketplace], Field(min_length=1, max_length=len(_MARKETPLACE_DEFAULT))
    ] = _MARKETPLACE_DEFAULT


class AudnexusProviderSettings(_Closed):
    name: Name
    adapter: Literal["audnexus"]
    enabled: StrictBool
    cover_enabled: Literal[False] = False


class AudioSiloProviderSettings(_Closed):
    name: Name
    adapter: Literal["audiosilo"]
    enabled: StrictBool
    cover_enabled: Literal[False] = False


class ItunesSearchProviderSettings(_Closed):
    name: Name
    adapter: Literal["itunes_search"]
    enabled: StrictBool
    cover_enabled: StrictBool = False


ProviderSettings = Annotated[
    HardcoverProviderSettings
    | GoogleBooksProviderSettings
    | OpenLibraryProviderSettings
    | InventaireProviderSettings
    | AudibleCatalogProviderSettings
    | AudnexusProviderSettings
    | AudioSiloProviderSettings
    | ItunesSearchProviderSettings,
    Field(discriminator="adapter"),
]


ProviderAdapter = Literal[
    "open_library",
    "inventaire",
    "hardcover",
    "google_books",
    "audible_catalog",
    "audnexus",
    "audiosilo",
    "itunes_search",
]
Credential = Literal["none", "contact_email", "api_key"]
# One display name per adapter. The capability list reads it directly; the
# browser vocabulary ships the same map to setup and to Settings, and
# `review_web._provider_label` restates it because that module is byte-bound by
# the F19a association re-qualification record. A test holds all four to the
# same word, because none of them can import the others. The instance name
# stays the owner's own identifier throughout: a label never reaches a field
# id, a `provider_connections` entry, or a capability `name`.
PROVIDER_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "audible_catalog": "Audible",
        "audiosilo": "AudioSilo",
        "audnexus": "Audnexus",
        "google_books": "Google Books",
        "hardcover": "Hardcover",
        "inventaire": "Inventaire",
        "itunes_search": "iTunes",
        "open_library": "Open Library",
    }
)


class ProviderCapability(_Closed):
    """One fixed adapter the owner may configure when it is not yet present."""

    name: Name
    adapter: ProviderAdapter
    label: StrictStr
    credential: Credential


# The order the owner sees an unconfigured provider offered in.
_PROVIDER_CREDENTIALS: tuple[tuple[ProviderAdapter, Credential], ...] = (
    ("audible_catalog", "none"),
    ("audiosilo", "none"),
    ("audnexus", "none"),
    ("google_books", "api_key"),
    ("hardcover", "api_key"),
    ("itunes_search", "none"),
    ("inventaire", "none"),
    ("open_library", "contact_email"),
)
_PROVIDER_CAPABILITIES: tuple[ProviderCapability, ...] = tuple(
    ProviderCapability(
        name=adapter,
        adapter=adapter,
        label=PROVIDER_LABELS[adapter],
        credential=credential,
    )
    for adapter, credential in _PROVIDER_CREDENTIALS
)


class LibrarySettings(_Closed):
    name: Name
    display_name: Annotated[StrictStr, Field(min_length=1, max_length=256)] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    connection: Name
    library_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    media_kind: Literal["book", "ebook", "audiobook"]
    provider_connections: Annotated[list[Name], Field(max_length=32)] = []
    # Deploy-authoritative display-only association.
    mount: Name | None = None
    policy_override: dict[str, dict[str, str]] | None = None

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: str | None) -> str | None:
        if value is not None and ("\x00" in value or value.strip() != value):
            raise OwnerSettingsError("library display name is invalid")
        return value


class ReportLimitSettings(_Closed):
    target_response_bytes: Annotated[StrictInt, Field(ge=1_024, le=4_194_304)] = (
        1_048_576
    )
    provider_response_bytes: Annotated[StrictInt, Field(ge=1_024, le=262_144)] = 65_536
    source_archive_bytes: Annotated[StrictInt, Field(ge=1_024, le=268_435_456)] = (
        268_435_456
    )
    audio_source_bytes: Annotated[StrictInt, Field(ge=1_024, le=17_179_869_184)] = (
        8_589_934_592
    )
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=60)] = 30


class ServerSettings(_Closed):
    session_lifetime_seconds: Annotated[
        StrictInt,
        Field(ge=OWNER_SESSION_LIFETIME_MINIMUM, le=OWNER_SESSION_LIFETIME_MAXIMUM),
    ] = OWNER_SESSION_LIFETIME_DEFAULT
    library_polling_interval_seconds: Annotated[StrictInt, Field(ge=60, le=86_400)] = (
        900
    )
    graceful_shutdown_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30
    diagnostic_timings_enabled: StrictBool = False


class CoverPreferenceSettings(_Closed):
    avoid_promotional_text: StrictBool = False
    avoid_tie_in_branding: StrictBool = False
    prefer_minimal_composition: StrictBool = False


class OwnerSettingsDocument(_Closed):
    schema_version: Literal["owner-settings-v3"]
    revision: Annotated[StrictInt, Field(ge=1)]
    connections: Annotated[list[TargetSettings], Field(min_length=1, max_length=32)]
    providers: Annotated[list[ProviderSettings], Field(max_length=32)] = []
    libraries: Annotated[list[LibrarySettings], Field(min_length=1, max_length=128)]
    limits: ReportLimitSettings = ReportLimitSettings()
    provider_recovery_generation: Annotated[StrictInt, Field(ge=1)] = 1
    server: ServerSettings = ServerSettings()
    cover_preferences: CoverPreferenceSettings = CoverPreferenceSettings()
    reduced_motion: Literal["follow_system", "always_reduced"] = "follow_system"
    pending_restart: Annotated[list[StrictStr], Field(max_length=32)] = []

    @model_validator(mode="after")
    def _validate_contract(self) -> "OwnerSettingsDocument":
        connection_names = tuple(value.name for value in self.connections)
        provider_names = tuple(value.name for value in self.providers)
        library_names = tuple(value.name for value in self.libraries)
        if any(
            values != tuple(sorted(set(values)))
            for values in (connection_names, provider_names, library_names)
        ):
            raise OwnerSettingsError("settings names are not in canonical unique order")
        if any(
            item.provider_connections != sorted(set(item.provider_connections))
            for item in self.libraries
        ):
            raise OwnerSettingsError(
                "settings provider references are duplicated or invalid"
            )
        if any(
            item.connection not in connection_names for item in self.libraries
        ) or any(
            not set(item.provider_connections).issubset(provider_names)
            for item in self.libraries
        ):
            raise OwnerSettingsError("settings library references are invalid")
        if self.pending_restart != sorted(set(self.pending_restart)):
            raise OwnerSettingsError("settings pending restart is invalid")
        for provider in self.providers:
            if provider.cover_enabled and not provider.enabled:
                raise OwnerSettingsError("settings document is invalid")
            if isinstance(provider, AudibleCatalogProviderSettings) and len(
                set(provider.marketplaces)
            ) != len(provider.marketplaces):
                # A repeated marketplace is a resolution order no lookup can
                # honour, and the configuration boundary refuses it too.
                raise OwnerSettingsError("settings document is invalid")
            if isinstance(provider, OpenLibraryProviderSettings) and (
                (provider.enabled and provider.contact_email is None)
                or (
                    provider.contact_email is not None
                    and _CONTACT_EMAIL.fullmatch(provider.contact_email) is None
                )
            ):
                raise OwnerSettingsError("settings document is invalid")
        for library in self.libraries:
            if library.policy_override is None:
                continue
            try:
                resolve_policy(
                    MetadataPolicyProfile.builtin(),
                    library.name,
                    library.policy_override,
                )
            except MetadataPolicyError as error:
                raise OwnerSettingsError("settings document is invalid") from error
        return self

    def secret_fields(self) -> frozenset[str]:
        return frozenset(
            {f"connection.{item.name}.key" for item in self.connections}
            | {
                f"provider.{item.name}.key"
                for item in self.providers
                if item.adapter in {"hardcover", "google_books"}
            }
        )

    def required_secret_fields(self) -> frozenset[str]:
        return frozenset(
            {f"connection.{item.name}.key" for item in self.connections}
            | {
                f"provider.{item.name}.key"
                for item in self.providers
                if item.adapter == "hardcover" and item.enabled
            }
        )

    def runtime_secret_fields(self) -> frozenset[str]:
        """Credentials admitted to one active runtime generation."""
        return frozenset(
            {f"connection.{item.name}.key" for item in self.connections}
            | {
                f"provider.{item.name}.key"
                for item in self.providers
                if item.enabled and item.adapter in {"hardcover", "google_books"}
            }
        )


class SecretWrite(_Closed):
    field_id: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]
    operation: Literal["set", "replace", "clear"]
    value: Annotated[
        StrictStr | None,
        Field(
            min_length=1,
            max_length=2048,
            repr=False,
            json_schema_extra={"writeOnly": True},
        ),
    ] = None

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: str | None) -> str | None:
        if value is not None and not credential(value):
            raise OwnerSettingsError("settings secret operation is invalid")
        return value

    def model_post_init(self, __context: object) -> None:
        if (self.operation == "clear") != (self.value is None):
            raise OwnerSettingsError("settings secret operation is invalid")


class SettingField(_Closed):
    field_id: StrictStr
    group: StrictStr
    managed_by_file: StrictBool
    managed_path: StrictStr | None = None
    apply_effect: Literal["applied_live", "deploy_managed"]


class SecretPresence(_Closed):
    field_id: StrictStr
    configured: StrictBool
    indicator: StrictStr | None = None


def secret_presence(field_id: str, value: str | None) -> SecretPresence:
    return SecretPresence(
        field_id=field_id,
        configured=value is not None,
        indicator=(
            None
            if value is None
            else "Configured · " + ("····" + value[-4:] if len(value) >= 8 else "····")
        ),
    )


def provider_capabilities(
    document: OwnerSettingsDocument,
) -> tuple[ProviderCapability, ...]:
    """Expose stable default instances only when their adapter is absent."""
    configured = {provider.adapter for provider in document.providers}
    return tuple(
        item for item in _PROVIDER_CAPABILITIES if item.adapter not in configured
    )


class SettingsView(_Closed):
    document: OwnerSettingsDocument
    fields: tuple[SettingField, ...]
    secrets: tuple[SecretPresence, ...]
    provider_capabilities: tuple[ProviderCapability, ...] = ()
    authority: Literal["file", "store"]
    managed_path: StrictStr | None = None


def store_field_metadata(document: OwnerSettingsDocument) -> tuple[SettingField, ...]:
    live = {
        "connections",
        "providers",
        "libraries",
        "limits",
        "provider_recovery_generation",
        "server.session_lifetime_seconds",
        "server.library_polling_interval_seconds",
        "server.graceful_shutdown_seconds",
        "server.diagnostic_timings_enabled",
    }
    fields = [
        SettingField(
            field_id=value,
            group=value.split(".", 1)[0],
            managed_by_file=False,
            apply_effect="applied_live",
        )
        for value in sorted(
            live
            | {
                "reduced_motion",
                "cover_preferences.avoid_promotional_text",
                "cover_preferences.avoid_tie_in_branding",
                "cover_preferences.prefer_minimal_composition",
            }
        )
    ]
    fields.append(
        SettingField(
            field_id="mounts",
            group="libraries",
            managed_by_file=True,
            managed_path="deployment configuration",
            apply_effect="deploy_managed",
        )
    )
    return tuple(fields)


def file_field_metadata(
    document: OwnerSettingsDocument, path: str = "/data/config.json"
) -> tuple[SettingField, ...]:
    """The file-authority projection deliberately locks every covered setting."""
    return tuple(
        SettingField(
            field_id=item.field_id,
            group=item.group,
            managed_by_file=True,
            managed_path=path,
            apply_effect=item.apply_effect,
        )
        for item in store_field_metadata(document)
    )


__all__ = [
    "PROVIDER_LABELS",
    "AudibleCatalogProviderSettings",
    "AudioSiloProviderSettings",
    "AudnexusProviderSettings",
    "GoogleBooksProviderSettings",
    "CoverPreferenceSettings",
    "HardcoverProviderSettings",
    "ItunesSearchProviderSettings",
    "LibrarySettings",
    "OpenLibraryProviderSettings",
    "OwnerSettingsDocument",
    "OwnerSettingsError",
    "ProviderAdapter",
    "ProviderCapability",
    "ReportLimitSettings",
    "SecretPresence",
    "SecretWrite",
    "ServerSettings",
    "SettingField",
    "SettingsView",
    "TargetSettings",
    "file_field_metadata",
    "provider_capabilities",
    "store_field_metadata",
    "secret_presence",
]
