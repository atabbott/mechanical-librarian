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
    EpubCoverFallback,
    EpubCoverRecovery,
    EpubCoverRecoveryRefusal,
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
        cover_source_id(kind, "product:B0SYNE7P1K"),
        kind,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            b"art", b"preview", "image/jpeg", 500, 500, "0f1e2d3c4b5a6978"
        ),
        provenance=CoverProvenance(
            "audiobooks",
            CoverDesignation.EXACT_EDITION,
            "unresolved",
            "audible-product-B0SYNE7P1K",
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


@pytest.mark.parametrize("kind", tuple(CoverSourceKind))
def test_canonical_source_vocabulary_is_admitted(kind: CoverSourceKind) -> None:
    source = CoverSource(cover_source_id(kind, "work-1"), kind, CoverSourceState.ABSENT)
    assert source.source_id.startswith(kind.value + "_")


@pytest.mark.parametrize("state", tuple(CoverSourceState))
def test_inventaire_all_acquisition_states(state: CoverSourceState) -> None:
    from mechanical_librarian.domain.cover_evidence import CoverTruncationReason

    provenance = (
        CoverProvenance(
            "inventaire",
            CoverDesignation.TRUNCATION,
            truncation_reason=CoverTruncationReason.PROVIDER_LIMIT,
        )
        if state is CoverSourceState.TRUNCATED
        else CoverProvenance(
            "inventaire", CoverDesignation.WORK, "inventaire-work-wd-Q1"
        )
    )
    source = CoverSource(
        cover_source_id(CoverSourceKind.INVENTAIRE, "work-1"),
        CoverSourceKind.INVENTAIRE,
        state,
        artifact=CoverArtifact.create(b"original", b"preview", "image/png", 2, 3)
        if state is CoverSourceState.RETRIEVED
        else None,
        timeout_scope="attempted" if state is CoverSourceState.TIMEOUT else None,
        provenance=provenance,
    )
    assert publication_ready((source,))
    assert source.to_data()["state"] == state.value


@pytest.mark.parametrize(
    "source_id",
    (
        "unknown_" + "a" * 64,
        "inventaire_" + "A" * 64,
        "inventaire_" + "a" * 63,
        "inventaire_" + "a" * 65,
        "inventaire_" + "g" * 64,
    ),
)
def test_invalid_inventaire_source_identity_is_rejected(source_id: str) -> None:
    with pytest.raises(CoverEvidenceError, match="^cover source is invalid$"):
        CoverSource(source_id, CoverSourceKind.INVENTAIRE, CoverSourceState.ABSENT)


def test_inventaire_source_kind_must_match_identity() -> None:
    with pytest.raises(
        CoverEvidenceError, match="^cover source identity is inconsistent$"
    ):
        CoverSource(
            cover_source_id(CoverSourceKind.INVENTAIRE, "work-1"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.ABSENT,
        )


@pytest.mark.parametrize(
    "recovery",
    (
        EpubCoverRecovery(tuple(EpubCoverFallback)),
        EpubCoverRecovery((EpubCoverFallback.GUIDE_COVER_REFERENCE,)),
        EpubCoverRecovery((), EpubCoverRecoveryRefusal.UNRESOLVED),
        EpubCoverRecovery(
            (EpubCoverFallback.COVER_IMAGE_WRAPPER,),
            EpubCoverRecoveryRefusal.WRAPPER_MULTIPLE_IMAGES,
        ),
    ),
    ids=("all_fallbacks", "one_fallback", "unresolved", "refused"),
)
def test_epub_cover_recovery_round_trips_as_closed_shadow_evidence(
    recovery: EpubCoverRecovery,
) -> None:
    data = recovery.to_data()
    assert data["authority"] == "shadow"
    assert EpubCoverRecovery.from_data(data) == recovery


@pytest.mark.parametrize(
    "fallbacks,refusal",
    (
        ((), None),
        (
            (EpubCoverFallback.GUIDE_COVER_REFERENCE,),
            EpubCoverRecoveryRefusal.UNRESOLVED,
        ),
        (
            (
                EpubCoverFallback.COVER_IMAGE_WRAPPER,
                EpubCoverFallback.GUIDE_COVER_REFERENCE,
            ),
            None,
        ),
        (
            (
                EpubCoverFallback.GUIDE_COVER_REFERENCE,
                EpubCoverFallback.GUIDE_COVER_REFERENCE,
            ),
            None,
        ),
        (("guide_cover_reference",), None),
        ((EpubCoverFallback.GUIDE_COVER_REFERENCE,), "cover_wrapper_no_image"),
        ([EpubCoverFallback.GUIDE_COVER_REFERENCE], None),
    ),
    ids=(
        "recovered_without_fallback",
        "unresolved_with_fallback",
        "out_of_order",
        "repeated",
        "plain_string",
        "plain_refusal",
        "list",
    ),
)
def test_epub_cover_recovery_refuses_an_open_shape(
    fallbacks: object, refusal: object
) -> None:
    with pytest.raises(CoverEvidenceError):
        EpubCoverRecovery(fallbacks, refusal)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    (
        None,
        {
            "authority": "verified",
            "fallbacks": [],
            "refusal": "cover_recovery_unresolved",
        },
        {"authority": "shadow", "fallbacks": ["first_image_in_spine"], "refusal": None},
        {"authority": "shadow", "fallbacks": [], "refusal": "made_up"},
        {"authority": "shadow", "fallbacks": "guide_cover_reference", "refusal": None},
        {
            "authority": "shadow",
            "fallbacks": ["guide_cover_reference"],
            "refusal": None,
            "href": "OEBPS/cover.xhtml",
        },
        {"authority": "shadow", "fallbacks": ["guide_cover_reference"]},
    ),
    ids=(
        "null",
        "authority",
        "fallback",
        "refusal",
        "fallbacks_text",
        "extra_key",
        "missing_key",
    ),
)
def test_epub_cover_recovery_parse_is_closed(value: object) -> None:
    with pytest.raises(CoverEvidenceError):
        EpubCoverRecovery.from_data(value)
