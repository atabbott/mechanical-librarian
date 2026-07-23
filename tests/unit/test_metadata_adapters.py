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

from mechanical_librarian.adapters.library_services.audiobookshelf_item import AudiobookshelfItemReader
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.hardcover_metadata import HARDCOVER_URL, HardcoverMetadataProvider
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    Comparison,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
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
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", title),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


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
    def test_clean_database_has_exactly_two_tables_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            first = _report()
            self.assertEqual(repository.publish(first, lambda: True), first.to_json())
            self.assertEqual(repository.publish(first, lambda: True), first.to_json())
            second = _report("Changed title")
            repository.publish(second, lambda: True)
            repository.close()

            connection = sqlite3.connect(path)
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            rows = connection.execute("SELECT report_id FROM metadata_reports ORDER BY report_id").fetchall()
            current = connection.execute("SELECT report_id FROM current_metadata_reports").fetchone()
            connection.close()
            self.assertEqual(tables, [("current_metadata_reports",), ("metadata_reports",)])
            self.assertEqual(len(rows), 2)
            self.assertEqual(current, (second.report_id,))

            reopened = open_repository(path)
            self.assertEqual(reopened.get(first.report_id), first.to_json())
            self.assertEqual(reopened.current("primary", "ebooks", "item-1", "a" * 64), second.to_json())
            reopened.close()

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
            repository.close()

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
            calls: list[str] = []
            with self.assertRaises(MetadataReportError):
                repository.publish(
                    _report(),
                    lambda: calls.append("transactional-currentness") or False,
                )
            self.assertEqual(calls, ["transactional-currentness"])
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
    def _claim(self) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="eBook ISBN: 9780306406157",
            applicability="ebook",
            checksum="valid_isbn_13",
            applicability_basis="ebook",
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
        )

    def test_one_request_retains_all_candidates_in_identity_order(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "data": {
                        "editions": [
                            {"id": 2, "isbn_10": None, "isbn_13": "9780306406157", "book": {"id": 20, "title": "Other title"}},
                            {"id": 1, "isbn_10": None, "isbn_13": "9780306406157", "book": {"id": 10, "title": None}},
                        ]
                    }
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = HardcoverMetadataProvider("catalogue", client, 65_536)
        observation = provider.observe((self._claim(), self._title_claim()))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertEqual([item.identity for item in observation.candidates], ["hardcover-edition-0000000001", "hardcover-edition-0000000002"])
        claims = {claim.kind: claim for claim in observation.candidates[1].claims}
        self.assertEqual(claims[ClaimKind.ISBN].comparison, Comparison.AGREEMENT)
        self.assertEqual(claims[ClaimKind.TITLE].comparison, Comparison.DISAGREEMENT)
        self.assertEqual(observation.input_references, (self._claim().reference,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url), HARDCOVER_URL)
        self.assertIn("limit:5", json.loads(requests[0].content)["query"])
        self.assertEqual(provider.observe((self._claim(), self._title_claim())).status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(len(requests), 1)

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
        payload = {
            "data": {
                "editions": [
                    {
                        "id": 1,
                        "isbn_10": None,
                        "isbn_13": "9780306406157",
                        "book": {"id": 1, "title": "Current title"},
                    }
                ]
            }
        }
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
        self.assertEqual(claims[ClaimKind.TITLE].comparison, Comparison.AGREEMENT)
        self.assertEqual(
            claims[ClaimKind.TITLE].local_references,
            (self._title_claim().reference,),
        )
        self.assertEqual(claims[ClaimKind.ISBN].comparison, Comparison.AGREEMENT)
        self.assertEqual(
            claims[ClaimKind.ISBN].local_references,
            (self._claim().reference,),
        )

    def test_no_usable_input_has_no_egress_and_malformed_is_all_or_nothing(self) -> None:
        calls: list[int] = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(1) or httpx.Response(200)))
        provider = HardcoverMetadataProvider("catalogue", client, 65_536)
        self.assertEqual(provider.observe(()).status, ProviderStatus.INSUFFICIENT_INPUT)
        self.assertEqual(calls, [])

        malformed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [{"id": 1, "isbn_10": None, "isbn_13": "9780306406157", "book": {"id": 1, "title": "Fine"}}, {"id": "bad", "isbn_10": None, "isbn_13": None, "book": None}]}})))
        observation = HardcoverMetadataProvider("catalogue", malformed, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.MALFORMED)
        self.assertEqual(observation.candidates, ())

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
            return httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": []}})

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
            ([{"id": 1, "isbn_10": None, "isbn_13": None, "book": None}], ProviderStatus.CANDIDATES),
            ([
                {"id": 2, "isbn_10": None, "isbn_13": None, "book": None},
                {"id": 1, "isbn_10": None, "isbn_13": None, "book": None},
            ], ProviderStatus.CANDIDATES),
        ):
            client = httpx.Client(transport=httpx.MockTransport(lambda request, value=rows: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": value}})))
            observation = HardcoverMetadataProvider("catalogue", client, 65_536).observe((self._claim(),))
            with self.subTest(count=len(rows)):
                self.assertEqual(observation.status, expected)
                self.assertEqual(len(observation.candidates), len(rows))
                self.assertEqual(
                    [candidate.identity for candidate in observation.candidates],
                    sorted(candidate.identity for candidate in observation.candidates),
                )

    def test_sparse_candidate_is_valid_but_result_cap_overflow_is_malformed(self) -> None:
        sparse = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [{"id": 1, "isbn_10": None, "isbn_13": None, "book": None}]}})))
        observation = HardcoverMetadataProvider("catalogue", sparse, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertEqual(observation.candidates[0].claims, ())

        rows = [{"id": index, "isbn_10": None, "isbn_13": None, "book": None} for index in range(1, 6)]
        overflow = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": rows}})))
        observation = HardcoverMetadataProvider("catalogue", overflow, 65_536).observe((self._claim(),))
        self.assertEqual(observation.status, ProviderStatus.MALFORMED)
        self.assertEqual(observation.candidates, ())

    def test_same_candidate_from_two_provider_connections_has_globally_unique_refs(self) -> None:
        payload = {
            "data": {
                "editions": [
                    {
                        "id": 1,
                        "isbn_10": None,
                        "isbn_13": "9780306406157",
                        "book": {"id": 1, "title": "Current title"},
                    }
                ]
            }
        }

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
                            "data": {
                                "editions": [
                                    {
                                        "id": 1,
                                        "isbn_10": None,
                                        "isbn_13": "9780306406157",
                                        "book": {"id": 1, "title": title},
                                    }
                                ]
                            }
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
