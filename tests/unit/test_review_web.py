from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from argon2 import PasswordHasher
from argon2.low_level import Type
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    InspectCurrentReviewCase,
    RecordHumanDecision,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ReviewWebError,
    _decode_cursor,
    _encode_cursor,
    _provider_outcomes,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import PipelineProgress
from mechanical_librarian.application.review_application import (
    ReviewApplication,
    ReviewApplicationError,
)
from mechanical_librarian.interfaces.cli import main as cli_main
from mechanical_librarian.interfaces.web.app import _CoverOmission
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from mechanical_librarian.application.metadata_policy import PolicyTransition
from mechanical_librarian.adapters.operational_evidence import LocalTimingStore, SupportBundle
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile, resolve_policy
from tests.unit.test_review_case_persistence import _report
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
    _report as _cover_report,
    _source as _cover_source,
)
from tests.unit.test_proposal_set import (
    _claim as _proposal_claim,
    _provider as _proposal_provider,
    _report as _proposal_report,
)
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source as _bibliographic_source,
)


class _Repository:
    def __init__(self, case: ReviewCase, report) -> None:
        self.case = case
        self.report = report
        self.browse_cases = [(case, report)]
        self.browse_without_cases = []
        self.advice: dict[str, object] = {
            "results": [],
            "state": "not_requested",
        }
        self.failures: list[int] = []
        self.sessions: dict[str, tuple[str, str, int, int | None]] = {}
        self.decisions: dict[str, str] = {}
        self.revoke_failure = False
        self.revoke_result = "revoked"
        self.rotate_failure = False
        self.rotate_result = "rotated"
        self.existence_failure = False
        self.case_current = True
        self.policy_documents: dict[str, str] = {}
        self.policy_count_calls: list[str] = []
        builtin = MetadataPolicyProfile.builtin()
        self.active_policy_id = builtin.policy_id
        self.policy_transition = None
        self.shadow_mode = "review"
        self.shadow_outcomes: tuple[tuple[str, int], ...] = ()
        self.case_ready_at = 990

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        if review_case_id != self.case.review_case_id:
            return None
        return self.case_ready_at

    def current_unapplied_count(self, library: str) -> int:
        self.policy_count_calls.append(library)
        return 0

    def create_metadata_policy_draft(self, document: str) -> str:
        policy_id = "policy_" + hashlib.sha256(document.encode()).hexdigest()
        self.policy_documents[policy_id] = document
        return policy_id

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        return self.policy_documents[policy_id]

    def active_metadata_policy(self) -> str:
        if self.active_policy_id == MetadataPolicyProfile.builtin().policy_id:
            return MetadataPolicyProfile.builtin().canonical_json
        return self.policy_documents[self.active_policy_id]

    def activate_metadata_policy(self, policy_id, expected_active_policy_id, resolutions, activated_at):
        if expected_active_policy_id != self.active_policy_id:
            raise RuntimeError("stale")
        if policy_id == self.active_policy_id:
            return None
        previous = self.active_policy_id
        self.active_policy_id = policy_id
        self.policy_transition = PolicyTransition(
            "policy_transition_" + "a" * 64, previous, policy_id, 2,
            "completed", 0, 0, 0, False,
        )
        return self.policy_transition

    def metadata_policy_transition(self):
        return self.policy_transition

    def retry_metadata_policy_transition(self, transition_id, retried_at):
        return self.policy_transition

    def acknowledge_metadata_policy_transition(self, transition_id, acknowledged_at):
        value = self.policy_transition
        self.policy_transition = PolicyTransition(
            value.transition_id, value.from_policy_id, value.to_policy_id,
            value.generation, value.state, value.completed, value.total,
            value.failed, True,
        )
        return self.policy_transition

    def current_resolved_policy_id(self, library):
        return resolve_policy(MetadataPolicyProfile.from_data(json.loads(self.active_metadata_policy())), library).resolved_id

    def shadow_posture(self):
        return self.shadow_mode

    def enter_shadow_preview(self, updated_at):
        self.shadow_mode = "preview"
        return self.shadow_mode

    def list_shadow_outcomes(self, limit):
        return self.shadow_outcomes[:limit]

    def begin_login(self, now: int, window_seconds: int, maximum_failures: int) -> bool:
        self.failures = [value for value in self.failures if value >= now - window_seconds]
        permitted = len(self.failures) < maximum_failures
        if permitted:
            self.failures.append(now)
        return permitted

    def create_review_session(self, session_digest: str, csrf_digest: str, created_at: int, expires_at: int) -> None:
        self.sessions[session_digest] = ("owner", csrf_digest, expires_at, None)
        self.failures.clear()

    def authenticate_review_session(self, session_digest: str, now: int) -> tuple[str, str, int]:
        actor, csrf, expires, revoked = self.sessions[session_digest]
        if revoked is not None or expires <= now:
            raise RuntimeError
        return actor, csrf, expires

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        if self.rotate_failure:
            raise RuntimeError
        if self.rotate_result != "rotated":
            return self.rotate_result
        actor, _, expires, revoked = self.sessions[session_digest]
        if revoked is not None or expires <= now:
            return "unauthorized"
        self.sessions[session_digest] = (actor, csrf_digest, expires, None)
        return "rotated"

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        if self.revoke_failure:
            raise RuntimeError
        if self.revoke_result != "revoked":
            return self.revoke_result
        session = self.sessions.get(session_digest)
        if session is None:
            return "unauthorized"
        actor, csrf, expires, revoked = session
        if revoked is not None or expires <= now:
            return "unauthorized"
        if csrf != csrf_digest:
            return "csrf"
        self.sessions[session_digest] = (actor, csrf, expires, now)
        return "revoked"

    def review_case_exists(self, review_case_id: str) -> bool:
        if self.existence_failure:
            raise RuntimeError
        return review_case_id == self.case.review_case_id

    def get_review_case(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return json.dumps(
            {
                "case": self.case.to_data(),
                "current": self.case_current,
                "schema_version": "review-case-get-v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def get_review_case_history(self, review_case_id: str, limit: int) -> tuple[dict[str, object], ...]:
        return (
            {
                "checkpoint": 1,
                "kind": "admitted",
                "reason": "actionable",
                "transition_id": "review_transition_" + "a" * 64,
            },
        )

    def get_review_case_decisions(self, review_case_id: str) -> tuple[str, ...]:
        return tuple(self.decisions.values())

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        return next(
            (
                document
                for document in self.decisions.values()
                if HumanDecision.from_json(document).idempotency_digest
                == idempotency_digest
            ),
            None,
        )

    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str:
        del cover_consideration_id
        existing = self.decisions.get(decision.destination)
        if existing is not None:
            return existing
        self.decisions[decision.destination] = decision.to_json()
        return decision.to_json()

    def get_review_case_report(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return self.report.to_json()

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        return CurrentReviewCaseSnapshot(
            case_document=self.get_review_case(review_case_id),
            report_document=self.get_review_case_report(review_case_id),
            decision_documents=self.get_review_case_decisions(review_case_id),
            history=(
                ()
                if history_limit is None
                else self.get_review_case_history(review_case_id, history_limit)
            ),
        )

    @staticmethod
    def _field_attention(
        case: ReviewCase, proposal: dict[str, object]
    ) -> bool:
        result = proposal["result"]
        assert isinstance(result, dict)
        return (
            result["outcome"] == "unresolved_choice"
            or (
                proposal["destination"] in {"title", "subtitle", "series"}
                and any(
                    sibling["result"]["reasons"]
                    == ["no_safe_bibliographic_combination"]
                    for sibling in case.proposals
                )
            )
        )

    @staticmethod
    def _attention(case: ReviewCase) -> bool:
        return any(
            _Repository._field_attention(case, proposal)
            for proposal in case.proposals
        )

    def _searched(self, search: str | None):
        if search is None:
            return self.browse_cases
        query = search.casefold()
        return [
            (case, report)
            for case, report in self.browse_cases
            if query
            in " ".join(
                (
                    report.target.current_title or "",
                    " ".join(
                        value
                        for field in report.target.fields
                        if field.destination is TargetDestination.AUTHORS
                        and type(field.value) is tuple
                        for value in field.value
                    ),
                    report.target.library,
                    report.target.item,
                    report.file.format,
                )
            ).casefold()
        ]

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str | None, str], ...]:
        rows = [
            (case, report)
            for case, report in self._searched(search)
            if attention is None or self._attention(case) is attention
        ]
        if attention is not True:
            query = None if search is None else search.casefold()
            rows.extend(
                (None, report)
                for report in self.browse_without_cases
                if query is None
                or query
                in " ".join(
                    (
                        report.target.current_title or "",
                        " ".join(
                            value
                            for field in report.target.fields
                            if field.destination is TargetDestination.AUTHORS
                            and type(field.value) is tuple
                            for value in field.value
                        ),
                        report.target.library,
                        report.target.item,
                        report.file.format,
                    )
                ).casefold()
            )
        rows.sort(
            key=lambda value: (
                value[0] is None or not self._attention(value[0]),
                (value[1].target.current_title or "").casefold(),
                "" if value[0] is None else value[0].review_case_id,
            )
        )
        return tuple(
            (None if case is None else case.to_json(), report.to_json())
            for case, report in rows[offset : offset + limit]
        )

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        rows = self.list_current_review_case_documents(search, 100_000, 0, None)
        return len(rows), sum(
            case_document is not None
            and self._attention(ReviewCase.from_json(case_document))
            for case_document, _ in rows
        )

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        rows = self.list_current_review_case_documents(
            search, limit, offset, attention
        )
        total, attention_total = self.count_current_review_cases(search)
        return ReviewLibrarySnapshot(
            rows=tuple(
                (
                    case_document,
                    report_document,
                    (
                        ()
                        if case_document is None
                        else self.get_review_case_decisions(
                            ReviewCase.from_json(case_document).review_case_id
                        )
                    ),
                    (),
                    (),
                )
                for case_document, report_document in rows
            ),
            total=total,
            attention_total=attention_total,
        )

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str, int], ...]:
        rows = [
            (
                case.to_json(),
                report.to_json(),
                proposal["destination"],
                sum(
                    self._field_attention(case, other)
                    for other in case.proposals
                ),
            )
            for case, report in self._searched(search)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
        ]
        return tuple(rows[offset : offset + limit])

    def count_current_attention_fields(self, search: str | None) -> int:
        return sum(
            1
            for case, _ in self._searched(search)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
        )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        return ReviewAttentionSnapshot(
            rows=self.list_current_attention_documents(search, limit, offset),
            total=self.count_current_attention_fields(search),
        )


class _Preparation:
    libraries = (("primary", "fiction", "lib-1"),)

    def __init__(self) -> None:
        self.is_paused = False
        self.status_calls = 0

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def progress(self, connection: str, library: str):
        if (connection, library) != ("primary", "fiction"):
            return None
        return PipelineProgress(
            "review_scan_" + "a" * 64,
            connection,
            library,
            "processing",
            2,
            5,
            1,
            0,
            False,
        )

    def request(self, connection: str, library: str):
        return self.progress(connection, library)

    def status(self):
        self.status_calls += 1
        return ((self.progress("primary", "fiction"),), self.is_paused)

    def paused(self) -> bool:
        return self.is_paused

    def set_paused(self, paused: bool) -> None:
        self.is_paused = paused

    def retry(self, connection: str, library: str):
        return self.progress(connection, library)


class _ActivityApplication(ContinuousGuardedApplication):
    def __init__(self) -> None:
        self.enabled_libraries: set[str] = set()
        self.paused = False

    def attach_serialization(self, serialize) -> None:
        self.serialize = serialize

    def attach_timing(self, timing) -> None:
        return None

    def start(self):
        return (0, 0, 0)

    def begin_drain(self) -> None:
        return None

    def close_admission(self) -> None:
        return None

    def wait_for_drain(self, deadline=None) -> None:
        return None

    def activity(self, **query):
        return {
            "backup_ready": True,
            "configured_libraries": [
                {"connection": "primary", "library": "fiction", "prepared": {"automatic": 1, "human": 1, "total": 2}},
                {"connection": "primary", "library": "history", "prepared": {"automatic": 1, "human": 0, "total": 1}},
            ],
            "consented_libraries": [{"connection": "primary", "library": library} for library in sorted(self.enabled_libraries)],
            "capabilities": [
                {"connection": "primary", "library": "fiction", "state": "allowed",
                 "destinations": [{"destination": "title", "state": "allowed",
                                   "consented": "fiction" in self.enabled_libraries}]},
                {"connection": "primary", "library": "history", "state": "allowed",
                 "destinations": [{"destination": "title", "state": "allowed",
                                   "consented": "history" in self.enabled_libraries}]},
            ],
            "counts": {}, "enabled_libraries": [{"connection": "primary", "library": library} for library in sorted(self.enabled_libraries)],
            "events": [{"destination": "title", "event_id": "event-1", "event_type": "metadata", "evidence": [{"label": "Recorded automatic decision", "value": "verified"}], "failure": None, "library": "fiction", "occurred_at": 1, "origin": "automatic_policy", "state": "verified", "work_id": "book-1"}], "example": {"after": "After", "before": "Before", "destination": "title", "library": "fiction", "work_id": "book-1"},
            "last_failure": None, "paused": self.paused,
            "ledger": {"event_type": query.get("event_type", "all"), "page": query.get("page", 1), "page_count": 1, "page_size": query.get("page_size", 25), "query": query.get("search"), "range_days": query.get("range_days", 30), "total": 1},
            "current_waiting": None,
            "prepared": {"automatic": 1, "human": 1, "total": 2},
            "schema_version": "continuous-field-activity-v2", "suspensions": [],
        }

    def enable(self, grants):
        self.enabled_libraries.update(library for library, _ in grants)
        return self.activity()

    def revoke(self, connection, library):
        self.enabled_libraries.discard(library)
        return self.activity()

    def set_paused(self, paused):
        self.paused = paused

    def activate_human_override(self, review_case_id, destination):
        return None

    def return_to_automatic(self, review_case_id, destination):
        return None


class ReviewWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.hash_path = root / "owner.hash"
        self.hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(self.hash_path, 0o600)
        report = _report()
        self.case = ReviewCase.create(evaluate_report(report), report)
        self.repository = _Repository(self.case, report)
        self.now = 1_000
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        self.client = TestClient(
            create_transport_test_app(
                self.repository, configuration, clock=lambda: self.now
            ),
            base_url="https://review.test",
        )
        self.origin = {"Origin": "https://review.test"}

    def tearDown(self) -> None:
        self.client.close()
        self.temporary.cleanup()

    def _login(self) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            json={"password": "correct horse"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
        return response.json()

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
        repository = _Repository(case, report)
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

                class _FieldApplication(_ActivityApplication):
                    def start(self):
                        calls.append("field-start")
                        return (0, 0, 0)

                    def close_admission(self) -> None:
                        calls.append("field-close")

                    def wait_for_drain(self, deadline=None) -> None:
                        calls.append(("field-wait", deadline))
                        if failing_worker == "field":
                            raise GuardedApplicationError("shutdown_timeout")

                class _Preparation:
                    def start(self) -> None:
                        calls.append("preparation-start")

                    def begin_drain(self) -> None:
                        calls.append("preparation-close")

                    def stop(self, deadline=None) -> None:
                        calls.append(("preparation-wait", deadline))
                        if failing_worker == "preparation":
                            raise ReviewPipelineError("shutdown_timeout")

                self.repository.close = lambda: closed.append(True)
                app = create_transport_test_app(
                    self.repository, configuration, close_repository=True,
                    field_application=_FieldApplication(),
                    review_preparation=_Preparation(),
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
        application = _ActivityApplication()
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
        preparation = _Preparation()
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
        with patch.object(_Preparation, "set_paused", side_effect=RuntimeError):
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
        preparation = _Preparation()
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

    def test_only_liveness_and_login_are_anonymous(self) -> None:
        live = self.client.get("/health/live")
        self.assertEqual(live.status_code, 200)
        self.assertLessEqual(len(live.content), 3)
        login_page = self.client.get("/login")
        self.assertNotIn("Owner session", login_page.text)
        self.assertNotIn('class="sign-out"', login_page.text)
        denied = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "unauthorized")
        self.assertEqual(denied.headers["cache-control"], "no-store, private")

    def test_operational_status_and_support_are_authenticated_bounded_surfaces(self) -> None:
        self.repository.operational_snapshot = lambda: {"signals": [{
            "kind": "backup_failure", "cause": "backup", "scope": "field",
            "connection": "primary", "library": "fiction", "category": None,
            "work_id": "book-1", "field": "title",
        }]}
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        timings = LocalTimingStore(Path(self.temporary.name) / "timings.json")
        support = SupportBundle((("runtime", lambda: {
            "status": "degraded", "secret": "never-export",
        }),))
        self.client.close()
        self.client = TestClient(create_transport_test_app(
            self.repository, configuration, clock=lambda: self.now,
            operational_timings=timings, operational_support=support,
        ), base_url="https://review.test")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/operations/status").status_code, 401)
        session = self._login()
        status = self.client.get("/api/v1/operations/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["summary"]["healthy"])
        preview = self.client.get("/api/v1/operations/support/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn("never-export", preview.text)
        exported = self.client.post(
            "/api/v1/operations/support/export",
            headers={**self.origin, "x-csrf-token": session["csrf_token"]},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertNotIn("never-export", exported.text)
        self.repository.operational_snapshot = lambda: (_ for _ in ()).throw(
            FieldEffectError("persistence")
        )
        failed = self.client.get("/api/v1/operations/status")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["error"]["code"], "operations_unavailable")

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
        self.repository.create_metadata_policy_draft = Mock(side_effect=RuntimeError)
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

    def test_trust_boundary_failures_match_declared_api_statuses(self) -> None:
        bad_host = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}",
            headers={"Host": "elsewhere.invalid"},
        )
        self.assertEqual(bad_host.status_code, 400)
        self.assertEqual(bad_host.json()["error"]["code"], "invalid_origin")
        bad_origin = self.client.post(
            "/api/v1/session/login",
            headers={"Origin": "https://elsewhere.invalid"},
            json={"password": "correct horse"},
        )
        self.assertEqual(bad_origin.status_code, 403)
        self.assertEqual(bad_origin.json()["error"]["code"], "origin")

    def test_decision_authentication_precedes_required_input_validation(self) -> None:
        self.client.cookies.set("ml_session", "malformed")
        denied = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers=self.origin,
            json={},
        )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["error"]["code"], "unauthorized")

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
        self.assertNotIn("qualification_manifest_sha256", field["automatic_decision"])
        malformed = json.loads(self.repository.decisions["title"])
        malformed["candidate_id"] = "alternative_" + "f" * 64
        self.repository.decisions["title"] = json.dumps(malformed, separators=(",", ":"), sort_keys=True)
        self.assertEqual(404, self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}").status_code)

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

    def test_login_case_evidence_history_and_logout_round_trip(self) -> None:
        login = self._login()
        cookie = self.client.cookies.get("ml_session")
        self.assertIsNotNone(cookie)
        detail = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["current"])
        self.assertEqual(detail.json()["review_case_id"], self.case.review_case_id)
        title = detail.json()["fields"][0]
        self.assertEqual(title["current"]["value"], "Title")
        self.assertEqual(
            title["proposed_values"][0]["value"], "Exact evaluator candidate"
        )
        self.assertEqual(title["deterministic_disposition"], "unresolved_choice")
        self.assertTrue(title["needs_attention"])
        self.assertFalse(detail.json()["fields"][-1]["needs_attention"])
        self.assertNotIn("model_advice", detail.json())
        evidence = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}/evidence")
        history = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}/history")
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.json()["items"][0]["source"], "local")
        self.assertIn("Exact evaluator candidate", evidence.json()["items"][0]["fact"])
        invalid_cursor = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}/evidence?cursor=bad"
        )
        self.assertEqual(invalid_cursor.status_code, 400)
        self.assertEqual(invalid_cursor.json()["error"]["code"], "request")
        self.assertEqual(history.json()["items"][0]["kind"], "admitted")
        self.assertIn("default-src 'none'", detail.headers["content-security-policy"])
        missing = self.client.post(
            "/api/v1/session/logout",
            headers=self.origin,
        )
        self.assertEqual(missing.status_code, 422)
        wrong = self.client.post(
            "/api/v1/session/logout",
            headers={**self.origin, "X-CSRF-Token": "x" * 32},
        )
        self.assertEqual(wrong.status_code, 403)
        logout = self.client.post(
            "/api/v1/session/logout",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
        )
        self.assertEqual(logout.status_code, 204)
        denied = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}")
        self.assertEqual(denied.status_code, 401)

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
            changed_detail = {"fields": [{"provenance": []}]}
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
        items = first["items"] + second["items"]
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

    def test_logout_persistence_failure_keeps_the_session_active(self) -> None:
        login = self._login()
        self.repository.revoke_failure = True
        failed = self.client.post(
            "/api/v1/session/logout",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
        )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["error"]["code"], "persistence")
        self.assertIsNotNone(self.client.cookies.get("ml_session"))
        detail = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        )
        self.assertEqual(detail.status_code, 200)

    def test_logout_concurrent_revocation_is_unauthorized_not_an_outage(self) -> None:
        login = self._login()
        self.repository.revoke_result = "unauthorized"
        failed = self.client.post(
            "/api/v1/session/logout",
            headers={**self.origin, "X-CSRF-Token": login["csrf_token"]},
        )
        self.assertEqual(failed.status_code, 401)
        self.assertEqual(failed.json()["error"]["code"], "unauthorized")
        self.assertNotIn("set-cookie", failed.headers)

    def test_logout_maps_expired_and_malformed_sessions_to_unauthorized(self) -> None:
        login = self._login()
        self.now += 601
        expired = self.client.post(
            "/api/v1/session/logout",
            headers=self.origin,
        )
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.json()["error"]["code"], "unauthorized")
        self.assertNotIn("set-cookie", expired.headers)

        self.client.cookies.clear()
        self.client.cookies.set("ml_session", "malformed")
        malformed = self.client.post(
            "/api/v1/session/logout",
            headers=self.origin,
        )
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(malformed.json()["error"]["code"], "unauthorized")
        self.assertNotIn("set-cookie", malformed.headers)

    def test_absent_decision_case_is_not_a_persistence_outage(self) -> None:
        login = self._login()
        detail = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        ).json()
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
            **self.origin,
            "X-CSRF-Token": login["csrf_token"],
            "Idempotency-Key": "decision-request-absent",
        }
        absent = self.client.post(url, headers=headers, json=payload)
        self.assertEqual(absent.status_code, 404)
        self.assertEqual(absent.json()["error"]["code"], "not_found")
        self.assertFalse(self.repository.decisions)

        self.repository.existence_failure = True
        unavailable = self.client.post(url, headers=headers, json=payload)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json()["error"]["code"], "persistence"
        )

    def test_one_explicit_decision_requires_csrf_and_is_server_authoritative(
        self,
    ) -> None:
        timings = LocalTimingStore(
            Path(self.temporary.name) / "private" / "decision-timings.json",
            enabled=True,
        )
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
        )
        self.client.close()
        self.client = TestClient(
            create_transport_test_app(
                self.repository, configuration, clock=lambda: self.now,
                operational_timings=timings,
            ),
            base_url="https://review.test",
        )
        login = self._login()
        detail = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        ).json()
        field = detail["fields"][0]
        payload = {
            "destination": field["destination"],
            "outcome": "accept",
            "proposal_id": field["proposal_id"],
            "selected_value": field["proposed_values"][0],
            "target_fingerprint": field["target_fingerprint"],
        }
        denied = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={**self.origin, "Idempotency-Key": "decision-request-0001"},
            json=payload,
        )
        self.assertEqual(denied.status_code, 422)
        accepted = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={
                **self.origin,
                "X-CSRF-Token": login["csrf_token"],
                "Idempotency-Key": "decision-request-0001",
            },
            json=payload,
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["outcome"], "accept")
        self.assertEqual(
            [
                (value.stage, value.wait_class, value.duration_ms)
                for value in timings.list()
            ],
            [("human_wait", "owner", 10_000)],
        )
        refreshed = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        ).json()
        self.assertFalse(refreshed["fields"][0]["needs_attention"])
        self.assertEqual(
            refreshed["fields"][0]["human_decision"]["decision_id"],
            accepted.json()["decision_id"],
        )
        self.repository.case_current = False
        self.now += 1
        replay = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={
                **self.origin,
                "X-CSRF-Token": login["csrf_token"],
                "Idempotency-Key": "decision-request-0001",
            },
            json=payload,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(
            replay.json()["decision_id"], accepted.json()["decision_id"]
        )
        self.assertEqual(len(timings.list()), 1)
        conflict_payload = {**payload, "outcome": "reject", "selected_value": None}
        conflict = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={
                **self.origin,
                "X-CSRF-Token": login["csrf_token"],
                "Idempotency-Key": "decision-request-0001",
            },
            json=conflict_payload,
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            conflict.json()["error"]["code"], "idempotency_conflict"
        )
        self.assertEqual(len(self.repository.decisions), 1)

    def test_review_page_rotates_one_memory_only_csrf_token(self) -> None:
        login = self._login()
        inactive = self.client.get("/api/v1/review-preparation")
        self.assertEqual(inactive.status_code, 200, inactive.text)
        self.assertEqual(
            inactive.json(),
            {
                "schema_version": "review-preparation-v1",
                "libraries": [],
                "paused": False,
                "active": False,
                "actionable": False,
            },
        )
        page = self.client.get(f"/review/{self.case.review_case_id}")
        self.assertEqual(page.status_code, 200, page.text)
        match = re.search(r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', page.text)
        self.assertIsNotNone(match)
        assert match is not None
        browser_csrf = match.group(1)
        self.assertNotEqual(browser_csrf, login["csrf_token"])
        self.assertIn(
            f'data-build-identity="{TEST_REACT_BUILD_IDENTITY}"', page.text
        )
        script = self.client.get(
            "/assets/review-app/assets/index-deadbeef.js"
        )
        self.assertEqual(script.status_code, 200)
        field = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        ).json()["fields"][0]
        payload = {
            "destination": field["destination"],
            "outcome": "keep_current",
            "proposal_id": field["proposal_id"],
            "selected_value": None,
            "target_fingerprint": field["target_fingerprint"],
        }
        stale = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={
                **self.origin,
                "X-CSRF-Token": login["csrf_token"],
                "Idempotency-Key": "browser-decision-rotate-0001",
            },
            json=payload,
        )
        self.assertEqual(stale.status_code, 403)
        accepted = self.client.post(
            f"/api/v1/review-cases/{self.case.review_case_id}/decisions",
            headers={
                **self.origin,
                "X-CSRF-Token": browser_csrf,
                "Idempotency-Key": "browser-decision-rotate-0001",
            },
            json=payload,
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_authenticated_page_preserves_csrf_rotation_persistence_failure(
        self,
    ) -> None:
        self._login()
        self.repository.rotate_failure = True
        for path in (
            "/library",
            "/attention",
            f"/review/{self.case.review_case_id}",
        ):
            unavailable = self.client.get(path)
            self.assertEqual(unavailable.status_code, 503, path)
            self.assertEqual(
                unavailable.json()["error"]["code"], "persistence", path
            )

    def test_authenticated_page_preserves_expired_rotation_as_unauthorized(
        self,
    ) -> None:
        self._login()
        self.repository.rotate_result = "unauthorized"
        for path in (
            "/library",
            "/attention",
            f"/review/{self.case.review_case_id}",
        ):
            denied = self.client.get(path)
            self.assertEqual(denied.status_code, 401, path)
            self.assertEqual(
                denied.json()["error"]["code"], "unauthorized", path
            )

    def test_malformed_case_decision_body_is_bounded_before_validation(self) -> None:
        oversized = b'{' + b'"private":"' + b'x' * 9_000 + b'"}'
        declared = self.client.post(
            "/api/v1/review-cases/not-a-case/decisions",
            headers={**self.origin, "Content-Type": "application/json"},
            content=oversized,
        )
        self.assertEqual(declared.status_code, 413)
        self.assertNotIn(b"private", declared.content)
        streamed = self.client.post(
            "/api/v1/review-cases/not-a-case/decisions",
            headers={**self.origin, "Content-Type": "application/json"},
            content=(chunk for chunk in (oversized[:4_500], oversized[4_500:])),
        )
        self.assertEqual(streamed.status_code, 413)
        self.assertNotIn(b"private", streamed.content)

    def test_origin_host_proxy_expiry_and_throttle_fail_closed(self) -> None:
        invalid_origin = self.client.post(
            "/api/v1/session/login",
            headers={"Origin": "https://evil.test"},
            json={"password": "correct horse"},
        )
        self.assertEqual(invalid_origin.status_code, 403)
        for name in (
            "cache-control",
            "content-security-policy",
            "strict-transport-security",
            "x-frame-options",
        ):
            self.assertIn(name, invalid_origin.headers)
        forwarded = self.client.get(
            "/login", headers={"X-Forwarded-Host": "review.test"}
        )
        self.assertEqual(forwarded.status_code, 400)
        for name in (
            "cache-control",
            "content-security-policy",
            "strict-transport-security",
            "x-frame-options",
        ):
            self.assertIn(name, forwarded.headers)
        for _ in range(5):
            failure = self.client.post(
                "/api/v1/session/login",
                headers=self.origin,
                json={"password": "wrong"},
            )
            self.assertEqual(failure.status_code, 401)
        throttled = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            json={"password": "correct horse"},
        )
        self.assertEqual(throttled.status_code, 429)
        self.repository.failures.clear()
        self._login()
        self.now += 601
        expired = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}")
        self.assertEqual(expired.status_code, 401)

    def test_openapi_is_deterministic_and_secret_free(self) -> None:
        anonymous = self.client.get("/api/v1/openapi.json")
        self.assertEqual(anonymous.status_code, 401)
        self._login()
        first = self.client.get("/api/v1/openapi.json")
        second = self.client.get("/api/v1/openapi.json")
        self.assertEqual(first.content, second.content)
        expected = json.loads(
            Path("docs/reference/review-openapi-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(first.json(), expected)
        self.assertIn(
            "413",
            expected["paths"]["/api/v1/metadata-policy/drafts"]["post"]["responses"],
        )
        self.assertIn(
            "413",
            expected["paths"]["/api/v1/metadata-policy/activate"]["post"]["responses"],
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
                self.assertEqual(len(build_parameters), 1, (path, method))
                self.assertTrue(build_parameters[0]["required"])
                self.assertEqual(build_parameters[0]["in"], "header")
                self.assertIn("409", operation["responses"])
        schemas = expected["components"]["schemas"]
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
            options = option_schema["enum"] if "enum" in option_schema else [
                option_schema["const"]
            ]
            closed_qualification.update(
                {f"{field}:{option}": disposition for option in options}
            )
        self.assertEqual(dict(F20_OPTION_DISPOSITIONS), closed_qualification)
        self.assertNotIn("HTTPValidationError", schemas)
        self.assertTrue(
            all(
                schema.get("additionalProperties") is False
                for schema in schemas.values()
                if schema.get("type") == "object"
            )
        )
        for path, schema in (
            ("/api/v1/metadata-policy/automatic-safety/pause", "_AutomaticPauseRequest"),
            ("/api/v1/metadata-policy/automatic-safety/recover", "_AutomaticRecoveryRequest"),
            ("/api/v1/metadata-policy/automatic-safety/suspend", "_AutomaticSuspendRequest"),
        ):
            self.assertEqual(
                {"$ref": f"#/components/schemas/{schema}"},
                expected["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"],
            )
        self.assertNotIn(str(self.hash_path).encode(), first.content)
        self.assertNotIn(b"correct horse", first.content)
        decision_operation = expected["paths"][
            "/api/v1/review-cases/{review_case_id}/decisions"
        ]["post"]
        self.assertIn("404", decision_operation["responses"])
        self.assertEqual(
            expected["components"]["securitySchemes"]["OwnerSession"],
            {"in": "cookie", "name": "ml_session", "type": "apiKey"},
        )
        self.assertEqual(
            decision_operation["security"], [{"OwnerSession": []}]
        )
        protected_operations = {
            (method, path)
            for path, path_item in expected["paths"].items()
            for method, operation in path_item.items()
            if operation.get("security") == [{"OwnerSession": []}]
        }
        self.assertEqual(
            protected_operations,
            {
                ("get", "/api/v1/activity"),
                ("post", "/api/v1/activity/consent"),
                ("post", "/api/v1/activity/{connection}/{library}/revoke"),
                ("get", "/api/v1/attention"),
                ("get", "/api/v1/library"),
                ("get", "/api/v1/operations/status"),
                ("get", "/api/v1/operations/support/preview"),
                ("post", "/api/v1/operations/support/export"),
                ("get", "/api/v1/field-effects/{plan_id}"),
                ("post", "/api/v1/review-cases/{review_case_id}/fields/{destination}/return-to-automatic"),
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
                ("get", "/api/v1/review-cases/{review_case_id}"),
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
                ("post", "/api/v1/review-preparation/pause"),
                ("post", "/api/v1/review-preparation/resume"),
                ("post", "/api/v1/session/logout"),
            },
        )
        response_statuses = {
            (method, path): set(operation["responses"])
            for path, path_item in expected["paths"].items()
            for method, operation in path_item.items()
        }
        self.assertEqual(
            response_statuses[("post", "/api/v1/session/login")],
            {"200", "400", "401", "403", "413", "422", "429", "503"},
        )
        self.assertEqual(
            response_statuses[("post", "/api/v1/session/logout")],
            {"204", "400", "401", "403", "409", "422", "503"},
        )
        self.assertEqual(
            response_statuses[
                ("post", "/api/v1/review-cases/{review_case_id}/decisions")
            ],
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        )
        for path in (
            "/api/v1/review-cases/{review_case_id}",
            "/api/v1/review-cases/{review_case_id}/evidence",
            "/api/v1/review-cases/{review_case_id}/history",
        ):
            self.assertEqual(
                response_statuses[("get", path)],
                {"200", "400", "401", "404", "409", "422"},
            )
        for path in ("/api/v1/library", "/api/v1/attention"):
            self.assertEqual(
                response_statuses[("get", path)],
                {"200", "400", "401", "422", "503"},
            )
        self.assertEqual(
            response_statuses[("get", "/api/v1/review-preparation")],
            {"200", "401", "503"},
        )
        self.assertEqual(
            response_statuses[("get", "/api/v1/field-effects/{plan_id}")],
            {"200", "401", "404", "422", "503"},
        )
        for path in (
            "/api/v1/field-effects/{plan_id}/apply",
            "/api/v1/field-effects/{plan_id}/reconcile",
        ):
            self.assertEqual(
                response_statuses[("post", path)],
                {"200", "401", "403", "404", "409", "422", "503"},
            )
        for path in (
            "/api/v1/field-effects/pause",
            "/api/v1/field-effects/resume",
        ):
            self.assertEqual(
                response_statuses[("post", path)],
                {"204", "401", "403", "409", "422", "503"},
            )
        self.assertEqual(
            response_statuses[
                (
                    "post",
                    "/api/v1/review-preparation/{connection}/{library_name}/scan",
                )
            ],
            {"202", "400", "401", "403", "404", "409", "422", "503"},
        )
        self.assertEqual(
            response_statuses[("post", "/api/v1/review-preparation/pause")],
            {"200", "401", "403", "409", "422", "503"},
        )
        self.assertEqual(
            response_statuses[("post", "/api/v1/review-preparation/resume")],
            {"200", "401", "403", "409", "422", "503"},
        )
        self.assertEqual(
            response_statuses[("post", "/api/v1/review-preparation/{connection}/{library_name}/retry")],
            {"202", "401", "403", "404", "409", "422", "503"},
        )
        headers = {
            value["name"]: value
            for value in decision_operation["parameters"]
            if value["in"] == "header"
        }
        self.assertTrue(headers["x-csrf-token"]["required"])
        self.assertTrue(headers["idempotency-key"]["required"])
        invalid = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            json={"password": "secret", "unexpected": "private-value"},
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn(b"private-value", invalid.content)

    def test_parallel_failures_reserve_the_five_attempt_bound(self) -> None:
        def attempt(_: int) -> int:
            return self.client.post(
                "/api/v1/session/login",
                headers=self.origin,
                json={"password": "wrong"},
            ).status_code

        with ThreadPoolExecutor(max_workers=6) as executor:
            statuses = list(executor.map(attempt, range(6)))
        self.assertEqual(sorted(statuses), [401, 401, 401, 401, 401, 429])
        self.assertEqual(len(self.repository.failures), 5)

    def test_login_body_is_bounded_before_json_parsing_or_throttling(self) -> None:
        oversized = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            content=b'{"password":"' + b"x" * 4096 + b'"}',
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["error"]["code"], "body_too_large")
        self.assertEqual(self.repository.failures, [])
        streamed = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            content=iter([b'{"password":"', b"x" * 4096, b'"}']),
        )
        self.assertEqual(streamed.status_code, 413)
        self.assertEqual(self.repository.failures, [])

    def test_browser_asset_loads_all_read_models_without_html_injection(self) -> None:
        script = self.client.get(
            "/assets/review-app/assets/index-deadbeef.js"
        )
        self.assertEqual(script.status_code, 200)
        self.assertTrue(script.headers["content-type"].startswith("text/javascript"))

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

    def test_composition_opens_the_single_database_for_worker_threads(self) -> None:
        from tests.support.tls import synthetic_tls_identity

        repository = Mock()
        database = Path(self.temporary.name) / "application.sqlite3"
        certificate, private_key = synthetic_tls_identity(
            Path(self.temporary.name)
        )
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            tls_certificate_file=str(certificate),
            tls_private_key_file=str(private_key),
        )
        with (
            patch(
                "mechanical_librarian.bootstrap.composition.load_configuration_file",
                return_value=SimpleNamespace(review=configuration),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.open_repository",
                return_value=repository,
            ) as opened,
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
                Path("/catalogue.sqlite3"),
                Path("/artifacts"),
                "127.0.0.1",
                8443,
            )
        opened.assert_called_once_with(
            database,
            create=False,
            cross_thread=True,
        )

    def test_argon2i_hash_file_is_rejected(self) -> None:
        self.client.close()
        self.hash_path.write_text(
            PasswordHasher(type=Type.I).hash("correct horse"), encoding="utf-8"
        )
        os.chmod(self.hash_path, 0o600)
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
        )
        with self.assertRaises(Exception):
            create_transport_test_app(self.repository, configuration)


class ReviewSessionPersistenceTests(unittest.TestCase):
    def test_terminal_provider_taxonomy_projects_closed_safe_outcomes(self) -> None:
        statuses = tuple(
            status
            for status in ProviderStatus
            if status is not ProviderStatus.CANDIDATES
        )
        source = _report()
        report = MetadataReport.create(
            target=source.target,
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=tuple(
                sorted(
                    (
                        ProviderObservation(f"provider-{index}", status)
                        for index, status in enumerate(statuses)
                    ),
                    key=lambda provider: provider.provider,
                )
            ),
        )
        outcomes = _provider_outcomes(report)
        self.assertEqual(
            {outcome["status"] for outcome in outcomes},
            {status.value for status in statuses}
            - {"disabled", "insufficient_input"},
        )
        self.assertTrue(
            all(
                set(outcome)
                == {"lane_kind", "provider", "provider_requests", "reason", "status"}
                and outcome["provider_requests"] == 0
                for outcome in outcomes
            )
        )
        self.assertNotIn("diagnostic", json.dumps(outcomes))
        many = MetadataReport.create(
            target=source.target, file=source.file, local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=tuple(ProviderObservation(f"lane-{index:02d}", ProviderStatus.NO_MATCH) for index in range(33)),
        )
        self.assertEqual(len(_provider_outcomes(many)), 33)

    def test_fifty_mixed_packages_and_failures_share_one_bounded_browse_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "review.sqlite")
            repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
            discovery = repository.claim_review_pipeline_action(11)
            assert discovery is not None
            from mechanical_librarian.application.catalogue import CatalogueEntry
            entries = tuple(
                CatalogueEntry("primary", "fiction", "lib-1", f"failed-{index:02d}", index, f"Failed {index:02d}", (), True, False, "singular-supported", f"{index + 1:064x}", f"/safe-{index}.epub", "epub", "/media")
                for index in range(25)
            )
            repository.complete_review_discovery(discovery, 1, "a" * 64, entries, 12)
            now = 13
            while action := repository.claim_review_pipeline_action(now):
                if action.stage == "extraction":
                    repository.advance_review_pipeline_action(action, None, now + 1)
                elif action.stage == "provider":
                    repository.fail_review_pipeline_action(action, "authentication", False, now + 1, "hardcover-main")
                else:
                    self.fail(f"unexpected stage {action.stage}")
                now += 2
            for index in range(25):
                source = _report(title=f"Published {index:02d}", content_digest=f"{index + 30:064x}"[-1], file_identity=f"{index + 60:064x}"[-1])
                report = MetadataReport.create(
                    target=TargetFacts("primary", "ebooks", f"published-{index:02d}", source.target.fields),
                    file=source.file,
                    local_claims=source.local_claims,
                    local_reasons=source.local_reasons,
                    providers=source.providers,
                )
                repository.publish(report, lambda: True)
            total, attention = repository.count_current_review_cases(None)
            rows = tuple(
                row
                for offset in range(0, 50, 7)
                for row in repository.list_current_review_case_documents(None, 7, offset, None)
            )
            failed_total = repository.count_current_review_cases("fiction")[0]
            published_total = repository.count_current_review_cases("ebooks")[0]
            repository.close()
        self.assertEqual((total, attention), (50, 0))
        self.assertEqual((failed_total, published_total), (25, 25))
        self.assertEqual(len(rows), 50)
        self.assertEqual(len(set(rows)), 50)

    def test_terminal_failed_work_is_library_only_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "review.sqlite")
            repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
            discovery = repository.claim_review_pipeline_action(11)
            assert discovery is not None
            from mechanical_librarian.application.catalogue import CatalogueEntry
            repository.complete_review_discovery(discovery, 1, "a" * 64, (CatalogueEntry("primary", "fiction", "lib-1", "item-secret", 1, "Title", (), True, False, "singular-supported", "b" * 64, "/safe.epub", "epub", "/media"),), 12)
            extraction = repository.claim_review_pipeline_action(13)
            assert extraction is not None
            repository.advance_review_pipeline_action(extraction, None, 14)
            provider = repository.claim_review_pipeline_action(15)
            assert provider is not None
            repository.fail_review_pipeline_action(provider, "authentication", False, 16, "hardcover-main")
            repository.publish(_report(), lambda: True)
            first_page = repository.list_current_review_case_documents(None, 1, 0, None)
            second_page = repository.list_current_review_case_documents(None, 1, 1, None)
            title_rows = repository.list_current_review_case_documents(
                "Preparation unavailable", 10, 0, None
            )
            media_rows = repository.list_current_review_case_documents(
                "unknown", 10, 0, None
            )
            snapshot = repository.read_review_library(None, 10, 0, None)
            title_total = repository.count_current_review_cases(
                "Preparation unavailable"
            )
            media_total = repository.count_current_review_cases("unknown")
            attention = repository.read_review_attention(None, 10, 0)
            repository.close()
        self.assertEqual(len(first_page), 1)
        self.assertEqual(len(second_page), 1)
        self.assertNotEqual(first_page, second_page)
        self.assertEqual(snapshot.total, 2)
        self.assertEqual(snapshot.attention_total, 0)
        self.assertEqual(len(snapshot.rows), 2)
        self.assertEqual((len(title_rows), title_total), (1, (1, 0)))
        self.assertEqual((len(media_rows), media_total), (1, (1, 0)))
        self.assertEqual(attention.total, 0)
        from mechanical_librarian.application.review_web import _library_item
        item = next(
            _library_item(*row[:3])
            for row in snapshot.rows
            if _library_item(*row[:3])["preparation"] is not None
        )
        self.assertEqual(item["preparation"], {"attempt": 1, "provider": "hardcover-main", "reason": "Provider credentials need owner attention.", "recovery": "Retry review preparation after correcting the reported condition.", "recovery_attempt": 0, "stage": "provider", "status": "authentication"})
        self.assertEqual(item["preparation_outcomes"], [])
        self.assertNotIn("credential unavailable", json.dumps(item))
    def test_cover_state_is_inside_the_current_case_read_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            reader = open_repository(database)
            report = _report()
            reader.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            reader.publish_proposal(proposal_set)
            case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
            replay_digest = "b" * 64
            consideration_id = "cover_consideration_" + "c" * 64
            document = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "replay_digest": replay_digest,
                    "report_id": report.report_id,
                    "review_case_id": case_id,
                    "schema_version": "cover-consideration-v1",
                    "supersedes_id": None,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            inserted = threading.Event()
            finished = threading.Event()
            writer_errors: list[BaseException] = []
            writer_path = database if reader._binding is None else reader._binding.alias_path

            def write_consideration() -> None:
                writer = None
                try:
                    connection = sqlite3.connect(writer_path, isolation_level=None)
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("PRAGMA busy_timeout=5000")
                    writer = SQLiteMetadataReportRepository(connection)
                    writer._connection.set_trace_callback(
                        lambda statement: inserted.set()
                        if statement.startswith("INSERT INTO cover_considerations")
                        else None
                    )
                    writer.record_cover_consideration(
                        document,
                        review_case_id=case_id,
                        report_id=report.report_id,
                        replay_digest=replay_digest,
                        supersedes_id=None,
                        occurred_at=1_000,
                    )
                except BaseException as error:
                    writer_errors.append(error)
                finally:
                    if writer is not None:
                        writer.close()
                    finished.set()

            original = reader.get_review_case
            started = False

            def interleave(review_case_id: str) -> str:
                nonlocal started
                value = original(review_case_id)
                if not started:
                    started = True
                    threading.Thread(target=write_consideration, daemon=True).start()
                    self.assertTrue(inserted.wait(5))
                return value

            with patch.object(reader, "get_review_case", side_effect=interleave):
                before = reader.read_current_review_case(case_id, 50)
            self.assertIsNone(before.cover_consideration_document)
            self.assertEqual((), before.cover_history)
            self.assertTrue(finished.wait(5))
            self.assertEqual([], writer_errors)
            after = reader.read_current_review_case(case_id, 50)
            self.assertEqual(document, after.cover_consideration_document)
            self.assertEqual("automatic_prepared", after.cover_history[0]["kind"])
            reader.close()

    def test_cover_retry_stops_after_an_owner_outcome(self) -> None:
        application = object.__new__(ReviewApplication)
        application._cover_considerations = Mock()
        application._cases = Mock()
        application._cases._read.return_value = (
            {
                "fields": [
                    {
                        "destination": "cover",
                        "human_decision": {"outcome": "accept"},
                        "proposal_id": "review_proposal_" + "d" * 64,
                    }
                ]
            },
            _report(),
            None,
        )
        application.serialized = lambda call: call()

        with self.assertRaisesRegex(ReviewApplicationError, "already_decided"):
            application.retry_cover_consideration("review_case_" + "e" * 64)
        application._cover_considerations.ensure.assert_not_called()

    def test_cover_observer_runs_outside_the_global_application_gate(self) -> None:
        application = object.__new__(ReviewApplication)
        application._gate = threading.RLock()
        application.serialized = ReviewApplication.serialized.__get__(application)
        application._cases = Mock()
        application._cases._read.return_value = (
            {
                "fields": [
                    {
                        "destination": "cover",
                        "human_decision": None,
                        "proposal_id": "review_proposal_" + "d" * 64,
                    }
                ]
            },
            _report(),
            None,
        )
        service = Mock()
        service.current.return_value = None
        service.existing.return_value = None
        service.latest_id.return_value = None
        service.prepare_observation.return_value = object()
        expected = {"consideration_id": "cover_consideration_" + "f" * 64}
        service.ensure.return_value = expected

        def observe(*_args):
            acquired: list[bool] = []

            def probe() -> None:
                held = application._gate.acquire(blocking=False)
                acquired.append(held)
                if held:
                    application._gate.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(5)
            self.assertEqual([True], acquired)
            return object()

        service.observe.side_effect = observe
        application._cover_considerations = service

        self.assertEqual(
            expected,
            application.prepare_cover_consideration("review_case_" + "e" * 64),
        )
        service.ensure.assert_called_once()

    def test_cover_retry_allows_a_successor_after_stale_retirement(self) -> None:
        application = object.__new__(ReviewApplication)
        application._cover_considerations = Mock()
        application._cases = Mock()
        application._cases._read.return_value = (
            {
                "fields": [
                    {
                        "destination": "cover",
                        "human_decision": {"outcome": "accept"},
                        "proposal_id": "review_proposal_" + "d" * 64,
                    }
                ]
            },
            _report(),
            None,
        )
        application._cover_considerations.current.return_value = None
        application._cover_considerations.latest_id.return_value = (
            "cover_consideration_" + "a" * 64
        )
        successor = {"consideration_id": "cover_consideration_" + "b" * 64}
        application._cover_considerations.ensure.return_value = successor
        application._cover_considerations.prepare_observation.return_value = object()
        application._cover_considerations.observe.return_value = object()
        application.serialized = lambda call: call()

        self.assertEqual(
            successor,
            application.retry_cover_consideration("review_case_" + "e" * 64),
        )

    def test_current_case_snapshot_cannot_mix_concurrent_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            reader = open_repository(database)
            report = _report()
            reader.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            reader.publish_proposal(proposal_set)
            case_id = json.loads(reader.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            newer = _report(title="New Title", file_identity="c")
            pointer_written = threading.Event()
            finished = threading.Event()
            writer_errors: list[BaseException] = []
            writer_path = (
                database
                if reader._binding is None
                else reader._binding.alias_path
            )

            def supersede() -> None:
                writer = None
                try:
                    connection = sqlite3.connect(
                        writer_path,
                        isolation_level=None,
                    )
                    connection.execute("PRAGMA foreign_keys=ON")
                    connection.execute("PRAGMA busy_timeout=5000")
                    writer = SQLiteMetadataReportRepository(connection)
                    writer._connection.set_trace_callback(
                        lambda statement: pointer_written.set()
                        if statement.startswith("INSERT INTO current_metadata_reports")
                        else None
                    )
                    writer.publish(
                        newer,
                        lambda: True,
                        expected_current=report.report_id,
                    )
                except BaseException as error:
                    writer_errors.append(error)
                finally:
                    if writer is not None:
                        writer.close()
                    finished.set()

            original = reader.get_review_case
            started = False

            def interleave(review_case_id: str) -> str:
                nonlocal started
                document = original(review_case_id)
                if not started:
                    started = True
                    threading.Thread(target=supersede, daemon=True).start()
                    if not pointer_written.wait(5):
                        self.fail(f"supersession did not reach its pointer: {writer_errors!r}")
                return document

            with patch.object(reader, "get_review_case", side_effect=interleave):
                snapshot = reader.read_current_review_case(case_id, 50)
            self.assertIs(json.loads(snapshot.case_document)["current"], True)
            self.assertEqual(
                MetadataReport.from_json(snapshot.report_document).report_id,
                report.report_id,
            )
            self.assertTrue(finished.wait(5))
            self.assertEqual(writer_errors, [])
            self.assertIs(json.loads(reader.get_review_case(case_id))["current"], False)
            reader.close()

    def test_review_reads_hold_one_snapshot_across_concurrent_decision(self) -> None:
        for surface in ("case", "library", "attention"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "application.sqlite3"
                reader = open_repository(database)
                report = _report()
                reader.publish(report, lambda: True)
                proposal_set = evaluate_report(report)
                reader.publish_proposal(proposal_set)
                case_id = json.loads(reader.admit_review_case(proposal_set))[
                    "review_case_id"
                ]
                case = ReviewCase.create_from_data(
                    json.loads(reader.get_review_case(case_id))["case"]
                )
                proposal = next(
                    value
                    for value in case.proposals
                    if value["result"]["outcome"] == "unresolved_choice"
                )
                decision = HumanDecision.create(
                    case,
                    proposal_id=proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination=proposal["destination"],
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key=f"snapshot-{surface}-decision",
                    occurred_at=1_000,
                )
                inserted = threading.Event()
                finished = threading.Event()
                writer_errors: list[BaseException] = []
                writer_path = (
                    database
                    if reader._binding is None
                    else reader._binding.alias_path
                )

                def write_decision() -> None:
                    writer = None
                    try:
                        connection = sqlite3.connect(
                            writer_path,
                            isolation_level=None,
                        )
                        connection.execute("PRAGMA foreign_keys=ON")
                        connection.execute("PRAGMA busy_timeout=5000")
                        writer = SQLiteMetadataReportRepository(connection)
                        writer._connection.set_trace_callback(
                            lambda statement: inserted.set()
                            if statement.startswith("INSERT INTO human_decisions")
                            else None
                        )
                        writer.record_human_decision(decision)
                    except BaseException as error:
                        writer_errors.append(error)
                    finally:
                        if writer is not None:
                            writer.close()
                        finished.set()

                started = False

                def interleave(original, *args):
                    nonlocal started
                    result = original(*args)
                    if not started:
                        started = True
                        threading.Thread(
                            target=write_decision, daemon=True
                        ).start()
                        self.assertTrue(inserted.wait(5))
                    return result

                if surface == "case":
                    original = reader.get_review_case
                    patched = patch.object(
                        reader,
                        "get_review_case",
                        side_effect=lambda *args: interleave(original, *args),
                    )
                elif surface == "library":
                    original = reader.list_current_review_case_documents
                    patched = patch.object(
                        reader,
                        "list_current_review_case_documents",
                        side_effect=lambda *args: interleave(original, *args),
                    )
                else:
                    original = reader.list_current_attention_documents
                    patched = patch.object(
                        reader,
                        "list_current_attention_documents",
                        side_effect=lambda *args: interleave(original, *args),
                    )

                with patched:
                    if surface == "case":
                        before = reader.read_current_review_case(case_id, 50)
                        self.assertEqual(before.decision_documents, ())
                        self.assertFalse(
                            any(
                                item.get("kind") == "human_decision_recorded"
                                for item in before.history
                            )
                        )
                    elif surface == "library":
                        before = reader.read_review_library(None, 10, 0, None)
                        self.assertEqual(before.attention_total, 1)
                        self.assertEqual(before.rows[0][2], ())
                    else:
                        before = reader.read_review_attention(None, 10, 0)
                        self.assertEqual(before.total, 1)
                        self.assertEqual(len(before.rows), 1)

                self.assertTrue(finished.wait(5))
                self.assertEqual(writer_errors, [])
                if surface == "case":
                    after = reader.read_current_review_case(case_id, 50)
                    self.assertEqual(len(after.decision_documents), 1)
                    self.assertTrue(
                        any(
                            item.get("kind") == "human_decision_recorded"
                            for item in after.history
                        )
                    )
                elif surface == "library":
                    after = reader.read_review_library(None, 10, 0, None)
                    self.assertEqual(after.attention_total, 0)
                    self.assertEqual(len(after.rows[0][2]), 1)
                else:
                    after = reader.read_review_attention(None, 10, 0)
                    self.assertEqual(after.total, 0)
                    self.assertEqual(after.rows, ())
                reader.close()

    def test_review_snapshot_failure_rolls_back_without_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
            with patch.object(
                repository,
                "list_current_review_case_documents",
                side_effect=RuntimeError("synthetic"),
            ):
                with self.assertRaises(MetadataReportError):
                    repository.read_review_library(None, 10, 0, None)
            self.assertFalse(repository._connection.in_transaction)
            repository.close()


    def test_sqlite_logout_classification_is_atomic_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            first = open_repository(database, cross_thread=True)
            second = SQLiteMetadataReportRepository(
                sqlite3.connect(
                    database,
                    check_same_thread=False,
                    isolation_level=None,
                )
            )
            first.create_review_session("a" * 64, "c" * 64, 100, 200)
            competing_started = threading.Event()
            competing_outcomes: list[str] = []
            competing_errors: list[BaseException] = []

            def competing_logout() -> None:
                competing_started.set()
                try:
                    competing_outcomes.append(
                        second.revoke_review_session(
                            "a" * 64, "c" * 64, 151
                        )
                    )
                except BaseException as error:
                    competing_errors.append(error)

            competing = threading.Thread(target=competing_logout)

            connection = first._connection

            class InterleavingConnection:
                def execute(self, statement: str, parameters=()):
                    if statement.startswith("SELECT 1 FROM review_sessions"):
                        competing.start()
                        self.assert_competing_started()
                    return connection.execute(statement, parameters)

                def assert_competing_started(self) -> None:
                    if not competing_started.wait(timeout=1):
                        raise AssertionError("competing logout did not start")

                def __getattr__(self, name: str):
                    return getattr(connection, name)

            first._connection = InterleavingConnection()  # type: ignore[assignment]
            self.assertEqual(
                first.revoke_review_session("a" * 64, "d" * 64, 151),
                "csrf",
            )
            competing.join(timeout=2)
            self.assertFalse(competing.is_alive())
            self.assertFalse(competing_errors)
            self.assertEqual(competing_outcomes, ["revoked"])
            first.close()
            second.close()

    def test_pre_decision_application_schema_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            repository = open_repository(database)
            repository.close()
            connection = sqlite3.connect(database)
            try:
                connection.execute("DROP TRIGGER human_decision_events_no_delete")
                connection.execute("DROP TRIGGER human_decision_events_no_update")
                connection.execute("DROP TRIGGER human_decisions_no_delete")
                connection.execute("DROP TRIGGER human_decisions_no_update")
                connection.execute("DROP TABLE human_decision_events")
                connection.execute("DROP TABLE human_decisions")
                connection.commit()
            finally:
                connection.close()
            before = database.read_bytes()
            with self.assertRaises(MetadataReportError):
                open_repository(database, create=False)
            self.assertEqual(database.read_bytes(), before)

    def test_concurrent_http_decisions_preserve_both_immutable_revisions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(
                PasswordHasher().hash("correct horse"), encoding="utf-8"
            )
            os.chmod(hash_path, 0o600)
            repository = open_repository(database, cross_thread=True)
            report = _report()
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            case_id = json.loads(repository.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            client = TestClient(
                create_transport_test_app(
                    repository,
                    ReviewWebConfiguration(
                        canonical_origin="https://review.test",
                        owner_password_hash_file=str(hash_path),
                    ),
                    clock=lambda: 1_000,
                ),
                base_url="https://review.test",
            )
            try:
                login = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                ).json()
                field = client.get(
                    f"/api/v1/review-cases/{case_id}"
                ).json()["fields"][0]
                base_payload = {
                    "destination": field["destination"],
                    "proposal_id": field["proposal_id"],
                    "selected_value": None,
                    "target_fingerprint": field["target_fingerprint"],
                }

                def submit(value: tuple[str, str]) -> int:
                    outcome, key = value
                    return client.post(
                        f"/api/v1/review-cases/{case_id}/decisions",
                        headers={
                            "Origin": "https://review.test",
                            "X-CSRF-Token": login["csrf_token"],
                            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
                            "Idempotency-Key": key,
                        },
                        json={**base_payload, "outcome": outcome},
                    ).status_code

                with ThreadPoolExecutor(max_workers=2) as executor:
                    statuses = sorted(
                        executor.map(
                            submit,
                            (
                                ("keep_current", "concurrent-decision-0001"),
                                ("reject", "concurrent-decision-0002"),
                            ),
                        )
                    )
                self.assertEqual(statuses, [200, 200])
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decisions"
                    ).fetchone(),
                    (2,),
                )
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decision_events"
                    ).fetchone(),
                    (2,),
                )
                library = client.get(
                    "/api/v1/library?lens=no_attention_needed"
                )
                self.assertEqual(library.status_code, 200, library.text)
                self.assertEqual(
                    library.json()["counts"],
                    {
                        "all": 1,
                        "needs_attention": 0,
                        "no_attention_needed": 1,
                    },
                )
                self.assertEqual(len(library.json()["items"]), 1)
                self.assertEqual(
                    library.json()["items"][0]["attention_state"],
                    "no_attention_needed",
                )
                self.assertEqual(
                    library.json()["items"][0]["attention_count"], 0
                )
                client.cookies.clear()
                second_login = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                )
                self.assertEqual(second_login.status_code, 200)
                second_context = client.get(
                    f"/api/v1/review-cases/{case_id}"
                ).json()
                self.assertIsNotNone(
                    second_context["fields"][0]["human_decision"]
                )
            finally:
                client.close()
                repository.close()

    def test_decision_event_failure_rolls_back_acknowledgement_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
            report = _report()
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            case_id = json.loads(repository.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            proposal = next(
                value
                for value in case.proposals
                if value["result"]["outcome"] == "unresolved_choice"
            )
            decision = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="rollback-decision-0001",
                occurred_at=1_000,
            )
            repository._connection.execute(
                "CREATE TEMP TRIGGER synthetic_event_failure "
                "BEFORE INSERT ON human_decision_events "
                "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
            )
            with self.assertRaises(MetadataReportError):
                repository.record_human_decision(decision)
            self.assertEqual(
                repository._connection.execute(
                    "SELECT count(*) FROM human_decisions"
                ).fetchone(),
                (0,),
            )
            repository.close()

    def test_sqlite_human_decision_is_atomic_idempotent_and_reopens_exactly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            repository = open_repository(database)
            report = _report()
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            case_id = json.loads(repository.admit_review_case(proposal_set))[
                "review_case_id"
            ]
            case = ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            )
            proposal = next(
                value
                for value in case.proposals
                if value["result"]["outcome"] == "unresolved_choice"
            )
            selected = proposal["result"]["alternatives"][0]["value"]
            decision = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="accept",
                selected_value=selected,
                idempotency_key="sqlite-decision-0001",
                occurred_at=1_000,
            )
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(decision)
                ).decision_id,
                decision.decision_id,
            )
            self.assertEqual(
                HumanDecision.from_json(
                    repository.find_human_decision(
                        decision.idempotency_digest
                    )
                    or ""
                ).decision_id,
                decision.decision_id,
            )
            retry = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="accept",
                selected_value=selected,
                idempotency_key="sqlite-decision-0001",
                occurred_at=1_001,
            )
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(retry)
                ).decision_id,
                decision.decision_id,
            )
            conflict = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="reject",
                selected_value=None,
                idempotency_key="sqlite-decision-0001",
                occurred_at=1_002,
            )
            with self.assertRaises(HumanDecisionError) as raised:
                repository.record_human_decision(conflict)
            self.assertEqual(raised.exception.code, "idempotency_conflict")
            duplicate = HumanDecision.create(
                case,
                proposal_id=proposal["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=proposal["destination"],
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="sqlite-decision-0002",
                occurred_at=1_003,
            )
            self.assertEqual(
                HumanDecision.from_json(
                    repository.record_human_decision(duplicate)
                ).decision_id,
                duplicate.decision_id,
            )
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            self.assertEqual(
                repository.get_review_case_history(case_id, 10)[0]["kind"],
                "human_decision_recorded",
            )
            repository.close()

            reopened = open_repository(database, create=False)
            try:
                documents = reopened.get_review_case_decisions(case_id)
                self.assertEqual(len(documents), 2)
                self.assertEqual(
                    HumanDecision.from_json(documents[0]).decision_id,
                    decision.decision_id,
                )
                self.assertEqual(
                    HumanDecision.from_json(documents[1]).decision_id,
                    duplicate.decision_id,
                )
                self.assertEqual(
                    HumanDecision.from_json(
                        reopened.find_human_decision(
                            decision.idempotency_digest
                        )
                        or ""
                    ).decision_id,
                    decision.decision_id,
                )
                detail = InspectCurrentReviewCase(reopened).run(case_id)
                self.assertFalse(detail["fields"][0]["needs_attention"])
                newer = _report(content_digest="c", file_identity="d")
                reopened.publish(newer, lambda: True)
                replayed = RecordHumanDecision(reopened, lambda: 2_000).run(
                    case_id,
                    proposal_id=proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination=proposal["destination"],
                    actor="owner",
                    outcome="accept",
                    selected_value=selected,
                    idempotency_key="sqlite-decision-0001",
                )
                self.assertEqual(replayed["decision_id"], decision.decision_id)
                self.assertEqual(
                    reopened._connection.execute(
                        "SELECT (SELECT count(*) FROM human_decisions),"
                        "(SELECT count(*) FROM human_decision_events)"
                    ).fetchone(),
                    (2, 2),
                )
                with self.assertRaises(ReviewWebError) as replay_conflict:
                    RecordHumanDecision(reopened, lambda: 2_001).run(
                        case_id,
                        proposal_id=proposal["proposal_id"],
                        target_fingerprint=case.target_fingerprint,
                        destination=proposal["destination"],
                        actor="owner",
                        outcome="reject",
                        selected_value=None,
                        idempotency_key="sqlite-decision-0001",
                    )
                self.assertEqual(
                    replay_conflict.exception.code, "idempotency_conflict"
                )
                stale_decision = HumanDecision.create(
                    case,
                    proposal_id=proposal["proposal_id"],
                    target_fingerprint=case.target_fingerprint,
                    destination=proposal["destination"],
                    actor="owner",
                    outcome="reject",
                    selected_value=None,
                    idempotency_key="sqlite-decision-0003",
                    occurred_at=2_002,
                )
                with self.assertRaises(HumanDecisionError) as stale:
                    reopened.record_human_decision(stale_decision)
                self.assertEqual(stale.exception.code, "stale")
            finally:
                reopened.close()

    def test_session_survives_exact_current_reopen_and_prior_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "application.sqlite3"
            repository = open_repository(path)
            repository.create_review_session("a" * 64, "b" * 64, 100, 200)
            repository.close()
            reopened = open_repository(path, create=False)
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 150),
                ("owner", "b" * 64, 200),
            )
            self.assertEqual(
                reopened.rotate_review_session_csrf("a" * 64, "c" * 64, 150),
                "rotated",
            )
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 150),
                ("owner", "c" * 64, 200),
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "d" * 64, 151),
                "csrf",
            )
            self.assertEqual(
                reopened.authenticate_review_session("a" * 64, 151),
                ("owner", "c" * 64, 200),
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "c" * 64, 151),
                "revoked",
            )
            self.assertEqual(
                reopened.revoke_review_session("a" * 64, "c" * 64, 151),
                "unauthorized",
            )
            reopened.create_review_session("e" * 64, "f" * 64, 100, 200)
            self.assertEqual(
                reopened.revoke_review_session("e" * 64, "f" * 64, 200),
                "unauthorized",
            )
            with self.assertRaises(Exception):
                reopened.authenticate_review_session("a" * 64, 152)
            self.assertEqual(
                reopened.rotate_review_session_csrf("a" * 64, "d" * 64, 152),
                "unauthorized",
            )
            reopened.close()

    def test_real_repository_serves_login_from_fastapi_worker_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            repository = open_repository(database, cross_thread=True)
            client = TestClient(
                create_transport_test_app(
                    repository,
                    ReviewWebConfiguration(
                        canonical_origin="https://review.test",
                        owner_password_hash_file=str(hash_path),
                    ),
                ),
                base_url="https://review.test",
            )
            try:
                response = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                )
                self.assertEqual(response.status_code, 200, response.text)
            finally:
                client.close()
                repository.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_authenticated_preparation_status_survives_writer_journal_churn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "application.sqlite3"
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            repository = open_repository(database, cross_thread=True)
            repository.request_review_scan(
                "primary", "fiction", "lib-1", "f" * 64, 1
            )
            repository.set_review_pipeline_paused(True, 2)
            authority = repository.authority
            self.assertIsNotNone(authority)
            assert authority is not None
            control_started = threading.Event()
            release_writer = threading.Event()
            commit_attempted = threading.Event()
            commit_completed = threading.Event()
            trace_errors: list[BaseException] = []

            def open_status_repository():
                reader = open_repository(database, authority=authority)

                def trace(statement: str) -> None:
                    if statement.startswith(
                        "SELECT paused FROM review_preparation_control"
                    ):
                        control_started.set()
                        release_writer.set()
                        try:
                            if not commit_attempted.wait(5):
                                trace_errors.append(
                                    AssertionError(
                                        "writer did not attempt commit during status read"
                                    )
                                )
                        finally:
                            reader._connection.set_trace_callback(None)

                reader._connection.set_trace_callback(trace)
                return reader

            preparation = ReviewPreparationPipeline(
                repository_factory=open_status_repository,
                libraries=(("primary", "fiction", "lib-1", "f" * 64),),
                discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
                extract=lambda *_: (_ for _ in ()).throw(AssertionError()),
                providers=lambda *_: (_ for _ in ()).throw(AssertionError()),
                propose=lambda *_: (_ for _ in ()).throw(AssertionError()),
                admit=lambda *_: (_ for _ in ()).throw(AssertionError()),
                project=lambda *_: (_ for _ in ()).throw(AssertionError()),
            )
            client = TestClient(
                create_transport_test_app(
                    repository,
                    ReviewWebConfiguration(
                        canonical_origin="https://review.test",
                        owner_password_hash_file=str(hash_path),
                    ),
                    review_preparation=preparation,
                ),
                base_url="https://review.test",
            )
            writer_errors: list[BaseException] = []

            def churn_journal_descriptors() -> None:
                writer_repository = None
                try:
                    writer_repository = open_repository(
                        database, authority=authority
                    )
                    for updated_at in range(3, 28):
                        if not release_writer.wait(5):
                            self.fail("authenticated status read did not release writer churn")
                        release_writer.clear()
                        paused = updated_at % 2 == 1
                        state = "queued" if paused else "discovering"
                        writer_repository._connection.execute("BEGIN IMMEDIATE")
                        writer_repository._connection.execute(
                            "UPDATE review_preparation_control "
                            "SET paused=?,updated_at=? WHERE singleton=1",
                            (paused, updated_at),
                        )
                        writer_repository._connection.execute(
                            "UPDATE review_preparation_scans "
                            "SET state=?,updated_at=? WHERE connection_name='primary' "
                            "AND library_name='fiction'",
                            (state, updated_at),
                        )
                        commit_attempted.set()
                        writer_repository._connection.execute("COMMIT")
                        commit_completed.set()
                except BaseException as error:
                    writer_errors.append(error)
                finally:
                    if writer_repository is not None:
                        writer_repository.close()

            writer = threading.Thread(target=churn_journal_descriptors)
            try:
                login = client.post(
                    "/api/v1/session/login",
                    headers={"Origin": "https://review.test"},
                    json={"password": "correct horse"},
                )
                self.assertEqual(login.status_code, 200, login.text)
                client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
                writer.start()
                for _ in range(25):
                    control_started.clear()
                    commit_attempted.clear()
                    commit_completed.clear()
                    status = client.get("/api/v1/review-preparation")
                    self.assertEqual(status.status_code, 200, status.text)
                    self.assertTrue(control_started.is_set())
                    self.assertTrue(commit_attempted.is_set())
                    payload = status.json()
                    self.assertIn(
                        (payload["paused"], payload["libraries"][0]["state"]),
                        ((True, "queued"), (False, "discovering")),
                    )
                    self.assertEqual(payload["libraries"][0]["connection"], "primary")
                    self.assertEqual(payload["libraries"][0]["library"], "fiction")
                    self.assertTrue(commit_completed.wait(5))
            finally:
                release_writer.set()
                writer.join(5)
                client.close()
                repository.close()
            self.assertFalse(writer.is_alive())
            self.assertEqual(writer_errors, [])
            self.assertEqual(trace_errors, [])

    def test_sqlite_library_and_attention_queries_are_current_and_bounded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            repository = open_repository(database)
            source_report = _report(title="Straße ÅNGSTRÖM")
            unresolved_report = MetadataReport.create(
                target=TargetFacts(
                    source_report.target.connection,
                    source_report.target.library,
                    source_report.target.item,
                    tuple(
                        TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.VALUE,
                            ("Ada Hostile Search",),
                        )
                        if field.destination is TargetDestination.AUTHORS
                        else field
                        for field in source_report.target.fields
                    ),
                ),
                file=source_report.file,
                local_claims=source_report.local_claims,
                local_reasons=source_report.local_reasons,
                providers=source_report.providers,
                cover_sources=source_report.cover_sources,
            )
            repository.publish(unresolved_report, lambda: True)
            unresolved_proposal = evaluate_report(unresolved_report)
            repository.publish_proposal(unresolved_proposal)
            unresolved_case_id = json.loads(
                repository.admit_review_case(unresolved_proposal)
            )["review_case_id"]

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
            source = _proposal_report(providers=providers)
            automatic_report = MetadataReport.create(
                target=TargetFacts(
                    "primary", "novels", "ÉDITION-2", source.target.fields
                ),
                file=FileFacts(1, "c" * 64, "d" * 64),
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=source.providers,
                cover_sources=source.cover_sources,
            )
            repository.publish(automatic_report, lambda: True)
            automatic_proposal = evaluate_report(automatic_report)
            repository.publish_proposal(automatic_proposal)
            repository.admit_review_case(automatic_proposal)

            self.assertEqual(repository.count_current_review_cases(None), (2, 1))
            attention_rows = repository.list_current_review_case_documents(
                None, 10, 0, True
            )
            self.assertEqual(len(attention_rows), 1)
            self.assertEqual(
                unresolved_case_id,
                ReviewCase.from_json(attention_rows[0][0]).review_case_id,
            )
            automatic_rows = repository.list_current_review_case_documents(
                "édition-2", 10, 0, False
            )
            self.assertEqual(len(automatic_rows), 1)
            self.assertEqual(repository.count_current_review_cases("ångström"), (1, 1))
            self.assertEqual(repository.count_current_review_cases("STRASSE"), (1, 1))
            self.assertEqual(repository.count_current_review_cases("NOVELS"), (1, 0))
            self.assertEqual(repository.count_current_review_cases("ada hostile"), (1, 1))
            self.assertEqual(repository.count_current_review_cases("EPUB"), (2, 1))
            self.assertEqual(repository.count_current_review_cases("%"), (0, 0))
            self.assertEqual(repository.count_current_review_cases("_"), (0, 0))
            self.assertEqual(repository.count_current_review_cases("\\"), (0, 0))
            queue = repository.list_current_attention_documents(
                None, 10, 0
            )
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0][2], "title")
            self.assertEqual(repository.count_current_attention_fields(None), 1)
            repository.suspend_field_effect_capability(
                scope="destination", connection="primary", destination="title",
                reason="field_unavailable", changed_at=1,
            )
            self.assertEqual(repository.count_current_review_cases(None), (2, 0))
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            self.assertEqual(
                repository.list_current_attention_documents(None, 10, 0), ()
            )
            repository.clear_field_effect_capability_suspension(
                scope="destination", connection="primary", destination="title",
                reason="field_unavailable", observed_at=2,
            )
            self.assertEqual(repository.count_current_review_cases(None), (2, 1))
            repository.suspend_field_effect_capability(
                scope="connection", connection="primary", destination="title",
                reason="connection_unavailable", changed_at=2,
            )
            self.assertEqual(repository.count_current_review_cases(None), (2, 0))
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            repository.clear_field_effect_capability_suspension(
                scope="connection", connection="primary", destination="",
                reason="connection_unavailable", observed_at=3,
            )
            self.assertEqual(repository.count_current_review_cases(None), (2, 1))
            self.assertEqual(
                repository.count_current_attention_fields("STRASSE"),
                1,
            )
            self.assertEqual(
                len(repository.list_current_attention_documents("ångström", 10, 0)),
                1,
            )
            repository.suspend_field_effect_capability(
                scope="destination", connection="primary", destination="title",
                reason="insufficient_permission", changed_at=4,
            )
            repository.close()
            reopened = open_repository(database, create=False)
            self.assertEqual(
                reopened.field_effect_capability_suspension("primary", "title"),
                "insufficient_permission",
            )
            self.assertEqual(reopened.count_current_review_cases("ångström"), (1, 0))
            self.assertEqual(reopened.count_current_review_cases("STRASSE"), (1, 0))
            self.assertEqual(reopened.count_current_review_cases("NOVELS"), (1, 0))
            reopened.close()

    def test_sqlite_attention_keeps_manual_bibliographic_membership_field_local(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
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
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
            first = repository.list_current_attention_documents(None, 1, 0)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0][3], 1)
            rows = repository.list_current_attention_documents(None, 10, 0)
            self.assertEqual(
                [row[2] for row in rows],
                ["series"],
            )
            self.assertEqual(repository.count_current_attention_fields(None), 1)
            self.assertEqual(repository.count_current_review_cases(None), (1, 1))
            repository.suspend_field_effect_capability(
                scope="destination", connection="primary", destination="series",
                reason="field_unavailable", changed_at=1,
            )
            suspended = InspectCurrentReviewCase(repository).run(
                json.loads(repository.admit_review_case(proposal))["review_case_id"]
            )
            field = next(value for value in suspended["fields"] if value["destination"] == "series")
            self.assertEqual("This field is waiting for an external service recovery.", field["classification_reason"])
            self.assertEqual(
                [row[2] for row in repository.list_current_attention_documents(None, 1, 0)],
                [],
            )
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            repository.clear_field_effect_capability_suspension(
                scope="destination", connection="primary", destination="series",
                reason="field_unavailable", observed_at=2,
            )
            repository.close()

    def test_sqlite_complete_library_keeps_current_no_case_and_retired_work(self) -> None:
        def for_item(source: MetadataReport, item: str) -> MetadataReport:
            return MetadataReport.create(
                target=TargetFacts(
                    source.target.connection,
                    source.target.library,
                    item,
                    source.target.fields,
                ),
                file=source.file,
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=source.providers,
                cover_sources=source.cover_sources,
            )

        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
            no_case = for_item(
                _report("No-case work", "1", "2", actionable=False),
                "no-case-item",
            )
            repository.publish(no_case, lambda: True)
            no_case_proposal = evaluate_report(no_case)
            repository.publish_proposal(no_case_proposal)
            self.assertEqual(
                json.loads(repository.admit_review_case(no_case_proposal))["status"],
                "no_case",
            )

            actionable = for_item(
                _report("Former case", "3", "4"), "retired-item"
            )
            repository.publish(actionable, lambda: True)
            actionable_proposal = evaluate_report(actionable)
            repository.publish_proposal(actionable_proposal)
            repository.admit_review_case(actionable_proposal)
            retired = for_item(
                _report("Retired work", "5", "6", actionable=False),
                "retired-item",
            )
            repository.publish(retired, lambda: True)
            retired_proposal = evaluate_report(retired)
            repository.publish_proposal(retired_proposal)
            self.assertEqual(
                json.loads(repository.admit_review_case(retired_proposal))["status"],
                "retired",
            )
            in_progress = for_item(
                _report("In-progress report", "7", "8"),
                "in-progress-item",
            )
            repository.publish(in_progress, lambda: True)

            self.assertEqual(repository.count_current_review_cases(None), (3, 0))
            self.assertEqual(repository.count_current_review_cases("No-case"), (1, 0))
            self.assertEqual(
                repository.count_current_review_cases("In-progress"), (1, 0)
            )
            rows = repository.list_current_review_case_documents(
                None, 10, 0, False
            )
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(case_document is None for case_document, _ in rows))
            self.assertEqual(
                repository.list_current_attention_documents(None, 10, 0), ()
            )
            repository.close()

    def test_sqlite_library_order_is_total_across_duplicate_work_identity(self) -> None:
        def for_connection(source: MetadataReport, connection: str) -> MetadataReport:
            return MetadataReport.create(
                target=TargetFacts(
                    connection,
                    source.target.library,
                    source.target.item,
                    source.target.fields,
                ),
                file=source.file,
                local_claims=source.local_claims,
                local_reasons=source.local_reasons,
                providers=source.providers,
                cover_sources=source.cover_sources,
            )

        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "application.sqlite3")
            source = _report("Duplicate title", "1", "2", actionable=False)
            for connection in ("zeta", "alpha"):
                repository.publish(for_connection(source, connection), lambda: True)

            pages = [
                repository.list_current_review_case_documents(None, 1, offset, None)
                for offset in (0, 1)
            ]
            connections = [
                MetadataReport.from_json(page[0][1]).target.connection
                for page in pages
            ]
            self.assertEqual(connections, ["alpha", "zeta"])
            self.assertEqual(len(set(connections)), 2)
            self.assertEqual(
                repository.list_current_review_case_documents(None, 1, 0, None),
                pages[0],
            )
            repository.close()


if __name__ == "__main__":
    unittest.main()
