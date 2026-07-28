from __future__ import annotations

import json
import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx

import mechanical_librarian.adapters.persistence.metadata_reports as metadata_persistence

from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader, _raw_cover_authority
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HARDCOVER_URL,
    HardcoverMetadataProvider,
    HardcoverPacer,
)
import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    MetadataReportError,
    TargetCover,
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
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
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    isbn_family,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact, CoverDesignation, CoverSource, CoverSourceKind,
    CoverSourceState, cover_source_id,
)


class _InterruptedProviderStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x"
        raise httpx.ReadTimeout("synthetic interrupted body")


class _HostileEncodedProviderStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"not-gzip"


def _isbn_lookup(claims: tuple[LocalClaim, ...]) -> MetadataLookup:
    return MetadataLookup(ProviderQueryKind.ISBN, claims)


def _work_lookup(
    title: str = "Current title",
    authors: tuple[str, ...] = ("Ada Example",),
) -> MetadataLookup:
    return MetadataLookup(ProviderQueryKind.WORK, (), title, authors)


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
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


def _target_snapshot(metadata: dict[str, object]) -> TargetSnapshot:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": metadata,
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
        service_path="/library",
        maximum_bytes=65_536,
    ).read("item-1")


def _create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        metadata_persistence._REPORTS_SQL.replace(
            "= 'metadata-report-v11'", "= 'metadata-report-v1'"
        )
    )
    connection.execute(metadata_persistence._CURRENT_SQL)
    connection.execute(metadata_persistence._NO_UPDATE_SQL)
    connection.execute(metadata_persistence._NO_DELETE_SQL)


def _create_v9_schema(connection: sqlite3.Connection) -> None:
    statements = (
        metadata_persistence._REPORTS_SQL.replace(
            "= 'metadata-report-v11'", "= 'metadata-report-v9'"
        ),
        metadata_persistence._CURRENT_SQL,
        metadata_persistence._CURRENT_LINEAGE_SQL,
        metadata_persistence._COVER_REFERENCES_SQL,
        metadata_persistence._COVER_ARTIFACT_INDEX_SQL,
        metadata_persistence._COVER_PREVIEW_INDEX_SQL,
        metadata_persistence._PROPOSAL_SETS_SQL,
        metadata_persistence._REVIEW_CASES_SQL,
        metadata_persistence._REVIEW_POINTERS_SQL,
        metadata_persistence._REVIEW_TRANSITIONS_SQL,
        metadata_persistence._SHADOW_REQUESTS_SQL,
        metadata_persistence._SHADOW_ATTEMPTS_SQL,
        metadata_persistence._SHADOW_DISPATCHES_SQL,
        metadata_persistence._SHADOW_OUTCOMES_SQL,
        metadata_persistence._NO_UPDATE_SQL,
        metadata_persistence._COVER_NO_UPDATE_SQL,
        metadata_persistence._COVER_NO_DELETE_SQL,
        metadata_persistence._PROPOSAL_NO_UPDATE_SQL,
        metadata_persistence._PROPOSAL_NO_DELETE_SQL,
        metadata_persistence._REVIEW_NO_UPDATE_SQL,
        metadata_persistence._REVIEW_NO_DELETE_SQL,
        metadata_persistence._REVIEW_TRANSITION_NO_UPDATE_SQL,
        metadata_persistence._REVIEW_TRANSITION_NO_DELETE_SQL,
        metadata_persistence._SHADOW_REQUEST_NO_UPDATE_SQL,
        metadata_persistence._SHADOW_REQUEST_NO_DELETE_SQL,
        metadata_persistence._SHADOW_ATTEMPT_NO_UPDATE_SQL,
        metadata_persistence._SHADOW_ATTEMPT_NO_DELETE_SQL,
        metadata_persistence._SHADOW_DISPATCH_NO_UPDATE_SQL,
        metadata_persistence._SHADOW_DISPATCH_NO_DELETE_SQL,
        metadata_persistence._SHADOW_OUTCOME_NO_UPDATE_SQL,
        metadata_persistence._SHADOW_OUTCOME_NO_DELETE_SQL,
        metadata_persistence._NO_DELETE_SQL,
    )
    for statement in statements:
        connection.execute(statement)


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
                "previous v9",
                _create_v9_schema,
            ),
            (
                "F09k report schema",
                lambda connection: (
                    connection.execute(
                        metadata_persistence._REPORTS_SQL.replace(
                            "= 'metadata-report-v6'",
                            "= 'metadata-report-v5'",
                        )
                    ),
                    connection.execute(metadata_persistence._CURRENT_SQL),
                    connection.execute(metadata_persistence._NO_UPDATE_SQL),
                    connection.execute(metadata_persistence._NO_DELETE_SQL),
                ),
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
            schema_version = connection.execute(
                "SELECT schema_version FROM metadata_reports"
            ).fetchone()
            rows = connection.execute("SELECT report_id FROM metadata_reports ORDER BY report_id").fetchall()
            current = connection.execute("SELECT report_id FROM current_metadata_reports").fetchone()
            connection.close()
            self.assertEqual(
                tables,
                [
                    ("current_metadata_report_lineages",),
                    ("current_metadata_reports",),
                    ("current_review_case_lineages",),
                    ("metadata_report_cover_references",),
                    ("metadata_reports",),
                    ("proposal_sets",),
                    ("review_case_transitions",),
                    ("review_cases",),
                    ("shadow_review_attempts",),
                    ("shadow_review_dispatches",),
                    ("shadow_review_outcomes",),
                    ("shadow_review_requests",),
                ],
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(schema_version, ("metadata-report-v11",))
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
            other = MetadataReport.create(target=TargetFacts("secondary", "ebooks", "item-2", default_target_fields("Other title")), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
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
            other = MetadataReport.create(target=TargetFacts("secondary", "ebooks", "item-2", default_target_fields("Other title")), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
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
            competing_open_started = threading.Event()
            reopened_after_close: list[bool] = []
            reopen_failures: list[Exception] = []

            def competing_open() -> None:
                try:
                    competing_open_started.set()
                    competing = open_repository(path)
                    reopened_after_close.append(True)
                    competing.close()
                except Exception as error:
                    reopen_failures.append(error)

            competitor = threading.Thread(target=competing_open)
            competitor.start()
            self.assertTrue(competing_open_started.wait(1))
            competitor.join(0.05)
            self.assertTrue(competitor.is_alive())
            self.assertEqual([], reopened_after_close)
            report = _report()
            repository.publish(report, lambda: True)
            repository.close()
            competitor.join(2)
            self.assertFalse(competitor.is_alive())
            self.assertEqual([], reopen_failures)
            self.assertEqual([True], reopened_after_close)
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
                target=TargetFacts("secondary", "other", "item-2", default_target_fields("Other title")),
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
                "image": row.get("image"),
                "publisher": row.get("publisher"), "language": row.get("language"),
                "contributions": row.get("contributions", []),
            }
            for row in rows
        ]
        works: list[dict[str, object]] = []
        tags: list[dict[str, object]] = []
        seen_tags: set[object] = set()
        for book in books:
            taggings: list[dict[str, object]] = []
            for raw_tagging in book.get("taggings", []):  # type: ignore[union-attr]
                tagging = cast(dict[str, object], raw_tagging)
                raw_tag = tagging.get("tag")
                if type(raw_tag) is dict:
                    tag = {
                        **raw_tag,
                        "tag_category": raw_tag.get(
                            "tag_category",
                            {"id": 1, "category": "Mood"},
                        ),
                    }
                    tag_id = tag.get("id")
                    if tag_id not in seen_tags:
                        seen_tags.add(tag_id)
                        tags.append(tag)
                else:
                    tag_id = tagging.get("tag_id")
                taggings.append(
                    {
                        "id": tagging.get("id"),
                        "tag_id": tag_id,
                    }
                )
            works.append(
                {
                    "id": book["id"],
                    "title": book.get("title"),
                    "description": book.get("description"),
                    "release_date": book.get("release_date"),
                    "image": book.get("image"),
                    "contributions": book.get("contributions", []),
                    "book_series": book.get("book_series", []),
                    "taggings": taggings,
                }
            )
        return {
            "data": {
                "editions": editions,
                "books": works,
                "tags": tags,
            }
        }
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

    def _observe_contributions(
        self,
        contributions: list[dict[str, object]],
    ) -> ProviderObservation:
        payload = self._envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                    "contributions": contributions,
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Synthetic title",
                    "release_date": "2001-02-03",
                    "contributions": contributions,
                }
            ],
        )
        return HardcoverMetadataProvider(
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
        ).observe(_isbn_lookup((self._claim(),)))

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
        observation = provider.observe(_isbn_lookup((self._claim(), self._title_claim())))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertEqual([item.identity for item in observation.candidates], ["hardcover-edition-0000000001", "hardcover-edition-0000000002"])
        claims = {claim.kind: claim for claim in observation.candidates[1].claims}
        self.assertEqual(claims[ClaimKind.ISBN].links[0].outcome, Comparison.AGREEMENT)
        self.assertEqual(claims[ClaimKind.TITLE].links[0].outcome, Comparison.DISAGREEMENT)
        self.assertEqual(observation.input_references, (self._claim().reference,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(4, len(provider.cover_descriptors))
        self.assertTrue(
            all(descriptor.url is None for descriptor in provider.cover_descriptors)
        )
        self.assertEqual(str(requests[0].url), HARDCOVER_URL)
        self.assertIn("limit:6", json.loads(requests[0].content)["query"])
        self.assertEqual(provider.observe(_isbn_lookup((self._claim(), self._title_claim()))).status, ProviderStatus.UNAVAILABLE)
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
        observation = provider.observe(_isbn_lookup((self._claim(), self._title_claim())))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(2, len(provider.cover_descriptors))
        edition = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.designation.value == "exact_edition"
        )
        self.assertEqual(CoverSourceState.OVERSIZED, edition.terminal_state)
        self.assertIsNone(edition.url)

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
        ).observe(_isbn_lookup((self._claim(), other_isbn, self._title_claim(), other_title)))
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
        observation = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(handler)), 65_536).observe(_isbn_lookup((self._claim(), self._title_claim())))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        claims = observation.candidates[0].claims
        self.assertEqual([claim.raw_order for claim in claims], list(range(len(claims))))
        self.assertEqual({claim.kind for claim in claims}, {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLICATION_DATE, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE, ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR, ClaimKind.DESCRIPTION, ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE, ClaimKind.TAG, ClaimKind.GENRE})
        self.assertEqual(next(claim.scope for claim in claims if claim.kind is ClaimKind.PUBLISHER), ClaimScope.EDITION)
        self.assertNotIn("parent_id", {claim.value for claim in claims if claim.value is not None})
        self.assertIn("contributions(order_by:{id:asc},limit:17)", captured[0]["query"])
        self.assertIn("taggings(order_by:{id:asc},limit:33)", captured[0]["query"])

    def test_hardcover_operations_keep_relationship_selections_within_depth_three(
        self,
    ) -> None:
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            )

        for lookup in (
            _isbn_lookup((self._claim(),)),
            _work_lookup(),
        ):
            with self.subTest(kind=lookup.kind):
                captured.clear()
                observation = HardcoverMetadataProvider(
                    "catalogue",
                    httpx.Client(transport=httpx.MockTransport(handler)),
                    65_536,
                ).observe(lookup)
                self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
                operation = captured[0]
                self.assertNotIn(
                    "taggings(order_by:{id:asc},limit:33)"
                    "{id tag{id tag tag_category",
                    operation["query"],
                )
                self.assertIn(
                    "taggings(order_by:{id:asc},limit:33){id tag_id}",
                    operation["query"],
                )
                self.assertIn("tags:tags(", operation["query"])
                self.assertIn(
                    "{id tag tag_category{id category}}",
                    operation["query"],
                )
                if lookup.kind is ProviderQueryKind.ISBN:
                    self.assertEqual(
                        {"isbns": ["9780306406157"]},
                        operation["variables"],
                    )
                else:
                    self.assertEqual(
                        {
                            "authors": ["Ada Example"],
                            "title": "Current title",
                        },
                        operation["variables"],
                    )

    def test_http_success_graphql_errors_are_typed_before_data(self) -> None:
        error = {
            "message": "query exceeds the provider depth limit",
            "extensions": {
                "code": "validation-failed",
                "path": "$.selectionSet.books.selectionSet",
            },
        }
        for payload in (
            {"errors": [error]},
            {
                "data": self._envelope(
                    [
                        {
                            "id": 1,
                            "book_id": 1,
                            "isbn_10": None,
                            "isbn_13": "9780306406157",
                        }
                    ],
                    [{"id": 1, "title": "Current title"}],
                )["data"],
                "errors": [error],
            },
        ):
            with self.subTest(partial="data" in payload):
                provider = HardcoverMetadataProvider(
                    "catalogue",
                    httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request, value=payload: httpx.Response(
                                200,
                                headers={"content-type": "application/json"},
                                json=value,
                            )
                        )
                    ),
                    65_536,
                )
                observation = provider.observe(
                    _isbn_lookup((self._claim(), self._title_claim()))
                )
                self.assertEqual(ProviderStatus.UNAVAILABLE, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertEqual((), provider.cover_descriptors)

    def test_graphql_error_envelope_bounds_are_closed_and_do_not_leak(self) -> None:
        def observe(payload: object) -> ProviderObservation:
            return HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            content=(
                                payload
                                if type(payload) is bytes
                                else json.dumps(payload).encode()
                            ),
                        )
                    )
                ),
                65_536,
            ).observe(_isbn_lookup((self._claim(),)))

        exact_message = "x" * hardcover_metadata.MAX_GRAPHQL_ERROR_MESSAGE_CHARACTERS
        for errors in (
            [{"message": exact_message}],
            [{"message": "declared"}] * hardcover_metadata.MAX_GRAPHQL_ERRORS,
            [
                {
                    "message": "Authorization: Bearer synthetic-private-token",
                    "locations": [{"line": 1, "column": 2}],
                    "path": ["books", 0],
                    "extensions": {
                        "code": "validation-failed",
                        "path": "$.selectionSet.books",
                    },
                }
            ],
        ):
            with self.subTest(valid=len(errors)):
                observation = observe({"errors": errors})
                self.assertEqual(ProviderStatus.UNAVAILABLE, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertNotIn("synthetic-private-token", repr(observation))

        malformed = (
            {"errors": []},
            {"errors": [{"message": "x" * (len(exact_message) + 1)}]},
            {
                "errors": [{"message": "declared"}]
                * (hardcover_metadata.MAX_GRAPHQL_ERRORS + 1)
            },
            {"errors": [{"message": "declared", "unexpected": True}]},
            {"errors": [{"message": "declared", "extensions": {"nested": {}}}]},
            b'{"errors":[{"message":"\xff"}]}',
            b'{"errors":',
        )
        for payload in malformed:
            with self.subTest(payload=repr(payload)[:80]):
                observation = observe(payload)
                self.assertEqual(ProviderStatus.MALFORMED, observation.status)
                self.assertEqual((), observation.candidates)

    def test_graphql_error_transport_timeout_and_expired_deadline_are_unavailable(
        self,
    ) -> None:
        timed_out = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: (_ for _ in ()).throw(
                        httpx.ReadTimeout("synthetic timeout")
                    )
                )
            ),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        ).observe(_isbn_lookup((self._claim(),)))
        self.assertEqual(ProviderStatus.UNAVAILABLE, timed_out.status)

        requests: list[httpx.Request] = []
        expired = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: requests.append(request)
                    or httpx.Response(500)
                )
            ),
            65_536,
        ).observe(_isbn_lookup((self._claim(),)), deadline=0)
        self.assertEqual(ProviderStatus.UNAVAILABLE, expired.status)
        self.assertEqual([], requests)

    def test_hardcover_retries_declared_transients_with_exact_authority(
        self,
    ) -> None:
        for status_code in (500, 502, 503, 504):
            responses = [
                httpx.Response(
                    status_code,
                    headers={"retry-after": "60"},
                ),
                httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=self._envelope([], []),
                ),
            ]
            delays: list[float] = []
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, values=responses: values.pop(0)
                    )
                ),
                65_536,
                sleep=delays.append,
                jitter=lambda: 0.0,
            )
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(_isbn_lookup((self._claim(),))).status,
                )
                self.assertEqual(
                    [60.0] if status_code == 503 else [1.0],
                    delays,
                )
                self.assertEqual(2, len(provider.attempt_records))

        dispatches = 0

        def unavailable(_: httpx.Request) -> httpx.Response:
            nonlocal dispatches
            dispatches += 1
            return httpx.Response(504)

        exhausted = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(unavailable)),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            exhausted.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual(3, dispatches)
        self.assertEqual("exhausted", exhausted.attempt_records[-1].outcome)

    def test_hardcover_recovers_at_every_permitted_retry_ordinal(
        self,
    ) -> None:
        for prior_failures in range(3):
            responses = [
                httpx.Response(503) for _ in range(prior_failures)
            ]
            responses.append(
                httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=self._envelope([], []),
                )
            )
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, values=responses: values.pop(0)
                    )
                ),
                65_536,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(prior_failures=prior_failures):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(
                        _isbn_lookup((self._claim(),))
                    ).status,
                )
                self.assertEqual(
                    prior_failures + 1, len(provider.attempt_records)
                )
                self.assertEqual(
                    ["retry"] * prior_failures + ["response_received"],
                    [record.outcome for record in provider.attempt_records],
                )

    def test_hardcover_charges_error_bodies_before_status_retry(self) -> None:
        oversized_calls = 0

        def oversized(_: httpx.Request) -> httpx.Response:
            nonlocal oversized_calls
            oversized_calls += 1
            return httpx.Response(503, content=b"x" * 2_000)

        oversized_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(oversized)),
            1_024,
            sleep=lambda _: self.fail("oversized body must not retry"),
        )
        self.assertEqual(
            ProviderStatus.OVERSIZED,
            oversized_provider.observe(
                _isbn_lookup((self._claim(),))
            ).status,
        )
        self.assertEqual(1, oversized_calls)
        self.assertEqual(0, oversized_provider._remaining_response_bytes)

        success = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=self._envelope([], []),
        )
        control = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: success)
            ),
            1_024,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            control.observe(_isbn_lookup((self._claim(),))).status,
        )
        responses = [
            httpx.Response(503, stream=_InterruptedProviderStream()),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            ),
        ]
        interrupted = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: responses.pop(0))
            ),
            1_024,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            interrupted.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual(
            control._remaining_response_bytes - 1,
            interrupted._remaining_response_bytes,
        )
        encoded_responses = [
            httpx.Response(
                503,
                headers={"content-encoding": "gzip"},
                stream=_HostileEncodedProviderStream(),
            ),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            ),
        ]
        encoded = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: encoded_responses.pop(0)
                )
            ),
            1_024,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            encoded.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual(
            control._remaining_response_bytes - len(b"not-gzip"),
            encoded._remaining_response_bytes,
        )

    def test_hardcover_429_retries_only_valid_retry_after_and_graphql_never(
        self,
    ) -> None:
        responses = [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            ),
        ]
        delays: list[float] = []
        recovered = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: responses.pop(0))
            ),
            65_536,
            sleep=delays.append,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            recovered.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual([2.0], delays)

        for retry_after in (None, "61", "2, 3"):
            calls = 0

            def rate_limited(_: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                headers = (
                    {}
                    if retry_after is None
                    else {"retry-after": retry_after}
                )
                return httpx.Response(429, headers=headers)

            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(rate_limited)
                ),
                65_536,
                sleep=lambda _: self.fail("invalid 429 must not retry"),
            )
            with self.subTest(retry_after=retry_after):
                self.assertEqual(
                    ProviderStatus.RATE_LIMITED,
                    provider.observe(
                        _isbn_lookup((self._claim(),))
                    ).status,
                )
                self.assertEqual(1, calls)

        graphql_calls = 0

        def graphql_error(_: httpx.Request) -> httpx.Response:
            nonlocal graphql_calls
            graphql_calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errors": [{"message": "declared"}]},
            )

        graphql = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(graphql_error)),
            65_536,
            sleep=lambda _: self.fail("GraphQL errors must not retry"),
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            graphql.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual(1, graphql_calls)
        self.assertNotEqual(
            "success", graphql.attempt_records[0].outcome
        )
        self.assertEqual(
            "response_received", graphql.attempt_records[0].outcome
        )

    def test_hardcover_pacer_shares_one_second_dispatch_starts(self) -> None:
        now = [0.0]
        delays: list[float] = []

        def sleep(delay: float) -> None:
            delays.append(delay)
            now[0] += delay

        pacer = HardcoverPacer(clock=lambda: now[0], sleep=sleep)
        self.assertTrue(pacer.wait(deadline=None))
        self.assertTrue(pacer.wait(deadline=None))
        self.assertEqual([1.0], delays)
        self.assertFalse(pacer.wait(deadline=now[0] + 1.0))
        self.assertEqual([1.0], delays)

    def test_hardcover_requests_retries_and_instances_share_pacing(
        self,
    ) -> None:
        now = [0.0]
        sleeps: list[float] = []
        dispatch_times: list[float] = []

        def sleep(delay: float) -> None:
            sleeps.append(delay)
            now[0] += delay

        pacer = HardcoverPacer(clock=lambda: now[0], sleep=sleep)
        responses = [
            httpx.Response(503),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            ),
        ]

        def retrying(_: httpx.Request) -> httpx.Response:
            dispatch_times.append(now[0])
            return responses.pop(0)

        first = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(retrying)),
            65_536,
            pacer=pacer,
            clock=lambda: now[0],
            sleep=sleep,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            first.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual([0.0, 1.0], dispatch_times)
        self.assertEqual([1.0], sleeps)

        second = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: dispatch_times.append(now[0])
                    or httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=self._envelope([], []),
                    )
                )
            ),
            65_536,
            pacer=pacer,
            clock=lambda: now[0],
            sleep=sleep,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            second.observe(_isbn_lookup((self._claim(),))).status,
        )
        self.assertEqual([0.0, 1.0, 2.0], dispatch_times)
        self.assertEqual([1.0, 1.0], sleeps)

    def test_hardcover_deadline_and_aggregate_attempt_bounds_are_exact(
        self,
    ) -> None:
        rate_limit_calls = 0

        def rate_limited(_: httpx.Request) -> httpx.Response:
            nonlocal rate_limit_calls
            rate_limit_calls += 1
            return httpx.Response(429, headers={"retry-after": "2"})

        deadline_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(rate_limited)),
            65_536,
            clock=lambda: 0.0,
            sleep=lambda _: self.fail("deadline must reject Retry-After"),
        )
        self.assertEqual(
            ProviderStatus.RATE_LIMITED,
            deadline_provider.observe(
                _isbn_lookup((self._claim(),)), deadline=2.0
            ).status,
        )
        self.assertEqual(1, rate_limit_calls)

        values = (
            "9780306406157",
            "9780140328721",
            "9780439023528",
            "9780061120084",
            "9780743273565",
            "9780316769488",
            "9780451524935",
            "9780547928227",
        )
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed=value,
                canonical=value,
                excerpt=f"ISBN {value}",
                applicability="ebook",
                basis="opening_matter",
                checksum="valid_isbn_13",
                applicability_basis="ebook",
                scope=ClaimScope.EDITION,
            )
            for value in values
        )
        title = LocalClaim.create(
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
        fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                ("Ada Example",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("Current title")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot(
                "ebook",
                "Current title",
                (),
                fields=fields,
            ),
            (title, *claims),
        )
        dispatches = 0

        def unavailable(_: httpx.Request) -> httpx.Response:
            nonlocal dispatches
            dispatches += 1
            return httpx.Response(503)

        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(unavailable)),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        provider.observe(plan)
        self.assertEqual(27, dispatches)
        self.assertEqual(27, len(provider.attempt_records))

        interrupted_dispatches = 0

        def interrupted(_: httpx.Request) -> httpx.Response:
            nonlocal interrupted_dispatches
            interrupted_dispatches += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=_InterruptedProviderStream(),
            )

        interrupted_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(interrupted)),
            1,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        interrupted_provider.observe(plan)
        self.assertEqual(9, interrupted_dispatches)
        self.assertEqual(9, len(interrupted_provider.attempt_records))

    def test_work_lookup_retains_typed_taxonomy_and_first_publication(self) -> None:
        captured: list[dict[str, object]] = []
        payload = self._envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Current title",
                    "release_date": "2001-02-03",
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {"id": 7, "name": "Ada Example"},
                        }
                    ],
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 11,
                                "tag": "Fantasy",
                                "tag_category": {"id": 2, "category": "Genre"},
                            },
                        },
                        {
                            "id": 2,
                            "tag": {
                                "id": 12,
                                "tag": "Hopeful",
                                "tag_category": {"id": 3, "category": "Mood"},
                            },
                        },
                        {
                            "id": 3,
                            "tag": {
                                "id": 13,
                                "tag": "Violence",
                                "tag_category": {
                                    "id": 4,
                                    "category": "ContentWarning",
                                },
                            },
                        },
                    ],
                }
            ],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=payload,
            )

        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        )
        observation = provider.observe(_work_lookup())
        self.assertEqual(ProviderQueryKind.WORK, observation.query_kind)
        self.assertEqual("MetadataEvidenceByWork", captured[0]["operationName"])
        self.assertEqual(
            {"authors": ["Ada Example"], "title": "Current title"},
            captured[0]["variables"],
        )
        work_claims = [
            claim
            for claim in observation.candidates[0].claims
            if claim.scope is ClaimScope.WORK and claim.state is FieldState.VALID
        ]
        self.assertEqual(
            {"Fantasy"},
            {claim.value for claim in work_claims if claim.kind is ClaimKind.GENRE},
        )
        self.assertEqual(
            {"Hopeful", "Violence"},
            {claim.value for claim in work_claims if claim.kind is ClaimKind.TAG},
        )
        self.assertEqual(
            {
                ProviderEvidenceKind.AUTHOR_IDENTITY,
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            },
            {value.kind for value in observation.candidates[0].evidence},
        )
        publication = next(
            value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
        )
        self.assertEqual((ClaimScope.WORK, "2001-02-03"), (publication.scope, publication.value))
        default_cover = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.designation.value == "default"
        )
        self.assertEqual(
            [("publication_date", "2001-02-03")],
            [
                (value.field.value, value.value)
                for value in default_cover.provenance.context
            ],
        )

    def test_hardcover_taxonomy_and_work_publication_fail_closed(self) -> None:
        def observe(book: dict[str, object]) -> ProviderObservation:
            payload = self._envelope(
                [
                    {
                        "id": 1,
                        "book_id": 1,
                        "isbn_10": None,
                        "isbn_13": None,
                    }
                ],
                [book],
            )
            return HardcoverMetadataProvider(
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
            ).observe(_work_lookup())

        base: dict[str, object] = {
            "id": 1,
            "title": "Current title",
            "contributions": [
                {
                    "id": 1,
                    "contribution": "Author",
                    "author": {"id": 7, "name": "Ada Example"},
                }
            ],
        }
        for value in (
            {
                **base,
                "taggings": [
                    {
                        "id": 1,
                        "tag": {
                            "id": 2,
                            "tag": "Unknown",
                            "tag_category": {"id": 9, "category": "Subject"},
                        },
                    }
                ],
            },
            {
                **base,
                "taggings": [
                    {
                        "id": 1,
                        "tag": {
                            "id": 2,
                            "tag": "Shared",
                            "tag_category": {"id": 9, "category": "Genre"},
                        },
                    },
                    {
                        "id": 2,
                        "tag": {
                            "id": 3,
                            "tag": "Shared",
                            "tag_category": {"id": 10, "category": "Mood"},
                        },
                    },
                ],
            },
            {**base, "release_date": "not-a-date"},
        ):
            with self.subTest(value=value):
                self.assertEqual(ProviderStatus.MALFORMED, observe(value).status)

        overflow = {
            **base,
            "taggings": [
                {
                    "id": index,
                    "tag": {
                        "id": index,
                        "tag": f"Tag {index}",
                        "tag_category": {"id": 1, "category": "Mood"},
                    },
                }
                for index in range(1, 34)
            ],
        }
        observation = observe(overflow)
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        states = {
            claim.kind: claim.state
            for claim in observation.candidates[0].claims
            if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        }
        self.assertEqual(
            {ClaimKind.GENRE: FieldState.OVERSIZED, ClaimKind.TAG: FieldState.OVERSIZED},
            states,
        )

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
        self.assertEqual(by_kind[ClaimKind.GENRE].state.value, "absent")
        contributors = [claim for claim in claims if claim.kind is ClaimKind.CONTRIBUTOR]
        self.assertIn("translator", {claim.role for claim in contributors})
        self.assertTrue(any(claim.state.value == "malformed" for claim in contributors))
        print_claim = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="Print ISBN: 9780306406157",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        print_claims = hardcover_metadata._parse_candidates(
            payload,
            "catalogue",
            (print_claim,),
        )[0].claims
        self.assertEqual(
            {"print"},
            {
                claim.applicability
                for claim in print_claims
                if claim.kind is ClaimKind.ISBN and claim.value is not None
            },
        )

    def test_response_cap_and_candidate_probe_are_terminal(self) -> None:
        payload = self._envelope([], [])
        capped = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}" * 20))), 1)
        self.assertEqual(capped.observe(_isbn_lookup((self._claim(),))).status, ProviderStatus.OVERSIZED)
        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope(rows, [{"id": index, "title": None} for index in range(1, 7)])))), 65_536)
        self.assertEqual(overflow.observe(_isbn_lookup((self._claim(),))).status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        for size, expected in ((262_144, ProviderStatus.MALFORMED), (262_145, ProviderStatus.OVERSIZED)):
            provider = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request, length=size: httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * length))), 262_144)
            self.assertEqual(provider.observe(_isbn_lookup((self._claim(),))).status, expected)

    def test_exact_rich_caps_and_pinned_schema_are_admitted(self) -> None:
        self.assertEqual(hardcover_metadata.HARDCOVER_SCHEMA_COMMIT, "1c5bdbd3244e02b03ccc658a792793e39cba17a8")
        self.assertIn("contributions(order_by:{id:asc},limit:17){id contribution author{id name}}", hardcover_metadata._QUERY)
        self.assertIn("books:books(", hardcover_metadata._QUERY)
        self.assertNotIn("books_through_editions", hardcover_metadata._QUERY)
        self.assertIn("book_series(order_by:{id:asc},limit:17){id position series{id name}}", hardcover_metadata._QUERY)
        self.assertIn(
            "taggings(order_by:{id:asc},limit:33){id tag_id}",
            hardcover_metadata._QUERY,
        )
        self.assertIn(
            "tags:tags(",
            hardcover_metadata._QUERY,
        )
        self.assertIn(
            "{id tag tag_category{id category}}",
            hardcover_metadata._QUERY,
        )
        for scalar, description, expected in (("x" * 512, "d" * 16384, "valid"), ("x" * 513, "d" * 16385, "oversized")):
            with self.subTest(expected=expected):
                payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "title": scalar}], [{"id": 1, "title": None, "description": description}])
                claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", () )[0].claims
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.TITLE), expected)
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.DESCRIPTION), expected)
        for count, kind, scope, key in ((16, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (17, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (16, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (17, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (16, ClaimKind.SERIES, ClaimScope.WORK, "series"), (17, ClaimKind.SERIES, ClaimScope.WORK, "series"), (32, ClaimKind.TAG, ClaimScope.WORK, "tag"), (33, ClaimKind.TAG, ClaimScope.WORK, "tag")):
            contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": f"A{index}"}} for index in range(1, count + 1)]
            series = [{"id": index, "position": str(index), "series": {"id": index, "name": f"S{index}"}} for index in range(1, count + 1)]
            tags = [{"id": index, "tag": {"id": index, "tag": f"T{index}"}} for index in range(1, count + 1)]
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

    def test_nullable_contribution_roles_preserve_candidate_with_typed_malformed_claims(self) -> None:
        contribution = {
            "id": 1,
            "contribution": None,
            "author": {"id": 1, "name": "Synthetic Contributor"},
        }
        observation = self._observe_contributions([contribution])

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        malformed = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.CONTRIBUTOR
            and claim.state is FieldState.MALFORMED
        ]
        self.assertEqual(
            {ClaimScope.EDITION, ClaimScope.WORK},
            {claim.scope for claim in malformed},
        )
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

    def test_nullable_and_malformed_contribution_authors_are_field_local(self) -> None:
        for label, role, author, expected_kind in (
            ("both-null", None, None, ClaimKind.CONTRIBUTOR),
            ("null-author", "Author", None, ClaimKind.CREATOR),
            ("malformed-author", "Translator", "invalid", ClaimKind.CONTRIBUTOR),
            ("malformed-role-and-author", {}, "invalid", ClaimKind.CONTRIBUTOR),
        ):
            with self.subTest(label=label):
                contribution = {
                    "id": 1,
                    "contribution": role,
                    "author": author,
                }
                observation = self._observe_contributions([contribution])

                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                malformed = [
                    claim
                    for claim in observation.candidates[0].claims
                    if claim.kind is expected_kind
                    and claim.state is FieldState.MALFORMED
                ]
                self.assertEqual(
                    {ClaimScope.EDITION, ClaimScope.WORK},
                    {claim.scope for claim in malformed},
                )
                self.assertEqual(
                    {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
                    {
                        evidence.kind
                        for evidence in observation.candidates[0].evidence
                    },
                )

    def test_malformed_author_identity_is_omitted_without_losing_valid_name_claims(self) -> None:
        contribution = {
            "id": 1,
            "contribution": "Author",
            "author": {"id": None, "name": "Synthetic Contributor"},
        }
        observation = self._observe_contributions([contribution])

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        creators = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        ]
        self.assertEqual(
            {ClaimScope.EDITION, ClaimScope.WORK},
            {claim.scope for claim in creators},
        )
        self.assertTrue(all(claim.state is FieldState.VALID for claim in creators))
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

    def test_nullable_contribution_overflow_remains_bounded_without_author_inference(self) -> None:
        contributions = [
            {"id": index, "contribution": None, "author": None}
            for index in range(1, 18)
        ]
        observation = self._observe_contributions(contributions)

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        contribution_claims = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
        ]
        self.assertEqual(
            {
                (ClaimKind.CONTRIBUTOR, ClaimScope.EDITION, FieldState.OVERSIZED),
                (ClaimKind.CONTRIBUTOR, ClaimScope.WORK, FieldState.OVERSIZED),
            },
            {
                (claim.kind, claim.scope, claim.state)
                for claim in contribution_claims
            },
        )
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

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
                self.assertEqual(provider.observe(_isbn_lookup((self._claim(),))).status, ProviderStatus.MALFORMED)

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
        self.assertEqual(provider.observe(_isbn_lookup(())).status, ProviderStatus.INSUFFICIENT_INPUT)
        self.assertEqual(calls, [])

        malformed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}, {"id": "bad", "book_id": 2, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": "Fine"}, {"id": 2, "title": None}]}})))
        observation = HardcoverMetadataProvider("catalogue", malformed, 65_536).observe(_isbn_lookup((self._claim(),)))
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
                self.assertEqual(HardcoverMetadataProvider("catalogue", client, 65_536).observe(_isbn_lookup((self._claim(),))).status, ProviderStatus.MALFORMED)

    def test_separate_taxonomy_root_join_rejects_missing_duplicate_and_extra_tags(
        self,
    ) -> None:
        base = self._envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Current title",
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 2,
                                "tag": "Hopeful",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Mood",
                                },
                            },
                        }
                    ],
                }
            ],
        )
        tag = cast(
            list[dict[str, object]],
            cast(dict[str, object], base["data"])["tags"],
        )[0]
        variants = []
        missing = json.loads(json.dumps(base))
        missing["data"]["tags"] = []
        variants.append(missing)
        duplicate = json.loads(json.dumps(base))
        duplicate["data"]["tags"].append(tag)
        variants.append(duplicate)
        extra = json.loads(json.dumps(base))
        extra["data"]["tags"].append(
            {
                "id": 99,
                "tag": "Unreferenced",
                "tag_category": {"id": 3, "category": "Mood"},
            }
        )
        variants.append(extra)
        for payload in variants:
            with self.subTest(tags=len(payload["data"]["tags"])):
                observation = HardcoverMetadataProvider(
                    "catalogue",
                    httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request, value=payload: httpx.Response(
                                200,
                                headers={"content-type": "application/json"},
                                json=value,
                            )
                        )
                    ),
                    65_536,
                ).observe(_isbn_lookup((self._claim(),)))
                self.assertEqual(ProviderStatus.MALFORMED, observation.status)
                self.assertEqual((), observation.candidates)

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
            return httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope([], []))

        observation = HardcoverMetadataProvider(
            "catalogue", httpx.Client(transport=httpx.MockTransport(success)), 65_536
        ).observe(_isbn_lookup((print_only, second, self._claim())))
        self.assertEqual(observation.status, ProviderStatus.NO_MATCH)
        self.assertEqual(
            observed_bodies[0]["variables"],
            {"isbns": ["0306406152", "9780306406157"]},
        )
        self.assertEqual(
            observation.input_references,
            tuple(sorted((second.reference, self._claim().reference))),
        )

    def test_hardcover_executes_every_family_and_work_lane_in_one_aggregate(self) -> None:
        title = LocalClaim.create(
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
        second = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="Print ISBN 9780140328721",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                ("Ada Example",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("Current title")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields),
            (title, self._claim(), second),
        )
        operations: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            operations.append(json.loads(request.content)["operationName"])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=self._envelope([], []),
            )

        observation = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        ).observe(plan)
        self.assertEqual(
            ["MetadataEvidenceByIsbn", "MetadataEvidenceByIsbn", "MetadataEvidenceByWork"],
            sorted(operations),
        )
        self.assertEqual(3, len(observation.lanes))
        self.assertEqual(
            {ProviderStatus.NO_MATCH},
            {lane.status for lane in observation.lanes},
        )

    def test_hardcover_oversized_lane_stops_remaining_requests_at_exact_bound(
        self,
    ) -> None:
        claims = (
            self._claim(),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780140328721",
                canonical="9780140328721",
                excerpt="Print ISBN 9780140328721",
                applicability="print",
                checksum="valid_isbn_13",
                applicability_basis="print",
            ),
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), claims
        )
        payload = json.dumps(
            self._envelope([], []),
            separators=(",", ":"),
        ).encode()

        for oversized in (False, True):
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=payload + (b" " if oversized else b""),
                )

            observation = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(transport=httpx.MockTransport(handler)),
                len(payload),
            ).observe(plan)
            with self.subTest(oversized=oversized):
                self.assertEqual(1 if oversized else 2, len(requests))
                self.assertEqual(
                    (
                        {ProviderStatus.OVERSIZED}
                        if oversized
                        else {ProviderStatus.NO_MATCH}
                    ),
                    {lane.status for lane in observation.lanes},
                )

    def test_hardcover_conflicting_lane_contributes_no_cover_descriptors(
        self,
    ) -> None:
        claims = (
            self._claim(),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780140328721",
                canonical="9780140328721",
                excerpt="Print ISBN 9780140328721",
                applicability="print",
                checksum="valid_isbn_13",
                applicability_basis="print",
            ),
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), claims
        )
        shared = {
            "id": 1,
            "book_id": 10,
            "isbn_10": "0306406152",
            "isbn_13": "9780140328721",
            "image": {"url": "https://images.example/accepted.jpg"},
        }
        accepted = self._envelope(
            [shared],
            [
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                }
            ],
        )
        conflicting = self._envelope(
            [
                {
                    "id": 2,
                    "book_id": 20,
                    "isbn_10": "0306406152",
                    "isbn_13": "9780140328721",
                    "image": {"url": "https://images.example/orphan.jpg"},
                },
                {
                    **shared,
                    "image": {"url": "https://images.example/conflict.jpg"},
                },
            ],
            [
                {
                    "id": 20,
                    "title": "Other title",
                    "image": {"url": "https://images.example/orphan-work.jpg"},
                },
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                },
            ],
        )
        responses = iter((accepted, conflicting))
        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=next(responses),
                    )
                )
            ),
            65_536,
        )

        observation = provider.observe(plan)

        self.assertEqual(
            {ProviderStatus.CANDIDATES, ProviderStatus.MALFORMED},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(
            {
                cover_source_id(
                    CoverSourceKind.HARDCOVER, "catalogue:edition:1"
                ),
                cover_source_id(
                    CoverSourceKind.HARDCOVER, "catalogue:work:10:default"
                ),
            },
            {descriptor.source_id for descriptor in provider.cover_descriptors},
        )

    def test_hardcover_converges_differing_isbn_applicability_and_shared_cover(
        self,
    ) -> None:
        title = self._title_claim()
        ebook = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="eBook ISBN 0306406152",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
        )
        print_only = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="Print ISBN 9780140328721",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        fields = tuple(
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Example",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("Current title")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields),
            (title, ebook, print_only),
        )
        payload = self._envelope(
            [
                {
                    "id": 1,
                    "book_id": 10,
                    "isbn_10": "0306406152",
                    "isbn_13": "9780140328721",
                    "image": {"url": "https://images.example/edition.jpg"},
                }
            ],
            [
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {"id": 1, "name": "Ada Example"},
                        }
                    ],
                }
            ],
        )
        provider = HardcoverMetadataProvider(
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
        )
        observation = provider.observe(plan)

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual(
            {ProviderStatus.CANDIDATES},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(
            tuple(sorted(lane.identity for lane in plan.lanes)),
            observation.convergence[0].lane_identities,
        )
        lanes = {lane.matched_family: lane for lane in observation.lanes}
        self.assertEqual(
            (ebook.reference,),
            lanes["9780306406157"].input_references,
        )
        self.assertEqual(
            (print_only.reference,),
            lanes["9780140328721"].input_references,
        )
        self.assertEqual((title.reference,), lanes[None].input_references)
        self.assertEqual(
            3,
            len(
                {
                    candidate.reference
                    for lane in observation.lanes
                    for candidate in lane.candidates
                }
            ),
        )

        isbn_claims = tuple(
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.ISBN and claim.value is not None
        )
        self.assertEqual(
            {
                "9780306406157": {"ebook"},
                "9780140328721": {"print"},
            },
            {
                family: {
                    claim.applicability
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertEqual(
            {
                "9780306406157": {ebook.reference},
                "9780140328721": {print_only.reference},
            },
            {
                family: {
                    link.local_reference
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                    for link in claim.links
                    if link.outcome is Comparison.AGREEMENT
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertTrue(
            all(
                {ebook.reference, print_only.reference}
                == {link.local_reference for link in claim.links}
                for claim in isbn_claims
            )
        )
        self.assertEqual(2, len(provider.cover_descriptors))
        self.assertEqual(
            2, len({value.source_id for value in provider.cover_descriptors})
        )
        edition_cover = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.edition_identity is not None
        )
        self.assertEqual(
            CoverDesignation.EXACT_EDITION,
            edition_cover.provenance.designation,
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
                "catalogue",
                httpx.Client(transport=httpx.MockTransport(handler)),
                65_536,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(status=status_code):
                self.assertEqual(provider.observe(_isbn_lookup((self._claim(),))).status, expected)
                self.assertEqual(provider.observe(_isbn_lookup((self._claim(),))).status, ProviderStatus.UNAVAILABLE)
                self.assertEqual(
                    calls,
                    [1, 1, 1] if status_code == 503 else [1],
                )

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
            observation = HardcoverMetadataProvider("catalogue", client, 65_536).observe(_isbn_lookup((self._claim(),)))
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
        observation = HardcoverMetadataProvider("catalogue", sparse, 65_536).observe(_isbn_lookup((self._claim(),)))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertTrue(observation.candidates[0].claims)

        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=self._envelope(rows, [{"id": index, "title": None} for index in range(1, 7)]))))
        observation = HardcoverMetadataProvider("catalogue", overflow, 65_536).observe(_isbn_lookup((self._claim(),)))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        self.assertEqual(observation.candidates, ())

    def test_same_candidate_from_two_provider_connections_has_globally_unique_refs(self) -> None:
        payload = self._envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}], [{"id": 1, "title": "Current title"}])

        def observation(
            name: str,
        ) -> tuple[HardcoverMetadataProvider, ProviderObservation]:
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            )
            provider = HardcoverMetadataProvider(name, client, 65_536)
            result = provider.observe(
                _isbn_lookup((self._claim(), self._title_claim()))
            )
            return provider, result

        first_provider, first = observation("catalogue")
        second_provider, second = observation("secondary")
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
        self.assertTrue(
            {value.source_id for value in first_provider.cover_descriptors}.isdisjoint(
                {value.source_id for value in second_provider.cover_descriptors}
            )
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
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
                ).observe(_isbn_lookup((self._claim(),)))
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

    def test_complete_target_projection_preserves_all_destination_states(self) -> None:
        metadata = {
            "title": "Current title",
            "subtitle": None,
            "authors": [{"name": "Ada Example"}, {"name": "Bea Example"}],
            "narrators": [],
            "series": [{"name": "Clockwork Archive", "sequence": "2.5"}],
            "genres": ["Fantasy"],
            "publishedYear": 2024,
            "publishedDate": "2024-03-04",
            "description": "",
            "isbn": "9780306406157",
            "asin": "B012345678",
            "language": "en",
            "explicit": False,
            "tags": ["Measured"],
        }
        payload = {
            "id": "item-1",
            "libraryId": "library-id",
            "isInvalid": False,
            "isMissing": False,
            "mediaType": "book",
            "coverPath": "/metadata/items/item-1/cover.jpg",
            "media": {
                "metadata": metadata,
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

        snapshot = reader.read("item-1")
        by_destination = {field.destination: field for field in snapshot.fields}
        self.assertEqual(
            tuple(field.destination for field in snapshot.fields),
            tuple(TargetDestination),
        )
        self.assertEqual(
            by_destination[TargetDestination.AUTHORS].value,
            ("Ada Example", "Bea Example"),
        )
        self.assertEqual(
            by_destination[TargetDestination.SUBTITLE].state,
            TargetFieldState.NULL,
        )
        self.assertEqual(
            by_destination[TargetDestination.NARRATORS].state,
            TargetFieldState.EMPTY,
        )
        self.assertEqual(
            by_destination[TargetDestination.PUBLISHER].state,
            TargetFieldState.MISSING,
        )
        self.assertEqual(
            by_destination[TargetDestination.DESCRIPTION].state,
            TargetFieldState.EMPTY,
        )
        self.assertEqual(
            by_destination[TargetDestination.EXPLICIT].value,
            False,
        )
        self.assertEqual(
            by_destination[TargetDestination.COVER].value,
            snapshot.cover.identity,
        )
        self.assertNotIn(
            "/metadata/",
            json.dumps([field.to_data() for field in snapshot.fields]),
        )

        payload["media"].pop("metadata")
        unloaded = reader.read("item-1")
        self.assertTrue(
            all(
                field.state is TargetFieldState.UNLOADED
                for field in unloaded.fields[:-1]
            )
        )

    def test_target_text_bounds_preserve_oversized_and_malformed_states(self) -> None:
        exact = _target_snapshot(
            {
                "title": "Current title",
                "subtitle": "s" * MAX_SCALAR_CHARACTERS,
                "authors": [{"name": "a" * MAX_SCALAR_CHARACTERS}],
                "series": [{"name": "r" * MAX_SCALAR_CHARACTERS}],
                "description": "d" * MAX_DESCRIPTION_CHARACTERS,
            }
        )
        exact_states = {
            field.destination: field.state for field in exact.fields
        }
        for destination in (
            TargetDestination.SUBTITLE,
            TargetDestination.AUTHORS,
            TargetDestination.SERIES,
            TargetDestination.DESCRIPTION,
        ):
            with self.subTest(boundary="exact", destination=destination):
                self.assertIs(
                    TargetFieldState.VALUE, exact_states[destination]
                )

        oversized = _target_snapshot(
            {
                "title": "Current title",
                "subtitle": "s" * (MAX_SCALAR_CHARACTERS + 1),
                "authors": [
                    {"name": "a" * (MAX_SCALAR_CHARACTERS + 1)}
                ],
                "series": [
                    {"name": "r" * (MAX_SCALAR_CHARACTERS + 1)}
                ],
                "publisher": "Publisher",
                "description": "d" * (MAX_DESCRIPTION_CHARACTERS + 1),
            }
        )
        oversized_destinations = {
            TargetDestination.SUBTITLE,
            TargetDestination.AUTHORS,
            TargetDestination.SERIES,
            TargetDestination.DESCRIPTION,
        }
        self.assertEqual(
            oversized_destinations,
            {
                field.destination
                for field in oversized.fields
                if field.state is TargetFieldState.OVERSIZED
            },
        )
        aggregate = TargetFacts(
            "primary", "ebooks", "item-1", oversized.fields
        )
        serialized_fields = cast(
            list[dict[str, object]], aggregate.to_data()["fields"]
        )
        self.assertEqual(
            {
                entry["destination"]
                for entry in serialized_fields
                if entry["state"] == TargetFieldState.OVERSIZED.value
            },
            {destination.value for destination in oversized_destinations},
        )
        self.assertIs(
            TargetFieldState.VALUE,
            next(
                field.state
                for field in oversized.fields
                if field.destination is TargetDestination.PUBLISHER
            ),
        )

        for unsafe_prefix in (
            "/Users/private/",
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        ):
            unsafe_scalar = unsafe_prefix + "x" * (
                MAX_SCALAR_CHARACTERS - len(unsafe_prefix) + 1
            )
            unsafe_description = unsafe_prefix + "x" * (
                MAX_DESCRIPTION_CHARACTERS - len(unsafe_prefix) + 1
            )
            malformed = _target_snapshot(
                {
                    "title": "Current title",
                    "subtitle": unsafe_scalar,
                    "authors": [{"name": unsafe_scalar}],
                    "series": [{"name": unsafe_scalar}],
                    "description": unsafe_description,
                }
            )
            malformed_states = {
                field.destination: field.state for field in malformed.fields
            }
            for destination in oversized_destinations:
                with self.subTest(
                    boundary="unsafe",
                    prefix=unsafe_prefix[:13],
                    destination=destination,
                ):
                    self.assertIs(
                        TargetFieldState.MALFORMED,
                        malformed_states[destination],
                    )

    def test_typed_target_syntax_precedes_oversized_classification(self) -> None:
        snapshot = _target_snapshot(
            {
                "title": "Current title",
                "isbn": "x" * (MAX_SCALAR_CHARACTERS + 1),
                "asin": "x" * (MAX_SCALAR_CHARACTERS + 1),
                "publishedDate": "x" * (MAX_SCALAR_CHARACTERS + 1),
                "publishedYear": "2" * (MAX_SCALAR_CHARACTERS + 1),
            }
        )
        states = {
            field.destination: field.state for field in snapshot.fields
        }
        for destination in (
            TargetDestination.ISBN,
            TargetDestination.ASIN,
            TargetDestination.PUBLISHED_DATE,
            TargetDestination.PUBLISHED_YEAR,
        ):
            with self.subTest(destination=destination):
                self.assertIs(
                    TargetFieldState.MALFORMED, states[destination]
                )

    def test_collection_safety_precedes_cardinality_oversized_state(self) -> None:
        unsafe = "/Users/private/" + "x" * MAX_SCALAR_CHARACTERS
        list_cases = (
            (
                TargetDestination.AUTHORS,
                "authors",
                MAX_CONTRIBUTIONS,
                lambda value: {"name": value},
            ),
            (
                TargetDestination.NARRATORS,
                "narrators",
                MAX_CONTRIBUTIONS,
                lambda value: value,
            ),
            (
                TargetDestination.GENRES,
                "genres",
                MAX_TAGS,
                lambda value: value,
            ),
            (
                TargetDestination.TAGS,
                "tags",
                MAX_TAGS,
                lambda value: value,
            ),
        )
        for destination, key, maximum, item in list_cases:
            safe_values = [
                item(f"Value {index}") for index in range(maximum + 1)
            ]
            unsafe_values = [
                item(f"Value {index}") for index in range(maximum)
            ] + [item(unsafe)]
            malformed_values = [
                item(f"Value {index}") for index in range(maximum)
            ] + [{"unexpected": "value"}]
            with self.subTest(destination=destination, case="safe"):
                safe_state = {
                    field.destination: field.state
                    for field in _target_snapshot(
                        {"title": "Current title", key: safe_values}
                    ).fields
                }
                self.assertIs(
                    TargetFieldState.OVERSIZED, safe_state[destination]
                )
            for case, values in (
                ("unsafe", unsafe_values),
                ("malformed", malformed_values),
            ):
                with self.subTest(destination=destination, case=case):
                    states = {
                        field.destination: field.state
                        for field in _target_snapshot(
                            {"title": "Current title", key: values}
                        ).fields
                    }
                    self.assertIs(
                        TargetFieldState.MALFORMED,
                        states[destination],
                    )

        safe_series = [
            {"name": f"Series {index}"}
            for index in range(MAX_SERIES + 1)
        ]
        unsafe_series = safe_series[:MAX_SERIES] + [{"name": unsafe}]
        malformed_series = safe_series[:MAX_SERIES] + ["not-a-series"]
        for case, values, expected in (
            ("safe", safe_series, TargetFieldState.OVERSIZED),
            ("unsafe", unsafe_series, TargetFieldState.MALFORMED),
            ("malformed", malformed_series, TargetFieldState.MALFORMED),
        ):
            with self.subTest(destination="series", case=case):
                states = {
                    field.destination: field.state
                    for field in _target_snapshot(
                        {"title": "Current title", "series": values}
                    ).fields
                }
                self.assertIs(expected, states[TargetDestination.SERIES])

        for case, sequence, expected in (
            (
                "safe_oversized_sequence",
                "s" * (MAX_SCALAR_CHARACTERS + 1),
                TargetFieldState.OVERSIZED,
            ),
            (
                "unsafe_sequence",
                unsafe,
                TargetFieldState.MALFORMED,
            ),
        ):
            with self.subTest(destination="series_sequence", case=case):
                states = {
                    field.destination: field.state
                    for field in _target_snapshot(
                        {
                            "title": "Current title",
                            "series": [
                                {"name": "Series", "sequence": sequence}
                            ],
                        }
                    ).fields
                }
                self.assertIs(expected, states[TargetDestination.SERIES])

        safe_title = _target_snapshot(
            {"title": "t" * (MAX_SCALAR_CHARACTERS + 1)}
        )
        self.assertIs(
            TargetFieldState.OVERSIZED,
            next(
                field.state
                for field in safe_title.fields
                if field.destination is TargetDestination.TITLE
            ),
        )

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
            "Authorization: Bearer abcd1234"
            + "x" * MAX_SCALAR_CHARACTERS,
            "/Users/private/library/book.epub",
            "/Users/private/library/book.epub"
            + "x" * MAX_SCALAR_CHARACTERS,
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
