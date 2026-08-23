"""Strict bounded JSON configuration boundary for the report command."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal

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


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ConnectionInput(_Closed):
    name: Name
    adapter: Literal["audiobookshelf"]
    endpoint: Annotated[StrictStr, Field(min_length=1, max_length=2048)]
    secret_file: BoundedAbsolutePath
    plain_http: StrictBool = False


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


class ProviderInput(
    RootModel[
        EnabledHardcoverProviderInput
        | DisabledHardcoverProviderInput
        | EnabledOpenLibraryProviderInput
        | DisabledOpenLibraryProviderInput
        | EnabledGoogleBooksProviderInput
        | DisabledGoogleBooksProviderInput
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
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=60)] = 30


class ReviewInput(_Closed):
    canonical_origin: Annotated[StrictStr, Field(min_length=8, max_length=512)]
    owner_password_hash_file: BoundedAbsolutePath
    tls_certificate_file: BoundedAbsolutePath | None = None
    tls_private_key_file: BoundedAbsolutePath | None = None
    session_lifetime_seconds: Annotated[StrictInt, Field(ge=300, le=86_400)] = 3600
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


class ConfigurationInput(_Closed):
    schema_version: Literal["config-v6"]
    provider_recovery_generation: Annotated[StrictInt, Field(ge=1)]
    connections: Annotated[list[ConnectionInput], Field(min_length=1, max_length=32)]
    libraries: Annotated[list[LibraryInput], Field(min_length=1, max_length=128)]
    mounts: Annotated[list[MountInput], Field(max_length=32)]
    providers: Annotated[list[ProviderInput], Field(max_length=32)]
    limits: LimitsInput = LimitsInput()
    review: ReviewInput | None = None


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
                    (
                        ProviderConnectionConfiguration(**item.model_dump())
                        for item in value.providers
                    ),
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
            ),
        )
    except ConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        raise ConfigurationError("configuration document is invalid") from None


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
