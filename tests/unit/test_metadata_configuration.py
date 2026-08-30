from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import cast, get_args
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from mechanical_librarian.application.configuration import (
    ConfigurationError,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import CredentialError
from mechanical_librarian.domain.audio_inspection import AudioInspectionLimits
from mechanical_librarian.domain.metadata_report import AUDIOBOOK_MARKETPLACES
from mechanical_librarian.domain.owner_settings import SecretWrite
from mechanical_librarian.domain.validation import credential
from mechanical_librarian.infrastructure.configuration import (
    ConfigurationInput,
    Marketplace,
    configuration_schema_json,
    load_configuration_file,
)
from mechanical_librarian.infrastructure.security.credentials import (
    hardcover_client,
    read_secret,
    target_client,
)

# `ConfigurationInput.model_validate` and `load_configuration_file` never open a
# declared secret; the secrets named below are absolute paths that do not exist,
# exactly as the temporary-directory paths they replace were.
_SYNTHETIC_ROOT = Path("/synthetic")
_SYNTHETIC_SECRET = str(_SYNTHETIC_ROOT / "provider.secret")


def _source(root: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v7",
        "provider_recovery_generation": 1,
        "connections": [
            {
                "name": "secondary",
                "adapter": "audiobookshelf",
                "endpoint": "https://secondary.invalid",
                "secret_file": str(root / "secondary.secret"),
            },
            {
                "name": "primary",
                "adapter": "audiobookshelf",
                "endpoint": "https://primary.invalid",
                "secret_file": str(root / "primary.secret"),
            },
        ],
        "libraries": [
            {
                "name": "books",
                "connection": "primary",
                "library_id": "library-a",
                "mount": "books",
                "provider_connections": ["second", "first"],
            },
            {
                "name": "other",
                "connection": "secondary",
                "library_id": "library-b",
                "mount": "other",
                "provider_connections": ["first"],
            },
        ],
        "mounts": [
            {
                "name": "other",
                "container_path": "/media/other",
                "service_path": "/other",
                "read_only": True,
            },
            {
                "name": "books",
                "container_path": "/media/books",
                "service_path": "/books",
                "read_only": True,
            },
        ],
        "providers": [
            {
                "name": "second",
                "adapter": "hardcover",
                "enabled": False,
                "cover_enabled": False,
                "secret_file": None,
            },
            {
                "name": "first",
                "adapter": "hardcover",
                "enabled": False,
                "cover_enabled": False,
                "secret_file": None,
            },
        ],
    }


_AUDIO_ADAPTERS = ("audible_catalog", "audnexus", "audiosilo", "itunes_search")


def _audio_provider(adapter: str, *, enabled: bool = True) -> dict[str, object]:
    return {
        "name": adapter.replace("_", "-"),
        "adapter": adapter,
        "enabled": enabled,
        "cover_enabled": False,
        "secret_file": None,
    }


def _with_providers(providers: list[dict[str, object]]) -> dict[str, object]:
    source = _source(_SYNTHETIC_ROOT)
    source["providers"] = [*cast(list, source["providers"]), *providers]
    return source


def test_every_audiobook_adapter_is_admitted_keyless_in_both_states() -> None:
    """ADR-0061 admits four providers for which no key exists at all.

    A fresh install gets audiobook evidence with zero secrets, so the
    enabled and disabled forms of each are the whole surface an owner has.
    """
    source = _with_providers(
        [
            _audio_provider(adapter, enabled=enabled)
            for adapter in _AUDIO_ADAPTERS
            for enabled in (True, False)
        ]
    )
    source["providers"] = sorted(
        cast(list, source["providers"]),
        key=lambda item: (item["adapter"], item["enabled"]),
    )
    for index, item in enumerate(cast(list, source["providers"])):
        item["name"] = f"provider-{index:02d}"
    value = ConfigurationInput.model_validate(source)
    admitted = {
        (item.model_dump()["adapter"], item.model_dump()["enabled"])
        for item in value.providers
    }
    assert {
        (adapter, enabled) for adapter in _AUDIO_ADAPTERS for enabled in (True, False)
    } <= admitted
    audible = next(
        item.model_dump()
        for item in value.providers
        if item.model_dump()["adapter"] == "audible_catalog"
        and item.model_dump()["enabled"]
    )
    assert AUDIOBOOK_MARKETPLACES == tuple(audible["marketplaces"])
    assert audible["marketplaces"][0] == "us"


@pytest.mark.parametrize("adapter", _AUDIO_ADAPTERS)
@pytest.mark.parametrize(
    "credential_surface",
    [
        {"secret_file": _SYNTHETIC_SECRET},
        {"contact_email": "owner@example.invalid"},
        {"api_key": "synthetic"},
        {"token": "synthetic"},
    ],
)
def test_no_audiobook_adapter_accepts_a_credential_surface(
    adapter: str, credential_surface: dict[str, object]
) -> None:
    entry = {**_audio_provider(adapter), **credential_surface}
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(_with_providers([entry]))


@pytest.mark.parametrize("adapter", ("audible_catalog", "itunes_search"))
def test_the_two_cover_bearing_audiobook_adapters_carry_a_real_cover_switch(
    adapter: str,
) -> None:
    """Audible and iTunes state art of their own, so the switch is a bool."""
    for enabled in (True, False):
        entry = {**_audio_provider(adapter), "cover_enabled": enabled}
        value = ConfigurationInput.model_validate(_with_providers([entry]))
        admitted = next(
            item.model_dump()
            for item in value.providers
            if item.model_dump()["adapter"] == adapter
        )
        assert admitted["cover_enabled"] is enabled


@pytest.mark.parametrize("adapter", ("audible_catalog", "itunes_search"))
def test_a_cover_bearing_audiobook_adapter_acquires_covers_by_default(
    adapter: str,
) -> None:
    entry = _audio_provider(adapter)
    del entry["cover_enabled"]
    value = ConfigurationInput.model_validate(_with_providers([entry]))
    admitted = next(
        item.model_dump()
        for item in value.providers
        if item.model_dump()["adapter"] == adapter
    )
    assert admitted["cover_enabled"] is True


@pytest.mark.parametrize("adapter", ("audnexus", "audiosilo"))
def test_a_restating_audiobook_adapter_refuses_a_cover_surface(adapter: str) -> None:
    """Audnexus and AudioSilo restate Audible's asset, so they carry none."""
    entry = {**_audio_provider(adapter), "cover_enabled": True}
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(_with_providers([entry]))


@pytest.mark.parametrize("adapter", _AUDIO_ADAPTERS)
def test_a_disabled_audiobook_adapter_refuses_a_cover_surface(adapter: str) -> None:
    entry = {**_audio_provider(adapter, enabled=False), "cover_enabled": True}
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(_with_providers([entry]))


def test_marketplaces_belong_to_the_enabled_audible_catalog_and_nowhere_else() -> None:
    for adapter in _AUDIO_ADAPTERS[1:]:
        with pytest.raises(ValidationError):
            ConfigurationInput.model_validate(
                _with_providers([{**_audio_provider(adapter), "marketplaces": ["us"]}])
            )
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(
            _with_providers(
                [
                    {
                        **_audio_provider("audible_catalog", enabled=False),
                        "marketplaces": ["us"],
                    }
                ]
            )
        )
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(
            _with_providers(
                [{**_audio_provider("google_books"), "marketplaces": ["us"]}]
            )
        )


@pytest.mark.parametrize(
    "marketplaces", [["zz"], ["us", "zz"], [], ["US"], ["us", "us"], "us"]
)
def test_a_marketplace_outside_the_closed_set_is_refused(
    marketplaces: object,
) -> None:
    entry = {**_audio_provider("audible_catalog"), "marketplaces": marketplaces}
    source = _with_providers([entry])
    if marketplaces == ["us", "us"]:
        # Pydantic admits the repetition; the domain value refuses it, because
        # a marketplace tried twice is a wasted request rather than a fallback.
        with pytest.raises(ConfigurationError):
            ProviderConnectionConfiguration(
                "audible-catalog",
                True,
                False,
                None,
                "audible_catalog",
                None,
                ("us", "us"),
            )
        return
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(source)


def test_the_boundary_marketplace_set_is_the_one_the_domain_names() -> None:
    assert AUDIOBOOK_MARKETPLACES == get_args(Marketplace)


def test_a_loaded_audible_catalog_carries_its_marketplaces_as_a_tuple(
    tmp_path: Path,
) -> None:
    source = _with_providers([_audio_provider("audible_catalog")])
    for library in cast(list, source["libraries"]):
        library["provider_connections"] = [
            *library["provider_connections"],
            "audible-catalog",
        ]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    configuration = load_configuration_file(path)
    audible = next(
        value for value in configuration.providers if value.adapter == "audible_catalog"
    )
    assert AUDIOBOOK_MARKETPLACES == audible.marketplaces
    assert audible.secret_file is None
    assert audible.contact_email is None
    assert audible.cover_enabled is False
    assert all(
        value.marketplaces == ()
        for value in configuration.providers
        if value.adapter != "audible_catalog"
    )


def _review(root: Path) -> dict[str, object]:
    return {
        "canonical_origin": "https://review.example.invalid",
        "owner_password_hash_file": str(root / "owner.hash"),
        "tls_certificate_file": str(root / "review.crt"),
        "tls_private_key_file": str(root / "review.key"),
    }


def test_owner_session_lifetime_defaults_to_thirty_days_and_rejects_noncanonical_values(
    tmp_path: Path,
) -> None:
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.example.invalid",
        owner_password_hash_file=str(tmp_path / "owner.hash"),
    )
    assert configuration.session_lifetime_seconds == 2_592_000
    for value in (299, 2_592_001, True, 2_592_000.0):
        with pytest.raises(ConfigurationError):
            ReviewWebConfiguration(
                canonical_origin="https://review.example.invalid",
                owner_password_hash_file=str(tmp_path / "owner.hash"),
                session_lifetime_seconds=cast(int, value),
            )

    for value in (300, 2_592_000):
        assert (
            ReviewWebConfiguration(
                canonical_origin="https://review.example.invalid",
                owner_password_hash_file=str(tmp_path / "owner.hash"),
                session_lifetime_seconds=value,
            ).session_lifetime_seconds
            == value
        )
        source = _source(tmp_path)
        source["review"] = {**_review(tmp_path), "session_lifetime_seconds": value}
        path = tmp_path / f"config-{value}.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        loaded = load_configuration_file(path)
        assert loaded.review is not None
        assert loaded.review.session_lifetime_seconds == value

    for value in (299, 2_592_001, True, 2_592_000.0):
        source = _source(tmp_path)
        source["review"] = {**_review(tmp_path), "session_lifetime_seconds": value}
        path = tmp_path / "invalid-session-lifetime.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(path)


def test_review_tls_paths_are_explicit_absolute_and_paired() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source["review"] = _review(root)
        path = root / "config.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        review = load_configuration_file(path).review
        assert review is not None
        assert review.tls_certificate_file == str(root / "review.crt")
        assert review.tls_private_key_file == str(root / "review.key")


@pytest.mark.parametrize("field", ("tls_certificate_file", "tls_private_key_file"))
def test_review_tls_paths_are_absolute_in_generated_schema(field: str) -> None:
    properties = json.loads(configuration_schema_json())["$defs"]["ReviewInput"][
        "properties"
    ]
    absolute = next(
        choice
        for choice in properties[field]["anyOf"]
        if choice.get("type") == "string"
    )
    assert absolute["pattern"] == "^/"


@pytest.mark.parametrize(
    "fields",
    (
        {"tls_private_key_file": None},
        {"tls_certificate_file": None},
        {"tls_certificate_file": "relative.crt"},
        {"tls_private_key_file": "relative.key"},
    ),
    ids=(
        "unpaired_certificate",
        "unpaired_private_key",
        "relative_certificate",
        "relative_private_key",
    ),
)
def test_unpaired_or_relative_review_tls_paths_are_rejected(
    fields: dict[str, object],
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        invalid = _source(root)
        invalid["review"] = {**_review(root), **fields}
        candidate = root / "invalid-review.json"
        candidate.write_text(json.dumps(invalid), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(candidate)


def test_multiple_named_scopes_and_provider_order_are_explicit_and_canonical() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "config.json"
        path.write_text(json.dumps(_source(root)), encoding="utf-8")
        configuration = load_configuration_file(path)
    connection, library, mount, providers = configuration.select("primary", "books")
    assert connection.name == "primary"
    assert library.library_id == "library-a"
    assert library.provider_connections == ("first", "second")
    assert cast(MediaMountConfiguration, mount).name == "books"
    assert [value.name for value in providers] == ["first", "second"]


def test_sparse_read_only_policy_override_is_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source["libraries"][0]["policy_override"] = {
            "options": {"date_precision": "full_date"},
            "destinations": {"tags": "ignore"},
        }
        path = root / "config.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        configuration = load_configuration_file(path)
        assert (
            "full_date"
            == configuration.libraries[0].policy_override["options"]["date_precision"]
        )
        source["libraries"][0]["policy_override"]["options"] = {"removed": "value"}
        path.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(path)


def test_policy_override_validation_absorbs_only_the_policy_refusal() -> None:
    """A rejected override is a configuration problem; a broken resolver is not.

    The profile and the library name are already exact at this call, so the
    override is the only value under test and `MetadataPolicyError` is the
    only refusal `resolve_policy` raises. Catching `Exception` also blamed
    the owner's configuration file for a fault in the builtin profile or in
    the resolution itself, which no edit to that file can clear (#698).
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source["libraries"][0]["policy_override"] = {
            "options": {"date_precision": "not_an_option"},
            "destinations": {},
        }
        path = root / "config.json"
        path.write_text(json.dumps(source), encoding="utf-8")

        with pytest.raises(ConfigurationError):
            load_configuration_file(path)

        source["libraries"][0]["policy_override"]["options"] = {}
        path.write_text(json.dumps(source), encoding="utf-8")
        fault = RuntimeError("policy resolution is broken")
        with patch(
            "mechanical_librarian.application.configuration.resolve_policy",
            side_effect=fault,
        ):
            with pytest.raises(RuntimeError) as surfaced:
                load_configuration_file(path)
        assert surfaced.value is fault


def _duplicate_provider_reference(source: dict[str, object]) -> None:
    source["libraries"][0]["provider_connections"] = ["first", "first"]


def _unknown_top_level_field(source: dict[str, object]) -> None:
    source["default_database"] = "state.sqlite"


def _original_schema_version(source: dict[str, object]) -> None:
    source["schema_version"] = "config-v1"


def _previous_schema_version(source: dict[str, object]) -> None:
    source["schema_version"] = "config-v3"


def _enabled_provider_without_a_secret(source: dict[str, object]) -> None:
    source["providers"][0]["enabled"] = True


@pytest.mark.parametrize(
    "mutate",
    (
        _duplicate_provider_reference,
        _unknown_top_level_field,
        _original_schema_version,
        _previous_schema_version,
        _enabled_provider_without_a_secret,
    ),
    ids=(
        "duplicate_reference",
        "unknown_field",
        "schema_v1",
        "schema_v3",
        "enabled_provider_without_a_secret",
    ),
)
def test_duplicate_reference_unknown_field_and_implicit_old_schema_fail(mutate) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        mutate(source)
        path = root / "bad.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(path)


def test_multiple_referenced_cover_providers_share_the_bounded_gallery() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        for provider in source["providers"]:
            provider["enabled"] = True
            provider["cover_enabled"] = True
            provider["secret_file"] = str(root / f"{provider['name']}.secret")
        path = root / "config.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        configuration = load_configuration_file(path)
    assert (True, True) == tuple(
        value.cover_enabled for value in configuration.providers
    )


def test_generated_schema_is_current() -> None:
    expected = (
        Path(__file__).parents[2] / "docs" / "reference" / "config-v7.schema.json"
    ).read_text(encoding="utf-8")
    assert configuration_schema_json() == expected


def test_provider_recovery_generation_is_required_and_positive() -> None:
    source = _source(Path("/synthetic"))
    source.pop("provider_recovery_generation")
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(source)
    source["provider_recovery_generation"] = 0
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(source)


def test_generated_schema_enforces_provider_variants() -> None:
    schema = json.loads(configuration_schema_json())["$defs"]
    assert {
        "DisabledAudibleCatalogProviderInput",
        "DisabledAudioSiloProviderInput",
        "DisabledAudnexusProviderInput",
        "DisabledGoogleBooksProviderInput",
        "DisabledHardcoverProviderInput",
        "DisabledItunesSearchProviderInput",
        "DisabledOpenLibraryProviderInput",
        "EnabledAudibleCatalogProviderInput",
        "EnabledAudioSiloProviderInput",
        "EnabledAudnexusProviderInput",
        "EnabledGoogleBooksProviderInput",
        "EnabledHardcoverProviderInput",
        "EnabledItunesSearchProviderInput",
        "EnabledOpenLibraryProviderInput",
    } == {
        value["$ref"].rsplit("/", 1)[-1] for value in schema["ProviderInput"]["anyOf"]
    }


@pytest.mark.parametrize(
    "provider",
    (
        {
            "name": "provider",
            "adapter": "hardcover",
            "enabled": True,
            "cover_enabled": True,
            "secret_file": _SYNTHETIC_SECRET,
        },
        {
            "name": "provider",
            "adapter": "hardcover",
            "enabled": False,
            "cover_enabled": False,
            "secret_file": None,
        },
        {
            "name": "provider",
            "adapter": "google_books",
            "enabled": True,
            "cover_enabled": False,
            "secret_file": None,
        },
        {
            "name": "provider",
            "adapter": "google_books",
            "enabled": True,
            "cover_enabled": False,
            "secret_file": _SYNTHETIC_SECRET,
        },
        {
            "name": "provider",
            "adapter": "google_books",
            "enabled": False,
            "cover_enabled": False,
            "secret_file": None,
        },
        {
            "name": "provider",
            "adapter": "open_library",
            "enabled": True,
            "cover_enabled": True,
            "secret_file": None,
            "contact_email": "operator@example.org",
        },
        {
            "name": "provider",
            "adapter": "open_library",
            "enabled": False,
            "cover_enabled": False,
            "secret_file": None,
        },
    ),
    ids=(
        "hardcover_enabled_cover",
        "hardcover_disabled",
        "google_books_enabled",
        "google_books_enabled_with_secret",
        "google_books_disabled",
        "open_library_enabled_cover",
        "open_library_disabled",
    ),
)
def test_generated_schema_admits_every_declared_provider_variant(
    provider: dict[str, object],
) -> None:
    source = _source(_SYNTHETIC_ROOT)
    source["providers"] = [provider]
    ConfigurationInput.model_validate(source)


@pytest.mark.parametrize(
    "changes",
    (
        {
            "adapter": "google_books",
            "enabled": False,
            "secret_file": _SYNTHETIC_SECRET,
        },
        {
            "adapter": "google_books",
            "enabled": True,
            "cover_enabled": True,
            "secret_file": None,
        },
        {
            "adapter": "open_library",
            "enabled": True,
            "secret_file": None,
        },
        {
            "adapter": "open_library",
            "enabled": True,
            "secret_file": _SYNTHETIC_SECRET,
            "contact_email": "operator@example.org",
        },
        {
            "adapter": "hardcover",
            "enabled": True,
            "secret_file": None,
        },
        {
            "adapter": "hardcover",
            "enabled": False,
            "cover_enabled": True,
            "secret_file": None,
        },
    ),
    ids=(
        "google_books_disabled_with_secret",
        "google_books_cover_without_secret",
        "open_library_without_contact",
        "open_library_with_secret",
        "hardcover_enabled_without_secret",
        "hardcover_disabled_with_cover",
    ),
)
def test_generated_schema_refuses_undeclared_provider_variants(
    changes: dict[str, object],
) -> None:
    source = _source(_SYNTHETIC_ROOT)
    source["providers"][0].update(changes)
    with pytest.raises(ValidationError):
        ConfigurationInput.model_validate(source)


def test_provider_adapters_have_closed_adapter_specific_fields() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        valid = _source(root)
        valid["providers"] = [
            {
                "name": "first",
                "adapter": "hardcover",
                "enabled": True,
                "cover_enabled": False,
                "secret_file": str(root / "hardcover.secret"),
            },
            {
                "name": "second",
                "adapter": "google_books",
                "enabled": True,
                "cover_enabled": False,
                "secret_file": None,
            },
            {
                "name": "third",
                "adapter": "open_library",
                "enabled": True,
                "cover_enabled": True,
                "secret_file": None,
                "contact_email": "operator@example.org",
            },
        ]
        path = root / "valid.json"
        path.write_text(json.dumps(valid), encoding="utf-8")
        configuration = load_configuration_file(path)
        assert tuple(
            (value.name, value.adapter) for value in configuration.providers
        ) == (
            ("first", "hardcover"),
            ("second", "google_books"),
            ("third", "open_library"),
        )
        assert "operator@example.org" not in repr(configuration)


@pytest.mark.parametrize(
    ("adapter", "secret", "contact"),
    (
        ("open_library", _SYNTHETIC_SECRET, "operator@example.org"),
        ("open_library", None, None),
        ("open_library", None, "not-an-email"),
        ("open_library", None, ".operator@example.org"),
        ("open_library", None, "operator.@example.org"),
        ("open_library", None, "op..erator@example.org"),
        ("open_library", None, "a" * 65 + "@example.org"),
        ("hardcover", _SYNTHETIC_SECRET, "operator@example.org"),
        ("unknown", None, "operator@example.org"),
    ),
    ids=(
        "open_library_with_secret",
        "open_library_without_contact",
        "contact_is_not_an_email",
        "contact_leading_dot",
        "contact_trailing_dot",
        "contact_consecutive_dots",
        "contact_local_part_too_long",
        "hardcover_with_contact",
        "unknown_adapter",
    ),
)
def test_adapter_specific_provider_fields_are_refused_elsewhere(
    adapter: str, secret: str | None, contact: str | None
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source["providers"][0].update(
            {
                "adapter": adapter,
                "enabled": True,
                "secret_file": secret,
                "contact_email": contact,
            }
        )
        candidate = root / "invalid-provider.json"
        candidate.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(candidate)


@pytest.mark.parametrize("field", ("container_path", "service_path"))
def test_mount_paths_are_absolute_in_generated_schema_and_runtime(field: str) -> None:
    mount_properties = json.loads(configuration_schema_json())["$defs"]["MountInput"][
        "properties"
    ]
    assert mount_properties[field]["pattern"] == "^/"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source["mounts"][0][field] = "relative/path"
        path = root / f"relative-{field}.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(path)


@pytest.mark.parametrize(
    ("section", "changes"),
    (
        ("connections", {"secret_file": "target.secret"}),
        ("providers", {"enabled": True, "secret_file": "provider.secret"}),
    ),
    ids=("target_connection", "provider"),
)
def test_secret_paths_are_absolute_in_generated_schema_and_runtime(
    section: str, changes: dict[str, object]
) -> None:
    schema = json.loads(configuration_schema_json())["$defs"]
    assert schema["ConnectionInput"]["properties"]["secret_file"]["pattern"] == "^/"
    provider_secret = schema["EnabledHardcoverProviderInput"]["properties"][
        "secret_file"
    ]
    assert provider_secret["pattern"] == "^/"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _source(root)
        source[section][0].update(changes)
        path = root / "relative-secret.json"
        path.write_text(json.dumps(source), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_configuration_file(path)

    with pytest.raises(ConfigurationError):
        TargetConnectionConfiguration(
            "primary", "https://example.invalid", "target.secret"
        )
    with pytest.raises(ConfigurationError):
        ProviderConnectionConfiguration("catalogue", True, True, "provider.secret")


def test_private_regular_file_is_required() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        secret = root / "secret"
        secret.write_text("token-value", encoding="ascii")
        secret.chmod(0o600)
        assert read_secret(str(secret)) == "token-value"
        secret.chmod(0o644)
        with pytest.raises(CredentialError):
            read_secret(str(secret))
        link = root / "link"
        link.symlink_to(secret)
        with pytest.raises(CredentialError):
            read_secret(str(link))


@pytest.mark.parametrize(
    ("value", "accepted"),
    (
        ("x", True),
        ("x" * 2048, True),
        ("", False),
        ("x" * 2049, False),
        (" token", False),
        ("token ", False),
        ("Bearer-token", False),
        ("line\nbreak", False),
        ("é", False),
    ),
    ids=(
        "minimum",
        "maximum",
        "empty",
        "oversized",
        "leading-space",
        "trailing-space",
        "bearer-prefix",
        "newline",
        "non-ascii",
    ),
)
def test_private_reader_and_owner_settings_share_the_credential_contract(
    tmp_path: Path, value: str, accepted: bool
) -> None:
    secret = tmp_path / "credential"
    secret.write_bytes(value.encode("utf-8"))
    secret.chmod(0o600)

    assert credential(value) is accepted
    if accepted:
        assert read_secret(str(secret)) == value
        assert (
            SecretWrite(
                field_id="connection.primary.key", operation="set", value=value
            ).value
            == value
        )
    else:
        with pytest.raises(CredentialError, match="credential unavailable"):
            read_secret(str(secret))
        with pytest.raises(ValidationError):
            SecretWrite(field_id="connection.primary.key", operation="set", value=value)


def test_target_authorization_is_confined_to_current_item_get() -> None:
    with tempfile.TemporaryDirectory() as directory:
        secret = Path(directory) / "secret"
        secret.write_text("token-value", encoding="ascii")
        secret.chmod(0o600)
        requests: list[httpx.Request] = []
        transport = httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the request and still yields the response.
        )
        client = target_client("https://library.invalid", str(secret), 5)
        # Replace only the transport for this structural authorization test.
        client._transport = transport
        client.get("https://library.invalid/api/items/item-1")
        client.post("https://library.invalid/api/items/item-1")
        client.get("https://other.invalid/api/items/item-1")
        client.close()
    assert requests[0].headers.get("authorization") == "Bearer token-value"
    assert requests[1].headers.get("authorization") is None
    assert requests[2].headers.get("authorization") is None


def test_hardcover_raw_token_is_confined_to_the_exact_graphql_post() -> None:
    token = "synthetic-provider-token"
    with tempfile.TemporaryDirectory() as directory:
        secret = Path(directory) / "secret"
        secret.write_text(token, encoding="ascii")
        secret.chmod(0o600)
        requests: list[httpx.Request] = []
        client = hardcover_client(
            str(secret),
            5,
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the request and still yields the response.
                    or httpx.Response(200)
                )
            ),
        )
        assert token not in repr(client)
        client.post("https://api.hardcover.app/v1/graphql")
        client.get("https://api.hardcover.app/v1/graphql")
        client.post("https://other.invalid/v1/graphql")
        client.close()
    assert "Bearer synthetic-provider-token" == requests[0].headers.get("authorization")
    assert requests[1].headers.get("authorization") is None
    assert requests[2].headers.get("authorization") is None


def test_the_audio_ceiling_is_a_default_a_config_document_need_not_carry() -> None:
    """No schema increment: an older `config-v7` document still validates.

    `LimitsInput` is closed, so a key added to it would reject every document
    written before it existed had it not carried a default. Both shapes an
    owner can have on disk -- no `limits` block, and one written before this
    key -- are read here, and an explicit value still reaches the domain.
    """
    without_limits = ConfigurationInput.model_validate(_source(_SYNTHETIC_ROOT))
    without_key = ConfigurationInput.model_validate(
        {
            **_source(_SYNTHETIC_ROOT),
            "limits": {"source_archive_bytes": 268_435_456, "timeout_seconds": 30},
        }
    )
    explicit = ConfigurationInput.model_validate(
        {
            **_source(_SYNTHETIC_ROOT),
            "limits": {"audio_source_bytes": 17_179_869_184},
        }
    )

    assert without_limits.limits.audio_source_bytes == 8_589_934_592
    assert without_key.limits.audio_source_bytes == 8_589_934_592
    assert explicit.limits.audio_source_bytes == 17_179_869_184
    # The immutable inspection cap is the ceiling of the configurable one.
    assert AudioInspectionLimits.HARD_CAPS["source_bytes"] == 17_179_869_184
    for value in (1_023, 17_179_869_185):
        with pytest.raises(ValidationError):
            ConfigurationInput.model_validate(
                {**_source(_SYNTHETIC_ROOT), "limits": {"audio_source_bytes": value}}
            )
