import json
import os
from pathlib import Path
from typing import get_args

import pytest

import mechanical_librarian.interfaces.web
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_PROVIDER_ADAPTERS,
    ConfigurationError,
)
from mechanical_librarian.domain.metadata_report import AUDIOBOOK_MARKETPLACES
from mechanical_librarian.domain.owner_settings import (
    AudibleCatalogProviderSettings,
    Marketplace,
    OwnerSettingsDocument,
    SettingsView,
    provider_capabilities,
)
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
                "schema_version": "review-bootstrap-v1",
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
            "schema_version": "owner-settings-v2",
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
            "schema_version": "review-bootstrap-v1",
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

    assert configuration.connections[0].endpoint == "https://library.example.invalid"
    assert configuration.connections[0].secret_file is None
    assert all(provider.secret_file is None for provider in configuration.providers)
    assert ".store-credential" not in repr(configuration)
    assert configuration.mounts[0].container_path == "/media/books"
    assert configuration.review is not None
    assert configuration.review.canonical_origin == "https://review.example.invalid"


def test_settings_projects_absent_fixed_provider_capabilities() -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v2",
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
            "schema_version": "owner-settings-v2",
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


def test_store_authority_carries_the_order_only_while_audible_is_enabled() -> None:
    bootstrap = ReviewBootstrap.model_validate(
        {
            "schema_version": "review-bootstrap-v1",
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

    vocabulary = json.loads(
        (
            Path(mechanical_librarian.interfaces.web.__file__).parent
            / "settings_vocabulary.json"
        ).read_text(encoding="utf-8")
    )

    assert get_args(Marketplace) == AUDIOBOOK_MARKETPLACES
    assert tuple(vocabulary["audible_marketplace_order"]) == AUDIOBOOK_MARKETPLACES
    assert (
        frozenset(vocabulary["keyless_provider_adapters"])
        == AUDIOBOOK_PROVIDER_ADAPTERS
    )
    assert not set(vocabulary["keyless_provider_adapters"]) & set(
        vocabulary["required_provider_keys"]
    )
