import pytest

from mechanical_librarian.domain.cover_evidence import (
    LOCAL_COVER_SOURCE_KINDS,
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


@pytest.mark.parametrize(
    "unsafe",
    ("/Users/private/library", "token=secret-value"),
    ids=("path", "token"),
)
def test_provider_provenance_is_public_safe_and_round_trippable(unsafe: str) -> None:
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
    assert publication_ready((source,))
    assert "url" not in source.to_data()
    assert "path" not in source.to_data()
    with pytest.raises(CoverEvidenceError):
        CoverContext(CoverContextField.PUBLISHER, unsafe)


def test_retrieved_source_has_content_addressed_artifact() -> None:
    artifact = CoverArtifact.create(b"original", b"preview", "image/png", 2, 3)
    source = CoverSource(
        cover_source_id(CoverSourceKind.EPUB, "OPS/cover.png"),
        CoverSourceKind.EPUB,
        CoverSourceState.RETRIEVED,
        artifact,
    )
    assert publication_ready((source,))


def test_timeout_has_explicit_scope() -> None:
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
    assert publication_ready((source,))
    with pytest.raises(CoverEvidenceError):
        CoverSource(source.source_id, source.kind, CoverSourceState.TIMEOUT)


def test_publication_rejects_more_than_eight_provider_outcomes() -> None:
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
    with pytest.raises(CoverEvidenceError):
        publication_ready(sources)


@pytest.mark.parametrize(
    "kind",
    (CoverSourceKind.AUDIBLE_CATALOG, CoverSourceKind.ITUNES_SEARCH),
)
def test_an_audiobook_cover_source_is_a_provider_row(kind: CoverSourceKind) -> None:
    """The two cover-bearing audiobook authorities enter the provider arithmetic."""
    source = CoverSource(
        cover_source_id(kind, "product:B08G9PRS1K"),
        kind,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            b"art", b"preview", "image/jpeg", 500, 500, "0f1e2d3c4b5a6978"
        ),
        provenance=CoverProvenance(
            "audiobooks",
            CoverDesignation.EXACT_EDITION,
            "unresolved",
            "audible-product-B08G9PRS1K",
        ),
    )
    assert publication_ready((source,))
    assert kind not in LOCAL_COVER_SOURCE_KINDS
    with pytest.raises(CoverEvidenceError):
        publication_ready(
            (
                CoverSource(
                    source.source_id,
                    kind,
                    CoverSourceState.RETRIEVED,
                    source.artifact,
                ),
            )
        )


def test_an_audiobook_cover_row_still_obeys_the_eight_provider_ceiling() -> None:
    sources = tuple(
        CoverSource(
            cover_source_id(
                CoverSourceKind.AUDIBLE_CATALOG,
                f"authority-{index // 4}:candidate-{index}",
            ),
            CoverSourceKind.AUDIBLE_CATALOG,
            CoverSourceState.ABSENT,
            provenance=CoverProvenance(
                f"authority-{index // 4}",
                CoverDesignation.WORK,
                f"work-{index}",
            ),
        )
        for index in range(9)
    )
    with pytest.raises(CoverEvidenceError):
        publication_ready(sources)


def test_a_perceptual_hash_is_a_64_bit_lower_case_hex_value_or_absent() -> None:
    assert CoverArtifact.create(b"a", b"b", "image/png", 2, 3).perceptual_hash is None
    assert (
        "0f1e2d3c4b5a6978"
        == CoverArtifact.create(
            b"a", b"b", "image/png", 2, 3, "0f1e2d3c4b5a6978"
        ).perceptual_hash
    )
    for invalid in ("0F1E2D3C4B5A6978", "0f1e2d3c4b5a697", "0f1e2d3c4b5a69789", "zz"):
        with pytest.raises(CoverEvidenceError):
            CoverArtifact.create(b"a", b"b", "image/png", 2, 3, invalid)
