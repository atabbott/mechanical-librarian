from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
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
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    InspectCurrentReviewCase,
    RecordHumanDecision,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ReviewWebError,
    _decode_cursor,
    _encode_cursor,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.interfaces.cli import main as cli_main
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.interfaces.web import create_review_app
from tests.unit.test_review_case_persistence import _report
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

    def record_human_decision(self, decision: HumanDecision) -> str:
        existing = self.decisions.get(decision.destination)
        if existing is not None:
            return existing
        self.decisions[decision.destination] = decision.to_json()
        return decision.to_json()

    def get_review_case_report(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return self.report.to_json()

    def get_review_model_advice(self, review_case_id: str) -> dict[str, object]:
        return self.advice

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        return CurrentReviewCaseSnapshot(
            case_document=self.get_review_case(review_case_id),
            report_document=self.get_review_case_report(review_case_id),
            model_advice=self.get_review_model_advice(review_case_id),
            decision_documents=self.get_review_case_decisions(review_case_id),
            history=(
                ()
                if history_limit is None
                else self.get_review_case_history(review_case_id, history_limit)
            ),
        )

    @staticmethod
    def _attention(case: ReviewCase) -> bool:
        return any(
            proposal["result"]["outcome"] == "unresolved_choice"
            or proposal["manual_required"] is True
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
                    report.target.library,
                    report.target.item,
                )
            ).casefold()
        ]

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str, str], ...]:
        rows = [
            (case, report)
            for case, report in self._searched(search)
            if attention is None or self._attention(case) is attention
        ]
        rows.sort(
            key=lambda value: (
                not self._attention(value[0]),
                (value[1].target.current_title or "").casefold(),
                value[0].review_case_id,
            )
        )
        return tuple(
            (case.to_json(), report.to_json())
            for case, report in rows[offset : offset + limit]
        )

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        rows = self._searched(search)
        return len(rows), sum(self._attention(case) for case, _ in rows)

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
                    self.get_review_case_decisions(
                        ReviewCase.from_json(case_document).review_case_id
                    ),
                )
                for case_document, report_document in rows
            ),
            total=total,
            attention_total=attention_total,
        )

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str], ...]:
        rows = [
            (case.to_json(), report.to_json(), proposal["destination"])
            for case, report in self._searched(search)
            for proposal in case.proposals
            if proposal["result"]["outcome"] == "unresolved_choice"
            or proposal["manual_required"] is True
        ]
        return tuple(rows[offset : offset + limit])

    def count_current_attention_fields(self, search: str | None) -> int:
        return sum(
            1
            for case, _ in self._searched(search)
            for proposal in case.proposals
            if proposal["result"]["outcome"] == "unresolved_choice"
            or proposal["manual_required"] is True
        )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        return ReviewAttentionSnapshot(
            rows=self.list_current_attention_documents(search, limit, offset),
            total=self.count_current_attention_fields(search),
        )


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
            create_review_app(
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
        return response.json()

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
        self.assertEqual(detail.json()["model_advice"]["state"], "not_requested")
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
            "fields": [
                {"provenance": sorted(references.difference({rich_reference}))}
            ],
            "model_advice": {
                "results": [
                    {
                        "recommendations": [
                            {"citations": [rich_reference]}
                        ]
                    }
                ]
            },
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
            changed_detail = {
                **detail,
                "model_advice": {"results": []},
            }
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
        page = self.client.get(f"/review/{self.case.review_case_id}")
        self.assertEqual(page.status_code, 200, page.text)
        match = re.search(r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', page.text)
        self.assertIsNotNone(match)
        assert match is not None
        browser_csrf = match.group(1)
        self.assertNotEqual(browser_csrf, login["csrf_token"])
        script = self.client.get("/assets/review.js").text
        self.assertIn("removeAttribute('data-csrf-token')", script)
        self.assertIn("No attention needed", script)
        self.assertNotIn("Automatically handled", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
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
        schemas = expected["components"]["schemas"]
        self.assertNotIn("HTTPValidationError", schemas)
        self.assertTrue(
            all(
                schema.get("additionalProperties") is False
                for schema in schemas.values()
                if schema.get("type") == "object"
            )
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
                ("get", "/api/v1/attention"),
                ("get", "/api/v1/library"),
                ("get", "/api/v1/review-cases/{review_case_id}"),
                ("get", "/api/v1/review-cases/{review_case_id}/evidence"),
                ("get", "/api/v1/review-cases/{review_case_id}/history"),
                ("post", "/api/v1/review-cases/{review_case_id}/decisions"),
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
            {"204", "400", "401", "403", "422", "503"},
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
        script = self.client.get("/assets/review.js").text
        self.assertIn("/evidence", script)
        self.assertIn("Load more evidence", script)
        self.assertIn("encodeURIComponent(cursor)", script)
        self.assertIn("/history", script)
        self.assertIn("textContent", script)
        self.assertNotIn("innerHTML", script)
        self.assertIn("item.kind==='human_decision_recorded'", script)
        self.assertIn("${item.destination}: ${item.outcome} by ${item.actor}", script)

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
        self.assertIn("?field=title", queue.json()["items"][0]["detail_url"])
        self.assertNotIn(
            automatic_case.review_case_id,
            json.dumps(queue.json()),
        )

        self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.client.get("/attention").status_code, 200)
        script = self.client.get("/assets/review.js").text
        for required in (
            "needs_attention",
            "no_attention_needed",
            "CSS.escape",
            "details",
            "location.search",
            "location.assign('/library')",
        ):
            self.assertIn(required, script)

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

    def test_model_advice_is_separate_bounded_nondecision_data(self) -> None:
        self.repository.advice = {
            "results": [
                {
                    "recommendations": [
                        {
                            "candidate_id": None,
                            "citations": ["evidence_" + "a" * 64],
                            "problems": ["conflicting editions"],
                            "proposal_id": self.case.proposals[0]["proposal_id"],
                            "reasons": ["manual comparison required"],
                            "recommendation": "manual",
                        }
                    ],
                    "review_request_id": "review_request_" + "b" * 64,
                }
            ],
            "state": "available",
        }
        self._login()
        response = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        advice = response.json()["model_advice"]
        self.assertEqual(advice["state"], "available")
        self.assertEqual(
            advice["results"][0]["recommendations"][0]["recommendation"],
            "manual",
        )
        self.assertNotIn("human_decision", advice)

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

    def test_configuration_and_cli_keep_review_authority_explicit(self) -> None:
        with self.assertRaises(Exception):
            ReviewWebConfiguration(
                canonical_origin="http://review.test",
                owner_password_hash_file=str(self.hash_path),
            )
        calls: list[tuple[Path, Path, str, int]] = []
        output = StringIO()
        errors = StringIO()
        result = cli_main(
            [
                "review-serve",
                "--config",
                "/config.json",
                "--database",
                "/application.sqlite3",
                "--host",
                "0.0.0.0",
                "--port",
                "8443",
            ],
            review_serve_runner=lambda *values: calls.append(values),
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(result, 0, errors.getvalue())
        self.assertEqual(
            calls,
            [(Path("/config.json"), Path("/application.sqlite3"), "0.0.0.0", 8443)],
        )

    def test_composition_opens_the_single_database_for_worker_threads(self) -> None:
        from tests.support.tls import synthetic_tls_identity

        repository = Mock()
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
            patch("mechanical_librarian.bootstrap.composition.uvicorn.run"),
        ):
            run_review_server(
                Path("/config.json"),
                Path("/application.sqlite3"),
                "127.0.0.1",
                8443,
            )
        opened.assert_called_once_with(
            Path("/application.sqlite3"),
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
            create_review_app(self.repository, configuration)


class ReviewSessionPersistenceTests(unittest.TestCase):
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

    def test_concurrent_http_decisions_publish_exactly_one_fact_and_event(
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
                create_review_app(
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
                self.assertEqual(statuses, [200, 409])
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decisions"
                    ).fetchone(),
                    (1,),
                )
                self.assertEqual(
                    repository._connection.execute(
                        "SELECT count(*) FROM human_decision_events"
                    ).fetchone(),
                    (1,),
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
            with self.assertRaises(HumanDecisionError) as already:
                repository.record_human_decision(duplicate)
            self.assertEqual(already.exception.code, "already_decided")
            self.assertEqual(repository.count_current_attention_fields(None), 0)
            self.assertEqual(
                repository.get_review_case_history(case_id, 10)[0]["kind"],
                "human_decision_recorded",
            )
            repository.close()

            reopened = open_repository(database, create=False)
            try:
                documents = reopened.get_review_case_decisions(case_id)
                self.assertEqual(len(documents), 1)
                self.assertEqual(
                    HumanDecision.from_json(documents[0]).decision_id,
                    decision.decision_id,
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
                    (1, 1),
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
                with self.assertRaises(HumanDecisionError) as stale:
                    reopened.record_human_decision(duplicate)
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
                create_review_app(
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

    def test_sqlite_library_and_attention_queries_are_current_and_bounded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            repository = open_repository(database)
            unresolved_report = _report(title="Straße ÅNGSTRÖM")
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
            self.assertEqual(repository.count_current_review_cases("%"), (0, 0))
            self.assertEqual(repository.count_current_review_cases("_"), (0, 0))
            self.assertEqual(repository.count_current_review_cases("\\"), (0, 0))
            queue = repository.list_current_attention_documents(
                None, 10, 0
            )
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0][2], "title")
            self.assertEqual(repository.count_current_attention_fields(None), 1)
            self.assertEqual(
                repository.count_current_attention_fields("STRASSE"),
                1,
            )
            self.assertEqual(
                len(repository.list_current_attention_documents("ångström", 10, 0)),
                1,
            )
            repository.close()
            reopened = open_repository(database, create=False)
            self.assertEqual(reopened.count_current_review_cases("ångström"), (1, 1))
            self.assertEqual(reopened.count_current_review_cases("STRASSE"), (1, 1))
            self.assertEqual(reopened.count_current_review_cases("NOVELS"), (1, 0))
            reopened.close()

    def test_sqlite_attention_includes_coherent_manual_bibliographic_siblings(
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

            rows = repository.list_current_attention_documents(None, 10, 0)
            self.assertEqual(
                [row[2] for row in rows],
                ["series", "subtitle", "title"],
            )
            self.assertEqual(repository.count_current_attention_fields(None), 3)
            self.assertEqual(repository.count_current_review_cases(None), (1, 1))
            repository.close()


if __name__ == "__main__":
    unittest.main()
