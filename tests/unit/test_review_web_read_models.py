"""Review web read models: preparation and activity controls, policy
and shadow surfaces, cover omission, evidence and choice
projections, and the Library and Attention lenses.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
)
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    ReviewWebError,
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
    _library_item,
    _provider_comparison_notices,
    _provider_label,
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
    ReviewWebTestCase,
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


class ReviewWebReadModelTests(ReviewWebTestCase):
    def test_cover_omission_response_models_every_valid_shape(self) -> None:
        duplicate = _CoverOmission.model_validate({
            "preview_sha256": "a" * 64,
            "reason": "duplicate_preview",
            "reference": "cover_ref_" + "b" * 64,
            "retained_reference": "cover_ref_" + "c" * 64,
        })
        bounded = _CoverOmission.model_validate({
            "preview_bytes": 1_048_577,
            "reason": "preview_byte_limit",
            "reference": "cover_ref_" + "d" * 64,
        })
        unavailable = _CoverOmission.model_validate({
            "reason": "source_unavailable",
            "reference": "cover_ref_" + "e" * 64,
        })

        self.assertEqual(duplicate.preview_sha256, "a" * 64)
        self.assertEqual(
            duplicate.retained_reference, "cover_ref_" + "c" * 64
        )
        self.assertEqual(bounded.preview_bytes, 1_048_577)
        self.assertIsNone(unavailable.preview_bytes)

    def test_cover_transport_retry_and_owner_outcome_are_protected_end_to_end(self) -> None:
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
                    "height": 1200,
                    "identity": {
                        "candidate_reference": candidate_reference,
                        "evidence_references": [],
                        "outcome": "verified_work_match",
                        "preview_reference": "cover_preview_" + "d" * 64,
                        "reasons": [],
                    },
                    "incumbent": True,
                    "observation": None,
                    "original_sha256": "e" * 64,
                    "ordinal": 0,
                    "preview_sha256": "f" * 64,
                    "reference": candidate_reference,
                    "selectable": True,
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
                    "evidence": [{"observation": "Local library cover retained.", "role": "local_presence", "source": "Local library record"}],
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
            "reviewer_status": "unavailable",
            "schema_version": "cover-consideration-v1",
            "supersedes_id": None,
            "target_fingerprint": case.target_fingerprint,
        }
        covers = Mock()
        covers.project.return_value = view
        covers.ensure.return_value = view
        covers.image.return_value = b"\x89PNG\r\n\x1a\nsynthetic"
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        self.client.close()
        self.client = TestClient(
            create_transport_test_app(
                repository,
                configuration,
                clock=lambda: self.now,
                cover_considerations=covers,
            ),
            base_url="https://review.test",
        )
        image_path = (
            f"/api/v1/review-cases/{case.review_case_id}"
            f"/cover-considerations/{consideration_id}/images/{candidate_reference}"
        )
        self.assertEqual(self.client.get(image_path).status_code, 401)
        login = self._login()
        image = self.client.get(image_path)
        self.assertEqual(200, image.status_code)
        self.assertEqual("image/png", image.headers["content-type"])
        self.assertEqual(
            {"no-store", "private"},
            {value.strip() for value in image.headers["cache-control"].split(",")},
        )
        self.assertEqual("nosniff", image.headers["x-content-type-options"])
        retry_path = (
            f"/api/v1/review-cases/{case.review_case_id}"
            "/cover-considerations/retry"
        )
        self.assertEqual(self.client.post(retry_path, headers=self.origin).status_code, 422)
        denied = self.client.post(
            retry_path,
            headers={
                "Origin": "https://hostile.test",
                "X-CSRF-Token": str(login["csrf_token"]),
            },
        )
        self.assertEqual(403, denied.status_code)
        retried = self.client.post(
            retry_path,
            headers={
                **self.origin,
                "X-CSRF-Token": str(login["csrf_token"]),
            },
        )
        self.assertEqual(200, retried.status_code, retried.text)
        detail = self.client.get(
            f"/api/v1/review-cases/{case.review_case_id}"
        ).json()
        cover = next(field for field in detail["fields"] if field["destination"] == "cover")
        decision = self.client.post(
            f"/api/v1/review-cases/{case.review_case_id}/decisions",
            headers={
                **self.origin,
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
        self.assertEqual(200, decision.status_code, decision.text)
        covers.validate_decision.assert_called_once()

    def test_shutdown_timeout_preserves_shared_repository_for_live_worker(self) -> None:
        closed = []

        class _DrainingApplication:
            started = False

            def attach_timing(self, timing) -> None:
                return None

            def start(self) -> None:
                self.started = True

            def begin_drain(self) -> None:
                raise GuardedApplicationError("shutdown_timeout")

        application = _DrainingApplication()
        self.repository.close = lambda: closed.append(True)
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        app = create_transport_test_app(
            self.repository, configuration, close_repository=True,
            field_application=application,
        )
        with self.assertRaisesRegex(GuardedApplicationError, "shutdown_timeout"):
            with TestClient(app, base_url="https://review.test"):
                pass
        self.assertTrue(application.started)
        self.assertEqual(closed, [])

    def test_shutdown_closes_both_admissions_before_one_shared_deadline(self) -> None:
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
            graceful_shutdown_seconds=1,
        )
        for failing_worker in ("field", "preparation"):
            with self.subTest(failing_worker=failing_worker):
                calls = []
                closed = []

                class _FieldApplication(FakeActivityApplication):
                    def start(self):
                        calls.append("field-start")  # noqa: B023 - closure is created and consumed within this iteration
                        return (0, 0, 0)

                    def close_admission(self) -> None:
                        calls.append("field-close")  # noqa: B023 - closure is created and consumed within this iteration

                    def wait_for_drain(self, deadline=None) -> None:
                        calls.append(("field-wait", deadline))  # noqa: B023 - closure is created and consumed within this iteration
                        if failing_worker == "field":  # noqa: B023 - closure is created and consumed within this iteration
                            raise GuardedApplicationError("shutdown_timeout")

                class FakePreparation:
                    def start(self) -> None:
                        calls.append("preparation-start")  # noqa: B023 - closure is created and consumed within this iteration

                    def begin_drain(self) -> None:
                        calls.append("preparation-close")  # noqa: B023 - closure is created and consumed within this iteration

                    def stop(self, deadline=None) -> None:
                        calls.append(("preparation-wait", deadline))  # noqa: B023 - closure is created and consumed within this iteration
                        if failing_worker == "preparation":  # noqa: B023 - closure is created and consumed within this iteration
                            raise ReviewPipelineError("shutdown_timeout")

                self.repository.close = lambda: closed.append(True)  # noqa: B023 - closure is created and consumed within this iteration
                app = create_transport_test_app(
                    self.repository, configuration, close_repository=True,
                    field_application=_FieldApplication(),
                    review_preparation=FakePreparation(),
                )
                with self.assertRaisesRegex(Exception, "shutdown_timeout"):
                    with TestClient(app, base_url="https://review.test"):
                        pass
                field_close = calls.index("field-close")
                preparation_close = calls.index("preparation-close")
                field_wait = next(
                    index for index, value in enumerate(calls)
                    if isinstance(value, tuple) and value[0] == "field-wait"
                )
                preparation_wait = next(
                    index for index, value in enumerate(calls)
                    if isinstance(value, tuple) and value[0] == "preparation-wait"
                )
                self.assertLess(field_close, field_wait)
                self.assertLess(preparation_close, field_wait)
                self.assertLess(field_close, preparation_wait)
                self.assertLess(preparation_close, preparation_wait)
                deadlines = [
                    value[1] for value in calls
                    if isinstance(value, tuple) and value[0].endswith("wait")
                ]
                self.assertEqual(len(deadlines), 2)
                self.assertEqual(deadlines[0], deadlines[1])
                self.assertEqual(closed, [])

    def test_activity_is_permanent_and_consent_is_csrf_bound(self) -> None:
        application = FakeActivityApplication()
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        self.client.close()
        self.client = TestClient(
            create_transport_test_app(
                self.repository, configuration, clock=lambda: self.now,
                field_application=application,
            ),
            base_url="https://review.test",
        )
        login = self._login()
        page = self.client.get("/activity")
        self.assertEqual(page.status_code, 200)
        self.assertIn(
            f'data-build-identity="{TEST_REACT_BUILD_IDENTITY}"', page.text
        )
        script = self.client.get(
            "/assets/review-app/assets/index-deadbeef.js"
        )
        self.assertEqual(script.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/activity").json()["prepared"]["total"], 2)
        filtered = self.client.get(
            "/api/v1/activity?event_type=all&range_days=30&page=1&page_size=25"
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["ledger"], {
            "event_type": "all", "page": 1, "page_count": 1, "page_size": 25,
            "query": None, "range_days": 30, "total": 1,
        })
        self.assertEqual(
            self.client.get("/api/v1/activity?range_days=8").status_code, 422
        )
        denied = self.client.post(
            "/api/v1/activity/consent",
            headers={**self.origin, "X-CSRF-Token": "invalid"},
            json={"grants": [{"library": "fiction", "destinations": ["title"]}]},
        )
        self.assertEqual(denied.status_code, 403)
        login = self._login()
        enabled = self.client.post(
            "/api/v1/activity/consent",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
            json={"grants": [{"library": "fiction", "destinations": ["title"]}]},
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertEqual(enabled.json()["enabled_libraries"][0]["library"], "fiction")
        later = self.client.post(
            "/api/v1/activity/consent",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
            json={"grants": [{"library": "history", "destinations": ["title"]}]},
        )
        self.assertEqual(later.status_code, 200)
        self.assertEqual(
            {item["library"] for item in later.json()["enabled_libraries"]},
            {"fiction", "history"},
        )
        revoked = self.client.post(
            "/api/v1/activity/primary/fiction/revoke",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(
            [item["library"] for item in revoked.json()["enabled_libraries"]],
            ["history"],
        )

    def test_review_preparation_is_authenticated_csrf_bound_and_bounded(self) -> None:
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        preparation = FakePreparation()
        self.client = TestClient(
            create_transport_test_app(
                self.repository,
                configuration,
                clock=lambda: self.now,
                review_preparation=preparation,
            ),
            base_url="https://review.test",
        )
        anonymous = self.client.get("/api/v1/review-preparation")
        self.assertEqual(anonymous.status_code, 401)
        session = self._login()
        status = self.client.get("/api/v1/review-preparation")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(preparation.status_calls, 1)
        self.assertTrue(status.json()["active"])
        self.assertFalse(status.json()["paused"])
        self.assertEqual(
            status.json()["libraries"][0],
            {
                "blocked": 0,
                "checked": 2,
                "connection": "primary",
                "library": "fiction",
                "library_id": "lib-1",
                "ready": 1,
                "rescan_requested": False,
                "scan_id": "review_scan_" + "a" * 64,
                "state": "processing",
                "total": 5,
            },
        )
        check_missing_csrf = self.client.post(
            "/api/v1/review-preparation/primary/check-connection",
            headers=self.origin,
        )
        self.assertEqual(check_missing_csrf.status_code, 422)
        checked = self.client.post(
            "/api/v1/review-preparation/primary/check-connection",
            headers={**self.origin, "x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json()["connections"], [])
        missing_csrf = self.client.post(
            "/api/v1/review-preparation/primary/fiction/scan",
            headers=self.origin,
        )
        self.assertEqual(missing_csrf.status_code, 422)
        requested = self.client.post(
            "/api/v1/review-preparation/primary/fiction/scan",
            headers={**self.origin, "x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(requested.status_code, 202)
        self.assertEqual(requested.json()["state"], "processing")
        paused = self.client.post(
            "/api/v1/review-preparation/pause",
            headers={**self.origin, "x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.json()["paused"])
        resumed = self.client.post(
            "/api/v1/review-preparation/resume",
            headers={**self.origin, "x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertFalse(resumed.json()["paused"])
        with patch.object(FakePreparation, "set_paused", side_effect=RuntimeError):
            failed = self.client.post(
                "/api/v1/review-preparation/pause",
                headers={**self.origin, "x-csrf-token": session["csrf_token"]},
            )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["error"]["code"], "persistence")

    def test_unconfigured_review_preparation_controls_remain_unavailable(self) -> None:
        session = self._login()
        headers = {**self.origin, "x-csrf-token": session["csrf_token"]}
        for path in (
            "/api/v1/review-preparation/primary/fiction/scan",
            "/api/v1/review-preparation/primary/fiction/retry",
            "/api/v1/review-preparation/pause",
            "/api/v1/review-preparation/resume",
        ):
            with self.subTest(path=path):
                response = self.client.post(path, headers=headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["error"]["code"], "unavailable")

    def test_review_preparation_control_runtime_failures_remain_persistence(self) -> None:
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        preparation = FakePreparation()
        self.client = TestClient(
            create_transport_test_app(
                self.repository,
                configuration,
                clock=lambda: self.now,
                review_preparation=preparation,
            ),
            base_url="https://review.test",
        )
        session = self._login()
        headers = {**self.origin, "x-csrf-token": session["csrf_token"]}
        controls = (
            ("/api/v1/review-preparation/primary/fiction/scan", "request"),
            ("/api/v1/review-preparation/primary/fiction/retry", "retry"),
            ("/api/v1/review-preparation/pause", "set_paused"),
            ("/api/v1/review-preparation/resume", "set_paused"),
        )
        for path, method in controls:
            with self.subTest(path=path), patch.object(
                preparation, method, side_effect=RuntimeError
            ):
                response = self.client.post(path, headers=headers)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "persistence")

    def test_policy_mutations_return_specific_bounded_problems_and_fail_closed(self) -> None:
        anonymous = self.client.post("/api/v1/metadata-policy/validate", headers=self.origin, json={})
        self.assertEqual(401, anonymous.status_code)
        login = self._login()
        headers = {**self.origin, "X-CSRF-Token": login["csrf_token"]}
        active = self.client.get("/api/v1/metadata-policy/state")
        payload = active.json()["profile"]
        del payload["schema_version"]
        payload["name"] = "Draft"
        payload["options"]["removed"] = "unknown"
        invalid = self.client.post("/api/v1/metadata-policy/validate", headers=headers, json=payload)
        self.assertEqual(422, invalid.status_code)
        self.assertEqual("policy_option_unavailable", invalid.json()["error"]["code"])
        self.assertEqual("review-error-v1", invalid.json()["schema_version"])
        self.assertEqual("no-store, private", invalid.headers["cache-control"])
        valid_payload = active.json()["profile"]
        del valid_payload["schema_version"]
        denied = self.client.post("/api/v1/metadata-policy/preview", headers=self.origin, json=valid_payload)
        self.assertEqual(403, denied.status_code)
        self.assertEqual("csrf", denied.json()["error"]["code"])

    def test_policy_routes_are_closed_read_only_and_schema_deterministic(self) -> None:
        login = self._login()
        headers = {**self.origin, "X-CSRF-Token": login["csrf_token"]}
        active = self.client.get("/api/v1/metadata-policy/state")
        self.assertEqual(200, active.status_code)
        payload = active.json()["profile"]
        del payload["schema_version"]
        payload["name"] = "<hostile draft>"
        payload["options"]["date_precision"] = "full_date"
        before = (dict(self.repository.decisions), dict(self.repository.policy_documents))
        validated = self.client.post("/api/v1/metadata-policy/validate", headers=headers, json=payload)
        self.assertEqual(200, validated.status_code)
        self.assertEqual("metadata-policy-notebook-v1", validated.json()["schema_version"])
        created = self.client.post("/api/v1/metadata-policy/drafts", headers=headers, json=payload)
        self.assertEqual(201, created.status_code)
        policy_id = created.json()["policy_id"]
        read = self.client.get(f"/api/v1/metadata-policy/drafts/{policy_id}")
        self.assertEqual(validated.json(), read.json())
        preview = self.client.post("/api/v1/metadata-policy/preview", headers=headers, json=payload)
        self.assertEqual(200, preview.status_code)
        self.assertEqual("metadata-policy-preview-v1", preview.json()["schema_version"])
        denied_activation = self.client.post(
            "/api/v1/metadata-policy/activate",
            headers=self.origin,
            json={"policy_id": policy_id, "expected_active_policy_id": active.json()["policy_id"]},
        )
        self.assertEqual(403, denied_activation.status_code)
        activated = self.client.post(
            "/api/v1/metadata-policy/activate",
            headers=headers,
            json={"policy_id": policy_id, "expected_active_policy_id": active.json()["policy_id"]},
        )
        self.assertEqual(200, activated.status_code)
        self.assertEqual("metadata-policy-activation-v1", activated.json()["schema_version"])
        self.assertEqual(policy_id, self.client.get("/api/v1/metadata-policy/state").json()["policy_id"])
        transition = self.client.get("/api/v1/metadata-policy/transition")
        self.assertEqual("metadata-policy-transition-v1", transition.json()["schema_version"])
        dismissed = self.client.post(
            f"/api/v1/metadata-policy/transitions/{transition.json()['transition_id']}/acknowledge",
            headers=headers,
        )
        self.assertTrue(dismissed.json()["acknowledged"])
        self.assertEqual(before[0], self.repository.decisions)
        self.assertEqual(1, len(self.repository.policy_documents))
        self.assertEqual([], self.repository.policy_count_calls)

    def test_shadow_posture_and_sample_are_authenticated_non_authoritative(self) -> None:
        anonymous = self.client.get("/api/v1/metadata-policy/shadow")
        self.assertEqual(401, anonymous.status_code)
        login = self._login()
        headers = {**self.origin, "X-CSRF-Token": login["csrf_token"]}
        state = self.client.get("/api/v1/metadata-policy/shadow")
        self.assertEqual(
            {
                "authority": "none",
                "can_leave_preview": False,
                "posture": "review",
                "schema_version": "shadow-posture-v1",
            },
            state.json(),
        )
        active = self.client.get("/api/v1/metadata-policy/state").json()
        automatic_payload = active["profile"]
        del automatic_payload["schema_version"]
        automatic_payload["name"] = "Preview automatic intent"
        automatic_payload["destinations"]["title_layout"] = "automatic"
        validated_before_preview = self.client.post(
            "/api/v1/metadata-policy/validate", headers=headers, json=automatic_payload
        )
        self.assertEqual(200, validated_before_preview.status_code)
        denied = self.client.post(
            "/api/v1/metadata-policy/shadow/enter", headers=self.origin
        )
        self.assertEqual(403, denied.status_code)
        entered = self.client.post(
            "/api/v1/metadata-policy/shadow/enter", headers=headers
        )
        self.assertEqual("preview", entered.json()["posture"])
        self.assertEqual("none", entered.json()["authority"])
        validated = self.client.post(
            "/api/v1/metadata-policy/validate",
            headers=headers,
            json=automatic_payload,
        )
        self.assertEqual(200, validated.status_code, validated.text)
        created = self.client.post(
            "/api/v1/metadata-policy/drafts",
            headers=headers,
            json=automatic_payload,
        )
        self.assertEqual(201, created.status_code, created.text)
        policy_id = created.json()["policy_id"]
        previewed = self.client.post(
            "/api/v1/metadata-policy/preview",
            headers=headers,
            json=automatic_payload,
        )
        self.assertEqual(200, previewed.status_code, previewed.text)
        activated = self.client.post(
            "/api/v1/metadata-policy/activate",
            headers=headers,
            json={
                "policy_id": policy_id,
                "expected_active_policy_id": active["policy_id"],
            },
        )
        self.assertEqual(200, activated.status_code, activated.text)
        self.assertEqual(
            "automatic",
            self.client.get("/api/v1/metadata-policy/state")
            .json()["profile"]["destinations"]["title_layout"],
        )
        sample = self.client.get(
            "/api/v1/metadata-policy/shadow/outcomes?limit=10"
        )
        self.assertEqual("shadow-outcome-sample-v1", sample.json()["schema_version"])
        self.assertEqual([], sample.json()["items"])
        leaving = self.client.get(
            "/api/v1/metadata-policy/shadow/leave-preview"
        )
        self.assertTrue(leaving.json()["confirmation_required"])
        self.assertFalse(leaving.json()["can_confirm"])
        self.assertEqual("preview", self.repository.shadow_mode)
        self.assertEqual(
            "qualified_decision_only_unavailable",
            leaving.json()["requested_posture"],
        )

    def test_policy_draft_persistence_failure_is_unavailable_not_validation(self) -> None:
        login = self._login()
        headers = {**self.origin, "X-CSRF-Token": login["csrf_token"]}
        payload = self.client.get("/api/v1/metadata-policy/state").json()["profile"]
        del payload["schema_version"]
        # A deliberate patch of the fake repository's bound method.
        self.repository.create_metadata_policy_draft = Mock(side_effect=RuntimeError)  # type: ignore[method-assign]  # reason: deliberate per-test monkeypatch of a bound method
        response = self.client.post("/api/v1/metadata-policy/drafts", headers=headers, json=payload)
        self.assertEqual(503, response.status_code)
        self.assertEqual("persistence", response.json()["error"]["code"])

    def test_policy_activation_body_is_bounded_before_validation(self) -> None:
        login = self._login()
        headers = {
            **self.origin,
            "Content-Type": "application/json",
            "X-CSRF-Token": login["csrf_token"],
        }
        oversized = b'{"policy_id":"' + b"x" * 16_384 + b'"}'
        before = self.repository.active_policy_id
        declared = self.client.post(
            "/api/v1/metadata-policy/activate",
            headers=headers,
            content=oversized,
        )
        streamed = self.client.post(
            "/api/v1/metadata-policy/activate",
            headers=headers,
            content=(chunk for chunk in (oversized[:8_000], oversized[8_000:])),
        )
        self.assertEqual((413, 413), (declared.status_code, streamed.status_code))
        self.assertNotIn(b"x" * 128, declared.content)
        self.assertNotIn(b"x" * 128, streamed.content)
        self.assertEqual(before, self.repository.active_policy_id)

    def test_automatic_detail_uses_a_closed_projection_without_human_commit(self) -> None:
        # Durable automatic documents carry authority bindings that must never
        # cross the authenticated case read model.
        self._login()
        self.repository.decisions["title"] = json.dumps(
            {
                "origin": "automatic_policy",
                "schema_version": "automatic-decision-v1",
                "decision_id": "automatic_decision_" + "a" * 64,
                "review_case_id": self.case.review_case_id,
                "proposal_id": self.case.proposals[0]["proposal_id"],
                "destination": "title",
                "outcome": "candidate",
                "candidate_id": self.case.proposals[0]["result"]["alternatives"][0]["alternative_id"],
                "occurred_at": 7,
                "target_fingerprint": self.case.target_fingerprint,
                "report_id": "report_" + "b" * 64,
                "proposal_set_id": "proposal_set_" + "c" * 64,
                "policy_category": "title_layout",
                "policy_option": "preserve",
                "qualification_manifest_sha256": "d" * 64,
                "idempotency_digest": "e" * 64,
            }, separators=(",", ":"), sort_keys=True,
        )
        detail = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}")
        self.assertEqual(200, detail.status_code)
        field = next(value for value in detail.json()["fields"] if value["destination"] == "title")
        self.assertIsNone(field["human_decision"])
        self.assertFalse(field["needs_attention"])
        self.assertEqual(
            {
                "candidate_id", "decision_id", "destination", "occurred_at", "outcome",
                "proposal_id", "review_case_id", "target_fingerprint",
                "selected_value",
            },
            set(field["automatic_decision"]),
        )
        self.assertNotIn("actor", field["automatic_decision"])
        self.assertEqual("candidate", field["automatic_decision"]["outcome"])
        self.assertEqual(self.case.proposals[0]["result"]["alternatives"][0]["alternative_id"], field["automatic_decision"]["candidate_id"])
        self.assertEqual(self.case.proposals[0]["result"]["alternatives"][0]["value"], field["automatic_decision"]["selected_value"])
        recommended = [
            context for context in field["choice_contexts"]
            if context["recommendation"] == "recommended"
        ]
        self.assertEqual(1, len(recommended))
        self.assertEqual(
            self.case.proposals[0]["result"]["alternatives"][0]["alternative_id"],
            recommended[0]["alternative_id"],
        )
        self.assertNotIn("qualification_manifest_sha256", field["automatic_decision"])
        ignored = json.loads(self.repository.decisions["title"])
        ignored["outcome"] = "ignore"
        ignored["candidate_id"] = None
        self.repository.decisions["title"] = json.dumps(
            ignored, separators=(",", ":"), sort_keys=True
        )
        ignored_detail = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        )
        self.assertEqual(200, ignored_detail.status_code)
        ignored_field = next(
            value for value in ignored_detail.json()["fields"]
            if value["destination"] == "title"
        )
        self.assertTrue(all(
            context["recommendation"] == "no_recommendation"
            for context in ignored_field["choice_contexts"]
        ))
        malformed = json.loads(self.repository.decisions["title"])
        malformed["outcome"] = "candidate"
        malformed["candidate_id"] = "alternative_" + "f" * 64
        self.repository.decisions["title"] = json.dumps(malformed, separators=(",", ":"), sort_keys=True)
        self.assertEqual(404, self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}").status_code)

    def test_choice_contexts_build_the_evidence_index_once(self) -> None:
        proposal = self.case.proposals[0]
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
                self.repository.report,
                {"state": "value", "value": "Title"},
            )

        self.assertEqual(1, indexed.call_count)
        self.assertEqual(len(alternatives) + 1, len(contexts))
        self.assertLess(len(json.dumps(contexts)), 16_384)
        selected_result = {
            **result,
            "selected_alternative_id": result["alternatives"][0]["alternative_id"],
        }
        ignored_contexts = _choice_contexts(
            result["alternatives"],
            selected_result,
            self.repository.report,
            {"state": "value", "value": "Title"},
            {"outcome": "ignore", "candidate_id": None},
        )
        self.assertTrue(all(
            context["recommendation"] == "no_recommendation"
            for context in ignored_contexts
        ))

    def test_automatic_safety_bodies_are_closed_before_control_access(self) -> None:
        login = self._login()
        headers = {**self.origin, "X-CSRF-Token": login["csrf_token"]}
        for path, payload in (
            ("/api/v1/metadata-policy/automatic-safety/pause", {"paused": True, "extra": True}),
            ("/api/v1/metadata-policy/automatic-safety/recover", {"category": None, "extra": True}),
            ("/api/v1/metadata-policy/automatic-safety/suspend", {"reason": "test", "extra": True}),
        ):
            response = self.client.post(path, headers=headers, json=payload)
            self.assertEqual(422, response.status_code)

    def test_complete_evidence_projection_is_page_bounded_and_case_bound(self) -> None:
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
        rich_reference = "evidence_" + hashlib.sha256(
            json.dumps(
                {
                    "candidate": "candidate-1",
                    "evidence": provider_evidence,
                    "provider": "catalogue",
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        provider_claim_reference = "provider_" + "a" * 64
        sibling_claim_reference = "provider_" + "d" * 64
        identifier_reference = "provider_identifier_" + "e" * 64
        cover_reference = "provider_cover_" + "b" * 64
        data = {
            "local": {"claims": local},
            "providers": [
                {
                    "provider": "catalogue",
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
                    "provider": "other_catalogue",
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
                    "provider": "other_catalogue",
                    "reference": identifier_reference,
                }
            ],
            "covers": [
                {
                    "kind": "provider",
                    "provenance": {"provider": "catalogue"},
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
            "fields": [{"provenance": sorted(references)}],
        }
        report = SimpleNamespace(to_data=lambda: data)
        cases = InspectCurrentReviewCase(Mock())
        with patch.object(cases, "_read", return_value=(detail, report, ())):
            first = cases.evidence("review_case_" + "c" * 64, limit=50)
            second = cases.evidence(
                "review_case_" + "c" * 64,
                limit=50,
                cursor=first["next_cursor"],
            )
            with self.assertRaises(ReviewWebError):
                cases.evidence(
                    "review_case_" + "d" * 64,
                    cursor=first["next_cursor"],
                )
            changed_detail: dict[str, Any] = {"fields": [{"provenance": []}]}
            with patch.object(
                cases,
                "_read",
                return_value=(changed_detail, report, ()),
            ):
                with self.assertRaises(ReviewWebError):
                    cases.evidence(
                        "review_case_" + "c" * 64,
                        cursor=first["next_cursor"],
                    )
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

        self.assertEqual(first["next_cursor"], reopened["next_cursor"])
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(len(second["items"]), 10)
        self.assertIsNone(second["next_cursor"])
        items = cast(list[Any], first["items"]) + cast(list[Any], second["items"])
        self.assertEqual({item["reference"] for item in items}, references)
        rich = next(item for item in items if item["reference"] == rich_reference)
        self.assertEqual(rich["provider"], "catalogue")
        self.assertEqual(rich["candidate"], "candidate-1")
        cover = next(item for item in items if item["reference"] == cover_reference)
        self.assertEqual(cover["source"], "cover")
        self.assertEqual(cover["provider"], "catalogue")
        identifier = next(
            item for item in items if item["reference"] == identifier_reference
        )
        self.assertEqual(identifier["source"], "identifier")
        self.assertEqual(identifier["provider"], "other_catalogue")
        self.assertEqual(identifier["candidate"], "candidate-2")
        hostile = next(item for item in items if item["source"] == "local")
        self.assertIn("</pre><script>", hostile["fact"])

    def test_choice_projection_is_bounded_and_never_leaks_unknown_provider_names(
        self,
    ) -> None:
        observation = _choice_observation({
            "fact": json.dumps({
                "kind": "title\x00private",
                "value": "\x00" + "hostile-value" * 40,
            }),
            "provider": "private-connection-name",
            "source": "provider_claim",
        })
        self.assertLessEqual(len(observation), 188)
        self.assertNotIn("\x00", observation)
        self.assertNotIn("private", observation)
        self.assertTrue(observation.startswith("Metadata: "))
        self.assertEqual(
            _choice_source_label({
                "fact": "{}",
                "provider": "private-connection-name",
                "source": "provider_claim",
            }),
            "Provider evidence (name unavailable)",
        )
        replacing = {
            "assessment": "replaces_target",
            "scope": "edition",
            "correlations": ["correlation_" + "a" * 64],
        }
        self.assertEqual(
            _choice_rationale(replacing, True),
            "This alternative replaces the local value using edition-specific evidence with retained supporting evidence.",
        )
        self.assertTrue(_current_value_present("Present title"))
        self.assertTrue(_current_value_present(["Ada Example"]))
        self.assertTrue(_current_value_present("2024-01-01"))
        self.assertFalse(_current_value_present(None))
        self.assertFalse(_current_value_present(""))
        self.assertFalse(_current_value_present([]))

    def test_provider_comparison_notices_keep_only_safe_material_failures(self) -> None:
        source = _report()
        report = MetadataReport.create(
            target=source.target, file=source.file, local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=(
                ProviderObservation("hardcover-main", ProviderStatus.DISABLED, authority="hardcover"),
                ProviderObservation("open-library-main", ProviderStatus.NO_MATCH, authority="open_library"),
                ProviderObservation("private-connection", ProviderStatus.UNAVAILABLE),
            ),
        )
        self.assertEqual(
            _provider_comparison_notices(report),
            [
                "Provider limitation: Open Library — Provider returned no matching metadata.",
                "Provider limitation: Provider (name unavailable) — Provider returned a bounded terminal status.",
            ],
        )
        affected_case = ReviewCase.create(evaluate_report(report), report)
        projected = InspectCurrentReviewCase(
            FakeReviewRepository(affected_case, report)
        ).run(affected_case.review_case_id)
        self.assertEqual(
            _provider_comparison_notices(report),
            projected["comparison_notices"],
        )
        unaffected = InspectCurrentReviewCase(self.repository).run(
            self.case.review_case_id
        )
        self.assertEqual([], unaffected["comparison_notices"])
        self.assertEqual("Google Books", _provider_label("google_books"))
        self.assertEqual("Provider (name unavailable)", _provider_label(None))
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
            self.assertEqual(
                [
                    "Provider limitation: Provider (name unavailable) — Provider returned a bounded terminal status.",
                ],
                _provider_comparison_notices(spoofed),
            )

    def test_choice_limitations_leave_multi_alternative_global_reasons_field_scoped(self) -> None:
        first = {"alternative_id": "alternative-a"}
        result = {"alternatives": [first, {"alternative_id": "alternative-b"}], "reasons": ["competing_evidence"]}
        self.assertEqual([], _choice_limitations(first, result))
        self.assertIn("competing_evidence", result["reasons"])

    def test_choice_limitations_attach_only_single_exact_choice_reason(self) -> None:
        alternative = {"alternative_id": "alternative-a"}
        result = {"alternatives": [alternative], "reasons": ["insufficient_independent_corroboration"]}
        self.assertEqual(["insufficient_independent_corroboration"], _choice_limitations(alternative, result))
        self.assertEqual([], _choice_limitations({"alternative_id": "alternative-b"}, result))

    def test_choice_limitations_do_not_invent_provider_terminal_attribution(self) -> None:
        alternative = {"alternative_id": "alternative-a"}
        result = {"alternatives": [alternative], "reasons": ["provider_no_match", "provider_unavailable"]}
        self.assertEqual([], _choice_limitations(alternative, result))

    def test_attention_first_library_lenses_queue_and_cursor_binding(self) -> None:
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
        self.repository.browse_cases.append(
            (automatic_case, automatic_report)
        )
        self._login()

        attention_first = self.client.get("/api/v1/library?limit=1")
        self.assertEqual(attention_first.status_code, 200, attention_first.text)
        body = attention_first.json()
        self.assertEqual(
            body["counts"],
            {
                "all": 2,
                "needs_attention": 1,
                "no_attention_needed": 1,
            },
        )
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["attention_state"], "needs_attention")
        self.assertIsNone(body["next_cursor"])

        all_first = self.client.get("/api/v1/library?lens=all&limit=1")
        cursor = all_first.json()["next_cursor"]
        self.assertIsNotNone(cursor)
        all_second = self.client.get(
            f"/api/v1/library?lens=all&limit=1&cursor={cursor}"
        )
        self.assertEqual(all_second.status_code, 200)
        self.assertEqual(
            all_second.json()["items"][0]["attention_state"],
            "no_attention_needed",
        )
        rebound = self.client.get(
            f"/api/v1/library?lens=all&q=other&limit=1&cursor={cursor}"
        )
        self.assertEqual(rebound.status_code, 400)

        no_attention = self.client.get(
            "/api/v1/library?lens=no_attention_needed"
        )
        self.assertEqual(len(no_attention.json()["items"]), 1)
        self.assertEqual(
            no_attention.json()["items"][0]["review_case_id"],
            automatic_case.review_case_id,
        )
        queue = self.client.get("/api/v1/attention")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(queue.json()["total"], 1)
        self.assertEqual(len(queue.json()["items"]), 1)
        self.assertEqual(queue.json()["items"][0]["destination"], "title")
        self.assertEqual(queue.json()["items"][0]["work_attention_count"], 1)
        self.assertIn("?field=title", queue.json()["items"][0]["detail_url"])
        self.assertNotIn(
            automatic_case.review_case_id,
            json.dumps(queue.json()),
        )

        self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.client.get("/attention").status_code, 200)

    def test_complete_library_includes_current_report_without_review_lineage(self) -> None:
        current = _report(title="In-progress report", actionable=False)
        self.repository.browse_without_cases.append(current)
        self._login()

        response = self.client.get(
            "/api/v1/library?lens=all&q=In-progress"
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["counts"], {
            "all": 1,
            "needs_attention": 0,
            "no_attention_needed": 1,
        })
        item = response.json()["items"][0]
        self.assertIsNone(item["review_case_id"])
        self.assertIsNone(item["detail_url"])
        self.assertTrue(item["work_id"].startswith("library_work_"))
        self.assertEqual(item["attention_state"], "no_attention_needed")
        self.assertEqual(item["dispositions"], [])

    def test_row_projection_refuses_every_document_the_parsers_cannot_read(self) -> None:
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
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(surrogate)

        for document in ("{not a report}", surrogate):
            with self.assertRaises(ReviewWebError) as raised:
                _library_item(None, document, ())
            self.assertEqual(raised.exception.code, "unavailable")
            with self.assertRaises(ReviewWebError) as raised:
                _case_and_report(case.to_json(), document)
            self.assertEqual(raised.exception.code, "unavailable")

        with self.assertRaises(ReviewWebError) as raised:
            _case_and_report(case.to_json(), report.to_json()[:-1])
        self.assertEqual(raised.exception.code, "unavailable")

        # Both handlers let a fault in the projection itself past. `_library_item`
        # caught every exception before the guard moved, so this is the
        # suppression the narrowing removes.
        fault = RuntimeError("projection is broken")
        with patch.object(MetadataReport, "from_json", side_effect=fault):
            with self.assertRaises(RuntimeError) as surfaced:
                _case_and_report(case.to_json(), report.to_json())
            self.assertIs(surfaced.exception, fault)
            with self.assertRaises(RuntimeError) as surfaced:
                _library_item(case.to_json(), report.to_json(), ())
            self.assertIs(surfaced.exception, fault)

    def test_attention_cursor_keeps_the_complete_work_field_count(self) -> None:
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
        self.repository.browse_cases = [(case, report)]
        self._login()

        first = self.client.get("/api/v1/attention?limit=1")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["items"][0]["work_attention_count"], 3)
        cursor = first.json()["next_cursor"]
        self.assertIsNotNone(cursor)
        second = self.client.get(
            f"/api/v1/attention?limit=1&cursor={cursor}"
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["items"][0]["work_attention_count"], 3)

    def test_attention_reads_the_automatic_outcome_and_cause_columns(self) -> None:
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
        self.repository.browse_cases = [(case, report)]
        self.repository.attention_causes["title"] = "automatic_safety_suspended"
        self.repository.attention_dispositions["series"] = "abstention"
        self._login()

        response = self.client.get("/api/v1/attention?limit=10")

        self.assertEqual(response.status_code, 200, response.text)
        reasons = {
            item["destination"]: item["classification_reason"]
            for item in response.json()["items"]
        }
        self.assertEqual(
            reasons["title"],
            "Automatic handling is suspended pending a safety recovery.",
        )
        self.assertEqual(
            reasons["series"],
            "Automatic handling abstained, so this field needs your review.",
        )
        # The third row carries neither column, so the two above prove the
        # reader read them rather than a constant the double always emits.
        self.assertEqual(reasons["subtitle"], "This field needs your review.")

    def test_cursor_encoder_never_emits_an_unreachable_offset(self) -> None:
        boundary = _encode_cursor(100_000, "library", "all", None)
        self.assertEqual(
            _decode_cursor(boundary, "library", "all", None),
            100_000,
        )
        with self.assertRaises(ReviewWebError):
            _encode_cursor(100_001, "library", "all", None)
        with self.assertRaises(ReviewWebError):
            _encode_cursor(True, "library", "all", None)

    def test_large_library_payload_and_dom_inputs_remain_page_bounded(self) -> None:
        self.repository.browse_cases = [
            (self.case, self.repository.report) for _ in range(2_001)
        ]
        self._login()
        library = self.client.get("/api/v1/library?lens=all&limit=50")
        self.assertEqual(library.status_code, 200, library.text)
        self.assertEqual(len(library.json()["items"]), 50)
        self.assertIsNotNone(library.json()["next_cursor"])
        self.assertLess(len(library.content), 100_000)
        attention = self.client.get("/api/v1/attention?limit=50")
        self.assertEqual(len(attention.json()["items"]), 50)
        self.assertIsNotNone(attention.json()["next_cursor"])
        self.assertLess(len(attention.content), 100_000)


if __name__ == "__main__":
    unittest.main()
