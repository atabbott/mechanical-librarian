from __future__ import annotations

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
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import uvicorn
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from playwright.sync_api import (
    BrowserContext,
    Page,
    Request,
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
from mechanical_librarian.domain.setup_state import SetupStateDocument
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from mechanical_librarian.interfaces.web.setup import create_setup_app
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
    repository.enter_shadow_preview(1)
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
    language_fixture: tuple[str, ...] | None = None,
    language_current: str | None = None,
    language_audio: bool = False,
    language_destination: str = "automatic",
    language_option: str = "display_name",
    author_fixture: str | None = None,
    author_destination: str = "automatic",
    isbn_title_fixture: str | None = None,
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
    cohort: int = 0,
    preparation: bool = False,
    preparation_terminal: bool = False,
    audiobook: bool = False,
    audiobook_case: bool = False,
    chapter_runtime: bool = False,
    audiobook_tranche: bool = False,
    settings_document: OwnerSettingsDocument | None = _DEFAULT_SETTINGS_DOCUMENT,
    on_app: Callable[[Any], None] | None = None,
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
                }
                for index, summary in enumerate(
                    (
                        "A voyage reveals a hidden city.",
                        "An explorer crosses the sea and discovers an ancient city. "
                        * 12,
                    ),
                    1,
                )
            )
        )
        assert audiobook_review_scope(report) == "work"
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
            repository, report, genre_destination
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
            repository.enter_shadow_preview(1)
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
    language_activity = None
    if (
        isbn_fixture
        or isbn_title_fixture is not None
        or synopsis_fixture is not None
        or work_review_fixture
        or language_fixture is not None
        or genre_fixture is not None
        or series_fixture is not None
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
        policy_libraries=((report.target.library, None),) if refusal_fixture else (),
        field_application=(
            _BrowserActivityApplication()
            if not isbn_fixture
            and isbn_title_fixture is None
            and synopsis_fixture is None
            and not work_review_fixture
            and language_fixture is None
            and genre_fixture is None
            and series_fixture is None
            else None
        ),
        activity_projection=language_activity,
        react_asset_loader=ReactAssetLoader(Path(os.environ["ML_REACT_ASSET_DIR"])),
        cover_considerations=cover_considerations,
        settings=settings,
        reviewer_access=reviewer_access,
    )
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


def _login(page: Page, origin: str) -> None:
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page.goto(f"{origin}/login")
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


@contextmanager
def _setup_asset_server(
    certificate: Path, key: Path, port: ReservedPort
) -> Iterator[str]:
    class State:
        def read(self, _verifier: str) -> SetupStateDocument:
            return SetupStateDocument(
                schema_version="setup-state-v3",
                revision=1,
                bootstrap_verifier_digest="0" * 64,
            )

    origin = f"https://127.0.0.1:{port.number}"
    app = create_setup_app(
        cast(Any, object()), cast(Any, State()), "", canonical_origin=origin
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
                assert persisted_sessions() == [(1_100, 1_400)]
                policy = page.locator('a[href="/policy?section=identity"]')
                expect(policy).to_be_attached()

                def expire_policy_request(route) -> None:
                    now[0] = 1_400
                    route.continue_()

                page.route("**/api/v1/metadata-policy/state", expire_policy_request)
                policy.evaluate("(element) => element.click()")
                expect(page).to_have_url(f"{origin}/login")
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
                    "Sign in failed. Check the password and try again."
                )
                page.get_by_label("Password").fill(new_password)
                page.get_by_role("button", name="Sign in").click()
                page.wait_for_url(f"{origin}/library")
                expect(
                    page.get_by_role("heading", name="Works", exact=True)
                ).to_be_visible()
                context.close()

        assert errors == []


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
                page.get_by_role("button", name="Attention queue").click()
                attention_rail = page.locator(".attention-rail")
                attention_rail.wait_for()
                expect(attention_rail).to_be_focused()
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
                page.locator(".cm-policy-finish").get_by_text(
                    "Change any field to create a new draft \u2014 validation always "
                    "covers the whole policy.",
                    exact=True,
                ).wait_for()
                policy_chapters = page.get_by_role("navigation", name="Policy chapters")
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "0",
                    "0",
                ]
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
                    == "policy-curation-changes"
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
                for control in page.locator(
                    ".cm-policy-sidebar nav button, .cm-policy-posture button, "
                    ".cm-dropdown-trigger, .cm-policy-finish-actions button"
                ).all():
                    assert control.bounding_box()["height"] >= 44
                assert (
                    page.locator(".cm-policy-field").first.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                    )
                    == 1
                )
                assert (
                    page.locator(".cm-policy-finish").evaluate(
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
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "1",
                    "0",
                ]
                # An exact revert returns every surface to the clean state: no
                # chapter count, no dirty header, no validation action.
                genres.get_by_role("button", name=original_mode, exact=True).click()
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "0",
                    "0",
                ]
                page.locator(".cm-policy-finish").get_by_text(
                    "Draft matches the active policy", exact=True
                ).wait_for()
                page.get_by_text("Draft matches active policy.", exact=True).wait_for()
                page.locator(".cm-policy-route > header").get_by_text(
                    "Draft matches active policy", exact=True
                ).wait_for()
                assert page.get_by_role(
                    "button", name="Validate and preview"
                ).is_disabled()
                genres.get_by_role("button", name="Ignore").click()
                assert policy_chapters.locator("button b").all_inner_texts() == [
                    "0",
                    "0",
                    "1",
                    "0",
                ]

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
                            .cm-policy-editor, .cm-policy-finish,
                            .cm-policy-finish-actions, .cm-policy-finish-actions button`)]
                            .filter(node => node.scrollWidth > node.clientWidth &&
                            getComputedStyle(node).overflowX === 'visible')
                            .map(node => [node.tagName, node.className, node.clientWidth, node.scrollWidth])"""
                        )
                        == []
                    )

                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
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
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                page.get_by_role("button", name="Validate and preview").click()
                page.get_by_text(
                    "Impact preview is current. The active policy is unchanged."
                ).wait_for()
                review_activation = page.get_by_role("button", name="Review activation")
                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
                assert_text_zoom_policy_layout(430)
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                review_activation.click()
                page.get_by_text("Activate this reviewed draft?").wait_for()
                assert page.get_by_role("dialog").count() == 0
                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%'; }"
                )
                assert_text_zoom_policy_layout(430)
                page.locator("html").evaluate("node => { node.style.fontSize = ''; }")
                page.get_by_role("button", name="Back to preview").click()
                review_activation.wait_for()
                dark_mode = page.get_by_role("button", name="Use dark mode")
                dark_mode.click()
                page.get_by_role("button", name="Use light mode").wait_for()
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
                tray_box = page.locator(".cm-policy-finish").bounding_box()
                assert sidebar_box is not None and tray_box is not None
                sidebar_right = sidebar_box["x"] + sidebar_box["width"]
                assert tray_box["x"] < sidebar_right
                assert (tray_box["x"] + tray_box["width"]) > sidebar_right
                covered = """() => {
                    const rail = document.querySelector('.cm-policy-sidebar')
                    const side = rail.getBoundingClientRect()
                    const tray = document.querySelector(
                        '.cm-policy-finish').getBoundingClientRect()
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
                rows = page.get_by_role("combobox", name="Rows per activity page")
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
                        "button", name="Choose current value:", exact=False
                    ).count()
                    == 1
                )
                assert (
                    page.get_by_role(
                        "button", name="Choose proposed value", exact=False
                    ).count()
                    == 1
                )
                page.get_by_role("button", name="Use dark mode").click()
                page.get_by_role("button", name="Use light mode").wait_for()
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
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
                    "button", name="Choose current value:", exact=False
                )
                assert recorded_title.count() == 1
                expect(recorded_title).to_have_attribute("aria-pressed", "true")
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
                    "button", name="Choose current value:", exact=False
                )
                title_candidate = page.get_by_role(
                    "button", name="Choose proposed value", exact=False
                )
                expect(title_candidate).to_have_attribute("aria-pressed", "true")
                assert title_candidate.locator("xpath=..").evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(title_current).to_have_attribute("aria-pressed", "false")
                assert not (
                    title_current.locator("xpath=..").evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(title_candidate).to_have_attribute("aria-pressed", "true")
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
                expect(title_current).to_have_attribute("aria-pressed", "true")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                series_current = page.get_by_role(
                    "button", name="Choose current value:", exact=False
                )
                expect(series_current).to_have_attribute("aria-pressed", "true")
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
                        "button", name="Choose proposed value", exact=False
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
                expect(page.get_by_alt_text("Alternative 1 cover")).to_be_visible()
                current_cover = page.locator(".library-cover-card").filter(
                    has_text="Current cover"
                )
                automatic_cover = page.locator(".library-cover-card").filter(
                    has_text="Alternative 1 cover"
                )
                expect(automatic_cover).to_have_attribute("aria-pressed", "true")
                assert automatic_cover.evaluate(
                    "node => node.classList.contains('is-selected')"
                )
                expect(current_cover).to_have_attribute("aria-pressed", "false")
                assert not (
                    current_cover.evaluate(
                        "node => node.classList.contains('is-selected')"
                    )
                )
                page.reload()
                expect(
                    page.locator(".library-cover-card").filter(
                        has_text="Alternative 1 cover"
                    )
                ).to_have_attribute("aria-pressed", "true")
                assert (
                    page.locator(".library-cover-card")
                    .filter(has_text="Alternative 1 cover")
                    .evaluate("node => node.classList.contains('is-selected')")
                )
                # A recorded automatic choice is already selected: choosing it
                # again is a no-op. Explicitly retain the incumbent first, then
                # confirm a different cover as a fresh owner revision.
                automatic_cover.click()
                expect(
                    page.get_by_role("dialog", name="Confirm this cover")
                ).to_have_count(0)
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as kept:
                    current_cover.click()
                assert kept.value.status == 200, kept.value.text()
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
                expect(current_cover).to_have_attribute("aria-pressed", "true")
                with (
                    page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as decision_response,
                ):
                    page.locator(".library-cover-card").filter(
                        has_text="Alternative 1 cover"
                    ).click()
                    page.get_by_role(
                        "button", name="Confirm cover choice", exact=True
                    ).click()
                response = decision_response.value
                assert 200 == response.status, f"decision response: {response.text()}"
                page.get_by_role("heading", name="Review complete").wait_for()
                page.goto(f"{origin}/review/{case_id}?field=cover&subview=history")
                page.get_by_role("heading", name="History").wait_for()
                selected_history = page.get_by_role(
                    "heading", name="Cover selected"
                ).locator("..")
                selected_history.get_by_text("Visual review", exact=False).wait_for()
                selected_history.get_by_text(
                    "Owner-confirmed choice: accept.", exact=False
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
                expect(page.get_by_alt_text("Alternative 1 cover")).to_be_visible()
                comparison.get_by_text(
                    "1 unavailable candidate", exact=False
                ).wait_for()
                assert page.locator(".library-cover-card").count() == 1
                candidate = page.locator(".library-cover-card").filter(
                    has_text="Alternative 1 cover"
                )
                expect(candidate).to_have_attribute("aria-pressed", "false")
                with page.expect_response(
                    lambda value: value.url.endswith("/decisions")
                ) as decision_response:
                    candidate.focus()
                    page.keyboard.press("Enter")
                    page.get_by_role(
                        "button", name="Confirm cover choice", exact=True
                    ).click()
                assert decision_response.value.status == 200
                page.get_by_role("heading", name="Review complete").wait_for()

                page.goto(f"{origin}/review/{case_id}?field=cover&subview=history")
                selected = page.get_by_role("heading", name="Cover selected").locator(
                    ".."
                )
                selected.get_by_text(
                    "Owner-confirmed choice: accept.", exact=False
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
                    has_text="Alternative 1 cover"
                ).click()
                page.get_by_role(
                    "button", name="Confirm cover choice", exact=True
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
                    "button", name="Evidence for Alternative 1 cover"
                ).click()
                disclosure = current_comparison.get_by_role(
                    "button", name="Evidence for Alternative 1 cover", exact=True
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
                    "button", name="Choose Alternative 1 cover", exact=True
                ).click()
                page.get_by_role(
                    "button", name="Confirm cover choice", exact=True
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
                    has_text="Alternative 1 cover"
                ).click()
                page.get_by_role(
                    "button", name="Confirm cover choice", exact=True
                ).click()

                stale = page.get_by_text(
                    "This cover comparison changed before your choice was recorded. "
                    "No cover was changed."
                )
                expect(stale).to_be_visible()
                assert case_id in page.url
                assert decision_statuses == [409]

                current_choice = page.locator(".library-cover-card").filter(
                    has_text="Alternative 1 cover"
                )
                current_choice.focus()
                page.keyboard.press("Enter")
                page.get_by_role(
                    "button", name="Confirm cover choice", exact=True
                ).click()
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
                page.locator(".recorded-field-observations").wait_for()
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
                    page.evaluate(
                        "size => document.documentElement.style.fontSize = size", size
                    )
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
                        page.get_by_role("button", name="Choose current description")
                    ).to_have_attribute("aria-pressed", "true")
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
                    page.locator(".library-cover-card[aria-pressed='true']").count()
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
                    "button", name="Choose Alternative 1 cover", exact=True
                ).click()
                page.get_by_role(
                    "button", name="Confirm cover choice", exact=True
                ).click()
                expect(
                    page.get_by_role("heading", name="Review complete")
                ).to_be_visible()
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
                    page.locator(".library-cover-card[aria-pressed='true']").count()
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
                # Below 900px the current cover joins the same scrollable row
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
                    page.evaluate(
                        "size => document.documentElement.style.fontSize = size", size
                    )
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
                        "window.matchMedia('(max-width: 700px)').matches === (width <= 700)",
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
                page.evaluate("document.documentElement.style.fontSize = ''")
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
                page.reload()
                page.get_by_text("Image unavailable").first.wait_for()
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
                    if "/api/v1/library" in response.url
                    else decision_responses.append(response.url)
                    if response.url.endswith("/decisions")
                    else None
                ),
            )

            def capture_request(request: Request) -> None:
                if request.url.endswith("/decisions"):
                    decision_requests.append(request.headers.get("idempotency-key"))
                elif "/api/v1/library" in request.url:
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
                        "button", name="Choose current value:", exact=False
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

                def release_aggregate_request(
                    expected: int, *, previous: str, committed: str
                ) -> None:
                    deadline = time.monotonic() + 5
                    while (
                        len(held_library_requests) < expected
                        or len(library_request_fields) < issued + expected
                    ) and time.monotonic() < deadline:
                        page.wait_for_timeout(10)
                    assert len(held_library_requests) == expected, held_library_requests
                    assert len(library_request_fields) == issued + expected
                    field = library_request_fields[issued + expected - 1]
                    assert field == committed
                    assert field != previous
                    held_library_requests[expected - 1].continue_()

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
                    "button", name="Choose current value:", exact=False
                ).first
                expect(recorded).to_have_attribute("aria-pressed", "true")
                assert (
                    page.get_by_role(
                        "button", name="Take no action", exact=True
                    ).count()
                    == 0
                )
                assert page.get_by_role(
                    "button", name="Choose proposed value", exact=False
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
                assert len(library_requests[issued:]) == 2, library_requests[issued:]

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
                # Same-work manual field navigation retains the real browser
                # scroll position after the router's restoration frame (#1070).
                for width in (390, 320):
                    page.set_viewport_size({"width": width, "height": 800})
                    for destination, keyboard in (("series", False), ("title", True)):
                        button = page.get_by_role(
                            "button",
                            name=re.compile(f"^{destination.title()} Needs review"),
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
                    "button", name="Choose proposed value", exact=False
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
                          const visibleTop = (masthead?.getBoundingClientRect().bottom ?? 0) + 8
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
                      const mastheadBottom = masthead?.getBoundingClientRect().bottom ?? 0
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
                expect(row).to_contain_text("Recorded evidence available")
                assert page.locator(".library-work-row.is-terminal").count() == 0

                row.click()

                evidence = page.get_by_role("region", name="Recorded evidence")
                expect(evidence).to_be_visible()
                expect(evidence).to_contain_text("Recorded Narrator")
                expect(evidence).to_contain_text("B0CW1KL2QD")
                expect(evidence).to_contain_text("43200.000")
                expect(evidence).to_contain_text("Embedded cover art is present")
                page.get_by_text(re.compile(r"Chapters \(1\)")).click()
                expect(evidence).to_contain_text("Chapter One")
                # What is wrong with the media, in its own named region: the
                # observation, the rule it broke, and what it was read from.
                health = page.get_by_role("region", name="Media health")
                expect(health).to_be_visible()
                expect(health).to_contain_text("Runtime short of provider")
                expect(health).to_contain_text("43200 seconds of local audio")
                expect(health).to_contain_text("within 90 seconds")
                expect(health).to_contain_text("audible-product-B08G9PRS1K")
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
                page.goto(f"{origin}/library?lens=all&q=Project Hail Mary")
                row = page.get_by_role(
                    "button", name=re.compile("^Project Hail Mary")
                ).first
                expect(row).to_be_visible()
                # A recording is an ordinary review row, not a failure card,
                # and no automatic authority reaches it, so both fields that
                # still admit a choice are the owner's to answer.
                expect(row).to_contain_text("2 fields need attention")
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
                    expect(basis).to_contain_text("audible-product-B08G9PRS1K")
                    source = basis.locator("summary")
                    desktop_bounds = source.bounding_box()
                    assert desktop_bounds is not None and desktop_bounds["height"] < 44
                    page.set_viewport_size({"width": 390, "height": 844})
                    bounds = source.bounding_box()
                    assert bounds is not None and bounds["height"] >= 44
                    source.focus()
                    source.press("Space")
                    expect(basis.locator("details")).not_to_have_attribute("open", "")
                    source.press("Space")
                    expect(basis.locator("details")).to_have_attribute("open", "")
                    page.get_by_role("link", name="Evidence", exact=True).click()
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
                assert "Asin" in labels

                page.get_by_role("button", name=re.compile("^Asin")).first.click()
                accept = page.get_by_role(
                    "button", name=re.compile("^Choose proposed value: ")
                ).first
                expect(accept).to_be_visible()
                accept.click()

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
                page.goto(f"{origin}/library?lens=all&q=Project Hail Mary")
                row = page.get_by_role(
                    "button", name=re.compile("^Project Hail Mary")
                ).first
                expect(row).to_be_visible()
                row.click()

                page.get_by_role("button", name=re.compile("^Asin")).first.click()
                choice = page.locator(".direct-choice").first
                expect(choice).to_be_visible()
                expect(choice).to_contain_text("B08G9PRS1K")
                expect(choice).to_contain_text(
                    "Selected from Audible and Audnexus to fill an empty ASIN field "
                    "under a qualified automatic policy; the runtime gate "
                    "corroborated that this recording is that product."
                )

                # Narrators is named by the same two authorities and has no
                # record, so it is still a question the owner answers.
                page.get_by_role("button", name=re.compile("^Narrators")).first.click()
                narrators = page.locator(".direct-choice").first
                expect(narrators).to_be_visible()
                expect(narrators).to_contain_text("Ray Porter")
                assert (
                    0 == page.get_by_text("under a qualified automatic policy").count()
                )
                expect(
                    page.get_by_role(
                        "button", name=re.compile("^Choose proposed value: ")
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
                    "button", name="Choose current value:", exact=False
                ).wait_for()
                expect(page.locator(".selected-work-header")).to_be_focused()
                page.unroute("**/api/v1/review-cases/**", hold_selected_detail)
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
                            schema_version="setup-state-v3",
                            revision=1,
                            bootstrap_verifier_digest="0" * 64,
                        )

                setup_client = TestClient(
                    create_setup_app(cast(Any, object()), cast(Any, State()), ""),
                    base_url="https://testserver",
                )

                secured_setup_asset = partial(_serve_setup_asset, setup_client)

                page.route(setup_url, secured_setup_asset)
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
                            json={"message": "Preparation continues in the workspace."}
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

                def preparation_api(
                    route,
                    _request,
                    state_ref=presentation_state,
                    expected_run_id=run_id,
                    requests=preparation_requests,
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
                                            "name": "Synthetic active title",
                                            "stage": "Checking metadata",
                                        }
                                    ]
                                ),
                                "active_overflow": 0,
                                "recent_completions": [],
                                "throughput_per_minute": 0,
                                "started_at": 100,
                                "updated_at": 101,
                                "completed_at": 101 if terminal else None,
                                "total": 4,
                                "dismissible": terminal,
                            },
                        }
                    )

                page.route("**/api/v1/review-preparation", preparation_api)
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
                expect(page.locator(".workspace-detail")).to_have_attribute(
                    "aria-live", "polite"
                )
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
                        "0 ready · 0 could not finish · 1 active",
                    ),
                    (
                        "completed_clean",
                        "Preparation complete",
                        "4 ready · No failures",
                    ),
                    (
                        "completed_with_failures",
                        "Preparation completed with failures",
                        "3 ready · 1 could not finish",
                    ),
                )
                for state, title, copy in summary_cases:
                    presentation_state["value"] = state
                    page.reload()
                    summary = page.locator(".preparation-summary")
                    expect(summary).to_contain_text(title)
                    expect(summary).to_contain_text(copy)
                    expect(page.get_by_role("heading", name="Works")).to_be_visible()
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

            def setup_api(route) -> None:
                path = urlparse(route.request.url).path
                method = route.request.method
                if path == "/api/setup/claim":
                    route.fulfill(
                        json={"claimed": False, "revision": 1, "nonce": "n" * 32}
                    )
                    return
                if path == "/api/setup/session":
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
                    page.get_by_text("Between 1 and 1,024 bytes", exact=False)
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
                page.get_by_role("button", name="Create owner access").click()
                alert = page.get_by_role("alert")
                expect(alert).to_be_visible()
                confirmation = page.get_by_label("Confirm owner password", exact=True)
                expect(confirmation).to_have_attribute("aria-invalid", "true")
                expect(confirmation).to_have_attribute(
                    "aria-describedby", "setup-error"
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
                # the password.
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
                expect(password).to_have_attribute("aria-describedby", "setup-error")
                assert confirmation.get_attribute("aria-describedby") is None
                password.fill(_PASSWORD)
                expect(alert).to_be_empty()
                assert password.get_attribute("aria-invalid") is None
                confirmation.fill(_PASSWORD)
                page.get_by_role("button", name="Create owner access").click()
                expect(
                    page.get_by_role("heading", name="Choose metadata providers")
                ).to_be_visible()
                for provider in ("Open Library", "Hardcover", "Google Books"):
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
                # ADR-0061 admits the four audiobook providers keyless: the
                # step offers them with no credential control and no account
                # link to obtain one (#749).
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
                            page.get_by_role("link", name="Get Open Library access")
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
                expect(page.get_by_label("Library name").first).to_be_hidden()
                page.get_by_role("button", name="Change name").first.click()
                display_name = page.get_by_label("Library name").first
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
                page.evaluate("document.documentElement.style.fontSize = '200%'")
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.evaluate("document.documentElement.style.fontSize = ''")
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
                page.evaluate("document.documentElement.style.fontSize = '200%'")
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                # The stacking breakpoint is stated in rem, so 200% browser
                # text moves it and the step card keeps a usable width at
                # every protected viewport instead of collapsing beside the
                # aside.
                page.evaluate("document.documentElement.style.fontSize = '200%'")
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
                page.evaluate("document.documentElement.style.fontSize = ''")
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
                page.evaluate("document.documentElement.style.fontSize = '200%'")
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
                page.evaluate("document.documentElement.style.fontSize = ''")
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
                expect(page.get_by_text("Recently ready · Ready")).to_be_visible()
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
                    page.get_by_text(
                        "No enabled metadata providers are available to associate."
                    ).first
                ).to_be_visible()
                expect(
                    page.get_by_role("group", name=re.compile(r"^Providers for"))
                ).to_have_count(0)
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
                          const save = [...document.querySelectorAll('button')]
                            .find((element) => element.textContent === 'Save settings');
                          const saveRect = save.getBoundingClientRect();
                          const overlapElements = [...document.querySelectorAll('.settings-fields > *, .settings-field-row, .settings-record-meta')]
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
                          const shellActions = document.querySelector('.shell-actions')?.getBoundingClientRect();
                          return {
                            overlaps: overlapElements.length,
                            overlapDiagnostic: {
                              group: new URL(location.href).searchParams.get('group'),
                              viewport: {width: innerWidth, height: innerHeight},
                              theme: document.documentElement.dataset.theme ?? getComputedStyle(document.documentElement).colorScheme,
                              save: saveRect.toJSON(),
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
                            disabledOpacity: Number.parseFloat(getComputedStyle(save).opacity),
                            minLabelWidth: labels.length ? Math.min(...labels) : null,
                            brandContained: masthead && brandMark
                              ? brandMark.left >= 0 && brandMark.right <= innerWidth && brandMark.top >= masthead.top && brandMark.bottom <= masthead.bottom
                              : false,
                            brandContentContained: brandMarkElement
                              ? brandMarkElement.scrollWidth <= brandMarkElement.clientWidth
                                && brandMarkElement.scrollHeight <= brandMarkElement.clientHeight
                              : false,
                            brandActionsOverlap: brandMark && shellActions
                              ? Math.min(brandMark.right, shellActions.right) > Math.max(brandMark.left, shellActions.left)
                                && Math.min(brandMark.bottom, shellActions.bottom) > Math.max(brandMark.top, shellActions.top)
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
                    assert layout["disabledOpacity"] < 1
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
                        assert_settings_layout(sidebar_visible=width > 700)
                        if width <= 390:
                            expect(
                                page.locator("label.settings-mobile-group select")
                            ).to_be_visible()
                        else:
                            expect(page.get_by_label("Settings groups")).to_be_visible()

                    group_labels = {
                        "connection": "Connection",
                        "providers": "Providers",
                        "libraries": "Libraries",
                        "automation": "Automation & limits",
                        "reviewer": "Reviewer",
                        "server": "Server",
                        "appearance": "Appearance",
                    }
                    for width, height in ((884, 1000), (390, 844), (320, 800)):
                        page.set_viewport_size({"width": width, "height": height})
                        for group, label in group_labels.items():
                            if width <= 390:
                                page.locator(
                                    "label.settings-mobile-group select"
                                ).select_option(group)
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
                            assert_settings_layout(sidebar_visible=width > 700)

                page.set_viewport_size({"width": 884, "height": 1000})
                page.evaluate("document.documentElement.style.fontSize = '200%'")
                for label in group_labels.values():
                    page.locator(".settings-sidebar button", has_text=label).click()
                    expect(
                        page.get_by_role("heading", name=label, exact=True)
                    ).to_be_visible()
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    assert_settings_layout(sidebar_visible=True, zoomed=True)
                page.evaluate("document.documentElement.style.fontSize = ''")

                for theme in ("light", "dark"):
                    requested = "Use dark mode" if theme == "dark" else "Use light mode"
                    toggle = page.get_by_role("button", name=requested)
                    if toggle.count():
                        toggle.click()
                    for width, height in ((390, 844), (320, 800)):
                        page.set_viewport_size({"width": width, "height": height})
                        page.locator(
                            "label.settings-mobile-group select"
                        ).select_option("libraries")
                        page.evaluate(
                            "document.documentElement.style.fontSize = '200%'"
                        )
                        expect(
                            page.get_by_role("heading", name="Libraries", exact=True)
                        ).to_be_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        assert_settings_layout(sidebar_visible=False, zoomed=True)
                        expect(
                            page.locator("label.settings-mobile-group select")
                        ).to_be_visible()
                        page.evaluate("document.documentElement.style.fontSize = ''")

                page.set_viewport_size({"width": 320, "height": 800})
                compact_group = page.locator("label.settings-mobile-group select")
                compact_group.select_option("providers")
                expect(
                    page.get_by_role("heading", name="Add a metadata provider")
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
                inventaire_enabled = page.get_by_role(
                    "checkbox", name="Provider state Enable inventaire"
                )
                expect(inventaire_enabled).to_be_checked()
                inventaire_enabled.click()
                expect(inventaire_enabled).not_to_be_checked()
                inventaire_enabled.click()
                expect(inventaire_enabled).to_be_checked()
                page.get_by_role("button", name="Save settings").click()
                expect(page.get_by_text("Settings saved and in use.")).to_be_visible()
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
                    page.get_by_role(
                        "checkbox", name="Cover evidence Use audnexus covers"
                    )
                ).to_have_count(0)
                audible_enabled = page.get_by_role(
                    "checkbox", name="Provider state Enable audible catalog"
                )
                audible_cover = page.get_by_role(
                    "checkbox", name="Cover evidence Use audible catalog covers"
                )
                expect(audible_enabled).not_to_be_checked()
                expect(audible_cover).to_be_disabled()
                expect(audible_cover).not_to_be_checked()
                audible_enabled.click()
                expect(audible_cover).to_be_enabled()
                audible_cover.focus()
                page.keyboard.press("Space")
                expect(audible_cover).to_be_checked()
                # Turning the provider off turns the evidence off with it and
                # puts the control out of reach again.
                audible_enabled.click()
                expect(audible_cover).not_to_be_checked()
                expect(audible_cover).to_be_disabled()
                audible_enabled.click()
                audible_cover.focus()
                page.keyboard.press("Space")
                expect(audible_cover).to_be_checked()
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
                compact_group = page.locator("label.settings-mobile-group select")
                compact_group.select_option("providers")
                expect(
                    page.get_by_role(
                        "checkbox", name="Cover evidence Use audible catalog covers"
                    )
                ).to_be_checked()
                expect(page.get_by_text("Applies after restart")).to_have_count(0)
                assert not errors

                compact_group.select_option("connection")
                expect(page.get_by_text("Configured · ····cret")).to_be_visible()
                page.get_by_role("button", name="Clear primary access key").click()
                expect(
                    page.get_by_role("button", name="Confirm clear primary access key")
                ).to_be_visible()
                page.get_by_role("button", name="Keep primary access key").click()
                page.get_by_role("button", name="Replace primary access key").click()
                expect(
                    page.get_by_label("Replacement primary access key")
                ).to_be_visible()

                compact_group.select_option("automation")
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
                expect(
                    page.get_by_role("button", name="Save settings")
                ).to_be_disabled()
                timeout_input.fill("30")

                compact_group.select_option("reviewer")
                reviewer_choice = page.get_by_role(
                    "button", name="Use dots-studio/dots-3-note-preview:free"
                )
                reviewer_metrics = reviewer_choice.evaluate(
                    "element => element.getBoundingClientRect().toJSON()"
                )
                assert reviewer_metrics["height"] >= 44
                expect(
                    page.get_by_text("Recommended advisory cover reviewer:")
                ).to_be_visible()
                expect(
                    page.get_by_role(
                        "button", name="Use GPT-6 Luna (openai/gpt-6-luna)"
                    )
                ).to_be_visible()
                expect(
                    page.get_by_text("Catalogue-capability-only free option:")
                ).to_be_visible()
                expect(
                    page.get_by_text(
                        "Less than 1 GiB of memory headroom is available. "
                        "Free memory or raise the container limit."
                    )
                ).to_be_visible()
                compact_group.select_option("server")
                diagnostic_checkbox = page.get_by_role(
                    "checkbox", name="Record diagnostic timings"
                )
                diagnostic_metrics = diagnostic_checkbox.evaluate(
                    """element => ({
                      glyph: element.getBoundingClientRect().toJSON(),
                      target: element.closest('label').getBoundingClientRect().toJSON(),
                    })"""
                )
                assert diagnostic_metrics["glyph"]["width"] <= 20
                assert diagnostic_metrics["glyph"]["height"] <= 20
                assert diagnostic_metrics["target"]["height"] >= 44

                compact_group.select_option("connection")
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
                expect(page.get_by_text("Unsaved settings changes.")).to_be_visible()
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
                expect(
                    page.get_by_role("button", name="Save settings")
                ).to_be_disabled()
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
                stale_alert = page.get_by_text("Settings changed elsewhere.")
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
                page.locator("label.settings-mobile-group select").select_option(
                    "providers"
                )
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
                expect(
                    page.get_by_text(
                        "The sources disagree about the work or title; choose the title yourself"
                        if mode == "conflict"
                        else "ISBN and author evidence support this title",
                        exact=True,
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
                    "button", name="Choose proposed value", exact=False
                ).first
                evidence = page.get_by_role(
                    "button", name=f"Evidence for Alternative 1: {expected}", exact=True
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
                        expect(proposed).to_have_attribute("aria-pressed", "false")
                        evidence.focus()
                        page.keyboard.press("Enter")
                        expect(evidence).to_have_attribute("aria-expanded", "false")
                        expect(panel).to_have_count(0)
                        expect(proposed).to_have_attribute("aria-pressed", "false")
                with page.expect_response(
                    lambda response: response.url.endswith("/decisions")
                ) as accepted:
                    proposed.click()
                assert accepted.value.status == 200
                page.goto(
                    f"{origin}/library?lens=all&preview={case_id}&field={component}"
                )
                expect(proposed).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(page.get_by_text(expected, exact=True).first).to_be_visible()
                expect(proposed).to_have_attribute("aria-pressed", "true")
                current = page.get_by_role(
                    "button", name="Choose current value:", exact=False
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
                expect(current).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(current).to_have_attribute("aria-pressed", "true")
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
                    "button", name="Choose current value:", exact=False
                ).click()
                retry = page.get_by_role("button", name="Try the same decision again")
                expect(retry).to_be_visible()
                expect(retry).to_be_focused()
                retry.click()
                page.get_by_role(
                    "heading", name="Which series should this work use?"
                ).wait_for()
                assert len(commands) == 2 and commands[0] == commands[1]
                assert commands[0].startswith("decision-")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                expect(
                    page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    )
                ).to_have_attribute("aria-pressed", "true")
                page.unroute("**/decisions")
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=series")
                page.route("**/decisions", lambda route: route.abort())
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).click()
                expect(
                    page.get_by_text(
                        "The response was lost. The current case was reloaded before any retry."
                    )
                ).to_be_visible()
                page.unroute("**/decisions")
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
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
                page.get_by_role("button", name="Attention queue", exact=False).click()
                queue = page.get_by_label("Attention queue", exact=True)
                queued = queue.locator(".attention-queue-item")
                expect(queued.first).to_be_visible()
                initial_count = queued.count()
                initial_titles = queued.locator(
                    ".attention-queue-copy > strong"
                ).all_text_contents()
                page.get_by_role(
                    "button", name="Load more attention", exact=True
                ).click()
                later = queued.nth(initial_count)
                expect(later).to_be_visible()
                later_title = later.locator(
                    ".attention-queue-copy > strong"
                ).inner_text()
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
                    page.get_by_role("button", name="Choose current value", exact=False)
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
                reason = "A contributing ISBN lookup did not supply usable evidence."
                expect(page.get_by_text(reason)).to_be_visible()
                expect(
                    page.get_by_role(
                        "button", name="Choose proposed value", exact=False
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
                page.get_by_role("button", name="Clear", exact=True).click()
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
                    page.get_by_role(
                        "button", name="Choose proposed value", exact=False
                    )
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
                    "button", name="Choose current value: Existing description."
                )
                provider = chooser.get_by_role(
                    "button", name="Choose proposed value: A qualified description."
                )
                expect(current).to_be_enabled()
                expect(provider).to_be_enabled()
                select = chooser.get_by_role(
                    "button", name=re.compile("^Choose advised value:")
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
                        expect(select).to_have_attribute("aria-pressed", "true")
                        expect(current).to_have_attribute("aria-pressed", "false")
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
                        expect(select).to_have_attribute("aria-pressed", "true")
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
                        "button", name=re.compile("Choose advised|Retry description")
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
                        "button", name=re.compile("Choose advised|Retry description")
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
                            "button", name="Choose current value: Existing description."
                        )
                    ).to_be_enabled()
                expect(
                    page.get_by_role(
                        "button", name=re.compile("^Choose advised value:")
                    )
                ).to_have_count(0)
                if state == "no_alternatives":
                    expect(
                        page.get_by_role(
                            "button", name=re.compile("^Choose proposed value:")
                        )
                    ).to_have_count(0)
                    expect(
                        page.get_by_role("button", name="Retry description advice")
                    ).to_have_count(0)
                else:
                    expect(
                        page.get_by_role(
                            "button",
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
                    expect(adviser).to_contain_text("model_unavailable")
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
                        "Selected: synthetic/description-b. Advisory availability: usable."
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
                        page.get_by_role("textbox", name="Model ID", exact=True)
                    ).to_have_attribute("placeholder", "synthetic/cover-a")
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
                        page.get_by_role("textbox", name="Model ID", exact=True)
                    ).to_have_attribute("placeholder", "synthetic/cover-a")
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
                        "button", name=re.compile("^Choose advised value:")
                    )
                    expect(choices).to_have_count(
                        2 if state == "retry_succeeded" else 1
                    )
                    expect(choices.first).to_have_attribute("aria-pressed", "true")
                    expect(choices.first).to_be_disabled()
                    expect(
                        page.get_by_text("Choice recorded.", exact=True)
                    ).to_be_visible()
                    expect(
                        page.get_by_text(
                            "Advised · Authored content · Recorded choice", exact=True
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
                        "button",
                        name="Choose advised value: A new authored description from the explicit retry.",
                    )
                    expect(latest).to_have_attribute("aria-pressed", "false")
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
                    expect(latest).to_have_attribute("aria-pressed", "true")
                    expect(
                        page.get_by_role(
                            "button", name=re.compile("^Choose advised value:")
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
                expect(popover).to_contain_text("7 of 9 checked · 4 ready")
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
                            "button", name="Choose proposed value: Ada Example"
                        )
                    ).to_have_count(0)
                    page.get_by_text("Inspect context", exact=True).click()
                    expect(page.get_by_text("Authors", exact=True)).to_be_visible()
                    expect(
                        page.get_by_text(
                            "No eligible change · Current Writer", exact=False
                        )
                    ).to_be_visible()
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
    "option,expected", (("display_name", "English"), ("preserve", "en"))
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
            if option == "preserve":
                recorded = page.get_by_role("region", name="Language recorded outcome")
                expect(recorded.locator("dd")).to_have_text(expected)
            else:
                expect(page.get_by_text("English", exact=True)).to_be_visible()
            page.get_by_role("link", name="Evidence", exact=True).click()
            page.get_by_text(
                "Undetermined language; not an eligible choice", exact=False
            ).wait_for()
            expect(page.get_by_text("Language: UND.", exact=False)).to_be_visible()
            page.get_by_role("link", name="History", exact=True).click()
            expect(page.get_by_text(f"To {expected}", exact=True)).to_be_visible()
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role(
                "heading", name="Activity and recovery", exact=True
            ).wait_for()
            page.get_by_role("article").filter(has_text="Automatic choice").get_by_role(
                "button", name="Details"
            ).click()
            expect(page.get_by_text(expected, exact=True)).to_be_visible()
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
                "button", name="Choose proposed value: English", exact=True
            )
            expect(choice).to_be_enabled()
            expect(
                page.get_by_role(
                    "button", name="Choose proposed value: UND", exact=True
                )
            ).to_have_count(0)
            choice.focus()
            choice.press("Space")
            expect(choice).to_have_attribute("aria-pressed", "true")
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
                    "button", name="Choose proposed value: English", exact=True
                )
            ).to_have_attribute("aria-pressed", "true")
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
                    "button", name="Choose proposed value: Fantasy", exact=True
                )
                expect(choice).to_be_enabled()
                choice.focus()
                choice.press("Space")
                expect(choice).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(
                    page.get_by_role(
                        "button", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_attribute("aria-pressed", "true")
            elif destination == "ignore":
                expect(
                    page.get_by_role(
                        "button", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_count(0)
            else:
                expect(
                    page.get_by_role(
                        "button", name="Choose proposed value: Fantasy", exact=True
                    )
                ).to_have_attribute("aria-pressed", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            if destination == "ignore":
                expect(
                    page.get_by_text("Metadata: Fantasy", exact=False)
                ).to_have_count(0)
                expect(
                    page.get_by_role("heading", name="Genres", exact=True)
                ).to_be_visible()
            else:
                expect(
                    page.get_by_text("Metadata: Fantasy", exact=False)
                ).to_be_visible()
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
                "button", name=re.compile(r"^Choose proposed value: " + title)
            )
            if destination == "ask":
                expect(choice).to_be_enabled()
                choice.focus()
                choice.press("Space")
                expect(choice).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-pressed", "true")
            elif destination == "automatic":
                expect(choice).to_have_attribute("aria-pressed", "true")
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
            choice = page.get_by_role(
                "button",
                name="Choose proposed value: A voyage reveals a hidden city.",
                exact=True,
            )
            choice.focus()
            choice.press("Space")
            expect(choice).to_have_attribute("aria-pressed", "true")
            page.reload()
            expect(choice).to_have_attribute("aria-pressed", "true")
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
                "button",
                name="Choose proposed value: A voyage reveals a hidden city.",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    choice.press("Space")
                expect(choice).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-pressed", "true")
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
                "button",
                name=f"Choose proposed value: {selected}",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    choice.press("Space")
                expect(choice).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-pressed", "true")
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
                "button",
                name=f"Choose proposed value: {selected}",
                exact=True,
            )
            if destination == "ignore":
                expect(choice).to_have_count(0)
            else:
                expect(choice).to_be_enabled()
                if destination == "ask":
                    choice.focus()
                    choice.press("Space")
                expect(choice).to_have_attribute("aria-pressed", "true")
                page.reload()
                expect(choice).to_have_attribute("aria-pressed", "true")
            page.get_by_role("link", name="Evidence", exact=True).click()
            expect(page.get_by_role("heading", name="Isbn", exact=True)).to_be_visible()
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
                    "No eligible value was found in the retained evidence.", exact=True
                )
            ).to_be_visible()
            page.goto(f"{origin}/policy?section=curation")
            tags = page.get_by_role("region", name="Tags", exact=True)
            tags.get_by_role("combobox", name="Tags representation").click()
            page.get_by_role("option", name="Fill only when empty", exact=True).click()
            tags.get_by_role("button", name="Automatic", exact=True).click()
            page.get_by_role("button", name="Validate and preview", exact=True).click()
            result = page.locator(".cm-policy-result")
            expect(result).to_be_visible()
            assert (
                result.locator("xpath=..").get_attribute("class") == "cm-policy-editor"
            )
            assert page.locator(".cm-policy-finish .cm-policy-result").count() == 0
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
            page.locator("html").evaluate("node => node.style.fontSize = '200%'")
            page.set_viewport_size({"width": 320, "height": 900})
            result.get_by_role("button", name=re.compile(r"Show .* more")).click()
            last = page.locator(".cm-policy-preview-list > div").last
            last.scroll_into_view_if_needed()
            expect(last).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            actions = page.locator(".cm-policy-finish-actions")
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
    assert len(access.cost_evidence.models) == 1
    assert access.cost_evidence.models[0].per_1000_usd == "0.1713055000"
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
                    expect(adviser).to_contain_text(
                        "About $0.1713 per 1,000 description review requests (estimate)."
                    )
                    cover = page.get_by_role("region", name="Optional cover reviewer")
                    expect(cover).to_contain_text("Not measured for this model.")
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
                                "schema_version": "setup-state-v3",
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
                        page.get_by_text("Request cost: not measured for this model.")
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
                    route = "/library?attention=open" if attention else "/library"
                    with page.expect_response(
                        lambda response: (
                            ("/api/v1/attention" if attention else "/api/v1/library")
                            in response.url
                        )
                    ) as pending:
                        page.goto(origin + route)
                    assert pending.value.status == (200 if transitions == 1 else 503)
                    assert reads == 2
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
