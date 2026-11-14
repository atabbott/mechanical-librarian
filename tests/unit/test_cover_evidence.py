import pytest

from mechanical_librarian.domain.cover_evidence import (
    DECODER_REJECTED_STATES,
    LOCAL_COVER_SOURCE_KINDS,
    CoverArtifact,
    CoverContext,
    CoverContextField,
    CoverDesignation,
    CoverEvidenceError,
    CoverFinalHost,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    EpubCoverFallback,
    EpubCoverRecovery,
    EpubCoverRecoveryRefusal,
    cover_source_id,
    publication_ready,
)
from mechanical_librarian.domain.cover_rules import CoverMeasures


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


def _provider_rows(
    kind: CoverSourceKind, provider: str, count: int, *, start: int = 0
) -> tuple[CoverSource, ...]:
    return tuple(
        CoverSource(
            cover_source_id(kind, f"{provider}:candidate-{index}"),
            kind,
            CoverSourceState.ABSENT,
            provenance=CoverProvenance(
                provider, CoverDesignation.WORK, f"work-{index}"
            ),
        )
        for index in range(start, start + count)
    )


def _truncation_rows(
    kind: CoverSourceKind, provider: str, reasons: tuple[CoverTruncationReason, ...]
) -> tuple[CoverSource, ...]:
    return tuple(
        CoverSource(
            cover_source_id(kind, f"truncation:{provider}:{reason.value}:{index}"),
            kind,
            CoverSourceState.TRUNCATED,
            provenance=CoverProvenance(
                provider, CoverDesignation.TRUNCATION, truncation_reason=reason
            ),
        )
        for index, reason in enumerate(reasons)
    )


def test_publication_admits_eight_hardcover_rows_and_twelve_in_all() -> None:
    sources = (
        *_provider_rows(CoverSourceKind.HARDCOVER, "hardcover", 8),
        *_provider_rows(CoverSourceKind.OPEN_LIBRARY, "open-library", 4),
    )
    assert publication_ready(sources)


def test_publication_refuses_a_thirteenth_provider_row() -> None:
    sources = (
        *_provider_rows(CoverSourceKind.HARDCOVER, "hardcover", 8),
        *_provider_rows(CoverSourceKind.OPEN_LIBRARY, "open-library", 4),
        *_provider_rows(CoverSourceKind.INVENTAIRE, "inventaire", 1),
    )
    with pytest.raises(CoverEvidenceError):
        publication_ready(sources)


def test_publication_refuses_a_ninth_hardcover_row() -> None:
    with pytest.raises(CoverEvidenceError):
        publication_ready(_provider_rows(CoverSourceKind.HARDCOVER, "hardcover", 9))


def test_publication_refuses_a_fifth_row_from_any_other_provider() -> None:
    assert publication_ready(
        _provider_rows(CoverSourceKind.OPEN_LIBRARY, "open-library", 4)
    )
    with pytest.raises(CoverEvidenceError):
        publication_ready(
            _provider_rows(CoverSourceKind.OPEN_LIBRARY, "open-library", 5)
        )


def test_publication_admits_five_truncation_rows_per_provider_and_no_sixth() -> None:
    reasons = tuple(CoverTruncationReason)
    assert len(reasons) == 5
    assert publication_ready(
        _truncation_rows(CoverSourceKind.HARDCOVER, "hardcover", reasons)
    )
    with pytest.raises(CoverEvidenceError):
        publication_ready(
            _truncation_rows(
                CoverSourceKind.HARDCOVER,
                "hardcover",
                (*reasons, CoverTruncationReason.SAME_ART),
            )
        )


def test_publication_refuses_a_thirteenth_retrieved_provider_cover() -> None:
    def retrieved(provider: str, index: int) -> CoverSource:
        return CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, f"{provider}:{index}"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.RETRIEVED,
            CoverArtifact.create(
                f"art-{provider}-{index}".encode(), b"preview", "image/png", 2, 3
            ),
            provenance=CoverProvenance(provider, CoverDesignation.WORK, f"w-{index}"),
        )

    twelve = (
        *(retrieved("hardcover-a", index) for index in range(8)),
        *(retrieved("hardcover-b", index) for index in range(4)),
    )
    assert publication_ready(twelve)
    with pytest.raises(CoverEvidenceError):
        publication_ready((*twelve, retrieved("hardcover-b", 4)))


def test_a_listed_edition_provenance_names_the_book_and_the_edition() -> None:
    provenance = CoverProvenance(
        "hardcover",
        CoverDesignation.LISTED_EDITION,
        "hardcover-work-0000000001",
        "hardcover-edition-0000000002",
        (CoverContext(CoverContextField.LANGUAGE, "en"),),
    )
    assert provenance.to_data() == {
        "context": [{"field": "language", "value": "en"}],
        "designation": "listed_edition",
        "edition_identity": "hardcover-edition-0000000002",
        "provider": "hardcover",
        "truncation_reason": None,
        "work_identity": "hardcover-work-0000000001",
    }
    with pytest.raises(CoverEvidenceError):
        CoverProvenance(
            "hardcover", CoverDesignation.LISTED_EDITION, "hardcover-work-0000000001"
        )
    with pytest.raises(CoverEvidenceError):
        CoverProvenance(
            "hardcover",
            CoverDesignation.LISTED_EDITION,
            None,
            "hardcover-edition-0000000002",
        )


def test_the_truncation_reasons_are_closed() -> None:
    assert {reason.value for reason in CoverTruncationReason} == {
        "provider_limit",
        "gallery_limit",
        "edition_limit",
        "edition_fetch_incomplete",
        "same_art",
    }


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


def test_an_audiobook_cover_row_still_obeys_the_twelve_provider_ceiling() -> None:
    sources = tuple(
        row
        for index in range(4)
        for row in _provider_rows(
            CoverSourceKind.AUDIBLE_CATALOG, f"authority-{index}", 4 if index < 3 else 1
        )
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


@pytest.mark.parametrize(
    "kind",
    tuple(value for value in CoverSourceKind if value is not CoverSourceKind.RETAINED),
)
def test_canonical_source_vocabulary_is_admitted(kind: CoverSourceKind) -> None:
    source = CoverSource(cover_source_id(kind, "work-1"), kind, CoverSourceState.ABSENT)
    assert source.source_id.startswith(kind.value + "_")


def test_a_retained_source_is_only_a_retrieved_original_with_no_provenance() -> None:
    artifact = CoverArtifact.create(b"original", b"preview", "image/png", 2, 3)
    source_id = cover_source_id(CoverSourceKind.RETAINED, "effect_plan_" + "a" * 64)
    source = CoverSource(
        source_id, CoverSourceKind.RETAINED, CoverSourceState.RETRIEVED, artifact
    )

    assert source.source_id.startswith("retained_")
    assert source.to_data()["provenance"] is None
    with pytest.raises(CoverEvidenceError, match="retained cover source is invalid"):
        CoverSource(source_id, CoverSourceKind.RETAINED, CoverSourceState.ABSENT)
    with pytest.raises(CoverEvidenceError, match="retained cover source is invalid"):
        CoverSource(
            source_id,
            CoverSourceKind.RETAINED,
            CoverSourceState.RETRIEVED,
            artifact,
            provenance=CoverProvenance(
                "inventaire", CoverDesignation.WORK, "inventaire-work-wd-Q1"
            ),
        )


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


def _open_library_source(
    state: CoverSourceState = CoverSourceState.RETRIEVED,
    final_host: object = None,
    kind: CoverSourceKind = CoverSourceKind.OPEN_LIBRARY,
) -> CoverSource:
    return CoverSource(
        cover_source_id(kind, "catalogue:edition:/books/OL1M"),
        kind,
        state,
        artifact=CoverArtifact.create(b"original", b"preview", "image/jpeg", 2, 3)
        if state is CoverSourceState.RETRIEVED
        else None,
        timeout_scope="attempted" if state is CoverSourceState.TIMEOUT else None,
        provenance=CoverProvenance(
            "catalogue",
            CoverDesignation.TRUNCATION,
            truncation_reason=CoverTruncationReason.PROVIDER_LIMIT,
        )
        if state is CoverSourceState.TRUNCATED
        else CoverProvenance("catalogue", CoverDesignation.WORK, "work-1"),
        final_host=final_host,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("family", tuple(CoverFinalHost))
def test_a_retrieved_open_library_source_records_its_closed_host_family(
    family: CoverFinalHost,
) -> None:
    source = _open_library_source(final_host=family)
    assert source.to_data()["final_host"] == family.value
    assert {"covers.openlibrary.org", "archive.org", "us.archive.org"} == {
        value.value for value in CoverFinalHost
    }


def test_a_retrieved_open_library_source_requires_a_final_host() -> None:
    with pytest.raises(CoverEvidenceError, match="final host is inconsistent"):
        _open_library_source()


@pytest.mark.parametrize(
    "state",
    tuple(
        value for value in CoverSourceState if value is not CoverSourceState.RETRIEVED
    ),
)
def test_only_a_retrieved_open_library_source_states_a_final_host(
    state: CoverSourceState,
) -> None:
    assert _open_library_source(state).to_data()["final_host"] is None
    with pytest.raises(CoverEvidenceError, match="final host is inconsistent"):
        _open_library_source(state, CoverFinalHost.US_ARCHIVE)


@pytest.mark.parametrize(
    "kind",
    tuple(
        value
        for value in CoverSourceKind
        if value
        not in {
            CoverSourceKind.OPEN_LIBRARY,
            CoverSourceKind.RETAINED,
            *LOCAL_COVER_SOURCE_KINDS,
        }
    ),
)
def test_other_provider_kinds_never_state_a_final_host(kind: CoverSourceKind) -> None:
    assert _open_library_source(kind=kind).final_host is None
    with pytest.raises(CoverEvidenceError, match="final host is inconsistent"):
        _open_library_source(final_host=CoverFinalHost.ARCHIVE, kind=kind)


@pytest.mark.parametrize(
    "value",
    ("us.archive.org", "ia800100.us.archive.org", 1),
    ids=("plain_string", "raw_datanode", "integer"),
)
def test_a_final_host_outside_the_closed_family_is_refused(value: object) -> None:
    with pytest.raises(CoverEvidenceError, match="final host is inconsistent"):
        _open_library_source(final_host=value)


def test_an_artifact_carries_its_measures_or_none() -> None:
    measures = CoverMeasures("0f" * 8, "f0" * 8, (1,) * 64, (128,) * 48)
    measured = CoverArtifact.create(
        b"a", b"b", "image/png", 2, 3, "0" * 16, measures=measures
    )

    assert measured.measures == measures
    assert measured.to_data()["measures"] == measures.to_data()
    assert (
        CoverArtifact.create(b"a", b"b", "image/png", 2, 3).to_data()["measures"]
        is None
    )
    with pytest.raises(CoverEvidenceError):
        CoverArtifact.create(
            b"a",
            b"b",
            "image/png",
            2,
            3,
            measures=measures.to_data(),  # type: ignore[arg-type]  # reason: proves the runtime refusal
        )


_REFUSED = "d" * 64


@pytest.mark.parametrize(
    "state",
    sorted(DECODER_REJECTED_STATES),
    ids=[state.value for state in sorted(DECODER_REJECTED_STATES)],
)
def test_an_audiobookshelf_cover_the_decoder_refused_binds_its_digest(
    state: CoverSourceState,
) -> None:
    source = CoverSource(
        cover_source_id(CoverSourceKind.ABS, "descriptor"),
        CoverSourceKind.ABS,
        state,
        original_digest=_REFUSED,
    )
    assert source.to_data()["original_digest"] == _REFUSED
    assert source.bytes_digest == _REFUSED
    # The reader may refuse bytes before the decoder sees them: no digest.
    assert (
        CoverSource(source.source_id, CoverSourceKind.ABS, state).bytes_digest is None
    )


def test_a_retrieved_source_is_bound_by_its_artifact_alone() -> None:
    artifact = CoverArtifact.create(b"original", b"preview", "image/png", 1, 1)
    source = CoverSource(
        cover_source_id(CoverSourceKind.ABS, "descriptor"),
        CoverSourceKind.ABS,
        CoverSourceState.RETRIEVED,
        artifact,
    )
    assert source.to_data()["original_digest"] is None
    assert source.bytes_digest == artifact.original_digest
    with pytest.raises(CoverEvidenceError):
        CoverSource(
            source.source_id,
            CoverSourceKind.ABS,
            CoverSourceState.RETRIEVED,
            artifact,
            original_digest=artifact.original_digest,
        )


@pytest.mark.parametrize(
    ("kind", "state", "digest"),
    (
        (CoverSourceKind.ABS, CoverSourceState.TIMEOUT, _REFUSED),
        (CoverSourceKind.ABS, CoverSourceState.UNAVAILABLE, _REFUSED),
        (CoverSourceKind.ABS, CoverSourceState.SOURCE_CHANGED, _REFUSED),
        (CoverSourceKind.ABS, CoverSourceState.ABSENT, _REFUSED),
        (CoverSourceKind.EPUB, CoverSourceState.DECODE_FAILED, _REFUSED),
        (CoverSourceKind.ABS, CoverSourceState.DECODE_FAILED, "D" * 64),
        (CoverSourceKind.ABS, CoverSourceState.DECODE_FAILED, "d" * 63),
    ),
    ids=(
        "timeout",
        "unavailable",
        "source_changed",
        "absent",
        "epub",
        "upper_case",
        "short",
    ),
)
def test_only_refused_audiobookshelf_bytes_carry_a_digest(
    kind: CoverSourceKind, state: CoverSourceState, digest: str
) -> None:
    with pytest.raises(CoverEvidenceError):
        CoverSource(
            cover_source_id(kind, "descriptor"),
            kind,
            state,
            timeout_scope=("attempted" if state is CoverSourceState.TIMEOUT else None),
            original_digest=digest,
        )


_RETAINED_PLAN = "effect_plan_" + "a" * 64


@pytest.mark.parametrize(
    "state",
    sorted(DECODER_REJECTED_STATES),
    ids=[state.value for state in sorted(DECODER_REJECTED_STATES)],
)
def test_a_retained_original_the_decoder_refused_is_kept_by_its_digest(
    state: CoverSourceState,
) -> None:
    source = CoverSource(
        cover_source_id(CoverSourceKind.RETAINED, _RETAINED_PLAN),
        CoverSourceKind.RETAINED,
        state,
        original_digest=_REFUSED,
    )

    assert source.artifact is None
    assert source.bytes_digest == _REFUSED
    assert source.to_data()["original_digest"] == _REFUSED
    assert publication_ready((source,)) is True


@pytest.mark.parametrize(
    ("kind", "state", "digest", "artifact"),
    (
        (CoverSourceKind.RETAINED, CoverSourceState.DECODE_FAILED, None, False),
        (CoverSourceKind.RETAINED, CoverSourceState.TIMEOUT, _REFUSED, False),
        (CoverSourceKind.RETAINED, CoverSourceState.UNAVAILABLE, None, False),
        (CoverSourceKind.RETAINED, CoverSourceState.RETRIEVED, _REFUSED, True),
        (CoverSourceKind.OPEN_LIBRARY, CoverSourceState.DECODE_FAILED, _REFUSED, False),
        (CoverSourceKind.HARDCOVER, CoverSourceState.OVERSIZED, _REFUSED, False),
    ),
    ids=(
        "retained_refused_without_digest",
        "retained_transient",
        "retained_unavailable",
        "retained_artifact_and_digest",
        "open_library_digest",
        "hardcover_digest",
    ),
)
def test_only_a_refused_retained_or_audiobookshelf_source_carries_a_digest(
    kind: CoverSourceKind,
    state: CoverSourceState,
    digest: str | None,
    artifact: bool,
) -> None:
    with pytest.raises(CoverEvidenceError):
        CoverSource(
            cover_source_id(kind, _RETAINED_PLAN),
            kind,
            state,
            (
                CoverArtifact.create(b"original", b"preview", "image/png", 1, 1)
                if artifact
                else None
            ),
            timeout_scope=("attempted" if state is CoverSourceState.TIMEOUT else None),
            provenance=(
                None
                if kind is CoverSourceKind.RETAINED
                else CoverProvenance("provider", CoverDesignation.WORK, "work-1")
            ),
            original_digest=digest,
        )
