"""A Hardcover listed-edition cover binds by work identity and stays unknown.

#1380 (S15) and ADR-0096: a cover read from an accepted book's edition list
binds to that book's work identity alone, and its only eligible outcome is
ADR-0096's title-and-creator work match, which #1355's activation reads from
the shadow reasons recorded here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest

from mechanical_librarian.application import cover_identity
from mechanical_librarian.application.cover_identity import (
    CLASSIFIER_REFERENCE,
    _classify_sources,
    classify_cover_identity,
)
from mechanical_librarian.application.cover_packets import prepare_cover_packet
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverContext,
    CoverContextField,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    FileFacts,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    provider_lane_candidate_reference,
)
from mechanical_librarian.domain.work_identity_levels import level_admission
from tests.support.cover_producers import _preview

_BOOK = "hardcover-work-0000000007"
_EDITION = "hardcover-edition-0000000099"
_ISBN_EDITION = "hardcover-edition-0000000005"


def _listed_source(
    index: int = 3, *, edition: str = _EDITION, work: str = _BOOK
) -> CoverSource:
    return CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, f"hardcover:listed:{index}"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            f"listed-{index}".encode(), _preview(index), "image/png", 400, 600
        ),
        provenance=CoverProvenance(
            "hardcover",
            CoverDesignation.LISTED_EDITION,
            work,
            edition,
            (CoverContext(CoverContextField.LANGUAGE, "en"),),
        ),
    )


def _default_source(index: int = 4) -> CoverSource:
    return CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, f"hardcover:work:{index}"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        CoverArtifact.create(
            f"default-{index}".encode(), _preview(index), "image/png", 400, 600
        ),
        provenance=CoverProvenance("hardcover", CoverDesignation.DEFAULT, _BOOK),
    )


def _claim(
    kind: ClaimKind,
    value: str,
    *,
    raw_order: int = 0,
    scope: ClaimScope = ClaimScope.WORK,
    state: FieldState = FieldState.VALID,
    links: tuple[object, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        kind=kind,
        value=value,
        raw_order=raw_order,
        scope=scope,
        state=state,
        links=links,
    )


def _candidate(
    title: str | None = "Synthetic Title",
    creators: tuple[str, ...] = ("Ada Author",),
    *,
    identity: str = _BOOK,
    creator_links: tuple[object, ...] = (),
    evidence: tuple[object, ...] = (),
    extra: tuple[SimpleNamespace, ...] = (),
) -> SimpleNamespace:
    claims = (
        *(() if title is None else (_claim(ClaimKind.TITLE, title),)),
        *(
            _claim(ClaimKind.CREATOR, value, raw_order=index + 1, links=creator_links)
            for index, value in enumerate(creators)
        ),
        *extra,
    )
    return SimpleNamespace(
        identity=identity, work_identity=_BOOK, claims=claims, evidence=evidence
    )


def _target(
    title: str = "Synthetic Title", authors: tuple[str, ...] | None = ("Ada Author",)
) -> TargetFacts:
    fields = []
    for destination in TargetDestination:
        if destination is TargetDestination.TITLE:
            fields.append(TargetField(destination, TargetFieldState.VALUE, title))
        elif destination is TargetDestination.AUTHORS and authors is not None:
            fields.append(TargetField(destination, TargetFieldState.VALUE, authors))
        else:
            fields.append(TargetField(destination, TargetFieldState.MISSING))
    return TargetFacts("synthetic", "books", "item", tuple(fields))


def _report(
    *candidates: SimpleNamespace,
    target: TargetFacts | None = None,
    identifier_evidence: tuple[object, ...] = (),
    lanes: tuple[object, ...] = (),
) -> MetadataReport:
    # A duck-typed stand-in: the classifier reads only these members.
    return cast(
        "MetadataReport",
        SimpleNamespace(
            audio=None,
            target=_target() if target is None else target,
            providers=(
                SimpleNamespace(
                    provider="hardcover",
                    candidates=candidates,
                    query_kind=ProviderQueryKind.DISCOVERY,
                    lanes=lanes,
                ),
            ),
            identifier_evidence=identifier_evidence,
            local_claims=(),
        ),
    )


def _identifier(relation: IdentifierRelation, identity: str = _BOOK) -> object:
    return SimpleNamespace(
        provider="hardcover",
        candidate_identity=identity,
        work_identity=_BOOK,
        relation=relation,
        reference=f"identifier-{relation.value}",
    )


def test_a_listed_edition_binds_by_the_book_alone_at_the_exact_level() -> None:
    assert _classify_sources(_report(_candidate()), (_listed_source(),)) == (
        "unknown",
        ["listed_edition_level_shadow", "work_identity:title_and_creator_exact"],
        [],
    )


def test_a_listed_edition_records_the_wider_level_it_meets() -> None:
    report = _report(
        _candidate("Synthetic Title: A Novel", ("Ada Author",)),
        target=_target(authors=("Ada Author", "Bea Author")),
    )
    assert _classify_sources(report, (_listed_source(),)) == (
        "unknown",
        ["listed_edition_level_shadow", "work_identity:title_and_creator"],
        [],
    )


def test_a_listed_edition_by_a_different_creator_is_refused() -> None:
    report = _report(_candidate(creators=("Different Author",)))
    assert _classify_sources(report, (_listed_source(),)) == (
        "unknown",
        ["listed_edition_level_refused"],
        [],
    )


@pytest.mark.parametrize(
    ("target_title", "record_title"),
    (
        ("Synthetic Title", "Synthetic Title: The Complete Trilogy"),
        ("Synthetic Title: The Complete Trilogy", "Synthetic Title"),
    ),
    ids=("collection_record", "collection_target"),
)
def test_a_listed_edition_of_an_omnibus_against_a_volume_is_refused(
    target_title: str, record_title: str
) -> None:
    report = _report(_candidate(record_title), target=_target(target_title))
    with mock.patch.object(
        cover_identity, "level_admission", wraps=level_admission
    ) as call:
        assert _classify_sources(report, (_listed_source(),)) == (
            "unknown",
            ["listed_edition_level_refused"],
            [],
        )
    assert call.call_args.kwargs["omnibus_against_volume"] is True


def test_a_contradicted_listed_edition_cannot_be_chosen() -> None:
    disagreement = SimpleNamespace(outcome=Comparison.DISAGREEMENT)
    report = _report(_candidate(creator_links=(disagreement,)))
    assert _classify_sources(report, (_listed_source(),)) == (
        "unknown",
        ["retained_creator_or_contributor_disagreement"],
        [],
    )


def test_a_listed_edition_keeps_the_existing_conflicts() -> None:
    contradicted = _candidate(
        evidence=(
            SimpleNamespace(
                kind=ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                links=(),
            ),
        )
    )
    assert _classify_sources(_report(contradicted), (_listed_source(),)) == (
        "conflict",
        ["edition_work_contradiction"],
        [],
    )
    unrelated = _report(
        _candidate(),
        identifier_evidence=(_identifier(IdentifierRelation.UNRELATED),),
    )
    assert _classify_sources(unrelated, (_listed_source(),)) == (
        "conflict",
        ["unrelated_identifier"],
        ["identifier-unrelated"],
    )


def test_a_listed_edition_is_never_verified_or_plausible() -> None:
    agreement = SimpleNamespace(outcome=Comparison.AGREEMENT)
    edition = _candidate(
        identity=_ISBN_EDITION,
        extra=(_claim(ClaimKind.TITLE, "Synthetic Title", links=(agreement,)),),
    )
    lane = SimpleNamespace(
        kind=ProviderQueryKind.ISBN,
        candidates=(
            SimpleNamespace(edition_identity=_ISBN_EDITION, work_identity=_BOOK),
        ),
    )
    report = _report(
        edition,
        _candidate(),
        identifier_evidence=(
            _identifier(IdentifierRelation.ACCEPTED, _ISBN_EDITION),
            _identifier(IdentifierRelation.ACCEPTED),
        ),
        lanes=(lane,),
    )
    outcome, reasons, _evidence = _classify_sources(report, (_listed_source(),))
    assert (outcome, reasons) == (
        "unknown",
        ["listed_edition_level_shadow", "work_identity:title_and_creator_exact"],
    )


def test_the_strictest_level_of_one_books_candidates_is_recorded() -> None:
    report = _report(
        _candidate("Synthetic Title: A Novel", identity=_ISBN_EDITION),
        _candidate(),
    )
    assert _classify_sources(report, (_listed_source(),))[1] == [
        "listed_edition_level_shadow",
        "work_identity:title_and_creator_exact",
    ]


def test_a_target_with_no_author_refuses_a_listed_edition() -> None:
    report = _report(_candidate(), target=_target(authors=None))
    assert _classify_sources(report, (_listed_source(),)) == (
        "unknown",
        ["listed_edition_level_refused"],
        [],
    )


def test_only_the_stored_target_authors_and_valid_work_claims_reach_the_call() -> None:
    record = _candidate(
        creators=("Ada Author", "Ada Author"),
        extra=(
            _claim(ClaimKind.CREATOR, "Edition Person", scope=ClaimScope.EDITION),
            _claim(ClaimKind.CREATOR, "Unparsed Person", state=FieldState.UNSUPPORTED),
            _claim(ClaimKind.TITLE, "Edition Title", scope=ClaimScope.EDITION),
        ),
    )
    report = _report(record, target=_target(authors=("Author, Ada",)))
    with mock.patch.object(
        cover_identity, "level_admission", wraps=level_admission
    ) as call:
        assert _classify_sources(report, (_listed_source(),))[1] == [
            "listed_edition_level_refused"
        ]
    assert call.call_args.args[1:] == (
        "Synthetic Title",
        ("Author, Ada",),
        "Synthetic Title",
        ("Ada Author",),
    )
    assert call.call_args.kwargs == {
        "omnibus_against_volume": False,
        "contradicted": False,
    }


def test_a_mixed_candidate_keeps_its_other_sources_outcome() -> None:
    verified = _report(
        _candidate(),
        identifier_evidence=(_identifier(IdentifierRelation.ACCEPTED),),
    )
    assert _classify_sources(verified, (_default_source(), _listed_source()))[:2] == (
        "verified_work_match",
        ["accepted_or_associated_work_identifier"],
    )
    unbound_default = _report(_candidate(title="Other Title"))
    assert _classify_sources(
        unbound_default, (_default_source(), _listed_source())
    ) == ("unknown", ["retained_title_evidence_insufficient"], [])


def test_a_mixed_candidate_takes_a_listed_editions_conflict() -> None:
    report = _report(
        _candidate(),
        _candidate(identity=_ISBN_EDITION),
        identifier_evidence=(
            _identifier(IdentifierRelation.ACCEPTED),
            _identifier(IdentifierRelation.UNRELATED, _ISBN_EDITION),
        ),
    )
    assert _classify_sources(report, (_default_source(), _listed_source()))[:2] == (
        "conflict",
        ["unrelated_identifier"],
    )


def test_the_classifier_reference_names_the_listed_edition_rule() -> None:
    assert (
        "listed-edition-work-identity-binding:unknown-with-level-shadow"
        in cover_identity._CLASSIFIER_RULES
    )
    assert CLASSIFIER_REFERENCE == cover_identity._CLASSIFIER_REFERENCE


def _isbn_lane_report(source: CoverSource) -> MetadataReport:
    isbn = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="unknown",
        checksum="valid_isbn_13",
        applicability_basis="unlabelled",
    )
    book = ProviderCandidate(
        _BOOK,
        _BOOK,
        (
            ProviderClaim.create(
                provider="hardcover",
                candidate=_BOOK,
                kind=ClaimKind.TITLE,
                value="Synthetic Title",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider="hardcover",
                candidate=_BOOK,
                kind=ClaimKind.CREATOR,
                value="Ada Author",
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )
    lane_identity = "lane_" + "a" * 64
    lane = ProviderLaneObservation(
        lane_identity,
        ProviderQueryKind.ISBN,
        ProviderStatus.CANDIDATES,
        (isbn.reference,),
        "9780306406157",
        (
            ProviderLaneCandidate(
                provider_lane_candidate_reference(
                    "hardcover", lane_identity, _BOOK, _BOOK
                ),
                _BOOK,
                _BOOK,
            ),
        ),
    )
    return MetadataReport.create(
        target=_target(),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(isbn,),
        local_reasons=("package_metadata_creator",),
        providers=(
            ProviderObservation(
                "hardcover",
                ProviderStatus.CANDIDATES,
                (isbn.reference,),
                (book,),
                ProviderQueryKind.ISBN,
                "hardcover",
                lanes=(lane,),
            ),
        ),
        cover_sources=(source,),
    )


def test_a_listed_edition_report_binds_its_packet_and_identity_documents() -> None:
    """The dry proof: the packet's projection and the identity's agree."""
    source = _listed_source(11)
    payload = _preview(11)
    report = _isbn_lane_report(source)
    packet = prepare_cover_packet(report.to_json(), lambda *_: payload)
    images = tuple(
        (value.reference, value.digest, value.payload) for value in packet.images
    )
    document = json.loads(packet.document)
    (candidate,) = document["candidates"]
    assert [value["designation"] for value in candidate["provenance"]] == [
        "related_edition"
    ]
    identity = json.loads(
        classify_cover_identity(
            report.to_json(),
            packet.document,
            images,
            read_preview=lambda *_: payload,
        ).document
    )
    (row,) = identity["candidates"]
    assert row["outcome"] == "unknown"
    assert row["reasons"] == [
        "listed_edition_level_shadow",
        "work_identity:title_and_creator_exact",
    ]
