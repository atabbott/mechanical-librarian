import json
import os
from pathlib import Path
from typing import Any, get_args

import pytest

import mechanical_librarian.interfaces.web
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_PROVIDER_ADAPTERS,
    ConfigurationError,
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.cover_consideration import (
    _COVER_EVIDENCE_STATEMENTS,
    _UNNAMED_COVER_EVIDENCE_STATEMENT,
)
from mechanical_librarian.application.owner_settings import file_settings_projection
from mechanical_librarian.application.review_web import (
    _choice_source_label,
    _provider_label,
)
from mechanical_librarian.domain.field_outcomes import (
    _ISO_639_ALIASES,
    MAX_LIBRARY_LANGUAGES,
    library_languages_valid,
)
from mechanical_librarian.domain.metadata_report import AUDIOBOOK_MARKETPLACES
from mechanical_librarian.domain.owner_settings import (
    PROVIDER_LABELS,
    AudibleCatalogProviderSettings,
    Marketplace,
    OwnerSettingsDocument,
    ProviderAdapter,
    SettingsView,
    provider_capabilities,
)
from mechanical_librarian.domain.validation import OWNER_PASSWORD_MINIMUM_CHARACTERS
from mechanical_librarian.infrastructure.settings_authority import (
    ReviewBootstrap,
    load_review_bootstrap,
    resolve_startup_configuration,
    store_runtime_configuration,
)


def test_absent_file_selects_store(tmp_path: Path) -> None:
    assert (
        resolve_startup_configuration(tmp_path / "config.json", lambda: "store")
        == "store"
    )


def test_invalid_present_file_does_not_select_store(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        resolve_startup_configuration(path, lambda: "store")


def test_bootstrap_is_strict_private_and_closed(tmp_path: Path) -> None:
    path = tmp_path / "review-bootstrap.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "review-bootstrap-v2",
                "canonical_origin": "https://review.example.invalid",
                "tls_certificate_file": "/run/secrets/tls.crt",
                "tls_private_key_file": "/run/secrets/tls.key",
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    with pytest.raises(ConfigurationError, match="bootstrap"):
        load_review_bootstrap(path)


def test_store_projection_combines_only_deploy_bootstrap_and_store_authority() -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "providers": [
                {
                    "name": "google",
                    "adapter": "google_books",
                    "enabled": True,
                },
                {
                    "name": "hardcover",
                    "adapter": "hardcover",
                    "enabled": True,
                },
            ],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                    "mount": "books",
                    "provider_connections": ["google", "hardcover"],
                    "languages": ["de", "en"],
                }
            ],
        }
    )
    settings = SettingsView(
        document=document,
        fields=(),
        secrets=(),
        authority="store",
    )
    bootstrap = ReviewBootstrap.model_validate(
        {
            "schema_version": "review-bootstrap-v2",
            "canonical_origin": "https://review.example.invalid",
            "tls_certificate_file": "/run/secrets/tls.crt",
            "tls_private_key_file": "/run/secrets/tls.key",
            "mounts": [
                {
                    "name": "books",
                    "container_path": "/media/books",
                    "service_path": "/library/books",
                    "read_only": True,
                }
            ],
        }
    )

    configuration = store_runtime_configuration(settings, bootstrap)
    assert configuration.review is not None
    assert (
        configuration.review.cover_reviewer_profile_file
        == "/opt/mechanical-librarian/reviewer/profile.json"
    )
    override = bootstrap.model_copy(
        update={"cover_reviewer_profile_file": "/deployment/override.json"}
    )
    explicit = store_runtime_configuration(settings, override)
    assert explicit.review is not None
    assert explicit.review.cover_reviewer_profile_file == "/deployment/override.json"

    assert configuration.connections[0].endpoint == "https://library.example.invalid"
    assert configuration.connections[0].secret_file is None
    assert all(provider.secret_file is None for provider in configuration.providers)
    assert ".store-credential" not in repr(configuration)
    assert configuration.mounts[0].container_path == "/media/books"
    assert configuration.review is not None
    assert configuration.review.canonical_origin == "https://review.example.invalid"
    # Matching reads the store's list exactly as it reads the file's, and a
    # file-managed deployment shows its own list back read-only.
    assert configuration.libraries[0].languages == ("de", "en")
    assert file_settings_projection(configuration).document.libraries[0].languages == [
        "de",
        "en",
    ]


def test_settings_projects_absent_fixed_provider_capabilities() -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "books",
                    "media_kind": "book",
                }
            ],
        }
    )
    assert (
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "google_books",
        "hardcover",
        "itunes_search",
        "inventaire",
        "open_library",
    ) == tuple(item.name for item in provider_capabilities(document))
    assert {item.credential for item in provider_capabilities(document)} == {
        "none",
        "contact_email",
        "api_key",
    }

    configured_value = document.model_dump()
    configured_value["providers"] = [
        {"name": "catalogue", "adapter": "google_books", "enabled": True}
    ]
    configured = OwnerSettingsDocument.model_validate(configured_value)
    assert (
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "hardcover",
        "itunes_search",
        "inventaire",
        "open_library",
    ) == tuple(item.name for item in provider_capabilities(configured))


def _audiobook_document(**overrides: object) -> OwnerSettingsDocument:
    providers = overrides.pop(
        "providers",
        [
            {"name": "audible", "adapter": "audible_catalog", "enabled": True},
            {"name": "audiosilo", "adapter": "audiosilo", "enabled": True},
            {"name": "audnexus", "adapter": "audnexus", "enabled": True},
            {"name": "itunes", "adapter": "itunes_search", "enabled": True},
        ],
    )
    return OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
            "providers": providers,
            "libraries": [
                {
                    "name": "spoken",
                    "connection": "primary",
                    "library_id": "spoken",
                    "media_kind": "audiobook",
                }
            ],
            **overrides,
        }
    )


def test_an_enabled_audiobook_provider_needs_nothing_but_its_own_fields() -> None:
    document = _audiobook_document()

    assert [item.adapter for item in document.providers] == [
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "itunes_search",
    ]
    # ADR-0061 admits them keyless: enabling one asks for no credential and
    # creates no secret field to hold, require, or admit to a runtime.
    assert document.secret_fields() == frozenset({"connection.primary.key"})
    assert document.required_secret_fields() == frozenset({"connection.primary.key"})
    assert document.runtime_secret_fields() == frozenset({"connection.primary.key"})


@pytest.mark.parametrize("field", ["secret_file", "contact_email", "unexpected"])
def test_no_audiobook_provider_admits_a_credential_surface(field: str) -> None:
    with pytest.raises(ValueError):
        _audiobook_document(
            providers=[
                {
                    "name": "audible",
                    "adapter": "audible_catalog",
                    "enabled": True,
                    field: "value",
                }
            ]
        )


def test_only_the_audible_catalogue_states_a_marketplace_order() -> None:
    document = _audiobook_document()
    audible, *others = document.providers

    assert isinstance(audible, AudibleCatalogProviderSettings)
    assert audible.marketplaces == list(AUDIOBOOK_MARKETPLACES)
    assert not any(hasattr(item, "marketplaces") for item in others)
    with pytest.raises(ValueError):
        _audiobook_document(
            providers=[
                {
                    "name": "audible",
                    "adapter": "audible_catalog",
                    "enabled": True,
                    "marketplaces": ["us", "us"],
                }
            ]
        )
    with pytest.raises(ValueError):
        _audiobook_document(
            providers=[
                {
                    "name": "audible",
                    "adapter": "audible_catalog",
                    "enabled": True,
                    "marketplaces": [],
                }
            ]
        )


def test_only_audible_and_itunes_may_carry_a_cover_switch() -> None:
    for adapter in ("audible_catalog", "itunes_search"):
        document = _audiobook_document(
            providers=[
                {
                    "name": "audio",
                    "adapter": adapter,
                    "enabled": True,
                    "cover_enabled": True,
                }
            ]
        )
        assert document.providers[0].cover_enabled is True
    for adapter in ("audiosilo", "audnexus"):
        with pytest.raises(ValueError):
            _audiobook_document(
                providers=[
                    {
                        "name": "audio",
                        "adapter": adapter,
                        "enabled": True,
                        "cover_enabled": True,
                    }
                ]
            )


def _settings_vocabulary() -> dict[str, Any]:
    return json.loads(
        (
            Path(mechanical_librarian.interfaces.web.__file__).parent
            / "settings_vocabulary.json"
        ).read_text(encoding="utf-8")
    )


def test_only_audible_and_itunes_may_carry_a_cover_switch_in_the_browser() -> None:
    """The browser list is a subset of what the contract accepts, never more.

    Settings renders the switch from this list alone, so an adapter that
    reaches it without the contract accepting `cover_enabled=True` would offer
    the owner a control whose save is refused (#967).
    """
    offered = _settings_vocabulary()["cover_switch_provider_adapters"]
    assert offered == ["audible_catalog", "itunes_search"]

    for adapter in offered:
        document = _audiobook_document(
            providers=[
                {
                    "name": "audio",
                    "adapter": adapter,
                    "enabled": True,
                    "cover_enabled": True,
                }
            ]
        )
        assert document.providers[0].cover_enabled is True
    # The converse is deliberately not asserted: Hardcover, Open Library and
    # Inventaire accept the field and are still offered no control here.
    assert not set(offered) & {"hardcover", "open_library", "inventaire"}


@pytest.mark.parametrize("adapter", ["audible_catalog", "itunes_search"])
def test_a_disabled_audiobook_source_may_not_carry_cover_evidence(
    adapter: str,
) -> None:
    """Settings hides the control while the provider is off; this is why.

    Both authorities refuse the pair outright, so a value that reached the
    document past a stale draft, a hand-edited file, or a future surface is
    rejected rather than quietly consulted. This holds the claim in
    `docs/reference/configuration.md` that `cover_enabled` can never be true
    for a provider that is off (#967).
    """
    with pytest.raises(ValueError, match="settings document is invalid"):
        _audiobook_document(
            providers=[
                {
                    "name": "audio",
                    "adapter": adapter,
                    "enabled": False,
                    "cover_enabled": True,
                }
            ]
        )
    with pytest.raises(
        ConfigurationError, match="cover enablement requires provider enablement"
    ):
        ProviderConnectionConfiguration(
            name="audio",
            enabled=False,
            cover_enabled=True,
            secret_file=None,
            adapter=adapter,
        )
    # The same pair is accepted the moment the provider is enabled, so the
    # refusal is about enablement and nothing else. Only the enabled Audible
    # catalogue carries a marketplace order, and it must be non-empty.
    assert (
        ProviderConnectionConfiguration(
            name="audio",
            enabled=True,
            cover_enabled=True,
            secret_file=None,
            adapter=adapter,
            marketplaces=(
                AUDIOBOOK_MARKETPLACES if adapter == "audible_catalog" else ()
            ),
        ).cover_enabled
        is True
    )


def test_every_surface_states_one_canonical_provider_label() -> None:
    """One label table, every reader of it, and no leak into an identifier.

    The capability list, the browser vocabulary setup and Settings share, the
    review report's provider label, the choice-source label beside a proposed
    value and the cover-evidence source row all name a provider with the same
    word, and the closed adapter set is exactly what the table covers. The
    instance name stays the owner's own identifier (#967).

    Every reader now reads `PROVIDER_LABELS` directly rather than restating it,
    so this test proves one table rather than four agreeing copies; what it
    still holds is the fallback wording, which each surface owns.
    """
    assert set(PROVIDER_LABELS) == set(get_args(ProviderAdapter))
    assert _settings_vocabulary()["provider_labels"] == dict(PROVIDER_LABELS)

    document = _audiobook_document(providers=[])
    capabilities = provider_capabilities(document)
    assert {item.adapter: item.label for item in capabilities} == dict(PROVIDER_LABELS)
    assert {item.adapter: item.name for item in capabilities} == {
        adapter: adapter for adapter in PROVIDER_LABELS
    }
    for adapter, label in PROVIDER_LABELS.items():
        assert _provider_label(adapter) == label
        assert (
            _choice_source_label({"provider": adapter, "source": "provider"}) == label
        )
    assert _provider_label(None) == "Provider (name unavailable)"
    assert _provider_label("unknown_adapter") == "Provider (name unavailable)"
    assert _choice_source_label({"provider": None, "source": "local"}) == (
        "Local library record"
    )
    assert _choice_source_label({"provider": None, "source": "provider"}) == (
        "Retained observation (provider name unavailable)"
    )
    cover_sources = {label for label, _, _ in _COVER_EVIDENCE_STATEMENTS.values()}
    assert {
        PROVIDER_LABELS["audible_catalog"],
        PROVIDER_LABELS["hardcover"],
        PROVIDER_LABELS["inventaire"],
        PROVIDER_LABELS["itunes_search"],
        PROVIDER_LABELS["open_library"],
    } < cover_sources
    assert "Provider evidence" == _UNNAMED_COVER_EVIDENCE_STATEMENT[0]


def test_store_authority_carries_the_order_only_while_audible_is_enabled() -> None:
    bootstrap = ReviewBootstrap.model_validate(
        {
            "schema_version": "review-bootstrap-v2",
            "canonical_origin": "https://review.example.invalid",
            "tls_certificate_file": "/run/secrets/tls.crt",
            "tls_private_key_file": "/run/secrets/tls.key",
        }
    )
    view = SettingsView(
        document=_audiobook_document(
            providers=[
                {
                    "name": "audible",
                    "adapter": "audible_catalog",
                    "enabled": True,
                    "marketplaces": ["uk", "us"],
                },
                {"name": "audnexus", "adapter": "audnexus", "enabled": False},
            ]
        ),
        fields=(),
        secrets=(),
        authority="store",
    )

    configuration = store_runtime_configuration(view, bootstrap)

    audible, audnexus = configuration.providers
    assert audible.marketplaces == ("uk", "us")
    assert audnexus.marketplaces == ()
    assert all(item.secret_file is None for item in configuration.providers)

    disabled = view.model_copy(
        update={
            "document": _audiobook_document(
                providers=[
                    {
                        "name": "audible",
                        "adapter": "audible_catalog",
                        "enabled": False,
                        "marketplaces": ["uk", "us"],
                    }
                ]
            )
        }
    )
    # The document keeps the recorded order; a disabled catalogue resolves no
    # ASIN, so the runtime value states none.
    assert (
        store_runtime_configuration(disabled, bootstrap).providers[0].marketplaces == ()
    )


def test_the_settings_marketplace_vocabulary_stays_one_closed_set() -> None:
    """Three restatements of one order, held together because none can import
    the others: the domain names it, the settings contract encodes it as a
    JSON-schema enum, and the browser vocabulary ships it to an added entry."""

    vocabulary = _settings_vocabulary()

    assert get_args(Marketplace) == AUDIOBOOK_MARKETPLACES
    assert tuple(vocabulary["audible_marketplace_order"]) == AUDIOBOOK_MARKETPLACES
    assert frozenset(
        vocabulary["keyless_provider_adapters"]
    ) == AUDIOBOOK_PROVIDER_ADAPTERS | {"inventaire"}
    assert not set(vocabulary["keyless_provider_adapters"]) & set(
        vocabulary["required_provider_keys"]
    )


def test_the_settings_language_picker_offers_only_what_the_domain_accepts() -> None:
    """Settings offers each favoured ISO 639 base the domain admits as a
    library language and nothing else, so no offered choice is refused on
    save (#1466); the limit is the domain's own."""

    vocabulary = _settings_vocabulary()["library_languages"]

    assert vocabulary["maximum"] == MAX_LIBRARY_LANGUAGES
    assert vocabulary["codes"] == sorted(
        set(_ISO_639_ALIASES.values()) - {"und", "mul", "mis", "zxx"}
    )
    assert all(library_languages_valid((code,)) for code in vocabulary["codes"])


def test_the_first_run_vocabulary_states_owner_rules_and_every_contribution() -> None:
    """The wizard states its password rule and each provider's relevance from
    the one shared vocabulary, so no copy is authored twice (#1008).

    The owner-password bound is the server's own rule in
    `domain.validation.owner_password`, a character minimum and a byte
    ceiling; the contribution map must cover exactly the closed adapter set,
    because the providers step reads it by adapter and a missing entry would
    render `undefined` beside a named provider.
    """
    vocabulary = _settings_vocabulary()

    assert vocabulary["owner_password"] == {
        "minimum_characters": OWNER_PASSWORD_MINIMUM_CHARACTERS,
        "maximum_bytes": 1024,
    }
    assert vocabulary["errors"]["owner_password"] == "Use at least 12 characters."
    assert vocabulary["errors"]["owner_password_long"].startswith(
        "That password is too long."
    )
    assert set(vocabulary["provider_contributions"]) == set(PROVIDER_LABELS)
    assert all(
        value.strip() == value and value.endswith(".")
        for value in vocabulary["provider_contributions"].values()
    )


@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
def test_bootstrap_accepts_exact_browser_modes(tmp_path: Path, mode: str) -> None:
    source = {
        "schema_version": "review-bootstrap-v2",
        "canonical_origin": "http://review.test"
        if mode == "http"
        else "https://review.test",
        "tls_certificate_file": "/synthetic/review.crt" if mode == "direct" else None,
        "tls_private_key_file": "/synthetic/review.key" if mode == "direct" else None,
        "trusted_proxy_ips": ["127.0.0.1"] if mode == "proxy" else [],
    }
    path = tmp_path / "bootstrap.json"
    path.write_text(json.dumps(source))
    path.chmod(0o600)
    value = load_review_bootstrap(path)
    assert value.model_dump()["canonical_origin"] == source["canonical_origin"]
    assert value.tls_certificate_file == source["tls_certificate_file"]


@pytest.mark.parametrize(
    "amendment",
    [
        {"schema_version": "review-bootstrap-v99"},
        {"canonical_origin": "http://review.test"},
        {"tls_certificate_file": None},
        {"tls_certificate_file": None, "tls_private_key_file": None},
        {"trusted_proxy_ips": ["127.0.0.1", "127.0.0.1"]},
        {"trusted_proxy_ips": ["127.0.0.0/8"]},
        {"allow_http": True},
        {},
    ],
)
def test_bootstrap_predecessor_and_invalid_modes_fail_closed(
    tmp_path: Path, amendment: dict[str, object]
) -> None:
    source = (
        Path(__file__).parents[1]
        / "support/configuration_predecessors/review-bootstrap-v1.json"
    )
    document = json.loads(source.read_bytes())
    if amendment:
        document.update({"schema_version": "review-bootstrap-v2", **amendment})
    path = tmp_path / "bootstrap.json"
    payload = json.dumps(document).encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    with pytest.raises(ConfigurationError):
        load_review_bootstrap(path)
    assert path.read_bytes() == payload
    assert list(tmp_path.iterdir()) == [path]
