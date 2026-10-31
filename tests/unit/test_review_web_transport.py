"""Review web transport: anonymous surface, authentication, CSRF
rotation, bounded bodies, the generated OpenAPI document, and the
served composition.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.operational_evidence import (
    LocalTimingStore,
    SupportBundle,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.persistence.owner_settings import OwnerSettingsStore
from mechanical_librarian.adapters.persistence.review_preparation import _FAILURE_CODES
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ReviewWebConfiguration,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
)
from mechanical_librarian.application.operations import (
    OperationalStatus,
    support_preparation_incidents,
)
from mechanical_librarian.application.owner_settings import (
    FileOwnerSettingsRepository,
    OwnerSettingsOperations,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    OwnerSessionService,
    ReviewWebError,
)
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.preparation_presentation import (
    PreparationActiveBook,
    PreparationCompletion,
    PreparationPassage,
    PreparationPresentation,
    PreparationRunState,
    PreparationTrigger,
    PreparationTriggerRecord,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalSet,
    ProposalValue,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
)
from mechanical_librarian.interfaces.web.app import owner_password_binding
from tests.support.proposal_evidence import _report as _proposal_report
from tests.support.review_web_doubles import (
    FakeActivityApplication,
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


def test_library_cover_serves_only_the_preview_its_current_report_binds(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The works list shows a cover by a digest only its current report binds.

    The route is a read of retained bytes, so it is held to the row: signed-out
    reads are refused before any preview is read, and a digest the report does
    not bind to the library's own cover -- an EPUB's, a guess, another item's --
    is not found rather than served (#1290).
    """

    def source(kind: CoverSourceKind) -> CoverSource:
        return CoverSource(
            cover_source_id(kind, "item-1"),
            kind,
            CoverSourceState.RETRIEVED,
            CoverArtifact.create(
                b"original-" + kind.value.encode(),
                b"preview-" + kind.value.encode(),
                "image/png",
                302,
                452,
            ),
        )

    library, epub = source(CoverSourceKind.ABS), source(CoverSourceKind.EPUB)
    assert library.artifact is not None and epub.artifact is not None
    report = _proposal_report(covers=(library, epub))
    case = ReviewCase.create(evaluate_report(report), report)
    previews = {
        library.artifact.preview_digest: b"\x89PNG\r\n\x1a\nlibrary",
        epub.artifact.preview_digest: b"\x89PNG\r\n\x1a\nepub",
    }
    covers = Mock()
    covers.retained_preview.side_effect = previews.__getitem__
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            FakeReviewRepository(case, report),
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
            ),
            clock=lambda: web.now,
            cover_considerations=covers,
        ),
        base_url="https://review.test",
    )

    def path(preview: str, item: str = "item-1") -> str:
        return (
            "/api/v1/library/cover?connection=primary&library=ebooks"
            f"&item={item}&preview={preview}"
        )

    signed_out = web.client.get(path(library.artifact.preview_digest))
    assert 401 == signed_out.status_code
    assert "no-store, private" == signed_out.headers["cache-control"]
    covers.retained_preview.assert_not_called()

    web.login()
    row = web.client.get("/api/v1/library?lens=all").json()["items"][0]
    assert row["cover"] == {
        "height": 452,
        "preview_sha256": library.artifact.preview_digest,
        "url": path(library.artifact.preview_digest),
        "width": 302,
    }
    served = web.client.get(row["cover"]["url"])
    assert 200 == served.status_code
    assert previews[library.artifact.preview_digest] == served.content
    assert "image/png" == served.headers["content-type"]
    assert "private, max-age=31536000, immutable" == served.headers["cache-control"]
    # Every response carries the application's policy, which grants nothing
    # by default.
    assert served.headers["content-security-policy"].startswith("default-src 'none';")
    assert "nosniff" == served.headers["x-content-type-options"]
    covers.retained_preview.assert_called_once_with(library.artifact.preview_digest)

    for refused in (
        path(epub.artifact.preview_digest),
        path("0" * 64),
        path("not-a-digest"),
        path(library.artifact.preview_digest, item="item-2"),
    ):
        response = web.client.get(refused)
        assert 404 == response.status_code, refused
        assert "not_found" == response.json()["error"]["code"]
        assert "no-store, private" == response.headers["cache-control"]
    covers.retained_preview.assert_called_once()

    covers.retained_preview.side_effect = CoverConsiderationError("unavailable")
    unavailable = web.client.get(path(library.artifact.preview_digest))
    assert 503 == unavailable.status_code
    assert "unavailable" == unavailable.json()["error"]["code"]


def test_only_liveness_and_login_are_anonymous(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    live = web.client.get("/health/live")
    assert live.status_code == 200
    assert len(live.content) <= 3
    login_page = web.client.get("/login")
    assert "Owner session" not in login_page.text
    assert 'class="sign-out"' not in login_page.text
    denied = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthorized"
    assert denied.headers["cache-control"] == "no-store, private"


def test_sign_in_page_speaks_the_owners_language(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    page = web.client.get("/login")
    assert page.status_code == 200
    assert (
        "Sign in to see what needs your attention in your library. Nothing changes"
        " in your library until you give write permission, and every change is"
        " checked before it is written."
    ) in page.text
    # The design document's vocabulary never reaches the owner (#1286).
    for term in ("exact-current", "exception cases", "safeguards"):
        assert term not in page.text


def test_settings_file_authority_transport_is_private_and_locked(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                }
            ],
        }
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
            ),
            clock=lambda: web.now,
            settings=OwnerSettingsOperations(FileOwnerSettingsRepository(document)),
        ),
        base_url="https://review.test",
    )
    web.cleanup.enter_context(web.client)
    assert web.client.get("/api/v1/settings").status_code == 401
    session = web.login()
    appearance = web.client.get("/api/v1/appearance")
    assert appearance.json() == {"reduced_motion": "follow_system"}
    assert appearance.headers["cache-control"] == "no-store, private"
    assert "old-secret" not in appearance.text
    response = web.client.get("/api/v1/settings")
    assert len(response.content) <= 65_536
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["authority"] == "file"
    assert all(item["managed_by_file"] for item in response.json()["fields"])
    locked = web.client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": 1,
            "document": document.model_dump() | {"revision": 2},
            "secrets": [],
        },
        headers={
            **web.origin,
            "x-csrf-token": session["csrf_token"],
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )
    assert locked.status_code == 409
    assert locked.json() == {
        "error": {
            "code": "settings_managed_by_file",
            "managed_by_file": "/data/config.json",
        },
        "schema_version": "review-error-v1",
    }


def test_settings_store_transport_redacts_secret_and_derives_effects(
    web: ReviewWebContext[FakeReviewRepository],
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v4",
            "revision": 1,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                }
            ],
        }
    )
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.execute(
        "CREATE TABLE owner_settings (singleton INTEGER PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE owner_setting_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO owner_settings VALUES(1,1,?)",
        (_canonical_json(document.model_dump()),),
    )
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES('connection.primary.key','old-secret-1234')"
    )
    connection.commit()
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
            ),
            clock=lambda: web.now,
            settings=OwnerSettingsOperations(OwnerSettingsStore(connection)),
        ),
        base_url="https://review.test",
    )
    web.cleanup.enter_context(web.client)
    valid = {
        "expected_revision": 1,
        "document": document.model_dump() | {"revision": 2},
        "secrets": [],
    }
    unauthenticated = web.client.patch(
        "/api/v1/settings",
        json=valid,
        headers={**web.origin, "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["cache-control"] == "no-store, private"
    session = web.login()
    missing_csrf = web.client.patch(
        "/api/v1/settings",
        json=valid,
        headers={**web.origin, "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY},
    )
    assert missing_csrf.status_code == 422
    bad_csrf = web.client.patch(
        "/api/v1/settings",
        json=valid,
        headers={
            **web.origin,
            "x-csrf-token": "bad",
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )
    assert bad_csrf.status_code == 403
    assert bad_csrf.headers["cache-control"] == "no-store, private"
    del web.client.headers["x-ml-build-identity"]
    missing_build = web.client.patch(
        "/api/v1/settings",
        json=valid,
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert missing_build.status_code == 409
    assert missing_build.headers["cache-control"] == "no-store, private"
    assert missing_csrf.headers["cache-control"] == "no-store, private"
    assert "old-secret" not in web.client.get("/api/v1/settings").text
    web.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    malformed = web.client.patch(
        "/api/v1/settings",
        json=valid | {"unexpected": True},
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert malformed.status_code == 422
    wrong_type = web.client.patch(
        "/api/v1/settings",
        json=valid | {"expected_revision": "1"},
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert wrong_type.status_code == 422
    canary = "Bearer settings-secret-canary"
    invalid_secret = web.client.patch(
        "/api/v1/settings",
        json=valid
        | {
            "secrets": [
                {
                    "field_id": "connection.primary.key",
                    "operation": "replace",
                    "value": canary,
                }
            ]
        },
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert invalid_secret.status_code == 422
    assert canary not in invalid_secret.text
    assert canary not in caplog.text
    oversized = web.client.patch(
        "/api/v1/settings",
        content=b"{" + b"x" * 262_144,
        headers={
            **web.origin,
            "x-csrf-token": session["csrf_token"],
            "content-type": "application/json",
        },
    )
    assert oversized.status_code == 413
    for field, changed in (
        ("mount", "invented"),
        (
            "policy_override",
            {"options": {"date_precision": "full_date"}, "destinations": {}},
        ),
    ):
        forbidden_document = document.model_dump() | {"revision": 2}
        forbidden_document["libraries"][0][field] = changed
        forbidden = web.client.patch(
            "/api/v1/settings",
            json={
                "expected_revision": 1,
                "document": forbidden_document,
                "secrets": [],
            },
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
        assert forbidden.status_code == 422
        assert forbidden.json()["error"]["code"] == "settings_invalid"
        assert OwnerSettingsStore(connection).read().document.revision == 1
    # A library's languages are refused exactly as the configuration file's
    # field is, before anything is stored (#1466).
    for languages in (
        ["de", "deu"],
        ["und"],
        ["mul"],
        ["mis"],
        ["zxx"],
        ["qqq"],
        ["de-latn"],
        ["de", "en", "fr", "es", "it", "nl", "pt", "sv", "da"],
        [1],
    ):
        invalid_document = document.model_dump() | {"revision": 2}
        invalid_document["libraries"][0]["languages"] = languages
        refused = web.client.patch(
            "/api/v1/settings",
            json={
                "expected_revision": 1,
                "document": invalid_document,
                "secrets": [],
            },
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "invalid"
        assert OwnerSettingsStore(connection).read().document.revision == 1
    response = web.client.patch(
        "/api/v1/settings",
        json={
            "expected_revision": 1,
            "document": document.model_dump()
            | {
                "revision": 2,
                "provider_recovery_generation": 2,
                "reduced_motion": "always_reduced",
            },
            "secrets": [
                {
                    "field_id": "connection.primary.key",
                    "operation": "replace",
                    "value": "new-secret-5678",
                }
            ],
        },
        headers={
            **web.origin,
            "x-csrf-token": session["csrf_token"],
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert "pending_restart" not in response.json()
    assert response.json()["secrets"][0]["indicator"] == "Configured · ····5678"
    assert "new-secret" not in response.text
    OwnerSettingsStore(connection).acknowledge_runtime(2)
    assert OwnerSettingsStore(connection).read().document.revision == 2
    assert "languages" not in response.json()["document"]["libraries"][0]
    with_languages = response.json()["document"] | {"revision": 3}
    with_languages["libraries"][0]["languages"] = ["fr", "de", "en"]
    stale_after_ack = web.client.patch(
        "/api/v1/settings",
        json={"expected_revision": 2, "document": with_languages, "secrets": []},
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert stale_after_ack.status_code == 200
    assert web.client.get("/api/v1/settings").json()["document"]["libraries"][0][
        "languages"
    ] == ["fr", "de", "en"]
    connection.close()
    unavailable = web.client.get("/api/v1/settings")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "settings_unavailable"


def test_operational_status_and_support_are_authenticated_bounded_surfaces(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.repository.operational_snapshot = lambda: {
        "signals": [
            {
                "attempt": 1,
                "cause": "preparation_failure",
                "code": "unreadable_media",
                "diagnostic_reference": "prep-0123456789abcdef",
                "kind": "owner_action_required",
                "path": "/private/book.epub",
                "scope": "work",
                "stage": "extraction",
                "work_id": "private-book-1",
            }
        ]
    }
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    timings = LocalTimingStore(Path(web.temporary.name) / "timings.json")
    support = SupportBundle(
        (
            (
                "runtime",
                lambda: {
                    "status": "degraded",
                    "secret": "never-export",
                },
            ),
            (
                "operational_incidents",
                lambda: support_preparation_incidents(
                    web.repository.operational_snapshot()
                ),
            ),
        )
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            operational_timings=timings,
            operational_support=support,
        ),
        base_url="https://review.test",
    )
    web.cleanup.enter_context(web.client)
    assert web.client.get("/health/ready").status_code == 200
    assert web.client.get("/api/v1/operations/status").status_code == 401
    session = web.login()
    status = web.client.get("/api/v1/operations/status")
    assert status.status_code == 200
    assert not status.json()["summary"]["healthy"]
    preview = web.client.get("/api/v1/operations/support/preview")
    assert preview.status_code == 200
    assert "never-export" not in preview.text
    assert "/private/book.epub" not in preview.text
    support_incident = preview.json()["payload"]["sections"]["operational_incidents"][
        "incidents"
    ][0]
    status_incident = status.json()["incidents"][0]
    assert support_incident == {
        key: status_incident[key]
        for key in ("attempt", "code", "diagnostic_reference", "stage")
    }
    exported = web.client.post(
        "/api/v1/operations/support/export",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert exported.status_code == 200
    assert "never-export" not in exported.text
    assert (
        exported.json()["sections"]["operational_incidents"]
        == preview.json()["payload"]["sections"]["operational_incidents"]
    )
    web.repository.operational_snapshot = lambda: (_ for _ in ()).throw(
        FieldEffectError("persistence")
    )
    failed = web.client.get("/api/v1/operations/status")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "operations_unavailable"


def test_real_terminal_configuration_failure_correlates_library_and_support_without_raw_work_values() -> (
    None
):
    """One persisted terminal work row supplies every value-free diagnostic surface."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "review.sqlite"
        repository = open_repository(database)
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery,
            1,
            "a" * 64,
            (
                CatalogueEntry(
                    "primary",
                    "fiction",
                    "lib-1",
                    "terminal-item",
                    1,
                    "Terminal title",
                    (),
                    True,
                    False,
                    "singular-supported",
                    "b" * 64,
                    "/private/work/path/credential.epub",
                    "epub",
                    "/private/root",
                ),
            ),
            12,
        )
        extraction = repository.claim_review_pipeline_action(13)
        assert extraction is not None
        repository.fail_review_pipeline_action(extraction, "configuration", False, 14)
        support = SupportBundle(
            (
                (
                    "operational_incidents",
                    lambda: support_preparation_incidents(
                        repository.operational_snapshot()
                    ),
                ),
            )
        )
        library = BrowseReviewLibrary(repository).library(
            lens="all", search=None, limit=25, cursor=None
        )
        operations = OperationalStatus(
            snapshot=repository.operational_snapshot,
            timings=LocalTimingStore(root / "timings.json"),
            support=support,
        )
        status = operations.status()
        preview = operations.support_preview()
        exported = json.loads(operations.support_export())
        repository.close()
    preparation = library["items"][0]["preparation"]
    assert isinstance(preparation, dict)
    incident = next(
        item for item in status["incidents"] if item["diagnostic_reference"] is not None
    )
    support_incident = preview["payload"]["sections"]["operational_incidents"][
        "incidents"
    ][0]
    expected = {
        "diagnostic_reference": preparation["diagnostic_reference"],
        "stage": preparation["stage"],
        "code": preparation["status"],
    }
    assert expected == {
        key: incident[key] for key in ("diagnostic_reference", "stage", "code")
    }
    assert expected == {
        key: support_incident[key] for key in ("diagnostic_reference", "stage", "code")
    }
    assert (
        preview["payload"]["sections"]["operational_incidents"]
        == exported["sections"]["operational_incidents"]
    )
    operational_payload = " ".join(
        json.dumps(value) for value in (status, preview, exported)
    )
    for raw in (
        "terminal-item",
        "/private/work/path/credential.epub",
        "/private/root",
        "configuration exception",
    ):
        assert raw not in operational_payload


def test_trust_boundary_failures_match_declared_api_statuses(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    bad_host = web.client.get(
        f"/api/v1/review-cases/{web.case.review_case_id}",
        headers={"Host": "elsewhere.invalid"},
    )
    assert bad_host.status_code == 400
    assert bad_host.json()["error"]["code"] == "invalid_origin"
    bad_origin = web.client.post(
        "/api/v1/session/login",
        headers={"Origin": "https://elsewhere.invalid"},
        json={"password": "correct horse"},
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "origin"


def test_activity_transition_transport_rejects_malformed_ids_and_guards_persistence(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    application = web.client.app.state.review_application
    valid_id = "effect_event_" + "a" * 64
    with patch.object(
        application,
        "activity_transitions",
        side_effect=FieldEffectError("persistence"),
    ) as transitions:
        malformed = web.client.get("/api/v1/activity/not-a-transition/transitions")
        assert malformed.status_code == 422
        assert malformed.json()["error"]["code"] == "invalid"
        transitions.assert_not_called()

        unavailable = web.client.get(
            f"/api/v1/activity/{valid_id}/transitions?after_sequence=7"
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "persistence"
        transitions.assert_called_once_with(valid_id, 7)


def test_seeded_activity_details_retain_real_transitions(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    configuration = ReviewWebConfiguration(
        canonical_origin="http://review.test",
        owner_password_hash_file=str(web.hash_path),
    )
    with TestClient(
        create_transport_test_app(
            web.repository, configuration, field_application=FakeActivityApplication()
        ),
        base_url="http://review.test",
    ) as client:
        assert (
            client.post(
                "/api/v1/session/login",
                json={"password": "correct horse"},
                headers={"Origin": "http://review.test"},
            ).status_code
            == 200
        )
        events = client.get("/api/v1/activity").json()["events"]
        # The seeded refusal stops at the pre-write read. A later title no
        # longer inherits its lost connection and stale with it (#1382).
        event = next(
            item
            for item in events
            if item["state"] == "stale" and item["destination"] == "genres"
        )
        path = f"/api/v1/activity/{event['transition_id']}/transitions"
        response = client.get(path)
        assert response.status_code == 200
        transitions = response.json()
        assert [
            (item["state"], item["sequence"]) for item in transitions["events"]
        ] == [("planned", 1), ("stale", 2)]
        assert transitions["has_more"] is False
        assert (
            client.get(path, params={"after_sequence": 1}).json()["events"]
            == (transitions["events"][1:])
        )


def test_host_header_cannot_relabel_a_routed_path_as_a_health_probe(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # `request.url` is reconstructed from the `Host` header, so a header
    # carrying a path plus a `#` terminator reads as an exact health path
    # on a request the router dispatched somewhere else entirely
    # (GHSA-86qp-5c8j-p5mr). The trust boundary exempts the two health
    # endpoints from origin validation, so a decision taken on that
    # reconstructed value skips origin and CSRF checking for the real
    # route. Every path-based decision reads the routed ASGI path instead.
    poisoned = {"Host": "review.test/health/live#"}
    assert web.client.get("/health/live").status_code == 200
    read = web.client.get(
        f"/api/v1/review-cases/{web.case.review_case_id}", headers=poisoned
    )
    assert read.status_code == 400
    assert read.json()["error"]["code"] == "invalid_origin"
    # Login is the one mutating route with no CSRF-token requirement of its
    # own, so the origin check is the whole of its cross-site defence.
    forged_login = web.client.post(
        "/api/v1/session/login",
        headers=poisoned,
        json={"password": "correct horse"},
    )
    assert forged_login.status_code == 400
    assert forged_login.json()["error"]["code"] == "invalid_origin"
    assert web.client.cookies.get("ml_session") is None
    web.login()
    page = web.client.get("/library")
    csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
    assert csrf is not None
    del web.client.headers["x-ml-build-identity"]
    forged_logout = web.client.post(
        "/api/v1/session/logout",
        headers={**poisoned, "X-CSRF-Token": csrf.group(1)},
    )
    assert forged_logout.status_code == 400
    assert forged_logout.json()["error"]["code"] == "invalid_origin"
    assert web.client.get("/library").status_code == 200


def test_an_encoded_fragment_cannot_borrow_the_health_exemption(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # `%23` decodes into the routed ASGI path but still terminates the
    # reconstructed URL's path component, so `/health/live%23x` routes
    # nowhere while `request.url.path` reads as the health endpoint. The
    # divergence here comes from the request path rather than the `Host`
    # header, so no amount of header validation in the framework closes
    # it and this keeps the routed-path decision pinned on its own.
    for path in ("/health/live%23x", "/health/live%3Fx"):
        refused = web.client.get(path, headers={"Host": "elsewhere.invalid"})
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "invalid_origin"
    probe = web.client.get("/health/live", headers={"Host": "elsewhere.invalid"})
    assert probe.status_code == 200
    assert probe.text == "ok\n"


def test_build_identity_gate_reads_the_routed_path_behind_a_trusted_proxy(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # Behind a trusted proxy the origin decision moves to `X-Forwarded-Host`
    # while `request.url` keeps following the raw `Host` header, so the two
    # path-based decisions have to be pinned separately: here the trust
    # boundary legitimately admits the request and the build-identity gate
    # is the only remaining path-based check.
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
        trusted_proxy_ips=("127.0.0.1",),
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(web.repository, configuration, clock=lambda: web.now),
        base_url="https://review.test",
        client=("127.0.0.1", 50_000),
    )
    web.cleanup.enter_context(web.client)
    proxied = {
        "Origin": "https://review.test",
        "X-Forwarded-Host": "review.test",
        "X-Forwarded-Proto": "https",
    }
    login = web.client.post(
        "/api/v1/session/login", headers=proxied, json={"password": "correct horse"}
    )
    assert login.status_code == 200, login.text
    for host, expected in (({}, 409), ({"Host": "review.test#"}, 409)):
        page = web.client.get("/library", headers=proxied)
        csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
        assert csrf is not None
        denied = web.client.post(
            "/api/v1/session/logout",
            headers={**proxied, **host, "X-CSRF-Token": csrf.group(1)},
        )
        assert denied.status_code == expected, denied.text
        assert denied.json()["error"]["code"] == "browser_build_mismatch"
    page = web.client.get("/library", headers=proxied)
    csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
    assert csrf is not None
    accepted = web.client.post(
        "/api/v1/session/logout",
        headers={
            **proxied,
            "Host": "review.test#",
            "X-CSRF-Token": csrf.group(1),
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )
    assert accepted.status_code == 204, accepted.text


def test_decision_authentication_precedes_required_input_validation(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.client.cookies.set("ml_session", "malformed")
    denied = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers=web.origin,
        json={},
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "unauthorized"


def test_login_case_evidence_history_and_logout_round_trip(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    cookie = web.client.cookies.get("ml_session")
    assert cookie is not None
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["current"]
    assert detail.json()["review_case_id"] == web.case.review_case_id
    assert detail.json()["target"]["title"] == "Title"
    title = detail.json()["fields"][0]
    assert title["current"]["value"] == "Title"
    assert title["proposed_values"][0]["value"] == "Exact evaluator candidate"
    assert title["deterministic_disposition"] == "unresolved_choice"
    assert title["needs_attention"]
    contexts = title["choice_contexts"]
    assert len(contexts) == len(title["proposed_values"]) + 1
    assert contexts[0]["selection"] == "current"
    assert contexts[0]["evidence"][0]["source"] == "Local library record"
    # The current value's card carries its value and nothing more (#1519).
    assert contexts[0]["rationale"] == ""
    assert all(context["recommendation_reason"] == "" for context in contexts)
    assert all(context["recommendation"] == "no_recommendation" for context in contexts)
    assert all(
        "{" not in observation["observation"]
        for context in contexts
        for observation in context["evidence"]
    )
    assert "insufficient_independent_corroboration" in title["notices"]
    assert [] == contexts[0]["limitations"]
    assert ["insufficient_independent_corroboration"] == contexts[1]["limitations"]
    assert not detail.json()["fields"][-1]["needs_attention"]
    assert "model_advice" not in detail.json()
    evidence = web.client.get(
        f"/api/v1/review-cases/{web.case.review_case_id}/evidence"
    )
    history = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}/history")
    assert evidence.status_code == 200
    assert evidence.json()["schema_version"] == "review-case-evidence-v4"
    assert evidence.json()["items"][0]["source"] == "Local library record"
    assert evidence.json()["items"][0]["destinations"] == ["title"]
    assert "Exact evaluator candidate" in evidence.json()["items"][0]["observation"]
    assert "reference" not in evidence.json()["items"][0]
    assert evidence.headers["cache-control"] == "no-store, private"
    invalid_cursor = web.client.get(
        f"/api/v1/review-cases/{web.case.review_case_id}/evidence?cursor=bad"
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "request"
    assert history.json()["items"][0]["kind"] == "admitted"
    assert history.json()["schema_version"] == "review-case-history-v3"
    assert history.headers["cache-control"] == "no-store, private"
    assert "default-src 'none'" in detail.headers["content-security-policy"]
    missing = web.client.post(
        "/api/v1/session/logout",
        headers=web.origin,
    )
    assert missing.status_code == 422
    wrong = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "X-CSRF-Token": "x" * 32},
    )
    assert wrong.status_code == 403
    logout = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
    )
    assert logout.status_code == 204
    denied = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert denied.status_code == 401


def test_session_cookie_lifetime_and_logout_deletion_attributes_match(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.client.post(
        "/api/v1/session/login", headers=web.origin, json={"password": "correct horse"}
    )
    assert login.status_code == 200
    created = login.headers["set-cookie"]
    assert "Max-Age=600" in created
    assert "Path=/" in created
    assert "HttpOnly" in created
    assert "SameSite=strict" in created
    assert "Secure" in created
    session = web.client.cookies.get("ml_session")
    assert session is not None
    logout = web.client.post(
        "/api/v1/session/logout",
        headers={
            **web.origin,
            "X-CSRF-Token": login.json()["csrf_token"],
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
        },
    )
    assert logout.status_code == 204
    assert session not in web.repository.sessions
    deleted = logout.headers["set-cookie"]
    assert "Max-Age=0" in deleted
    assert "Path=/" in deleted
    assert "HttpOnly" in deleted
    assert "SameSite=strict" in deleted
    assert "Secure" in deleted


def test_case_detail_orders_selectable_contexts_before_preservation_contexts(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    proposal_set = ProposalSet.from_json(web.case.proposal_document)
    original = next(
        result for result in proposal_set.results if result.destination.value == "title"
    )
    original_alternative = original.alternatives[0]
    preserved = ProposalAlternative.create(
        ProposalValue(original_alternative.value.kind, "Title"),
        ProposalAssessment.PRESERVES_TARGET,
        original_alternative.evidence_references,
        original_alternative.correlations,
        original_alternative.scope,
    )
    eligible = ProposalAlternative.create(
        ProposalValue(original_alternative.value.kind, "Zulu"),
        ProposalAssessment.REPLACES_TARGET,
        original_alternative.evidence_references,
        original_alternative.correlations,
        original_alternative.scope,
    )
    # Canonical alternative order can put a preserved incumbent first. The
    # ordinary choice projection must still pair its first selectable value
    # with the eligible context, keeping the retained context after it.
    assert preserved.alternative_id < eligible.alternative_id
    title = ProposalResult(
        original.destination,
        ProposalOutcome.UNRESOLVED_CHOICE,
        tuple(sorted((preserved, eligible), key=lambda value: value.alternative_id)),
        None,
        ("competing_evidence",),
    )
    case = type(web.case).create(
        ProposalSet.create(
            report_id=proposal_set.report_id,
            target_fingerprint=proposal_set.target_fingerprint,
            results=tuple(
                title if result.destination is title.destination else result
                for result in proposal_set.results
            ),
            resolved_policy_id=proposal_set.resolved_policy_id,
        ),
        web.repository.report,
    )
    web.case = case
    web.repository.case = case

    web.login()
    response = web.client.get(f"/api/v1/review-cases/{case.review_case_id}")
    assert response.status_code == 200, response.text
    title = next(
        field for field in response.json()["fields"] if field["destination"] == "title"
    )
    assert [eligible.value.to_data()] == title["proposed_values"]
    assert [None, eligible.alternative_id, preserved.alternative_id] == [
        context["alternative_id"] for context in title["choice_contexts"]
    ]


def test_case_successor_is_authenticated_bounded_and_serialized(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    path = f"/api/v1/review-cases/{web.case.review_case_id}/successor"
    denied = web.client.get(path)
    assert denied.status_code == 401

    successor = "review_case_" + "d" * 64
    web.repository.current_successor = successor
    web.login()
    resolved = web.client.get(path)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json() == {"review_case_id": successor}

    web.repository.current_successor = None
    closed = web.client.get(path)
    assert closed.status_code == 200
    assert closed.json() == {"review_case_id": None}

    web.repository.successor_failure = True
    unavailable = web.client.get(path)
    assert unavailable.status_code == 404
    assert unavailable.json()["error"]["code"] == "unavailable"

    malformed = web.client.get("/api/v1/review-cases/not-a-case/successor")
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "not_found"


def test_logout_persistence_failure_keeps_the_session_active(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    web.repository.revoke_failure = True
    failed = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "persistence"
    assert web.client.cookies.get("ml_session") is not None
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert detail.status_code == 200


def test_logout_concurrent_revocation_is_unauthorized_not_an_outage(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    web.repository.revoke_result = "unauthorized"
    failed = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
    )
    assert failed.status_code == 401
    assert failed.json()["error"]["code"] == "unauthorized"
    assert "set-cookie" not in failed.headers


def test_logout_maps_expired_and_malformed_sessions_to_unauthorized(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    web.now += 601
    expired = web.client.post(
        "/api/v1/session/logout",
        headers=web.origin,
    )
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "unauthorized"
    assert "set-cookie" not in expired.headers

    web.client.cookies.clear()
    web.client.cookies.set("ml_session", "malformed")
    malformed = web.client.post(
        "/api/v1/session/logout",
        headers=web.origin,
    )
    assert malformed.status_code == 401
    assert malformed.json()["error"]["code"] == "unauthorized"
    assert "set-cookie" not in malformed.headers


def test_absent_decision_case_is_not_a_persistence_outage(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    field = detail["fields"][0]
    payload = {
        "destination": field["destination"],
        "outcome": "accept",
        "proposal_id": field["proposal_id"],
        "selected_value": field["proposed_values"][0],
        "target_fingerprint": field["target_fingerprint"],
    }
    url = "/api/v1/review-cases/review_case_" + "f" * 64 + "/decisions"
    headers = {
        **web.origin,
        "X-CSRF-Token": login["csrf_token"],
        "Idempotency-Key": "decision-request-absent",
    }
    absent = web.client.post(url, headers=headers, json=payload)
    assert absent.status_code == 404
    assert absent.json()["error"]["code"] == "not_found"
    assert not web.repository.decisions

    web.repository.existence_failure = True
    unavailable = web.client.post(url, headers=headers, json=payload)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "persistence"


def test_one_explicit_decision_requires_csrf_and_is_server_authoritative(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    timings = LocalTimingStore(
        Path(web.temporary.name) / "private" / "decision-timings.json",
        enabled=True,
    )
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            operational_timings=timings,
        ),
        base_url="https://review.test",
    )
    login = web.login()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    field = detail["fields"][0]
    payload = {
        "destination": field["destination"],
        "outcome": "accept",
        "proposal_id": field["proposal_id"],
        "selected_value": field["proposed_values"][0],
        "target_fingerprint": field["target_fingerprint"],
    }
    denied = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={**web.origin, "Idempotency-Key": "decision-request-0001"},
        json=payload,
    )
    assert denied.status_code == 422
    accepted = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "decision-request-0001",
        },
        json=payload,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["outcome"] == "accept"
    assert [
        (value.stage, value.wait_class, value.duration_ms) for value in timings.list()
    ] == [("human_wait", "owner", 10_000)]
    refreshed = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    assert not refreshed["fields"][0]["needs_attention"]
    assert (
        refreshed["fields"][0]["human_decision"]["decision_id"]
        == accepted.json()["decision_id"]
    )
    assert refreshed["fields"][0]["current"] == field["current"]
    assert (
        refreshed["fields"][0]["human_decision"]["selected_value"]
        == field["proposed_values"][0]
    )
    history = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}/history")
    assert history.status_code == 200, history.text
    recorded = next(
        item
        for item in history.json()["items"]
        if item["kind"] == "human_decision_recorded"
        and item["destination"] == field["destination"]
        and item["outcome"] == "accept"
    )
    assert recorded["previous_value"] == {
        "kind": "text",
        "value": field["current"]["value"],
    }
    assert recorded["selected_value"] == field["proposed_values"][0]
    web.repository.case_current = False
    web.now += 1
    replay = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "decision-request-0001",
        },
        json=payload,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["decision_id"] == accepted.json()["decision_id"]
    assert len(timings.list()) == 1
    conflict_payload = {**payload, "outcome": "reject", "selected_value": None}
    conflict = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "decision-request-0001",
        },
        json=conflict_payload,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(web.repository.decisions) == 1


def _preparation_presentation(state: PreparationRunState) -> PreparationPresentation:
    terminal = state in {
        PreparationRunState.COMPLETED_CLEAN,
        PreparationRunState.COMPLETED_WITH_FAILURES,
        PreparationRunState.DISMISSED,
    }
    return PreparationPresentation(
        run_id="preparation_run_" + "a" * 64,
        state=state,
        triggers=(
            PreparationTriggerRecord(
                PreparationTrigger.MANUAL_SCAN,
                1,
                100,
                100,
            ),
        ),
        passage=PreparationPassage(1, 1, 1, 1, 1, 0),
        active_books=(),
        active_overflow=0,
        recent_completions=(),
        throughput_per_minute=1.0,
        started_at=100,
        updated_at=101,
        completed_at=101 if terminal else None,
        total=1,
    )


def test_preparation_dismissal_is_authenticated_guarded_and_stale_safe(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    path = "/api/v1/review-preparation/dismiss"
    run_id = "preparation_run_" + "a" * 64
    anonymous = web.client.post(path, headers=web.origin, json={"run_id": run_id})
    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "no-store, private"

    login = web.login()
    missing_csrf = web.client.post(path, headers=web.origin, json={"run_id": run_id})
    assert missing_csrf.status_code == 422
    bad_csrf = web.client.post(
        path,
        headers={**web.origin, "x-csrf-token": "wrong"},
        json={"run_id": run_id},
    )
    assert bad_csrf.status_code == 403
    headers = {**web.origin, "x-csrf-token": login["csrf_token"]}
    stale = web.client.post(path, headers=headers, json={"run_id": run_id})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale"

    web.repository.preparation_presentation_value = _preparation_presentation(
        PreparationRunState.BLOCKED
    )
    blocked = web.client.post(path, headers=headers, json={"run_id": run_id})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "blocked"

    web.repository.dismiss_preparation_error = RuntimeError("database unavailable")
    unavailable = web.client.post(path, headers=headers, json={"run_id": run_id})
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "persistence"
    web.repository.dismiss_preparation_error = None

    web.repository.preparation_presentation_value = _preparation_presentation(
        PreparationRunState.COMPLETED_WITH_FAILURES
    )
    accepted = web.client.post(path, headers=headers, json={"run_id": run_id})
    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store, private"
    assert accepted.json()["presentation"] is None
    assert web.repository.dismissed_preparation_runs == [run_id]
    current = web.client.get("/api/v1/review-preparation")
    assert current.status_code == 200
    assert current.headers["cache-control"] == "no-store, private"
    assert current.json()["presentation"] is None


def test_preparation_dismissal_rejects_declared_and_streamed_oversized_bodies(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    path = "/api/v1/review-preparation/dismiss"
    login = web.login()
    headers = {**web.origin, "x-csrf-token": login["csrf_token"]}

    declared = web.client.post(
        path,
        headers={**headers, "content-length": "2049"},
        content=b"{}",
    )
    streamed = web.client.post(
        path,
        headers={**headers, "content-type": "application/json"},
        content=iter((b'{"run_id":"', b"x" * 2048, b'"}')),
    )

    for response in (declared, streamed):
        assert response.status_code == 413
        assert response.headers["cache-control"] == "no-store, private"
        assert response.json() == {
            "schema_version": "review-error-v1",
            "error": {"code": "body_too_large"},
        }
    assert web.repository.dismissed_preparation_runs == []


def test_review_page_rotates_one_memory_only_csrf_token(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    inactive = web.client.get("/api/v1/review-preparation")
    assert inactive.status_code == 200, inactive.text
    assert inactive.json() == {
        "schema_version": "review-preparation-v2",
        "recovery_required": False,
        "libraries": [],
        "paused": False,
        "active": False,
        "actionable": False,
        "connections": [],
        "first_boot": False,
        "first_boot_complete": False,
        "first_boot_works": None,
        "presentation": None,
        "provider_throttling": None,
    }
    page = web.client.get(f"/review/{web.case.review_case_id}")
    assert page.status_code == 200, page.text
    match = re.search(r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', page.text)
    assert match is not None
    assert match is not None
    browser_csrf = match.group(1)
    assert browser_csrf != login["csrf_token"]
    assert f'data-build-identity="{TEST_REACT_BUILD_IDENTITY}"' in page.text
    script = web.client.get("/assets/review-app/assets/index-deadbeef.js")
    assert script.status_code == 200
    field = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()[
        "fields"
    ][0]
    payload = {
        "destination": field["destination"],
        "outcome": "keep_current",
        "proposal_id": field["proposal_id"],
        "selected_value": None,
        "target_fingerprint": field["target_fingerprint"],
    }
    stale = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "browser-decision-rotate-0001",
        },
        json=payload,
    )
    assert stale.status_code == 403
    accepted = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": browser_csrf,
            "Idempotency-Key": "browser-decision-rotate-0001",
        },
        json=payload,
    )
    assert accepted.status_code == 200, accepted.text


@pytest.mark.parametrize("name", ("x" * 2048, "界" * 682 + "ab"))
def test_preparation_http_preserves_maximum_display_names(
    web: ReviewWebContext[FakeReviewRepository], name: str
) -> None:
    assert len(name.encode("utf-8")) == 2048
    presentation = _preparation_presentation(PreparationRunState.RUNNING)
    web.repository.preparation_presentation_value = replace(
        presentation,
        active_books=(PreparationActiveBook(name, "Reading from library"),),
        recent_completions=(PreparationCompletion(name, "ready", 101),),
    )
    web.login()

    response = web.client.get("/api/v1/review-preparation")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["presentation"]["active_books"][0]["name"] == name
    assert response.json()["presentation"]["recent_completions"][0]["name"] == name


def test_authenticated_page_preserves_csrf_rotation_persistence_failure(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    web.repository.rotate_failure = True
    for path in (
        "/library",
        "/attention",
        f"/review/{web.case.review_case_id}",
    ):
        unavailable = web.client.get(path)
        assert unavailable.status_code == 503, path
        assert unavailable.json()["error"]["code"] == "persistence", path


def test_authenticated_page_preserves_expired_rotation_as_unauthorized(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    web.repository.rotate_result = "unauthorized"
    for path in (
        "/library",
        "/attention",
        f"/review/{web.case.review_case_id}",
    ):
        denied = web.client.get(path)
        assert denied.status_code == 401, path
        assert denied.json()["error"]["code"] == "unauthorized", path


def test_expired_session_redirects_html_shell_while_api_remains_json(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.login()
    web.now += 601
    shell = web.client.get("/library", follow_redirects=False)
    assert shell.status_code == 303
    assert shell.headers["location"] == "/login"
    api = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert api.status_code == 401
    assert api.json()["error"]["code"] == "unauthorized"


def test_root_opens_library_only_with_a_valid_session(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    signed_out = web.client.get("/", follow_redirects=False)
    assert signed_out.status_code == 303
    assert signed_out.headers["location"] == "/login"
    web.login()
    signed_in = web.client.get("/", follow_redirects=False)
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/library"
    web.now += 601
    expired = web.client.get("/", follow_redirects=False)
    assert expired.headers["location"] == "/login"


@pytest.mark.parametrize(
    ("requested", "location"),
    [
        ("/library", "/login"),
        ("/library?lens=all", "/login?next=%2Flibrary%3Flens%3Dall"),
        ("/attention", "/login?next=%2Fattention"),
        (
            "/review/case-1?field=title",
            "/login?next=%2Freview%2Fcase-1%3Ffield%3Dtitle",
        ),
        ("/settings", "/login?next=%2Fsettings"),
        ("/policy", "/login?next=%2Fpolicy"),
        ("/activity", "/login?next=%2Factivity"),
        ("/review/case%201", "/login"),
    ],
    ids=[
        "library_plain",
        "library_query",
        "attention",
        "review_query",
        "settings",
        "policy",
        "activity",
        "undecodable_case",
    ],
)
def test_signed_out_workspace_page_signs_in_with_its_return_path(
    web: ReviewWebContext[FakeReviewRepository], requested: str, location: str
) -> None:
    shell = web.client.get(requested, follow_redirects=False)
    assert shell.status_code == 303
    assert shell.headers["location"] == location


@pytest.mark.parametrize(
    "next_path",
    [
        "/attention",
        "/library?lens=all&page=2",
        "/review/review_case_" + "a" * 64,
        "/review/case-1?field=title",
        "/activity?filter=stopped",
    ],
    ids=[
        "attention",
        "library_query",
        "review_case_id",
        "review_query",
        "activity_query",
    ],
)
def test_sign_in_page_carries_a_workspace_return_path(
    web: ReviewWebContext[FakeReviewRepository], next_path: str
) -> None:
    page = web.client.get("/login", params={"next": next_path})
    assert page.status_code == 200
    assert f' data-next="{html.escape(next_path)}"' in page.text


@pytest.mark.parametrize(
    "next_path",
    [
        "//evil.invalid/library",
        "///evil.invalid",
        "/\\evil.invalid",
        "https://evil.invalid/library",
        "javascript:alert(1)",
        "evil.invalid/library",
        "/library/../../evil",
        "/library/x",
        "/login",
        "/api/v1/library",
        "/unknown",
        "/library#top",
        "/library?q=a b",
        '/library?q="><script>',
        "/library?q=\\evil",
        "/library\n",
        "/review/" + "a" * 129,
        "/library?" + "a" * 2049,
        "",
    ],
    ids=[
        "protocol_relative",
        "triple_slash",
        "backslash_host",
        "absolute_url",
        "javascript",
        "bare_host",
        "dot_segments",
        "nested_path",
        "login",
        "api",
        "unknown_page",
        "fragment",
        "space",
        "markup",
        "query_backslash",
        "newline",
        "long_case_id",
        "long_query",
        "empty",
    ],
)
def test_sign_in_page_refuses_any_other_return_path(
    web: ReviewWebContext[FakeReviewRepository], next_path: str
) -> None:
    page = web.client.get("/login", params={"next": next_path})
    assert page.status_code == 200
    assert "data-next" not in page.text


def test_malformed_case_decision_body_is_bounded_before_validation(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    oversized = b"{" + b'"private":"' + b"x" * 9_000 + b'"}'
    declared = web.client.post(
        "/api/v1/review-cases/not-a-case/decisions",
        headers={**web.origin, "Content-Type": "application/json"},
        content=oversized,
    )
    assert declared.status_code == 413
    assert b"private" not in declared.content
    streamed = web.client.post(
        "/api/v1/review-cases/not-a-case/decisions",
        headers={**web.origin, "Content-Type": "application/json"},
        content=(chunk for chunk in (oversized[:4_500], oversized[4_500:])),
    )
    assert streamed.status_code == 413
    assert b"private" not in streamed.content


_REVIEWER_MUTATIONS = (
    ("post", "/api/v1/reviewer-access/openrouter/refresh"),
    ("put", "/api/v1/reviewer-access/openrouter"),
    ("delete", "/api/v1/reviewer-access/openrouter"),
    ("put", "/api/v1/reviewer-access/selection"),
    ("post", "/api/v1/reviewer-access/codex/device"),
    ("post", "/api/v1/reviewer-access/codex/recheck"),
    ("post", "/api/v1/reviewer-access/codex/device/cancel"),
    ("delete", "/api/v1/reviewer-access/codex"),
)


@pytest.mark.parametrize(("method", "path"), _REVIEWER_MUTATIONS)
def test_reviewer_mutation_bodies_are_bounded_before_json_parsing(
    web: ReviewWebContext[FakeReviewRepository], method: str, path: str
) -> None:
    private = b'"api_key":"' + b"s" * 4_100 + b'"'
    response = web.client.request(
        method,
        path,
        headers={**web.origin, "Content-Type": "application/json"},
        content=(chunk for chunk in (b"{" + private[:2_000], private[2_000:] + b"}")),
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"
    assert b"api_key" not in response.content


def test_openrouter_body_limit_admits_the_largest_valid_key_envelope(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    response = web.client.put(
        "/api/v1/reviewer-access/openrouter",
        headers={**web.origin, "Content-Type": "application/json"},
        json={
            "api_key": "s" * 2_048,
            "expected_revision": 1,
            "model": "provider/model",
        },
    )

    assert response.status_code != 413


def test_origin_host_proxy_expiry_and_throttle_fail_closed(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    invalid_origin = web.client.post(
        "/api/v1/session/login",
        headers={"Origin": "https://evil.test"},
        json={"password": "correct horse"},
    )
    assert invalid_origin.status_code == 403
    for name in (
        "cache-control",
        "content-security-policy",
        "strict-transport-security",
        "x-frame-options",
    ):
        assert name in invalid_origin.headers
    forwarded = web.client.get("/login", headers={"X-Forwarded-Host": "review.test"})
    assert forwarded.status_code == 400
    for name in (
        "cache-control",
        "content-security-policy",
        "strict-transport-security",
        "x-frame-options",
    ):
        assert name in forwarded.headers
    for _ in range(5):
        failure = web.client.post(
            "/api/v1/session/login",
            headers=web.origin,
            json={"password": "wrong"},
        )
        assert failure.status_code == 401
    throttled = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        json={"password": "correct horse"},
    )
    assert throttled.status_code == 429
    # The five attempts above pin the count the throttle counts to. These two
    # pin the window it counts over, from both sides: the recorded failures are
    # still inside a 300-second window at exactly 300 seconds, and outside it
    # one second later, where the next attempt is refused for the password
    # rather than for the throttle. The clock is `web.now`, an integer the
    # composition reads back through the context, so nothing here waits or
    # measures elapsed time.
    #
    # What this cannot see: the window is applied by the store, and the store
    # here is `FakeReviewRepository`, which mirrors
    # `adapters/persistence/review_sessions.py::begin_login` rather than being
    # it. The mutant this kills is in the constants `login` passes, so the two
    # agreeing about what a window means is an assumption, not a check.
    web.now += 300
    inside = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        json={"password": "correct horse"},
    )
    assert inside.status_code == 429
    web.now += 1
    released = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        json={"password": "wrong"},
    )
    assert released.status_code == 401
    web.repository.failures.clear()
    web.login()
    web.now += 601
    expired = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert expired.status_code == 401


class _FailingSessionStore:
    """One session-store port whose login writes fail.

    `login` converts a store fault into `ReviewWebError("persistence")`, and
    #698 makes that identity the thing the failure reports rather than a
    generic runtime error. Forcing the fault through a separate implementation
    of the port keeps it on the boundary `login` must cross, instead of
    substituting a member on the shared fake -- which would stop reaching the
    call the moment the store's internals moved.
    """

    def __init__(self, *, failing_begin: bool) -> None:
        self._failing_begin = failing_begin

    def begin_login(
        self, now, client_digest, window_seconds, client_maximum, global_maximum
    ):
        if self._failing_begin:
            raise RuntimeError("session store unavailable")
        return True

    def create_review_session(
        self, session_digest, csrf_digest, created_at, expires_at, client_digest
    ):
        raise RuntimeError("session store unavailable")


def _session_service(repository, token: str, requested: list[int]):
    """One `OwnerSessionService` over a recording token factory and a fixed clock."""

    def token_factory(length: int) -> str:
        requested.append(length)
        return token

    return OwnerSessionService(
        repository,
        lambda _password: True,
        lambda token: hashlib.sha256(token.encode()).hexdigest(),
        lambda: 1_000,
        600,
        token_factory=token_factory,
        client_digest=lambda identity: hashlib.sha256(identity.encode()).hexdigest(),
    )


def test_login_pins_the_token_and_password_bounds_it_declares(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    requested: list[int] = []
    issued = _session_service(web.repository, "a" * 32, requested).login(
        "correct horse", "client:192.0.2.10"
    )
    # Two tokens, each requested at exactly the declared length, and a token of
    # exactly the lower bound admitted rather than refused.
    assert [32, 32] == requested
    assert 32 == len(issued.session_token) == len(issued.csrf_token)

    for length in (31, 129):
        with pytest.raises(ReviewWebError) as refused:
            _session_service(web.repository, "a" * length, []).login(
                "correct horse", "client:192.0.2.10"
            )
        assert "runtime" == refused.value.code

    # One byte is inside the declared password length, so it reaches the
    # verifier rather than being refused for its size.
    admitted = _session_service(web.repository, "a" * 32, []).login(
        "x", "client:192.0.2.10"
    )
    assert 32 == len(admitted.session_token)

    for failing_begin in (True, False):
        with pytest.raises(ReviewWebError) as unavailable:
            _session_service(
                _FailingSessionStore(failing_begin=failing_begin), "a" * 32, []
            ).login("correct horse", "client:192.0.2.10")
        assert "persistence" == unavailable.value.code


def test_verifier_bound_session_digest_invalidates_replaced_owner_verifier(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    old_verifier = PasswordHasher().hash("correct horse")
    old_verify, old_digest, old_client = owner_password_binding(old_verifier)
    tokens = iter(("s" * 32, "c" * 32))
    old_sessions = OwnerSessionService(
        web.repository,
        old_verify,
        old_digest,
        lambda: 1_000,
        600,
        lambda _length: next(tokens),
        client_digest=old_client,
    )
    issued = old_sessions.login("correct horse", "client:192.0.2.10")
    assert old_sessions.authenticate(issued.session_token) == "owner"

    new_verifier = PasswordHasher().hash("correct horse")
    new_verify, new_digest, new_client = owner_password_binding(new_verifier)
    replaced_sessions = OwnerSessionService(
        web.repository,
        new_verify,
        new_digest,
        lambda: 1_000,
        600,
        lambda _length: "n" * 32,
        client_digest=new_client,
    )
    with pytest.raises(ReviewWebError, match="unauthorized"):
        replaced_sessions.authenticate(issued.session_token)
    assert (
        replaced_sessions.login("correct horse", "client:192.0.2.10").session_token
        == "n" * 32
    )
    assert issued.session_token not in web.repository.sessions
    assert old_verifier not in repr(web.repository.sessions)


def test_openapi_is_deterministic_and_secret_free(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    anonymous = web.client.get("/api/v1/openapi.json")
    assert anonymous.status_code == 401
    web.login()
    first = web.client.get("/api/v1/openapi.json")
    second = web.client.get("/api/v1/openapi.json")
    assert first.content == second.content
    expected = json.loads(
        Path("docs/reference/review-openapi-v1.json").read_text(encoding="utf-8")
    )
    assert first.json() == expected

    assert (
        expected["components"]["schemas"]["SecretWrite"]["properties"]["value"][
            "writeOnly"
        ]
        is True
    )
    assert (
        set(
            first.json()["components"]["schemas"]["_OperationalIncident"]["properties"][
                "code"
            ]["anyOf"][0]["enum"]
        )
        == _FAILURE_CODES
    )
    assert (
        "413"
        in expected["paths"]["/api/v1/metadata-policy/drafts"]["post"]["responses"]
    )
    assert (
        "413"
        in expected["paths"]["/api/v1/metadata-policy/activate"]["post"]["responses"]
    )
    for path, path_item in expected["paths"].items():
        if path == "/api/v1/session/login":
            continue
        for method in ("post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if operation is None:
                continue
            build_parameters = [
                parameter
                for parameter in operation.get("parameters", [])
                if parameter.get("name") == "x-ml-build-identity"
            ]
            assert len(build_parameters) == 1, (path, method)
            assert build_parameters[0]["required"]
            assert build_parameters[0]["in"] == "header"
            assert "409" in operation["responses"]
    schemas = expected["components"]["schemas"]
    automatic_destination = schemas["_AutomaticDecisionProjection"]["properties"][
        "destination"
    ]
    assert "cover" not in automatic_destination["enum"]
    cover_automatic = schemas["_CoverAutomaticDecisionProjection"]
    assert cover_automatic["properties"]["destination"]["const"] == "cover"
    assert "supersedes_decision_id" in cover_automatic["required"]
    qualification_items = schemas["_PolicyProfileResponse"]["properties"][
        "rich_qualification"
    ]["items"]
    closed_qualification: dict[str, str] = {}
    for reference in qualification_items["anyOf"]:
        schema = schemas[reference["$ref"].rsplit("/", 1)[-1]]
        properties = schema["properties"]
        field = properties["field"]["const"]
        disposition = properties["disposition"]["const"]
        option_schema = properties["option"]
        options = (
            option_schema["enum"]
            if "enum" in option_schema
            else [option_schema["const"]]
        )
        closed_qualification.update(
            {f"{field}:{option}": disposition for option in options}
        )
    assert {
        **dict(F20_OPTION_DISPOSITIONS),
        "cover_order:fixed_source_order": "pass",
        "cover_order:replace_if_strictly_better": "pass",
        **{
            key: "pass"
            for key in (
                "title_layout:preserve",
                "title_layout:title_and_subtitle",
                "date_precision:year",
                "date_precision:full_date",
                "language_rendering:display_name",
                "contributor_names:preserve",
                "contributor_names:canonical",
                "publisher_preference:publisher",
            )
        },
        **{
            key: "not_qualified"
            for key in (
                "publisher_preference:imprint",
                "subtitle:replace_descriptive",
                "series:add_qualified",
                "series:replace_qualified",
                "description_fill:replace_safe_plain_text",
                "explicit:adult_product",
            )
        },
    } == closed_qualification
    assert "HTTPValidationError" not in schemas
    assert all(
        schema.get("additionalProperties") is False
        for schema in schemas.values()
        if schema.get("type") == "object"
    )
    for path, schema in (
        ("/api/v1/metadata-policy/automatic-safety/pause", "_AutomaticPauseRequest"),
        (
            "/api/v1/metadata-policy/automatic-safety/recover",
            "_AutomaticRecoveryRequest",
        ),
        (
            "/api/v1/metadata-policy/automatic-safety/suspend",
            "_AutomaticSuspendRequest",
        ),
    ):
        assert {"$ref": f"#/components/schemas/{schema}"} == expected["paths"][path][
            "post"
        ]["requestBody"]["content"]["application/json"]["schema"]
    assert str(web.hash_path).encode() not in first.content
    assert b"correct horse" not in first.content
    decision_operation = expected["paths"][
        "/api/v1/review-cases/{review_case_id}/decisions"
    ]["post"]
    assert "404" in decision_operation["responses"]
    assert expected["components"]["securitySchemes"]["OwnerSession"] == {
        "in": "cookie",
        "name": "ml_session",
        "type": "apiKey",
    }
    assert decision_operation["security"] == [{"OwnerSession": []}]
    protected_operations = {
        (method, path)
        for path, path_item in expected["paths"].items()
        for method, operation in path_item.items()
        if operation.get("security") == [{"OwnerSession": []}]
    }
    assert protected_operations == {
        ("post", "/api/v1/reviewer-access/description-model/contained"),
        ("post", "/api/v1/reviewer-access/description-model/test"),
        ("post", "/api/v1/review-cases/{review_case_id}/description-advice/retry"),
        ("post", "/api/v1/review-cases/{review_case_id}/description-advice/selection"),
        ("get", "/api/v1/appearance"),
        ("get", "/api/v1/reviewer-access"),
        ("delete", "/api/v1/reviewer-access/codex"),
        ("get", "/api/v1/reviewer-access/codex/device"),
        ("post", "/api/v1/reviewer-access/codex/device"),
        ("post", "/api/v1/reviewer-access/codex/device/cancel"),
        ("post", "/api/v1/reviewer-access/codex/recheck"),
        ("delete", "/api/v1/reviewer-access/openrouter"),
        ("put", "/api/v1/reviewer-access/openrouter"),
        ("post", "/api/v1/reviewer-access/openrouter/refresh"),
        ("put", "/api/v1/reviewer-access/selection"),
        ("get", "/api/v1/settings"),
        ("patch", "/api/v1/settings"),
        ("post", "/api/v1/provider-recovery/retry"),
        ("get", "/api/v1/activity"),
        ("get", "/api/v1/activity/permission-readiness"),
        ("get", "/api/v1/activity/consent-state"),
        ("get", "/api/v1/activity/{event_id}/transitions"),
        ("post", "/api/v1/activity/consent"),
        ("post", "/api/v1/activity/{connection}/{library}/revoke"),
        ("get", "/api/v1/attention"),
        ("get", "/api/v1/library"),
        ("get", "/api/v1/library/cover"),
        ("get", "/api/v1/library/report"),
        ("get", "/api/v1/operations/status"),
        ("get", "/api/v1/operations/support/preview"),
        ("post", "/api/v1/operations/support/export"),
        ("get", "/api/v1/field-effects/{plan_id}"),
        (
            "post",
            "/api/v1/review-cases/{review_case_id}/fields/{destination}/return-to-automatic",
        ),
        ("post", "/api/v1/field-effects/{plan_id}/apply"),
        ("post", "/api/v1/field-effects/{plan_id}/reconcile"),
        ("post", "/api/v1/field-effects/pause"),
        ("post", "/api/v1/field-effects/resume"),
        ("post", "/api/v1/metadata-policy/activate"),
        ("post", "/api/v1/metadata-policy/drafts"),
        ("get", "/api/v1/metadata-policy/drafts/{policy_id}"),
        ("post", "/api/v1/metadata-policy/preview"),
        ("get", "/api/v1/metadata-policy/shadow"),
        ("post", "/api/v1/metadata-policy/shadow/enter"),
        ("post", "/api/v1/metadata-policy/shadow/confirm-automatic"),
        ("get", "/api/v1/metadata-policy/shadow/leave-preview"),
        ("get", "/api/v1/metadata-policy/shadow/outcomes"),
        ("get", "/api/v1/metadata-policy/automatic-safety"),
        ("post", "/api/v1/metadata-policy/automatic-safety/pause"),
        ("post", "/api/v1/metadata-policy/automatic-safety/recover"),
        ("post", "/api/v1/metadata-policy/automatic-safety/suspend"),
        ("get", "/api/v1/metadata-policy/state"),
        ("get", "/api/v1/metadata-policy/transition"),
        (
            "post",
            "/api/v1/metadata-policy/transitions/{transition_id}/acknowledge",
        ),
        (
            "post",
            "/api/v1/metadata-policy/transitions/{transition_id}/retry",
        ),
        ("post", "/api/v1/metadata-policy/validate"),
        ("get", "/api/v1/review-preparation"),
        ("post", "/api/v1/review-preparation/dismiss"),
        ("post", "/api/v1/review-preparation/first-boot/acknowledge"),
        # The live invalidation stream is a protected read like any other.
        ("get", "/api/v1/updates"),
        ("get", "/api/v1/review-cases/{review_case_id}"),
        ("get", "/api/v1/review-cases/{review_case_id}/successor"),
        (
            "get",
            "/api/v1/review-cases/{review_case_id}/cover-considerations/{consideration_id}/images/{reference}",
        ),
        (
            "post",
            "/api/v1/review-cases/{review_case_id}/cover-considerations/retry",
        ),
        ("get", "/api/v1/review-cases/{review_case_id}/evidence"),
        ("get", "/api/v1/review-cases/{review_case_id}/history"),
        ("post", "/api/v1/review-cases/{review_case_id}/decisions"),
        (
            "post",
            "/api/v1/review-preparation/{connection}/{library_name}/scan",
        ),
        (
            "post",
            "/api/v1/review-preparation/{connection}/{library_name}/retry",
        ),
        ("post", "/api/v1/review-preparation/{connection}/check-connection"),
        ("post", "/api/v1/review-preparation/pause"),
        ("post", "/api/v1/review-preparation/resume"),
        ("post", "/api/v1/session/logout"),
    }
    response_statuses = {
        (method, path): set(operation["responses"])
        for path, path_item in expected["paths"].items()
        for method, operation in path_item.items()
    }
    assert response_statuses[("post", "/api/v1/session/login")] == {
        "200",
        "400",
        "401",
        "403",
        "413",
        "422",
        "429",
        "503",
    }
    assert response_statuses[("post", "/api/v1/session/logout")] == {
        "204",
        "400",
        "401",
        "403",
        "409",
        "422",
        "503",
    }
    assert response_statuses[
        ("post", "/api/v1/review-cases/{review_case_id}/decisions")
    ] == {"200", "400", "401", "403", "404", "409", "413", "422", "503"}
    for operation in _REVIEWER_MUTATIONS:
        assert "413" in response_statuses[operation]
    for path in (
        "/api/v1/review-cases/{review_case_id}",
        "/api/v1/review-cases/{review_case_id}/successor",
        "/api/v1/review-cases/{review_case_id}/evidence",
        "/api/v1/review-cases/{review_case_id}/history",
    ):
        assert response_statuses[("get", path)] == {
            "200",
            "400",
            "401",
            "404",
            "409",
            "422",
        }
    for path in ("/api/v1/library", "/api/v1/attention"):
        assert response_statuses[("get", path)] == {"200", "400", "401", "422", "503"}
    assert response_statuses[("get", "/api/v1/review-preparation")] == {
        "200",
        "401",
        "503",
    }
    assert response_statuses[("get", "/api/v1/field-effects/{plan_id}")] == {
        "200",
        "401",
        "404",
        "422",
        "503",
    }
    for path in (
        "/api/v1/field-effects/{plan_id}/apply",
        "/api/v1/field-effects/{plan_id}/reconcile",
    ):
        assert response_statuses[("post", path)] == {
            "200",
            "401",
            "403",
            "404",
            "409",
            "422",
            "503",
        }
    for path in (
        "/api/v1/field-effects/pause",
        "/api/v1/field-effects/resume",
    ):
        assert response_statuses[("post", path)] == {
            "204",
            "401",
            "403",
            "409",
            "422",
            "503",
        }
    assert response_statuses[
        (
            "post",
            "/api/v1/review-preparation/{connection}/{library_name}/scan",
        )
    ] == {"202", "400", "401", "403", "404", "409", "422", "503"}
    assert response_statuses[("post", "/api/v1/review-preparation/pause")] == {
        "200",
        "401",
        "403",
        "409",
        "422",
        "503",
    }
    assert response_statuses[("post", "/api/v1/review-preparation/resume")] == {
        "200",
        "401",
        "403",
        "409",
        "422",
        "503",
    }
    assert response_statuses[
        ("post", "/api/v1/review-preparation/{connection}/{library_name}/retry")
    ] == {"202", "401", "403", "404", "409", "422", "503"}
    assert response_statuses[
        ("post", "/api/v1/review-preparation/{connection}/check-connection")
    ] == {"200", "401", "403", "404", "409", "422", "503"}
    headers = {
        value["name"]: value
        for value in decision_operation["parameters"]
        if value["in"] == "header"
    }
    assert headers["x-csrf-token"]["required"]
    assert headers["idempotency-key"]["required"]
    invalid = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        json={"password": "secret", "unexpected": "private-value"},
    )
    assert invalid.status_code == 422
    assert b"private-value" not in invalid.content


def test_openrouter_request_validation_redacts_the_write_only_key() -> None:
    from mechanical_librarian.interfaces.web.app import _OpenRouterWriteRequest

    canary = "secret-canary-" + "x" * 2048
    request = _OpenRouterWriteRequest.model_validate(
        {"expected_revision": 1, "api_key": canary, "model": "example/vision"}
    )
    assert canary not in str(request)
    assert canary not in repr(request.model_dump())


def test_parallel_failures_reserve_the_five_attempt_bound(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    def attempt(_: int) -> int:
        return web.client.post(
            "/api/v1/session/login",
            headers=web.origin,
            json={"password": "wrong"},
        ).status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(attempt, range(6)))
    assert sorted(statuses) == [401, 401, 401, 401, 401, 429]
    assert len(web.repository.failures) == 5


def test_login_body_is_bounded_before_json_parsing_or_throttling(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    oversized = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        content=b'{"password":"' + b"x" * 4096 + b'"}',
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "body_too_large"
    assert web.repository.failures == []
    streamed = web.client.post(
        "/api/v1/session/login",
        headers=web.origin,
        content=iter([b'{"password":"', b"x" * 4096, b'"}']),
    )
    assert streamed.status_code == 413
    assert web.repository.failures == []


def test_browser_asset_loads_all_read_models_without_html_injection(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    script = web.client.get("/assets/review-app/assets/index-deadbeef.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")


def test_argon2i_hash_file_is_rejected(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    web.client.close()
    web.hash_path.write_text(
        PasswordHasher(type=Type.I).hash("correct horse"), encoding="utf-8"
    )
    os.chmod(web.hash_path, 0o600)
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
    )
    with pytest.raises(ReviewWebError):
        create_transport_test_app(web.repository, configuration)


def test_composition_uses_one_application_owner_for_worker_threads(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    from threading import RLock

    from mechanical_librarian.application.operation_admission import OperationAdmission
    from tests.support.tls import synthetic_tls_identity

    admission = OperationAdmission()
    repository = Mock()
    repository.operation_admission = admission
    repository.operation_lock = RLock()
    repository.reviewer_access_store.return_value.read.return_value = (
        SimpleNamespace(cover_observation=None),
        False,
    )
    repository.reviewer_access_store.return_value.snapshot.return_value = (
        SimpleNamespace(cover_observation=None),
        None,
    )
    database = Path(web.temporary.name) / "application.sqlite3"
    certificate, private_key = synthetic_tls_identity(Path(web.temporary.name))
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        tls_certificate_file=str(certificate),
        tls_private_key_file=str(private_key),
    )
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.OperationAdmission",
            return_value=admission,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=ApplicationConfiguration(
                review=configuration,
                connections=(),
                providers=(),
                libraries=(),
                mounts=(),
            ),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ) as opened,
        patch(
            "mechanical_librarian.bootstrap.composition.file_settings_projection",
            return_value=SimpleNamespace(document=Mock(), secrets=()),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.create_review_app",
            return_value=object(),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.create_review_pipeline",
            return_value=Mock(),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.CoverArtifactStore",
            return_value=Mock(),
        ),
        patch("mechanical_librarian.bootstrap.composition.uvicorn.run"),
    ):
        run_review_server(
            Path("/config.json"),
            database,
            Path(web.temporary.name) / "catalogue.sqlite3",
            Path(web.temporary.name) / "artifacts",
            "127.0.0.1",
            8443,
        )
    assert opened.call_args_list == [
        call(
            database,
            create=True,
            cross_thread=True,
            admission=admission,
            recover_stale_bindings=True,
        ),
    ]


def test_store_composition_fault_preserves_pending_restart_acknowledgement(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    repository = Mock()
    store = repository.owner_settings_store.return_value
    document = Mock(revision=7, pending_restart=["libraries"])
    store.activate_runtime.return_value = (
        SimpleNamespace(document=document),
        {"connection.primary.key": "synthetic-secret"},
        web.hash_path.read_text(encoding="utf-8").strip(),
    )
    review = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        tls_certificate_file="/synthetic/certificate.pem",
        tls_private_key_file="/synthetic/private-key.pem",
    )
    configuration = ApplicationConfiguration(
        review=review, connections=(), providers=(), libraries=(), mounts=()
    )
    tls = SimpleNamespace(
        certificate_file="/synthetic/certificate.pem",
        private_key_file="/synthetic/private-key.pem",
    )
    with (
        patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            side_effect=FileNotFoundError,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.load_review_bootstrap",
            return_value=SimpleNamespace(
                tls_certificate_file=Path(tls.certificate_file),
                tls_private_key_file=Path(tls.private_key_file),
            ),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_review_tls_identity",
            return_value=nullcontext(tls),
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.open_repository",
            return_value=repository,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.store_runtime_configuration",
            return_value=configuration,
        ),
        patch(
            "mechanical_librarian.bootstrap.composition.create_review_pipeline",
            side_effect=RuntimeError("synthetic later composition fault"),
        ),
    ):
        with pytest.raises(RuntimeError, match="later composition fault"):
            run_review_server(
                Path("/data/config.json"),
                Path(web.temporary.name) / "application.sqlite3",
                Path("/synthetic/catalogue.sqlite3"),
                Path("/synthetic/artifacts"),
                "127.0.0.1",
                18444,
            )

    store.acknowledge_runtime.assert_not_called()


def test_served_composition_keeps_automatic_safety_reachable(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # Every other test in this suite hands `compose_review_application` a
    # repository directly, so nothing exercised what the server actually
    # composes. `AutomaticSafetyControls` duck-types through `object` and
    # converts a missing member into "automatic safety is unavailable", so a
    # composition that dropped the safety seam read as a domain error rather
    # than a wiring fault and every safety read and mutation failed silently.
    from tests.support.tls import synthetic_tls_identity

    root = Path(web.temporary.name)
    database = root / "application.sqlite3"
    private = root / "private"
    private.mkdir()
    os.chmod(private, 0o700)
    repository = open_repository(database)
    certificate, private_key = synthetic_tls_identity(root)
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        tls_certificate_file=str(certificate),
        tls_private_key_file=str(private_key),
    )
    composed: list[object] = []
    try:
        with (
            patch(
                "mechanical_librarian.bootstrap.composition.load_configuration_file",
                return_value=ApplicationConfiguration(
                    review=configuration,
                    connections=(),
                    providers=(),
                    libraries=(),
                    mounts=(),
                ),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.open_repository",
                return_value=repository,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.file_settings_projection",
                return_value=SimpleNamespace(document=Mock(), secrets=()),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.create_review_app",
                side_effect=lambda application, _: composed.append(application),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.create_review_pipeline",
                return_value=Mock(),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.CoverArtifactStore",
                return_value=Mock(),
            ),
            patch("mechanical_librarian.bootstrap.composition.uvicorn.run"),
        ):
            run_review_server(
                Path("/config.json"),
                database,
                root / "catalogue.sqlite3",
                root / "artifacts",
                "127.0.0.1",
                8443,
            )
        application = composed[0]
        safety = application.automatic_safety
        assert not safety.state().paused
        safety.pause(True, 5)
        assert safety.state().paused
    finally:
        repository.close()


def test_first_boot_acknowledgement_is_authenticated_csrf_bound_and_durable(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    acknowledgements: list[str] = []
    web.client.close()
    app = create_transport_test_app(
        web.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin="https://review.test",
            owner_password_hash_file=str(web.hash_path),
        ),
        first_boot=True,
        acknowledge_first_boot=lambda: acknowledgements.append("acknowledged"),
    )
    web.client = TestClient(app, base_url="https://review.test")
    web.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    anonymous = web.client.post(
        "/api/v1/review-preparation/first-boot/acknowledge",
        headers={**web.origin, "x-csrf-token": "x" * 32},
    )
    assert anonymous.status_code == 401
    login = web.login()
    wrong_csrf = web.client.post(
        "/api/v1/review-preparation/first-boot/acknowledge",
        headers={**web.origin, "x-csrf-token": "x" * 32},
    )
    assert wrong_csrf.status_code == 403
    acknowledged = web.client.post(
        "/api/v1/review-preparation/first-boot/acknowledge",
        headers={**web.origin, "x-csrf-token": str(login["csrf_token"])},
    )
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["first_boot"] is False
    assert acknowledgements == ["acknowledged"]


@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
def test_browser_transport_cookie_hsts_and_session_contract(mode: str) -> None:
    context = review_web_context(FakeReviewRepository, mode=mode)
    web = next(context)
    try:
        secure = mode != "http"
        for path, status in [
            ("/login", 200),
            ("/api/v1/library", 401),
            ("/health/ready", 200),
        ]:
            response = web.client.get(path)
            assert response.status_code == status
            assert ("strict-transport-security" in response.headers) is secure
        response = web.client.post(
            "/api/v1/session/login",
            headers=web.origin,
            json={"password": "correct horse"},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert ("; Secure" in cookie) is secure
        assert (
            "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
        )
        assert "Max-Age=600" in cookie
        # A real TLS proxy forwards this Secure cookie to its HTTP backend.
        web.client.headers["cookie"] = cookie.split(";", 1)[0]
        web.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
        assert web.client.get("/api/v1/library").status_code == 200
        logout = web.client.post(
            "/api/v1/session/logout",
            headers={**web.origin, "x-csrf-token": response.json()["csrf_token"]},
        )
        assert logout.status_code == 204
        assert ("; Secure" in logout.headers["set-cookie"]) is secure
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert web.client.get("/api/v1/library").status_code == 401
    finally:
        context.close()
        web.close()


@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
@pytest.mark.parametrize(
    "failure", ["host", "origin", "validation", "declared", "streamed"]
)
def test_browser_transport_hsts_in_early_failures(mode: str, failure: str) -> None:
    context = review_web_context(FakeReviewRepository, mode=mode)
    web = next(context)
    try:
        headers = dict(web.origin)
        if failure == "host":
            headers["host"] = "wrong.invalid"
            if mode == "proxy":
                headers["x-forwarded-host"] = "wrong.invalid"
        if failure == "origin":
            headers["Origin"] = "https://wrong.invalid"
        if failure == "declared":
            headers["content-length"] = "999999"
        if failure == "streamed":
            response = web.client.post(
                "/api/v1/session/login", headers=headers, content=iter([b"x" * 2049])
            )
        else:
            response = web.client.post(
                "/api/v1/session/login", headers=headers, json={}
            )
        assert (
            response.status_code
            == {
                "host": 400,
                "origin": 403,
                "validation": 422,
                "declared": 413,
                "streamed": 413,
            }[failure]
        )
        assert ("strict-transport-security" in response.headers) is (mode != "http")
        assert response.headers["x-frame-options"] == "DENY"
    finally:
        context.close()
        web.close()


@pytest.mark.parametrize("peer", ["127.0.0.1", "192.0.2.1", None])
@pytest.mark.parametrize(
    "headers",
    [
        [("host", "review.test")],
        [("host", "review.test"), ("x-forwarded-host", "review.test")],
        [("host", "review.test"), ("x-forwarded-proto", "https")],
        [
            ("host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-proto", "https"),
        ],
        [
            ("host", "review.test"),
            ("host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-proto", "https"),
        ],
        [
            ("host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-proto", "https"),
        ],
        [
            ("host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-proto", "https"),
            ("x-forwarded-proto", "https"),
        ],
        [
            ("host", "review.test"),
            ("x-forwarded-host", "review.test,other"),
            ("x-forwarded-proto", "https"),
        ],
        [
            ("host", "review.test"),
            ("x-forwarded-host", "review.test"),
            ("x-forwarded-proto", "https"),
            ("forwarded", "proto=https"),
        ],
    ],
)
def test_raw_proxy_admission_is_exact(
    peer: str | None, headers: list[tuple[str, str]]
) -> None:
    from starlette.requests import Request

    from mechanical_librarian.interfaces.web.app import _request_matches_origin

    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "path": "/login",
            "headers": [(k.encode(), v.encode()) for k, v in headers],
            "client": None if peer is None else (peer, 12345),
            "server": ("review.test", 80),
        }
    )
    expected = peer == "127.0.0.1" and headers == [
        ("host", "review.test"),
        ("x-forwarded-host", "review.test"),
        ("x-forwarded-proto", "https"),
    ]
    assert (
        _request_matches_origin(
            request, "https://review.test", ("127.0.0.1",), require_forwarding=True
        )
        is expected
    )


@pytest.mark.parametrize(
    "fault",
    [
        "missing-selection",
        "missing-snapshot",
        "binding",
        "candidate",
        "extra",
        "outcome",
    ],
)
def test_description_response_rejects_mixed_selected_snapshot(tmp_path, fault):
    from copy import deepcopy

    from pydantic import ValidationError

    from mechanical_librarian.interfaces.web.app import _DescriptionAdviceResponse
    from tests.support.description_advice import _prepared

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
    advice = store.read(binding.review_case_id)
    assert _DescriptionAdviceResponse.model_validate(advice).selected_advice is not None
    changed = deepcopy(advice)
    if fault == "missing-selection":
        changed["selection"] = None
    elif fault == "missing-snapshot":
        changed["selected_advice"] = None
    elif fault == "binding":
        changed["selected_advice"]["binding"]["access_revision"] += 1
    elif fault == "candidate":
        changed["selected_advice"]["candidate"]["candidate_id"] = (
            "description_candidate_" + "f" * 64
        )
    elif fault == "extra":
        changed["selected_advice"]["extra"] = True
    else:
        changed.update(
            selection=None, selected_advice=None, application_outcome="verified"
        )
    with pytest.raises(ValidationError):
        _DescriptionAdviceResponse.model_validate(changed)
    repository.close()


def test_current_audiobook_qualification_inventory_crosses_closed_api_contract():
    from pydantic import ValidationError

    from mechanical_librarian.domain.audiobook_decision_qualification import (
        public_summary,
    )
    from mechanical_librarian.interfaces.web.app import _AudiobookQualification

    records = public_summary()
    for record in records:
        assert _AudiobookQualification.model_validate(record).model_dump() == record
    assert {record["destination"] for record in records} >= {"isbn", "explicit"}
    assert {record["option"] for record in records} >= {
        "adult_product",
        "prefer_isbn13",
        "replace_descriptive",
    }
    for field in ("destination", "field", "option", "operation"):
        with pytest.raises(ValidationError):
            _AudiobookQualification.model_validate({**records[0], field: "unknown"})

    for identity in (
        "bad1:preserve:audiobook_title_fill",
        "title_layout:preserve:bad1",
        "title_layout:bad-option:audiobook_title_fill",
    ):
        with pytest.raises(ValidationError):
            _AudiobookQualification.model_validate({**records[0], "identity": identity})


def test_shadow_sample_response_refuses_genuine_predecessor_outcome():
    from pydantic import ValidationError

    from mechanical_librarian.interfaces.web.app import _ShadowSampleResponse

    document = Path("tests/support/audiobook_predecessors/outcome.json").read_bytes()
    outcome = json.loads(document)
    assert outcome["schema_version"] == "shadow-outcome-set-v5"
    sample = {
        "authority": "none",
        "items": [{**outcome, "evaluated_at": 2000}],
        "next_cursor": None,
        "schema_version": "shadow-outcome-sample-v1",
    }
    with pytest.raises(ValidationError) as failure:
        _ShadowSampleResponse.model_validate(sample)
    assert any(
        error["loc"] == ("items", 0, "schema_version")
        and error["type"] == "literal_error"
        for error in failure.value.errors()
    )
    assert (
        Path("tests/support/audiobook_predecessors/outcome.json").read_bytes()
        == document
    )


@pytest.mark.parametrize("release", ["title", "pause", "global"])
def test_owner_title_recovery_route_requeues_actual_completed_work(
    tmp_path, web: ReviewWebContext[FakeReviewRepository], release: str
):
    from mechanical_librarian.application.live_updates import LiveUpdateChannel
    from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
    from tests.support.evidence_led_titles import complete_title_work, isbn_title_report

    repository = open_repository(tmp_path / "state.sqlite", cross_thread=True)
    case_id, _ = complete_title_work(
        repository,
        isbn_title_report(current="Example Title"),
        safety=AutomaticSafetyState(
            release == "pause",
            "retained_global" if release == "global" else None,
            (("title_layout", "automatic_admission_failed"),)
            if release == "title"
            else (),
        ),
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            repository,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
            ),
            clock=lambda: web.now,
        ),
        base_url="https://review.test",
    )
    context = web
    try:
        route = "/api/v1/metadata-policy/automatic-safety/" + (
            "pause" if release == "pause" else "recover"
        )
        body = (
            {"paused": False}
            if release == "pause"
            else {"category": "title_layout" if release == "title" else None}
        )
        assert (
            context.client.post(route, headers=context.origin, json=body).status_code
            == 401
        )
        login = context.login()
        assert (
            context.client.post(route, headers=context.origin, json=body).status_code
            == 403
        )
        published: list[str] = []
        publish = LiveUpdateChannel.publish

        def observe(
            channel: LiveUpdateChannel, topic: str, subject: str | None = None
        ) -> int:
            published.append(topic)
            return publish(channel, topic, subject)

        with patch.object(LiveUpdateChannel, "publish", observe):
            response = context.client.post(
                route,
                headers={**context.origin, "x-csrf-token": login["csrf_token"]},
                json=body,
            )
        assert published == ["preparation"]
        assert response.status_code == 200
        assert response.json()["category_suspensions"] == {}
        action = repository.claim_review_pipeline_action(1001)
        assert action is not None and action.stage == "projection"
        repository.persist_shadow_outcome(case_id, 1002, staged_action=action)
        repository.advance_review_pipeline_action(action, None, 1003)
        assert repository.claim_review_pipeline_action(1004) is None
        assert len(repository.get_review_case_decisions(case_id)) == 1
    finally:
        repository.close()
