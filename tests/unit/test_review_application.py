from __future__ import annotations

import inspect
import threading
import unittest

from mechanical_librarian.application.review_application import (
    AttentionView,
    CaseDetailView,
    LibraryView,
    ReviewApplicationError,
    compose_review_application,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web import create_review_app
from tests.unit.test_review_case_persistence import _report
from tests.unit.test_review_web import _Repository


class ReviewApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        report = _report()
        self.case = ReviewCase.create(evaluate_report(report), report)
        self.repository = _Repository(self.case, report)
        self.now = 1_000
        self.application = compose_review_application(
            state_owner=self.repository,
            session_store=self.repository,
            case_store=self.repository,
            decision_store=self.repository,
            library_store=self.repository,
            policy_store=self.repository,
            verify_password=lambda value: value == "correct horse",
            clock=lambda: self.now,
            session_lifetime_seconds=600,
            graceful_shutdown_seconds=30,
        )

    def test_named_case_operation_returns_bounded_view(self) -> None:
        view = self.application.case_detail(self.case.review_case_id)

        self.assertIsInstance(view, CaseDetailView)
        self.assertEqual(view.review_case_id, self.case.review_case_id)
        self.assertTrue(view.current)
        self.assertEqual(view.to_data()["schema_version"], "review-case-detail-v1")

    def test_named_browse_operations_return_bounded_views(self) -> None:
        library = self.application.library(
            lens="all", search=None, limit=20, cursor=None
        )
        attention = self.application.attention(
            search=None, limit=20, cursor=None
        )

        self.assertIsInstance(library, LibraryView)
        self.assertEqual(library.schema_version, "review-library-v1")
        self.assertEqual(library.counts["all"], 1)
        self.assertIsInstance(attention, AttentionView)
        self.assertEqual(attention.schema_version, "review-attention-v1")
        self.assertEqual(attention.total, 1)

    def test_session_operations_are_composed_before_transport(self) -> None:
        tokens = self.application.login("correct horse")

        self.assertEqual(
            self.application.authenticate(tokens.session_token), "owner"
        )
        browser_csrf = self.application.issue_browser_csrf(tokens.session_token)
        self.assertEqual(
            self.application.authenticate(tokens.session_token, browser_csrf),
            "owner",
        )

    def test_policy_candidate_workflows_are_application_operations(self) -> None:
        candidate = MetadataPolicyProfile.builtin()
        values = (
            "Candidate",
            dict(candidate.options),
            dict(candidate.destinations),
        )
        validated = self.application.validate_policy(*values)
        created = self.application.create_policy_draft(*values)
        previewed, impact = self.application.preview_policy(
            *values
        )

        self.assertEqual(validated.policy_id, created.policy_id)
        self.assertEqual(previewed.policy_id, created.policy_id)
        self.assertEqual(impact.profile_id, created.policy_id)

    def test_unavailable_mutations_have_named_typed_application_failures(self) -> None:
        calls = (
            lambda: self.application.apply_field_effect("effect_plan_" + "a" * 64),
            lambda: self.application.reconcile_field_effect(
                "effect_plan_" + "a" * 64
            ),
            self.application.pause_field_effects,
            self.application.resume_field_effects,
            lambda: self.application.request_review_scan("primary", "books"),
            lambda: self.application.retry_review_scan("primary", "books"),
            self.application.pause_review_preparation,
            self.application.resume_review_preparation,
            self.application.activity,
        )
        for call in calls:
            with self.subTest(call=call), self.assertRaises(
                ReviewApplicationError
            ) as captured:
                call()
            self.assertEqual(captured.exception.code, "unavailable")

    def test_http_factory_accepts_only_composed_application_and_configuration(self) -> None:
        self.assertEqual(
            tuple(inspect.signature(create_review_app).parameters),
            ("application", "configuration"),
        )
        source = inspect.getsource(create_review_app)
        self.assertNotIn("ReviewWebRepository", source)
        self.assertNotIn("_SerializedReviewRepository", source)
        self.assertNotIn("repository.", source)

    def test_connection_probe_does_not_hold_the_global_application_gate(self) -> None:
        started = threading.Event()
        release = threading.Event()
        browse_finished = threading.Event()

        class BlockingPreparation:
            libraries: tuple[tuple[str, str, str], ...] = ()

            def check_connection(self, _connection: str) -> bool:
                started.set()
                self.assertTrue(release.wait(2))
                return True

            def status(self):
                return (), False

            def connection_conditions(self):
                return ()

        preparation = BlockingPreparation()
        # Bind the assertion to the test rather than the tiny fake supervisor.
        preparation.assertTrue = self.assertTrue
        application = compose_review_application(
            state_owner=self.repository, session_store=self.repository,
            case_store=self.repository, decision_store=self.repository,
            library_store=self.repository, policy_store=self.repository,
            verify_password=lambda _: True, clock=lambda: self.now,
            session_lifetime_seconds=600, graceful_shutdown_seconds=30,
            preparation=preparation,
        )
        probe = threading.Thread(
            target=lambda: application.check_review_preparation_connection("primary")
        )
        probe.start()
        self.assertTrue(started.wait(1))
        browse = threading.Thread(
            # The tuple sequences the browse call and the `set()` signal inside one
            # lambda, so the discarded `None` element is the point, not a mistake.
            target=lambda: (application.library(lens="all", search=None, limit=20, cursor=None), browse_finished.set())  # type: ignore[func-returns-value]
        )
        browse.start()
        self.assertTrue(browse_finished.wait(1))
        release.set()
        probe.join(1)
        browse.join(1)
        self.assertFalse(probe.is_alive())
        self.assertFalse(browse.is_alive())


if __name__ == "__main__":
    unittest.main()
