from __future__ import annotations

import json
import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.bootstrap import composition

from mechanical_librarian.adapters.persistence.epub_opening_evidence import (
    SQLiteEpubOpeningEvidenceRepository,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.application.epub_opening_evidence import (
    EpubOpeningExecution,
    RunEpubOpeningEvidence,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceFailure,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    PUBLISHABLE_EPUB_OUTCOMES,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    ExternalItemId,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import FileReference, ItemLifecycle, MediaType
from mechanical_librarian.domain.inventory_v2 import (
    FieldObservation,
    InventoryFileBinding,
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanUsage,
    opaque_snapshot_reference,
    opaque_target_reference,
)
from mechanical_librarian.domain.local_evidence import LocalEvidenceOutcome
from mechanical_librarian.infrastructure.local_media import LocalMediaError
from mechanical_librarian.infrastructure.persistence import sqlite as sqlite_foundation
from mechanical_librarian.infrastructure.persistence.sqlite import (
    MIGRATIONS,
    SchemaError,
    connect,
    migrate,
    preflight,
)


class SQLiteEpubOpeningEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "state.sqlite3"
        self.connection = connect(self.database)
        self.assertEqual(migrate(self.connection), 8)
        target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("item"),
        )
        observation = InventoryObservation.create(
            target=target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(ObservationField.TITLE, ObservationState.PRESENT, ("Synthetic",)),
            ),
            file_identities=("1" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.binding_configuration = "2" * 64
        self.snapshot = InventorySnapshotV2.create(
            observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "1" * 64,
                    "epub",
                    self.binding_configuration,
                    FileReference("books", "book.epub"),
                ),
            ),
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        inventory._persist_or_reuse(self.snapshot)
        self.connection.execute(
            "INSERT INTO current_inventory_v2(adapter_id,connection_id,library_id,"
            "external_item,snapshot_id) VALUES (?,?,?,?,?)",
            (
                target.adapter.value,
                target.connection.value,
                target.library.value,
                target.external_item.value,
                self.snapshot.id.value,
            ),
        )
        inventory._set_current_binding_set(self.snapshot, self.snapshot.id)
        self.locator = EpubEvidenceLocator(
            opaque_target_reference(target),
            opaque_snapshot_reference(self.snapshot),
            0,
        )
        self.repository = SQLiteEpubOpeningEvidenceRepository(self.connection)
        self.now = datetime(2026, 2, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_restart_reuses_same_evidence_and_source_change_supersedes(self) -> None:
        selectors = SQLiteInventoryV2Repository(
            self.connection,
            binding_configuration_digests=frozenset({self.binding_configuration}),
        )._selectors("audiobookshelf", "connection", "library")
        self.assertEqual(
            (
                selectors[0].target_reference,
                selectors[0].snapshot_reference,
                selectors[0].file_index,
                selectors[0].media_kind,
            ),
            (
                self.locator.target_reference,
                self.locator.snapshot_reference,
                0,
                "epub",
            ),
        )
        self.assertEqual(
            SQLiteInventoryV2Repository(
                self.connection,
                binding_configuration_digests=frozenset({"9" * 64}),
            )._selectors("audiobookshelf", "connection", "library"),
            (),
        )
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        self.assertIsNotNone(source)
        accepted = self.repository.accept(source, "3" * 64, "4" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
            EpubOpeningLimits(),
        )
        published = self.repository.publish(
            claim, source, "3" * 64, "3" * 64, "4" * 64, report, self.now
        )
        self.assertEqual(published.mode, EpubEvidenceMode.EXECUTED)
        reopened = SQLiteEpubOpeningEvidenceRepository(self.connection)
        reused = reopened.accept(
            source, "3" * 64, "4" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(reused.mode, EpubEvidenceMode.REUSED)
        self.assertEqual(reused.evidence_id, published.evidence_id)

        changed = reopened.accept(
            source, "5" * 64, "4" * 64, self.now + timedelta(seconds=2)
        )
        changed_claim = reopened.claim(
            changed[0], "worker", self.now + timedelta(seconds=2), timedelta(seconds=30)
        )
        replacement = reopened.publish(
            changed_claim,
            source,
            "5" * 64, "5" * 64,
            "4" * 64,
            report,
            self.now + timedelta(seconds=2),
        )
        self.assertNotEqual(replacement.evidence_id, published.evidence_id)
        row = self.connection.execute(
            "SELECT evidence_id,sequence FROM current_epub_opening_evidence"
        ).fetchone()
        self.assertEqual(row, (replacement.evidence_id, 2))
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone()[0],
            2,
        )
        reverse = reopened.accept(
            source, "3" * 64, "4" * 64, self.now + timedelta(seconds=3)
        )[1]
        self.assertEqual(reverse.evidence_id, published.evidence_id)
        self.assertFalse(reverse.current_applicable)
        self.assertEqual(
            self.connection.execute(
                "SELECT evidence_id,sequence FROM current_epub_opening_evidence"
            ).fetchone(),
            (replacement.evidence_id, 2),
        )

    def test_closed_publishable_outcomes_are_durable_evidence(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        rejection_outcomes = PUBLISHABLE_EPUB_OUTCOMES - {
            LocalEvidenceOutcome.SUCCESS,
            LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE,
        }
        for index, outcome in enumerate(
            sorted(rejection_outcomes, key=lambda value: value.value), start=1
        ):
            with self.subTest(outcome=outcome):
                governing_digest = f"{index:064x}"
                accepted = self.repository.accept(
                    source, "f" * 64, governing_digest, self.now
                )
                claim = self.repository.claim(
                    accepted[0],
                    f"worker-{index}",
                    self.now,
                    timedelta(seconds=30),
                )
                published = self.repository.publish(
                    claim,
                    source,
                    "f" * 64, "f" * 64,
                    governing_digest,
                    EpubOpeningReport(outcome, EpubOpeningLimits()),
                    self.now,
                )
                self.assertEqual(published.mode, EpubEvidenceMode.EXECUTED)
                self.assertEqual(published.outcome, outcome)
                self.assertIsNotNone(published.evidence_id)

    def test_expired_fence_cannot_publish(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "6" * 64, "7" * 64, self.now)
        stale = self.repository.claim(
            accepted[0], "worker-one", self.now, timedelta(seconds=1)
        )
        replacement = self.repository.claim(
            accepted[0],
            "worker-two",
            self.now + timedelta(seconds=2),
            timedelta(seconds=30),
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
            EpubOpeningLimits(),
        )
        self.assertIsNone(
            self.repository.publish(
                stale,
                source,
                "6" * 64, "6" * 64,
                "7" * 64,
                report,
                self.now + timedelta(seconds=2),
            )
        )
        self.assertIsNotNone(
            self.repository.publish(
                replacement,
                source,
                "6" * 64, "6" * 64,
                "7" * 64,
                report,
                self.now + timedelta(seconds=2),
            )
        )

    def test_lease_expiring_during_revalidation_cannot_publish(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "6" * 64, "8" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=1)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        self.assertIsNone(
            self.repository.publish(
                claim,
                source,
                "6" * 64, "6" * 64,
                "8" * 64,
                report,
                self.now,
                lambda: True,
                lambda: self.now + timedelta(seconds=2),
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )

    def test_lease_expiring_during_finalization_rolls_back_publication(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "6" * 64, "9" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=1)
        )
        finalization_clock = iter(
            (self.now, self.now + timedelta(seconds=2))
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        self.assertIsNone(
            self.repository.publish(
                claim,
                source,
                "6" * 64, "6" * 64,
                "9" * 64,
                report,
                self.now,
                lambda: True,
                lambda: next(finalization_clock),
            )
        )
        self.assertEqual(
            tuple(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )

    def test_run_lease_expiring_during_finalization_cannot_publish(self) -> None:
        clock_values = iter(
            (
                self.now,
                self.now,
                self.now,
                self.now,
                self.now + timedelta(seconds=2),
                self.now + timedelta(seconds=2),
            )
        )
        result = RunEpubOpeningEvidence(self.repository).run(
            self.locator,
            self.binding_configuration,
            "a" * 64,
            "worker",
            lambda: next(clock_values),
            lambda _source: "b" * 64,
            lambda _source: EpubOpeningExecution(
                EpubOpeningReport(
                    LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
                ),
                "b" * 64,
            ),
            lease_duration=timedelta(seconds=1),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.PERSISTENCE_FAILURE)
        self.assertEqual(
            tuple(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs"
            ).fetchone(),
            ("running", None),
        )

    def test_failure_lease_expiry_rolls_back_and_allows_reclaim(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "c" * 64, "d" * 64, self.now)
        stale = self.repository.claim(
            accepted[0], "stale-worker", self.now, timedelta(seconds=1)
        )
        failed = self.repository.fail(
            stale,
            EpubEvidenceFailure.SOURCE_CHANGED,
            self.now,
            retryable=False,
            failure_clock=lambda: self.now + timedelta(seconds=2),
        )
        self.assertEqual(failed.failure, EpubEvidenceFailure.PERSISTENCE_FAILURE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT finished_at,failure FROM epub_evidence_attempts WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            (None, None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT kind FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
                (accepted[0],),
            ).fetchall(),
            [("accepted",), ("claimed",)],
        )
        replacement = self.repository.claim(
            accepted[0],
            "replacement-worker",
            self.now + timedelta(seconds=2),
            timedelta(seconds=30),
        )
        self.assertIsNotNone(replacement)
        self.assertEqual(replacement.attempt, 2)
        self.assertEqual(replacement.fence, 2)

    def test_publication_clock_regression_rolls_back(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "1" * 64, "2" * 64, self.now
        )
        claimed_at = self.now + timedelta(seconds=10)
        claim = self.repository.claim(
            accepted[0], "worker", claimed_at, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        backwards = self.repository.publish(
            claim,
            source,
            "1" * 64, "1" * 64,
            "2" * 64,
            report,
            self.now + timedelta(seconds=5),
        )
        self.assertEqual(
            backwards.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

        clock = iter(
            (
                self.now + timedelta(seconds=12),
                self.now + timedelta(seconds=11),
            )
        )
        self.assertIsNone(
            self.repository.publish(
                claim,
                source,
                "1" * 64, "1" * 64,
                "2" * 64,
                report,
                self.now + timedelta(seconds=11),
                publication_clock=lambda: next(clock),
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            tuple(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT finished_at,outcome FROM epub_evidence_attempts "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            (None, None),
        )

    def test_failure_clock_regression_rolls_back(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "3" * 64, "4" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0],
            "worker",
            self.now + timedelta(seconds=10),
            timedelta(seconds=30),
        )
        backwards = self.repository.fail(
            claim,
            EpubEvidenceFailure.RESOURCE_LIMIT,
            self.now + timedelta(seconds=5),
            retryable=False,
        )
        self.assertEqual(
            backwards.failure, EpubEvidenceFailure.PERSISTENCE_FAILURE
        )
        regressed_final = self.repository.fail(
            claim,
            EpubEvidenceFailure.RESOURCE_LIMIT,
            self.now + timedelta(seconds=11),
            retryable=False,
            failure_clock=lambda: self.now + timedelta(seconds=10),
        )
        self.assertEqual(
            regressed_final.failure, EpubEvidenceFailure.PERSISTENCE_FAILURE
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT finished_at,failure FROM epub_evidence_attempts "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            (None, None),
        )

    def test_reuse_clock_cannot_precede_publication_finalization(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "2" * 64, "3" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        clock = iter(
            (
                self.now + timedelta(seconds=2),
                self.now + timedelta(seconds=3),
            )
        )
        published = self.repository.publish(
            claim,
            source,
            "2" * 64, "2" * 64,
            "3" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now + timedelta(seconds=1),
            publication_clock=lambda: next(clock),
        )
        self.assertTrue(published.succeeded)

        premature = self.repository.accept(
            source,
            "2" * 64,
            "3" * 64,
            self.now + timedelta(seconds=2, microseconds=500_000),
        )[1]
        self.assertEqual(premature.failure, EpubEvidenceFailure.CORRUPT_STATE)
        reused = self.repository.accept(
            source, "2" * 64, "3" * 64, self.now + timedelta(seconds=4)
        )[1]
        self.assertEqual(reused.evidence_id, published.evidence_id)
        self.assertEqual(
            self.connection.execute(
                "SELECT kind FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
                (accepted[0],),
            ).fetchall(),
            [("accepted",), ("claimed",), ("published",), ("reused",)],
        )

    def test_cancellation_clock_regression_rolls_back(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted_at = self.now + timedelta(seconds=10)
        accepted = self.repository.accept(
            source, "5" * 64, "6" * 64, accepted_at
        )
        self.assertFalse(
            self.repository.cancel(
                accepted[0],
                "operator",
                self.now + timedelta(seconds=5),
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,updated_at FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("pending", accepted_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT kind FROM epub_evidence_events WHERE work_id=?",
                (accepted[0],),
            ).fetchall(),
            [("accepted",)],
        )

    def test_retry_claim_clock_regression_rolls_back(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "7" * 64, "8" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0],
            "worker",
            self.now + timedelta(seconds=10),
            timedelta(seconds=30),
        )
        self.repository.fail(
            claim,
            EpubEvidenceFailure.INTERNAL_FAILURE,
            self.now + timedelta(seconds=12),
            retryable=True,
        )
        reclaimed = self.repository.claim(
            accepted[0],
            "replacement",
            self.now + timedelta(seconds=11),
            timedelta(seconds=30),
        )
        self.assertEqual(reclaimed, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt,terminal_failure FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("retry_wait", 1, "internal_failure"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT kind FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
                (accepted[0],),
            ).fetchall(),
            [("accepted",), ("claimed",), ("failed",)],
        )

    def test_older_governing_work_cannot_rewind_current_lane(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        older = self.repository.accept(source, "a" * 64, "b" * 64, self.now)
        older_claim = self.repository.claim(older[0], "older", self.now, timedelta(seconds=30))
        newer = self.repository.accept(source, "a" * 64, "c" * 64, self.now)
        newer_claim = self.repository.claim(newer[0], "newer", self.now, timedelta(seconds=30), 2)
        report = EpubOpeningReport(LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits())
        newest = self.repository.publish(newer_claim, source, "a" * 64, "a" * 64, "c" * 64, report, self.now)
        delayed = self.repository.publish(older_claim, source, "a" * 64, "a" * 64, "b" * 64, report, self.now)
        self.assertTrue(newest.current_applicable)
        self.assertFalse(delayed.current_applicable)
        self.assertEqual(
            self.connection.execute("SELECT evidence_id,lane_revision FROM current_epub_opening_evidence").fetchone(),
            (newest.evidence_id, 2),
        )

    def test_older_work_is_not_current_after_newer_lane_is_accepted(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        older = self.repository.accept(source, "a" * 64, "d" * 64, self.now)
        older_claim = self.repository.claim(
            older[0], "older", self.now, timedelta(seconds=30)
        )
        newer = self.repository.accept(source, "a" * 64, "e" * 64, self.now)
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        delayed = self.repository.publish(
            older_claim, source, "a" * 64, "a" * 64, "d" * 64, report, self.now
        )
        self.assertFalse(delayed.current_applicable)
        self.assertIsNone(
            self.connection.execute(
                "SELECT evidence_id FROM current_epub_opening_evidence"
            ).fetchone()
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,lane_revision FROM epub_evidence_jobs WHERE work_id=?",
                (newer[0],),
            ).fetchone(),
            ("pending", 2),
        )

    def test_current_binding_set_tracks_absence_and_configuration_revision(self) -> None:
        inventory = SQLiteInventoryV2Repository(
            self.connection,
            binding_configuration_digests=frozenset(
                {self.binding_configuration, "a" * 64}
            ),
        )
        self.assertEqual(len(inventory._selectors("audiobookshelf", "connection", "library")), 1)

        without_binding = InventorySnapshotV2.create(self.snapshot.observation)
        inventory._persist_or_reuse(without_binding)
        inventory._set_current_binding_set(without_binding, without_binding.id)
        self.assertEqual(
            inventory._selectors("audiobookshelf", "connection", "library"), ()
        )
        self.assertNotEqual(
            InventoryPass((self.snapshot,), ScanUsage()).digest,
            InventoryPass((without_binding,), ScanUsage()).digest,
        )

        replacement = InventorySnapshotV2.create(
            self.snapshot.observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "1" * 64,
                    "epub",
                    "a" * 64,
                    FileReference("replacement", "book.epub"),
                ),
            ),
        )
        inventory._persist_or_reuse(replacement)
        inventory._set_current_binding_set(replacement, replacement.id)
        current = inventory._current_values(
            "audiobookshelf", "connection", "library"
        )
        self.assertEqual(current[0].file_bindings, replacement.file_bindings)
        self.assertEqual(len(inventory._selectors("audiobookshelf", "connection", "library")), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM inventory_snapshot_file_bindings"
            ).fetchone(),
            (2,),
        )

    def test_evidence_resolution_uses_only_the_current_binding_set(self) -> None:
        original = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        inventory = SQLiteInventoryV2Repository(
            self.connection,
            binding_configuration_digests=frozenset(
                {self.binding_configuration, "a" * 64}
            ),
        )

        without_binding = InventorySnapshotV2.create(self.snapshot.observation)
        inventory._persist_or_reuse(without_binding)
        inventory._set_current_binding_set(without_binding, without_binding.id)
        self.assertEqual(
            self.repository.resolve(
                self.locator, self.binding_configuration
            ).failure,
            EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE,
        )
        self.assertIsNone(
            self.repository.accept(original, "a" * 64, "b" * 64, self.now)
        )

        replacement = InventorySnapshotV2.create(
            self.snapshot.observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "1" * 64,
                    "epub",
                    "a" * 64,
                    FileReference("replacement", "book.epub"),
                ),
            ),
        )
        inventory._persist_or_reuse(replacement)
        inventory._set_current_binding_set(replacement, replacement.id)
        self.assertEqual(
            self.repository.resolve(
                self.locator, self.binding_configuration
            ).failure,
            EpubEvidenceFailure.MOUNT_MISMATCH,
        )
        current = self.repository.resolve(self.locator, "a" * 64).source
        self.assertEqual(current.relative_path, "book.epub")
        self.assertEqual(current.mount_name, "replacement")

    def test_binding_set_change_before_publish_rolls_back_success(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "a" * 64, "b" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        without_binding = InventorySnapshotV2.create(self.snapshot.observation)
        inventory._persist_or_reuse(without_binding)
        inventory._set_current_binding_set(without_binding, without_binding.id)

        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        self.assertIsNone(
            self.repository.publish(
                claim, source, "a" * 64, "a" * 64, "b" * 64, report, self.now
            )
        )
        self.assertEqual(
            tuple(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_evidence_events "
                "WHERE work_id=? AND kind='published'",
                (accepted[0],),
            ).fetchone(),
            (0,),
        )

    def test_restored_trigger_binding_set_substitution_fails_closed(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "a" * 64, "b" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        replacement = InventorySnapshotV2.create(
            self.snapshot.observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "1" * 64,
                    "epub",
                    "a" * 64,
                    FileReference("replacement", "book.epub"),
                ),
            ),
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        inventory._persist_or_reuse(replacement)
        current_set = self.connection.execute(
            "SELECT binding_set_digest FROM current_inventory_snapshot_binding_sets"
        ).fetchone()[0]
        replacement_binding = self.connection.execute(
            "SELECT binding_digest FROM inventory_snapshot_file_bindings "
            "WHERE mount_configuration_digest=?",
            ("a" * 64,),
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER inventory_snapshot_binding_set_members_no_update"
        )
        self.connection.execute(
            "UPDATE inventory_snapshot_binding_set_members SET binding_digest=? "
            "WHERE binding_set_digest=? AND file_index=0",
            (replacement_binding, current_set),
        )
        trigger = next(
            statement
            for statement in MIGRATIONS[7].statements
            if statement.startswith(
                "CREATE TRIGGER inventory_snapshot_binding_set_members_no_update"
            )
        )
        self.connection.execute(trigger)
        self.connection.close()
        preflight(self.database)
        self.connection = connect(self.database)
        self.repository = SQLiteEpubOpeningEvidenceRepository(self.connection)

        self.assertEqual(
            self.repository.resolve(
                self.locator, self.binding_configuration
            ).failure,
            EpubEvidenceFailure.CORRUPT_STATE,
        )
        with self.assertRaisesRegex(ValueError, "binding set digest"):
            SQLiteInventoryV2Repository(self.connection)._current_values(
                "audiobookshelf", "connection", "library"
            )
        accepted_corrupt = self.repository.accept(
            source, "c" * 64, "d" * 64, self.now
        )
        self.assertEqual(
            accepted_corrupt[1].failure, EpubEvidenceFailure.CORRUPT_STATE
        )
        result = self.repository.publish(
            claim,
            source,
            "a" * 64, "a" * 64,
            "b" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            tuple(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )

    def test_newer_acceptance_retires_old_current_even_if_new_work_fails(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        older = self.repository.accept(source, "e" * 64, "f" * 64, self.now)
        older_claim = self.repository.claim(
            older[0], "older", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        published = self.repository.publish(
            older_claim, source, "e" * 64, "e" * 64, "f" * 64, report, self.now
        )
        self.assertTrue(published.current_applicable)

        newer = self.repository.accept(
            source, "e" * 64, "0" * 64, self.now + timedelta(seconds=1)
        )
        self.assertIsNone(
            self.connection.execute(
                "SELECT evidence_id FROM current_epub_opening_evidence"
            ).fetchone()
        )
        newer_claim = self.repository.claim(
            newer[0],
            "newer",
            self.now + timedelta(seconds=1),
            timedelta(seconds=30),
        )
        failed = self.repository.fail(
            newer_claim,
            EpubEvidenceFailure.INTERNAL_FAILURE,
            self.now + timedelta(seconds=1),
            retryable=False,
        )
        self.assertEqual(failed.failure, EpubEvidenceFailure.INTERNAL_FAILURE)
        reused = self.repository.accept(
            source, "e" * 64, "f" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(reused.evidence_id, published.evidence_id)
        self.assertFalse(reused.current_applicable)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (1,),
        )

    def test_acceptance_failure_rolls_back_current_retirement(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        older = self.repository.accept(source, "1" * 64, "2" * 64, self.now)
        older_claim = self.repository.claim(
            older[0], "older", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        published = self.repository.publish(
            older_claim, source, "1" * 64, "1" * 64, "2" * 64, report, self.now
        )
        self.connection.execute(
            "CREATE TRIGGER injected_epub_acceptance_failure BEFORE INSERT ON "
            "epub_evidence_events WHEN NEW.kind='accepted' "
            "BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        self.assertIsNone(
            self.repository.accept(
                source, "1" * 64, "3" * 64, self.now + timedelta(seconds=1)
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT evidence_id,lane_revision FROM current_epub_opening_evidence"
            ).fetchone(),
            (published.evidence_id, 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_evidence_jobs"
            ).fetchone(),
            (1,),
        )

    def test_final_lease_expiry_terminalizes_and_capacity_is_enforced(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "d" * 64, "e" * 64, self.now)
        claim = self.repository.claim(accepted[0], "worker-1", self.now, timedelta(seconds=1))
        for attempt in (2, 3):
            claim = self.repository.claim(
                accepted[0], f"worker-{attempt}", self.now + timedelta(seconds=attempt), timedelta(seconds=1)
            )
            self.assertEqual(claim.attempt, attempt)
        self.assertEqual(
            self.repository.claim(
                accepted[0],
                "exhausted",
                self.now + timedelta(seconds=4),
                timedelta(seconds=1),
            ),
            EpubEvidenceFailure.RETRY_EXHAUSTED,
        )
        self.assertEqual(
            self.connection.execute("SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?", (accepted[0],)).fetchone(),
            ("failed_terminal", "retry_exhausted"),
        )
        replayed = self.repository.accept(
            source, "d" * 64, "e" * 64, self.now + timedelta(seconds=5)
        )[1]
        self.assertEqual(
            replayed.failure, EpubEvidenceFailure.RETRY_EXHAUSTED
        )
        first = self.repository.accept(source, "1" * 64, "2" * 64, self.now)
        second = self.repository.accept(source, "2" * 64, "2" * 64, self.now)
        self.assertIsNotNone(self.repository.claim(first[0], "capacity-1", self.now, timedelta(seconds=30), 1))
        self.assertIsNone(self.repository.claim(second[0], "capacity-2", self.now, timedelta(seconds=30), 1))

    def test_terminal_replay_preserves_failure_and_cancellation(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        failed = self.repository.accept(
            source, "a" * 64, "b" * 64, self.now
        )
        claim = self.repository.claim(
            failed[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.fail(
            claim,
            EpubEvidenceFailure.RESOURCE_LIMIT,
            self.now,
            retryable=False,
        )
        replayed_failure = self.repository.accept(
            source, "a" * 64, "b" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            replayed_failure.failure, EpubEvidenceFailure.RESOURCE_LIMIT
        )

        cancelled = self.repository.accept(
            source, "c" * 64, "d" * 64, self.now
        )
        self.assertTrue(
            self.repository.cancel(cancelled[0], "operator", self.now)
        )
        replayed_cancellation = self.repository.accept(
            source, "c" * 64, "d" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            replayed_cancellation.failure, EpubEvidenceFailure.CANCELLED
        )

        worker_cancelled = self.repository.accept(
            source, "e" * 64, "f" * 64, self.now
        )
        worker_claim = self.repository.claim(
            worker_cancelled[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.fail(
            worker_claim,
            EpubEvidenceFailure.CANCELLED,
            self.now,
            retryable=False,
        )
        replayed_worker_cancellation = self.repository.accept(
            source, "e" * 64, "f" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            replayed_worker_cancellation.failure,
            EpubEvidenceFailure.CANCELLED,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,cancellation_requested FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (worker_cancelled[0],),
            ).fetchone(),
            ("cancelled", 1),
        )

    def test_cancellation_records_every_allowed_origin(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        expected = {}

        pending = self.repository.accept(
            source, "1" * 64, "a" * 64, self.now
        )
        self.assertTrue(self.repository.cancel(pending[0], "operator", self.now))
        expected[pending[0]] = ("pending", 0)

        running = self.repository.accept(
            source, "2" * 64, "a" * 64, self.now
        )
        self.repository.claim(
            running[0], "worker", self.now, timedelta(seconds=30)
        )
        self.assertTrue(self.repository.cancel(running[0], "operator", self.now))
        expected[running[0]] = ("running", 1)

        waiting = self.repository.accept(
            source, "3" * 64, "a" * 64, self.now
        )
        waiting_claim = self.repository.claim(
            waiting[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.fail(
            waiting_claim,
            EpubEvidenceFailure.INTERNAL_FAILURE,
            self.now,
            retryable=True,
        )
        self.assertTrue(self.repository.cancel(waiting[0], "operator", self.now))
        expected[waiting[0]] = ("retry_wait", 1)

        for work_id, (origin, attempt) in expected.items():
            self.assertEqual(
                self.connection.execute(
                    "SELECT state,terminal_failure,attempt FROM epub_evidence_jobs "
                    "WHERE work_id=?",
                    (work_id,),
                ).fetchone(),
                ("cancelled", "cancelled", attempt),
            )
            self.assertEqual(
                self.connection.execute(
                    "SELECT from_state,to_state FROM epub_evidence_events "
                    "WHERE work_id=? AND kind='cancelled'",
                    (work_id,),
                ).fetchone(),
                (origin, "cancelled"),
            )

    def test_resume_reports_final_lease_exhaustion(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "d" * 64, "e" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker-1", self.now, timedelta(seconds=1)
        )
        self.assertEqual(claim.attempt, 1)
        for attempt in (2, 3):
            claim = self.repository.claim(
                accepted[0],
                f"worker-{attempt}",
                self.now + timedelta(seconds=attempt * 2 - 2),
                timedelta(seconds=1),
            )
            self.assertEqual(claim.attempt, attempt)

        resumed = RunEpubOpeningEvidence(self.repository).resume(
            frozenset({self.binding_configuration}),
            "e" * 64,
            "recovery-worker",
            lambda: self.now + timedelta(seconds=6),
            lambda _source: "d" * 64,
            lambda _source: self.fail("exhausted work executed"),
            lambda _source, _expected: True,
        )

        self.assertEqual(resumed.failure, EpubEvidenceFailure.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure,attempt FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("failed_terminal", "retry_exhausted", 3),
        )
        self.assertEqual(
            tuple(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )

    def test_terminal_replay_rejects_premature_retry_exhaustion(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "6" * 64, "7" * 64, self.now
        )
        self.repository.claim(
            accepted[0], "first-worker", self.now, timedelta(seconds=1)
        )
        blocker = self.repository.accept(
            source, "8" * 64, "7" * 64, self.now
        )
        self.repository.claim(
            blocker[0], "blocking-worker", self.now, timedelta(seconds=30), 2
        )
        exhausted_at = self.now + timedelta(seconds=2)
        self.assertIsNone(
            self.repository.claim(
                accepted[0],
                "premature-worker",
                exhausted_at,
                timedelta(seconds=30),
                1,
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt,max_attempts,terminal_failure "
                "FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("retry_wait", 1, 3, "lease_expired"),
        )

        self.connection.execute(
            "UPDATE epub_evidence_jobs SET state='failed_terminal',"
            "terminal_failure='retry_exhausted' WHERE work_id=?",
            (accepted[0],),
        )
        self.repository._event(
            accepted[0],
            "retry_exhausted",
            exhausted_at,
            actor="premature-worker",
            attempt=1,
            fence=1,
            from_state="retry_wait",
            to_state="failed_terminal",
        )
        replayed = self.repository.accept(
            source, "6" * 64, "7" * 64, self.now + timedelta(seconds=3)
        )[1]
        self.assertEqual(replayed.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_terminal_replay_rejects_reserved_attempt_exhaustion(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "9" * 64, "0" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        finished_at = self.now + timedelta(seconds=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE epub_evidence_attempts SET finished_at=?,"
                "failure='retry_exhausted' WHERE work_id=? AND attempt=?",
                (
                    finished_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    accepted[0],
                    claim.attempt,
                ),
            )

        attempt_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_attempts_finalize_only'"
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER epub_evidence_attempts_finalize_only"
        )
        self.connection.execute("PRAGMA ignore_check_constraints=ON")
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET finished_at=?,"
            "failure='retry_exhausted' WHERE work_id=? AND attempt=?",
            (
                finished_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                accepted[0],
                claim.attempt,
            ),
        )
        self.connection.execute("PRAGMA ignore_check_constraints=OFF")
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET state='failed_terminal',"
            "lease_owner=NULL,lease_expires_at=NULL,"
            "terminal_failure='retry_exhausted',updated_at=? WHERE work_id=?",
            (
                finished_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                accepted[0],
            ),
        )
        self.repository._event(
            accepted[0],
            "failed",
            finished_at,
            actor=claim.worker_id,
            attempt=claim.attempt,
            fence=claim.fence,
            from_state="running",
            to_state="retry_wait",
        )
        self.repository._event(
            accepted[0],
            "retry_exhausted",
            finished_at,
            actor="replacement",
            attempt=claim.attempt,
            fence=claim.fence,
            from_state="retry_wait",
            to_state="failed_terminal",
        )
        self.connection.execute(str(attempt_trigger))

        replayed = self.repository.accept(
            source, "9" * 64, "0" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(replayed.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_retry_wait_replay_rejects_reserved_attempt_exhaustion(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "1" * 64, "0" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        finished_at = self.now + timedelta(seconds=1)
        self.repository.fail(
            claim,
            EpubEvidenceFailure.INTERNAL_FAILURE,
            finished_at,
            retryable=True,
        )
        attempt_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_attempts_finalize_only'"
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER epub_evidence_attempts_finalize_only"
        )
        self.connection.execute("PRAGMA ignore_check_constraints=ON")
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET failure='retry_exhausted' "
            "WHERE work_id=? AND attempt=?",
            (accepted[0], claim.attempt),
        )
        self.connection.execute("PRAGMA ignore_check_constraints=OFF")
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET terminal_failure='retry_exhausted' "
            "WHERE work_id=?",
            (accepted[0],),
        )
        self.connection.execute(str(attempt_trigger))

        replayed = self.repository.accept(
            source, "1" * 64, "0" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(replayed.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_succeeded_replay_rejects_reserved_prior_attempt_exhaustion(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "2" * 64, "0" * 64, self.now
        )
        self.repository.claim(
            accepted[0], "first-worker", self.now, timedelta(seconds=1)
        )
        second_claim = self.repository.claim(
            accepted[0],
            "second-worker",
            self.now + timedelta(seconds=2),
            timedelta(seconds=30),
        )
        self.repository.publish(
            second_claim,
            source,
            "2" * 64, "2" * 64,
            "0" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now + timedelta(seconds=3),
        )
        attempt_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_attempts_finalize_only'"
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER epub_evidence_attempts_finalize_only"
        )
        self.connection.execute("PRAGMA ignore_check_constraints=ON")
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET failure='retry_exhausted' "
            "WHERE work_id=? AND attempt=1",
            (accepted[0],),
        )
        self.connection.execute("PRAGMA ignore_check_constraints=OFF")
        self.connection.execute(str(attempt_trigger))

        replayed = self.repository.accept(
            source, "2" * 64, "0" * 64, self.now + timedelta(seconds=4)
        )[1]
        self.assertEqual(replayed.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_recovery_cancellation_and_transactional_source_revalidation(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "3" * 64, "4" * 64, self.now)
        recoverable = self.repository.next_recoverable(
            frozenset({self.binding_configuration}), "4" * 64, self.now
        )
        self.assertEqual(recoverable.locator, self.locator)
        self.assertEqual(recoverable.work_id, accepted[0])
        claim = self.repository.claim(accepted[0], "worker", self.now, timedelta(seconds=30))
        report = EpubOpeningReport(LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits())
        rejected = self.repository.publish(
            claim, source, "3" * 64, "3" * 64, "4" * 64, report, self.now, lambda: False
        )
        self.assertEqual(rejected.failure, EpubEvidenceFailure.SOURCE_CHANGED)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM epub_opening_evidence").fetchone(), (0,))
        self.assertTrue(self.repository.cancel(accepted[0], "operator", self.now))
        self.assertIsNone(self.repository.publish(claim, source, "3" * 64, "3" * 64, "4" * 64, report, self.now))
        self.assertEqual(
            self.connection.execute("SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?", (accepted[0],)).fetchone(),
            ("cancelled", "cancelled"),
        )

    def test_recovery_terminalizes_changed_original_work_and_failure_frees_capacity(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "3" * 64, "4" * 64, self.now)
        resumed = RunEpubOpeningEvidence(self.repository).resume(
            frozenset({self.binding_configuration}),
            "4" * 64,
            "recovery-worker",
            lambda: self.now,
            lambda _source: "5" * 64,
            lambda _source: self.fail("changed work executed"),
            lambda _source, _expected: True,
        )
        self.assertEqual(resumed.failure, EpubEvidenceFailure.SOURCE_CHANGED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("failed_terminal", "source_changed"),
        )
        self.assertIsNone(
            self.repository.next_recoverable(
                frozenset({self.binding_configuration}), "4" * 64, self.now
            )
        )

        runner = RunEpubOpeningEvidence(self.repository)
        failed = runner.run(
            self.locator,
            self.binding_configuration,
            "6" * 64,
            "first-worker",
            lambda: self.now,
            lambda _source: "7" * 64,
            lambda _source: EpubOpeningExecution(
                EpubOpeningReport(
                    LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
                ),
                "7" * 64,
            ),
            lambda _source, _expected: False,
            capacity=1,
        )
        self.assertEqual(failed.failure, EpubEvidenceFailure.SOURCE_CHANGED)
        sibling = self.repository.accept(source, "8" * 64, "9" * 64, self.now)
        self.assertIsNotNone(
            self.repository.claim(
                sibling[0], "sibling", self.now, timedelta(seconds=30), 1
            )
        )

    def test_recovery_lease_expiring_during_finalization_cannot_publish(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "3" * 64, "4" * 64, self.now)
        clock_values = iter(
            (
                self.now,
                self.now,
                self.now,
                self.now,
                self.now + timedelta(seconds=2),
                self.now + timedelta(seconds=2),
            )
        )
        result = RunEpubOpeningEvidence(self.repository).resume(
            frozenset({self.binding_configuration}),
            "4" * 64,
            "recovery-worker",
            lambda: next(clock_values),
            lambda _source: "3" * 64,
            lambda _source: EpubOpeningExecution(
                EpubOpeningReport(
                    LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
                ),
                "3" * 64,
            ),
            lambda _source, _expected: True,
            lease_duration=timedelta(seconds=1),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.PERSISTENCE_FAILURE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            tuple(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "epub_opening_evidence",
                    "current_epub_opening_evidence",
                    "epub_evidence_checkpoints",
                )
            ),
            (0, 0, 0),
        )

    def test_recovery_selects_one_oldest_job_across_multiple_mounts(self) -> None:
        second_configuration = "a" * 64
        second_target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("second-item"),
        )
        second_observation = InventoryObservation.create(
            target=second_target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(
                    ObservationField.TITLE,
                    ObservationState.PRESENT,
                    ("Second synthetic",),
                ),
            ),
            file_identities=("a" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        second_snapshot = InventorySnapshotV2.create(
            second_observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "a" * 64,
                    "epub",
                    second_configuration,
                    FileReference("alternate", "book.epub"),
                ),
            ),
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        inventory._persist_or_reuse(second_snapshot)
        self.connection.execute(
            "INSERT INTO current_inventory_v2(adapter_id,connection_id,library_id,"
            "external_item,snapshot_id) VALUES (?,?,?,?,?)",
            (
                second_target.adapter.value,
                second_target.connection.value,
                second_target.library.value,
                second_target.external_item.value,
                second_snapshot.id.value,
            ),
        )
        inventory._set_current_binding_set(second_snapshot, second_snapshot.id)
        second_locator = EpubEvidenceLocator(
            opaque_target_reference(second_target),
            opaque_snapshot_reference(second_snapshot),
            0,
        )
        first_source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        second_source = self.repository.resolve(
            second_locator, second_configuration
        ).source
        first = self.repository.accept(first_source, "b" * 64, "c" * 64, self.now)
        second = self.repository.accept(second_source, "d" * 64, "c" * 64, self.now)
        recoverable = self.repository.next_recoverable(
            frozenset({self.binding_configuration, second_configuration}),
            "c" * 64,
            self.now,
        )
        self.assertEqual(recoverable.work_id, first[0])
        self.assertTrue(self.repository.cancel(first[0], "operator", self.now))
        self.assertEqual(
            self.repository.next_recoverable(
                frozenset({self.binding_configuration, second_configuration}),
                "c" * 64,
                self.now,
            ).work_id,
            second[0],
        )

    def test_lock_read_only_disk_full_and_backup_restore_are_fail_closed(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        database = Path(self.temporary.name) / "state.sqlite3"
        contender = connect(database)
        try:
            contender.execute("PRAGMA busy_timeout=0")
            contender_source = SQLiteEpubOpeningEvidenceRepository(contender).resolve(
                self.locator, self.binding_configuration
            ).source
            self.connection.execute("BEGIN IMMEDIATE")
            self.assertIsNone(
                SQLiteEpubOpeningEvidenceRepository(contender).accept(
                    contender_source, "a" * 64, "b" * 64, self.now
                )
            )
            self.connection.rollback()
        finally:
            contender.close()
        self.connection.execute("PRAGMA query_only=ON")
        self.assertIsNone(
            self.repository.accept(source, "b" * 64, "c" * 64, self.now)
        )
        self.connection.execute("PRAGMA query_only=OFF")
        self.connection.execute(
            "CREATE TRIGGER injected_epub_disk_full BEFORE INSERT ON epub_evidence_jobs "
            "BEGIN SELECT RAISE(ABORT,'database or disk is full'); END"
        )
        self.assertIsNone(
            self.repository.accept(source, "c" * 64, "d" * 64, self.now)
        )
        self.connection.execute("DROP TRIGGER injected_epub_disk_full")
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM epub_evidence_jobs").fetchone(),
            (0,),
        )

        accepted = self.repository.accept(source, "d" * 64, "e" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "backup-worker", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        published = self.repository.publish(
            claim, source, "d" * 64, "d" * 64, "e" * 64, report, self.now
        )
        restored_path = Path(self.temporary.name) / "restored.sqlite3"
        restored_raw = sqlite3.connect(restored_path)
        self.connection.backup(restored_raw)
        restored_raw.close()
        restored = connect(restored_path)
        try:
            self.assertEqual(migrate(restored), 8)
            with patch.object(sqlite_foundation, "MIGRATIONS", MIGRATIONS[:7]):
                with self.assertRaises(SchemaError):
                    migrate(restored)
            reused = SQLiteEpubOpeningEvidenceRepository(restored).accept(
                SQLiteEpubOpeningEvidenceRepository(restored).resolve(
                    self.locator, self.binding_configuration
                ).source,
                "d" * 64,
                "e" * 64,
                self.now,
            )[1]
            self.assertEqual(reused.evidence_id, published.evidence_id)
            self.assertEqual(reused.mode, EpubEvidenceMode.REUSED)
        finally:
            restored.close()

    def test_selector_failures_are_typed_before_enqueue(self) -> None:
        self.assertEqual(
            self.repository.resolve(EpubEvidenceLocator(self.locator.target_reference, self.locator.snapshot_reference, 1), self.binding_configuration).failure,
            EpubEvidenceFailure.FILE_INDEX_OUT_OF_RANGE,
        )
        self.assertEqual(
            self.repository.resolve(self.locator, "f" * 64).failure,
            EpubEvidenceFailure.MOUNT_MISMATCH,
        )
        wrong = EpubEvidenceLocator("target_" + "f" * 32, self.locator.snapshot_reference, 0)
        self.assertEqual(self.repository.resolve(wrong, self.binding_configuration).failure, EpubEvidenceFailure.WRONG_TARGET)
        unbound_target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("unbound-item"),
        )
        unbound_observation = InventoryObservation.create(
            target=unbound_target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(
                    ObservationField.TITLE, ObservationState.PRESENT, ("Unbound",)
                ),
            ),
            file_identities=("8" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=self.now,
        )
        unbound_snapshot = InventorySnapshotV2.create(unbound_observation)
        SQLiteInventoryV2Repository(self.connection)._persist_or_reuse(unbound_snapshot)
        wrong_unbound = EpubEvidenceLocator(
            self.locator.target_reference, opaque_snapshot_reference(unbound_snapshot), 0
        )
        self.assertEqual(
            self.repository.resolve(wrong_unbound, self.binding_configuration).failure,
            EpubEvidenceFailure.WRONG_TARGET,
        )
        unavailable = RunEpubOpeningEvidence(self.repository).run(
            self.locator,
            self.binding_configuration,
            "3" * 64,
            "worker",
            lambda: self.now,
            lambda _source: (_ for _ in ()).throw(
                LocalMediaError("synthetic unavailable local source")
            ),
            lambda _source: EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
        )
        self.assertEqual(
            unavailable.failure, EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE
        )
        current_unbound_target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("connection"),
            LibraryId("library"),
            ExternalItemId("item"),
        )
        current_unbound_observation = InventoryObservation.create(
            target=current_unbound_target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(
                    ObservationField.TITLE,
                    ObservationState.PRESENT,
                    ("Current unbound",),
                ),
            ),
            file_identities=("7" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=self.now,
        )
        current_unbound_snapshot = InventorySnapshotV2.create(
            current_unbound_observation
        )
        current_unbound_inventory = SQLiteInventoryV2Repository(self.connection)
        current_unbound_inventory._persist_or_reuse(current_unbound_snapshot)
        self.connection.execute(
            "UPDATE current_inventory_v2 SET snapshot_id=? WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=?",
            (
                current_unbound_snapshot.id.value,
                current_unbound_target.adapter.value,
                current_unbound_target.connection.value,
                current_unbound_target.library.value,
                current_unbound_target.external_item.value,
            ),
        )
        current_unbound_inventory._set_current_binding_set(
            current_unbound_snapshot, current_unbound_snapshot.id
        )
        current_unbound_locator = EpubEvidenceLocator(
            self.locator.target_reference,
            opaque_snapshot_reference(current_unbound_snapshot),
            0,
        )
        self.assertEqual(
            self.repository.resolve(
                current_unbound_locator, self.binding_configuration
            ).failure,
            EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM epub_evidence_jobs").fetchone(),
            (0,),
        )
        audio_target = TargetId(
            AdapterId("audiobookshelf"), ConnectionId("connection"), LibraryId("library"), ExternalItemId("audio-item")
        )
        audio_observation = InventoryObservation.create(
            target=audio_target,
            media_type=MediaType.AUDIOBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(FieldObservation(ObservationField.TITLE, ObservationState.PRESENT, ("Audio",)),),
            file_identities=("9" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=self.now,
        )
        audio_snapshot = InventorySnapshotV2.create(audio_observation)
        audio_inventory = SQLiteInventoryV2Repository(self.connection)
        audio_inventory._persist_or_reuse(audio_snapshot)
        self.connection.execute(
            "INSERT INTO current_inventory_v2(adapter_id,connection_id,library_id,external_item,snapshot_id) VALUES (?,?,?,?,?)",
            (
                audio_target.adapter.value,
                audio_target.connection.value,
                audio_target.library.value,
                audio_target.external_item.value,
                audio_snapshot.id.value,
            ),
        )
        audio_inventory._set_current_binding_set(audio_snapshot, audio_snapshot.id)
        audio_locator = EpubEvidenceLocator(
            opaque_target_reference(audio_target), opaque_snapshot_reference(audio_snapshot), 0
        )
        self.assertEqual(
            self.repository.resolve(audio_locator, self.binding_configuration).failure,
            EpubEvidenceFailure.NON_EPUB,
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM epub_evidence_jobs").fetchone(),
            (0,),
        )

    def test_unavailable_snapshot_locator_resolves_as_stale_without_enqueue(self) -> None:
        observation = InventoryObservation.create(
            target=self.snapshot.observation.target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.UNAVAILABLE,
            fields=self.snapshot.observation.fields,
            file_identities=self.snapshot.observation.file_identities,
            cover_identity=None,
            adapter_version=self.snapshot.observation.adapter_version,
            normalizer_version=self.snapshot.observation.normalizer_version,
            service_version=self.snapshot.observation.service_version,
            warnings=(),
            observed_at=self.now,
        )
        unavailable = InventorySnapshotV2.create(
            observation,
            file_bindings=self.snapshot.file_bindings,
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        persisted_id = inventory._persist_or_reuse(unavailable)
        target = observation.target
        self.connection.execute(
            "DELETE FROM current_inventory_snapshot_binding_sets WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=?",
            (
                target.adapter.value,
                target.connection.value,
                target.library.value,
                target.external_item.value,
            ),
        )
        self.connection.execute(
            "UPDATE current_inventory_v2 SET snapshot_id=? WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=?",
            (
                persisted_id.value,
                target.adapter.value,
                target.connection.value,
                target.library.value,
                target.external_item.value,
            ),
        )
        inventory._set_current_binding_set(unavailable, persisted_id)
        locator = EpubEvidenceLocator(
            opaque_target_reference(target),
            opaque_snapshot_reference(unavailable),
            0,
        )
        self.assertEqual(
            self.repository.resolve(locator, self.binding_configuration).failure,
            EpubEvidenceFailure.STALE_SNAPSHOT,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT lifecycle FROM inventory_snapshot_locator_index "
                "WHERE snapshot_reference=?",
                (locator.snapshot_reference,),
            ).fetchone(),
            ("unavailable",),
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM epub_evidence_jobs").fetchone(),
            (0,),
        )

    def test_composition_maps_present_oversized_source_before_enqueue(self) -> None:
        root = Path(self.temporary.name)
        mount = root / "media"
        mount.mkdir()
        (mount / "oversized.epub").write_bytes(b"x" * 1_048_577)
        config = root / "configuration.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": "config-v1",
                    "instance_name": "epub-evidence-limit-test",
                    "connections": [
                        {
                            "adapter": "audiobookshelf",
                            "id": "connection",
                            "endpoint": "https://library.example",
                            "secret_reference": "unused-secret",
                            "enabled_libraries": [
                                "01234567-89ab-4def-8123-456789abcdef"
                            ],
                        }
                    ],
                    "media_mounts": [
                        {
                            "name": "books",
                            "library_type": "ebook",
                            "container_path": str(mount),
                            "service_path": "/library/books",
                            "read_only": True,
                        }
                    ],
                    "evidence": {
                        "maximum_document_bytes": 1_048_576,
                        "epub_package": {"source_archive_bytes": 1_048_576},
                    },
                    "providers": [],
                    "reviewers": [],
                    "notifications": [],
                    "backups_enabled": True,
                }
            ),
            encoding="utf-8",
        )
        loaded = composition.load_configuration_file(config)
        binding_configuration = composition._inventory_mount_mappings(loaded)[
            0
        ].configuration_digest
        observation = InventoryObservation.create(
            target=self.snapshot.observation.target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=self.snapshot.observation.fields,
            file_identities=("f" * 64,),
            cover_identity=None,
            adapter_version=self.snapshot.observation.adapter_version,
            normalizer_version=self.snapshot.observation.normalizer_version,
            service_version=self.snapshot.observation.service_version,
            warnings=(),
            observed_at=self.now,
        )
        oversized = InventorySnapshotV2.create(
            observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    "f" * 64,
                    "epub",
                    binding_configuration,
                    FileReference("books", "oversized.epub"),
                ),
            ),
        )
        inventory = SQLiteInventoryV2Repository(self.connection)
        persisted_id = inventory._persist_or_reuse(oversized)
        target = observation.target
        identity = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        self.connection.execute(
            "DELETE FROM current_inventory_snapshot_binding_sets WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=?",
            identity,
        )
        self.connection.execute(
            "UPDATE current_inventory_v2 SET snapshot_id=? WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=?",
            (persisted_id.value, *identity),
        )
        inventory._set_current_binding_set(oversized, persisted_id)
        locator = EpubEvidenceLocator(
            opaque_target_reference(target),
            opaque_snapshot_reference(oversized),
            0,
        )
        result = composition._run_epub_opening_evidence(
            config,
            self.database,
            locator.target_reference,
            locator.snapshot_reference,
            locator.file_index,
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.RESOURCE_LIMIT)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM epub_evidence_jobs").fetchone(),
            (0,),
        )

    def test_publication_boundary_rolls_back_and_immutable_rows_reject_tamper(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "8" * 64, "9" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
            EpubOpeningLimits(),
        )
        self.connection.execute(
            "CREATE TRIGGER injected_epub_publication_failure BEFORE INSERT ON "
            "current_epub_opening_evidence BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        self.assertIsNone(
            self.repository.publish(
                claim, source, "8" * 64, "8" * 64, "9" * 64, report, self.now
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM epub_evidence_jobs WHERE work_id=?",
                (claim.work_id,),
            ).fetchone(),
            ("running",),
        )
        self.connection.execute("DROP TRIGGER injected_epub_publication_failure")
        published = self.repository.publish(
            claim, source, "8" * 64, "8" * 64, "9" * 64, report, self.now
        )
        self.assertIsNotNone(published)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE epub_opening_evidence SET outcome='malformed'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE inventory_snapshot_file_bindings SET media_kind='epub'"
            )

    def test_repository_requires_exact_execution_provenance_before_publication(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "8" * 64, "9" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        for label, execution_source in (
            ("missing", None),
            ("mismatched", "7" * 64),
        ):
            with self.subTest(partition=label):
                self.assertIsNone(
                    self.repository.publish(
                        claim,
                        source,
                        "8" * 64,
                        execution_source,
                        "9" * 64,
                        report,
                        self.now,
                    )
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT state,evidence_id FROM epub_evidence_jobs "
                        "WHERE work_id=?",
                        (claim.work_id,),
                    ).fetchone(),
                    ("running", None),
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM epub_opening_evidence"
                    ).fetchone(),
                    (0,),
                )
        published = self.repository.publish(
            claim,
            source,
            "8" * 64,
            "8" * 64,
            "9" * 64,
            report,
            self.now,
        )
        self.assertTrue(published.succeeded)

    def test_a_to_b_to_a_execution_provenance_never_publishes_evidence(self) -> None:
        fingerprints = iter(("8" * 64, "8" * 64))
        result = RunEpubOpeningEvidence(self.repository).run(
            self.locator,
            self.binding_configuration,
            "9" * 64,
            "worker",
            lambda: self.now,
            lambda _source: next(fingerprints),
            lambda _source: EpubOpeningExecution(
                EpubOpeningReport(
                    LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
                    EpubOpeningLimits(),
                ),
                "7" * 64,
            ),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.SOURCE_CHANGED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure FROM epub_evidence_jobs"
            ).fetchone(),
            ("retry_wait", "source_changed"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_reuse_recomputes_immutable_evidence_identity_after_tamper(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "4" * 64, "5" * 64, self.now)
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        self.repository.publish(
            claim, source, "4" * 64, "4" * 64, "5" * 64, report, self.now
        )
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_opening_evidence_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_opening_evidence_no_update")
        replacement = EpubOpeningReport(
            LocalEvidenceOutcome.MALFORMED, EpubOpeningLimits()
        )
        self.connection.execute(
            "UPDATE epub_opening_evidence SET report_json=?",
            (replacement.to_json(),),
        )
        self.connection.execute(str(trigger))
        reused = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(reused.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_reuse_recomputes_succeeded_job_identity_after_tamper(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET governing_digest=? WHERE work_id=?",
            ("6" * 64, accepted[0]),
        )
        reused = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(reused.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_reuse_rejects_succeeded_job_reset_before_extraction(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET state='pending',evidence_id=NULL,"
            "terminal_outcome=NULL WHERE work_id=?",
            (accepted[0],),
        )
        result = RunEpubOpeningEvidence(self.repository).run(
            self.locator,
            self.binding_configuration,
            "5" * 64,
            "replacement-worker",
            lambda: self.now + timedelta(seconds=1),
            lambda _source: "4" * 64,
            lambda _source: self.fail("corrupt succeeded work executed again"),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id,terminal_outcome FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("pending", None, None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT (SELECT COUNT(*) FROM epub_opening_evidence),"
                "(SELECT COUNT(*) FROM epub_evidence_checkpoints)"
            ).fetchone(),
            (1, 1),
        )

    def test_reuse_rejects_missing_or_disagreeing_evidence_link(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        published = self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET evidence_id=? WHERE work_id=?",
            ("evidence_" + "f" * 32, accepted[0]),
        )
        missing = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(missing.failure, EpubEvidenceFailure.CORRUPT_STATE)

        self.connection.execute(
            "UPDATE epub_evidence_jobs SET evidence_id=? WHERE work_id=?",
            (published.evidence_id, accepted[0]),
        )
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_opening_evidence_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_opening_evidence_no_update")
        self.connection.execute(
            "UPDATE epub_opening_evidence SET work_key=? WHERE evidence_id=?",
            ("f" * 64, published.evidence_id),
        )
        self.connection.execute(str(trigger))
        disagreeing = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(disagreeing.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_reuse_rejects_terminal_attempt_or_event_disagreement(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        terminal_attempt = self.connection.execute(
            "SELECT finished_at,outcome,failure FROM epub_evidence_attempts "
            "WHERE work_id=? AND attempt=?",
            (accepted[0], claim.attempt),
        ).fetchone()
        attempt_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_attempts_finalize_only'"
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER epub_evidence_attempts_finalize_only"
        )
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET finished_at=NULL,outcome=NULL,"
            "failure='internal_failure' WHERE work_id=? AND attempt=?",
            (accepted[0], claim.attempt),
        )
        attempt_result = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            attempt_result.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

        self.connection.execute(
            "UPDATE epub_evidence_attempts SET finished_at=?,outcome=?,failure=? "
            "WHERE work_id=? AND attempt=?",
            (*terminal_attempt, accepted[0], claim.attempt),
        )
        self.connection.execute(str(attempt_trigger))
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_events_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_evidence_events_no_update")
        self.connection.execute(
            "UPDATE epub_evidence_events SET actor='other-worker' "
            "WHERE work_id=? AND kind='published'",
            (accepted[0],),
        )
        self.connection.execute(str(trigger))
        event_result = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(event_result.failure, EpubEvidenceFailure.CORRUPT_STATE)

    def test_reuse_rejects_claim_event_or_later_attempt_disagreement(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_events_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_evidence_events_no_update")
        self.connection.execute(
            "UPDATE epub_evidence_events SET actor='replacement-worker' "
            "WHERE work_id=? AND kind='claimed'",
            (accepted[0],),
        )
        self.connection.execute(str(trigger))
        claim_event_result = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            claim_event_result.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

        self.connection.execute("DROP TRIGGER epub_evidence_events_no_update")
        self.connection.execute(
            "UPDATE epub_evidence_events SET actor='worker' "
            "WHERE work_id=? AND kind='claimed'",
            (accepted[0],),
        )
        self.connection.execute(str(trigger))
        self.connection.execute(
            "INSERT INTO epub_evidence_attempts(work_id,attempt,fence,worker_id,"
            "started_at) VALUES (?,?,?,?,?)",
            (
                accepted[0],
                claim.attempt + 1,
                claim.fence + 1,
                "later-worker",
                (self.now + timedelta(seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
            ),
        )
        later_attempt_result = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=2)
        )[1]
        self.assertEqual(
            later_attempt_result.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

    def test_terminal_history_is_immutable_and_validated_on_replay(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        failed = self.repository.accept(
            source, "7" * 64, "8" * 64, self.now
        )
        claim = self.repository.claim(
            failed[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.fail(
            claim,
            EpubEvidenceFailure.RESOURCE_LIMIT,
            self.now,
            retryable=False,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE epub_evidence_attempts SET worker_id='replacement' "
                "WHERE work_id=? AND attempt=?",
                (failed[0], claim.attempt),
            )

        attempt_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_attempts_finalize_only'"
        ).fetchone()[0]
        self.connection.execute(
            "DROP TRIGGER epub_evidence_attempts_finalize_only"
        )
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET worker_id='replacement' "
            "WHERE work_id=? AND attempt=?",
            (failed[0], claim.attempt),
        )
        self.connection.execute(str(attempt_trigger))
        corrupted_failure = self.repository.accept(
            source, "7" * 64, "8" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            corrupted_failure.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

        cancelled = self.repository.accept(
            source, "9" * 64, "a" * 64, self.now
        )
        self.assertTrue(
            self.repository.cancel(cancelled[0], "operator", self.now)
        )
        event_trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_events_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_evidence_events_no_update")
        self.connection.execute(
            "UPDATE epub_evidence_events SET actor='replacement' "
            "WHERE work_id=? AND kind='cancelled'",
            (cancelled[0],),
        )
        self.connection.execute(str(event_trigger))
        corrupted_cancellation = self.repository.accept(
            source, "9" * 64, "a" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(
            corrupted_cancellation.failure, EpubEvidenceFailure.CORRUPT_STATE
        )

    def test_retry_rejects_inconsistent_failed_event_before_reclaim(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.repository.fail(
            claim,
            EpubEvidenceFailure.INTERNAL_FAILURE,
            self.now,
            retryable=True,
        )
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_events_no_update'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_evidence_events_no_update")
        self.connection.execute(
            "UPDATE epub_evidence_events SET actor='replacement-worker' "
            "WHERE work_id=? AND kind='failed'",
            (accepted[0],),
        )
        self.connection.execute(str(trigger))
        retried = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=1)
        )[1]
        self.assertEqual(retried.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.repository.claim(
                accepted[0],
                "replacement-worker",
                self.now + timedelta(seconds=1),
                timedelta(seconds=30),
            ),
            EpubEvidenceFailure.CORRUPT_STATE,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("retry_wait", 1),
        )

    def test_run_rejects_inconsistent_lease_owner_before_extraction(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        self.repository.claim(
            accepted[0], "first-worker", self.now, timedelta(seconds=30)
        )
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET lease_owner='replacement-worker' "
            "WHERE work_id=?",
            (accepted[0],),
        )
        result = RunEpubOpeningEvidence(self.repository).run(
            self.locator,
            self.binding_configuration,
            "5" * 64,
            "next-worker",
            lambda: self.now + timedelta(seconds=31),
            lambda _source: "4" * 64,
            lambda _source: self.fail("inconsistent lease work executed"),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt,lease_owner FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", 1, "replacement-worker"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_resume_reports_inconsistent_lease_owner_as_corrupt(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        self.repository.claim(
            accepted[0], "first-worker", self.now, timedelta(seconds=30)
        )
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET lease_owner='replacement-worker' "
            "WHERE work_id=?",
            (accepted[0],),
        )
        result = RunEpubOpeningEvidence(self.repository).resume(
            frozenset({self.binding_configuration}),
            "5" * 64,
            "recovery-worker",
            lambda: self.now + timedelta(seconds=31),
            lambda _source: "4" * 64,
            lambda _source: self.fail("inconsistent lease recovery executed"),
            lambda _source, _expected: True,
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt,lease_owner FROM epub_evidence_jobs "
                "WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", 1, "replacement-worker"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_publish_rejects_missing_claim_event_before_writing_evidence(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        trigger = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='epub_evidence_events_no_delete'"
        ).fetchone()[0]
        self.connection.execute("DROP TRIGGER epub_evidence_events_no_delete")
        self.connection.execute(
            "DELETE FROM epub_evidence_events WHERE work_id=? AND kind='claimed'",
            (accepted[0],),
        )
        self.connection.execute(str(trigger))
        published = self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.assertEqual(published.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,evidence_id FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("running", None),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_publish_rejects_precompleted_attempt_before_writing_evidence(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        self.connection.execute(
            "UPDATE epub_evidence_attempts SET finished_at=?,failure='internal_failure' "
            "WHERE work_id=? AND attempt=?",
            (
                self.now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                accepted[0],
                claim.attempt,
            ),
        )
        published = self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        self.assertEqual(published.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_reuse_validates_complete_retry_history(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        first_claim = self.repository.claim(
            accepted[0], "first-worker", self.now, timedelta(seconds=1)
        )
        second_claim = self.repository.claim(
            accepted[0],
            "second-worker",
            self.now + timedelta(seconds=2),
            timedelta(seconds=30),
        )
        self.assertEqual(second_claim.attempt, first_claim.attempt + 1)
        published = self.repository.publish(
            second_claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now + timedelta(seconds=2),
        )
        reused = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now + timedelta(seconds=3)
        )[1]
        self.assertEqual(reused.mode, EpubEvidenceMode.REUSED)
        self.assertEqual(reused.evidence_id, published.evidence_id)
        self.assertEqual(
            self.connection.execute(
                "SELECT kind,actor,attempt,fence,from_state,to_state "
                "FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
                (accepted[0],),
            ).fetchall(),
            [
                ("accepted", "local_operator", 0, 0, "missing", "pending"),
                ("claimed", "first-worker", 1, 1, "pending", "running"),
                ("failed", "first-worker", 1, 1, "running", "retry_wait"),
                ("claimed", "second-worker", 2, 2, "retry_wait", "running"),
                ("published", "second-worker", 2, 2, "running", "succeeded"),
                ("reused", "local_operator", 0, 0, "succeeded", "succeeded"),
            ],
        )

    def test_evidence_work_key_is_unique(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        accepted = self.repository.accept(
            source, "4" * 64, "5" * 64, self.now
        )
        claim = self.repository.claim(
            accepted[0], "worker", self.now, timedelta(seconds=30)
        )
        published = self.repository.publish(
            claim,
            source,
            "4" * 64, "4" * 64,
            "5" * 64,
            EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
            ),
            self.now,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO epub_opening_evidence(evidence_id,evidence_digest,work_key,"
                "target_reference,snapshot_reference,file_index,binding_digest,"
                "source_fingerprint,governing_digest,outcome,report_json,report_digest,"
                "claim_count,citation_count,selected_count,rejected_count,warning_count,"
                "package_mode,parser_mode,created_at) SELECT ?,?,work_key,target_reference,"
                "snapshot_reference,file_index,binding_digest,source_fingerprint,"
                "governing_digest,outcome,report_json,report_digest,claim_count,"
                "citation_count,selected_count,rejected_count,warning_count,package_mode,"
                "parser_mode,created_at FROM epub_opening_evidence WHERE evidence_id=?",
                (
                    "evidence_" + "f" * 32,
                    "f" * 64,
                    published.evidence_id,
                ),
            )

    def test_missing_current_binding_set_pointer_is_corrupt(self) -> None:
        source = self.repository.resolve(
            self.locator, self.binding_configuration
        ).source
        self.connection.execute(
            "DELETE FROM current_inventory_snapshot_binding_sets"
        )
        self.assertEqual(
            self.repository.resolve(
                self.locator, self.binding_configuration
            ).failure,
            EpubEvidenceFailure.CORRUPT_STATE,
        )
        with self.assertRaisesRegex(ValueError, "pointer is unavailable"):
            SQLiteInventoryV2Repository(self.connection)._current_values(
                "audiobookshelf", "connection", "library"
            )
        accepted = self.repository.accept(
            source, "7" * 64, "8" * 64, self.now
        )
        self.assertEqual(accepted[1].failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_evidence_jobs"
            ).fetchone(),
            (0,),
        )

    def test_resume_recomputes_persisted_work_identity_after_tamper(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration).source
        accepted = self.repository.accept(source, "6" * 64, "7" * 64, self.now)
        self.connection.execute(
            "UPDATE epub_evidence_jobs SET work_key=? WHERE work_id=?",
            ("8" * 64, accepted[0]),
        )
        result = RunEpubOpeningEvidence(self.repository).resume(
            frozenset({self.binding_configuration}),
            "7" * 64,
            "recovery-worker",
            lambda: self.now,
            lambda _source: "6" * 64,
            lambda _source: self.fail("corrupt work executed"),
            lambda _source, _expected: True,
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.CORRUPT_STATE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,terminal_failure FROM epub_evidence_jobs WHERE work_id=?",
                (accepted[0],),
            ).fetchone(),
            ("failed_terminal", "corrupt_state"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM epub_opening_evidence"
            ).fetchone(),
            (0,),
        )

    def test_legacy_snapshot_without_binding_has_no_source(self) -> None:
        self.connection.execute("DELETE FROM current_inventory_v2")
        observation = self.snapshot.observation
        legacy = self.snapshot.removed(
            observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc)
        )
        SQLiteInventoryV2Repository(self.connection)._persist_or_reuse(legacy)
        self.connection.execute(
            "INSERT INTO current_inventory_v2(adapter_id,connection_id,library_id,"
            "external_item,snapshot_id) VALUES (?,?,?,?,?)",
            (
                observation.target.adapter.value,
                observation.target.connection.value,
                observation.target.library.value,
                observation.target.external_item.value,
                legacy.id.value,
            ),
        )
        missing = EpubEvidenceLocator(
            opaque_target_reference(observation.target),
            opaque_snapshot_reference(legacy),
            0,
        )
        self.assertIsNone(
            self.repository.resolve(missing, self.binding_configuration).source
        )


if __name__ == "__main__":
    unittest.main()
