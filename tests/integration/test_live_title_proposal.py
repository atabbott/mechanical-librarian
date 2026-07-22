from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
from mechanical_librarian.application.live_title_proposals import (
    LiveSourceEvidence,
    LiveSourceState,
    ProposeLiveTitle,
)
from mechanical_librarian.bootstrap.composition import (
    _epub_material_configuration,
    _inventory_mount_mappings,
    _mount_configuration_digest,
    _run_epub_opening_report,
    _run_live_title_proposal_report,
)
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceLocator,
    epub_governing_digest,
)
from mechanical_librarian.domain.title_proposals import (
    TitleProposalResult,
    live_proposal_set_from_manifest,
)
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
from mechanical_librarian.adapters.persistence.live_title_evidence import (
    SQLiteLiveTitleEvidenceReader,
)
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.local_media import sha256_descriptor
from tests.support.epub_packages import opening_title_isbn_applicability


LIBRARY_ID = "00000000-0000-4000-8000-000000000001"


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
        config["connections"] = [
            {
                "adapter": "audiobookshelf",
                "enabled_libraries": [LIBRARY_ID],
                "endpoint": "https://audiobookshelf.invalid",
                "id": "connection",
                "secret_reference": "/run/secrets/audiobookshelf-token",
            }
        ]
        config["hardcover_provider"] = {
            "enabled": True,
            "secret_reference": "/run/secrets/hardcover-token",
            "authority_revision": 1,
        }
        config["evidence"]["maximum_text_characters"] = 1024
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        configured = load_configuration_file(self.config_path)
        self.mount_digest = _mount_configuration_digest(configured.media_mounts[0])
        mappings = _inventory_mount_mappings(configured)
        package_limits = replace(
            configured.evidence.epub_package,
            source_archive_bytes=min(
                configured.evidence.epub_package.source_archive_bytes,
                configured.evidence.maximum_document_bytes,
            ),
        )
        opening_limits = replace(
            configured.evidence.epub_opening,
            package=package_limits,
            text_characters_per_selection=min(
                configured.evidence.epub_opening.text_characters_per_selection,
                configured.evidence.maximum_text_characters,
            ),
        )
        self.governing_digest = epub_governing_digest(
            opening_limits, _epub_material_configuration(configured, mappings)
        )
        target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("connection"),
            LibraryId(LIBRARY_ID),
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
            self.governing_digest,
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
            self.governing_digest,
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
        self.provider_current_since = start
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
        epub_repository = SQLiteEpubOpeningEvidenceRepository(self.connection)
        reader = SQLiteLiveTitleEvidenceReader(
            self.connection,
            epub_repository,
            epub_repository,
            SQLiteHardcoverIdentityWorkRepository(self.connection),
            lambda source: hashlib.sha256(
                (self.root / source.relative_path).read_bytes()
            ).hexdigest(),
            self.governing_digest,
            kwargs.pop("target_is_configured", lambda _target: True),
        )
        publication_clock = kwargs.pop("publication_clock", lambda: self.now)
        provider_configuration_is_current = kwargs.pop(
            "provider_configuration_is_current", lambda: True
        )
        return SQLiteTitleProposalRepository(
            self.connection,
            source_reader=reader,
            provider_reader=reader,
            publication_clock=publication_clock,
            provider_configuration_is_current=provider_configuration_is_current,
            **kwargs,
        )

    def test_positive_replay_and_atomic_rollback_use_the_shared_history(self) -> None:
        failing = self.repository(
            after_pointer_advance=lambda: (_ for _ in ()).throw(RuntimeError("injected")),
        )
        with self.assertRaisesRegex(RuntimeError, "injected"):
            ProposeLiveTitle(failing).run(
                self.locator,
                (self.mount_digest,),
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
            (self.mount_digest,),
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        replay = ProposeLiveTitle(repository).run(
            self.locator,
            (self.mount_digest,),
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

    def test_mounted_epub_replacement_is_rehashed_before_provider_lookup(self) -> None:
        replacement = self.root / "replacement.epub"
        replacement.write_bytes(b"not the evidenced epub")
        replacement.replace(self.root / "book.epub")

        repository = self.repository()
        source = repository.inspect_source(self.locator, (self.mount_digest,))
        self.assertEqual(source.state.value, "not_ready")
        report = ProposeLiveTitle(repository).run(
            self.locator,
            (self.mount_digest,),
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        self.assertEqual(report.proposal_set.result.value, "source_not_ready")
        self.assertFalse(report.to_data()["production_applicable"])

    def test_non_audiobookshelf_or_non_ebook_inventory_is_not_a_live_source(self) -> None:
        original = self.snapshot.observation
        cases = (
            InventoryObservation.create(
                target=TargetId(
                    AdapterId("synthetic"),
                    ConnectionId("connection"),
                    LibraryId(LIBRARY_ID),
                    ExternalItemId("item"),
                ),
                media_type=original.media_type,
                lifecycle=original.lifecycle,
                fields=original.fields,
                file_identities=original.file_identities,
                cover_identity=original.cover_identity,
                adapter_version=original.adapter_version,
                normalizer_version=original.normalizer_version,
                service_version=original.service_version,
                warnings=original.warnings,
                observed_at=original.observed_at,
            ),
            InventoryObservation.create(
                target=original.target,
                media_type=MediaType.AUDIOBOOK,
                lifecycle=original.lifecycle,
                fields=original.fields,
                file_identities=original.file_identities,
                cover_identity=original.cover_identity,
                adapter_version=original.adapter_version,
                normalizer_version=original.normalizer_version,
                service_version=original.service_version,
                warnings=original.warnings,
                observed_at=original.observed_at,
            ),
        )
        for observation in cases:
            with (
                self.subTest(target=observation.target.adapter.value, media=observation.media_type.value),
                patch.object(InventoryObservation, "from_json", return_value=observation),
                patch.object(
                    SQLiteLiveTitleEvidenceReader,
                    "inspect_title_provider",
                    side_effect=AssertionError("provider lookup must remain unreachable"),
                ) as provider_lookup,
            ):
                report = ProposeLiveTitle(self.repository()).run(
                    self.locator,
                    (self.mount_digest,),
                    provider_enabled=True,
                    authority_revision=1,
                    observed_at=self.now,
                )
                provider_lookup.assert_not_called()
                self.assertEqual(report.proposal_set.result.value, "source_not_ready")
                self.assertFalse(report.to_data()["production_applicable"])

    def test_off_config_connection_or_library_is_not_a_live_source(self) -> None:
        original = json.loads(self.config_path.read_text(encoding="utf-8"))
        for field, value in (
            ("id", "other-connection"),
            ("enabled_libraries", ["00000000-0000-4000-8000-000000000002"]),
        ):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(original))
                changed["connections"][0][field] = value
                self.config_path.write_text(json.dumps(changed), encoding="utf-8")
                with patch.object(
                    SQLiteLiveTitleEvidenceReader,
                    "inspect_title_provider",
                    side_effect=AssertionError(
                        "provider lookup must remain unreachable"
                    ),
                ) as provider_lookup:
                    report = _run_live_title_proposal_report(
                        self.config_path,
                        self.database,
                        self.locator.target_reference,
                        self.locator.snapshot_reference,
                        self.locator.file_index,
                    )
                provider_lookup.assert_not_called()
                self.assertEqual(report.proposal_set.result.value, "source_not_ready")
                self.assertFalse(report.to_data()["production_applicable"])

    def test_atomic_path_replacement_during_final_hash_rejects_publication(self) -> None:
        calls = 0

        def replace_during_hash(
            descriptor: int, length: int, *, maximum: int
        ) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                replacement = self.root / "replacement.epub"
                replacement.write_bytes(b"replacement after final descriptor open")
                replacement.replace(self.root / "book.epub")
            return sha256_descriptor(descriptor, length, maximum=maximum)

        with patch(
            "mechanical_librarian.bootstrap.composition.sha256_descriptor",
            side_effect=replace_during_hash,
        ):
            with self.assertRaisesRegex(
                TitleProposalPersistenceError, "live proposal source changed"
            ):
                _run_live_title_proposal_report(
                    self.config_path,
                    self.database,
                    self.locator.target_reference,
                    self.locator.snapshot_reference,
                    self.locator.file_index,
                )

        self.assertEqual(calls, 2)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM synthetic_title_proposal_sets "
                "WHERE execution_mode='production_report_only'"
            ).fetchone(),
            (0,),
        )

    def test_source_replacement_after_store_callbacks_rolls_back_publication(self) -> None:
        original = opening_title_isbn_applicability()

        def replace_source() -> None:
            replacement = self.root / "replacement.epub"
            replacement.write_bytes(b"replacement during proposal transaction")
            replacement.replace(self.root / "book.epub")

        for callback in (
            "after_history_insert",
            "after_pointer_advance",
            "before_readback",
        ):
            with self.subTest(callback=callback):
                (self.root / "book.epub").write_bytes(original)
                with self.assertRaisesRegex(
                    TitleProposalPersistenceError, "live proposal source changed"
                ):
                    ProposeLiveTitle(
                        self.repository(**{callback: replace_source})
                    ).run(
                        self.locator,
                        (self.mount_digest,),
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

    def test_config_revocation_after_history_insert_rolls_back_publication(self) -> None:
        selected = True

        def revoke_configuration() -> None:
            nonlocal selected
            selected = False

        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live proposal source changed"
        ):
            ProposeLiveTitle(
                self.repository(
                    target_is_configured=lambda _target: selected,
                    after_history_insert=revoke_configuration,
                )
            ).run(
                self.locator,
                (self.mount_digest,),
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

    def test_repository_rejects_a_persisted_positive_with_not_ready_evidence(self) -> None:
        repository = self.repository()
        report = ProposeLiveTitle(repository).run(
            self.locator,
            (self.mount_digest,),
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        data = json.loads(report.proposal_set.to_json())
        data["manifest"]["provider_status"] = "not_ready"
        self.connection.execute(
            "DROP TRIGGER synthetic_title_proposal_sets_no_update"
        )
        self.connection.execute(
            "UPDATE synthetic_title_proposal_sets SET canonical_set=? WHERE set_id=?",
            (
                json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                report.proposal_set.set_id,
            ),
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "proposal state could not be read"
        ):
            repository.current_live(report.proposal_set.lineage)

    def test_provider_expiry_is_checked_with_fresh_publication_clock(self) -> None:
        repository = self.repository(
            publication_clock=lambda: self.now + timedelta(days=365)
        )
        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live provider evidence changed"
        ):
            ProposeLiveTitle(repository).run(
                self.locator,
                (self.mount_digest,),
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

    def test_provider_expiry_after_store_callbacks_rolls_back_successor(self) -> None:
        prior = ProposeLiveTitle(self.repository()).run(
            self.locator,
            (self.mount_digest,),
            provider_enabled=False,
            authority_revision=1,
            observed_at=self.now,
        )

        for callback in (
            "after_history_insert",
            "after_pointer_advance",
            "before_readback",
        ):
            with self.subTest(callback=callback):
                publication_time = self.now

                def expire_provider() -> None:
                    nonlocal publication_time
                    publication_time = self.now + timedelta(days=365)

                repository = self.repository(
                    publication_clock=lambda: publication_time,
                    **{callback: expire_provider},
                )
                with self.assertRaisesRegex(
                    TitleProposalPersistenceError, "live provider evidence changed"
                ):
                    ProposeLiveTitle(repository).run(
                        self.locator,
                        (self.mount_digest,),
                        provider_enabled=True,
                        authority_revision=1,
                        observed_at=self.now,
                    )
                self.assertEqual(
                    repository.current_live(prior.proposal_set.lineage),
                    prior.proposal_set,
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT COUNT(*) FROM synthetic_title_proposal_sets "
                        "WHERE execution_mode='production_report_only'"
                    ).fetchone(),
                    (1,),
                )

    def test_provider_configuration_changes_after_store_callbacks_roll_back_successor(
        self,
    ) -> None:
        prior = ProposeLiveTitle(self.repository()).run(
            self.locator,
            (self.mount_digest,),
            provider_enabled=False,
            authority_revision=1,
            observed_at=self.now,
        )

        for callback in (
            "after_history_insert",
            "after_pointer_advance",
            "before_readback",
        ):
            for change in ("disabled", "authority_revision"):
                with self.subTest(callback=callback, change=change):
                    provider_configuration = {
                        "enabled": True,
                        "authority_revision": 1,
                    }

                    def change_provider_configuration() -> None:
                        if change == "disabled":
                            provider_configuration["enabled"] = False
                        else:
                            provider_configuration["authority_revision"] = 2

                    repository = self.repository(
                        provider_configuration_is_current=lambda: (
                            provider_configuration["enabled"]
                            and provider_configuration["authority_revision"] == 1
                        ),
                        **{callback: change_provider_configuration},
                    )
                    with self.assertRaisesRegex(
                        TitleProposalPersistenceError,
                        "live provider configuration changed",
                    ):
                        ProposeLiveTitle(repository).run(
                            self.locator,
                            (self.mount_digest,),
                            provider_enabled=True,
                            authority_revision=1,
                            observed_at=self.now,
                        )
                    self.assertEqual(
                        repository.current_live(prior.proposal_set.lineage),
                        prior.proposal_set,
                    )
                    self.assertEqual(
                        self.connection.execute(
                            "SELECT COUNT(*) FROM synthetic_title_proposal_sets "
                            "WHERE execution_mode='production_report_only'"
                        ).fetchone(),
                        (1,),
                    )

    def test_future_provider_current_since_rejects_clock_rollback_without_history(self) -> None:
        repository = self.repository(
            publication_clock=lambda: self.provider_current_since - timedelta(seconds=1)
        )
        future = repository.inspect_provider(
            self.work_key, self.provider_current_since - timedelta(seconds=1)
        )
        self.assertEqual(future.state.value, "not_ready")

        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live provider evidence changed"
        ):
            ProposeLiveTitle(repository).run(
                self.locator,
                (self.mount_digest,),
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

    def test_forged_current_f07b_row_without_work_lifecycle_is_rejected(self) -> None:
        original = list(
            self.connection.execute(
                "SELECT * FROM epub_opening_evidence"
            ).fetchone()
        )
        original[0] = "evidence_" + "a" * 32
        original[1] = hashlib.sha256(b"forged-evidence-digest").hexdigest()
        original[2] = hashlib.sha256(b"forged-work-key").hexdigest()
        self.connection.execute("DELETE FROM current_epub_opening_evidence")
        self.connection.execute(
            "INSERT INTO epub_opening_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(original),
        )
        self.connection.execute(
            "INSERT INTO current_epub_opening_evidence("
            "target_reference,file_index,evidence_id,sequence,lane_revision,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                self.locator.target_reference,
                self.locator.file_index,
                original[0],
                2,
                2,
                self.now.isoformat().replace("+00:00", "Z"),
            ),
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live proposal source could not be read"
        ):
            _run_live_title_proposal_report(
                self.config_path,
                self.database,
                self.locator.target_reference,
                self.locator.snapshot_reference,
                self.locator.file_index,
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

    def test_governing_configuration_change_makes_old_f07b_evidence_not_ready(self) -> None:
        first = _run_live_title_proposal_report(
            self.config_path,
            self.database,
            self.locator.target_reference,
            self.locator.snapshot_reference,
            self.locator.file_index,
        )
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["evidence"]["maximum_text_characters"] = 2048
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        changed = _run_live_title_proposal_report(
            self.config_path,
            self.database,
            self.locator.target_reference,
            self.locator.snapshot_reference,
            self.locator.file_index,
        )
        self.assertEqual(first.proposal_set.result.value, "needs_review")
        self.assertEqual(changed.proposal_set.result.value, "source_not_ready")
        self.assertFalse(changed.persistence.replayed)
        self.assertFalse(changed.to_data()["production_applicable"])
        self.assertEqual(changed.persistence.history_count, 2)

    def test_disabling_epub_prevents_positive_replay_and_provider_lookup(self) -> None:
        first = _run_live_title_proposal_report(
            self.config_path,
            self.database,
            self.locator.target_reference,
            self.locator.snapshot_reference,
            self.locator.file_index,
        )
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["evidence"]["enabled_formats"] = ["m4b"]
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

        with patch.object(
            SQLiteLiveTitleEvidenceReader,
            "inspect_title_provider",
            side_effect=AssertionError("provider lookup must remain unreachable"),
        ) as provider_lookup:
            changed = _run_live_title_proposal_report(
                self.config_path,
                self.database,
                self.locator.target_reference,
                self.locator.snapshot_reference,
                self.locator.file_index,
            )

        provider_lookup.assert_not_called()
        self.assertEqual(first.proposal_set.result.value, "needs_review")
        self.assertEqual(changed.proposal_set.result.value, "source_not_ready")
        self.assertFalse(changed.persistence.replayed)
        self.assertFalse(changed.to_data()["production_applicable"])
        self.assertEqual(changed.persistence.history_count, 2)

    def test_exact_replay_rejects_a_concurrently_advanced_current_pointer(self) -> None:
        repository = self.repository()
        first = ProposeLiveTitle(repository).run(
            self.locator,
            (self.mount_digest,),
            provider_enabled=True,
            authority_revision=1,
            observed_at=self.now,
        )
        manifest = dict(first.proposal_set.manifest)
        manifest["lineage_revision"] = 2
        manifest["previous_set_id"] = first.proposal_set.set_id
        successor = live_proposal_set_from_manifest(
            manifest=manifest,
            result=TitleProposalResult.NO_CHANGE,
            current_value=None,
            proposed_value=None,
        )
        self.connection.execute(
            "INSERT INTO synthetic_title_proposal_sets("
            "set_id,identity_digest,lineage,lineage_revision,previous_set_id,mode,"
            "canonical_set,execution_mode) VALUES (?,?,?,?,?,'synthetic_preview',?,?)",
            (
                successor.set_id,
                successor.identity_digest,
                successor.lineage,
                successor.lineage_revision,
                successor.previous_set_id,
                successor.to_json(),
                successor.mode.value,
            ),
        )
        self.connection.execute(
            "UPDATE current_synthetic_title_proposals SET set_id=? WHERE lineage=?",
            (successor.set_id, successor.lineage),
        )
        self.connection.commit()
        source = repository.inspect_source(self.locator, (self.mount_digest,))
        provider = repository.inspect_provider(self.work_key, self.now)
        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live proposal is no longer current"
        ):
            repository.publish_live(
                first.proposal_set,
                source,
                provider,
                (self.mount_digest,),
            )

    def test_newly_ready_second_mount_is_rejected_at_publication(self) -> None:
        real_reader = SQLiteLiveTitleEvidenceReader(
            self.connection,
            SQLiteEpubOpeningEvidenceRepository(self.connection),
            SQLiteEpubOpeningEvidenceRepository(self.connection),
            SQLiteHardcoverIdentityWorkRepository(self.connection),
            lambda source: hashlib.sha256(
                (self.root / source.relative_path).read_bytes()
            ).hexdigest(),
            self.governing_digest,
            lambda _target: True,
        )
        second_digest = hashlib.sha256(b"second-mount").hexdigest()
        calls = 0

        class ChangingMountReader:
            def inspect_title_source(inner_self, locator, mount_digest):
                nonlocal calls
                if mount_digest == self.mount_digest:
                    return real_reader.inspect_title_source(locator, mount_digest)
                calls += 1
                if calls == 1:
                    return LiveSourceEvidence(
                        locator, LiveSourceState.NOT_READY, mount_digest
                    )
                return replace(
                    real_reader.inspect_title_source(locator, self.mount_digest),
                    mount_configuration_digest=mount_digest,
                )

        mounts = tuple(sorted((self.mount_digest, second_digest)))
        repository = SQLiteTitleProposalRepository(
            self.connection,
            source_reader=ChangingMountReader(),
            provider_reader=real_reader,
            publication_clock=lambda: self.now,
        )
        with self.assertRaisesRegex(
            TitleProposalPersistenceError, "live proposal source is ambiguous"
        ):
            ProposeLiveTitle(repository).run(
                self.locator,
                mounts,
                provider_enabled=True,
                authority_revision=1,
                observed_at=self.now,
            )


if __name__ == "__main__":
    unittest.main()
