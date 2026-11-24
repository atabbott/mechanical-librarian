"""Clearing a field and entering names against a real store (#1523, ADR-0105).

Every write goes through the guarded path over the fake library, so a clear is
shown planned with an empty desired value, written, read back and held, and a
refusal is shown to have appended nothing.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.level_reconciliation import (
    HUMAN_DECISION_INSERT_SQL,
    decision_copies,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_field_answers import next_ordinal
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    RecordHumanDecision,
    ReviewWebError,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from tests.support.genre_reports import genre_report
from tests.support.owner_values import OwnerStore, rewritten

EMPTY = {"kind": "empty", "value": None}
# Synthetic sentinels: no decision or effect event may carry them.
SENTINEL_NAMES = ("Quillon Sentinelle", "Zebulon Markwright")
SENTINEL_SUBTITLE = "Sentinel Subtitle Ninety"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OwnerStore]:
    value = OwnerStore(
        tmp_path,
        rewritten(
            genre_report(current=("Drama",)),
            subtitle=SENTINEL_SUBTITLE,
            publisher="Harbour Press",
        ),
    )
    yield value
    value.close()


def _rows(store: OwnerStore) -> int:
    return store.repository._connection.execute(
        "SELECT (SELECT count(*) FROM human_decisions)"
        "+(SELECT count(*) FROM human_decision_events)"
    ).fetchone()[0]


def _refused(store: OwnerStore, code: str, *args, **kwargs) -> None:
    before = _rows(store)
    with pytest.raises(HumanDecisionError) as error:
        store.decide(*args, **kwargs)
    assert error.value.code == code
    assert _rows(store) == before


def _field(store: OwnerStore, destination: str) -> dict[str, object]:
    detail = InspectCurrentReviewCase(store.repository).run(store.case.review_case_id)
    return next(
        value for value in detail["fields"] if value["destination"] == destination
    )


def _hold(store: OwnerStore, destination: str) -> str | None:
    return store.repository.field_effect_manual_hold_token(
        store.case.review_case_id, destination
    )


@pytest.mark.parametrize(
    ("destination", "held"),
    (
        ("subtitle", SENTINEL_SUBTITLE),
        ("publisher", "Harbour Press"),
        ("genres", ["Drama"]),
    ),
    ids=("subtitle", "publisher", "genres"),
)
def test_a_clear_is_written_read_back_held_and_audited(
    store: OwnerStore, destination: str, held: object
) -> None:
    control = _field(store, destination)["owner_control"]
    # No source returns a subtitle or publisher here, so those rows are
    # context only and offer no control (#1527); the store still takes a clear.
    assert (control and control["clear"]) is (True if destination == "genres" else None)
    decision = store.decide("owner_entered", EMPTY, destination=destination)
    # Every clear holds its field, with no automatic decision on the case.
    assert _hold(store, destination) == decision.decision_id
    written = store.write(decision)
    assert (written.plan.pre_value, written.plan.desired_value) == (held, None)
    assert store.target.read(written.plan).value is None
    events = store.repository._connection.execute(
        "SELECT kind FROM human_decision_events WHERE decision_id=?",
        (decision.decision_id,),
    ).fetchall()
    assert events == [("human_decision_recorded",)]
    assert _field(store, destination)["human_decision"]["selected_value"] == EMPTY


def test_title_and_authors_refuse_an_empty_value_and_append_nothing(
    store: OwnerStore,
) -> None:
    _refused(store, "owner_value_required", "owner_entered", EMPTY)
    _refused(
        store, "owner_value_required", "owner_entered", {"kind": "text", "value": ""}
    )
    _refused(
        store, "owner_value_required", "owner_entered", EMPTY, destination="authors"
    )
    _refused(
        store,
        "owner_value_required",
        "owner_entered",
        {"kind": "list", "value": []},
        destination="authors",
    )
    assert _field(store, "title")["owner_control"]["clear"] is False
    assert _field(store, "authors")["owner_control"]["clear"] is False


def test_the_web_command_states_a_required_value_as_its_own_refusal(
    store: OwnerStore,
) -> None:
    field = _field(store, "title")
    before = _rows(store)
    with pytest.raises(ReviewWebError) as refused:
        RecordHumanDecision(store.repository, clock=store.clock).run(
            store.case.review_case_id,
            proposal_id=str(field["proposal_id"]),
            target_fingerprint=str(field["target_fingerprint"]),
            destination="title",
            actor="owner",
            outcome="owner_entered",
            selected_value=dict(EMPTY),
            idempotency_key="owner-command-required-0001",
            offered_alternatives_digest=str(field["offer_digest"]),
        )
    assert refused.value.code == "owner_value_required"
    assert _rows(store) == before


def test_an_empty_field_refuses_a_clear_as_unchanged(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, genre_report())
    try:
        _refused(
            store, "owner_value_unchanged", "owner_entered", EMPTY, destination="genres"
        )
    finally:
        store.close()


def test_authors_take_names_in_the_owner_order_and_no_event_carries_one(
    store: OwnerStore, capfd: pytest.CaptureFixture[str]
) -> None:
    names = {"kind": "list", "value": list(reversed(SENTINEL_NAMES))}
    control = _field(store, "authors")["owner_control"]
    assert control["names"] == {
        "max_items": 32,
        "max_characters": 512,
        "previous": None,
    }
    assert control["current"] == ["Ada Example"]
    written = store.write(store.decide("owner_entered", names, destination="authors"))
    assert written.plan.desired_value == list(reversed(SENTINEL_NAMES))
    store.write(store.decide("owner_entered", EMPTY, destination="subtitle"))
    captured = capfd.readouterr()
    documents = [
        row[0]
        for row in store.repository._connection.execute(
            "SELECT document FROM human_decision_events"
        )
    ]
    assert len(documents) == 2
    for sentinel in (*SENTINEL_NAMES, SENTINEL_SUBTITLE):
        assert sentinel not in captured.err
        assert sentinel not in captured.out
        assert all(sentinel not in document for document in documents)
    for document in map(json.loads, documents):
        assert set(document) == {
            "actor",
            "decision_id",
            "destination",
            "event_id",
            "kind",
            "occurred_at",
            "outcome",
            "review_case_id",
            "schema_version",
        }


def test_previous_names_are_offered_once_the_field_moved_on(store: OwnerStore) -> None:
    store.write(
        store.decide(
            "owner_entered",
            {"kind": "list", "value": list(SENTINEL_NAMES)},
            destination="authors",
        )
    )
    store.publish(
        rewritten(
            genre_report(current=("Drama",)),
            subtitle=SENTINEL_SUBTITLE,
            publisher="Harbour Press",
            authors=SENTINEL_NAMES,
        )
    )
    names = _field(store, "authors")["owner_control"]["names"]
    assert names["previous"] == ["Ada Example"]


def _insert(store: OwnerStore, outcome: str, selected: str) -> None:
    store.repository._connection.execute(
        HUMAN_DECISION_INSERT_SQL,
        (
            next_ordinal(store.repository._connection),
            "human_decision_" + "f" * 64,
            store.case.review_case_id,
            "review_proposal_" + "e" * 64,
            "d" * 64,
            "subtitle",
            "owner",
            outcome,
            selected,
            "c" * 64,
            "b" * 64,
            1,
            "{}",
            *decision_copies(store.case, "subtitle"),
            None,
        ),
    )


@pytest.mark.parametrize(
    ("outcome", "selected"),
    (
        ("owner_entered", '{"kind":"unknown","value":"x"}'),
        ("owner_entered", '"just text"'),
        ("owner_entered", '{"kind":"empty","value":"x"}'),
        ("accept", '{"kind":"empty","value":null}'),
        ("keep_current", '{"kind":"empty","value":null}'),
    ),
    ids=(
        "unknown-kind",
        "not-an-object",
        "empty-with-value",
        "empty-accept",
        "empty-keep",
    ),
)
def test_the_store_admits_only_closed_selected_kinds(
    store: OwnerStore, outcome: str, selected: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(store, outcome, selected)


def test_the_store_admits_the_empty_kind_on_an_owner_entered_row(
    store: OwnerStore,
) -> None:
    _insert(store, "owner_entered", '{"kind":"empty","value":null}')
    stored = store.repository._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE selected_value_document="
        '\'{"kind":"empty","value":null}\''
    ).fetchone()
    assert stored == (1,)


def test_a_store_built_before_the_empty_kind_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = schema._HUMAN_DECISIONS_SQL
    earlier = current[: current.index(", CHECK(selected_value_document='null'")] + ")"
    monkeypatch.setattr(schema, "_HUMAN_DECISIONS_SQL", earlier)
    path = tmp_path / "earlier.sqlite"
    open_repository(path).close()
    monkeypatch.setattr(schema, "_HUMAN_DECISIONS_SQL", current)
    before = path.read_bytes()
    with pytest.raises(MetadataReportError, match="incompatible"):
        open_repository(path, create=False)
    assert path.read_bytes() == before
