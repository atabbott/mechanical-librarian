from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.title_review_cases import (
    SQLiteTitleReviewCaseRepository,
    TitleReviewCasePersistenceError,
    _transition_id,
)
from mechanical_librarian.adapters.persistence.title_proposals import (
    SQLiteTitleProposalRepository,
)
from mechanical_librarian.application.title_proposals import scenario_lineage
from mechanical_librarian.application.title_review_cases import (
    ReviewTransitionKind,
    TitleReviewCaseRunError,
)
from mechanical_librarian.bootstrap.composition import (
    _run_synthetic_title_proposal_report,
    _run_synthetic_title_review_case_report,
)
from mechanical_librarian.infrastructure.persistence.sqlite import (
    connect,
    connect_existing,
    migrate,
)
from mechanical_librarian.domain.title_proposals import (
    TitleProposalResult,
    TitleProposalSet,
    proposal_set_from_manifest,
)
from mechanical_librarian.domain.title_review_cases import (
    review_target_from_proposal_set,
)
from tests.unit.test_title_proposals import manifest as title_manifest


ZERO_SCENARIOS = (
    "current-equivalent",
    "current-missing",
    "current-unavailable",
    "local-provider-conflict",
    "f06-missing",
    "f06-ambiguous",
    "f06-unusable",
    "provider-disabled",
    "provider-failed",
    "provider-empty",
    "provider-no-match",
)


def _review_rows(database: Path) -> tuple[list[tuple], list[tuple], list[tuple]]:
    connection = sqlite3.connect(database)
    try:
        return (
            connection.execute(
                "SELECT * FROM synthetic_title_review_cases ORDER BY case_id"
            ).fetchall(),
            connection.execute(
                "SELECT * FROM synthetic_title_review_case_transitions ORDER BY lineage, sequence"
            ).fetchall(),
            connection.execute(
                "SELECT * FROM current_synthetic_title_review_cases ORDER BY lineage"
            ).fetchall(),
        )
    finally:
        connection.close()


def _audit_rows(database: Path) -> list[tuple]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT id, kind, created_at, summary FROM audit_events ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


def _proposal(
    result: TitleProposalResult,
    *,
    previous: TitleProposalSet | None = None,
    stale: bool = False,
) -> TitleProposalSet:
    source = title_manifest()
    if previous is not None:
        source["lineage"] = previous.lineage
        source["lineage_revision"] = previous.lineage_revision + 1
        source["previous_set_id"] = previous.set_id
        source["scenario"] = "lifecycle-successor"
    if stale:
        source["snapshot"]["current_fingerprint"] = "0" * 64  # type: ignore[index]
    return proposal_set_from_manifest(
        manifest=source,
        result=result,
        proposed_value=(
            "A Synthetic Book" if result is TitleProposalResult.NEEDS_REVIEW else None
        ),
        previous_set_id=None if previous is None else previous.set_id,
    )


def _store_proposal(database: Path, proposal: TitleProposalSet) -> None:
    connection = connect(database)
    try:
        migrate(connection)
        SQLiteTitleProposalRepository(connection).store(proposal)
    finally:
        connection.close()


class SyntheticTitleReviewCaseIntegrationTests(unittest.TestCase):
    def test_admission_restart_and_exact_replay_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            proposal = _run_synthetic_title_proposal_report(database, "agree-empty")
            first = _run_synthetic_title_review_case_report(database, "agree-empty")
            state = _review_rows(database)
            replay = _run_synthetic_title_review_case_report(database, "agree-empty")

            self.assertEqual(first.admission.source_set_id, proposal.proposal_set.set_id)
            self.assertIsNotNone(first.admission.current_case)
            self.assertFalse(first.admission.replayed)
            self.assertEqual(replay.admission.current_case, first.admission.current_case)
            self.assertTrue(replay.admission.replayed)
            self.assertEqual(_review_rows(database), state)
            data = json.loads(replay.to_json())
            self.assertEqual(data["outcome"], "current_case")
            self.assertEqual(data["authority"], "synthetic-report-only")
            self.assertIs(data["production_applicable"], False)

    def test_first_admission_accepts_current_qualifying_revision_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            before = _run_synthetic_title_proposal_report(
                database, "supersede-before"
            )
            after = _run_synthetic_title_proposal_report(database, "supersede-after")

            first = _run_synthetic_title_review_case_report(database, "supersede-after")
            state = _review_rows(database)
            replay = _run_synthetic_title_review_case_report(
                database, "supersede-after"
            )

            self.assertEqual(before.proposal_set.lineage_revision, 1)
            self.assertEqual(after.proposal_set.lineage_revision, 2)
            self.assertEqual(
                after.proposal_set.previous_set_id,
                before.proposal_set.set_id,
            )
            current = first.admission.current_case
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.source_revision, 2)
            self.assertIsNone(first.admission.previous_case)
            self.assertEqual(
                first.admission.transition_kind,
                ReviewTransitionKind.CREATED,
            )
            self.assertEqual(first.admission.case_history_count, 1)
            self.assertEqual(first.admission.transition_count, 1)
            self.assertFalse(first.admission.replayed)
            self.assertEqual(replay.admission.current_case, first.admission.current_case)
            self.assertTrue(replay.admission.replayed)
            self.assertEqual(_review_rows(database), state)

    def test_every_current_zero_proposal_result_records_no_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for scenario in ZERO_SCENARIOS:
                with self.subTest(scenario=scenario):
                    database = Path(directory) / f"{scenario}.sqlite"
                    _run_synthetic_title_proposal_report(database, scenario)
                    report = _run_synthetic_title_review_case_report(database, scenario)
                    self.assertIsNone(report.admission.current_case)
                    self.assertEqual(json.loads(report.to_json())["outcome"], "no_case")
                    cases, transitions, pointers = _review_rows(database)
                    self.assertEqual(cases, [])
                    self.assertEqual(len(transitions), 1)
                    self.assertEqual(transitions[0][11], "no_case")
                    self.assertEqual(len(pointers), 1)
                    self.assertIsNone(pointers[0][7])

    def test_stale_source_is_rejected_for_every_prior_state_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for prior_result in (
                None,
                TitleProposalResult.NEEDS_REVIEW,
                TitleProposalResult.NO_CHANGE,
            ):
                with self.subTest(prior_result=prior_result):
                    database = Path(directory) / f"stale-{prior_result}.sqlite"
                    previous = None
                    if prior_result is not None:
                        previous = _proposal(prior_result)
                        _store_proposal(database, previous)
                        connection = connect_existing(database)
                        try:
                            SQLiteTitleReviewCaseRepository(connection).admit_current(
                                previous.lineage
                            )
                        finally:
                            connection.close()
                    stale = _proposal(
                        TitleProposalResult.STALE_INPUT,
                        previous=previous,
                        stale=True,
                    )
                    _store_proposal(database, stale)
                    original = _review_rows(database)
                    connection = connect_existing(database)
                    try:
                        with self.assertRaises(TitleReviewCasePersistenceError):
                            SQLiteTitleReviewCaseRepository(connection).admit_current(
                                stale.lineage
                            )
                    finally:
                        connection.close()
                    self.assertEqual(_review_rows(database), original)

    def test_terminal_no_case_state_rejects_every_changed_successor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for result in (
                TitleProposalResult.NEEDS_REVIEW,
                TitleProposalResult.NO_CHANGE,
            ):
                with self.subTest(result=result):
                    database = Path(directory) / f"terminal-{result}.sqlite"
                    initial = _proposal(TitleProposalResult.NO_CHANGE)
                    _store_proposal(database, initial)
                    connection = connect_existing(database)
                    try:
                        SQLiteTitleReviewCaseRepository(connection).admit_current(
                            initial.lineage
                        )
                    finally:
                        connection.close()
                    original = _review_rows(database)
                    successor = _proposal(result, previous=initial)
                    _store_proposal(database, successor)
                    connection = connect_existing(database)
                    try:
                        with self.assertRaises(TitleReviewCasePersistenceError):
                            SQLiteTitleReviewCaseRepository(connection).admit_current(
                                successor.lineage
                            )
                    finally:
                        connection.close()
                    self.assertEqual(_review_rows(database), original)

    def test_current_qualifying_successor_supersedes_immutable_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "supersede-before")
            before = _run_synthetic_title_review_case_report(database, "supersede-before")
            _run_synthetic_title_proposal_report(database, "supersede-after")
            after = _run_synthetic_title_review_case_report(database, "supersede-after")
            replay = _run_synthetic_title_review_case_report(database, "supersede-before")

            self.assertIsNotNone(before.admission.current_case)
            self.assertIsNotNone(after.admission.current_case)
            self.assertNotEqual(
                before.admission.current_case_id, after.admission.current_case_id
            )
            self.assertEqual(after.admission.previous_case, before.admission.current_case)
            self.assertEqual(after.admission.transition_kind.value, "superseded")
            self.assertEqual(replay.admission.current_case, after.admission.current_case)
            self.assertTrue(replay.admission.replayed)
            cases, transitions, pointers = _review_rows(database)
            self.assertEqual(len(cases), 2)
            self.assertEqual(len(transitions), 2)
            self.assertEqual(pointers[0][7], after.admission.current_case_id)

    def test_nonqualifying_successor_retires_without_empty_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "retire-before")
            before = _run_synthetic_title_review_case_report(database, "retire-before")
            _run_synthetic_title_proposal_report(database, "retire-after")
            retired = _run_synthetic_title_review_case_report(database, "retire-after")
            reverse = _run_synthetic_title_review_case_report(database, "retire-before")

            self.assertIsNotNone(before.admission.current_case)
            self.assertIsNone(retired.admission.current_case)
            self.assertEqual(retired.admission.previous_case, before.admission.current_case)
            self.assertEqual(retired.admission.transition_kind.value, "retired")
            self.assertIsNone(reverse.admission.current_case)
            self.assertTrue(reverse.admission.replayed)
            cases, transitions, pointers = _review_rows(database)
            self.assertEqual(len(cases), 1)
            self.assertEqual(len(transitions), 2)
            self.assertIsNone(pointers[0][7])

    def test_each_publication_boundary_rolls_back_and_preserves_prior_case(self) -> None:
        hooks = (
            "after_case_insert",
            "after_transition_insert",
            "after_pointer_update",
            "before_readback",
        )
        with tempfile.TemporaryDirectory() as directory:
            for hook in hooks:
                with self.subTest(hook=hook):
                    database = Path(directory) / f"{hook}.sqlite"
                    _run_synthetic_title_proposal_report(database, "supersede-before")
                    before = _run_synthetic_title_review_case_report(
                        database, "supersede-before"
                    )
                    _run_synthetic_title_proposal_report(database, "supersede-after")
                    sentinel = (
                        "unrelated-review-sentinel",
                        "review",
                        "2026-07-15T10:00:00.000000Z",
                        "review requested",
                    )
                    sentinel_connection = sqlite3.connect(database)
                    try:
                        sentinel_connection.execute(
                            "INSERT INTO audit_events(id, kind, created_at, summary) "
                            "VALUES (?, ?, ?, ?)",
                            sentinel,
                        )
                        sentinel_connection.commit()
                    finally:
                        sentinel_connection.close()
                    original = _review_rows(database)
                    original_audit = _audit_rows(database)
                    connection = connect_existing(database)
                    try:
                        migrate(connection)

                        def fail() -> None:
                            raise RuntimeError("injected")

                        repository = SQLiteTitleReviewCaseRepository(
                            connection, **{hook: fail}
                        )
                        with self.assertRaisesRegex(RuntimeError, "injected"):
                            repository.admit_current(
                                scenario_lineage("supersede-after")
                            )
                    finally:
                        connection.close()
                    self.assertEqual(_review_rows(database), original)
                    self.assertEqual(_audit_rows(database), original_audit)
                    restored = _run_synthetic_title_review_case_report(
                        database, "supersede-after"
                    )
                    self.assertEqual(restored.admission.previous_case, before.admission.current_case)

    def test_tampered_current_case_pointer_fails_closed_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            _run_synthetic_title_review_case_report(database, "agree-empty")
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                connection.execute(
                    "UPDATE current_synthetic_title_review_cases SET current_case_id = NULL"
                )
            finally:
                connection.close()
            state = _review_rows(database)
            with self.assertRaises(TitleReviewCasePersistenceError):
                connection = connect_existing(database)
                try:
                    SQLiteTitleReviewCaseRepository(connection).admit_current(
                        scenario_lineage("agree-empty")
                    )
                finally:
                    connection.close()
            self.assertEqual(_review_rows(database), state)

    def test_arbitrary_and_cross_lineage_admission_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            connection = connect_existing(database)
            try:
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(connection).admit_current(
                        "arbitrary-lineage"
                    )
            finally:
                connection.close()
            self.assertEqual(_review_rows(database), ([], [], []))

            _run_synthetic_title_review_case_report(database, "agree-empty")
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE current_synthetic_title_review_cases "
                    "SET lineage = 'synthetic-title-proposal-cross-lineage'"
                )
            finally:
                connection.close()
            state = _review_rows(database)
            connection = connect_existing(database)
            try:
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(connection).admit_current(
                        scenario_lineage("agree-empty")
                    )
            finally:
                connection.close()
            self.assertEqual(_review_rows(database), state)

    def test_tampered_canonical_case_fails_closed_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            report = _run_synthetic_title_review_case_report(database, "agree-empty")
            case_id = report.admission.current_case_id
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                connection.execute("DROP TRIGGER synthetic_title_review_cases_no_update")
                serialized = connection.execute(
                    "SELECT canonical_case FROM synthetic_title_review_cases WHERE case_id = ?",
                    (case_id,),
                ).fetchone()[0]
                value = json.loads(serialized)
                value["production_applicable"] = True
                connection.execute(
                    "UPDATE synthetic_title_review_cases SET canonical_case = ? WHERE case_id = ?",
                    (json.dumps(value, separators=(",", ":"), sort_keys=True), case_id),
                )
            finally:
                connection.close()
            state = _review_rows(database)
            connection = connect_existing(database)
            try:
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(connection).admit_current(
                        scenario_lineage("agree-empty")
                    )
            finally:
                connection.close()
            self.assertEqual(_review_rows(database), state)

    def test_tampered_transition_identity_fails_closed_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            _run_synthetic_title_review_case_report(database, "agree-empty")
            replacement_id = f"transition-{'0' * 53}"
            connection = sqlite3.connect(database, isolation_level=None)
            try:
                connection.execute(
                    "DROP TRIGGER synthetic_title_review_case_transitions_no_update"
                )
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "UPDATE synthetic_title_review_case_transitions "
                    "SET transition_id = ?",
                    (replacement_id,),
                )
                connection.execute(
                    "UPDATE current_synthetic_title_review_cases "
                    "SET last_transition_id = ?",
                    (replacement_id,),
                )
            finally:
                connection.close()
            state = _review_rows(database)
            connection = connect_existing(database)
            try:
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(connection).admit_current(
                        scenario_lineage("agree-empty")
                    )
            finally:
                connection.close()
            self.assertEqual(_review_rows(database), state)

    def test_schema_valid_lifecycle_corruption_fails_for_every_transition_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scenarios = (
                (
                    "created",
                    ("agree-empty",),
                    ReviewTransitionKind.NO_CASE,
                    "record current synthetic no-case result",
                ),
                (
                    "no-case",
                    ("provider-empty",),
                    ReviewTransitionKind.NO_CASE,
                    "retire current synthetic review case",
                ),
                (
                    "superseded",
                    ("supersede-before", "supersede-after"),
                    ReviewTransitionKind.CREATED,
                    "admit current synthetic review case",
                ),
                (
                    "retired",
                    ("retire-before", "retire-after"),
                    ReviewTransitionKind.NO_CASE,
                    "record current synthetic no-case result",
                ),
            )
            for name, steps, replacement_kind, replacement_reason in scenarios:
                with self.subTest(name=name):
                    database = Path(directory) / f"{name}.sqlite"
                    report = None
                    for scenario in steps:
                        _run_synthetic_title_proposal_report(database, scenario)
                        report = _run_synthetic_title_review_case_report(
                            database, scenario
                        )
                    assert report is not None
                    current = report.admission.current_case
                    replacement_id = (
                        current.case_id
                        if replacement_kind is ReviewTransitionKind.CREATED
                        and current is not None
                        else None
                    )
                    connection = sqlite3.connect(database, isolation_level=None)
                    try:
                        source_json = connection.execute(
                            "SELECT sets.canonical_set FROM synthetic_title_proposal_sets AS sets "
                            "JOIN current_synthetic_title_proposals AS current "
                            "ON current.set_id = sets.set_id WHERE current.lineage = ?",
                            (scenario_lineage(steps[-1]),),
                        ).fetchone()[0]
                        source = TitleProposalSet.from_json(source_json)
                        target = review_target_from_proposal_set(source)
                        transition_id = _transition_id(
                            target,
                            source.lineage,
                            report.admission.transition_count,
                            source.set_id,
                            None,
                            replacement_id,
                            replacement_kind,
                            replacement_reason,
                        )
                        trigger_sql = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                            "AND name = 'synthetic_title_review_case_transitions_no_update'"
                        ).fetchone()[0]
                        connection.execute(
                            "DROP TRIGGER synthetic_title_review_case_transitions_no_update"
                        )
                        connection.execute(
                            "UPDATE synthetic_title_review_case_transitions SET "
                            "transition_id = ?, prior_case_id = NULL, "
                            "replacement_case_id = ?, kind = ?, reason = ? "
                            "WHERE sequence = ?",
                            (
                                transition_id,
                                replacement_id,
                                replacement_kind.value,
                                replacement_reason,
                                report.admission.transition_count,
                            ),
                        )
                        connection.execute(
                            "UPDATE current_synthetic_title_review_cases "
                            "SET current_case_id = ?, last_transition_id = ?",
                            (replacement_id, transition_id),
                        )
                        connection.execute(trigger_sql)
                    finally:
                        connection.close()
                    state = _review_rows(database)
                    with self.assertRaises(TitleReviewCaseRunError):
                        _run_synthetic_title_review_case_report(
                            database, steps[-1]
                        )
                    self.assertEqual(_review_rows(database), state)

    def test_concurrent_identical_admission_converges_on_one_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")

            def admit() -> object:
                connection = connect_existing(database)
                try:
                    migrate(connection)
                    return SQLiteTitleReviewCaseRepository(connection).admit_current(
                        scenario_lineage("agree-empty")
                    )
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: admit(), range(2)))
            self.assertEqual(results[0].current_case, results[1].current_case)  # type: ignore[attr-defined]
            self.assertEqual(sum(not item.replayed for item in results), 1)  # type: ignore[attr-defined]
            cases, transitions, pointers = _review_rows(database)
            self.assertEqual((len(cases), len(transitions), len(pointers)), (1, 1, 1))

    def test_lock_and_read_only_fail_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            locker = connect_existing(database)
            contender = connect_existing(database)
            try:
                locker.execute("BEGIN IMMEDIATE")
                contender.execute("PRAGMA busy_timeout = 1")
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(contender).admit_current(
                        scenario_lineage("agree-empty")
                    )
            finally:
                locker.rollback()
                contender.close()
                locker.close()
            self.assertEqual(_review_rows(database), ([], [], []))

            readonly = sqlite3.connect(
                f"{database.absolute().as_uri()}?mode=ro",
                uri=True,
                isolation_level=None,
            )
            try:
                readonly.execute("PRAGMA foreign_keys = ON")
                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(readonly).admit_current(
                        scenario_lineage("agree-empty")
                    )
            finally:
                readonly.close()
            self.assertEqual(_review_rows(database), ([], [], []))

    def test_disk_full_equivalent_failure_rolls_back_without_partial_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            connection = connect_existing(database)
            try:
                migrate(connection)

                def disk_full() -> None:
                    raise sqlite3.OperationalError("database or disk is full")

                with self.assertRaises(TitleReviewCasePersistenceError):
                    SQLiteTitleReviewCaseRepository(
                        connection, after_transition_insert=disk_full
                    ).admit_current(scenario_lineage("agree-empty"))
            finally:
                connection.close()
            self.assertEqual(_review_rows(database), ([], [], []))

    def test_review_path_constructs_no_evidence_provider_reviewer_or_write_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "private-sentinel.sqlite"
            _run_synthetic_title_proposal_report(database, "agree-empty")
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.generated_synthetic_epub",
                    side_effect=AssertionError("must not generate media"),
                ),
                patch(
                    "mechanical_librarian.adapters.providers.synthetic_title.SyntheticTitleProvider.lookup",
                    side_effect=AssertionError("must not invoke a provider"),
                ),
            ):
                report = _run_synthetic_title_review_case_report(
                    database, "agree-empty"
                )
            serialized = report.to_json()
            self.assertNotIn(str(database), serialized)
            self.assertNotIn("private-sentinel", serialized)
            self.assertNotIn("authorization", serialized.casefold())
            connection = sqlite3.connect(database)
            try:
                for table in (
                    "audit_events",
                    "workflow_runs",
                    "jobs",
                    "inventory_snapshots",
                    "inventory_v2_snapshots",
                    "inventory_scans",
                    "inventory_polls",
                ):
                    self.assertEqual(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone(),
                        (0,),
                    )
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(synthetic_title_review_cases)"
                    )
                }
                self.assertNotIn("production_applicable", columns)
                self.assertNotIn("decision", columns)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
