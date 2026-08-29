"""Review web read models: preparation and activity controls, policy
and shadow surfaces, cover omission, evidence and choice
projections, and the Library and Attention lenses.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    InspectCurrentReviewCase,
    ReviewWebError,
    _attention_item,
    _case_and_report,
    _choice_contexts,
    _choice_limitations,
    _choice_observation,
    _choice_rationale,
    _choice_source_label,
    _current_value_present,
    _decode_cursor,
    _encode_cursor,
    _evidence_by_reference,
    _field_inventory_role,
    _library_item,
    _provider_comparison_notices,
    _provider_label,
)
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web.app import _CoverOmission
from tests.support.proposal_evidence import (
    _claim as _proposal_claim,
)
from tests.support.proposal_evidence import (
    _provider as _proposal_provider,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.review_cases import _report
from tests.support.review_web_doubles import (
    FakeActivityApplication,
    FakePreparation,
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
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


class _CloseRecordingRepository(FakeReviewRepository):
    """The fake repository with the transport-owned close recorded, not run.

    The fake declares no `close` of its own, and the two shutdown proofs
    below need one that records the call. Declaring it here keeps the seam a
    real member of a local double rather than an assignment onto a bound
    name, so a rename reaches this file instead of passing silently.
    """

    def __init__(self, case: ReviewCase, report) -> None:
        super().__init__(case, report)
        self.closes: list[bool] = []

    def close(self) -> None:
        self.closes.append(True)


@pytest.fixture
def web() -> Iterator[ReviewWebContext[_CloseRecordingRepository]]:
    yield from review_web_context(_CloseRecordingRepository)


def test_cover_omission_response_models_every_valid_shape() -> None:
    duplicate = _CoverOmission.model_validate(
        {
            "preview_sha256": "a" * 64,
            "reason": "duplicate_preview",
            "reference": "cover_ref_" + "b" * 64,
            "retained_reference": "cover_ref_" + "c" * 64,
        }
    )
    bounded = _CoverOmission.model_validate(
        {
            "preview_bytes": 1_048_577,
            "reason": "preview_byte_limit",
            "reference": "cover_ref_" + "d" * 64,
        }
    )
    unavailable = _CoverOmission.model_validate(
        {
            "reason": "source_unavailable",
            "reference": "cover_ref_" + "e" * 64,
        }
    )

    assert duplicate.preview_sha256 == "a" * 64
    assert duplicate.retained_reference == "cover_ref_" + "c" * 64
    assert bounded.preview_bytes == 1_048_577
    assert unavailable.preview_bytes is None


def test_cover_transport_retry_and_owner_outcome_are_protected_end_to_end(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    payloads = (_cover_preview(1), _cover_preview(2))
    sources = (
        _cover_source(1, payloads[0], incumbent=True),
        _cover_source(2, payloads[1], embedded=True),
    )
    base = _cover_report(sources)
    incumbent_artifact = sources[0].artifact
    assert incumbent_artifact is not None
    report = MetadataReport.create(
        target=TargetFacts(
            base.target.connection,
            base.target.library,
            base.target.item,
            tuple(
                TargetField(
                    field.destination,
                    TargetFieldState.VALUE,
                    incumbent_artifact.artifact_id,
                )
                if field.destination is TargetDestination.COVER
                else field
                for field in base.target.fields
            ),
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=base.cover_sources,
    )
    case = ReviewCase.create(evaluate_report(report), report)
    repository = FakeReviewRepository(case, report)
    candidate_reference = "cover_ref_" + "a" * 64
    consideration_id = "cover_consideration_" + "b" * 64
    view = {
        "candidates": [
            {
                "artifact_id": "cover_" + "c" * 64,
                "deterministic_citations": [candidate_reference],
                "deterministic_rank": None,
                "deterministic_rank_key": [
                    1,
                    0,
                    -960000,
                    -800,
                    -1200,
                    0,
                    candidate_reference,
                ],
                "height": 1200,
                "identity": {
                    "candidate_reference": candidate_reference,
                    "evidence_references": [],
                    "outcome": "verified_work_match",
                    "preview_reference": "cover_preview_" + "d" * 64,
                    "reasons": [],
                },
                "incumbent": True,
                "match_state": "same_book",
                "observation": None,
                "original_sha256": "e" * 64,
                "ordinal": 0,
                "preview_sha256": "f" * 64,
                "reference": candidate_reference,
                "selectable": False,
                "source_kinds": ["abs"],
                "state": "available",
                "transport_sha256": "1" * 64,
                "width": 800,
            }
        ],
        "consideration_id": consideration_id,
        "choice_contexts": [
            {
                "alternative_id": None,
                "evidence": [
                    {
                        "observation": "Local library cover retained.",
                        "role": "local_presence",
                        "source": "Local library record",
                    }
                ],
                "limitations": [],
                "rationale": "Keep the retained local cover.",
                "recommendation": "no_recommendation",
                "recommendation_reason": "No deterministic recommendation is retained for the current cover.",
                "selection": "current",
            }
        ],
        "current": True,
        "incumbent": {"reference": candidate_reference, "state": "available"},
        "omissions": [],
        "policy": None,
        "policy_preferences": {
            "avoid_promotional_text": False,
            "avoid_tie_in_branding": False,
            "prefer_minimal_composition": False,
            "schema_version": "cover-policy-v1",
        },
        "policy_reference": "cover_policy_" + "2" * 64,
        "provider_fetching": False,
        "ready": False,
        "reviewer_access_revision": None,
        "reviewer_engine": None,
        "reviewer_model": None,
        "reviewer_status": "unavailable",
        "reviewer_unavailable_reason": None,
        "schema_version": "cover-consideration-v3",
        "supersedes_id": None,
        "target_fingerprint": case.target_fingerprint,
    }
    covers = Mock()
    covers.project.return_value = view
    covers.ensure.return_value = view
    covers.image.return_value = b"\x89PNG\r\n\x1a\nsynthetic"
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            repository,
            configuration,
            clock=lambda: web.now,
            cover_considerations=covers,
        ),
        base_url="https://review.test",
    )
    image_path = (
        f"/api/v1/review-cases/{case.review_case_id}"
        f"/cover-considerations/{consideration_id}/images/{candidate_reference}"
    )
    assert web.client.get(image_path).status_code == 401
    login = web.login()
    image = web.client.get(image_path)
    assert 200 == image.status_code
    assert "image/png" == image.headers["content-type"]
    assert {"no-store", "private"} == {
        value.strip() for value in image.headers["cache-control"].split(",")
    }
    assert "nosniff" == image.headers["x-content-type-options"]
    retry_path = (
        f"/api/v1/review-cases/{case.review_case_id}/cover-considerations/retry"
    )
    assert web.client.post(retry_path, headers=web.origin).status_code == 422
    denied = web.client.post(
        retry_path,
        headers={
            "Origin": "https://hostile.test",
            "X-CSRF-Token": str(login["csrf_token"]),
        },
    )
    assert 403 == denied.status_code
    retried = web.client.post(
        retry_path,
        headers={
            **web.origin,
            "X-CSRF-Token": str(login["csrf_token"]),
        },
    )
    assert 200 == retried.status_code, retried.text
    detail = web.client.get(f"/api/v1/review-cases/{case.review_case_id}").json()
    cover = next(field for field in detail["fields"] if field["destination"] == "cover")
    decision = web.client.post(
        f"/api/v1/review-cases/{case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": str(login["csrf_token"]),
            "Idempotency-Key": "protected-cover-outcome-0001",
        },
        json={
            "consideration_id": consideration_id,
            "destination": "cover",
            "outcome": "keep_current",
            "proposal_id": cover["proposal_id"],
            "selected_value": None,
            "target_fingerprint": cover["target_fingerprint"],
        },
    )
    assert 200 == decision.status_code, decision.text
    covers.validate_decision.assert_called_once()


def test_shutdown_timeout_preserves_shared_repository_for_live_worker(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    class _DrainingApplication:
        started = False

        def attach_timing(self, timing) -> None:
            return None

        def start(self) -> None:
            self.started = True

        def begin_drain(self) -> None:
            raise GuardedApplicationError("shutdown_timeout")

    application = _DrainingApplication()
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    app = create_transport_test_app(
        web.repository,
        configuration,
        close_repository=True,
        field_application=application,
    )
    with pytest.raises(GuardedApplicationError, match="shutdown_timeout"):
        with TestClient(app, base_url="https://review.test"):
            pass
    assert application.started
    assert web.repository.closes == []


@pytest.mark.parametrize("failing_worker", ("field", "preparation"))
def test_shutdown_closes_both_admissions_before_one_shared_deadline(
    web: ReviewWebContext[_CloseRecordingRepository], failing_worker: str
) -> None:
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=1,
    )
    calls = []

    class _FieldApplication(FakeActivityApplication):
        def start(self):
            calls.append("field-start")
            return (0, 0, 0)

        def close_admission(self) -> None:
            calls.append("field-close")

        def wait_for_drain(self, deadline=None) -> None:
            calls.append(("field-wait", deadline))
            if failing_worker == "field":
                raise GuardedApplicationError("shutdown_timeout")

    class FakePreparation:
        def attach_updates(self, announce: object) -> None:
            return None

        def start(self) -> None:
            calls.append("preparation-start")

        def begin_drain(self) -> None:
            calls.append("preparation-close")

        def stop(self, deadline=None) -> None:
            calls.append(("preparation-wait", deadline))
            if failing_worker == "preparation":
                raise ReviewPipelineError("shutdown_timeout")

    app = create_transport_test_app(
        web.repository,
        configuration,
        close_repository=True,
        field_application=_FieldApplication(),
        review_preparation=FakePreparation(),
    )
    with pytest.raises(Exception, match="shutdown_timeout"):
        with TestClient(app, base_url="https://review.test"):
            pass
    field_close = calls.index("field-close")
    preparation_close = calls.index("preparation-close")
    field_wait = next(
        index
        for index, value in enumerate(calls)
        if isinstance(value, tuple) and value[0] == "field-wait"
    )
    preparation_wait = next(
        index
        for index, value in enumerate(calls)
        if isinstance(value, tuple) and value[0] == "preparation-wait"
    )
    assert field_close < field_wait
    assert preparation_close < field_wait
    assert field_close < preparation_wait
    assert preparation_close < preparation_wait
    deadlines = [
        value[1]
        for value in calls
        if isinstance(value, tuple) and value[0].endswith("wait")
    ]
    assert len(deadlines) == 2
    assert deadlines[0] == deadlines[1]
    assert web.repository.closes == []


def test_activity_is_permanent_and_consent_is_csrf_bound(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    application = FakeActivityApplication()
    configuration = ReviewWebConfiguration(
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
            field_application=application,
        ),
        base_url="https://review.test",
    )
    login = web.login()
    page = web.client.get("/activity")
    assert page.status_code == 200
    assert f'data-build-identity="{TEST_REACT_BUILD_IDENTITY}"' in page.text
    script = web.client.get("/assets/review-app/assets/index-deadbeef.js")
    assert script.status_code == 200
    assert web.client.get("/api/v1/activity").json()["prepared"]["total"] == 2
    filtered = web.client.get(
        "/api/v1/activity?event_type=all&range_days=30&page=1&page_size=25"
    )
    assert filtered.status_code == 200, filtered.text
    # The ledger block is the projection's own, echoed unchanged. Comparing
    # against the projection rather than a copied literal keeps this a proof
    # that the transport does not reshape it, and stops the seeded ledger's
    # size from being restated in a second place that can drift from it.
    ledger = filtered.json()["ledger"]
    assert (
        ledger
        == application.activity(event_type="all", range_days=30, page=1, page_size=25)[
            "ledger"
        ]
    )
    assert ledger["page_count"] > 1, "the seeded ledger must cross a page boundary"
    assert (
        ledger["event_type"],
        ledger["page"],
        ledger["page_size"],
        ledger["query"],
        ledger["range_days"],
    ) == ("all", 1, 25, None, 30)
    second_page = web.client.get(
        "/api/v1/activity?event_type=all&range_days=30&page=2&page_size=25"
    )
    assert second_page.status_code == 200, second_page.text
    second_ledger = second_page.json()["ledger"]
    assert second_ledger["page"] == 2
    assert second_ledger["page_count"] == ledger["page_count"]
    assert second_page.json()["events"]
    assert {event["event_id"] for event in filtered.json()["events"]}.isdisjoint(
        {event["event_id"] for event in second_page.json()["events"]}
    )
    assert web.client.get("/api/v1/activity?range_days=8").status_code == 422
    denied = web.client.post(
        "/api/v1/activity/consent",
        headers={**web.origin, "X-CSRF-Token": "invalid"},
        json={"grants": [{"library": "fiction", "destinations": ["title"]}]},
    )
    assert denied.status_code == 403
    login = web.login()
    enabled = web.client.post(
        "/api/v1/activity/consent",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
        json={"grants": [{"library": "fiction", "destinations": ["title"]}]},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled_libraries"][0]["library"] == "fiction"
    later = web.client.post(
        "/api/v1/activity/consent",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
        json={"grants": [{"library": "history", "destinations": ["title"]}]},
    )
    assert later.status_code == 200
    assert {item["library"] for item in later.json()["enabled_libraries"]} == {
        "fiction",
        "history",
    }
    revoked = web.client.post(
        "/api/v1/activity/primary/fiction/revoke",
        headers={**web.origin, "X-CSRF-Token": login["csrf_token"]},
    )
    assert revoked.status_code == 200
    assert [item["library"] for item in revoked.json()["enabled_libraries"]] == [
        "history"
    ]


def test_review_preparation_is_authenticated_csrf_bound_and_bounded(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    preparation = FakePreparation()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            review_preparation=preparation,
        ),
        base_url="https://review.test",
    )
    anonymous = web.client.get("/api/v1/review-preparation")
    assert anonymous.status_code == 401
    session = web.login()
    status = web.client.get("/api/v1/review-preparation")
    assert status.status_code == 200
    assert preparation.status_calls == 1
    assert status.json()["active"]
    assert not status.json()["paused"]
    assert status.json()["libraries"][0] == {
        "blocked": 0,
        "checked": 2,
        "connection": "primary",
        "library": "fiction",
        "library_id": "lib-1",
        "ready": 1,
        "rescan_requested": False,
        "scan_id": "review_scan_" + "a" * 64,
        # Every bucket #641 names, in owner reading order, carried whole rather
        # than collapsed into the three "how far through" numbers beside it.
        "stages": [
            {"stage": "queued", "count": 1},
            {"stage": "extracting", "count": 1},
            {"stage": "providers", "count": 1},
            {"stage": "proposals", "count": 0},
            {"stage": "admission", "count": 0},
            {"stage": "projection", "count": 0},
            {"stage": "ready", "count": 1},
            {"stage": "failed", "count": 1},
        ],
        "state": "processing",
        "total": 5,
    }
    check_missing_csrf = web.client.post(
        "/api/v1/review-preparation/primary/check-connection",
        headers=web.origin,
    )
    assert check_missing_csrf.status_code == 422
    checked = web.client.post(
        "/api/v1/review-preparation/primary/check-connection",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert checked.status_code == 200
    assert checked.json()["connections"] == []
    missing_csrf = web.client.post(
        "/api/v1/review-preparation/primary/fiction/scan",
        headers=web.origin,
    )
    assert missing_csrf.status_code == 422
    requested = web.client.post(
        "/api/v1/review-preparation/primary/fiction/scan",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert requested.status_code == 202
    assert requested.json()["state"] == "processing"
    assert preparation.requests == [
        ("primary", "fiction", PreparationTrigger.MANUAL_SCAN)
    ]
    retried = web.client.post(
        "/api/v1/review-preparation/primary/fiction/retry",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert retried.status_code == 202
    with patch.object(
        preparation, "request", side_effect=ReviewPipelineError("paused")
    ):
        paused_request = web.client.post(
            "/api/v1/review-preparation/primary/fiction/scan",
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
    assert paused_request.status_code == 409
    assert paused_request.json()["error"]["code"] == "paused"
    with patch.object(preparation, "retry", side_effect=ReviewPipelineError("retry")):
        unavailable_retry = web.client.post(
            "/api/v1/review-preparation/primary/fiction/retry",
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
    assert unavailable_retry.status_code == 409
    assert unavailable_retry.json()["error"]["code"] == "retry"
    paused = web.client.post(
        "/api/v1/review-preparation/pause",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert paused.status_code == 200
    assert paused.json()["paused"]
    resumed = web.client.post(
        "/api/v1/review-preparation/resume",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert resumed.status_code == 200
    assert not resumed.json()["paused"]
    with patch.object(FakePreparation, "set_paused", side_effect=RuntimeError):
        failed = web.client.post(
            "/api/v1/review-preparation/pause",
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "persistence"


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/review-preparation/primary/fiction/scan",
        "/api/v1/review-preparation/primary/fiction/retry",
        "/api/v1/review-preparation/pause",
        "/api/v1/review-preparation/resume",
    ),
    ids=("scan", "retry", "pause", "resume"),
)
def test_unconfigured_review_preparation_controls_remain_unavailable(
    web: ReviewWebContext[_CloseRecordingRepository], path: str
) -> None:
    session = web.login()
    headers = {**web.origin, "x-csrf-token": session["csrf_token"]}
    response = web.client.post(path, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"


@pytest.mark.parametrize(
    ("path", "method"),
    (
        ("/api/v1/review-preparation/primary/fiction/scan", "request"),
        ("/api/v1/review-preparation/primary/fiction/retry", "retry"),
        ("/api/v1/review-preparation/pause", "set_paused"),
        ("/api/v1/review-preparation/resume", "set_paused"),
    ),
    ids=("scan", "retry", "pause", "resume"),
)
def test_review_preparation_control_runtime_failures_remain_persistence(
    web: ReviewWebContext[_CloseRecordingRepository], path: str, method: str
) -> None:
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    preparation = FakePreparation()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            review_preparation=preparation,
        ),
        base_url="https://review.test",
    )
    session = web.login()
    headers = {**web.origin, "x-csrf-token": session["csrf_token"]}
    with patch.object(preparation, method, side_effect=RuntimeError):
        response = web.client.post(path, headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "persistence"


def test_policy_mutations_return_specific_bounded_problems_and_fail_closed(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    anonymous = web.client.post(
        "/api/v1/metadata-policy/validate", headers=web.origin, json={}
    )
    assert 401 == anonymous.status_code
    login = web.login()
    headers = {**web.origin, "X-CSRF-Token": login["csrf_token"]}
    active = web.client.get("/api/v1/metadata-policy/state")
    payload = active.json()["profile"]
    del payload["schema_version"]
    payload["name"] = "Draft"
    payload["options"]["removed"] = "unknown"
    invalid = web.client.post(
        "/api/v1/metadata-policy/validate", headers=headers, json=payload
    )
    assert 422 == invalid.status_code
    assert "policy_option_unavailable" == invalid.json()["error"]["code"]
    assert "review-error-v1" == invalid.json()["schema_version"]
    assert "no-store, private" == invalid.headers["cache-control"]
    valid_payload = active.json()["profile"]
    del valid_payload["schema_version"]
    denied = web.client.post(
        "/api/v1/metadata-policy/preview", headers=web.origin, json=valid_payload
    )
    assert 403 == denied.status_code
    assert "csrf" == denied.json()["error"]["code"]


def test_policy_routes_are_closed_read_only_and_schema_deterministic(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    login = web.login()
    headers = {**web.origin, "X-CSRF-Token": login["csrf_token"]}
    active = web.client.get("/api/v1/metadata-policy/state")
    assert 200 == active.status_code
    payload = active.json()["profile"]
    del payload["schema_version"]
    payload["name"] = "<hostile draft>"
    payload["options"]["date_precision"] = "full_date"
    before = (dict(web.repository.decisions), dict(web.repository.policy_documents))
    validated = web.client.post(
        "/api/v1/metadata-policy/validate", headers=headers, json=payload
    )
    assert 200 == validated.status_code
    assert "metadata-policy-notebook-v1" == validated.json()["schema_version"]
    created = web.client.post(
        "/api/v1/metadata-policy/drafts", headers=headers, json=payload
    )
    assert 201 == created.status_code
    policy_id = created.json()["policy_id"]
    read = web.client.get(f"/api/v1/metadata-policy/drafts/{policy_id}")
    assert validated.json() == read.json()
    preview = web.client.post(
        "/api/v1/metadata-policy/preview", headers=headers, json=payload
    )
    assert 200 == preview.status_code
    assert "metadata-policy-preview-v1" == preview.json()["schema_version"]
    denied_activation = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=web.origin,
        json={
            "policy_id": policy_id,
            "expected_active_policy_id": active.json()["policy_id"],
        },
    )
    assert 403 == denied_activation.status_code
    activated = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=headers,
        json={
            "policy_id": policy_id,
            "expected_active_policy_id": active.json()["policy_id"],
        },
    )
    assert 200 == activated.status_code
    assert "metadata-policy-activation-v1" == activated.json()["schema_version"]
    assert (
        policy_id == web.client.get("/api/v1/metadata-policy/state").json()["policy_id"]
    )
    transition = web.client.get("/api/v1/metadata-policy/transition")
    assert "metadata-policy-transition-v1" == transition.json()["schema_version"]
    dismissed = web.client.post(
        f"/api/v1/metadata-policy/transitions/{transition.json()['transition_id']}/acknowledge",
        headers=headers,
    )
    assert dismissed.json()["acknowledged"]
    assert before[0] == web.repository.decisions
    assert 1 == len(web.repository.policy_documents)
    assert [] == web.repository.policy_count_calls


def test_shadow_posture_and_sample_are_authenticated_non_authoritative(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    anonymous = web.client.get("/api/v1/metadata-policy/shadow")
    assert 401 == anonymous.status_code
    login = web.login()
    headers = {**web.origin, "X-CSRF-Token": login["csrf_token"]}
    state = web.client.get("/api/v1/metadata-policy/shadow")
    assert {
        "authority": "none",
        "can_leave_preview": False,
        "posture": "review",
        "schema_version": "shadow-posture-v1",
    } == state.json()
    active = web.client.get("/api/v1/metadata-policy/state").json()
    automatic_payload = active["profile"]
    del automatic_payload["schema_version"]
    automatic_payload["name"] = "Preview automatic intent"
    automatic_payload["destinations"]["title_layout"] = "automatic"
    validated_before_preview = web.client.post(
        "/api/v1/metadata-policy/validate", headers=headers, json=automatic_payload
    )
    assert 200 == validated_before_preview.status_code
    denied = web.client.post("/api/v1/metadata-policy/shadow/enter", headers=web.origin)
    assert 403 == denied.status_code
    entered = web.client.post("/api/v1/metadata-policy/shadow/enter", headers=headers)
    assert "preview" == entered.json()["posture"]
    assert "none" == entered.json()["authority"]
    validated = web.client.post(
        "/api/v1/metadata-policy/validate",
        headers=headers,
        json=automatic_payload,
    )
    assert 200 == validated.status_code, validated.text
    created = web.client.post(
        "/api/v1/metadata-policy/drafts",
        headers=headers,
        json=automatic_payload,
    )
    assert 201 == created.status_code, created.text
    policy_id = created.json()["policy_id"]
    previewed = web.client.post(
        "/api/v1/metadata-policy/preview",
        headers=headers,
        json=automatic_payload,
    )
    assert 200 == previewed.status_code, previewed.text
    activated = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=headers,
        json={
            "policy_id": policy_id,
            "expected_active_policy_id": active["policy_id"],
        },
    )
    assert 200 == activated.status_code, activated.text
    assert (
        "automatic"
        == web.client.get("/api/v1/metadata-policy/state").json()["profile"][
            "destinations"
        ]["title_layout"]
    )
    sample = web.client.get("/api/v1/metadata-policy/shadow/outcomes?limit=10")
    assert "shadow-outcome-sample-v1" == sample.json()["schema_version"]
    assert [] == sample.json()["items"]
    leaving = web.client.get("/api/v1/metadata-policy/shadow/leave-preview")
    assert leaving.json()["confirmation_required"]
    assert not leaving.json()["can_confirm"]
    assert "preview" == web.repository.shadow_mode
    assert "qualified_decision_only_unavailable" == leaving.json()["requested_posture"]


def test_policy_draft_persistence_failure_is_unavailable_not_validation(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    class _FailingDraftRepository(_CloseRecordingRepository):
        """The fake repository with only its draft write failing at the store."""

        def create_metadata_policy_draft(self, document: str) -> str:
            raise RuntimeError("policy draft store is unavailable")

    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
        session_lifetime_seconds=600,
    )
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            _FailingDraftRepository(web.case, web.repository.report),
            configuration,
            clock=lambda: web.now,
        ),
        base_url="https://review.test",
    )
    login = web.login()
    headers = {**web.origin, "X-CSRF-Token": login["csrf_token"]}
    payload = web.client.get("/api/v1/metadata-policy/state").json()["profile"]
    del payload["schema_version"]
    response = web.client.post(
        "/api/v1/metadata-policy/drafts", headers=headers, json=payload
    )
    assert 503 == response.status_code
    assert "persistence" == response.json()["error"]["code"]


def test_policy_activation_body_is_bounded_before_validation(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    login = web.login()
    headers = {
        **web.origin,
        "Content-Type": "application/json",
        "X-CSRF-Token": login["csrf_token"],
    }
    oversized = b'{"policy_id":"' + b"x" * 16_384 + b'"}'
    before = web.repository.active_policy_id
    declared = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=headers,
        content=oversized,
    )
    streamed = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=headers,
        content=(chunk for chunk in (oversized[:8_000], oversized[8_000:])),
    )
    assert (413, 413) == (declared.status_code, streamed.status_code)
    assert b"x" * 128 not in declared.content
    assert b"x" * 128 not in streamed.content
    assert before == web.repository.active_policy_id


def test_automatic_detail_uses_a_closed_projection_without_human_commit(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    # Durable automatic documents carry authority bindings that must never
    # cross the authenticated case read model.
    web.login()
    candidate_id = web.case.proposals[0]["result"]["alternatives"][0]["alternative_id"]
    authority = AutomaticDecision.create(
        review_case_id=web.case.review_case_id,
        report_id=web.repository.report.report_id,
        proposal_set_id=web.case.proposal_set_id,
        proposal_id=web.case.proposals[0]["proposal_id"],
        target_fingerprint=web.case.target_fingerprint,
        destination="title",
        outcome="candidate",
        candidate_id=candidate_id,
        resolved_policy_id="resolved_policy_" + "0" * 64,
        policy_generation=1,
        rule_id="shadow_rule_" + "1" * 64,
        policy_category="title_layout",
        policy_option="preserve",
        qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
        source_outcome_id="shadow_outcome_" + "3" * 64,
        bundle_id="automatic_bundle_" + "4" * 64,
        occurred_at=7,
    )
    web.repository.decisions["title"] = authority.to_json()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert 200 == detail.status_code
    field = next(
        value for value in detail.json()["fields"] if value["destination"] == "title"
    )
    assert field["human_decision"] is None
    assert not field["needs_attention"]
    assert {
        "candidate_id",
        "decision_id",
        "destination",
        "occurred_at",
        "outcome",
        "proposal_id",
        "review_case_id",
        "target_fingerprint",
        "selected_value",
    } == set(field["automatic_decision"])
    assert "actor" not in field["automatic_decision"]
    assert "candidate" == field["automatic_decision"]["outcome"]
    assert (
        web.case.proposals[0]["result"]["alternatives"][0]["alternative_id"]
        == field["automatic_decision"]["candidate_id"]
    )
    assert (
        web.case.proposals[0]["result"]["alternatives"][0]["value"]
        == field["automatic_decision"]["selected_value"]
    )
    recommended = [
        context
        for context in field["choice_contexts"]
        if context["recommendation"] == "recommended"
    ]
    assert 1 == len(recommended)
    assert (
        web.case.proposals[0]["result"]["alternatives"][0]["alternative_id"]
        == recommended[0]["alternative_id"]
    )
    assert "qualification_manifest_sha256" not in field["automatic_decision"]
    ignored = AutomaticDecision.create(
        **{
            **authority._core(),
            "outcome": "ignore",
            "candidate_id": None,
        },
        occurred_at=8,
    )
    web.repository.decisions["title"] = ignored.to_json()
    ignored_detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert 200 == ignored_detail.status_code
    ignored_field = next(
        value
        for value in ignored_detail.json()["fields"]
        if value["destination"] == "title"
    )
    assert all(
        context["recommendation"] == "no_recommendation"
        for context in ignored_field["choice_contexts"]
    )
    malformed = json.loads(web.repository.decisions["title"])
    malformed["outcome"] = "candidate"
    malformed["candidate_id"] = "alternative_" + "f" * 64
    web.repository.decisions["title"] = json.dumps(
        malformed, separators=(",", ":"), sort_keys=True
    )
    assert (
        404
        == web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").status_code
    )


def test_choice_contexts_build_the_evidence_index_once(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    proposal = web.case.proposals[0]
    result = cast(dict[str, Any], proposal["result"])
    alternatives = json.loads(json.dumps(result["alternatives"]))
    references = alternatives[0]["evidence_references"]
    alternatives[0]["evidence_references"] = references * 4
    repeated_result = {**result, "alternatives": alternatives}
    with patch(
        "mechanical_librarian.application.review_web._evidence_by_reference",
        wraps=_evidence_by_reference,
    ) as indexed:
        contexts = _choice_contexts(
            alternatives,
            repeated_result,
            web.repository.report,
            {"state": "value", "value": "Title"},
        )

    assert 1 == indexed.call_count
    assert len(alternatives) + 1 == len(contexts)
    assert len(json.dumps(contexts)) < 16_384
    selected_result = {
        **result,
        "selected_alternative_id": result["alternatives"][0]["alternative_id"],
    }
    ignored_contexts = _choice_contexts(
        result["alternatives"],
        selected_result,
        web.repository.report,
        {"state": "value", "value": "Title"},
        {"outcome": "ignore", "candidate_id": None},
    )
    assert all(
        context["recommendation"] == "no_recommendation" for context in ignored_contexts
    )


def test_choice_contexts_classifies_local_identifier_evidence_as_local_presence(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    proposal = web.case.proposals[0]
    result = cast(dict[str, Any], proposal["result"])
    alternatives = json.loads(json.dumps(result["alternatives"]))
    alternatives[0]["evidence_references"] = ["local_isbn_9780000000000"]
    contexts = _choice_contexts(
        alternatives,
        {**result, "alternatives": alternatives},
        web.repository.report,
        {"state": "value", "value": "Title"},
        evidence={
            "local_isbn_9780000000000": {
                "candidate": None,
                "fact": json.dumps({"kind": "isbn", "value": "9780000000000"}),
                "kind": "evidence",
                "provider": None,
                "reference": "local_isbn_9780000000000",
                "source": "identifier",
            }
        },
    )

    evidence = contexts[1]["evidence"]
    assert [
        {
            "observation": "ISBN: 9780000000000",
            "role": "local_presence",
            "source": "Local library record",
        }
    ] == evidence


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        (
            "/api/v1/metadata-policy/automatic-safety/pause",
            {"paused": True, "extra": True},
        ),
        (
            "/api/v1/metadata-policy/automatic-safety/recover",
            {"category": None, "extra": True},
        ),
        (
            "/api/v1/metadata-policy/automatic-safety/suspend",
            {"reason": "test", "extra": True},
        ),
    ),
    ids=("pause", "recover", "suspend"),
)
def test_automatic_safety_bodies_are_closed_before_control_access(
    web: ReviewWebContext[_CloseRecordingRepository],
    path: str,
    payload: dict[str, object],
) -> None:
    login = web.login()
    headers = {**web.origin, "X-CSRF-Token": login["csrf_token"]}
    response = web.client.post(path, headers=headers, json=payload)
    assert 422 == response.status_code


def test_complete_evidence_projection_is_page_bounded_and_case_bound() -> None:
    local = [
        {
            "kind": "title",
            "observed": f"hostile </pre><script>{index}</script>",
            "reference": f"local_{index:064x}",
        }
        for index in range(55)
    ]
    provider_evidence = {
        "kind": "edition_statement",
        "scope": "edition",
        "source_ordinal": 0,
        "value": "Second edition",
    }
    rich_reference = (
        "evidence_"
        + hashlib.sha256(
            json.dumps(
                {
                    "candidate": "candidate-1",
                    "evidence": provider_evidence,
                    "provider": "hardcover-main",
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    provider_claim_reference = "provider_" + "a" * 64
    sibling_claim_reference = "provider_" + "d" * 64
    identifier_reference = "provider_identifier_" + "e" * 64
    cover_reference = "provider_cover_" + "b" * 64
    data = {
        "local": {"claims": local},
        "providers": [
            {
                "provider": "hardcover-main",
                "authority": "hardcover",
                "candidates": [
                    {
                        "identity": "candidate-1",
                        "claims": [
                            {
                                "kind": "publisher",
                                "reference": provider_claim_reference,
                                "value": "Example Press",
                            }
                        ],
                        "evidence": [provider_evidence],
                    }
                ],
            },
            {
                "provider": "open-library-main",
                "authority": "open_library",
                "candidates": [
                    {
                        "identity": "candidate-2",
                        "claims": [
                            {
                                "kind": "title",
                                "reference": sibling_claim_reference,
                                "value": "Sibling candidate",
                            }
                        ],
                        "evidence": [],
                    }
                ],
            },
        ],
        "identifier_evidence": [
            {
                "candidate_identity": "candidate-2",
                "kind": "identifier_relation",
                "provider": "open-library-main",
                "reference": identifier_reference,
            }
        ],
        "covers": [
            {
                "kind": "provider",
                "provenance": {"provider": "hardcover-main"},
                "source_id": cover_reference,
                "state": "retrieved",
            }
        ],
    }
    references = {
        *(item["reference"] for item in local),
        provider_claim_reference,
        sibling_claim_reference,
        identifier_reference,
        rich_reference,
        cover_reference,
    }
    detail = {
        "fields": [
            {"destination": "title", "provenance": sorted(references)},
            {"destination": "authors", "provenance": [provider_claim_reference]},
        ],
    }
    report = SimpleNamespace(providers=(), to_data=lambda: data)
    cases = InspectCurrentReviewCase(Mock())
    with patch.object(cases, "_read", return_value=(detail, report, ())):
        first = cases.evidence("review_case_" + "c" * 64, limit=50)
        second = cases.evidence(
            "review_case_" + "c" * 64,
            limit=50,
            cursor=first["next_cursor"],
        )
        with pytest.raises(ReviewWebError):
            cases.evidence(
                "review_case_" + "d" * 64,
                cursor=first["next_cursor"],
            )
        changed_detail: dict[str, Any] = {
            "fields": [{"destination": "title", "provenance": []}]
        }
        with patch.object(
            cases,
            "_read",
            return_value=(changed_detail, report, ()),
        ):
            with pytest.raises(ReviewWebError):
                cases.evidence(
                    "review_case_" + "c" * 64,
                    cursor=first["next_cursor"],
                )
        missing_detail: dict[str, Any] = {
            "fields": [
                {
                    "destination": "title",
                    "provenance": sorted(references | {"missing-lineage"}),
                }
            ]
        }
        with patch.object(cases, "_read", return_value=(missing_detail, report, ())):
            partial = cases.evidence("review_case_" + "c" * 64, limit=50)
    reopened_cases = InspectCurrentReviewCase(Mock())
    with patch.object(
        reopened_cases,
        "_read",
        return_value=(detail, report, ()),
    ):
        reopened = reopened_cases.evidence(
            "review_case_" + "c" * 64,
            limit=50,
        )

    assert first["next_cursor"] == reopened["next_cursor"]
    assert first["schema_version"] == "review-case-evidence-v4"
    assert first["missing_lineage_count"] == 0
    assert first["source_ledger"] == []
    assert partial["missing_lineage_count"] == 1
    assert len(partial["items"]) == 50
    assert len(first["items"]) == 50
    assert len(second["items"]) == 10
    assert second["next_cursor"] is None
    items = cast(list[Any], first["items"]) + cast(list[Any], second["items"])
    assert all(
        set(item) == {"destinations", "observation", "role", "source"} for item in items
    )
    shared = next(item for item in items if "Example Press" in item["observation"])
    assert shared["destinations"] == ["authors", "title"]
    assert {item["source"] for item in items} >= {
        "Hardcover",
        "Local library record",
        "Open Library",
    }
    assert all(len(item["observation"]) <= 188 for item in items)
    hostile = next(
        item for item in items if "hostile </pre><script>" in item["observation"]
    )
    assert hostile["role"] == "local_presence"
    assert not any(
        reference in json.dumps(items, ensure_ascii=False) for reference in references
    )


def test_automatic_history_values_require_an_exact_decision_identity() -> None:
    exact = "automatic_decision_" + "a" * 64
    other = "automatic_decision_" + "b" * 64
    detail = {
        "fields": [
            {
                "automatic_decision": {
                    "decision_id": exact,
                    "outcome": "candidate",
                    "selected_value": {"kind": "text", "value": "New title"},
                },
                "current": {
                    "destination": "title",
                    "state": "value",
                    "value": "Old title",
                },
                "destination": "title",
            }
        ]
    }
    snapshot = CurrentReviewCaseSnapshot(
        case_document="{}",
        report_document="{}",
        decision_documents=(),
        history=(
            {
                "decision_id": exact,
                "destination": "title",
                "kind": "automatic_decision_recorded",
                "occurred_at": 2,
                "origin": "automatic_policy",
                "outcome": "candidate",
                "review_case_id": "review_case_" + "c" * 64,
                "schema_version": "automatic-decision-history-event-v1",
            },
            {
                "decision_id": other,
                "destination": "title",
                "kind": "automatic_decision_recorded",
                "occurred_at": 1,
                "origin": "automatic_policy",
                "outcome": "candidate",
                "review_case_id": "review_case_" + "c" * 64,
                "schema_version": "automatic-decision-history-event-v1",
            },
        ),
    )
    cases = InspectCurrentReviewCase(Mock())
    with patch.object(cases, "_read", return_value=(detail, Mock(), snapshot)):
        history = cases.history("review_case_" + "c" * 64)

    assert history["schema_version"] == "review-case-history-v2"
    assert history["items"][0]["previous_value"] == {
        "kind": "text",
        "value": "Old title",
    }
    assert history["items"][0]["selected_value"] == {
        "kind": "text",
        "value": "New title",
    }
    assert history["items"][1]["previous_value"] is None
    assert history["items"][1]["selected_value"] is None


def test_choice_projection_is_bounded_and_never_leaks_unknown_provider_names() -> None:
    observation = _choice_observation(
        {
            "fact": json.dumps(
                {
                    "kind": "title\x00private",
                    "value": "\x00" + "hostile-value" * 40,
                }
            ),
            "provider": "private-connection-name",
            "source": "provider_claim",
        }
    )
    assert len(observation) <= 188
    assert "\x00" not in observation
    assert "private" not in observation
    assert observation.startswith("Metadata: ")
    assert (
        _choice_source_label(
            {
                "fact": "{}",
                "provider": "private-connection-name",
                "source": "provider_claim",
            }
        )
        == "Retained observation (provider name unavailable)"
    )
    replacing = {
        "assessment": "replaces_target",
        "scope": "edition",
        "correlations": ["correlation_" + "a" * 64],
    }
    assert (
        _choice_rationale(replacing, True)
        == "This alternative replaces the local value using edition-specific evidence with retained supporting evidence."
    )
    assert _current_value_present("Present title")
    assert _current_value_present(["Ada Example"])
    assert _current_value_present("2024-01-01")
    assert not _current_value_present(None)
    assert not _current_value_present("")
    assert not _current_value_present([])


def test_provider_comparison_notices_never_create_a_permanent_case_banner(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    source = _report()
    report = MetadataReport.create(
        target=source.target,
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(
            ProviderObservation(
                "hardcover-main", ProviderStatus.DISABLED, authority="hardcover"
            ),
            ProviderObservation(
                "open-library-main", ProviderStatus.NO_MATCH, authority="open_library"
            ),
            ProviderObservation("private-connection", ProviderStatus.UNAVAILABLE),
        ),
    )
    # Provider outcomes remain in the source/choice disclosure. They do not
    # turn a terminal outcome into a permanent case-level warning.
    assert _provider_comparison_notices(report) == []
    affected_case = ReviewCase.create(evaluate_report(report), report)
    projected = InspectCurrentReviewCase(
        FakeReviewRepository(affected_case, report)
    ).run(affected_case.review_case_id)
    assert _provider_comparison_notices(report) == projected["comparison_notices"]
    unaffected = InspectCurrentReviewCase(web.repository).run(web.case.review_case_id)
    assert [] == unaffected["comparison_notices"]
    assert "Google Books" == _provider_label("google_books")
    assert "Provider (name unavailable)" == _provider_label(None)
    for spoofed_name in (
        "google-books-private-instance",
        "open-library-owner-home",
    ):
        spoofed = MetadataReport.create(
            target=source.target,
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=(
                ProviderObservation(
                    spoofed_name,
                    ProviderStatus.UNAVAILABLE,
                ),
            ),
        )
        assert _provider_comparison_notices(spoofed) == []


def test_choice_limitations_leave_multi_alternative_global_reasons_field_scoped() -> (
    None
):
    first = {"alternative_id": "alternative-a"}
    result: dict[str, Any] = {
        "alternatives": [first, {"alternative_id": "alternative-b"}],
        "reasons": ["competing_evidence"],
    }
    assert [] == _choice_limitations(first, result)
    assert "competing_evidence" in result["reasons"]


def test_choice_limitations_attach_only_single_exact_choice_reason() -> None:
    alternative = {"alternative_id": "alternative-a"}
    result = {
        "alternatives": [alternative],
        "reasons": ["insufficient_independent_corroboration"],
    }
    assert ["insufficient_independent_corroboration"] == _choice_limitations(
        alternative, result
    )
    assert [] == _choice_limitations({"alternative_id": "alternative-b"}, result)


def test_choice_limitations_do_not_invent_provider_terminal_attribution() -> None:
    alternative = {"alternative_id": "alternative-a"}
    result = {
        "alternatives": [alternative],
        "reasons": ["provider_no_match", "provider_unavailable"],
    }
    assert [] == _choice_limitations(alternative, result)


def test_attention_first_library_lenses_queue_and_cursor_binding(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    providers = tuple(
        _proposal_provider(
            name,
            f"{name}-edition",
            (
                _proposal_claim(
                    name,
                    f"{name}-edition",
                    ClaimKind.TITLE,
                    "Corrected title",
                    scope=ClaimScope.WORK,
                ),
            ),
            authority=authority,
        )
        for name, authority in (
            ("hardcover_primary", "hardcover"),
            ("open_library_primary", "open_library"),
        )
    )
    automatic_report = _proposal_report(providers=providers)
    automatic_case = ReviewCase.create(
        evaluate_report(automatic_report), automatic_report
    )
    web.repository.browse_cases.append((automatic_case, automatic_report))
    web.login()

    attention_first = web.client.get("/api/v1/library?limit=1")
    assert attention_first.status_code == 200, attention_first.text
    body = attention_first.json()
    assert body["counts"] == {
        "all": 2,
        "needs_attention": 1,
        "no_attention_needed": 1,
    }
    assert len(body["items"]) == 1
    assert body["items"][0]["attention_state"] == "needs_attention"
    assert body["next_cursor"] is None

    all_first = web.client.get("/api/v1/library?lens=all&limit=1")
    cursor = all_first.json()["next_cursor"]
    assert cursor is not None
    all_second = web.client.get(f"/api/v1/library?lens=all&limit=1&cursor={cursor}")
    assert all_second.status_code == 200
    assert all_second.json()["items"][0]["attention_state"] == "no_attention_needed"
    rebound = web.client.get(
        f"/api/v1/library?lens=all&q=other&limit=1&cursor={cursor}"
    )
    assert rebound.status_code == 400

    no_attention = web.client.get("/api/v1/library?lens=no_attention_needed")
    assert len(no_attention.json()["items"]) == 1
    assert (
        no_attention.json()["items"][0]["review_case_id"]
        == automatic_case.review_case_id
    )
    queue = web.client.get("/api/v1/attention")
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert len(queue.json()["items"]) == 1
    assert queue.json()["items"][0]["destination"] == "title"
    assert queue.json()["items"][0]["work_attention_count"] == 1
    assert "?field=title" in queue.json()["items"][0]["detail_url"]
    assert automatic_case.review_case_id not in json.dumps(queue.json())

    assert web.client.get("/library").status_code == 200
    assert web.client.get("/attention").status_code == 200


def test_complete_library_includes_current_report_without_review_lineage(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    current = _report(title="In-progress report", actionable=False)
    web.repository.browse_without_cases.append(current)
    web.login()

    response = web.client.get("/api/v1/library?lens=all&q=In-progress")

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {
        "all": 1,
        "needs_attention": 0,
        "no_attention_needed": 1,
    }
    item = response.json()["items"][0]
    assert item["review_case_id"] is None
    assert item["detail_url"] is None
    assert item["work_id"].startswith("library_work_")
    assert item["attention_state"] == "no_attention_needed"
    assert item["dispositions"] == []


def test_library_reads_the_three_trailing_columns_its_row_declares(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    """`suspended`, `shadow_abstention` and `terminal` reach the reader.

    `ReviewLibrarySnapshot.rows` declares eight columns and the double
    emitted five, so `library()` padded the last three with `()` and `None`
    behind four `len(row)` guards. Every assertion below therefore held on
    padding the reader invented rather than on anything a repository said
    (#711). The two works carry different values for each column, so an
    answer that collapsed back to a constant fails here.
    """
    base = web.repository.report
    successor = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-2", base.target.fields),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(),
    )
    successor_case = ReviewCase.create(evaluate_report(successor), successor)
    web.repository.browse_cases.append((successor_case, successor))
    web.repository.library_suspensions[web.case.review_case_id] = ("title",)
    web.repository.library_shadow_abstentions[successor_case.review_case_id] = (
        "subtitle",
    )
    web.repository.library_automatic_attention[successor_case.review_case_id] = (
        "series",
    )
    web.repository.library_attention_counts[web.case.review_case_id] = 0
    web.repository.library_attention_counts[successor_case.review_case_id] = 3
    web.repository.library_terminal_documents["item-2"] = json.dumps(
        {
            "attempt": 2,
            "connection": "primary",
            "failure_root": None,
            "item": "item-2",
            "lane": "projection",
            "library": "ebooks",
            "recovery_attempt": 1,
            "schema_version": "review-terminal-work-v1",
            "status": "projection",
            "work_id": "review_work_" + "c" * 64,
        }
    )
    web.login()

    response = web.client.get("/api/v1/library?lens=all&limit=10")

    assert response.status_code == 200, response.text
    items = {item["item"]: item for item in response.json()["items"]}
    assert {"item-1", "item-2"} == set(items)
    # `title` is the one field this fixture leaves unresolved. Suspending it
    # on the first work retires its attention; on the second work an abstained
    # `subtitle` and a safety-suspended `series` each add one that no proposal
    # asks for. The eighth column is the persisted authoritative count.
    assert items["item-1"]["attention_count"] == 0
    assert items["item-1"]["attention_state"] == "no_attention_needed"
    assert items["item-2"]["attention_count"] == 3
    assert items["item-2"]["attention_state"] == "needs_attention"
    assert items["item-2"]["preparation"] is not None
    assert items["item-2"]["preparation"]["stage"] == "projection"
    assert items["item-2"]["preparation"]["status"] == "projection"
    assert items["item-1"]["preparation"] is None


def test_row_projection_refuses_every_document_the_parsers_cannot_read() -> None:
    """Every unreadable stored document is `unavailable`, surrogates included.

    `MetadataReport.from_json` used to run its length guard outside the
    block converting `UnicodeError`, so a row holding an unpaired surrogate
    escaped it as `UnicodeEncodeError` and neither handler here could
    narrow to the parser's declared type. The guard now sits under that
    boundary, so both handlers match `ReportValueError` and refuse the row
    rather than admitting every `ValueError` this projection can raise
    (#698).
    """
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    surrogate = '{"a": "\ud800"}'

    # The fix: the surrogate now reaches both handlers as the parser's one
    # declared refusal rather than as a raw codec fault.
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(surrogate)

    for document in ("{not a report}", surrogate):
        with pytest.raises(ReviewWebError) as raised:
            _library_item(None, document, ())
        assert raised.value.code == "unavailable"
        with pytest.raises(ReviewWebError) as raised:
            _case_and_report(case.to_json(), document)
        assert raised.value.code == "unavailable"

    with pytest.raises(ReviewWebError) as raised:
        _case_and_report(case.to_json(), report.to_json()[:-1])
    assert raised.value.code == "unavailable"

    # Both handlers let a fault in the projection itself past. `_library_item`
    # catches only the domain parser's declared refusal, not arbitrary faults.
    fault = RuntimeError("projection is broken")
    with patch.object(MetadataReport, "from_json", side_effect=fault):
        with pytest.raises(RuntimeError) as surfaced:
            _case_and_report(case.to_json(), report.to_json())
        assert surfaced.value is fault
        with pytest.raises(RuntimeError) as surfaced:
            _library_item(case.to_json(), report.to_json(), ())
        assert surfaced.value is fault


def test_attention_cursor_keeps_the_complete_work_field_count(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    report = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=(),
                series="Mistborn",
            ),
        ),
    )
    case = ReviewCase.create(evaluate_report(report), report)
    web.repository.browse_cases = [(case, report)]
    web.login()

    first = web.client.get("/api/v1/attention?limit=1")
    assert first.status_code == 200, first.text
    assert first.json()["items"][0]["work_attention_count"] == 3
    cursor = first.json()["next_cursor"]
    assert cursor is not None
    second = web.client.get(f"/api/v1/attention?limit=1&cursor={cursor}")
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["work_attention_count"] == 3


def test_attention_reads_the_automatic_outcome_and_cause_columns(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    """The queue classifies from all six declared attention columns.

    The reader consumed the last two positionally behind `len(row)`
    guards, so a store that stopped at four columns produced a plain
    "needs your review" queue with no failure anywhere (#703).
    """
    report = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=(),
                series="Mistborn",
            ),
        ),
    )
    case = ReviewCase.create(evaluate_report(report), report)
    web.repository.browse_cases = [(case, report)]
    web.repository.attention_causes["title"] = "automatic_safety_suspended"
    web.repository.attention_dispositions["series"] = "abstention"
    web.login()

    response = web.client.get("/api/v1/attention?limit=10")

    assert response.status_code == 200, response.text
    reasons = {
        item["destination"]: item["classification_reason"]
        for item in response.json()["items"]
    }
    assert (
        reasons["title"] == "Automatic handling is suspended pending a safety recovery."
    )
    assert (
        reasons["series"]
        == "Automatic handling abstained, so this field needs your review."
    )
    # The third row carries neither column, so the two above prove the
    # reader read them rather than a constant the double always emits.
    assert reasons["subtitle"] == "This field needs your review."


def test_epub_inventory_and_attention_omit_narrator_and_explicit() -> None:
    report = _proposal_report()
    case = ReviewCase.create(evaluate_report(report), report)
    assert {
        destination: _field_inventory_role(
            "needs_attention", destination, report.file.format, [], False
        )
        for destination in ("narrators", "explicit")
    } == {"narrators": "omitted", "explicit": "omitted"}
    assert all(
        _attention_item(case.to_json(), report.to_json(), destination, 1) is None
        for destination in ("narrators", "explicit")
    )


def test_cursor_encoder_never_emits_an_unreachable_offset() -> None:
    boundary = _encode_cursor(100_000, "library", "all", None)
    assert _decode_cursor(boundary, "library", "all", None) == 100_000
    with pytest.raises(ReviewWebError):
        _encode_cursor(100_001, "library", "all", None)
    with pytest.raises(ReviewWebError):
        _encode_cursor(True, "library", "all", None)


def test_large_library_payload_and_dom_inputs_remain_page_bounded(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    web.repository.browse_cases = [
        (web.case, web.repository.report) for _ in range(2_001)
    ]
    web.login()
    library = web.client.get("/api/v1/library?lens=all&limit=50")
    assert library.status_code == 200, library.text
    assert len(library.json()["items"]) == 50
    assert library.json()["next_cursor"] is not None
    assert len(library.content) < 100_000
    attention = web.client.get("/api/v1/attention?limit=50")
    assert len(attention.json()["items"]) == 50
    assert attention.json()["next_cursor"] is not None
    assert len(attention.content) < 100_000


def _decide(web: ReviewWebContext[_CloseRecordingRepository]) -> str:
    """Record one owner decision through the exact-case command."""
    login = web.login()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    field = detail["fields"][0]
    recorded = web.client.post(
        f"/api/v1/review-cases/{web.case.review_case_id}/decisions",
        headers={
            **web.origin,
            "X-CSRF-Token": str(login["csrf_token"]),
            "Idempotency-Key": "revisable-decision-0001",
        },
        json={
            "destination": field["destination"],
            "outcome": "accept",
            "proposal_id": field["proposal_id"],
            "selected_value": field["proposed_values"][0],
            "target_fingerprint": field["target_fingerprint"],
        },
    )
    assert recorded.status_code == 200, recorded.text
    return str(field["destination"])


def test_a_recorded_decision_stays_revisable_until_its_effect_leaves_reversible_states(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    destination = _decide(web)
    revisable = next(
        field
        for field in web.client.get(
            f"/api/v1/review-cases/{web.case.review_case_id}"
        ).json()["fields"]
        if field["destination"] == destination
    )
    assert revisable["classification"] == "human_recorded"
    assert revisable["needs_attention"] is False
    # Editability is what lets the surface show every eligible option again and
    # mark the recorded one, and the store would accept the change.
    assert revisable["editable"] is True
    assert "You can change it here." in revisable["classification_reason"]

    web.repository.override_blocked_destinations = (destination,)
    advanced = next(
        field
        for field in web.client.get(
            f"/api/v1/review-cases/{web.case.review_case_id}"
        ).json()["fields"]
        if field["destination"] == destination
    )
    assert advanced["classification"] == "human_recorded"
    assert advanced["editable"] is False
    # In-flight authority is temporary: a settled reversible scalar effect is
    # voluntarily revisitable again.
    assert "cannot be changed yet" in advanced["classification_reason"]


def test_one_decision_and_its_exact_case_read_never_project_the_whole_library(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    projections: list[str] = []
    for name in ("read_review_library", "read_review_attention"):
        original = getattr(web.repository, name)

        def record(*arguments: object, _name: str = name, _original: Any = original):
            projections.append(_name)
            return _original(*arguments)

        setattr(web.repository, name, record)

    _decide(web)
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}").json()
    # Next-field authority is available from the exact case alone.
    assert projections == []
    assert any(field["needs_attention"] for field in detail["fields"]) or all(
        field["classification"] != "needs_attention" for field in detail["fields"]
    )

    # The counter is real: the Library lens does reach the projection it is for.
    assert web.client.get("/api/v1/library?lens=all&limit=5").status_code == 200
    assert web.client.get("/api/v1/attention?limit=5").status_code == 200
    assert projections == ["read_review_library", "read_review_attention"]


@pytest.mark.parametrize("consented", (False, True))
def test_case_detail_reports_write_consent_it_never_asked_the_read_model_for(
    web: ReviewWebContext[_CloseRecordingRepository], consented: bool
) -> None:
    """The surface's reassurance copy needs the truth, and reads cannot know it.

    A read model has no write authority and must not acquire one. The
    composition root stamps what the applier's own consent says, so a recorded
    decision can be described as "nothing is written yet" or as "applying
    began when you decided it" without either being a guess.
    """
    application = FakeActivityApplication()
    application.writes_consented = consented
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            ReviewWebConfiguration(
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
                session_lifetime_seconds=600,
            ),
            clock=lambda: web.now,
            field_application=application,
        ),
        base_url="https://review.test",
    )
    web.login()
    detail = web.client.get(f"/api/v1/review-cases/{web.case.review_case_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["writes_consented"] is consented


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    (
        ("contributor", "Recorded Narrator", "Narration credit: Recorded Narrator"),
        ("duration", "43200.000", "Running time: 43200.000"),
        ("performance_type", "unabridged", "Performance: unabridged"),
    ),
)
def test_audio_observations_are_labelled_for_the_owner(
    kind: str, value: str, expected: str
) -> None:
    assert (
        _choice_observation(
            {
                "fact": json.dumps({"kind": kind, "value": value}),
                "provider": None,
                "source": "local_claim",
            }
        )
        == expected
    )
