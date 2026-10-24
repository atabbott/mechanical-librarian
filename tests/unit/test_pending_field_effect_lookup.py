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
import itertools
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence import field_effects
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.adapters.persistence.schema import (
    _FIELD_EFFECT_COMPANION_DECISION_SQL,
    _FIELD_EFFECT_READY_STATES_SQL,
    _FIELD_EFFECT_WAITING_STATES_SQL,
    _FIELD_EFFECTS_COMPANION_DECISION_INDEX_SQL,
    _FIELD_EFFECTS_DECISION_INDEX_SQL,
)
from mechanical_librarian.domain.description_advice import DescriptionAdviceBinding
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.review_case import ReviewCase
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
_PRIOR_ARM = "SEARCH prior USING INDEX field_effects_decision (decision_id=?)"
_REPLANS = itertools.count()
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
    updated_at: int = 1,
    target: tuple[str, str, str, str] = ("primary", "books", "work", "title"),
) -> None:
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,?,?,?,?,?,0,?,?)",
        (
            "effect_plan_" + _digest(label),
            decision_id,
            *target,
            state,
            updated_at,
            document,
        ),
    )


def _companion(decision_id: object) -> str:
    return json.dumps(
        {"plan": {"subtitle_companion": {"authority": {"decision_id": decision_id}}}}
    )


# #1204's indexes. The originals run without them, so they answer, and order
# their answers, as they did before.
_INDEXES_1204 = (
    "field_effects_decision",
    "field_effects_companion_decision",
    "field_effects_ready",
    "field_effects_waiting",
    "field_effects_target",
    "description_decisions_case",
)


def _before_1204(
    connection: sqlite3.Connection, queries: list[tuple[str, tuple[object, ...]]]
) -> list[list[Any]]:
    connection.execute("SAVEPOINT before_1204")
    try:
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            + str(_INDEXES_1204)
        ).fetchall():
            connection.execute(f"DROP INDEX {name}")
        return [connection.execute(sql, values).fetchall() for sql, values in queries]
    finally:
        connection.execute("ROLLBACK TO before_1204")
        connection.execute("RELEASE before_1204")


def _assert_oracle(connection: sqlite3.Connection) -> None:
    statements = _statements(connection)
    originals = _before_1204(
        connection, [(_original(statement), ()) for statement in statements.values()]
    )
    for (name, statement), original in zip(statements.items(), originals, strict=True):
        assert connection.execute(statement).fetchall() == original, name


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


def _replanned(connection: sqlite3.Connection, statement: str) -> tuple[str, ...]:
    # A cached EXPLAIN of identical text was seen to report the plan from before
    # an index was dropped; a distinct text forces a fresh plan.
    return _plan(connection, f"{statement} -- {next(_REPLANS)}", ())


# Every name these statements give field_effects. A scan, or an index SQLite
# builds for one statement, walks every effect ever recorded.
_EFFECTS = r"(field_effects|effect|prior)\b"


def _scans_effects(plan: tuple[str, ...]) -> bool:
    return any(
        re.match(f"SCAN {_EFFECTS}|SEARCH {_EFFECTS} USING AUTOMATIC", step)
        for step in plan
    )


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
        # The corrective keep_current's predecessor, through the case's
        # description decisions rather than an automatic index over effects.
        assert _PRIOR_ARM in plan, name
        # The original predicate is the per-candidate scan being removed.
        assert _scans_effects(_plan(connection, _original(statement), ()))


@pytest.mark.parametrize(
    "index_name",
    [
        "field_effects_decision",
        "field_effects_companion_decision",
        "description_decisions_case",
    ],
)
def test_dropping_any_index_restores_the_effect_scan(
    store: tuple[sqlite3.Connection, list[str]], index_name: str
) -> None:
    connection, _ = store
    statements = _statements(connection)
    connection.execute(f"DROP INDEX {index_name}")
    for statement in statements.values():
        assert _scans_effects(_replanned(connection, statement))


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


# The other lookups that asked whether a live effect claims a decision by its
# own or its companion's authority, each exactly as it stood before #1204. The
# revocation of a library consent matches consent ids, not decisions, and is
# not among them.
_JSON = "json_extract(document," + _COMPANION_PATH + ")"
_EFFECT_JSON = "json_extract(effect.document," + _COMPANION_PATH + ")"
_RECORDED_HISTORY = (
    "SELECT earlier.decision_id,earlier.ordinal FROM human_decisions earlier "
    "WHERE earlier.review_case_id=? AND earlier.destination=? AND earlier.ordinal<? "
    "UNION ALL SELECT earlier.decision_id,-1 FROM automatic_decisions earlier "
    "WHERE earlier.review_case_id=? AND earlier.destination=?"
)
_RECORDED_AUTHORITY = (
    "SELECT decision_id,ordinal FROM human_decisions earlier WHERE "
    "earlier.review_case_id=? AND earlier.destination=? AND earlier.ordinal<("
    "SELECT ordinal FROM human_decisions WHERE decision_id=?) UNION ALL "
    "SELECT decision_id,-1 FROM automatic_decisions earlier WHERE "
    "earlier.review_case_id=? AND earlier.destination=?"
)
_RECORDED_CASE = (
    "SELECT destination,decision_id FROM automatic_decisions WHERE review_case_id=? "
    "UNION ALL SELECT 'cover',selection.decision_id FROM current_review_case_lineages lineage "
    "JOIN current_cover_selection_authorities current ON "
    "current.connection_name=lineage.connection_name AND "
    "current.library_name=lineage.library_name AND current.item_id=lineage.item_id "
    "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id "
    "WHERE lineage.review_case_id=? UNION ALL "
    "SELECT destination,decision_id FROM human_decisions WHERE review_case_id=?"
)
_ORIGINAL_LOOKUPS = {
    "revoke": "SELECT document,state FROM field_effects WHERE (decision_id=? OR "
    + _JSON
    + "=?) AND state IN ('planned','retryable')",
    "replacement": "SELECT 1 FROM field_effects WHERE (decision_id=? OR "
    + _JSON
    + "=?) AND (state IN ('claimed','applying','reconciling') OR "
    "(state='verified' AND ?)) LIMIT 1",
    "history": "SELECT effect.document FROM field_effects effect JOIN ("
    + _RECORDED_HISTORY
    + ") recorded ON (recorded.decision_id=effect.decision_id OR recorded.decision_id="
    + _EFFECT_JSON
    + ") WHERE effect.state='verified' ORDER BY recorded.ordinal DESC LIMIT 1",
    "authority": "SELECT effect.document FROM field_effects effect JOIN ("
    + _RECORDED_AUTHORITY
    + ") recorded ON (recorded.decision_id=effect.decision_id OR recorded.decision_id="
    + _EFFECT_JSON
    + ") WHERE effect.state='verified' ORDER BY recorded.ordinal DESC LIMIT 1",
    "override": "SELECT DISTINCT recorded.destination FROM ("
    + _RECORDED_CASE
    + ") recorded JOIN field_effects effect ON (effect.decision_id=recorded.decision_id "
    "OR "
    + _EFFECT_JSON
    + "=recorded.decision_id) WHERE effect.state IN ('claimed','applying','reconciling') "
    "OR (effect.state='verified' AND recorded.destination='cover') "
    "ORDER BY recorded.destination",
}


def _lookups(
    connection: sqlite3.Connection,
) -> tuple[str, dict[str, tuple[str, tuple[object, ...]]]]:
    """Run every lookup through its own code path, before any effect exists.

    Returns the earlier owner decision the lookups ask about, and for each
    lookup its traced current statement and the original's parameters.
    """
    repository = SQLiteMetadataReportRepository(connection)
    earlier_id, document = connection.execute(
        "SELECT decision.decision_id,decision.document FROM human_decisions decision "
        "JOIN human_decision_events event ON event.decision_id=decision.decision_id "
        "ORDER BY decision.ordinal LIMIT 1"
    ).fetchone()
    earlier = HumanDecision.from_json(document)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(earlier.review_case_id))["case"]
    )
    later = HumanDecision.create(
        case,
        proposal_id=earlier.proposal_id,
        target_fingerprint=earlier.target_fingerprint,
        destination=earlier.destination,
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="pending-field-effect-lookup-later",
        occurred_at=earlier.occurred_at + 1,
    )
    connection.execute(
        "INSERT INTO field_effect_authorities(decision_id,current,updated_at,document) "
        "VALUES(?,1,0,'{}')",
        (earlier_id,),
    )
    connection.commit()
    case_id, destination = case.review_case_id, later.destination
    repository = SQLiteMetadataReportRepository(connection)
    calls: dict[str, tuple[Callable[[], object], str]] = {
        "revoke": (
            lambda: repository.revoke_field_effect_authority(earlier_id, 1),
            "SELECT document,state FROM field_effects WHERE rowid IN",
        ),
        "replacement": (
            lambda: repository.record_human_decision(later),
            "SELECT 1 FROM field_effects WHERE rowid IN",
        ),
        "history": (
            lambda: repository.get_review_case_history(case_id, 20),
            "SELECT earlier.decision_id,earlier.ordinal",
        ),
        "authority": (
            lambda: repository.field_effect_authority_source(later),
            "SELECT ordinal FROM human_decisions WHERE decision_id=",
        ),
        "override": (
            lambda: repository.read_current_review_case(case_id, 20),
            "SELECT DISTINCT recorded.destination",
        ),
    }
    traced = {}
    for name, (call, marker) in calls.items():
        traced[name] = {
            statement
            for statement in _traced_selects(connection, call)
            if marker in statement and _FIELD_EFFECT_COMPANION_DECISION_SQL in statement
        }
    (ordinal,) = connection.execute(
        "SELECT ordinal FROM human_decisions WHERE decision_id=?",
        (later.decision_id,),
    ).fetchone()
    # History asks once per event; keep the later decision's question.
    traced["history"] = {
        statement
        for statement in traced["history"]
        if f"earlier.ordinal<{ordinal} " in statement
    }
    parameters: dict[str, tuple[object, ...]] = {
        "revoke": (earlier_id, earlier_id),
        "replacement": (earlier_id, earlier_id, False),
        "history": (case_id, destination, ordinal, case_id, destination),
        "authority": (case_id, destination, later.decision_id, case_id, destination),
        "override": (case_id, case_id, case_id),
    }
    lookups = {}
    for name, statements in traced.items():
        (statement,) = statements
        lookups[name] = (statement, parameters[name])
    return earlier_id, lookups


def _assert_lookup_oracle(
    connection: sqlite3.Connection,
    lookups: dict[str, tuple[str, tuple[object, ...]]],
) -> None:
    originals = _before_1204(
        connection,
        [(_ORIGINAL_LOOKUPS[name], values) for name, (_, values) in lookups.items()],
    )
    for (name, (statement, _)), original in zip(
        lookups.items(), originals, strict=True
    ):
        assert connection.execute(statement).fetchall() == original, name


def test_remaining_authority_lookups_equal_the_original(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    earlier, lookups = _lookups(connection)
    assert set(lookups) == set(_ORIGINAL_LOOKUPS)
    _assert_lookup_oracle(connection, lookups)
    seen: dict[str, set[object]] = {name: set() for name in lookups}

    def compare() -> None:
        connection.commit()
        _assert_lookup_oracle(connection, lookups)
        for name, (statement, _) in lookups.items():
            seen[name].add(repr(connection.execute(statement).fetchall()))

    # Own authority, verified: the predecessor and history lookups find it.
    _effect(
        connection, "own-verified", earlier, json.dumps({"plan": {"n": 1}}), "verified"
    )
    compare()
    # A later companion claim on the same decision ties on its ordinal; the
    # lower rowid wins, as the original's scan order made it.
    _effect(
        connection,
        "companion-verified",
        "human_decision_x",
        _companion(earlier),
        "verified",
    )
    compare()
    connection.execute(
        "DELETE FROM field_effects WHERE plan_id=?",
        ("effect_plan_" + _digest("own-verified"),),
    )
    _effect(
        connection,
        "own-verified-later",
        earlier,
        json.dumps({"plan": {"n": 2}}),
        "verified",
    )
    compare()
    # Live states each lookup filters on, by either authority, in either order.
    for state in ("planned", "claimed", "retryable", "applying", "superseded"):
        _effect(
            connection,
            f"companion-{state}",
            "human_decision_y",
            _companion(earlier),
            state,
        )
        _effect(connection, f"own-{state}", earlier, "{}", state)
        compare()
    # Both arms on one row, missing paths and non-text companion values.
    _effect(connection, "both", earlier, _companion(earlier), "planned")
    for label, document in {
        "empty": "{}",
        "no-authority": json.dumps({"plan": {"subtitle_companion": {}}}),
        "plan-text": json.dumps({"plan": "text"}),
        "integer": _companion(5),
        "real": _companion(1.5),
        "true": _companion(True),
        "null": _companion(None),
        "object": _companion({"decision_id": earlier}),
        "array": _companion([earlier]),
    }.items():
        _effect(connection, label, "human_decision_" + label, document, "claimed")
    compare()
    # Every lookup's answer changed at least once, so the oracle compared
    # something other than empty results throughout.
    assert all(len(answers) > 1 for answers in seen.values()), seen


def test_remaining_authority_lookups_reach_effects_by_index_only(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    _, lookups = _lookups(connection)
    for name, (statement, parameters) in lookups.items():
        plan = _plan(connection, statement, ())
        assert not _scans_effects(plan), name
        assert any("INDEX field_effects_decision " in step for step in plan), name
        assert any(
            "INDEX field_effects_companion_decision " in step for step in plan
        ), name
        assert _scans_effects(_plan(connection, _ORIGINAL_LOOKUPS[name], parameters))
    for index_name, definition in (
        ("field_effects_decision", _FIELD_EFFECTS_DECISION_INDEX_SQL),
        (
            "field_effects_companion_decision",
            _FIELD_EFFECTS_COMPANION_DECISION_INDEX_SQL,
        ),
    ):
        connection.execute(f"DROP INDEX {index_name}")
        for statement, _ in lookups.values():
            assert _scans_effects(_replanned(connection, statement)), index_name
        connection.execute(definition)
    bare = "json_extract(document," + _COMPANION_PATH + ")"
    for statement, _ in lookups.values():
        altered = statement.replace(_FIELD_EFFECT_COMPANION_DECISION_SQL, bare)
        assert _scans_effects(_plan(connection, altered, ()))


# The reads #1204 gave a partial or a target index, unchanged in what they ask:
# each partial index serves only a statement whose state list is its own.
def _effect_steps(plan: tuple[str, ...]) -> list[str]:
    return [step for step in plan if re.match(f"(SCAN|SEARCH) {_EFFECTS}", step)]


def _quietly(call: Callable[[], object]) -> Callable[[], None]:
    # The synthetic documents are not valid operations; only the statement
    # each read issues before decoding one is under test here.
    def run() -> None:
        try:
            call()
        except FieldEffectError:
            pass

    return run


def test_live_and_target_reads_reach_effects_by_their_indexes(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    repository = SQLiteMetadataReportRepository(connection)
    case_id, document = connection.execute(
        "SELECT review_case_id,document FROM review_cases ORDER BY review_case_id LIMIT 1"
    ).fetchone()
    target = ReviewCase.from_json(document).target
    work = (
        str(target["connection"]),
        str(target["library"]),
        str(target["item"]),
        "description",
    )
    changed = {
        "plan": {
            "authority": {"review_case_id": case_id},
            "pre_value": "before",
            "desired_value": "after",
        }
    }
    # Updated times run against insertion order, so an index order and a scan
    # order differ wherever a read's answer depends on order.
    for index, state in enumerate(
        ("planned", "claimed", "retryable", "applying", "reconciling", "verified") * 3
    ):
        _effect(
            connection,
            f"live-{index}",
            "human_decision_live",
            json.dumps(changed),
            state,
            updated_at=100 - index * 7 % 31,
            target=(
                work if index % 4 == 0 else ("primary", "books", f"w{index}", "title")
            ),
        )
    # Several verified changing writes qualify, so the target read's first row
    # is an answer its order decides.
    for label in ("same", "again", "later"):
        changed["plan"]["pre_value"] = "before " + label
        _effect(
            connection,
            label,
            "human_decision_s",
            json.dumps(changed),
            "verified",
            1,
            work,
        )
    connection.commit()
    binding = DescriptionAdviceBinding(
        review_case_id=case_id,
        report_id="report_" + _digest("report"),
        proposal_set_id="proposal_set_" + _digest("proposal-set"),
        target_fingerprint=_digest("fingerprint"),
        policy_generation=1,
        access_revision=1,
        preferences_digest=_digest("preferences"),
    )
    reads = {
        "claim": (
            lambda: repository.next_field_effect_plan_id(None),
            ["SCAN field_effects USING INDEX field_effects_ready"],
            "field_effects_ready",
        ),
        "alternating claim": (
            lambda: repository.next_field_effect_plan_id("books"),
            ["SCAN field_effects USING INDEX field_effects_ready"],
            "field_effects_ready",
        ),
        "waiting": (
            _quietly(repository._field_effect_activity._activity_current_waiting),
            ["SCAN effect USING INDEX field_effects_waiting"],
            "field_effects_waiting",
        ),
        "description target": (
            _quietly(lambda: repository._description_advice._target_unchanged(binding)),
            [
                "SEARCH field_effects USING INDEX field_effects_target "
                "(connection_name=? AND library_name=? AND work_id=? AND destination=?)"
            ],
            "field_effects_target",
        ),
    }
    statements = {}
    for name, (call, expected, _) in reads.items():
        (statement,) = (
            traced
            for traced in _traced_selects(connection, call)
            if "FROM field_effects" in traced
        )
        statements[name] = statement
        assert _effect_steps(_plan(connection, statement, ())) == expected, name
    # Each answer is the one the read gave before #1204, order included.
    originals = _before_1204(connection, [(sql, ()) for sql in statements.values()])
    for (name, statement), original in zip(statements.items(), originals, strict=True):
        assert original, name
        if name == "description target":
            assert (
                len(connection.execute(statement.replace(" LIMIT 1", "")).fetchall())
                == 3
            )
        assert connection.execute(statement).fetchall() == original, name
    for name, statement in statements.items():
        index_name = reads[name][2]
        connection.execute("SAVEPOINT dropped")
        connection.execute(f"DROP INDEX {index_name}")
        assert _scans_effects(_replanned(connection, statement)), name
        connection.execute("ROLLBACK TO dropped")
        connection.execute("RELEASE dropped")
    # A state list spelled other than the shared one loses its partial index.
    for name, states in (
        ("claim", _FIELD_EFFECT_READY_STATES_SQL),
        ("waiting", _FIELD_EFFECT_WAITING_STATES_SQL),
    ):
        respelled = "(" + ",".join(reversed(states[1:-1].split(","))) + ")"
        altered = statements[name].replace(states, respelled)
        assert altered != statements[name]
        assert _scans_effects(_plan(connection, altered, ())), name


# The consent revocation as it stood before #1204, which the claim's partial
# index would serve, and so reorder, without its explicit rowid order.
_ORIGINAL_CONSENT_REVOCATION = (
    "SELECT document,state FROM field_effects WHERE "
    "(json_extract(document,'$.plan.consent.consent_id')=? OR "
    "json_extract(document,'$.plan.subtitle_companion.consent.consent_id')=?) "
    "AND state IN ('planned','retryable')"
)


def test_consent_revocation_supersedes_in_its_original_order(
    store: tuple[sqlite3.Connection, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, _ = store
    consent_id = "effect_consent_" + _digest("consent")
    connection.execute(
        "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) "
        "VALUES(?,1,0,?)",
        (consent_id, json.dumps({"consent_id": consent_id, "current": True})),
    )
    own: dict[str, object] = {"plan": {"consent": {"consent_id": consent_id}}}
    companion: dict[str, object] = {
        "plan": {"subtitle_companion": {"consent": {"consent_id": consent_id}}}
    }
    other: dict[str, object] = {
        "plan": {"consent": {"consent_id": "effect_consent_other"}}
    }
    for index, (state, document) in enumerate(
        (
            ("planned", own),
            ("retryable", companion),
            ("verified", own),
            ("planned", other),
            ("retryable", own),
            ("planned", companion),
            ("claimed", companion),
            ("planned", own),
        )
    ):
        # Later rows were updated earlier, so the claim index runs backwards.
        _effect(
            connection,
            f"consent-{index}",
            "human_decision_consent",
            json.dumps({**document, "n": index}),
            state,
            updated_at=100 - index,
        )
    connection.commit()
    (original,) = _before_1204(
        connection, [(_ORIGINAL_CONSENT_REVOCATION, (consent_id, consent_id))]
    )
    assert len(original) == 5
    assert original != sorted(original, key=lambda row: -json.loads(row[0])["n"])

    class Operation:
        document: str

        @staticmethod
        def from_json(document: str) -> Operation:
            operation = Operation()
            operation.document = document
            return operation

        def transition(self, *_: object, **__: object) -> str:
            return self.document

    effects = SQLiteMetadataReportRepository(connection)._field_effects
    superseded: list[tuple[object, str]] = []
    events: list[object] = []
    monkeypatch.setattr(field_effects, "FieldEffectOperation", Operation)
    monkeypatch.setattr(
        effects,
        "_update_field_effect",
        lambda operation, state: superseded.append((operation, state)),
    )
    monkeypatch.setattr(
        effects,
        "_record_field_effect_event",
        lambda operation, kind, at: events.append(operation),
    )
    monkeypatch.setattr(effects, "_schedule_field_effect_cleanup", lambda *_: None)
    revoked: list[int] = []
    (statement,) = (
        traced
        for traced in _traced_selects(
            connection,
            lambda: revoked.append(
                effects.revoke_field_effect_consent(consent_id, 200)
            ),
        )
        if "FROM field_effects" in traced
    )
    assert revoked == [len(original)]
    assert superseded == original
    assert events == [document for document, _ in original]
    # Only live effects are walked, through the claim's partial index.
    assert _effect_steps(_plan(connection, statement, ())) == [
        "SCAN field_effects USING INDEX field_effects_ready"
    ]


def test_description_application_history_breaks_tied_times_as_displayed(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from tests.support.description_advice import _guarded_application, _prepared
    from tests.support.field_effects import CAPABILITY

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    decision = store.admit(
        candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
    )
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    application, _ = _guarded_application(repository, tmp_path, authority)
    operation = application.plan(
        decision,
        connection=snapshot.connection,
        library=snapshot.library,
        work_id=snapshot.work_id,
        destination=snapshot.destination,
        pre_value=snapshot.pre_value,
        desired_value=snapshot.decision_value,
        target_fingerprint=snapshot.target_fingerprint,
        target_version=snapshot.target_version,
        dependency_digests=snapshot.dependency_digests,
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    connection = repository._connection
    plan_id = operation.plan.plan_id
    sequence, event_document = connection.execute(
        "SELECT sequence,document FROM field_effect_events WHERE plan_id=? "
        "ORDER BY sequence DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    # Seven applications share one second, across both arms of the read, and
    # are stored in ascending event-id order, so any order but the displayed
    # one keeps a different four.
    tied = 10**9
    for offset, (digit, kind) in enumerate(
        zip(
            "13579b",
            ("planned", "claimed", "applying", "reconciling", "retryable", "stale"),
            strict=True,
        ),
        start=1,
    ):
        connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,"
            "occurred_at,document) VALUES(?,?,?,?,?,?)",
            (
                "effect_event_" + digit * 64,
                plan_id,
                sequence + offset,
                kind,
                tied,
                event_document,
            ),
        )
    connection.execute(
        "INSERT INTO field_effect_no_changes(decision_id,review_case_id,report_id,"
        "connection_name,library_name,work_id,destination,target_fingerprint,"
        "target_version,origin,occurred_at,event_id) "
        "SELECT decision_id,?,?,connection_name,library_name,work_id,destination,"
        "?,'v','automatic_policy',?,? FROM field_effects WHERE plan_id=?",
        (
            binding.review_case_id,
            binding.report_id,
            binding.target_fingerprint,
            tied,
            "effect_event_" + "8" * 64,
            plan_id,
        ),
    )
    connection.commit()
    limited = repository.get_review_case_history(binding.review_case_id, 4)
    assert [(event["kind"], event["state"]) for event in limited] == [
        ("description_application", "stale"),
        ("description_application", "retryable"),
        ("description_application", "no_change_required"),
        ("description_application", "reconciling"),
    ]
    assert limited == repository.get_review_case_history(binding.review_case_id, 50)[:4]
    repository.close()


# #1205: a pending decision's case must hold a current lineage. Both statements
# asked that with a correlated EXISTS for each decision, which the planner served
# by scanning every lineage (the counts owner arm only on older SQLite, such as
# the shipped image's 3.40), and did the same for the automatic arm's lineage
# and package. Each is now an uncorrelated IN list built once per statement.
# These are the texts as they stood at #1204.
_PACKAGE_JOIN = (
    "current_review_packages package ON package.connection_name=lineage.connection_name "
    "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
    "AND package.proposal_set_id=lineage.proposal_set_id AND package.report_id=lineage.report_id "
    "AND package.review_case_id=lineage.review_case_id AND package.checkpoint=lineage.checkpoint"
)
_HUMAN_LINEAGE = (
    "(EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
    "WHERE lineage.review_case_id=human.review_case_id) OR (",
    "(human.review_case_id IN (SELECT review_case_id FROM current_review_case_lineages) OR (",
)


def _automatic_lineage(outcome: str) -> tuple[str, str]:
    extract = "json_extract(automatic.document,'$.outcome')" + outcome
    return (
        "EXISTS (SELECT 1 FROM current_review_case_lineages lineage JOIN "
        + _PACKAGE_JOIN
        + " WHERE lineage.review_case_id=automatic.review_case_id) AND "
        + extract
        + " AND NOT EXISTS (",
        extract
        + " AND +automatic.review_case_id IN (SELECT lineage.review_case_id FROM "
        "current_review_case_lineages lineage JOIN "
        + _PACKAGE_JOIN
        + ") AND NOT EXISTS (",
    )


_CURRENT_OWNER_DECISION = (
    "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
    "WHERE current.review_case_id=human.review_case_id AND "
    "current.destination=human.destination)"
)
_LINEAGE_REWRITES = {
    "documents": (
        _HUMAN_LINEAGE,
        _automatic_lineage(" IN ('candidate','keep_current')"),
    ),
    "counts": (
        (
            "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
            "WHERE lineage.review_case_id=human.review_case_id) AND "
            + _CURRENT_OWNER_DECISION
            + " AND ",
            "CASE WHEN human.review_case_id IN (SELECT review_case_id FROM "
            "current_review_case_lineages) THEN "
            + _CURRENT_OWNER_DECISION
            + " END AND ",
        ),
        _automatic_lineage("='candidate'"),
    ),
}
# SHA-256 of each statement's text at 2dcf75cd, as this module's store traces it.
_HUMAN_PLANS = "human.outcome IN ('accept','owner_entered')"
_STATEMENTS_1204 = {
    "documents": "e62a8aca670e77687afeda2585f588070e80386e773408a62ef496edca4e4d16",
    "cursor": "820b901727766eaf3a516804cfe1cbd283381fd59b3f9ce36eea506539448608",
    "example": "7aa5ac04c56ef298a1b0f1e05b08a2cce050309bec37fd2706c4d9cb629a769f",
    "counts": "6efa0744ef03ff2d0723e653f68c6ee54cbf941cdd295f921f9055918a1f8f0c",
}


def _before_1205(name: str, statement: str) -> str:
    for before, after in _LINEAGE_REWRITES[
        "counts" if name == "counts" else "documents"
    ]:
        assert statement.count(after) == 1, name
        statement = statement.replace(after, before)
    return statement


def _answer(connection: sqlite3.Connection, statement: str) -> object:
    try:
        return connection.execute(statement).fetchall()
    except sqlite3.OperationalError as error:
        return ("refused", str(error))


def _assert_lineage_oracle(
    connection: sqlite3.Connection, statements: dict[str, str]
) -> dict[str, object]:
    answers = {}
    for name, statement in statements.items():
        answers[name] = _answer(connection, statement)
        assert answers[name] == _answer(connection, _before_1205(name, statement)), name
    return answers


def _lineage_row(connection: sqlite3.Connection, case_id: str) -> tuple[Any, ...]:
    return tuple(
        connection.execute(
            "SELECT connection_name,library_name,item_id,proposal_set_id,report_id,"
            "review_case_id,transition_id,checkpoint,outcome "
            "FROM current_review_case_lineages WHERE review_case_id=?",
            (case_id,),
        ).fetchone()
    )


def _forget_lineage(connection: sqlite3.Connection, case_id: str) -> None:
    for table in ("current_review_case_lineages", "current_review_packages"):
        connection.execute(f"DELETE FROM {table} WHERE review_case_id=?", (case_id,))


def _pending_ids(connection: sqlite3.Connection, statement: str) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            statement.replace(
                "SELECT pending.document FROM", "SELECT pending.decision_id FROM", 1
            )
        )
    ]


def test_lineage_membership_restores_the_1204_text(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    # #1285 later let an owner-entered value plan as an accept does.
    restored = {
        name: hashlib.sha256(
            _before_1205(name, statement)
            .replace(_HUMAN_PLANS, "human.outcome='accept'", 1)
            .encode()
        ).hexdigest()
        for name, statement in _statements(connection).items()
    }
    assert restored == _STATEMENTS_1204


def test_lineage_membership_equals_the_correlated_test_across_lineage_shapes(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, pending = store
    repository = SQLiteMetadataReportRepository(connection)
    statements = _statements(connection)
    before = _assert_lineage_oracle(connection, statements)
    automatic = [value for value in pending if value.startswith("automatic_")]
    cases = dict(
        connection.execute(
            "SELECT decision_id,review_case_id FROM automatic_decisions "
            "UNION ALL SELECT decision_id,review_case_id FROM human_decisions"
        ).fetchall()
    )
    # Retired, no-case and tombstoned lineages carry no case at all.
    template = _lineage_row(connection, cases[automatic[0]])
    for index, outcome in enumerate(("retired", "no_case", "tombstoned")):
        row = (*template[:2], f"caseless-{index}", *template[3:5], None, *template[6:8])
        connection.execute(
            "INSERT INTO current_review_case_lineages VALUES(?,?,?,?,?,?,?,?,?)",
            (*row, outcome),
        )
    # One case current under two items, each with its own matching package:
    # still one pending row per decision.
    doubled = cases[automatic[1]]
    row = _lineage_row(connection, doubled)
    connection.execute(
        "INSERT INTO current_review_case_lineages VALUES(?,?,?,?,?,?,?,?,?)",
        (row[0], row[1], "second-item", *row[3:]),
    )
    connection.execute(
        "INSERT INTO current_review_packages(connection_name,library_name,item_id,"
        "proposal_set_id,report_id,review_case_id,checkpoint,outcome) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (row[0], row[1], "second-item", row[3], row[4], row[5], row[7], row[8]),
    )
    # A case whose package no longer matches its lineage: its owner decision
    # stays pending, its automatic decision does not.
    mismatched = cases[automatic[2]]
    connection.execute(
        "UPDATE current_review_packages SET checkpoint=checkpoint+1 "
        "WHERE review_case_id=?",
        (mismatched,),
    )
    # A case with no current lineage at all.
    forgotten = cases[automatic[3]]
    _forget_lineage(connection, forgotten)
    # An owner cover decision on a lineage-less case reaches the pending set
    # only through its current selected consideration.
    cover_case, consideration = connection.execute(
        "SELECT review_case_id,consideration_id FROM current_cover_considerations "
        "ORDER BY review_case_id LIMIT 1"
    ).fetchone()
    cover_id = "human_decision_" + _digest("cover-owner")
    connection.execute(
        "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
        "target_fingerprint,destination,actor,outcome,selected_value_document,"
        "idempotency_digest,request_digest,occurred_at,document) "
        "VALUES(?,?,?,?,'cover','owner','accept','null',?,?,0,?)",
        (
            cover_id,
            cover_case,
            "review_proposal_" + _digest("cover-proposal"),
            _digest("cover-fingerprint"),
            _digest("cover-idempotency"),
            _digest("cover-request"),
            json.dumps({"decision_id": cover_id}),
        ),
    )
    connection.execute(
        "INSERT INTO cover_consideration_events(event_id,consideration_id,"
        "review_case_id,kind,decision_id,occurred_at,document) "
        "VALUES(?,?,?,'selected',?,0,'{}')",
        (
            "cover_consideration_event_" + _digest("selected"),
            consideration,
            cover_case,
            cover_id,
        ),
    )
    _forget_lineage(connection, cover_case)
    connection.commit()
    after = _assert_lineage_oracle(connection, statements)
    assert after != before
    ids = _pending_ids(connection, statements["documents"])
    assert len(ids) == len(set(ids))
    assert cover_id in ids
    owner = {value: case for value, case in cases.items() if value.startswith("human_")}
    for value, case in cases.items():
        if case == doubled:
            assert value in ids
        if case == mismatched:
            assert (value in ids) == value.startswith("human_")
        if case == forgotten:
            assert value not in ids
    assert any(case == mismatched for case in owner.values())
    counts = repository.pending_field_effect_decision_counts()
    assert sum(int(str(row["count"])) for row in counts) > 0


# Each corrupted document sits on a case with a lineage and on one without; a
# refusal depends on which rows reach the JSON function, so the membership test
# must not filter a row before the corrective check or outcome extraction see it.
@pytest.mark.parametrize("lineage", [True, False], ids=["lineage", "no-lineage"])
@pytest.mark.parametrize("corrupted", ["review", "owner", "automatic"])
def test_lineage_membership_refuses_malformed_documents_as_before(
    store: tuple[sqlite3.Connection, list[str]], corrupted: str, lineage: bool
) -> None:
    connection, pending = store
    statements = _statements(connection)
    automatic_id = next(value for value in pending if value.startswith("automatic_"))
    (case_id,) = connection.execute(
        "SELECT review_case_id FROM automatic_decisions WHERE decision_id=?",
        (automatic_id,),
    ).fetchone()
    earlier_id, ordinal = connection.execute(
        "SELECT decision_id,ordinal FROM human_decisions WHERE review_case_id=? "
        "AND destination='authors'",
        (case_id,),
    ).fetchone()
    # A corrective keep_current after a verified effect of the case's earlier
    # owner decision reads the case's review document.
    later_id = "human_decision_" + _digest("corrective")
    connection.execute(
        "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
        "target_fingerprint,destination,actor,outcome,selected_value_document,"
        "idempotency_digest,request_digest,occurred_at,document) "
        "SELECT ?,review_case_id,proposal_id,target_fingerprint,destination,actor,"
        "'keep_current',selected_value_document,?,?,occurred_at+1,? "
        "FROM human_decisions WHERE ordinal=?",
        (
            later_id,
            _digest("corrective-idempotency"),
            _digest("corrective-request"),
            json.dumps({"decision_id": later_id}),
            ordinal,
        ),
    )
    _effect(connection, "corrective-prior", earlier_id, "{}", "verified")
    if not lineage:
        _forget_lineage(connection, case_id)
    table, key, trigger = {
        "review": ("review_cases", "review_case_id", "review_cases_no_update"),
        "owner": ("human_decisions", "decision_id", "human_decisions_no_update"),
        "automatic": (
            "automatic_decisions",
            "decision_id",
            "automatic_decisions_no_update",
        ),
    }[corrupted]
    connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(
        f"UPDATE {table} SET document='{{not json' WHERE {key}=?",
        ({"review": case_id, "owner": later_id, "automatic": automatic_id}[corrupted],),
    )
    connection.commit()
    answers = _assert_lineage_oracle(connection, statements)
    # Every statement that reads past the first pending row reaches the
    # corrupted document, lineage or not; the unordered example stops first.
    refused = ("refused", "malformed JSON")
    assert [name for name, answer in answers.items() if answer == refused] == [
        "documents",
        "cursor",
        "counts",
    ]


def _plan_tree(connection: sqlite3.Connection, statement: str) -> list[tuple[str, ...]]:
    """Each plan step with the details of the steps it sits under."""
    rows = connection.execute(
        f"EXPLAIN QUERY PLAN {statement} -- {next(_REPLANS)}"
    ).fetchall()
    parents = {row[0]: row[1] for row in rows}
    details = {row[0]: row[3] for row in rows}
    paths = []
    for step, _, _, detail in rows:
        path = [detail]
        while parents.get(step, 0) in details:
            step = parents[step]
            path.append(details[step])
        paths.append(tuple(path))
    return paths


def _scans_lineage_per_decision(connection: sqlite3.Connection, statement: str) -> bool:
    return any(
        re.match(r"SCAN (lineage|current_review_case_lineages)\b", path[0])
        and any(step.startswith("CORRELATED") for step in path[1:])
        for path in _plan_tree(connection, statement)
    )


def _arm_drivers(connection: sqlite3.Connection, statement: str) -> set[str]:
    return {
        path[0]
        for path in _plan_tree(connection, statement)
        if re.match(r"(SCAN|SEARCH) (human|automatic)\b", path[0])
        and not any(step.startswith(("CORRELATED", "LIST")) for step in path[1:])
    }


def _lineage_memberships(
    connection: sqlite3.Connection, statement: str
) -> tuple[int, int]:
    """How many lineage lists a statement builds, and how many case-index probes."""
    tree = _plan_tree(connection, statement)
    return (
        sum(path[0].startswith("LIST SUBQUERY") for path in tree),
        sum(
            path[0] == "USING INDEX current_review_case_lineages_case FOR IN-OPERATOR"
            for path in tree
        ),
    )


def test_lineage_lists_are_built_once_and_arms_keep_their_scans(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, _ = store
    for name, statement in _statements(connection).items():
        assert not _scans_lineage_per_decision(connection, statement), name
        assert _arm_drivers(connection, statement) == {"SCAN human", "SCAN automatic"}
        # The owner arm's membership test probes #1211's case index once per
        # decision; the automatic arm's, a join, still builds its list once.
        assert _lineage_memberships(connection, statement) == (1, 1), name
        assert not connection.in_transaction
        connection.execute("BEGIN")
        try:
            connection.execute("DROP INDEX current_review_case_lineages_case")
            connection.execute("DROP INDEX current_review_packages_case")
            # Without that index both arms build their list once, and the
            # correlated test is the per-decision scan being removed; the index
            # serves it by search instead.
            assert _lineage_memberships(connection, statement) == (2, 0), name
            assert _scans_lineage_per_decision(
                connection, _before_1205(name, statement)
            )
        finally:
            connection.execute("ROLLBACK")
        # Without the unary plus the planner drives the automatic arm from the
        # lineage list, so the arm's rows arrive in case order, not rowid order.
        bare = statement.replace(
            "+automatic.review_case_id IN", "automatic.review_case_id IN"
        )
        assert "SCAN automatic" not in _arm_drivers(connection, bare), name


def test_example_keeps_rowid_order_without_an_order_by(
    store: tuple[sqlite3.Connection, list[str]],
) -> None:
    connection, pending = store
    statement = _statements(connection)["example"]
    # Only automatic decisions remain pending: a live effect claims each owner
    # decision.
    for value in pending:
        if value.startswith("human_"):
            _effect(connection, "claimed-" + value, value, "{}")
    rows = connection.execute(
        "SELECT decision_id,review_case_id FROM automatic_decisions ORDER BY rowid"
    ).fetchall()
    pending_rows = [row for row in rows if row[0] in pending]
    first, later = next(
        (first, later)
        for index, first in enumerate(pending_rows)
        for later in pending_rows[index + 1 :]
        if later[1] < first[1]
    )
    for value, _ in rows:
        if value not in (first[0], later[0]):
            _effect(connection, "claimed-" + value, value, "{}")
    connection.commit()
    (answer,) = connection.execute(statement).fetchall()
    assert json.loads(answer[0])["decision_id"] == first[0]
    assert [answer] == connection.execute(_before_1205("example", statement)).fetchall()
    bare = statement.replace(
        "+automatic.review_case_id IN", "automatic.review_case_id IN"
    )
    (other,) = connection.execute(bare).fetchall()
    assert json.loads(other[0])["decision_id"] == later[0]
