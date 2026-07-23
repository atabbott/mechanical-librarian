import tempfile
import unittest
import hashlib
from pathlib import Path

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.cover_evidence import CoverArtifact


class CoverArtifactStoreTests(unittest.TestCase):
    def test_stage_deduplicates_and_reads_exact_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CoverArtifactStore(Path(directory))
            artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
            store.stage(artifact, b"source", b"preview")
            store.stage(artifact, b"source", b"preview")
            self.assertEqual(b"preview", store.preview(artifact))

    def test_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CoverArtifactStore(Path(directory))
            artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
            (Path(directory) / artifact.preview_digest).write_bytes(b"wrong")
            with self.assertRaises(MetadataReportError):
                store.preview(artifact)

    def test_recovery_preserves_referenced_history_and_removes_staged_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CoverArtifactStore(root)
            retained = CoverArtifact.create(b"retained source", b"retained preview", "image/png", 1, 1)
            orphan = CoverArtifact.create(b"orphan source", b"orphan preview", "image/png", 1, 1)
            store.stage(retained, b"retained source", b"retained preview")
            store.stage(orphan, b"orphan source", b"orphan preview")
            store.recover_unreferenced((retained.original_digest, retained.preview_digest))
            self.assertEqual(store.preview(retained), b"retained preview")
            self.assertFalse((root / orphan.original_digest).exists())
            self.assertFalse((root / orphan.preview_digest).exists())

    def test_recovery_fails_before_cleanup_for_missing_or_mismatched_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = CoverArtifactStore(root)
            orphan = CoverArtifact.create(b"orphan source", b"orphan preview", "image/png", 1, 1)
            store.stage(orphan, b"orphan source", b"orphan preview")
            missing = hashlib.sha256(b"missing").hexdigest()
            with self.assertRaises(MetadataReportError):
                store.recover_unreferenced((missing,))
            self.assertTrue((root / orphan.original_digest).is_file())
            (root / orphan.original_digest).write_bytes(b"different bytes")
            with self.assertRaises(MetadataReportError):
                store.recover_unreferenced((orphan.original_digest,))
            self.assertTrue((root / orphan.preview_digest).is_file())

    def test_same_artifact_identity_with_different_bytes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CoverArtifactStore(Path(directory))
            artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
            store.stage(artifact, b"source", b"preview")
            with self.assertRaises(MetadataReportError):
                store.stage(artifact, b"different source", b"preview")
