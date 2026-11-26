"""Every screen reads the owner's answers the same across a regeneration
(#2039, T-RT; ADR-0108).

Three books in one real store, under Automatic with the title set to
Automatic and the ISBN to Ask me. The owner answers six fields: an entered
title that is written and read back, a kept ISBN and a pinned author list on
book A; an accepted title and a kept ISBN on book B; a kept title whose
Resume failed and a declined ISBN on book C. A fourth answer's Resume is
requested after the run. Attention, each case page, Activity (page 1 and a
title search) and History are read over HTTP; then the real
`regenerate-derived` runs, the books are prepared again (A unchanged, B with
other choices, C with another title and ISBN in Audiobookshelf) and the same
screens are read again.

A kept answer reads as before, with no Activity row of its own and a History
line naming the choice it kept. An answer asked again says why and quotes
the earlier answer on every screen. The Resume offer follows ADR-0108's
"Resume automatic handling" rule: offered from the record wherever it holds
an answer for a field set to Automatic and no pin is in force, and offered
again after a failed Resume meets a new case. Activity keeps every owner row
with its title. The upgrade notice counts what the question check did.
Readings that differ after a regeneration by design (contract 7.2) are
asserted as they now read and named where they are.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.upgrade_notice import UPGRADE_NOTICE_SCHEMA
from tests.integration.test_owner_field_record import _regenerated
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.owner_field_record import (
    assert_record_recomputes,
    record_recomputes,  # noqa: F401
    request_resume,
)
from tests.support.owner_values import OwnerStore, rewritten
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_level_offer_binding import field_of
from tests.unit.test_owner_answer_holds import _activate, _profile

_ORIGIN = "https://review.test"
_PASSWORD = "correct horse"
# The owner answers a few hours before the run, which
# `regenerate_as_the_command` finishes at 1_700_000_000; the books are
# prepared again after it, and the screens are read later still, so every
# row is inside Activity's 30-day range.
_BEFORE = 1_699_990_000
_AFTER = 1_700_000_010
_NOW = 1_700_000_500
_FIELDS = ("title", "isbn", "authors")
_ENTERED = {"kind": "text", "value": "Alder Owner Title"}


def _book(item: str, name: str, **changes: Any) -> MetadataReport:
    """Book ``item`` of the library, named ``name`` in Audiobookshelf."""
    report = isbn_title_report(
        current=f"{name} Series 01 - {name} Title",
        titles=changes.pop("titles", (f"{name} Title",)),
    )
    return MetadataReport.create(
        target=replace(report.target, item=item),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


_A = _book("item-a", "Alder")
_B = _book("item-b", "Birch")
_C = _book("item-c", "Cedar")
# A written title is read back, as the scan after a write reads it.
_A_READ_BACK = rewritten(_A, title=str(_ENTERED["value"]))
# B's sources now offer another title: the question changed.
_B_AGAIN = _book("item-b", "Birch", titles=("Birch Title, Revised",))
# C's title and ISBN were changed in Audiobookshelf.
_C_AGAIN = rewritten(_C, title="Cedar Retitled Elsewhere", isbn="9780306406164")


def _store(root: Path) -> OwnerStore:
    store = OwnerStore(root, None)
    store.ticks = _BEFORE
    store.repository.enter_shadow_preview(1, ())
    _activate(
        store,
        _profile("First policy", "automatic"),
        MetadataPolicyProfile.builtin(),
        _A.target.library,
    )
    store.repository.activate_qualified_automatic(store.clock())
    return store


def _proposed_title(store: OwnerStore) -> dict[str, object]:
    proposal = next(
        value for value in store.case.proposals if value["destination"] == "title"
    )
    return next(
        alternative["value"]
        for alternative in proposal["result"]["alternatives"]
        if alternative["assessment"] != "preserves_target"
    )


@contextmanager
def _client(store: OwnerStore, root: Path) -> Iterator[TestClient]:
    """A logged-in client over the store's repository, with Activity read
    by the activity projection as the product composes it."""
    password = root / "owner.hash"
    password.write_text(PasswordHasher().hash(_PASSWORD))
    password.chmod(0o600)
    activity = ContinuousGuardedApplication(
        store.repository,
        {},
        {},
        PersistedFieldEffectAuthority(store.repository),
        (("primary", _A.target.library),),
        lambda: _NOW,
        lambda *_: None,
        activity_only=True,
    )
    app = create_transport_test_app(
        store.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=_ORIGIN,
            owner_password_hash_file=str(password),
        ),
        clock=lambda: _NOW,
        activity_projection=activity,
    )
    with TestClient(app, base_url=_ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": _PASSWORD},
            headers={"Origin": _ORIGIN},
        )
        assert login.status_code == 200, login.text
        yield client


def _get(client: TestClient, path: str, **params: str | int) -> dict[str, Any]:
    response = client.get(path, params=params)
    assert response.status_code == 200, (path, response.text)
    return response.json()


def _screens(client: TestClient, cases: dict[str, str]) -> dict[str, Any]:
    """What Attention, the case pages, Activity and History say about the
    owner's answers, keyed by book and field."""
    fields = {}
    for item, case in cases.items():
        detail = _get(client, f"/api/v1/review-cases/{case}")
        for destination in _FIELDS:
            field = field_of(detail, destination)
            decision = field["human_decision"]
            fields[item, destination] = {
                "cause": field["classification_cause"],
                "decision": None
                if decision is None
                else (decision["decision_id"], decision["outcome"]),
                "asked_again": field["asked_again"],
                "resume_offered": field["manual_hold"],
                "resume_requested": field["manual_return_requested"],
                "manual_return_failed": field["manual_return_failed"],
                "resume_failed": field["resume_failed"],
                "pin": field["pin"],
            }
    attention = {
        (item["item"], item["destination"]): (
            item["classification_reason"],
            item["asked_again"],
        )
        for item in _get(client, "/api/v1/attention", limit=50)["items"]
    }
    page = _get(client, "/api/v1/activity", page=1, page_size=100)
    owner = {
        event["event_id"]: (
            event["work_label"],
            event["destination"],
            event["change"],
            event["note"],
            event["author_label"],
        )
        for event in page["events"]
        if event["category"] == "owner"
    }
    search = _get(client, "/api/v1/activity", q="Alder", page=1, page_size=100)
    history = {
        item: _get(client, f"/api/v1/review-cases/{case}/history")["items"]
        for item, case in cases.items()
    }
    return {
        "fields": fields,
        "attention": attention,
        "activity": owner,
        "automatic_rows": sum(
            event["category"] == "automatic" for event in page["events"]
        ),
        # Automatic rows are left out: the run drops them by design
        # (contract 7.2, ADR-0108 "Activity").
        "search": {
            event["event_id"]
            for event in search["events"]
            if event["category"] == "owner"
        },
        "search_labels": {
            event["work_label"]
            for event in search["events"]
            if event["category"] == "owner"
        },
        "history": history,
    }


def _notice(client: TestClient) -> dict[str, object] | None:
    body = _get(client, "/api/v1/upgrade-notice")
    assert body["schema_version"] == UPGRADE_NOTICE_SCHEMA
    return body["notice"]


def _carried(store: OwnerStore) -> dict[str, tuple[str, str]]:
    """Each carried row: its root original and the case it was carried to."""
    return {
        str(row[0]): (str(row[1]), str(row[2]))
        for row in store.repository._connection.execute(
            "SELECT decision_id,carried_from,review_case_id FROM human_decisions "
            "WHERE carried_from IS NOT NULL"
        )
    }


def _answer_six_fields(store: OwnerStore) -> dict[str, Any]:
    """The owner's answers before the run; returns each by (book, field)."""
    answers: dict[tuple[str, str], Any] = {}
    cases = {}
    for report in (_A, _B, _C):
        cases[report.target.item] = store.publish(report).review_case_id

    store.load(cases["item-a"])
    entered = store.decide("owner_entered", _ENTERED)
    store.write(entered)
    answers["item-a", "title"] = entered
    answers["item-a", "isbn"] = store.decide("keep_current", destination="isbn")
    answers["item-a", "authors"] = store.decide("pin", destination="authors")

    store.load(cases["item-b"])
    answers["item-b", "title"] = store.decide("accept", _proposed_title(store))
    answers["item-b", "isbn"] = store.decide("keep_current", destination="isbn")

    store.load(cases["item-c"])
    answers["item-c", "title"] = store.decide("keep_current")
    request = request_resume(store.repository, cases["item-c"], "title", store.clock())
    assert request is not None
    (scan,) = store.repository._connection.execute(
        "SELECT scan_id FROM owner_field_events WHERE event_id=?", (request,)
    ).fetchone()
    assert store.repository._fail_open_resumes_for_scan(scan, store.clock()) == 1
    store.repository._connection.commit()
    # A decline is a historical answer no current page offers; it is still
    # read and still asked again.
    answers["item-c", "isbn"] = store.decide("reject", destination="isbn")

    # The written title is read back by the next scan: a new case of A,
    # where both of A's answers are carried before the run.
    cases["item-a"] = store.publish(_A_READ_BACK).review_case_id
    return {"answers": answers, "cases": cases}


def test_the_screens_read_the_same_answers_across_a_regeneration(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    given = _answer_six_fields(store)
    answers, cases = given["answers"], given["cases"]
    carried_before = _carried(store)
    try:
        with _client(store, tmp_path) as client:
            before = _screens(client, cases)
            assert _notice(client) is None
    finally:
        store.close()

    # Before the run: each answer stands on its book's case.
    fields = before["fields"]
    a_title_before = fields["item-a", "title"]["decision"]
    assert a_title_before[1] == "owner_entered"
    assert fields["item-a", "isbn"]["decision"][1] == "keep_current"
    assert fields["item-b", "title"]["decision"] == (
        answers["item-b", "title"].decision_id,
        "accept",
    )
    assert fields["item-c", "isbn"]["decision"] == (
        answers["item-c", "isbn"].decision_id,
        "reject",
    )
    assert fields["item-a", "authors"]["cause"] == "pinned"
    assert fields["item-a", "authors"]["pin"]["state"] == "in_force"
    assert all(value["asked_again"] is None for value in fields.values())
    # Resume: offered on each title (set to Automatic, answered), never on
    # the ISBN (Ask me) or the pinned author list. C's request failed.
    assert {key: value["resume_offered"] for key, value in fields.items()} == {
        key: key[1] == "title" for key in fields
    }
    assert {key for key, value in fields.items() if value["resume_failed"]} == {
        ("item-c", "title")
    }
    assert fields["item-c", "title"]["manual_return_failed"] is True
    # No answered field is in Attention.
    assert not set(before["attention"]) & set(answers)
    # Every owner answer has its own Activity row with its book's title.
    owner_rows = before["activity"]
    for (item, _destination), answer in answers.items():
        row = owner_rows[_event_id(tmp_path, answer.decision_id)]
        assert row[0] == _title_of(item), row
    # A's rows carried to its read-back case have no row of their own.
    assert not {
        _event_id(tmp_path, decision_id) for decision_id in carried_before
    } & set(owner_rows)
    # A title search finds A's owner rows, each named by A's title.
    assert before["search"] == {
        event_id
        for event_id, row in owner_rows.items()
        if row[0] == _title_of("item-a")
    }
    assert before["search_labels"] == {_title_of("item-a")}

    store = _regenerated(store)
    store.ticks = _AFTER
    try:
        with _client(store, tmp_path) as client:
            regeneration = store.repository.regeneration_notice_store()
            pending = regeneration.read_regeneration_notice()
            assert pending is not None
            # Six answers wait for their book's first new case; the pin is
            # never checked, so it is in no count.
            assert (pending.kept, pending.asked_again, pending.not_yet_checked) == (
                0,
                0,
                6,
            )
            assert _notice(client) == {
                "regeneration_id": str(pending.regeneration_id),
                "preparing": False,
                "kept": 0,
                "asked_again": 0,
            }

            new_cases = {
                report.target.item: store.publish(report).review_case_id
                for report in (_A_READ_BACK, _B_AGAIN, _C_AGAIN)
            }
            assert not set(new_cases.values()) & set(cases.values())
            after = _screens(client, new_cases)
            settled = regeneration.read_regeneration_notice()
            assert settled is not None
            assert (settled.kept, settled.asked_again, settled.not_yet_checked) == (
                3,
                3,
                0,
            )
            assert _notice(client) == {
                "regeneration_id": str(settled.regeneration_id),
                "preparing": False,
                "kept": 3,
                "asked_again": 3,
            }

            # Resume asked after the run, over A's carried title (one open
            # before the run would have stopped it without --retire-pending).
            assert (
                request_resume(
                    store.repository, new_cases["item-a"], "title", store.clock()
                )
                is not None
            )
            requested = _screens(client, new_cases)
        carried = {
            decision_id: value
            for decision_id, value in _carried(store).items()
            if decision_id not in carried_before
        }
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()

    _kept_answers_read_the_same(before, after, carried, new_cases, answers)
    _asked_again_answers_say_why(after, answers)
    _resume_offers_follow_the_record(after, requested)
    _activity_keeps_every_owner_row(tmp_path, before, after, answers)


def _event_id(root: Path, decision_id: str) -> str:
    import sqlite3

    with sqlite3.connect(root / "owner.sqlite") as connection:
        (event_id,) = connection.execute(
            "SELECT event_id FROM human_decision_events WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
    return str(event_id)


def _title_of(item: str) -> str:
    return {
        "item-a": "Alder Series 01 - Alder Title",
        "item-b": "Birch Series 01 - Birch Title",
        "item-c": "Cedar Series 01 - Cedar Title",
    }[item]


def _kept_answers_read_the_same(
    before: dict[str, Any],
    after: dict[str, Any],
    carried: dict[str, tuple[str, str]],
    cases: dict[str, str],
    answers: dict[tuple[str, str], Any],
) -> None:
    """A's title and ISBN and B's ISBN are carried to their book's first new
    case: the field names the same outcome, asks nothing, and its History
    names the day of the choice it kept."""
    kept = {
        ("item-a", "title"),
        ("item-a", "isbn"),
        ("item-b", "isbn"),
    }
    by_case = {
        (case, root): decision_id for decision_id, (root, case) in carried.items()
    }
    for item, destination in kept:
        root = answers[item, destination]
        carried_id = by_case[cases[item], root.decision_id]
        field, earlier = (
            after["fields"][item, destination],
            before["fields"][item, destination],
        )
        assert field["decision"] == (carried_id, earlier["decision"][1])
        assert field["cause"] == earlier["cause"] == "human_decided"
        assert field["asked_again"] is None
        assert (item, destination) not in after["attention"]
        (line,) = [
            value
            for value in after["history"][item]
            if value.get("decision_id") == carried_id
        ]
        assert (line["kind"], line["kept_from"]) == (
            "human_decision_carried",
            root.occurred_at,
        )
    assert len(by_case) == len(kept)


def _asked_again_answers_say_why(
    after: dict[str, Any], answers: dict[tuple[str, str], Any]
) -> None:
    """B's title (other choices), C's title (its Resume failed, which the
    question check reads first) and C's ISBN (another value in
    Audiobookshelf) are asked again: the case page and Attention give the
    same reason and quote the same earlier answer on its day."""
    # The earlier answer's value: what was accepted, the value kept, and
    # none for a decline.
    expected = {
        ("item-b", "title"): (
            "question_changed",
            answers["item-b", "title"].selected_value,
        ),
        ("item-c", "title"): (
            "resume_failed",
            {"kind": "text", "value": _title_of("item-c")},
        ),
        ("item-c", "isbn"): ("target_changed", None),
    }
    for key, (reason, value) in expected.items():
        answer = answers[key]
        document = {
            "reason": reason,
            "earlier": {
                "outcome": answer.outcome,
                "value": value,
                "occurred_at": answer.occurred_at,
            },
        }
        field = after["fields"][key]
        assert (field["cause"], field["decision"], field["asked_again"]) == (
            "asked_again",
            None,
            document,
        ), key
        assert after["attention"][key] == ("This field needs your review.", document)


def _resume_offers_follow_the_record(
    after: dict[str, Any], requested: dict[str, Any]
) -> None:
    """ADR-0108, "Resume automatic handling": offered from the record on a
    field set to Automatic that it holds an answer for, with no pin in
    force; "Resume failed, then a new case": asked again and offered again,
    the failed request closed, so it no longer reads failed."""
    fields = after["fields"]
    assert {key: value["resume_offered"] for key, value in fields.items()} == {
        key: key[1] == "title" for key in fields
    }
    assert not any(value["resume_failed"] for value in fields.values())
    assert not any(value["manual_return_failed"] for value in fields.values())
    assert not any(value["resume_requested"] for value in fields.values())
    # A Resume requested on A's carried title reads requested, the panel
    # still shown; no other field's reading moves.
    title = requested["fields"]["item-a", "title"]
    assert (title["resume_offered"], title["resume_requested"]) == (True, True)
    assert {
        key: value
        for key, value in requested["fields"].items()
        if key != ("item-a", "title")
    } == {key: value for key, value in fields.items() if key != ("item-a", "title")}


def _activity_keeps_every_owner_row(
    root: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    answers: dict[tuple[str, str], Any],
) -> None:
    """Every owner row read before the run is read after it with the same
    title, field and change; carried rows add none. The rows whose answer is
    asked again say why in their note. A title search finds the same rows."""
    notes = {
        _event_id(root, answers["item-b", "title"].decision_id): "asked_again_choices",
        _event_id(root, answers["item-c", "title"].decision_id): "asked_again_resume",
        _event_id(root, answers["item-c", "isbn"].decision_id): "asked_again_value",
    }
    assert set(after["activity"]) == set(before["activity"])
    for event_id, row in before["activity"].items():
        assert row[3] is None, row
        assert after["activity"][event_id] == (*row[:3], notes.get(event_id), row[4])
    assert after["search"] == before["search"]
    assert after["search_labels"] == before["search_labels"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#2039 P6c finding: the case page reads a pin's value from the pinned "
        "answer's own case (review_projections._pins joins review_cases), "
        "which a regeneration removes, so the pin is not shown on the new "
        "case although the record keeps it in force"
    ),
)
def test_a_pin_reads_in_force_on_the_first_case_after_a_regeneration(
    tmp_path: Path,
) -> None:
    """A pin is never checked by the question check (ADR-0108): the record
    keeps it, and the book's first new case with the same value reads it in
    force, as the case before the run did."""
    store = _store(tmp_path)
    store.publish(_A)
    store.decide("pin", destination="authors")
    store = _regenerated(store)
    store.ticks = _AFTER
    try:
        case = store.publish(_A).review_case_id
        with _client(store, tmp_path) as client:
            field = field_of(_get(client, f"/api/v1/review-cases/{case}"), "authors")
        # The record still holds the pin, unchanged.
        assert store.repository._connection.execute(
            "SELECT count(*) FROM owner_field_answers "
            "WHERE destination='authors' AND pin_fingerprint IS NOT NULL"
        ).fetchone() == (1,)
    finally:
        store.close()
    assert field["classification_cause"] == "pinned"
    assert field["pin"] is not None and field["pin"]["state"] == "in_force"
