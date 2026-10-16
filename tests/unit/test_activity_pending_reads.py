"""Activity readers reuse pending counts and the example per exact stamp (#1223).

A configured Activity reader answers the pending counts and the readiness
example once per SQLite revision and database file identity. These oracles
compare it, read by read, with a repository that recomputes every time, over
the same store as decisions, effects, holds, suspensions and refusals change.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from tests.unit.test_pending_field_effect_lookup import (  # noqa: F401
    _companion,
    _digest,
    _effect,
    _forget_lineage,
    store,
)

# The two memoised statements, as the reader's trace callback sees them.
_COUNTS = "WITH pending AS ("
_EXAMPLE = "SELECT pending.document FROM ("


class _Traced:
    """One repository over its own connection, counting memoised statements."""

    def __init__(self, path: Path, *, reader: bool) -> None:
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.repository = SQLiteMetadataReportRepository(self.connection)
        if reader:
            self.repository.configure_activity_page_reader()
        self.executed: list[str] = []
        self.connection.set_trace_callback(self._trace)
        self.on_counts: Callable[[], None] | None = None

    def _trace(self, statement: str) -> None:
        for name, prefix in (("counts", _COUNTS), ("example", _EXAMPLE)):
            if statement.startswith(prefix):
                self.executed.append(name)
                if name == "counts" and self.on_counts is not None:
                    self.on_counts()

    def answers(self) -> dict[str, object]:
        answers: dict[str, object] = {}
        for name, call in (
            ("counts", self.repository.pending_field_effect_decision_counts),
            ("example", self.repository.pending_field_effect_activity_example_document),
        ):
            try:
                answers[name] = call()
            except FieldEffectError as error:
                answers[name] = ("refused", error.code)
        return answers

    def readiness(self) -> dict[str, object]:
        repository = self.repository

        def project(ready: bool | None) -> dict[str, object]:
            return {
                "backup_ready": ready,
                "summary": repository.field_effect_activity_summary(),
                **self.answers(),
            }

        return repository.field_effect_permission_readiness_snapshot(project)


@pytest.fixture
def pair(
    store: tuple[sqlite3.Connection, list[str]],  # noqa: F811
) -> Iterator[tuple[sqlite3.Connection, list[str], _Traced, _Traced]]:
    writer, pending = store
    path = Path(writer.execute("PRAGMA database_list").fetchone()[2])
    reader = _Traced(path, reader=True)
    canonical = _Traced(path, reader=False)
    try:
        yield writer, pending, reader, canonical
    finally:
        reader.connection.close()
        canonical.connection.close()


def _total(counts: object) -> int:
    rows = cast("tuple[dict[str, int], ...]", counts)
    return sum(row["count"] for row in rows)


def _refused(value: object) -> bool:
    return type(value) is tuple and value[:1] == ("refused",)


def _assert_oracle(reader: _Traced, canonical: _Traced) -> dict[str, object]:
    """Two reads at one stamp equal recomputation; only the first executes."""
    reader.executed.clear()
    canonical.executed.clear()
    first = reader.answers()
    expected = canonical.answers()
    assert first == expected
    computed = list(reader.executed)
    assert computed == ["counts", "example"]
    assert reader.answers() == expected == canonical.answers()
    refused = [name for name, value in first.items() if _refused(value)]
    # Only a refusal is read again at an unchanged stamp; recomputation always is.
    assert reader.executed == computed + refused
    assert canonical.executed == computed * 2
    return first


def test_reader_equals_recomputation_across_pending_changes(
    pair: tuple[sqlite3.Connection, list[str], _Traced, _Traced],
) -> None:
    writer, pending, reader, canonical = pair
    case, held, destination = writer.execute(
        "SELECT first.review_case_id,held.review_case_id,held.destination "
        "FROM automatic_decisions first,automatic_decisions held "
        "WHERE first.decision_id=? AND held.decision_id=?",
        (pending[0], pending[5]),
    ).fetchone()
    answers: list[dict[str, object]] = []

    def step(*statements: tuple[str, tuple[object, ...]]) -> None:
        for sql, values in statements:
            writer.execute(sql, values)
        writer.commit()
        answers.append(_assert_oracle(reader, canonical))
        # The readiness snapshot reads at the same stamp, so only refusals rerun.
        reader.executed.clear()
        assert reader.readiness() == canonical.readiness()
        assert reader.executed == [
            name for name, value in answers[-1].items() if _refused(value)
        ]

    step()

    # Effects: the decision's own, a companion's, and a terminal one.
    _effect(writer, "own", pending[0], json.dumps({"plan": {}}))
    step()
    _effect(writer, "companion", "human_decision_absent", _companion(pending[1]))
    step()
    _effect(writer, "terminal", pending[2], "{}", "superseded")
    step()
    example = json.loads(str(answers[0]["example"]))["decision_id"]
    _effect(writer, "example", example, json.dumps({"plan": {}}))
    step()
    # Holds, a recorded no-change and a new accepted owner decision.
    hold = (
        "INSERT INTO field_effect_manual_holds VALUES(?,?,?,1,'{}') "
        "ON CONFLICT DO UPDATE SET active=excluded.active"
    )
    step((hold, (held, destination, 1)))
    step((hold, (held, destination, 0)))
    report = writer.execute("SELECT report_id FROM metadata_reports").fetchone()[0]
    step(
        (
            "INSERT INTO field_effect_no_changes VALUES(?,?,?,'primary','books',"
            "'work','title','fingerprint','version','human',1,?)",
            (pending[3], case, report, "event-" + _digest("no-change")),
        )
    )
    step(
        (
            "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document) "
            "VALUES(?,?,?,?,'genres','owner','accept','null',?,?,1,'{}')",
            (
                "human_decision_" + _digest("new"),
                case,
                "review_proposal_" + _digest("new"),
                _digest("new-fingerprint"),
                _digest("new-idempotency"),
                _digest("new-request"),
            ),
        )
    )
    # A suspension changes no count; readiness still reports it.
    step(
        (
            "INSERT INTO field_effect_suspensions VALUES('destination','primary',"
            "'title','field_unavailable',1,'{}')",
            (),
        )
    )
    _forget_lineage(writer, case)
    step()
    # A refused read stays refused, and so does the example once it is reached.
    writer.execute("DROP TRIGGER automatic_decisions_no_update")
    step(
        (
            "UPDATE automatic_decisions SET document='{not json' WHERE decision_id=?",
            (pending[4],),
        )
    )
    assert answers[-1]["counts"] == ("refused", "persistence")
    writer.execute("DROP INDEX field_effects_companion_decision")
    _effect(writer, "malformed", "human_decision_malformed", "{not json")
    step()
    assert answers[-1] == {
        "counts": ("refused", "persistence"),
        "example": ("refused", "persistence"),
    }
    step(("DELETE FROM field_effects WHERE document='{not json'", ()))
    step(
        (
            "UPDATE automatic_decisions SET document=? WHERE decision_id=?",
            (json.dumps({"outcome": "candidate"}), pending[4]),
        )
    )
    totals = [
        "refused" if _refused(value["counts"]) else _total(value["counts"])
        for value in answers
    ]
    refused = ["refused"] * 3
    assert totals == [20, 19, 18, 18, 17, 16, 17, 16, 17, 17, 15, *refused, 15]
    assert len({str(value["example"]) for value in answers}) == 3


def test_revision_alone_invalidates(
    pair: tuple[sqlite3.Connection, list[str], _Traced, _Traced],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer, pending, reader, canonical = pair
    activity = reader.repository._field_effect_activity
    monkeypatch.setattr(activity, "_activity_database_identity", lambda: (1, 1, 1, 1))
    before = _assert_oracle(reader, canonical)
    _effect(writer, "own", pending[0], json.dumps({"plan": {}}))
    writer.commit()
    assert _assert_oracle(reader, canonical) != before


def test_identity_alone_invalidates_even_when_it_reverts(
    pair: tuple[sqlite3.Connection, list[str], _Traced, _Traced],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity that moved during a read is never stored under either value."""
    writer, pending, reader, canonical = pair
    activity = reader.repository._field_effect_activity
    identity = [(1, 1, 1, 1)]
    monkeypatch.setattr(activity, "_activity_projection_revision", lambda: (1, 1))
    monkeypatch.setattr(activity, "_activity_database_identity", lambda: identity[0])
    before = _assert_oracle(reader, canonical)
    _effect(writer, "own", pending[0], json.dumps({"plan": {}}))
    writer.commit()
    identity[0] = (2, 2, 2, 2)
    changed = _assert_oracle(reader, canonical)
    assert changed != before
    # The bytes change while the counts are read.
    _effect(writer, "companion", "human_decision_absent", _companion(pending[1]))
    writer.commit()
    identity[0] = (3, 3, 3, 3)
    reader.on_counts = lambda: identity.__setitem__(0, (4, 4, 4, 4))
    during = reader.answers()
    reader.on_counts = None
    assert during == canonical.answers() != changed
    # They change again, and identity reverts to the value sampled before.
    _effect(writer, "third", pending[3], json.dumps({"plan": {}}))
    writer.commit()
    identity[0] = (3, 3, 3, 3)
    assert _assert_oracle(reader, canonical) != during


def test_a_transient_refusal_is_read_again_at_the_same_stamp(
    pair: tuple[sqlite3.Connection, list[str], _Traced, _Traced],
) -> None:
    writer, _, reader, canonical = pair
    expected = canonical.answers()

    def lock() -> None:
        writer.execute("BEGIN EXCLUSIVE")

    # Another connection takes the database after the stamp is sampled and
    # gives it back without a change, so the stamp is the same afterwards.
    activity = reader.repository._field_effect_activity
    stamp = activity._activity_projection_revision()
    reader.on_counts = lock
    with pytest.raises(FieldEffectError, match="persistence"):
        reader.repository.pending_field_effect_decision_counts()
    reader.on_counts = None
    writer.rollback()
    assert activity._activity_projection_revision() == stamp
    reader.executed.clear()
    assert reader.answers() == expected
    assert reader.executed == ["counts", "example"]
