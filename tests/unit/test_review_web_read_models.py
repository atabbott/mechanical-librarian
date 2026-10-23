"""Review web read models: preparation and activity controls, policy
and shadow surfaces, cover omission, evidence and choice
projections, and the Library and Attention lenses.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast, get_args
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mechanical_librarian.adapters.evidence_extractors.audio_local import (
    AudioPathContext,
    AudioSidecar,
    interpret,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
)
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
    ReviewAttentionSnapshot,
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
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.activity_records import readable_activity_event
from mechanical_librarian.domain.audio_health import (
    AUDIO_HEALTH_BOUNDS,
    MAX_HEALTH_OBSERVATIONS,
    AudioHealthKind,
)
from mechanical_librarian.domain.audio_inspection import (
    AudioChapter,
    AudioEvidenceSurface,
    AudioInspectionLimits,
    AudioTrackDigest,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    F46_ASIN_IDENTITY,
)
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    AutomaticDecision,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_browse import ReviewBrowseValueError
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.review_field_classification import ReviewFieldState
from mechanical_librarian.interfaces.web.app import (
    _CaseDetailResponse,
    _CoverOmission,
    _MediaHealthObservation,
    _ReportDetailResponse,
    _ReviewField,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asserted_asin_report,
)
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
                "manual_eligible": False,
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
        "schema_version": "cover-consideration-v4",
        "supersedes_id": None,
        "target_fingerprint": case.target_fingerprint,
    }
    covers = Mock()
    covers.project.return_value = view
    covers.project_for_review.return_value = (view, "current")
    covers.ensure.return_value = view
    covers.image.return_value = b"\x89PNG\r\n\x1a\nsynthetic"
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
    signed_out = web.client.get(image_path)
    assert 401 == signed_out.status_code
    assert "no-store, private" == signed_out.headers["cache-control"]
    covers.image.assert_not_called()
    login = web.login()
    image = web.client.get(image_path)
    assert 200 == image.status_code
    assert "image/png" == image.headers["content-type"]
    # The URL fixes these bytes, so the browser may keep them privately.
    assert "private, max-age=31536000, immutable" == image.headers["cache-control"]
    assert "nosniff" == image.headers["x-content-type-options"]
    covers.image.side_effect = CoverConsiderationError("stale")
    stale = web.client.get(image_path)
    assert 409 == stale.status_code
    assert "no-store, private" == stale.headers["cache-control"]
    covers.image.side_effect = None
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
    with patch.object(
        repository, "record_human_decision", wraps=repository.record_human_decision
    ) as record:
        decision = web.client.post(
            f"/api/v1/review-cases/{case.review_case_id}/decisions",
            headers={
                **web.origin,
                "X-CSRF-Token": str(login["csrf_token"]),
                "Idempotency-Key": "protected-cover-outcome-0001",
            },
            json={
                "consideration_id": consideration_id,
                "cover_confirmation": {
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                },
                "destination": "cover",
                "outcome": "keep_current",
                "proposal_id": cover["proposal_id"],
                "selected_value": None,
                "target_fingerprint": cover["target_fingerprint"],
            },
        )
    assert 200 == decision.status_code, decision.text
    covers.validate_decision.assert_not_called()
    assert record.call_args.kwargs == {
        "cover_consideration_id": consideration_id,
        "cover_confirmation": {
            "expected_selection_id": None,
            "confirm_unverified_match": False,
        },
    }
    assert (
        json.loads(repository.decisions["cover"])["decision_id"]
        == decision.json()["decision_id"]
    )
    logout = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "X-CSRF-Token": str(login["csrf_token"])},
    )
    assert 204 == logout.status_code, logout.text
    reads = covers.image.call_count
    revoked = web.client.get(image_path)
    assert 401 == revoked.status_code
    assert "no-store, private" == revoked.headers["cache-control"]
    assert reads == covers.image.call_count


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
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
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
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
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
            field_application=application,
        ),
        base_url="https://review.test",
    )
    assert web.client.get("/api/v1/activity/permission-readiness").status_code == 401
    assert web.client.get("/api/v1/activity/consent-state").status_code == 401
    # A cookie is not a session: the read authenticates it (#1236).
    web.client.cookies.set("ml_session", "malformed")
    assert web.client.get("/api/v1/activity/consent-state").status_code == 401
    web.client.cookies.clear()
    login = web.login()
    page = web.client.get("/activity")
    assert page.status_code == 200
    assert f'data-build-identity="{TEST_REACT_BUILD_IDENTITY}"' in page.text
    script = web.client.get("/assets/review-app/assets/index-deadbeef.js")
    assert script.status_code == 200
    assert web.client.get("/api/v1/activity").json()["prepared"]["total"] == 2
    history = web.client.get("/api/v1/activity").json()
    assert history["schema_version"] == "continuous-field-activity-v3"
    assert history["backup_ready"] is None and history["example"] is None
    readiness = web.client.get("/api/v1/activity/permission-readiness")
    assert readiness.status_code == 200
    assert readiness.headers["cache-control"] == "no-store, private"
    assert readiness.json() == application.activity_permission_readiness()
    for invalid in (
        {"backup_ready": True},
        {"backup_ready": False},
        {"example": readiness.json()["example"]},
    ):
        with (
            patch.object(application, "activity", return_value={**history, **invalid}),
            pytest.raises(ValidationError),
        ):
            web.client.get("/api/v1/activity")
    assert set(readiness.json()) == {
        "backup_ready",
        "configured_libraries",
        "capabilities",
        "prepared",
        "example",
    }
    with patch.object(
        application, "activity_permission_readiness", side_effect=RuntimeError
    ):
        unavailable = web.client.get("/api/v1/activity/permission-readiness")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "unavailable"
    # The notice's read is Activity's consent inputs and nothing else (#1236).
    consent = web.client.get("/api/v1/activity/consent-state")
    assert consent.status_code == 200
    assert consent.headers["cache-control"] == "no-store, private"
    assert consent.json() == {
        key: history[key]
        for key in ("backup_ready", "capabilities", "configured_libraries", "waiting")
    }
    for invalid in ({"backup_ready": True}, {"events": []}):
        with (
            patch.object(
                application,
                "activity_consent",
                return_value={**consent.json(), **invalid},
            ),
            pytest.raises(ValidationError),
        ):
            web.client.get("/api/v1/activity/consent-state")
    with patch.object(application, "activity_consent", side_effect=RuntimeError):
        unavailable = web.client.get("/api/v1/activity/consent-state")
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "unavailable"
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


def test_activity_loads_after_a_verified_write_of_a_long_value(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    """A verified description longer than the evidence bound keeps Activity up.

    The confirmation once restated the bounded value behind a prefix, so the
    response model refused the whole ledger and the page answered 500 (#1278).
    """
    application = FakeActivityApplication()
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
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
    history = application.activity()
    written: dict[str, object] = {
        "destination": "description",
        "event_type": "metadata",
        "origin": "human",
        "state": "verified",
        "record": {
            "source_kind": "field_effect",
            "pre_value": None,
            "desired_value": "A long synthetic description. " * 70,
        },
    }
    readable_activity_event(written, None)
    events = [{**history["events"][0], **written}, *history["events"][1:]]
    with patch.object(
        application, "activity", return_value={**history, "events": events}
    ):
        response = web.client.get("/api/v1/activity")
    assert response.status_code == 200, response.text
    assert {
        "label": "Confirmed in Audiobookshelf",
        "value": "Matches the new value",
    } in response.json()["events"][0]["evidence"]


def test_review_preparation_is_authenticated_csrf_bound_and_bounded(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
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
        "recorded": 0,
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
            {"stage": "recorded", "count": 0},
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
    with patch.object(
        preparation, "check_connection", side_effect=ReviewPipelineError("unavailable")
    ):
        unavailable = web.client.post(
            "/api/v1/review-preparation/primary/check-connection",
            headers={**web.origin, "x-csrf-token": session["csrf_token"]},
        )
    assert unavailable.status_code == 503
    assert unavailable.json() == {
        "error": {"code": "unavailable"},
        "schema_version": "review-error-v1",
    }
    retried = web.client.post(
        "/api/v1/review-preparation/primary/check-connection",
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert retried.status_code == 200
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
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
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


def test_the_policy_state_names_every_audiobook_tranche_and_a_record_opens_one(
    web: ReviewWebContext[_CloseRecordingRepository],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner's routing surface can see an audiobook record.

    Routing is one library-wide owner choice, so the page that offers
    Automatic has to read the audiobook envelope as well as the ebook one.
    While no record is published every tranche states itself unavailable and
    names no digest, which is what keeps the page rendering exactly what it
    renders today. Publishing one record makes exactly that identity's own
    category and option routable and moves no ebook record and no other
    tranche, so activation needs no further change on any surface (#967).
    """
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_PASSING_AUTOMATIC_OPTIONS", frozenset()
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", None
    )
    web.login()
    inert = web.client.get("/api/v1/metadata-policy/state").json()
    entries = inert["audiobook_qualification"]
    assert {entry["identity"] for entry in entries} == set(
        audiobook_decision_qualification.F46_AUDIOBOOK_IDENTITIES
    )
    assert len(entries) == len(
        audiobook_decision_qualification.F46_AUDIOBOOK_IDENTITIES
    )
    for entry in entries:
        assert set(entry) == {
            "automatic_available",
            "destination",
            "field",
            "identity",
            "option",
            "operation",
            "record_sha256",
        }
        assert entry["automatic_available"] is False
        assert entry["record_sha256"] is None
    assert {entry["operation"] for entry in entries} == {
        "preserve",
        "fill",
        "normalize",
        "add",
        "replace",
        "replace_invalid",
        "replace_recording",
    }

    record = "7" * 64
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset({F46_ASIN_IDENTITY}),
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", record
    )
    qualified = web.client.get("/api/v1/metadata-policy/state").json()
    assert {
        (entry["identity"], entry["operation"], entry["record_sha256"])
        for entry in qualified["audiobook_qualification"]
        if entry["automatic_available"]
    } == {(F46_ASIN_IDENTITY, "fill", record)}
    # The ebook records are the same bytes either way: an audiobook record
    # never speaks for one, and the ASIN it names is exactly the option the
    # ebook envelope still leaves unqualified.
    assert inert["rich_qualification"] == qualified["rich_qualification"]
    assert {
        (entry["field"], entry["option"], entry["automatic_available"])
        for entry in qualified["rich_qualification"]
        if entry["field"] == "asin"
    } == {("asin", "preserve_exact", False), ("asin", "ebook_manifestation", False)}


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
            "expected_active_generation": preview.json()["active_generation"],
        },
    )
    assert 403 == denied_activation.status_code
    activated = web.client.post(
        "/api/v1/metadata-policy/activate",
        headers=headers,
        json={
            "policy_id": policy_id,
            "expected_active_policy_id": active.json()["policy_id"],
            "expected_active_generation": preview.json()["active_generation"],
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
            "expected_active_generation": previewed.json()["active_generation"],
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
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
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
    web.repository.automatic_selected_values = (
        (
            authority.decision_id,
            web.case.proposals[0]["result"]["alternatives"][0]["value"],
        ),
    )
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
                "recording_lookup_candidates": [],
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
                "recording_lookup_candidates": [],
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
    report = SimpleNamespace(
        audio=None, local_claims=(), providers=(), to_data=lambda: data
    )
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


@pytest.mark.parametrize(
    "mismatch",
    [
        None,
        "missing",
        "review_case_id",
        "report_id",
        "proposal_set_id",
        "target_fingerprint",
        "proposal_id",
        "candidate_id",
    ],
)
def test_automatic_history_values_require_an_exact_decision_identity(
    tmp_path, mismatch
) -> None:
    from dataclasses import asdict, replace

    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from tests.support.description_advice import _prepared

    repository, _store, binding, _candidate, automatic = _prepared(
        tmp_path, automatic=False
    )
    cases = InspectCurrentReviewCase(repository)
    detail, report, snapshot = cases._read(binding.review_case_id, 50)
    event = next(
        value
        for value in snapshot.history
        if value.get("decision_id") == automatic.decision_id
    )
    other = {**event, "decision_id": "automatic_decision_" + "b" * 64, "occurred_at": 0}
    documents = snapshot.decision_documents
    if mismatch is not None:
        documents = tuple(
            value
            for value in documents
            if json.loads(value)["decision_id"] != automatic.decision_id
        )
        if mismatch != "missing":
            values = asdict(automatic)
            for key in ("decision_id", "idempotency_digest"):
                values.pop(key)
            prefix = {
                "review_case_id": "review_case_",
                "report_id": "report_",
                "proposal_set_id": "proposal_set_",
                "proposal_id": "review_proposal_",
                "target_fingerprint": "",
                "candidate_id": "alternative_",
            }[mismatch]
            values[mismatch] = prefix + "f" * 64
            if mismatch == "candidate_id":
                values["outcome"] = "candidate"
            changed = AutomaticDecision.create(**values)
            documents += (changed.to_json(),)
            event = {**event, "decision_id": changed.decision_id}
    snapshot = replace(snapshot, decision_documents=documents, history=(event, other))
    with patch.object(cases, "_read", return_value=(detail, report, snapshot)):
        history = cases.history(binding.review_case_id)
    expected = (
        {"kind": "text", "value": "Existing description."} if mismatch is None else None
    )
    assert history["schema_version"] == "review-case-history-v3"
    assert history["items"][0]["previous_value"] == expected
    assert history["items"][0]["selected_value"] == expected
    assert history["items"][1]["previous_value"] is None
    assert history["items"][1]["selected_value"] is None
    repository.close()


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
    # No case to open, but the evidence is still addressable: the row points at
    # the report it stands for rather than at nothing, and says which of the
    # two it is rather than leaving a reader to guess from the URL's shape.
    assert (
        item["detail_url"]
        == "/api/v1/library/report?connection=primary&library=ebooks&item=item-1"
    )
    assert item["detail_kind"] == "report"
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

    # Both handlers let a fault in the projection itself past. They catch only
    # the bounded readers' declared refusal, not arbitrary faults.
    fault = RuntimeError("projection is broken")
    with patch.object(TargetFacts, "from_data", side_effect=fault):
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
    """The queue retains automatic precedence in the seven-column contract.

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


def test_retained_policy_attention_fact_reaches_detail_and_attention(web):
    from tests.support.proposal_evidence import retained_decoration_report

    report = retained_decoration_report("subtitle")
    case = ReviewCase.create(evaluate_report(report), report)
    web.repository.case = case
    web.repository.report = report
    web.repository.browse_cases = [(case, report)]
    web.repository.policy_attention_destinations = ("title", "subtitle")
    web.login()
    detail = web.client.get(f"/api/v1/review-cases/{case.review_case_id}")
    assert detail.status_code == 200, detail.text
    fields = {field["destination"]: field for field in detail.json()["fields"]}
    assert fields["subtitle"]["classification"] == "needs_attention"
    assert fields["subtitle"]["editable"] is True
    attention = web.client.get("/api/v1/attention?limit=10")
    assert attention.status_code == 200, attention.text
    assert {"title", "subtitle"} <= {
        item["destination"] for item in attention.json()["items"]
    }


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
        _attention_item(case, report, destination, 1) is None
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
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
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


def _recorded_audiobook() -> MetadataReport:
    """Build one audiobook report the way the isolated inspector builds it.

    `interpret` is the same pure grammar the inspection child runs, fed a
    recorded ffprobe document rather than a probe, so the claims this report
    carries are the ones a real m4b produces and not a hand-written imitation
    of them.
    """
    interpretation = interpret(
        json.dumps(
            {
                "chapters": [
                    {
                        "start_time": "0.000",
                        "end_time": "1800.000",
                        "tags": {"title": "Chapter One"},
                    }
                ],
                "format": {
                    "duration": "43200.000000",
                    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                    "tags": {"PERFORMANCETYPE": "unabridged"},
                },
                "streams": [
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "tags": {"language": "eng"},
                    },
                    {
                        "codec_type": "video",
                        "codec_name": "mjpeg",
                        "disposition": {"attached_pic": 1},
                    },
                ],
            }
        ),
        (
            AudioSidecar(
                AudioEvidenceSurface.SIDECAR_LIBATION,
                "book.metadata.json",
                json.dumps(
                    {
                        "asin": "B0CW1KL2QD",
                        "narrators": [{"name": "Recorded Narrator"}],
                        "publisherSummary": "A recorded summary.",
                        "series": [{"name": "Discworld", "sequence": "3"}],
                        "title": "Recorded work",
                    }
                ),
            ),
        ),
        AudioPathContext("book.m4b"),
        Fingerprint("sha256", SchemaVersion("local-file-v1"), "a" * 64),
        AudioInspectionLimits(),
    )
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind(fact.field.value),
            observed=fact.observed_value,
            canonical=fact.canonical_value,
            excerpt=fact.supporting_context,
            applicability="not_applicable",
            basis=fact.basis,
            checksum="not_applicable",
            applicability_basis="not_applicable",
            state=FieldState(fact.state),
            role=fact.role,
            occurrence=occurrence,
        )
        for occurrence, fact in enumerate(interpretation.facts, 1)
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-9", default_target_fields("Recorded work")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=tuple(
            sorted({f"{claim.basis}_{claim.kind.value}" for claim in claims})
        )
        or ("no_local_audio_claims",),
        providers=(),
        audio=AudioFacts(
            interpretation.chapters,
            interpretation.chapters_truncated,
            interpretation.embedded_cover,
            chapter_rows_omitted=interpretation.chapter_rows_omitted,
            tracks=(AudioTrackDigest(1, "c" * 64, "b" * 64, "m4b", "book.m4b"),),
        ),
    )


def test_an_audiobooks_recorded_evidence_is_reachable_from_the_library(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The owner can get from the listing to what the inspection recorded.

    An audiobook acquires no review case, so its row carried no address and
    every field the F43 inspection wrote -- ASIN, narrator, series, duration,
    performance type, language, description, chapters, cover presence -- was
    recorded, retained and unreachable.  This walks the only route an owner
    has: the listing, the address it hands back, and the document behind it.
    """
    web.repository.browse_without_cases.append(_recorded_audiobook())
    web.login()

    listing = web.client.get("/api/v1/library?lens=all&q=Recorded work")
    assert listing.status_code == 200, listing.text
    item = next(value for value in listing.json()["items"] if value["item"] == "item-9")
    assert item["review_case_id"] is None

    detail = web.client.get(cast(str, item["detail_url"]))

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["schema_version"] == "review-report-detail-v5"
    assert (body["connection"], body["library"], body["item"]) == (
        "primary",
        "ebooks",
        "item-9",
    )
    assert body["media_kind"] == "m4b"
    evidence = {
        (value["kind"], value["role"], value["value"], value["state"])
        for value in body["evidence"]
    }
    assert {
        ("asin", None, "B0CW1KL2QD", "valid"),
        ("contributor", "narrator", "Recorded Narrator", "valid"),
        ("description", None, "A recorded summary.", "valid"),
        ("duration", None, "43200.000", "valid"),
        ("language", None, "eng", "valid"),
        ("performance_type", None, "unabridged", "valid"),
        ("series", None, "Discworld", "valid"),
        ("series_sequence", None, "3", "valid"),
    } <= evidence
    # Every claim states where it came from and what the extraction made of
    # it, so the owner reads evidence rather than an assertion -- and a
    # malformed or oversized claim cannot render as a sound one.
    assert all(value["basis"] for value in body["evidence"])
    assert body["evidence_truncated"] is False
    assert body["chapters"] == [
        {
            "end_milliseconds": 1_800_000,
            "ordinal": 1,
            "start_milliseconds": 0,
            "title": "Chapter One",
        }
    ]
    assert body["chapters_truncated"] is False
    # Presence and its surface, and deliberately no bytes.
    assert body["embedded_cover"] == {
        "present": True,
        "surface": "container_stream",
    }
    # A recording with nothing wrong with it states nothing: the section is
    # present and empty rather than absent, so the owner reads a checked
    # silence rather than a missing surface.
    assert body["media_health"] == []


def test_a_recordings_media_health_reaches_the_owner_with_its_bound(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The derived section on the wire, in the one closed shape both surfaces use.

    This item carries its own ASIN, so the per-ASIN answer keeps the running
    time it stated and the derivation has two sides to compare. What the owner
    reads is the statement, what it was read from, and the rule it broke.
    """
    web.repository.browse_without_cases.append(
        asserted_asin_report(
            provider_duration="72000.000",
            local_asin="B08G9PRS1K",
            library="ebooks",
            item="item-10",
        )
    )
    web.login()

    response = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-10"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "review-report-detail-v5"
    assert len(body["media_health"]) == 1
    stated = body["media_health"][0]
    assert stated["kind"] == "runtime_short_of_provider"
    assert (
        stated["bound"]
        == (AUDIO_HEALTH_BOUNDS[AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER])
    )
    assert stated["observed"] == "58244 seconds of local audio"
    assert stated["expected"] == "72000 seconds stated by the matched edition"
    assert stated["cited"][-1] == "audible-product-B08G9PRS1K"
    assert stated["tracks"] == []


def test_an_audiobook_case_states_its_media_health_beside_its_fields() -> None:
    """The surface Library opens for an admitted audiobook carries the section.

    An audiobook whose identity was admitted acquires a review case, so its row
    opens `/review/<id>` and never the recorded-evidence view. Deriving the
    section only there would have hidden it from exactly the items it was built
    for, and it is read from the same retained report the fields beside it were
    read from rather than from a second source.
    """
    report = admitted_audiobook_report(
        chapters=(
            AudioChapter(1, 0, 100_000, "One"),
            AudioChapter(2, 100_000, 300_000, "Two"),
        )
    )
    case = ReviewCase.create(evaluate_report(report), report)

    detail = InspectCurrentReviewCase(FakeReviewRepository(case, report)).run(
        case.review_case_id
    )

    stated = cast(list[dict[str, Any]], detail["media_health"])
    assert [value["kind"] for value in stated] == ["chapter_structure_mismatch"]
    assert stated[0]["cited"] == ["single_file", "audible-product-B08G9PRS1K"]
    assert (
        stated[0]["bound"]
        == (AUDIO_HEALTH_BOUNDS[AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH])
    )
    # A recording with nothing wrong with it states a present and empty
    # section rather than an absent member, so a reader can tell a checked
    # silence from a surface that was never asked.
    silent = admitted_audiobook_report()
    silent_case = ReviewCase.create(evaluate_report(silent), silent)

    assert (
        InspectCurrentReviewCase(FakeReviewRepository(silent_case, silent)).run(
            silent_case.review_case_id
        )["media_health"]
        == []
    )


def test_the_field_classification_wire_vocabulary_is_the_domain_vocabulary(
    tmp_path,
) -> None:
    """The wire states exactly the classifications the domain can produce.

    The response model reads the domain's own vocabulary rather than a copy,
    so a new state, such as a recording's optional choice (#1249), reaches the
    generated OpenAPI and the browser's types with it, and nothing else does.
    """
    from contextlib import closing

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.audiobook_reports import publish_audiobook_preview

    published = _ReviewField.model_json_schema()["properties"]["classification"]
    assert set(published["enum"]) == set(get_args(ReviewFieldState))
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, admitted_audiobook_report(), ("isbn_form",)
        )
        detail = InspectCurrentReviewCase(repository).run(case_id)
    # The application layer adds the field's effect capability on the way out.
    asin = {
        **next(f for f in detail["fields"] if f["destination"] == "asin"),
        "effect_capability": "unavailable",
    }

    assert _ReviewField.model_validate(asin).classification == "owner_choosable"
    with pytest.raises(ValidationError):
        _ReviewField.model_validate({**asin, "classification": "queued"})


def test_the_media_health_wire_vocabulary_is_the_closed_domain_vocabulary() -> None:
    """The wire admits the thirteen kinds the domain states, and nothing else.

    A bounded string would let a build widen the vocabulary without the client
    ever being told, so the response model closes it and this pins the two sets
    equal -- including in the generated OpenAPI, which is what the frontend's
    types and any other reader are built from.
    """
    observation = {
        "bound": AUDIO_HEALTH_BOUNDS[AudioHealthKind.MEDIA_UNREADABLE],
        "cited": ["track 2"],
        "expected": None,
        "kind": AudioHealthKind.MEDIA_UNREADABLE.value,
        "observed": None,
        "tracks": [2],
    }
    published = _MediaHealthObservation.model_json_schema()["properties"]["kind"]

    assert set(published["enum"]) == {kind.value for kind in AudioHealthKind}
    assert {
        _MediaHealthObservation.model_validate({**observation, "kind": kind.value}).kind
        for kind in AudioHealthKind
    } == {kind.value for kind in AudioHealthKind}
    with pytest.raises(ValidationError):
        _MediaHealthObservation.model_validate(
            {**observation, "kind": "media_shredded"}
        )
    # An observation citing nothing cannot be checked against the report, which
    # is the whole of what separates this vocabulary from an accusation. The
    # domain shape refuses to build one; the wire refuses to carry one.
    with pytest.raises(ValidationError):
        _MediaHealthObservation.model_validate({**observation, "cited": []})


def test_the_media_health_transport_cap_is_the_domain_ceiling() -> None:
    """The wire carries every observation the domain can state, and no fewer.

    The domain truncates at one observation per kind per track and stops there,
    so a transport bound below that ceiling would not protect anything -- it
    would turn a report the domain built legally into a 500 at response
    validation, on exactly the crowded item whose health the owner most needs
    to read. The two bounds are read from one constant here so that widening
    the vocabulary moves them together.
    """
    for model in (_ReportDetailResponse, _CaseDetailResponse):
        field = model.model_fields["media_health"]
        (constraint,) = [item for item in field.metadata if hasattr(item, "max_length")]
        assert constraint.max_length == MAX_HEALTH_OBSERVATIONS


def _claim_rich_report(count: int) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-8", default_target_fields("Crowded work")
        ),
        file=FileFacts(0, "c" * 64, "d" * 64),
        local_claims=tuple(
            LocalClaim.create(
                kind=ClaimKind.SUBJECT,
                observed=f"Subject {index:04d}",
                canonical=f"Subject {index:04d}",
                excerpt=f"Subject {index:04d}",
                applicability="not_applicable",
                basis="opening_matter",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=index,
            )
            for index in range(1, count + 1)
        ),
        local_reasons=("opening_matter_subject",),
        providers=(),
    )


@pytest.mark.parametrize(
    ("claims", "returned", "truncated"),
    (
        pytest.param(1_024, 1_024, False, id="at-the-bound"),
        pytest.param(1_025, 1_024, True, id="past-the-bound"),
    ),
)
def test_a_claim_rich_report_is_truncated_rather_than_refused(
    web: ReviewWebContext[FakeReviewRepository],
    claims: int,
    returned: int,
    truncated: bool,
) -> None:
    """Nothing but the report's byte ceiling caps how many claims it carries.

    A transport bound on the evidence list would therefore be a number the
    domain never agreed to, and the work that exceeded it would answer 500
    instead of showing its evidence.  The projection truncates in the report's
    own order and says so.
    """
    report = _claim_rich_report(claims)
    web.repository.browse_without_cases.append(report)
    web.login()

    response = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-8"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["evidence"]) == returned
    assert body["evidence_truncated"] is truncated
    # The kept prefix is the report's own, so two reads agree on what was cut.
    assert [value["value"] for value in body["evidence"][:3]] == [
        "Subject 0001",
        "Subject 0002",
        "Subject 0003",
    ]


def test_an_item_id_holding_a_slash_is_reachable_at_the_address_it_advertises(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """A library server's item id may contain a slash, and both ends must hold.

    Percent-encoded in a path segment it is decoded before the router matches,
    so such a work answered 404 at exactly the address its own row handed out.
    The row's `detail_url` is followed here verbatim rather than rebuilt, so
    the generator and the route are proved to agree.
    """
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "col/1", default_target_fields("Slashed work")
        ),
        file=FileFacts(0, "e" * 64, "f" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )
    web.repository.browse_without_cases.append(report)
    web.login()

    listing = web.client.get("/api/v1/library?lens=all&q=Slashed")
    assert listing.status_code == 200, listing.text
    advertised = next(
        value["detail_url"]
        for value in listing.json()["items"]
        if value["item"] == "col/1"
    )
    response = web.client.get(cast(str, advertised))

    assert advertised == (
        "/api/v1/library/report?connection=primary&library=ebooks&item=col%2F1"
    )
    assert response.status_code == 200, response.text
    assert response.json()["item"] == "col/1"


def test_a_store_that_cannot_answer_is_not_a_work_that_is_not_there(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """Absence is 404 and a persistence fault is 503, never one for the other.

    Collapsing them told an owner their audiobook had vanished whenever the
    database was busy or unreadable, which is the opposite of what happened.
    """
    web.login()

    missing = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=no-such-item"
    )

    def unavailable(_connection: str, _library: str, _item: str) -> str | None:
        raise MetadataReportError("review snapshot is unavailable")

    web.repository.read_current_report_document = unavailable  # type: ignore[method-assign,assignment]
    failed = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-1"
    )

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "missing")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "unavailable",
    )


def test_a_shaped_but_corrupt_document_is_refused_rather_than_projected(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The browse envelope closes the shape; it does not check the report.

    Every member arrives from it on trust, so a document that satisfies the
    envelope while contradicting the domain would have projected arbitrary
    text to the owner as recorded evidence. Reconstructing the report is what
    refuses it, and the report id below is exactly the kind of member the
    envelope has no opinion about.
    """
    document = json.loads(_report(title="Corrupt work", actionable=False).to_json())
    document["report_id"] = "report_" + "0" * 64
    corrupted = json.dumps(document)

    def corrupt(_connection: str, _library: str, _item: str) -> str | None:
        return corrupted

    web.repository.read_current_report_document = corrupt  # type: ignore[method-assign,assignment]
    web.login()

    response = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-1"
    )

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "unavailable",
    )


def test_every_library_row_names_what_its_address_opens() -> None:
    """The three shapes a row can have, and the one word that tells them apart.

    A reader that inferred the kind from the URL -- a `/api/` prefix against a
    `/review/` one -- would be silently re-orphaned by a route rename, which is
    how this evidence became unreachable in the first place. The discriminator
    is asserted here rather than only through the browser, so the joint is held
    by the contract.
    """
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    terminal = json.dumps(
        {
            "attempt": 1,
            "connection": "primary",
            "failure_root": None,
            "item": "item-3",
            "lane": "extraction",
            "library": "ebooks",
            "recovery_attempt": 0,
            "schema_version": "review-terminal-work-v1",
            "status": "unreadable_media",
            "work_id": "review_work_" + "c" * 64,
        }
    )

    opened = _library_item(case.to_json(), report.to_json(), ())
    recorded = _library_item(None, report.to_json(), ())
    stopped = _library_item(None, terminal, ())

    assert (opened["detail_kind"], opened["detail_url"]) == (
        "case",
        f"/review/{case.review_case_id}",
    )
    assert (recorded["detail_kind"], recorded["detail_url"]) == (
        "report",
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-1",
    )
    # Preparation stopped before a report existed, so there is nothing to open
    # and the row says so rather than offering an address that answers nothing.
    assert (stopped["detail_kind"], stopped["detail_url"]) == (None, None)


def test_a_terminal_unreadable_item_states_the_track_it_could_not_read() -> None:
    """The terminal row carries media health in the report detail's own shape.

    An item that stopped before publishing a report has no evidence to derive
    from, so the one statement its record supports is the file that could not
    be read -- and the owner reads it in the same vocabulary the evidence view
    uses rather than only inside a sentence of prose.
    """

    def _terminal(status: str, root: str | None) -> str:
        return json.dumps(
            {
                "attempt": 1,
                "connection": "primary",
                "failure_root": root,
                "item": "item-4",
                "lane": "extraction",
                "library": "audio",
                "recovery_attempt": 0,
                "schema_version": "review-terminal-work-v1",
                "status": status,
                "work_id": "review_work_" + "d" * 64,
            }
        )

    unreadable = _library_item(None, _terminal("unreadable_media", "track_2"), ())
    whole_item = _library_item(None, _terminal("unreadable_media", None), ())
    refused = _library_item(None, _terminal("parser_refused", "track_2"), ())
    malformed = _library_item(None, _terminal("unreadable_media", "shredded"), ())
    past_bound = _library_item(None, _terminal("unreadable_media", "track_600"), ())
    padded = _library_item(None, _terminal("unreadable_media", "track_007"), ())
    zeroed = _library_item(None, _terminal("unreadable_media", "track_0"), ())

    assert cast(dict[str, Any], unreadable["preparation"])["media_health"] == [
        {
            "bound": AUDIO_HEALTH_BOUNDS[AudioHealthKind.MEDIA_UNREADABLE],
            "cited": ["track 2"],
            "expected": None,
            "kind": "media_unreadable",
            "observed": None,
            "tracks": [2],
        }
    ]
    # A single-file item records no ordinal and cites the item instead.
    assert cast(dict[str, Any], whole_item["preparation"])["media_health"] == [
        {
            "bound": AUDIO_HEALTH_BOUNDS[AudioHealthKind.MEDIA_UNREADABLE],
            "cited": ["this item's audio files"],
            "expected": None,
            "kind": "media_unreadable",
            "observed": None,
            "tracks": [],
        }
    ]
    # Every other terminal status is about preparation rather than the bytes.
    assert cast(dict[str, Any], refused["preparation"])["media_health"] == []
    # The writer records either no root or one track ordinal within the
    # composition bound, so anything else is malformed retained content. It
    # states nothing rather than being read as "no track was named", which
    # would turn a corrupt row into an accusation about every file of the item.
    assert cast(dict[str, Any], malformed["preparation"])["media_health"] == []
    assert cast(dict[str, Any], past_bound["preparation"])["media_health"] == []
    # The reader admits exactly the grammar the writer emits -- `track_{ordinal}`
    # from an integer that is never zero and never zero-padded -- so a row
    # naming `track_007` or `track_0` names no file this build ever wrote. The
    # observation and the refusing-file sentence agree by construction because
    # both are read through that one grammar.
    assert cast(dict[str, Any], padded["preparation"])["media_health"] == []
    assert cast(dict[str, Any], zeroed["preparation"])["media_health"] == []
    for corrupt in (padded, zeroed):
        assert "refusing file" not in str(
            cast(dict[str, Any], corrupt["preparation"])["reason"]
        )


def test_a_package_row_pointing_at_another_work_serves_no_evidence(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The report must be the report of the work that was asked for.

    `current_review_packages` names a report by id, and the report names its
    own target. Nothing made the two agree here, so a row corrupted to point
    at another work's canonical report would have served that work's ASIN,
    narrator and description under this item's name -- evidence attributed to
    the wrong book, which is worse than no evidence at all. The browse path
    already compares the two; this is the same comparison.
    """
    stranger = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "someone-else", default_target_fields("Other work")
        ),
        file=FileFacts(0, "9" * 64, "8" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )

    def crossed(_connection: str, _library: str, _item: str) -> str | None:
        return stranger.to_json()

    web.repository.read_current_report_document = crossed  # type: ignore[method-assign,assignment]
    web.login()

    response = web.client.get(
        "/api/v1/library/report?connection=primary&library=ebooks&item=item-1"
    )

    assert (response.status_code, response.json()["error"]["code"]) == (
        503,
        "unavailable",
    )


@pytest.mark.parametrize("omitted", (False, True))
def test_chapter_runtime_admission_is_attributed_in_case_and_report(
    web, omitted: bool
) -> None:
    from mechanical_librarian.application.review_web import _report_detail
    from tests.support.audiobook_reports import chapter_runtime_report

    report = chapter_runtime_report(chapter_rows_omitted=omitted)
    case = ReviewCase.create(evaluate_report(report), report)
    repository = _CloseRecordingRepository(case, report)
    web.client.close()
    web.client = TestClient(
        create_transport_test_app(
            repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(web.hash_path),
                session_lifetime_seconds=600,
            ),
            clock=lambda: web.now,
        ),
        base_url="https://review.test",
    )
    web.login()
    response = web.client.get(f"/api/v1/review-cases/{case.review_case_id}")
    assert response.status_code == 200
    basis = response.json()["recording_identity_basis"]
    assert (basis is None) is omitted
    assert _report_detail(report.to_data(), report)["recording_identity_basis"] == basis
    assert response.json()["schema_version"] == "review-case-detail-v6"
    if basis is not None:
        assert basis == {
            "basis": "chapter_runtime_and_structure",
            "provider": "catalogue",
            "candidate": "audible-product-B08G9PRS1K",
        }
        observation = next(
            p for p in report.providers if p.provider == basis["provider"]
        )
        assert basis["candidate"] in {c.identity for c in observation.candidates}
        assert not any(f["automatic_decision"] for f in response.json()["fields"])


@pytest.mark.parametrize(
    "basis",
    (
        {},
        {"basis": "other", "provider": "source", "candidate": "item"},
        {
            "basis": "chapter_runtime_and_structure",
            "provider": "source",
            "candidate": "item",
            "authority": True,
        },
    ),
)
def test_recording_identity_wire_shape_is_closed(basis) -> None:
    from mechanical_librarian.interfaces.web.app import _RecordingIdentityBasis

    with pytest.raises(ValidationError):
        _RecordingIdentityBasis.model_validate(basis)


@pytest.mark.parametrize(
    "field,value",
    (
        ("provider", " "),
        ("candidate", "bad\nreference"),
        ("candidate", "x" * 129),
        ("provider", None),
        ("candidate", 1),
    ),
)
def test_recording_identity_wire_references_are_bounded_canonical_text(
    field: str, value: object
) -> None:
    from mechanical_librarian.interfaces.web.app import _RecordingIdentityBasis

    data = {
        "basis": "chapter_runtime_and_structure",
        "provider": "source",
        "candidate": "recording",
    }
    with pytest.raises(ValidationError):
        _RecordingIdentityBasis.model_validate({**data, field: value})


def test_current_policy_exposes_exact_accepted_audiobook_operations(web):
    web.login()
    entries = web.client.get("/api/v1/metadata-policy/state").json()[
        "audiobook_qualification"
    ]
    available = {entry["identity"] for entry in entries if entry["automatic_available"]}
    assert available == audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
    assert len(available) == 76
    assert {
        entry["destination"] for entry in entries if entry["automatic_available"]
    } == {
        "title",
        "authors",
        "narrators",
        "language",
        "series",
        "genres",
        "asin",
        "cover",
        "publishedDate",
        "description",
        "explicit",
        "isbn",
        "publisher",
        "subtitle",
        "tags",
    }
    assert all(
        entry["record_sha256"]
        == audiobook_decision_qualification.F46_QUALIFICATION_MANIFEST_SHA256
        for entry in entries
        if entry["automatic_available"]
    )
    assert all(
        entry["record_sha256"] is None
        for entry in entries
        if not entry["automatic_available"]
    )


@pytest.mark.parametrize(
    "shape", ("conflict", "malformed", "unassociated", "absent", "unavailable")
)
def test_retained_audiobook_identifiers_are_paged_observations_without_choices(
    tmp_path, shape
):
    from collections import Counter
    from pathlib import Path

    from argon2 import PasswordHasher

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.audiobook_reports import (
        publish_audiobook_preview,
        synthetic_audiobook_isbn_reason_report,
    )

    report = synthetic_audiobook_isbn_reason_report(shape)
    repository = open_repository(tmp_path / "state.sqlite", cross_thread=True)
    case_id, _ = publish_audiobook_preview(
        repository,
        report,
        ("isbn_form",),
        selected_options={"isbn_form": "preserve_exact"},
    )
    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"))
    password.chmod(0o600)
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=lambda: 10,
    )
    if shape == "unavailable":
        from mechanical_librarian.application.review_web import _report_detail
        from mechanical_librarian.domain.audiobook_corroboration import (
            admitted_audiobook_identity,
        )

        assert not admitted_audiobook_identity(report)
        assert case_id is None
        recorded = _report_detail(report.to_data(), report)
        assert recorded["audiobook_review_scope"] is None
        assert recorded["recording_identity_basis"] is None
        assert {source["status"] for source in recorded["provider_sources"]} == {
            "no_match",
            "unavailable",
        }
        assert not any(
            claim.kind is ClaimKind.ISBN
            for provider in report.providers
            for candidate in (
                *provider.candidates,
                *provider.recording_lookup_candidates,
            )
            for claim in candidate.claims
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        with TestClient(app, base_url="https://review.test") as client:
            assert (
                client.post(
                    "/api/v1/session/login",
                    json={"password": "correct horse"},
                    headers={"Origin": "https://review.test"},
                ).status_code
                == 200
            )
            response = client.get(
                "/api/v1/library/report",
                params={
                    "connection": report.target.connection,
                    "library": report.target.library,
                    "item": report.target.item,
                },
            )
            assert response.status_code == 200
            assert response.json()["audiobook_review_scope"] is None
            assert {
                source["status"] for source in response.json()["provider_sources"]
            } == {"no_match", "unavailable"}
        repository.close()
        return
    repository.persist_shadow_outcome(case_id, 4)
    before = repository.get_review_case_decisions(case_id)
    inspector = InspectCurrentReviewCase(repository)
    detail = inspector.run(case_id)
    for field in detail["fields"]:
        if field["destination"] == "isbn":
            assert field["proposed_values"] == []
            assert field["automatic_decision"] is None
        if field["destination"] == "asin":
            assert {value["value"] for value in field["proposed_values"]} == {
                "B000000001",
                "B000000002",
            }
            assert set(field["provenance"]) <= {
                claim.reference
                for provider in report.providers
                for candidate in provider.candidates
                for claim in candidate.claims
                if claim.kind is ClaimKind.ASIN
                and claim.value in {"B000000001", "B000000002"}
            }
            assert field["provenance"]
            assert field["automatic_decision"] is None
    linked = {
        reference for field in detail["fields"] for reference in field["provenance"]
    }
    claims = {
        claim.reference: (provider.authority, candidate, claim)
        for provider in report.providers
        for candidate in (*provider.candidates, *provider.recording_lookup_candidates)
        for claim in candidate.claims
        if claim.kind in {ClaimKind.ISBN, ClaimKind.ASIN}
        and claim.reference not in linked
    }
    expected = inspector.evidence(case_id)
    observations = [
        item for item in expected["items"] if item["role"] == "external_observation"
    ]
    assert len(observations) == len(claims)
    expected_facts: Counter[tuple[str, str, tuple[str, ...], str]] = Counter()
    for reference, (authority, candidate, claim) in claims.items():
        fact = (
            claim.value
            if claim.state is FieldState.VALID
            else "No valid identifier retained"
        )
        expected_facts[
            (
                {"audible_catalog": "Audible", "audnexus": "Audnexus"}[authority],
                "external_observation",
                (claim.kind.value,),
                f"{claim.kind.value.upper()}: {fact}. Retained for review only; "
                f"not an eligible choice. Scope: {claim.scope.value}; "
                f"state: {claim.state.value}; applicability: {claim.applicability}.",
            )
        ] += 1
        assert all(reference not in item["observation"] for item in observations)
        assert all(
            candidate.identity not in item["observation"] for item in observations
        )
    assert (
        Counter(
            (
                item["source"],
                item["role"],
                tuple(item["destinations"]),
                item["observation"],
            )
            for item in observations
        )
        == expected_facts
    )
    if shape == "conflict":
        assert {
            claim.value
            for _, _, claim in claims.values()
            if claim.kind is ClaimKind.ISBN
        } == {"9781250077004", "9780593135204"}
        assert {fact.reference for fact in report.identifier_evidence} <= set(claims)
    if shape == "malformed":
        assert any("state: malformed" in item["observation"] for item in observations)
    with TestClient(app, base_url="https://review.test") as client:
        assert (
            client.post(
                "/api/v1/session/login",
                json={"password": "correct horse"},
                headers={"Origin": "https://review.test"},
            ).status_code
            == 200
        )
        items = []
        cursor = None
        while True:
            response = client.get(
                f"/api/v1/review-cases/{case_id}/evidence",
                params={"limit": 1, **({"cursor": cursor} if cursor else {})},
            )
            assert response.status_code == 200
            page = response.json()
            assert page["missing_lineage_count"] == 0
            assert len(page["items"]) <= 1
            items.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert items == expected["items"]
        if shape == "conflict":
            response = client.get(f"/api/v1/review-cases/{case_id}")
            assert response.status_code == 200
            fixture = json.loads(
                Path("frontend/tests/identifierEvidenceFixture.json").read_text()
            )
            from mechanical_librarian.domain.review_case import (
                ReviewCase,
            )

            case = ReviewCase.from_json(
                repository._connection.execute(
                    "SELECT document FROM review_cases WHERE review_case_id=?",
                    (case_id,),
                ).fetchone()[0]
            )
            proposal_ids = {
                proposal["destination"]: proposal["proposal_id"]
                for proposal in case.proposals
            }
            for field in fixture["detail"]["fields"]:
                field["proposal_id"] = proposal_ids[field["destination"]]
            fixture["detail"]["review_case_id"] = case_id
            fixture["evidence"]["review_case_id"] = case_id
            assert fixture == {"detail": response.json(), "evidence": expected}
        retained = [item for item in items if item["role"] == "external_observation"]
        assert retained == observations
        assert len(retained) == sum(expected_facts.values())
        assert repository.get_review_case_decisions(case_id) == before
        assert inspector.run(case_id) == detail
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
    repository.close()


def test_linked_audiobook_identifier_evidence_is_not_duplicated(tmp_path):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.audiobook_reports import publish_audiobook_preview

    repository = open_repository(tmp_path / "state.sqlite")
    try:
        case_id, _ = publish_audiobook_preview(
            repository, admitted_audiobook_report(), ("isbn_form",)
        )
        inspector = InspectCurrentReviewCase(repository)
        field = next(
            item
            for item in inspector.run(case_id)["fields"]
            if item["destination"] == "asin"
        )
        assert field["proposed_values"] == [{"kind": "text", "value": "B08G9PRS1K"}]
        assert len(field["provenance"]) == 1
        items = [
            item
            for item in inspector.evidence(case_id)["items"]
            if "asin" in item["destinations"]
        ]
        assert len(items) == 1
        assert items[0]["role"] == "external_corroboration"
        assert items[0]["observation"] == "ASIN: B08G9PRS1K"
    finally:
        repository.close()


@pytest.mark.parametrize(
    "statements", (("abridged", "unabridged"), ("abridged", "full cast"))
)
def test_conflicting_recording_statements_remain_nonselectable_evidence(statements):
    from dataclasses import replace

    from mechanical_librarian.application.review_web import (
        _review_unselectable_evidence,
    )
    from mechanical_librarian.domain.metadata_report import (
        ClaimScope,
        MetadataReport,
        ProviderEvidence,
        ProviderEvidenceKind,
        TargetDestination,
        recording_variant_conflict,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.audiobook_reports import admitted_audiobook_report

    base = admitted_audiobook_report()
    provider = base.providers[0]
    candidate = provider.candidates[0]
    candidate = replace(
        candidate,
        evidence=tuple(
            sorted(
                (
                    *candidate.evidence,
                    *(
                        ProviderEvidence(
                            ProviderEvidenceKind.EDITION_STATEMENT,
                            value,
                            ClaimScope.EDITION,
                        )
                        for value in statements
                    ),
                ),
                key=lambda evidence: (
                    evidence.scope.value,
                    evidence.kind.value,
                    evidence.value,
                ),
            )
        ),
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(replace(provider, candidates=(candidate,)),),
        audio=base.audio,
    )
    reopened = MetadataReport.from_json(report.to_json())
    assert reopened == report
    assert (
        recording_variant_conflict(
            candidate, reopened.local_claims, reopened.target.current_title or ""
        )
        == "recording_variant_conflict"
    )
    narrator = next(
        result
        for result in evaluate_report(reopened).results
        if result.destination is TargetDestination.NARRATORS
    )
    assert narrator.alternatives == ()
    claim = next(claim for claim in candidate.claims if claim.role == "narrator")
    evidence = _review_unselectable_evidence(reopened, set())[claim.reference]
    assert evidence["role"] == "external_observation"
    assert evidence["destinations"] == ["narrators"]
    assert evidence["observation"] == (
        "Narrator: Ray Porter. Retained for review only; not an eligible choice. "
        "Recording variant conflicts with this item. "
        f"Scope: {claim.scope.value}; state: {claim.state.value}; "
        f"applicability: {claim.applicability}."
    )
    assert claim.reference not in _review_unselectable_evidence(
        reopened, {claim.reference}
    )


@pytest.mark.parametrize("mode", ("automatic", "ask", "ignore"))
def test_unrecorded_synopsis_does_not_reconstruct_a_refusal(mode):
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from tests.support.ebook_synopsis import synopsis_report

    report = synopsis_report(summaries=("(duplicate)",))
    builtin = MetadataPolicyProfile.builtin()
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Unrecorded synopsis",
            dict(builtin.options) | {"description_fill": "safe_plain_text"},
            dict(builtin.destinations) | {"description_fill": mode},
        ),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    case = ReviewCase.create(proposal, report)
    repository = FakeReviewRepository(case, report)
    snapshot = replace(
        repository.read_current_review_case(case.review_case_id, None),
        resolved_policy_document=policy.canonical_json,
    )
    with patch.object(repository, "read_current_review_case", return_value=snapshot):
        detail = InspectCurrentReviewCase(repository).run(case.review_case_id)
    field = next(f for f in detail["fields"] if f["destination"] == "description")
    assert field["automatic_withheld_reason"] == (
        "No recorded automatic evaluation is available for this field."
        if mode == "automatic"
        else None
    )


def test_identifier_relationship_preserves_actual_claim_value() -> None:
    from mechanical_librarian.application.review_web import (
        _choice_evidence,
        _evidence_by_reference,
    )
    from tests.support.proposal_evidence import _image_unavailable_isbn_report

    report = _image_unavailable_isbn_report()
    evidence = _evidence_by_reference(report)
    for relationship in report.identifier_evidence:
        retained = evidence[relationship.reference]
        fact = json.loads(retained["fact"])
        assert retained["source"] in {"local", "provider_claim"}
        assert fact["kind"] == "isbn"
        expected = fact.get("value", fact.get("observed"))
        assert expected
        assert _choice_evidence(retained)["observation"] == f"ISBN: {expected}"
        changed = {**retained, "fact": json.dumps({**fact, "value": "9780593135204"})}
        assert _choice_evidence(changed)["observation"] == "ISBN: 9780593135204"


@pytest.mark.parametrize(
    "fact",
    [
        None,
        "{",
        "[]",
        '{"kind":"isbn","value":{"secret":"never displayed"}}',
        '{"kind":"isbn","value":["never displayed",null]}',
    ],
)
def test_choice_observation_does_not_invent_nested_values(fact: str | None) -> None:
    from mechanical_librarian.application.review_web import _choice_observation

    text = _choice_observation({"source": "provider_claim", "fact": fact})
    assert "never displayed" not in text
    assert "supports" not in text


@pytest.mark.parametrize("provider", [None, "unknown-provider"])
def test_unknown_choice_authority_is_observation_not_corroboration(
    provider: str | None,
) -> None:
    from mechanical_librarian.application.review_web import _choice_evidence

    projected = _choice_evidence(
        {
            "source": "provider_claim",
            "provider": provider,
            "fact": '{"kind":"isbn","value":"9780593135204"}',
        }
    )
    assert projected == {
        "observation": "ISBN: 9780593135204",
        "role": "external_observation",
        "source": "Retained observation (provider name unavailable)",
    }


@pytest.mark.parametrize("backup_ready", [True, False, None])
def test_activity_history_defers_permission_fields(backup_ready: bool | None) -> None:
    from mechanical_librarian.interfaces.web.app import (
        _ActivityPermissionReadiness,
        _ActivityResponse,
    )

    application = FakeActivityApplication()
    history = application.activity()
    assert _ActivityResponse.model_validate(history).backup_ready is None
    permission = application.activity_permission_readiness()
    permission["backup_ready"] = backup_ready
    checked = _ActivityPermissionReadiness.model_validate(permission)
    assert checked.backup_ready is backup_ready
    assert checked.example is not None
    if backup_ready is not None:
        with pytest.raises(ValidationError):
            _ActivityResponse.model_validate({**history, "backup_ready": backup_ready})
    with pytest.raises(ValidationError):
        _ActivityResponse.model_validate({**history, "example": permission["example"]})
    for field in ("backup_ready", "example"):
        with pytest.raises(ValidationError):
            _ActivityResponse.model_validate(
                {key: value for key, value in history.items() if key != field}
            )


@pytest.fixture
def canonical_attention_pair():
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    return case, report


def test_attention_consecutive_documents_keep_row_specific_classification():
    from tests.support.proposal_evidence import retained_decoration_report

    report = retained_decoration_report("subtitle")
    case = ReviewCase.create(evaluate_report(report), report)
    flags = (
        (1, None, None, False),
        (2, None, None, True),
        (3, "abstention", None, True),
        (4, None, "automatic_safety_suspended", False),
    )
    rows = tuple((case.to_json(), report.to_json(), "title", *flag) for flag in flags)
    expected = [
        item
        for count, outcome, cause, policy in flags
        if (
            item := _attention_item(
                *_case_and_report(case.to_json(), report.to_json()),
                "title",
                count,
                outcome,
                cause,
                policy,
            )
        )
        is not None
    ]
    repository = Mock()
    repository.read_review_attention.return_value = ReviewAttentionSnapshot(
        rows, len(rows)
    )
    with patch(
        "mechanical_librarian.application.review_web._case_and_report",
        wraps=_case_and_report,
    ) as parse:
        page = BrowseReviewLibrary(repository).attention(
            search=None, limit=10, cursor=None
        )
    parse.assert_called_once_with(case.to_json(), report.to_json())
    assert page["items"] == expected
    assert [item["work_attention_count"] for item in expected] == [2, 3, 4]
    assert len({item["classification_reason"] for item in expected}) == 3
    assert page["total"] == 4 and page["next_cursor"] is None


@pytest.mark.parametrize(
    "changed", ("case", "report", "both", "case_subclass", "report_subclass")
)
def test_attention_equal_ids_never_replace_exact_document_validation(
    canonical_attention_pair, changed
):
    case, report = canonical_attention_pair
    original = (case.to_json(), report.to_json())
    documents = list(original)

    class Document(str):
        pass

    if changed.endswith("subclass"):
        index = 0 if changed == "case_subclass" else 1
        documents[index] = Document(documents[index])
    else:
        for index in (
            (0, 1) if changed == "both" else (0,) if changed == "case" else (1,)
        ):
            value = json.loads(documents[index])
            value["unexpected"] = (
                True  # The claimed case/report identities are unchanged.
            )
            documents[index] = json.dumps(value)
    rows = tuple(
        (*pair, "title", 1, None, None, False) for pair in (original, documents)
    )
    repository = Mock()
    repository.read_review_attention.return_value = ReviewAttentionSnapshot(rows, 2)
    with (
        patch(
            "mechanical_librarian.application.review_web._case_and_report",
            wraps=_case_and_report,
        ) as parse,
        pytest.raises(ReviewWebError, match="unavailable") as failure,
    ):
        BrowseReviewLibrary(repository).attention(search=None, limit=1, cursor=None)
    assert parse.call_count == 2  # Even the lookahead row must validate.
    assert isinstance(failure.value.__cause__, ReviewWebError)
    refusal = failure.value.__cause__.__cause__
    assert type(refusal) is ReviewBrowseValueError
    assert (
        str(refusal)
        == {
            "case": "review browse case is invalid",
            "report": "review browse report is invalid",
            "both": "review browse case is invalid",
            "case_subclass": "review browse document is invalid",
            "report_subclass": "review browse document is invalid",
        }[changed]
    )


def test_attention_revalidates_each_raw_page_and_fresh_request(
    canonical_attention_pair,
):
    case, report = canonical_attention_pair
    documents = (case.to_json(), report.to_json())
    rows = tuple(
        (*documents, destination, 1, None, None, False)
        for destination in ("authors", "authors", "title")
    )
    repository = Mock()
    repository.read_review_attention.side_effect = (
        lambda search, limit, offset, labels: ReviewAttentionSnapshot(
            rows[offset : offset + limit], len(rows)
        )
    )
    browse = BrowseReviewLibrary(repository)
    with patch(
        "mechanical_librarian.application.review_web._case_and_report",
        wraps=_case_and_report,
    ) as parse:
        first = browse.attention(search=None, limit=1, cursor=None)
        assert parse.call_count == 2
        second = browse.attention(search=None, limit=1, cursor=None)
        assert parse.call_count == 4
    assert first == second
    assert [item["destination"] for item in first["items"]] == ["title"]
    assert first["total"] == 3 and first["next_cursor"] is None
    assert [
        call.args[2] for call in repository.read_review_attention.call_args_list
    ] == [0, 2, 0, 2]


def test_library_item_projects_proposals_without_the_canonical_case(
    canonical_attention_pair,
):
    case, report = canonical_attention_pair
    with (
        patch.object(ReviewCase, "from_json", side_effect=AssertionError),
        patch.object(MetadataReport, "from_json", side_effect=AssertionError),
        patch.object(ProposalSet, "from_json", side_effect=AssertionError),
    ):
        item = _library_item(case.to_json(), report.to_json(), ())
    assert item["title"] == "Title"
    assert item["attention_count"] == 1
    assert item["attention_state"] == "needs_attention"
    assert item["detail_url"] == f"/review/{case.review_case_id}"
    dispositions = item["dispositions"]
    assert type(dispositions) is list
    assert "unresolved_choice" in dispositions


def test_attention_nonconsecutive_pair_is_parsed_again(canonical_attention_pair):
    case, report = canonical_attention_pair
    other_report = _report(title="Other")
    other_case = ReviewCase.create(evaluate_report(other_report), other_report)
    rows = tuple(
        (
            selected_case.to_json(),
            selected_report.to_json(),
            "title",
            1,
            None,
            None,
            False,
        )
        for selected_case, selected_report in (
            (case, report),
            (other_case, other_report),
            (case, report),
        )
    )
    repository = Mock()
    repository.read_review_attention.return_value = ReviewAttentionSnapshot(rows, 3)
    with patch(
        "mechanical_librarian.application.review_web._case_and_report",
        wraps=_case_and_report,
    ) as parse:
        page = BrowseReviewLibrary(repository).attention(
            search=None, limit=10, cursor=None
        )
    assert parse.call_count == 3
    assert [item["title"] for item in page["items"]] == ["Title", "Other", "Title"]


def _throttle_row(
    connection: str,
    adapter: str,
    scope: str,
    state: str,
    reason: str | None,
    deferred: int,
    next_eligible_at: int | None = None,
) -> dict[str, object]:
    return {
        "connection": connection,
        "adapter": adapter,
        "scope": scope,
        "state": state,
        "next_eligible_at": next_eligible_at,
        "reason": reason,
        "deferred": deferred,
    }


@pytest.mark.parametrize(
    ("rows", "providers", "entries"),
    (
        pytest.param(
            (
                _throttle_row(
                    "google-a",
                    "google_books",
                    "books",
                    "waiting",
                    "burst_throttled",
                    3,
                    90,
                ),
                _throttle_row(
                    "google-b",
                    "google_books",
                    "books",
                    "waiting",
                    "quota_exhausted",
                    4,
                    70,
                ),
            ),
            {"google-a": False, "google-b": False},
            [("books", "google_books", "waiting", 7, 70, "add_api_key")],
            id="two-keyless-google-instances-summed",
        ),
        pytest.param(
            (
                _throttle_row(
                    "google",
                    "google_books",
                    "books",
                    "waiting",
                    "burst_throttled",
                    1,
                    9,
                ),
                _throttle_row(
                    "google", "google_books", "audio", "dormant", "authentication", 2
                ),
            ),
            {"google": False},
            [
                ("audio", "google_books", "dormant", 2, None, "check_api_key"),
                ("books", "google_books", "waiting", 1, 9, "add_api_key"),
            ],
            id="split-by-library",
        ),
        pytest.param(
            (
                _throttle_row(
                    "google-a", "google_books", "books", "exhausted", None, 1
                ),
                _throttle_row("google-b", "google_books", "books", "dormant", None, 1),
                _throttle_row(
                    "google-c", "google_books", "books", "waiting", None, 1, 5
                ),
                _throttle_row("google-d", "google_books", "books", "fanout", None, 1),
            ),
            {},
            [("books", "google_books", "checking", 4, 5, None)],
            id="checking-precedes-waiting-dormant-exhausted",
        ),
        pytest.param(
            (
                _throttle_row(
                    "google-a", "google_books", "books", "exhausted", None, 1
                ),
                _throttle_row("google-b", "google_books", "books", "dormant", None, 1),
            ),
            {},
            [("books", "google_books", "dormant", 2, None, None)],
            id="dormant-precedes-exhausted",
        ),
        pytest.param(
            (
                _throttle_row(
                    "google", "google_books", "books", "waiting", "rate_limited", 0, 4
                ),
            ),
            {"google": True},
            [("books", "google_books", "waiting", 0, 4, None)],
            id="google-with-a-key-has-no-remedy",
        ),
        pytest.param(
            (
                _throttle_row(
                    "google", "google_books", "books", "waiting", "rate_limited", 2, 4
                ),
            ),
            {},
            [("books", "google_books", "waiting", 2, 4, None)],
            id="unknown-instance-has-no-remedy",
        ),
        pytest.param(
            (
                _throttle_row(
                    "hc", "hardcover", "books", "waiting", "burst_throttled", 2, 4
                ),
            ),
            {"hc": False},
            [("books", "hardcover", "waiting", 2, 4, None)],
            id="throttled-hardcover-has-no-remedy",
        ),
        pytest.param(
            (_throttle_row("hc", "hardcover", "books", "dormant", "authorization", 2),),
            {"hc": True},
            [("books", "hardcover", "dormant", 2, None, "check_api_key")],
            id="hardcover-key-check",
        ),
        pytest.param(
            (
                _throttle_row(
                    "ol", "open_library", "books", "waiting", "transient", 0, 4
                ),
                _throttle_row(
                    "ol", "open_library", "audio", "dormant", "authentication", 0
                ),
            ),
            {"ol": False},
            [],
            id="nothing-deferred-and-not-throttled-is-dropped",
        ),
    ),
)
def test_provider_throttling_view_totals_each_library_and_source(
    rows: tuple[dict[str, object], ...],
    providers: dict[str, bool],
    entries: list[tuple[object, ...]],
) -> None:
    from mechanical_librarian.application.review_application import (
        provider_throttling_view,
    )

    view = provider_throttling_view(rows, providers, 4, "available")

    assert (4, "available") == (view["generation"], view["retry"])
    assert entries == [
        (
            entry["library"],
            entry["adapter"],
            entry["state"],
            entry["deferred"],
            entry["next_retry_at"],
            entry["remedy"],
        )
        for entry in cast("list[dict[str, object]]", view["entries"])
    ]
    assert all(
        set(entry)
        == {
            "library",
            "adapter",
            "state",
            "reasons",
            "deferred",
            "next_retry_at",
            "remedy",
        }
        for entry in cast("list[dict[str, object]]", view["entries"])
    )


def test_preparation_read_carries_value_free_provider_throttling(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    from mechanical_librarian.application.review_application import (
        provider_throttling_view,
    )

    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
    )
    rows = (
        _throttle_row(
            "private-google-instance",
            "google_books",
            "books",
            "waiting",
            "burst_throttled",
            214,
            90,
        ),
    )
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            review_preparation=FakePreparation(),
            provider_throttling=lambda: provider_throttling_view(
                rows, {"private-google-instance": False}, 3, "available"
            ),
        ),
        base_url="https://review.test",
    )
    web.login()
    response = web.client.get("/api/v1/review-preparation")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "review-preparation-v2"
    assert response.json()["provider_throttling"] == {
        "generation": 3,
        "retry": "available",
        "entries": [
            {
                "library": "books",
                "adapter": "google_books",
                "state": "waiting",
                "reasons": ["burst_throttled"],
                "deferred": 214,
                "next_retry_at": 90,
                "remedy": "add_api_key",
            }
        ],
    }
    assert "private-google-instance" not in response.text


def _retry_client(
    web: ReviewWebContext[_CloseRecordingRepository], settings: object | None
) -> None:
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(web.hash_path),
    )
    web.client = TestClient(
        create_transport_test_app(
            web.repository,
            configuration,
            clock=lambda: web.now,
            settings=cast("Any", settings),
        ),
        base_url="https://review.test",
    )


def _retry_settings_document(generation: int):
    from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

    return OwnerSettingsDocument.model_validate(
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
            "provider_recovery_generation": generation,
        }
    )


def test_provider_recovery_retry_raises_the_generation_exactly_once(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    import sqlite3

    from mechanical_librarian.adapters.persistence.documents import _canonical_json
    from mechanical_librarian.adapters.persistence.owner_settings import (
        OwnerSettingsStore,
    )
    from mechanical_librarian.application.owner_settings import (
        OwnerSettingsOperations,
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
        (_canonical_json(_retry_settings_document(4).model_dump()),),
    )
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES('connection.primary.key','old-secret-1234')"
    )
    connection.commit()
    _retry_client(web, OwnerSettingsOperations(OwnerSettingsStore(connection)))
    route = "/api/v1/provider-recovery/retry"
    body = {"expected_generation": 4}
    assert web.client.post(route, json=body, headers=web.origin).status_code == 401
    session = web.login()
    csrf = {**web.origin, "x-csrf-token": session["csrf_token"]}
    assert web.client.post(route, json=body, headers=web.origin).status_code == 422
    assert (
        web.client.post(
            route, json=body, headers={**web.origin, "x-csrf-token": "bad"}
        ).status_code
        == 403
    )
    for invalid in (
        {"expected_generation": True},
        {"expected_generation": 0},
        {"expected_generation": "4"},
        {"expected_generation": 4, "generation": 5},
        {},
    ):
        assert web.client.post(route, json=invalid, headers=csrf).status_code == 422
    oversized = web.client.post(
        route,
        content=b"{" + b"x" * 4096,
        headers={**csrf, "content-type": "application/json"},
    )
    assert oversized.status_code == 413
    assert OwnerSettingsStore(connection).read().document.revision == 1

    raised = web.client.post(route, json=body, headers=csrf)
    assert raised.status_code == 200, raised.text
    assert raised.json()["document"]["provider_recovery_generation"] == 5
    assert raised.json()["document"]["revision"] == 2
    assert "old-secret" not in raised.text
    repeated = web.client.post(route, json=body, headers=csrf)
    assert repeated.status_code == 409
    assert repeated.json()["error"] == {"code": "provider_recovery_stale"}
    stored = OwnerSettingsStore(connection).read().document
    assert (stored.revision, stored.provider_recovery_generation) == (2, 5)
    # Nothing but the generation and its revision moved.
    assert stored.model_dump() == _retry_settings_document(5).model_dump() | {
        "revision": 2
    }
    connection.close()
    unavailable = web.client.post(route, json={"expected_generation": 5}, headers=csrf)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == {"code": "settings_unavailable"}


@pytest.mark.parametrize("expected", (3, 4), ids=("current", "mismatched"))
def test_provider_recovery_retry_refuses_file_authority_first(
    web: ReviewWebContext[_CloseRecordingRepository], expected: int
) -> None:
    from mechanical_librarian.application.owner_settings import (
        FileOwnerSettingsRepository,
        OwnerSettingsOperations,
    )

    _retry_client(
        web,
        OwnerSettingsOperations(
            FileOwnerSettingsRepository(_retry_settings_document(3))
        ),
    )
    session = web.login()
    locked = web.client.post(
        "/api/v1/provider-recovery/retry",
        json={"expected_generation": expected},
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert locked.status_code == 409
    assert locked.json()["error"] == {
        "code": "settings_managed_by_file",
        "managed_by_file": "/data/config.json",
    }


def test_provider_recovery_retry_without_settings_is_unavailable(
    web: ReviewWebContext[_CloseRecordingRepository],
) -> None:
    _retry_client(web, None)
    session = web.login()
    response = web.client.post(
        "/api/v1/provider-recovery/retry",
        json={"expected_generation": 1},
        headers={**web.origin, "x-csrf-token": session["csrf_token"]},
    )
    assert response.status_code == 503
    assert response.json()["error"] == {"code": "settings_unavailable"}
