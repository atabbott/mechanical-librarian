from __future__ import annotations

import json
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.evidence_extractors.synthetic_epub import generated_synthetic_epub
from mechanical_librarian.adapters.persistence.title_proposals import (
    SQLiteTitleProposalRepository,
    TitleProposalPersistenceError,
)
from mechanical_librarian.application.title_proposals import SCENARIOS, scenario_named
from mechanical_librarian.application.ports import (
    FailureKind,
    Page,
    PortFailure,
    PortResult,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.bootstrap.composition import _run_synthetic_title_proposal_report
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)
from mechanical_librarian.domain.title_proposals import (
    TitleProposalResult,
    proposal_set_from_manifest,
)


EXPECTED = {
    "agree-empty": "needs_review",
    "agree-different": "needs_review",
    "current-equivalent": "no_change",
    "current-missing": "current_missing",
    "current-unavailable": "current_unavailable",
    "local-provider-conflict": "local_provider_conflict",
    "f06-missing": "f06_missing",
    "f06-ambiguous": "f06_ambiguous",
    "f06-unusable": "f06_unusable",
    "provider-disabled": "provider_disabled",
    "provider-failed": "provider_failed",
    "provider-empty": "provider_empty",
    "provider-no-match": "provider_no_match",
    "stale-input": "stale_input",
    "supersede-before": "needs_review",
    "supersede-after": "needs_review",
}


def _insert_set(connection: sqlite3.Connection, proposal_set) -> None:
    connection.execute(
        "INSERT INTO synthetic_title_proposal_sets("
        "set_id, identity_digest, lineage, lineage_revision, previous_set_id, mode, canonical_set"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            proposal_set.set_id,
            proposal_set.identity_digest,
            proposal_set.lineage,
            proposal_set.lineage_revision,
            proposal_set.previous_set_id,
            proposal_set.mode.value,
            proposal_set.to_json(),
        ),
    )


def _proposal_rows(connection: sqlite3.Connection) -> tuple[list[tuple], list[tuple]]:
    return (
        connection.execute(
            "SELECT * FROM synthetic_title_proposal_sets ORDER BY lineage, lineage_revision"
        ).fetchall(),
        connection.execute(
            "SELECT * FROM current_synthetic_title_proposals ORDER BY lineage"
        ).fetchall(),
    )


class SyntheticTitleProposalIntegrationTests(unittest.TestCase):
    def test_complete_closed_scenario_table_uses_real_f06_child(self) -> None:
        self.assertEqual(set(SCENARIOS), set(EXPECTED))
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            for name, expected in EXPECTED.items():
                with self.subTest(name=name):
                    if name == "supersede-after":
                        _run_synthetic_title_proposal_report(database, "supersede-before")
                    report = _run_synthetic_title_proposal_report(database, name)
                    data = report.to_data()
                    self.assertEqual(data["result"], expected)
                    self.assertEqual(data["mode"], "synthetic_preview")
                    self.assertIs(data["production_applicable"], False)
                    self.assertEqual(len(data["proposals"]), 1 if expected == "needs_review" else 0)
                    self.assertEqual(
                        data["manifest"]["f06"]["source_fingerprint"],
                        data["manifest"]["epub_digest"],
                    )

    def test_reopen_replay_is_duplicate_safe_and_reverse_replay_never_rewinds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            first = _run_synthetic_title_proposal_report(database, "agree-empty")
            replay = _run_synthetic_title_proposal_report(database, "agree-empty")
            self.assertEqual(first.proposal_set, replay.proposal_set)
            self.assertTrue(replay.persistence.replayed)
            self.assertTrue(replay.persistence.current)
            before = _run_synthetic_title_proposal_report(database, "supersede-before")
            after = _run_synthetic_title_proposal_report(database, "supersede-after")
            after_replay = _run_synthetic_title_proposal_report(database, "supersede-after")
            reverse = _run_synthetic_title_proposal_report(database, "supersede-before")
            self.assertEqual(after.proposal_set.previous_set_id, before.proposal_set.set_id)
            self.assertEqual(after_replay.proposal_set, after.proposal_set)
            self.assertTrue(after_replay.persistence.replayed)
            self.assertTrue(reverse.persistence.replayed)
            self.assertFalse(reverse.persistence.current)
            connection = sqlite3.connect(database)
            self.addCleanup(connection.close)
            pointer = connection.execute(
                "SELECT set_id FROM current_synthetic_title_proposals WHERE lineage = ?",
                (after.proposal_set.lineage,),
            ).fetchone()
            self.assertEqual(pointer, (after.proposal_set.set_id,))

    def test_current_holds_one_read_snapshot_across_concurrent_supersession(self) -> None:
        class InterleavingCursor:
            def __init__(self, cursor, after_fetch) -> None:
                self._cursor = cursor
                self._after_fetch = after_fetch

            def fetchall(self):
                rows = self._cursor.fetchall()
                self._after_fetch()
                return rows

            def __getattr__(self, name):
                return getattr(self._cursor, name)

        class InterleavingConnection:
            def __init__(self, connection, after_history) -> None:
                self._connection = connection
                self._after_history = after_history
                self._interleaved = False

            @property
            def in_transaction(self):
                return self._connection.in_transaction

            def execute(self, statement, parameters=()):
                cursor = self._connection.execute(statement, parameters)
                if (
                    not self._interleaved
                    and "FROM synthetic_title_proposal_sets WHERE lineage" in statement
                    and "ORDER BY lineage_revision" in statement
                ):
                    self._interleaved = True
                    return InterleavingCursor(cursor, self._after_history)
                return cursor

            def commit(self) -> None:
                self._connection.commit()

            def rollback(self) -> None:
                self._connection.rollback()

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            source_before = _run_synthetic_title_proposal_report(
                source, "supersede-before"
            ).proposal_set
            source_after = _run_synthetic_title_proposal_report(
                source, "supersede-after"
            ).proposal_set
            target = Path(directory) / "target.sqlite"
            target_before = _run_synthetic_title_proposal_report(
                target, "supersede-before"
            ).proposal_set
            self.assertEqual(source_before, target_before)

            reader = connect(target)
            writer = connect(target)
            try:
                committed: list[object] = []

                def commit_supersession() -> None:
                    committed.append(
                        SQLiteTitleProposalRepository(writer).store(source_after)
                    )

                interleaved = InterleavingConnection(reader, commit_supersession)
                observed = SQLiteTitleProposalRepository(interleaved).current(
                    target_before.lineage
                )
                self.assertEqual(observed, target_before)
                self.assertEqual(len(committed), 1)
                self.assertEqual(
                    SQLiteTitleProposalRepository(reader).current(target_before.lineage),
                    source_after,
                )
            finally:
                writer.close()
                reader.close()

    def test_preview_does_not_mutate_inventory_job_audit_or_poll_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            _run_synthetic_title_proposal_report(database, "provider-empty")
            connection = sqlite3.connect(database)
            self.addCleanup(connection.close)
            for table in (
                "audit_events", "workflow_runs", "jobs", "inventory_snapshots",
                "inventory_v2_snapshots", "inventory_scans", "inventory_polls",
            ):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone(), (0,))
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(synthetic_title_proposal_sets)")
            }
            self.assertNotIn("production_applicable", columns)
            self.assertNotIn("epub_bytes", columns)
            self.assertNotIn("path", columns)

    def test_history_pointer_and_readback_failures_roll_back_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite"
            proposal_set = _run_synthetic_title_proposal_report(source, "agree-empty").proposal_set
            for hook_name in ("after_history_insert", "after_pointer_advance", "before_readback"):
                with self.subTest(hook=hook_name):
                    target = Path(directory) / f"{hook_name}.sqlite"
                    connection = connect(target)
                    try:
                        migrate(connection)
                        def fail() -> None:
                            raise RuntimeError("injected")
                        repository = SQLiteTitleProposalRepository(
                            connection, **{hook_name: fail}
                        )
                        with self.assertRaises(RuntimeError):
                            repository.store(proposal_set)
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM synthetic_title_proposal_sets").fetchone(),
                            (0,),
                        )
                        self.assertEqual(
                            connection.execute("SELECT COUNT(*) FROM current_synthetic_title_proposals").fetchone(),
                            (0,),
                        )
                    finally:
                        connection.close()

    def test_tampered_canonical_state_fails_closed_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            report = _run_synthetic_title_proposal_report(database, "agree-empty")
            connection = sqlite3.connect(database, isolation_level=None)
            self.addCleanup(connection.close)
            connection.execute("DROP TRIGGER synthetic_title_proposal_sets_no_update")
            value = json.loads(report.proposal_set.to_json())
            value["production_applicable"] = True
            connection.execute(
                "UPDATE synthetic_title_proposal_sets SET canonical_set = ? WHERE set_id = ?",
                (json.dumps(value, separators=(",", ":"), sort_keys=True), report.proposal_set.set_id),
            )
            with self.assertRaises(TitleProposalPersistenceError):
                SQLiteTitleProposalRepository(connection).current(report.proposal_set.lineage)
            stored = connection.execute(
                "SELECT canonical_set FROM synthetic_title_proposal_sets WHERE set_id = ?",
                (report.proposal_set.set_id,),
            ).fetchone()[0]
            self.assertIn('"production_applicable":true', stored)

    def test_generated_epub_has_no_path_and_closes_on_base_exception(self) -> None:
        stream = None
        with self.assertRaises(KeyboardInterrupt):
            with generated_synthetic_epub(scenario_named("agree-empty")) as generated:
                stream = generated.stream
                self.assertFalse(hasattr(generated, "path"))
                raise KeyboardInterrupt
        self.assertIsNotNone(stream)
        self.assertTrue(stream.closed)

    def test_f06_failure_and_malformed_provider_publish_no_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            f06_database = Path(directory) / "f06.sqlite"
            failed_report = LocalEvidenceReport(
                LocalEvidenceOutcome.CHILD_FAILURE,
                LocalEvidenceCapability.PACKAGE,
                EpubPackageLimits(source_archive_bytes=4 * 1024 * 1024),
            )
            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.run_epub_package_child",
                    return_value=failed_report,
                ),
                self.assertRaises(Exception),
            ):
                _run_synthetic_title_proposal_report(f06_database, "agree-empty")
            self.assertFalse(f06_database.exists())

            provider_database = Path(directory) / "provider.sqlite"
            with (
                patch(
                    "mechanical_librarian.adapters.providers.synthetic_title.SyntheticTitleProvider.lookup",
                    return_value=object(),
                ),
                self.assertRaises(Exception),
            ):
                _run_synthetic_title_proposal_report(provider_database, "agree-empty")
            self.assertFalse(provider_database.exists())

    def test_application_derives_provider_results_only_from_typed_port_outcomes(self) -> None:
        port_results = {
            "provider_disabled": PortResult.failed(
                PortFailure(FailureKind.UNSUPPORTED, RetryAdvice(False), SafeSummary("disabled"))
            ),
            "provider_failed": PortResult.failed(
                PortFailure(FailureKind.UNAVAILABLE, RetryAdvice(True), SafeSummary("unavailable"))
            ),
            "provider_empty": PortResult.success(Page((), None)),
            "provider_no_match": PortResult.failed(
                PortFailure(FailureKind.NOT_FOUND, RetryAdvice(False), SafeSummary("no match"))
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            for expected, port_result in port_results.items():
                with self.subTest(expected=expected):
                    database = Path(directory) / f"{expected}.sqlite"
                    with patch(
                        "mechanical_librarian.adapters.providers.synthetic_title.SyntheticTitleProvider.lookup",
                        return_value=port_result,
                    ):
                        report = _run_synthetic_title_proposal_report(
                            database, "agree-empty"
                        )
                    self.assertEqual(report.proposal_set.result.value, expected)
                    self.assertEqual(
                        report.proposal_set.to_data()["manifest"]["provider"]["outcome"],
                        expected.removeprefix("provider_"),
                    )

    def test_lineage_corruption_fails_closed_without_repair_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # Missing pointer.
            missing_path = Path(directory) / "missing.sqlite"
            missing = _run_synthetic_title_proposal_report(
                missing_path, "provider-empty"
            ).proposal_set
            connection = sqlite3.connect(missing_path, isolation_level=None)
            try:
                connection.execute(
                    "DROP TRIGGER current_synthetic_title_proposals_no_delete"
                )
                connection.execute(
                    "DELETE FROM current_synthetic_title_proposals WHERE lineage = ?",
                    (missing.lineage,),
                )
                self._assert_corrupt_lineage_unchanged(connection, missing)
            finally:
                connection.close()

            # Rewound pointer.
            rewind_path = Path(directory) / "rewound.sqlite"
            before = _run_synthetic_title_proposal_report(
                rewind_path, "supersede-before"
            ).proposal_set
            after = _run_synthetic_title_proposal_report(
                rewind_path, "supersede-after"
            ).proposal_set
            connection = sqlite3.connect(rewind_path, isolation_level=None)
            try:
                connection.execute(
                    "UPDATE current_synthetic_title_proposals SET set_id = ? WHERE lineage = ?",
                    (before.set_id, after.lineage),
                )
                self._assert_corrupt_lineage_unchanged(connection, after)
            finally:
                connection.close()

            # Gapped revision with otherwise canonical history.
            gap_path = Path(directory) / "gap.sqlite"
            first = _run_synthetic_title_proposal_report(
                gap_path, "provider-empty"
            ).proposal_set
            gap_manifest = copy.deepcopy(first.to_data()["manifest"])
            gap_manifest["lineage_revision"] = 3
            gap_manifest["previous_set_id"] = first.set_id
            gap_manifest["snapshot"]["id"] = "synthetic-snapshot-3"
            gap = proposal_set_from_manifest(
                manifest=gap_manifest,
                result=TitleProposalResult.PROVIDER_EMPTY,
                previous_set_id=first.set_id,
            )
            connection = sqlite3.connect(gap_path, isolation_level=None)
            try:
                _insert_set(connection, gap)
                connection.execute(
                    "UPDATE current_synthetic_title_proposals SET set_id = ? WHERE lineage = ?",
                    (gap.set_id, gap.lineage),
                )
                self._assert_corrupt_lineage_unchanged(connection, gap)
            finally:
                connection.close()

            # Cross-lineage predecessor with valid foreign-key target.
            cross_path = Path(directory) / "cross.sqlite"
            own = _run_synthetic_title_proposal_report(
                cross_path, "provider-empty"
            ).proposal_set
            other = _run_synthetic_title_proposal_report(
                cross_path, "provider-disabled"
            ).proposal_set
            cross_manifest = copy.deepcopy(own.to_data()["manifest"])
            cross_manifest["lineage_revision"] = 2
            cross_manifest["previous_set_id"] = other.set_id
            cross_manifest["snapshot"]["id"] = "synthetic-snapshot-2"
            cross = proposal_set_from_manifest(
                manifest=cross_manifest,
                result=TitleProposalResult.PROVIDER_EMPTY,
                previous_set_id=other.set_id,
            )
            connection = sqlite3.connect(cross_path, isolation_level=None)
            try:
                _insert_set(connection, cross)
                connection.execute(
                    "UPDATE current_synthetic_title_proposals SET set_id = ? WHERE lineage = ?",
                    (cross.set_id, cross.lineage),
                )
                self._assert_corrupt_lineage_unchanged(connection, cross)
            finally:
                connection.close()

    def _assert_corrupt_lineage_unchanged(
        self, connection: sqlite3.Connection, candidate
    ) -> None:
        state = _proposal_rows(connection)
        repository = SQLiteTitleProposalRepository(connection)
        with self.assertRaises(TitleProposalPersistenceError):
            repository.current(candidate.lineage)
        with self.assertRaises(TitleProposalPersistenceError):
            repository.store(candidate)
        self.assertEqual(_proposal_rows(connection), state)


if __name__ == "__main__":
    unittest.main()
