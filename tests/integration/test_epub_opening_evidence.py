from __future__ import annotations

import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.epub_opening_evidence import (
    SQLiteEpubOpeningEvidenceRepository,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceLocator,
    EpubEvidenceMode,
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
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    opaque_snapshot_reference,
    opaque_target_reference,
)
from mechanical_librarian.domain.local_evidence import LocalEvidenceOutcome
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate


class SQLiteEpubOpeningEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.connection = connect(Path(self.temporary.name) / "state.sqlite3")
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
        source = self.repository.resolve(self.locator, self.binding_configuration)
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
            claim, source, "3" * 64, "4" * 64, report, self.now
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
            "5" * 64,
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

    def test_expired_fence_cannot_publish(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration)
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
                "6" * 64,
                "7" * 64,
                report,
                self.now + timedelta(seconds=2),
            )
        )
        self.assertIsNotNone(
            self.repository.publish(
                replacement,
                source,
                "6" * 64,
                "7" * 64,
                report,
                self.now + timedelta(seconds=2),
            )
        )

    def test_publication_boundary_rolls_back_and_immutable_rows_reject_tamper(self) -> None:
        source = self.repository.resolve(self.locator, self.binding_configuration)
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
                claim, source, "8" * 64, "9" * 64, report, self.now
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
            claim, source, "8" * 64, "9" * 64, report, self.now
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
            self.repository.resolve(missing, self.binding_configuration)
        )


if __name__ == "__main__":
    unittest.main()
