"""Secret-safe, explicit configuration values for the 0.4 report command."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit

from mechanical_librarian.domain.cover_rules import CoverProtection
from mechanical_librarian.domain.field_outcomes import (
    language_identity,
    library_languages_valid,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_MARKETPLACE_LANGUAGES,
    AUDIOBOOK_MARKETPLACES,
    COVER_ONLY_AUDIOBOOK_AUTHORITIES,
)
from mechanical_librarian.domain.validation import (
    OWNER_SESSION_LIFETIME_DEFAULT,
    http_origin,
    owner_session_lifetime,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CONTACT_EMAIL = re.compile(
    r"^(?=.{1,64}@)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9]"
    r"(?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


EBOOK_PROVIDER_ADAPTERS = frozenset(
    {"google_books", "hardcover", "inventaire", "open_library"}
)
# ADR-0061's admitted set, in its stated layer order: the anonymous Audible
# catalog is primary discovery and enrichment, Audnexus is per-ASIN enrichment,
# AudioSilo is community corroboration and degraded mode, and iTunes Search
# supplies a cover only (#1779). Every one of them is keyless.
AUDIOBOOK_PROVIDER_ADAPTERS = frozenset(
    {"audible_catalog", "audnexus", "audiosilo", "itunes_search"}
)
# The two of those four that state a cover asset of their own. Audnexus and
# AudioSilo restate Audible's CDN asset, so enabling covers on them would buy a
# second copy of one image rather than a second opinion about it.
AUDIOBOOK_COVER_PROVIDER_ADAPTERS = frozenset({"audible_catalog", "itunes_search"})
# ADR-0061 clause 9 (#1247, #1779): the only adapters an audiobook is ever
# dispatched to. A library's connections narrow this set and never widen it, so
# Google Books and Inventaire stay admitted for configuration and ebooks but
# receive no audiobook request, observation or recovery work. iTunes Search is
# asked once per audiobook, for its cover alone.
AUDIOBOOK_DISPATCH_ADAPTERS = frozenset(
    {
        "audible_catalog",
        "audnexus",
        "audiosilo",
        "hardcover",
        "itunes_search",
        "open_library",
    }
)
# ADR-0061 (#1486): the only adapters an ebook is ever dispatched to, under
# the same narrowing rule. iTunes Search keeps its ebook title-and-author
# corroboration lane; Audible, Audnexus and AudioSilo have no ebook role.
EBOOK_DISPATCH_ADAPTERS = frozenset(
    {"google_books", "hardcover", "inventaire", "itunes_search", "open_library"}
)


def dispatch_adapters(*, audio: bool) -> frozenset[str]:
    """The adapters a work of this media kind may be dispatched to."""
    return AUDIOBOOK_DISPATCH_ADAPTERS if audio else EBOOK_DISPATCH_ADAPTERS


def recovery_adapters(*, audio: bool) -> frozenset[str]:
    """The dispatched adapters whose failures may become recovery work.

    A cover-only audiobook authority is asked once and never retried: its
    failure leaves the audiobook without that cover and nothing else (#1779).
    """
    if not audio:
        return EBOOK_DISPATCH_ADAPTERS
    return AUDIOBOOK_DISPATCH_ADAPTERS - COVER_ONLY_AUDIOBOOK_AUTHORITIES


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


@dataclass(frozen=True, slots=True)
class ReviewWebConfiguration:
    canonical_origin: str
    owner_password_hash_file: str
    session_lifetime_seconds: int = OWNER_SESSION_LIFETIME_DEFAULT
    trusted_proxy_ips: tuple[str, ...] = ()
    tls_certificate_file: str | None = None
    tls_private_key_file: str | None = None
    library_polling_interval_seconds: int = 900
    graceful_shutdown_seconds: int = 30
    diagnostic_timings_enabled: bool = False
    cover_reviewer_profile_file: str | None = None
    cover_avoid_promotional_text: bool = False
    cover_avoid_tie_in_branding: bool = False
    cover_prefer_minimal_composition: bool = False
    cover_current_protection: str = "protected"
    cover_prefer_edition: bool = False
    description_remove_marketing: bool = True
    description_remove_sales_boasts: bool = True
    description_remove_praise: bool = True
    description_automatic_application: bool = False
    # ADR-0096's Book matching level (#1355): which provider records count as
    # the same book when the owner reviews. Review-only until #1418.
    work_identity: WorkIdentityLevel = WorkIdentityLevel.IDENTIFIER

    def __post_init__(self) -> None:
        if (
            not http_origin(self.canonical_origin)
            or len(self.canonical_origin) > 512
            or self.canonical_origin.endswith("/")
        ):
            raise ConfigurationError("review canonical origin is invalid")
        origin = urlsplit(self.canonical_origin)
        _absolute_path(self.owner_password_hash_file, "owner password hash file")
        if (self.tls_certificate_file is None) != (self.tls_private_key_file is None):
            raise ConfigurationError("review TLS configuration is invalid")
        if self.tls_certificate_file is not None:
            _absolute_path(self.tls_certificate_file, "TLS certificate file")
            assert self.tls_private_key_file is not None
            _absolute_path(self.tls_private_key_file, "TLS private key file")
        if self.cover_reviewer_profile_file is not None:
            _absolute_path(
                self.cover_reviewer_profile_file, "cover reviewer profile file"
            )
        if (
            not owner_session_lifetime(self.session_lifetime_seconds)
            or type(self.trusted_proxy_ips) is not tuple
            or len(self.trusted_proxy_ips) > 16
            or any(
                type(value) is not str
                or not value
                or len(value) > 64
                or value.strip() != value
                for value in self.trusted_proxy_ips
            )
            or self.trusted_proxy_ips != tuple(sorted(set(self.trusted_proxy_ips)))
            or type(self.library_polling_interval_seconds) is not int
            or not 60 <= self.library_polling_interval_seconds <= 86_400
            or type(self.graceful_shutdown_seconds) is not int
            or not 1 <= self.graceful_shutdown_seconds <= 300
            or type(self.diagnostic_timings_enabled) is not bool
            or type(self.cover_current_protection) is not str
            or self.cover_current_protection
            not in {item.value for item in CoverProtection}
            or any(
                type(value) is not bool
                for value in (
                    self.cover_avoid_promotional_text,
                    self.cover_avoid_tie_in_branding,
                    self.cover_prefer_minimal_composition,
                    self.cover_prefer_edition,
                    self.description_remove_marketing,
                    self.description_remove_sales_boasts,
                    self.description_remove_praise,
                    self.description_automatic_application,
                )
            )
            or type(self.work_identity) is not WorkIdentityLevel
        ):
            raise ConfigurationError("review web configuration is invalid")
        try:
            for value in self.trusted_proxy_ips:
                ip_address(value)
        except ValueError as error:
            raise ConfigurationError("review web configuration is invalid") from error
        if (origin.scheme == "http" and self.tls_certificate_file is not None) or (
            origin.scheme == "https"
            and self.tls_certificate_file is None
            and not self.trusted_proxy_ips
        ):
            raise ConfigurationError("review TLS configuration is invalid")


@dataclass(frozen=True, repr=False, slots=True)
class TargetConnectionConfiguration:
    name: str
    endpoint: str
    secret_file: str | None
    adapter: str = "audiobookshelf"

    def __post_init__(self) -> None:
        _name(self.name, "target connection name")
        if self.adapter != "audiobookshelf" or type(self.endpoint) is not str:
            raise ConfigurationError("target connection is invalid")
        if self.secret_file is not None:
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
    contact_email: str | None = None
    marketplaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.name, "provider connection name")
        if (
            self.adapter not in EBOOK_PROVIDER_ADAPTERS | AUDIOBOOK_PROVIDER_ADAPTERS
            or type(self.enabled) is not bool
            or type(self.cover_enabled) is not bool
        ):
            raise ConfigurationError("provider connection is invalid")
        if self.cover_enabled and not self.enabled:
            raise ConfigurationError("cover enablement requires provider enablement")
        # A marketplace list orders the fallback of one ASIN lookup, so it
        # belongs to the enabled Audible catalog and to nothing else.
        if self.marketplaces and (
            self.adapter != "audible_catalog"
            or not self.enabled
            or type(self.marketplaces) is not tuple
            or len(self.marketplaces) > len(AUDIOBOOK_MARKETPLACES)
            or len(set(self.marketplaces)) != len(self.marketplaces)
            or any(value not in AUDIOBOOK_MARKETPLACES for value in self.marketplaces)
        ):
            raise ConfigurationError("provider marketplaces are invalid")
        if self.adapter in AUDIOBOOK_PROVIDER_ADAPTERS:
            # ADR-0061 admits these keyless: no credential exists for them even
            # as an optional upgrade, so no credential surface may be
            # configured on one. Covers are admitted on the two that state an
            # asset of their own and refused on the two that restate Audible's.
            if (
                self.secret_file is not None
                or self.contact_email is not None
                or (
                    self.cover_enabled
                    and self.adapter not in AUDIOBOOK_COVER_PROVIDER_ADAPTERS
                )
                or (self.adapter == "audible_catalog" and self.enabled)
                != bool(self.marketplaces)
            ):
                raise ConfigurationError("provider connection is invalid")
            return
        if self.adapter == "hardcover":
            if self.contact_email is not None or (
                not self.enabled and self.secret_file is not None
            ):
                raise ConfigurationError("provider connection is invalid")
        elif self.adapter == "inventaire":
            if self.secret_file is not None or self.contact_email is not None:
                raise ConfigurationError("provider connection is invalid")
        elif self.adapter == "google_books":
            if (
                self.contact_email is not None
                or self.cover_enabled
                or (not self.enabled and self.secret_file is not None)
            ):
                raise ConfigurationError("provider connection is invalid")
        elif (
            self.secret_file is not None
            or (self.enabled and self.contact_email is None)
            or (
                self.contact_email is not None
                and (
                    len(self.contact_email) > 254
                    or _CONTACT_EMAIL.fullmatch(self.contact_email) is None
                )
            )
        ):
            raise ConfigurationError("provider connection is invalid")
        if (
            self.adapter in {"google_books", "hardcover"}
            and self.secret_file is not None
        ):
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
        if (
            type(self.container_path) is not str
            or not self.container_path.startswith("/")
            or type(self.service_path) is not str
            or not self.service_path.startswith("/")
            or self.read_only is not True
        ):
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
    policy_override: dict[str, dict[str, str]] | None = None
    # The languages the owner says this library holds, first preferred
    # (#1315). Matching reads it as a prior; nothing writes it into an item.
    languages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _name(self.name, "library name")
        _name(self.connection, "library connection")
        if (
            type(self.library_id) is not str
            or not 1 <= len(self.library_id) <= 128
            or self.media_kind not in {"book", "ebook", "audiobook"}
        ):
            raise ConfigurationError("library is invalid")
        if self.mount is not None:
            _name(self.mount, "library mount")
        if (
            type(self.provider_connections) is not tuple
            or any(type(value) is not str for value in self.provider_connections)
            or self.provider_connections
            != tuple(sorted(set(self.provider_connections)))
        ):
            raise ConfigurationError(
                "library provider references are duplicated or invalid"
            )
        for value in self.provider_connections:
            _name(value, "library provider reference")
        if not library_languages_valid(self.languages):
            raise ConfigurationError("library languages are invalid")
        if self.policy_override is not None:
            if type(self.policy_override) is not dict:
                raise ConfigurationError("library policy override is invalid")
            try:
                resolve_policy(
                    MetadataPolicyProfile.builtin(), self.name, self.policy_override
                )
            except MetadataPolicyError as error:
                # The profile and the library name are already exact here, so
                # the override is the only value under test and
                # ``MetadataPolicyError`` is the only refusal ``resolve_policy``
                # raises. Anything else is a fault in the builtin profile or in
                # this validation, which is not an invalid override.
                raise ConfigurationError(
                    "library policy override is invalid"
                ) from error


@dataclass(frozen=True, slots=True)
class ReportLimits:
    target_response_bytes: int = 1_048_576
    provider_response_bytes: int = 65_536
    source_archive_bytes: int = 268_435_456
    # An audiobook is not an archive that must be read whole: the probe reads
    # its headers.  The ceiling exists to bound the digest, so it is the size
    # of the files people actually hold rather than the EPUB archive limit,
    # which would refuse a routine multi-hour recording outright.
    audio_source_bytes: int = 8_589_934_592
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if (
            type(self.target_response_bytes) is not int
            or not 1_024 <= self.target_response_bytes <= 4_194_304
            or type(self.provider_response_bytes) is not int
            or not 1_024 <= self.provider_response_bytes <= 262_144
            or type(self.source_archive_bytes) is not int
            or not 1_024 <= self.source_archive_bytes <= 268_435_456
            or type(self.audio_source_bytes) is not int
            or not 1_024 <= self.audio_source_bytes <= 17_179_869_184
            or type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 60
        ):
            raise ConfigurationError("report limits are invalid")


@dataclass(frozen=True, repr=False, slots=True)
class ApplicationConfiguration:
    connections: tuple[TargetConnectionConfiguration, ...]
    libraries: tuple[LibraryConfiguration, ...]
    mounts: tuple[MediaMountConfiguration, ...]
    providers: tuple[ProviderConnectionConfiguration, ...]
    limits: ReportLimits = ReportLimits()
    schema_version: str = "config-v11"
    provider_recovery_generation: int = 1
    review: ReviewWebConfiguration | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "config-v11":
            raise ConfigurationError("configuration schema is unsupported")
        # Recovery is deliberately tied to an operator-visible configuration
        # generation.  There is no file watcher, hash, or environment override:
        # changing credentials or provider configuration requires an increment
        # and process restart before a dormant provider may be considered again.
        if (
            type(self.provider_recovery_generation) is not int
            or self.provider_recovery_generation < 1
        ):
            raise ConfigurationError("provider recovery generation is invalid")
        if self.review is not None and type(self.review) is not ReviewWebConfiguration:
            raise ConfigurationError("review web configuration is invalid")
        typed = (
            (self.connections, TargetConnectionConfiguration, "target connections"),
            (self.libraries, LibraryConfiguration, "libraries"),
            (self.mounts, MediaMountConfiguration, "media mounts"),
            (self.providers, ProviderConnectionConfiguration, "provider connections"),
        )
        for values, expected, label in typed:
            if type(values) is not tuple or any(
                type(value) is not expected for value in values
            ):
                raise ConfigurationError(f"{label} are invalid")
            names = tuple(value.name for value in values)
            if names != tuple(sorted(set(names))):
                raise ConfigurationError(f"{label} are not in canonical unique order")
        connection_names = {value.name for value in self.connections}
        mount_names = {value.name for value in self.mounts}
        provider_names = {value.name for value in self.providers}
        for library in self.libraries:
            if (
                library.connection not in connection_names
                or (library.mount is not None and library.mount not in mount_names)
                or not set(library.provider_connections).issubset(provider_names)
            ):
                raise ConfigurationError("library references are invalid")

    def __repr__(self) -> str:
        return "ApplicationConfiguration(<redacted>)"

    def select(
        self, connection: str, library: str
    ) -> tuple[
        TargetConnectionConfiguration,
        LibraryConfiguration,
        MediaMountConfiguration | None,
        tuple[ProviderConnectionConfiguration, ...],
    ]:
        matches = tuple(
            value
            for value in self.libraries
            if value.name == library and value.connection == connection
        )
        if len(matches) != 1:
            raise ConfigurationError("requested connection or library is unavailable")
        selected = matches[0]
        target = next(value for value in self.connections if value.name == connection)
        mount = (
            None
            if selected.mount is None
            else next(value for value in self.mounts if value.name == selected.mount)
        )
        providers_by_name = {value.name: value for value in self.providers}
        providers = tuple(
            providers_by_name[name] for name in sorted(selected.provider_connections)
        )
        return target, selected, mount, providers


def audiobook_marketplaces(
    providers: Sequence[ProviderConnectionConfiguration],
    languages: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Read the one marketplace order every ASIN lookup in this build walks.

    `marketplaces` is configurable on the enabled Audible catalog and on
    nothing else, but an ASIN is marketplace-scoped for Audnexus in exactly the
    same way and it has no configuration surface of its own. Both read the
    owner's one stated order here rather than each inventing a default.

    A library's declared languages move the marketplaces in its first
    language to the front, then those in its second, and so on, each in the
    owner's order (#1315). The title search asks the first marketplace, so a
    German library searches Audible.de. Nothing is added or removed.
    """
    stated = next(
        (
            value.marketplaces
            for value in providers
            if value.adapter == "audible_catalog"
            and value.enabled
            and value.marketplaces
        ),
        ("us",),
    )
    # Each marketplace ranks by the first declared language whose catalogue
    # it carries; the sort is stable, so ties keep the owner's order.
    rank: dict[str, int] = {}
    for position, language in enumerate(languages):
        identity = language_identity(language)
        if identity is not None:
            for code, bases in AUDIOBOOK_MARKETPLACE_LANGUAGES.items():
                if identity.base in bases:
                    rank.setdefault(code, position)
    return tuple(sorted(stated, key=lambda code: rank.get(code, len(languages))))


__all__ = [
    "AUDIOBOOK_DISPATCH_ADAPTERS",
    "AUDIOBOOK_PROVIDER_ADAPTERS",
    "EBOOK_DISPATCH_ADAPTERS",
    "EBOOK_PROVIDER_ADAPTERS",
    "ApplicationConfiguration",
    "ConfigurationError",
    "LibraryConfiguration",
    "MediaMountConfiguration",
    "ProviderConnectionConfiguration",
    "ReportLimits",
    "TargetConnectionConfiguration",
    "audiobook_marketplaces",
    "dispatch_adapters",
    "recovery_adapters",
]
