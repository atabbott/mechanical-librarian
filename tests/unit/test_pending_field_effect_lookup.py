"""Pending field-effect documents and counts reach effects by index (#1204).

A decision is pending while no live effect names it as its own authority or as
its subtitle companion's. Those statements once asked that with one OR, which
no index serves, so every pending candidate scanned every effect. The oracle
here runs the statements exactly as they were, recovered from the traced
current statements by restoring the original predicate, against the current
ones over the same store.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.adapters.persistence.schema import (
    _FIELD_EFFECT_COMPANION_DECISION_SQL,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from tests.unit.test_review_read_indexes import (
    _plan,
    _representative_database,
    _traced_selects,
)

_COMPANION_PATH = "'$.plan.subtitle_companion.authority.decision_id'"
# The predicate as it stood before #1204, byte for byte.
_ORIGINAL_PREDICATE = (
    "NOT EXISTS (SELECT 1 FROM field_effects effect "
    "WHERE (effect.decision_id=pending.decision_id OR json_extract(effect.document,"
    + _COMPANION_PATH
    + ")=pending.decision_id) AND "
    "effect.state NOT IN ('superseded','abandoned')) "
)
# The two indexed arms that replace it in both statements.
_CURRENT_PREDICATE = (
    "NOT EXISTS (SELECT 1 FROM field_effects WHERE decision_id=pending.decision_id "
    "AND state NOT IN ('superseded','abandoned')) AND NOT EXISTS (SELECT 1 FROM field_effects WHERE "
    + _FIELD_EFFECT_COMPANION_DECISION_SQL
    + "=pending.decision_id AND state NOT IN ('superseded','abandoned')) "
)
_DECISION_ARM = (
    "SEARCH field_effects USING INDEX field_effects_decision (decision_id=?)"
)
_COMPANION_ARM = (
    "SEARCH field_effects USING INDEX field_effects_companion_decision (<expr>=?)"
)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, list[str]]]:
    """A store with human and automatic decisions pending in both statements."""
    connection = _representative_database(tmp_path / "application.sqlite3", works=40)
    cases = connection.execute(
        "SELECT review_case_id,occurred_at,document FROM automatic_decisions "
        "ORDER BY decision_id"
    ).fetchall()
    for index, (case, occurred_at, document) in enumerate(cases):
        automatic = json.loads(document)
        automatic_id = "automatic_decision_" + _digest(f"title-{index}")
        human_id = "human_decision_" + _digest(f"human-{index}")
        automatic.update(decision_id=automatic_id, outcome="candidate")
        connection.execute(
            "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,"
            "idempotency_digest,occurred_at,document) VALUES(?,?,'title',?,?,?)",
            (
                automatic_id,
                case,
                _digest(f"title-idempotency-{index}"),
                occurred_at,
                json.dumps(automatic),
            ),
        )
        connection.execute(
            "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document) "
            "VALUES(?,?,?,?,'authors','owner','accept','null',?,?,?,?)",
            (
                human_id,
                case,
                "review_proposal_" + _digest(f"proposal-{index}"),
                _digest(f"fingerprint-{index}"),
                _digest(f"human-idempotency-{index}"),
                _digest(f"request-{index}"),
                occurred_at,
                json.dumps({"decision_id": human_id}),
            ),
        )
    connection.commit()
    pending = [
        row[0]
        for row in connection.execute(
            "SELECT decision_id FROM automatic_decisions WHERE destination='title' "
            "UNION ALL SELECT decision_id FROM human_decisions "
            "WHERE destination='authors' ORDER BY 1"
        )
    ]
    try:
        yield connection, pending
    finally:
        connection.close()


def _calls(
    repository: SQLiteMetadataReportRepository,
) -> dict[str, Callable[[], object]]:
    return {
        "documents": lambda: repository.pending_field_effect_decision_documents(100),
        "cursor": lambda: repository.pending_field_effect_decision_documents(
            3, (0, "automatic_decision_8")
        ),
        "example": lambda: repository.pending_field_effect_activity_example_document(),
        "counts": lambda: repository.pending_field_effect_decision_counts(),
    }


def _statements(connection: sqlite3.Connection) -> dict[str, str]:
    repository = SQLiteMetadataReportRepository(connection)
    statements = {}
    for name, call in _calls(repository).items():
        (statement,) = (
            traced
            for traced in _traced_selects(connection, call)
            if "field_effects" in traced
        )
        statements[name] = statement
    return statements


def _original(statement: str) -> str:
    assert statement.count(_CURRENT_PREDICATE) == 1
    return statement.replace(_CURRENT_PREDICATE, _ORIGINAL_PREDICATE)


def _effect(
    connection: sqlite3.Connection,
    label: str,
    decision_id: str,
    document: str,
    state: str = "planned",
) -> None:
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,'primary','books','work',?,?,0,1,?)",
        ("effect_plan_" + _digest(label), decision_id, "title", state, document),
    )


def _companion(decision_id: object) -> str:
    return json.dumps(
        {"plan": {"subtitle_companion": {"authority": {"decision_id": decision_id}}}}
    )


def _assert_oracle(connection: sqlite3.Connection) -> None:
    for name, statement in _statements(connection).items():
        current = connection.execute(statement).fetchall()
        original = connection.execute(_original(statement)).fetchall()
        assert current == original, name


def test_original_predicate_is_restored_byte_for_byte(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    for statement in _statements(connection).values():
        restored = _original(statement)
        assert "OR json_extract(effect.document," + _COMPANION_PATH in restored
        assert _FIELD_EFFECT_COMPANION_DECISION_SQL not in restored


def test_current_statements_equal_the_original_across_effect_shapes(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, pending = store
    repository = SQLiteMetadataReportRepository(connection)
    assert len(pending) >= 8
    before = repository.pending_field_effect_decision_counts()
    assert sum(int(str(row["count"])) for row in before) >= len(pending)
    _assert_oracle(connection)

    # Effect naming a decision as its own authority, no companion.
    _effect(connection, "own", pending[0], json.dumps({"plan": {}}))
    # A terminal effect leaves its decision pending in either arm.
    _effect(connection, "terminal", pending[1], _companion(pending[2]), "superseded")
    # Companion authority names a different, otherwise effect-free decision.
    _effect(connection, "companion", "human_decision_absent", _companion(pending[3]))
    # Both arms match one row: the companion names the effect's own decision.
    _effect(connection, "both", pending[4], _companion(pending[4]))
    # Own authority and companion authority are two different pending decisions.
    _effect(connection, "pair", pending[5], _companion(pending[6]))
    # A second live effect for a decision already excluded.
    _effect(connection, "repeat", pending[5], _companion(pending[5]), "retryable")
    # Missing paths and companion values that are not text.
    for label, document in {
        "empty": "{}",
        "no-companion": json.dumps({"plan": {"subtitle_companion": None}}),
        "no-authority": json.dumps({"plan": {"subtitle_companion": {}}}),
        "plan-text": json.dumps({"plan": "text"}),
        "array-root": "[1,2]",
        "integer": _companion(5),
        "real": _companion(1.5),
        "true": _companion(True),
        "null": _companion(None),
        "object": _companion({"decision_id": pending[7]}),
        "array": _companion([pending[7]]),
    }.items():
        _effect(connection, label, "human_decision_" + label, document)
    connection.commit()
    _assert_oracle(connection)

    after = {
        json.loads(document)["decision_id"]
        for document in repository.pending_field_effect_decision_documents(100)
        if "decision_id" in json.loads(document)
    }
    assert pending[1] in after and pending[2] in after and pending[7] in after
    assert not after & {pending[0], pending[3], pending[4], pending[5], pending[6]}
    excluded = sum(int(str(row["count"])) for row in before) - sum(
        int(str(row["count"]))
        for row in repository.pending_field_effect_decision_counts()
    )
    assert excluded == 5


def test_companion_cast_preserves_the_original_comparison_affinity() -> None:
    """The CAST is the conversion a TEXT decision id already applied.

    Comparing a bare json_extract, which has no affinity, with a TEXT-affinity
    decision id applies TEXT affinity to the JSON value; CAST AS TEXT performs
    exactly that conversion and is itself TEXT, so nothing else converts.
    """
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE decisions(decision_id TEXT)")
    connection.execute("CREATE TABLE field_effects(document TEXT)")
    values: list[object] = [5, 1.5, True, False, None, {"a": 1}, [1, 2], "x", "5"]
    texts = ["5", "1.5", "1", "0", '{"a":1}', "[1,2]", "x", "", "null", "true"]
    connection.executemany(
        "INSERT INTO decisions VALUES(?)", [(text,) for text in texts]
    )
    connection.executemany(
        "INSERT INTO field_effects VALUES(?)",
        [(_companion(value),) for value in values] + [("{}",)],
    )
    # Like the statements' pending relation: a compound of TEXT columns.
    pending = (
        "(SELECT decision_id FROM decisions "
        "UNION ALL SELECT decision_id FROM decisions WHERE 0) pending"
    )
    original, current = (
        connection.execute(
            f"SELECT pending.decision_id,count(field_effects.rowid) FROM {pending} "
            f"LEFT JOIN field_effects ON {left}=pending.decision_id "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()
        for left in (
            "json_extract(field_effects.document," + _COMPANION_PATH + ")",
            _FIELD_EFFECT_COMPANION_DECISION_SQL,
        )
    )
    assert current == original
    # Non-text values matched their text form; JSON null matched nothing.
    assert dict(original) == {
        "": 0,
        "0": 1,
        "1": 1,
        "1.5": 1,
        "5": 2,
        "[1,2]": 1,
        "null": 0,
        "true": 0,
        "x": 1,
        '{"a":1}': 1,
    }
    connection.close()


def _scans_effects(plan: tuple[str, ...]) -> bool:
    return any(re.match(r"SCAN (field_effects|effect)\b", step) for step in plan)


def test_every_pending_call_shape_reaches_effects_by_index_only(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    statements = _statements(connection)
    assert set(statements) == {"documents", "cursor", "example", "counts"}
    for name, statement in statements.items():
        plan = _plan(connection, statement, ())
        assert not _scans_effects(plan), name
        assert _DECISION_ARM in plan and _COMPANION_ARM in plan, name
        # The original predicate is the per-candidate scan being removed.
        assert _scans_effects(_plan(connection, _original(statement), ()))


@pytest.mark.parametrize(
    "index_name", ["field_effects_decision", "field_effects_companion_decision"]
)
def test_dropping_either_index_restores_the_effect_scan(
    store: tuple[sqlite3.Connection, list[str]], index_name: str
) -> None:
    connection, _ = store
    statements = _statements(connection)
    connection.execute(f"DROP INDEX {index_name}")
    for statement in statements.values():
        assert _scans_effects(_plan(connection, statement, ()))


def test_a_companion_expression_other_than_the_indexed_one_scans(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    bare = "json_extract(document," + _COMPANION_PATH + ")"
    for statement in _statements(connection).values():
        assert statement.count(_FIELD_EFFECT_COMPANION_DECISION_SQL) == 1
        altered = statement.replace(_FIELD_EFFECT_COMPANION_DECISION_SQL, bare)
        assert _scans_effects(_plan(connection, altered, ()))


def test_malformed_effect_json_is_refused_at_write_and_still_at_read(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The companion index evaluates every written document.

    A malformed effect document is now refused when written. A store that
    holds one without the index refuses the pending reads exactly as the
    original statements did.
    """
    connection, _ = store
    with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
        _effect(connection, "malformed", "human_decision_malformed", "{not json")
    connection.rollback()
    connection.execute("DROP INDEX field_effects_companion_decision")
    statements = _statements(connection)
    _effect(connection, "malformed", "human_decision_malformed", "{not json")
    connection.commit()
    repository = SQLiteMetadataReportRepository(connection)
    for name, call in _calls(repository).items():
        with pytest.raises(FieldEffectError, match="persistence"):
            call()
        with pytest.raises(sqlite3.OperationalError, match="malformed JSON"):
            connection.execute(_original(statements[name])).fetchall()
