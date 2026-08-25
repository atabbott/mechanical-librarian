from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import cast

import pytest
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


def test_attention_total_and_row_count_agree_with_the_cohort_works() -> None:
    attention = rich_payload("/api/v1/attention")
    assert isinstance(attention, dict)
    items = attention["items"]
    assert isinstance(items, list)
    expected_attention = sum(
        int(cast(int, work["attention_count"]))
        for work in LIBRARY_WORKS[:4]
        if int(cast(int, work["attention_count"])) > 0
    )
    assert attention["total"] == expected_attention
    assert len(items) == expected_attention


def test_current_only_case_proposes_nothing_and_asks_for_nothing() -> None:
    current_only = rich_payload("/api/v1/review-cases/case-11")
    assert isinstance(current_only, dict)
    for field in current_only["fields"]:
        assert not field["needs_attention"]
        assert not field["manual_required"]
        assert field["proposed_values"] == []


def test_held_case_carries_a_manual_hold() -> None:
    held = rich_payload("/api/v1/review-cases/case-4")
    assert isinstance(held, dict)
    assert any(field["manual_hold"] for field in held["fields"])


def test_activity_carries_a_future_origin_and_every_cover_state() -> None:
    activity = rich_payload("/api/v1/activity")
    assert isinstance(activity, dict)
    assert "future_provider_origin" in {event["origin"] for event in activity["events"]}
    cover_states = {
        event["state"]
        for event in activity["events"]
        if event["destination"] == "cover"
    }
    assert {"completed", "unavailable", "failed", "stale", "superseded"} == cover_states


@pytest.mark.parametrize(
    ("case_id", "status"),
    (("case-1", "completed"), ("case-2", "failed"), ("case-3", "unavailable")),
    ids=("completed", "failed", "unavailable"),
)
def test_cover_consideration_reports_its_reviewer_status(
    case_id: str, status: str
) -> None:
    payload = rich_payload(f"/api/v1/review-cases/{case_id}")
    assert isinstance(payload, dict)
    cover = next(
        field for field in payload["fields"] if field["destination"] == "cover"
    )
    consideration = cover["cover_consideration"]
    assert status == consideration["reviewer_status"]
    assert cover["editable"]
    assert cover["human_decision"] is None
    assert any(candidate["selectable"] for candidate in consideration["candidates"])


def test_real_app_gates_aliases_and_synthetic_cursors() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        password_hash = root / "owner.hash"
        password_hash.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
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
        app = RichFidelityApiMiddleware(inner, review_case_id=installed_case_id)

        with TestClient(app, base_url=_ORIGIN) as client:
            for target in (
                "/api/v1/review-cases/case-1",
                "/api/v1/review-cases/case-2/evidence?limit=50&cursor=evidence-page-2",
                "/api/v1/library?lens=all&cursor=page-2",
            ):
                assert client.get(target).status_code == 401

            login = client.post(
                "/api/v1/session/login",
                json={"password": _PASSWORD},
                headers={"origin": _ORIGIN},
            )
            assert login.status_code == 200

            linked_works = [
                work for work in LIBRARY_WORKS if work["review_case_id"] is not None
            ]
            for work in linked_works:
                case_id = str(work["review_case_id"])
                response = client.get(f"/api/v1/review-cases/{case_id}")
                assert response.status_code == 200
                payload = response.json()
                assert payload["review_case_id"] == case_id
                assert payload["target"]["title"] == work["title"]
                assert payload["target"]["item"] != payload["target"]["title"]

            evidence = client.get("/api/v1/review-cases/case-2/evidence")
            assert evidence.status_code == 200
            assert evidence.json()["review_case_id"] == "case-2"

            evidence_page_2 = client.get(
                "/api/v1/review-cases/case-2/evidence?limit=50&cursor=evidence-page-2"
            )
            assert evidence_page_2.status_code == 200
            page_2_payload = evidence_page_2.json()
            assert page_2_payload["review_case_id"] == "case-2"
            assert len(page_2_payload["items"]) == 3
            assert all(
                set(item) == {"observation", "role", "source"}
                for item in page_2_payload["items"]
            )
            assert {item["source"] for item in page_2_payload["items"]} == {
                "Open catalogue",
                "Second catalogue",
                "This library",
            }
            assert page_2_payload["next_cursor"] is None

            history = client.get("/api/v1/review-cases/case-2/history")
            assert history.status_code == 200
            assert history.json()["review_case_id"] == "case-2"
            assert {item["kind"] for item in history.json()["items"]} == {
                "admitted",
                "automatic_decision_recorded",
                "automatic_prepared",
                "human_decision_recorded",
                "shadow_preview_recorded",
            }

            library_page_2 = client.get("/api/v1/library?lens=all&cursor=page-2")
            assert library_page_2.status_code == 200
            library_payload = library_page_2.json()
            assert len(library_payload["items"]) == 6
            assert library_payload["next_cursor"] == "page-3"
            assert library_payload["items"][-1]["title"] == LIBRARY_WORKS[11]["title"]

            library_page_4 = client.get("/api/v1/library?lens=all&cursor=page-4")
            assert library_page_4.status_code == 200
            final_library_payload = library_page_4.json()
            assert len(final_library_payload["items"]) == 2
            assert final_library_payload["next_cursor"] is None
            assert (
                final_library_payload["items"][-1]["title"]
                == LIBRARY_WORKS[-1]["title"]
            )
