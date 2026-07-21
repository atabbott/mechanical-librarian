from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.epub_opening_evidence import (
    SQLiteEpubOpeningEvidenceRepository,
)
from mechanical_librarian.adapters.persistence.hardcover_identity_work import (
    SQLiteHardcoverIdentityWorkRepository,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import SQLiteInventoryV2Repository
from mechanical_librarian.adapters.persistence.title_proposals import (
    SQLiteTitleProposalRepository,
    TitleProposalPersistenceError,
)
from mechanical_librarian.application.hardcover_safe_executor import hardcover_identity_work_key
from mechanical_librarian.application.live_title_proposals import ProposeLiveTitle
from mechanical_librarian.bootstrap.composition import (
    _mount_configuration_digest,
    _run_epub_opening_report,
    _run_live_title_proposal_report,
)
from mechanical_librarian.domain.epub_opening_evidence import EpubEvidenceLocator
from mechanical_librarian.domain.hardcover_identity import validate_private_query
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkObservation,
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
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate
from mechanical_librarian.infrastructure.persistence.live_title_evidence import (
    SQLiteLiveTitleEvidenceReader,
)
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from tests.support.epub_packages import opening_title_isbn_applicability


class LiveTitleProposalIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "state.sqlite"
        self.connection = connect(self.database)
        migrate(self.connection)
        (self.root / "book.epub").write_bytes(opening_title_isbn_applicability())
        config = json.loads(
            Path("docs/examples/synthetic-config.json").read_text(encoding="utf-8")
        )
        config["media_mounts"] = [
            {
                "name": "books",
                "library_type": "ebook",
                "service_path": "/books",
                "container_path": str(self.root),
                "read_only": True,
            }
        ]
        config["hardcover_provider"] = {
            "enabled": True,
            "secret_reference": "/run/secrets/hardcover-token",
            "authority_revision": 1,
        }
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.mount_digest = _mount_configuration_digest(
            load_configuration_file(self.config_path).media_mounts[0]
        )
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
                FieldObservation(ObservationField.TITLE, ObservationState.EMPTY),
            ),
            file_identities=(hashlib.sha256(b"file").hexdigest(),),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        self.snapshot = InventorySnapshotV2.create(
            observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    hashlib.sha256(b"file").hexdigest(),
                    "epub",
                    self.mount_digest,
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
            opaque_target_reference(target), opaque_snapshot_reference(self.snapshot), 0
        )
        report = _run_epub_opening_report(self.config_path, "books", "book.epub")
        evidence = SQLiteEpubOpeningEvidenceRepository(self.connection)
        source = evidence.resolve(self.locator, self.mount_digest).source
        accepted = evidence.accept(
            source,
            report.source_fingerprint.digest,
            hashlib.sha256(b"governing").hexdigest(),
            datetime(2026, 7, 20, 1, tzinfo=timezone.utc),
        )
        claim = evidence.claim(
            accepted[0],
            "worker",
            datetime(2026, 7, 20, 1, tzinfo=timezone.utc),
            timedelta(seconds=30),
        )
        evidence.publish(
            claim,
            source,
            report.source_fingerprint.digest,
            report.source_fingerprint.digest,
            hashlib.sha256(b"governing").hexdigest(),
            report,
            datetime(2026, 7, 20, 1, tzinfo=timezone.utc),
        )
        self.query = validate_private_query(
            {"isbn": "9780306406157", "title": "The Applicable Clock"}
        )
        self.work_key = hardcover_identity_work_key(self.query, 1)
        hardcover = SQLiteHardcoverIdentityWorkRepository(self.connection)
        self.now = datetime.now(timezone.utc)
        start = self.now - timedelta(minutes=1)
        accepted_work = hardcover.accept(self.work_key, start)
        identity_claim = hardcover.claim(
            accepted_work.reference, "worker", start, gate_owned=True
        )
        started = hardcover.renew_and_start(
            identity_claim, "worker", start, gate_owned=True
        )
        hardcover.publish_matched(
            started,
            "worker",
            start,
            IdentityWorkObservation(
                IdentityMatchOutcome.MATCHED,
                1,
                1001,
                2001,
                "The Applicable Clock",
            ),
            gate_owned=True,
        )

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def repository(self, **kwargs) -> SQLiteTitleProposalRepository:
        reader = SQLiteLiveTitleEvidenceReader(
            self.connection,
            SQLiteEpubOpeningEvidenceRepository(self.connection),
            SQLiteHardcoverIdentityWorkRepository(self.connection),
        )
        return SQLiteTitleProposalRepository(
            self.connection,
            source_reader=reader,
            provider_reader=reader,
            **kwargs,
        )

    def test_positive_replay_and_atomic_rollback_use_the_shared_history(self) -> None:
        failing = self.repository(
            after_pointer_advance=lambda: (_ for _ in ()).throw(RuntimeError("injected")),
        )
        with self.assertRaisesRegex(RuntimeError, "injected"):
            ProposeLiveTitle(failing).run(
                self.locator,
                self.mount_digest,
                provider_enabled=True,
                authority_revision=1,
                observed_at=self.now,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM synthetic_title_proposal_sets "
                "WHERE execution_mode='production_report_only'"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM current_synthetic_title_proposals"
            ).fetchone(),
            (0,),
        )

        repository = self.repository()
        first = ProposeLiveTitle(repository).run(
            self.locator,
            self.mount_digest,
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        replay = ProposeLiveTitle(repository).run(
            self.locator,
            self.mount_digest,
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        self.assertEqual(first.proposal_set.result.value, "needs_review")
        self.assertTrue(first.proposal_set.production_applicable)
        self.assertTrue(replay.persistence.replayed)
        self.assertEqual(replay.proposal_set, first.proposal_set)
        self.assertEqual(
            self.connection.execute(
                "SELECT mode,execution_mode FROM synthetic_title_proposal_sets "
                "WHERE set_id=?",
                (first.proposal_set.set_id,),
            ).fetchone(),
            ("synthetic_preview", "production_report_only"),
        )

        self.assertEqual(repository.current_live(first.proposal_set.lineage), first.proposal_set)

        composed = _run_live_title_proposal_report(
            self.config_path,
            self.database,
            self.locator.target_reference,
            self.locator.snapshot_reference,
            self.locator.file_index,
        )
        self.assertEqual(composed.proposal_set, first.proposal_set)
        self.assertTrue(composed.persistence.replayed)


if __name__ == "__main__":
    unittest.main()
