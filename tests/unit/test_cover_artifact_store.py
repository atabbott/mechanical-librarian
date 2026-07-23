import tempfile
import unittest
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
