"""Strict bounded JSON configuration boundary for the report command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    WithJsonSchema,
    model_validator,
)

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.domain.validation import (
    OWNER_SESSION_LIFETIME_DEFAULT,
    OWNER_SESSION_LIFETIME_MAXIMUM,
    OWNER_SESSION_LIFETIME_MINIMUM,
)

_MAX_CONFIG_BYTES = 262_144
Name = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
BoundedAbsolutePath = Annotated[
    StrictStr,
    Field(min_length=1, max_length=1024, pattern=r"^/"),
]
_CONTACT_EMAIL_PATTERN = (
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)


def _contact_email(value: str) -> str:
    if len(value.partition("@")[0]) > 64:
        raise ValueError("contact email is invalid")
    return value


ContactEmail = Annotated[
    StrictStr,
    Field(
        min_length=3,
        max_length=254,
        pattern="^" + _CONTACT_EMAIL_PATTERN + "$",
    ),
    AfterValidator(_contact_email),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 3,
            "maxLength": 254,
            "pattern": r"^(?=.{1,64}@)" + _CONTACT_EMAIL_PATTERN + "$",
        }
    ),
]


Marketplace = Literal["us", "uk", "de", "fr", "au", "in", "ca", "jp", "it", "es"]
# The same closed set the domain names, in the same US-first order. A test
# holds the two together, because a `Literal` cannot be built from the tuple.
_MARKETPLACE_DEFAULT: list[Marketplace] = list(get_args(Marketplace))


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConnectionInput(_Closed):
    name: Name
    adapter: Literal["audiobookshelf"]
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    secret_file: BoundedAbsolutePath


class _ProviderInput(_Closed):
    name: Name
    cover_enabled: StrictBool


class EnabledHardcoverProviderInput(_ProviderInput):
    adapter: Literal["hardcover"]
    enabled: Literal[True]
    secret_file: BoundedAbsolutePath
    contact_email: Literal[None] = None


class DisabledHardcoverProviderInput(_ProviderInput):
    adapter: Literal["hardcover"]
    enabled: Literal[False]
    cover_enabled: Literal[False]
    secret_file: Literal[None]
    contact_email: Literal[None] = None


class EnabledOpenLibraryProviderInput(_ProviderInput):
    adapter: Literal["open_library"]
    enabled: Literal[True]
    secret_file: Literal[None]
    contact_email: ContactEmail


class DisabledOpenLibraryProviderInput(_ProviderInput):
    adapter: Literal["open_library"]
    enabled: Literal[False]
    cover_enabled: Literal[False]
    secret_file: Literal[None]
    contact_email: Literal[None] = None


class EnabledInventaireProviderInput(_ProviderInput):
    adapter: Literal["inventaire"]
    enabled: Literal[True]
    secret_file: Literal[None]
    contact_email: Literal[None] = None
    cover_enabled: StrictBool = True


class DisabledInventaireProviderInput(_ProviderInput):
    adapter: Literal["inventaire"]
    enabled: Literal[False]
    cover_enabled: Literal[False]
    secret_file: Literal[None]
    contact_email: Literal[None] = None


class EnabledGoogleBooksProviderInput(_ProviderInput):
    adapter: Literal["google_books"]
    enabled: Literal[True]
    cover_enabled: Literal[False]
    secret_file: BoundedAbsolutePath | None
    contact_email: Literal[None] = None


class DisabledGoogleBooksProviderInput(_ProviderInput):
    adapter: Literal["google_books"]
    enabled: Literal[False]
    cover_enabled: Literal[False]
    secret_file: Literal[None]
    contact_email: Literal[None] = None


# ADR-0061 admits every audiobook provider keyless: no key exists for them even
# as an optional upgrade, so the four pairs below carry `secret_file: None` and
# no credential surface of any kind, exactly as Open Library's disabled form
# does. `cover_enabled` is closed shut by default and opened only on the two
# adapters that state a cover asset of their own.
class _KeylessAudioProviderInput(_Closed):
    name: Name
    cover_enabled: Literal[False]
    secret_file: Literal[None]
    contact_email: Literal[None] = None


# Audnexus and AudioSilo restate Audible's CDN asset, so only the Audible
# catalog and iTunes Search carry a real cover switch, and it defaults on: an
# audiobook's art is the corroborating surface a recording without an
# identifier is left with.  The fields are spelt
# out rather than inherited because widening a closed `Literal[False]` in a
# subclass would be an unsound override of the pair above.
class _CoverBearingAudioProviderInput(_Closed):
    name: Name
    cover_enabled: StrictBool = True
    secret_file: Literal[None]
    contact_email: Literal[None] = None


class EnabledAudibleCatalogProviderInput(_CoverBearingAudioProviderInput):
    adapter: Literal["audible_catalog"]
    enabled: Literal[True]
    # The resolution order for one ASIN. 27 of the reference library's 103
    # known ASINs resolve outside the US marketplace, so the fallback set is
    # configuration rather than a constant, and it defaults to all of them.
    marketplaces: Annotated[
        list[Marketplace], Field(min_length=1, max_length=len(_MARKETPLACE_DEFAULT))
    ] = _MARKETPLACE_DEFAULT


class DisabledAudibleCatalogProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["audible_catalog"]
    enabled: Literal[False]


class EnabledAudnexusProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["audnexus"]
    enabled: Literal[True]


class DisabledAudnexusProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["audnexus"]
    enabled: Literal[False]


class EnabledAudioSiloProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["audiosilo"]
    enabled: Literal[True]


class DisabledAudioSiloProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["audiosilo"]
    enabled: Literal[False]


class EnabledItunesSearchProviderInput(_CoverBearingAudioProviderInput):
    adapter: Literal["itunes_search"]
    enabled: Literal[True]


class DisabledItunesSearchProviderInput(_KeylessAudioProviderInput):
    adapter: Literal["itunes_search"]
    enabled: Literal[False]


class ProviderInput(
    RootModel[
        EnabledHardcoverProviderInput
        | DisabledHardcoverProviderInput
        | EnabledOpenLibraryProviderInput
        | DisabledOpenLibraryProviderInput
        | EnabledInventaireProviderInput
        | DisabledInventaireProviderInput
        | EnabledGoogleBooksProviderInput
        | DisabledGoogleBooksProviderInput
        | EnabledAudibleCatalogProviderInput
        | DisabledAudibleCatalogProviderInput
        | EnabledAudnexusProviderInput
        | DisabledAudnexusProviderInput
        | EnabledAudioSiloProviderInput
        | DisabledAudioSiloProviderInput
        | EnabledItunesSearchProviderInput
        | DisabledItunesSearchProviderInput
    ]
):
    pass


class MountInput(_Closed):
    name: Name
    container_path: BoundedAbsolutePath
    service_path: BoundedAbsolutePath
    read_only: Literal[True]


class PolicyOverrideInput(_Closed):
    """Sparse, read-only deviations from the built-in policy profile."""

    options: dict[Name, StrictStr] = {}
    destinations: dict[Name, StrictStr] = {}


class LibraryInput(_Closed):
    name: Name
    connection: Name
    library_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    mount: Name | None
    provider_connections: Annotated[list[Name], Field(max_length=32)]
    policy_override: PolicyOverrideInput | None = None


class LimitsInput(_Closed):
    target_response_bytes: Annotated[StrictInt, Field(ge=1024, le=4_194_304)] = (
        1_048_576
    )
    provider_response_bytes: Annotated[StrictInt, Field(ge=1024, le=262_144)] = 65_536
    source_archive_bytes: Annotated[StrictInt, Field(ge=1024, le=268_435_456)] = (
        268_435_456
    )
    # A per-field default on a closed model: a document written before this key
    # existed still validates and takes the default, so the audio ceiling
    # needed no schema increment of its own.
    audio_source_bytes: Annotated[StrictInt, Field(ge=1024, le=17_179_869_184)] = (
        8_589_934_592
    )
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=60)] = 30


class ReviewInput(_Closed):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "canonical_origin": {"pattern": "^http://"},
                        "tls_certificate_file": {"type": "null"},
                        "tls_private_key_file": {"type": "null"},
                    },
                },
                {
                    "properties": {
                        "canonical_origin": {"pattern": "^https://"},
                        "tls_certificate_file": {"type": "string"},
                        "tls_private_key_file": {"type": "string"},
                    },
                    "required": ["tls_certificate_file", "tls_private_key_file"],
                },
                {
                    "properties": {
                        "canonical_origin": {"pattern": "^https://"},
                        "tls_certificate_file": {"type": "null"},
                        "tls_private_key_file": {"type": "null"},
                        "trusted_proxy_ips": {"minItems": 1},
                    },
                    "required": ["trusted_proxy_ips"],
                },
            ],
        },
    )
    canonical_origin: Annotated[StrictStr, Field(min_length=8, max_length=512)]
    owner_password_hash_file: BoundedAbsolutePath
    tls_certificate_file: BoundedAbsolutePath | None = None
    tls_private_key_file: BoundedAbsolutePath | None = None
    session_lifetime_seconds: Annotated[
        StrictInt,
        Field(ge=OWNER_SESSION_LIFETIME_MINIMUM, le=OWNER_SESSION_LIFETIME_MAXIMUM),
    ] = OWNER_SESSION_LIFETIME_DEFAULT
    trusted_proxy_ips: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=64)]],
        Field(max_length=16),
    ] = []
    library_polling_interval_seconds: Annotated[StrictInt, Field(ge=60, le=86_400)] = (
        900
    )
    graceful_shutdown_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30
    diagnostic_timings_enabled: StrictBool = False
    cover_reviewer_profile_file: BoundedAbsolutePath | None = None
    cover_avoid_promotional_text: StrictBool = False
    cover_avoid_tie_in_branding: StrictBool = False
    cover_prefer_minimal_composition: StrictBool = False
    description_remove_marketing: StrictBool = True
    description_remove_sales_boasts: StrictBool = True
    description_remove_praise: StrictBool = True
    description_automatic_application: StrictBool = False

    @model_validator(mode="after")
    def validate_transport(self) -> ReviewInput:
        ReviewWebConfiguration(
            **{
                **self.model_dump(),
                "trusted_proxy_ips": tuple(sorted(self.trusted_proxy_ips)),
            }
        )
        return self


class ConfigurationInput(_Closed):
    schema_version: Literal["config-v9"]
    provider_recovery_generation: Annotated[StrictInt, Field(ge=1)]
    connections: Annotated[list[ConnectionInput], Field(min_length=1, max_length=32)]
    libraries: Annotated[list[LibraryInput], Field(min_length=1, max_length=128)]
    mounts: Annotated[list[MountInput], Field(max_length=32)]
    providers: Annotated[list[ProviderInput], Field(max_length=32)]
    limits: LimitsInput = LimitsInput()
    review: ReviewInput | None = None


def load_configuration_document(payload: bytes) -> ApplicationConfiguration:
    """Parse one already bound configuration document."""
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= _MAX_CONFIG_BYTES:
            raise ConfigurationError("configuration document is invalid")
        source = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
        )
        value = ConfigurationInput.model_validate(source)
        return ApplicationConfiguration(
            tuple(
                sorted(
                    (
                        TargetConnectionConfiguration(**item.model_dump())
                        for item in value.connections
                    ),
                    key=lambda item: item.name,
                )
            ),
            tuple(
                sorted(
                    (
                        LibraryConfiguration(
                            **{
                                **item.model_dump(),
                                "media_kind": "book",
                                "provider_connections": tuple(
                                    sorted(item.provider_connections)
                                ),
                            }
                        )
                        for item in value.libraries
                    ),
                    key=lambda item: item.name,
                )
            ),
            tuple(
                sorted(
                    (
                        MediaMountConfiguration(**item.model_dump())
                        for item in value.mounts
                    ),
                    key=lambda item: item.name,
                )
            ),
            tuple(
                sorted(
                    (_provider_connection(item) for item in value.providers),
                    key=lambda item: item.name,
                )
            ),
            ReportLimits(**value.limits.model_dump()),
            value.schema_version,
            value.provider_recovery_generation,
            None
            if value.review is None
            else ReviewWebConfiguration(
                canonical_origin=value.review.canonical_origin,
                owner_password_hash_file=value.review.owner_password_hash_file,
                tls_certificate_file=value.review.tls_certificate_file,
                tls_private_key_file=value.review.tls_private_key_file,
                session_lifetime_seconds=value.review.session_lifetime_seconds,
                trusted_proxy_ips=tuple(sorted(value.review.trusted_proxy_ips)),
                library_polling_interval_seconds=value.review.library_polling_interval_seconds,
                graceful_shutdown_seconds=value.review.graceful_shutdown_seconds,
                diagnostic_timings_enabled=value.review.diagnostic_timings_enabled,
                cover_reviewer_profile_file=value.review.cover_reviewer_profile_file,
                cover_avoid_promotional_text=value.review.cover_avoid_promotional_text,
                cover_avoid_tie_in_branding=value.review.cover_avoid_tie_in_branding,
                cover_prefer_minimal_composition=value.review.cover_prefer_minimal_composition,
                description_remove_marketing=value.review.description_remove_marketing,
                description_remove_sales_boasts=value.review.description_remove_sales_boasts,
                description_remove_praise=value.review.description_remove_praise,
                description_automatic_application=value.review.description_automatic_application,
            ),
        )
    except ConfigurationError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


def load_configuration_file(path: Path) -> ApplicationConfiguration:
    try:
        return load_configuration_document(_read_regular(path))
    except ConfigurationError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


def _provider_connection(value: ProviderInput) -> ProviderConnectionConfiguration:
    """Read one validated provider input into its immutable domain value."""
    fields = dict(value.model_dump())
    marketplaces = fields.pop("marketplaces", ())
    return ProviderConnectionConfiguration(**fields, marketplaces=tuple(marketplaces))


def configuration_schema_json() -> str:
    return (
        json.dumps(
            ConfigurationInput.model_json_schema(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _read_regular(path: Path) -> bytes:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or not 1 <= information.st_size <= _MAX_CONFIG_BYTES
        ):
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
            raise ConfigurationError(
                "configuration document contains a duplicate field"
            )
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ConfigurationError("configuration document is invalid")


__all__ = ["ConfigurationInput", "configuration_schema_json", "load_configuration_file"]
