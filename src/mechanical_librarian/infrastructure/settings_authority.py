"""Startup authority selection for file and owner-managed configuration."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
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
from mechanical_librarian.domain.owner_settings import SettingsView
from mechanical_librarian.infrastructure.configuration import (
    _read_regular,
    _reject_number,
    _unique_object,
    load_configuration_file,
)

DEFAULT_REVIEWER_PROFILE = "/opt/mechanical-librarian/reviewer/profile.json"

_Configuration = TypeVar("_Configuration")


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BootstrapMount(_Closed):
    name: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    container_path: Annotated[StrictStr, Field(min_length=1, max_length=1024)]
    service_path: Annotated[StrictStr, Field(min_length=1, max_length=1024)]
    read_only: Literal[True]


class ReviewBootstrap(_Closed):
    schema_version: Literal["review-bootstrap-v2"]
    canonical_origin: Annotated[StrictStr, Field(min_length=8, max_length=512)]
    tls_certificate_file: (
        Annotated[StrictStr, Field(min_length=1, max_length=1024)] | None
    ) = None
    tls_private_key_file: (
        Annotated[StrictStr, Field(min_length=1, max_length=1024)] | None
    ) = None
    trusted_proxy_ips: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=64)]],
        Field(max_length=16),
    ] = []
    mounts: Annotated[list[BootstrapMount], Field(max_length=32)] = []
    cover_reviewer_profile_file: (
        Annotated[StrictStr, Field(min_length=1, max_length=1024)] | None
    ) = None

    @model_validator(mode="after")
    def validate_transport(self) -> ReviewBootstrap:
        ReviewWebConfiguration(
            canonical_origin=self.canonical_origin,
            owner_password_hash_file="/data/.owner-verifier-store",
            tls_certificate_file=self.tls_certificate_file,
            tls_private_key_file=self.tls_private_key_file,
            trusted_proxy_ips=tuple(sorted(self.trusted_proxy_ips)),
        )
        return self


def load_review_bootstrap(path: Path) -> ReviewBootstrap:
    try:
        source = json.loads(
            _read_regular(path).decode("utf-8", "strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_number,
        )
        return ReviewBootstrap.model_validate(source)
    except (OSError, UnicodeError, ValueError, ValidationError, RecursionError):
        raise ConfigurationError("review bootstrap is unavailable") from None


def store_runtime_configuration(
    settings: SettingsView,
    bootstrap: ReviewBootstrap,
) -> ApplicationConfiguration:
    """Project one already validated store snapshot into the runtime graph."""
    document = settings.document
    mounts = tuple(
        sorted(
            (MediaMountConfiguration(**item.model_dump()) for item in bootstrap.mounts),
            key=lambda item: item.name,
        )
    )
    review = ReviewWebConfiguration(
        canonical_origin=bootstrap.canonical_origin,
        owner_password_hash_file="/data/.owner-verifier-store",
        tls_certificate_file=bootstrap.tls_certificate_file,
        tls_private_key_file=bootstrap.tls_private_key_file,
        trusted_proxy_ips=tuple(sorted(bootstrap.trusted_proxy_ips)),
        session_lifetime_seconds=document.server.session_lifetime_seconds,
        library_polling_interval_seconds=document.server.library_polling_interval_seconds,
        graceful_shutdown_seconds=document.server.graceful_shutdown_seconds,
        diagnostic_timings_enabled=document.server.diagnostic_timings_enabled,
        # The deployment owns the executable profile; application state owns
        # whether Codex is selected for any task.
        cover_reviewer_profile_file=(
            bootstrap.cover_reviewer_profile_file or DEFAULT_REVIEWER_PROFILE
        ),
        cover_avoid_promotional_text=document.cover_preferences.avoid_promotional_text,
        cover_avoid_tie_in_branding=document.cover_preferences.avoid_tie_in_branding,
        cover_prefer_minimal_composition=document.cover_preferences.prefer_minimal_composition,
        description_remove_marketing=document.description_advice.remove_marketing,
        description_remove_sales_boasts=document.description_advice.remove_sales_boasts,
        description_remove_praise=document.description_advice.remove_praise,
        description_automatic_application=document.description_advice.automatic_application,
    )
    return ApplicationConfiguration(
        connections=tuple(
            TargetConnectionConfiguration(
                name=item.name,
                endpoint=item.endpoint,
                secret_file=None,
                adapter=item.adapter,
            )
            for item in document.connections
        ),
        libraries=tuple(
            LibraryConfiguration(
                name=item.name,
                connection=item.connection,
                library_id=item.library_id,
                media_kind=item.media_kind,
                mount=item.mount,
                provider_connections=tuple(item.provider_connections),
                policy_override=item.policy_override,
            )
            for item in document.libraries
        ),
        mounts=mounts,
        providers=tuple(
            ProviderConnectionConfiguration(
                name=item.name,
                adapter=item.adapter,
                enabled=item.enabled,
                cover_enabled=item.cover_enabled,
                contact_email=getattr(item, "contact_email", None),
                secret_file=None,
                # A resolution order belongs to the enabled Audible catalogue
                # and to nothing else, so a disabled entry keeps its recorded
                # order in the document and states none to the runtime.
                marketplaces=(
                    tuple(getattr(item, "marketplaces", ())) if item.enabled else ()
                ),
            )
            for item in document.providers
        ),
        limits=ReportLimits(**document.limits.model_dump()),
        provider_recovery_generation=document.provider_recovery_generation,
        review=review,
    )


def resolve_startup_configuration(
    config_path: Path, load_store: Callable[[], _Configuration]
) -> _Configuration:
    """Use store authority only when the protected file is genuinely absent.

    ``load_configuration_file`` owns the no-follow regular-file check.  Its
    validation or filesystem failures intentionally remain failures: treating
    them as absence would let an unsafe file silently switch authority.
    """
    try:
        return load_configuration_file(config_path)  # type: ignore[return-value]
    except FileNotFoundError:
        try:
            return load_store()
        except ConfigurationError:
            raise


__all__ = [
    "ReviewBootstrap",
    "load_review_bootstrap",
    "resolve_startup_configuration",
    "store_runtime_configuration",
]
