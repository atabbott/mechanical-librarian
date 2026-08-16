"""Review web transport: anonymous surface, authentication, CSRF
rotation, bounded bodies, the generated OpenAPI document, and the
served composition.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from argon2 import PasswordHasher
from argon2.low_level import Type
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.operational_evidence import (
    LocalTimingStore,
    SupportBundle,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_web import (
    ReviewWebError,
)
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
)
from tests.support.review_web_doubles import (
    ReviewWebTestCase,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)


class ReviewWebTransportTests(ReviewWebTestCase):
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

    def test_host_header_cannot_relabel_a_routed_path_as_a_health_probe(self) -> None:
        # `request.url` is reconstructed from the `Host` header, so a header
        # carrying a path plus a `#` terminator reads as an exact health path
        # on a request the router dispatched somewhere else entirely
        # (GHSA-86qp-5c8j-p5mr). The trust boundary exempts the two health
        # endpoints from origin validation, so a decision taken on that
        # reconstructed value skips origin and CSRF checking for the real
        # route. Every path-based decision reads the routed ASGI path instead.
        poisoned = {"Host": "review.test/health/live#"}
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        read = self.client.get(
            f"/api/v1/review-cases/{self.case.review_case_id}", headers=poisoned
        )
        self.assertEqual(read.status_code, 400)
        self.assertEqual(read.json()["error"]["code"], "invalid_origin")
        # Login is the one mutating route with no CSRF-token requirement of its
        # own, so the origin check is the whole of its cross-site defence.
        forged_login = self.client.post(
            "/api/v1/session/login",
            headers=poisoned,
            json={"password": "correct horse"},
        )
        self.assertEqual(forged_login.status_code, 400)
        self.assertEqual(forged_login.json()["error"]["code"], "invalid_origin")
        self.assertIsNone(self.client.cookies.get("ml_session"))
        self._login()
        page = self.client.get("/library")
        csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
        assert csrf is not None
        del self.client.headers["x-ml-build-identity"]
        forged_logout = self.client.post(
            "/api/v1/session/logout",
            headers={**poisoned, "X-CSRF-Token": csrf.group(1)},
        )
        self.assertEqual(forged_logout.status_code, 400)
        self.assertEqual(forged_logout.json()["error"]["code"], "invalid_origin")
        self.assertEqual(self.client.get("/library").status_code, 200)

    def test_an_encoded_fragment_cannot_borrow_the_health_exemption(self) -> None:
        # `%23` decodes into the routed ASGI path but still terminates the
        # reconstructed URL's path component, so `/health/live%23x` routes
        # nowhere while `request.url.path` reads as the health endpoint. The
        # divergence here comes from the request path rather than the `Host`
        # header, so no amount of header validation in the framework closes
        # it and this keeps the routed-path decision pinned on its own.
        for path in ("/health/live%23x", "/health/live%3Fx"):
            refused = self.client.get(path, headers={"Host": "elsewhere.invalid"})
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertEqual(refused.json()["error"]["code"], "invalid_origin")
        probe = self.client.get("/health/live", headers={"Host": "elsewhere.invalid"})
        self.assertEqual(probe.status_code, 200)
        self.assertEqual(probe.text, "ok\n")

    def test_build_identity_gate_reads_the_routed_path_behind_a_trusted_proxy(self) -> None:
        # Behind a trusted proxy the origin decision moves to `X-Forwarded-Host`
        # while `request.url` keeps following the raw `Host` header, so the two
        # path-based decisions have to be pinned separately: here the trust
        # boundary legitimately admits the request and the build-identity gate
        # is the only remaining path-based check.
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            session_lifetime_seconds=600,
            trusted_proxy_ips=("127.0.0.1",),
        )
        self.client.close()
        self.client = TestClient(
            create_transport_test_app(
                self.repository, configuration, clock=lambda: self.now
            ),
            base_url="https://review.test",
            client=("127.0.0.1", 50_000),
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        proxied = {
            "Origin": "https://review.test",
            "X-Forwarded-Host": "review.test",
            "X-Forwarded-Proto": "https",
        }
        login = self.client.post(
            "/api/v1/session/login", headers=proxied, json={"password": "correct horse"}
        )
        self.assertEqual(login.status_code, 200, login.text)
        for host, expected in (({}, 409), ({"Host": "review.test#"}, 409)):
            page = self.client.get("/library", headers=proxied)
            csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
            assert csrf is not None
            denied = self.client.post(
                "/api/v1/session/logout",
                headers={**proxied, **host, "X-CSRF-Token": csrf.group(1)},
            )
            self.assertEqual(denied.status_code, expected, denied.text)
            self.assertEqual(denied.json()["error"]["code"], "browser_build_mismatch")
        page = self.client.get("/library", headers=proxied)
        csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
        assert csrf is not None
        accepted = self.client.post(
            "/api/v1/session/logout",
            headers={
                **proxied,
                "Host": "review.test#",
                "X-CSRF-Token": csrf.group(1),
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
        )
        self.assertEqual(accepted.status_code, 204, accepted.text)

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
        contexts = title["choice_contexts"]
        self.assertEqual(len(contexts), len(title["proposed_values"]) + 1)
        self.assertEqual(contexts[0]["selection"], "current")
        self.assertEqual(contexts[0]["evidence"][0]["source"], "Local library record")
        self.assertEqual(
            contexts[0]["rationale"],
            "Keep the value currently held by the local library.",
        )
        self.assertTrue(all(
            context["recommendation"] == "no_recommendation"
            for context in contexts
        ))
        self.assertTrue(all(
            "{" not in observation["observation"]
            for context in contexts
            for observation in context["evidence"]
        ))
        self.assertIn("insufficient_independent_corroboration", title["notices"])
        self.assertEqual([], contexts[0]["limitations"])
        self.assertEqual(
            ["insufficient_independent_corroboration"],
            contexts[1]["limitations"],
        )
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
        self._login()
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
                "connections": [],
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
                ("post", "/api/v1/review-preparation/{connection}/check-connection"),
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
        self.assertEqual(
            response_statuses[("post", "/api/v1/review-preparation/{connection}/check-connection")],
            {"200", "401", "403", "404", "409", "422", "503"},
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
        with self.assertRaises(ReviewWebError):
            create_transport_test_app(self.repository, configuration)

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

    def test_served_composition_keeps_automatic_safety_reachable(self) -> None:
        # Every other test in this suite hands `compose_review_application` a
        # repository directly, so nothing exercised what the server actually
        # composes. `AutomaticSafetyControls` duck-types through `object` and
        # converts a missing member into "automatic safety is unavailable", so a
        # composition that dropped the safety seam read as a domain error rather
        # than a wiring fault and every safety read and mutation failed silently.
        from tests.support.tls import synthetic_tls_identity

        root = Path(self.temporary.name)
        database = root / "application.sqlite3"
        private = root / "private"
        private.mkdir()
        os.chmod(private, 0o700)
        repository = open_repository(database)
        certificate, private_key = synthetic_tls_identity(root)
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(self.hash_path),
            tls_certificate_file=str(certificate),
            tls_private_key_file=str(private_key),
        )
        composed: list[object] = []
        try:
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.load_configuration_file",
                    return_value=SimpleNamespace(review=configuration),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.open_repository",
                    return_value=repository,
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
                    Path("/catalogue.sqlite3"),
                    root / "artifacts",
                    "127.0.0.1",
                    8443,
                )
            application = composed[0]
            safety = application.automatic_safety
            self.assertFalse(safety.state().paused)
            safety.pause(True, 5)
            self.assertTrue(safety.state().paused)
        finally:
            repository.close()


if __name__ == "__main__":
    unittest.main()
