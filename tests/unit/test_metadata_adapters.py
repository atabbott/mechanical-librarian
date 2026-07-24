from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import mechanical_librarian.adapters.persistence.metadata_reports as metadata_persistence

from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader, _raw_cover_authority
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.hardcover_metadata import HARDCOVER_URL, HardcoverMetadataProvider
import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.application.metadata_report import MetadataReportError, TargetCover
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact, CoverSource, CoverSourceKind, CoverSourceState, cover_source_id,
)


def _report(title: str = "Current title") -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", title),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


def _create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        metadata_persistence._REPORTS_SQL.replace(
            "= 'metadata-report-v5'", "= 'metadata-report-v1'"
        )
    )
    connection.execute(metadata_persistence._CURRENT_SQL)
    connection.execute(metadata_persistence._NO_UPDATE_SQL)
    connection.execute(metadata_persistence._NO_DELETE_SQL)


def _leave_hot_journal(path: Path) -> Path:
    child = (
        "import os,sqlite3,sys\n"
        "connection=sqlite3.connect(sys.argv[1])\n"
        "connection.execute('PRAGMA journal_mode=DELETE')\n"
        "connection.execute('PRAGMA synchronous=FULL')\n"
        "connection.execute('BEGIN IMMEDIATE')\n"
        "connection.execute('PRAGMA user_version=1')\n"
        "os._exit(0)\n"
    )
    subprocess.run(
        [sys.executable, "-c", child, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    journal = Path(f"{path}-journal")
    if not journal.is_file() or journal.stat().st_size <= 512:
        raise AssertionError("synthetic hot journal was not created")
    return journal


class MetadataPersistenceTests(unittest.TestCase):
    def test_legacy_and_intermediate_schemas_are_rejected_without_mutation(self) -> None:
        for kind, prepare in (
            (
                "legacy v1",
                _create_legacy_schema,
            ),
            (
                "F09i document bound",
                lambda connection: (
                    connection.execute(
                        metadata_persistence._REPORTS_SQL.replace(
                            "BETWEEN 2 AND 2097152", "BETWEEN 2 AND 262144"
                        )
                    ),
                    connection.execute(metadata_persistence._CURRENT_SQL),
                    connection.execute(metadata_persistence._NO_UPDATE_SQL),
                    connection.execute(metadata_persistence._NO_DELETE_SQL),
                ),
            ),
            (
                "F09j one mebibyte bound",
                lambda connection: (
                    connection.execute(
                        metadata_persistence._REPORTS_SQL.replace(
                            "BETWEEN 2 AND 2097152", "BETWEEN 2 AND 1048576"
                        )
                    ),
                    connection.execute(metadata_persistence._CURRENT_SQL),
                    connection.execute(metadata_persistence._NO_UPDATE_SQL),
                    connection.execute(metadata_persistence._NO_DELETE_SQL),
                ),
            ),
            (
                "intermediate extra column",
                lambda connection: (
                    connection.execute(metadata_persistence._REPORTS_SQL),
                    connection.execute("ALTER TABLE metadata_reports ADD COLUMN intermediate TEXT"),
                ),
            ),
        ):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "reports.sqlite"
                connection = sqlite3.connect(path)
                prepare(connection)
                connection.commit()
                connection.close()
                before = path.read_bytes()
                with self.assertRaises(MetadataReportError):
                    open_repository(path)
                self.assertEqual(path.read_bytes(), before)

    def test_clean_database_has_exact_current_tables_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            first = _report()
            self.assertEqual(repository.publish(first, lambda: True), first.to_json())
            self.assertEqual(repository.publish(first, lambda: True), first.to_json())
            second = _report("Changed title")
            repository.publish(second, lambda: True, first.report_id)
            repository.close()

            connection = sqlite3.connect(path)
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            rows = connection.execute("SELECT report_id FROM metadata_reports ORDER BY report_id").fetchall()
            current = connection.execute("SELECT report_id FROM current_metadata_reports").fetchone()
            connection.close()
            self.assertEqual(
                tables,
                [
                    ("current_metadata_reports",),
                    ("metadata_report_cover_references",),
                    ("metadata_reports",),
                ],
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(current, (second.report_id,))

            reopened = open_repository(path)
            self.assertEqual(reopened.get(first.report_id), first.to_json())
            self.assertEqual(reopened.current("primary", "ebooks", "item-1", "a" * 64), second.to_json())
            reopened.close()

    def test_same_candidate_replay_converges_after_a_stale_null_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True)
            self.assertEqual(
                repository.publish(report, lambda: True, expected_current=None),
                report.to_json(),
            )
            changed = _report("Changed title")
            with self.assertRaisesRegex(MetadataReportError, "current report changed"):
                repository.publish(changed, lambda: True, expected_current=None)
            repository.close()

    def test_preexisting_unknown_database_is_rejected_without_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE legacy(value TEXT)")
            connection.commit()
            connection.close()
            before = path.read_bytes()
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            self.assertEqual(path.read_bytes(), before)

    def test_preview_digest_requires_exact_report_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
            source = CoverSource(
                cover_source_id(CoverSourceKind.EPUB, "cover"),
                CoverSourceKind.EPUB,
                CoverSourceState.RETRIEVED,
                artifact,
            )
            report = _report()
            report = MetadataReport.create(
                target=report.target, file=report.file, local_claims=report.local_claims,
                local_reasons=report.local_reasons, providers=report.providers,
                cover_sources=(source,),
            )
            repository = open_repository(path)
            repository.publish(report, lambda: True)
            self.assertEqual(artifact.preview_digest, repository.preview_digest(report.report_id, artifact.artifact_id, artifact.preview_variant))
            self.assertTrue(repository.artifact_is_referenced(artifact.original_digest))
            repository._connection.execute(
                "DROP TRIGGER metadata_report_cover_references_no_delete"
            )
            repository._connection.execute(
                "DELETE FROM metadata_report_cover_references WHERE report_id=?",
                (report.report_id,),
            )
            with self.assertRaisesRegex(MetadataReportError, "cover references"):
                repository.artifact_is_referenced(artifact.original_digest)
            with self.assertRaises(MetadataReportError):
                repository.preview_digest(report.report_id, "cover_" + "0" * 64, artifact.preview_variant)
            repository.close()

    def test_corrupt_document_and_envelope_disagreement_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            report = _report()
            repository.publish(report, lambda: True)
            repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
            repository._connection.execute("UPDATE metadata_reports SET document='{}'")
            with self.assertRaises(MetadataReportError):
                repository.get(report.report_id)
            repository._connection.execute(
                "UPDATE metadata_reports SET document=?,document_digest=?",
                (report.to_json(), hashlib.sha256(report.to_json().encode()).hexdigest()),
            )
            repository._connection.execute("UPDATE metadata_reports SET connection_name='other'")
            with self.assertRaises(MetadataReportError):
                repository.get(report.report_id)
            unsafe = json.loads(report.to_json())
            unsafe["local"]["claims"][0]["canonical"] = "/Users/private/title"
            document = json.dumps(unsafe, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            repository._connection.execute(
                "UPDATE metadata_reports SET connection_name=?,document=?,document_digest=?",
                ("primary", document, hashlib.sha256(document.encode()).hexdigest()),
            )
            with self.assertRaises(MetadataReportError):
                repository.get(report.report_id)
            repository.close()

    def test_existing_schema_rejects_unexpected_objects_without_mutation(self) -> None:
        for statement in (
            "CREATE VIEW unexpected_view AS SELECT report_id FROM metadata_reports",
            "CREATE INDEX unexpected_index ON metadata_reports(connection_name)",
        ):
            with self.subTest(statement=statement), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "reports.sqlite"
                repository = open_repository(path)
                repository.publish(_report(), lambda: True)
                repository.close()
                connection = sqlite3.connect(path)
                connection.execute(statement)
                connection.commit()
                expected_objects = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
                expected_counts = connection.execute("SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)").fetchone()
                connection.close()
                before = path.read_bytes()
                with self.assertRaises(MetadataReportError):
                    open_repository(path)
                self.assertEqual(path.read_bytes(), before)
                connection = sqlite3.connect(path)
                self.assertEqual(connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall(), expected_objects)
                self.assertEqual(connection.execute("SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)").fetchone(), expected_counts)
                connection.close()

    def test_candidate_cleanup_ignores_unrelated_malformed_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True)
            repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
            repository._connection.execute(
                "UPDATE metadata_reports SET document='{}',document_digest=? WHERE report_id=?",
                (hashlib.sha256(b"{}").hexdigest(), report.report_id),
            )
            self.assertFalse(repository.artifact_is_referenced("0" * 64))
            repository.close()

    def test_existing_schema_rejects_persistent_sqlite_master_system_objects_without_mutation(self) -> None:
        for kind in ("analyze", "sequence", "arbitrary"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "reports.sqlite"
                repository = open_repository(path)
                repository.publish(_report(), lambda: True)
                repository.close()
                connection = sqlite3.connect(path)
                if kind == "analyze":
                    connection.execute("ANALYZE")
                elif kind == "sequence":
                    connection.execute("CREATE TABLE fixture_sequence(id INTEGER PRIMARY KEY AUTOINCREMENT)")
                    connection.execute("DROP TABLE fixture_sequence")
                else:
                    connection.execute("CREATE TABLE fixture_extra(value TEXT)")
                    connection.execute("PRAGMA writable_schema=ON")
                    connection.execute("UPDATE sqlite_master SET name='sqlite_fixture_extra',tbl_name='sqlite_fixture_extra',sql='CREATE TABLE sqlite_fixture_extra(value TEXT)' WHERE name='fixture_extra'")
                    connection.execute("PRAGMA writable_schema=OFF")
                connection.commit()
                expected_objects = connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
                expected_counts = connection.execute("SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)").fetchone()
                connection.close()
                before = path.read_bytes()
                with self.assertRaises(MetadataReportError):
                    open_repository(path)
                self.assertEqual(path.read_bytes(), before)
                connection = sqlite3.connect(path)
                self.assertEqual(connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall(), expected_objects)
                self.assertEqual(connection.execute("SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)").fetchone(), expected_counts)
                connection.close()

    def test_exact_schema_isolates_unselected_history_and_validates_selected_rows_and_pointers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            primary = _report()
            other = MetadataReport.create(target=TargetFacts("secondary", "ebooks", "item-2", "Other title"), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(primary, lambda: True)
            repository.publish(other, lambda: True)
            repository.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER metadata_reports_no_update")
            document = "{}"
            connection.execute("UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?", (document, hashlib.sha256(document.encode()).hexdigest(), other.report_id))
            connection.execute(metadata_persistence._NO_UPDATE_SQL)
            connection.commit(); connection.close()
            repository = open_repository(path)
            self.assertEqual(repository.get(primary.report_id), primary.to_json())
            with self.assertRaises(MetadataReportError):
                repository.get(other.report_id)
            repository.close()

    def test_selected_corrupt_pointer_blocks_only_its_publication_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            primary = _report()
            other = MetadataReport.create(target=TargetFacts("secondary", "ebooks", "item-2", "Other title"), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(primary, lambda: True); repository.publish(other, lambda: True); repository.close()
            connection = sqlite3.connect(path)
            connection.execute("UPDATE current_metadata_reports SET report_id=? WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (other.report_id, primary.target.connection, primary.target.library, primary.target.item, primary.file.identity))
            connection.commit(); connection.close()
            before = path.read_bytes()
            repository = open_repository(path)
            self.assertEqual(repository.current(other.target.connection, other.target.library, other.target.item, other.file.identity), other.to_json())
            successor = _report("Successor")
            with self.assertRaises(MetadataReportError):
                repository.publish(successor, lambda: True)
            repository.close()
            self.assertEqual(path.read_bytes(), before)

    def test_pointer_failure_rolls_back_new_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            repository._connection.execute(
                "CREATE TRIGGER fail_pointer BEFORE INSERT ON current_metadata_reports BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
            )
            with self.assertRaises(MetadataReportError):
                repository.publish(_report(), lambda: True)
            self.assertEqual(repository._connection.execute("SELECT COUNT(*) FROM metadata_reports").fetchone(), (0,))
            repository.close()

    def test_publication_precondition_failure_rolls_back_report_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            calls: list[bool] = []
            with self.assertRaises(MetadataReportError):
                repository.publish(
                    _report(),
                    lambda: calls.append(repository._connection.in_transaction) or False,
                )
            self.assertEqual(calls, [True])
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM metadata_reports"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM current_metadata_reports"
                ).fetchone(),
                (0,),
            )
            repository.close()

    def test_wal_incompatible_database_is_rejected_byte_for_byte_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "other.sqlite"
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            connection.execute("CREATE TABLE legacy(value TEXT)")
            connection.commit()
            connection.close()
            before = {value.name: value.read_bytes() for value in root.iterdir()}
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            after = {value.name: value.read_bytes() for value in root.iterdir()}
            self.assertEqual(after, before)

    def test_incompatible_hot_journal_is_rejected_and_preserved_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "other.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("CREATE TABLE legacy(value TEXT)")
            connection.execute("INSERT INTO legacy VALUES('original')")
            connection.commit()
            connection.close()
            journal = _leave_hot_journal(path)
            before = {
                path.name: path.read_bytes(),
                journal.name: journal.read_bytes(),
            }
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            after = {
                path.name: path.read_bytes(),
                journal.name: journal.read_bytes(),
            }
            self.assertEqual(after, before)

    def test_path_swap_to_symlink_cannot_bind_or_write_alternate_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports.sqlite"
            repository = open_repository(path)
            repository.close()
            original = root / "original.sqlite"
            alternate = root / "alternate.sqlite"
            connection = sqlite3.connect(alternate)
            connection.execute("CREATE TABLE alternate(value TEXT)")
            connection.commit()
            connection.close()
            alternate_before = alternate.read_bytes()
            real_connect = sqlite3.connect
            calls = 0

            def swapping_connect(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    path.rename(original)
                    path.symlink_to(alternate)
                return real_connect(*args, **kwargs)

            try:
                with patch.object(
                    metadata_persistence.sqlite3,
                    "connect",
                    side_effect=swapping_connect,
                ), self.assertRaises(MetadataReportError):
                    open_repository(path)
            finally:
                if path.is_symlink():
                    path.unlink()
                if original.exists():
                    original.rename(path)
            self.assertEqual(alternate.read_bytes(), alternate_before)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_double_path_swap_cannot_hide_the_alternate_sqlite_connection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports.sqlite"
            repository = open_repository(path)
            repository.close()
            alternate = root / "alternate.sqlite"
            alternate_repository = open_repository(alternate)
            alternate_repository.close()
            original = root / "original.sqlite"
            alternate_before = alternate.read_bytes()
            real_connect = sqlite3.connect
            calls = 0

            def double_swapping_connect(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls != 2:
                    return real_connect(*args, **kwargs)
                path.rename(original)
                path.symlink_to(alternate)
                connection = real_connect(*args, **kwargs)
                path.unlink()
                original.rename(path)
                return connection

            try:
                with patch.object(
                    metadata_persistence.sqlite3,
                    "connect",
                    side_effect=double_swapping_connect,
                ):
                    safely_bound = open_repository(path)
                    safely_bound.close()
            finally:
                if path.is_symlink():
                    path.unlink()
                if original.exists():
                    if path.exists():
                        path.unlink()
                    original.rename(path)
            self.assertEqual(alternate.read_bytes(), alternate_before)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_descriptor_binding_allows_ordinary_open_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports.sqlite"
            repository = open_repository(path)
            aliases = tuple(
                value.name
                for value in root.iterdir()
                if value.name.startswith(".metadata-report-binding-")
            )
            self.assertEqual(len(aliases), 1)
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            report = _report()
            repository.publish(report, lambda: True)
            repository.close()
            self.assertEqual(
                tuple(
                    value.name
                    for value in root.iterdir()
                    if value.name.startswith(".metadata-report-binding-")
                ),
                (),
            )
            reopened = open_repository(path)
            self.assertEqual(reopened.get(report.report_id), report.to_json())
            reopened.close()
            self.assertEqual(tuple(value.name for value in root.iterdir()), ("reports.sqlite",))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_raced_configured_hot_journal_cannot_control_alias_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports.sqlite"
            repository = open_repository(path)
            repository.close()
            real_connect = sqlite3.connect
            raced: dict[str, bytes] = {}
            observed_uris: list[str] = []

            def racing_connect(database, *args, **kwargs):
                observed_uris.append(str(database))
                if len(observed_uris) == 2:
                    journal = _leave_hot_journal(path)
                    raced["main"] = path.read_bytes()
                    raced["journal"] = journal.read_bytes()
                return real_connect(database, *args, **kwargs)

            with patch.object(
                metadata_persistence.sqlite3,
                "connect",
                side_effect=racing_connect,
            ), self.assertRaises(MetadataReportError):
                open_repository(path)
            journal = Path(f"{path}-journal")
            self.assertEqual(len(observed_uris), 2)
            self.assertIn("/proc/self/fd/", observed_uris[1])
            self.assertIn(".metadata-report-binding-", observed_uris[1])
            self.assertNotIn(str(path.absolute()), observed_uris[1])
            self.assertEqual(path.read_bytes(), raced["main"])
            self.assertEqual(journal.read_bytes(), raced["journal"])
            self.assertEqual(
                set(value.name for value in root.iterdir()),
                {"reports.sqlite", "reports.sqlite-journal"},
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_publication_requires_configured_sidecars_to_remain_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "reports.sqlite"
            repository = open_repository(path)
            sidecar = Path(f"{path}-journal")
            sidecar.write_bytes(b"configured-path-race")
            with self.assertRaises(MetadataReportError):
                repository.publish(_report(), lambda: True)
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM metadata_reports"
                ).fetchone(),
                (0,),
            )
            repository.close()
            self.assertEqual(sidecar.read_bytes(), b"configured-path-race")
            self.assertEqual(
                set(value.name for value in root.iterdir()),
                {"reports.sqlite", "reports.sqlite-journal"},
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_parent_replacement_at_final_precommit_check_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            outer = Path(directory) / "outer"
            outer.mkdir(mode=0o700)
            live = outer / "live"
            live.mkdir(mode=0o700)
            path = live / "reports.sqlite"
            repository = open_repository(path)
            original_bytes = path.read_bytes()
            displaced = outer / "displaced"
            replacement_bytes = b"replacement-database"
            binding = repository._binding
            self.assertIsNotNone(binding)
            require_current = binding.require_current
            calls = 0

            def replace_parent_at_final_check() -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    live.rename(displaced)
                    live.mkdir(mode=0o700)
                    path.write_bytes(replacement_bytes)
                require_current()

            with patch.object(
                binding,
                "require_current",
                side_effect=replace_parent_at_final_check,
            ), self.assertRaises(MetadataReportError):
                repository.publish(_report(), lambda: True)
            self.assertEqual(calls, 2)
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM metadata_reports"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                repository._connection.execute(
                    "SELECT COUNT(*) FROM current_metadata_reports"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                (displaced / "reports.sqlite").read_bytes(),
                original_bytes,
            )
            self.assertEqual(path.read_bytes(), replacement_bytes)
            repository.close()
            self.assertEqual(
                tuple(value.name for value in displaced.iterdir()),
                ("reports.sqlite",),
            )
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            self.assertEqual(path.read_bytes(), replacement_bytes)

            path.unlink()
            live.rmdir()
            displaced.rename(live)
            reopened = open_repository(path)
            with self.assertRaises(MetadataReportError):
                reopened.get(_report().report_id)
            reopened.close()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_database_parent_must_be_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "shared"
            parent.mkdir()
            parent.chmod(0o755)
            path = parent / "reports.sqlite"
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            self.assertFalse(path.exists())
            parent.chmod(0o700)
            repository = open_repository(path)
            repository.close()
            self.assertEqual(tuple(value.name for value in parent.iterdir()), ("reports.sqlite",))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor proof")
    def test_linux_database_ancestor_must_exclude_other_users(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory) / "shared"
            shared.mkdir(mode=0o700)
            parent = shared / "private"
            parent.mkdir(mode=0o700)
            shared.chmod(0o777)
            path = parent / "reports.sqlite"
            with self.assertRaises(MetadataReportError):
                open_repository(path)
            self.assertFalse(path.exists())
            shared.chmod(0o700)
            repository = open_repository(path)
            repository.close()
            self.assertEqual(
                tuple(value.name for value in parent.iterdir()),
                ("reports.sqlite",),
            )

    def test_current_pointer_scope_must_agree_with_decoded_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            primary = _report("Primary title")
            other = MetadataReport.create(
                target=TargetFacts("secondary", "other", "item-2", "Other title"),
                file=FileFacts(0, "c" * 64, "d" * 64),
                local_claims=(),
                local_reasons=("no_opening_claims",),
                providers=(),
            )
            repository.publish(primary, lambda: True)
            repository.publish(other, lambda: True)
            repository._connection.execute(
                "UPDATE current_metadata_reports SET report_id=? "
                "WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (
                    other.report_id,
                    "primary",
                    "ebooks",
                    "item-1",
                    "a" * 64,
                ),
            )
            with self.assertRaises(MetadataReportError):
                repository.current("primary", "ebooks", "item-1", "a" * 64)
            repository.close()


class ProviderAdapterTests(unittest.TestCase):
    @staticmethod
    def _envelope(rows: list[dict[str, object]], books: list[dict[str, object]]) -> dict[str, object]:
        editions = [
            {
                "id": row["id"], "book_id": row["book_id"],
                "isbn_10": row.get("isbn_10"), "isbn_13": row.get("isbn_13"),
                "asin": row.get("asin"), "title": row.get("title"),
                "subtitle": row.get("subtitle"), "release_date": row.get("release_date"),
                "publisher": row.get("publisher"), "language": row.get("language"),
                "contributions": row.get("contributions", []),
            }
            for row in rows
        ]
        works = [
            {"id": book["id"], "title": book.get("title"), "description": book.get("description"),
             "contributions": book.get("contributions", []), "book_series": book.get("book_series", []),
             "taggings": book.get("taggings", [])}
            for book in books
        ]
        return {"data": {"editions": editions, "books": works}}
    def _claim(self) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="eBook ISBN: 9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )

    def _title_claim(self) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Current title",
            canonical="Current title",
            excerpt="Current title",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )

    def test_one_request_retains_all_candidates_in_identity_order(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope(
                    [{"id": 2, "book_id": 20, "isbn_10": None, "isbn_13": "9780306406157"}, {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": "9780306406157"}],
                    [{"id": 20, "title": "Other title"}, {"id": 10, "title": None}],
                ),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = HardcoverMetadataProvider("catalogue", client, 65_536)
        observation = provider.observe((self._claim(), self._title_claim()))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertEqual([item.identity for item in observation.candidates], ["hardcover-edition-0000000001", "hardcover-edition-0000000002"])
        claims = {claim.kind: claim for claim in observation.candidates[1].claims}
        self.assertEqual(claims[ClaimKind.ISBN].links[0].outcome, Comparison.AGREEMENT)
        self.assertEqual(claims[ClaimKind.TITLE].links[0].outcome, Comparison.DISAGREEMENT)
        self.assertEqual(observation.input_references, (self._claim().reference,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(2, len(provider.cover_descriptors))
        self.assertTrue(all(url is None for _, url in provider.cover_descriptors))
        self.assertEqual(str(requests[0].url), HARDCOVER_URL)
        self.assertIn("limit:6", json.loads(requests[0].content)["query"])
        self.assertEqual(provider.observe((self._claim(), self._title_claim())).status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(len(requests), 1)

    def test_malformed_optional_cover_descriptor_does_not_discard_metadata(self) -> None:
        payload = self._envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
            [{"id": 1, "title": "Current title"}],
        )
        payload["data"]["editions"][0]["image"] = {"url": "x" * 2049}  # type: ignore[index]
        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=payload))),
            65_536,
        )
        observation = provider.observe((self._claim(), self._title_claim()))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual((), provider.cover_descriptors)

    def test_agreement_references_only_matching_title_and_isbn_claims(self) -> None:
        other_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Other title",
            canonical="Other title",
            excerpt="Other title",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        other_isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="ISBN: 0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
        )
        payload = self._envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
            [{"id": 1, "title": "Current title"}],
        )
        observation = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            ),
            65_536,
        ).observe((self._claim(), other_isbn, self._title_claim(), other_title))
        claims = {
            claim.kind: claim for claim in observation.candidates[0].claims
        }
        self.assertEqual(
            {link.local_reference: link.outcome for link in claims[ClaimKind.TITLE].links},
            {
                self._title_claim().reference: Comparison.AGREEMENT,
                other_title.reference: Comparison.DISAGREEMENT,
            },
        )
        self.assertEqual(
            {link.local_reference: link.outcome for link in claims[ClaimKind.ISBN].links},
            {
                self._claim().reference: Comparison.AGREEMENT,
                other_isbn.reference: Comparison.NOT_COMPARABLE,
            },
        )

    def test_rich_schema_query_and_field_order_are_closed(self) -> None:
        captured: list[dict[str, object]] = []
        payload = self._envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", "asin": "B012345678", "title": "Edition title", "subtitle": "Edition subtitle", "release_date": "2026-01-01", "publisher": {"id": 2, "name": "Example Press", "parent_id": 9}, "language": {"id": 3, "code2": "en", "code3": "eng", "language": "English"}, "contributions": [{"id": 7, "contribution": "Author", "author": {"id": 4, "name": "Ada Example"}}]}],
            [{"id": 1, "title": "Work title", "description": "Description", "contributions": [{"id": 8, "contribution": "Translator", "author": {"id": 4, "name": "Ada Example"}}], "book_series": [{"id": 9, "position": "1", "series": {"id": 5, "name": "Series"}}], "taggings": [{"id": 10, "tag": {"id": 6, "tag": "Speculative"}}]}],
        )
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)
        observation = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(handler)), 65_536).observe((self._claim(), self._title_claim()))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        claims = observation.candidates[0].claims
        self.assertEqual([claim.raw_order for claim in claims], list(range(len(claims))))
        self.assertEqual({claim.kind for claim in claims}, {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLICATION_DATE, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE, ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR, ClaimKind.DESCRIPTION, ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE, ClaimKind.TAG, ClaimKind.GENRE})
        self.assertEqual(next(claim.scope for claim in claims if claim.kind is ClaimKind.PUBLISHER), ClaimScope.EDITION)
        self.assertNotIn("parent_id", {claim.value for claim in claims if claim.value is not None})
        self.assertIn("contributions(order_by:{id:asc},limit:17)", captured[0]["query"])
        self.assertIn("taggings(order_by:{id:asc},limit:33)", captured[0]["query"])

    def test_rich_field_boundaries_are_local_and_public_safe(self) -> None:
        row = {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", "asin": "B012345678", "title": "T" * 513, "subtitle": None, "release_date": "not-a-date", "publisher": None, "language": None, "contributions": [{"id": 1, "contribution": "Author", "author": {"id": 1, "name": "Ada"}}, {"id": 2, "contribution": "/Users/private", "author": {"id": 2, "name": "Bea"}}]}
        book = {"id": 1, "title": "/Users/private/title", "description": "D" * 16385, "contributions": [{"id": 3, "contribution": "Translator", "author": {"id": 3, "name": "Cy"}}], "book_series": [{"id": 4, "position": 1.5, "series": {"id": 4, "name": "Series"}}], "taggings": []}
        payload = json.dumps(self._envelope([row], [book])).encode()
        claims = hardcover_metadata._parse_candidates(payload, "catalogue", (self._claim(),)).__getitem__(0).claims
        by_kind = {claim.kind: claim for claim in claims}
        self.assertEqual(by_kind[ClaimKind.ASIN].state.value, "valid")
        self.assertEqual(by_kind[ClaimKind.PUBLICATION_DATE].state.value, "malformed")
        self.assertEqual(by_kind[ClaimKind.DESCRIPTION].state.value, "oversized")
        self.assertEqual(by_kind[ClaimKind.TITLE].state.value, "malformed")
        self.assertEqual(by_kind[ClaimKind.SERIES_SEQUENCE].value, "1.5")
        self.assertEqual(by_kind[ClaimKind.IMPRINT].state.value, "unsupported")
        self.assertEqual(by_kind[ClaimKind.GENRE].state.value, "unsupported")
        contributors = [claim for claim in claims if claim.kind is ClaimKind.CONTRIBUTOR]
        self.assertIn("translator", {claim.role for claim in contributors})
        self.assertTrue(any(claim.state.value == "malformed" for claim in contributors))

    def test_response_cap_and_candidate_probe_are_terminal(self) -> None:
        payload = self._envelope([], [])
        capped = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}" * 20))), 1)
        self.assertEqual(capped.observe((self._claim(),)).status, ProviderStatus.OVERSIZED)
        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope(rows, [{"id": index, "title": None} for index in range(1, 7)])))), 65_536)
        self.assertEqual(overflow.observe((self._claim(),)).status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        for size, expected in ((262_144, ProviderStatus.MALFORMED), (262_145, ProviderStatus.OVERSIZED)):
            provider = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request, length=size: httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * length))), 262_144)
            self.assertEqual(provider.observe((self._claim(),)).status, expected)

    def test_exact_rich_caps_and_pinned_schema_are_admitted(self) -> None:
        self.assertEqual(hardcover_metadata.HARDCOVER_SCHEMA_COMMIT, "2c03110845765c32af945ed550a942330c394a3e")
        self.assertIn("contributions(order_by:{id:asc},limit:17){id contribution author{id name}}", hardcover_metadata._QUERY)
        self.assertIn("books:books(", hardcover_metadata._QUERY)
        self.assertNotIn("books_through_editions", hardcover_metadata._QUERY)
        self.assertIn("book_series(order_by:{id:asc},limit:17){id position series{id name}}", hardcover_metadata._QUERY)
        self.assertIn("taggings(order_by:{id:asc},limit:33){id tag{id tag}}", hardcover_metadata._QUERY)
        for scalar, description, expected in (("x" * 512, "d" * 16384, "valid"), ("x" * 513, "d" * 16385, "oversized")):
            with self.subTest(expected=expected):
                payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "title": scalar}], [{"id": 1, "title": None, "description": description}])
                claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", () )[0].claims
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.TITLE), expected)
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.DESCRIPTION), expected)
        for count, kind, scope, key in ((16, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (17, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (16, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (17, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (16, ClaimKind.SERIES, ClaimScope.WORK, "series"), (17, ClaimKind.SERIES, ClaimScope.WORK, "series"), (32, ClaimKind.TAG, ClaimScope.WORK, "tag"), (33, ClaimKind.TAG, ClaimScope.WORK, "tag")):
            contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": f"A{index}"}} for index in range(count)]
            series = [{"id": index, "position": str(index), "series": {"id": index, "name": f"S{index}"}} for index in range(count)]
            tags = [{"id": index, "tag": {"id": index, "tag": f"T{index}"}} for index in range(count)]
            row = {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions if key == "edition" else []}
            book = {"id": 1, "title": None, "contributions": contributions if key == "work" else [], "book_series": series if key == "series" else [], "taggings": tags if key == "tag" else []}
            claims = hardcover_metadata._parse_candidates(json.dumps(self._envelope([row], [book])).encode(), "catalogue", ())[0].claims
            relevant = [claim for claim in claims if claim.kind is kind and claim.scope is scope]
            if count in {17, 33}:
                self.assertEqual(len(relevant), 1)
                self.assertEqual(relevant[0].state, FieldState.OVERSIZED)
            else:
                self.assertEqual(len(relevant), count)
                self.assertTrue(all(claim.state is FieldState.VALID for claim in relevant))

    def test_contribution_probe_preserves_roles_and_overflow_validates_rows(self) -> None:
        for roles, expected in (((["Author"] * 16) + ["Translator"], {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}), ((["Translator"] * 16) + ["Author"], {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR})):
            contributions = [{"id": index, "contribution": role, "author": {"id": index, "name": f"A{index}"}} for index, role in enumerate(roles, 1)]
            payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions}], [{"id": 1, "title": None}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            overflow = {claim.kind for claim in claims if claim.state is FieldState.OVERSIZED and claim.scope is ClaimScope.EDITION}
            self.assertEqual(overflow, expected)
        for bad in (0, 16):
            contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": "A"}} for index in range(17)]
            contributions[bad] = {"id": bad, "contribution": "Author"}
            payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions}], [{"id": 1, "title": None}])
            with self.assertRaises(ValueError):
                hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())

    def test_nested_overflow_probe_objects_are_structurally_validated(self) -> None:
        cases = []
        contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": "A"}} for index in range(17)]
        contributions[16]["author"] = {"id": 17, "unexpected": "A"}
        cases.append(({"contributions": contributions}, {}))
        series = [{"id": index, "position": str(index), "series": {"id": index, "name": "S"}} for index in range(17)]
        series[16]["series"] = {"id": 17, "unexpected": "S"}
        cases.append(({}, {"book_series": series}))
        tags = [{"id": index, "tag": {"id": index, "tag": "T"}} for index in range(33)]
        tags[32]["tag"] = {"id": 33, "unexpected": "T"}
        cases.append(({}, {"taggings": tags}))
        for edition, work in cases:
            with self.subTest(edition=bool(edition)):
                payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", **edition}], [{"id": 1, "title": None, **work}])
                provider = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request, value=payload: httpx.Response(200, headers={"content-type": "application/json"}, json=value))), 65_536)
                self.assertEqual(provider.observe((self._claim(),)).status, ProviderStatus.MALFORMED)

    def test_series_positions_and_code3_are_retained_at_projection_bounds(self) -> None:
        for position, expected in (("10", "10"), ("20.0", "20"), ("100", "100"), (10, "10")):
            payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "language": {"id": 1, "code2": None, "code3": "eng", "language": "English"}}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": position, "series": {"id": 1, "name": "S"}}]}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), expected)
            self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.LANGUAGE), "eng")
        boundary = "1" * 512
        payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": boundary, "series": {"id": 1, "name": "S"}}]}])
        claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
        self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), boundary)
        for position, state in (("9" * 513, FieldState.OVERSIZED), ("1e-999", FieldState.MALFORMED)):
            payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": position, "series": {"id": 1, "name": "S"}}]}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            self.assertEqual(next(claim.state for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), state)

    def test_no_usable_input_has_no_egress_and_malformed_is_all_or_nothing(self) -> None:
        calls: list[int] = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(1) or httpx.Response(200)))
        provider = HardcoverMetadataProvider("catalogue", client, 65_536)
        self.assertEqual(provider.observe(()).status, ProviderStatus.INSUFFICIENT_INPUT)
        self.assertEqual(calls, [])

        malformed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}, {"id": "bad", "book_id": 2, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": "Fine"}, {"id": 2, "title": None}]}})))
        observation = HardcoverMetadataProvider("catalogue", malformed, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.MALFORMED)
        self.assertEqual(observation.candidates, ())

    def test_separate_root_join_rejects_missing_duplicate_and_unreferenced_work(self) -> None:
        envelopes = (
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": []},
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": None}, {"id": 1, "title": None}]},
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": None}, {"id": 2, "title": None}]},
        )
        for data in envelopes:
            with self.subTest(data=data):
                client = httpx.Client(transport=httpx.MockTransport(lambda request, value=data: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": value})))
                self.assertEqual(HardcoverMetadataProvider("catalogue", client, 65_536).observe((self._claim(),)).status, ProviderStatus.MALFORMED)

    def test_multiple_inputs_are_selected_deterministically_and_statuses_do_not_retry(self) -> None:
        second = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="ISBN: 0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
        )
        print_only = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="Print ISBN: 9780306406157",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        observed_bodies: list[dict[str, object]] = []

        def success(request: httpx.Request) -> httpx.Response:
            observed_bodies.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [], "books": []}})

        observation = HardcoverMetadataProvider(
            "catalogue", httpx.Client(transport=httpx.MockTransport(success)), 65_536
        ).observe((print_only, second, self._claim()))
        self.assertEqual(observation.status, ProviderStatus.NO_MATCH)
        self.assertEqual(
            observed_bodies[0]["variables"],
            {"isbns": ["0306406152", "9780306406157"]},
        )
        self.assertEqual(
            observation.input_references,
            tuple(sorted((second.reference, self._claim().reference))),
        )

        for status_code, expected in (
            (401, ProviderStatus.AUTHENTICATION),
            (403, ProviderStatus.AUTHORIZATION),
            (429, ProviderStatus.RATE_LIMITED),
            (503, ProviderStatus.UNAVAILABLE),
        ):
            calls: list[int] = []

            def handler(request: httpx.Request, code: int = status_code) -> httpx.Response:
                calls.append(1)
                return httpx.Response(code)

            provider = HardcoverMetadataProvider(
                "catalogue", httpx.Client(transport=httpx.MockTransport(handler)), 65_536
            )
            with self.subTest(status=status_code):
                self.assertEqual(provider.observe((self._claim(),)).status, expected)
                self.assertEqual(provider.observe((self._claim(),)).status, ProviderStatus.UNAVAILABLE)
                self.assertEqual(calls, [1])

    def test_zero_one_and_multiple_candidates_are_neutral(self) -> None:
        for rows, expected in (
            ([], ProviderStatus.NO_MATCH),
            ([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], ProviderStatus.CANDIDATES),
            ([
                {"id": 2, "book_id": 2, "isbn_10": None, "isbn_13": None},
                {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None},
            ], ProviderStatus.CANDIDATES),
        ):
            client = httpx.Client(transport=httpx.MockTransport(lambda request, value=rows: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope(value, [{"id": row["book_id"], "title": None} for row in value]))))
            observation = HardcoverMetadataProvider("catalogue", client, 65_536).observe((self._claim(),))
            with self.subTest(count=len(rows)):
                self.assertEqual(observation.status, expected)
                self.assertEqual(len(observation.candidates), len(rows))
                self.assertEqual(
                    [candidate.identity for candidate in observation.candidates],
                    sorted(candidate.identity for candidate in observation.candidates),
                )

    def test_candidate_work_identity_is_joined_bounded_and_provider_local(self) -> None:
        same_work = hardcover_metadata._parse_candidates(
            json.dumps(self._envelope(
                [
                    {"id": 2, "book_id": 10, "isbn_10": None, "isbn_13": None},
                    {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": None},
                ],
                [{"id": 10, "title": None}],
            )).encode(),
            "catalogue",
            (),
        )
        self.assertEqual(
            [candidate.identity for candidate in same_work],
            ["hardcover-edition-0000000001", "hardcover-edition-0000000002"],
        )
        self.assertEqual(
            {candidate.work_identity for candidate in same_work},
            {"hardcover-work-0000000010"},
        )
        different_works = hardcover_metadata._parse_candidates(
            json.dumps(self._envelope(
                [
                    {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": None},
                    {"id": 2, "book_id": 20, "isbn_10": None, "isbn_13": None},
                ],
                [{"id": 10, "title": None}, {"id": 20, "title": None}],
            )).encode(),
            "catalogue",
            (),
        )
        self.assertEqual(
            {candidate.work_identity for candidate in different_works},
            {"hardcover-work-0000000010", "hardcover-work-0000000020"},
        )
        for malformed in (0, True, 2_147_483_648, "10"):
            with self.subTest(malformed=malformed):
                payload = self._envelope(
                    [{"id": 1, "book_id": malformed, "isbn_10": None, "isbn_13": None}],
                    [{"id": 1, "title": None}],
                )
                with self.assertRaises(ValueError):
                    hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())

    def test_sparse_candidate_is_valid_but_result_cap_overflow_is_malformed(self) -> None:
        sparse = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None}]))))
        observation = HardcoverMetadataProvider("catalogue", sparse, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertTrue(observation.candidates[0].claims)

        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope(rows, [{"id": index, "title": None} for index in range(1, 7)]))))
        observation = HardcoverMetadataProvider("catalogue", overflow, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        self.assertEqual(observation.candidates, ())

    def test_same_candidate_from_two_provider_connections_has_globally_unique_refs(self) -> None:
        payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}], [{"id": 1, "title": "Current title"}])

        def observation(name: str) -> ProviderObservation:
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            )
            return HardcoverMetadataProvider(name, client, 65_536).observe(
                (self._claim(), self._title_claim())
            )

        first = observation("catalogue")
        second = observation("secondary")
        first_references = {
            claim.reference
            for candidate in first.candidates
            for claim in candidate.claims
        }
        second_references = {
            claim.reference
            for candidate in second.candidates
            for claim in candidate.claims
        }
        self.assertTrue(first_references)
        self.assertTrue(first_references.isdisjoint(second_references))
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", "Current title"),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(self._claim(), self._title_claim()),
            local_reasons=("opening_isbn",),
            providers=(first, second),
        )
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)

    def test_credential_and_private_path_provider_titles_are_malformed_without_leakage(self) -> None:
        for hostile in (
            "api_key=supersecretvalue",
            "/Users/private/library/book.epub",
        ):
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, title=hostile: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={
                            "data": {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}], "books": [{"id": 1, "title": title}]}
                        },
                    )
                )
            )
            with self.subTest(hostile=hostile):
                observation = HardcoverMetadataProvider(
                    "catalogue", client, 65_536
                ).observe((self._claim(),))
                self.assertEqual(observation.status, ProviderStatus.MALFORMED)
                self.assertNotIn(hostile, repr(observation))


class AudiobookshelfItemTests(unittest.TestCase):
    def test_raw_cover_authority_preserves_nondefault_port(self) -> None:
        self.assertEqual("example.invalid:13378", _raw_cover_authority("http://example.invalid:13378"))
        self.assertEqual("example.invalid", _raw_cover_authority("https://example.invalid"))

    def test_raw_cover_request_uses_endpoint_authority(self) -> None:
        seen: dict[str, str] = {}

        class Response:
            status_code = 404
            headers: dict[str, str] = {}
            def __enter__(self): return self
            def __exit__(self, *_: object): return None

        class Client:
            def __enter__(self): return self
            def __exit__(self, *_: object): return None
            def stream(self, _method: str, _url: str, *, headers: dict[str, str], timeout: object):
                del timeout
                seen.update(headers)
                return Response()

        reader = AudiobookshelfItemReader(client_factory=lambda: httpx.Client(), endpoint="http://example.invalid:13378", library_id="library-id", service_path="/library", maximum_bytes=1)
        with patch("mechanical_librarian.adapters.library_services.audiobookshelf_item.httpx.Client", return_value=Client()):
            self.assertIsNone(reader._read_raw_cover(TargetCover("identity", "/cover", "item-1"), None))
        self.assertEqual("example.invalid:13378", seen["Host"])
    def test_scoped_reader_derives_stable_opaque_file_identity(self) -> None:
        payload = {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "media": {
                "metadata": {"title": "Current title"},
                "ebookFiles": [
                    {"ebookFormat": "epub", "ino": "inode-2", "metadata": {"relPath": "two.epub", "path": "/library/two.epub", "size": 2}},
                    {"ebookFormat": "epub", "ino": "inode-1", "metadata": {"relPath": "one.epub", "path": "/library/one.epub", "size": 1}},
                ],
            },
        }
        requests: list[httpx.Request] = []

        def factory() -> httpx.Client:
            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return httpx.Response(200, headers={"content-type": "application/json"}, content=json.dumps(payload).encode())
            return httpx.Client(transport=httpx.MockTransport(handler))

        reader = AudiobookshelfItemReader(client_factory=factory, endpoint="https://example.invalid", library_id="library-id", service_path="/library", maximum_bytes=65_536)
        first = reader.read("item-1")
        payload["media"]["ebookFiles"].reverse()
        second = reader.read("item-1")
        self.assertEqual(first.title, "Current title")
        self.assertEqual(first.files[0].identity, second.files[1].identity)
        self.assertEqual(len(requests), 2)

    def test_safe_noncanonical_title_is_preserved_exactly(self) -> None:
        title = "Cafe\u0301  Story"
        payload = {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "media": {
                "metadata": {"title": title},
                "ebookFile": {
                    "ebookFormat": "epub",
                    "ino": "inode-1",
                    "metadata": {
                        "relPath": "book.epub",
                        "path": "/library/book.epub",
                        "size": 1,
                    },
                },
            },
        }
        reader = AudiobookshelfItemReader(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            ),
            endpoint="https://example.invalid",
            library_id="library-id",
            service_path="/library",
            maximum_bytes=65_536,
        )
        self.assertEqual(reader.read("item-1").title, title)

    def test_duplicate_and_oversized_target_responses_fail_closed(self) -> None:
        duplicate = b'{"id":"item-1","id":"item-1"}'
        oversized = b"x" * 1025
        for payload, maximum in ((duplicate, 65_536), (oversized, 1024)):
            client_factory = lambda value=payload: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, body=value: httpx.Response(
                        200, headers={"content-type": "application/json"}, content=body
                    )
                )
            )
            reader = AudiobookshelfItemReader(
                client_factory=client_factory,
                endpoint="https://example.invalid",
                library_id="library-id",
                service_path="/library",
                maximum_bytes=maximum,
            )
            with self.subTest(maximum=maximum), self.assertRaises(MetadataReportError):
                reader.read("item-1")

    def test_ambiguous_singular_and_plural_file_shapes_are_rejected(self) -> None:
        file_value = {
            "ebookFormat": "epub",
            "ino": "inode-1",
            "metadata": {"relPath": "book.epub", "path": "/library/book.epub", "size": 1},
        }
        payload = {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "media": {
                "metadata": {"title": "Current title"},
                "ebookFile": file_value,
                "ebookFiles": [file_value],
            },
        }
        reader = AudiobookshelfItemReader(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, headers={"content-type": "application/json"}, json=payload
                    )
                )
            ),
            endpoint="https://example.invalid",
            library_id="library-id",
            service_path="/library",
            maximum_bytes=65_536,
        )
        with self.assertRaises(MetadataReportError):
            reader.read("item-1")

    def test_root_service_path_fallback_and_dual_path_agreement_are_closed(self) -> None:
        base_file = {
            "ebookFormat": "epub",
            "ino": "inode-1",
            "metadata": {"path": "/book.epub", "size": 1},
        }
        payload = {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "media": {
                "metadata": {"title": "Current title"},
                "ebookFile": base_file,
            },
        }

        def reader() -> AudiobookshelfItemReader:
            return AudiobookshelfItemReader(
                client_factory=lambda: httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=payload,
                        )
                    )
                ),
                endpoint="https://example.invalid",
                library_id="library-id",
                service_path="/",
                maximum_bytes=65_536,
            )

        self.assertEqual(reader().read("item-1").files[0].relative_path, "book.epub")
        base_file["metadata"]["relPath"] = "book.epub"
        self.assertEqual(reader().read("item-1").files[0].relative_path, "book.epub")
        base_file["metadata"]["relPath"] = "other.epub"
        with self.assertRaises(MetadataReportError):
            reader().read("item-1")

    def test_credential_and_private_path_target_titles_fail_without_raw_values(self) -> None:
        for hostile in (
            "Authorization: Bearer abcd1234",
            "/Users/private/library/book.epub",
        ):
            payload = {
                "id": "item-1",
                "libraryId": "library-id",
                "isInvalid": False,
                "isMissing": False,
                "mediaType": "book",
                "media": {
                    "metadata": {"title": hostile},
                    "ebookFile": {
                        "ebookFormat": "epub",
                        "ino": "inode-1",
                        "metadata": {
                            "relPath": "book.epub",
                            "path": "/library/book.epub",
                            "size": 1,
                        },
                    },
                },
            }
            reader = AudiobookshelfItemReader(
                client_factory=lambda: httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=payload,
                        )
                    )
                ),
                endpoint="https://example.invalid",
                library_id="library-id",
                service_path="/library",
                maximum_bytes=65_536,
            )
            with self.subTest(hostile=hostile):
                with self.assertRaises(MetadataReportError) as raised:
                    reader.read("item-1")
                self.assertNotIn(hostile, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
