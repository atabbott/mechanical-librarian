import unittest

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverContext,
    CoverContextField,
    CoverDesignation,
    CoverEvidenceError,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
    publication_ready,
)


class CoverEvidenceTests(unittest.TestCase):
    def test_provider_provenance_is_public_safe_and_round_trippable(self) -> None:
        provenance = CoverProvenance(
            "catalogue",
            CoverDesignation.EXACT_EDITION,
            "hardcover-work-0000000001",
            "hardcover-edition-0000000002",
            (
                CoverContext(CoverContextField.ISBN, "9780306406157"),
                CoverContext(CoverContextField.LANGUAGE, "en"),
            ),
        )
        artifact = CoverArtifact.create(
            b"same-image",
            b"preview",
            "image/png",
            2,
            3,
        )
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "edition:2"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.RETRIEVED,
            artifact,
            provenance=provenance,
        )
        self.assertTrue(publication_ready((source,)))
        self.assertNotIn("url", source.to_data())
        self.assertNotIn("path", source.to_data())
        for unsafe in (
            "/Users/private/library",
            "token=secret-value",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(CoverEvidenceError):
                CoverContext(CoverContextField.PUBLISHER, unsafe)

    def test_retrieved_source_has_content_addressed_artifact(self) -> None:
        artifact = CoverArtifact.create(b"original", b"preview", "image/png", 2, 3)
        source = CoverSource(
            cover_source_id(CoverSourceKind.EPUB, "OPS/cover.png"),
            CoverSourceKind.EPUB,
            CoverSourceState.RETRIEVED,
            artifact,
        )
        self.assertTrue(publication_ready((source,)))

    def test_timeout_has_explicit_scope(self) -> None:
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "candidate-1"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.TIMEOUT,
            timeout_scope="attempted",
            provenance=CoverProvenance(
                "hardcover",
                CoverDesignation.WORK,
                "hardcover-work-0000000001",
            ),
        )
        self.assertTrue(publication_ready((source,)))
        with self.assertRaises(CoverEvidenceError):
            CoverSource(source.source_id, source.kind, CoverSourceState.TIMEOUT)

    def test_publication_rejects_more_than_eight_provider_outcomes(self) -> None:
        sources = tuple(
            CoverSource(
                cover_source_id(
                    CoverSourceKind.HARDCOVER,
                    f"catalogue-{index // 4}:candidate-{index}",
                ),
                CoverSourceKind.HARDCOVER,
                CoverSourceState.ABSENT,
                provenance=CoverProvenance(
                    f"catalogue-{index // 4}",
                    CoverDesignation.WORK,
                    f"work-{index}",
                ),
            )
            for index in range(9)
        )
        with self.assertRaises(CoverEvidenceError):
            publication_ready(sources)
