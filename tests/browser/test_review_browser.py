from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, quote, urlparse

import pytest
import uvicorn
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from playwright.sync_api import (
    APIResponse,
    BrowserContext,
    ConsoleMessage,
    Locator,
    Page,
    Request,
    Route,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from scripts.verify_playwright_browser import verify_playwright_browser

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    qualify_provider_observation,
)
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessOperations,
    ReviewerAccessView,
    ReviewerEngineStatus,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from mechanical_librarian.domain.human_decision import RECORDED_ONLY_REASON
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FileFacts,
    MetadataReport,
    ProviderQueryKind,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.setup_state import (
    SetupStateDocument,
    claim_token_digest,
)
from mechanical_librarian.infrastructure.settings_authority import BootstrapMount
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from mechanical_librarian.interfaces.web.setup import create_setup_app
from tests.browser.text_size import set_root_text
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asin_lane_observation,
    chapter_runtime_report,
    corroborated_audiobook_report,
)
from tests.support.metadata_report_doubles import audio_result
from tests.support.ports import ReservedPort, bind_loopback
from tests.support.proposal_evidence import _fields as _bibliographic_fields
from tests.support.review_proxy import review_tls_proxy
from tests.support.review_web_doubles import FakeActivityApplication
from tests.support.setup_server import real_setup_server
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
)
from tests.unit.test_cover_packets import (
    _report as _cover_report,
)
from tests.unit.test_cover_packets import (
    _source as _cover_source,
)

_PASSWORD = "synthetic browser password"
_CLAIM_TOKEN = "synthetic-browser-claim-token-0123456789"


class _BrowserActivityApplication(FakeActivityApplication):
    def wake(self) -> None:
        return None


def _certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "browser-test.crt"
    key = root / "browser-test.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def _report():
    return _bibliographic_report(
        current_title="Mistborn <img src=x onerror=alert(1)>",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=("Mistborn: The Final Empire",),
                series="Mistborn",
            ),
        ),
    )


_DEFAULT_SETTINGS_DOCUMENT = OwnerSettingsDocument.model_validate(
    {
        "schema_version": "owner-settings-v4",
        "revision": 1,
        "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
        "libraries": [
            {
                "name": "ebooks",
                "connection": "primary",
                "library_id": "library",
                "media_kind": "ebook",
            }
        ],
    }
)


def _publish_audiobook(repository: Any, chapter_runtime: bool = False) -> None:
    """Publish one audiobook exactly as preparation does, and stop there.

    The row this leaves behind is the whole point: a `report_only` package
    with no review case, whose evidence the browser could not reach at all
    until the Library row learned to open it.
    """
    inspection = audio_result(
        (
            (ClaimKind.ASIN, "B0CW1KL2QD", "sidecar", None),
            (ClaimKind.CONTRIBUTOR, "Recorded Narrator", "sidecar", "narrator"),
            (ClaimKind.DURATION, "43200.000", "embedded_tag", None),
            (ClaimKind.PERFORMANCE_TYPE, "unabridged", "path", None),
        ),
        # The one-track manifest the double writes must digest to the file the
        # report is bound to, exactly as the composed inspector guarantees.
        content_digest="2" * 64,
        chapters=(AudioChapter(1, 0, 1_800_000, "Chapter One"),),
        embedded_cover=AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM),
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "audio-item",
            default_target_fields("Recorded audiobook"),
        ),
        file=FileFacts(0, "1" * 64, "2" * 64, "m4b"),
        local_claims=inspection.claims,
        local_reasons=tuple(
            sorted({f"{claim.basis}_{claim.kind.value}" for claim in inspection.claims})
        ),
        # The recording carries its own ASIN, so the per-ASIN answer keeps the
        # running time it states and the derived media-health section has two
        # sides to compare. Without it the section renders empty and proves
        # only that it exists.
        providers=(
            qualify_provider_observation(
                asin_lane_observation(inspection.claims, duration="72000.000"),
                MetadataLookup(ProviderQueryKind.ASIN, inspection.claims),
            ),
        ),
        audio=inspection.audio,
    )
    if chapter_runtime:
        base = chapter_runtime_report(chapter_rows_omitted=True)
        report = MetadataReport.create(
            target=report.target,
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            audio=base.audio,
        )
    producer = "f" * 64
    repository.request_review_scan("primary", "ebooks", "library", producer, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    entry = CatalogueEntry(
        "primary",
        "ebooks",
        "library",
        "audio-item",
        1,
        "Recorded audiobook",
        ("Example Author",),
        False,
        False,
        "singular-supported",
        report.file.identity,
        "/audio-item.m4b",
        "m4b",
        "audiobooks",
    )
    repository.complete_review_discovery(discovery, 1, "a" * 64, (entry,), 12)
    extraction = repository.claim_review_pipeline_action(13)
    assert extraction is not None
    repository.publish(
        report,
        lambda: True,
        None,
        lambda value, published: value.complete_review_extraction_publication(
            extraction, published.report_id, 14, 0, "evidence_only"
        ),
    )


def _publish_audiobook_case(repository: Any, chapter_runtime: bool = False) -> str:
    """Prepare one recording whose work identity is admitted, through to a case.

    This is the F45 outcome the owner actually meets: the same admission the
    runtime corroboration band already made, carried through the same
    evaluator and the same admission an ebook uses (#966).
    """
    report = admitted_audiobook_report(
        connection="primary",
        library="ebooks",
        item="audio-case-item",
        identity="3" * 64,
        content_digest="4" * 64,
    )
    if chapter_runtime:
        base = chapter_runtime_report()
        report = MetadataReport.create(
            target=TargetFacts(
                "primary", "ebooks", "chapter-case-item", base.target.fields
            ),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            audio=base.audio,
        )
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return cast("str", admitted["review_case_id"])


def _publish_audiobook_tranche_case(repository: Any) -> str:
    """Decide one recording's ASIN under a test-supplied F46 record.

    The caller holds the record open around this whole server, so the
    projection, the decision and the rail all read the same qualified
    envelope (#967).
    """
    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )

    report = corroborated_audiobook_report(
        connection="primary",
        library="ebooks",
        item="audio-tranche-item",
        identity="5" * 64,
        content_digest="6" * 64,
    )
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["asin"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "F46 ASIN tranche", dict(DEFAULT_OPTIONS), destinations
    )
    resolved = resolve_policy(profile, "ebooks")
    repository.enter_shadow_preview(1, ())
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("ebooks", resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    repository.activate_qualified_automatic(3)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    outcome = repository.persist_shadow_outcome(case_id, 4)
    assert outcome not in {"inactive", "stale"}, outcome
    return cast("str", case_id)


def _consent_applications(repository: Any, root: Path, libraries: tuple[str, ...]):
    """The activity projection and guarded scheduler over fake services for
    these libraries, so Activity, Library and Policy read the store's consent."""
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeBackupReadiness,
        FakeCapabilityObserver,
        FakeDependencyCurrentness,
        FakeForwardReevaluation,
        FakeTargetService,
        PrivateCoverRecoveryStore,
    )
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
    )
    from mechanical_librarian.application.continuous_field_effects import (
        ContinuousGuardedApplication,
    )
    from mechanical_librarian.application.field_effects import (
        GuardedFieldApplication,
    )

    authority = PersistedFieldEffectAuthority(repository)
    capability = FakeCapabilityObserver("c" * 64)
    services = {
        ("primary", library): GuardedFieldApplication(
            repository,
            FakeTargetService(),
            FakeBackupReadiness(),
            capability,
            authority,
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old"),
            FakeForwardReevaluation(),
            lambda: 1_000,
        )
        for library in libraries
    }
    activity = ContinuousGuardedApplication(
        repository,
        {},
        {},
        authority,
        tuple(services),
        lambda: 1_000,
        lambda *_: None,
        activity_only=True,
    )
    application = ContinuousGuardedApplication(
        repository,
        services,
        {key: capability for key in services},
        authority,
        tuple(services),
        lambda: 1_000,
        lambda *_: None,
    )
    return activity, application


@contextmanager
def _server(
    *,
    database: Path,
    hash_path: Path,
    certificate: Path,
    key: Path,
    port: ReservedPort,
    transport_mode: str = "direct",
    cover: bool = False,
    configured_cover_reviewer: bool = False,
    sole_cover_timeout: bool = False,
    stale_cover_successor: bool = False,
    stale_cover_same_case: bool = False,
    automatic_fixture: bool = False,
    refusal_fixture: bool = False,
    synopsis_fixture: str | None = None,
    work_review_fixture: bool = False,
    work_series_title: bool = False,
    synopsis_destination: str = "automatic",
    ebook_synopsis: bool = False,
    isbn_fixture: bool = False,
    library_identity_fixture: bool = False,
    isbn_destination: str = "automatic",
    series_fixture: str | None = None,
    series_destination: str = "automatic",
    genre_fixture: tuple[str, ...] | None = None,
    genre_destination: str = "automatic",
    genre_current: tuple[str, ...] | None = None,
    genre_others: str = "ask",
    owner_write: bool = False,
    owner_recur: bool = False,
    owner_library_change: bool = False,
    owner_readback: list[Callable[[str, bool], str]] | None = None,
    language_fixture: tuple[str, ...] | None = None,
    language_current: str | None = None,
    language_audio: bool = False,
    language_destination: str = "automatic",
    language_option: str = "display_name",
    author_fixture: str | None = None,
    author_destination: str = "automatic",
    isbn_title_fixture: str | None = None,
    activity_checks_fixture: bool = False,
    description_fixture: Literal[
        "ready",
        "needs_review",
        "failed",
        "automatic",
        "no_change",
        "no_alternatives",
        "missing_prices",
        "unavailable_model",
        "retry_requested",
        "retry_running",
        "retry_failed",
        "retry_succeeded",
    ]
    | None = None,
    long_descriptions: bool = False,
    provider_recovery: bool = False,
    retained_decoration: tuple[str, bool] | None = None,
    retained_component_text: str | None = None,
    cover_shapes: tuple[tuple[int, int], ...] = ((1, 1), (1, 1)),
    cover_duplicate: bool = False,
    cohort: int = 0,
    preparation: bool = False,
    preparation_terminal: bool = False,
    audiobook: bool = False,
    audiobook_case: bool = False,
    chapter_runtime: bool = False,
    audiobook_tranche: bool = False,
    settings_document: OwnerSettingsDocument | None = _DEFAULT_SETTINGS_DOCUMENT,
    on_app: Callable[[Any], None] | None = None,
    write_consent: Callable[[Any], tuple[str, ...]] | None = None,
):
    repository = open_repository(database, create=True, cross_thread=True)
    if (
        description_fixture in {"automatic", "no_change"}
        and settings_document is not None
    ):
        from mechanical_librarian.domain.description_advice import (
            DescriptionAdviceSettings,
        )

        settings_document = settings_document.model_copy(
            update={
                "description_advice": DescriptionAdviceSettings(
                    automatic_application=True
                )
            }
        )
    if library_identity_fixture and settings_document is not None:
        settings_document = settings_document.model_copy(
            update={
                "libraries": [
                    settings_document.libraries[0].model_copy(
                        update={
                            "name": "audio",
                            "media_kind": "audiobook",
                            "display_name": "Listening Room",
                        }
                    )
                ]
            }
        )
    settings = None
    if settings_document is not None:
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO owner_settings VALUES(1,?,?)",
            (
                settings_document.revision,
                _canonical_json(settings_document.model_dump()),
            ),
        )
        connection.executemany(
            "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
            [
                (f"connection.{item.name}.key", f"synthetic-{item.name}-secret")
                for item in settings_document.connections
            ],
        )
        connection.commit()
        connection.close()
        settings = OwnerSettingsOperations(repository.owner_settings_store())
    if audiobook_tranche:
        # The tranche's policy has to reach the automatic posture before any
        # other case exists, because confirmation requires a settled
        # transition.
        _publish_audiobook_tranche_case(repository)
    origin = (
        f"http://review-transport.test:{port.number}"
        if transport_mode == "insecure"
        else f"http://127.0.0.1:{port.number}"
        if transport_mode == "http"
        else f"https://127.0.0.1:{port.number}"
    )
    review_configuration = ReviewWebConfiguration(
        tls_certificate_file=str(certificate) if transport_mode == "direct" else None,
        tls_private_key_file=str(key) if transport_mode == "direct" else None,
        trusted_proxy_ips=("127.0.0.1",) if transport_mode == "proxy" else (),
        canonical_origin=origin,
        owner_password_hash_file=str(hash_path),
    )
    reviewer_access: ReviewerAccessOperations | Mock | None = None
    if configured_cover_reviewer:
        from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
        from tests.unit.test_reviewer_access_application import _Auth, _OpenRouter

        assert cover
        reviewer_access = ReviewerAccessOperations(
            repository.reviewer_access_store(),
            codex_auth=_Auth("usable"),
            codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
                True, None, model="gpt-5.6-sol"
            ),
            codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
            codex_binding=lambda: (
                "synthetic-contained-model",
                b"synthetic-cover-profile",
                lambda *_args: "{}",
            ),
            openrouter_factory=lambda key, model: _OpenRouter(key, model, []),
        )
        reviewer_access.configure_openrouter(
            expected_revision=1, api_key="synthetic-key", model="synthetic/cover-a"
        )
    cover_considerations = None
    if cover:
        # Real pixel dimensions, because containment is a property of the
        # rendered image's intrinsic ratio against its frame and a 1x1 preview
        # would make every ratio assertion vacuously true (#644).
        payloads = tuple(
            _cover_preview(index, width=width, height=height)
            for index, (width, height) in enumerate(cover_shapes, 1)
        )
        if cover_duplicate:
            # Hardcover (index 3) and Open Library (index 4) offer one image,
            # which the comparison merges into a single candidate (#1290).
            payloads = (*payloads[:3], payloads[2], *payloads[4:])
        sources = (
            (
                _cover_source(1, payloads[0], embedded=True),
                replace(
                    _cover_source(2, payloads[1]),
                    state=CoverSourceState.TIMEOUT,
                    artifact=None,
                    timeout_scope="attempted",
                ),
            )
            if sole_cover_timeout
            else tuple(
                _cover_source(index, payload, incumbent=index == 1, embedded=index == 2)
                for index, payload in enumerate(payloads, 1)
            )
        )
        base = _cover_report(sources)
        incumbent = sources[0].artifact
        assert incumbent is not None
        fields = (
            base.target.fields
            if sole_cover_timeout
            else tuple(
                TargetField(
                    field.destination,
                    TargetFieldState.VALUE,
                    incumbent.artifact_id,
                )
                if field.destination is TargetDestination.COVER
                else field
                for field in base.target.fields
            )
        )
        report = MetadataReport.create(
            target=TargetFacts(
                base.target.connection,
                base.target.library,
                base.target.item,
                fields,
            ),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=(
                _bibliographic_source(
                    "catalogue",
                    "edition-1",
                    titles=("Synthetic title revised",),
                    series="Mistborn",
                ),
            )
            if automatic_fixture
            else base.providers,
            cover_sources=base.cover_sources,
        )
        artifacts = CoverArtifactStore(database.parent / "cover-artifacts")
        for index, (source, payload) in enumerate(
            zip(sources, payloads, strict=True), 1
        ):
            if source.artifact is not None:
                artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
        if configured_cover_reviewer:
            from mechanical_librarian.bootstrap.composition import (
                _compose_cover_considerations,
            )

            assert isinstance(reviewer_access, ReviewerAccessOperations)
            cover_considerations = _compose_cover_considerations(
                repository,
                database.parent / "cover-artifacts",
                review_configuration,
                reviewer_access,
            )
        else:
            cover_considerations = CoverConsiderationService(
                repository,
                read_preview=lambda artifact_id, _variant: (
                    artifacts.read_preview_digest(
                        next(
                            source.artifact.preview_digest
                            for source in sources
                            if source.artifact is not None
                            and source.artifact.artifact_id == artifact_id
                        )
                    )
                ),
                read_preview_digest=artifacts.read_preview_digest,
                require_artifact=artifacts.require_artifact,
                clock=lambda: 1_000,
                bind_policy=repository.bind_current_cover_authority,
                reviewer_dispatch=(
                    reviewer_access.dispatch_cover_observation
                    if reviewer_access
                    else None
                ),
                reviewer_identity=(
                    reviewer_access.current_cover_observation_identity
                    if reviewer_access
                    else None
                ),
                reviewer_revision_current=(
                    reviewer_access.revision_is_current if reviewer_access else None
                ),
            )
            repository.bind_owner_cover_validation(
                cover_considerations.require_owner_cover
            )
            cover_considerations.update_policy(CoverPolicy())
    elif retained_decoration is not None:
        from tests.support.proposal_evidence import retained_decoration_report

        component, keep = retained_decoration
        report = retained_decoration_report(
            component, keep=keep, component_text=retained_component_text
        )
    else:
        report = _report()
    if refusal_fixture:
        from types import SimpleNamespace

        from mechanical_librarian.domain.metadata_policy import (
            human_review_destinations,
        )
        from tests.support.automatic_decisions import _automatic_decisions
        from tests.support.ebook_synopsis import synopsis_report as refusal_report

        report = refusal_report(summaries=("",))
        assert (
            _automatic_decisions(
                SimpleNamespace(repository=repository),
                report,
                "description_fill",
                "safe_plain_text",
                qualified=True,
            )
            == ()
        )
        policy = repository.current_resolved_metadata_policy(report.target.library)
        assert policy is not None
        proposal_set = evaluate_report(
            report, policy.resolved_id, human_review_destinations(policy)
        )
        case_id = ReviewCase.create(proposal_set, report).review_case_id
        for index in range(1, 10):
            base = refusal_report(
                summaries=(
                    "A voyage reveals a hidden city."
                    if index == 9
                    else "294 pages;22 cm",
                )
            )
            other = MetadataReport.create(
                target=replace(base.target, item=f"refusal-{index}"),
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
            )
            proposal = evaluate_report(
                other, policy.resolved_id, human_review_destinations(policy)
            )
            repository.publish(other, lambda: True)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))[
                "review_case_id"
            ]
            repository.persist_shadow_outcome(admitted, 4)
        transition = repository.metadata_policy_transition()
        assert transition is not None and transition.state == "completed"
    elif library_identity_fixture:
        from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
        from mechanical_librarian.domain.metadata_policy import (
            human_review_destinations,
        )
        from tests.support.audiobook_reports import (
            publish_audiobook_preview,
            synthetic_audiobook_isbn_reason_report,
        )

        report = synthetic_audiobook_isbn_reason_report("conflict")
        case_id, identity_policy = publish_audiobook_preview(
            repository,
            report,
            ("isbn_form",),
            selected_options={"isbn_form": "preserve_exact"},
        )
        proposal_set = evaluate_report(
            report,
            identity_policy.resolved_id,
            human_review_destinations(identity_policy),
        )
        repository.persist_shadow_outcome(case_id, 4)
        sibling = MetadataReport.create(
            target=TargetFacts(
                report.target.connection,
                report.target.library,
                "same-title-sibling",
                tuple(
                    replace(field, value=("Another Author",))
                    if field.destination is TargetDestination.AUTHORS
                    else field
                    for field in report.target.fields
                ),
            ),
            file=report.file,
            local_claims=(),
            local_reasons=report.local_reasons,
            providers=(),
            audio=report.audio,
        )
        repository.publish(sibling, lambda: True)
        sibling_proposal = evaluate_report(
            sibling,
            identity_policy.resolved_id,
            human_review_destinations(identity_policy),
        )
        repository.publish_proposal(sibling_proposal)
        repository.admit_review_case(sibling_proposal)
        entry = CatalogueEntry(
            "primary",
            report.target.library,
            "library",
            "terminal-item",
            1,
            "Retained Terminal Title",
            ("Retained Writer",),
            True,
            False,
            "singular-supported",
            "b" * 64,
            "/synthetic.epub",
            "epub",
            "/media",
        )
        catalogue_path = database.parent / "catalogue.sqlite"
        with closing(open_catalogue(catalogue_path, create=True)) as catalogue:
            catalogue.acquire_refresh()
            generation = catalogue.publish("a" * 64, (entry,))
        repository.bind_library_catalogue(catalogue_path, "a" * 64)
        repository.request_review_scan(
            "primary", report.target.library, "library", "f" * 64, 10
        )
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, generation, "a" * 64, (entry,), 12
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None
        repository.fail_review_pipeline_action(
            action, "unreadable_media", False, 14, "track_1"
        )
    elif isbn_fixture:
        from mechanical_librarian.domain.metadata_policy import (
            human_review_destinations,
        )
        from tests.support.audiobook_reports import publish_audiobook_preview
        from tests.support.proposal_evidence import _image_unavailable_isbn_report

        report = _image_unavailable_isbn_report(
            review_choice=isbn_destination == "ignore"
        )
        case_id, isbn_policy = publish_audiobook_preview(
            repository,
            report,
            ("tags",),
            selected_options={"isbn_form": "prefer_isbn13", "tags": "fill_empty"},
            selected_destinations={"isbn_form": isbn_destination},
        )
        proposal_set = evaluate_report(
            report, isbn_policy.resolved_id, human_review_destinations(isbn_policy)
        )
        repository.persist_shadow_outcome(case_id, 4)
    elif work_review_fixture:
        from mechanical_librarian.domain.proposal_set import audiobook_review_scope
        from tests.support.audiobook_reports import (
            synthetic_audiobook_product_lookup_report,
        )

        report = synthetic_audiobook_product_lookup_report(
            tuple(
                {
                    "asin": f"B00000000{index}",
                    "title": "Synthetic Journey",
                    "authors": [{"name": "Example Author"}],
                    "summary": summary,
                    "genres": [
                        {"type": "genre", "name": genre}
                        for genre in (
                            "Adventure",
                            "Historical Fiction",
                            "Sea Stories",
                            "Exploration",
                        )
                    ],
                    **(
                        {"seriesPrimary": {"name": "Example Saga", "position": "2"}}
                        if work_series_title
                        else {}
                    ),
                }
                for index, summary in enumerate(
                    (
                        "A voyage reveals a hidden city.",
                        "A librarian follows a forgotten expedition across a distant archipelago. "
                        "The letters left behind describe the lives of sailors, the rituals of "
                        "island communities, and a city whose history has been deliberately "
                        "concealed. As storms close the shipping routes, she must decide which "
                        "account to trust and whether the discoveries belong in the archive or "
                        "with the people who preserved them.",
                    ),
                    1,
                )
            ),
            target_title="Synthetic Journey (Unabridged)"
            if work_series_title
            else "Synthetic Journey",
        )
        assert audiobook_review_scope(report) == "work"
        assert (
            len(
                next(
                    result
                    for result in evaluate_report(report).results
                    if result.destination.value == "description"
                ).alternatives
            )
            == 2
        )
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
    elif synopsis_fixture is not None:
        from mechanical_librarian.domain.metadata_policy import (
            human_review_destinations,
        )
        from tests.support.audiobook_reports import publish_audiobook_preview
        from tests.support.audiobook_synopsis import (
            recording_synopsis_report as synopsis_report,
        )

        if ebook_synopsis:
            from tests.support.ebook_synopsis import (
                publish_synopsis_preview,
            )
            from tests.support.ebook_synopsis import (
                synopsis_report as ebook_report,
            )

            report = ebook_report(
                summaries=("(duplicate)", "A voyage reveals a hidden city.")
            )
            case_id, synopsis_policy = publish_synopsis_preview(
                repository, report, synopsis_destination
            )
        else:
            report = synopsis_report(synopsis_fixture)
            case_id, synopsis_policy = publish_audiobook_preview(
                repository,
                report,
                ("tags",),
                selected_options={
                    "description_fill": "replace_safe_plain_text",
                    "tags": "fill_empty",
                },
                selected_destinations={"description_fill": synopsis_destination},
            )
        proposal_set = evaluate_report(
            report,
            synopsis_policy.resolved_id,
            human_review_destinations(synopsis_policy),
        )
        repository.persist_shadow_outcome(case_id, 4)
    elif series_fixture is not None:
        from mechanical_librarian.domain.metadata_policy import (
            human_review_destinations,
        )
        from tests.support.audiobook_reports import publish_audiobook_preview
        from tests.support.same_name_series import same_name_series_report

        report = same_name_series_report(
            "ebook" if series_fixture == "long" else series_fixture,
            ebook_title=("First Journey through the Long Valley " * 6).strip()
            if series_fixture == "long"
            else "First Journey",
        )
        case_id, series_policy = publish_audiobook_preview(
            repository,
            report,
            ("title_layout",),
            selected_options={
                "series": "primary_narrative",
                "title_layout": "preserve",
            },
            selected_destinations={"series": series_destination},
        )
        proposal_set = evaluate_report(
            report, series_policy.resolved_id, human_review_destinations(series_policy)
        )
        repository.persist_shadow_outcome(case_id, 4)
    elif genre_fixture is not None:
        from tests.support.genre_reports import genre_report, publish_genre_case

        report = genre_report(genres=genre_fixture, current=genre_current)
        case_id, proposal_set = publish_genre_case(
            repository, report, genre_destination, genre_others
        )
        if owner_write:
            case_id, report = _seed_owner_write(
                repository, database.parent, report, case_id, genre_destination
            )
        if owner_recur:
            # The previous title is chosen again and written, so the work
            # reads as it first did and its first case is current again.
            case_id, report = _seed_owner_write(
                repository,
                database.parent,
                report,
                case_id,
                genre_destination,
                title="Real Tigers",
                at=910,
            )
    elif language_fixture is not None:
        from tests.support.language_reports import (
            language_report,
            publish_language_case,
        )

        report = language_report(
            language_fixture, language_current, audio=language_audio
        )
        case_id, proposal_set = publish_language_case(
            repository, report, language_destination, language_option
        )
    elif author_fixture is not None:
        from tests.support.author_evidence import author_report, publish_author_case

        report = author_report(database.parent / "author-media", author_fixture)
        case_id, proposal_set = publish_author_case(
            repository, report, author_destination
        )
    elif isbn_title_fixture is not None:
        from tests.support.evidence_led_titles import (
            isbn_title_report,
            publish_title_case,
        )

        report = isbn_title_report(
            titles=("Example Title", "Example Title", "Another Work")
            if isbn_title_fixture == "conflict"
            else ("Example Title",),
            failed=isbn_title_fixture == "unavailable",
            current=(
                "A very long local title with repeated source context " * 8
            ).strip()
            if isbn_title_fixture == "long"
            else "Example Title"
            if isbn_title_fixture == "conflict"
            else "Example Series 03 - Example Title",
        )
        case_id, proposal_set = publish_title_case(
            repository,
            report,
            "automatic" if isbn_title_fixture in {"automatic", "conflict"} else "ask",
        )
    elif activity_checks_fixture:
        # One preparation's two kept fields, each scheduled to its no-change
        # result, so every field holds a recorded decision and its check.
        from types import SimpleNamespace

        from mechanical_librarian.adapters.field_effect_authority import (
            PersistedFieldEffectAuthority,
        )
        from mechanical_librarian.application.continuous_field_effects import (
            ContinuousGuardedApplication,
        )
        from mechanical_librarian.application.field_effects import (
            GuardedFieldApplication,
        )
        from tests.support.automatic_decisions import _automatic_decisions
        from tests.support.automatic_no_change import _automatic_no_change_inputs

        report, category, option, _ = _automatic_no_change_inputs("unproposed_isbn")
        case_id = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
            also=(("genres", "fill_empty"),),
        )[0].review_case_id
        lane = (report.target.connection, report.target.library)
        ContinuousGuardedApplication(
            repository,
            {lane: cast(GuardedFieldApplication, SimpleNamespace())},
            {lane: SimpleNamespace()},
            PersistedFieldEffectAuthority(repository),
            (lane,),
            lambda: 10,
            lambda *_: None,
        ).schedule()
    elif description_fixture is not None:
        case_id = ""  # The real description producer below owns case admission.
    elif provider_recovery:
        from tests.support.provider_recovery_rail import publish_provider_failure_case

        case_id, report, _, proposal_set = publish_provider_failure_case(repository)
    else:
        repository.publish(report, lambda: True)
        if retained_decoration is not None:
            from mechanical_librarian.domain.metadata_policy import (
                MetadataPolicyProfile,
                human_review_destinations,
                resolve_policy,
            )

            destinations = {
                name: "unsupported" if value == "unsupported" else "ask"
                for name, value in MetadataPolicyProfile.builtin().destinations
            }
            if automatic_fixture:
                destinations.update(
                    title_layout="automatic", subtitle="automatic", series="automatic"
                )
            profile = MetadataPolicyProfile.create(
                "Retained components",
                dict(MetadataPolicyProfile.builtin().options),
                destinations,
            )
            resolved = resolve_policy(profile, report.target.library)
            repository.enter_shadow_preview(1, ())
            repository.create_metadata_policy_draft(profile.canonical_json)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
                expected_active_generation=1,
            )
            if automatic_fixture:
                repository.activate_qualified_automatic(3)
            proposal_set = evaluate_report(
                report, resolved.resolved_id, human_review_destinations(resolved)
            )
        else:
            from mechanical_librarian.domain.metadata_policy import (
                human_review_destinations as _effective_destinations,
            )

            # A fixture that activated a policy of its own (the F46 tranche)
            # leaves a current resolution this report has to be evaluated
            # under, or admission refuses it as changed.
            current_policy = repository.current_resolved_metadata_policy(
                report.target.library
            )
            proposal_set = (
                evaluate_report(report)
                if current_policy is None
                else evaluate_report(
                    report,
                    current_policy.resolved_id,
                    _effective_destinations(current_policy),
                )
            )
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        if retained_decoration is not None and automatic_fixture:
            repository.persist_shadow_outcome(case_id, 4)
    if preparation:
        repository.request_review_scan(
            "primary",
            "ebooks",
            "library",
            "f" * 64,
            990,
            trigger=PreparationTrigger.MANUAL_SCAN,
        )
        discovery = repository.claim_review_pipeline_action(991)
        assert discovery is not None
        entries = tuple(
            CatalogueEntry(
                "primary",
                "ebooks",
                "library",
                f"preparation-{index:02}",
                1,
                f"Preparation book {index:02}",
                ("Synthetic Author",),
                True,
                False,
                "singular-supported",
                f"{index + 10_000:064x}",
                f"/synthetic/preparation-{index:02}.epub",
                "epub",
                "books",
            )
            for index in range(7)
        )
        repository.complete_review_discovery(discovery, 1, "e" * 64, entries, 992)
        assert repository.claim_review_pipeline_action(993) is not None
        if preparation_terminal:
            repository._connection.execute(
                "UPDATE review_preparation_presentation SET work_stage='projection',"
                "current_work_state='complete',work_outcome='ready',updated_at=1000,"
                "completion_sequence=ordinal+1 "
                "WHERE row_kind='work'"
            )
            repository._connection.execute(
                "UPDATE review_preparation_presentation SET cycle_state='completed',"
                "updated_at=1000 WHERE row_kind='enrollment'"
            )
            repository._connection.execute(
                "UPDATE review_preparation_presentation SET state='completed_clean',"
                "completed_at=1000,updated_at=1000,active_count=0,read=7,"
                "searches_finished=7,compared=7,checked=7,ready=7,failed=0,"
                "completion_count=7 "
                "WHERE row_kind='run'"
            )
            repository._connection.commit()
    # A cohort large enough that a whole-Library projection is measurably
    # slower than one exact-case read. #635's acceptance is about what the
    # next field waits on, and one work cannot tell those two apart.
    for index in range(cohort):
        extra = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                f"cohort-item-{index:03d}",
                _bibliographic_fields(
                    f"Cohort work {index:03d}",
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.MISSING
                    ),
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    ),
                ),
            ),
            file=FileFacts(0, f"{index:064x}", f"{index + 5_000:064x}"),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                _bibliographic_source(
                    "catalogue",
                    f"edition-{index}",
                    titles=(),
                    series="Mistborn",
                ),
            ),
        )
        repository.publish(extra, lambda: True)
        if provider_recovery:
            cohort_policy = repository.current_resolved_metadata_policy(
                extra.target.library
            )
            assert cohort_policy is not None
            extra_proposal = evaluate_report(
                extra, cohort_policy.resolved_id, dict(cohort_policy.destinations)
            )
        else:
            extra_proposal = evaluate_report(extra)
        repository.publish_proposal(extra_proposal)
        extra_case = json.loads(repository.admit_review_case(extra_proposal))[
            "review_case_id"
        ]
        if provider_recovery:
            repository.persist_shadow_outcome(extra_case, 3)
    if audiobook:
        _publish_audiobook(repository, chapter_runtime)
    if audiobook_case:
        _publish_audiobook_case(repository, chapter_runtime)
    listener_port = ReservedPort() if transport_mode == "proxy" else port
    session_store = SQLiteReviewSessionRepository.open(
        database.with_name("review-sessions.sqlite")
    )
    unconfigured_reviewer = Mock(spec=ReviewerAccessOperations)
    unconfigured_reviewer.read.return_value = ReviewerAccessView(
        revision=1,
        mapping=None,
        openrouter_key_configured=False,
        codex=ReviewerEngineStatus(
            engine="codex",
            model="gpt-5.6-sol",
            state="host_unsupported",
            reason="headroom_below_1_gib",
            host_headroom_bytes=536_870_912,
        ),
        openrouter=ReviewerEngineStatus(engine="openrouter", state="unconfigured"),
    )
    if reviewer_access is None:
        reviewer_access = unconfigured_reviewer
    if description_fixture is not None:
        from types import SimpleNamespace

        from mechanical_librarian.application.contained_reviewer_contract import (
            ContainedReviewRequest,
            canonical_json,
        )
        from mechanical_librarian.application.description_advice import (
            DescriptionAdviceService,
        )
        from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
        from mechanical_librarian.domain.description_advice import (
            DescriptionAdviceBinding,
            description_digest,
        )
        from tests.support.automatic_decisions import _automatic_decisions
        from tests.support.automatic_no_change import _automatic_no_change_inputs
        from tests.unit.test_reviewer_access_application import _Auth, _OpenRouter

        report, category, option, _ = _automatic_no_change_inputs(
            "description_preserved"
        )
        if long_descriptions:
            from tests.support.description_advice import _description_candidate

            current_description = (
                (
                    "The local catalogue describes a quiet coastal archive. "
                    "Its keeper records the letters of families separated by the sea. "
                )
                * 9
            ).strip()
            proposed_description = (
                (
                    "The publisher follows a travelling historian through mountain towns. "
                    "Each recovered notebook changes her understanding of the journey. "
                )
                * 10
            ).strip()
            report = MetadataReport.create(
                target=replace(
                    report.target,
                    fields=tuple(
                        replace(field, value=current_description)
                        if field.destination is TargetDestination.DESCRIPTION
                        else field
                        for field in report.target.fields
                    ),
                ),
                file=report.file,
                local_claims=report.local_claims,
                local_reasons=report.local_reasons,
                providers=tuple(
                    replace(
                        provider,
                        candidates=(
                            _description_candidate(
                                "catalogue", "edition-1", "work-1", proposed_description
                            ),
                        ),
                    )
                    for provider in report.providers
                ),
                cover_sources=report.cover_sources,
            )
        if description_fixture == "no_alternatives":
            report = MetadataReport.create(
                target=report.target,
                file=report.file,
                local_claims=report.local_claims,
                local_reasons=report.local_reasons,
                providers=tuple(
                    replace(
                        provider,
                        candidates=tuple(
                            replace(
                                candidate,
                                claims=tuple(
                                    claim
                                    for claim in candidate.claims
                                    if claim.kind is not ClaimKind.DESCRIPTION
                                ),
                            )
                            for candidate in provider.candidates
                        ),
                    )
                    for provider in report.providers
                ),
                cover_sources=report.cover_sources,
            )
        decisions = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            category,
            option,
            qualified=True,
        )
        deterministic = next(
            value for value in decisions if value.destination == "description"
        )
        case_id = deterministic.review_case_id
        description_profile = b"synthetic-contained-description-profile"
        description_calls = []
        unavailable = False

        class DescriptionOpenRouter(_OpenRouter):
            def run_description(self, *args):
                pytest.fail("browser metadata fixture dispatched an OpenRouter model")

            def probe_description(self):
                from mechanical_librarian.application.reviewer_contracts import (
                    OpenRouterReviewerError,
                )

                if unavailable and description_fixture == "unavailable_model":
                    raise OpenRouterReviewerError("model_unavailable")
                result = super().probe_description()
                return replace(result, prompt_price=None) if unavailable else result

        def description_runner(profile_bytes, instruction, inputs, schema, images):
            description_calls.append("dispatch")
            assert profile_bytes == description_profile and images == ()
            request = ContainedReviewRequest(
                instruction.decode(), inputs.decode(), schema.decode()
            )
            if description_fixture == "failed":
                return "invalid synthetic output"
            if description_fixture == "retry_failed" and len(description_calls) > 1:
                from mechanical_librarian.application.contained_reviewer_contract import (
                    ContainedReviewerError,
                )

                raise ContainedReviewerError("setup")
            text = (
                "Existing description."
                if description_fixture == "no_change"
                else "A synthetic reader explores an old library. Its stories bring distant communities together."
                if description_fixture == "automatic"
                else "A synthetic reader explores an old library.\nIts stories bring distant communities together. <strong>Authored text stays text.</strong>"
            )
            if long_descriptions:
                text = (
                    (
                        "An archivist and a historian uncover the lives behind a scattered collection. "
                        "Their search connects distant communities through stories of memory and belonging. "
                    )
                    * 11
                ).strip()
            if len(description_calls) > 1:
                text = "A new authored description from the explicit retry."
            return canonical_json(
                {
                    "request_sha256": request.digest,
                    "result": {
                        "description": text,
                        "disposition": "needs_review"
                        if description_fixture == "needs_review"
                        else "ready",
                    },
                }
            )

        reviewer_access = ReviewerAccessOperations(
            repository.reviewer_access_store(),
            codex_auth=_Auth("usable"),
            codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
                True, None, model="gpt-5.6-sol"
            ),
            codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
            codex_identity=lambda: ("synthetic-contained-model", description_profile),
            codex_binding=lambda: (
                "synthetic-contained-model",
                description_profile,
                description_runner,
            ),
            codex_description_binding=lambda: (
                "synthetic-contained-model",
                description_profile,
                description_runner,
            ),
            openrouter_factory=lambda key, model: DescriptionOpenRouter(key, model, []),
        )
        if description_fixture in {"missing_prices", "unavailable_model"}:
            cover_access = reviewer_access.configure_openrouter(
                expected_revision=1, api_key="synthetic-key", model="synthetic/cover-a"
            )
            description_access = reviewer_access.configure_openrouter(
                expected_revision=cover_access.revision,
                api_key="",
                model="synthetic/description-b",
                task="description_rewrite",
            )
            unavailable = True
            unavailable_access = reviewer_access.refresh_openrouter(
                "description_rewrite"
            )
            assert unavailable_access.description is not None
            assert unavailable_access.description.state == "model_unavailable"
            assert unavailable_access.description.reason == "model_unavailable"
            assert unavailable_access.description_metadata is None
            assert unavailable_access.mapping == cover_access.mapping
            assert unavailable_access.revision == description_access.revision + 1
            unavailable = (
                False  # Only a subsequent explicit refresh can recover metadata.
            )
        else:
            reviewer_access.select_codex(
                expected_revision=1, task="description_rewrite"
            )
        store = repository.description_advice_store()
        store.bind_identity(
            lambda engine, _model: (
                (
                    "synthetic-contained-model",
                    hashlib.sha256(description_profile).hexdigest(),
                )
                if engine == "codex"
                else None
            )
        )
        preferences = store.current_settings()
        binding = DescriptionAdviceBinding(
            review_case_id=case_id,
            report_id=report.report_id,
            proposal_set_id=deterministic.proposal_set_id,
            target_fingerprint=report.target.fingerprint,
            policy_generation=repository._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()[0],
            access_revision=reviewer_access.read().revision,
            preferences_digest=description_digest(preferences.model_dump()),
        )
        candidate = DescriptionAdviceService(
            store, reviewer_access, store.current_settings, lambda: 1000
        ).prepare(report, binding, lambda: store.binding_is_current(binding))
        if description_fixture in {
            "no_alternatives",
            "missing_prices",
            "unavailable_model",
        }:
            assert candidate is None and description_calls == []
            assert repository._connection.execute(
                "SELECT count(*) FROM description_advice_candidates"
            ).fetchone() == (0,)
            assert repository._connection.execute(
                "SELECT count(*) FROM description_decisions"
            ).fetchone() == (0,)
        if candidate is not None and description_fixture.startswith("retry_"):
            selected = store.admit(
                candidate.candidate_id, binding, "human", "a" * 64, 1001
            )
            if description_fixture in {"retry_requested", "retry_running"}:
                attempt = store.begin_attempt(
                    candidate.request_identity,
                    binding,
                    candidate.engine,
                    candidate.model,
                    1002,
                    lambda: store.binding_is_current(binding),
                    retry=True,
                )
                assert attempt is not None
                if description_fixture == "retry_running":
                    assert store.start(
                        attempt[0], 1003, lambda: store.binding_is_current(binding)
                    )
            else:
                DescriptionAdviceService(
                    store, reviewer_access, store.current_settings, lambda: 1002
                ).prepare(
                    report,
                    binding,
                    lambda: store.binding_is_current(binding),
                    retry=True,
                )
            retained = store.read(case_id)
            assert retained["selection"]["decision_id"] == selected.decision_id
            assert (
                retained["selected_advice"]["candidate"]["candidate_id"]
                == candidate.candidate_id
            )
            assert retained["state"] == description_fixture.removeprefix("retry_")
        if candidate is not None and description_fixture in {"automatic", "no_change"}:
            selected = store.admit(
                candidate.candidate_id, binding, "automatic_policy", "e" * 64, 1000
            )
            if description_fixture == "no_change":
                assert repository.record_current_field_effect_no_change(selected, 1001)
            else:
                from mechanical_librarian.adapters.field_effect_authority import (
                    PersistedFieldEffectAuthority,
                )
                from tests.support.description_advice import _guarded_application

                authority = PersistedFieldEffectAuthority(repository)
                snapshot = authority.resolve(selected)
                service, target = _guarded_application(
                    repository, database.parent, authority
                )
                operation = service.plan(
                    selected,
                    connection=snapshot.connection,
                    library=snapshot.library,
                    work_id=snapshot.work_id,
                    destination=snapshot.destination,
                    pre_value=snapshot.pre_value,
                    desired_value=snapshot.decision_value,
                    target_fingerprint=snapshot.target_fingerprint,
                    target_version=snapshot.target_version,
                    dependency_digests=snapshot.dependency_digests,
                    capability_digest="c" * 64,
                )
                assert operation is not None
                target.seed(operation.plan)
                assert service.apply(operation.plan.plan_id).state == "verified"
                assert target.read(operation.plan).value == candidate.output.description
    # Seeds the store's consents and names the libraries the guarded
    # scheduler writes to, so consent is read as the product reads it (#1370).
    consent_libraries = () if write_consent is None else write_consent(repository)
    language_activity = None
    consent_application = None
    if consent_libraries:
        language_activity, consent_application = _consent_applications(
            repository, database.parent, consent_libraries
        )
    elif (
        isbn_fixture
        or isbn_title_fixture is not None
        or synopsis_fixture is not None
        or work_review_fixture
        or language_fixture is not None
        or genre_fixture is not None
        or series_fixture is not None
        or activity_checks_fixture
    ):
        from mechanical_librarian.adapters.field_effect_authority import (
            PersistedFieldEffectAuthority,
        )
        from mechanical_librarian.application.continuous_field_effects import (
            ContinuousGuardedApplication,
        )

        language_activity = ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            ((report.target.connection, report.target.library),),
            lambda: 1_000,
            lambda *_: None,
            activity_only=True,
        )
    app = create_transport_test_app(
        repository,
        review_configuration,
        session_store=session_store,
        clock=lambda: 1_000,
        close_repository=True,
        policy_libraries=(
            tuple((library, None) for library in consent_libraries)
            or (
                ((report.target.library, None),)
                if refusal_fixture or work_review_fixture
                else ()
            )
        ),
        field_application=(
            consent_application
            if consent_libraries
            else _BrowserActivityApplication()
            if not isbn_fixture
            and isbn_title_fixture is None
            and synopsis_fixture is None
            and not work_review_fixture
            and language_fixture is None
            and genre_fixture is None
            and series_fixture is None
            and not activity_checks_fixture
            else None
        ),
        activity_projection=language_activity,
        react_asset_loader=ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
        cover_considerations=cover_considerations,
        settings=settings,
        reviewer_access=reviewer_access,
    )
    if owner_library_change:
        owner_changed = False

        @app.middleware("http")
        async def change_title_before_decision(request, call_next):
            # The library edits the written title just before the decision
            # arrives, so the case the owner answered is now stale.
            nonlocal owner_changed
            if (
                request.method == "POST"
                and request.url.path.endswith("/decisions")
                and not owner_changed
            ):
                owner_changed = True
                current = ReviewCase.create_from_data(
                    json.loads(repository.get_review_case(case_id))["case"]
                )
                _read_back_title(
                    repository, report, current, genre_destination, OWNER_EDIT, 910
                )
            return await call_next(request)

    if owner_readback is not None:
        # The library edits the title while the page is open: read the work
        # back as its successor and announce Library, as a scan does, naming
        # the successor case or not.
        def read_back(title: str, named: bool) -> str:
            current = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            successor_id, _ = _read_back_title(
                repository, report, current, genre_destination, title, 910
            )
            app.state.review_application.updates.publish(
                "library", successor_id if named else None
            )
            return successor_id

        owner_readback.append(read_back)

    stale_cover_published = False
    if stale_cover_successor or stale_cover_same_case:
        if cover_considerations is None:
            raise RuntimeError("stale cover fixture requires cover considerations")

        @app.middleware("http")
        async def publish_cover_successor_before_decision(request, call_next):
            nonlocal stale_cover_published
            if (
                request.method == "POST"
                and request.url.path.endswith("/decisions")
                and not stale_cover_published
            ):
                stale_cover_published = True
                if stale_cover_same_case:
                    cover_considerations.update_policy(
                        CoverPolicy(prefer_minimal_composition=True)
                    )
                    current_case = ReviewCase.create_from_data(
                        json.loads(repository.get_review_case(case_id))["case"]
                    )
                    cover_proposal = next(
                        value
                        for value in current_case.proposals
                        if value["destination"] == "cover"
                    )
                    cover_considerations.ensure(
                        case_id,
                        report.to_json(),
                        cover_proposal,
                    )
                    return await call_next(request)
                successor_report = MetadataReport.create(
                    target=report.target,
                    file=FileFacts(0, "c" * 64, "d" * 64, report.file.format),
                    local_claims=report.local_claims,
                    local_reasons=report.local_reasons,
                    providers=report.providers,
                    cover_sources=report.cover_sources,
                )
                repository.publish(successor_report, lambda: True)
                successor_proposal = evaluate_report(successor_report)
                repository.publish_proposal(successor_proposal)
                successor_case_id = json.loads(
                    repository.admit_review_case(successor_proposal)
                )["review_case_id"]
                successor_case = ReviewCase.create_from_data(
                    json.loads(repository.get_review_case(successor_case_id))["case"]
                )
                cover_proposal = next(
                    value
                    for value in successor_case.proposals
                    if value["destination"] == "cover"
                )
                cover_considerations.ensure(
                    successor_case_id,
                    successor_report.to_json(),
                    cover_proposal,
                )
            return await call_next(request)

    # The composed application, for a cohort that has to announce something the
    # server would announce rather than assert on what it answered.
    if on_app is not None:
        on_app(app)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=listener_port.number,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate) if transport_mode == "direct" else None,
            ssl_keyfile=str(key) if transport_mode == "direct" else None,
            proxy_headers=False,
            # Longer than any idle gap a test in this module leaves between
            # requests on one connection, with room to spare. The longest is
            # the 5,500 ms `page.wait_for_timeout` that proves the activity
            # poll has settled; every other gap -- `expect()` retries,
            # `press_sequentially`, viewport changes -- is shorter.
            #
            # It has to exceed them because `route.fetch()` is forwarded by
            # Playwright's Node driver, which keeps its own per-origin
            # keep-alive pool. That pool is not governed by the browser
            # context, is not flushed by `context.close()`, and outlives
            # `page.unroute()`. Under the previous one-second window a pooled
            # socket idled out, uvicorn closed it server-side, and the driver
            # selected that same socket inside the close race, reporting
            # `Route.fetch: socket hang up`. Only the two `route.fetch()`
            # tests ever showed it; the `route.continue_()` test never did,
            # because the browser owns that connection. The container gate's
            # `--cpus=2` widened the window, which is why it failed there
            # while the same commit passed on macOS (#730).
            timeout_keep_alive=30,
        )
    )
    # Everything above -- opening the repository, staging cover artifacts,
    # publishing the report and admitting the case -- ran while the port was
    # still held. It is given up here because uvicorn binds it inside
    # `server.run`, and two live binds on one loopback port is what the
    # operating system exists to refuse.
    listener_port.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic review server did not start")
    try:
        with (
            review_tls_proxy(
                f"http://127.0.0.1:{listener_port.number}",
                origin,
                port,
                certificate,
                key,
            )
            if transport_mode == "proxy"
            else nullcontext()
        ):
            yield origin, str(case_id)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        session_store.close()


OWNER_TITLE = "Real Tigers Revisited"
OWNER_EDIT = "Real Tigers: Library Edition"


def _seed_owner_write(
    repository,
    root: Path,
    report,
    case_id: str,
    destination: str,
    *,
    title: str = OWNER_TITLE,
    at: int = 900,
):
    """Write an owner-entered title through the guarded pipeline over a fake
    library, then read the work back as its successor case (#1285)."""
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.domain.human_decision import HumanDecision
    from tests.support.description_advice import _guarded_application

    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "title"
    )
    decision = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="owner_entered",
        selected_value={"kind": "text", "value": title},
        idempotency_key=f"browser-owner-entered-title-{at}",
        occurred_at=at,
        previous=dict(
            repository.previous_field_values(
                report.target.connection, report.target.library, report.target.item
            )
        ).get("title"),
    )
    repository.record_human_decision(decision)
    authority = PersistedFieldEffectAuthority(repository)
    service, target = _guarded_application(repository, root, authority)
    if at == 900:
        repository.grant_field_effect_consents(
            report.target.connection, (report.target.library,), ("title",), 12
        )
    snapshot = authority.resolve(decision)
    operation = service.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=snapshot.destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest="c" * 64,
    )
    assert operation is not None
    target.seed(operation.plan)
    assert service.apply(operation.plan.plan_id).state == "verified"
    return _read_back_title(repository, report, case, destination, title, at + 5)


def _read_back_title(
    repository, report, case: ReviewCase, destination: str, title: str, at: int
):
    """Publish the work as the library now reads it, with this title, under
    the policy `publish_genre_case` activated, and admit its successor case."""
    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from tests.support.owner_values import rewritten

    successor = rewritten(report, title=title)
    repository.publish(successor, lambda: True, report.report_id)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    } | {"genres": destination}
    resolved = resolve_policy(
        MetadataPolicyProfile.create(
            "Genre evidence",
            dict(DEFAULT_OPTIONS) | {"genres": "fill_empty"},
            destinations,
        ),
        report.target.library,
    )
    assert (
        resolved.resolved_id
        == json.loads(repository.get_proposal(case.proposal_set_id))[
            "resolved_policy_id"
        ]
    )
    successor_proposal = evaluate_report(
        successor, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish_proposal(successor_proposal)
    admitted = json.loads(repository.admit_review_case(successor_proposal))
    assert admitted["status"] == "superseded", admitted
    repository.persist_shadow_outcome(admitted["review_case_id"], at)
    return admitted["review_case_id"], successor


_FILL_AND_ACCENT = """node => {
    const probe = document.createElement('span');
    probe.style.backgroundColor = 'var(--accent-strong)';
    document.body.append(probe);
    const accent = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return [getComputedStyle(node).backgroundColor, accent];
}"""


def _assert_primary_fill(primary: Locator, secondary: Locator | None) -> None:
    """The primary is filled with the theme's accent, unlike its neighbour (#1289).

    It first waits out a theme change, whose fill can trail it by a frame.
    """
    try:
        primary.page.wait_for_function(
            f"node => (([fill, accent]) => fill === accent)(({_FILL_AND_ACCENT})(node))",
            arg=primary.element_handle(),
            timeout=5_000,
        )
    except PlaywrightTimeoutError:
        pass  # The assertion below reports the fill and accent it saw.
    fill, accent = primary.evaluate(_FILL_AND_ACCENT)
    assert fill == accent, (fill, accent)
    if secondary is not None:
        assert secondary.evaluate(_FILL_AND_ACCENT)[0] != fill


def _login(page: Page, origin: str) -> None:
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page.goto(f"{origin}/login")
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


@contextmanager
def _setup_asset_server(
    certificate: Path,
    key: Path,
    port: ReservedPort,
    react_asset_loader: ReactAssetLoader | None = None,
) -> Iterator[str]:
    class State:
        def read(self, _verifier: str) -> SetupStateDocument:
            return SetupStateDocument(
                schema_version="setup-state-v4",
                revision=1,
                bootstrap_verifier_digest="0" * 64,
                claim_token_digest=claim_token_digest(_CLAIM_TOKEN),
            )

    origin = f"https://127.0.0.1:{port.number}"
    app = create_setup_app(
        cast(Any, object()),
        cast(Any, State()),
        "",
        canonical_origin=origin,
        react_asset_loader=react_asset_loader,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port.number,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
        )
    )
    port.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic setup server did not start")
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@contextmanager
def _closing_context(context: BrowserContext) -> Iterator[None]:
    try:
        yield
    finally:
        context.close()


def _record_page_error(errors: list[str], error: Any) -> None:
    errors.append(str(error))


def _record_transition_console(
    errors: list[str], records: list[dict[str, object]], message: Any
) -> None:
    if message.type == "error":
        errors.append(message.text)
    elif message.text.startswith("__ML_PREPARATION_TRANSITION__"):
        records.append(
            json.loads(message.text.removeprefix("__ML_PREPARATION_TRANSITION__"))
        )


def _serve_setup_asset(client: TestClient, route: Any) -> None:
    path = urlparse(route.request.url).path
    response = client.get("/" if path == "/setup-handoff" else path)
    route.fulfill(
        status=response.status_code,
        headers=dict(response.headers),
        body=response.content,
    )


@pytest.fixture(scope="module", autouse=True)
def _verified_playwright_browser() -> None:
    """Refuse the module before its first test, as `setUpClass` did.

    The `browser-payload-evidence-v1` record it returns names the Chromium
    revision, tree digest, Playwright version and platform, which is what
    makes "did the browser change between the failing run and the passing
    re-run" answerable. It was previously computed and discarded on the exact
    path that then fails, so no sighting could answer it (#730).

    It goes to `sys.__stderr__` because that is the only stream that survives
    the tier. `scripts/run_test_tier.py` redirects `sys.stdout` and
    `sys.stderr` to `os.devnull` around `pytest.main`, and `--capture=sys`
    replaces them again, so a bare `print` from a test reaches nothing. The
    tier's own stall breadcrumb uses this same seam for this same reason.
    """

    print(
        json.dumps(verify_playwright_browser(), sort_keys=True, separators=(",", ":")),
        file=sys.__stderr__,
        flush=True,
    )


def test_remembered_owner_session_survives_profile_and_server_restart() -> None:
    """Exercise the fixed, verifier-bound browser session through real restarts."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        database = root / "review.sqlite3"
        profile = root / "persistent-profile"
        hash_path = root / "owner.hash"
        old_password = _PASSWORD
        new_password = "replacement synthetic browser password"
        hash_path.write_text(PasswordHasher().hash(old_password), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        now = [1_000]
        errors: list[str] = []
        # The real workspace asks for settings alongside its Library data.
        # Seed the same synthetic complete configuration as `_server` so a
        # lifecycle check never reads an expected setup failure as a session
        # failure or browser-console error.
        seed_repository = open_repository(database)
        seed_repository.close()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "INSERT INTO owner_settings VALUES(1,?,?)",
                (
                    _DEFAULT_SETTINGS_DOCUMENT.revision,
                    _canonical_json(_DEFAULT_SETTINGS_DOCUMENT.model_dump()),
                ),
            )
            connection.executemany(
                "INSERT INTO owner_setting_secrets(field_id,value) VALUES(?,?)",
                [
                    (f"connection.{item.name}.key", f"synthetic-{item.name}-secret")
                    for item in _DEFAULT_SETTINGS_DOCUMENT.connections
                ],
            )
            connection.commit()

        sessions_database = database.with_name("review-sessions.sqlite")

        def persisted_sessions() -> list[tuple[int, int]]:
            with closing(sqlite3.connect(sessions_database)) as connection:
                return cast(
                    list[tuple[int, int]],
                    connection.execute(
                        "SELECT created_at, expires_at FROM review_sessions "
                        "WHERE revoked_at IS NULL ORDER BY created_at"
                    ).fetchall(),
                )

        def revocations() -> list[tuple[int]]:
            with closing(sqlite3.connect(sessions_database)) as connection:
                return cast(
                    list[tuple[int]],
                    connection.execute(
                        "SELECT revoked_at FROM review_sessions "
                        "WHERE revoked_at IS NOT NULL ORDER BY created_at"
                    ).fetchall(),
                )

        def persistent_page(playwright) -> tuple[BrowserContext, Page]:
            context = playwright.chromium.launch_persistent_context(
                str(profile),
                headless=True,
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.route(
                "**/favicon.ico", lambda route: route.fulfill(status=204, body="")
            )
            page.on("pageerror", lambda error: errors.append(str(error)))

            def record_console_error(message) -> None:
                # The two deliberate 401s below are the expiry and verifier
                # replacement proofs. Chromium logs their transport status as
                # console errors even though the workspace handles expiry by
                # returning the owner to Login.
                if message.type == "error" and "status of 401" not in message.text:
                    errors.append(message.text)

            page.on("console", record_console_error)
            return context, page

        @contextmanager
        def running_server(port: int, listener: socket.socket) -> Iterator[str]:
            repository = open_repository(database, create=True, cross_thread=True)
            # The session store must be the real file this test reads back, not
            # the fixture's in-memory fallback: remembered authority surviving a
            # restart is exactly what is under test, and an in-memory store
            # would discard it at every server boundary (#912).
            session_store = SQLiteReviewSessionRepository.open(sessions_database)
            origin = f"https://127.0.0.1:{port}"
            app = create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    tls_certificate_file=str(certificate),
                    tls_private_key_file=str(key),
                    canonical_origin=origin,
                    owner_password_hash_file=str(hash_path),
                    session_lifetime_seconds=300,
                ),
                session_store=session_store,
                clock=lambda: now[0],
                close_repository=True,
                field_application=_BrowserActivityApplication(),
                react_asset_loader=ReactAssetLoader(
                    Path(os.environ["ML_REACT_ASSET_DIR"])
                ),
                settings=OwnerSettingsOperations(repository.owner_settings_store()),
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=port,
                    # A duplicate of this listener lets each application own
                    # and close its server socket while the test keeps one
                    # exact HTTPS origin reserved for the next generation.
                    fd=listener.fileno(),
                    log_level="error",
                    access_log=False,
                    ssl_certfile=str(certificate),
                    ssl_keyfile=str(key),
                    timeout_keep_alive=30,
                )
            )
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            deadline = time.monotonic() + 10
            while (
                not server.started and thread.is_alive() and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            if not server.started:
                raise RuntimeError("remembered-session server did not start")
            try:
                yield origin
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                if thread.is_alive():
                    server.force_exit = True
                    thread.join(timeout=5)
                session_store.close()

        listener = bind_loopback(0)
        assert listener is not None
        with sync_playwright() as playwright, closing(listener):
            port = int(listener.getsockname()[1])
            with running_server(port, listener) as origin:
                context, page = persistent_page(playwright)
                _login(page, origin)
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                assert persisted_sessions() == [(1_000, 1_300)]
                context.close()

            now[0] = 1_100
            with running_server(port, listener) as origin:
                context, page = persistent_page(playwright)
                page.goto(f"{origin}/library")
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                page.reload()
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                assert persisted_sessions() == [(1_000, 1_300)]
                storage = page.evaluate(
                    """async () => ({
                      local: Object.entries(localStorage),
                      session: Object.entries(sessionStorage),
                      indexed: indexedDB.databases
                        ? (await indexedDB.databases()).map((database) => database.name)
                        : [],
                    })"""
                )
                serialized_storage = json.dumps(storage).lower()
                assert old_password not in serialized_storage
                assert all(
                    marker not in serialized_storage
                    for marker in ("password", "credential", "ml_session")
                )
                page.get_by_role("button", name="Sign out").click()
                page.wait_for_url(f"{origin}/login")
                assert persisted_sessions() == []
                assert revocations() == [(1_100,)]
                # A theme chosen at sign-in is remembered like the workspace's,
                # so it survives the profile restart below (#1013). Until one is
                # saved, sign-in follows the system live; afterwards it does not.
                # The system theme is followed by the stylesheet alone, so no
                # choice is written to the document until the owner saves one.
                html = page.locator("html")
                body = page.locator("body")
                toggle = page.locator(".theme-toggle")
                page.emulate_media(color_scheme="dark")
                expect(body).to_have_css("background-color", "rgb(15, 21, 18)")
                expect(toggle).to_contain_text("Light mode")
                page.emulate_media(color_scheme="light")
                expect(body).to_have_css("background-color", "rgb(244, 242, 236)")
                expect(toggle).to_contain_text("Dark mode")
                assert html.get_attribute("data-theme") is None
                page.get_by_role("button", name="Dark mode").click()
                expect(html).to_have_attribute("data-theme", "dark")
                page.emulate_media(color_scheme="dark")
                page.emulate_media(color_scheme="light")
                expect(html).to_have_attribute("data-theme", "dark")
                expect(body).to_have_css("background-color", "rgb(15, 21, 18)")
                _login(page, origin)
                assert persisted_sessions() == [(1_100, 1_400)]
                context.close()

            now[0] = 1_200
            with running_server(port, listener) as origin:
                context, page = persistent_page(playwright)
                page.goto(f"{origin}/library")
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                assert persisted_sessions() == [(1_100, 1_400)]
                policy = page.locator('a[href="/policy?section=identity"]')
                expect(policy).to_be_attached()

                def expire_policy_request(route) -> None:
                    now[0] = 1_400
                    route.continue_()

                page.route("**/api/v1/metadata-policy/state", expire_policy_request)
                policy.evaluate("(element) => element.click()")
                # The expired session signs in again toward the page it was opening.
                expect(page).to_have_url(
                    f"{origin}/login?next=%2Fpolicy%3Fsection%3Didentity"
                )
                _login(page, origin)
                assert persisted_sessions() == [(1_400, 1_700)]
                context.close()

            hash_path.write_text(PasswordHasher().hash(new_password), encoding="utf-8")
            now[0] = 1_450
            with running_server(port, listener) as origin:
                context, page = persistent_page(playwright)
                page.goto(f"{origin}/library")
                page.wait_for_url(f"{origin}/login")
                page.get_by_label("Password").fill(old_password)
                page.get_by_role("button", name="Sign in").click()
                expect(page.locator("#login-status")).to_have_text(
                    "That password was not accepted. Check it and try again."
                )
                page.get_by_label("Password").fill(new_password)
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(f"{origin}/library")
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                context.close()

        assert errors == []


# Reports, on the console, each cross-document navigation the page starts and
# each settled Library read two tasks late: a 401's expiry hand-off is a 0 ms
# timer queued as that response settles, so by then it has run and navigated,
# if it would. (A navigating page can no longer be evaluated.) The probe marks
# its reads, so only they are held, not reads of the page it replaced.
_SIGN_OUT_NAVIGATION_PROBE = """
navigation.addEventListener('navigate', (event) => {
  if (!event.destination.sameDocument) console.debug('ml-1588 navigate', event.destination.url)
})
const send = window.fetch
window.fetch = function (input, init) {
  if (!String(input instanceof Request ? input.url : input).includes('/api/v1/library'))
    return send.call(this, input, init)
  const headers = new Headers(init?.headers)
  headers.set('x-ml-1588-probe', '1')
  const sent = send.call(this, input, { ...init, headers })
  const settle = () =>
    setTimeout(() => setTimeout(() => console.debug('ml-1588 read-settled'), 0), 0)
  sent.then(settle, settle)
  return sent
}
"""


@pytest.mark.parametrize("read_answers", ["before_login_commits", "before_logout_ends"])
def test_sign_out_lands_on_login_when_a_held_read_answers_401(
    read_answers: str,
) -> None:
    """A Library read held across Sign out answers 401 once the session is
    revoked. Sign out alone navigates, to /login exactly: the read's expiry
    must neither abort that navigation nor replace it with /login?next (#1588).
    /login documents are held, so the signed-in page records every navigation
    it starts."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright, closing(ReservedPort()) as port:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_context(ignore_https_errors=True).new_page()
            with _server(
                database=root / "review.sqlite",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=port,
            ) as (origin, _case_id):
                _login(page, origin)
                reads: list[Route] = []
                documents: list[Route] = []
                logouts: list[tuple[Route, APIResponse]] = []
                navigations: list[str] = []
                settled: list[str] = []

                def observe(message: ConsoleMessage) -> None:
                    kind, _, value = message.text.removeprefix("ml-1588 ").partition(
                        " "
                    )
                    if message.text.startswith("ml-1588 "):
                        (navigations if kind == "navigate" else settled).append(value)

                def until(condition: Callable[[], bool]) -> None:
                    for _ in range(500):
                        if condition():
                            return
                        page.wait_for_timeout(20)
                    raise AssertionError("condition not reached")

                def release_reads() -> None:
                    held = len(reads)
                    for route in reads:
                        route.continue_()
                    until(lambda: len(settled) >= held)

                page.add_init_script(_SIGN_OUT_NAVIGATION_PROBE)
                page.on("console", observe)
                page.route(
                    "**/api/v1/library?*",
                    lambda route: (
                        reads.append(route)
                        if route.request.headers.get("x-ml-1588-probe")
                        else route.continue_()
                    ),
                )
                page.route(
                    re.compile(r".*/login(\?.*)?$"),
                    lambda route: (
                        documents.append(route)
                        if route.request.is_navigation_request()
                        else route.continue_()
                    ),
                )
                if read_answers == "before_logout_ends":
                    # The server revokes the session; its answer is held.
                    page.route(
                        "**/api/v1/session/logout",
                        lambda route: logouts.append((route, route.fetch())),
                    )
                page.goto(f"{origin}/library?lens=all")
                until(lambda: len(reads) >= 1)
                # The /login document is held, so the click must not wait for it.
                page.get_by_role("button", name="Sign out").click(no_wait_after=True)
                if read_answers == "before_login_commits":
                    until(lambda: len(documents) >= 1)
                    release_reads()
                else:
                    until(lambda: len(logouts) == 1)
                    release_reads()
                    logout, response = logouts[0]
                    logout.fulfill(response=response)
                login = f"{origin}/login"
                until(lambda: login in navigations)
                assert navigations == [login]
                until(lambda: len(documents) >= 1)
                documents[0].continue_()
                page.wait_for_url(login)
                expect(page.get_by_role("button", name="Sign in")).to_be_visible()
            browser.close()


@pytest.mark.parametrize("transport_mode", ["direct", "http", "proxy"])
def test_persistent_workspace_direct_entry_navigation_and_safe_decision(
    transport_mode: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--host-resolver-rules=MAP review-transport.test 127.0.0.1",
                    "--no-proxy-server",
                ],
            )
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    transport_mode=transport_mode,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                assert page.evaluate("isSecureContext") is True
                assert page.evaluate("typeof crypto.randomUUID") == "function"
                try:
                    page.get_by_role("heading", name="Works", exact=True).wait_for(
                        timeout=8_000
                    )
                except PlaywrightTimeoutError:
                    document = page.request.get(page.url)
                    pytest.fail(
                        f"url={page.url!r}; errors={errors!r}; "
                        f"status={document.status}; html={document.text()[:1200]!r}; "
                        f"dom={page.content()[:1200]!r}"
                    )
                page.set_viewport_size({"width": 1440, "height": 900})
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 1440
                )
                page.locator(".library-work-row").click()
                page.wait_for_url(f"**/library?*preview={case_id}*")
                page.set_viewport_size({"width": 390, "height": 844})
                brand_box = page.locator(".brand").bounding_box()
                actions_box = page.locator(".shell-actions").bounding_box()
                primary_navigation = page.get_by_role(
                    "navigation", name="Primary workspace"
                )
                navigation_box = primary_navigation.bounding_box()
                settings_box = primary_navigation.get_by_role(
                    "link", name="Settings", exact=True
                ).bounding_box()
                assert (
                    brand_box is not None
                    and actions_box is not None
                    and navigation_box is not None
                    and settings_box is not None
                )
                assert primary_navigation.get_by_role("link").all_inner_texts() == [
                    "Library",
                    "Policy",
                    "Activity",
                    "Settings",
                ]
                assert settings_box["width"] >= 44
                assert settings_box["height"] >= 44
                assert navigation_box["y"] >= max(
                    brand_box["y"] + brand_box["height"],
                    actions_box["y"] + actions_box["height"],
                )
                assert navigation_box["x"] <= settings_box["x"]
                assert settings_box["x"] + settings_box["width"] <= (
                    navigation_box["x"] + navigation_box["width"]
                )
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"
                )
                documents = 0

                def count_document(request) -> None:
                    nonlocal documents
                    if request.resource_type == "document":
                        documents += 1

                page.on("request", count_document)
                initial_documents = documents
                page.get_by_role("button", name=re.compile(r"^Attention ")).click()
                attention_rail = page.locator(".attention-rail")
                attention_rail.wait_for()
                expect(attention_rail.locator("h2")).to_be_focused()
                page.get_by_role("link", name="Policy", exact=True).click()
                page.get_by_role("heading", name="Library policy").wait_for()
                page.get_by_role("button", name="Curation").click()
                page.wait_for_url("**/policy?section=curation")
                expect(page.locator("#policy-curation")).to_be_focused()
                # A fresh container activated nothing. The seeded built-in
                # policy is named as the active baseline and never reported as
                # a completed owner activation over zero works.
                page.locator(".cm-policy-sidebar > header").get_by_text(
                    "Active baseline", exact=True
                ).wait_for()
                policy_body = page.locator("body").inner_text()
                assert "Activation complete" not in policy_body
                assert "Policy update complete" not in policy_body
                assert "0 of 0" not in policy_body
                assert "Draft is separate from the active policy" not in policy_body
                assert page.locator(".policy-cutover").count() == 0
                page.locator(".cm-finish-tray").get_by_text(
                    "Change any field to create a new draft \u2014 validation always "
                    "covers the whole policy.",
                    exact=True,
                ).wait_for()
                policy_chapters = page.get_by_role("navigation", name="Policy chapters")
                # A chapter with no draft change carries no count at all.
                assert policy_chapters.locator("button b").all_inner_texts() == []
                assert page.locator(".cm-policy-chapter-legend").count() == 0
                assert (
                    policy_chapters.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 2
                )
                assert (
                    policy_chapters.get_by_role(
                        "button", name="Curation"
                    ).get_attribute("aria-describedby")
                    is None
                )
                page.get_by_text(
                    "Choose how metadata is stored and when you want to review it.",
                    exact=True,
                ).wait_for()
                unsupported_description = policy_chapters.get_by_role(
                    "button", name="Additional fields"
                ).get_by_text(
                    "Fields with limited support that start off until you enable them.",
                    exact=True,
                )
                assert unsupported_description.count() == 1
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                ), page.evaluate(
                    """() => [...document.querySelectorAll('*')]
                        .filter(node => node.scrollWidth > node.clientWidth)
                        .map(node => [node.tagName, node.className, node.textContent?.slice(0, 40), node.clientWidth, node.scrollWidth, getComputedStyle(node).gridTemplateColumns])"""
                )
                genres = page.get_by_role("region", name="Genres")
                representation = genres.get_by_role(
                    "combobox", name="Genres representation"
                )
                representation.focus()
                page.keyboard.press("Enter")
                page.get_by_role("listbox", name="Genres representation").wait_for()
                page.keyboard.press("Escape")
                expect(representation).to_be_focused()
                representation.click()
                menu_box = page.get_by_role("listbox", name="Genres representation")
                expect(menu_box).to_have_css("scale", "none")
                menu_box = menu_box.bounding_box()  # type: ignore[assignment]  # reason: the name is deliberately rebound from the Locator to its measured bounding box, which the next lines assert on.
                assert menu_box is not None
                assert menu_box is not None
                assert menu_box["x"] >= 8
                assert (menu_box["x"] + menu_box["width"]) <= 382
                for option in page.get_by_role("option").all():
                    assert option.bounding_box()["height"] >= 44
                page.keyboard.press("Escape")
                # Tab closes the portaled menu onto its trigger, in both
                # directions, instead of leaving for the end of the page (#1288).
                for leaving in ("Tab", "Shift+Tab"):
                    representation.click()
                    page.get_by_role("listbox", name="Genres representation").wait_for()
                    page.keyboard.press(leaving)
                    expect(representation).to_be_focused()
                    assert page.locator(".cm-dropdown-menu").count() == 0
                for control in page.locator(
                    ".cm-policy-sidebar nav button, .cm-segmented button, "
                    ".cm-dropdown-trigger, .cm-finish-actions button"
                ).all():
                    assert control.bounding_box()["height"] >= 44
                assert (
                    page.locator(".cm-policy-field").first.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 1
                )
                assert (
                    page.locator(".cm-finish-tray").evaluate(
                        "node => getComputedStyle(node).position"
                    )
                    == "static"
                )
                page.emulate_media(reduced_motion="reduce")
                assert page.locator(".cm-dropdown-menu").count() == 0
                genres_mode = genres.get_by_role("group", name="Genres review mode")
                original_mode = next(
                    button.inner_text()
                    for button in genres_mode.get_by_role("button").all()
                    if button.get_attribute("aria-pressed") == "true"
                )
                genres.get_by_role("button", name="Ignore").click()
                assert policy_chapters.locator("button b").all_inner_texts() == ["1"]
                # An exact revert returns every surface to the clean state: no
                # chapter count, no dirty status, no validation action. The
                # tray states it once; only the live region repeats it.
                genres.get_by_role("button", name=original_mode, exact=True).click()
                assert policy_chapters.locator("button b").all_inner_texts() == []
                page.locator(".cm-finish-tray").get_by_text(
                    "Draft matches the active policy", exact=True
                ).wait_for()
                page.get_by_text("Draft matches active policy.", exact=True).wait_for()
                assert (
                    page.evaluate("""() => [...document.querySelectorAll('body *')]
                        .filter(node => !node.closest('.sr-only')
                            && node.checkVisibility()
                            && [...node.childNodes].some(text => text.nodeType === 3
                                && text.data.includes('Draft matches')))
                        .length""")
                    == 1
                )
                assert page.get_by_role(
                    "button", name="Validate and preview"
                ).is_disabled()
                genres.get_by_role("button", name="Ignore").click()
                assert policy_chapters.locator("button b").all_inner_texts() == ["1"]
                _assert_primary_fill(
                    page.get_by_role("button", name="Validate and preview"), None
                )

                def assert_text_zoom_policy_layout(width: int) -> None:
                    page.set_viewport_size({"width": width, "height": 844})
                    page.wait_for_function(
                        """() => getComputedStyle(document.querySelector(
                            '.cm-policy-sidebar nav')).gridTemplateColumns.split(' ').length === 1"""
                    )
                    assert (
                        page.evaluate("() => document.documentElement.scrollWidth")
                        <= width
                    )
                    assert (
                        page.evaluate(
                            """() => [...document.querySelectorAll(
                            `.masthead nav, .masthead nav a, .cm-policy-route,
                            .cm-policy-editor, .cm-finish-tray,
                            .cm-finish-actions, .cm-finish-actions button`)]
                            .filter(node => node.scrollWidth > node.clientWidth &&
                            getComputedStyle(node).overflowX === 'visible')
                            .map(node => [node.tagName, node.className, node.clientWidth, node.scrollWidth])"""
                        )
                        == []
                    )

                set_root_text(page, "200%")
                assert_text_zoom_policy_layout(390)
                assert (
                    page.locator(".cm-policy-sidebar nav").evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 1
                )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert page.get_by_role("link", name="Policy", exact=True).is_visible()
                assert page.get_by_role(
                    "link", name="Activity", exact=True
                ).is_visible()
                assert not (
                    page.evaluate(
                        """() => [...document.querySelectorAll('*')].some(node =>
                        node.scrollWidth > node.clientWidth &&
                        !node.classList.contains('brand-mark') &&
                        getComputedStyle(node).overflowX === 'visible')"""
                    )
                )
                assert_text_zoom_policy_layout(430)
                page.get_by_role("button", name="Use dark mode").click()
                page.get_by_role("button", name="Use light mode").wait_for()
                assert_text_zoom_policy_layout(390)
                assert_text_zoom_policy_layout(430)
                page.get_by_role("button", name="Use light mode").click()
                page.get_by_role("button", name="Use dark mode").wait_for()
                page.set_viewport_size({"width": 390, "height": 844})
                set_root_text(page, "")
                page.get_by_role("button", name="Validate and preview").click()
                page.get_by_text(
                    "Impact preview is current. The active policy is unchanged."
                ).wait_for()
                review_activation = page.get_by_role("button", name="Review activation")
                set_root_text(page, "200%")
                assert_text_zoom_policy_layout(430)
                set_root_text(page, "")
                review_activation.click()
                page.get_by_text("Activate this reviewed draft?").wait_for()
                assert page.get_by_role("dialog").count() == 0
                # The served consent state holds no permission, so the Activate
                # step says activating grants none rather than nothing (#1370).
                page.get_by_text(
                    "No library has write permission, and activating grants none."
                ).wait_for()
                set_root_text(page, "200%")
                assert_text_zoom_policy_layout(430)
                set_root_text(page, "")
                page.get_by_role("button", name="Back to preview").click()
                review_activation.wait_for()
                back_to_editing = page.get_by_role("button", name="Back to editing")
                _assert_primary_fill(review_activation, back_to_editing)
                dark_mode = page.get_by_role("button", name="Use dark mode")
                dark_mode.click()
                # The label changes on render and the theme in an effect after
                # it, so the dark fill check waits for the theme itself.
                expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                _assert_primary_fill(review_activation, back_to_editing)
                page.set_viewport_size({"width": 1440, "height": 900})
                unsupported_description.wait_for()
                assert page.get_by_role(
                    "navigation", name="Policy chapters"
                ).is_visible()
                # The sticky finish tray spans both columns and never covers a
                # sidebar safety control, with the safety group expanded.
                if not page.locator(".cm-policy-mode").evaluate("node => node.open"):
                    page.locator(".cm-policy-mode summary").click()
                page.get_by_role("button", name="Enter Preview mode").wait_for()
                sidebar_box = page.locator(".cm-policy-sidebar").bounding_box()
                tray_box = page.locator(".cm-finish-tray").bounding_box()
                assert sidebar_box is not None and tray_box is not None
                sidebar_right = sidebar_box["x"] + sidebar_box["width"]
                assert tray_box["x"] < sidebar_right
                assert (tray_box["x"] + tray_box["width"]) > sidebar_right
                covered = """() => {
                    const rail = document.querySelector('.cm-policy-sidebar')
                    const side = rail.getBoundingClientRect()
                    const tray = document.querySelector(
                        '.cm-finish-tray').getBoundingClientRect()
                    return [...rail.querySelectorAll('button, summary')]
                        .map(node => [node, node.getBoundingClientRect()])
                        .filter(([, box]) => {
                            const top = Math.max(box.top, side.top)
                            const bottom = Math.min(box.bottom, side.bottom)
                            return bottom > top && bottom > tray.top &&
                                top < tray.bottom && box.right > tray.left &&
                                box.left < tray.right
                        })
                        .map(([node]) => node.textContent.slice(0, 48))
                    }"""
                assert page.evaluate(covered) == []
                # Anything the bounded rail clips stays reachable by scrolling
                # the rail itself, and is still never under the tray.
                assert (
                    page.locator(".cm-policy-sidebar").evaluate(
                        "node => getComputedStyle(node).overflowY"
                    )
                    != "visible"
                )
                page.locator(".cm-policy-sidebar").evaluate(
                    "node => { node.scrollTop = node.scrollHeight }"
                )
                page.get_by_role("button", name="Pause automatic decisions").click()
                page.get_by_role("button", name="Resume automatic decisions").wait_for()
                assert page.evaluate(covered) == []
                page.get_by_role("button", name="Resume automatic decisions").click()
                page.get_by_role("button", name="Pause automatic decisions").wait_for()
                review_activation.click()
                page.get_by_role("button", name="Back to preview").click()
                review_activation.wait_for()
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 1440
                )
                page.set_viewport_size({"width": 390, "height": 844})
                light_mode = page.get_by_role("button", name="Use light mode")
                light_mode.click()
                page.get_by_role("button", name="Use dark mode").wait_for()
                activity_requests = 0
                held_page_size_response: list[tuple[Any, Any, Any]] = []

                def activity_poll(route) -> None:
                    nonlocal activity_requests
                    activity_requests += 1
                    response = route.fetch()
                    payload = response.json()
                    payload["counts"]["claimed"] = 1
                    query = parse_qs(urlparse(route.request.url).query)
                    payload["ledger"].update(
                        page=int(query.get("page", ["1"])[0]),
                        page_count=3,
                        page_size=int(query.get("page_size", ["25"])[0]),
                        total=75,
                    )
                    if query.get("page_size") == ["50"] and not held_page_size_response:
                        held_page_size_response.append((route, response, payload))
                        page.evaluate(
                            "console.debug('activity-page-size-response-held')"
                        )
                        return
                    route.fulfill(status=response.status, json=payload)

                page.route("**/api/v1/activity?*", activity_poll)
                page.get_by_role("link", name="Activity", exact=True).click()
                # This harness serves prepared work with no consent yet, and
                # that is what the panel says. It used to claim "No
                # intervention needed" directly above its own
                # `3 Awaiting permission`, which is the claim #639 forbids.
                page.get_by_role(
                    "heading",
                    name="Writes are off. Review permissions when you are ready.",
                ).wait_for()
                expect(
                    page.get_by_text("Ready for permission", exact=True)
                ).to_have_count(0)
                expect(
                    page.get_by_text("Awaiting permission", exact=True)
                ).to_have_count(1)
                expect(page.locator(".activity-consent-library")).to_have_count(0)
                page.get_by_role("button", name="Review write permissions").click()
                page.get_by_text(
                    "Backup readiness checked. Review the included and excluded destinations.",
                    exact=True,
                ).wait_for()
                page.get_by_role(
                    "heading", name="3 prepared changes are ready. Writes are off."
                ).wait_for()
                expect(
                    page.get_by_text("Ready for permission", exact=True)
                ).to_have_count(0)
                expect(
                    page.get_by_text("Awaiting permission", exact=True)
                ).to_have_count(1)
                expect(
                    page.get_by_role("button", name="Confirm write permissions")
                ).to_be_disabled()
                # The guarded confirmation is the filled primary beside a plain
                # Cancel, rather than reading as a second Cancel (#1289).
                _assert_primary_fill(
                    page.get_by_role("button", name="Confirm write permissions"),
                    page.get_by_role("button", name="Cancel permission review"),
                )
                expect(
                    page.get_by_role(
                        "heading", name="Prepared work needs your permission"
                    )
                ).to_have_count(0)
                for calm in (
                    "No intervention needed",
                    "No action required",
                    "No action is required.",
                ):
                    expect(page.get_by_text(calm, exact=True)).to_have_count(0)
                assert activity_requests >= 1
                activity_search = page.get_by_role("searchbox", name="Search activity")
                activity_search.click()
                activity_search.press_sequentially("ab")
                assert activity_search.input_value() == "ab"
                page.wait_for_url("**/activity?*q=ab*")
                expect(activity_search).to_be_focused()
                next_page = page.get_by_role("button", name="Next", exact=True)
                next_page.click()
                page.wait_for_url("**/activity?*page=2*")
                expect(next_page).to_be_focused()
                page_number = page.get_by_role(
                    "spinbutton", name="Activity page number"
                )
                expect(page_number).to_have_value("2")
                page_number.fill("99")
                page_number.press("Enter")
                page.wait_for_url("**/activity?*page=3*")
                page_number = page.get_by_role(
                    "spinbutton", name="Activity page number"
                )
                assert page_number.input_value() == "3"
                expect(page_number).to_be_focused()
                page_number.fill("2")
                page_number.press("Escape")
                assert page_number.input_value() == "3"
                expect(page_number).to_be_focused()
                previous_page = page.get_by_role("button", name="Previous", exact=True)
                previous_page.click()
                page.wait_for_url("**/activity?*page=2*")
                previous_page.click()
                page.wait_for_url("**/activity?*page=1*")
                expect(
                    page.get_by_role("spinbutton", name="Activity page number")
                ).to_be_focused()
                page.get_by_role("button", name="Last", exact=True).click()
                page.wait_for_url("**/activity?*page=3*")
                expect(
                    page.get_by_role("spinbutton", name="Activity page number")
                ).to_be_focused()
                rows = page.get_by_role("combobox", name="Events per activity page")
                rows.click()
                with page.expect_console_message(
                    predicate=lambda message: (
                        message.text == "activity-page-size-response-held"
                    )
                ):
                    page.get_by_role("option", name="50 per page").click()
                expect(rows).to_be_focused()
                page.set_viewport_size({"width": 1440, "height": 900})
                activity_search = page.get_by_role("searchbox", name="Search activity")
                search_node = activity_search.element_handle()
                assert search_node is not None
                activity_search.fill("")
                assert activity_search.input_value() == ""
                expect(activity_search).to_be_focused()
                assert len(held_page_size_response) == 1
                held_route, held_response, held_payload = held_page_size_response[0]
                held_route.fulfill(status=held_response.status, json=held_payload)
                assert activity_search.input_value() == ""
                expect(activity_search).to_be_focused()
                assert search_node.evaluate("node => node.isConnected")
                activity_search.press_sequentially("desktop")
                assert activity_search.input_value() == "desktop"
                page.wait_for_url("**/activity?*q=desktop*")
                expect(activity_search).to_be_focused()
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_role("link", name="Library", exact=True).click()
                page.get_by_role("heading", name="Works", exact=True).wait_for()
                settled_activity_requests = activity_requests
                page.wait_for_timeout(5_500)
                assert activity_requests == settled_activity_requests
                page.unroute("**/api/v1/activity?*", activity_poll)
                assert documents == initial_documents
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.locator(".workspace-detail").wait_for()
                page.get_by_role(
                    "heading", name="Which title should this work use?"
                ).wait_for()
                expect(page.locator(".selected-work-header")).to_be_focused()
                assert page.locator("img,script:not([src])").count() == 0
                assert "<img" in page.locator("body").inner_text()
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert (
                    page.get_by_role(
                        "radio", name="Choose current value:", exact=False
                    ).count()
                    == 1
                )
                assert (
                    page.get_by_role(
                        "radio", name="Choose proposed value", exact=False
                    ).count()
                    == 1
                )
                page.get_by_role("button", name="Use dark mode").click()
                page.get_by_role("button", name="Use light mode").wait_for()
                page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).first.click()
                # The acknowledgement advances to the next field rather than
                # leaking a recorded status across the selected-work surface.
                page.get_by_role(
                    "heading", name="Which series should this work use?"
                ).wait_for()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.get_by_role("heading", name="Title").wait_for()
                # #635: revisiting a recorded field restores the whole choice
                # composition and marks the recorded answer, rather than
                # replacing it with prose.
                recorded_title = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                )
                assert recorded_title.count() == 1
                expect(recorded_title).to_have_attribute("aria-checked", "true")
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role("heading", name="Activity and recovery").wait_for()
                page.go_back()
                page.get_by_role("heading", name="Title").wait_for()
                page.go_forward()
                page.get_by_role("heading", name="Activity and recovery").wait_for()
                assert documents == (initial_documents + 2)
                assert errors == []
            browser.close()


def test_protected_cover_round_trip_preserves_explicit_owner_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        responses: list[tuple[str, int]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    responses.append((response.url, response.status))
                    if "/cover-considerations" in response.url
                    or response.url.endswith("/decisions")
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    automatic_fixture=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.get_by_role("heading", name="Works", exact=True).wait_for()

                # This is a closed presentation fixture, not a new automatic
                # authority path: its projections retain the exact candidate
                # values already supplied by the current case.  It protects
                # restoration of recorded automatic choices, including cover
                # values, without widening the qualified-policy envelope.
                read_only_title_candidate = False

                def automatic_projection(route) -> None:
                    response = route.fetch()
                    payload = response.json()
                    fields = payload["fields"]
                    outcomes = {
                        "title": "candidate",
                        "series": "keep_current",
                        "tags": "ignore",
                        "cover": "candidate",
                    }
                    for index, field in enumerate(fields, 1):
                        outcome = outcomes.get(field["destination"])
                        if outcome is None:
                            continue
                        if (
                            field["destination"] == "cover"
                            and field["cover_consideration"] is None
                        ):
                            continue
                        selected = None
                        if outcome == "candidate":
                            selected = (
                                next(
                                    {
                                        "kind": "cover",
                                        "value": candidate["artifact_id"],
                                    }
                                    for candidate in field["cover_consideration"][
                                        "candidates"
                                    ]
                                    if candidate["selectable"]
                                )
                                if field["destination"] == "cover"
                                else field["proposed_values"][0]
                            )
                        read_only_candidate = (
                            field["destination"] == "title"
                            and read_only_title_candidate
                        )
                        if read_only_candidate:
                            field["human_decision"] = None
                            for context in field["choice_contexts"]:
                                context["evidence"] = [
                                    {
                                        "source": (
                                            "Automatic candidate source"
                                            if context["selection"] == "proposed"
                                            else "Automatic current source"
                                        ),
                                        "observation": "Synthetic automatic evidence.",
                                        "role": "external_corroboration",
                                    }
                                ]
                        field.update(
                            automatic_decision={
                                "candidate_id": (
                                    None
                                    if outcome != "candidate"
                                    else "alternative_" + f"{index:064x}"
                                ),
                                "decision_id": "automatic_decision_" + f"{index:064x}",
                                "destination": field["destination"],
                                "occurred_at": 1_000,
                                "outcome": outcome,
                                "proposal_id": field["proposal_id"],
                                "review_case_id": payload["review_case_id"],
                                "selected_value": selected,
                                "target_fingerprint": field["target_fingerprint"],
                            },
                            classification="automatic_recorded",
                            classification_reason=(
                                "Automatic handling recorded this field; "
                                "you may inspect or override it."
                            ),
                            decision_actions=[]
                            if read_only_candidate
                            else field["decision_actions"],
                            editable=not read_only_candidate,
                            needs_attention=False,
                            navigation_role="inspect",
                        )
                    route.fulfill(status=response.status, json=payload)

                page.route(f"**/api/v1/review-cases/{case_id}", automatic_projection)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                title_current = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                )
                title_candidate = page.get_by_role(
                    "radio", name="Choose proposed value", exact=False
                )
                expect(title_candidate).to_have_attribute("aria-checked", "true")
                assert title_candidate.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(title_current).to_have_attribute("aria-checked", "false")
                assert not (
                    title_current.locator("xpath=..").evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(title_candidate).to_have_attribute("aria-checked", "true")
                assert title_candidate.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as override_response:
                    before = page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()
                    title_current.focus()
                    page.keyboard.press("Enter")
                assert 200 == override_response.value.status
                # Acknowledged exact-case state advances the owner. Reopening
                # remains the place to inspect and revise the recorded title.
                page.wait_for_function(_CURRENT_FIELD_CHANGED, arg=before)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                expect(title_current).to_have_attribute("aria-checked", "true")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                series_current = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                )
                expect(series_current).to_have_attribute("aria-checked", "true")
                assert series_current.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                read_only_title_candidate = True
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                page.get_by_role("heading", name="Title", exact=True).wait_for()
                recorded = page.get_by_label("Title recorded outcome")
                recorded.get_by_text("Resolved automatically", exact=True).wait_for()
                expect(recorded.get_by_text("Chosen value", exact=True)).to_be_visible()
                expect(
                    recorded.get_by_text("Synthetic title revised", exact=True)
                ).to_be_visible()
                assert (
                    page.get_by_role(
                        "radio", name="Choose proposed value", exact=False
                    ).count()
                    == 0
                )
                recorded.get_by_role("button", name="Evidence", exact=True).click()
                recorded.get_by_text(
                    "Automatic candidate source", exact=True
                ).wait_for()
                assert (
                    recorded.get_by_text("Automatic current source", exact=True).count()
                    == 0
                )
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                page.get_by_role("region", name="Choose a cover").wait_for()
                expect(page.get_by_alt_text("Current cover")).to_be_visible()
                expect(page.get_by_alt_text("EPUB file cover")).to_be_visible()
                current_cover = page.locator(".library-cover-card").filter(
                    has_text="Current cover"
                )
                automatic_cover = page.locator(".library-cover-card").filter(
                    has=page.get_by_alt_text("EPUB file cover")
                )
                expect(automatic_cover).to_have_attribute("aria-checked", "true")
                assert automatic_cover.evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(current_cover).to_have_attribute("aria-checked", "false")
                assert not (
                    current_cover.evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(
                    page.locator(".library-cover-card").filter(
                        has=page.get_by_alt_text("EPUB file cover")
                    )
                ).to_have_attribute("aria-checked", "true")
                assert (
                    page.locator(".library-cover-card")
                    .filter(has=page.get_by_alt_text("EPUB file cover"))
                    .evaluate("node => node.classList.contains('is-selected')")
                )
                # A recorded automatic choice is already selected: choosing it
                # again is a no-op. Explicitly retain the incumbent first, then
                # choose a different cover, in one tap, as a fresh owner revision.
                automatic_cover.click()
                expect(page.get_by_role("dialog")).to_have_count(0)
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as kept:
                    current_cover.click()
                assert kept.value.status == 200, kept.value.text()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                expect(current_cover).to_have_attribute("aria-checked", "true")
                with (
                    page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as decision_response,
                ):
                    page.locator(".library-cover-card").filter(
                        has=page.get_by_alt_text("EPUB file cover")
                    ).click()
                response = decision_response.value
                assert 200 == response.status, f"decision response: {response.text()}"
                page.get_by_role("heading", name="Review complete").wait_for()
                page.goto(f"{origin}/review/{case_id}?field=cover&subview=history")
                page.get_by_role("heading", name="History").wait_for()
                selected_history = page.get_by_role(
                    "heading", name="Cover chosen"
                ).locator("..")
                selected_history.get_by_text("Visual review", exact=False).wait_for()
                selected_history.get_by_text(
                    "Owner-confirmed choice: a proposed cover.", exact=False
                ).wait_for()
                assert "candidate" in selected_history.inner_text().lower()
                for identifier in (
                    "cover_consideration_",
                    "cover_ref_",
                    "human_decision_",
                    "review_transition_",
                ):
                    assert (
                        identifier not in page.locator(".diagnostic-route").inner_text()
                    )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert responses
                assert all(status == 200 for _, status in responses), responses
                assert errors == []
            browser.close()


def test_ask_surfaces_and_records_the_sole_cover_choice_after_timeout() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    sole_cover_timeout=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                response = page.request.get(
                    f"{origin}/api/v1/library?lens=needs_attention&limit=25"
                )
                assert response.ok, response.text()
                library = response.json()
                assert [case_id] == [
                    item["review_case_id"] for item in library["items"]
                ]

                page.goto(f"{origin}/library?preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                comparison = page.get_by_role("region", name="Choose a cover")
                comparison.wait_for()
                expect(page.get_by_alt_text("EPUB file cover")).to_be_visible()
                comparison.get_by_text(
                    "1 unavailable candidate", exact=False
                ).wait_for()
                assert page.locator(".library-cover-card").count() == 1
                candidate = page.locator(".library-cover-card").filter(
                    has=page.get_by_alt_text("EPUB file cover")
                )
                expect(candidate).to_have_attribute("aria-checked", "false")
                with page.expect_response(
                    lambda value: value.url.endswith("/decisions")
                ) as decision_response:
                    candidate.focus()
                    page.keyboard.press("Enter")
                assert decision_response.value.status == 200
                page.get_by_role("heading", name="Review complete").wait_for()

                page.goto(f"{origin}/review/{case_id}?field=cover&subview=history")
                selected = page.get_by_role("heading", name="Cover chosen").locator(
                    ".."
                )
                selected.get_by_text(
                    "Owner-confirmed choice: a proposed cover.", exact=False
                ).wait_for()
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"
                )
                assert errors == []
            browser.close()


def test_stale_cover_choice_stays_visible_until_owner_opens_current_comparison() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        decision_statuses: list[int] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    decision_statuses.append(response.status)
                    if response.url.endswith("/decisions")
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    stale_cover_successor=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                comparison = page.get_by_role("region", name="Choose a cover")
                comparison.wait_for()
                comparison.locator(".library-cover-card").filter(
                    has=page.get_by_alt_text("EPUB file cover")
                ).click()

                stale = page.get_by_text(
                    "This cover comparison changed before your choice was recorded. "
                    "No cover was changed."
                )
                expect(stale).to_be_visible()
                assert case_id in page.url
                assert decision_statuses == [409]
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_have_count(0)

                page.get_by_role("button", name="Review current comparison").click()
                expect(stale).to_have_count(0)
                page.wait_for_function(
                    "oldCase => new URL(location.href).searchParams.get('preview') !== oldCase",
                    arg=case_id,
                )
                current_comparison = page.get_by_role("region", name="Choose a cover")
                current_comparison.wait_for()
                current_comparison.get_by_role(
                    "button", name="Evidence for EPUB file cover"
                ).click()
                disclosure = current_comparison.get_by_role(
                    "button", name="Evidence for EPUB file cover", exact=True
                )
                disclosure.focus()
                assert disclosure.evaluate("node => document.activeElement === node")
                disclosure.press("Enter")
                expect(disclosure).to_have_attribute("aria-expanded", "false")
                assert decision_statuses == [409]
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_have_count(0)
                current_comparison.get_by_role(
                    "radio", name="Choose EPUB file cover", exact=True
                ).click()
                page.get_by_role("heading", name="Review complete").wait_for()
                assert decision_statuses == [409, 200]
                assert errors and all("409" in error for error in errors), errors
            browser.close()


def test_current_same_case_cover_choice_clears_stale_feedback_after_recording() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        decision_statuses: list[int] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    decision_statuses.append(response.status)
                    if response.url.endswith("/decisions")
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    stale_cover_same_case=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                comparison = page.get_by_role("region", name="Choose a cover")
                comparison.wait_for()
                comparison.locator(".library-cover-card").filter(
                    has=page.get_by_alt_text("EPUB file cover")
                ).click()

                stale = page.get_by_text(
                    "This cover comparison changed before your choice was recorded. "
                    "No cover was changed."
                )
                expect(stale).to_be_visible()
                assert case_id in page.url
                assert decision_statuses == [409]

                current_choice = page.locator(".library-cover-card").filter(
                    has=page.get_by_alt_text("EPUB file cover")
                )
                current_choice.focus()
                page.keyboard.press("Enter")
                page.get_by_role("heading", name="Review complete").wait_for()
                expect(stale).to_have_count(0)
                expect(
                    page.get_by_role("button", name="Review current comparison")
                ).to_have_count(0)
                assert decision_statuses == [409, 200]
                assert errors and all("409" in error for error in errors), errors
            browser.close()


# Measure each rendered cover against its own frame's content box. A cover that
# overruns its frame is clipped by `overflow:hidden` and is invisible to
# `document.scrollWidth`, so the document-level check cannot see it at all --
# the same trap `overrunning_rail_controls` exists to avoid (#644).
_COVER_CONTAINMENT = """(selector) => {
  const frames = [...document.querySelectorAll(selector)]
  if (frames.length === 0) return [{ error: 'no cover frames', selector }]
  const inside = (box, content) =>
    box.left >= content.left - 0.5 && box.right <= content.right + 0.5 &&
    box.top >= content.top - 0.5 && box.bottom <= content.bottom + 0.5
  return frames.map((frame) => {
    const style = getComputedStyle(frame)
    const box = frame.getBoundingClientRect()
    const content = {
      left: box.left + parseFloat(style.paddingLeft) + parseFloat(style.borderLeftWidth),
      right: box.right - parseFloat(style.paddingRight) - parseFloat(style.borderRightWidth),
      top: box.top + parseFloat(style.paddingTop) + parseFloat(style.borderTopWidth),
      bottom: box.bottom - parseFloat(style.paddingBottom) - parseFloat(style.borderBottomWidth),
    }
    const shared = {
      background: style.backgroundColor,
      frame_ratio: (content.right - content.left) / (content.bottom - content.top),
      frame_top: Math.round(box.top),
      frame_width: Math.round(content.right - content.left),
    }
    const image = frame.querySelector('img')
    if (image === null) {
      const alternative = frame.matches('[role="img"]') ? frame : frame.querySelector('[role="img"]')
      return {
        ...shared,
        kind: 'alternative',
        label: alternative === null ? null : alternative.getAttribute('aria-label'),
        text: (frame.textContent || '').trim(),
        bounded: alternative !== null && inside(alternative.getBoundingClientRect(), content),
      }
    }
    const imageBox = image.getBoundingClientRect()
    const natural = [image.naturalWidth, image.naturalHeight]
    // `object-fit: contain` paints inside the element box; the painted area is
    // what the owner actually sees, so containment is measured on that, not on
    // the element box alone.
    const scale = Math.min(imageBox.width / natural[0], imageBox.height / natural[1])
    const painted = { width: natural[0] * scale, height: natural[1] * scale }
    const paintedBox = {
      left: imageBox.left + (imageBox.width - painted.width) / 2,
      right: imageBox.right - (imageBox.width - painted.width) / 2,
      top: imageBox.top + (imageBox.height - painted.height) / 2,
      bottom: imageBox.bottom - (imageBox.height - painted.height) / 2,
    }
    return {
      ...shared,
      kind: 'image',
      alt: image.alt,
      loaded: image.complete && natural[0] > 0 && natural[1] > 0,
      natural,
      object_fit: getComputedStyle(image).objectFit,
      element_in_frame: inside(imageBox, content),
      painted_in_frame: inside(paintedBox, content),
      ratio_error: Math.abs(painted.width / painted.height - natural[0] / natural[1]),
      painted_ratio: painted.width / painted.height,
    }
  })
}"""

# Very tall, wide, portrait and square, all within the 320x480 preview
# bound the cover packet enforces. Four ratios that no single frame can
# satisfy is the point: containment has to hold for all of them at once.
# The first two are the incumbent and the embedded candidate. #864's prepared
# Library comparison keeps those alongside every qualified replacement, so
# the widest ratio spread lands together on the surface with the least room.
_COVER_SHAPES = ((120, 480), (320, 140), (200, 300), (280, 280), (300, 200))
_LIBRARY_SHAPES = _COVER_SHAPES


@contextmanager
def _identity_library(
    reduced_motion: Literal["reduce", "no-preference"] = "no-preference",
) -> Iterator[tuple[Page, str, list[str]]]:
    """Sign in to the real server over the library identity fixture.

    It holds one case with two open fields, a same-title sibling with none, and
    one book whose preparation stopped before its report, catalogued as
    "Retained Terminal Title" by "Retained Writer". The list records the query
    of every `/api/v1/library` request.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, reduced_motion=reduced_motion
            )
            page = context.new_page()
            queries: list[str] = []
            page.on(
                "request",
                lambda request: (
                    queries.append(urlparse(request.url).query)
                    if urlparse(request.url).path == "/api/v1/library"
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    library_identity_fixture=True,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                yield page, origin, queries


def test_library_search_scopes_the_list_and_leaves_global_counts() -> None:
    """A search narrows Works and the rail; the badge, the handoff and every
    summary denominator keep the whole library, and no summary read carries q
    (#1284). Under a lens the Works count is that lens's, in its own words
    (#1425)."""
    with _identity_library() as (page, origin, queries):
        page.goto(f"{origin}/library?lens=needs_attention&attention=open")
        status = page.locator(".library-works-heading [role='status']")
        badge = page.locator(".attention-toggle strong")
        rail = page.get_by_role("complementary", name="Attention queue")
        handoff = page.locator(".cm-library-handoff h2")
        expect(status).to_have_text("3 works · 1 not matched · 1 could not finish")
        expect(badge).to_have_text("1 work")
        expect(rail.locator("header p")).to_have_text("1 work needs a decision")
        # One work with two open fields is one group with two rows.
        expect(rail.locator(".attention-work-group")).to_have_count(1)
        expect(rail.locator("button.attention-queue-item")).to_have_count(2)
        expect(rail.locator(".attention-work-count")).to_have_text("2 fields")
        expect(rail.locator(".attention-work-identity strong")).to_have_text(
            "Synthetic Journey"
        )
        expect(handoff).to_have_text("3 works are in Library. Writes are off.")
        search = page.get_by_role("searchbox")
        search.fill("Retained Terminal")
        expect(page).to_have_url(re.compile(r"q=Retained"))
        expect(status).to_have_text("Found 0 of 1 work needing attention")
        expect(rail.locator("header p")).to_have_text(
            "Matching: 0 of 1 work needing a decision"
        )
        expect(rail.locator("button.attention-queue-item")).to_have_count(0)
        expect(badge).to_have_text("1 work")
        expect(handoff).to_have_text("3 works are in Library. Writes are off.")
        search.fill("zzz-no-match")
        expect(status).to_have_text("Found 0 of 1 work needing attention")
        expect(badge).to_have_text("1 work")
        search.fill("")
        expect(page).not_to_have_url(re.compile(r"q="))
        expect(status).to_have_text("3 works · 1 not matched · 1 could not finish")
        expect(rail.locator("header p")).to_have_text("1 work needs a decision")
        expect(badge).to_have_text("1 work")
        expect(handoff).to_have_text("3 works are in Library. Writes are off.")
        summary = [query for query in queries if "lens=all" in query]
        assert summary, queries
        assert all("q=" not in query for query in summary), queries
        search.fill("Retained Terminal")
        expect(page).to_have_url(re.compile(r"q=Retained"))
        lenses = page.get_by_role("navigation", name="Library lenses")
        # The status shows the lens's count; its words reach screen readers only.
        shown = (
            "node => [...node.childNodes].filter(child =>"
            " !child.classList?.contains('sr-only')).map(child =>"
            " child.textContent).join('')"
        )
        for lens, visible, announced in (
            ("Not matched", "Found 0 of 1 work", "Found 0 of 1 work not matched"),
            # The retained work's latest preparation failed, so it is
            # counted where it could not finish, not as clear (#1413).
            (
                "No attention needed",
                "Found 0 of 0 works",
                "Found 0 of 0 works needing no attention",
            ),
            (
                "Could not finish",
                "Found 1 of 1 work",
                "Found 1 of 1 work that could not finish",
            ),
            ("All", "Found 1 of 3 works", "Found 1 of 3 works"),
            (
                "Needs attention",
                "Found 0 of 1 work",
                "Found 0 of 1 work needing attention",
            ),
        ):
            lenses.get_by_role("link", name=lens, exact=True).click()
            expect(page).to_have_url(re.compile(r"q=Retained"))
            expect(status).to_have_text(announced)
            assert status.evaluate(shown) == visible
            # It stays on the Works heading's line.
            heading = page.locator("#library-works-title").bounding_box()
            line = status.bounding_box()
            assert heading and line and line["y"] < heading["y"] + heading["height"]
        expect(badge).to_have_text("1 work")


def test_activity_checks_open_onto_one_row_per_field() -> None:
    """A preparation's checks open onto one row per checked field, and each
    field's recorded decision is reached from inside that field's Details
    (#1425)."""
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                activity_checks_fixture=True,
            ) as (origin, _case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/activity")
            checks = page.locator("article.is-checks")
            expect(checks).to_have_count(1)
            expect(checks).to_contain_text("Checked 2 fields, no changes")
            checks.get_by_role("button", name="Details").click()
            fields = page.locator(
                ".cm-activity-event-group > .cm-activity-checks > li > article"
            )
            expect(fields).to_have_count(2)
            expect(fields.locator(":scope > b")).to_have_text(
                ["No change required", "No change required"]
            )
            expect(
                page.locator(".cm-activity-checks").get_by_text("Recorded")
            ).to_have_count(0)
            for index in range(2):
                field = fields.nth(index)
                details = field.locator(":scope > button")
                details.focus()
                page.keyboard.press("Enter")
                expect(details).to_have_attribute("aria-expanded", "true")
                paired = field.locator(
                    ":scope > .cm-activity-evidence > ul > li > article"
                )
                expect(paired).to_have_count(1)
                expect(paired.locator(":scope > b")).to_have_text("Automatic decision")
                decision = paired.locator(":scope > button")
                page.keyboard.press("Tab")
                expect(decision).to_be_focused()
                page.keyboard.press("Enter")
                expect(decision).to_have_attribute("aria-expanded", "true")
                expect(paired.locator(".cm-activity-evidence")).to_contain_text(
                    "Policy setting"
                )
                page.keyboard.press("Enter")
                expect(paired.locator(".cm-activity-evidence")).to_have_count(0)
                expect(decision).to_be_focused()
                expect(details).to_have_attribute("aria-expanded", "true")
                details.click()
                expect(field.locator(".cm-activity-evidence")).to_have_count(0)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_terminal_preparation_row_is_listed_and_found_by_its_library_title() -> None:
    """A book that stopped before its report is listed under its catalogue
    title and authors, stays noninteractive, and a title search finds it
    (#1284)."""
    with _identity_library(reduced_motion="reduce") as (page, origin, _queries):
        page.goto(f"{origin}/library?lens=all")
        row = page.locator(".library-work-row.is-terminal")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Retained Terminal Title")
        expect(row).to_contain_text("Retained Writer")
        expect(row).not_to_contain_text("Preparation unavailable")
        assert row.evaluate("node => node.tagName") == "ARTICLE"
        assert row.locator("button, a").count() == 0
        expect(row).to_contain_text("The local media file could not be read.")
        page.get_by_role("searchbox").fill("Retained Terminal")
        expect(page).to_have_url(re.compile(r"q=Retained"))
        expect(page.locator(".library-works-heading [role='status']")).to_have_text(
            "Found 1 of 3 works"
        )
        expect(page.locator(".library-work-row")).to_have_count(1)
        expect(page.locator(".library-work-row.is-terminal")).to_contain_text(
            "Retained Terminal Title"
        )


def test_workspace_paints_the_saved_or_system_theme_before_the_application_loads() -> (
    None
):
    """The shell's blocking theme script sets the saved, else the system, theme
    before the application bundle runs, under the production CSP; the bundled
    Inter face loads from the same origin (#1289)."""
    held: list[Route] = []
    first_paint = (
        "() => { const body = document.body; if (!body) return null;"
        " const background = getComputedStyle(body).backgroundColor;"
        " return background === 'rgba(0, 0, 0, 0)' ? null :"
        " [document.documentElement.dataset.theme ?? null, background] }"
    )

    def painted(page: Page, url: str) -> list[str | None]:
        page.goto(url, wait_until="commit")
        return cast(list[str | None], page.wait_for_function(first_paint).json_value())

    with _identity_library() as (page, origin, _queries):
        context = page.context
        errors: list[str] = []
        fonts: list[tuple[int, str | None, str | None]] = []
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on(
            "response",
            lambda response: (
                fonts.append(
                    (
                        response.status,
                        response.headers.get("content-type"),
                        response.headers.get("cache-control"),
                    )
                )
                if response.url.endswith(".woff2")
                else None
            ),
        )
        page.route(
            "**/assets/review-app/assets/index-*.js", lambda route: held.append(route)
        )
        page.emulate_media(color_scheme="dark")
        url = f"{origin}/library?lens=all"
        assert painted(page, url) == ["dark", "rgb(15, 21, 18)"]
        assert len(held) == 1
        held.pop().abort()
        context.add_cookies([{"name": "ml_theme", "value": "light", "url": origin}])
        assert painted(page, url) == ["light", "rgb(244, 242, 236)"]
        held.pop().abort()
        context.clear_cookies(name="ml_theme")

        # Without the script the shell paints light under a dark system: the
        # gap the script closes, and the proof this test bites.
        control = context.new_page()
        control.emulate_media(color_scheme="dark")
        control.route(
            "**/assets/review-app/assets/index-*.js", lambda route: held.append(route)
        )
        control.route(
            "**/assets/review-app/assets/theme-*.js", lambda route: route.abort()
        )
        assert painted(control, url) == [None, "rgb(244, 242, 236)"]
        held.pop().abort()
        control.close()

        page.goto(url, wait_until="commit")
        assert page.wait_for_function(first_paint).json_value()[0] == "dark"
        held.pop().continue_()
        expect(page.get_by_role("heading", name="Works", exact=True)).to_be_visible()
        html = page.locator("html")
        page.emulate_media(color_scheme="light")
        expect(html).to_have_attribute("data-theme", "light")
        page.emulate_media(color_scheme="dark")
        expect(html).to_have_attribute("data-theme", "dark")
        page.get_by_role("button", name="Use light mode").click()
        expect(html).to_have_attribute("data-theme", "light")
        page.emulate_media(color_scheme="light")
        page.emulate_media(color_scheme="dark")
        expect(html).to_have_attribute("data-theme", "light")

        assert page.evaluate(
            "async () => { await document.fonts.ready;"
            " return document.fonts.check('400 16px Inter') && [...document.fonts]"
            ".some(face => face.family.replaceAll('\"', '') === 'Inter'"
            " && face.status === 'loaded') }"
        )
        assert fonts and all(
            status == 200
            and content_type == "font/woff2"
            and cache_control == "private, max-age=31536000, immutable"
            for status, content_type, cache_control in fonts
        ), fonts
        assert not [error for error in errors if "Content Security Policy" in error], (
            errors
        )


@pytest.mark.parametrize("cohort", [24, 30])
def test_load_more_attention_keeps_focus_on_the_first_row_it_added(
    cohort: int,
) -> None:
    """Load more never drops focus: the first row the next page adds takes it,
    even when that row joins a work already in the rail (#1284).

    24 works put one work's last field alone on the second 25-row page; 30 put
    seven rows there, so the next Tab continues through the rail.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 900}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    provider_recovery=True,
                    cohort=cohort,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&attention=open")
                rail = page.get_by_label("Attention queue", exact=True)
                rows = rail.locator("button.attention-queue-item")
                expect(rows.first).to_be_visible()
                keys = "rows => rows.map(row => row.dataset.attentionRow)"
                before = rows.evaluate_all(keys)
                titles = rail.locator(".attention-work-identity strong")
                before_titles = titles.all_text_contents()
                more = rail.get_by_role(
                    "button", name="Load more attention", exact=True
                )
                more.focus()
                page.keyboard.press("Enter")
                expect(rows).not_to_have_count(len(before))
                page.evaluate(_TWO_FRAMES)
                added = [key for key in rows.evaluate_all(keys) if key not in before]
                assert added, "the next page added no row"
                focused = page.evaluate(
                    "() => document.activeElement?.dataset?.attentionRow ?? null"
                )
                assert focused == added[0], (focused, added)
                # A row's name ends with its work's title (#1361).
                work = page.evaluate(
                    "() => (row => "
                    "row.getAttribute('aria-label')"
                    ".slice(row.querySelector('b').textContent.length + 2))"
                    "(document.activeElement)"
                )
                assert titles.all_text_contents().count(work) == 1
                if cohort == 24:
                    # The page continued a work already shown, inside its group.
                    assert added == [focused]
                    assert work in before_titles, (work, before_titles)
                else:
                    assert work not in before_titles, (work, before_titles)
                    page.keyboard.press("Tab")
                    assert page.evaluate(_FOCUSED) == "BUTTON.attention-queue-item"
                    assert (
                        page.evaluate(
                            "() => document.activeElement.dataset.attentionRow"
                        )
                        == added[1]
                    )
                context.close()
            browser.close()


_CHOSEN_HEADER_IN_VIEW = """() => {
  const active = document.activeElement
  if (!active?.matches('.selected-work-header')) return null
  const header = active.getBoundingClientRect()
  const masthead = document.querySelector('.masthead').getBoundingClientRect()
  const clear = Math.max(0, masthead.bottom)
  const room = innerHeight + 0.5
  // Where Back to works leads the work, it is in view and the header follows
  // it: whole when it fits, otherwise at least its first line (#1530).
  const back = document.querySelector('.compact-workspace-back')
  const lead = back?.getClientRects().length ? back.getBoundingClientRect() : null
  return {
    top: header.top,
    bottom: header.bottom,
    back: lead && [lead.top, lead.bottom],
    clear,
    height: innerHeight,
    inView: lead
      ? lead.top >= clear - 0.5 && lead.bottom <= room && header.top >= lead.bottom - 0.5 &&
        (header.bottom <= room || header.top <= innerHeight - 44)
      : header.top >= clear - 0.5 && header.bottom <= room,
  }
}"""


def test_choosing_an_attention_item_keeps_the_focused_work_header_in_view() -> None:
    """Choosing the rail's last queue item leaves the focused
    selected-work header fully in view and clear of the masthead at 100, 150
    and 200% text, from the widest window to the narrowest phone (#1487).

    With enlarged text the header used to take focus below the fold: the page
    returned to its top while the header sat further down.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    provider_recovery=True,
                    cohort=24,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                for width, height, size, by in (
                    (width, height, size, by)
                    for width, height in (
                        (1440, 900),
                        (1200, 900),
                        (390, 844),
                        (320, 640),
                    )
                    for size in ("100%", "150%", "200%")
                    # Back to works leads the work at phone widths, where a
                    # pointer choice is checked too (#1530).
                    for by in ("keyboard", "pointer")
                    if by == "keyboard" or width <= 390
                ):
                    where = f"{size} text at {width}x{height} by {by}"
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(f"{origin}/library?lens=all&attention=open")
                    set_root_text(page, size)
                    rows = page.get_by_label("Attention queue", exact=True).locator(
                        "button.attention-queue-item"
                    )
                    expect(rows.first).to_be_visible()
                    chosen = rows.last
                    work = chosen.get_attribute("data-attention-row")
                    if by == "keyboard":
                        chosen.focus()
                        page.keyboard.press("Enter")
                    else:
                        chosen.click()
                    assert work is not None, where
                    expect(page).to_have_url(
                        re.compile(f"preview={re.escape(work.split(':')[0])}")
                    )
                    try:
                        page.wait_for_function(
                            f"() => ({_CHOSEN_HEADER_IN_VIEW})()?.inView === true",
                            timeout=5_000,
                        )
                    except PlaywrightTimeoutError:
                        pytest.fail(f"{where}: {page.evaluate(_CHOSEN_HEADER_IN_VIEW)}")
                    # It stays there once the page settles.
                    page.evaluate(_TWO_FRAMES)
                    page.evaluate(_TWO_FRAMES)
                    settled = page.evaluate(_CHOSEN_HEADER_IN_VIEW)
                    assert settled and settled["inView"], (where, settled)
                context.close()
            browser.close()


@pytest.mark.parametrize("identity", [False, True])
@pytest.mark.parametrize("dark", [False, True])
def test_library_description_controls_preserve_words_at_enlarged_text(
    identity: bool, dark: bool
) -> None:
    """Text enlargement keeps each control word intact without page overflow."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, reduced_motion="reduce"
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    description_fixture=None if identity else "ready",
                    library_identity_fixture=identity,
                    long_descriptions=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                if dark:
                    page.get_by_role("button", name="Use dark mode").click()
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field={'isbn' if identity else 'description'}"
                )
                page.locator(
                    ".recorded-field-observations, [role=radiogroup]"
                ).first.wait_for()
                if not identity:
                    page.get_by_role(
                        "button",
                        name="Evidence for Current value description",
                        exact=True,
                    ).click()
                    expect(page.locator("#choice-evidence-current")).to_contain_text(
                        "Local library recordLocal presence"
                    )
                for width, size in (
                    (1440, "100%"),
                    (390, "100%"),
                    (320, "100%"),
                    (1440, "200%"),
                    (320, "200%"),
                ):
                    page.set_viewport_size({"width": width, "height": 900})
                    set_root_text(page, size)
                    page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => "
                        "requestAnimationFrame(resolve)))"
                    )
                    words = page.evaluate("""() => {
                      const targets = [
                        ...document.querySelectorAll('.lenses a'),
                        ...document.querySelectorAll('.selected-work-header h2, .recorded-field-observations h3, .context-value dd'),
                        ...document.querySelectorAll('.description-value button'),
                        ...document.querySelectorAll('#choice-evidence-current li strong, #choice-evidence-current li > span, #choice-evidence-current li p'),
                        ...document.querySelectorAll('.description-choice-content + .choice-select strong'),
                      ].filter(node => node.getClientRects().length);
                      return targets.flatMap(node => {
                        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
                        const result = [];
                        while (walker.nextNode()) {
                          const text = walker.currentNode;
                          for (const match of text.textContent.matchAll(/\\S+/g)) {
                            const range = document.createRange();
                            range.setStart(text, match.index);
                            range.setEnd(text, match.index + match[0].length);
                            result.push({word: match[0], lines: new Set([...range.getClientRects()].map(rect => rect.top)).size});
                          }
                        }
                        return result;
                      });
                    }""")
                    assert words and all(word["lines"] == 1 for word in words), (
                        width,
                        size,
                        words,
                    )
                    assert (
                        page.evaluate("document.documentElement.scrollWidth") <= width
                    )
                if not identity:
                    disclosure = page.get_by_role(
                        "button", name="Read full description"
                    ).first
                    disclosure.click()
                    expect(
                        page.get_by_role("button", name="Read full description")
                    ).to_have_count(2)
                    expect(
                        page.get_by_role("button", name="Show less description")
                    ).to_have_count(1)
                    expect(
                        page.get_by_role("radio", name="Choose current description")
                    ).to_have_attribute("aria-checked", "true")
                    expect(
                        page.get_by_role("heading", name="Review complete")
                    ).to_have_count(0)
            browser.close()


def test_configured_cover_reviewer_keeps_failed_advice_retryable() -> None:
    """Real reviewer configuration and dispatch reach the Library retry control."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    cover=True,
                    configured_cover_reviewer=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                retry = page.get_by_role("button", name="Retry advisory review")
                expect(retry).to_be_enabled()
                with page.expect_response(
                    lambda response: response.url.endswith(
                        "/cover-considerations/retry"
                    )
                ) as response:
                    retry.click()
                assert response.value.status == 200
                expect(retry).to_be_enabled()
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_have_count(0)
                assert (
                    page.locator(".library-cover-card[aria-checked='true']").count()
                    == 0
                )
                page.goto(f"{origin}/settings?group=reviewer")
                page.get_by_role("button", name="Continue without a reviewer").click()
                page.get_by_text("No reviewer", exact=True).wait_for()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                expect(retry).to_have_count(0)
                expect(page.get_by_text("1 remaining · field 1 of 1")).to_be_visible()
                library = page.request.get(f"{origin}/api/v1/library?lens=all").json()
                attention = page.request.get(f"{origin}/api/v1/attention").json()
                assert library["counts"]["needs_attention"] == 1, library
                assert attention["total"] == 1, attention
                page.get_by_role(
                    "radio", name="Choose EPUB file cover", exact=True
                ).click()
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_be_visible()
            browser.close()


def test_reviewer_access_failure_sits_under_its_focused_button() -> None:
    """#1584: a pending Continue without a reviewer keeps real focus, ignores a
    second press, and its unconfirmed failure renders directly under it inside
    the row's inset at 1440 and 390 px and at 200% text."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 900}
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    cover=True,
                    configured_cover_reviewer=True,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                held: list[Route] = []
                page.route(
                    "**/api/v1/reviewer-access/selection",
                    lambda route: held.append(route),
                )
                page.goto(f"{origin}/settings?group=reviewer")
                button = page.get_by_role("button", name="Continue without a reviewer")
                button.focus()
                page.keyboard.press("Enter")
                expect(button).to_have_attribute("aria-disabled", "true")
                # Chromium blurs a natively disabled button; this one keeps focus.
                expect(button).to_be_focused()
                page.keyboard.press("Enter")
                button.click(force=True)
                assert len(held) == 1
                held[0].fulfill(
                    status=503,
                    json={
                        "schema_version": "review-error-v1",
                        "error": {"code": "reviewer_access_unavailable"},
                    },
                )
                alert = page.get_by_text(
                    "The change could not be fully confirmed. It may have been saved; "
                    "reviewer access was reloaded. Check it before trying again."
                )
                expect(alert).to_be_visible()
                expect(alert).to_have_attribute("role", "alert")
                expect(button).to_be_focused()
                assert len(held) == 1
                for width, size in (
                    (1440, ""),
                    (390, ""),
                    (1440, "200%"),
                    (390, "200%"),
                ):
                    page.set_viewport_size({"width": width, "height": 900})
                    set_root_text(page, size)
                    placement = button.evaluate("""button => {
                      const alert = button.nextElementSibling;
                      const row = button.closest('.settings-field-row');
                      const control = button.closest('.settings-field-control');
                      const own = button.getBoundingClientRect();
                      const shown = alert.getBoundingClientRect();
                      const box = row.getBoundingClientRect();
                      const style = getComputedStyle(row);
                      return {
                        sibling: alert.getAttribute('role') === 'alert',
                        inside: control.contains(alert),
                        gap: shown.top - own.bottom,
                        rem: parseFloat(getComputedStyle(document.documentElement).fontSize),
                        left: shown.left - (box.left + parseFloat(style.paddingLeft)),
                        right: box.right - parseFloat(style.paddingRight) - shown.right,
                        scroll: document.documentElement.scrollWidth,
                      };
                    }""")
                    assert placement["sibling"] and placement["inside"], placement
                    assert 0 <= placement["gap"] <= placement["rem"], (
                        width,
                        size,
                        placement,
                    )
                    assert placement["left"] >= -0.5, (width, size, placement)
                    assert placement["right"] >= -0.5, (width, size, placement)
                    assert placement["scroll"] <= width, (width, size, placement)
                    expect(button).to_be_focused()
                set_root_text(page, "")
            browser.close()


def test_protected_cover_comparison_contains_every_image_without_cropping() -> None:
    """#644: every cover is shown whole, in a quiet 5:8 frame, at every width.

    The frames are measured against their own content boxes because a cropped
    cover is clipped inside `overflow:hidden` and never reaches the document
    scroll width. Real mixed-ratio PNGs are served so the intrinsic ratio the
    browser reports is the ratio being contained.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    cover_shapes=_COVER_SHAPES,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                page.get_by_role("region", name="Choose a cover").wait_for()

                def measured(selector: str) -> list[dict[str, Any]]:
                    page.wait_for_function(
                        "(selector) => [...document.querySelectorAll(selector)]"
                        ".every((image) => image.complete)",
                        arg=f"{selector} img",
                    )
                    return cast(
                        "list[dict[str, Any]]",
                        page.evaluate(_COVER_CONTAINMENT, selector),
                    )

                def assert_contained(
                    selector: str,
                    viewport: int,
                    *,
                    label: str,
                    shapes: tuple[tuple[int, int], ...],
                ) -> list[dict[str, Any]]:
                    frames = measured(selector)
                    images = [frame for frame in frames if frame["kind"] == "image"]
                    assert len(images) == len(shapes), (label, frames)
                    for frame in images:
                        context_line = (label, frame)
                        assert frame["loaded"], context_line
                        assert frame["object_fit"] == "contain", context_line
                        assert frame["element_in_frame"], context_line
                        assert frame["painted_in_frame"], context_line
                        assert frame["ratio_error"] < 0.02, context_line
                        # A quiet 5:8 comparison frame, not the image's ratio.
                        assert abs(frame["frame_ratio"] - 5 / 8) < 0.02, context_line
                        # Transparent letterboxing paints the choice card's own
                        # surface, so unused frame area cannot read as content.
                        assert frame["background"] == "rgba(0, 0, 0, 0)", context_line
                    # Mixed proportions coexist without being normalised.
                    assert {tuple(frame["natural"]) for frame in images} == {
                        shape for shape in shapes
                    }, (label, images)
                    assert (
                        page.evaluate("() => document.documentElement.scrollWidth")
                        <= viewport
                    ), label
                    return frames

                assert_contained(
                    ".library-cover-image",
                    1440,
                    label="library:desktop",
                    shapes=_LIBRARY_SHAPES,
                )
                # Every card states its size and its sources, beside any
                # rationale (#1290).
                dimensions = page.locator(
                    ".library-cover-card .cover-dimensions"
                ).all_inner_texts()
                assert len(dimensions) == len(_LIBRARY_SHAPES), dimensions
                assert all(
                    re.fullmatch(r"\d+ × \d+ px", text) for text in dimensions
                ), dimensions
                assert len(set(dimensions)) == len(dimensions), dimensions
                assert all(
                    page.locator(".library-cover-card .cover-sources").all_inner_texts()
                )
                # Every visible word in the comparison renders at the 12px floor
                # or above, including text sized only by the browser's own
                # `small` rule, which no stylesheet scan can see; and each
                # Evidence toggle is a 44px target (#1290).
                under_floor = page.get_by_role(
                    "region", name="Choose a cover"
                ).evaluate(
                    """region => [...region.querySelectorAll('*')]
                      .filter((node) => node.checkVisibility() && !node.closest('.sr-only')
                        && [...node.childNodes].some((child) =>
                          child.nodeType === Node.TEXT_NODE && child.textContent.trim()))
                      .map((node) => [node.tagName.toLowerCase(), node.textContent.trim().slice(0, 40),
                        parseFloat(getComputedStyle(node).fontSize)])
                      .filter(([, , size]) => size < 12)"""
                )
                assert under_floor == [], under_floor
                toggle_heights = page.locator(
                    ".cover-choice .choice-evidence-toggle"
                ).evaluate_all(
                    "nodes => nodes.map((node) => node.getBoundingClientRect().height)"
                )
                assert len(toggle_heights) == len(_LIBRARY_SHAPES), toggle_heights
                assert min(toggle_heights) >= 44, toggle_heights

                # A scrolled pointer must hit disclosure, never a sticky choice
                # covering it. Keyboard-only tests cannot detect this overlap.
                disclosure = page.get_by_role(
                    "button", name="Evidence for Current cover", exact=True
                )
                disclosure.evaluate(
                    "node => window.scrollBy(0, node.getBoundingClientRect().top - 450)"
                )
                bounds = disclosure.bounding_box()
                assert bounds is not None
                page.mouse.click(
                    bounds["x"] + bounds["width"] / 2,
                    bounds["y"] + bounds["height"] / 2,
                )
                expect(disclosure).to_have_attribute("aria-expanded", "true")
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_have_count(0)
                assert (
                    page.locator(".library-cover-card[aria-checked='true']").count()
                    == 0
                )
                cards = page.locator(".library-cover-gallery .cover-choice")
                assert cards.count() == 4
                boxes = [cards.nth(index).bounding_box() for index in range(4)]
                assert all(box is not None for box in boxes)
                first, second, third, last = boxes
                assert first is not None and second is not None
                assert third is not None and last is not None
                assert last["y"] >= max(
                    box["y"] + box["height"] for box in (first, second, third)
                )
                current_box = page.locator(
                    ".library-cover-pinned > aside .cover-choice"
                ).bounding_box()
                assert current_box is not None
                assert current_box["height"] > first["height"]

                # 200% zoom is a halved CSS-pixel viewport at twice the device
                # scale, which is exactly the layout the owner gets when they
                # zoom a 1440x900 window to 200%.
                page.set_viewport_size({"width": 720, "height": 450})
                assert_contained(
                    ".library-cover-image",
                    720,
                    label="library:zoom200",
                    shapes=_LIBRARY_SHAPES,
                )

                page.set_viewport_size({"width": 390, "height": 844})
                compact = assert_contained(
                    ".library-cover-image",
                    390,
                    label="library:compact",
                    shapes=_LIBRARY_SHAPES,
                )
                # At 1024px and below the current cover joins the same scrollable row
                # as every replacement rather than sitting above them.
                assert len({frame["frame_top"] for frame in compact}) == 1, compact
                track = page.locator(
                    ".library-cover-pinned, .library-cover-layout"
                ).first
                assert track.evaluate(
                    "node => node.scrollWidth > node.clientWidth + 1"
                ), "the compact comparison row provides no scroll affordance"
                assert (
                    track.evaluate("node => getComputedStyle(node).overscrollBehaviorX")
                    == "contain"
                )
                expect(page.locator(".cover-track-hint")).to_be_visible()

                for width, size in ((390, "100%"), (390, "200%"), (320, "200%")):
                    page.set_viewport_size({"width": width, "height": 844})
                    set_root_text(page, size)
                    track.evaluate("node => { node.scrollLeft = 0 }")
                    # Unconfirmed choices remain visible but correctly disabled.
                    # Reach the last actual disclosure, not a disabled choice button.
                    assert track.locator(".cover-choice.is-unconfirmed").count() > 0
                    last_choice = track.locator(
                        ".cover-choice:has(.choice-evidence-toggle)"
                    ).last
                    focus_control = last_choice.locator(".choice-evidence-toggle")
                    assert focus_control.is_enabled()
                    focus_control.focus()
                    assert focus_control.evaluate(
                        "node => document.activeElement === node"
                    )
                    forward_scroll = cast(
                        "float", track.evaluate("node => node.scrollLeft")
                    )
                    assert forward_scroll > 0
                    assert last_choice.evaluate(
                        "node => { const track = node.closest('.library-cover-pinned, "
                        ".library-cover-layout'); const choice = node.getBoundingClientRect(); "
                        "const bounds = track.getBoundingClientRect(); return choice.left >= "
                        "bounds.left - 1 && choice.right <= bounds.right + 1 }"
                    )
                    first_choice = track.locator(".cover-choice").first
                    first_choice.get_by_role("button").first.focus()
                    reverse_scroll = cast(
                        "float", track.evaluate("node => node.scrollLeft")
                    )
                    assert reverse_scroll < forward_scroll
                    assert first_choice.evaluate(
                        "node => { const track = node.closest('.library-cover-pinned, "
                        ".library-cover-layout'); const choice = node.getBoundingClientRect(); "
                        "const bounds = track.getBoundingClientRect(); return choice.left >= "
                        "bounds.left - 1 && choice.right <= bounds.right + 1 }"
                    )

                for width in (1440, 320):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.wait_for_function(
                        "width => window.innerWidth === width && "
                        "window.matchMedia('(max-width: 760px)').matches === (width <= 760)",
                        arg=width,
                    )
                    # Viewport changes reach responsive layout on a rendering frame.
                    # Measure settled text, rather than the previous desktop tracks.
                    page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => "
                        "requestAnimationFrame(resolve)))"
                    )
                    words = page.evaluate("""() => {
                      const targets = document.querySelectorAll(
                        '.library-cover-card > span:last-child, .cover-choice .choice-evidence, .cover-choice .choice-evidence-toggle, .cover-observation');
                      return [...targets].flatMap(node => {
                        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
                        const result = [];
                        while (walker.nextNode()) {
                          const text = walker.currentNode;
                          for (const match of text.textContent.matchAll(/\\S+/g)) {
                            const range = document.createRange();
                            range.setStart(text, match.index);
                            range.setEnd(text, match.index + match[0].length);
                            const rects = [...range.getClientRects()].map(rect => rect.toJSON());
                            const parent = text.parentElement;
                            const style = getComputedStyle(parent);
                            result.push({word: match[0], lines: new Set(rects.map(rect => rect.top)).size,
                              rects, tag: parent.tagName, cls: parent.className,
                              parent: parent.getBoundingClientRect().toJSON(),
                              card: parent.closest('.cover-choice')?.getBoundingClientRect().toJSON(),
                              font: style.font, lineHeight: style.lineHeight, padding: style.padding,
                              columns: style.gridTemplateColumns, width: style.width});
                          }
                        }
                        return result;
                      });
                    }""")
                    assert words and all(word["lines"] == 1 for word in words), (
                        width,
                        [word for word in words if word["lines"] != 1],
                        page.evaluate(
                            """() => [...document.querySelectorAll('.library-cover-pinned, .cover-choice, .library-cover-card strong')].map(el => ({cls:el.className, width:el.getBoundingClientRect().width, font:getComputedStyle(el).fontSize, columns:getComputedStyle(el).gridAutoColumns}))"""
                        ),
                    )
                    assert (
                        page.evaluate("document.documentElement.scrollWidth") <= width
                    )
                set_root_text(page, "")
                page.set_viewport_size({"width": 390, "height": 844})

                assert errors == []
                # A cover whose bytes never arrive keeps a bounded, labelled
                # alternative inside the same frame. The aborted requests are
                # the only console errors this test tolerates, and they are
                # named rather than admitted by an empty allowance.
                page.route(
                    "**/cover-considerations/**/images/**",
                    lambda route: route.abort(),
                )
                requested: set[str] = set()
                page.on(
                    "request",
                    lambda request: (
                        requested.add(request.url.split("?")[0])
                        if "/images/" in request.url
                        else None
                    ),
                )
                page.reload()
                page.get_by_text("Image unavailable").first.wait_for()
                page.wait_for_timeout(300)
                # Covers load lazily (#1290): a card below the fold has not
                # asked for its image until it nears the viewport.
                frames = page.locator(".library-cover-image")
                assert 0 < len(requested) < frames.count(), requested
                for cover_frame in frames.all():
                    cover_frame.scroll_into_view_if_needed()
                page.wait_for_function(
                    "() => document.querySelectorAll('.library-cover-image img')"
                    ".length === 0"
                )
                unavailable = cast(
                    "list[dict[str, Any]]",
                    page.evaluate(_COVER_CONTAINMENT, ".library-cover-image"),
                )
                alternatives = [
                    frame for frame in unavailable if frame["kind"] == "alternative"
                ]
                assert len(alternatives) == len(unavailable), unavailable
                for frame in alternatives:
                    assert frame["bounded"], frame
                    assert frame["text"] == "Image unavailable", frame
                    assert frame["label"] == "Cover unavailable", frame
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                assert errors and all(
                    "Failed to load resource" in error for error in errors
                ), errors
            browser.close()


def test_duplicate_cover_images_are_one_choice_naming_both_sources() -> None:
    """#1290: one image offered by two sources is one card that names both."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    cover_shapes=_COVER_SHAPES,
                    cover_duplicate=True,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                comparison = page.get_by_role("region", name="Choose a cover")
                comparison.wait_for()
                group = comparison.get_by_role(
                    "radiogroup", name="Which cover should this work use?"
                )
                # Five sources, four distinct images: the current cover and
                # three alternatives.
                expect(group.get_by_role("radio")).to_have_count(4)
                sources = group.locator(".cover-sources").all_inner_texts()
                assert sources.count("Hardcover · Open Library") == 1, sources
                assert all(
                    text and "Open Library" not in text
                    for text in sources
                    if text != "Hardcover · Open Library"
                ), sources
                dimensions = group.locator(".cover-dimensions").all_inner_texts()
                assert len(dimensions) == 4, dimensions
                assert all(
                    re.fullmatch(r"\d+ × \d+ px", text) for text in dimensions
                ), dimensions
                expect(
                    comparison.get_by_text(
                        "1 duplicate image merged. Each card names every source "
                        "that offered it."
                    )
                ).to_be_visible()
                expect(comparison.locator(".library-cover-excluded")).to_have_count(0)
            browser.close()


@pytest.mark.parametrize("mode", ["click", "tap"])
@pytest.mark.parametrize("width", [1440, 1024, 375, 320])
def test_cover_choice_records_in_one_tap(width: int, mode: str) -> None:
    """#1290: one press on a cover records it, even half off a compact track.

    Pointer focus arrives on mousedown; before #1290 it scrolled the track
    under the press and the first tap at 375px and 320px was lost.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": width, "height": 812},
                reduced_motion="reduce",
                has_touch=mode == "tap",
            )
            page = context.new_page()
            decisions: list[str] = []
            page.on(
                "request",
                lambda request: (
                    decisions.append(request.url)
                    if request.method == "POST" and "/decisions" in request.url
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    cover_shapes=_COVER_SHAPES,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                group = page.get_by_role(
                    "radiogroup", name="Which cover should this work use?"
                )
                group.wait_for()
                track = page.locator(".library-cover-pinned, .library-cover-layout")
                # The alternative to press: the first enabled one that the
                # track's right edge cuts at compact widths, else the first.
                target = cast(
                    "dict[str, Any]",
                    track.evaluate(
                        """track => {
                          const edge = track.getBoundingClientRect().right
                          const cards = [...track.querySelectorAll(
                            '.library-cover-card:not(:disabled)')].filter(card =>
                            !card.getAttribute('aria-label').startsWith('Choose Current'))
                          const cut = cards.find(card => {
                            const box = card.getBoundingClientRect()
                            return box.left < edge - 24 && box.right > edge
                          })
                          const card = cut ?? cards[0]
                          const box = card.getBoundingClientRect()
                          return { label: card.getAttribute('aria-label'), cut: Boolean(cut),
                            left: box.left, right: Math.min(box.right, edge),
                            top: box.top, bottom: box.bottom }
                        }"""
                    ),
                )
                if width <= 375:
                    assert target["cut"], target
                card = group.get_by_role("radio", name=target["label"], exact=True)
                card.evaluate(
                    "node => window.scrollBy(0, node.getBoundingClientRect().top - 200)"
                )
                box = card.bounding_box()
                assert box is not None
                x = (box["x"] + min(box["x"] + box["width"], target["right"])) / 2
                y = box["y"] + box["height"] / 2
                scroll_before = track.evaluate("node => node.scrollLeft")
                if mode == "tap":
                    page.touchscreen.tap(x, y)
                else:
                    page.mouse.click(x, y)
                deadline = time.monotonic() + 10
                while not decisions and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(decisions) == 1, (decisions, target)
                # The press did not move the track from under itself.
                assert track.evaluate("node => node.scrollLeft") == scroll_before
                page.wait_for_function(
                    "() => document.activeElement?.matches("
                    '\'.decision-canvas [role="radio"][tabindex="0"], .review-complete\')'
                    " === true"
                )
                page.wait_for_timeout(300)
                assert len(decisions) == 1, decisions
                expect(page.get_by_role("dialog")).to_have_count(0)
            browser.close()


def test_cover_group_arrows_reveal_offscreen_cards_and_space_records_once() -> None:
    """#1290: at 375px the arrows walk the cover track without recording.

    Each card that keyboard focus reaches is scrolled fully into the track,
    including ones that started off its right edge; Space then records the
    focused card once and focus moves to the next field's stop.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 375, "height": 812},
                reduced_motion="reduce",
            )
            page = context.new_page()
            decisions: list[str] = []
            page.on(
                "request",
                lambda request: (
                    decisions.append(request.url)
                    if request.method == "POST" and "/decisions" in request.url
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cover=True,
                    cover_shapes=_COVER_SHAPES,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                page.get_by_role("button", name="Prepare cover comparison").click()
                group = page.get_by_role(
                    "radiogroup", name="Which cover should this work use?"
                )
                group.wait_for()
                track = page.locator(".library-cover-pinned, .library-cover-layout")
                # The enabled cards, in order, and those that start past the
                # track's right edge.
                layout = cast(
                    "dict[str, Any]",
                    track.evaluate(
                        """track => {
                          const edge = track.getBoundingClientRect().right
                          const cards = [...track.querySelectorAll(
                            '.library-cover-card:not(:disabled)')]
                          return {
                            labels: cards.map(card => card.getAttribute('aria-label')),
                            offscreen: cards
                              .filter(card => card.getBoundingClientRect().right > edge + 1)
                              .map(card => card.getAttribute('aria-label')),
                          }
                        }"""
                    ),
                )
                labels = cast("list[str]", layout["labels"])
                offscreen = cast("list[str]", layout["offscreen"])
                assert len(labels) >= 2 and offscreen, layout
                stop = group.locator('[role="radio"][tabindex="0"]')
                assert stop.count() == 1
                stop.focus()
                page.keyboard.press("Shift+Tab")
                page.keyboard.press("Tab")
                focused = """track => {
                  const node = document.activeElement
                  const box = node.getBoundingClientRect()
                  const edge = track.getBoundingClientRect()
                  return { label: node.getAttribute('aria-label'),
                    inside: box.left >= edge.left - 1 && box.right <= edge.right + 1,
                    scrollLeft: track.scrollLeft }
                }"""
                start = cast("dict[str, Any]", track.evaluate(focused))
                assert start["label"] == stop.get_attribute("aria-label"), start
                seen: list[dict[str, Any]] = []
                for _ in labels:
                    page.keyboard.press("ArrowRight")
                    seen.append(cast("dict[str, Any]", track.evaluate(focused)))
                # The arrows visit every enabled card in order and wrap back.
                index = labels.index(start["label"])
                assert [step["label"] for step in seen] == [
                    labels[(index + offset) % len(labels)]
                    for offset in range(1, len(labels) + 1)
                ], seen
                # Every card keyboard focus reached sat wholly inside the track,
                # so the ones that started off its edge were scrolled in.
                assert all(step["inside"] for step in seen), seen
                assert max(step["scrollLeft"] for step in seen) > start["scrollLeft"]
                page.wait_for_timeout(300)
                assert decisions == [], decisions
                # Record the first card that started off screen.
                target = offscreen[0]
                for _ in labels:
                    if track.evaluate(focused)["label"] == target:
                        break
                    page.keyboard.press("ArrowRight")
                landed = cast("dict[str, Any]", track.evaluate(focused))
                assert landed["label"] == target and landed["inside"], landed
                assert decisions == [], decisions
                page.keyboard.press("Space")
                deadline = time.monotonic() + 10
                while not decisions and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(decisions) == 1, decisions
                page.wait_for_function(
                    "() => document.activeElement?.matches("
                    '\'.decision-canvas [role="radio"][tabindex="0"], .review-complete\')'
                    " === true"
                )
                page.wait_for_timeout(300)
                assert len(decisions) == 1, decisions
                expect(page.get_by_role("dialog")).to_have_count(0)
            browser.close()


_CURRENT_FIELD_SELECTOR = '.field-filmstrip button[aria-current="true"]'
_CURRENT_FIELD_CHANGED = (
    "(previous) => { const cell = document.querySelector("
    f"{_CURRENT_FIELD_SELECTOR!r}"
    "); return cell !== null && cell.innerText !== previous }"
)
_COHORT_WORKS = 50


def test_recorded_decision_advances_without_whole_library_projection() -> None:
    """#635: the next field arrives from exact-case state, not from a Library rebuild.

    Fifty works, because one work cannot tell a whole-Library projection apart
    from a single exact-case read. The ordering assertion is a wall-clock
    threshold's opposite: no Library response may complete before the next
    field is actionable, so a settlement that awaits the projection fails it
    whatever the machine's speed.

    That ordering was the whole observable until #641 gave a decision a second
    reason to rebuild the Library, on a coalescing delay long enough that the
    refetch usually landed after the counter had already been read. So the
    each post-advance Library request is held and checked against the committed
    field before it is released. That makes ordering observable directly rather
    than inferring it from a response landing inside a timing window.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        announced: list[Any] = []
        library_requests: list[str] = []
        library_request_fields: list[str | None] = []
        library_responses: list[float] = []
        decision_requests: list[str | None] = []
        decision_responses: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    library_responses.append(time.monotonic())
                    if urlparse(response.url).path == "/api/v1/library"
                    else decision_responses.append(response.url)
                    if response.url.endswith("/decisions")
                    else None
                ),
            )

            def capture_request(request: Request) -> None:
                if request.url.endswith("/decisions"):
                    decision_requests.append(request.headers.get("idempotency-key"))
                elif urlparse(request.url).path == "/api/v1/library":
                    library_requests.append(request.url)
                    field = page.locator(_CURRENT_FIELD_SELECTOR).first
                    library_request_fields.append(
                        field.inner_text() if field.count() else None
                    )

            page.on("request", capture_request)
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    cohort=_COHORT_WORKS - 1,
                    on_app=lambda app: announced.append(
                        app.state.review_application.updates
                    ),
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                search = f"?lens=needs_attention&q=Mistborn&preview={case_id}"
                page.goto(f"{origin}/library{search}")
                page.locator(".field-map").wait_for()
                page.wait_for_load_state("networkidle")
                # Exercise an actual middle-field advance after the owner has
                # read down the page. The rail keeps its original visible order
                # and may move horizontally, but must not pull the document up.
                rail_order = [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ]
                page.locator(".field-filmstrip button").nth(1).click()
                page.evaluate("window.scrollTo(0, 220)")
                scroll_before = page.evaluate("() => window.scrollY")
                page.emulate_media(reduced_motion="no-preference")
                held_library_requests: list[Any] = []

                def hold_library_request(route: Any) -> None:
                    held_library_requests.append(route)

                page.route("**/api/v1/library**", hold_library_request)

                def current_field() -> str:
                    return page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()

                def advance(*, pointer: bool = False) -> tuple[float, int, str, str]:
                    before = current_field()
                    settled = len(library_responses)
                    choice = page.get_by_role(
                        "radio", name="Choose current value:", exact=False
                    ).first
                    started = time.monotonic()
                    if pointer:
                        choice.evaluate(
                            "node => node.addEventListener('pointerdown', () => { "
                            "window.__decisionPointerScroll = window.scrollY }, "
                            "{once: true, capture: true})"
                        )
                        choice.click()
                    else:
                        choice.focus()
                        page.keyboard.press("Enter")
                    page.wait_for_function(
                        _CURRENT_FIELD_CHANGED,
                        arg=before,
                    )
                    return (
                        time.monotonic() - started,
                        len(library_responses) - settled,
                        before,
                        current_field(),
                    )

                # Each decision refreshes this page and the library summary
                # (the unsearched lens=all read), so it issues two (#1284).
                per_decision = 2

                def release_aggregate_request(
                    decision: int, *, previous: str, committed: str
                ) -> None:
                    expected = decision * per_decision
                    deadline = time.monotonic() + 5
                    while (
                        len(held_library_requests) < expected
                        or len(library_request_fields) < issued + expected
                    ) and time.monotonic() < deadline:
                        page.wait_for_timeout(10)
                    assert len(held_library_requests) == expected, held_library_requests
                    assert len(library_request_fields) == issued + expected
                    batch = slice(issued + expected - per_decision, issued + expected)
                    assert sorted(
                        urlparse(url).query for url in library_requests[batch]
                    ) == ["lens=all", "lens=needs_attention&q=Mistborn"]
                    for field in library_request_fields[batch]:
                        assert field == committed
                        assert field != previous
                    for held in held_library_requests[expected - per_decision :]:
                        held.continue_()

                issued = len(library_requests)
                elapsed, projections, first_field, first_next = advance(pointer=True)
                # The aggregate request is held after it starts. The exact
                # case still advances, proving this presentation-ready step
                # does not await a whole-Library read; a request can only see
                # the committed next field, never the recorded one.
                assert projections == 0, (elapsed, projections)
                release_aggregate_request(1, previous=first_field, committed=first_next)
                # A generous ceiling: the measured exact-case read is single
                # figures of milliseconds, and a whole-Library projection over
                # this cohort is two orders of magnitude slower.
                assert elapsed < 1.0, elapsed
                # The exact decision acknowledgement is the authority for this
                # advance; aggregate Library reads remain out of the path.
                assert len(decision_responses) == 1, decision_responses
                assert len(decision_requests) == 1, decision_requests
                assert decision_requests[0]
                pointer_scroll = page.evaluate("() => window.__decisionPointerScroll")
                assert type(pointer_scroll) in {int, float}
                print(
                    json.dumps(
                        {
                            "requested_scroll": scroll_before,
                            "pointer_scroll": pointer_scroll,
                        }
                    ),
                    flush=True,
                )
                assert page.evaluate("() => window.scrollY") == pointer_scroll
                assert page.evaluate(
                    "() => document.activeElement?.matches('.decision-canvas button:not(:disabled)')"
                )
                page.mouse.move(0, 0)
                pointer_focus = page.evaluate(
                    """() => {
                      const card = document.activeElement?.closest('.metadata-choice')
                      const probe = document.createElement('div')
                      probe.style.background = 'var(--surface-strong)'
                      document.body.append(probe)
                      const result = {
                        background: card && getComputedStyle(card).backgroundColor,
                        base: getComputedStyle(probe).backgroundColor,
                        hover: card?.matches(':hover'),
                        keyboardFocus: card?.matches(':has(:focus-visible)'),
                      }
                      probe.remove()
                      return result
                    }"""
                )
                assert pointer_focus == {
                    "background": pointer_focus["base"],
                    "base": pointer_focus["base"],
                    "hover": False,
                    "keyboardFocus": False,
                }
                assert [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ] == rail_order
                assert (
                    page.locator(".decision-canvas").first.evaluate(
                        "node => getComputedStyle(node).animationName"
                    )
                    == "library-field-arrive"
                )
                page.emulate_media(reduced_motion="reduce")
                # The same advanced state remains, but the live media-query
                # change removes its spatial animation. Chromium serialises
                # that removal as either an empty name or `none`.
                reduced_animation = page.locator(".decision-canvas").first.evaluate(
                    "node => ({ name: getComputedStyle(node).animationName, "
                    "transform: getComputedStyle(node).transform })"
                )
                assert reduced_animation == {"name": "", "transform": "none"} or (
                    reduced_animation == {"name": "none", "transform": "none"}
                )
                # The next field is actionable by keyboard without a pointer.
                assert page.evaluate(
                    "() => document.activeElement !== null &&"
                    " document.activeElement !== document.body"
                )
                # Convergence never changes the active search, lens or work.
                # Only `field` moves, which is the advance itself.
                preserved = parse_qs(urlparse(page.url).query)
                assert preserved["lens"] == ["needs_attention"]
                assert preserved["q"] == ["Mistborn"]
                assert preserved["preview"] == [case_id]
                assert preserved["field"] != []
                advanced_field = preserved["field"][0]

                # The recorded middle field is revisitable before the owner
                # continues, and still shows every eligible option with the
                # recorded one integrated into the choice treatment.
                page.locator(".field-filmstrip button").filter(
                    has_text=first_field.split("\n")[0]
                ).first.click()
                recorded = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).first
                expect(recorded).to_have_attribute("aria-checked", "true")
                assert (
                    page.get_by_role(
                        "button", name="Take no action", exact=True
                    ).count()
                    == 0
                )
                assert page.get_by_role(
                    "radio", name="Choose proposed value", exact=False
                ).count()
                assert (
                    "You can select another eligible value"
                    in page.locator(".decision-canvas").first.inner_text()
                )

                # Back and Forward keep the same Library position, then the
                # owner returns to the exact field the first decision opened.
                page.go_back()
                page.wait_for_load_state("networkidle")
                page.go_forward()
                page.wait_for_load_state("networkidle")
                assert page.url.startswith(f"{origin}/library")
                assert "lens=needs_attention" in page.url
                page.locator(".field-filmstrip button").filter(
                    has_text=advanced_field.title()
                ).first.click()

                # A second choice is accepted while the first settlement is
                # still converging, in order, without a reload.
                second_elapsed, second_projections, second_field, second_next = (
                    advance()
                )
                assert second_projections == 0, (second_elapsed, second_projections)
                release_aggregate_request(
                    2, previous=second_field, committed=second_next
                )
                assert second_field != first_field
                deadline = time.monotonic() + 10
                while len(decision_responses) < 2 and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(decision_responses) == 2, decision_responses
                assert len(decision_requests) == 2, decision_requests
                assert all(decision_requests)
                assert decision_requests[0] != decision_requests[1]

                # Aggregate refreshes may now converge badges and the work rail,
                # but remain background work: the exact-case acknowledgement
                # above advanced and focused the next field before either one
                # completed.
                page.unroute("**/api/v1/library**", hold_library_request)
                page.wait_for_timeout(600)
                assert len(library_requests[issued:]) == 2 * per_decision, (
                    library_requests[issued:]
                )

                # And the other half of the same rule: everything this tab did
                # not cause still reaches it while the owner keeps recording.
                # This is the announcement a work makes when it finishes
                # preparing and becomes browseable, published through the
                # server's own channel, naming a case that is not this one.
                # Suppressing it would be a panel that silently stops updating
                # while a large library is still preparing, which is the whole
                # of what #641 exists to prevent.
                channel = announced[0]
                cutover = len(library_requests)
                channel.publish("library", "review_case_" + "0" * 64)
                deadline = time.monotonic() + 10
                while len(library_requests) == cutover and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(library_requests) > cutover, (
                    "a work finishing preparation never reached the Library"
                )

                assert errors == []
            browser.close()


def test_compact_lower_choice_reveals_only_the_next_focused_control() -> None:
    """#635: a lower compact choice reveals, but does not centre, the next action."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 320},
                reduced_motion="reduce",
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(
                    f"{origin}/library?lens=needs_attention&preview={case_id}&field=title"
                )
                page.locator(".decision-canvas").wait_for()
                # The write handoff above the work arrives on its own reads. Until
                # it does, the browser's scroll anchoring moves scrollY by its
                # height while the view stays still, which a cold run measured
                # as a scroll (#1240, #1290).
                expect(page.locator("#library-handoff-title")).to_contain_text(
                    "1 work is in Library"
                )
                # Same-work manual field navigation retains the real browser
                # scroll position after the router's restoration frame (#1070).
                for width in (390, 320):
                    page.set_viewport_size({"width": width, "height": 800})
                    for destination, keyboard in (("series", False), ("title", True)):
                        button = page.get_by_role(
                            "button",
                            name=re.compile(f"^{destination.title()} Needs you"),
                        )
                        button.evaluate(
                            """button => {
                                const masthead = document.querySelector('.masthead');
                                const top = button.getBoundingClientRect().top + scrollY;
                                window.scrollTo(0, top - masthead.getBoundingClientRect().bottom - 24);
                            }"""
                        )
                        if keyboard:
                            button.focus()
                        before_scroll = page.evaluate("() => window.scrollY")
                        assert before_scroll > 0
                        if keyboard:
                            button.press("Enter")
                        else:
                            button.click()
                        expect(button).to_have_attribute("aria-current", "true")
                        page.evaluate(
                            "() => new Promise(resolve => requestAnimationFrame(() => "
                            "requestAnimationFrame(resolve)))"
                        )
                        expect(button).to_be_focused()
                        expect(button).to_be_in_viewport()
                        assert page.evaluate("() => window.scrollY") == before_scroll
                page.set_viewport_size({"width": 390, "height": 320})
                rail_order = [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ]
                before = page.locator(_CURRENT_FIELD_SELECTOR).first.inner_text()
                lower_choice = page.get_by_role(
                    "radio", name="Choose proposed value", exact=False
                )
                lower_choice.scroll_into_view_if_needed()
                scroll_before = page.evaluate("() => window.scrollY")
                assert scroll_before > 0
                page.evaluate(
                    """() => {
                      const original = window.scrollBy.bind(window)
                      window.scrollBy = (options) => {
                          const control = document.activeElement
                          const bounds = control?.getBoundingClientRect()
                          const masthead = document.querySelector('.masthead')
                          // A static masthead may have scrolled away above the top.
                          const visibleTop = Math.max(0, masthead?.getBoundingClientRect().bottom ?? 0) + 8
                          window.__compactReveal = {
                            requested: typeof options === 'object' ? options.top : null,
                            beforeTop: bounds?.top ?? null,
                            beforeBottom: bounds?.bottom ?? null,
                            visibleTop,
                            viewport: innerHeight,
                        }
                        original(options)
                      }
                    }"""
                )
                lower_choice.click()
                page.wait_for_function(_CURRENT_FIELD_CHANGED, arg=before)
                page.wait_for_function(
                    "() => document.activeElement?.matches("
                    "'.decision-canvas button:not(:disabled)')"
                )
                result = page.evaluate(
                    """() => {
                      const control = document.activeElement
                      if (!(control instanceof HTMLButtonElement) || control.disabled)
                        return null
                      const bounds = control.getBoundingClientRect()
                      const masthead = document.querySelector('.masthead')
                      const mastheadBottom = Math.max(0, masthead?.getBoundingClientRect().bottom ?? 0)
                      const hit = document.elementFromPoint(
                        Math.min(innerWidth - 1, Math.max(0, bounds.left + bounds.width / 2)),
                        Math.min(innerHeight - 1, Math.max(0, bounds.top + 1)),
                      )
                          return {
                            focused: control.matches('.decision-canvas button:not(:disabled)'),
                            focusedCount: document.querySelectorAll(
                              '.decision-canvas button:not(:disabled):focus'
                            ).length,
                            top: bounds.top,
                            bottom: bounds.bottom,
                            visibleHeight: Math.min(bounds.bottom, innerHeight) - Math.max(bounds.top, 0),
                            mastheadBottom,
                        occluded: hit !== control && !control.contains(hit),
                        scrollY,
                        reveal: window.__compactReveal ?? null,
                      }
                    }"""
                )
                assert result is not None
                assert result["focused"]
                assert result["focusedCount"] == 1
                assert abs(result["top"] - (result["mastheadBottom"] + 8)) <= 1
                assert result["visibleHeight"] >= 44
                assert not result["occluded"]
                assert result["scrollY"] > 0
                reveal = result["reveal"]
                assert reveal is not None
                expected = (
                    reveal["beforeTop"] - reveal["visibleTop"]
                    if reveal["beforeTop"] < reveal["visibleTop"]
                    else max(0, reveal["beforeBottom"] - reveal["viewport"])
                )
                assert abs(reveal["requested"] - expected) <= 1
                assert [
                    label.splitlines()[0]
                    for label in page.locator(
                        ".field-filmstrip button"
                    ).all_inner_texts()
                ] == rail_order
            browser.close()


_FOCUSED_STOP = (
    "() => document.activeElement?.matches("
    '\'.decision-canvas [role="radio"][tabindex="0"]\') === true'
)


@pytest.mark.parametrize("width", [1440, 375])
def test_review_choice_group_keyboard_model(width: int) -> None:
    """#1290: one radio group per field; arrows move, Space or Enter records once."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": width, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            decisions: list[str] = []
            page.on(
                "request",
                lambda request: (
                    decisions.append(request.url)
                    if request.method == "POST" and "/decisions" in request.url
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(
                    f"{origin}/library?lens=needs_attention&preview={case_id}&field=title"
                )
                group = page.get_by_role(
                    "radiogroup", name="Which title should this work use?"
                )
                group.wait_for()
                snapshot = group.aria_snapshot()
                assert snapshot.startswith(
                    '- radiogroup "Which title should this work use?"'
                ), snapshot
                # Each choice's Evidence toggle is a 44px target at every width,
                # as the cover cards' are (#1290).
                toggle_heights = page.locator(
                    ".metadata-choice .choice-evidence-toggle"
                ).evaluate_all(
                    "nodes => nodes.map((node) => node.getBoundingClientRect().height)"
                )
                assert len(toggle_heights) >= 2, toggle_heights
                assert min(toggle_heights) >= 44, (width, toggle_heights)
                radios = group.get_by_role("radio")
                count = radios.count()
                assert count >= 2, snapshot
                names = [
                    radios.nth(index).get_attribute("aria-label")
                    for index in range(count)
                ]
                assert names[0] is not None
                assert names[0].startswith("Choose current value:"), names
                assert all(
                    name is not None and name.startswith("Choose proposed value:")
                    for name in names[1:]
                ), names
                for index in range(count):
                    radio = radios.nth(index)
                    expect(radio).to_have_attribute("aria-checked", "false")
                    expect(radio).to_have_attribute("aria-posinset", str(index + 1))
                    expect(radio).to_have_attribute("aria-setsize", str(count))
                    assert f'radio "{names[index]}"' in snapshot, snapshot

                # Tab reaches the group at its one stop, the first choice.
                radios.nth(0).focus()
                page.keyboard.press("Shift+Tab")
                expect(radios.nth(0)).not_to_be_focused()
                page.keyboard.press("Tab")
                expect(radios.nth(0)).to_be_focused()
                # Arrows wrap over Current and every alternative and send nothing.
                for step in range(1, count + 1):
                    page.keyboard.press("ArrowDown")
                    expect(radios.nth(step % count)).to_be_focused()
                page.keyboard.press("ArrowUp")
                expect(radios.nth(count - 1)).to_be_focused()
                page.wait_for_timeout(300)
                assert decisions == []

                # Space records the focused alternative once and opens the next
                # field with focus on its group's stop.
                page.keyboard.press("Space")
                page.wait_for_function(
                    "() => !new URL(location.href).searchParams.get('field')"
                    "?.startsWith('title')"
                )
                page.wait_for_function(_FOCUSED_STOP)
                assert len(decisions) == 1, decisions
                next_group = page.locator(".decision-canvas [role='radiogroup']")
                expect(next_group).to_have_count(1)
                next_name = next_group.get_attribute("aria-label")
                assert next_name is not None
                assert next_name != "Which title should this work use?"
                expect(
                    page.locator(".decision-canvas [role='radio']:focus")
                ).to_be_visible()
                # Enter records the next field once in the same way.
                page.keyboard.press("Enter")
                deadline = time.monotonic() + 10
                while len(decisions) < 2 and time.monotonic() < deadline:
                    page.wait_for_timeout(25)
                assert len(decisions) == 2, decisions
                page.wait_for_function(
                    "() => document.activeElement?.matches("
                    '\'.decision-canvas [role="radio"][tabindex="0"], .review-complete\')'
                    " === true"
                )
                page.wait_for_timeout(300)
                assert len(decisions) == 2, decisions

                # Revisiting the first field states the recorded choice.
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                group.wait_for()
                recorded = group.aria_snapshot()
                assert f'radio "{names[-1]}" [checked]' in recorded, recorded
                chosen = radios.nth(count - 1)
                expect(chosen).to_have_attribute("aria-checked", "true")
                expect(chosen).to_have_attribute("tabindex", "0")
                expect(chosen).to_contain_text("Your choice")
                # Tab reaches no other choice on the way: an Evidence button in
                # an earlier card may come first, in reading order (R2).
                radios.nth(0).focus()
                page.keyboard.press("Shift+Tab")
                for _ in range(4):
                    page.keyboard.press("Tab")
                    if page.evaluate(
                        "() => document.activeElement?.getAttribute('role') === 'radio'"
                    ):
                        break
                expect(chosen).to_be_focused()
            browser.close()


def test_an_audiobook_row_opens_the_evidence_its_report_recorded() -> None:
    """Walk the shipped browser to the evidence, because only that proves it.

    Every layer below this passed while the browser dropped `detail_url` on
    the floor and drew the row as an unclickable failure card, so an owner
    could not reach a single field the F43 inspection recorded. This clicks
    the real row in a real Chromium against the real server and reads the
    narrator, the ASIN and the running time off the screen.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    audiobook=True,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&q=Recorded audiobook")
                row = page.get_by_role("button", name=re.compile("^Recorded audiobook"))
                expect(row).to_be_visible()
                # Not a failure card: the report is the outcome, not the fault.
                # Its work identity was not admitted, so it is Not matched and
                # says why (#1407).
                expect(row).to_contain_text("Found, but not this work")
                assert page.locator(".library-work-row.is-terminal").count() == 0

                row.click()

                evidence = page.get_by_role("region", name="Recorded evidence")
                expect(evidence).to_be_visible()
                expect(evidence).to_contain_text("Recorded Narrator")
                expect(evidence).to_contain_text("B0CW1KL2QD")
                # A running time reads in hours and minutes (#1286).
                expect(evidence).to_contain_text(re.compile(r"Running time\s*12 h"))
                expect(evidence).not_to_contain_text("43200")
                expect(evidence).to_contain_text("Embedded cover art is present")
                page.get_by_text(re.compile(r"Chapters \(1\)")).click()
                expect(evidence).to_contain_text("Chapter One")
                # What is wrong with the media, in its own named region: the
                # observation, the rule it broke, and what it was read from.
                health = page.get_by_role("region", name="Media health")
                expect(health).to_be_visible()
                expect(health).to_contain_text("Shorter than the edition")
                expect(health).to_contain_text("12 h of local audio, 8 h shorter")
                expect(health).to_contain_text("within 1 min 30 s")
                expect(health).to_contain_text("Audible product B0SYNE7P1K")
            browser.close()
        assert errors == []


def test_an_admitted_audiobook_is_decided_like_any_other_case(
    chapter_runtime: bool = False,
) -> None:
    """The F47 outcome in the shipped browser, end to end.

    An admitted recording is a review case like any other: the owner opens it
    from the Library, sees Narrators and ASIN among its fields, accepts one,
    and reads it in the history with no "recorded, not applied" note, because
    an audiobook decision now applies through the guarded path (#968).
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    audiobook_case=True,
                    audiobook=chapter_runtime,
                    chapter_runtime=chapter_runtime,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&q=Ember Tide Protocol")
                row = page.get_by_role(
                    "button", name=re.compile("^Ember Tide Protocol")
                ).first
                expect(row).to_be_visible()
                # A recording is an ordinary review row, not a failure card,
                # and it joins the queue under an ebook's conditions: its
                # settled Narrators and ASIN fills ask for nothing, yet both
                # stay choosable in the case below (#1249).
                expect(row).to_contain_text("No attention needed")
                assert page.locator(".library-work-row.is-terminal").count() == 0

                row.click()

                basis = page.get_by_role("region", name="Recording identity evidence")
                if chapter_runtime:
                    expect(basis).to_contain_text(
                        "Complete chapter timing and structure corroborate this recording where overall runtime is unavailable."
                    )
                    expect(basis).to_contain_text(
                        "where overall runtime is unavailable."
                    )
                    basis.get_by_text("Recording source").click()
                    expect(basis).to_contain_text("Audible product B0SYNE7P1K")
                    source = basis.locator("summary")
                    desktop_bounds = source.bounding_box()
                    # Every disclosure is a full target, at every width (#1288).
                    assert desktop_bounds is not None and desktop_bounds["height"] >= 44
                    page.set_viewport_size({"width": 390, "height": 844})
                    bounds = source.bounding_box()
                    assert bounds is not None and bounds["height"] >= 44
                    source.focus()
                    source.press("Space")
                    expect(basis.locator("details")).not_to_have_attribute("open", "")
                    source.press("Space")
                    expect(basis.locator("details")).to_have_attribute("open", "")
                    page.get_by_role("link", name="Evidence", exact=True).click()
                    # The router keeps Library in place until the diagnostic route's
                    # code is ready, so wait for that route before measuring it.
                    expect(
                        page.get_by_role("link", name="Back to work", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_role("region", name="Recording identity evidence")
                    ).to_be_visible()
                    source = page.get_by_role(
                        "region", name="Recording identity evidence"
                    ).locator("summary")
                    bounds = source.bounding_box()
                    assert bounds is not None and bounds["height"] >= 44
                    source.focus()
                    source.press("Space")
                    expect(source.locator("..")).to_have_attribute("open", "")
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.get_by_role("link", name="Back to work").click()
                else:
                    expect(basis).to_have_count(0)
                rail = page.locator(".field-filmstrip button")
                expect(rail.first).to_be_visible()
                labels = [label.splitlines()[0] for label in rail.all_inner_texts()]
                assert "Narrators" in labels
                assert "ASIN" in labels
                # The case page agrees with its row: both fills are optional
                # choices, so nothing is counted as needing review (#1249).
                expect(
                    page.locator("dt", has_text=re.compile("^Needs review$")).locator(
                        "xpath=following-sibling::dd[1]"
                    )
                ).to_have_text("0")
                expect(page.get_by_text(re.compile("^0 remaining"))).to_be_visible()
                for name in ("ASIN", "Narrators"):
                    button = page.get_by_role(
                        "button", name=re.compile(f"^{name}")
                    ).first
                    expect(button).not_to_have_accessible_name(re.compile("Needs you"))
                    expect(button).to_have_accessible_name(re.compile("Optional$"))
                if chapter_runtime:
                    # The compact layout offers the same optional choices.
                    page.set_viewport_size({"width": 390, "height": 844})

                page.get_by_role("button", name=re.compile("^ASIN")).first.click()
                canvas = page.locator(".decision-canvas")
                expect(canvas).to_be_visible()
                expect(canvas).not_to_contain_text(
                    re.compile("needs (your )?review", re.IGNORECASE)
                )
                accept = page.get_by_role(
                    "radio", name=re.compile("^Choose proposed value: ")
                ).first
                expect(accept).to_be_visible()
                accept.click()
                if chapter_runtime:
                    expect(
                        page.get_by_role("heading", name="Review complete")
                    ).to_be_visible()
                    page.get_by_role(
                        "button", name=re.compile("^Narrators")
                    ).first.click()
                    keep = page.get_by_role(
                        "radio", name=re.compile("^Choose current value: ")
                    ).first
                    expect(keep).to_be_visible()
                    keep.click()
                    expect(
                        page.get_by_role(
                            "button",
                            name=re.compile("^Narrators · Current value retained"),
                        )
                    ).to_be_visible()
                    page.set_viewport_size({"width": 1440, "height": 900})

                page.get_by_role("link", name="History").click()
                expect(
                    page.get_by_text("Owner selected a proposed value.").first
                ).to_be_visible()
                assert 0 == page.get_by_text(RECORDED_ONLY_REASON).count()
                if chapter_runtime:
                    page.goto(f"{origin}/library?lens=all&q=Recorded audiobook")
                    page.get_by_role(
                        "button", name=re.compile("^Recorded audiobook")
                    ).click()
                    expect(
                        page.get_by_role("region", name="Recording identity evidence")
                    ).to_have_count(0)
                    page.get_by_text("Chapters (6)", exact=True).click()
                    expect(page.get_by_text("00:00:00 – 00:01:53")).to_be_visible()

            browser.close()
        assert errors == []


def test_chapter_runtime_case_explains_its_identity_basis() -> None:
    test_an_admitted_audiobook_is_decided_like_any_other_case(chapter_runtime=True)


def test_a_qualified_audiobook_tranche_states_its_candidate_backing_and_gate() -> None:
    """A6: the field rail says what decided a recording's field and why.

    Under a test-supplied F46 ASIN record the recording's ASIN is resolved
    automatically. The rail states the candidate, the authorities behind it and
    the gate that bound the identifier to this recording, while Narrators --
    named by the same two authorities but without a record -- is still the
    owner's to answer (#967).
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1440, "height": 900},
                reduced_motion="reduce",
            )
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            with (
                patch.object(
                    audiobook_decision_qualification,
                    "F46_PASSING_AUTOMATIC_OPTIONS",
                    frozenset({audiobook_decision_qualification.F46_ASIN_IDENTITY}),
                ),
                patch.object(
                    audiobook_decision_qualification,
                    "F46_QUALIFICATION_MANIFEST_SHA256",
                    "7" * 64,
                ),
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    audiobook_tranche=True,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&q=Ember Tide Protocol")
                row = page.get_by_role(
                    "button", name=re.compile("^Ember Tide Protocol")
                ).first
                expect(row).to_be_visible()
                row.click()

                page.get_by_role("button", name=re.compile("^ASIN")).first.click()
                choice = page.locator(".direct-choice").first
                expect(choice).to_be_visible()
                expect(choice).to_contain_text("B0SYNE7P1K")
                expect(choice).to_contain_text(
                    "Selected from Audible and Audnexus to fill an empty ASIN field "
                    "under a qualified automatic policy; the runtime gate "
                    "corroborated that this recording is that product."
                )

                # Narrators is named by the same two authorities and has no
                # record, so under the Ask policy for authors and narrators it is
                # still a question the owner answers, and is counted as one (#1249).
                expect(
                    page.get_by_role(
                        "button", name=re.compile("^Narrators Needs you")
                    ).first
                ).to_be_visible()
                page.get_by_role("button", name=re.compile("^Narrators")).first.click()
                expect(
                    page.get_by_role(
                        "radiogroup", name="Which narrators should this work use?"
                    )
                ).to_be_visible()
                narrators = page.locator(".direct-choice").first
                expect(narrators).to_be_visible()
                expect(narrators).to_contain_text("Ilsa Brandt")
                assert (
                    0 == page.get_by_text("under a qualified automatic policy").count()
                )
                expect(
                    page.get_by_role(
                        "radio", name=re.compile("^Choose proposed value: ")
                    ).first
                ).to_be_visible()
            browser.close()
        assert errors == []


def test_z_compact_uncached_library_detail_keeps_visible_focus() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                held_selected_detail: list[Any] = []

                def hold_selected_detail(route) -> None:
                    if route.request.url.endswith(f"/api/v1/review-cases/{case_id}"):
                        held_selected_detail.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/review-cases/**", hold_selected_detail)
                _login(page, origin)
                page.locator(".library-work-row").first.click()
                page.get_by_text("Loading selected work…").wait_for()
                assert len(held_selected_detail) == 1
                pending_detail = page.locator(".workspace-detail")
                expect(pending_detail).to_be_focused()
                assert pending_detail.bounding_box() is not None
                held_selected_detail[0].continue_()
                page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).wait_for()
                expect(page.locator(".selected-work-header")).to_be_focused()
                page.unroute("**/api/v1/review-cases/**", hold_selected_detail)
            browser.close()


def test_refused_openrouter_validation_is_shown_beside_its_button_on_a_phone() -> None:
    """A long reviewer form must not leave its refusal above a phone viewport."""

    class State:
        def read(self, _verifier: str) -> SetupStateDocument:
            return SetupStateDocument(
                schema_version="setup-state-v4",
                revision=1,
                bootstrap_verifier_digest="0" * 64,
                claim_token_digest=claim_token_digest(_CLAIM_TOKEN),
            )

    setup_client = TestClient(
        create_setup_app(cast(Any, object()), cast(Any, State()), ""),
        base_url="https://testserver",
    )
    origin = "https://setup.test"
    access = {
        "schema_version": "reviewer-access-view-v2",
        "cover_metadata": None,
        "cost_evidence": {"schema_version": "reviewer-cost-evidence-v2", "models": []},
        "revision": 3,
        "mapping": None,
        "codex": {
            "state": "not_provisioned",
            "reason": "profile_not_provisioned",
            "model": None,
        },
        "recommendations": ["openai/gpt-6-luna"],
        "candidates": [
            {"model": f"synthetic/vision-{index}", "tier": "paid", "suggested": False}
            for index in range(12)
        ],
        "free_tier_caveat": "Account limits apply.",
        "data_use_caveat": "Cover previews are sent to the selected provider.",
    }

    def setup_api(route: Any) -> None:
        path = urlparse(route.request.url).path
        if path == "/api/setup/state":
            route.fulfill(
                json={
                    "csrf_token": "synthetic-csrf",
                    "next_step": "reviewer",
                    "completed_steps": [
                        "claim",
                        "connect",
                        "providers",
                        "libraries",
                        "mounts",
                    ],
                    "revision": 7,
                    "providers": [],
                    "libraries": [],
                    "mounts": [],
                }
            )
        elif path == "/api/setup/reviewer-access":
            route.fulfill(json=access)
        elif path == "/api/setup/reviewer/openrouter":
            route.fulfill(status=422, json={"detail": {"error": "invalid_key"}})
        else:
            route.fulfill(status=404, json={"error": "not_found"})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 375, "height": 667})
        with _closing_context(context):
            page = context.new_page()
            page_errors: list[str] = []
            page.on("pageerror", partial(_record_page_error, page_errors))
            page.route(origin + "/**", partial(_serve_setup_asset, setup_client))
            page.route(origin + "/api/setup/**", setup_api)
            page.goto(origin + "/")
            page.get_by_role("textbox", name="Model ID").fill("synthetic/vision-1")
            page.get_by_label("API key (write-only)").fill("synthetic-openrouter-key")
            save = page.get_by_role("button", name="Validate and use OpenRouter")
            save.click()
            alert = page.get_by_role("alert")
            expect(alert).to_have_text(
                "OpenRouter rejected this key. Replace it and validate again."
            )
            key_field = page.get_by_label("API key (write-only)")
            expect(key_field).to_be_focused()
            expect(key_field).to_have_value("")
            assert page.evaluate("document.activeElement !== document.body")
            # The form is long enough at this width that its top is off screen.
            assert page.evaluate(
                "document.querySelector('#setup-main form').getBoundingClientRect().top < 0"
            )
            expect(alert).to_be_in_viewport()
            expect(save).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page_errors == []
        browser.close()


def test_setup_to_library_preparation_handoff_focuses_and_announces_destination() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as reserved_port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=reserved_port,
                preparation=True,
            ) as (origin, _case_id),
        ):
            browser = playwright.chromium.launch(headless=True)
            for reduced in (False, True):
                transition_records: list[dict[str, object]] = []
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                    reduced_motion="reduce" if reduced else "no-preference",
                )
                context.add_init_script(
                    "const preparationTime=value=>value.split(',').reduce((largest,item)=>Math.max(largest,parseFloat(item)*(item.trim().endsWith('ms')?1:1000)),0);"
                    "globalThis.preparationPseudoStyles=()=>['root','mechanical-librarian-brand','works-index','preparation-detail'].flatMap(name=>['group','image-pair','old','new'].map(kind=>{const pseudo=`::view-transition-${kind}(${name})`;const style=getComputedStyle(document.documentElement,pseudo);return {pseudo,duration:preparationTime(style.animationDuration),delay:preparationTime(style.animationDelay),state:style.animationPlayState}}));"
                    "const preparationRecord=(event,phase)=>console.debug('__ML_PREPARATION_TRANSITION__'+JSON.stringify({event:event.type,phase,observed:Boolean(event.viewTransition),pseudos:event.viewTransition?preparationPseudoStyles():[],animations:event.viewTransition?document.documentElement.getAnimations({subtree:true}).map(animation=>{const timing=animation.effect?.getComputedTiming?.()||{};return {pseudo:animation.effect?.pseudoElement||animation.effect?.target?.type||'',duration:Number(timing.duration)||0,delay:Number(timing.delay)||0,state:animation.playState}}):[]}));"
                    "addEventListener('pageswap',event=>{preparationRecord(event,'event');if(event.viewTransition)event.viewTransition.ready.then(()=>preparationRecord(event,'ready'),()=>preparationRecord(event,'rejected'))});"
                    "addEventListener('pagereveal',event=>{document.documentElement.dataset.preparationPagereveal=event.viewTransition?'observed':'absent';preparationRecord(event,'event');if(event.viewTransition)event.viewTransition.ready.then(()=>preparationRecord(event,'ready'),()=>preparationRecord(event,'rejected'))});"
                )
                page = context.new_page()
                console_errors: list[str] = []
                page_errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, page_errors))
                page.on(
                    "console",
                    partial(
                        _record_transition_console,
                        console_errors,
                        transition_records,
                    ),
                )
                page.route("**/favicon.ico", lambda route: route.fulfill(status=204))
                _login(page, origin)
                setup_url = origin + "/setup-handoff"

                class State:
                    def read(self, _verifier: str) -> SetupStateDocument:
                        return SetupStateDocument(
                            schema_version="setup-state-v4",
                            revision=1,
                            bootstrap_verifier_digest="0" * 64,
                            claim_token_digest=claim_token_digest(_CLAIM_TOKEN),
                        )

                setup_client = TestClient(
                    create_setup_app(cast(Any, object()), cast(Any, State()), ""),
                    base_url="https://testserver",
                )

                secured_setup_asset = partial(_serve_setup_asset, setup_client)

                page.route(setup_url, secured_setup_asset)
                page.route(origin + "/assets/theme.js", secured_setup_asset)
                page.route(origin + "/assets/setup.css", secured_setup_asset)
                page.route(origin + "/assets/setup-handoff.css", secured_setup_asset)
                page.route(origin + "/assets/setup.js", secured_setup_asset)

                def setup_api(route) -> None:
                    path = urlparse(route.request.url).path
                    if path == "/api/setup/state":
                        route.fulfill(
                            json={
                                "csrf_token": "synthetic-csrf",
                                "next_step": "kick_off",
                                "completed_steps": [
                                    "claim",
                                    "connect",
                                    "providers",
                                    "libraries",
                                    "mounts",
                                    "reviewer",
                                ],
                                "revision": 7,
                                "providers": [],
                                "libraries": [],
                                "mounts": [],
                            }
                        )
                    elif path == "/api/setup/reviewer-access":
                        # The kickoff summary states the recorded reviewer choice.
                        route.fulfill(json={"revision": 7, "mapping": None})
                    elif path == "/api/setup/kick-off":
                        route.fulfill(
                            json={
                                "message": "Nothing in your library changes. Writes "
                                "stay off until you turn them on in Activity."
                            }
                        )
                    else:
                        route.fulfill(status=404, json={"error": "not_found"})

                page.route("**/api/setup/**", setup_api)
                page.goto(setup_url)
                assert (
                    "style-src 'self'"
                    in page.locator("html").evaluate(
                        "() => document.querySelector('meta[http-equiv=content-security-policy]')?.content || ''"
                    )
                    or "style-src 'self'"
                    in setup_client.get("/").headers["content-security-policy"]
                )
                transition_records.clear()
                page.get_by_role("button", name="Kick off preparation").click()
                handoff = page.get_by_role("link", name="Open workspace")
                expect(handoff).to_have_attribute(
                    "href", re.compile(r"preparation=preparation_run_[0-9a-f]{64}")
                )
                handoff_href = handoff.get_attribute("href")
                assert handoff_href is not None
                run_id = parse_qs(urlparse(handoff_href).query)["preparation"][0]
                presentation_state: dict[str, str | None] = {"value": "running"}
                preparation_requests = {"count": 0}
                active_name = {"value": "Synthetic active title"}

                def preparation_api(
                    route,
                    _request,
                    state_ref=presentation_state,
                    expected_run_id=run_id,
                    requests=preparation_requests,
                    active=active_name,
                ) -> None:
                    state = state_ref["value"]
                    requests["count"] += 1
                    if state is None:
                        route.fulfill(
                            json={
                                "schema_version": "review-preparation-v2",
                                "recovery_required": False,
                                "active": False,
                                "actionable": False,
                                "paused": False,
                                "libraries": [],
                                "connections": [],
                                "presentation": None,
                            }
                        )
                        return
                    terminal = state in {
                        "completed_clean",
                        "completed_with_failures",
                    }
                    failed = 1 if state == "completed_with_failures" else 0
                    route.fulfill(
                        json={
                            "schema_version": "review-preparation-v2",
                            "recovery_required": False,
                            "active": not terminal,
                            "actionable": terminal,
                            "paused": state == "blocked",
                            "libraries": [],
                            "connections": [],
                            "presentation": {
                                "run_id": expected_run_id,
                                "state": state,
                                "triggers": [
                                    {
                                        "trigger": "initial_setup",
                                        "accepted_count": 1,
                                        "first_accepted_at": 100,
                                        "last_accepted_at": 100,
                                    }
                                ],
                                "passage": {
                                    "read": 4,
                                    "searches_finished": 4 if terminal else 3,
                                    "compared": 4 if terminal else 2,
                                    "checked": 4 if terminal else 1,
                                    "ready": 4 - failed if terminal else 0,
                                    "failed": failed,
                                },
                                "active_books": (
                                    []
                                    if terminal
                                    else [
                                        {
                                            "name": active["value"],
                                            "stage": "Checking metadata",
                                        }
                                    ]
                                ),
                                "active_overflow": 0,
                                "recent_completions": [
                                    {
                                        "name": "Synthetic finished title",
                                        "outcome": "ready",
                                        "completed_at": 100,
                                    }
                                ],
                                "throughput_per_minute": 0,
                                "started_at": 100,
                                "updated_at": 101,
                                "completed_at": 101 if terminal else None,
                                "total": 4,
                                "dismissible": terminal,
                                "now": {
                                    "ready": 4 - failed if terminal else 0,
                                    "recorded": 0,
                                    "not_matched": 0,
                                    "failed": failed,
                                    "in_progress": 0 if terminal else 4,
                                    "scan_ids": [],
                                },
                            },
                        }
                    )

                page.route("**/api/v1/review-preparation", preparation_api)
                # #1240 item 8: a long unbroken title wraps inside the card at
                # the 320 px floor, and the running state is said only once.
                page.set_viewport_size({"width": 320, "height": 844})
                active_name["value"] = "Synthetic" + "unbroken" * 24
                expect(page.locator(".setup-active strong")).to_have_text(
                    active_name["value"]
                )
                assert page.evaluate(
                    "[...document.querySelectorAll('.setup-active li, .step-card')]"
                    ".every(node => node.scrollWidth <= node.clientWidth + 1)"
                    " && document.documentElement.scrollWidth <= 320"
                )
                assert (
                    page.locator("#setup-main")
                    .inner_text()
                    .count("Preparation continues")
                    == 1
                )
                active_name["value"] = "Synthetic active title"
                expect(page.locator(".setup-active strong")).to_have_text(
                    active_name["value"]
                )
                page.set_viewport_size({"width": 390, "height": 844})
                source_names = page.evaluate(
                    "[getComputedStyle(document.querySelector('.masthead')).viewTransitionName,"
                    "getComputedStyle(document.querySelector('#setup-ledger')).viewTransitionName,"
                    "getComputedStyle(document.querySelector('.step-card')).viewTransitionName]"
                )
                has_navigation_rule = page.evaluate(
                    "Array.from(document.styleSheets).some(sheet=>Array.from(sheet.cssRules).some(rule=>rule.cssText.startsWith('@view-transition')))"
                )
                assert not has_navigation_rule
                assert page.locator("#setup-handoff-motion").evaluate(
                    "element => element.disabled"
                )
                assert source_names == (
                    ["none", "none", "none"]
                    if reduced
                    else [
                        "mechanical-librarian-brand",
                        "works-index",
                        "preparation-detail",
                    ]
                )

                handoff.click()
                page.wait_for_url(re.compile(r"/library\?.*preparation="))
                heading = page.get_by_role("heading", name="Preparing your library")
                expect(heading).to_be_focused()
                expect(page.locator(".preparation-run-state")).to_have_text(
                    "Preparation continues"
                )
                # Its one status line speaks for it; the detail is no live
                # region re-read on every poll (#1288).
                expect(page.locator(".workspace-detail")).not_to_have_attribute(
                    "aria-live", re.compile(r".*")
                )
                expect(page.locator(".preparation-run-state")).to_have_attribute(
                    "role", "status"
                )
                # Find in Works is a full target even though it wraps with the
                # name it searches for (#1288).
                find = page.get_by_role("link", name="Find in Works")
                expect(find).to_have_count(1)
                find_bounds = find.bounding_box()
                assert find_bounds is not None and find_bounds["height"] >= 44
                passage = page.get_by_role("progressbar")
                expect(passage).to_have_count(4)
                passage_facts = passage.evaluate_all(
                    "elements => elements.map(element => ({label:element.getAttribute('aria-label'),value:element.value,max:element.max,position:element.position,style:element.getAttribute('style'),appearance:getComputedStyle(element).appearance,width:element.getBoundingClientRect().width}))"
                )
                assert passage_facts == [
                    {
                        "label": "Read from library: 4 of 4",
                        "value": 4,
                        "max": 4,
                        "position": 1,
                        "style": None,
                        "appearance": "none",
                        "width": passage_facts[0]["width"],
                    },
                    {
                        "label": "Searches finished: 3 of 4",
                        "value": 3,
                        "max": 4,
                        "position": 0.75,
                        "style": None,
                        "appearance": "none",
                        "width": passage_facts[1]["width"],
                    },
                    {
                        "label": "Candidates compared: 2 of 4",
                        "value": 2,
                        "max": 4,
                        "position": 0.5,
                        "style": None,
                        "appearance": "none",
                        "width": passage_facts[2]["width"],
                    },
                    {
                        "label": "Metadata checked: 1 of 4",
                        "value": 1,
                        "max": 4,
                        "position": 0.25,
                        "style": None,
                        "appearance": "none",
                        "width": passage_facts[3]["width"],
                    },
                ]
                assert all(fact["width"] > 0 for fact in passage_facts)
                destination_names = page.evaluate(
                    "[getComputedStyle(document.querySelector('.masthead')).viewTransitionName,"
                    "getComputedStyle(document.querySelector('.library-works')).viewTransitionName,"
                    "getComputedStyle(document.querySelector('.preparation-detail')).viewTransitionName]"
                )
                assert destination_names == (
                    ["none", "none", "none"]
                    if reduced
                    else [
                        "mechanical-librarian-brand",
                        "works-index",
                        "preparation-detail",
                    ]
                )
                page.wait_for_timeout(100)
                assert any(
                    record["event"] == "pageswap" and record["observed"]
                    for record in transition_records
                )
                assert (
                    page.locator("html").get_attribute("data-preparation-pagereveal")
                    == "observed"
                )
                animation_sets = [
                    animation
                    for record in transition_records
                    for animation in cast(list[dict[str, object]], record["animations"])
                ]
                pseudo_styles = [
                    animation
                    for record in transition_records
                    for animation in cast(list[dict[str, object]], record["pseudos"])
                ]
                nonzero_running = [
                    animation
                    for animation in animation_sets
                    if animation["state"] == "running"
                    and cast(float, animation["duration"])
                    + cast(float, animation["delay"])
                    > 0
                ]
                if reduced:
                    assert nonzero_running == []
                    assert all(
                        cast(float, animation["duration"])
                        + cast(float, animation["delay"])
                        == 0
                        for animation in pseudo_styles
                    )
                else:
                    assert any(
                        cast(float, animation["duration"]) > 0
                        and any(
                            name in cast(str, animation["pseudo"])
                            for name in (
                                "mechanical-librarian-brand",
                                "works-index",
                                "preparation-detail",
                            )
                        )
                        for animation in pseudo_styles
                    ), pseudo_styles
                summary_cases = (
                    (
                        "blocked",
                        "Preparation needs attention",
                        "0 ready to browse · 1 active",
                    ),
                    (
                        "completed_clean",
                        "Preparation complete",
                        "4 ready to browse",
                    ),
                    (
                        "completed_with_failures",
                        "Preparation completed with failures",
                        "3 ready to browse · 1 could not finish",
                    ),
                )
                for state, title, copy in summary_cases:
                    presentation_state["value"] = state
                    page.reload()
                    summary = page.locator(".preparation-summary")
                    expect(summary).to_contain_text(title)
                    expect(summary).to_contain_text(copy)
                    # At 390 the selected run fills the screen and Works is one
                    # Back away. (A substring "Works" match once passed here on
                    # the write handoff's "… works are ready to browse" heading.)
                    expect(page.locator("#library-works-title")).to_have_text("Works")
                    expect(
                        page.get_by_role("button", name="Back to works")
                    ).to_be_visible()
                    expect(page.locator(".library-work-row").first).to_be_enabled()
                if not reduced:
                    presentation_state["value"] = "running"
                    page.goto(setup_url)
                    page.get_by_role("button", name="Kick off preparation").click()
                    handoff = page.get_by_role("link", name="Open workspace")
                    expect(handoff).to_have_attribute(
                        "href",
                        re.compile(r"preparation=preparation_run_[0-9a-f]{64}"),
                    )
                    page.eval_on_selector(
                        "a.workspace-handoff",
                        "element => { element.addEventListener('click', event => event.preventDefault(), {once:true}); element.click() }",
                    )
                    page.evaluate(
                        "() => { const event = new Event('pageswap'); Object.defineProperty(event, 'viewTransition', {value:{ready:Promise.resolve(),finished:Promise.reject(new Error('synthetic rejected transition'))}}); dispatchEvent(event) }"
                    )
                    page.wait_for_function(
                        "document.querySelector('#setup-handoff-motion').disabled"
                    )
                    page.eval_on_selector(
                        "a.workspace-handoff",
                        "element => { element.addEventListener('click', event => event.preventDefault(), {once:true}); element.click() }",
                    )
                    page.wait_for_timeout(2100)
                    assert page.url == setup_url
                    assert page.locator("#setup-handoff-motion").evaluate(
                        "element => element.disabled"
                    )
                    presentation_state["value"] = "completed_clean"
                    page.get_by_role(
                        "heading", name="Preparation is complete"
                    ).wait_for()
                    requests_after_terminal = preparation_requests["count"]
                    presentation_state["value"] = None
                    page.get_by_role(
                        "heading", name="Preparation is no longer available"
                    ).wait_for(timeout=7000)
                    assert preparation_requests["count"] == requests_after_terminal + 1
                    expect(handoff).to_have_attribute("href", "/library?lens=all")
                    expect(page.get_by_text("Reconnecting", exact=False)).to_have_count(
                        0
                    )
                    requests_after_null = preparation_requests["count"]
                    page.wait_for_timeout(1600)
                    assert preparation_requests["count"] == requests_after_null
                else:
                    presentation_state["value"] = None
                    page.goto(setup_url)
                    page.get_by_role("button", name="Kick off preparation").click()
                    handoff = page.get_by_role("link", name="Open workspace")
                    page.get_by_role(
                        "heading", name="Preparation is no longer available"
                    ).wait_for()
                    expect(handoff).to_have_attribute("href", "/library?lens=all")
                    expect(page.get_by_text("Reconnecting", exact=False)).to_have_count(
                        0
                    )
                    requests_after_initial_null = preparation_requests["count"]
                    page.wait_for_timeout(1600)
                    assert preparation_requests["count"] == requests_after_initial_null
                    presentation_state["value"] = "dismissed"
                    page.goto(setup_url)
                    page.get_by_role("button", name="Kick off preparation").click()
                    handoff = page.get_by_role("link", name="Open workspace")
                    page.get_by_role(
                        "heading", name="Preparation was dismissed"
                    ).wait_for()
                    expect(
                        page.get_by_text("Open the ordinary workspace.")
                    ).to_be_visible()
                    expect(handoff).to_have_attribute("href", "/library?lens=all")
                    requests_after_legacy_dismissal = preparation_requests["count"]
                    page.wait_for_timeout(1600)
                    assert (
                        preparation_requests["count"] == requests_after_legacy_dismissal
                    )
                assert console_errors == []
                assert page_errors == []
                setup_client.close()
                context.close()
            browser.close()


def test_first_owner_claim_requires_the_deployment_claim_token() -> None:
    """ADR-0086 through the shipped page, on the real setup generation.

    The real composition mints the token on a temporary volume behind TLS
    uvicorn. A visitor without it is refused in place; the owner who reads it
    from the volume reaches the connection step.
    """
    with (
        tempfile.TemporaryDirectory() as directory,
        real_setup_server(Path(directory)) as (origin, token_file, _certificate),
    ):
        token = token_file.read_text(encoding="ascii").strip()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            with _closing_context(context):
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, errors))
                page.route(
                    "**/favicon.ico", lambda route: route.fulfill(status=204, body="")
                )
                # A phone with enlarged text, where the refusal falls below
                # the fold unless it is brought into view (#1291).
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(origin)
                set_root_text(page, "200%")
                expect(
                    page.get_by_role("heading", name="Create your owner password")
                ).to_be_visible()
                claim_token = page.get_by_label("Setup claim token", exact=True)
                page.get_by_label("Owner password", exact=True).fill(_PASSWORD)
                page.get_by_label("Confirm owner password", exact=True).fill(_PASSWORD)
                page.get_by_role("button", name="Create owner access").click()
                refusal = page.get_by_role("alert")
                expect(refusal).to_contain_text(
                    "That setup claim token was not accepted"
                )
                expect(claim_token).to_have_attribute("aria-invalid", "true")

                def assert_refusal_in_reach(height: int) -> None:
                    # The focused field is never off screen. When it and its
                    # refusal fit together both are in view; otherwise the
                    # field leads and the refusal begins below it.
                    page.evaluate("() => new Promise(requestAnimationFrame)")
                    field = claim_token.bounding_box()
                    box = refusal.bounding_box()
                    assert field is not None and box is not None
                    assert field["y"] >= 0, (height, field, box)
                    assert field["y"] + field["height"] <= height, (height, field)
                    assert box["y"] < height, (height, field, box)
                    # Scroll offsets snap to whole pixels, so allow one.
                    if box["y"] + box["height"] - field["y"] <= height:
                        assert box["y"] + box["height"] <= height + 1, (height, box)
                    expect(claim_token).to_be_focused()

                assert_refusal_in_reach(844)
                # Shorter and narrower phones, each refused on a fresh page; at
                # 150% the two fit together, at 200% the field leads.
                for width, height, size in (
                    (375, 667, "200%"),
                    (320, 640, "200%"),
                    (375, 667, "150%"),
                    (320, 640, "150%"),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    page.goto(origin)
                    set_root_text(page, size)
                    page.get_by_label("Owner password", exact=True).fill(_PASSWORD)
                    page.get_by_label("Confirm owner password", exact=True).fill(
                        _PASSWORD
                    )
                    page.get_by_role("button", name="Create owner access").click()
                    expect(refusal).to_contain_text(
                        "That setup claim token was not accepted"
                    )
                    assert_refusal_in_reach(height)
                assert token_file.exists()
                claim_token.fill(token)
                page.get_by_role("button", name="Create owner access").click()
                expect(
                    page.get_by_role("heading", name="Connect Audiobookshelf")
                ).to_be_visible()
                assert not token_file.exists()
                assert token not in page.content()
                assert errors == []
            browser.close()


def test_a_short_owner_password_is_refused_on_its_own_field() -> None:
    """#1384 through the shipped page, on the real setup generation.

    The twelve-character rule is stated with the password field and describes
    it. A short password is refused on that field before anything is sent, and
    a password the server itself refuses lands on the same field after a fresh
    nonce is read, so the corrected claim succeeds without a reload.
    """
    with (
        tempfile.TemporaryDirectory() as directory,
        real_setup_server(Path(directory)) as (origin, token_file, _certificate),
    ):
        token = token_file.read_text(encoding="ascii").strip()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            with _closing_context(context):
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, errors))
                page.route(
                    "**/favicon.ico", lambda route: route.fulfill(status=204, body="")
                )
                requests: list[str] = []
                page.on(
                    "request",
                    lambda request: requests.append(urlparse(request.url).path),
                )
                page.set_viewport_size({"width": 390, "height": 844})
                page.goto(origin)
                password = page.get_by_label("Owner password", exact=True)
                confirmation = page.get_by_label("Confirm owner password", exact=True)
                claim_token = page.get_by_label("Setup claim token", exact=True)
                expect(page.locator("#setup-password-rules")).to_contain_text(
                    "At least 12 characters. Any characters are allowed, including spaces."
                )
                expect(confirmation).to_have_attribute(
                    "aria-describedby", "setup-password-match"
                )
                expect(password).to_have_attribute(
                    "aria-describedby", "setup-password-rules"
                )
                follows = """selector => document.querySelector(selector)
                  .previousElementSibling
                  .contains(document.querySelector('input[name=password]'))"""
                # The rules follow the password field, not the claim token.
                assert page.evaluate(follows, "#setup-password-rules")
                claim_token.fill(token)
                for short in ("a" * 11, "é" * 11, "\U0001f600" * 11):
                    password.fill(short)
                    confirmation.fill(short)
                    page.get_by_role("button", name="Create owner access").click()
                    alert = page.get_by_role("alert")
                    expect(alert).to_have_text("Use at least 12 characters.")
                    expect(password).to_have_attribute("aria-invalid", "true")
                    expect(password).to_have_attribute(
                        "aria-describedby", "setup-error setup-password-rules"
                    )
                    page.evaluate("() => new Promise(requestAnimationFrame)")
                    expect(password).to_be_focused()
                    assert claim_token.get_attribute("aria-invalid") is None
                    assert page.evaluate(follows, "#setup-error")
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                # Over the byte ceiling is refused in place too, in its own words.
                password.fill("a" * 1_025)
                confirmation.fill("a" * 1_025)
                page.get_by_role("button", name="Create owner access").click()
                expect(page.get_by_role("alert")).to_have_text(
                    "That password is too long. Use a shorter one."
                )
                expect(password).to_have_attribute("aria-invalid", "true")
                assert "/api/setup/session" not in requests
                password.fill(_PASSWORD)
                expect(page.get_by_role("alert")).to_be_empty()
                confirmation.fill(_PASSWORD)

                # A refusal from the server itself spends the nonce: the page
                # reads a fresh one and refuses on the password field.
                def shorten(route) -> None:
                    body = json.loads(route.request.post_data or "{}")
                    body.update(password="a" * 11, confirmation="a" * 11)
                    route.continue_(post_data=json.dumps(body))

                page.route("**/api/setup/session", shorten)
                claims_before = requests.count("/api/setup/claim")
                page.get_by_role("button", name="Create owner access").click()
                expect(page.get_by_role("alert")).to_contain_text(
                    "Use at least 12 characters"
                )
                expect(password).to_have_attribute("aria-invalid", "true")
                assert claim_token.get_attribute("aria-invalid") is None
                assert requests.count("/api/setup/claim") == claims_before + 1
                assert page.evaluate(follows, "#setup-error")
                assert token_file.exists()
                page.unroute("**/api/setup/session", shorten)
                page.get_by_role("button", name="Create owner access").click()
                expect(
                    page.get_by_role("heading", name="Connect Audiobookshelf")
                ).to_be_visible()
                assert not token_file.exists()
                assert errors == []
            browser.close()


_FAKE_AUDIOBOOKSHELF_KEY = "synthetic-audiobookshelf-reader-key"


class _FakeAudiobookshelf(BaseHTTPRequestHandler):
    """Answers only the one bounded read setup makes, and only with the key."""

    def do_GET(self) -> None:
        if self.path != "/api/libraries":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {_FAKE_AUDIOBOOKSHELF_KEY}":
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = json.dumps(
            {
                "libraries": [
                    {
                        "id": "lib-reading",
                        "name": "Reading Shelf",
                        "mediaType": "book",
                        "folders": [{"fullPath": "/srv/reading"}],
                        "settings": {"audiobooksOnly": False},
                    },
                    {
                        "id": "lib-listening",
                        "name": "Listening Room",
                        "mediaType": "book",
                        "folders": [{"fullPath": "/srv/listening"}],
                        "settings": {"audiobooksOnly": True},
                    },
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@contextmanager
def _fake_audiobookshelf() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAudiobookshelf)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _claim_real_setup(page: Page, origin: str, token: str) -> None:
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page.goto(origin)
    page.get_by_label("Setup claim token", exact=True).fill(token)
    page.get_by_label("Owner password", exact=True).fill(_PASSWORD)
    page.get_by_label("Confirm owner password", exact=True).fill(_PASSWORD)
    page.get_by_role("button", name="Create owner access").click()
    expect(page.get_by_role("heading", name="Connect Audiobookshelf")).to_be_visible()


_LINK_AND_TEAL = """element => {
  const probe = document.createElement('span');
  probe.style.color = 'var(--teal)';
  document.body.append(probe);
  const teal = getComputedStyle(probe).color;
  probe.remove();
  return [getComputedStyle(element).color, teal];
}"""


def test_setup_connection_failures_name_the_address_and_remedy() -> None:
    """#1287 A1, A4, A6, A7 through the shipped page on the real setup generation.

    Each reach failure is produced for real (an unresolvable name, a closed
    port, this server's own untrusted certificate, a key the fake service
    refuses) except the timeout, which is routed so the test does not wait
    thirty seconds. Every message names only the origin, never the key.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        # Only each declared root is stat'ed; nothing inside is ever read.
        (root / "reading").mkdir()
        (root / "listening").mkdir()
        mounts = (
            BootstrapMount(
                name="reading",
                container_path=str(root / "reading"),
                service_path="/srv/reading",
                read_only=True,
            ),
            BootstrapMount(
                name="listening",
                container_path=str(root / "listening"),
                service_path="/srv/listening",
                read_only=True,
            ),
        )
        with (
            real_setup_server(root, mounts=mounts) as (origin, token_file, _cert),
            _fake_audiobookshelf() as service,
            closing(ReservedPort()) as closed_port,
            sync_playwright() as playwright,
        ):
            token = token_file.read_text(encoding="ascii").strip()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 375, "height": 812}
            )
            with _closing_context(context):
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, errors))
                _claim_real_setup(page, origin, token)
                endpoint = page.get_by_label("Audiobookshelf address", exact=True)
                key = page.get_by_label("Audiobookshelf API key", exact=True)
                submit = page.get_by_role("button", name="Verify and continue")
                closed = f"http://127.0.0.1:{closed_port.number}"
                cases = [
                    # More than scheme, host and port is refused before any
                    # probe, and never echoed (#1287).
                    (
                        service + "/abs-subpath",
                        _FAKE_AUDIOBOOKSHELF_KEY,
                        "Enter only the scheme, host and port Audiobookshelf serves on",
                        endpoint,
                    ),
                    (
                        service.replace("http://", "http://owner:synthetic-pw@"),
                        _FAKE_AUDIOBOOKSHELF_KEY,
                        "with no path, user name or password.",
                        endpoint,
                    ),
                    (
                        "https://audiobookshelf.invalid:13378",
                        _FAKE_AUDIOBOOKSHELF_KEY,
                        "This deployment could not find "
                        "https://audiobookshelf.invalid:13378: that name does not "
                        "resolve here.",
                        endpoint,
                    ),
                    (
                        closed,
                        _FAKE_AUDIOBOOKSHELF_KEY,
                        f"{closed} refused the connection.",
                        endpoint,
                    ),
                    (
                        origin,
                        _FAKE_AUDIOBOOKSHELF_KEY,
                        f"The certificate for {origin} could not be verified",
                        endpoint,
                    ),
                    (
                        service + "/",
                        "synthetic-wrong-reader-key",
                        f"Audiobookshelf at {service} did not accept that API key.",
                        key,
                    ),
                ]
                for address, secret, message, focused in cases:
                    endpoint.fill(address)
                    key.fill(secret)
                    submit.click()
                    alert = page.get_by_role("alert")
                    expect(alert).to_contain_text(message)
                    text = alert.inner_text()
                    assert secret not in text
                    assert "abs-subpath" not in text
                    assert "synthetic-pw" not in text
                    assert _PASSWORD not in text
                    expect(focused).to_be_focused()
                    expect(focused).to_have_attribute("aria-invalid", "true")
                    expect(alert).to_be_in_viewport()

                def time_out(route: Any) -> None:
                    route.fulfill(status=502, json={"detail": {"error": "timeout"}})

                page.route("**/api/setup/connect", time_out)
                endpoint.fill(service)
                key.fill(_FAKE_AUDIOBOOKSHELF_KEY)
                submit.click()
                expect(page.get_by_role("alert")).to_contain_text(
                    f"{service} did not answer in time."
                )
                expect(endpoint).to_be_focused()
                page.unroute("**/api/setup/connect", time_out)

                submit.click()
                expect(
                    page.get_by_role("heading", name="Choose metadata providers")
                ).to_be_visible()
                expect(page.locator(".confirmation")).to_have_text(
                    f"Connected to Audiobookshelf at {service}."
                )
                assert _FAKE_AUDIOBOOKSHELF_KEY not in page.content()
                books = page.get_by_role("group", name="Books and ebooks")
                audiobooks = page.get_by_role("group", name="Audiobooks")
                expect(books).to_have_accessible_description(
                    "Suggested for Reading Shelf"
                )
                expect(audiobooks).to_have_accessible_description(
                    "Suggested for Reading Shelf, Listening Room"
                )
                assert page.locator("fieldset legend").all_inner_texts() == [
                    "Books and ebooks",
                    "Audiobooks",
                ]
                # iTunes only corroborates ebooks, so it sits with them (#1458).
                expect(books.get_by_role("checkbox", name="iTunes")).to_be_visible()
                expect(audiobooks.get_by_role("checkbox", name="iTunes")).to_have_count(
                    0
                )
                assert page.locator("input[type=checkbox]:checked").count() == 0
                open_library = page.locator(".option", has_text="Open Library")
                expect(open_library).to_contain_text("No account or key.")
                expect(open_library.locator("a")).to_have_count(0)
                access = page.locator("a.provider-access")
                expect(access).to_have_count(2)
                for scheme in ("light", "dark"):
                    page.emulate_media(color_scheme=scheme)
                    for link in access.all():
                        colour, teal = link.evaluate(_LINK_AND_TEAL)
                        assert colour == teal, (scheme, colour, teal)
                page.emulate_media(color_scheme="light")
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                # Doubled text never pushes a provider card past a phone width.
                set_root_text(page, "200%")
                for width in (375, 320):
                    page.set_viewport_size({"width": width, "height": 812})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), width
                set_root_text(page, "")
                page.set_viewport_size({"width": 375, "height": 812})
                page.get_by_role("button", name="Skip providers").click()

                expect(
                    page.get_by_role("heading", name="Choose libraries")
                ).to_be_visible()
                expect(page.locator(".confirmation")).to_contain_text(
                    "No providers recorded."
                )
                for name in ("Reading Shelf", "Listening Room"):
                    expect(
                        page.get_by_role("button", name=f"Change name of {name}")
                    ).to_have_count(1)
                    page.get_by_role("checkbox", name=name).check()
                page.get_by_role("button", name="Change name of Listening Room").click()
                rename = page.get_by_label("New name for Listening Room", exact=True)
                expect(rename).to_be_focused()
                # A name another library carries is refused on that field.
                rename.fill("reading shelf")
                page.get_by_role("button", name="Continue").click()
                expect(page.get_by_role("alert")).to_have_text(
                    "That name is already used by another library. Choose a "
                    "different name for this one."
                )
                expect(rename).to_be_focused()
                expect(rename).to_have_attribute("aria-invalid", "true")
                rename.fill("Listening Room")
                page.get_by_role("button", name="Continue").click()

                expect(
                    page.get_by_role("heading", name="Check read-only mounts")
                ).to_be_visible()
                expect(page.locator(".confirmation")).to_have_text(
                    "Libraries recorded: Reading Shelf, Listening Room."
                )
                page.get_by_role("button", name="Check mounts").click()
                expect(
                    page.get_by_role(
                        "heading", name="Choose an optional cover reviewer"
                    )
                ).to_be_visible()
                confirmation = page.locator(".confirmation")
                expect(confirmation).to_contain_text(
                    "Reading Shelf: found /srv/reading on read-only mount reading"
                )
                expect(confirmation).to_contain_text(
                    "Listening Room: found /srv/listening on read-only mount listening"
                )
                assert "file" not in confirmation.inner_text()
                assert errors == []
            browser.close()


# The setup "ML" mark's contrast against the masthead it sits on, by its edge,
# and its letters' against its fill, from the colours the page renders.
_SETUP_MARK_CONTRAST = """() => {
  const paint = (css) => {
    const context = document.createElement('canvas').getContext('2d');
    context.fillStyle = css;
    context.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = context.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top, ground) => [0, 1, 2].map((i) => top[i] * top[3] + ground[i] * (1 - top[3])).concat(1);
  const luminance = (rgb) => [0.2126, 0.7152, 0.0722].reduce((sum, weight, i) => {
    const c = rgb[i] / 255;
    return sum + weight * (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  }, 0);
  const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };
  const background = (selector) => paint(getComputedStyle(document.querySelector(selector)).backgroundColor);
  const page = over(background('body'), [255, 255, 255, 1]);
  const masthead = over(background('.masthead'), page);
  const style = getComputedStyle(document.querySelector('.book-mark'));
  const fill = over(paint(style.backgroundColor), masthead);
  const edge = parseFloat(style.borderTopWidth) ? over(paint(style.borderTopColor), masthead) : fill;
  return [contrast(edge, masthead), contrast(over(paint(style.color), fill), fill)];
}"""
# Each setup step-list button shorter than a 2.75rem target.
_SHORT_STEP_BUTTONS = """() => {
  const floor = 2.75 * parseFloat(getComputedStyle(document.documentElement).fontSize);
  return [...document.querySelectorAll('.ledger-row button')]
    .filter((node) => node.getBoundingClientRect().height < floor - 0.5)
    .map((node) => `${node.getAttribute('aria-label')} is ${Math.round(node.getBoundingClientRect().height)}px`);
}"""
# Each word split across lines, other than at a hyphen, in the step list, the "ML" mark, a provider's
# checkbox label or its access link, and the mark's letters past its box.
_SPLIT_SETUP_WORDS = """() => {
  const found = [];
  for (const node of document.querySelectorAll(
      '.ledger-row > span, .book-mark, .option > label:first-child, .provider-access')) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/[^\\s\\u2010-\\u2014-]+/g)) {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        if (new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size > 1)
          found.push(`${match[0]} breaks mid-word`);
      }
  }
  const mark = document.querySelector('.book-mark');
  if (mark.scrollWidth > mark.clientWidth || mark.scrollHeight > mark.clientHeight)
    found.push('the mark spills its letters');
  return found;
}"""
# Each step-list button that reaches past either side of the step list.
_STEP_BUTTONS_PAST_ASIDE = """() => {
  const aside = document.querySelector('aside').getBoundingClientRect();
  return [...document.querySelectorAll('aside button')]
    .map((node) => [node, node.getBoundingClientRect()])
    .filter(([, box]) => box.left < aside.left - 0.5 || box.right > aside.right + 0.5)
    .map(([node]) => `${node.getAttribute('aria-label')} reaches past the step list`);
}"""
# Each step-list button that, once focused, lies outside the viewport.
_OFF_SCREEN_STEP_BUTTONS = """() => [...document.querySelectorAll('aside button')]
  .map((node) => { node.focus(); return [node, node.getBoundingClientRect()]; })
  .filter(([, box]) => box.top < 0 || box.bottom > innerHeight + 0.5)
  .map(([node, box]) => `${node.getAttribute('aria-label')} at ${Math.round(box.top)}px`)"""


def test_setup_mark_steps_and_provider_names_hold_in_dark_and_enlarged_text() -> None:
    """On the providers step, the "ML" mark stands 3:1 off the masthead and its
    letters 4.5:1 off the mark in dark mode, set or followed from the system
    (#1490); every step-list Change button is a 2.75rem target at every width
    and text size; with 200% text, in both themes and from 1440 to 320px, no
    word breaks in the step list, such as Audiobookshelf beside its Change
    button, the mark, a provider's name or its access link, such as Hardcover,
    the mark holds its letters and every Change button stays inside the step
    list (#1491, #1508); and at 200% text on a wide screen, root or browser
    font, the step list never holds a focused button off screen (#1491)."""
    with tempfile.TemporaryDirectory() as directory:
        with (
            real_setup_server(Path(directory)) as (origin, token_file, _cert),
            _fake_audiobookshelf() as service,
            sync_playwright() as playwright,
        ):
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1440, "height": 900}
            )
            with _closing_context(context):
                page = context.new_page()
                _claim_real_setup(page, origin, token_file.read_text().strip())
                page.get_by_label("Audiobookshelf address", exact=True).fill(service)
                page.get_by_label("Audiobookshelf API key", exact=True).fill(
                    _FAKE_AUDIOBOOKSHELF_KEY
                )
                page.get_by_role("button", name="Verify and continue").click()
                expect(
                    page.get_by_role("heading", name="Choose metadata providers")
                ).to_be_visible()
                expect(page.locator(".ledger-row button")).not_to_have_count(0)
                failures = []
                # Dark set on the page over a light system, then followed from it.
                themes: tuple[tuple[str | None, Literal["light", "dark"]], ...] = (
                    ("dark", "light"),
                    (None, "dark"),
                )
                for theme, system in themes:
                    page.emulate_media(color_scheme=system)
                    page.evaluate(
                        "theme => theme ? document.documentElement.dataset.theme = theme"
                        " : delete document.documentElement.dataset.theme",
                        theme,
                    )
                    mark, letters = page.evaluate(_SETUP_MARK_CONTRAST)
                    if mark < 3 or letters < 4.5:
                        failures.append(
                            f"dark ({'set' if theme else 'system'}): the mark is "
                            f"{mark:.2f}:1 off the masthead, its letters {letters:.2f}:1"
                        )
                schemes: tuple[tuple[Literal["light", "dark"], str], ...] = (
                    ("dark", ""),
                    ("dark", "200%"),
                    ("light", "200%"),
                )
                for scheme, text in schemes:
                    page.emulate_media(color_scheme=scheme)
                    set_root_text(page, text)
                    for width in (1440, 1200, 760, 390, 375, 320):
                        page.set_viewport_size({"width": width, "height": 812})
                        where = f"{scheme}, {text or '100%'} text at {width}px"
                        failures += [
                            f"{where}: {short}"
                            for short in page.evaluate(_SHORT_STEP_BUTTONS)
                        ]
                        if text:
                            failures += [
                                f"{where}: {split}"
                                for split in page.evaluate(_SPLIT_SETUP_WORDS)
                            ]
                            failures += [
                                f"{where}: {spill}"
                                for spill in page.evaluate(_STEP_BUTTONS_PAST_ASIDE)
                            ]
                        if text and width == 1440:
                            failures += [
                                f"{where}: {off}"
                                for off in page.evaluate(_OFF_SCREEN_STEP_BUTTONS)
                            ]
                set_root_text(page, "")
                # A real 32px browser font moves the page's rem breakpoints too,
                # so its two columns need a wider screen.
                session = context.new_cdp_session(page)
                session.send(
                    "Page.setFontSizes", {"fontSizes": {"standard": 32, "fixed": 26}}
                )
                page.set_viewport_size({"width": 1700, "height": 900})
                failures += [
                    f"32px browser font at 1700x900: {off}"
                    for off in page.evaluate(_OFF_SCREEN_STEP_BUTTONS)
                ]
                assert failures == [], "\n".join(failures)
            browser.close()


def test_setup_change_in_another_tab_is_named() -> None:
    """#1240 item 1: a token rotated by another tab reads as a change, not expiry."""
    with tempfile.TemporaryDirectory() as directory:
        with (
            real_setup_server(Path(directory)) as (origin, token_file, _cert),
            _fake_audiobookshelf() as service,
            sync_playwright() as playwright,
        ):
            token = token_file.read_text(encoding="ascii").strip()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            with _closing_context(context):
                first = context.new_page()
                errors: list[str] = []
                first.on("pageerror", partial(_record_page_error, errors))
                _claim_real_setup(first, origin, token)
                second = context.new_page()
                second.on("pageerror", partial(_record_page_error, errors))
                second.route(
                    "**/favicon.ico", lambda route: route.fulfill(status=204, body="")
                )
                second.goto(origin)
                expect(
                    second.get_by_role("heading", name="Connect Audiobookshelf")
                ).to_be_visible()
                first.get_by_label("Audiobookshelf address", exact=True).fill(service)
                first.get_by_label("Audiobookshelf API key", exact=True).fill(
                    _FAKE_AUDIOBOOKSHELF_KEY
                )
                first.get_by_role("button", name="Verify and continue").click()
                expect(first.get_by_role("alert")).to_have_text(
                    "Setup changed in another tab. The latest recorded step has "
                    "been restored."
                )
                assert "expired" not in first.get_by_role("alert").inner_text()
                # The restored form carries the fresh token, so a retry records.
                first.get_by_label("Audiobookshelf address", exact=True).fill(service)
                first.get_by_label("Audiobookshelf API key", exact=True).fill(
                    _FAKE_AUDIOBOOKSHELF_KEY
                )
                first.get_by_role("button", name="Verify and continue").click()
                expect(
                    first.get_by_role("heading", name="Choose metadata providers")
                ).to_be_visible()
                assert errors == []
            browser.close()


_GAP_BETWEEN = """element => {
  const [mark, label] = element.querySelectorAll('span');
  return label.getBoundingClientRect().left - mark.getBoundingClientRect().right;
}"""


def test_sign_in_failures_are_specific_and_recoverable() -> None:
    """#1287 A8: each refusal says what happened and leaves the owner ready to retry."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as reserved_port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=reserved_port,
            ) as (origin, _case_id),
        ):
            browser = playwright.chromium.launch(headless=True)
            for width in (1440, 375, 320):
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": width, "height": 800},
                    color_scheme="light",
                )
                with _closing_context(context):
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", partial(_record_page_error, errors))
                    page.route(
                        "**/favicon.ico",
                        lambda route: route.fulfill(status=204, body=""),
                    )
                    page.goto(f"{origin}/login")
                    heading = page.get_by_role("heading", level=1)
                    assert "Georgia" in heading.evaluate(
                        "element => getComputedStyle(element).fontFamily"
                    )
                    toggle = page.locator(".theme-toggle")
                    expect(toggle).to_have_text(re.compile(r"◐\s+Dark mode"))
                    assert toggle.evaluate(_GAP_BETWEEN) >= 4
                    password = page.get_by_label("Password")
                    status = page.locator("#login-status")
                    sign_in = page.get_by_role("button", name="Sign in")

                    sign_in.click()
                    expect(status).to_have_text("Enter the owner password.")
                    expect(password).to_be_focused()

                    password.fill("synthetic wrong owner password")
                    sign_in.click()
                    expect(status).to_have_text(
                        "That password was not accepted. Check it and try again."
                    )
                    expect(password).to_have_value("")
                    expect(password).to_be_focused()
                    expect(password).to_have_attribute("aria-invalid", "true")
                    expect(sign_in).to_be_enabled()
                    password.fill("x")
                    expect(password).not_to_have_attribute("aria-invalid", "true")

                    reveal = page.get_by_role("button", name="Show password")
                    expect(reveal).to_have_attribute("aria-controls", "password")
                    expect(reveal).to_have_attribute("aria-pressed", "false")
                    assert reveal.get_attribute("aria-label") is None
                    reveal.click()
                    expect(password).to_have_attribute("type", "text")
                    hide = page.get_by_role("button", name="Hide password")
                    expect(hide).to_have_attribute("aria-pressed", "true")
                    hide.click()
                    expect(password).to_have_attribute("type", "password")

                    def throttle(route: Any) -> None:
                        route.fulfill(status=429, json={"detail": "throttled"})

                    page.route("**/api/v1/session/login", throttle)
                    password.fill("synthetic another wrong password")
                    sign_in.click()
                    expect(status).to_have_text(
                        "Too many sign-in attempts from this device. Wait five "
                        "minutes, then try again."
                    )
                    page.unroute("**/api/v1/session/login", throttle)

                    page.get_by_text("Forgotten the owner password?").click()
                    guide = page.get_by_role(
                        "link", name="Read the recovery steps in the deployment guide"
                    )
                    expect(guide).to_be_visible()
                    expect(guide).to_have_attribute(
                        "href",
                        re.compile(
                            r"docs/operations/deployment\.md#forgotten-owner-password$"
                        ),
                    )
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    light_status = status.evaluate(
                        "element => getComputedStyle(element).color"
                    )
                    toggle.click()
                    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
                    expect(toggle).to_have_text(re.compile(r"◐\s+Light mode"))
                    assert light_status == "rgb(140, 52, 52)"
                    assert (
                        status.evaluate("element => getComputedStyle(element).color")
                        == "rgb(243, 155, 155)"
                    )
                    password.fill(_PASSWORD)
                    sign_in.click()
                    page.wait_for_url(f"{origin}/library")
                    assert errors == []
            browser.close()


def test_sign_in_returns_to_the_requested_page_and_root_opens_library() -> None:
    """#1371: a deep link survives sign-in, only to this origin, and / opens Library."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as reserved_port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=reserved_port,
            ) as (origin, case_id),
        ):
            browser = playwright.chromium.launch(headless=True)

            def sign_in(page: Page) -> None:
                page.get_by_label("Password").fill(_PASSWORD)
                page.get_by_role("button", name="Sign in").click()

            context = browser.new_context(ignore_https_errors=True)
            with _closing_context(context):
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, errors))
                page.route(
                    "**/favicon.ico", lambda route: route.fulfill(status=204, body="")
                )
                deep_link = f"/review/{case_id}"
                page.goto(origin + deep_link)
                page.wait_for_url(f"{origin}/login?next=%2Freview%2F{case_id}")
                sign_in(page)
                page.wait_for_url(origin + deep_link)
                expect(page.locator("#main-content")).to_have_attribute(
                    "data-restored-path", deep_link
                )

                opened = context.request.get(origin + "/", max_redirects=0)
                assert opened.status == 303
                assert opened.headers["location"] == "/library"
                page.goto(origin + "/")
                page.wait_for_url(re.compile(re.escape(origin) + r"/library(\?.*)?$"))
                assert errors == []

            for refused in (
                "//evil.invalid/library",
                "https://evil.invalid/library",
                "/\\evil.invalid/library",
                "javascript:alert(1)",
            ):
                context = browser.new_context(ignore_https_errors=True)
                with _closing_context(context):
                    page = context.new_page()
                    page.route(
                        "**/favicon.ico",
                        lambda route: route.fulfill(status=204, body=""),
                    )
                    page.goto(f"{origin}/login?next={quote(refused, safe='')}")
                    assert page.locator("#login").get_attribute("data-next") is None
                    # The page's own check holds even if a value got through.
                    page.locator("#login").evaluate(
                        "(form, next) => { form.dataset.next = next }", refused
                    )
                    sign_in(page)
                    page.wait_for_url(f"{origin}/library")


def test_sign_in_and_setup_paint_the_saved_or_system_theme_first() -> None:
    """#1287 A10: theme before any deferred script runs, and the workspace Inter.

    The page scripts are held back, so whatever is painted comes from the
    blocking theme.js and the stylesheet alone. Both the system preference
    with no saved choice and a saved choice against the system are checked.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as review_port,
            closing(ReservedPort()) as setup_port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=review_port,
            ) as (review_origin, _case_id),
            _setup_asset_server(
                certificate,
                key,
                setup_port,
                ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
            ) as setup_origin,
        ):
            browser = playwright.chromium.launch(headless=True)
            pages = (
                (f"{review_origin}/login", "/assets/login.js"),
                (f"{setup_origin}/", "/assets/setup.js"),
            )
            expectations: tuple[
                tuple[Literal["dark", "light"], str | None, str | None, str], ...
            ] = (
                ("dark", None, None, "rgb(15, 21, 18)"),
                ("light", None, None, "rgb(244, 242, 236)"),
                ("dark", "light", "light", "rgb(244, 242, 236)"),
                ("light", "dark", "dark", "rgb(15, 21, 18)"),
            )
            for url, script in pages:
                for system, saved, attribute, background in expectations:
                    context = browser.new_context(
                        ignore_https_errors=True, color_scheme=system
                    )
                    with _closing_context(context):
                        if saved is not None:
                            context.add_cookies(
                                [{"name": "ml_theme", "value": saved, "url": url}]
                            )
                        page = context.new_page()
                        console: list[str] = []
                        page.on(
                            "console",
                            partial(_record_transition_console, console, []),
                        )
                        page.route(
                            "**/favicon.ico",
                            lambda route: route.fulfill(status=204, body=""),
                        )
                        page.route(f"**{script}", lambda route: route.abort())
                        page.goto(url)
                        assert (
                            page.locator("html").get_attribute("data-theme")
                            == attribute
                        ), (url, system, saved)
                        assert (
                            page.evaluate(
                                "getComputedStyle(document.body).backgroundColor"
                            )
                            == background
                        ), (url, system, saved)
                        faces = page.evaluate(
                            """async () => {
                              await document.fonts.ready;
                              await document.fonts.load('400 1em Inter', 'Library');
                              return [...document.fonts]
                                .filter(face => face.family.replaceAll('"', '') === 'Inter')
                                .map(face => face.status);
                            }"""
                        )
                        assert "loaded" in faces, (url, faces)
                        loaded = page.evaluate(
                            "performance.getEntriesByType('resource').map(entry => new URL(entry.name).pathname)"
                        )
                        assert any(
                            re.fullmatch(
                                r"/assets/review-app/assets/inter-latin-wght-normal-[A-Za-z0-9_-]{8,}\.woff2",
                                path,
                            )
                            for path in loaded
                        ), loaded
                        assert not [
                            text
                            for text in console
                            if "Content Security Policy" in text
                        ], console
            browser.close()


def test_first_run_installed_semantics_responsiveness_and_handoff() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            stage = {"value": "claim"}
            compared = {"count": 0}
            nonces: list[str] = []
            claims: list[tuple[str | None, object]] = []

            def setup_api(route) -> None:
                path = urlparse(route.request.url).path
                method = route.request.method
                if path == "/api/setup/claim":
                    nonces.append(str(len(nonces) + 1) * 32)
                    route.fulfill(
                        json={"claimed": False, "revision": 1, "nonce": nonces[-1]}
                    )
                    return
                if path == "/api/setup/session":
                    body = json.loads(route.request.post_data or "{}")
                    claims.append(
                        (
                            route.request.headers.get("x-setup-nonce"),
                            body.get("claim_token"),
                        )
                    )
                    if body.get("claim_token") != _CLAIM_TOKEN:
                        route.fulfill(
                            status=403, json={"detail": {"error": "claim_token"}}
                        )
                        return
                    stage["value"] = "providers"
                    route.fulfill(json={"csrf_token": "c" * 32, "expires_at": 3_601})
                    return
                if path == "/api/setup/providers" and method == "POST":
                    stage["value"] = "libraries"
                    route.fulfill(
                        json={
                            "completed_steps": ["claim", "connect", "providers"],
                            "next_step": "libraries",
                            "revision": 4,
                        }
                    )
                    return
                if path == "/api/setup/libraries" and method == "GET":
                    route.fulfill(
                        json={
                            "libraries": [
                                {
                                    "library_id": "books",
                                    "name": "Books",
                                    "media_kind": "book",
                                    "service_path": "/library/books",
                                    "service_path_state": "available",
                                },
                                {
                                    "library_id": "long",
                                    "name": (
                                        "A very long library name " + "wrapped " * 12
                                    ).strip(),
                                    "media_kind": "book",
                                    "service_path": "/library/long",
                                    "service_path_state": "available",
                                },
                                {
                                    "library_id": "books-collision",
                                    "name": "Books!",
                                    "media_kind": "book",
                                    "service_path": "/library/books-collision",
                                    "service_path_state": "available",
                                },
                                {
                                    "library_id": "podcasts",
                                    "name": "Podcasts",
                                    "media_kind": "unsupported",
                                },
                            ]
                        }
                    )
                    return
                if path == "/api/setup/libraries" and method == "POST":
                    stage["value"] = "mounts"
                    route.fulfill(
                        json={
                            "completed_steps": [
                                "claim",
                                "connect",
                                "providers",
                                "libraries",
                            ],
                            "next_step": "mounts",
                            "revision": 5,
                        }
                    )
                    return
                if path == "/api/setup/mounts":
                    compared["count"] += 1
                    if compared["count"] == 1:
                        # UX-008: the first comparison finds no mapping, so the
                        # step stays open and must keep what the owner typed.
                        route.fulfill(
                            json={
                                "completed_steps": [
                                    "claim",
                                    "connect",
                                    "providers",
                                    "libraries",
                                ],
                                "next_step": "mounts",
                                "revision": 5,
                            }
                        )
                        return
                    stage["value"] = "reviewer"
                    route.fulfill(
                        json={
                            "completed_steps": [
                                "claim",
                                "connect",
                                "providers",
                                "libraries",
                                "mounts",
                            ],
                            "next_step": "reviewer",
                            "revision": 6,
                        }
                    )
                    return
                if path == "/api/setup/reviewer-access":
                    route.fulfill(
                        json=ReviewerAccessView(
                            revision=1,
                            mapping=None,
                            openrouter_key_configured=False,
                            codex=ReviewerEngineStatus(
                                engine="codex",
                                model="gpt-5.6-sol",
                                state="host_unsupported",
                                reason="headroom_below_1_gib",
                                host_headroom_bytes=536_870_912,
                            ),
                            openrouter=ReviewerEngineStatus(
                                engine="openrouter", state="unconfigured"
                            ),
                        ).model_dump(mode="json")
                    )
                    return
                if path == "/api/setup/reviewer" and method == "POST":
                    assert route.request.post_data_json == {
                        "expected_revision": 6,
                        "outcome": "skipped",
                    }
                    stage["value"] = "kick_off"
                    route.fulfill(
                        json={
                            "completed_steps": [
                                "claim",
                                "connect",
                                "providers",
                                "libraries",
                                "mounts",
                                "reviewer",
                            ],
                            "next_step": "kick_off",
                            "revision": 7,
                        }
                    )
                    return
                if path == "/api/setup/kick-off":
                    stage["value"] = "active"
                    route.fulfill(
                        status=202,
                        json={
                            "status": "active",
                            "message": "Nothing in your library changes. Writes stay off until you turn them on in Activity.",
                        },
                    )
                    return
                if path == "/api/setup/state":
                    value = stage["value"]
                    selected = value in {"mounts", "reviewer", "kick_off"}
                    route.fulfill(
                        json={
                            "csrf_token": "c" * 32,
                            "completed_steps": ["claim", "connect"]
                            if value == "providers"
                            else ["claim", "connect", "providers"]
                            if value == "libraries"
                            else ["claim", "connect", "providers", "libraries"]
                            if value == "mounts"
                            else [
                                "claim",
                                "connect",
                                "providers",
                                "libraries",
                                "mounts",
                            ]
                            + (["reviewer"] if value in {"kick_off", "active"} else []),
                            "next_step": value,
                            "revision": {
                                "providers": 3,
                                "libraries": 4,
                                "mounts": 5,
                                "reviewer": 6,
                                "kick_off": 7,
                            }.get(value, 7),
                            "connection": {
                                "name": "primary",
                                "endpoint": "https://library.invalid",
                            },
                            "providers": [],
                            "libraries": (
                                [
                                    {
                                        "name": "books",
                                        "library_id": "books",
                                        "media_kind": "book",
                                        "display_name": "My Reading Shelf",
                                        "provider_connections": [],
                                    },
                                    {
                                        "name": "long",
                                        "library_id": "long",
                                        "media_kind": "book",
                                        "display_name": (
                                            "A very long library name "
                                            + "wrapped " * 12
                                        ).strip(),
                                        "provider_connections": [],
                                    },
                                ]
                                if selected
                                else []
                            ),
                            "mounts": (
                                [
                                    {"library": "books", "state": "matched"},
                                    {"library": "long", "state": "matched"},
                                ]
                                if value in {"reviewer", "kick_off"}
                                else [
                                    {"library": "books", "state": "missing"},
                                    {"library": "long", "state": "missing"},
                                ]
                                if value == "mounts" and compared["count"]
                                else []
                            ),
                        }
                    )
                    return
                route.fulfill(status=404, json={"error": "not_found"})

            page.route("**/api/setup/**", setup_api)
            page.route(
                "**/api/v1/review-preparation",
                lambda route: route.fulfill(
                    json={
                        "libraries": [],
                        "active": True,
                        "paused": False,
                        "actionable": False,
                        "presentation": {
                            "run_id": "preparation_run_" + "a" * 64,
                            "state": "running",
                            "passage": {
                                "read": 9,
                                "searches_finished": 8,
                                "compared": 7,
                                "checked": 6,
                                "ready": 1,
                                "failed": 1,
                            },
                            "active_books": [
                                {
                                    "name": f"Private setup book {index}",
                                    "stage": "Comparing candidates",
                                }
                                for index in range(5)
                            ],
                            "active_overflow": 2,
                            "recent_completions": [
                                {
                                    "name": "Recently ready",
                                    "outcome": "ready",
                                    "completed_at": 99,
                                }
                            ],
                            "throughput_per_minute": 1.2,
                            "total": 10,
                        },
                    }
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _setup_asset_server(certificate, key, reserved_port) as origin,
                _closing_context(context),
            ):
                protected_viewports = (
                    (1440, 900),
                    (884, 900),
                    (390, 844),
                    (320, 800),
                )
                color_schemes: tuple[Literal["light", "dark"], ...] = (
                    "light",
                    "dark",
                )
                page.goto(origin)
                expect(
                    page.get_by_role("heading", name="Create your owner password")
                ).to_be_visible()
                for color in color_schemes:
                    page.emulate_media(color_scheme=color)
                    for width, height in protected_viewports:
                        page.set_viewport_size({"width": width, "height": height})
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                expect(
                    page.get_by_role("heading", name="Before you start")
                ).to_be_visible()
                expect(
                    page.get_by_text(
                        "Bare Compose without this prepared bootstrap is not supported",
                        exact=False,
                    )
                ).to_be_visible()
                expect(
                    page.get_by_text("At least 12 characters", exact=False)
                ).to_be_visible()
                # This listener terminates TLS itself, so the claim step states
                # the browser-trusted mode rather than the clear-text one.
                expect(
                    page.get_by_text(
                        "https that your browser already trusts", exact=False
                    )
                ).to_be_visible()
                password = page.get_by_label("Owner password", exact=True)
                reveal = page.get_by_role("button", name="Show the owner password")
                expect(reveal).to_have_attribute("aria-pressed", "false")
                password.fill(_PASSWORD)
                assert password.get_attribute("type") == "password"
                password.focus()
                page.keyboard.press("Tab")
                expect(reveal).to_be_focused()
                reveal.click()
                assert password.get_attribute("type") == "text"
                hide = page.get_by_role("button", name="Hide the owner password")
                expect(hide).to_have_attribute("aria-pressed", "true")
                hide.click()
                assert password.get_attribute("type") == "password"
                # UX-001: the refusal names the field it belongs to and clears.
                page.evaluate(_LATE_FRAMES_ON_FOCUS, "input[name=password]")
                page.get_by_role("button", name="Create owner access").click()
                alert = page.get_by_role("alert")
                expect(alert).to_be_visible()
                confirmation = page.get_by_label("Confirm owner password", exact=True)
                expect(confirmation).to_have_attribute("aria-invalid", "true")
                expect(confirmation).to_have_attribute(
                    "aria-describedby", "setup-error setup-password-match"
                )
                expect(confirmation).to_be_focused()
                assert alert.get_attribute("id") == "setup-error"
                for width, height in ((1440, 900), (320, 800)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                # A refusal that moves to another field releases the first one:
                # typing there must not blank a live alert that now belongs to
                # the password. The refused confirmation's settling frame, held
                # since that refusal, runs only once the password takes focus, as
                # a slow frame would, and must not take focus back from it.
                password.fill("")
                page.get_by_role("button", name="Create owner access").click()
                expect(password).to_have_attribute("aria-invalid", "true")
                assert confirmation.get_attribute("aria-invalid") is None
                # Error focus settles on the next frame; filling another field
                # before then races that focus transition and types in password.
                page.evaluate("() => new Promise(requestAnimationFrame)")
                expect(password).to_be_focused()
                confirmation.fill("a different password")
                expect(alert).not_to_be_empty()
                expect(password).to_have_attribute("aria-invalid", "true")
                expect(password).to_have_attribute(
                    "aria-describedby", "setup-error setup-password-rules"
                )
                expect(confirmation).to_have_attribute(
                    "aria-describedby", "setup-password-match"
                )
                password.fill(_PASSWORD)
                expect(alert).to_be_empty()
                assert password.get_attribute("aria-invalid") is None
                confirmation.fill(_PASSWORD)
                # ADR-0086: a mistyped token is refused in place, on its own
                # field, with a fresh nonce and every typed value kept.
                claim_token = page.get_by_label("Setup claim token", exact=True)
                expect(
                    page.get_by_text("from the container log", exact=False)
                ).to_be_visible()
                claim_token.fill("mistyped-token")
                page.get_by_role("button", name="Create owner access").click()
                expect(alert).to_contain_text("That setup claim token was not accepted")
                expect(claim_token).to_have_attribute("aria-invalid", "true")
                expect(claim_token).to_be_focused()
                assert password.input_value() == _PASSWORD
                assert confirmation.input_value() == _PASSWORD
                claim_token.fill(f"  {_CLAIM_TOKEN} ")
                page.get_by_role("button", name="Create owner access").click()
                expect(
                    page.get_by_role("heading", name="Choose metadata providers")
                ).to_be_visible()
                assert claims == [
                    (nonces[0], "mistyped-token"),
                    (nonces[1], _CLAIM_TOKEN),
                ]
                # Open Library needs only a contact email, so it has no
                # account link; the two keyed providers keep theirs (#1287).
                expect(
                    page.get_by_role("link", name="Get Open Library access")
                ).to_have_count(0)
                expect(
                    page.locator(".option", has_text="Open Library")
                ).to_contain_text("Needs only a contact email")
                for provider in ("Hardcover", "Google Books"):
                    access = page.get_by_role("link", name=f"Get {provider} access")
                    expect(access).to_be_visible()
                    expect(access).to_have_class(re.compile(r"\bprovider-access\b"))
                    assert (
                        access.evaluate(
                            "element => getComputedStyle(element).textDecorationLine"
                        )
                        == "underline"
                    )
                    assert (
                        access.evaluate("element => getComputedStyle(element).color")
                        != "rgb(0, 0, 238)"
                    )
                    page.get_by_role("checkbox", name=provider).focus()
                    page.keyboard.press("Tab")
                    expect(access).to_be_focused()
                    assert (
                        access.evaluate(
                            "element => getComputedStyle(element).outlineStyle"
                        )
                        != "none"
                    )
                # ADR-0061 admits the audiobook providers and iTunes keyless:
                # the step offers them with no credential control and no
                # account link to obtain one (#749).
                for provider in ("Audible", "Audnexus", "AudioSilo", "iTunes"):
                    expect(page.get_by_role("checkbox", name=provider)).to_be_visible()
                    assert (
                        page.get_by_role("link", name=f"Get {provider} access").count()
                        == 0
                    )
                assert page.evaluate(
                    """() => [...document.querySelectorAll('.option')]
                      .filter((row) => ['Audible', 'Audnexus', 'AudioSilo', 'iTunes']
                        .includes(row.querySelector('label').textContent.trim()))
                      .every((row) => !row.querySelector('input[type=password], input[type=email], a'))"""
                )
                for color in color_schemes:
                    page.emulate_media(color_scheme=color)
                    for width, height in protected_viewports:
                        page.set_viewport_size({"width": width, "height": height})
                        expect(
                            page.get_by_role("link", name="Get Hardcover access")
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                expect(
                    page.get_by_text(
                        "Book editions, ISBNs, publication dates and cover images.",
                        exact=False,
                    )
                ).to_be_visible()
                page.get_by_role("checkbox", name="Hardcover").check()
                expect(
                    page.get_by_label("Hardcover API key", exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_role("button", name="Show the Hardcover API key")
                ).to_be_visible()
                # With 200% text on the narrowest phone, a key and its Show
                # button share the row or wrap inside it, never past it.
                page.set_viewport_size({"width": 320, "height": 800})
                set_root_text(page, "200%")
                overflowing = page.evaluate(
                    """() => [...document.querySelectorAll('.reveal-row')].filter(row => {
                      const edge = row.getBoundingClientRect().right;
                      return row.scrollWidth > row.clientWidth + 1 || [...row.children]
                        .some(child => child.getBoundingClientRect().right > edge + 1);
                    }).length"""
                )
                assert overflowing == 0
                set_root_text(page, "")
                page.set_viewport_size({"width": 1440, "height": 900})
                expect(
                    page.get_by_text(
                        "Cover evidence from Audible stays off until you turn it on "
                        "in Settings.",
                        exact=False,
                    )
                ).to_be_visible()
                assert page.get_by_label("API key", exact=True).count() == 0
                page.get_by_role("button", name="Skip providers").click()
                expect(
                    page.get_by_text("1 unsupported podcast library excluded.")
                ).to_be_visible()
                page.get_by_role("checkbox", name="Books", exact=True).check()
                page.get_by_role("checkbox", name="Books!").check()
                page.get_by_role(
                    "checkbox", name="A very long library name", exact=False
                ).check()
                expect(
                    page.get_by_label("New name for Books", exact=True)
                ).to_be_hidden()
                page.get_by_role(
                    "button", name="Change name of Books", exact=True
                ).click()
                display_name = page.get_by_label("New name for Books", exact=True)
                expect(display_name).to_be_visible()
                expect(display_name).to_have_value("Books")
                display_name.fill("My Reading Shelf")
                selected_libraries: list[dict[str, object]] = []

                def record_library_selection(request) -> None:
                    if (
                        request.method == "POST"
                        and urlparse(request.url).path == "/api/setup/libraries"
                    ):
                        selected_libraries.extend(request.post_data_json["libraries"])

                page.on("request", record_library_selection)
                set_root_text(page, "200%")
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                set_root_text(page, "")
                page.get_by_role("button", name="Continue").click()
                expect(
                    page.get_by_role("heading", name="Check read-only mounts")
                ).to_be_visible()
                selected_by_id = {
                    item["library_id"]: item for item in selected_libraries
                }
                assert selected_by_id["books"]["name"] == "books"
                assert selected_by_id["books"]["display_name"] == "My Reading Shelf"
                assert selected_by_id["books-collision"]["name"] == "books-2"
                assert selected_by_id["books-collision"]["display_name"] == "Books!"
                # The mounts step and the kickoff summary must name a renamed
                # library the same way: its recorded local display name.
                expect(
                    page.get_by_label("My Reading Shelf service path")
                ).to_be_visible()
                assert page.get_by_label("Books service path").count() == 0
                expect(
                    page.get_by_label(
                        re.compile(r"^A very long library name.* service path$")
                    )
                ).to_be_visible()
                expect(
                    page.get_by_label(re.compile(r"^a-very-long-library-name"))
                ).to_have_count(0)
                for color in color_schemes:
                    page.emulate_media(color_scheme=color)
                    for width, height in protected_viewports:
                        page.set_viewport_size({"width": width, "height": height})
                        expect(
                            page.get_by_role("button", name="Check mounts")
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                page.emulate_media(color_scheme="dark")
                page.set_viewport_size({"width": 320, "height": 800})
                set_root_text(page, "200%")
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                # The stacking breakpoint is stated in rem, so 200% browser
                # text moves it and the step card keeps a usable width at
                # every protected viewport instead of collapsing beside the
                # aside.
                set_root_text(page, "200%")
                for width, height in protected_viewports:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    card = page.evaluate(
                        "document.querySelector('.step-card')"
                        ".getBoundingClientRect().width"
                    )
                    assert card >= width * 0.55, (width, card)
                page.set_viewport_size({"width": 1440, "height": 900})
                set_root_text(page, "")
                for field in page.get_by_label("service path", exact=False).all():
                    expect(field).not_to_have_value("")
                expect(
                    page.get_by_text("the path inside that service", exact=False)
                ).to_be_visible()
                books_path = page.get_by_label("My Reading Shelf service path")
                books_path.fill("/owner/typed/books")
                page.get_by_role("button", name="Check mounts").click()
                retest = page.get_by_role("button", name="Retest mounts")
                expect(retest).to_be_visible()
                expect(
                    page.get_by_label("My Reading Shelf service path")
                ).to_have_value("/owner/typed/books")
                results = page.get_by_role("status").first
                expect(results).to_contain_text("My Reading Shelf: No mapping")
                expect(results).to_contain_text(
                    "Correct the Audiobookshelf service path and compare again"
                )
                assert "missing" not in results.inner_text()
                assert (
                    results.locator("p.mount-line-attention").count()
                    == results.locator("p").count()
                )
                for width, height in ((1440, 900), (320, 800)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                retest.click()
                for label, model in (("GPT-6 Luna", "openai/gpt-6-luna"),):
                    page.get_by_role("button", name=f"Use {label} ({model})").click()
                    expect(page.get_by_label("Model ID", exact=True)).to_have_value(
                        model
                    )
                    expect(page.get_by_label("Model ID", exact=True)).to_be_focused()
                # Data use and limits are reference detail, read on demand (#1287).
                caveat = page.get_by_text(
                    "Cover requests deny provider data collection", exact=False
                )
                expect(caveat).to_be_hidden()
                page.get_by_text("OpenRouter limits, data use and key sharing").click()
                expect(
                    page.get_by_text(
                        "Cover requests deny provider data collection", exact=False
                    )
                ).to_be_visible()
                page.get_by_role("button", name="Continue without a reviewer").click()
                expect(
                    page.get_by_role("heading", name="Kick off preparation")
                ).to_be_visible()
                summary = page.locator(".summary")
                expect(summary).to_be_visible()
                expect(summary).to_contain_text("https://library.invalid")
                expect(summary).to_contain_text(
                    "My Reading Shelf \u2014 read-only mount matched"
                )
                expect(summary).to_contain_text("No reviewer")
                summary_text = summary.inner_text()
                assert "None" in summary_text
                assert "/owner/typed/books" not in summary_text
                assert "/library/books" not in summary_text
                # Every reopen control names the step it reopens.
                assert (
                    page.get_by_role("button", name="Change", exact=True).count() == 0
                )
                for action in (
                    "Change Connect Audiobookshelf",
                    "Change Choose metadata providers",
                    "Change Choose libraries",
                    "Change Check read-only mounts",
                    "Change Choose reviewer",
                ):
                    expect(page.get_by_role("button", name=action)).to_be_visible()
                # The summary value keeps a floor at 200% text, and the Change
                # control moves onto its own row instead of starving it.
                set_root_text(page, "200%")
                for width, height in protected_viewports:
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    widths = page.evaluate(
                        "[...document.querySelectorAll('.summary-row dd')]"
                        ".map(node => node.getBoundingClientRect().width)"
                    )
                    assert widths and all(value > 0 for value in widths), (
                        width,
                        widths,
                    )
                page.set_viewport_size({"width": 1440, "height": 900})
                set_root_text(page, "")
                for action in (
                    "Change the Audiobookshelf address",
                    "Change the metadata providers",
                    "Change the selected libraries",
                    "Change the cover reviewer",
                ):
                    expect(page.get_by_role("button", name=action)).to_be_visible()
                for color in color_schemes:
                    page.emulate_media(color_scheme=color)
                    for width, height in protected_viewports:
                        page.set_viewport_size({"width": width, "height": height})
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                page.set_viewport_size({"width": 1440, "height": 900})
                page.get_by_role("button", name="Kick off preparation").click()
                expect(page.locator(".summary")).to_have_count(0)
                expect(
                    page.get_by_role("heading", name="Now preparing")
                ).to_be_visible()
                # Nothing to prepare for once preparation has started.
                expect(
                    page.get_by_role("heading", name="Before you start")
                ).not_to_be_visible()
                expect(page.get_by_text("Private setup book 0")).to_be_visible()
                expect(
                    page.get_by_text("2 more active books follow in this run.")
                ).to_be_visible()
                expect(
                    page.get_by_role("heading", name="How many books made it this far?")
                ).to_be_visible()
                expect(page.get_by_text("1.2 books/min")).to_be_visible()
                expect(page.get_by_text("Recently ready · Prepared")).to_be_visible()
                for width, height in ((1440, 900), (390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                handoff = page.get_by_role("link", name="Open workspace")
                expect(handoff).to_be_visible()
                expect(handoff).to_have_class(re.compile(r"\bprimary\b"))
                assert "transform" in handoff.evaluate(
                    "element => getComputedStyle(element).transitionProperty"
                )
                page.emulate_media(reduced_motion="reduce")
                assert (
                    handoff.evaluate(
                        "element => getComputedStyle(element).transitionDuration"
                    )
                    == "0s"
                )
                expect(
                    page.get_by_text(
                        "Writes stay off until you turn them on in Activity."
                    )
                ).to_be_visible()
                assert not errors
            browser.close()


# The focused control and how much of it the floating saved notice covers.
_UNDER_SAVED_NOTICE = """() => {
  const notice = document.querySelector('.settings-saved');
  const focused = document.activeElement;
  if (!notice || !focused || notice.contains(focused)) return null;
  const a = notice.getBoundingClientRect();
  const b = focused.getBoundingClientRect();
  const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  if (x <= 0.5 || y <= 0.5) return null;
  const name = focused.getAttribute('aria-label') || focused.textContent.trim().slice(0, 40);
  return `${name} by ${Math.round(y)}px of ${Math.round(b.height)}px`;
}"""


# The language picker's label and the tray's "Review changes" when either
# clips its text, and the tray's share of the viewport while it floats.
_SETTINGS_LANGUAGE_FIT = """() => {
  const clipped = (node) => node && node.scrollWidth > node.clientWidth + 0.5;
  const tray = document.querySelector('.settings-tray');
  const toggle = document.querySelector('.settings-review-toggle');
  const floating = tray && getComputedStyle(tray).position === 'sticky';
  return {
    picker: clipped(document.querySelector('.settings-language-editor .cm-dropdown-trigger > span')),
    toggle: clipped(toggle),
    share: floating ? tray.getBoundingClientRect().height / innerHeight : 0,
  };
}"""


def test_settings_language_picker_and_tray_fit_a_phone_at_200_percent_text() -> None:
    """At 375 and 320px with 200% text the "Add a language" picker shows its
    whole label, "Review changes" stays inside its button, and the save tray
    floats only while it takes at most a third of the screen (#1503). Opening
    its change list on a phone, which settles it into the page, keeps the
    focused toggle and Save settings in view."""
    in_view = "node => { const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= innerHeight + 0.5; }"
    with _owner_page() as (page, origin, _case_id):
        failures = []
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{origin}/settings?group=libraries")
        page.get_by_role("combobox", name=re.compile("^Add a language to")).click()
        page.get_by_role("option").nth(1).click()
        # From the top of the page, where only the floating tray shows the toggle.
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator(".settings-review-toggle").focus()
        page.keyboard.press("Enter")
        expect(page.locator(".settings-change-review")).to_be_visible()
        page.evaluate(_TWO_FRAMES)
        for control in (
            page.locator(".settings-review-toggle"),
            page.get_by_role("button", name="Save settings"),
        ):
            if not control.evaluate(in_view):
                failures.append(f"open change list at 375x667: {control} is off screen")
        for width, height in ((375, 812), (320, 640)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto(f"{origin}/settings?group=libraries")
            picker = page.get_by_role("combobox", name=re.compile("^Add a language to"))
            picker.wait_for()
            set_root_text(page, "200%")
            where = f"200% text at {width}x{height}"
            if page.evaluate(_SETTINGS_LANGUAGE_FIT)["picker"]:
                failures.append(f"{where}: the picker's label is cut off")
            picker.click()
            page.get_by_role("option").nth(1).click()
            expect(page.locator(".settings-review-toggle")).to_be_visible()
            page.evaluate(_TWO_FRAMES)
            fit = page.evaluate(_SETTINGS_LANGUAGE_FIT)
            if fit["toggle"]:
                failures.append(f'{where}: "Review changes" overflows its button')
            if fit["share"] > 1 / 3:
                failures.append(
                    f"{where}: the floating tray takes {fit['share']:.0%} of the screen"
                )
            assert page.evaluate("document.documentElement.scrollWidth") <= width, where
        assert failures == [], "\n".join(failures)


def test_owner_settings_installed_semantics_responsiveness_and_bounds() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        settings = OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v4",
                "revision": 1,
                "connections": [
                    {
                        "name": "primary",
                        "endpoint": "https://library.invalid/" + "unbroken" * 120,
                    }
                ],
                "providers": [],
                "libraries": [
                    {
                        "name": (
                            "a-very-long-library-name-from-audiobookshelf"
                            if index == 0
                            else f"library-{index:03d}"
                        ),
                        "display_name": (
                            "Ebooks & Audiobooks: Collected Editions"
                            if index == 0
                            else f"Library {index:03d}"
                        ),
                        "connection": "primary",
                        "library_id": f"synthetic-{index:03d}",
                        "media_kind": "ebook",
                    }
                    for index in range(128)
                ],
                "pending_restart": [],
            }
        )
        errors: list[str] = []
        unavailable_responses: list[tuple[str, int]] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.add_init_script(
                """
                window.__settingsLongTasks = [];
                new PerformanceObserver((list) => {
                  window.__settingsLongTasks.push(...list.getEntries().map((entry) => entry.duration));
                }).observe({entryTypes: ['longtask']});
                """
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.on(
                "response",
                lambda response: (
                    unavailable_responses.append((response.url, response.status))
                    if response.url.endswith("/api/v1/settings")
                    and response.status == 503
                    else None
                ),
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    settings_document=settings,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                initial_settings = page.request.get(origin + "/api/v1/settings").json()
                held_initial_settings: list[Any] = []

                def hold_initial_settings(route) -> None:
                    if route.request.method == "GET":
                        held_initial_settings.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/settings", hold_initial_settings)
                page.goto(origin + "/settings?group=libraries")
                expect(
                    page.get_by_role("heading", name="Loading settings…")
                ).to_be_visible()
                for _ in range(100):
                    if held_initial_settings:
                        break
                    page.wait_for_timeout(10)
                assert len(held_initial_settings) == 1
                held_initial_settings[0].fulfill(status=200, json=initial_settings)
                expect(
                    page.get_by_role("heading", name="Libraries", exact=True)
                ).to_be_visible()
                page.unroute("**/api/v1/settings", hold_initial_settings)
                expect(page.get_by_text("Applies to the next operation")).to_have_count(
                    0
                )
                expect(
                    page.get_by_role(
                        "heading",
                        name="Ebooks & Audiobooks: Collected Editions",
                    )
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "heading",
                        name="a-very-long-library-name-from-audiobookshelf",
                    )
                ).to_have_count(0)
                library_metadata = page.locator(".settings-record-meta").first
                expect(library_metadata).to_contain_text(
                    "Local identifier: a-very-long-library-name-from-audiobookshelf"
                )
                expect(library_metadata).to_contain_text(
                    "Audiobookshelf identifier: synthetic-000"
                )
                expect(
                    page.get_by_text("Turn on a metadata source to use it here.").first
                ).to_be_visible()
                expect(
                    page.get_by_role("group", name=re.compile(r"^Sources for"))
                ).to_have_count(0)
                # Nothing is edited yet, so there is no idle tray to reserve.
                expect(page.locator(".settings-tray")).to_have_count(0)

                def choose_section(label: str) -> None:
                    page.get_by_role("combobox", name="Settings section").click()
                    page.get_by_role(
                        "option", name=re.compile("^" + re.escape(label))
                    ).click()

                assert page.locator("*").count() < 2_000

                def assert_settings_layout(
                    *, sidebar_visible: bool, zoomed: bool = False
                ) -> None:
                    layout = page.evaluate(
                        """() => {
                              const visible = (element) => {
                                const style = getComputedStyle(element);
                                const rect = element.getBoundingClientRect();
                                const closedDetails = element.closest('details:not([open])');
                                if (closedDetails && element !== closedDetails.querySelector(':scope > summary')) return false;
                                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                              };
                          const tray = document.querySelector('.settings-tray');
                          const saveRect = tray?.getBoundingClientRect();
                          const overlapElements = !saveRect ? [] : [...document.querySelectorAll('.settings-fields > *, .settings-field-row, .settings-record-meta')]
                            .filter(visible)
                            .filter((element) => {
                              const rect = element.getBoundingClientRect();
                              return Math.min(saveRect.right, rect.right) > Math.max(saveRect.left, rect.left)
                                && Math.min(saveRect.bottom, rect.bottom) > Math.max(saveRect.top, rect.top);
                            });
                          const active = document.querySelector('.settings-sidebar button[aria-current="page"]');
                          const title = active?.querySelector('strong')?.getBoundingClientRect();
                          const subtitle = active?.querySelector('small')?.getBoundingClientRect();
                          const heading = getComputedStyle(document.querySelector('.settings-heading h1'));
                          const labels = [...document.querySelectorAll('.settings-field-row > div:first-child')]
                            .filter(visible).map((element) => element.getBoundingClientRect().width);
                          const masthead = document.querySelector('.masthead')?.getBoundingClientRect();
                          const main = document.querySelector('#main-content')?.getBoundingClientRect();
                          const brandMarkElement = document.querySelector('.brand-mark');
                          const brandMark = brandMarkElement?.getBoundingClientRect();
                          const shellControls = [...document.querySelectorAll('.shell-status > *, .shell-actions')].map((node) => node.getBoundingClientRect());
                          return {
                            overlaps: overlapElements.length,
                            overlapDiagnostic: {
                              group: new URL(location.href).searchParams.get('group'),
                              viewport: {width: innerWidth, height: innerHeight},
                              theme: document.documentElement.dataset.theme ?? getComputedStyle(document.documentElement).colorScheme,
                              tray: saveRect?.toJSON(),
                              offenders: overlapElements.map((element) => ({
                                tag: element.tagName,
                                className: element.className,
                                text: element.textContent?.trim().slice(0, 120),
                                rect: element.getBoundingClientRect().toJSON(),
                              })),
                            },
                            sidebarVisible: active ? visible(active) : false,
                            sidebarSeparated: title && subtitle ? title.bottom < subtitle.top : false,
                            headingSize: Number.parseFloat(heading.fontSize),
                            headingLineHeight: Number.parseFloat(heading.lineHeight),
                            rootSize: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
                            minLabelWidth: labels.length ? Math.min(...labels) : null,
                            brandContained: masthead && brandMark
                              ? brandMark.left >= 0 && brandMark.right <= innerWidth && brandMark.top >= masthead.top && brandMark.bottom <= masthead.bottom
                              : false,
                            brandContentContained: brandMarkElement
                              ? brandMarkElement.scrollWidth <= brandMarkElement.clientWidth
                                && brandMarkElement.scrollHeight <= brandMarkElement.clientHeight
                              : false,
                            brandActionsOverlap: brandMark
                              ? shellControls.some((controls) => Math.min(brandMark.right, controls.right) > Math.max(brandMark.left, controls.left)
                                && Math.min(brandMark.bottom, controls.bottom) > Math.max(brandMark.top, controls.top))
                              : false,
                            compactHeaderShare: masthead ? masthead.height / innerHeight : null,
                            compactContentTopShare: main ? main.top / innerHeight : null,
                            undersizedHeaderTargets: [...document.querySelectorAll('.masthead a, .masthead button')]
                              .filter(visible)
                              .filter((element) => {
                                const rect = element.getBoundingClientRect();
                                return rect.width < 44 || rect.height < 44;
                              }).length,
                          };
                        }"""
                    )
                    if layout["overlaps"]:
                        raise AssertionError(layout["overlapDiagnostic"])
                    if zoomed:
                        assert layout["headingSize"] / layout["rootSize"] <= 2.5
                        assert (
                            1
                            <= layout["headingLineHeight"] / layout["headingSize"]
                            <= 1.25
                        )
                    else:
                        assert layout["headingSize"] <= 40
                        assert layout["headingLineHeight"] <= 45
                    if layout["minLabelWidth"] is not None:
                        assert layout["minLabelWidth"] >= 180
                    assert layout["brandContained"] is True
                    assert layout["brandContentContained"] is True
                    assert layout["brandActionsOverlap"] is False
                    if zoomed and page.viewport_size["width"] <= 390:
                        assert layout["compactHeaderShare"] <= 0.5
                        assert layout["compactContentTopShare"] <= 0.55
                        assert layout["undersizedHeaderTargets"] == 0
                    if sidebar_visible:
                        assert layout["sidebarVisible"] is True
                        assert layout["sidebarSeparated"] is True

                for theme in ("light", "dark"):
                    requested = "Use dark mode" if theme == "dark" else "Use light mode"
                    toggle = page.get_by_role("button", name=requested)
                    if toggle.count():
                        toggle.click()
                    page.locator(
                        ".settings-sidebar button", has_text="Libraries"
                    ).evaluate("element => element.click()")
                    expect(
                        page.get_by_role("heading", name="Libraries", exact=True)
                    ).to_be_visible()
                    for width, height in (
                        (1440, 900),
                        (884, 900),
                        (390, 844),
                        (320, 800),
                    ):
                        page.set_viewport_size({"width": width, "height": height})
                        expect(
                            page.get_by_role("heading", name="Libraries", exact=True)
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        assert_settings_layout(sidebar_visible=width > 760)
                        # Exactly one of the two navigations at every width,
                        # including the 391-760px band that once had neither.
                        compact = page.get_by_role("combobox", name="Settings section")
                        sidebar = page.get_by_role(
                            "navigation", name="Settings sections"
                        )
                        if width <= 760:
                            expect(compact).to_be_visible()
                            expect(sidebar).to_be_hidden()
                        else:
                            expect(sidebar).to_be_visible()
                            expect(compact).to_be_hidden()

                    for width in (600, 760, 761):
                        page.set_viewport_size({"width": width, "height": 900})
                        assert (
                            page.get_by_role(
                                "combobox", name="Settings section"
                            ).is_visible()
                            != page.get_by_role(
                                "navigation", name="Settings sections"
                            ).is_visible()
                        )

                    group_labels = {
                        "connection": "Audiobookshelf",
                        "libraries": "Libraries",
                        "providers": "Metadata sources",
                        "reviewer": "Reviewer",
                        "background": "Background work",
                        "access": "Access and diagnostics",
                        "appearance": "Appearance",
                    }
                    for width, height in ((884, 1000), (390, 844), (320, 800)):
                        page.set_viewport_size({"width": width, "height": height})
                        for label in group_labels.values():
                            if width <= 760:
                                choose_section(label)
                            else:
                                page.locator(
                                    ".settings-sidebar button", has_text=label
                                ).click()
                            expect(
                                page.get_by_role("heading", name=label, exact=True)
                            ).to_be_visible()
                            assert page.evaluate(
                                "document.documentElement.scrollWidth <= window.innerWidth"
                            )
                            assert_settings_layout(sidebar_visible=width > 760)

                page.set_viewport_size({"width": 884, "height": 1000})
                set_root_text(page, "200%")
                for label in group_labels.values():
                    page.locator(".settings-sidebar button", has_text=label).click()
                    expect(
                        page.get_by_role("heading", name=label, exact=True)
                    ).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    assert_settings_layout(sidebar_visible=True, zoomed=True)
                set_root_text(page, "")

                for theme in ("light", "dark"):
                    requested = "Use dark mode" if theme == "dark" else "Use light mode"
                    toggle = page.get_by_role("button", name=requested)
                    if toggle.count():
                        toggle.click()
                    for width, height in ((390, 844), (320, 800)):
                        page.set_viewport_size({"width": width, "height": height})
                        choose_section("Libraries")
                        set_root_text(page, "200%")
                        expect(
                            page.get_by_role("heading", name="Libraries", exact=True)
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        assert_settings_layout(sidebar_visible=False, zoomed=True)
                        expect(
                            page.get_by_role("combobox", name="Settings section")
                        ).to_be_visible()
                        set_root_text(page, "")

                page.set_viewport_size({"width": 320, "height": 800})
                choose_section("Metadata sources")
                expect(
                    page.get_by_role("heading", name="Add a metadata source")
                ).to_be_visible()
                for provider in (
                    "Audible",
                    "AudioSilo",
                    "Audnexus",
                    "Google Books",
                    "Hardcover",
                    "iTunes",
                    "Inventaire",
                    "Open Library",
                ):
                    expect(
                        page.get_by_role("button", name=f"Add {provider}")
                    ).to_be_visible()
                # One wrapped row per two or three capability buttons at 320px.
                # Eight buttons is four wrapped rows, and the guard is
                # still that the add-list wraps rather than stacking one
                # full-width button per row.
                assert (
                    page.locator(".settings-sheet").evaluate(
                        "element => element.getBoundingClientRect().height"
                    )
                    < 700
                )
                assert page.evaluate(
                    """() => [...document.querySelectorAll('.settings-empty h3, .settings-capabilities h3')]
                      .every((heading) => heading.getBoundingClientRect().bottom < heading.nextElementSibling.getBoundingClientRect().top)"""
                )
                page.get_by_role("button", name="Add Inventaire").click()

                def segment(group: str, choice: str):
                    return page.get_by_role("group", name=group).get_by_role(
                        "button", name=choice, exact=True
                    )

                expect(segment("Use Inventaire", "On")).to_have_attribute(
                    "aria-pressed", "true"
                )
                segment("Use Inventaire", "Off").click()
                expect(segment("Use Inventaire", "Off")).to_have_attribute(
                    "aria-pressed", "true"
                )
                segment("Use Inventaire", "On").click()
                expect(segment("Use Inventaire", "On")).to_have_attribute(
                    "aria-pressed", "true"
                )
                # The tray floats while there is more to read, and at the end of
                # the page it rests in its own reserved space covering no row.
                expect(page.locator(".settings-tray")).to_have_css("position", "sticky")
                page.evaluate(
                    "window.scrollTo(0, document.documentElement.scrollHeight)"
                )
                assert_settings_layout(sidebar_visible=False)
                # Save is the filled primary beside a plain Discard, in each theme.
                for _theme in range(2):
                    _assert_primary_fill(
                        page.get_by_role("button", name="Save settings"),
                        page.get_by_role("button", name="Discard all"),
                    )
                    shown = page.locator("html").get_attribute("data-theme")
                    page.locator(".theme-toggle").click()
                    expect(page.locator("html")).to_have_attribute(
                        "data-theme", "light" if shown == "dark" else "dark"
                    )
                heading_top = (
                    "() => document.querySelector('.settings-heading h1')"
                    ".getBoundingClientRect().top + scrollY"
                )
                before_save = page.evaluate(heading_top)
                page.get_by_role("button", name="Save settings").click()
                expect(page.get_by_text("Settings saved and in use.")).to_be_visible()
                # The notice floats; its arrival moves no heading.
                assert abs(page.evaluate(heading_top) - before_save) <= 1
                # Tabbing on after the save never leaves focus under the
                # floating notice, at either text size (WCAG 2.4.11). Text is
                # resized before the window, as a zoom does both at once.
                hidden = []
                for size, width, height in (
                    ("", 390, 844),
                    ("", 1440, 900),
                    ("200%", 390, 844),
                    ("200%", 1100, 700),
                ):
                    set_root_text(page, size)
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("scrollTo(0, 0); document.activeElement?.blur()")
                    for _ in range(120):
                        page.keyboard.press("Tab")
                        if page.evaluate("document.activeElement === document.body"):
                            break
                        under = page.evaluate(_UNDER_SAVED_NOTICE)
                        if under:
                            hidden.append(f"{size or '100%'} at {width}px: {under}")
                assert hidden == [], "\n".join(hidden)
                set_root_text(page, "")
                page.set_viewport_size({"width": 320, "height": 800})
                saved_settings = page.request.get(origin + "/api/v1/settings").json()[
                    "document"
                ]
                saved_inventaire = next(
                    item
                    for item in saved_settings["providers"]
                    if item["adapter"] == "inventaire"
                )
                assert saved_inventaire["enabled"] is True
                assert saved_inventaire["cover_enabled"] is True

                # #967: the two audiobook sources that state a cover asset of
                # their own carry an owner-visible switch; the two that restate
                # Audible's carry none, and the heading names the provider
                # while the instance name stays beside it.
                page.get_by_role("button", name="Add Audible").click()
                page.get_by_role("button", name="Add Audnexus").click()
                expect(
                    page.get_by_role("heading", name="Audible audible_catalog")
                ).to_be_visible()
                expect(
                    page.get_by_role("heading", name="Audnexus audnexus")
                ).to_be_visible()
                expect(
                    page.get_by_role("group", name="Audnexus cover evidence")
                ).to_have_count(0)
                audible_cover_on = segment("Audible cover evidence", "On")
                expect(segment("Use Audible", "Off")).to_have_attribute(
                    "aria-pressed", "true"
                )
                expect(audible_cover_on).to_be_disabled()
                segment("Use Audible", "On").click()
                expect(audible_cover_on).to_be_enabled()
                audible_cover_on.focus()
                page.keyboard.press("Space")
                expect(audible_cover_on).to_have_attribute("aria-pressed", "true")
                # Turning the provider off turns the evidence off with it and
                # puts the control out of reach again.
                segment("Use Audible", "Off").click()
                expect(segment("Audible cover evidence", "Off")).to_have_attribute(
                    "aria-pressed", "true"
                )
                expect(audible_cover_on).to_be_disabled()
                segment("Use Audible", "On").click()
                audible_cover_on.focus()
                page.keyboard.press("Enter")
                expect(audible_cover_on).to_have_attribute("aria-pressed", "true")
                page.get_by_role("button", name="Save settings").click()
                expect(page.get_by_text("Settings saved and in use.")).to_be_visible()
                saved_audible = next(
                    item
                    for item in page.request.get(origin + "/api/v1/settings").json()[
                        "document"
                    ]["providers"]
                    if item["adapter"] == "audible_catalog"
                )
                assert saved_audible["enabled"] is True
                assert saved_audible["cover_enabled"] is True
                # The saved value survives a fresh read of the document. The
                # field is a live one, so the reload shows what the runtime
                # already holds rather than a restart-pending value.
                page.reload()
                choose_section("Metadata sources")
                expect(segment("Audible cover evidence", "On")).to_have_attribute(
                    "aria-pressed", "true"
                )
                expect(page.get_by_text("Applies after restart")).to_have_count(0)
                assert not errors

                choose_section("Audiobookshelf")
                expect(page.get_by_text("Configured · ····cret")).to_be_visible()
                page.get_by_role(
                    "button", name="Clear Audiobookshelf access key"
                ).click()
                expect(
                    page.get_by_role(
                        "button", name="Confirm clear Audiobookshelf access key"
                    )
                ).to_be_visible()
                page.get_by_role(
                    "button", name="Keep Audiobookshelf access key"
                ).click()
                page.get_by_role(
                    "button", name="Replace Audiobookshelf access key"
                ).click()
                expect(
                    page.get_by_label("Replacement Audiobookshelf access key")
                ).to_be_visible()

                choose_section("Background work")
                timeout_input = page.get_by_role("spinbutton", name="Request timeout")
                timeout_input.fill("0")
                expect(page.get_by_role("alert")).to_contain_text(
                    "Request timeout must be"
                )
                expect(timeout_input).to_have_attribute("aria-invalid", "true")
                described_by = timeout_input.get_attribute("aria-describedby")
                assert described_by is not None
                assert "settings-limits-timeout_seconds-error" in described_by
                expect(
                    page.locator("#settings-limits-timeout_seconds-error")
                ).to_contain_text("Request timeout must be from 1 to 60")
                expect(timeout_input).to_be_focused()
                # Save takes the owner to the first invalid value, here from
                # another section, rather than sitting disabled.
                choose_section("Appearance")
                page.get_by_role("button", name="Save settings").click()
                expect(
                    page.get_by_role("heading", name="Background work", exact=True)
                ).to_be_visible()
                expect(timeout_input).to_be_focused()
                timeout_input.fill("31")
                page.get_by_role("button", name="Discard all").click()
                expect(page.locator(".settings-tray")).to_have_count(0)
                expect(timeout_input).to_have_value("30")

                choose_section("Reviewer")
                expect(
                    page.get_by_text(
                        "Reviewer actions take effect as soon as you use them."
                    )
                ).to_be_visible()
                reviewer_choice = page.get_by_role("combobox", name="Cover model")
                reviewer_metrics = reviewer_choice.evaluate(
                    "element => element.getBoundingClientRect().toJSON()"
                )
                assert reviewer_metrics["height"] >= 44
                reviewer_choice.click()
                expect(
                    page.get_by_role("option", name="GPT-6 Luna (openai/gpt-6-luna)")
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "option", name="dots-studio/dots-3-note-preview:free"
                    )
                ).to_be_visible()
                page.get_by_role("option", name="Custom model ID").click()
                expect(
                    page.get_by_role("textbox", name="Cover model ID")
                ).to_be_visible()
                expect(
                    page.get_by_text(
                        "Less than 1 GB of memory headroom is available. "
                        "Free memory or raise the container limit."
                    )
                ).to_be_visible()
                choose_section("Access and diagnostics")
                diagnostic_metrics = page.get_by_role(
                    "group", name="Diagnostic timings"
                ).evaluate(
                    """element => [...element.querySelectorAll('button')]
                      .map((button) => button.getBoundingClientRect().toJSON())"""
                )
                assert len(diagnostic_metrics) == 2
                assert all(item["height"] >= 44 for item in diagnostic_metrics)
                # Off and On share one width and one baseline.
                assert diagnostic_metrics[0]["width"] == diagnostic_metrics[1]["width"]
                assert diagnostic_metrics[0]["top"] == diagnostic_metrics[1]["top"]

                choose_section("Audiobookshelf")
                page.evaluate("window.__settingsLongTasks = []")
                feedback = page.evaluate(
                    """async () => {
                      const input = document.querySelector('#settings-connection-primary-endpoint');
                      const start = performance.now();
                      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                      setter.call(input, 'https://changed.invalid');
                      input.dispatchEvent(new Event('input', {bubbles: true}));
                      await new Promise(requestAnimationFrame);
                      return performance.now() - start;
                    }"""
                )
                assert feedback <= 100
                expect(page.get_by_text("1 unsaved change")).to_be_visible()
                held_save: list[Any] = []

                def hold_save(route) -> None:
                    if route.request.method == "PATCH":
                        held_save.append(route)
                    else:
                        route.continue_()

                page.route("**/api/v1/settings", hold_save)
                pending_feedback = page.evaluate(
                    """async () => {
                      const button = [...document.querySelectorAll('button')]
                        .find((item) => item.textContent === 'Save settings');
                      const start = performance.now();
                      button.click();
                      await new Promise(requestAnimationFrame);
                      return performance.now() - start;
                    }"""
                )
                assert pending_feedback <= 100
                expect(page.get_by_text("Saving settings…")).to_be_visible()
                assert len(held_save) == 1
                held_save[0].continue_()
                expect(page.get_by_text("Saving settings…")).to_have_count(0)
                expect(page.get_by_text("Settings saved and in use.")).to_be_visible()
                page.unroute("**/api/v1/settings", hold_save)
                assert max(page.evaluate("window.__settingsLongTasks"), default=0) <= 50

                endpoint = page.get_by_role("textbox", name="Audiobookshelf endpoint")
                endpoint.fill("https://failed.invalid")
                assert not errors
                page.route(
                    "**/api/v1/settings",
                    lambda route: (
                        route.fulfill(
                            status=503,
                            json={
                                "schema_version": "review-error-v1",
                                "error": {"code": "settings_unavailable"},
                            },
                        )
                        if route.request.method == "PATCH"
                        else route.continue_()
                    ),
                )
                page.get_by_role("button", name="Save settings").click()
                recovered_status = page.locator(".settings-stale-recovery").filter(
                    has_text="Current settings reloaded"
                )
                expect(recovered_status).to_be_visible()
                expect(recovered_status).to_be_focused()
                expect(page.get_by_role("button", name="Save settings")).to_have_count(
                    0
                )
                assert errors == [
                    "Failed to load resource: the server responded with a status of 503 "
                    "(Service Unavailable)"
                ]
                assert unavailable_responses == [(origin + "/api/v1/settings", 503)]
                errors.clear()
                unavailable_responses.clear()
                page.unroute("**/api/v1/settings")

                endpoint.fill("https://stale.invalid")
                page.route(
                    "**/api/v1/settings",
                    lambda route: (
                        route.fulfill(
                            status=409,
                            json={
                                "schema_version": "review-error-v1",
                                "error": {"code": "settings_revision_stale"},
                            },
                        )
                        if route.request.method == "PATCH"
                        else route.continue_()
                    ),
                )
                page.get_by_role("button", name="Save settings").click()
                stale_alert = page.get_by_text(
                    "Settings changed elsewhere, but the newer values could not be confirmed."
                )
                expect(stale_alert).to_be_visible()
                expect(stale_alert).to_be_focused()
                assert errors == [
                    "Failed to load resource: the server responded with a status of 409 "
                    "(Conflict)"
                ]
                errors.clear()
                page.unroute("**/api/v1/settings")

                current = page.request.get(origin + "/api/v1/settings").json()
                current["authority"] = "file"
                current["managed_path"] = "/data/config.json"
                for field in current["fields"]:
                    field["managed_by_file"] = True
                    field["managed_path"] = "/data/config.json"
                page.route(
                    "**/api/v1/settings",
                    lambda route: route.fulfill(status=200, json=current),
                )
                page.reload()
                expect(page.get_by_text("Managed by /data/config.json")).to_be_visible()
                assert page.locator(".settings-sheet input").count() == 0
                assert page.get_by_role("button", name="Save settings").count() == 0
                choose_section("Metadata sources")
                expect(
                    page.get_by_role("heading", name="Audible audible_catalog")
                ).to_be_visible()
                expect(
                    page.locator(".settings-field-row").filter(
                        has_text="Cover evidence"
                    )
                ).to_contain_text("On")
                page.unroute("**/api/v1/settings")
                assert not errors

                page.route(
                    "**/api/v1/settings",
                    lambda route: route.fulfill(
                        status=503,
                        json={
                            "schema_version": "review-error-v1",
                            "error": {"code": "settings_unavailable"},
                        },
                    ),
                )
                page.reload()
                expect(
                    page.get_by_role("heading", name="Settings are unavailable.")
                ).to_be_visible()
                expect(page.get_by_role("button", name="Try again")).to_be_visible()
                assert unavailable_responses == [(origin + "/api/v1/settings", 503)]
                assert len(errors) == 1
                assert errors[0] == (
                    "Failed to load resource: the server responded with a status of 503 "
                    "(Service Unavailable)"
                )
                errors.clear()
                page.unroute("**/api/v1/settings")
                page.get_by_role("button", name="Try again").click()
                expect(
                    page.get_by_role("heading", name="Settings", exact=True)
                ).to_be_visible()
                assert not errors
            browser.close()


@pytest.mark.parametrize(
    "mode", ("automatic", "ask", "conflict", "unavailable", "long")
)
def test_isbn_title_reaches_real_library_with_retained_sources(mode):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    isbn_title_fixture=mode,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", partial(_record_page_error, errors))
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&case={case_id}")
                page.get_by_role("button", name=re.compile("^Title ")).click()
                expect(
                    page.get_by_text("Example Title", exact=True).first
                ).to_be_visible()
                # The field's own reason is the one line under the question (#1519).
                expect(
                    page.get_by_text(
                        re.compile(
                            "^"
                            + re.escape(
                                "The sources disagree about the work or title; choose the title yourself"
                                if mode == "conflict"
                                else "ISBN and author evidence support this title"
                            )
                            + r"\.?$"
                        )
                    ).first
                ).to_be_visible()
                for width in (1280, 390, 320):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                assert not errors
            browser.close()


@pytest.mark.parametrize("component", ("subtitle", "series"))
@pytest.mark.parametrize("long_component", (False, True))
def test_retained_decoration_is_visible_through_real_library(component, long_component):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    retained_decoration=(component, False),
                    retained_component_text=(
                        "A Journey Through the Carefully Remembered Stories of the Northern Coast"
                        if long_component
                        else None
                    ),
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&case={case_id}")
                page.get_by_role("button", name=re.compile("^Plain Work")).first.click()
                page.get_by_role("button", name=re.compile("^Title ")).click()
                expect(page.get_by_text("Plain Work", exact=True).first).to_be_visible()
                page.get_by_role(
                    "button", name=re.compile("^" + component.capitalize() + " ")
                ).click()
                expected = (
                    "A Second Look" if component == "subtitle" else "The Sequence"
                )
                if long_component:
                    expected = "A Journey Through the Carefully Remembered Stories of the Northern Coast"
                expect(page.get_by_text(expected, exact=True).first).to_be_visible()
                proposed = page.get_by_role(
                    "radio", name="Choose proposed value", exact=False
                ).first
                evidence = page.get_by_role(
                    "button",
                    name=f"Evidence for proposed value: {expected}",
                    exact=True,
                )
                for width, height in ((1280, 800), (390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    for theme in ("dark", "light"):
                        page.get_by_role("button", name=f"Use {theme} mode").click()
                        evidence.click()
                        expect(evidence).to_have_attribute("aria-expanded", "true")
                        panel_id = evidence.get_attribute("aria-controls")
                        assert panel_id is not None
                        panel = page.locator("[id='" + panel_id + "']")
                        expect(panel).to_be_visible()
                        toggle_box = evidence.bounding_box()
                        panel_box = panel.bounding_box()
                        assert toggle_box is not None and panel_box is not None
                        assert toggle_box["y"] + toggle_box["height"] <= panel_box["y"]
                        expect(proposed).to_have_attribute("aria-checked", "false")
                        evidence.focus()
                        page.keyboard.press("Enter")
                        expect(evidence).to_have_attribute("aria-expanded", "false")
                        expect(panel).to_have_count(0)
                        expect(proposed).to_have_attribute("aria-checked", "false")
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as accepted:
                    proposed.click()
                assert accepted.value.status == 200
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field={component}"
                )
                expect(proposed).to_have_attribute("aria-checked", "true")
                page.reload()
                expect(page.get_by_text(expected, exact=True).first).to_be_visible()
                expect(proposed).to_have_attribute("aria-checked", "true")
                current = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).first
                current.focus()
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as kept:
                    page.keyboard.press("Enter")
                assert kept.value.status == 200
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field={component}"
                )
                expect(current).to_have_attribute("aria-checked", "true")
                page.reload()
                expect(current).to_have_attribute("aria-checked", "true")
            browser.close()


def test_insecure_http_decision_fallback_retries_and_reconciles() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright, closing(ReservedPort()) as port:
            browser = playwright.chromium.launch(
                args=[
                    "--host-resolver-rules=MAP review-transport.test 127.0.0.1",
                    "--no-proxy-server",
                ]
            )
            context = browser.new_context()
            page = context.new_page()
            with _server(
                database=root / "review.sqlite",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=port,
                transport_mode="insecure",
            ) as (origin, case_id):
                _login(page, origin)
                assert page.evaluate("isSecureContext") is False
                assert page.evaluate("typeof crypto.randomUUID") == "undefined"
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                commands: list[str] = []

                def first_failure(route):
                    commands.append(route.request.headers["idempotency-key"])
                    if len(commands) == 1:
                        route.fulfill(
                            status=503,
                            json={
                                "schema_version": "review-error-v1",
                                "error": {"code": "persistence"},
                            },
                        )
                    else:
                        route.continue_()

                page.route("**/decisions", first_failure)
                page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).click()
                retry = page.get_by_role("button", name="Try the same decision again")
                expect(retry).to_be_visible()
                expect(retry).to_be_focused()
                # #1584: the failure sits directly under the retry it describes.
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    placement = retry.evaluate("""retry => {
                      const message = retry.nextElementSibling;
                      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
                      return {
                        alert: message?.getAttribute('role') === 'alert',
                        gap: message.getBoundingClientRect().top - retry.getBoundingClientRect().bottom,
                        rem,
                        scroll: document.documentElement.scrollWidth,
                      };
                    }""")
                    assert placement["alert"], (width, placement)
                    # Clear of the focused retry's ring, and still directly under it.
                    assert 4 <= placement["gap"] <= placement["rem"], (width, placement)
                    assert placement["scroll"] <= width, (width, placement)
                page.set_viewport_size({"width": 1280, "height": 720})
                retry.click()
                page.get_by_role(
                    "heading", name="Which series should this work use?"
                ).wait_for()
                assert len(commands) == 2 and commands[0] == commands[1]
                assert commands[0].startswith("decision-")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                expect(
                    page.get_by_role("radio", name="Choose current value:", exact=False)
                ).to_have_attribute("aria-checked", "true")
                page.unroute("**/decisions")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                page.route("**/decisions", lambda route: route.abort())
                page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).click()
                expect(
                    page.get_by_text(
                        "The response was lost. The current case was reloaded before any retry."
                    )
                ).to_be_visible()
                page.unroute("**/decisions")
                page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                ).click()
                expect(
                    page.get_by_text(
                        "The response was lost. The current case was reloaded before any retry."
                    )
                ).to_have_count(0)
                context.close()
            browser.close()


def test_provider_recovery_rail_explains_exact_automatic_withholding():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        authorities: list[object] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    provider_recovery=True,
                    cohort=30,
                    on_app=lambda app: authorities.append(
                        app.state.review_application._state_owner.authority
                    ),
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                from urllib.parse import parse_qs, urlsplit

                # Select from a genuinely fetched later Attention page. The work
                # is absent from both initial identity sources, so reload cannot
                # pass by retaining the original infinite-query cache.
                library_titles = page.locator(".library-works .library-work-title")
                expect(library_titles.first).to_be_visible()
                first_library_titles = library_titles.all_text_contents()
                assert first_library_titles
                page.get_by_role("button", name=re.compile(r"^Attention ")).click()
                queue = page.get_by_label("Attention queue", exact=True)
                queued = queue.locator(".attention-queue-item")
                expect(queued.first).to_be_visible()
                initial_count = queued.count()
                initial_titles = queue.locator(
                    ".attention-work-identity strong"
                ).all_text_contents()
                page.get_by_role(
                    "button", name="Load more attention", exact=True
                ).click()
                later = queued.nth(initial_count)
                expect(later).to_be_visible()
                # A field row's name ends with its work's title (#1284, #1361).
                later_title = later.evaluate(
                    "row => "
                    "row.getAttribute('aria-label').slice(row.querySelector('b').textContent.length + 2)"
                )
                assert later_title not in initial_titles
                assert later_title not in first_library_titles
                later.press("Enter")
                expect(
                    page.get_by_role("heading", name=later_title, exact=True)
                ).to_be_visible()
                produced = parse_qs(urlsplit(page.url).query)
                assert produced["attentionCursor"]
                selected_field = produced["field"]
                page.get_by_role("link", name="Evidence", exact=True).click()
                page.get_by_role("link", name="History", exact=True).click()
                page.reload()
                page.get_by_role("link", name="Back to work", exact=True).click()
                expect(
                    page.get_by_role("heading", name=later_title, exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_role("radio", name="Choose current value", exact=False)
                ).to_be_visible()
                returned = parse_qs(urlsplit(page.url).query)
                assert returned["attentionCursor"] == produced["attentionCursor"]
                assert returned["field"] == selected_field
                page.goto(f"{origin}/library?lens=all")
                page.get_by_role("searchbox").fill("Current title")
                expect(
                    page.get_by_role("heading", name="Provider evidence limited")
                ).to_be_visible()
                row = page.get_by_role("button", name=re.compile(r"^Current title\."))
                expect(row).to_have_attribute(
                    "aria-describedby", re.compile("provider-summary")
                )
                row.click()
                page.get_by_role("button", name=re.compile("^Series ")).click()
                reason = "An ISBN lookup did not return a usable answer."
                expect(page.get_by_text(reason)).to_be_visible()
                expect(
                    page.get_by_role(
                        "radio", name="Choose proposed value", exact=False
                    ).first
                ).to_be_enabled()
                for width in (1280, 390):
                    page.set_viewport_size({"width": width, "height": 844})
                    expect(page.get_by_text(reason)).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                from urllib.parse import parse_qs, urlsplit

                for departure in ("Evidence", "History"):
                    page.get_by_role("link", name=departure, exact=True).click()
                    expect(
                        page.get_by_role("heading", name=departure, exact=True)
                    ).to_be_visible()
                    other = "History" if departure == "Evidence" else "Evidence"
                    page.get_by_role("link", name=other, exact=True).click()
                    page.get_by_role("link", name="Back to work", exact=True).click()
                    expect(
                        page.get_by_role("heading", name="Current title", exact=True)
                    ).to_be_visible()
                    expect(page.get_by_text(reason)).to_be_visible()
                    assert parse_qs(urlsplit(page.url).query)["q"] == ["Current title"]
                    assert parse_qs(urlsplit(page.url).query)["field"] == ["series"]

                page.set_viewport_size({"width": 1280, "height": 844})
                page.get_by_role("button", name="Clear search text", exact=True).click()
                expect(row).to_have_count(0)
                page.get_by_role("button", name="Next →", exact=True).click()
                row.click()
                page.get_by_role("button", name=re.compile("^Series ")).click()
                expect(page.get_by_text(reason)).to_be_visible()
                cursor = parse_qs(urlsplit(page.url).query)["cursor"]
                page.get_by_role("link", name="Evidence", exact=True).click()
                page.get_by_role("link", name="History", exact=True).click()
                page.get_by_role("link", name="Back to work", exact=True).click()
                expect(
                    page.get_by_role("heading", name="Current title", exact=True)
                ).to_be_visible()
                expect(page.get_by_text(reason)).to_be_visible()
                assert parse_qs(urlsplit(page.url).query)["cursor"] == cursor
                page.reload()
                expect(page.get_by_text(reason)).to_be_visible()
                page.goto(f"{origin}/review/{case_id}?subview=evidence")
                expect(page.get_by_text(reason)).to_be_visible()
                expect(
                    page.get_by_role("radio", name="Choose proposed value", exact=False)
                ).to_have_count(0)
                from tests.support.provider_recovery_rail import provider_failure_report

                with closing(
                    open_repository(root / "review.sqlite", authority=authorities[0])
                ) as writer:
                    original_report = provider_failure_report()
                    recovered = provider_failure_report(contributing=False)
                    policy = writer.current_resolved_metadata_policy(
                        recovered.target.library
                    )
                    assert policy is not None
                    writer.publish(
                        recovered,
                        lambda: True,
                        expected_current=original_report.report_id,
                    )
                    successor = evaluate_report(
                        recovered, policy.resolved_id, dict(policy.destinations)
                    )
                    writer.publish_proposal(successor)
                    successor_id = json.loads(writer.admit_review_case(successor))[
                        "review_case_id"
                    ]
                    writer.persist_shadow_outcome(successor_id, 4)
                page.reload()
                expect(page.get_by_text(reason)).to_have_count(0)
                page.goto(f"{origin}/review/{successor_id}?subview=evidence")
                expect(
                    page.get_by_role("heading", name="Evidence", exact=True)
                ).to_be_visible()
                expect(page.get_by_text(reason)).to_have_count(0)
            browser.close()


@pytest.mark.parametrize(
    "state", ("ready", "needs_review", "failed", "automatic", "no_change")
)
def test_description_advice_displays_real_persisted_outcomes(state):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": 1280, "height": 800},
                reduced_motion="reduce",
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    description_fixture=state,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field=description"
                )
                chooser = page.locator("section.direct-choice")
                expect(chooser).to_be_visible()
                expect(
                    page.get_by_role("article", name="Advised description")
                ).to_have_count(0)
                current = chooser.get_by_role(
                    "radio", name="Choose current value: Existing description."
                )
                provider = chooser.get_by_role(
                    "radio", name="Choose proposed value: A qualified description."
                )
                expect(current).to_be_enabled()
                expect(provider).to_be_enabled()
                select = chooser.get_by_role(
                    "radio", name=re.compile("^Choose advised value:")
                )
                if state == "failed":
                    expect(chooser.get_by_role("status")).to_contain_text(
                        "The adviser returned an invalid response. No new description was produced."
                    )
                    expect(select).to_have_count(0)
                else:
                    expect(select).to_be_visible()
                    expect(select).to_contain_text("Advised · Authored content")
                    expect(select).to_contain_text("codex · synthetic-contained-model")
                    if state in {"automatic", "no_change"}:
                        expect(select).to_have_attribute("aria-checked", "true")
                        expect(current).to_have_attribute("aria-checked", "false")
                        expect(select).to_contain_text(
                            "Applied and verified"
                            if state == "automatic"
                            else "No change needed"
                        )
                        if state == "automatic":
                            expect(select).to_be_disabled()
                    else:
                        expect(select).to_be_enabled()
                        expect(select).to_contain_text(
                            "<strong>Authored text stays text.</strong>"
                        )
                        expect(select.locator("strong strong")).to_have_count(0)
                        if state == "needs_review":
                            expect(select).to_contain_text("cannot apply automatically")
                        selection_requests = []
                        page.on(
                            "request",
                            lambda request: (
                                selection_requests.append(request.post_data_json)
                                if request.method == "POST"
                                and request.url.endswith(
                                    "/description-advice/selection"
                                )
                                else None
                            ),
                        )
                        select.focus()
                        with page.expect_response(
                            lambda response: (
                                response.request.method == "POST"
                                and response.url.endswith(
                                    "/description-advice/selection"
                                )
                            )
                        ) as selection_response:
                            select.press("Enter")
                        assert selection_response.value.ok
                        page.wait_for_url(
                            lambda url: (
                                "field=" in url and "field=description" not in url
                            )
                        )
                        page.get_by_role(
                            "button", name=re.compile("^Description ")
                        ).click()
                        expect(select).to_have_attribute("aria-checked", "true")
                        assert len(selection_requests) == 1
                        assert set(selection_requests[0]) == {"candidate_id", "binding"}
                        assert (
                            selection_requests[0]["binding"]["review_case_id"]
                            == case_id
                        )
                presentations: tuple[tuple[int, Literal["light", "dark"]], ...] = (
                    (1280, "light"),
                    (390, "dark"),
                )
                for width, theme in presentations:
                    page.set_viewport_size({"width": width, "height": 800})
                    page.emulate_media(
                        color_scheme="dark" if theme == "dark" else "light"
                    )
                    expect(chooser).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                chooser.get_by_role("button", name="Retry description advice").click()
                expect(
                    chooser.get_by_role("status").filter(
                        has_text="Description preparation requested"
                    )
                ).to_be_visible()
                page.get_by_role("link", name="History", exact=True).click()
                expect(
                    page.get_by_role("heading", name="History", exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "radio", name=re.compile("Choose advised|Retry description")
                    )
                ).to_have_count(0)
                expect(
                    page.get_by_role(
                        "heading", name="Advised description · Authored content"
                    ).first
                ).to_be_visible()
                if state == "automatic":
                    expect(
                        page.get_by_text(
                            "Applied and verified in the library.", exact=False
                        )
                    ).to_be_visible()
                if state == "no_change":
                    expect(
                        page.get_by_text(
                            "No change needed; the library already had this text.",
                            exact=False,
                        )
                    ).to_be_visible()
                page.get_by_role("link", name="Evidence", exact=True).click()
                expect(
                    page.get_by_role("heading", name="Evidence", exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "radio", name=re.compile("Choose advised|Retry description")
                    )
                ).to_have_count(0)
                expect(
                    page.get_by_text(
                        "<strong>Authored text stays text.</strong>", exact=False
                    )
                ).to_have_count(0)
            browser.close()


@pytest.mark.parametrize(
    "state", ("no_alternatives", "missing_prices", "unavailable_model")
)
def test_description_unavailable_inputs_and_explicit_metadata_recovery(state):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 390, "height": 844}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    description_fixture=state,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field=description"
                )
                if state == "no_alternatives":
                    retained = page.get_by_role(
                        "region", name="Description recorded outcome"
                    )
                    expect(retained).to_contain_text("Resolved automatically")
                    expect(retained).to_contain_text("Existing description.")
                else:
                    expect(
                        page.get_by_role(
                            "radio", name="Choose current value: Existing description."
                        )
                    ).to_be_enabled()
                expect(
                    page.get_by_role("radio", name=re.compile("^Choose advised value:"))
                ).to_have_count(0)
                if state == "no_alternatives":
                    expect(
                        page.get_by_role(
                            "radio", name=re.compile("^Choose proposed value:")
                        )
                    ).to_have_count(0)
                    expect(
                        page.get_by_role("button", name="Retry description advice")
                    ).to_have_count(0)
                else:
                    expect(
                        page.get_by_role(
                            "radio",
                            name="Choose proposed value: A qualified description.",
                        )
                    ).to_be_enabled()
                page.goto(f"{origin}/settings?group=reviewer")
                adviser = page.get_by_role(
                    "region", name="Optional description adviser", exact=True
                )
                if state == "no_alternatives":
                    expect(adviser).to_contain_text("contained Codex")
                else:
                    expect(adviser).to_contain_text(
                        "Advisory availability: model unavailable."
                    )
                    expect(adviser).to_contain_text(
                        "Price and text capability metadata must be available"
                    )
                    with page.expect_response(
                        lambda response: urlparse(response.url).path.endswith(
                            "/api/v1/reviewer-access/openrouter/refresh"
                        )
                    ) as refreshed:
                        adviser.get_by_role(
                            "button", name="Refresh description model status and prices"
                        ).click()
                    assert refreshed.value.status == 200
                    expect(adviser).to_contain_text(
                        "Selected: synthetic/description-b. Advisory availability: ready to use."
                    )
                    prices = adviser.locator("dt").filter(
                        has_text=re.compile(r"^Prices in USD$")
                    )
                    expect(prices).to_have_count(1)
                    price_value = prices.locator("..").locator("dd")
                    expect(price_value).to_have_count(1)
                    expect(price_value).to_have_text(
                        "0.000001 per input token, 0.000002 per output token, 0 per request."
                    )
                    expect(
                        page.get_by_role("textbox", name="Cover model ID", exact=True)
                    ).to_have_value("synthetic/cover-a")
                    with page.expect_response(
                        lambda response: response.url.endswith(
                            "/api/v1/reviewer-access/selection"
                        )
                    ) as removed:
                        adviser.get_by_role(
                            "button", name="Continue without description advice"
                        ).click()
                    assert removed.value.status == 200
                    expect(adviser).to_contain_text("No description adviser selected.")
                    expect(
                        page.get_by_role("textbox", name="Cover model ID", exact=True)
                    ).to_have_value("synthetic/cover-a")
            browser.close()


@pytest.mark.parametrize(
    "state", ("retry_requested", "retry_running", "retry_failed", "retry_succeeded")
)
def test_description_retry_retains_real_selected_choice(state):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    description_fixture=state,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                url = f"{origin}/library?lens=all&preview={case_id}&field=description"
                page.goto(url)
                for width, theme in ((1280, "light"), (390, "dark")):
                    page.set_viewport_size({"width": width, "height": 844})
                    page.emulate_media(
                        color_scheme="dark" if theme == "dark" else "light"
                    )
                    choices = page.get_by_role(
                        "radio", name=re.compile("^Choose advised value:")
                    )
                    expect(choices).to_have_count(
                        2 if state == "retry_succeeded" else 1
                    )
                    expect(choices.first).to_have_attribute("aria-checked", "true")
                    expect(choices.first).to_be_disabled()
                    expect(
                        page.get_by_text("Choice recorded.", exact=True)
                    ).to_be_visible()
                    # The recorded choice also says so in words (#1290).
                    expect(
                        page.get_by_text(
                            re.compile(
                                "^Advised · Authored content · Recorded choice"
                                " ?Your choice$"
                            )
                        )
                    ).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    if state in {"retry_requested", "retry_running"}:
                        expect(
                            page.get_by_text(re.compile("Preparing description advice"))
                        ).to_be_visible()
                    elif state == "retry_failed":
                        expect(
                            page.get_by_text(
                                re.compile("Description adviser unavailable")
                            )
                        ).to_be_visible()
                    for view in ("Evidence", "History"):
                        page.get_by_role("link", name=view, exact=True).click()
                        expect(
                            page.get_by_role("heading", name=view, exact=True)
                        ).to_be_visible()
                        page.get_by_role(
                            "link", name="Back to work", exact=True
                        ).click()
                        expect(
                            page.get_by_text("Choice recorded.", exact=True)
                        ).to_be_visible()
                    page.reload()
                    expect(
                        page.get_by_text("Choice recorded.", exact=True)
                    ).to_be_visible()
                if state == "retry_succeeded":
                    latest = page.get_by_role(
                        "radio",
                        name="Choose advised value: A new authored description from the explicit retry.",
                    )
                    expect(latest).to_have_attribute("aria-checked", "false")
                    latest.focus()
                    with page.expect_response(
                        lambda response: (
                            response.request.method == "POST"
                            and response.url.endswith("/description-advice/selection")
                        )
                    ) as response:
                        latest.press("Enter")
                    assert response.value.ok
                    page.goto(url)
                    expect(latest).to_have_attribute("aria-checked", "true")
                    expect(
                        page.get_by_role(
                            "radio", name=re.compile("^Choose advised value:")
                        )
                    ).to_have_count(1)
            browser.close()


@pytest.mark.parametrize("width", [1440, 390, 320])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_preparation_stop_preserves_browse_and_requires_service_restart(
    width: int, theme: Literal["light", "dark"]
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=port,
                preparation=True,
            ) as (origin, case_id),
        ):
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                viewport={"width": width, "height": 900},
                color_scheme=theme,
            )
            page = context.new_page()
            stopped = {"value": True}
            mutations: list[str] = []
            _login(page, origin)
            response = page.request.get(origin + "/api/v1/review-preparation")
            assert response.status == 200
            snapshot = response.json()

            def preparation_response(route) -> None:
                document = dict(snapshot)
                # Deliberately retain stale active wording beneath the newer
                # runtime stop. The actual server supplies durable presentation.
                document["recovery_required"] = stopped["value"]
                document["active"] = True
                document["actionable"] = stopped["value"]
                document["libraries"] = [
                    {
                        "scan_id": "retained",
                        "connection": "primary",
                        "library": "Synthetic household library with a long descriptive name",
                        "library_id": "library",
                        "state": "completed_with_failures",
                        "checked": 7,
                        "total": 9,
                        "ready": 4,
                        "blocked": 1,
                        "recorded": 2,
                        "not_matched": 0,
                        "rescan_requested": False,
                        "stages": [{"stage": "extraction", "count": 2}],
                    }
                ]
                route.fulfill(status=200, json=document)

            page.route("**/api/v1/review-preparation", preparation_response)
            page.on(
                "request",
                lambda request: (
                    mutations.append(request.url)
                    if request.method == "POST"
                    and "/review-preparation/" in request.url
                    else None
                ),
            )
            try:
                page.reload()
                summary = page.locator(".preparation-summary")
                expect(summary).to_contain_text("Preparation stopped")
                summary.click()
                expect(
                    page.get_by_role("heading", name="Preparation stopped", exact=True)
                ).to_be_visible()
                expect(page.locator(".preparation-detail")).to_contain_text(
                    "Restart the Mechanical Librarian service"
                )
                expect(page.locator(".preparation-detail")).to_contain_text(
                    "last saved progress"
                )
                expect(page.locator(".preparation-detail")).not_to_contain_text(
                    "in progress"
                )
                trigger = page.get_by_role(
                    "button", name="Preparation stopped", exact=True
                )
                trigger.click()
                popover = page.get_by_role("region", name="Background activity")
                # A finished scan states only its checked count; its split is
                # its buckets.
                expect(popover).to_contain_text("Last saved progress7 of 9 checked")
                for label in ("Scan now", "Retry", "Pause"):
                    expect(
                        popover.get_by_role("button", name=label, exact=True)
                    ).to_be_disabled()
                popover.get_by_role("button", name="Close", exact=True).focus()
                page.keyboard.press("Escape")
                expect(trigger).to_be_focused()
                page.reload()
                expect(page.locator(".preparation-summary")).to_contain_text(
                    "Preparation stopped"
                )
                back = page.get_by_role("button", name="Back to works", exact=True)
                if back.is_visible():
                    back.click()
                page.locator(".library-work-row").first.click()
                page.wait_for_url(f"**/library?*preview={case_id}*")
                expect(page.locator(".selected-work-header")).to_be_visible()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth"
                )
                assert mutations == []
                # The backend restart boundary is covered by the real SQLite
                # claim fixture. A fresh snapshot alone clears its browser flag.
                stopped["value"] = False
                page.reload()
                expect(
                    page.get_by_role("button", name="Preparation stopped", exact=True)
                ).to_have_count(0)
            finally:
                page.unroute_all(behavior="wait")
                context.close()
                browser.close()


@pytest.mark.parametrize("scenario", ("supported", "conflict", "long", "ask", "ignore"))
def test_generated_authors_fixture_reaches_library_choice_and_evidence(
    scenario: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        os.chmod(hash_path, 0o600)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            with (
                closing(ReservedPort()) as port,
                _server(
                    database=root / "review.sqlite",
                    hash_path=hash_path,
                    certificate=root / "unused.crt",
                    key=root / "unused.key",
                    port=port,
                    transport_mode="http",
                    author_fixture="supported"
                    if scenario in {"ask", "ignore"}
                    else scenario,
                    author_destination=scenario
                    if scenario in {"ask", "ignore"}
                    else "automatic",
                ) as (origin, case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=authors")
                if scenario == "ignore":
                    expect(
                        page.get_by_role(
                            "radio", name="Choose proposed value: Ada Example"
                        )
                    ).to_have_count(0)
                    expect(
                        page.locator(".automatic-resolution-details summary")
                    ).to_contain_text(re.compile(r"No change: (?:[^,]+, )*Authors\b"))
                    page.get_by_text("Show why", exact=True).click()
                    row = page.locator(".automatic-resolution-row").filter(
                        has_text="Authors"
                    )
                    expect(row).to_contain_text("Current Writer")
                    expect(row).to_contain_text("No source offered a different value.")
                    context.close()
                    browser.close()
                    return
                choice = page.locator(".direct-choice").first
                expect(choice).to_be_visible()
                expect(choice).to_contain_text(
                    "Alexandra" if scenario == "long" else "Ada Example"
                )
                if scenario == "conflict":
                    expect(choice).to_contain_text("Different Writer")
                page.get_by_role("link", name="Evidence", exact=True).click()
                expect(
                    page.get_by_text(
                        "Alexandra Everlasting Example of the Northern Collaborative Writing Society"
                        if scenario == "long"
                        else "Ada Example",
                        exact=False,
                    ).first
                ).to_be_visible()
            browser.close()


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize(
    # A tag reads by name beside the tag itself, so it never reads like the
    # name the display-name option writes (#1286).
    "option,expected",
    # The retired preserve option is refused, so only the name is written (#1314).
    (("display_name", "English"),),
)
def test_language_observed_form_and_unknown_evidence_in_owner_workspace(
    audio, option, expected
):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "language.sqlite",
                hash_path=hash_path,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                language_fixture=("English", "en", "UND"),
                language_audio=audio,
                language_option=option,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=language")
            page.get_by_role("heading", name="Language", exact=True).wait_for()
            expect(page.get_by_text("English", exact=True)).to_be_visible()
            page.get_by_role("link", name="Evidence", exact=True).click()
            page.get_by_text(
                "Undetermined language; not an eligible choice", exact=False
            ).wait_for()
            expect(page.get_by_text("Language: UND.", exact=False)).to_be_visible()
            # The page names the language beside its tag, as Library does (#1286 L1).
            expect(
                page.get_by_text("Language: English (en)", exact=True).first
            ).to_be_visible()
            page.get_by_role("link", name="History", exact=True).click()
            expect(page.get_by_text(f"To {expected}", exact=True)).to_be_visible()
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role(
                "heading", name="Activity and recovery", exact=True
            ).wait_for()
            row = page.get_by_role("article").filter(has_text="Automatic choice")
            row.get_by_role("button", name="Details").click()
            # Activity names the language as Library does, in the row's change,
            # its announced sentence and its Details, never by a bare tag (#1490).
            expect(
                row.get_by_text(f"Set language to {expected}", exact=True)
            ).to_be_visible()
            expect(
                row.get_by_text(f"Set Language from Not set to {expected}", exact=True)
            ).to_have_count(1)
            expect(row.locator("dd").filter(has_text=expected)).to_have_text(expected)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


@pytest.mark.parametrize("audio", (False, True))
def test_language_ask_owner_choice_is_reachable_and_durable(audio):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "language.sqlite",
                hash_path=hash_path,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                language_fixture=("English", "en", "UND"),
                language_audio=audio,
                language_destination="ask",
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=language")
            page.get_by_role(
                "heading", name="Which language should this work use?", exact=True
            ).wait_for()
            choice = page.get_by_role(
                "radio", name="Choose proposed value: English", exact=True
            )
            expect(choice).to_be_enabled()
            expect(
                page.get_by_role("radio", name="Choose proposed value: UND", exact=True)
            ).to_have_count(0)
            choice.focus()
            choice.press("Space")
            expect(choice).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="History", exact=True).click()
            expect(page.get_by_text("To English", exact=True)).to_be_visible()
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role(
                "heading", name="Activity and recovery", exact=True
            ).wait_for()
            owner = page.get_by_role("article").filter(has_text="Owner choice")
            owner.get_by_role("button", name="Details").click()
            expect(owner.get_by_text("English", exact=True)).to_be_visible()
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=language")
            page.reload()
            expect(
                page.get_by_role(
                    "radio", name="Choose proposed value: English", exact=True
                )
            ).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(page.get_by_text("Language: UND.", exact=False)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_genre_creator_equivalence_owner_journey(destination):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "genre.sqlite",
                hash_path=hash_path,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                genre_fixture=("Fantasy",),
                genre_destination=destination,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
            page.get_by_role("heading", name="Real Tigers", exact=True).wait_for()
            if destination == "ask":
                choice = page.get_by_role(
                    "radio", name="Choose proposed value: Fantasy", exact=True
                )
                expect(choice).to_be_enabled()
                choice.focus()
                # The radio checks as soon as the decision is sent, while it is
                # still recording, and once the server answers the completed
                # work moves the page on. Neither is the recorded decision, and
                # a reload before the answer can cancel it. So the test waits for
                # the server's answer, then opens the field afresh (#1489).
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.endswith(f"/review-cases/{case_id}/decisions")
                    )
                ) as recorded:
                    choice.press("Space")
                assert recorded.value.ok, recorded.value.status
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
                expect(
                    page.get_by_role(
                        "radio", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_attribute("aria-checked", "true")
            elif destination == "ignore":
                expect(
                    page.get_by_role(
                        "radio", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_count(0)
            else:
                expect(
                    page.get_by_role(
                        "radio", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            if destination == "ignore":
                expect(page.get_by_text("Genre: Fantasy", exact=False)).to_have_count(0)
                expect(
                    page.get_by_role("heading", name="Genres", exact=True)
                ).to_be_visible()
            else:
                expect(page.get_by_text("Genre: Fantasy", exact=False)).to_be_visible()
            if destination != "ignore":
                page.get_by_role("link", name="History", exact=True).click()
                expect(page.get_by_text("To Fantasy", exact=True)).to_be_visible()
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role(
                    "heading", name="Activity and recovery", exact=True
                ).wait_for()
                event = page.get_by_role("article").filter(
                    has_text="Owner choice"
                    if destination == "ask"
                    else "Automatic choice"
                )
                event.get_by_role("button", name="Details").click()
                expect(event.get_by_text("Fantasy", exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


@contextmanager
def _owner_page(**fixture) -> Iterator[tuple[Page, str, str]]:
    """A signed-in page over a genre work the owner reviews by hand (#1285)."""
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "owner.sqlite",
                hash_path=hash_path,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                genre_destination="ask",
                **fixture,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            yield page, origin, case_id
        browser.close()


def _owner_layout_holds(page: Page) -> None:
    """Every owner control word stays whole and the page never scrolls sideways."""
    for width in (1440, 1100, 884, 761, 760, 390, 320):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => "
            "requestAnimationFrame(resolve)))"
        )
        assert page.locator(".owner-controls").count(), width
        assert not page.evaluate(_POLICY_MID_WORD, ".owner-controls"), width
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        heights = page.evaluate(
            "() => [...document.querySelectorAll('.owner-controls button')]"
            ".map(node => node.getBoundingClientRect().height)"
        )
        assert all(height >= 44 for height in heights), (width, heights)
    # At 320 px with 200% text, nothing in the controls passes the canvas edge.
    page.set_viewport_size({"width": 320, "height": 900})
    set_root_text(page, "200%")
    try:
        past = page.evaluate(
            """() => {
              const edge = document.querySelector('.decision-canvas')
                .getBoundingClientRect().right
              return [...document.querySelectorAll('.owner-controls, .owner-controls *')]
                .filter(node => node.getBoundingClientRect().right > edge + 0.5)
                .map(node => `${node.tagName}.${node.className}: ${
                  Math.round(node.getBoundingClientRect().right)} > ${Math.round(edge)}`)
            }"""
        )
        assert past == [], past
    finally:
        set_root_text(page, "")


def _attention_destinations(page: Page, case_id: str) -> list[str]:
    return page.evaluate(
        """async (caseId) => {
          const response = await fetch('/api/v1/attention?page_size=100')
          const body = await response.json()
          return body.items.filter(item => item.review_case_id === caseId)
            .map(item => item.destination)
        }""",
        case_id,
    )


# A value whose longest word is wide enough to set a column's width at 200% text.
_LONG_TITLE = "The Extraordinarily Long-Winded Chronicle of the Silent Orchard"


def test_owner_enters_a_title_at_enlarged_text():
    """B1: enter a value, see it recorded, with words whole at every width."""
    from tests.browser.test_live_updates_browser import _widen_text

    with _owner_page(genre_fixture=("Fantasy",), genre_current=("Drama",)) as (
        page,
        origin,
        case_id,
    ):
        _widen_text(page)
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
        panel = page.get_by_role("region", name="Your value for Title")
        expect(
            page.get_by_text("There’s no other value to choose from.")
        ).to_have_count(0)
        panel.get_by_role("button", name="Enter a value", exact=True).click()
        field = panel.get_by_role("textbox", name="Your value for Title")
        expect(field).to_be_focused()
        field.fill(_LONG_TITLE)
        expect(panel.locator(".owner-count")).to_have_text(
            f"{len(_LONG_TITLE)} of 512 characters"
        )
        _owner_layout_holds(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        with page.expect_response(
            lambda response: response.url.endswith("/decisions")
        ) as recorded:
            panel.get_by_role("button", name="Record this value", exact=True).click()
        assert recorded.value.status == 200
        entered = panel.get_by_text(f"You entered “{_LONG_TITLE}”.", exact=True)
        expect(entered).to_be_focused()
        expect(panel.get_by_role("button", name="Withdraw", exact=True)).to_be_visible()
        expect(panel.get_by_role("button", name="Change", exact=True)).to_be_visible()
        _owner_layout_holds(page)
        # Changing it opens the entry prefilled with the long value.
        page.set_viewport_size({"width": 1440, "height": 900})
        panel.get_by_role("button", name="Change", exact=True).click()
        expect(panel.get_by_role("textbox", name="Your value for Title")).to_have_value(
            _LONG_TITLE
        )
        _owner_layout_holds(page)


def test_owner_chooses_a_genre_subset_then_withdraws_it():
    """B2 and B3: a union-order subset records; withdrawing returns the field
    to Attention with focus on its heading."""
    # Drama is both current and proposed, so its option carries two tags.
    with _owner_page(
        genre_fixture=("Drama", "Fantasy", "Mystery"), genre_current=("Drama",)
    ) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
        panel = page.get_by_role("region", name="Your value for Genres")
        panel.get_by_role("button", name="Choose genres", exact=True).click()
        group = panel.get_by_role("group", name="Choose genres")
        expect(group.get_by_role("checkbox")).to_have_count(3)
        # Opening the subset puts focus on its first genre, never on body (D1).
        expect(group.get_by_role("checkbox").first).to_be_focused()
        expect(group.locator(".owner-option").first).to_contain_text(
            "DramaCurrentProposed"
        )
        group.get_by_role("checkbox", name=re.compile("^Mystery")).check()
        group.get_by_role("checkbox", name=re.compile("^Drama")).uncheck()
        group.get_by_role("checkbox", name=re.compile("^Fantasy")).check()
        _owner_layout_holds(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        with page.expect_request(
            lambda request: request.url.endswith("/decisions")
        ) as sent:
            panel.get_by_role("button", name="Record these genres", exact=True).click()
        body = json.loads(sent.value.post_data or "{}")
        assert (body["outcome"], body["selected_value"]) == (
            "owner_entered",
            {"kind": "list", "value": ["Fantasy", "Mystery"]},
        )
        expect(panel.get_by_text("You chose 2 genres.", exact=True)).to_be_focused()
        assert "genres" not in _attention_destinations(page, case_id)
        with page.expect_response(
            lambda response: response.url.endswith("/decisions")
        ) as withdrawn:
            panel.get_by_role("button", name="Withdraw", exact=True).click()
        assert withdrawn.value.status == 200
        expect(
            page.get_by_text(
                "Withdrawn. This field needs a decision again.", exact=True
            )
        ).to_be_focused()
        assert "genres" in _attention_destinations(page, case_id)
        page.get_by_role("link", name="History", exact=True).click()
        expect(page.get_by_text("Owner withdrew the decision.")).to_be_visible()
        expect(page.get_by_text("Owner entered a value.")).to_be_visible()


def _choose_previous_title(page: Page, origin: str, case_id: str):
    """Open the entry on the case that read the write back, and fill it from
    the value the write replaced."""
    page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
    panel = page.get_by_role("region", name="Your value for Title")
    expect(panel.get_by_role("button", name=re.compile("Restore"))).to_have_count(0)
    panel.get_by_role("button", name="Enter a value", exact=True).click()
    panel.get_by_role(
        "button", name="Use previous value “Real Tigers”", exact=True
    ).click()
    expect(panel.get_by_role("textbox", name="Your value for Title")).to_have_value(
        "Real Tigers"
    )
    return panel


# Every alert and status text the page shows from now on, however briefly.
_WATCH_ANNOUNCEMENTS = """() => { window.__said = new Set(); new MutationObserver(() => {
  for (const node of document.querySelectorAll('[role=alert],[role=status]'))
    if (node.textContent.trim()) window.__said.add(node.textContent.trim()) })
  .observe(document.body, {subtree: true, childList: true, characterData: true}) }"""


def _said_nothing_false_while_moving(page: Page) -> None:
    said = page.evaluate("[...window.__said]")
    assert not [
        text
        for text in said
        if "could not be loaded" in text or "not available in the current" in text
    ], said


def test_owner_chooses_the_previous_title_again():
    """B4: the case that read the write back offers the value it replaced as a
    suggestion, and choosing it is an ordinary owner-entered decision."""
    with _owner_page(
        genre_fixture=("Fantasy",), genre_current=("Drama",), owner_write=True
    ) as (page, origin, case_id):
        panel = _choose_previous_title(page, origin, case_id)
        _owner_layout_holds(page)
        page.set_viewport_size({"width": 1440, "height": 900})
        with page.expect_request(
            lambda request: request.url.endswith("/decisions")
        ) as sent:
            panel.get_by_role("button", name="Record this value", exact=True).click()
        body = json.loads(sent.value.post_data or "{}")
        assert (body["outcome"], body["selected_value"]) == (
            "owner_entered",
            {"kind": "text", "value": "Real Tigers"},
        )
        assert "reverts_plan_id" not in body
        expect(
            panel.get_by_text("You entered “Real Tigers”.", exact=True)
        ).to_be_focused()
        expect(panel.get_by_text("Your value is recorded.", exact=True)).to_be_visible()


def test_a_work_back_at_its_earlier_title_reads_as_new():
    """#1358: once the previous title is chosen again and written, the work's
    first case is current again and shows nothing decided on it."""
    with _owner_page(
        genre_fixture=("Fantasy",),
        genre_current=("Drama",),
        owner_write=True,
        owner_recur=True,
    ) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
        panel = page.get_by_role("region", name="Your value for Title")
        expect(
            panel.get_by_role("button", name="Enter a value", exact=True)
        ).to_be_visible()
        # Not "You entered …" with Change and Withdraw from its first episode.
        expect(page.get_by_text(re.compile("^You entered"))).to_have_count(0)
        expect(panel.get_by_role("button", name="Change", exact=True)).to_have_count(0)
        expect(panel.get_by_role("button", name="Withdraw", exact=True)).to_have_count(
            0
        )
        _owner_layout_holds(page)


def test_the_previous_title_chosen_after_the_library_changed_is_refused():
    """B4: the library edits the title while the entry is open; the store
    refuses the stale entry and the owner reaches the current case."""
    with _owner_page(
        genre_fixture=("Fantasy",),
        genre_current=("Drama",),
        owner_write=True,
        owner_library_change=True,
    ) as (page, origin, case_id):
        panel = _choose_previous_title(page, origin, case_id)
        page.evaluate(_WATCH_ANNOUNCEMENTS)
        with page.expect_response(
            lambda response: response.url.endswith("/decisions")
        ) as refused:
            panel.get_by_role("button", name="Record this value", exact=True).click()
        assert refused.value.status == 409
        assert refused.value.json()["error"]["code"] == "stale"
        current = page.evaluate(
            """async (caseId) => (await (await fetch(
              `/api/v1/review-cases/${caseId}/successor`)).json()).review_case_id""",
            case_id,
        )
        assert current and current != case_id
        # The owner stays on the work, on the case that replaced theirs, and is
        # told there why nothing was recorded (M4).
        page.wait_for_url(re.compile(f"preview={current}"))
        moved = page.get_by_role("region", name="Your value for Title")
        expect(
            moved.get_by_text(
                "Not recorded: the work changed. Review it again.", exact=True
            )
        ).to_be_visible()
        expect(page.locator(".decision-canvas > header h3")).to_be_focused()
        _said_nothing_false_while_moving(page)


@pytest.mark.parametrize("named", (False, True), ids=("unnamed", "named"))
@pytest.mark.parametrize(
    "library_last", (False, True), ids=("library-first", "library-last")
)
def test_an_open_entry_moves_to_the_case_that_superseded_it(library_last, named):
    """MF1: the library edits the title while the owner types; the real
    server refuses the superseded case's read as stale, and the owner still
    reaches the current case, told why, with focus on the field heading.
    Whether the Library update names the successor or leaves the page to ask
    for it, and whether the Library read answers before or after the other
    read that drops the case, the page never says it is unavailable."""
    readback: list[Callable[[str, bool], str]] = []
    held: list[Route] = []
    with _owner_page(
        genre_fixture=("Fantasy",), genre_current=("Drama",), owner_readback=readback
    ) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
        panel = page.get_by_role("region", name="Your value for Title")
        panel.get_by_role("button", name="Enter a value", exact=True).click()
        panel.get_by_role("textbox", name="Your value for Title").fill("Half Typed")
        page.evaluate(_WATCH_ANNOUNCEMENTS)

        def library(url: str) -> bool:
            return "/api/v1/library?" in url

        def lineage(url: str) -> bool:
            return url.endswith("/successor")

        def attention(url: str) -> bool:
            return "/api/v1/attention" in url

        def hold(route: Route) -> None:
            held.append(route)

        # A named successor needs no lineage read; Attention then is the other
        # read. The held read answers last, after the other and Attention.
        other = attention if named else lineage
        sooner, later = (other, library) if library_last else (library, other)
        page.route(later, hold)
        with (
            page.expect_response(lambda response: sooner(response.url)),
            nullcontext()
            if attention in (sooner, later)
            else page.expect_response(lambda response: attention(response.url)),
        ):
            successor = readback[0](OWNER_EDIT, named)
        page.unroute(later)
        for route in held:
            route.continue_()
        assert successor != case_id
        page.wait_for_url(re.compile(f"preview={successor}"))
        moved = page.get_by_role("region", name="Your value for Title")
        expect(
            moved.get_by_role("status").get_by_text(
                "The work changed. Review it again.", exact=True
            )
        ).to_be_visible()
        expect(page.locator(".decision-canvas > header h3")).to_be_focused()
        _said_nothing_false_while_moving(page)


# Genres is the work's only attention field, so a decision moves it between
# the Needs attention and No attention needed lenses.
_SOLE_GENRES: dict[str, Any] = {
    "genre_fixture": ("Drama", "Fantasy", "Mystery"),
    "genre_current": ("Drama",),
    "genre_others": "ignore",
}


def _lens_read(lens: str) -> Callable[[Any], bool]:
    return lambda response: (
        "/api/v1/library?" in response.url and f"lens={lens}" in response.url
    )


def _work_stays_in_view(page: Page) -> None:
    """The selected work keeps its identity and field controls although the
    Library result no longer lists it (#1365)."""
    expect(
        page.get_by_role("button", name=re.compile(r"^Real Tigers\."))
    ).to_have_count(0)
    expect(page.get_by_role("heading", name="Real Tigers", exact=True)).to_be_visible()
    expect(page.get_by_role("region", name="Your value for Genres")).to_be_visible()
    _said_nothing_false_while_moving(page)


def _record_genres(
    panel, *names: str, said: str, opener: str = "Choose genres"
) -> None:
    panel.get_by_role("button", name=opener, exact=True).click()
    group = panel.get_by_role("group", name="Choose genres")
    for name in names:
        group.get_by_role("checkbox", name=re.compile(f"^{name}")).click()
    panel.get_by_role("button", name="Record these genres", exact=True).click()
    expect(panel.get_by_text(said, exact=True)).to_be_focused()


def test_owner_values_keep_the_work_that_leaves_attention_in_view():
    """#1365: a genre subset recorded for the work's last attention field, and
    then changed, takes the work off the Needs attention page; the work, its
    field and the outcome stay on screen with focus on the field's controls."""
    with _owner_page(**_SOLE_GENRES) as (page, origin, case_id):
        page.goto(
            f"{origin}/library?lens=needs_attention&preview={case_id}&field=genres"
        )
        panel = page.get_by_role("region", name="Your value for Genres")
        expect(
            page.get_by_role("button", name=re.compile(r"^Real Tigers\."))
        ).to_be_visible()
        assert _attention_destinations(page, case_id) == ["genres"]
        page.evaluate(_WATCH_ANNOUNCEMENTS)
        with page.expect_response(_lens_read("needs_attention")):
            _record_genres(panel, "Fantasy", said="You chose 2 genres.")
        _work_stays_in_view(page)
        expect(panel.get_by_text("You chose 2 genres.", exact=True)).to_be_focused()
        _record_genres(panel, "Drama", said="You chose 1 genre.", opener="Change")
        _work_stays_in_view(page)


def test_withdrawing_keeps_the_work_that_leaves_its_lens_in_view():
    """#1365: withdrawing returns the field to Attention, which takes the work
    off the No attention needed page; the work and field stay on screen."""
    with _owner_page(**_SOLE_GENRES) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
        panel = page.get_by_role("region", name="Your value for Genres")
        _record_genres(panel, "Fantasy", said="You chose 2 genres.")
        page.goto(
            f"{origin}/library?lens=no_attention_needed&preview={case_id}&field=genres"
        )
        expect(
            page.get_by_role("button", name=re.compile(r"^Real Tigers\."))
        ).to_be_visible()
        page.evaluate(_WATCH_ANNOUNCEMENTS)
        with page.expect_response(_lens_read("no_attention_needed")):
            panel.get_by_role("button", name="Withdraw", exact=True).click()
            expect(
                panel.get_by_text(
                    "Withdrawn. This field needs a decision again.", exact=True
                )
            ).to_be_visible()
        _work_stays_in_view(page)
        expect(
            panel.get_by_text(
                "Withdrawn. This field needs a decision again.", exact=True
            )
        ).to_be_focused()


# The focused element lies inside the viewport and below the sticky masthead.
_FOCUS_IN_VIEW = """() => {
  const box = document.activeElement.getBoundingClientRect()
  const masthead = document.querySelector('.masthead').getBoundingClientRect()
  return box.top >= masthead.bottom - 0.5 && box.bottom <= innerHeight + 0.5
}"""


@pytest.mark.parametrize("width", (390, 320))
def test_withdrawing_at_compact_width_focuses_its_outcome_in_place(width):
    """#1365: after a withdrawal focus is on its outcome line, where Withdraw
    was, inside the viewport below the masthead, and the page barely moves."""
    with _owner_page(**_SOLE_GENRES) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
        panel = page.get_by_role("region", name="Your value for Genres")
        _record_genres(panel, "Fantasy", said="You chose 2 genres.")
        page.set_viewport_size({"width": width, "height": 844})
        page.goto(
            f"{origin}/library?lens=no_attention_needed&preview={case_id}&field=genres"
        )
        withdraw = panel.get_by_role("button", name="Withdraw", exact=True)
        withdraw.scroll_into_view_if_needed()
        before = page.evaluate("scrollY")
        withdraw.click()
        outcome = panel.get_by_text(
            "Withdrawn. This field needs a decision again.", exact=True
        )
        expect(outcome).to_be_focused()
        # Still so once the confirmed case and Library reads have painted.
        page.wait_for_timeout(1_000)
        expect(outcome).to_be_focused()
        moved = abs(page.evaluate("scrollY") - before)
        assert page.evaluate(_FOCUS_IN_VIEW), page.evaluate(
            "() => [document.activeElement.getBoundingClientRect().top,"
            " document.querySelector('.masthead').getBoundingClientRect().bottom]"
        )
        assert moved < 100, moved


def test_a_search_that_excludes_the_selected_work_leaves_it_in_view():
    """#1365 and #1284: search scopes only the result list."""
    with _owner_page(**_SOLE_GENRES) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
        expect(
            page.get_by_role("button", name=re.compile(r"^Real Tigers\."))
        ).to_be_visible()
        page.evaluate(_WATCH_ANNOUNCEMENTS)
        with page.expect_response(
            lambda response: (
                _lens_read("all")(response) and "q=Nothing+here" in response.url
            )
        ):
            page.get_by_role("searchbox").fill("Nothing here")
        _work_stays_in_view(page)
        expect(
            page.get_by_role("button", name="Choose genres", exact=True)
        ).to_be_visible()


def test_a_deep_link_to_a_work_off_the_current_page_shows_it():
    """#1365: the linked work is in neither the Library page nor Attention."""
    with _owner_page(**_SOLE_GENRES) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=genres")
        panel = page.get_by_role("region", name="Your value for Genres")
        _record_genres(panel, "Fantasy", said="You chose 2 genres.")
        assert _attention_destinations(page, case_id) == []
        # Watch from the first paint, so a passing absence is caught too.
        page.add_init_script(
            f"({_WATCH_ANNOUNCEMENTS.replace('document.body', 'document')})()"
        )
        page.goto(
            f"{origin}/library?lens=needs_attention&preview={case_id}&field=genres"
        )
        _work_stays_in_view(page)
        expect(panel.get_by_role("button", name="Withdraw", exact=True)).to_be_visible()


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_same_name_series_owner_journey(media, destination):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        title = "Synthetic Journey" if media == "audiobook" else "First Journey"
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "series.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                series_fixture=media,
                series_destination=destination,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
            page.get_by_role("heading", name=title, exact=True).wait_for()
            choice = page.get_by_role(
                "radio", name=re.compile(r"^Choose proposed value: " + title)
            )
            if destination == "ask":
                expect(choice).to_be_enabled()
                choice.focus()
                # Reopen the field only once the decision is acknowledged and
                # the case has advanced past it (#1297).
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as recorded:
                    choice.press("Space")
                assert 200 == recorded.value.status
                following = "ASIN" if media == "audiobook" else "ISBN"
                expect(
                    page.get_by_role(
                        "heading",
                        name=f"Which {following} should this work use?",
                        exact=True,
                    )
                ).to_be_visible()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                expect(choice).to_have_attribute("aria-checked", "true")
            elif destination == "automatic":
                expect(choice).to_have_attribute("aria-checked", "true")
                if media == "ebook":
                    expect(
                        page.get_by_text(
                            "Selected from Hardcover to fill an empty Series field"
                        )
                    ).to_be_visible()
            else:
                expect(choice).to_have_count(0)
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(
                page.get_by_role("heading", name="Series", exact=True)
            ).to_be_visible()
            if destination != "ignore":
                expect(
                    page.get_by_text("Series membership: " + title, exact=False).first
                ).to_be_visible()
                page.get_by_role("link", name="History", exact=True).click()
                expect(page.get_by_text("To " + title, exact=False)).to_be_visible()
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role(
                    "heading", name="Activity and recovery", exact=True
                ).wait_for()
                expect(
                    page.get_by_role("article").filter(has_text=title).first
                ).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_audiobook_work_owner_journey():
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "work.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                work_review_fixture=True,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=description")
            expect(
                page.get_by_role("region", name="Book-level evidence")
            ).to_contain_text("The exact recording remains unresolved.")
            # The note names only what the rail offers here, Description and
            # Genres, never every field the case carries (#1286 X3).
            expect(
                page.get_by_role("region", name="Book-level evidence")
            ).to_contain_text(
                "Book-level evidence supports description and genre choices. "
                "The exact recording remains unresolved."
            )
            # A title the evidence only restates is no choice: the case shows
            # the same rows as before #1253.
            for field in ("Title", "Series"):
                expect(
                    page.get_by_role("button", name=re.compile(rf"^{field}\b"))
                ).to_have_count(0)
            choice = page.get_by_role(
                "radio",
                name="Choose proposed value: A voyage reveals a hidden city.",
                exact=True,
            )
            choice.focus()
            # The choice reads pressed while it is still recording, and a reload
            # before the request leaves aborts it. Reload only once the case has
            # acknowledged the decision and completed (#1297).
            with page.expect_response(
                lambda response: response.url.endswith("/decisions")
            ) as recorded:
                choice.press("Space")
            assert 200 == recorded.value.status
            expect(
                page.get_by_role("heading", name="Review complete", exact=True)
            ).to_be_visible()
            page.reload()
            expect(choice).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(
                page.get_by_role("region", name="Book-level evidence")
            ).to_be_visible()
            page.get_by_role("link", name="History", exact=True).click()
            expect(
                page.get_by_text("To A voyage reveals a hidden city.", exact=True)
            ).to_be_visible()
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role(
                "heading", name="Activity and recovery", exact=True
            ).wait_for()
            expect(
                page.get_by_role("article")
                .filter(has_text="Owner choice")
                .filter(has_text="Description")
            ).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_audiobook_work_series_title_owner_journey():
    """Book-level series and title choices for an unresolved recording (#1253)."""
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "work.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                work_review_fixture=True,
                work_series_title=True,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
            expect(
                page.get_by_role("region", name="Book-level evidence")
            ).to_contain_text(
                "Book-level evidence supports series, title, description, and genre "
                "choices. The exact recording remains unresolved."
            )
            series = page.get_by_role(
                "radio", name=re.compile(r"^Choose proposed value: Example Saga")
            )
            series.focus()
            # Each choice is acknowledged, and the case advances past it,
            # before the page moves on (#1297).
            with page.expect_response(
                lambda response: response.url.endswith("/decisions")
            ) as recorded:
                series.press("Space")
            assert 200 == recorded.value.status
            expect(
                page.get_by_role(
                    "heading", name="Which title should this work use?", exact=True
                )
            ).to_be_visible()
            title = page.get_by_role(
                "radio", name="Choose proposed value: Synthetic Journey", exact=True
            )
            title.focus()
            with page.expect_response(
                lambda response: response.url.endswith("/decisions")
            ) as recorded:
                title.press("Enter")
            assert 200 == recorded.value.status
            expect(
                page.get_by_role(
                    "heading",
                    name="Which description should this work use?",
                    exact=True,
                )
            ).to_be_visible()
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
            expect(title).to_have_attribute("aria-checked", "true")
            # Only the book's own fields are offered; the subtitle, cover and
            # recording rows stay out.
            expect(page.get_by_text("field 2 of 4", exact=False)).to_be_visible()
            for field, count in (
                ("Description", 1),
                ("Title", 1),
                ("Series", 1),
                ("Genres", 1),
                ("Subtitle", 0),
                ("Cover", 0),
                ("Narrators", 0),
                ("ASIN", 0),
            ):
                expect(
                    page.get_by_role("button", name=re.compile(rf"^{field}\b"))
                ).to_have_count(count)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
            expect(series).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(
                page.get_by_role("heading", name="Series", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("Series membership: Example Saga", exact=False).first
            ).to_be_visible()
            expect(
                page.get_by_text("Audnexus · External corroboration").first
            ).to_be_visible()
            expect(
                page.get_by_role("region", name="Book-level evidence")
            ).to_be_visible()
            expect(
                page.get_by_role("region", name="Recording identity evidence")
            ).to_have_count(0)
            page.get_by_role("link", name="History", exact=True).click()
            expect(page.get_by_text("To Example Saga", exact=False)).to_be_visible()
            expect(page.get_by_text("To Synthetic Journey", exact=True)).to_be_visible()
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role(
                "heading", name="Activity and recovery", exact=True
            ).wait_for()
            for field in ("Series", "Title"):
                expect(
                    page.get_by_role("article")
                    .filter(has_text="Owner choice")
                    .filter(has_text=field)
                    .first
                ).to_be_visible()
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
            expect(title).to_have_attribute("aria-checked", "true")
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 844})
                for scheme in ("light", "dark"):
                    page.emulate_media(color_scheme=scheme)
                    expect(title).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth"
                        " <= document.documentElement.clientWidth"
                    ), (width, scheme)
        browser.close()


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_audiobook_synopsis_owner_journey(destination):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "synopsis.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                synopsis_fixture="Chapter 1",
                synopsis_destination=destination,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=description")
            page.get_by_role("heading", name="Synthetic Journey", exact=True).wait_for()
            choice = page.get_by_role(
                "radio",
                name="Choose proposed value: A voyage reveals a hidden city.",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    # Reopen the field only once the decision is acknowledged
                    # and the case has advanced past it (#1297).
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as recorded:
                        choice.press("Space")
                    assert 200 == recorded.value.status
                    expect(
                        page.get_by_role(
                            "heading",
                            name="Which ASIN should this work use?",
                            exact=True,
                        )
                    ).to_be_visible()
                    page.goto(
                        f"{origin}/library?lens=all&preview={case_id}&field=description"
                    )
                else:
                    expect(choice).to_have_attribute("aria-checked", "true")
                    page.reload()
                expect(choice).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(
                page.get_by_role("heading", name="Description", exact=True)
            ).to_be_visible()
            if destination != "ignore":
                page.get_by_role("link", name="History", exact=True).click()
                expect(
                    page.get_by_text("To A voyage reveals a hidden city.", exact=True)
                ).to_be_visible()
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role(
                    "heading", name="Activity and recovery", exact=True
                ).wait_for()
                event = (
                    page.get_by_role("article")
                    .filter(
                        has_text="Owner choice"
                        if destination == "ask"
                        else "Automatic choice"
                    )
                    .filter(has_text="Description")
                )
                event.get_by_role("button", name="Details").click()
                expect(
                    event.get_by_text("A voyage reveals a hidden city.", exact=True)
                ).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_ebook_synopsis_owner_journey(destination):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "synopsis.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                synopsis_fixture="Chapter 1",
                synopsis_destination=destination,
                ebook_synopsis=True,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=description")
            page.get_by_role("heading", name="Synthetic Voyage", exact=True).wait_for()
            selected = (
                "(duplicate)"
                if destination == "ask"
                else "A voyage reveals a hidden city."
            )
            choice = page.get_by_role(
                "radio",
                name=f"Choose proposed value: {selected}",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    # Reload only once the decision is acknowledged (#1297).
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as recorded:
                        choice.press("Space")
                    assert 200 == recorded.value.status
                    expect(
                        page.get_by_role("heading", name="Review complete", exact=True)
                    ).to_be_visible()
                else:
                    expect(choice).to_have_attribute("aria-checked", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(
                page.get_by_role("heading", name="Description", exact=True)
            ).to_be_visible()
            if destination != "ignore":
                page.get_by_role("link", name="History", exact=True).click()
                expect(page.get_by_text(f"To {selected}", exact=True)).to_be_visible()
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role(
                    "heading", name="Activity and recovery", exact=True
                ).wait_for()
                event = (
                    page.get_by_role("article")
                    .filter(
                        has_text="Owner choice"
                        if destination == "ask"
                        else "Automatic choice"
                    )
                    .filter(has_text="Description")
                )
                event.get_by_role("button", name="Details").click()
                expect(event.get_by_text(selected, exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


# Where the Review complete heading lands against the masthead, and each of the
# card's rows and values that runs past the card.
_COMPLETION_GEOMETRY = """() => {
  const card = document.querySelector('.review-complete').getBoundingClientRect();
  const heading = document.querySelector('.review-complete h3').getBoundingClientRect();
  const masthead = document.querySelector('.masthead').getBoundingClientRect();
  return {
    focused: document.activeElement?.matches('.review-complete') ?? false,
    heading: [Math.round(heading.top), Math.round(heading.bottom)],
    clear: Math.max(0, Math.round(masthead.bottom)),
    height: innerHeight,
    truncated: [...document.querySelectorAll('.review-complete li strong')]
      .filter((node) => node.scrollWidth > node.clientWidth + 1)
      .map((node) => `"${node.textContent}" is cut short`),
    escaped: [...document.querySelectorAll('.review-complete li :is(button, small, strong)')]
      .map((node) => [node.tagName, node.getBoundingClientRect()])
      .filter(([, box]) => box.left < card.left - 1 || box.right > card.right + 1)
      .map(([name, box]) => `${name} ${Math.round(box.left)}..${Math.round(box.right)}`
        + ` past ${Math.round(card.left)}..${Math.round(card.right)}`),
  };
}"""


def test_review_complete_comes_into_view_and_fits_its_card() -> None:
    """After the last keyboard choice, the Review complete heading is wholly in
    view and clear of the masthead at every ladder width in both themes, and
    with 200% text on a phone the card's rows and values stay inside it,
    whole (#1490). Each measured choice is the last field's, made again from the
    card with the page scrolled to it, as it is at the end of a long review."""
    sizes = [
        (1440, 900, ""),
        (1200, 900, ""),
        (1024, 768, ""),
        (760, 1024, ""),
        (390, 844, ""),
        (375, 812, ""),
        (320, 640, ""),
        (375, 812, "200%"),
        (320, 640, "200%"),
    ]
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "complete.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                preparation=True,
                genre_fixture=("Fantasy",),
                genre_current=("Drama",),
                genre_destination="ask",
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}")
            heading = page.get_by_role("heading", name="Review complete", exact=True)
            choice = page.locator(".decision-canvas [role=radio][aria-checked=false]")
            while not heading.count():
                choice.first.focus()
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ):
                    page.keyboard.press("Space")
                expect(heading.or_(choice.first)).to_be_visible()
            failures = []
            for theme in ("light", "dark"):
                toggle = page.get_by_role("button", name=f"Use {theme} mode")
                if toggle.count():
                    toggle.click()
                for width, height, text in sizes:
                    where = f"{theme} {width}x{height} {text or '100%'}"
                    page.set_viewport_size({"width": width, "height": height})
                    set_root_text(page, text)
                    page.locator(".review-complete li button").last.click()
                    choice.first.wait_for()
                    page.evaluate(_TWO_FRAMES)
                    choice.first.evaluate(
                        """node => {
                          const masthead = document.querySelector('.masthead').getBoundingClientRect();
                          scrollBy(0, node.getBoundingClientRect().top - Math.max(0, masthead.bottom) - 8);
                          node.focus({preventScroll: true});
                        }"""
                    )
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ):
                        page.keyboard.press("Space")
                    expect(heading).to_be_visible()
                    page.evaluate(_TWO_FRAMES)
                    found = page.evaluate(_COMPLETION_GEOMETRY)
                    top, bottom = found["heading"]
                    if not found["focused"]:
                        failures.append(f"{where}: the card does not have focus")
                    if top < found["clear"] or bottom > found["height"]:
                        failures.append(
                            f"{where}: the heading spans {top}..{bottom}, the clear"
                            f" view {found['clear']}..{found['height']}"
                        )
                    failures += [f"{where}: {item}" for item in found["escaped"]]
                    if text:
                        failures += [f"{where}: {item}" for item in found["truncated"]]
            assert failures == [], "\n".join(failures)
        browser.close()


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
def test_ebook_isbn_owner_journey(destination):
    with tempfile.TemporaryDirectory() as directory, sync_playwright() as playwright:
        root = Path(directory)
        root.chmod(0o700)
        password = root / "owner.hash"
        password.write_text(PasswordHasher().hash(_PASSWORD))
        password.chmod(0o600)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        with (
            closing(ReservedPort()) as port,
            _server(
                database=root / "isbn.sqlite",
                hash_path=password,
                certificate=root / "unused",
                key=root / "unused",
                port=port,
                transport_mode="http",
                isbn_fixture=True,
                isbn_destination=destination,
            ) as (origin, case_id),
            _closing_context(context),
        ):
            _login(page, origin)
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=isbn")
            page.get_by_role(
                "heading", name="Synthetic ISBN Journey", exact=True
            ).wait_for()
            selected = "9780306406157"
            choice = page.get_by_role(
                "radio",
                name=f"Choose proposed value: {selected}",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    # Reload only once the decision is acknowledged (#1297).
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as recorded:
                        choice.press("Space")
                    assert 200 == recorded.value.status
                    expect(
                        page.get_by_role("heading", name="Review complete", exact=True)
                    ).to_be_visible()
                else:
                    expect(choice).to_have_attribute("aria-checked", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-checked", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(page.get_by_role("heading", name="ISBN", exact=True)).to_be_visible()
            if destination != "ignore":
                page.get_by_role("link", name="History", exact=True).click()
                expect(page.get_by_text(f"To {selected}", exact=True)).to_be_visible()
                page.get_by_role("link", name="Activity", exact=True).click()
                page.get_by_role(
                    "heading", name="Activity and recovery", exact=True
                ).wait_for()
                event = (
                    page.get_by_role("article")
                    .filter(
                        has_text="Owner choice"
                        if destination == "ask"
                        else "Automatic choice"
                    )
                    .filter(has_text="ISBN")
                )
                event.get_by_role("button", name="Details").click()
                expect(event.get_by_text(selected, exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


# Clipped overflow escapes a check that only reads `overflow: visible` boxes, so
# this reads every rendered box: one that scrolls its own content, and one that
# ends past its parent's content edge. Visually hidden text is skipped because
# an sr-only node always reports a scroll width larger than its 1 px client box.
_POLICY_CLIPPED = """root => [...root.querySelectorAll('*')].flatMap(node => {
    if (!(node instanceof HTMLElement) || node.closest('.sr-only')
        || !node.getClientRects().length) return [];
    const owner = node.parentElement?.closest('[class]');
    const name = `${node.tagName}.${node.className} in ${owner?.className ?? 'root'}`
        + ` "${node.textContent.trim().slice(0, 32)}"`;
    const found = [];
    if (getComputedStyle(node).display !== 'inline'
        && node.scrollWidth > node.clientWidth + 1)
        found.push(`${name} scrolls ${node.scrollWidth} > ${node.clientWidth}`);
    const parent = node.parentElement;
    const style = parent && getComputedStyle(parent);
    if (parent && node !== root && !['inline', 'contents'].includes(style.display)) {
        const edge = parent.getBoundingClientRect().right
            - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight);
        const right = node.getBoundingClientRect().right;
        if (right > edge + 1)
            found.push(`${name} ends ${right} past ${parent.className} ${edge}`);
    }
    return found;
})"""
# A label word that renders on two lines has client rects with two tops.
_POLICY_LABELS = (
    ".cm-policy-sidebar nav button strong, .cm-policy-field > header strong, "
    ".cm-segmented button, .cm-policy-representation > span, "
    ".cm-policy-control-heading strong, .cm-finish-tray > ol small, "
    ".cm-finish-actions button, .cm-finish-summary strong, .cm-policy-impact > b, "
    ".cm-policy-route h1, .cm-policy-route h2"
)
_POLICY_MID_WORD = """selector => [...document.querySelectorAll(selector)].flatMap(node => {
    const words = [];
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
        for (const match of text.data.matchAll(/[^\\s\\u2010-\\u2014-]+/g)) {
            const range = document.createRange();
            range.setStart(text, match.index);
            range.setEnd(text, match.index + match[0].length);
            const tops = new Set([...range.getClientRects()]
                .filter(rect => rect.width > 0).map(rect => Math.round(rect.top)));
            if (tops.size > 1) words.push(match[0]);
        }
    return words;
})"""


def test_recorded_refusal_and_populated_policy_results_use_document_flow(tmp_path):
    hash_path = tmp_path / "owner.hash"
    hash_path.write_text(PasswordHasher().hash(_PASSWORD))
    hash_path.chmod(0o600)
    with (
        closing(ReservedPort()) as port,
        _server(
            database=tmp_path / "review.sqlite",
            hash_path=hash_path,
            certificate=tmp_path / "unused.crt",
            key=tmp_path / "unused.key",
            port=port,
            transport_mode="insecure",
            refusal_fixture=True,
            settings_document=None,
        ) as (origin, case_id),
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch(
            args=[
                "--host-resolver-rules=MAP review-transport.test 127.0.0.1",
                "--no-proxy-server",
            ]
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            _login(page, origin)
            active_policy = page.evaluate(
                "async () => (await fetch('/api/v1/metadata-policy/state')).json()"
            )
            page.goto(f"{origin}/library?lens=all&preview={case_id}&field=description")
            expect(
                page.get_by_text(
                    "No source offered a value that can be used here.", exact=True
                )
            ).to_be_visible()
            # Language is a rule the preview does not evaluate, ISBN one it
            # evaluates without a change here, and Tags one it evaluates with
            # changes: the mixed case a truthful preview has to keep apart.
            page.goto(f"{origin}/policy?section=identity")
            chapters = page.get_by_role("navigation", name="Policy chapters")
            # Language has one representation, so its destination changes.
            page.get_by_role("region", name="Language", exact=True).get_by_role(
                "button", name="Ignore", exact=True
            ).click()
            for chapter, field, option in (
                ("Publication", "ISBN", "Prefer ISBN-13"),
                ("Curation", "Tags", "Fill only when empty"),
            ):
                if chapter:
                    chapters.get_by_role(
                        "button", name=re.compile(f"^{chapter}")
                    ).click()
                region = page.get_by_role("region", name=field, exact=True)
                region.get_by_role("combobox", name=f"{field} representation").click()
                page.get_by_role("option", name=option, exact=True).click()
            tags = page.get_by_role("region", name="Tags", exact=True)
            tags.get_by_role("button", name="Automatic", exact=True).click()
            with page.expect_response(
                lambda response: response.url.endswith("/metadata-policy/preview")
            ) as previewed:
                page.get_by_role(
                    "button", name="Validate and preview", exact=True
                ).click()
            libraries = previewed.value.json()["libraries"]
            assert all(
                "language_rendering" in library["affected_categories"]
                for library in libraries
            )
            evaluated = {
                field["draft"]["field"]
                for library in libraries
                for comparison in library["comparisons"]
                for field in comparison["fields"]
            }
            assert "language_rendering" not in evaluated
            assert {"isbn_form", "tags"} <= evaluated
            result = page.locator(".cm-policy-result")
            expect(result).to_be_visible()
            assert (
                result.locator("xpath=..").get_attribute("class") == "cm-policy-editor"
            )
            assert page.locator(".cm-finish-tray .cm-policy-result").count() == 0
            # Language was never evaluated, so it is stated as not previewed
            # (below the collapsed previewed rows, by the library slug while
            # Settings is unavailable or the library has no display name) and
            # never as unchanged.
            unpreviewed = result.locator(".cm-policy-preview-list > .is-unpreviewed")
            expect(unpreviewed).to_have_count(1)
            expect(unpreviewed.locator("strong")).to_have_text("Language")
            expect(unpreviewed.locator("small").first).to_have_text("ebooks")
            expect(unpreviewed.locator(".cm-policy-impact")).to_have_text(
                "Not previewed.This rule takes effect on the next preparation."
            )
            expect(
                result.get_by_text("No policy-caused changes in this sample")
            ).to_have_count(0)
            assert page.locator(".cm-policy-preview-list > div").last.evaluate(
                "node => node.classList.contains('is-unpreviewed')"
            )
            for theme in ("light", "dark"):
                page.locator("html").evaluate(
                    "(node, theme) => node.dataset.theme = theme", theme
                )
                for width in (1440, 390, 320):
                    page.set_viewport_size({"width": width, "height": 900})
                    if width == 1440:
                        result.scroll_into_view_if_needed()
                        assert result.evaluate("""node => {
                            const result = node.getBoundingClientRect();
                            const rail = document.querySelector('.cm-policy-sidebar')
                                .getBoundingClientRect();
                            return result.right <= rail.left || result.left >= rail.right
                                || result.bottom <= rail.top || result.top >= rail.bottom;
                        }""")
                    expand = result.get_by_role(
                        "button", name=re.compile(r"Show .* more")
                    )
                    expand.focus()
                    page.keyboard.press("Enter")
                    if width == 1440:
                        result.locator(
                            ".cm-policy-preview"
                        ).scroll_into_view_if_needed()
                        assert result.evaluate("""node => {
                            const panel = node.querySelector('.cm-policy-preview').getBoundingClientRect();
                            const rail = document.querySelector('.cm-policy-sidebar')
                                .getBoundingClientRect();
                            return panel.right <= rail.left || panel.left >= rail.right
                                || panel.bottom <= rail.top || panel.top >= rail.bottom;
                        }""")
                    last = page.locator(".cm-policy-preview-list > div").last
                    last.scroll_into_view_if_needed()
                    expect(last).to_be_in_viewport()
                    assert page.locator(".cm-policy-preview-list").evaluate(
                        "node => node.scrollHeight <= node.clientHeight"
                    )
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    if width < 1440:
                        # The fixture's work titles are short; a long one is
                        # the title that a truncating cell would clip.
                        page.locator(
                            ".cm-policy-preview-list > div > span:nth-child(2) strong"
                        ).first.evaluate(
                            "node => { node.textContent = 'The Exceptionally Long "
                            "Collected Chronicle of the Synthetic Voyage, Volume "
                            "Twelve'; }"
                        )
                        editor = page.locator(".cm-policy-editor")
                        assert (
                            width,
                            theme,
                            editor.evaluate(_POLICY_CLIPPED),
                            page.evaluate(_POLICY_MID_WORD, _POLICY_LABELS),
                        ) == (width, theme, [], [])
                    collapse = result.get_by_role("button", name="Show fewer outcomes")
                    collapse.focus()
                    page.keyboard.press("Enter")
                    expect(
                        result.get_by_role("button", name=re.compile(r"Show .* more"))
                    ).to_be_focused()
                    page.get_by_role(
                        "button", name="Review activation", exact=True
                    ).scroll_into_view_if_needed()
                    expect(
                        page.get_by_role("button", name="Review activation", exact=True)
                    ).to_be_visible()
            page.emulate_media(reduced_motion="reduce")
            set_root_text(page, "200%")
            page.set_viewport_size({"width": 320, "height": 900})
            result.get_by_role("button", name=re.compile(r"Show .* more")).click()
            last = page.locator(".cm-policy-preview-list > div").last
            last.scroll_into_view_if_needed()
            expect(last).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            actions = page.locator(".cm-finish-actions")
            actions.scroll_into_view_if_needed()
            assert actions.evaluate("""node => {
                const [back, activate] = [...node.querySelectorAll('button')];
                const first = back.getBoundingClientRect();
                const second = activate.getBoundingClientRect();
                return first.bottom <= second.top && first.width >= node.clientWidth - 1
                    && second.width >= node.clientWidth - 1
                    && [back, activate].every(button =>
                        button.getBoundingClientRect().height <=
                        parseFloat(getComputedStyle(button).fontSize) * 5);
            }""")
            result.get_by_role("button", name="Show fewer outcomes").focus()
            page.keyboard.press("Enter")
            expect(
                result.get_by_role("button", name=re.compile(r"Show .* more"))
            ).to_be_focused()
            assert (
                page.evaluate(
                    "async () => (await fetch('/api/v1/metadata-policy/state')).json()"
                )
                == active_policy
            )
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("width", (1440, 390))
def test_packaged_description_cost_in_settings_and_cover_only_setup(width: int) -> None:
    from mechanical_librarian.domain.reviewer_access import OpenRouterTaskSelection

    access = ReviewerAccessView(
        revision=1,
        mapping=OpenRouterTaskSelection(engine="openrouter", model="openai/gpt-6-luna"),
        description_mapping=OpenRouterTaskSelection(
            engine="openrouter", model="openai/gpt-6-luna"
        ),
        openrouter_key_configured=False,
        codex=ReviewerEngineStatus(
            engine="codex", state="not_provisioned", reason="profile_not_provisioned"
        ),
        openrouter=ReviewerEngineStatus(engine="openrouter", state="unconfigured"),
    )
    assert [(row.task, row.per_1000_usd) for row in access.cost_evidence.models] == [
        ("description", "0.1713055000"),
        ("cover", "0.636415000"),
    ]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD))
        hash_path.chmod(0o600)
        certificate, key = _certificate(root)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True, viewport={"width": width, "height": 900}
            )
            with _closing_context(context):
                page = context.new_page()
                page.route(
                    "**/api/v1/reviewer-access",
                    lambda route: route.fulfill(json=access.model_dump(mode="json")),
                )
                with (
                    closing(ReservedPort()) as port,
                    _server(
                        database=root / "review.sqlite",
                        hash_path=hash_path,
                        certificate=certificate,
                        key=key,
                        port=port,
                    ) as (origin, _),
                ):
                    _login(page, origin)
                    page.goto(f"{origin}/settings?group=reviewer")
                    adviser = page.get_by_role(
                        "region", name="Optional description adviser"
                    )
                    expect(adviser).to_contain_text("Description advice: about $0.17.")
                    cover = page.get_by_role("region", name="Optional cover reviewer")
                    expect(cover).to_contain_text(
                        "Cover review: about $0.64, including about 2.7 cover images per book."
                    )
                    expect(adviser).not_to_contain_text("1790929488")
                    expect(adviser).not_to_contain_text("2026")
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    output = os.environ.get("ML_BROWSER_SCREENSHOT_DIR")
                    if output:
                        page.screenshot(
                            path=str(
                                Path(output) / f"reviewer-cost-settings-{width}.png"
                            ),
                            full_page=True,
                        )

                def setup_read(route: Any) -> None:
                    if urlparse(route.request.url).path.endswith("reviewer-access"):
                        route.fulfill(json=access.model_dump(mode="json"))
                    else:
                        route.fulfill(
                            json={
                                "schema_version": "setup-state-v4",
                                "revision": 6,
                                "csrf_token": "synthetic-csrf",
                                "completed_steps": [
                                    "claim",
                                    "connect",
                                    "providers",
                                    "libraries",
                                    "mounts",
                                ],
                                "connection": {
                                    "endpoint": "https://example.invalid",
                                    "name": "primary",
                                },
                                "providers": [],
                                "libraries": [],
                                "mounts": [],
                                "next_step": "reviewer",
                            }
                        )

                page.route("**/api/setup/**", setup_read)
                with (
                    closing(ReservedPort()) as port,
                    _setup_asset_server(certificate, key, port) as origin,
                ):
                    page.goto(origin)
                    expect(
                        page.get_by_text(
                            "About $0.6364 per 1,000 cover review requests (estimate)."
                        )
                    ).to_be_visible()
                    expect(
                        page.get_by_text(
                            "Optional description advice is configured separately in Settings → Reviewer after setup."
                        )
                    ).to_be_visible()
                    expect(page.get_by_text("$0.1713", exact=False)).to_have_count(0)
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                    if output:
                        page.screenshot(
                            path=str(Path(output) / f"reviewer-cost-setup-{width}.png"),
                            full_page=True,
                        )
            browser.close()


@pytest.mark.parametrize("attention", [False, True])
@pytest.mark.parametrize("transitions", [1, 2])
def test_fetching_transition_browse_and_manual_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attention: bool,
    transitions: int,
) -> None:
    from mechanical_librarian.adapters.persistence.provider_recovery import (
        SQLiteProviderRecoveryStore,
    )
    from mechanical_librarian.application.live_updates import (
        RESYNC,
        LiveUpdate,
        LiveUpdateChannel,
        LiveUpdateSubscription,
    )
    from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob
    from mechanical_librarian.interfaces.web import app as web_app
    from tests.support.owner_responsiveness import running_owner_fixture

    # Only the asset location differs from installation; the real composed app,
    # SQL projection, observation and recovery stores serve every browser request.
    monkeypatch.setattr(
        web_app,
        "ReactAssetLoader",
        lambda: ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
    )
    # Every new stream starts with a resync, and a resync refetches Library and
    # attention 120 ms later. When the 503 settles first, that refetch succeeds
    # and replaces the error before the assertion can see it (#1214). The
    # harness holds each connection's first resync until the manual recovery is
    # proven, then delivers it, so the stream contract is still exercised but
    # never decides which read the owner's control answers.
    held: list[tuple[LiveUpdateChannel, LiveUpdateSubscription, int]] = []
    connected = 0
    holding = True
    subscribe = LiveUpdateChannel.subscribe

    def holding_subscribe(
        channel: LiveUpdateChannel, last_sequence: int | None = None
    ) -> LiveUpdateSubscription:
        nonlocal connected
        subscription = subscribe(channel, last_sequence)
        connected += 1
        with channel.lock:
            sequence = subscription._pending.pop((RESYNC, None), None)
            if sequence is not None:
                if holding:
                    held.append((channel, subscription, sequence))
                else:
                    subscription._pending[RESYNC, None] = sequence
        return subscription

    def release_resyncs() -> None:
        nonlocal holding
        holding = False
        for channel, subscription, sequence in held:
            with channel.lock:
                subscription.offer(LiveUpdate(sequence, RESYNC))

    monkeypatch.setattr(LiveUpdateChannel, "subscribe", holding_subscribe)
    with running_owner_fixture(tmp_path, 10) as (fixture, origin):
        actor = SQLiteProviderRecoveryStore.open(tmp_path / "recovery.sqlite")
        try:
            for index in range(transitions):
                report = fixture.reports[f"work-{index:04d}"]
                actor.enqueue_jobs(
                    (
                        ProviderRecoveryJob(
                            f"google-{index}",
                            "google_books",
                            f"scope-{index}",
                            1,
                            f"member-{index}",
                            report.target.connection,
                            report.target.library,
                            report.target.item,
                            "file",
                            report.report_id,
                            "proposal",
                            report.report_id,
                            "case",
                            1,
                            "admitted",
                            '["work"]',
                        ),
                    ),
                    enqueued_at=3,
                )
            method = "read_review_attention" if attention else "read_review_library"
            original = getattr(fixture.browse, method)
            reads = 0
            armed = False

            def changing(*args):
                nonlocal reads
                result = original(*args)
                if armed:
                    reads += 1
                    if reads <= transitions:
                        assert (
                            actor.claim_one(
                                generation=1, now=7203, token=f"browser-{reads}"
                            )
                            is not None
                        )
                return result

            monkeypatch.setattr(fixture.browse, method, changing)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    page.goto(origin + "/login")
                    page.get_by_label("Password").fill("synthetic-owner")
                    # Works renders before its collection requests finish. Both
                    # initial reads must settle before they can consume churn.
                    with (
                        page.expect_response(
                            lambda response: (
                                urlparse(response.url).path == "/api/v1/library"
                            )
                        ) as initial_library,
                        page.expect_response(
                            lambda response: (
                                urlparse(response.url).path == "/api/v1/attention"
                            )
                        ) as initial_attention,
                    ):
                        page.get_by_role("button", name="Sign in").click()
                        page.get_by_role("heading", name="Works", exact=True).wait_for()
                    for initial in (initial_library, initial_attention):
                        initial.value.finished()
                        assert initial.value.status == 200
                    armed = True
                    streams = connected
                    route = "/library?attention=open" if attention else "/library"
                    with page.expect_response(
                        lambda response: (
                            urlparse(response.url).path
                            == ("/api/v1/attention" if attention else "/api/v1/library")
                        )
                    ) as pending:
                        page.goto(origin + route)
                    assert pending.value.status == (200 if transitions == 1 else 503)
                    assert reads == 2
                    # Look only once the armed page's stream is connected, which
                    # is the ordering that used to refetch the error away.
                    deadline = time.monotonic() + 5
                    while connected <= streams:
                        assert time.monotonic() < deadline, "no stream connected"
                        page.wait_for_timeout(20)
                    if transitions == 2:
                        surface = page.get_by_label(
                            "Attention queue" if attention else "Works", exact=True
                        )
                        retry = surface.get_by_role(
                            "button", name="Try again", exact=True
                        )
                        expect(retry).to_be_visible()
                        retry.focus()
                        expect(retry).to_be_focused()
                        with page.expect_response(
                            lambda response: (
                                (
                                    "/api/v1/attention"
                                    if attention
                                    else "/api/v1/library"
                                )
                                in response.url
                            )
                        ) as recovered:
                            page.keyboard.press("Enter")
                        assert recovered.value.status == 200
                        expect(retry).to_have_count(0)
                        # Both independent collection reads may observe the same
                        # churn; recover Works through its own manual control.
                        works_retry = page.get_by_role(
                            "region", name="Works", exact=True
                        ).get_by_role("button", name="Try again", exact=True)
                        if works_retry.count():
                            works_retry.click()
                    release_resyncs()
                    expect(
                        page.get_by_role("heading", name="Works", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_text("Synthetic title", exact=True).first
                    ).to_be_visible()
                finally:
                    browser.close()
                with fixture.application._browse_lifetime:
                    assert fixture.application._browse_lifetime.wait_for(
                        lambda: fixture.application._active_browses == 0, 5
                    )

                def assert_released():
                    assert fixture.browse._cover_read_observation is None
                    assert not fixture.browse._connection.in_transaction

                fixture.application.serialized(assert_released)
        finally:
            actor.close()


def test_completed_work_remains_browsable_during_explained_provider_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mechanical_librarian.interfaces.web import app as web_app
    from tests.support.owner_responsiveness import running_owner_fixture

    monkeypatch.setattr(
        web_app,
        "ReactAssetLoader",
        lambda: ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
    )
    with running_owner_fixture(tmp_path, 2) as (fixture, origin):
        for failure, delay in (
            ("burst_throttled", 100),
            ("quota_exhausted", 3600),
            ("transient", None),
        ):
            fixture.recovery.observe_failure(
                connection=failure,
                adapter="google_books",
                scope="books",
                generation=1,
                failure=failure,
                now=100,
                retry_after=delay,
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(origin + "/login")
                page.get_by_label("Password").fill("synthetic-owner")
                page.get_by_role("button", name="Sign in").click()
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                expect(
                    page.get_by_text("Synthetic title", exact=True).first
                ).to_be_visible()
                case_id = fixture.cases[0].review_case_id
                page.goto(origin + f"/review/{case_id}?subview=evidence")
                expect(page.get_by_role("main")).to_contain_text(
                    "Prepared synthetic work 0000"
                )
                page.goto(origin + "/activity")
                recovery = page.get_by_role("region", name="Provider recovery")
                expect(recovery).to_contain_text(
                    "Provider burst limit; Provider quota exhausted; Provider temporarily unavailable"
                )
                expect(recovery).to_contain_text("next eligible")
                expect(
                    recovery.get_by_role("button", name=re.compile("retry", re.I))
                ).to_have_count(0)
                for index in range(3):
                    token = f"recovered-{index}"
                    claimed = fixture.recovery.claim_one(
                        generation=1, now=8000, token=token
                    )
                    assert claimed is not None
                    fixture.recovery.complete_success(
                        token=token, recovery=claimed, now=8000
                    )
                page.reload()
                expect(recovery).not_to_contain_text("Provider burst limit")
                page.goto(origin + "/library")
                expect(
                    page.get_by_text("Synthetic title", exact=True).first
                ).to_be_visible()
            finally:
                browser.close()


def test_library_states_provider_throttling_and_idle_preparation_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real server's throttling projection and idle masthead (#1282)."""
    from mechanical_librarian.interfaces.web import app as web_app
    from tests.support.owner_responsiveness import running_owner_fixture
    from tests.unit.test_provider_recovery import _job

    monkeypatch.setattr(
        web_app,
        "ReactAssetLoader",
        lambda: ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
    )
    with running_owner_fixture(tmp_path, 2, prepare=True) as (fixture, origin):
        preparation = fixture.preparation
        assert preparation is not None
        now = int(time.time())
        fixture.recovery.observe_failure(
            connection="google",
            adapter="google_books",
            scope="books",
            generation=1,
            failure="burst_throttled",
            now=now,
            retry_after=3600,
        )
        fixture.recovery.enqueue_jobs(
            tuple(_job(f"member-{index}") for index in range(3)), enqueued_at=now
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(origin + "/login")
                page.get_by_label("Password").fill("synthetic-owner")
                page.get_by_role("button", name="Sign in").click()
                notice = page.get_by_role(
                    "region", name="Provider lookups waiting to retry"
                )
                entry = notice.get_by_role("listitem")
                expect(entry).to_have_count(1)
                expect(entry).to_contain_text(
                    "Google Books · books: It is limiting requests. "
                    "3 deferred lookups. Next automatic retry "
                )
                # File-managed settings: the instruction, never a command.
                expect(notice).to_contain_text(
                    "raise provider_recovery_generation by one there"
                )
                expect(notice.get_by_role("button")).to_have_count(0)
                # The instruction keeps its 0.7rem separation from the list
                # above it (#1282 R3 F3).
                separation = page.evaluate(_THROTTLE_FILE_SEPARATION)
                assert separation["gap"] >= 0.7 * separation["rem"] - 0.5, separation

                # One trigger whose name follows the state, located by place.
                trigger = page.locator(".preparation-activity > button")
                expect(trigger).to_have_text("Preparation idle")
                trigger.click()
                popover = page.get_by_role("region", name="Background activity")
                popover.get_by_role("button", name="Pause", exact=True).click()
                expect(trigger).to_have_text("Preparation paused")
                resume = popover.get_by_role("button", name="Resume", exact=True)
                expect(resume).to_be_focused()
                resume.click()
                expect(trigger).to_have_text("Preparation idle")
                popover.get_by_role("button", name="Scan now", exact=True).first.click()
                expect(trigger).to_have_text("Preparing libraries")
                # Idle had no progress at all; the press started a real scan.
                progress, paused = preparation.status()
                assert not paused
                assert progress[0] is not None
                assert progress[0].scan_id
                assert not page.evaluate(
                    "() => document.activeElement === document.body"
                )

                # Arriving at Settings by the nav link, focus stays
                # in the route after the loading state that held it unmounts
                # (#1282 R3 F7). The settings read is slowed so that state is
                # always there to unmount.
                def slow_settings(route: Route) -> None:
                    time.sleep(0.25)
                    route.continue_()

                page.route(
                    lambda url: urlparse(url).path == "/api/v1/settings",
                    slow_settings,
                )
                # This fixture's source has no remedy link; the platform
                # artifact test arrives by one.
                arrivals = []
                for attempt in range(5):
                    page.goto(origin + "/library?lens=all")
                    expect(entry).to_have_count(1)
                    page.get_by_role("navigation").get_by_role(
                        "link", name="Settings", exact=True
                    ).click()
                    page.locator("section.settings-route").wait_for()
                    page.evaluate(_TWO_FRAMES)
                    arrivals.append((attempt, page.evaluate(_FOCUSED)))
                assert {focused for _, focused in arrivals} == {
                    "SECTION.settings-route"
                }, arrivals
            finally:
                browser.close()


def test_masthead_and_run_line_agree_on_the_real_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run line is the masthead rows of its run, before and after a change.

    books-1 finished in an earlier run, so it is outside the current one, which
    covers books-0. A prepared books-0 work then fails after its cycle completed:
    the run line and the finished library's buckets move together, and the
    first-finish history does not (#1366).
    """
    from mechanical_librarian.interfaces.web import app as web_app
    from tests.support.owner_responsiveness import running_owner_fixture

    monkeypatch.setattr(
        web_app,
        "ReactAssetLoader",
        lambda: ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
    )
    with running_owner_fixture(tmp_path, 10, prepare=True) as (fixture, origin):

        def finished(library: str) -> dict[str, Any]:
            deadline = time.monotonic() + 30
            while True:
                view = fixture.application.preparation().to_data()
                row = next(
                    item
                    for item in cast(list[dict[str, Any]], view["libraries"])
                    if item["library"] == library
                )
                presentation = cast(dict[str, Any] | None, view["presentation"])
                if (
                    row["state"] in {"completed", "completed_with_failures"}
                    and presentation is not None
                    and presentation["state"]
                    in {"completed_clean", "completed_with_failures"}
                ):
                    return view
                assert time.monotonic() < deadline, view
                time.sleep(0.05)

        fixture.application.request_review_scan("primary", "books-1")
        earlier = finished("books-1")
        fixture.application.request_review_scan("primary", "books-0")
        finished("books-0")
        # One prepared case is re-projected as recorded with nothing to decide,
        # so the run line carries all three counts, including its longest word.
        # Each state change goes through the fixture repository's own scope:
        # a raw connection to the configured path leaves its rollback journal
        # beside the database, which the Linux binding refuses as recovery
        # state, so a pipeline lane opening a repository in that window would
        # stop for recovery (#1422). The scope writes through the private
        # binding, serialized with the pipeline.
        scope = fixture.repository._transaction("reproject_recorded_work")
        scope.begin()
        try:
            assert (
                fixture.repository._connection.execute(
                    "UPDATE review_preparation_work SET outcome='evidence_only',"
                    "proposal_set_id=NULL,review_case_id=NULL WHERE work_id=("
                    "SELECT work_id FROM review_preparation_work WHERE library_name="
                    "'books-0' AND state='complete' AND outcome='case' "
                    "ORDER BY work_id DESC LIMIT 1)"
                ).rowcount
                == 1
            )
        finally:
            scope.commit()
        current = fixture.application.preparation().to_data()
        presentation = cast(dict[str, Any], current["presentation"])
        assert presentation["run_id"] != cast(dict, earlier["presentation"])["run_id"]
        books = next(
            item for item in current["libraries"] if item["library"] == "books-0"
        )
        assert presentation["now"]["scan_ids"] == [books["scan_id"]]
        assert books["ready"] + books["recorded"] > 0

        def split(ready: int, recorded: int, failed: int) -> str:
            # Only the ready count stands at zero.
            return " · ".join(
                f"{count} {words}"
                for index, (count, words) in enumerate(
                    (
                        (ready, "ready to browse"),
                        (recorded, "recorded, nothing to decide"),
                        (failed, "could not finish"),
                    )
                )
                if index == 0 or count
            )

        def buckets(ready: int, recorded: int, failed: int) -> dict[str, str]:
            return {
                words: str(count)
                for words, count in (
                    ("Ready to browse", ready),
                    ("Recorded, nothing to decide", recorded),
                    ("Could not finish", failed),
                )
                if count
            }

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(origin + "/login")
                page.get_by_label("Password").fill("synthetic-owner")
                page.get_by_role("button", name="Sign in").click()
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()

                def observe() -> tuple[str, str, dict[str, str], str, str]:
                    summary = page.locator(".preparation-summary small")
                    expect(summary).to_contain_text("This run: ")
                    page.locator(".preparation-activity > button").click()
                    popover = page.get_by_role("region", name="Background activity")
                    rows = {
                        library: popover.locator("article").filter(
                            # The popover names a library as Settings does (#1286).
                            has=page.locator(
                                "strong",
                                has_text=re.compile(f"^Owner room {library}$"),
                            )
                        )
                        for library in ("books-0", "books-1")
                    }
                    expect(rows["books-1"]).to_contain_text("not in this run")
                    expect(rows["books-0"]).not_to_contain_text("not in this run")
                    stages = rows["books-0"].locator(".preparation-stages > div")
                    facts = (
                        cast(str, summary.text_content()),
                        cast(
                            str, rows["books-0"].locator("span").nth(1).text_content()
                        ),
                        {
                            cast(str, stage.locator("dt").text_content()): cast(
                                str, stage.locator("dd").text_content()
                            )
                            for stage in stages.all()
                        },
                        cast(
                            str, rows["books-1"].locator("span").nth(0).text_content()
                        ),
                    )
                    page.keyboard.press("Escape")
                    summary.click()
                    # The latched first finishes, which a later failure keeps.
                    outcomes = page.locator(
                        ".preparation-section-heading:has(#recent-completions-title) > span"
                    )
                    return (*facts, cast(str, outcomes.text_content()))

                page.goto(origin + "/library?lens=all")
                run_line, books_row, books_stages, other_state, prepared = observe()
                counts = (books["ready"], books["recorded"], books["blocked"])
                assert run_line == f"This run: {split(*counts)}"
                # A finished row's split is its buckets, in the same words.
                assert books_row == f"{books['checked']} of {books['total']} checked"
                assert books_stages == buckets(*counts)
                assert other_state.endswith(" · not in this run")
                # Enlarged on a narrow screen, the counts wrap between words and
                # never split one (#1013, #1366).
                page.set_viewport_size({"width": 320, "height": 900})
                set_root_text(page, "200%")
                for selector in (
                    ".preparation-run-state",
                    ".preparation-outcomes strong",
                ):
                    assert page.locator(selector).evaluate(_SPLIT_WORDS) == [], selector
                set_root_text(page, "")
                page.set_viewport_size({"width": 1440, "height": 1000})

                # A prepared work fails after its cycle completed, as a policy
                # restart that ends in failure would leave it.
                scope = fixture.repository._transaction("fail_completed_work")
                scope.begin()
                try:
                    changed = fixture.repository._connection.execute(
                        "UPDATE review_preparation_work SET stage='proposal',"
                        "state='failed',outcome=NULL,failure_code='failed' "
                        "WHERE work_id=(SELECT work_id FROM review_preparation_work "
                        "WHERE scan_id=? AND state='complete' ORDER BY work_id LIMIT 1)",
                        (books["scan_id"],),
                    ).rowcount
                finally:
                    scope.commit()
                assert changed == 1
                after = fixture.application.preparation().to_data()
                now = cast(dict[str, Any], after["presentation"])["now"]
                assert now["failed"] == books["blocked"] + 1
                assert now["ready"] + now["recorded"] == (
                    books["ready"] + books["recorded"] - 1
                )
                page.goto(origin + "/library?lens=all")
                run_line, books_row, books_stages, other_state, prepared_after = (
                    observe()
                )
                counts = (now["ready"], now["recorded"], now["failed"])
                assert run_line == f"This run: {split(*counts)}"
                assert books_stages == buckets(*counts)
                assert other_state.endswith(" · not in this run")
                assert prepared_after == prepared

                # In a wide bundled font, so whatever fonts the host has, at
                # every count length: a number and its first word move to the
                # next line together when they fit there, break at their space
                # only when they do not, and no number or word splits (#1366).
                page.set_viewport_size({"width": 320, "height": 900})
                set_root_text(page, "200%")
                font = Path(__file__).parent / "fonts/DejaVuSans-Bold-subset.ttf"
                assert page.evaluate(
                    _WIDE_BOLD_FONT, base64.b64encode(font.read_bytes()).decode()
                )
                for count in ("1", "12", "1234", "123456"):
                    # Only the rendered numbers are set, inside the page: this
                    # checks how the lines wrap, not what the server counts.
                    page.evaluate(_SET_COUNTS, count)
                    for selector in (
                        ".preparation-run-state",
                        ".preparation-outcomes strong",
                    ):
                        line = page.locator(selector)
                        assert line.evaluate(_SPLIT_WORDS) == [], (selector, count)
                        assert line.evaluate(_SPLIT_PAIRS) == [], (selector, count)
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    ), count
                for selector in (".preparation-run-state", ".preparation-outcomes"):
                    assert page.locator(f"{selector} .count-pair").count(), selector
            finally:
                browser.close()


# Every word in the element whose glyphs land on more than one line.
_SPLIT_WORDS = """element => {
  const split = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const match of node.data.matchAll(/[^\\s\\u00a0·]+/g)) {
      const range = document.createRange();
      range.setStart(node, match.index);
      range.setEnd(node, match.index + match[0].length);
      const tops = new Set([...range.getClientRects()].map(rect => Math.round(rect.top)));
      if (tops.size > 1) split.push(match[0]);
    }
  }
  return split;
}"""
# Every number and the word after it that fit on one line together but landed
# on two.
_SPLIT_PAIRS = """element => {
  const split = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const match of node.data.matchAll(/(\\d+)[ \\u00a0]([^\\s\\u00a0·]+)/gu)) {
      const top = (start, length) => {
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, start + length);
        return Math.round(range.getClientRects()[0].top);
      };
      const word = match.index + match[1].length + 1;
      if (top(match.index, match[1].length) === top(word, match[2].length)) continue;
      const probe = document.createElement('span');
      probe.style.whiteSpace = 'nowrap';
      probe.textContent = `${match[1]} ${match[2]}`;
      node.parentElement.append(probe);
      const width = probe.getBoundingClientRect().width;
      probe.remove();
      let block = node.parentElement;
      while (['inline', 'inline-block'].includes(getComputedStyle(block).display))
        block = block.parentElement;
      const style = getComputedStyle(block);
      const line = block.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      if (width <= line) split.push(match[0]);
    }
  }
  return split;
}"""
# Sets every count the run-state and outcomes lines render to `count`.
_SET_COUNTS = """count => {
  for (const line of document.querySelectorAll('.preparation-run-state, .preparation-outcomes strong')) {
    const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode())
      node.data = node.data.replace(/\\d+(?=[ \\u00a0])/gu, count);
  }
}"""
# DejaVu Sans Bold in place of the page's first font, for its bold text.
_WIDE_BOLD_FONT = """async font => {
  const bytes = Uint8Array.from(atob(font), character => character.charCodeAt(0));
  const face = new FontFace('Inter', bytes, { weight: '700' });
  document.fonts.add(await face.load());
  await document.fonts.ready;
  return document.fonts.check('700 16px Inter');
}"""
_THROTTLE_FILE_SEPARATION = """() => {
  const notice = document.querySelector('.provider-throttle-notice');
  const list = notice.querySelector('ul');
  const file = notice.querySelector('.provider-throttle-file');
  return {
    gap: file.getBoundingClientRect().top - list.getBoundingClientRect().bottom,
    rem: parseFloat(getComputedStyle(document.documentElement).fontSize),
  };
}"""
_TWO_FRAMES = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
# Holds the page's pending and new animation frames until `selector` takes
# focus, then runs them just after that focus and restores ordinary frames.
_LATE_FRAMES_ON_FOCUS = """selector => {
  const request = window.requestAnimationFrame, cancel = window.cancelAnimationFrame;
  const held = new Map();
  let next = 0;
  window.requestAnimationFrame = callback => { held.set(++next, callback); return next; };
  window.cancelAnimationFrame = id => { held.delete(id); };
  document.querySelector(selector).addEventListener('focus', () => queueMicrotask(() => {
    window.requestAnimationFrame = request;
    window.cancelAnimationFrame = cancel;
    for (const callback of [...held.values()]) callback(performance.now());
  }), { once: true });
}"""
_FOCUSED = """() => {
  const active = document.activeElement;
  return active === null ? 'null'
    : `${active.tagName}${active.className ? '.' + active.className : ''}`;
}"""


_NAMED_LIBRARIES = OwnerSettingsDocument.model_validate(
    {
        "schema_version": "owner-settings-v4",
        "revision": 1,
        "connections": [{"name": "primary", "endpoint": "https://library.invalid"}],
        "libraries": [
            {
                "name": "ebooks",
                "connection": "primary",
                "library_id": "library",
                "media_kind": "ebook",
                "display_name": "Reading Room",
            },
            {
                "name": "papers",
                "connection": "primary",
                "library_id": "library-2",
                "media_kind": "ebook",
                "display_name": "Paper Shelf",
            },
        ],
    }
)
_CALM = (
    "No intervention needed",
    "No action required",
    "Guarded work continues automatically",
)
_DIFFERENT_FIELDS = (
    "lapsed because this version of Mechanical Librarian can write a "
    "different set of fields."
)


def _consented(lapsed: tuple[str, ...], stored: dict[str, Any] | None = None):
    """Grant every destination for both libraries, the ones in `lapsed` by an
    earlier build that wrote one field fewer, so their envelope no longer
    matches what this version writes (#1370)."""
    from mechanical_librarian.adapters.persistence import field_effect_activity
    from mechanical_librarian.domain.field_effect import SUPPORTED_DESTINATIONS

    def seed(repository: Any) -> tuple[str, ...]:
        if stored is not None:
            stored["repository"] = repository
            stored["path"] = next(
                path
                for _, name, path in repository._connection.execute(
                    "PRAGMA database_list"
                )
                if name == "main"
            )
        for library in ("ebooks", "papers"):
            destinations = (
                SUPPORTED_DESTINATIONS - {"explicit"}
                if library in lapsed
                else SUPPORTED_DESTINATIONS
            )
            with patch.object(
                field_effect_activity, "SUPPORTED_DESTINATIONS", destinations
            ):
                repository.grant_field_effect_consents(
                    "primary", (library,), tuple(sorted(destinations)), 10
                )
        return ("ebooks", "papers")

    return seed


def _schedule_accepted_title(repository: Any, root: Path) -> None:
    from mechanical_librarian.domain.human_decision import HumanDecision
    from mechanical_librarian.domain.metadata_policy import human_review_destinations

    base = _report()
    report = MetadataReport.create(
        target=replace(base.target, item="item-2"),
        file=FileFacts(0, "c" * 64, "d" * 64),
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
    )
    repository.publish(report, lambda: True)
    policy = repository.current_resolved_metadata_policy("ebooks")
    proposal_set = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(item for item in case.proposals if item["destination"] == "title")
    repository.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="title",
            actor="owner",
            outcome="accept",
            selected_value=proposal["result"]["alternatives"][0]["value"],
            idempotency_key="browser-carried-consent-title",
            occurred_at=1_000,
        )
    )
    _, application = _consent_applications(repository, root, ("ebooks",))
    assert application.schedule() == 1


def test_an_activation_keeps_each_library_write_permission():
    """R3(a): Activate names the library that keeps permission, and after
    activating its consent is carried and both pages read writes enabled."""
    stored: dict[str, Any] = {}
    apps: list[Any] = []
    with _owner_page(
        settings_document=_NAMED_LIBRARIES,
        write_consent=_consented((), stored),
        on_app=apps.append,
    ) as (page, origin, _case_id):
        page.goto(f"{origin}/policy")
        page.get_by_role("region", name="Subtitle", exact=True).get_by_role(
            "button", name="Ask me"
        ).click()
        page.get_by_role("button", name="Validate and preview").click()
        page.get_by_role("button", name="Review activation").click()
        footer = page.get_by_text("Activate this reviewed draft?").locator("xpath=..")
        expect(footer).to_contain_text(
            "Write permission stays on for Reading Room and Paper Shelf."
        )
        page.get_by_role("button", name="Activate policy").click()
        page.get_by_text("Policy activated.").first.wait_for()
        with closing(sqlite3.connect(f"file:{stored['path']}?mode=ro", uri=True)) as db:
            rows = [
                json.loads(document)
                for (document,) in db.execute(
                    "SELECT document FROM field_effect_consents WHERE current=1"
                )
            ]
            (transition_id,) = db.execute(
                "SELECT transition_id FROM metadata_policy_transitions "
                "ORDER BY generation DESC LIMIT 1"
            ).fetchone()
        assert rows and {
            (row["basis"]["kind"], row["basis"]["transition_id"]) for row in rows
        } == {("policy_activation", transition_id)}
        # A title the owner accepts under the activated policy is scheduled on
        # the carried consent, with no new grant. Both pages read writes enabled
        # only while it stays scheduled, so the server's own worker, which polls
        # every two seconds and would claim it, is drained first (#1404).
        apps[0].state.review_application._field_application.begin_drain()
        _schedule_accepted_title(stored["repository"], Path(stored["path"]).parent)
        page.goto(f"{origin}/library")
        expect(page.locator(".cm-library-handoff")).to_contain_text(
            "Writes are enabled."
        )
        page.get_by_role("link", name="Activity", exact=True).click()
        page.locator(".cm-activity-calm").get_by_text("Writes enabled").wait_for()
        for phrase in ("Writes are off", "lapsed"):
            expect(page.locator("main")).not_to_contain_text(phrase)


@pytest.mark.parametrize("lapsed", [("ebooks", "papers"), ("papers",)])
def test_a_lapsed_write_permission_is_stated_by_its_settings_name(lapsed):
    """R3(b): every lapse, and a lapse beside a library that kept its
    permission, is stated with its reason and the library's display name,
    and neither page reads calm."""
    names = " and ".join(
        {"ebooks": "Reading Room", "papers": "Paper Shelf"}[name] for name in lapsed
    )
    sentence = f"Write permission for {names} {_DIFFERENT_FIELDS}"
    with _owner_page(
        settings_document=_NAMED_LIBRARIES, write_consent=_consented(lapsed)
    ) as (page, origin, _case_id):
        notice = page.locator(".cm-library-handoff")
        expect(notice).to_contain_text(sentence)
        expect(notice).not_to_contain_text("papers")
        if lapsed == ("papers",):
            # Writes are enabled only for the library that kept permission.
            expect(notice).to_contain_text("Writes are enabled for Reading Room.")
            expect(notice).to_contain_text(
                "Review current write permissions in Activity."
            )
        page.get_by_role("link", name="Activity", exact=True).click()
        page.get_by_text(sentence).first.wait_for()
        main = page.locator("main")
        for phrase in _CALM:
            expect(main).not_to_contain_text(phrase)
        if lapsed == ("papers",):
            page.get_by_text("Some writes are off", exact=True).wait_for()
            page.get_by_text("Action needed", exact=True).first.wait_for()
        else:
            page.get_by_text(
                "Nothing is written to your libraries until you give permission again."
            ).wait_for()


def test_works_no_provider_matched_have_their_own_lens_and_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1407: over the real composed app, SQL projection and recovery store,
    the Not matched lens counts and lists only caseless unmatched works, each
    with one reason, and promises a retry only where a job is scheduled."""
    from mechanical_librarian.domain.metadata_report import ProviderStatus
    from mechanical_librarian.interfaces.web import app as web_app
    from tests.support.owner_responsiveness import running_owner_fixture
    from tests.unit.test_not_matched_lens import (
        _ebook,
        _for,
        _publish,
        _recovery_job,
        _refused,
    )

    monkeypatch.setattr(
        web_app,
        "ReactAssetLoader",
        lambda: ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
    )
    rows = (
        "Ember Tide Protocol. Corin Halloway. Found, but not this work",
        "Work no-match. Author details unavailable. No provider matched",
        "Work rechecked. Author details unavailable. "
        "Providers unavailable; will be checked again",
        "Work unavailable. Author details unavailable. Providers unavailable",
    )
    with running_owner_fixture(tmp_path, 1) as (fixture, origin):
        rechecked = _ebook("rechecked", ProviderStatus.UNAVAILABLE)
        for report in (
            _ebook("no-match", ProviderStatus.NO_MATCH),
            _ebook("unavailable", ProviderStatus.NO_MATCH, ProviderStatus.UNAVAILABLE),
            rechecked,
            _for(_refused("refused"), "ebooks", "refused"),
        ):
            _publish(fixture.repository, report)
        fixture.recovery.enqueue_jobs(
            (_recovery_job("rechecked", rechecked.report_id),), enqueued_at=3
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(origin + "/login")
                page.get_by_label("Password").fill("synthetic-owner")
                page.get_by_role("button", name="Sign in").click()
                page.get_by_role("heading", name="Works", exact=True).wait_for()
                page.goto(origin + "/library?lens=all")
                lens = page.get_by_role("link", name="Not matched", exact=True)
                expect(lens).to_be_visible()
                # Counted beside the Works count, in the All count's words.
                expect(
                    page.get_by_role("status").filter(has_text="not matched")
                ).to_have_text("5 works · 4 not matched")
                with page.expect_response(_lens_read("not_matched")):
                    lens.click()
                expect(page).to_have_url(re.compile(r"lens=not_matched"))
                expect(lens).to_have_attribute("aria-current", "true")
                works = page.get_by_label("Works", exact=True)
                # A work's row is the button that carries its title.
                listed = works.get_by_role("button").filter(has=page.locator("strong"))
                expect(listed).to_have_count(len(rows))
                for name in rows:
                    expect(
                        works.get_by_role("button", name=name, exact=True)
                    ).to_be_visible()
                expect(works).not_to_contain_text("Prepared synthetic work 0000")
            finally:
                browser.close()
