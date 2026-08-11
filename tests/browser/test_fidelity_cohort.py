from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from argon2 import PasswordHasher
from starlette.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.browser.fidelity_cohort import (
    LIBRARY_WORKS,
    RichFidelityApiMiddleware,
    rich_payload,
)
from tests.browser.test_review_browser import _report
from tests.unit.review_app_fixture import create_transport_test_app


_ORIGIN = "https://testserver"
_PASSWORD = "rich-fixture-proof"


class RichFidelityCohortTests(unittest.TestCase):
    def test_rich_state_matrix_is_internally_consistent(self) -> None:
        attention = rich_payload("/api/v1/attention")
        self.assertIsInstance(attention, dict)
        items = attention["items"]
        self.assertIsInstance(items, list)
        expected_attention = sum(
            int(work["attention_count"])
            for work in LIBRARY_WORKS[:4]
            if int(work["attention_count"]) > 0
        )
        self.assertEqual(attention["total"], expected_attention)
        self.assertEqual(len(items), expected_attention)

        current_only = rich_payload("/api/v1/review-cases/case-11")
        self.assertIsInstance(current_only, dict)
        for field in current_only["fields"]:
            self.assertFalse(field["needs_attention"])
            self.assertFalse(field["manual_required"])
            self.assertEqual(field["proposed_values"], [])

        held = rich_payload("/api/v1/review-cases/case-4")
        self.assertIsInstance(held, dict)
        self.assertTrue(
            any(field["manual_hold"] for field in held["fields"])
        )

        activity = rich_payload("/api/v1/activity")
        self.assertIsInstance(activity, dict)
        self.assertIn(
            "future_provider_origin",
            {event["origin"] for event in activity["events"]},
        )

    def test_real_app_gates_aliases_and_synthetic_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            password_hash = root / "owner.hash"
            password_hash.write_text(
                PasswordHasher().hash(_PASSWORD), encoding="utf-8"
            )
            os.chmod(password_hash, 0o600)

            repository = open_repository(
                root / "application.sqlite3", create=True, cross_thread=True
            )
            report = _report()
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            repository.publish_proposal(proposal_set)
            installed_case_id = str(
                json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
            )
            inner = create_transport_test_app(
                repository,
                ReviewWebConfiguration(
                    canonical_origin=_ORIGIN,
                    owner_password_hash_file=str(password_hash),
                ),
                clock=lambda: 1_000,
                close_repository=True,
            )
            app = RichFidelityApiMiddleware(
                inner, review_case_id=installed_case_id
            )

            with TestClient(app, base_url=_ORIGIN) as client:
                for target in (
                    "/api/v1/review-cases/case-1",
                    "/api/v1/review-cases/case-2/evidence"
                    "?limit=50&cursor=evidence-page-2",
                    "/api/v1/library?lens=all&cursor=page-2",
                ):
                    self.assertEqual(client.get(target).status_code, 401)

                login = client.post(
                    "/api/v1/session/login",
                    json={"password": _PASSWORD},
                    headers={"origin": _ORIGIN},
                )
                self.assertEqual(login.status_code, 200)

                linked_works = [
                    work
                    for work in LIBRARY_WORKS
                    if work["review_case_id"] is not None
                ]
                for work in linked_works:
                    case_id = str(work["review_case_id"])
                    response = client.get(f"/api/v1/review-cases/{case_id}")
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()
                    self.assertEqual(payload["review_case_id"], case_id)
                    self.assertEqual(payload["target"]["item"], work["title"])

                evidence = client.get(
                    "/api/v1/review-cases/case-2/evidence"
                )
                self.assertEqual(evidence.status_code, 200)
                self.assertEqual(evidence.json()["review_case_id"], "case-2")

                evidence_page_2 = client.get(
                    "/api/v1/review-cases/case-2/evidence"
                    "?limit=50&cursor=evidence-page-2"
                )
                self.assertEqual(evidence_page_2.status_code, 200)
                page_2_payload = evidence_page_2.json()
                self.assertEqual(page_2_payload["review_case_id"], "case-2")
                self.assertEqual(len(page_2_payload["items"]), 3)
                self.assertTrue(
                    all(
                        str(item["reference"]).startswith("evidence-2-")
                        for item in page_2_payload["items"]
                    )
                )
                self.assertIsNone(page_2_payload["next_cursor"])

                history = client.get(
                    "/api/v1/review-cases/case-2/history"
                )
                self.assertEqual(history.status_code, 200)
                self.assertEqual(history.json()["review_case_id"], "case-2")
                self.assertEqual(len(history.json()["items"]), 4)

                library_page_2 = client.get(
                    "/api/v1/library?lens=all&cursor=page-2"
                )
                self.assertEqual(library_page_2.status_code, 200)
                library_payload = library_page_2.json()
                self.assertEqual(len(library_payload["items"]), 6)
                self.assertEqual(library_payload["next_cursor"], "page-3")
                self.assertEqual(
                    library_payload["items"][-1]["title"],
                    LIBRARY_WORKS[11]["title"],
                )

                library_page_4 = client.get(
                    "/api/v1/library?lens=all&cursor=page-4"
                )
                self.assertEqual(library_page_4.status_code, 200)
                final_library_payload = library_page_4.json()
                self.assertEqual(len(final_library_payload["items"]), 2)
                self.assertIsNone(final_library_payload["next_cursor"])
                self.assertEqual(
                    final_library_payload["items"][-1]["title"],
                    LIBRARY_WORKS[-1]["title"],
                )


if __name__ == "__main__":
    unittest.main()
