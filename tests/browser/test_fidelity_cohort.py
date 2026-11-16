from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import cast

import pytest
from argon2 import PasswordHasher
from starlette.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web.app import (
    _LibraryProgressResponse,
    _PreparationResponse,
)
from tests.browser import fidelity_cohort
from tests.browser.fidelity_cohort import (
    COHORT_WRITE_PLANS,
    LIBRARY_PROGRESS_STATES,
    LIBRARY_WORKS,
    THROTTLE_LONG_LIBRARY,
    RichFidelityApiMiddleware,
    finished_preparation,
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
    # The hold waits for the owner: no return to automatic is requested yet,
    # so the journey can request one (#1755).
    assert not any(field["manual_return_requested"] for field in held["fields"])
    assert not any(field["manual_return_failed"] for field in held["fields"])


@pytest.mark.parametrize(
    "state", tuple(COHORT_WRITE_PLANS), ids=tuple(COHORT_WRITE_PLANS)
)
def test_permission_readiness_agrees_with_each_write_plan(
    state: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every planned field is listed, and its count is its groups' count (#1336)."""
    plan = COHORT_WRITE_PLANS[state]
    monkeypatch.setattr(fidelity_cohort, "_COHORT_WRITE_PLAN", plan)
    readiness = rich_payload("/api/v1/activity/permission-readiness")
    assert isinstance(readiness, dict)
    assert readiness["plan"] is plan
    groups = cast("list[dict[str, object]]", plan["groups"])
    planned: dict[tuple[str, str], int] = {}
    for group in groups:
        key = (cast("str", group["library"]), cast("str", group["destination"]))
        planned[key] = planned.get(key, 0) + cast("int", group["count"])
    fields = {
        (library["library"], field["destination"]): field["prepared"]
        for library in readiness["capabilities"]
        for field in library["destinations"]
    }
    assert set(planned) <= set(fields)
    assert {key: count for key, count in fields.items() if count} == planned
    assert sum(planned.values()) == plan["total"] == readiness["prepared"]["total"]
    for library in readiness["configured_libraries"]:
        assert library["prepared"]["total"] == sum(
            count for (name, _), count in planned.items() if name == library["library"]
        )


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


def test_activity_carries_one_preparations_checks_across_several_fields() -> None:
    activity = rich_payload("/api/v1/activity")
    assert isinstance(activity, dict)
    groups: dict[str, set[str]] = {}
    for event in activity["events"]:
        if event["check_group"] is not None:
            groups.setdefault(event["check_group"], set()).add(event["destination"])
    assert [len(fields) for fields in groups.values()] == [3]


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
    assert consideration["reviewer_unavailable_reason"] is None
    assert cover["editable"]
    assert cover["human_decision"] is None
    assert consideration["provider_fetching"] is False
    assert consideration["ready"] is True
    assert consideration["policy"]["status"] == "recommended"
    assert consideration["policy"]["recommended_candidate_reference"] == (
        "cover-ref-choice"
    )
    assert consideration["policy"]["acceptable_candidate_references"] == [
        "cover-ref-choice"
    ]
    recommended = next(
        candidate
        for candidate in consideration["candidates"]
        if candidate["reference"] == "cover-ref-choice"
    )
    assert recommended["selectable"] is True
    assert recommended["match_state"] == "same_book"
    assert recommended["deterministic_rank"] == 1
    assert recommended["deterministic_citations"]
    assert recommended["state"] == "recommended"


def test_cover_consideration_keeps_unconfirmed_alternative_manual_only() -> None:
    payload = rich_payload("/api/v1/review-cases/case-1")
    assert isinstance(payload, dict)
    cover = next(
        field for field in payload["fields"] if field["destination"] == "cover"
    )
    consideration = cover["cover_consideration"]
    unconfirmed = next(
        candidate
        for candidate in consideration["candidates"]
        if candidate["reference"] == "cover-ref-unconfirmed"
    )
    assert unconfirmed["identity"]["outcome"] == "unknown"
    assert unconfirmed["match_state"] == "match_not_confirmed"
    assert unconfirmed["selectable"] is False
    assert unconfirmed["manual_eligible"] is True
    assert unconfirmed["deterministic_rank"] is None
    assert unconfirmed["deterministic_rank_key"] is None
    assert unconfirmed["state"] == "excluded"


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
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
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
            retry = "/api/v1/provider-recovery/retry"
            origin = {"origin": _ORIGIN}
            assert (
                client.post(
                    retry, json={"expected_generation": 1}, headers=origin
                ).status_code
                == 401
            )

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
            admitted_destinations = {"title", "authors", "series"}
            assert all(
                set(item)
                == {"destinations", "language", "observation", "role", "source"}
                for item in page_2_payload["items"]
            )
            for item in page_2_payload["items"]:
                destinations = item["destinations"]
                assert isinstance(destinations, list)
                assert 0 < len(destinations) <= 16
                assert len(destinations) == len(set(destinations))
                assert set(destinations) <= admitted_destinations
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
                "preview_recorded",
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


def test_preparation_carries_three_throttled_sources_and_every_remedy() -> None:
    preparation = rich_payload("/api/v1/review-preparation")
    assert isinstance(preparation, dict)
    throttling = preparation["provider_throttling"]
    assert throttling["retry"] == "available"
    entries = throttling["entries"]
    assert [(entry["library"], entry["adapter"]) for entry in entries] == [
        ("Synthetic ebooks", "google_books"),
        ("Synthetic ebooks", "hardcover"),
        (THROTTLE_LONG_LIBRARY, "open_library"),
        ("Synthetic ebooks", "inventaire"),
    ]
    assert {entry["remedy"] for entry in entries} == {
        "add_api_key",
        "check_api_key",
        None,
    }
    assert {entry["state"] for entry in entries} == {"waiting", "dormant", "exhausted"}
    assert sorted(entry["deferred"] for entry in entries) == [1, 12, 214, 10_000]
    # The needs-you set (#1494): every entry but the routine Inventaire wait,
    # which has no remedy, is waiting and has not yet waited a day.
    routine = [
        entry["adapter"]
        for entry in entries
        if entry["state"] == "waiting"
        and entry["remedy"] is None
        and entry["notice_at"] > time.time()
    ]
    assert routine == ["inventaire"]


def test_library_progress_states_are_ones_the_server_could_answer() -> None:
    """The cohort's progress and finished-run states pass the wire models (#1335)."""
    library = rich_payload("/api/v1/library?lens=all")
    assert isinstance(library, dict)
    default = rich_payload("/api/v1/library/progress")
    assert isinstance(default, dict)
    # The cohort's own progress agrees with its Library counts.
    assert default["totals"] == library["counts"]
    for state in (default, *LIBRARY_PROGRESS_STATES.values()):
        _LibraryProgressResponse.model_validate(state)
        libraries = state["libraries"]
        assert isinstance(libraries, list)
        totals = state["totals"]
        assert isinstance(totals, dict)
        for key, total in totals.items():
            assert total == sum(row["counts"][key] for row in libraries)
    for run, failed in (("completed_clean", 0), ("completed_with_failures", 2)):
        _PreparationResponse.model_validate(finished_preparation(run, failed=failed))
