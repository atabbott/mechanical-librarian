"""Exactness of the incremental Activity witness and its rolling re-verification.

ADR-0082 as amended by #1200 admits trigger-enforced append-only tables from
their high-water mark and re-verifies a bounded slice of retained rows on every
changed revision. Each oracle here compares the incremental store with a full
witness of the same bytes, and the borrower's pages with the canonical
projection, across every mutation route the amendment names.
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence import activity_facts
from mechanical_librarian.adapters.persistence.activity_facts import (
    _TABLES,
    ActivityFactStore,
    _append_only,
    _shape,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.activity_seed import SEED_NOW, _report, seed_activity
from tests.support.audiobook_reports import synthetic_audiobook_isbn_reason_report
from tests.support.field_effects import (  # noqa: F401
    _EffectEnvironment,
    effects,
)

# A small slice makes the lap long enough to observe on a seeded ledger.
SLICE = 4
MUTABLE = (
    "field_effect_no_changes",
    "field_effects",
    "description_advice_attempts",
    "metadata_policy_transitions",
    "metadata_policy_resolutions",
    "current_cover_considerations",
)
QUERIES = (
    (None, "all", 90, 1, 25),
    ("title", "all", 90, 1, 25),
    ("no readable match", "all", 30, 1, 100),
    (None, "review", 7, 100, 25),
    (None, "policy", 30, 1, 50),
    (None, "metadata", 90, 1, 100),
    (None, "safety", 90, 1, 25),
    (None, "unknown", 90, 1, 25),
    (None, "all", 90, 2, 25),
    (None, "all", 3, 1, 25),
)


def _outcome(call: Callable[[], object]) -> object:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - a refusal is a compared result.
        return ("refused", type(error), str(error))


def _pages(repository: SQLiteMetadataReportRepository) -> list[object]:
    application = ContinuousGuardedApplication(
        repository,
        {},
        {},
        PersistedFieldEffectAuthority(repository),
        (("primary", "books"),),
        lambda: SEED_NOW + 2 * 86_400,
        lambda *_: None,
        activity_only=True,
    )

    def read(query: tuple[str | None, str, int, int, int]) -> object:
        search, event_type, range_days, page, page_size = query
        return _outcome(
            lambda: application.activity(
                search=search,
                event_type=event_type,
                range_days=range_days,
                page=page,
                page_size=page_size,
            )
        )

    return [read(query) for query in QUERIES]


def _full_witness(path: Path) -> activity_facts._Snapshot:
    """Witness every row of the current bytes, independent of any projection."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        snapshot = ActivityFactStore().refresh(connection, lambda *_: {})
        assert snapshot is not None
        return snapshot
    finally:
        connection.close()


@dataclass
class _Ledger:
    path: Path
    root: SQLiteMetadataReportRepository
    reader: SQLiteMetadataReportRepository
    canonical: SQLiteMetadataReportRepository
    store: ActivityFactStore

    def snapshot(self) -> activity_facts._Snapshot:
        snapshot = self.store.snapshot()
        assert snapshot is not None
        return snapshot

    def commit(self) -> None:
        """Move the revision with an ordinary commit to a mutable source."""
        self.root._connection.execute(
            "UPDATE metadata_policy_transitions SET updated_at=updated_at+1 "
            "WHERE generation=(SELECT min(generation) FROM metadata_policy_transitions)"
        )
        self.root._connection.commit()

    def read(self) -> None:
        _outcome(
            lambda: self.reader.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
        )

    def serving(self) -> bool:
        return self.reader._field_effect_activity._activity_facts is not None

    def exact(self) -> bool:
        """The borrower no longer serves anything a full witness would not."""
        return not self.serving() or (
            self.snapshot().witnesses == _full_witness(self.path).witnesses
        )

    def assert_exact(self) -> None:
        assert _pages(self.reader) == _pages(self.canonical)
        if self.serving():
            current, full = self.snapshot(), _full_witness(self.path)
            assert current.witnesses == full.witnesses
            assert current.marks == full.marks
            assert set(current.witnesses) == set(_TABLES)
            fresh = ActivityFactStore()
            borrower = open_repository(
                self.path, create=False, authority=self.root.authority
            )
            try:
                borrower.configure_activity_page_reader(fresh)
                assert _pages(borrower) == _pages(self.canonical)
                rebuilt = fresh.snapshot()
                assert rebuilt is not None
                assert current.facts == rebuilt.facts
                assert current.ordered == rebuilt.ordered
            finally:
                borrower.close()

    def admitted_rows(self) -> int:
        return sum(count for _, count in self.snapshot().marks.values())

    def advance_into(self, table: str) -> int:
        """Refresh until the cursor has just re-verified a row of ``table``."""
        for _ in range(4 * self.admitted_rows()):
            index, after = self.store._cursor
            if _TABLES[index] == table and after > 0:
                return after
            self.commit()
            self.read()
        raise AssertionError("the re-verification cursor never reached the table")

    def detect(self, bound: int) -> int:
        """Refresh until exact; return how many refreshes detection took."""
        for refreshes in range(1, bound + 1):
            self.commit()
            self.read()
            if self.exact():
                return refreshes
        raise AssertionError(f"not detected within {bound} refreshes")


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Ledger]:
    monkeypatch.setattr(activity_facts, "_REVERIFY_ROWS", SLICE)
    seeded = seed_activity(tmp_path)
    root = seeded.repository
    path = tmp_path / "activity.sqlite"
    reader = open_repository(path, create=False, authority=root.authority)
    canonical = open_repository(path, create=False, authority=root.authority)
    store = ActivityFactStore()
    reader.configure_activity_page_reader(store)
    value = _Ledger(path, root, reader, canonical, store)
    try:
        value.read()
        value.assert_exact()
        yield value
    finally:
        reader.close()
        canonical.close()
        root.close()


def _raw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def _drop_trigger(path: Path, name: str) -> str:
    """Remove a trigger through writable_schema, leaving schema_version alone."""
    connection = _raw(path)
    try:
        before = connection.execute("PRAGMA schema_version").fetchone()
        sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?", (name,)
        ).fetchone()[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute("DELETE FROM sqlite_schema WHERE name=?", (name,))
        connection.execute("PRAGMA writable_schema=OFF")
        assert connection.execute("PRAGMA schema_version").fetchone() == before
        return cast(str, sql)
    finally:
        connection.close()


def _restore_trigger(path: Path, name: str, table: str, sql: str) -> None:
    connection = _raw(path)
    try:
        before = connection.execute("PRAGMA schema_version").fetchone()
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "INSERT INTO sqlite_schema VALUES('trigger',?,?,0,?)", (name, table, sql)
        )
        connection.execute("PRAGMA writable_schema=OFF")
        assert connection.execute("PRAGMA schema_version").fetchone() == before
    finally:
        connection.close()


def _event_row(ledger: _Ledger, rowid: int) -> list[object]:
    return list(
        ledger.root._connection.execute(
            "SELECT event_id,plan_id,sequence,kind,occurred_at,document "
            "FROM field_effect_events WHERE rowid=?",
            (rowid,),
        ).fetchone()
    )


def _changed_document(document: object) -> str:
    value = json.loads(cast(str, document))
    value["oracle"] = True
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _record_decision(ledger: _Ledger, index: int) -> None:
    """Admit a new report, case and owner choice through supported seams."""
    root = ledger.root
    report = _report(f"Oracle title {index}", f"oracle-book-{index}", "3", "4")
    root.publish(report, lambda: True)
    policy = root.current_resolved_metadata_policy(report.target.library)
    assert policy is not None
    proposal_set = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    root.publish_proposal(proposal_set)
    case = ReviewCase.create_from_data(
        json.loads(
            root.get_review_case(
                json.loads(root.admit_review_case(proposal_set))["review_case_id"]
            )
        )["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    root.record_human_decision(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=str(proposal["destination"]),
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key=f"oracle-owner-choice-{index:04d}",
            occurred_at=SEED_NOW + index * 90,
        )
    )


def test_classification_is_derived_from_the_live_refusal_triggers(
    ledger: _Ledger,
) -> None:
    connection = ledger.root._connection
    shapes = {table: _shape(connection, table) for table in _TABLES}
    derived = _append_only(connection, shapes)
    assert derived == set(_TABLES) - set(MUTABLE)
    assert set(ledger.snapshot().marks) == derived
    sql = _drop_trigger(ledger.path, "field_effect_events_no_delete")
    assert _append_only(connection, shapes) == derived - {"field_effect_events"}
    _restore_trigger(
        ledger.path, "field_effect_events_no_delete", "field_effect_events", sql
    )


def test_supported_inserts_are_admitted_incrementally_and_exactly(
    ledger: _Ledger,
) -> None:
    before = ledger.snapshot()
    for index in range(3):
        _record_decision(ledger, index)
        ledger.read()
        ledger.assert_exact()
    after = ledger.snapshot()
    assert after.marks["human_decision_events"] == (
        before.marks["human_decision_events"][0] + 3,
        before.marks["human_decision_events"][1] + 3,
    )
    # Retained rows keep their admitted witness objects.
    for key, witness in before.witnesses["field_effect_events"].items():
        assert after.witnesses["field_effect_events"][key] is witness


def test_insert_or_replace_silent_delete_is_detected_at_the_next_refresh(
    ledger: _Ledger,
) -> None:
    row = _event_row(ledger, 1)
    row[5] = _changed_document(row[5])
    ledger.root._connection.execute(
        "INSERT OR REPLACE INTO field_effect_events VALUES (?,?,?,?,?,?)", row
    )
    ledger.root._connection.commit()
    ledger.read()
    assert ledger.exact()
    ledger.assert_exact()


@pytest.mark.parametrize("table", ("field_effect_events", "human_decision_events"))
def test_deletion_is_detected_by_the_count_at_the_next_refresh(
    ledger: _Ledger, table: str
) -> None:
    sql = _drop_trigger(ledger.path, f"{table}_no_delete")
    raw = _raw(ledger.path)
    try:
        raw.execute(f"DELETE FROM {table} WHERE rowid=(SELECT min(rowid) FROM {table})")
    finally:
        raw.close()
    _restore_trigger(ledger.path, f"{table}_no_delete", table, sql)
    ledger.commit()
    ledger.read()
    assert ledger.exact()
    ledger.assert_exact()


def test_mutable_tables_are_rewitnessed_in_full(ledger: _Ledger) -> None:
    raw = _raw(ledger.path)
    try:
        raw.execute(
            "DELETE FROM metadata_policy_transitions WHERE generation="
            "(SELECT max(generation) FROM metadata_policy_transitions)"
        )
    finally:
        raw.close()
    ledger.read()
    assert ledger.exact()
    ledger.assert_exact()


def _replace_in_place(ledger: _Ledger, rowid: int) -> None:
    row = _event_row(ledger, rowid)
    row[5] = _changed_document(row[5])
    ledger.root._connection.execute(
        "REPLACE INTO field_effect_events"
        "(rowid,event_id,plan_id,sequence,kind,occurred_at,document) "
        "VALUES (?,?,?,?,?,?,?)",
        (rowid, *row),
    )
    ledger.root._connection.commit()


def _corrupt_bytes(ledger: _Ledger, rowid: int) -> None:
    document = cast(str, _event_row(ledger, rowid)[5]).encode()
    raw = ledger.path.read_bytes()
    # A free page can retain an earlier copy; overwrite every copy.
    offsets = [offset for offset in range(len(raw)) if raw.startswith(document, offset)]
    assert offsets
    descriptor = os.open(ledger.path, os.O_WRONLY)
    try:
        for offset in offsets:
            os.pwrite(descriptor, b"0" + b" " * (len(document) - 1), offset)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bypass_trigger(ledger: _Ledger, rowid: int) -> None:
    name = "field_effect_events_no_update"
    sql = _drop_trigger(ledger.path, name)
    raw = _raw(ledger.path)
    try:
        document = raw.execute(
            "SELECT document FROM field_effect_events WHERE rowid=?", (rowid,)
        ).fetchone()[0]
        raw.execute(
            "UPDATE field_effect_events SET document=? WHERE rowid=?",
            (_changed_document(document), rowid),
        )
    finally:
        raw.close()
    _restore_trigger(ledger.path, name, "field_effect_events", sql)


def _blob_write(ledger: _Ledger, rowid: int) -> None:
    raw = _raw(ledger.path)
    try:
        with raw.blobopen("field_effect_events", "document", rowid) as blob:
            blob.seek(0)
            blob.write(b"[")
    finally:
        raw.close()


@pytest.mark.parametrize(
    "mutation",
    (_replace_in_place, _corrupt_bytes, _bypass_trigger, _blob_write),
    ids=("replace_rowid", "raw_bytes", "writable_schema_bypass", "blob_io"),
)
def test_in_place_change_is_detected_by_the_rolling_sweep_within_its_bound(
    ledger: _Ledger, mutation: Callable[[_Ledger, int], None]
) -> None:
    rowid = ledger.advance_into("field_effect_events")
    bound = math.ceil(ledger.admitted_rows() / SLICE)
    marks = ledger.snapshot().marks
    mutation(ledger, rowid)
    # The worst case: the cursor has just passed the changed row, so neither
    # the mark nor the count can see it and a whole lap is needed.
    ledger.commit()
    ledger.read()
    assert ledger.snapshot().marks == marks
    assert ledger.serving() and not ledger.exact()
    detected = 1 + ledger.detect(bound - 1)
    assert 1 < detected <= bound
    ledger.assert_exact()


def test_trigger_removal_without_restoration_rewitnesses_the_table(
    ledger: _Ledger,
) -> None:
    rowid = ledger.advance_into("field_effect_events")
    _drop_trigger(ledger.path, "field_effect_events_no_update")
    raw = _raw(ledger.path)
    try:
        document = raw.execute(
            "SELECT document FROM field_effect_events WHERE rowid=?", (rowid,)
        ).fetchone()[0]
        raw.execute(
            "UPDATE field_effect_events SET document=? WHERE rowid=?",
            (_changed_document(document), rowid),
        )
    finally:
        raw.close()
    ledger.commit()
    ledger.read()
    assert "field_effect_events" not in ledger.snapshot().marks or not ledger.serving()
    assert ledger.exact()
    ledger.assert_exact()


def test_schema_drift_serves_the_canonical_projection(ledger: _Ledger) -> None:
    ledger.root._connection.execute(
        "CREATE INDEX activity_oracle_drift ON field_effect_events(kind)"
    )
    ledger.root._connection.commit()
    ledger.read()
    assert not ledger.serving()
    ledger.assert_exact()


def test_unchanged_revision_with_changed_file_identity_refuses(
    ledger: _Ledger,
) -> None:
    ledger.commit()
    ledger.read()
    status = os.stat(ledger.path)
    os.utime(ledger.path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000))
    with pytest.raises(FieldEffectError, match="^persistence$"):
        ledger.reader.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )


def test_overflow_counts_marks_and_cursor_and_serves_canonical(
    ledger: _Ledger,
) -> None:
    snapshot = ledger.snapshot()
    state = activity_facts._CURSOR_BYTES + sum(
        len(table) + activity_facts._MARK_BYTES for table in snapshot.marks
    )
    witnesses = sum(snapshot.witness_bytes.values())
    assert snapshot.accounted_bytes == state + witnesses + snapshot.fact_bytes
    for maximum in (state + witnesses - 1, snapshot.accounted_bytes - 1):
        bounded = ActivityFactStore(max_bytes=maximum)
        borrower = open_repository(
            ledger.path, create=False, authority=ledger.root.authority
        )
        try:
            borrower.configure_activity_page_reader(bounded)
            assert _pages(borrower) == _pages(ledger.canonical)
            assert bounded.snapshot() is None and bounded._disabled
        finally:
            borrower.close()


def _gap_insert(ledger: _Ledger, rowid: int, source: int, index: int) -> None:
    """Insert a new event into a rowid gap below the mark."""
    row = _event_row(ledger, source)
    raw = _raw(ledger.path)
    try:
        raw.execute(
            "INSERT INTO field_effect_events"
            "(rowid,event_id,plan_id,sequence,kind,occurred_at,document) "
            "VALUES (?,?,?,?,?,?,?)",
            (rowid, f"effect_event_{index:064x}", row[1], 900 + index, *row[3:]),
        )
    finally:
        raw.close()


def test_replacement_key_above_the_mark_is_detected_when_the_count_agrees(
    ledger: _Ledger,
) -> None:
    store = ActivityFactStore()
    marks = _witness_refresh(store, ledger.path).marks["field_effect_events"]
    # The replaced row moves above the mark and leaves a gap; filling it keeps
    # count(*) equal to the recorded count plus the rows above the mark, so
    # only the previously witnessed key above the mark proves the change.
    row = _event_row(ledger, 2)
    row[5] = _changed_document(row[5])
    ledger.root._connection.execute(
        "INSERT OR REPLACE INTO field_effect_events VALUES (?,?,?,?,?,?)", row
    )
    ledger.root._connection.commit()
    _gap_insert(ledger, 2, 1, 1)
    current = _witness_refresh(store, ledger.path)
    assert current.marks["field_effect_events"] == (marks[0] + 1, marks[1] + 1)
    assert current.witnesses == _full_witness(ledger.path).witnesses
    ledger.commit()
    ledger.read()
    assert ledger.exact()
    ledger.assert_exact()


def _delete_event(ledger: _Ledger, where: str) -> None:
    sql = _drop_trigger(ledger.path, "field_effect_events_no_delete")
    raw = _raw(ledger.path)
    try:
        raw.execute(f"DELETE FROM field_effect_events WHERE {where}")
    finally:
        raw.close()
    _restore_trigger(
        ledger.path, "field_effect_events_no_delete", "field_effect_events", sql
    )


def _witness_refresh(store: ActivityFactStore, path: Path) -> activity_facts._Snapshot:
    """Refresh the witness alone; canonical validity of the rows is irrelevant."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        snapshot = store.refresh(connection, lambda *_: {})
        assert snapshot is not None
        return snapshot
    finally:
        connection.close()


def test_decreased_mark_is_detected_when_the_count_agrees(ledger: _Ledger) -> None:
    store = ActivityFactStore()
    _witness_refresh(store, ledger.path)
    _delete_event(ledger, "rowid=5")
    marks = _witness_refresh(store, ledger.path).marks["field_effect_events"]
    assert marks[1] == marks[0] - 1
    # Deleting the newest row and filling the gap keeps count(*) and leaves
    # no row above the mark; only the decreased mark proves the change.
    _delete_event(ledger, "rowid=(SELECT max(rowid) FROM field_effect_events)")
    _gap_insert(ledger, 5, 1, 2)
    current = _witness_refresh(store, ledger.path)
    assert current.marks["field_effect_events"] == (marks[0] - 1, marks[1])
    assert current.witnesses == _full_witness(ledger.path).witnesses


def test_vacuum_renumbering_is_detected_at_the_next_refresh(ledger: _Ledger) -> None:
    row = _event_row(ledger, 1)
    ledger.root._connection.execute(
        "INSERT OR REPLACE INTO field_effect_events VALUES (?,?,?,?,?,?)", row
    )
    ledger.root._connection.commit()
    ledger.read()
    assert ledger.exact()
    before = ledger.snapshot().marks["field_effect_events"]
    raw = _raw(ledger.path)
    try:
        raw.execute("VACUUM")
        after = raw.execute(
            "SELECT max(rowid),count(*) FROM field_effect_events"
        ).fetchone()
    finally:
        raw.close()
    # Rowids may or may not be renumbered; either way the witness is exact.
    assert after[1] == before[1]
    ledger.commit()
    ledger.read()
    assert ledger.exact()
    ledger.assert_exact()


def test_outcome_bindings_follow_their_review_case_exactly(
    effects: _EffectEnvironment,  # noqa: F811
) -> None:
    root = effects.repository
    path = effects.database_path
    report = synthetic_audiobook_isbn_reason_report("conflict")
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    root.enter_shadow_preview(1)
    root.publish(report, lambda: True)
    proposal = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    root.publish_proposal(proposal)
    case_id = json.loads(root.admit_review_case(proposal))["review_case_id"]
    outcome = json.loads(root.persist_shadow_outcome(case_id, 12))
    key = activity_facts._key("shadow_decision_outcomes", outcome["outcome_set_id"])
    lookup = activity_facts._key(
        "isbn_resolution", outcome["policy_generation"], report.target.library
    )
    store = ActivityFactStore()

    def bound() -> bool:
        current = _witness_refresh(store, path)
        assert current.witnesses == _full_witness(path).witnesses
        return lookup in current.witnesses["shadow_decision_outcomes"][key].dependencies

    assert bound()
    case = root._connection.execute(
        "SELECT * FROM review_cases WHERE review_case_id=?", (case_id,)
    ).fetchone()
    facts = root._connection.execute(
        "SELECT * FROM review_case_field_facts WHERE review_case_id=?", (case_id,)
    ).fetchall()
    # A missing case is a re-witnessed review_cases table; its outcomes are
    # rebound in full and lose the case's ISBN policy lookup.
    sql = _drop_trigger(path, "review_cases_no_delete")
    raw = _raw(path)
    try:
        raw.execute(
            "DELETE FROM review_case_field_facts WHERE review_case_id=?", (case_id,)
        )
        raw.execute("DELETE FROM review_cases WHERE review_case_id=?", (case_id,))
    finally:
        raw.close()
    _restore_trigger(path, "review_cases_no_delete", "review_cases", sql)
    assert not bound()
    # The case reappears above the mark; the retained outcome below its own
    # mark is rebound incrementally, exactly as a full witness would.
    raw = _raw(path)
    try:
        raw.execute("BEGIN")
        raw.execute(
            "INSERT INTO review_cases VALUES (" + ",".join("?" * len(case)) + ")",
            case,
        )
        raw.execute(
            "DELETE FROM review_case_field_facts WHERE review_case_id=?", (case_id,)
        )
        for row in facts:
            raw.execute(
                "INSERT INTO review_case_field_facts VALUES ("
                + ",".join("?" * len(row))
                + ")",
                row,
            )
        raw.execute("COMMIT")
    finally:
        raw.close()
    assert bound()


APPEND_ONLY = frozenset(_TABLES) - frozenset(MUTABLE)
SOURCE = Path(__file__).resolve().parents[2] / "src" / "mechanical_librarian"
_REPLACE = re.compile(r"\b(?:OR\s+REPLACE|REPLACE)\s+INTO\s+(\S+)", re.IGNORECASE)
_UPDATE_REPLACE = re.compile(r"\bUPDATE\s+OR\s+REPLACE\s+(\S+)", re.IGNORECASE)
_BYPASS = re.compile(r"writable_schema|blobopen", re.IGNORECASE)


def _literals(tree: ast.AST) -> Iterator[tuple[int, str]]:
    """Yield string literals, with each f-string field shown as ``{}``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            yield (
                node.lineno,
                "".join(
                    part.value
                    if isinstance(part, ast.Constant) and type(part.value) is str
                    else "{}"
                    for part in node.values
                ),
            )


def _bypass_findings(root: Path, append_only: frozenset[str]) -> list[str]:
    findings = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _BYPASS.finditer(text):
            findings.append(f"{path.name}: {match[0]}")
        for line, literal in _literals(ast.parse(text)):
            for pattern in (_REPLACE, _UPDATE_REPLACE):
                for match in pattern.finditer(literal):
                    target = match[1].split("(")[0].strip('"`[]').lower()
                    # An interpolated target cannot be proven mutable.
                    if target in append_only or "{" in target:
                        findings.append(f"{path.name}:{line}: {match[0]}")
    return findings


def test_product_source_never_takes_an_in_place_route_on_append_only_tables() -> None:
    """REPLACE, BLOB I/O and writable_schema bypass the refusal triggers."""
    assert SOURCE.is_dir()
    assert _bypass_findings(SOURCE, APPEND_ONLY) == []


def test_bypass_scan_recognizes_each_route(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "A = 'INSERT OR REPLACE INTO review_cases VALUES (?)'\n"
        "B = 'REPLACE INTO field_effect_events(rowid) VALUES (?)'\n"
        "C = f'insert or replace into {table} values (?)'\n"
        "D = 'UPDATE OR REPLACE human_decisions SET x=1'\n"
        "E = connection.blobopen('metadata_reports', 'document', 1)\n"
        "F = 'PRAGMA writable_schema=ON'\n"
        "G = 'INSERT OR REPLACE INTO field_effects VALUES (?)'\n"
        "H = 'title'.replace('a', 'b')\n",
        encoding="utf-8",
    )
    findings = _bypass_findings(tmp_path, APPEND_ONLY)
    assert len(findings) == 6
    assert not any("field_effects " in finding for finding in findings)


def test_every_incrementally_witnessed_table_carries_both_refusal_triggers(
    tmp_path: Path,
) -> None:
    os.chmod(tmp_path, 0o700)
    repository = open_repository(tmp_path / "schema.sqlite", create=True)
    try:
        connection = repository._connection
        shapes = {table: _shape(connection, table) for table in _TABLES}
        assert _append_only(connection, shapes) == APPEND_ONLY
        for table in APPEND_ONLY:
            triggers = {
                sql
                for (sql,) in connection.execute(
                    "SELECT sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=?",
                    (table,),
                )
            }
            for operation in ("UPDATE", "DELETE"):
                assert any(
                    activity_facts._REFUSAL.fullmatch(sql)
                    and f" BEFORE {operation} ON {table} " in sql
                    for sql in triggers
                ), (table, operation)
            ddl = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            # A declared ON CONFLICT REPLACE would make a plain INSERT delete.
            assert "REPLACE" not in ddl.upper()
            assert "WITHOUT ROWID" not in ddl.upper()
    finally:
        repository.close()


_TRIGGERS = (
    "CREATE TRIGGER t_no_update BEFORE UPDATE ON t "
    "BEGIN SELECT RAISE(ABORT, 'x'); END;"
    "CREATE TRIGGER t_no_delete BEFORE DELETE ON t "
    "BEGIN SELECT RAISE(ABORT,'x'); END;"
)


@pytest.mark.parametrize(
    "ddl,admitted",
    (
        ("", True),
        ("DROP TRIGGER t_no_delete", False),
        ("DROP TRIGGER t_no_update", False),
        (
            "DROP TRIGGER t_no_update;CREATE TRIGGER t_no_update BEFORE UPDATE ON t "
            "WHEN NEW.document<>OLD.document BEGIN SELECT RAISE(ABORT, 'x'); END",
            False,
        ),
        (
            "DROP TRIGGER t_no_update;CREATE TRIGGER t_no_update BEFORE UPDATE OF "
            "document ON t BEGIN SELECT RAISE(ABORT, 'x'); END",
            False,
        ),
        (
            "DROP TRIGGER t_no_delete;CREATE TRIGGER t_no_delete BEFORE DELETE ON t "
            "BEGIN SELECT RAISE(IGNORE); END",
            False,
        ),
        (
            "DROP TRIGGER t_no_delete;CREATE TRIGGER t_no_delete AFTER DELETE ON t "
            "BEGIN SELECT RAISE(ABORT, 'x'); END",
            False,
        ),
        (
            "DROP TRIGGER t_no_delete;CREATE TRIGGER t_no_delete BEFORE DELETE ON t "
            "BEGIN SELECT RAISE(ABORT, 'x'); SELECT 1; END",
            False,
        ),
        (
            "DROP TABLE t;CREATE TABLE t(k TEXT PRIMARY KEY) WITHOUT ROWID;"
            + _TRIGGERS,
            False,
        ),
        (
            "DROP TABLE t;CREATE TABLE t(k TEXT PRIMARY KEY, oid TEXT);" + _TRIGGERS,
            False,
        ),
    ),
)
def test_only_unconditional_refusal_of_both_routes_classifies_a_table(
    monkeypatch: pytest.MonkeyPatch, ddl: str, admitted: bool
) -> None:
    monkeypatch.setattr(activity_facts, "_TABLES", ("t",))
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(
            "CREATE TABLE t(k TEXT PRIMARY KEY, document TEXT);" + _TRIGGERS + ddl
        )
        shapes = {"t": _shape(connection, "t")}
        assert _append_only(connection, shapes) == ({"t"} if admitted else set())
    finally:
        connection.close()
