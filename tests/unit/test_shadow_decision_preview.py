from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.application.decision_preview import preview_shadow_decision
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.shadow_decision import (
    ShadowDecisionSet,
    decide_shadow_packet,
)
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
)
from tests.unit.test_review_case_persistence import _report


def _snapshot(directory: Path) -> dict[str, tuple[bytes, tuple[int, ...]]]:
    result: dict[str, tuple[bytes, tuple[int, ...]]] = {}
    for entry in sorted(directory.iterdir(), key=lambda value: value.name):
        info = entry.lstat()
        data = entry.read_bytes() if entry.is_file() and not entry.is_symlink() else b""
        result[entry.name] = (
            data,
            (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_uid,
                info.st_gid,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ),
        )
    return result


def _database(directory: Path):
    path = directory / "reports.sqlite"
    repository = open_repository(path)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    case_id = admitted["review_case_id"]
    packet = repository.preview_shadow_packet(case_id)
    repository.close()
    return path, case_id, packet


class ShadowDecisionPreviewTests(unittest.TestCase):
    def test_provider_bound_and_provider_independent_projection_are_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, _, packet = _database(Path(temporary))
            self.assertEqual(packet, ShadowReviewPacket.from_source_validated_json(packet.to_json()))

    def test_read_only_repository_success_and_attempted_write_do_not_mutate_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, case_id, packet = _database(directory)
            before = _snapshot(directory)
            repository = open_shadow_preview_repository(path)
            try:
                self.assertEqual(packet, repository.preview_shadow_packet(case_id))
                self.assertEqual(1, repository._connection.execute("PRAGMA query_only").fetchone()[0])
                with self.assertRaises(sqlite3.OperationalError):
                    repository._connection.execute("CREATE TABLE forbidden(value TEXT)")
            finally:
                repository.close()
            self.assertEqual(before, _snapshot(directory))
            self.assertFalse(any(path.with_name(path.name + suffix).exists() for suffix in ("-wal", "-shm", "-journal")))

    def test_missing_malformed_prior_schema_sidecar_and_symlink_fail_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            cases: list[Path] = []
            missing = directory / "missing.sqlite"
            cases.append(missing)
            malformed = directory / "malformed.sqlite"
            malformed.write_bytes(b"not sqlite")
            cases.append(malformed)
            prior = directory / "prior.sqlite"
            connection = sqlite3.connect(prior)
            connection.execute("CREATE TABLE old_schema(value TEXT)")
            connection.commit()
            connection.close()
            cases.append(prior)
            for path in cases:
                with self.subTest(path=path.name):
                    before = _snapshot(directory)
                    with self.assertRaises(MetadataReportError):
                        open_shadow_preview_repository(path)
                    self.assertEqual(before, _snapshot(directory))

            database, _, _ = _database(directory)
            sidecar = Path(f"{database}-journal")
            sidecar.write_bytes(b"synthetic recovery state")
            before = _snapshot(directory)
            with self.assertRaises(MetadataReportError):
                open_shadow_preview_repository(database)
            self.assertEqual(before, _snapshot(directory))

            if getattr(os, "O_NOFOLLOW", 0):
                sidecar.unlink()
                link = directory / "link.sqlite"
                link.symlink_to(database.name)
                before = _snapshot(directory)
                with self.assertRaises(MetadataReportError):
                    open_shadow_preview_repository(link)
                self.assertEqual(before, _snapshot(directory))

    def test_wal_header_without_sidecars_fails_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, _, _ = _database(directory)
            connection = sqlite3.connect(path)
            self.assertEqual(
                ("wal",),
                connection.execute("PRAGMA journal_mode=WAL").fetchone(),
            )
            connection.close()
            self.assertFalse(
                any(
                    path.with_name(path.name + suffix).exists()
                    for suffix in ("-wal", "-shm", "-journal")
                )
            )
            before = _snapshot(directory)
            with self.assertRaises(MetadataReportError):
                open_shadow_preview_repository(path)
            self.assertEqual(before, _snapshot(directory))

    def test_unverifiable_exclusive_lock_fails_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, _, _ = _database(directory)
            writer = sqlite3.connect(path, isolation_level=None)
            try:
                writer.execute("BEGIN EXCLUSIVE")
                before = _snapshot(directory)
                with self.assertRaises(MetadataReportError):
                    open_shadow_preview_repository(path)
                self.assertEqual(before, _snapshot(directory))
            finally:
                writer.execute("ROLLBACK")
                writer.close()

    def test_application_use_case_returns_the_exact_domain_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, case_id, packet = _database(Path(temporary))
            repository = open_shadow_preview_repository(path)
            try:
                self.assertEqual(
                    decide_shadow_packet(packet),
                    preview_shadow_decision(repository, case_id),
                )
            finally:
                repository.close()

    def test_composed_cli_uses_read_only_repository_and_preserves_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, case_id, packet = _database(directory)
            before = _snapshot(directory)
            serialized = composition.preview_shadow_decision_case(path, case_id)
            self.assertEqual(
                decide_shadow_packet(packet),
                ShadowDecisionSet.from_json(serialized, packet),
            )
            self.assertEqual(before, _snapshot(directory))

    def test_invalid_shadow_packet_becomes_a_report_error_on_both_previews(self) -> None:
        """Both preview paths must name `ShadowReviewValueError` at runtime.

        The `except` tuples resolve their names lazily, so a missing import
        surfaces as `NameError` from the handler instead of the intended
        `MetadataReportError`. This regression test exercises the handler.
        """

        def _invalid(review_case_id: str) -> ShadowReviewPacket:
            raise ShadowReviewValueError("shadow-review packet is invalid")

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path, case_id, _ = _database(directory)

            writable = open_repository(path)
            try:
                writable._shadow_packet = _invalid  # type: ignore[method-assign]  # reason: the point of this test is to drive the handler, not the packet reader
                with self.assertRaises(MetadataReportError) as writable_failure:
                    writable.preview_shadow_packet(case_id)
            finally:
                writable.close()
            self.assertIsInstance(
                writable_failure.exception.__cause__, ShadowReviewValueError
            )

            preview = open_shadow_preview_repository(path)
            try:
                preview._reader._shadow_packet = _invalid  # type: ignore[method-assign]  # reason: the point of this test is to drive the handler, not the packet reader
                with self.assertRaises(MetadataReportError) as preview_failure:
                    preview.preview_shadow_packet(case_id)
                self.assertFalse(preview._connection.in_transaction)
            finally:
                preview.close()
            self.assertIsInstance(
                preview_failure.exception.__cause__, ShadowReviewValueError
            )


if __name__ == "__main__":
    unittest.main()
