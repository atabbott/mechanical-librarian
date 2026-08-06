from __future__ import annotations

import hashlib
import json
import os
import tempfile
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
from mechanical_librarian.interfaces.cli import main as cli_main
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web import create_review_app
from tests.unit.test_review_case_persistence import _report


class _Repository:
    def __init__(self, case: ReviewCase) -> None:
        self.case = case
        self.advice: dict[str, object] = {
            "results": [],
            "state": "not_requested",
        }
        self.failures: list[int] = []
        self.sessions: dict[str, tuple[str, str, int, int | None]] = {}

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

    def revoke_review_session(self, session_digest: str, now: int) -> None:
        actor, csrf, expires, _ = self.sessions[session_digest]
        self.sessions[session_digest] = (actor, csrf, expires, now)

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

    def get_review_case_report(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return _report().to_json()

    def get_review_model_advice(self, review_case_id: str) -> dict[str, object]:
        return self.advice


class ReviewWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.hash_path = root / "owner.hash"
        self.hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
        os.chmod(self.hash_path, 0o600)
        report = _report()
        self.case = ReviewCase.create(evaluate_report(report), report)
        self.repository = _Repository(self.case)
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
        self.assertEqual(detail.json()["model_advice"]["state"], "not_requested")
        evidence = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}/evidence")
        history = self.client.get(f"/api/v1/review-cases/{self.case.review_case_id}/history")
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.json()["items"][0]["source"], "local")
        self.assertIn("Exact evaluator candidate", evidence.json()["items"][0]["fact"])
        self.assertEqual(history.json()["items"][0]["kind"], "admitted")
        self.assertIn("default-src 'none'", detail.headers["content-security-policy"])
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
            reopened.revoke_review_session("a" * 64, 151)
            with self.assertRaises(Exception):
                reopened.authenticate_review_session("a" * 64, 152)
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


if __name__ == "__main__":
    unittest.main()
