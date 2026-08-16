"""Metadata-report identifier evidence: provider-local work relations,
unresolved collisions, tamper rejection, and the candidate
finalization that promotes only safe manifestation dates.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    DateLinkConfidence,
    FieldState,
    FileFacts,
    IdentifierEvidenceBasis,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    PublicationDateRelation,
    RearClaimProvenance,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    comparison_links,
    default_target_fields,
    derive_identifier_evidence,
    finalize_candidate_comparisons,
    manifestation_publication_compatible,
)


@pytest.fixture
def local_isbn() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="unknown",
        checksum="valid_isbn_13",
        applicability_basis="unlabelled",
        scope=ClaimScope.EDITION,
    )


@pytest.fixture
def local_date() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2021-09",
        canonical="2021-09",
        excerpt="2021-09",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.MONTH,
    )


@pytest.fixture
def provider_isbn() -> ProviderClaim:
    return ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.ISBN,
        value="9780306406157",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="unknown",
    )


@pytest.fixture
def date_text() -> ProviderEvidence:
    return ProviderEvidence(
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
        "2021-09-14",
        ClaimScope.EDITION,
    )


@pytest.fixture
def candidate(
    provider_isbn: ProviderClaim, date_text: ProviderEvidence
) -> ProviderCandidate:
    return ProviderCandidate(
        "edition-1",
        "work-1",
        (provider_isbn,),
        (date_text,),
    )


@pytest.fixture
def local(local_isbn: LocalClaim, local_date: LocalClaim) -> tuple[LocalClaim, ...]:
    return (local_isbn, local_date)


@pytest.fixture
def unbound_report(candidate: ProviderCandidate) -> MetadataReport:
    target_fields = tuple(
        (
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
        )
        for field in default_target_fields("Target title")
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            target_fields,
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
                query_kind=ProviderQueryKind.WORK,
            ),
        ),
    )


@pytest.fixture
def contradictory_candidate(
    candidate: ProviderCandidate, date_text: ProviderEvidence
) -> ProviderCandidate:
    return replace(
        candidate,
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                "work-1",
                ClaimScope.EDITION,
            ),
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                "contradictory_or_multiple",
                ClaimScope.EDITION,
                source_ordinal=1,
            ),
            replace(date_text, source_ordinal=2),
        ),
    )


def test_candidate_finalization_promotes_only_safe_manifestation_dates(
    candidate: ProviderCandidate,
    local: tuple[LocalClaim, ...],
    local_isbn: LocalClaim,
    date_text: ProviderEvidence,
) -> None:
    assert manifestation_publication_compatible(candidate, local)
    safe = finalize_candidate_comparisons(candidate, local, "catalogue")
    publication = next(
        claim for claim in safe.claims if claim.kind is ClaimKind.PUBLICATION_DATE
    )
    assert "2021-09-14" == publication.date_lexical
    assert (
        PublicationDateRelation.MONTH_COMPATIBLE
        == publication.links[0].publication_date_relation
    )
    assert DatePrecision.MONTH == publication.links[0].shared_date_precision
    assert not any(
        evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        for evidence in safe.evidence
    )

    print_candidate = replace(
        candidate,
        evidence=(
            ProviderEvidence(
                ProviderEvidenceKind.PHYSICAL_FORMAT,
                "Hardcover",
                ClaimScope.EDITION,
                source_ordinal=1,
            ),
            date_text,
        ),
    )
    unsafe = finalize_candidate_comparisons(print_candidate, local, "catalogue")
    assert not any(claim.kind is ClaimKind.PUBLICATION_DATE for claim in unsafe.claims)
    retained = next(
        evidence
        for evidence in unsafe.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        == retained.links[0].publication_date_relation
    )

    unsafe_without_local_date = finalize_candidate_comparisons(
        print_candidate, (local_isbn,), "catalogue"
    )
    retained = next(
        evidence
        for evidence in unsafe_without_local_date.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        == retained.links[0].publication_date_relation
    )


def test_title_only_local_claims_keep_manifestation_dates_unpromoted(
    candidate: ProviderCandidate,
) -> None:
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Local title",
        canonical="Local title",
        excerpt="Local title",
        applicability="not_applicable",
        basis="package_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    title_only = finalize_candidate_comparisons(
        candidate,
        (local_title,),
        "catalogue",
    )
    retained = next(
        evidence
        for evidence in title_only.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (
        local_title.reference,
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
    ) == (
        retained.links[0].local_reference,
        retained.links[0].publication_date_relation,
    )
    title_report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(local_title,),
        local_reasons=("package_metadata_title",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )
    assert title_report == MetadataReport.from_json(title_report.to_json())
    tampered_title_report = title_report.to_data()
    tampered_title_report["providers"][0]["candidates"][0]["evidence"][0]["links"] = []
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(tampered_title_report))


def test_unbound_manifestation_link_is_recorded_without_local_claims(
    candidate: ProviderCandidate,
    unbound_report: MetadataReport,
) -> None:
    unbound = finalize_candidate_comparisons(
        candidate,
        (),
        "catalogue",
        allow_unbound_manifestation_link=True,
    )
    retained = next(
        evidence
        for evidence in unbound.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (
        None,
        Comparison.NOT_COMPARABLE,
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
    ) == (
        retained.links[0].local_reference,
        retained.links[0].outcome,
        retained.links[0].publication_date_relation,
    )
    assert unbound_report == MetadataReport.from_json(unbound_report.to_json())


@pytest.mark.parametrize(
    "evidence",
    (
        ProviderEvidence(
            ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
            "not-a-date",
            ClaimScope.EDITION,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_confidence=DateLinkConfidence.UNRESOLVED,
        ),
        ProviderEvidence(
            ProviderEvidenceKind.COPYRIGHT_DATE,
            "2001",
            ClaimScope.EDITION,
        ),
        ProviderEvidence(
            ProviderEvidenceKind.LAST_MODIFIED,
            "2001-02-03",
            ClaimScope.EDITION,
        ),
    ),
    ids=("publication_date_text", "copyright_date", "last_modified"),
)
def test_unlinkable_evidence_rejects_the_unbound_manifestation_link(
    evidence: ProviderEvidence,
    candidate: ProviderCandidate,
    unbound_report: MetadataReport,
) -> None:
    unbound_link = unbound_report.providers[0].candidates[0].evidence[0].links[0]

    def target_report(evidence: ProviderEvidence) -> MetadataReport:
        return MetadataReport.create(
            target=unbound_report.target,
            file=unbound_report.file,
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    candidates=(replace(candidate, evidence=(evidence,)),),
                    query_kind=ProviderQueryKind.WORK,
                ),
            ),
        )

    with pytest.raises(ReportValueError):
        replace(evidence, links=(unbound_link,))
    sanitized = target_report(evidence)
    assert () == sanitized.providers[0].candidates[0].evidence[0].links
    forged_report = unbound_report.to_data()
    forged_evidence = evidence.to_data()
    forged_evidence["links"] = [unbound_link.to_data()]
    forged_report["providers"][0]["candidates"][0]["evidence"] = [forged_evidence]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(forged_report))


def test_unbound_manifestation_evidence_rejects_contradiction_and_tampering(
    unbound_report: MetadataReport,
) -> None:
    valid_unbound_evidence = unbound_report.providers[0].candidates[0].evidence[0]
    with pytest.raises(ReportValueError):
        replace(
            valid_unbound_evidence,
            date_event=DateEvent.UNKNOWN,
        )
    contradictory_report = unbound_report.to_data()
    contradictory_evidence = valid_unbound_evidence.to_data()
    contradictory_evidence["date_event"] = DateEvent.UNKNOWN.value
    contradictory_report["providers"][0]["candidates"][0]["evidence"] = [
        contradictory_evidence
    ]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(contradictory_report))

    tampered_unbound_report = unbound_report.to_data()
    tampered_unbound_report["providers"][0]["candidates"][0]["evidence"][0][
        "links"
    ] = []
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(tampered_unbound_report))
    non_work_report = unbound_report.to_data()
    non_work_report["providers"][0]["query_kind"] = "none"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(non_work_report))
    with pytest.raises(ReportValueError):
        ComparisonLink(
            None,
            Comparison.NOT_COMPARABLE,
            publication_date_relation=(PublicationDateRelation.EVENT_INCOMPATIBLE),
        )


def test_work_scoped_first_publication_date_is_event_incompatible(
    candidate: ProviderCandidate,
    local_isbn: LocalClaim,
) -> None:
    work_first = finalize_candidate_comparisons(
        replace(
            candidate,
            evidence=(
                ProviderEvidence(
                    ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                    "2021",
                    ClaimScope.WORK,
                ),
            ),
        ),
        (local_isbn,),
        "catalogue",
    )
    assert (
        PublicationDateRelation.EVENT_INCOMPATIBLE
        == work_first.evidence[0].links[0].publication_date_relation
    )


def test_edition_work_relation_contradiction_keeps_dates_unpromoted(
    candidate: ProviderCandidate,
    contradictory_candidate: ProviderCandidate,
    local: tuple[LocalClaim, ...],
    provider_isbn: ProviderClaim,
) -> None:
    assert not manifestation_publication_compatible(contradictory_candidate, local)
    contradictory = finalize_candidate_comparisons(
        contradictory_candidate, local, "catalogue"
    )
    assert any(
        evidence.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
        for evidence in contradictory.evidence
    )
    retained = next(
        evidence
        for evidence in contradictory.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert (
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        == retained.links[0].publication_date_relation
    )
    with pytest.raises(ReportValueError):
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
            "work-2",
            ClaimScope.EDITION,
        )
    with pytest.raises(ReportValueError):
        replace(
            candidate,
            evidence=(
                ProviderEvidence(
                    ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                    "contradictory_or_multiple",
                    ClaimScope.EDITION,
                ),
            ),
        )
    with pytest.raises(ReportValueError):
        replace(
            contradictory_candidate,
            evidence=(
                ProviderEvidence(
                    ProviderEvidenceKind.EDITION_WORK_RELATION,
                    "work-2",
                    ClaimScope.EDITION,
                ),
            ),
        )
    work_title = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.TITLE,
        value="Unrelated work title",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        raw_order=1,
    )
    with pytest.raises(ReportValueError):
        replace(
            contradictory_candidate,
            claims=(provider_isbn, work_title),
        )


@pytest.mark.parametrize(
    "work_evidence",
    (
        ProviderEvidence(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            "1900",
            ClaimScope.WORK,
            source_ordinal=3,
        ),
        ProviderEvidence(
            ProviderEvidenceKind.WORK_COVER_ID,
            "cover-1",
            ClaimScope.WORK,
            source_ordinal=3,
        ),
    ),
    ids=("first_publication_date", "work_cover_id"),
)
def test_work_scoped_evidence_is_refused_on_a_contradictory_candidate(
    work_evidence: ProviderEvidence,
    contradictory_candidate: ProviderCandidate,
) -> None:
    with pytest.raises(ReportValueError):
        replace(
            contradictory_candidate,
            evidence=(*contradictory_candidate.evidence, work_evidence),
        )


def test_work_scoped_creator_binding_is_refused_on_a_contradictory_candidate(
    contradictory_candidate: ProviderCandidate,
    provider_isbn: ProviderClaim,
) -> None:
    work_creator = ProviderClaim.create(
        provider="catalogue",
        candidate="edition-1",
        kind=ClaimKind.CREATOR,
        value="Ada Example",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        role="author",
        raw_order=1,
    )
    with pytest.raises(ReportValueError):
        replace(
            contradictory_candidate,
            claims=(provider_isbn, work_creator),
            identity_bindings=(
                ProviderIdentityBinding(
                    work_creator.reference,
                    "author-1",
                    ClaimScope.WORK,
                    "author",
                    0,
                ),
            ),
        )


def test_report_rejects_appended_work_scoped_evidence(
    contradictory_candidate: ProviderCandidate,
    local: tuple[LocalClaim, ...],
) -> None:
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(None),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local,
        local_reasons=("no_opening_claims",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(contradictory_candidate,),
            ),
        ),
    )
    tampered = report.to_data()
    tampered["providers"][0]["candidates"][0]["evidence"].append(
        ProviderEvidence(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            "1900",
            ClaimScope.WORK,
            source_ordinal=3,
        ).to_data()
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(tampered))


def test_identifier_evidence_classifies_provider_local_work_relations() -> None:
    def local(
        value: str,
        *,
        basis: str,
        applicability: str,
        applicability_basis: str,
        occurrence: int,
    ) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability=applicability,
            basis=basis,
            checksum=f"valid_isbn_{len(value)}",
            applicability_basis=applicability_basis,
            scope=ClaimScope.EDITION,
            occurrence=occurrence,
        )

    package = local(
        "9780306406157",
        basis="package_metadata",
        applicability="unknown",
        applicability_basis="unlabelled",
        occurrence=1,
    )
    opening = local(
        "0306406152",
        basis="opening_matter",
        applicability="ebook",
        applicability_basis="ebook",
        occurrence=2,
    )
    same_work_print = local(
        "9780140328721",
        basis="opening_matter",
        applicability="print",
        applicability_basis="paperback",
        occurrence=3,
    )
    other_work = local(
        "9783161484100",
        basis="opening_matter",
        applicability="unknown",
        applicability_basis="unlabelled",
        occurrence=4,
    )
    unresolved = local(
        "9781861972712",
        basis="opening_matter",
        applicability="ambiguous",
        applicability_basis="ambiguous",
        occurrence=5,
    )
    malformed = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406158",
        canonical=None,
        excerpt="9780306406158",
        applicability="ebook",
        basis="opening_matter",
        checksum="invalid",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
        state=FieldState.MALFORMED,
        occurrence=6,
    )
    local_claims = (
        package,
        opening,
        same_work_print,
        other_work,
        unresolved,
        malformed,
    )

    def provider_claim(
        candidate: str,
        value: str,
        raw_order: int,
    ) -> ProviderClaim:
        bare = ProviderClaim.create(
            provider="catalogue",
            candidate=candidate,
            kind=ClaimKind.ISBN,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
            raw_order=raw_order,
        )
        return ProviderClaim.create(
            provider="catalogue",
            candidate=candidate,
            kind=ClaimKind.ISBN,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
            raw_order=raw_order,
            links=comparison_links(bare, local_claims),
        )

    seed = provider_claim("edition-seed", "9780306406157", 0)
    alternate = provider_claim("edition-alternate", "9780140328721", 0)
    unrelated = provider_claim("edition-unrelated", "9783161484100", 0)
    providers = (
        ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            (
                ProviderCandidate(
                    "edition-alternate",
                    "work-one",
                    (alternate,),
                ),
                ProviderCandidate("edition-seed", "work-one", (seed,)),
                ProviderCandidate(
                    "edition-unrelated",
                    "work-two",
                    (unrelated,),
                ),
            ),
        ),
    )

    evidence = derive_identifier_evidence(local_claims, providers)
    by_reference = {item.reference: item for item in evidence}
    assert {item.reference for item in evidence} == {
        *(claim.reference for claim in local_claims),
        seed.reference,
        alternate.reference,
        unrelated.reference,
    }
    assert by_reference[package.reference].relation == IdentifierRelation.ASSOCIATED
    assert by_reference[opening.reference].relation == IdentifierRelation.ACCEPTED
    assert (
        by_reference[package.reference].basis
        == IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY
    )
    assert (
        by_reference[opening.reference].basis == IdentifierEvidenceBasis.EXPLICIT_EBOOK
    )
    assert (
        by_reference[same_work_print.reference].relation
        == IdentifierRelation.ASSOCIATED
    )
    assert (
        by_reference[same_work_print.reference].basis
        == IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY
    )
    assert by_reference[other_work.reference].relation == IdentifierRelation.UNRELATED
    assert by_reference[unresolved.reference].relation == IdentifierRelation.UNRESOLVED
    assert by_reference[malformed.reference].relation == IdentifierRelation.INVALID
    assert by_reference[seed.reference].relation == IdentifierRelation.ACCEPTED
    assert by_reference[alternate.reference].relation == IdentifierRelation.ASSOCIATED
    assert by_reference[unrelated.reference].relation == IdentifierRelation.UNRELATED
    assert by_reference[alternate.reference].provider == "catalogue"
    assert by_reference[alternate.reference].candidate_identity == "edition-alternate"
    assert by_reference[alternate.reference].work_identity == "work-one"
    assert set(by_reference[alternate.reference].to_data()) == {
        "basis",
        "candidate_identity",
        "provider",
        "reference",
        "related_references",
        "relation",
        "work_identity",
    }
    assert {
        "observed",
        "canonical",
        "excerpt",
        "resource_identity",
    }.isdisjoint(by_reference[alternate.reference].to_data())

    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=("opening_matter_isbn",),
        providers=providers,
    )
    assert report.identifier_evidence == evidence
    assert MetadataReport.from_json(report.to_json()) == report
    assert report.local_claims == local_claims


def test_identifier_evidence_keeps_provider_work_collisions_unresolved() -> None:
    accepted = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
        occurrence=1,
    )
    alternate = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780140328721",
        canonical="9780140328721",
        excerpt="9780140328721",
        applicability="print",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="paperback",
        scope=ClaimScope.EDITION,
        occurrence=2,
    )

    def provider_claim(
        provider: str,
        candidate: str,
        value: str,
        raw_order: int,
    ) -> ProviderClaim:
        return ProviderClaim.create(
            provider=provider,
            candidate=candidate,
            kind=ClaimKind.ISBN,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
            raw_order=raw_order,
        )

    collision_seed_a = provider_claim("collision", "edition-a", "9780306406157", 0)
    collision_alternate_a = provider_claim("collision", "edition-a", "9780140328721", 1)
    collision_seed_b = provider_claim("collision", "edition-b", "9780306406157", 0)
    collision_alternate_b = provider_claim("collision", "edition-b", "9783161484100", 1)
    collision = ProviderObservation(
        "collision",
        ProviderStatus.CANDIDATES,
        (),
        (
            ProviderCandidate(
                "edition-a",
                "work-a",
                (collision_seed_a, collision_alternate_a),
            ),
            ProviderCandidate(
                "edition-b",
                "work-b",
                (collision_seed_b, collision_alternate_b),
            ),
        ),
    )

    collision_evidence = {
        item.reference: item
        for item in derive_identifier_evidence(
            (accepted, alternate),
            (collision,),
        )
    }
    assert (
        collision_evidence[alternate.reference].relation
        == IdentifierRelation.UNRESOLVED
    )
    assert (
        collision_evidence[collision_alternate_a.reference].relation
        == IdentifierRelation.UNRESOLVED
    )
    assert (
        collision_evidence[collision_alternate_b.reference].relation
        == IdentifierRelation.UNRESOLVED
    )

    independent_seed = provider_claim("independent", "edition-c", "9780306406157", 0)
    independent_alternate = provider_claim(
        "independent", "edition-c", "9780140328721", 1
    )
    independent = ProviderObservation(
        "independent",
        ProviderStatus.CANDIDATES,
        (),
        (
            ProviderCandidate(
                "edition-c",
                "work-c",
                (independent_seed, independent_alternate),
            ),
        ),
    )
    combined_evidence = {
        item.reference: item
        for item in derive_identifier_evidence(
            (accepted, alternate),
            (collision, independent),
        )
    }
    assert (
        combined_evidence[independent_alternate.reference].relation
        == IdentifierRelation.ASSOCIATED
    )
    assert (
        combined_evidence[alternate.reference].relation == IdentifierRelation.ASSOCIATED
    )


def test_identifier_evidence_preserves_repeats_conflicts_and_rejects_tampering() -> (
    None
):
    provenance = RearClaimProvenance(
        "a" * 64,
        12,
        1,
        "copyright",
        "Copyright",
    )
    claims = (
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="9780306406157",
            applicability="unknown",
            basis="package_metadata",
            checksum="valid_isbn_13",
            applicability_basis="unlabelled",
            occurrence=1,
        ),
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="unknown",
            basis="opening_matter",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
            rear_provenance=provenance,
            occurrence=2,
        ),
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="unknown",
            basis="opening_matter",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
            rear_provenance=provenance,
            occurrence=3,
        ),
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="9780140328721",
            applicability="ambiguous",
            basis="opening_matter",
            checksum="valid_isbn_13",
            applicability_basis="ambiguous",
            rear_provenance=provenance,
            occurrence=4,
        ),
    )
    evidence = derive_identifier_evidence(claims, ())
    assert len(evidence) == len(claims)
    assert [item.relation for item in evidence] == [
        IdentifierRelation.ACCEPTED,
        IdentifierRelation.ACCEPTED,
        IdentifierRelation.ACCEPTED,
        IdentifierRelation.UNRESOLVED,
    ]
    primary_package = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9783161484100",
        canonical="9783161484100",
        excerpt="9783161484100",
        applicability="ebook",
        basis="package_metadata",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        occurrence=5,
    )
    primary_evidence = derive_identifier_evidence((primary_package,), ())[0]
    assert (primary_evidence.relation, primary_evidence.basis) == (
        IdentifierRelation.ACCEPTED,
        IdentifierEvidenceBasis.EXPLICIT_EBOOK,
    )
    inferred_package = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9783161484100",
        canonical="9783161484100",
        excerpt="9783161484100",
        applicability="unknown",
        basis="package_metadata",
        checksum="valid_isbn_13",
        applicability_basis="unlabelled",
        occurrence=6,
    )
    inferred_evidence = derive_identifier_evidence((inferred_package,), ())[0]
    assert (inferred_evidence.relation, inferred_evidence.basis) == (
        IdentifierRelation.UNRESOLVED,
        IdentifierEvidenceBasis.INSUFFICIENT_CONTEXT,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=claims,
        local_reasons=("opening_matter_isbn",),
        providers=(),
    )
    assert [claim.to_data() for claim in report.local_claims] == [
        claim.to_data() for claim in claims
    ]
    data = report.to_data()
    data["identifier_evidence"][0]["relation"] = "associated"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))


def test_identifier_evidence_is_claim_local_under_applicability_conflicts() -> None:
    def claim(
        value: str,
        *,
        basis: str,
        applicability: str,
        applicability_basis: str,
        occurrence: int,
    ) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability=applicability,
            basis=basis,
            checksum="valid_isbn_13",
            applicability_basis=applicability_basis,
            occurrence=occurrence,
        )

    secondary_package = claim(
        "9780306406157",
        basis="package_metadata",
        applicability="unknown",
        applicability_basis="unlabelled",
        occurrence=1,
    )
    paperback = claim(
        "9780306406157",
        basis="opening_matter",
        applicability="print",
        applicability_basis="paperback",
        occurrence=2,
    )
    primary_ebook = claim(
        "9780140328721",
        basis="package_metadata",
        applicability="ebook",
        applicability_basis="ebook",
        occurrence=3,
    )
    primary_print = claim(
        "9780140328721",
        basis="opening_matter",
        applicability="print",
        applicability_basis="hardcover",
        occurrence=4,
    )
    unknown = claim(
        "9783161484100",
        basis="package_metadata",
        applicability="unknown",
        applicability_basis="unlabelled",
        occurrence=5,
    )
    explicit_ebook = claim(
        "9783161484100",
        basis="opening_matter",
        applicability="ebook",
        applicability_basis="ebook",
        occurrence=6,
    )
    ambiguous = claim(
        "9783161484100",
        basis="opening_matter",
        applicability="ambiguous",
        applicability_basis="ambiguous",
        occurrence=7,
    )
    explicit_print = claim(
        "9783161484100",
        basis="opening_matter",
        applicability="print",
        applicability_basis="print",
        occurrence=8,
    )
    evidence = {
        item.reference: item
        for item in derive_identifier_evidence(
            (
                secondary_package,
                paperback,
                primary_ebook,
                primary_print,
                unknown,
                explicit_ebook,
                ambiguous,
                explicit_print,
            ),
            (),
        )
    }
    assert {
        claim.reference: evidence[claim.reference].relation
        for claim in (
            secondary_package,
            paperback,
            primary_ebook,
            primary_print,
            unknown,
            explicit_ebook,
            ambiguous,
            explicit_print,
        )
    } == {
        secondary_package.reference: IdentifierRelation.UNRESOLVED,
        paperback.reference: IdentifierRelation.UNRESOLVED,
        primary_ebook.reference: IdentifierRelation.ACCEPTED,
        primary_print.reference: IdentifierRelation.UNRESOLVED,
        unknown.reference: IdentifierRelation.UNRESOLVED,
        explicit_ebook.reference: IdentifierRelation.ACCEPTED,
        ambiguous.reference: IdentifierRelation.UNRESOLVED,
        explicit_print.reference: IdentifierRelation.UNRESOLVED,
    }
    assert (
        evidence[primary_ebook.reference].basis
        == IdentifierEvidenceBasis.EXPLICIT_EBOOK
    )
    assert (
        evidence[explicit_ebook.reference].basis
        == IdentifierEvidenceBasis.EXPLICIT_EBOOK
    )
