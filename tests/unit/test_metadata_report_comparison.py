"""Metadata-report comparison: closed title methods, conservative
creator order and alias collapse, and date relations that keep both
lexicals and their shared precision.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    ComparisonMethod,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderStatus,
    PublicationDateRelation,
    ReportValueError,
    TargetFacts,
    WorkIdentityContradiction,
    canonical_json,
    comparison_links,
    default_target_fields,
    finalize_candidate_comparisons,
    normalize_date_value,
    normalize_title,
    provider_candidate_work_identity_contradiction,
    publication_date_relation,
)


def _work_identity_candidate(
    title: str = "Example Book",
    authors: tuple[tuple[str, str | None], ...] = (("Ada Example", "author"),),
) -> ProviderCandidate:
    claims = (
        ProviderClaim.create(
            provider="catalogue",
            candidate="edition",
            kind=ClaimKind.TITLE,
            value=title,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        ),
        *(
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition",
                kind=ClaimKind.CREATOR,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=index + 1,
            )
            for index, (value, role) in enumerate(authors)
        ),
    )
    return ProviderCandidate("edition", "work", claims)


@pytest.mark.parametrize(
    ("candidate", "expected", "target_title"),
    (
        (
            _work_identity_candidate(),
            WorkIdentityContradiction.NOT_CONTRADICTED,
            "Example Book",
        ),
        (
            _work_identity_candidate("Plain Work"),
            WorkIdentityContradiction.NOT_CONTRADICTED,
            "Ada Example - Plain_Work",
        ),
        (
            _work_identity_candidate("Different Book"),
            WorkIdentityContradiction.EXPLICIT,
            "Example Book",
        ),
        (
            _work_identity_candidate(authors=()),
            WorkIdentityContradiction.UNAVAILABLE,
            "Example Book",
        ),
        (
            _work_identity_candidate(
                authors=(("Ada Example", "author"), ("Editor Example", None))
            ),
            WorkIdentityContradiction.UNAVAILABLE,
            "Example Book",
        ),
        (
            ProviderCandidate(
                "edition",
                "work",
                (
                    *_work_identity_candidate().claims,
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition",
                        kind=ClaimKind.TITLE,
                        value="Another Possible Title",
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        raw_order=2,
                    ),
                ),
            ),
            WorkIdentityContradiction.UNAVAILABLE,
            "Example Book",
        ),
        (
            _work_identity_candidate("Example Book"),
            WorkIdentityContradiction.UNAVAILABLE,
            "Example Book: A Novel",
        ),
        (
            _work_identity_candidate("Example Book: Abridged"),
            WorkIdentityContradiction.UNAVAILABLE,
            "Example Book",
        ),
    ),
    ids=(
        "applicable",
        "qualified_compound",
        "contradictory",
        "incomplete",
        "mixed_roles",
        "multiple_titles",
        "unqualified_query_match",
        "variant",
    ),
)
def test_work_identity_contradiction_requires_complete_unambiguous_evidence(
    candidate: ProviderCandidate,
    expected: WorkIdentityContradiction,
    target_title: str,
) -> None:
    assert expected is provider_candidate_work_identity_contradiction(
        candidate, target_title, ("Ada Example",)
    )


@pytest.mark.parametrize(
    ("target_authors", "provider_authors", "expected"),
    (
        (("Ada Example",), ("Ada L Example",), WorkIdentityContradiction.UNAVAILABLE),
        (("Ada L Example",), ("Ada Example",), WorkIdentityContradiction.UNAVAILABLE),
        (
            ("Ada L. Example",),
            ("Ada L Example",),
            WorkIdentityContradiction.UNAVAILABLE,
        ),
        (("Ada L Example",), ("Ada M Example",), WorkIdentityContradiction.EXPLICIT),
        (("Ada Example",), ("Other Author",), WorkIdentityContradiction.EXPLICIT),
        (
            ("Ada Example", "Bob B Example"),
            ("Bob Example", "Ada A Example"),
            WorkIdentityContradiction.UNAVAILABLE,
        ),
        (
            ("Ada Example", "Ada B Example"),
            ("Ada Example", "Ada B. Example"),
            WorkIdentityContradiction.UNAVAILABLE,
        ),
    ),
    ids=(
        "target_omits_middle",
        "provider_omits_middle",
        "optional_period",
        "unequal_initials",
        "disjoint",
        "permutation",
        "ambiguous_complete_pairings",
    ),
)
def test_work_identity_contradiction_uses_creator_display_only_for_negative_proof(
    target_authors: tuple[str, ...],
    provider_authors: tuple[str, ...],
    expected: WorkIdentityContradiction,
) -> None:
    candidate = _work_identity_candidate(
        authors=tuple((author, "author") for author in provider_authors)
    )

    assert expected is provider_candidate_work_identity_contradiction(
        candidate, "Example Book", target_authors
    )


def test_creator_display_possibility_requires_existing_title_query_overlap() -> None:
    assert WorkIdentityContradiction.EXPLICIT is (
        provider_candidate_work_identity_contradiction(
            _work_identity_candidate("Different Book", (("Ada L Example", "author"),)),
            "Example Book",
            ("Ada Example",),
        )
    )


def test_comparison_links_are_complete_per_local_claim_and_canonical() -> None:
    agree = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Shared title",
        canonical="Shared title",
        excerpt="Shared title",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    disagree = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Other title",
        canonical="Other title",
        excerpt="Other title",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    invalid = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="unsafe-source-text-sha256:" + "a" * 64,
        canonical=None,
        excerpt="unsafe-source-text-sha256:" + "a" * 64,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
        state=FieldState.UNSUPPORTED,
    )
    edition = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Shared title",
        canonical="Shared title",
        excerpt="Shared title",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    author = LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed="Ada",
        canonical="Ada",
        excerpt="Ada",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
        role="author",
    )
    provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.TITLE,
        value="Shared title",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    links = tuple(
        sorted(
            (
                ComparisonLink(
                    agree.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT
                ),
                ComparisonLink(disagree.reference, Comparison.DISAGREEMENT),
                ComparisonLink(invalid.reference, Comparison.NOT_COMPARABLE),
                ComparisonLink(edition.reference, Comparison.NOT_COMPARABLE),
            ),
            key=lambda link: cast(str, link.local_reference),
        )
    )
    linked = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.TITLE,
        value="Shared title",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        links=links,
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(agree, disagree, invalid, edition, author),
        local_reasons=("opening_title",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(
                    ProviderCandidate("candidate", "work-candidate", (linked,)),
                ),
            ),
        ),
    )
    changed_work = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-1", default_target_fields("Current title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(agree, disagree, invalid, edition, author),
        local_reasons=("opening_title",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(
                    ProviderCandidate("candidate", "different-work", (linked,)),
                ),
            ),
        ),
    )
    assert provider.links == ()
    assert {link.outcome for link in linked.links} == {
        Comparison.AGREEMENT,
        Comparison.DISAGREEMENT,
        Comparison.NOT_COMPARABLE,
    }
    assert author.reference not in {link.local_reference for link in linked.links}
    assert report.report_id != changed_work.report_id
    assert MetadataReport.from_json(changed_work.to_json()) == changed_work
    contributor = LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed="Ada",
        canonical="Ada",
        excerpt="Ada",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
        role="translator",
    )
    creator = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.CREATOR,
        value="Ada",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        role="author",
    )
    assert {
        link.local_reference: link.outcome
        for link in comparison_links(creator, (author, contributor))
    } == {
        author.reference: Comparison.AGREEMENT,
        contributor.reference: Comparison.NOT_COMPARABLE,
    }
    data = report.to_data()
    data["providers"][0]["candidates"][0]["claims"][0]["links"] = []
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))
    data = report.to_data()
    data["providers"][0]["candidates"][0]["claims"][0]["links"][0]["outcome"] = (
        "disagreement"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(data))


@pytest.mark.parametrize(
    ("provider_value", "local_value", "method"),
    (
        ("Exact Title", "Exact Title", ComparisonMethod.EXACT),
        (
            "The Road—Translated",
            "The Road—Translated",
            ComparisonMethod.EXACT,
        ),
        (
            "CAFE\u0301   SOCIETY",
            "café society",
            ComparisonMethod.UNICODE_CASE_WHITESPACE,
        ),
        (
            "The “Long\u00a0Road”—Home",
            'the "long road"-home',
            ComparisonMethod.TYPOGRAPHIC_PUNCTUATION,
        ),
    ),
    ids=(
        "exact",
        "em_dash_exact",
        "unicode_case_whitespace",
        "typographic_punctuation",
    ),
)
def test_title_comparison_methods_are_closed_and_preserve_diacritics(
    provider_value: str,
    local_value: str,
    method: ComparisonMethod,
) -> None:
    def local(value: str, kind: ClaimKind = ClaimKind.TITLE) -> LocalClaim:
        return LocalClaim.create(
            kind=kind,
            observed=value,
            canonical=normalize_title(value),
            excerpt=value,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )

    claim = local(local_value)
    provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.TITLE,
        value=provider_value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    assert comparison_links(provider, (claim,)) == (
        ComparisonLink(claim.reference, Comparison.AGREEMENT, method),
    )

    subtitle_claim = local("the road-abridgment", ClaimKind.SUBTITLE)
    subtitle_provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.SUBTITLE,
        value="The Road—Abridgment",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    assert (
        comparison_links(subtitle_provider, (subtitle_claim,))[0].outcome
        is Comparison.DISAGREEMENT
    )

    for provider_value, local_value in (
        ("Cafe", "Café"),
        ("The Road", "The Long Road"),
        ("The Road: Home", "The Road"),
        ("The Road (Translated)", "the road (translated)"),
        ("The Road—Translated", "the road-translated"),
        ("The Road–Translation", "the road-translation"),
        ("The Road—Adapted", "the road-adapted"),
        ("The Road–Adaptation", "the road-adaptation"),
        ("The Road—Variant", "the road-variant"),
        ("The Road–Abridged", "the road-abridged"),
        ("The Road—Abridgement", "the road-abridgement"),
        ("The Road–Abridgment", "the road-abridgment"),
        ("The Road “Translated”", 'the road "translated"'),
        ("The Road ‘Abridged’", "the road 'abridged'"),
        ("The Rod", "The Road"),
        ("The ‘Road", "the 'road"),
        ("The Road’", "the road'"),
        ("The ’Road‘", "the 'road'"),
        ("The “Road’", "the \"road'"),
        ("The ‘“Road’”", "the '\"road'\""),
        ('The "Road"', "the 'road'"),
    ):
        claim = local(local_value)
        provider = ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.TITLE,
            value=provider_value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )
        assert (
            comparison_links(provider, (claim,))[0].outcome == Comparison.DISAGREEMENT
        )

    for provider_value, local_value in (
        ("The ‘Road’", "the 'road'"),
        ("The “Road”", 'the "road"'),
        ("The “Long ‘Road’”", "the \"long 'road'\""),
    ):
        claim = local(local_value)
        provider = ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.TITLE,
            value=provider_value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )
        link = comparison_links(provider, (claim,))[0]
        assert link.outcome is Comparison.AGREEMENT
        assert link.method is ComparisonMethod.TYPOGRAPHIC_PUNCTUATION

    replay_local = local('The "Road"')
    replay_provider = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.TITLE,
        value="The “Road”",
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    replay_candidate = finalize_candidate_comparisons(
        ProviderCandidate("candidate", "work", (replay_provider,)),
        (replay_local,),
    )
    replay_report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(replay_local,),
        local_reasons=("opening_title",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(replay_candidate,),
            ),
        ),
    )
    reopened = MetadataReport.from_json(replay_report.to_json())
    assert reopened == replay_report
    assert (
        reopened.providers[0].candidates[0].claims[0].links[0].method
        is ComparisonMethod.TYPOGRAPHIC_PUNCTUATION
    )


@pytest.mark.parametrize(
    "values",
    (
        ("Ada Example",),
        ("Bob Example", "Ada Example"),
        ("Ada Example", "Bob Example", "Bob Example"),
    ),
    ids=("single_creator", "reversed_pair", "duplicated_tail"),
)
def test_complete_creator_order_and_comma_inversion_are_conservative(
    values: tuple[str, ...],
) -> None:
    def local(value: str, occurrence: int = 0) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
            occurrence=occurrence,
        )

    def provider(value: str, raw_order: int) -> ProviderClaim:
        return ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.CREATOR,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=raw_order,
        )

    locals_ = (local("Ada Example"), local("Bob Example", 1))
    ordered = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            (provider("ADA  EXAMPLE", 0), provider("Bob Example", 1)),
        ),
        locals_,
    )
    assert tuple(
        link.method
        for claim in ordered.claims
        for link in claim.links
        if link.outcome is Comparison.AGREEMENT
    ) == (
        ComparisonMethod.UNICODE_CASE_WHITESPACE,
        ComparisonMethod.EXACT,
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            tuple(provider(value, index) for index, value in enumerate(values)),
        ),
        locals_,
    )
    assert not any(
        link.outcome is Comparison.AGREEMENT
        for claim in candidate.claims
        for link in claim.links
    )

    one = (local("Ada Example"),)
    for value, expected in (
        ("Example, Ada", ComparisonMethod.CONSERVATIVE_COMMA_INVERSION),
        ("Example, Ada, Jr.", ComparisonMethod.NONE),
        ("Example University, Ada", ComparisonMethod.NONE),
        ("Example, Ada and Bob", ComparisonMethod.NONE),
        ("Penguin, Random House", ComparisonMethod.NONE),
        ("Studios, Universal", ComparisonMethod.NONE),
        ("Smith, John Esq.", ComparisonMethod.NONE),
        ("Smith, John MBE", ComparisonMethod.NONE),
        ("Smith, John OBE", ComparisonMethod.NONE),
        ("Smith, John Ph.D.", ComparisonMethod.NONE),
        ("Smith, John M.D.", ComparisonMethod.NONE),
        ("Example,", ComparisonMethod.NONE),
    ):
        candidate = finalize_candidate_comparisons(
            ProviderCandidate("candidate", "work", (provider(value, 0),)),
            one,
        )
        link = candidate.claims[0].links[0]
        assert link.method is expected
        assert link.outcome is (
            Comparison.AGREEMENT
            if expected is not ComparisonMethod.NONE
            else Comparison.DISAGREEMENT
        )
    for organization in ("Library", "Libraries"):
        organization_local = (local(f"British {organization}"),)
        organization_candidate = finalize_candidate_comparisons(
            ProviderCandidate(
                "candidate",
                "work",
                (provider(f"{organization}, British", 0),),
            ),
            organization_local,
        )
        assert (
            organization_candidate.claims[0].links[0].outcome is Comparison.DISAGREEMENT
        )


def test_identity_bound_aliases_collapse_only_one_complete_creator_set() -> None:
    local_claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
            occurrence=index,
        )
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    claims = tuple(
        ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.CREATOR,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=index,
        )
        for index, value in enumerate(("A. Example", "Ada Example", "Bob Example"))
    )
    bindings = tuple(
        sorted(
            (
                ProviderIdentityBinding(
                    claim.reference,
                    identity,
                    ClaimScope.WORK,
                    "author",
                    (0, 2, 7)[index],
                )
                for index, (claim, identity) in enumerate(
                    zip(
                        claims,
                        ("author-1", "author-1", "author-2"),
                        strict=True,
                    )
                )
            ),
            key=lambda value: (
                value.scope.value,
                value.role,
                value.source_ordinal,
                value.identity,
                value.claim_reference,
            ),
        )
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate("candidate", "work", claims, (), bindings),
        local_claims,
    )
    assert tuple(
        (
            claim.value,
            next(
                link.method
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            ),
            next(
                link.local_reference
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            ),
        )
        for claim in candidate.claims
    ) == (
        (
            "A. Example",
            ComparisonMethod.STRUCTURAL_IDENTITY_BINDING,
            local_claims[0].reference,
        ),
        (
            "Ada Example",
            ComparisonMethod.EXACT,
            local_claims[0].reference,
        ),
        (
            "Bob Example",
            ComparisonMethod.EXACT,
            local_claims[1].reference,
        ),
    )
    replay = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=("package_metadata_creator",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )
    assert replay == MetadataReport.from_json(replay.to_json())
    contradictory_bindings = tuple(
        sorted(
            (
                replace(bindings[0], source_ordinal=7),
                replace(bindings[1], source_ordinal=8),
                replace(bindings[2], source_ordinal=0),
            ),
            key=lambda value: (
                value.scope.value,
                value.role,
                value.source_ordinal,
                value.identity,
                value.claim_reference,
            ),
        )
    )
    contradictory = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            claims,
            (),
            contradictory_bindings,
        ),
        local_claims,
    )
    assert not any(
        link.outcome is Comparison.AGREEMENT
        for claim in contradictory.claims
        for link in claim.links
    )

    direct_claims = claims[1:]
    reversed_bindings = (
        replace(bindings[2], source_ordinal=0),
        replace(bindings[1], source_ordinal=7),
    )
    reversed_bound = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            direct_claims,
            (),
            reversed_bindings,
        ),
        local_claims,
    )
    assert not any(
        link.outcome is Comparison.AGREEMENT
        for claim in reversed_bound.claims
        for link in claim.links
    )

    partial = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            direct_claims,
            (),
            (replace(bindings[1], source_ordinal=0),),
        ),
        local_claims,
    )
    assert (
        ComparisonMethod.EXACT,
        ComparisonMethod.EXACT,
    ) == tuple(
        link.method
        for claim in partial.claims
        for link in claim.links
        if link.outcome is Comparison.AGREEMENT
    )
    partial_alias = finalize_candidate_comparisons(
        ProviderCandidate(
            "candidate",
            "work",
            claims,
            (),
            (replace(bindings[0], source_ordinal=0),),
        ),
        local_claims,
    )
    assert not any(
        link.outcome is Comparison.AGREEMENT
        for claim in partial_alias.claims
        for link in claim.links
    )
    partial_replay = MetadataReport.create(
        target=replay.target,
        file=replay.file,
        local_claims=local_claims,
        local_reasons=("package_metadata_creator",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(partial,),
            ),
        ),
    )
    assert partial_replay == MetadataReport.from_json(partial_replay.to_json())
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        repository.publish(partial_replay, lambda: True)
        assert partial_replay == MetadataReport.from_json(
            repository.get(partial_replay.report_id)
        )
        repository.close()
    duplicate_ordinal = replace(bindings[1], source_ordinal=0)
    with pytest.raises(ReportValueError):
        ProviderCandidate(
            "candidate",
            "work",
            claims,
            (),
            tuple(
                sorted(
                    (bindings[0], duplicate_ordinal, bindings[2]),
                    key=lambda value: (
                        value.scope.value,
                        value.role,
                        value.source_ordinal,
                        value.identity,
                        value.claim_reference,
                    ),
                )
            ),
        )
    tampered = replay.to_data()
    tampered["providers"][0]["candidates"][0]["identity_bindings"][0][
        "claim_reference"
    ] = claims[2].reference
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(tampered))
    ordinal_tampered = replay.to_data()
    ordinal_tampered["providers"][0]["candidates"][0]["identity_bindings"][1][
        "source_ordinal"
    ] = 0
    serialized_tamper = canonical_json(ordinal_tampered)
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(serialized_tamper)
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        repository.publish(replay, lambda: True)
        repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
        repository._connection.execute(
            "UPDATE metadata_reports SET document=?,document_digest=?",
            (
                serialized_tamper,
                hashlib.sha256(serialized_tamper.encode()).hexdigest(),
            ),
        )
        with pytest.raises(MetadataReportError):
            repository.get(replay.report_id)
        repository.close()

    duplicated_claims = tuple(
        ProviderClaim.create(
            provider="catalogue",
            candidate="duplicated",
            kind=ClaimKind.CREATOR,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=index,
        )
        for index, value in enumerate(("Ada Example", "ADA  EXAMPLE", "Bob Example"))
    )
    duplicated_bindings = tuple(
        ProviderIdentityBinding(
            claim.reference,
            identity,
            ClaimScope.WORK,
            "author",
            index,
        )
        for index, (claim, identity) in enumerate(
            zip(
                duplicated_claims,
                ("author-1", "author-1", "author-2"),
                strict=True,
            )
        )
    )
    duplicated = finalize_candidate_comparisons(
        ProviderCandidate(
            "duplicated",
            "work",
            duplicated_claims,
            (),
            duplicated_bindings,
        ),
        local_claims,
    )
    assert not any(
        link.outcome is Comparison.AGREEMENT
        for claim in duplicated.claims
        for link in claim.links
    )

    duplicate_local = tuple(
        LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="Ada Example",
            canonical="Ada Example",
            excerpt="Ada Example",
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
            occurrence=index,
        )
        for index in range(2)
    )
    duplicate_provider = tuple(
        ProviderClaim.create(
            provider="catalogue",
            candidate="duplicates",
            kind=ClaimKind.CREATOR,
            value="Ada Example",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=index,
        )
        for index in range(2)
    )
    for provider_values, local_values in (
        (duplicate_provider, local_claims),
        (claims[1:], duplicate_local),
        (duplicate_provider, duplicate_local),
    ):
        duplicate_set = finalize_candidate_comparisons(
            ProviderCandidate("duplicates", "work", provider_values),
            local_values,
        )
        assert not any(
            link.outcome is Comparison.AGREEMENT
            for claim in duplicate_set.claims
            for link in claim.links
        )


@pytest.mark.parametrize(
    ("left", "left_precision", "right", "right_precision", "relation", "shared"),
    (
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-03-05T01:30:00+00:00",
            DatePrecision.TIMESTAMP,
            PublicationDateRelation.EXACT,
            DatePrecision.DAY,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-03-06T01:30:00+00:00",
            DatePrecision.TIMESTAMP,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.MONTH,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-03-05",
            DatePrecision.DAY,
            PublicationDateRelation.EXACT,
            DatePrecision.DAY,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-03-06",
            DatePrecision.DAY,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.MONTH,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-03",
            DatePrecision.MONTH,
            PublicationDateRelation.MONTH_COMPATIBLE,
            DatePrecision.MONTH,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024",
            DatePrecision.YEAR,
            PublicationDateRelation.YEAR_COMPATIBLE,
            DatePrecision.YEAR,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2024-04",
            DatePrecision.MONTH,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.YEAR,
        ),
        (
            "2024-03-05T23:30:00-02:00",
            DatePrecision.TIMESTAMP,
            "2025",
            DatePrecision.YEAR,
            PublicationDateRelation.FINER_CONFLICT,
            None,
        ),
    ),
)
def test_date_comparison_normalizes_both_lexicals_without_losing_precision(
    left: str,
    left_precision: DatePrecision,
    right: str,
    right_precision: DatePrecision,
    relation: PublicationDateRelation,
    shared: DatePrecision | None,
) -> None:
    def local(value: str, precision: DatePrecision) -> LocalClaim:
        return LocalClaim.create(
            kind=ClaimKind.PUBLICATION_DATE,
            observed=value,
            canonical=normalize_date_value(value)[0],
            excerpt=value,
            applicability="not_applicable",
            basis="package_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=precision,
        )

    zulu = local("2024-03-05T14:30:00Z", DatePrecision.TIMESTAMP)
    positive_offset = local(
        "2024-03-05T11:30:00+01:00",
        DatePrecision.TIMESTAMP,
    )
    rollover = local(
        "2024-03-05T23:30:00Z",
        DatePrecision.TIMESTAMP,
    )
    different = local(
        "2024-03-05T14:30:01Z",
        DatePrecision.TIMESTAMP,
    )
    day = local("2024-03-05", DatePrecision.DAY)
    month = local("2024-03", DatePrecision.MONTH)
    year = local("2024", DatePrecision.YEAR)
    naive = local(
        "2024-03-05T14:30:00",
        DatePrecision.TIMESTAMP,
    )
    with pytest.raises(ReportValueError):
        local(
            "2024-02-30T14:30:00Z",
            DatePrecision.TIMESTAMP,
        )

    def provider(value: str) -> ProviderClaim:
        return ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )

    negative_offset = provider("2024-03-05T10:30:00-04:00")
    assert {
        zulu.reference: Comparison.AGREEMENT,
        different.reference: Comparison.AGREEMENT,
        day.reference: Comparison.AGREEMENT,
    } == {
        link.local_reference: link.outcome
        for link in comparison_links(
            negative_offset,
            (zulu, different, day),
        )
    }
    coarse_links = {
        link.local_reference: link
        for link in comparison_links(negative_offset, (month, year))
    }
    assert (
        ComparisonLink(
            month.reference,
            Comparison.AGREEMENT,
            ComparisonMethod.DATE_PRECISION_COMPATIBILITY,
            PublicationDateRelation.MONTH_COMPATIBLE,
            DatePrecision.MONTH,
        )
        == coarse_links[month.reference]
    )
    assert (
        ComparisonLink(
            year.reference,
            Comparison.AGREEMENT,
            ComparisonMethod.DATE_PRECISION_COMPATIBILITY,
            PublicationDateRelation.YEAR_COMPATIBLE,
            DatePrecision.YEAR,
        )
        == coarse_links[year.reference]
    )
    assert (
        ComparisonLink(
            positive_offset.reference,
            Comparison.AGREEMENT,
            ComparisonMethod.EXACT,
            PublicationDateRelation.EXACT,
            DatePrecision.DAY,
        ),
    ) == comparison_links(
        provider("2024-03-05T10:30:00Z"),
        (positive_offset,),
    )
    assert (
        ComparisonLink(
            rollover.reference,
            Comparison.DISAGREEMENT,
            ComparisonMethod.NONE,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.MONTH,
        ),
    ) == comparison_links(
        provider("2024-03-06T00:30:00+01:00"),
        (rollover,),
    )
    expected = (relation, shared)
    assert expected == publication_date_relation(
        left_value=left,
        left_event=DateEvent.MANIFESTATION_PUBLICATION,
        left_precision=left_precision,
        right_value=right,
        right_event=DateEvent.MANIFESTATION_PUBLICATION,
        right_precision=right_precision,
    )
    assert expected == publication_date_relation(
        left_value=right,
        left_event=DateEvent.MANIFESTATION_PUBLICATION,
        left_precision=right_precision,
        right_value=left,
        right_event=DateEvent.MANIFESTATION_PUBLICATION,
        right_precision=left_precision,
    )
    assert () == comparison_links(negative_offset, (naive,))
    with pytest.raises(ReportValueError):
        comparison_links(
            provider("2024-03-05T14:30:00"),
            (zulu,),
        )
    assert "2024-03-05T14:30:00Z" == zulu.observed
    assert "2024-03-05T14:30:00+00:00" == zulu.canonical
    assert "2024-03-05T10:30:00-04:00" == negative_offset.date_lexical


@pytest.mark.parametrize(
    (
        "left",
        "left_precision",
        "right",
        "right_precision",
        "expected_relation",
        "expected_shared",
    ),
    (
        (
            "2021-09-14",
            DatePrecision.DAY,
            "2021",
            DatePrecision.YEAR,
            PublicationDateRelation.YEAR_COMPATIBLE,
            DatePrecision.YEAR,
        ),
        (
            "2021-09-14",
            DatePrecision.DAY,
            "2021-09",
            DatePrecision.MONTH,
            PublicationDateRelation.MONTH_COMPATIBLE,
            DatePrecision.MONTH,
        ),
        (
            "2021-09-14",
            DatePrecision.DAY,
            "2021-09-20",
            DatePrecision.DAY,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.MONTH,
        ),
        (
            "2021-09",
            DatePrecision.MONTH,
            "2021-10",
            DatePrecision.MONTH,
            PublicationDateRelation.FINER_CONFLICT,
            DatePrecision.YEAR,
        ),
        (
            "2021",
            DatePrecision.YEAR,
            "2022",
            DatePrecision.YEAR,
            PublicationDateRelation.FINER_CONFLICT,
            None,
        ),
    ),
)
def test_publication_date_relation_preserves_shared_precision_and_conflict(
    left: str,
    left_precision: DatePrecision,
    right: str,
    right_precision: DatePrecision,
    expected_relation: PublicationDateRelation,
    expected_shared: DatePrecision | None,
) -> None:
    assert (expected_relation, expected_shared) == publication_date_relation(
        left_value=left,
        left_event=DateEvent.MANIFESTATION_PUBLICATION,
        left_precision=left_precision,
        right_value=right,
        right_event=DateEvent.MANIFESTATION_PUBLICATION,
        right_precision=right_precision,
    )
    assert (
        PublicationDateRelation.EVENT_INCOMPATIBLE,
        None,
    ) == publication_date_relation(
        left_value="2021",
        left_event=DateEvent.WORK_FIRST_PUBLICATION,
        left_precision=DatePrecision.YEAR,
        right_value="2021",
        right_event=DateEvent.MANIFESTATION_PUBLICATION,
        right_precision=DatePrecision.YEAR,
    )
    assert (
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
        None,
    ) == publication_date_relation(
        left_value="2021",
        left_event=DateEvent.MANIFESTATION_PUBLICATION,
        left_precision=DatePrecision.YEAR,
        right_value="2021",
        right_event=DateEvent.MANIFESTATION_PUBLICATION,
        right_precision=DatePrecision.YEAR,
        manifestation_compatible=False,
    )


def test_maximum_description_comparison_is_field_appropriate() -> None:
    local_value = "d" * 512
    provider_value = "p" * MAX_DESCRIPTION_CHARACTERS
    local = LocalClaim.create(
        kind=ClaimKind.DESCRIPTION,
        observed=local_value,
        canonical=local_value,
        excerpt=local_value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    bare = ProviderClaim.create(
        provider="catalogue",
        candidate="candidate",
        kind=ClaimKind.DESCRIPTION,
        value=provider_value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
    )
    assert comparison_links(bare, (local,)) == (
        ComparisonLink(local.reference, Comparison.DISAGREEMENT),
    )
