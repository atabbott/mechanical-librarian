import unittest

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact, CoverEvidenceError, CoverSource, CoverSourceKind,
    CoverSourceState, cover_source_id, publication_ready,
)


class CoverEvidenceTests(unittest.TestCase):
    def test_retrieved_source_has_content_addressed_artifact(self) -> None:
        artifact = CoverArtifact.create(b"original", b"preview", "image/png", 2, 3)
        source = CoverSource(
            cover_source_id(CoverSourceKind.EPUB, "OPS/cover.png"),
            CoverSourceKind.EPUB,
            CoverSourceState.RETRIEVED,
            artifact,
        )
        self.assertTrue(publication_ready((source,)))

    def test_declared_source_cannot_be_published(self) -> None:
        source = CoverSource(
            cover_source_id(CoverSourceKind.EPUB, "OPS/cover.png"),
            CoverSourceKind.EPUB,
            CoverSourceState.DECLARED,
        )
        self.assertFalse(publication_ready((source,)))

    def test_timeout_has_explicit_scope(self) -> None:
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "candidate-1"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.TIMEOUT,
            timeout_scope="attempted",
        )
        self.assertTrue(publication_ready((source,)))
        with self.assertRaises(CoverEvidenceError):
            CoverSource(source.source_id, source.kind, CoverSourceState.TIMEOUT)
