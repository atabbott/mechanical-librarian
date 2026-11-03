from __future__ import annotations

import copy
import functools
import unittest
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mechanical_librarian.application.batch import BatchError
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    GenerateMetadataReport,
    InspectionResult,
    MetadataReportError,
    ProviderBinding,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    classify_provider_recovery_failure,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetrySuccessorError,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.bootstrap.composition import (
    _load_batch_credentials,
    run_batch,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from tests.support.metadata_report_doubles import (
    FakeInspector,
    FakeReportRepository,
    FakeTarget,
    single_track_audio_facts,
)


def _audiobook_report() -> MetadataReport:
    """One real recording report whose providers admit no work identity.

    The receipt now asks the report itself whether any observation admitted
    this recording's work identity, so this case has to be a report rather
    than a stand-in for one (#966).
    """
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        audio=single_track_audio_facts(),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Current title",
                canonical="Current title",
                excerpt="Current title",
                applicability="not_applicable",
                basis="target_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        ),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


def test_review_receipt_keeps_complete_mixed_provider_report_for_proposal():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (LibraryConfiguration("books", "primary", "library", "book", "media", ()),),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (),
    )
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    action = SimpleNamespace(action_id="work")
    report = SimpleNamespace(
        report_id="report-current",
        file=SimpleNamespace(format="epub"),
        providers=(
            SimpleNamespace(
                provider="hardcover", status=ProviderStatus.CANDIDATES, lanes=()
            ),
            SimpleNamespace(
                provider="open-library", status=ProviderStatus.CANDIDATES, lanes=()
            ),
            SimpleNamespace(
                provider="google-books", status=ProviderStatus.AUTHENTICATION, lanes=()
            ),
        ),
    )
    repository = unittest.mock.Mock()
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
            review_action=action,
            pipeline_repository=repository,
        )
    receipt = generate.call_args.kwargs["publication_receipt"]
    receipt(repository, report)
    repository.complete_review_extraction_publication.assert_called_once_with(
        action, "report-current", unittest.mock.ANY, 0, None, False, None, False
    )


def test_review_receipt_ends_an_audiobook_report_at_its_local_evidence():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "audio", "primary", "library", "audiobook", "media", ()
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (),
    )
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    action = SimpleNamespace(action_id="work")
    report = _audiobook_report()
    repository = unittest.mock.Mock()
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "audio",
            "item",
            0,
            review_action=action,
            pipeline_repository=repository,
        )
    receipt = generate.call_args.kwargs["publication_receipt"]
    receipt(repository, report)
    repository.complete_review_extraction_publication.assert_called_once_with(
        action,
        report.report_id,
        unittest.mock.ANY,
        0,
        "evidence_only",
        False,
        None,
        # No provider admitted a recording, so the item is searched again.
        True,
    )


def test_google_books_keyless_keyed_and_disabled_composition():
    providers = (
        ProviderConnectionConfiguration("disabled", False, False, None, "google_books"),
        ProviderConnectionConfiguration(
            "keyed", True, False, "/google.secret", "google_books"
        ),
        ProviderConnectionConfiguration("keyless", True, False, None, "google_books"),
    )
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books",
                "primary",
                "library",
                "book",
                "media",
                ("disabled", "keyed", "keyless"),
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        providers,
    )
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    constructed = []
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(
            composition, "google_books_client", side_effect=(object(), object())
        ) as clients,
        patch.object(composition, "read_secret", return_value="private-key") as secret,
        patch.object(
            composition,
            "GoogleBooksMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append((args, kwargs)) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config, Path("/reports"), Path("/artifacts"), "primary", "books", "item", 0
        )
        bindings = generate.call_args.kwargs["providers"]
        assert (False, True, True) == tuple(binding.enabled for binding in bindings)
        for binding in bindings:
            if binding.enabled:
                binding.factory()
    assert 2 == clients.call_count
    secret.assert_called_once_with("/google.secret")
    assert ("private-key", None) == tuple(item[1]["api_key"] for item in constructed)


@pytest.mark.parametrize(
    "endpoint", ["https://example.invalid", "http://audiobookshelf:13378"]
)
def test_store_runtime_uses_only_injected_target_and_provider_credentials(endpoint):
    config = ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", endpoint, None),),
        (
            LibraryConfiguration(
                "books",
                "primary",
                "library",
                "book",
                "media",
                ("google", "hardcover"),
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration(
                "google", True, False, None, "google_books"
            ),
            ProviderConnectionConfiguration(
                "hardcover", True, False, None, "hardcover"
            ),
        ),
    )
    credentials = {
        "target:primary": "target-token",
        "provider:google": "google-token",
        "provider:hardcover": "hardcover-token",
    }
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader") as reader,
        patch.object(composition, "target_client") as target_file_client,
        patch.object(
            composition, "target_client_token", return_value=object()
        ) as target_token,
        patch.object(composition, "hardcover_client") as hardcover_file_client,
        patch.object(
            composition, "hardcover_client_token", return_value=object()
        ) as hardcover_token,
        patch.object(composition, "google_books_client", return_value=object()),
        patch.object(composition, "read_secret") as read_secret,
        patch.object(composition, "GoogleBooksMetadataProvider") as google_provider,
        patch.object(composition, "HardcoverMetadataProvider"),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
            credentials=credentials,
        )
        reader.call_args.kwargs["client_factory"]()
        for binding in generate.call_args.kwargs["providers"]:
            binding.factory()

    target_token.assert_called_once_with(endpoint, "target-token", 30)
    hardcover_token.assert_called_once_with("hardcover-token", 30)
    assert google_provider.call_args.kwargs["api_key"] == "google-token"
    target_file_client.assert_not_called()
    hardcover_file_client.assert_not_called()
    read_secret.assert_not_called()


def test_namespaced_equal_name_credentials():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "shared", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "shared", "library", "book", None, ("shared",)
            ),
        ),
        (),
        (ProviderConnectionConfiguration("shared", True, False, "/provider"),),
    )
    with patch(
        "mechanical_librarian.bootstrap.composition.read_secret",
        side_effect=lambda path: (
            "target-token" if path == "/target" else "provider-token"
        ),
    ):
        assert _load_batch_credentials(config, {"shared"}, {"shared"}) == {
            "target:shared": "target-token",
            "provider:shared": "provider-token",
        }


def test_provider_mismatch_precedes_secret_read():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (LibraryConfiguration("books", "primary", "library", "book", None, ()),),
        (),
        (),
    )
    value = {
        "id": "a" * 64,
        "providers": ["extra"],
        "entries": [
            {
                "connection": "primary",
                "library": "books",
                "library_id": "library",
                "item": "item",
                "file_identity": "a" * 64,
                "classification": "singular-supported",
                "eligible": True,
            }
        ],
        "batch_config_fingerprint": "b" * 64,
    }
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.read_batch_manifest",
            return_value=b"x",
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.parse_manifest",
            return_value=value,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=config,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.batch_fingerprint",
            return_value="b" * 64,
        ),
        patch("mechanical_librarian.bootstrap.composition.read_secret") as secret,
    ):
        with pytest.raises(BatchError):
            run_batch(
                __import__("pathlib").Path("/config"),
                __import__("pathlib").Path("/catalogue"),
                __import__("pathlib").Path("/manifest"),
                "a" * 64,
                __import__("pathlib").Path("/reports"),
                __import__("pathlib").Path("/artifacts"),
                lambda _: None,
            )
        secret.assert_not_called()


def test_foreground_report_shares_one_open_library_pacer():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", "media", ("first", "second")
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration(
                "first", True, False, None, "open_library", "first@example.org"
            ),
            ProviderConnectionConfiguration(
                "second", True, False, None, "open_library", "second@example.org"
            ),
        ),
    )
    shared = object()
    constructed = []
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(composition, "OpenLibraryPacer", return_value=shared) as pacer,
        patch.object(
            composition, "open_library_client", side_effect=(object(), object())
        ),
        patch.object(
            composition,
            "OpenLibraryMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append(kwargs["pacer"]) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config, Path("/reports"), Path("/artifacts"), "primary", "books", "item", 0
        )
        bindings = generate.call_args.kwargs["providers"]
        for binding in bindings:
            binding.factory()
    pacer.assert_called_once_with()
    assert [shared, shared] == constructed


def test_batch_shares_one_hardcover_pacer_across_entries():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", None, ("hardcover",)
            ),
        ),
        (),
        (
            ProviderConnectionConfiguration(
                "hardcover", True, False, "/provider", "hardcover"
            ),
        ),
    )
    entries = tuple(
        {
            "connection": "primary",
            "library": "books",
            "library_id": "library",
            "item": f"item-{index}",
            "file_identity": f"{index}" * 64,
            "classification": "singular-supported",
            "eligible": True,
        }
        for index in (1, 2)
    )
    value = {
        "id": "a" * 64,
        "providers": ["hardcover"],
        "provider_adapters": ["hardcover"],
        "entries": list(entries),
        "batch_config_fingerprint": "b" * 64,
        "catalogue_generation": 1,
        "catalogue_fingerprint": "c" * 64,
    }
    snapshot = SimpleNamespace(generation=1, fingerprint="c" * 64)
    catalogue = unittest.mock.Mock()
    catalogue.snapshot.return_value = snapshot
    artifacts = SimpleNamespace(identity=object())
    repository = unittest.mock.Mock()
    repository.identity = object()
    shared = object()
    loaded = unittest.mock.Mock(return_value="{}")
    report_value = SimpleNamespace(providers=(), report_id="report")

    def foreground(**kwargs):
        runner = unittest.mock.Mock()
        runner.run.side_effect = lambda: (
            [
                kwargs["report"](
                    SimpleNamespace(
                        connection="primary",
                        library="books",
                        item_id=f"item-{index}",
                        file_identity=f"{index}" * 64,
                    )
                )
                for index in (1, 2)
            ]
            and True
        )
        return runner

    with (
        patch.object(composition, "read_batch_manifest", return_value=b"x"),
        patch.object(composition, "parse_manifest", return_value=value),
        patch.object(composition, "load_configuration_file", return_value=config),
        patch.object(composition, "batch_fingerprint", return_value="b" * 64),
        patch.object(composition, "_batch_provider_names", return_value=("hardcover",)),
        patch.object(
            composition, "_batch_provider_adapters", return_value=("hardcover",)
        ),
        patch.object(composition, "_require_open_library_batch_envelope"),
        patch.object(composition, "open_catalogue", return_value=catalogue),
        patch.object(composition, "CoverArtifactStore", return_value=artifacts),
        patch.object(composition, "open_repository", return_value=repository),
        patch.object(composition, "_load_batch_credentials", return_value={}),
        patch.object(composition, "HardcoverPacer", return_value=shared) as pacer,
        patch.object(composition, "run_metadata_report_loaded", loaded),
        patch.object(composition, "ForegroundBatchRun", side_effect=foreground),
        patch(
            "mechanical_librarian.domain.metadata_report.MetadataReport.from_json",
            return_value=report_value,
        ),
    ):
        assert run_batch(
            Path("/config"),
            Path("/catalogue"),
            Path("/manifest"),
            "a" * 64,
            Path("/reports"),
            Path("/artifacts"),
            lambda _: None,
        )
    pacer.assert_called_once_with()
    assert 2 == loaded.call_count
    assert all(
        call.kwargs["hardcover_pacer"] is shared for call in loaded.call_args_list
    )


def test_foreground_report_shares_one_hardcover_pacer():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", "media", ("first", "second")
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration(
                "first", True, False, "/first.secret", "hardcover"
            ),
            ProviderConnectionConfiguration(
                "second", True, False, "/second.secret", "hardcover"
            ),
        ),
    )
    shared = object()
    constructed = []
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(composition, "HardcoverPacer", return_value=shared) as pacer,
        patch.object(composition, "hardcover_client", side_effect=(object(), object())),
        patch.object(
            composition,
            "HardcoverMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append(kwargs["pacer"]) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config, Path("/reports"), Path("/artifacts"), "primary", "books", "item", 0
        )
        bindings = generate.call_args.kwargs["providers"]
        for binding in bindings:
            binding.factory()
    pacer.assert_called_once_with()
    assert [shared, shared] == constructed


def test_foreground_report_shares_one_google_books_pacer():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", "media", ("first", "second")
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration("first", True, False, None, "google_books"),
            ProviderConnectionConfiguration(
                "second", True, False, None, "google_books"
            ),
        ),
    )
    shared = object()
    constructed = []
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(composition, "GoogleBooksPacer", return_value=shared) as pacer,
        patch.object(
            composition, "google_books_client", side_effect=(object(), object())
        ),
        patch.object(
            composition,
            "GoogleBooksMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append(kwargs["pacer"]) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
        )
        bindings = generate.call_args.kwargs["providers"]
        for binding in bindings:
            binding.factory()
    pacer.assert_called_once_with()
    assert [shared, shared] == constructed


def test_foreground_report_shares_one_inventaire_pacer():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", "media", ("first", "second")
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration("first", True, True, None, "inventaire"),
            ProviderConnectionConfiguration("second", True, True, None, "inventaire"),
        ),
    )
    shared = object()
    constructed = []
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(composition, "InventairePacer", return_value=shared) as pacer,
        patch.object(
            composition, "inventaire_client", side_effect=(object(), object())
        ),
        patch.object(
            composition,
            "InventaireMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append(kwargs["pacer"]) or object()  # type: ignore[func-returns-value]  # reason: the recorder returns a provider double
            ),
        ),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
        )
        for binding in generate.call_args.kwargs["providers"]:
            binding.factory()
    pacer.assert_called_once_with()
    assert [shared, shared] == constructed


def test_disabled_inventaire_constructs_no_client_or_pacer():
    config = ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books", "primary", "library", "book", "media", ("inventaire",)
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        (
            ProviderConnectionConfiguration(
                "inventaire", False, False, None, "inventaire"
            ),
        ),
    )
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(composition, "InventairePacer") as pacer,
        patch.object(composition, "inventaire_client") as client,
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
        )
        binding = generate.call_args.kwargs["providers"][0]
        assert binding.enabled is False
    pacer.assert_not_called()
    client.assert_not_called()


# The two provider constructors -- the foreground report's factory and a
# provider retry's -- refuse the same configurations, each with its own error.
# These tests pin each refusal's type and text, the boundary each crosses, and
# the pacer each path hands its adapter.

_REFUSALS = ("hardcover_without_credential", "open_library_without_email", "unknown")
_FOREGROUND_REFUSAL = {
    "hardcover_without_credential": "provider credential unavailable",
    "open_library_without_email": "provider configuration is invalid",
    "unknown": "provider configuration is invalid",
}
_ADAPTERS = (
    "google_books",
    "open_library",
    "inventaire",
    "hardcover",
    "audible_catalog",
    "audnexus",
    "audiosilo",
    "itunes_search",
)
_PROVIDER_CLASSES = {
    "google_books": "GoogleBooksMetadataProvider",
    "open_library": "OpenLibraryMetadataProvider",
    "inventaire": "InventaireMetadataProvider",
    "hardcover": "HardcoverMetadataProvider",
    "audible_catalog": "AudibleCatalogMetadataProvider",
    "audnexus": "AudnexusMetadataProvider",
    "audiosilo": "AudioSiloMetadataProvider",
    "itunes_search": "ITunesSearchMetadataProvider",
}
_PROVIDER_CLIENTS = (
    "google_books_client",
    "hardcover_client",
    "hardcover_client_token",
    "open_library_client",
    "inventaire_client",
    "audible_catalog_client",
    "audnexus_client",
    "audiosilo_client",
    "itunes_search_client",
)


def _provider_for(adapter: str) -> ProviderConnectionConfiguration:
    """One valid, enabled connection for the adapter, named after it."""
    name = adapter.replace("_", "-")
    if adapter == "open_library":
        return ProviderConnectionConfiguration(
            name, True, False, None, adapter, "owner@example.org"
        )
    if adapter == "audible_catalog":
        return ProviderConnectionConfiguration(
            name, True, False, None, adapter, marketplaces=("us",)
        )
    return ProviderConnectionConfiguration(name, True, False, None, adapter)


def _refused(branch: str) -> ProviderConnectionConfiguration:
    """A connection both constructors refuse.

    Validated configuration admits a hardcover connection with no credential
    but refuses the other two outright, so those are stand-ins: a copy of a
    valid connection with one field changed underneath its validation.
    """
    if branch == "hardcover_without_credential":
        return _provider_for("hardcover")
    stand_in = copy.copy(_provider_for("open_library"))
    if branch == "open_library_without_email":
        object.__setattr__(stand_in, "contact_email", None)
    else:
        object.__setattr__(stand_in, "adapter", "unknown")
    return stand_in


def _report_config(
    providers: tuple[ProviderConnectionConfiguration, ...],
) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books",
                "primary",
                "library",
                "book",
                "media",
                tuple(sorted(value.name for value in providers)),
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        tuple(sorted(providers, key=lambda value: value.name)),
    )


def _foreground_bindings(
    config: ApplicationConfiguration,
    credentials: dict[str, str] | None = None,
    **pacers: object,
) -> tuple[ProviderBinding, ...]:
    service = unittest.mock.Mock()
    service.run.return_value = "report"
    with (
        patch.object(composition, "CoverArtifactStore"),
        patch.object(composition, "AudiobookshelfItemReader"),
        patch.object(
            composition, "GenerateMetadataReport", return_value=service
        ) as generate,
    ):
        assert "report" == composition.run_metadata_report_loaded(
            config,
            Path("/reports"),
            Path("/artifacts"),
            "primary",
            "books",
            "item",
            0,
            credentials=credentials,
            **pacers,
        )
    return tuple(generate.call_args.kwargs["providers"])


def _foreground_factory() -> Callable[..., object]:
    (binding,) = _foreground_bindings(_report_config((_provider_for("hardcover"),)))
    return binding.factory


@pytest.mark.parametrize("branch", _REFUSALS)
def test_foreground_factory_refusal_type_and_text(branch):
    factory = _foreground_factory()
    with pytest.raises(MetadataReportError) as raised:
        factory(_refused(branch))
    assert type(raised.value) is MetadataReportError
    assert str(raised.value) == _FOREGROUND_REFUSAL[branch]


@pytest.mark.parametrize("branch", _REFUSALS)
def test_foreground_refusal_is_a_construction_failure_at_the_run_boundary(branch):
    selected = TargetFile("a" * 64, "book.epub", "epub")
    service = GenerateMetadataReport(
        target=FakeTarget([TargetSnapshot("ebook", "Title", (selected,))]),
        inspector=FakeInspector(InspectionResult("c" * 64, ())),
        repository_factory=FakeReportRepository,
        providers=(
            ProviderBinding(
                "catalogue",
                True,
                functools.partial(_foreground_factory(), _refused(branch)),
                authority="hardcover",
            ),
        ),
        connection=TargetConnectionConfiguration(
            "primary", "https://example.invalid", "/private/target"
        ),
        library=LibraryConfiguration(
            "ebooks", "primary", "library-id", "ebook", "books", ("catalogue",)
        ),
        mount=MediaMountConfiguration("books", "/media", "/library", True),
        limits=ReportLimits(),
    )
    with pytest.raises(MetadataReportError) as raised:
        service.run("item-1", 0)
    assert not isinstance(raised.value, CredentialError)
    assert str(raised.value) == "provider construction failed"
    assert type(raised.value.__cause__) is MetadataReportError
    assert str(raised.value.__cause__) == _FOREGROUND_REFUSAL[branch]
    assert classify_provider_recovery_failure(raised.value)[0] != "authentication"


@pytest.mark.parametrize("branch", _REFUSALS)
def test_retry_factory_refusal_type_and_text(branch):
    with pytest.raises(MetadataReportError) as raised:
        composition._retry_provider(
            _refused(branch),
            _report_config((_provider_for("hardcover"),)),
            None,
            ("us",),
            composition._provider_pacers(()),
        )
    assert type(raised.value) is ProviderRetrySuccessorError
    assert str(raised.value) == "provider retry configuration changed"


def test_hardcover_with_an_injected_credential_is_constructed_on_both_paths():
    provider = _provider_for("hardcover")
    config = _report_config((provider,))
    credentials = {"provider:hardcover": "token", "target:primary": "target"}
    foreground_client, retry_client = object(), object()
    constructed: list[object] = []
    with (
        patch.object(
            composition,
            "hardcover_client_token",
            side_effect=(foreground_client, retry_client),
        ) as token_client,
        patch.object(composition, "hardcover_client") as file_client,
        patch.object(
            composition,
            "HardcoverMetadataProvider",
            side_effect=lambda *args, **kwargs: (
                constructed.append(args[1]) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
    ):
        (binding,) = _foreground_bindings(config, credentials)
        binding.factory()
        composition._retry_provider(
            provider,
            config,
            credentials,
            ("us",),
            composition._provider_pacers((provider,)),
        )
    file_client.assert_not_called()
    assert [
        unittest.mock.call("token", 30),
        unittest.mock.call("token", 30),
    ] == token_client.call_args_list
    assert [foreground_client, retry_client] == constructed


@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_each_path_passes_its_own_pacer_for_the_adapter(adapter):
    provider = _provider_for(adapter)
    config = _report_config((provider,))
    credentials = (
        {"provider:hardcover": "token", "target:primary": "target"}
        if adapter == "hardcover"
        else None
    )
    foreground = {name: object() for name in _ADAPTERS}
    retry = {name: object() for name in _ADAPTERS}
    received: list[object] = []
    with ExitStack() as stack:
        for client in _PROVIDER_CLIENTS:
            stack.enter_context(patch.object(composition, client))
        for name in _ADAPTERS:
            stack.enter_context(
                patch.object(
                    composition,
                    _PROVIDER_CLASSES[name],
                    side_effect=lambda *args, **kwargs: (
                        received.append(kwargs["pacer"]) or object()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
                    ),
                )
            )
        (binding,) = _foreground_bindings(
            config,
            credentials,
            **{name + "_pacer": value for name, value in foreground.items()},
        )
        binding.factory()
        composition._retry_provider(
            provider,
            config,
            credentials,
            ("us",),
            composition._ProviderPacers(*(retry[name] for name in _ADAPTERS)),
        )
    assert [foreground[adapter], retry[adapter]] == received


_PACER_CLASSES = {
    "google_books": "GoogleBooksPacer",
    "open_library": "OpenLibraryPacer",
    "inventaire": "InventairePacer",
    "hardcover": "HardcoverPacer",
    "audible_catalog": "AudibleCatalogPacer",
    "audnexus": "AudnexusPacer",
    "audiosilo": "AudioSiloPacer",
    "itunes_search": "ITunesSearchPacer",
}


@pytest.mark.parametrize("adapter", _ADAPTERS)
def test_a_pipeline_paces_each_enabled_adapter_alone(adapter):
    enabled = _provider_for(adapter)
    disabled = ProviderConnectionConfiguration("disabled", False, False, None, adapter)
    pacers = composition._provider_pacers((enabled,))
    for name in _ADAPTERS:
        pacer = getattr(pacers, name)
        if name == adapter:
            assert type(pacer) is getattr(composition, _PACER_CLASSES[name])
        else:
            assert pacer is None
    unpaced = composition._provider_pacers((disabled,))
    assert all(getattr(unpaced, name) is None for name in _ADAPTERS)


def _audiobook_config(
    *providers: ProviderConnectionConfiguration,
) -> ApplicationConfiguration:
    """An audiobook library whose declared German moves Audible.de first.

    The owner states ``us, uk, de``; the library's ``de`` makes the order every
    ASIN lookup walks ``de, us, uk``, which neither the stated order nor the
    ``us`` default produces.
    """
    audible = ProviderConnectionConfiguration(
        "audible-catalog",
        True,
        False,
        None,
        "audible_catalog",
        marketplaces=("us", "uk", "de"),
    )
    configured = (audible, _provider_for("audnexus"), *providers)
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/target"
            ),
        ),
        (
            LibraryConfiguration(
                "books",
                "primary",
                "library",
                "audiobook",
                "media",
                tuple(sorted(value.name for value in configured)),
                languages=("de",),
            ),
        ),
        (MediaMountConfiguration("media", "/media", "/books", True),),
        tuple(sorted(configured, key=lambda value: value.name)),
    )


def _recovery_resolver(
    config: ApplicationConfiguration, credentials: dict[str, str] | None
) -> Callable[..., object]:
    """The retry resolver `create_review_pipeline` hands provider recovery."""
    with (
        patch.object(composition, "ProviderRecoveryActivation") as activation,
        patch.object(composition, "ScopedProviderRecoveryRunner"),
    ):
        composition._provider_recovery_wiring(
            config,
            Path("/artifacts"),
            credentials,
            composition._provider_pacers(config.providers),
            unittest.mock.Mock(),
            unittest.mock.Mock(),
            {},
        )
    return activation.call_args.kwargs["provider_resolver"]


def _recorded_marketplaces(build: Callable[[], None]) -> dict[str, object]:
    """Run `build` and return the marketplaces each audiobook adapter received."""
    received: dict[str, object] = {}

    def recorder(adapter: str) -> Callable[..., object]:
        def construct(*args: object, **kwargs: object) -> object:
            received[adapter] = kwargs["marketplaces"]
            return object()

        return construct

    with ExitStack() as stack:
        for client in _PROVIDER_CLIENTS:
            stack.enter_context(patch.object(composition, client))
        for adapter in ("audible_catalog", "audnexus"):
            stack.enter_context(
                patch.object(
                    composition,
                    _PROVIDER_CLASSES[adapter],
                    side_effect=recorder(adapter),
                )
            )
        build()
    return received


def test_a_report_walks_the_librarys_marketplace_order_for_each_asin_adapter():
    config = _audiobook_config()

    def build() -> None:
        for binding in _foreground_bindings(config):
            binding.factory()

    assert {
        "audible_catalog": ("de", "us", "uk"),
        "audnexus": ("de", "us", "uk"),
    } == _recorded_marketplaces(build)


def test_a_retry_walks_the_librarys_marketplace_order_for_each_asin_adapter():
    config = _audiobook_config()
    resolver = _recovery_resolver(config, None)

    def build() -> None:
        for provider in config.providers:
            retry = resolver("primary", "books", provider.name, provider.adapter, 1)
            retry.binding.factory()

    assert {
        "audible_catalog": ("de", "us", "uk"),
        "audnexus": ("de", "us", "uk"),
    } == _recorded_marketplaces(build)


def test_a_retry_under_injected_credentials_builds_its_provider_from_them():
    hardcover = ProviderConnectionConfiguration(
        "hardcover", True, False, "/secrets/hardcover", "hardcover"
    )
    config = _audiobook_config(hardcover)
    credentials = {"provider:hardcover": "injected-token"}
    resolver = _recovery_resolver(config, credentials)
    client = object()
    with (
        patch.object(
            composition, "hardcover_client_token", return_value=client
        ) as token_client,
        patch.object(composition, "hardcover_client") as file_client,
        patch.object(composition, "HardcoverMetadataProvider") as constructed,
    ):
        retry = resolver("primary", "books", "hardcover", "hardcover", 1)
        retry.binding.factory()
    file_client.assert_not_called()
    token_client.assert_called_once_with("injected-token", 30)
    assert client is constructed.call_args.args[1]
