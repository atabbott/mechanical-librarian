"""Runtime-owned serialized Activity facts, admitted by exact SQLite witnesses."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, cast

from mechanical_librarian.domain.activity_records import ReadableActivityEvent
from mechanical_librarian.domain.readable_activity import ISBN_REFUSAL_REASONS

_TABLES = (
    "metadata_reports",
    "review_cases",
    "proposal_sets",
    "shadow_decision_outcomes",
    "automatic_decisions",
    "human_decisions",
    "human_decision_events",
    "cover_considerations",
    "cover_consideration_events",
    "cover_selection_authorities",
    "field_effect_events",
    "field_effect_no_changes",
    "metadata_policy_drafts",
    "description_advice_candidates",
    "description_decisions",
    "field_effects",
    "description_advice_attempts",
    "metadata_policy_transitions",
    "metadata_policy_resolutions",
    "current_cover_considerations",
)
Key = tuple[str, bytes]
Candidate = tuple[str, str]
# A changed revision re-verifies this many previously admitted append-only rows
# (ADR-0082, #1200); construction still witnesses every row.
_REVERIFY_ROWS = 256
# (_TABLES index, last re-verified rowid); None is the table's start, so rowid
# zero and negative rowids are swept as well.
Cursor = tuple[int, int | None]
_START: Cursor = (0, None)
# The only trigger shape that proves a table refuses every UPDATE or DELETE.
_REFUSAL = re.compile(
    r"CREATE TRIGGER \w+ BEFORE (UPDATE|DELETE) ON (\w+) "
    r"BEGIN SELECT RAISE\(ABORT, ?'[^']*'\); END"
)
_ROWID_NAMES = frozenset({"rowid", "oid", "_rowid_"})
_MARK_BYTES = 16  # max(rowid) and count(*) per append-only table.
_CURSOR_BYTES = 16  # Re-verification table index and rowid.
_CASE_CHUNK = 500


def _encoded(values: object) -> bytes:
    def encode(value: object) -> object:
        if type(value) is bytes:
            return {"b": base64.b64encode(value).decode("ascii")}
        if type(value) is float:
            return {"f": value.hex()}
        if type(value) in (tuple, list):
            return [
                encode(item) for item in cast(tuple[object, ...] | list[object], value)
            ]
        return value

    return json.dumps(encode(values), ensure_ascii=True, separators=(",", ":")).encode()


def _key(table: str, *values: object) -> Key:
    return table, _encoded(values)


@dataclass(frozen=True, slots=True)
class _Witness:
    value: bytes
    dependencies: frozenset[Key]
    aliases: frozenset[Key]
    candidates: frozenset[Candidate]


@dataclass(frozen=True, slots=True)
class ActivityFact:
    event: ReadableActivityEvent
    search: str
    occurred_at: int
    event_type: str
    event_id: str


class ActivityFactCapacity(Exception):
    """The next serialized fact would exceed the process-owned content bound."""


def activity_fact_bytes(key: Candidate, fact: ActivityFact) -> int:
    return (
        len(key[0])
        + len(key[1])
        + len(fact.event.document)
        + len(fact.search.encode())
        + len(fact.event_type)
        + len(fact.event_id)
        + 16  # Timestamp and sorted-reference slot.
    )


@dataclass(frozen=True, slots=True, eq=False)
class _Snapshot:
    witnesses: dict[str, dict[Key, _Witness]]
    facts: dict[Candidate, ActivityFact]
    ordered: tuple[ActivityFact, ...]
    accounted_bytes: int
    fact_bytes: int
    witness_bytes: dict[str, int]
    # Append-only table -> (max(rowid), count(*)) in the witnessing snapshot.
    marks: dict[str, tuple[int, int]]
    version: object = field(default_factory=object)


@dataclass(frozen=True, slots=True)
class _Shape:
    names: tuple[str, ...]
    primary: tuple[int, ...]
    documents: tuple[int, ...]
    selections: str


def _shape(connection: sqlite3.Connection, table: str) -> _Shape:
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    names = tuple(column[1] for column in columns)
    primary = tuple(
        i
        for _, i in sorted(
            (column[5], i) for i, column in enumerate(columns) if column[5]
        )
    )
    documents = tuple(i for i, name in enumerate(names) if name.endswith("document"))
    selections = [
        f"CAST({name} AS BLOB)" if i in documents else name
        for i, name in enumerate(names)
    ]
    selections += [f"typeof({names[i]})" for i in documents]
    return _Shape(names, primary, documents, ",".join(selections))


def _append_only(
    connection: sqlite3.Connection, shapes: dict[str, _Shape]
) -> frozenset[str]:
    """Derive the trigger-enforced append-only rowid tables from the live schema.

    Only an exact unconditional refusal of both UPDATE and DELETE qualifies; a
    table without both, a WITHOUT ROWID table, or one whose column shadows its
    rowid is re-witnessed in full on every changed revision.
    """
    refusals: dict[str, set[str]] = defaultdict(set)
    for table, sql in connection.execute(
        "SELECT tbl_name,sql FROM sqlite_schema WHERE type='trigger'"
    ):
        match = _REFUSAL.fullmatch(sql) if type(sql) is str else None
        if match is not None and match[2] == table:
            refusals[table].add(match[1])
    rowid_tables = {
        name
        for name, without_rowid in connection.execute(
            # pragma_table_list needs SQLite 3.37 or later.
            "SELECT name,wr FROM pragma_table_list WHERE schema='main' AND type='table'"
        )
        if without_rowid == 0
    }
    return frozenset(
        table
        for table in _TABLES
        if refusals[table] == {"UPDATE", "DELETE"}
        and table in rowid_tables
        and not _ROWID_NAMES & {name.lower() for name in shapes[table].names}
    )


def _value(row: Sequence[object], documents: tuple[int, ...]) -> bytes:
    values = list(row)
    for i in documents:
        if values[i] is not None:
            values[i] = hashlib.sha256(cast(bytes, values[i])).digest()
    return _encoded(values)


def _witness_bytes(key: Key, witness: _Witness) -> int:
    return (
        len(key[0])
        + len(key[1])
        + len(witness.value)
        + sum(len(k[0]) + len(k[1]) for k in witness.dependencies | witness.aliases)
        + sum(len(k[0]) + len(k[1]) for k in witness.candidates)
    )


def _reverify(
    connection: sqlite3.Connection,
    previous: _Snapshot,
    cursor: Cursor,
    shapes: dict[str, _Shape],
    append_only: frozenset[str],
) -> tuple[frozenset[str], Cursor]:
    """Re-verify the next bounded slice of rows admitted before this refresh.

    The cursor walks every admitted append-only row in (table, rowid) order, so
    an in-place change that preserves rowid and count is found within
    ceil(rows / slice) refreshes. A disagreeing table is re-witnessed in full.
    """
    index, after = cursor
    eligible = append_only & previous.marks.keys()
    suspect: set[str] = set()
    budget = _REVERIFY_ROWS
    # At most one lap plus a return to the starting table.
    for _ in range(len(_TABLES) + 1):
        if not eligible or budget == 0:
            break
        table = _TABLES[index]
        if table in eligible:
            shape = shapes[table]
            admitted = previous.witnesses[table]
            read = 0
            mark = previous.marks[table][0]
            rows = (
                connection.execute(
                    f"SELECT rowid,{shape.selections} FROM {table} "
                    "WHERE rowid<=? ORDER BY rowid LIMIT ?",
                    (mark, budget),
                )
                if after is None
                else connection.execute(
                    f"SELECT rowid,{shape.selections} FROM {table} "
                    "WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                    (after, mark, budget),
                )
            )
            try:
                for rowid, *row in rows:
                    read += 1
                    after = rowid
                    witness = admitted.get(
                        _key(table, *(row[i] for i in shape.primary))
                    )
                    if witness is None or witness.value != _value(row, shape.documents):
                        suspect.add(table)
                        break
            finally:
                rows.close()
            budget -= read
            if budget == 0 and table not in suspect:
                return frozenset(suspect), (index, after)
        index, after = (index + 1) % len(_TABLES), None
    return frozenset(suspect), (index, after)


def _bindings(
    table: str, row: dict[str, Any], connection: sqlite3.Connection
) -> tuple[frozenset[Key], frozenset[Key], frozenset[Candidate]]:
    dependencies: set[Key] = set()
    aliases: set[Key] = set()
    candidates: set[Candidate] = set()

    def dependency(name: str, *values: object) -> None:
        if all(value is not None for value in values):
            dependencies.add(_key(name, *values))

    def candidate(kind: str, identity: str) -> None:
        candidates.add((kind, identity))

    if table in {
        "review_cases",
        "proposal_sets",
        "shadow_decision_outcomes",
        "cover_considerations",
        "field_effect_no_changes",
    }:
        dependency("metadata_reports", row.get("report_id"))
    if table in {"review_cases", "shadow_decision_outcomes"}:
        dependency("proposal_sets", row.get("proposal_set_id"))
    if table in {
        "shadow_decision_outcomes",
        "automatic_decisions",
        "human_decisions",
        "human_decision_events",
        "cover_considerations",
        "cover_consideration_events",
        "field_effect_no_changes",
        "description_decisions",
    }:
        dependency("review_cases", row.get("review_case_id"))
    if table == "shadow_decision_outcomes":
        document = json.loads(row["document"])
        # Only ISBN-refusal membership reads policy authority. Keep every
        # outcome candidate below so a source change can enter that membership.
        if any(
            type(member) is dict
            and member.get("destination") == "isbn"
            and member.get("reason_code") in ISBN_REFUSAL_REASONS
            for member in document["outcomes"]
        ):
            case = connection.execute(
                "SELECT CAST(document AS BLOB) FROM review_cases WHERE review_case_id=?",
                (row["review_case_id"],),
            ).fetchone()
            if case is not None:
                dependency(
                    "isbn_resolution",
                    document["policy_generation"],
                    json.loads(case[0])["target"]["library"],
                )
            if document["policy_generation"] == 1:
                dependency("isbn_fallback", document["policy_profile_id"])
        aliases.add(
            _key(
                "outcome_identity",
                *(
                    row[name]
                    for name in (
                        "review_case_id",
                        "report_id",
                        "proposal_set_id",
                        "resolved_policy_id",
                    )
                ),
            )
        )
        candidate("isbn_refusal", row["outcome_set_id"] + ":isbn")
    elif table == "human_decisions":
        aliases.add(_key("human_identity", row["decision_id"]))
    elif table == "human_decision_events":
        dependency("human_identity", row["decision_id"])
        candidate("human_decision", row["event_id"])
    elif table == "automatic_decisions":
        document = json.loads(row["document"])
        dependency("metadata_reports", document["report_id"])
        dependency("proposal_sets", document["proposal_set_id"])
        dependency(
            "outcome_identity",
            *(
                document[name]
                for name in (
                    "review_case_id",
                    "report_id",
                    "proposal_set_id",
                    "resolved_policy_id",
                )
            ),
        )
        candidate("automatic_decision", row["decision_id"])
    elif table == "cover_consideration_events":
        dependency("cover_considerations", row["consideration_id"])
        dependency("cover_identity", row["decision_id"])
        candidate("cover_safeguard", row["event_id"])
    elif table == "cover_selection_authorities":
        dependency("cover_considerations", row["consideration_id"])
        aliases.add(_key("cover_identity", row["decision_id"]))
        if row["kind"] == "automatic":
            document = json.loads(json.loads(row["document"])["decision_document"])
            for name, field in (
                ("review_cases", "review_case_id"),
                ("metadata_reports", "report_id"),
                ("proposal_sets", "proposal_set_id"),
                ("current_cover_considerations", "review_case_id"),
            ):
                dependency(name, document[field])
            dependency(
                "resolution_identity",
                document["resolved_policy_id"],
                document["policy_generation"],
                row["library_name"],
            )
            candidate("automatic_decision", row["decision_id"])
    elif table == "field_effect_events":
        dependency("field_effects", row["plan_id"])
        candidate("field_effect", row["plan_id"])
    elif table == "field_effects":
        for name in (
            "human_identity",
            "automatic_decisions",
            "cover_identity",
            "description_decisions",
        ):
            dependency(name, row["decision_id"])
        candidate("field_effect", row["plan_id"])
    elif table == "field_effect_no_changes":
        candidate("field_effect_no_change", row["event_id"])
    elif table == "description_advice_attempts":
        document = json.loads(row["binding_document"])
        dependency("review_cases", document["review_case_id"])
        dependency("candidate_attempt", row["attempt_id"])
        candidate("description_preparation", row["attempt_id"])
    elif table == "description_advice_candidates":
        aliases.add(_key("candidate_attempt", row["attempt_id"]))
    elif table == "description_decisions":
        dependency("description_advice_candidates", row["candidate_id"])
        candidate("description_decision", row["decision_id"])
    elif table == "metadata_policy_transitions":
        dependency("metadata_policy_drafts", row["from_policy_id"])
        dependency("metadata_policy_drafts", row["to_policy_id"])
        candidate("policy_transition", row["transition_id"])
    elif table == "metadata_policy_drafts":
        aliases.add(_key("isbn_fallback", row["policy_id"]))
    elif table == "metadata_policy_resolutions":
        aliases.add(_key("isbn_resolution", row["generation"], row["library_name"]))
        aliases.add(
            _key(
                "resolution_identity",
                row["resolved_policy_id"],
                row["generation"],
                row["library_name"],
            )
        )
    return frozenset(dependencies), frozenset(aliases), frozenset(candidates)


def _witness(
    connection: sqlite3.Connection,
    table: str,
    shape: _Shape,
    row: Sequence[object],
    old: _Witness | None,
    rebind: bool,
) -> _Witness:
    value = _value(row, shape.documents)
    if old is not None and old.value == value and not rebind:
        return old
    # Only changed documents needed for a dependency binding are decoded, and
    # never retained after this row.
    decoded = dict(zip(shape.names, row, strict=False))
    dependencies, aliases, candidates = _bindings(table, decoded, connection)
    return _Witness(value, dependencies, aliases, candidates)


def _rebind_outcomes(
    connection: sqlite3.Connection,
    shape: _Shape,
    mark: int,
    cases: list[object],
    rows: dict[Key, _Witness],
) -> tuple[dict[Key, _Witness], int, set[Key]] | None:
    """Rebuild retained outcome bindings whose review case was just appended.

    A full witness rebinds every outcome whose case witness changed; an
    appended case is such a change. Returns the rows, their byte delta and the
    changed keys, or None when a retained row carries an unwitnessed key.
    """
    delta = 0
    changed: set[Key] = set()
    retained = rows
    for start in range(0, len(cases), _CASE_CHUNK):
        chunk = cases[start : start + _CASE_CHUNK]
        found = connection.execute(
            f"SELECT {shape.selections} FROM shadow_decision_outcomes "
            f"WHERE rowid<=? AND review_case_id IN ({','.join('?' * len(chunk))})",
            (mark, *chunk),
        )
        try:
            for row in found:
                key = _key("shadow_decision_outcomes", *(row[i] for i in shape.primary))
                old = retained.get(key)
                if old is None:
                    return None
                new = _witness(
                    connection, "shadow_decision_outcomes", shape, row, old, True
                )
                if new != old:
                    if rows is retained:
                        rows = dict(retained)
                    rows[key] = new
                    changed.add(key)
                    delta += _witness_bytes(key, new) - _witness_bytes(key, old)
        finally:
            found.close()
    return rows, delta, changed


class ActivityFactStore:
    """One root-lifetime store; readers borrow immutable published snapshots."""

    def __init__(self, max_bytes: int = 64 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._snapshot: _Snapshot | None = None
        # The rolling re-verification cursor lives beside the published
        # snapshot so an unchanged refresh keeps the published identity while
        # the sweep still advances.
        self._cursor = _START
        self._disabled = False

    def snapshot(self) -> _Snapshot | None:
        with self._lock:
            return self._snapshot

    def refresh(
        self,
        connection: sqlite3.Connection,
        project: Callable[[set[Candidate] | None, int], dict[Candidate, ActivityFact]],
        *,
        full: bool = False,
    ) -> _Snapshot | None:
        """Admit this borrower's snapshot; ``full`` witnesses every row.

        A new borrower generation passes ``full`` so that it streams every
        source witness in its own snapshot and compares it with the previous
        snapshot, instead of admitting append-only tables from their marks.
        Only candidates whose witnesses differ are reprojected.
        """
        with self._lock:
            if self._disabled:
                return None
            previous = self._snapshot
            # A full witness re-verifies every row, so the sweep restarts.
            cursor = self._cursor if previous is not None and not full else _START
        # Every witness below is read from this borrower's own SQLite snapshot.
        # No object parser or shared connection revision can stand in for it.
        shapes = {table: _shape(connection, table) for table in _TABLES}
        append_only = _append_only(connection, shapes)
        suspect: frozenset[str] = frozenset()
        if previous is not None and not full:
            suspect, cursor = _reverify(
                connection, previous, cursor, shapes, append_only
            )
        witnesses: dict[str, dict[Key, _Witness]] = {}
        witness_bytes: dict[str, int] = {}
        marks: dict[str, tuple[int, int]] = {}
        changed: set[Key] = set()
        appended_cases: list[object] = []
        cases_rewitnessed = False
        accounted = _CURSOR_BYTES
        for table in _TABLES:
            shape = shapes[table]
            statement = f"SELECT {shape.selections} FROM {table}"
            prior = {} if previous is None else previous.witnesses[table]
            prior_mark = None if previous is None else previous.marks.get(table)
            mark = None
            if table in append_only:
                found = connection.execute(
                    f"SELECT max(rowid),count(*) FROM {table}"
                ).fetchone()
                mark = (0 if found[0] is None else found[0], found[1])
                marks[table] = mark
                accounted += len(table) + _MARK_BYTES
            rows: dict[Key, _Witness] | None = None
            size = 0
            if (
                previous is not None
                and not full
                and mark is not None
                and prior_mark is not None
                and mark[0] >= prior_mark[0]
                and table not in suspect
                and not (table == "shadow_decision_outcomes" and cases_rewitnessed)
            ):
                # Admit only rows above the mark, and only when none carries a
                # previously witnessed key and the count accounts for every
                # row. Deletion, replacement or renumbering re-witnesses all.
                appended: dict[Key, _Witness] = {}
                cases: list[object] = []
                admitted = True
                size = previous.witness_bytes[table]
                above = connection.execute(
                    statement + " WHERE rowid>?", (prior_mark[0],)
                )
                try:
                    for row in above:
                        key = _key(table, *(row[i] for i in shape.primary))
                        if key in prior:
                            admitted = False
                            break
                        appended[key] = _witness(
                            connection, table, shape, row, None, False
                        )
                        size += _witness_bytes(key, appended[key])
                        if accounted + size > self.max_bytes:
                            self._disable()
                            return None
                        if table == "review_cases":
                            cases.append(row[shape.names.index("review_case_id")])
                finally:
                    above.close()
                if admitted and mark[1] == prior_mark[1] + len(appended):
                    rows = {**prior, **appended} if appended else prior
                    if table == "shadow_decision_outcomes" and appended_cases:
                        rebound = _rebind_outcomes(
                            connection, shape, prior_mark[0], appended_cases, rows
                        )
                        if rebound is None:
                            rows = None
                        else:
                            rows, delta, rebound_keys = rebound
                            size += delta
                            changed.update(rebound_keys)
                if rows is not None:
                    changed.update(appended)
                    appended_cases += cases
                    if accounted + size > self.max_bytes:
                        self._disable()
                        return None
            if rows is None:
                rows = {}
                size = 0
                for row in connection.execute(statement):
                    key = _key(table, *(row[i] for i in shape.primary))
                    # The ISBN policy lookup uses the case target's library. A
                    # replaced/missing case must rebuild that binding even when
                    # the outcome document itself is unchanged.
                    rows[key] = _witness(
                        connection,
                        table,
                        shape,
                        row,
                        prior.get(key),
                        table == "shadow_decision_outcomes"
                        and previous is not None
                        and _key(
                            "review_cases", row[shape.names.index("review_case_id")]
                        )
                        in changed,
                    )
                    size += _witness_bytes(key, rows[key])
                    if accounted + size > self.max_bytes:
                        self._disable()
                        return None
                differing = {
                    key
                    for key in rows.keys() | prior.keys()
                    if rows.get(key) != prior.get(key)
                }
                changed.update(differing)
                cases_rewitnessed |= table == "review_cases" and bool(differing)
            witnesses[table] = rows
            witness_bytes[table] = size
            accounted += size
        if previous is not None and not changed:
            if marks == previous.marks:
                result = previous
            else:
                # Unchanged facts keep their version, so borrowers' stamps stay
                # current; only the marks move.
                accounted += previous.fact_bytes
                if accounted > self.max_bytes:
                    self._disable()
                    return None
                result = _Snapshot(
                    witnesses,
                    previous.facts,
                    previous.ordered,
                    accounted,
                    previous.fact_bytes,
                    witness_bytes,
                    marks,
                    previous.version,
                )
            self._publish(previous, result, cursor)
            return result
        affected: set[Candidate] | None
        if previous is None:
            affected = None
            facts: dict[Candidate, ActivityFact] = {}
        else:
            # Reverse edges are needed only while computing a changed revision.
            # Retain each forward binding once; discard this temporary traversal
            # index before parsing any affected canonical candidates.
            reverse: dict[Key, set[Key]] = defaultdict(set)
            currentness: dict[Key, set[Candidate]] = defaultdict(set)
            for nodes in (witnesses, previous.witnesses):
                for table_rows in nodes.values():
                    for key, node in table_rows.items():
                        for dependency in node.dependencies:
                            if dependency[0] in {
                                "current_cover_considerations",
                                "resolution_identity",
                                "isbn_resolution",
                                "isbn_fallback",
                            }:
                                # Currentness and ISBN policy lookups affect
                                # these candidates, not ordinary retained
                                # decision lineage.
                                currentness[dependency].update(node.candidates)
                            else:
                                reverse[dependency].add(key)
                        reverse[key].update(node.aliases)
            affected = set()
            pending = deque(changed)
            reached = set(changed)
            while pending:
                key = pending.popleft()
                affected.update(currentness.get(key, ()))
                for nodes in (witnesses, previous.witnesses):
                    reached_witness = nodes.get(key[0], {}).get(key)
                    if reached_witness is not None:
                        affected.update(reached_witness.candidates)
                for dependent in reverse.get(key, ()):
                    if dependent not in reached:
                        pending.append(dependent)
                        reached.add(dependent)
            del reverse, currentness
            facts = {
                key: value
                for key, value in previous.facts.items()
                if key not in affected
            }
        state_bytes = accounted
        accounted += sum(activity_fact_bytes(key, fact) for key, fact in facts.items())
        if accounted > self.max_bytes:
            self._disable()
            return None
        if affected is None or affected:
            try:
                refreshed = project(affected, self.max_bytes - accounted)
            except ActivityFactCapacity:
                self._disable()
                return None
            accounted += sum(
                activity_fact_bytes(key, fact) for key, fact in refreshed.items()
            )
            facts.update(refreshed)
        ordered = tuple(
            sorted(
                facts.values(),
                key=lambda fact: (fact.occurred_at, fact.event_id),
                reverse=True,
            )
        )
        result = _Snapshot(
            witnesses,
            facts,
            ordered,
            accounted,
            accounted - state_bytes,
            witness_bytes,
            marks,
        )
        self._publish(previous, result, cursor)
        return result

    def _publish(
        self, expected: _Snapshot | None, result: _Snapshot, cursor: Cursor
    ) -> None:
        with self._lock:
            if self._snapshot is expected:
                self._snapshot = result
                self._cursor = cursor

    def _disable(self) -> None:
        with self._lock:
            self._snapshot = None
            self._cursor = _START
            self._disabled = True
