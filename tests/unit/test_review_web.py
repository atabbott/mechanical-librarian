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

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    RecordHumanDecision,
    ReviewWebError,
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
        self.existence_failure = False

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
    ) -> None:
        actor, _, expires, revoked = self.sessions[session_digest]
        if revoked is not None or expires <= now:
            raise RuntimeError
        self.sessions[session_digest] = (actor, csrf_digest, expires, None)

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
                "current": True,
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
        repository = Mock()
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
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

    def test_sqlite_logout_classification_is_atomic_across_connections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "application.sqlite3"
            first = open_repository(database, cross_thread=True)
            second = open_repository(database, cross_thread=True)
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
                detail = InspectCurrentReviewCase(reopened).run(case_id)
                self.assertFalse(detail["fields"][0]["needs_attention"])
                newer = _report(content_digest="c", file_identity="d")
                reopened.publish(newer, lambda: True)
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
            self.assertIsNone(
                reopened.rotate_review_session_csrf("a" * 64, "c" * 64, 150)
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
            with self.assertRaises(Exception):
                reopened.rotate_review_session_csrf("a" * 64, "d" * 64, 152)
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
            repository = open_repository(Path(directory) / "application.sqlite3")
            unresolved_report = _report()
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
                    "primary", "ebooks", "item-2", source.target.fields
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
                "item-2", 10, 0, False
            )
            self.assertEqual(len(automatic_rows), 1)
            queue = repository.list_current_attention_documents(
                None, 10, 0
            )
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0][2], "title")
            self.assertEqual(repository.count_current_attention_fields(None), 1)
            repository.close()

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
