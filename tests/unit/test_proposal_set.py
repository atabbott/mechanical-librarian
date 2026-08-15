import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    _corroboration_source_labels,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
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
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    PublicationDateRelation,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    canonical_json,
    comparison_links,
    default_target_fields,
    finalize_candidate_comparisons,
    provider_lane_candidate_reference,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    ProposalSet,
    ProposalValue,
    ProposalValueError,
    ProposalValueKind,
    _ignored_policy_targets,
    _target_backed_lookup_contains,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase


def _fields(
    current_title: str | None = "Current title",
    **overrides: TargetField,
) -> tuple[TargetField, ...]:
    values = {
        value.destination: value
        for value in default_target_fields(current_title)
    }
    values.update(
        {
            TargetDestination[key.upper()]: value
            for key, value in overrides.items()
        }
    )
    return tuple(values[destination] for destination in TargetDestination)


def _claim(
    provider: str,
    candidate: str,
    kind: ClaimKind,
    value: str,
    *,
    scope: ClaimScope,
    order: int = 0,
    role: str | None = None,
    applicability: str = "not_applicable",
) -> ProviderClaim:
    return ProviderClaim.create(
        provider=provider,
        candidate=candidate,
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=order,
    )


def _provider(
    name: str,
    candidate: str,
    claims: tuple[ProviderClaim, ...],
    evidence: tuple[ProviderEvidence, ...] = (),
    authority: str | None = None,
) -> ProviderObservation:
    return ProviderObservation(
        name,
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate(candidate, f"{name}-work", claims, evidence),),
        authority=authority,
    )


def _publication_local_isbn() -> LocalClaim:
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


def _explicit_local_ebook_isbn() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _publication_provider(
    name: str,
    candidate: str,
    values: tuple[str, ...],
    *,
    authority: str | None = None,
) -> ProviderObservation:
    claims = (
        _claim(
            name,
            candidate,
            ClaimKind.ISBN,
            "9780306406157",
            scope=ClaimScope.EDITION,
            applicability="unknown",
        ),
        *(
            _claim(
                name,
                candidate,
                ClaimKind.PUBLICATION_DATE,
                value,
                scope=ClaimScope.EDITION,
                order=index + 1,
            )
            for index, value in enumerate(values)
        ),
    )
    return _provider(name, candidate, claims, authority=authority)


def _manifestation_publisher_provider(
    name: str,
    candidate: str,
    kind: ClaimKind,
    value: str,
    *,
    isbn: str = "9780306406157",
    applicability: str = "ebook",
) -> ProviderObservation:
    return _provider(
        name,
        candidate,
        (
            _claim(
                name,
                candidate,
                ClaimKind.ISBN,
                isbn,
                scope=ClaimScope.EDITION,
                applicability=applicability,
            ),
            _claim(
                name,
                candidate,
                kind,
                value,
                scope=ClaimScope.EDITION,
                order=1,
            ),
        ),
    )


def _contradictory_publication_provider(
    name: str = "catalogue",
    candidate: str = "edition-1",
) -> ProviderObservation:
    return _provider(
        name,
        candidate,
        (
            _claim(
                name,
                candidate,
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="unknown",
            ),
            _claim(
                name,
                candidate,
                ClaimKind.PUBLICATION_DATE,
                "2001-02-03",
                scope=ClaimScope.EDITION,
                order=1,
            ),
        ),
        (
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                f"{name}-work",
                ClaimScope.EDITION,
            ),
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                "contradictory_or_multiple",
                ClaimScope.EDITION,
                source_ordinal=1,
            ),
        ),
    )


def _report(
    *,
    title: str | None = "Current title",
    fields: tuple[TargetField, ...] | None = None,
    local_claims: tuple[LocalClaim, ...] = (),
    providers: tuple[ProviderObservation, ...] = (),
    covers: tuple[CoverSource, ...] = (),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            _fields(title) if fields is None else fields,
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=(
            ("opening_matter_creator",)
            if local_claims
            else ("no_opening_claims",)
        ),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        cover_sources=covers,
    )


def _local_subtitle(value: str, scope: ClaimScope) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.SUBTITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=scope,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _local_creator(
    value: str,
    *,
    basis: str = "package_metadata",
    occurrence: int = 0,
) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.CREATOR,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis=basis,
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="author",
        occurrence=occurrence,
    )


def _identity_bound_alias_report(
    *, include_title: bool = False
) -> MetadataReport:
    local_claims = tuple(
        _local_creator(value, occurrence=index)
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    creator_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            value,
            scope=ClaimScope.WORK,
            role="author",
            order=index,
        )
        for index, value in enumerate(
            ("A. Example", "Ada Example", "Bob Example")
        )
    )
    title_claims = (
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Candidate title",
                scope=ClaimScope.WORK,
                order=3,
            ),
        )
        if include_title
        else ()
    )
    bindings = tuple(
        ProviderIdentityBinding(
            claim.reference,
            identity,
            ClaimScope.WORK,
            "author",
            ordinal,
        )
        for claim, identity, ordinal in zip(
            creator_claims,
            ("author-1", "author-1", "author-2"),
            (0, 2, 7),
            strict=True,
        )
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "edition-1",
            "catalogue-work",
            creator_claims + title_claims,
            (),
            bindings,
        ),
        local_claims,
    )
    return _report(
        title=None,
        fields=_fields(
            None,
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=local_claims,
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )


def _partially_bound_direct_author_report(
    *, include_title: bool = False
) -> MetadataReport:
    local_claims = tuple(
        _local_creator(value, occurrence=index)
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    creator_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            value,
            scope=ClaimScope.WORK,
            role="author",
            order=index,
        )
        for index, value in enumerate(("Ada Example", "Bob Example"))
    )
    title_claims = (
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.TITLE,
                "Candidate title",
                scope=ClaimScope.WORK,
                order=2,
            ),
        )
        if include_title
        else ()
    )
    candidate = finalize_candidate_comparisons(
        ProviderCandidate(
            "edition-1",
            "catalogue-work",
            creator_claims + title_claims,
            (),
            (
                ProviderIdentityBinding(
                    creator_claims[0].reference,
                    "author-1",
                    ClaimScope.WORK,
                    "author",
                    0,
                ),
            ),
        ),
        local_claims,
    )
    return _report(
        title=None,
        fields=_fields(
            None,
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=local_claims,
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
            ),
        ),
    )


def _result(proposal_set: ProposalSet, destination: TargetDestination):
    return next(
        value
        for value in proposal_set.results
        if value.destination is destination
    )


def _isbn_lane_observation(
    provider: str,
    candidate: ProviderCandidate,
    authority: str,
    lane,
) -> ProviderObservation:
    return provider_observation(
        provider,
        (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, (candidate,)),),
        authority=authority,
    )


def _real_tigers_corroboration_report() -> MetadataReport:
    fields = _fields(
        "Real Tigers",
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
    )
    isbn = _publication_local_isbn()
    lane = metadata_discovery_plan(
        TargetSnapshot("ebook", "Real Tigers", (), fields=fields), (isbn,),
    ).lanes[0]
    author_claim = _claim(
        "open-library", "author-edition", ClaimKind.CREATOR, "Mick Herron",
        scope=ClaimScope.WORK, role="author", order=1,
    )
    neutral_claim = _claim(
        "hardcover", "neutral-edition", ClaimKind.CREATOR, "Mick Herron",
        scope=ClaimScope.WORK, role=None, order=1,
    )
    author_candidate = ProviderCandidate(
        "author-edition", "open-library-work",
        (_claim("open-library", "author-edition", ClaimKind.TITLE, "Real Tigers", scope=ClaimScope.WORK), author_claim), (),
        (ProviderIdentityBinding(author_claim.reference, "open-library-author-OL1", ClaimScope.WORK, "author"),),
    )
    neutral_candidate = ProviderCandidate(
        "neutral-edition", "hardcover-work",
        (_claim("hardcover", "neutral-edition", ClaimKind.TITLE, "Real Tigers", scope=ClaimScope.WORK), neutral_claim), (),
        (ProviderIdentityBinding(neutral_claim.reference, "hardcover-author-0000000001", ClaimScope.WORK, None),),
    )
    return _report(
        title="Real Tigers",
        fields=fields,
        local_claims=(isbn,),
        providers=(
            _isbn_lane_observation("hardcover", neutral_candidate, "hardcover", lane),
            _isbn_lane_observation("open-library", author_candidate, "open_library", lane),
        ),
    )


class ProposalSetTests(unittest.TestCase):
    def test_corroboration_is_provider_order_deterministic(self) -> None:
        report = _real_tigers_corroboration_report()
        reordered = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
        )
        # Provider observations are canonically stored by name.  Evaluate the
        # same qualified observations in reverse to prove the pure pairing
        # seam itself has no iteration-order vote.
        object.__setattr__(reordered, "providers", tuple(reversed(report.providers)))
        first, second = evaluate_report(report), evaluate_report(reordered)
        self.assertEqual(first.to_json(), second.to_json())
        result = _result(second, TargetDestination.AUTHORS)
        self.assertEqual(("explicit_author_corroborates_untyped_creator",), result.reasons)
        self.assertEqual(2, len(result.alternatives[0].evidence_references))

    def test_unqualified_work_lookup_cannot_corroborate_authors(self) -> None:
        report = _real_tigers_corroboration_report()
        lookup = MetadataLookup(
            ProviderQueryKind.WORK, (), "Real Tigers", ("Mick Herron",),
        )
        author_source = qualify_provider_observation(report.providers[1], lookup)
        mismatched_candidate = replace(
            report.providers[0].candidates[0],
            claims=(
                _claim("hardcover", "neutral-edition", ClaimKind.TITLE, "Other Tigers", scope=ClaimScope.WORK),
                _claim("hardcover", "neutral-edition", ClaimKind.CREATOR, "Mick Herron", scope=ClaimScope.WORK, role=None, order=1),
            ),
            identity_bindings=(),
        )
        mismatched = qualify_provider_observation(
            ProviderObservation("hardcover", ProviderStatus.CANDIDATES, (), (mismatched_candidate,), authority="hardcover"),
            lookup,
        )
        self.assertEqual(ProviderStatus.CANDIDATES, author_source.status)
        self.assertEqual(ProviderStatus.NO_MATCH, mismatched.status)
        rejected = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(None, authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)),
                    providers=(mismatched, author_source),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(("unique_typed_candidate",), rejected.reasons)

    def test_untyped_creator_corroboration_rejects_closed_matrix(self) -> None:
        def candidate(
            provider: str,
            identity: str,
            values: tuple[str, ...],
            roles: tuple[str | None, ...],
            bindings: int,
        ) -> ProviderCandidate:
            claims = tuple(
                _claim(
                    provider, identity, ClaimKind.CREATOR, value,
                    scope=ClaimScope.WORK, role=role, order=index,
                )
                for index, (value, role) in enumerate(zip(values, roles, strict=True))
            )
            prefix = "open-library-author-OL" if provider == "open-library" else "hardcover-author-"
            return ProviderCandidate(
                identity, provider + "-work", claims, (),
                tuple(
                    ProviderIdentityBinding(
                        claim.reference, prefix + str(index + 1), ClaimScope.WORK,
                        claim.role, index,
                    )
                    for index, claim in enumerate(claims[:bindings])
                    if claim.role in {None, "author"}
                ),
            )

        author = candidate(
            "open-library", "author-edition", ("Mick Herron", "Jane Doe"),
            ("author", "author"), 2,
        )

        def has_pair(
            neutral_values: tuple[str, ...],
            neutral_roles: tuple[str | None, ...],
            bindings: int,
            query_kind: ProviderQueryKind = ProviderQueryKind.WORK,
        ) -> bool:
            neutral = candidate(
                "hardcover", "neutral-edition", neutral_values, neutral_roles,
                bindings,
            )
            report = _report(
                title=None,
                fields=_fields(
                    None,
                    authors=TargetField(
                        TargetDestination.AUTHORS, TargetFieldState.MISSING,
                    ),
                ),
                providers=(
                    ProviderObservation("hardcover", ProviderStatus.CANDIDATES, (), (neutral,), query_kind, "hardcover"),
                    ProviderObservation("open-library", ProviderStatus.CANDIDATES, (), (author,), ProviderQueryKind.WORK, "open_library"),
                ),
            )
            references = {
                reference
                for alternative in _result(
                    evaluate_report(report), TargetDestination.AUTHORS,
                ).alternatives
                for reference in alternative.evidence_references
            }
            return all(claim.reference in references for claim in neutral.claims)

        cases = (
            ("name_mismatch", ("Mick Herron", "Other Doe"), (None, None), 2, ProviderQueryKind.WORK),
            ("reordered", ("Jane Doe", "Mick Herron"), (None, None), 2, ProviderQueryKind.WORK),
            ("duplicate", ("Mick Herron", "MICK HERRON"), (None, None), 2, ProviderQueryKind.WORK),
            ("partial", ("Mick Herron", "Jane Doe"), (None, None), 1, ProviderQueryKind.WORK),
            ("mixed_role", ("Mick Herron", "Jane Doe"), (None, "translator"), 2, ProviderQueryKind.WORK),
            ("work_mismatch", ("Mick Herron", "Jane Doe"), (None, None), 2, ProviderQueryKind.ISBN),
        )
        for label, values, roles, bindings, query_kind in cases:
            with self.subTest(label=label):
                self.assertFalse(has_pair(values, roles, bindings, query_kind))

        untyped_only = candidate(
            "hardcover", "untyped-only", ("Mick Herron",), (None,), 1,
        )
        report = _report(
            title=None,
            fields=_fields(
                None,
                authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
            ),
            providers=(
                ProviderObservation("hardcover", ProviderStatus.CANDIDATES, (), (untyped_only,), ProviderQueryKind.WORK, "hardcover"),
            ),
        )
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(evaluate_report(report), TargetDestination.AUTHORS).outcome,
        )

    def test_mixed_discovery_rejects_lane_shapes_without_target_evidence(
        self,
    ) -> None:
        """Lane shape alone cannot turn a creator reference into target evidence."""
        report = _real_tigers_corroboration_report()
        neutral = report.providers[0].candidates[0]
        author = report.providers[1].candidates[0]
        local = _local_creator("Mick Herron")

        def lane(
            provider: str,
            identity: str,
            kind: ProviderQueryKind,
            candidate: ProviderCandidate,
        ) -> ProviderLaneObservation:
            return ProviderLaneObservation(
                identity,
                kind,
                ProviderStatus.CANDIDATES,
                (local.reference,),
                "9780306406157" if kind is ProviderQueryKind.ISBN else None,
                (
                    ProviderLaneCandidate(
                        provider_lane_candidate_reference(
                            provider, identity, candidate.identity,
                            candidate.work_identity,
                        ),
                        candidate.identity,
                        candidate.work_identity,
                    ),
                ),
            )

        isbn_lane = lane(
            "hardcover", "lane_" + "1" * 64, ProviderQueryKind.ISBN, neutral,
        )
        work_lane = lane(
            "hardcover", "lane_" + "2" * 64, ProviderQueryKind.WORK, neutral,
        )
        mixed_neutral = ProviderObservation(
            "hardcover", ProviderStatus.CANDIDATES, (local.reference,),
            (neutral,), ProviderQueryKind.DISCOVERY, "hardcover",
            lanes=(isbn_lane, work_lane),
        )
        mixed_author = ProviderObservation(
            "open-library", ProviderStatus.CANDIDATES, (local.reference,),
            (author,), ProviderQueryKind.DISCOVERY, "open_library",
            lanes=(
                lane(
                    "open-library", "lane_" + "3" * 64,
                    ProviderQueryKind.ISBN, author,
                ),
                lane(
                    "open-library", "lane_" + "4" * 64,
                    ProviderQueryKind.WORK, author,
                ),
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                    providers=(mixed_neutral, mixed_author),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(
            ("unique_typed_candidate",), result.reasons,
        )
        self.assertNotIn(
            neutral.claims[1].reference,
            {
                reference
                for alternative in result.alternatives
                for reference in alternative.evidence_references
            },
        )

        isbn_only_neutral = replace(
            mixed_neutral,
            lanes=(isbn_lane,),
            query_kind=ProviderQueryKind.ISBN,
            convergence=(),
        )
        rejected = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                    providers=(isbn_only_neutral, mixed_author),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(("unique_typed_candidate",), rejected.reasons)
        self.assertNotIn(
            neutral.claims[1].reference,
            {
                reference
                for alternative in rejected.alternatives
                for reference in alternative.evidence_references
            },
        )

    def test_isbn_backed_creator_corroboration_requires_exact_complete_membership(
        self,
    ) -> None:
        """ISBN discovery admits complete creators without a local author lookup."""
        fields = _fields(
            "Real Tigers",
            authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
        )
        isbn = _publication_local_isbn()
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Real Tigers", (), fields=fields), (isbn,),
        )
        self.assertEqual(1, len(plan.lanes))
        isbn_lane = plan.lanes[0]
        self.assertIs(ProviderQueryKind.ISBN, isbn_lane.kind)
        self.assertEqual((isbn.reference,), isbn_lane.input_references)

        def candidate(
            provider: str,
            identity: str,
            work: str,
            role: str | None,
            names: tuple[str, ...] = ("Mick Herron", "Jane Doe"),
        ) -> ProviderCandidate:
            claims = tuple(
                _claim(
                    provider, identity, ClaimKind.CREATOR, name,
                    scope=ClaimScope.WORK, role=role, order=index,
                )
                for index, name in enumerate(names)
            )
            bindings = (
                tuple(
                    ProviderIdentityBinding(
                        claim.reference, f"{provider}-creator-{index}",
                        ClaimScope.WORK, None, index,
                    )
                    for index, claim in enumerate(claims)
                )
                if role is None else ()
            )
            return ProviderCandidate(identity, work, claims, (), bindings)

        explicit = candidate("open-connection", "open-edition", "real-work", "author")
        neutral = candidate("hardcover-connection", "hardcover-edition", "real-work", None)

        def observation(
            provider: str,
            authority: str,
            value: ProviderCandidate,
            status: ProviderStatus = ProviderStatus.CANDIDATES,
        ) -> ProviderObservation:
            return provider_observation(
                provider,
                (
                    ProviderLaneResult(
                        isbn_lane,
                        status,
                        (value,) if status is ProviderStatus.CANDIDATES else (),
                    ),
                ),
                authority=authority,
            )

        def result(*providers: ProviderObservation) -> ProposalResult:
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", fields),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(isbn,),
                local_reasons=("opening_matter_isbn",),
                providers=providers,
            )
            return _result(evaluate_report(report), TargetDestination.AUTHORS)

        admitted = result(
            observation("hardcover-connection", "hardcover", neutral),
            observation("open-connection", "open_library", explicit),
        )
        self.assertIs(ProposalOutcome.PROPOSE_CHANGE, admitted.outcome)
        self.assertEqual(
            ("explicit_author_corroborates_untyped_creator",), admitted.reasons,
        )
        self.assertEqual(
            {claim.reference for claim in (*explicit.claims, *neutral.claims)},
            set(admitted.alternatives[0].evidence_references),
        )

        exact_observation = observation(
            "hardcover-connection", "hardcover", neutral,
        )
        with self.assertRaisesRegex(ReportValueError, "lane candidates"):
            replace(
                exact_observation,
                lanes=(
                    replace(
                        exact_observation.lanes[0],
                        candidates=(
                            ProviderLaneCandidate(
                                provider_lane_candidate_reference(
                                    "hardcover-connection", isbn_lane.identity,
                                    neutral.identity, "other-work",
                                ),
                                neutral.identity,
                                "other-work",
                            ),
                        ),
                    ),
                ),
            )
        mixed = candidate(
            "open-connection", "open-mixed", "real-work", "author", ("Mick Herron",),
        )
        mixed = replace(
            mixed,
            claims=(
                *mixed.claims,
                _claim(
                    "open-connection", "open-mixed", ClaimKind.CREATOR, "Jane Doe",
                    scope=ClaimScope.WORK, role=None, order=1,
                ),
            ),
        )
        partial = candidate(
            "hardcover-connection", "hardcover-partial", "real-work", None,
            ("Mick Herron",),
        )
        for label, providers in (
            (
                "unsuccessful ISBN lane",
                (
                    observation(
                        "hardcover-connection", "hardcover", neutral,
                        ProviderStatus.NO_MATCH,
                    ),
                    observation("open-connection", "open_library", explicit),
                ),
            ),
            (
                "same authority",
                (
                    observation("hardcover-connection", "hardcover", neutral),
                    observation("open-connection", "hardcover", explicit),
                ),
            ),
            (
                "mixed creator collection",
                (
                    observation("hardcover-connection", "hardcover", neutral),
                    observation("open-connection", "open_library", mixed),
                ),
            ),
            (
                "partial creator collection",
                (
                    observation("hardcover-connection", "hardcover", partial),
                    observation("open-connection", "open_library", explicit),
                ),
            ),
        ):
            with self.subTest(label=label):
                rejected = result(*providers)
                self.assertNotIn(
                    "explicit_author_corroborates_untyped_creator", rejected.reasons,
                )
                self.assertFalse(
                    any(
                        reference in {claim.reference for claim in neutral.claims}
                        for alternative in rejected.alternatives
                        for reference in alternative.evidence_references
                    )
                )

        creator = _local_creator("Mick Herron")
        other_isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780131103627",
            canonical="9780131103627",
            excerpt="9780131103627",
            applicability="unknown",
            checksum="valid_isbn_13",
            applicability_basis="unlabelled",
            scope=ClaimScope.EDITION,
        )
        for label, local_claims, reference in (
            ("creator ref posing as ISBN", (creator,), creator.reference),
            ("mismatched local ISBN family", (other_isbn,), other_isbn.reference),
        ):
            with self.subTest(label=label):
                source = observation("hardcover-connection", "hardcover", neutral)
                tampered = replace(
                    source,
                    input_references=(reference,),
                    lanes=(replace(source.lanes[0], input_references=(reference,)),),
                )
                report = MetadataReport.create(
                    target=TargetFacts("primary", "ebooks", "item-1", fields),
                    file=FileFacts(0, "a" * 64, "b" * 64),
                    local_claims=local_claims,
                    local_reasons=("opening_matter_isbn",),
                    providers=(tampered,),
                )
                replayed = MetadataReport.from_json(report.to_json())
                self.assertFalse(
                    _target_backed_lookup_contains(
                        replayed, replayed.providers[0], replayed.providers[0].candidates[0],
                    )
                )

        source = observation("hardcover-connection", "hardcover", neutral)
        forged_identity = "lane_" + "0" * 64
        forged_lane = replace(
            source.lanes[0],
            identity=forged_identity,
            candidates=(
                ProviderLaneCandidate(
                    provider_lane_candidate_reference(
                        "hardcover-connection", forged_identity,
                        neutral.identity, neutral.work_identity,
                    ),
                    neutral.identity,
                    neutral.work_identity,
                ),
            ),
        )
        forged_source = replace(source, lanes=(forged_lane,))
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(isbn,),
            local_reasons=("opening_matter_isbn",),
            providers=(forged_source,),
        )
        replayed = MetadataReport.from_json(report.to_json())
        self.assertFalse(
            _target_backed_lookup_contains(
                replayed, replayed.providers[0], replayed.providers[0].candidates[0],
            )
        )

        isbn_10 = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
            scope=ClaimScope.EDITION,
        )
        non_valid_isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
            scope=ClaimScope.EDITION,
            state=FieldState.UNTYPED,
        )
        complete_lane = metadata_discovery_plan(
            TargetSnapshot("ebook", "Real Tigers", (), fields=fields),
            (isbn, isbn_10, non_valid_isbn),
        ).lanes[0]
        complete_references = tuple(
            sorted(
                claim.reference for claim in (isbn, isbn_10, non_valid_isbn)
            )
        )
        complete_canonical_isbns = sorted(
            {isbn.canonical, isbn_10.canonical, non_valid_isbn.canonical}
        )
        self.assertEqual(complete_references, complete_lane.input_references)
        self.assertEqual(
            "lane_" + hashlib.sha256(
                canonical_json(
                    {
                        "canonical_isbns": complete_canonical_isbns,
                        "input_references": list(complete_references),
                        "kind": ProviderQueryKind.ISBN.value,
                        "matched_family": complete_lane.matched_family,
                    }
                ).encode()
            ).hexdigest(),
            complete_lane.identity,
        )
        complete_source = provider_observation(
            "hardcover-connection",
            (ProviderLaneResult(
                complete_lane, ProviderStatus.CANDIDATES, (neutral,),
            ),),
            authority="hardcover",
        )
        complete_report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(isbn, isbn_10, non_valid_isbn),
            local_reasons=("opening_matter_isbn",),
            providers=(complete_source,),
        )
        self.assertTrue(
            _target_backed_lookup_contains(
                complete_report, complete_source, neutral,
            )
        )
        subset_identity = "lane_" + hashlib.sha256(
            canonical_json(
                {
                    "canonical_isbns": [isbn.canonical],
                    "input_references": [isbn.reference],
                    "kind": ProviderQueryKind.ISBN.value,
                    "matched_family": complete_lane.matched_family,
                }
            ).encode()
        ).hexdigest()
        subset_lane = replace(
            complete_source.lanes[0],
            identity=subset_identity,
            input_references=(isbn.reference,),
            candidates=(
                ProviderLaneCandidate(
                    provider_lane_candidate_reference(
                        "hardcover-connection", subset_identity,
                        neutral.identity, neutral.work_identity,
                    ),
                    neutral.identity,
                    neutral.work_identity,
                ),
            ),
        )
        subset_source = replace(
            complete_source,
            input_references=(isbn.reference,),
            lanes=(subset_lane,),
        )
        subset_report = MetadataReport.from_json(
            MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", fields),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(isbn, isbn_10, non_valid_isbn),
                local_reasons=("opening_matter_isbn",),
                providers=(subset_source,),
            ).to_json()
        )
        self.assertFalse(
            _target_backed_lookup_contains(
                subset_report, subset_report.providers[0], neutral,
            )
        )

    def test_work_lane_corroboration_rejects_replayed_target_binding_tampering(
        self,
    ) -> None:
        fields = _fields(
            "Real Tigers",
            authors=TargetField(
                TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Mick Herron",),
            ),
        )
        title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Real Tigers",
            canonical="Real Tigers",
            excerpt="Real Tigers",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            basis="target_metadata",
        )
        lane = metadata_discovery_plan(
            TargetSnapshot("ebook", "Real Tigers", ("Mick Herron",), fields=fields),
            (title,),
        ).lanes[0]
        claim = _claim(
            "open-connection", "edition", ClaimKind.CREATOR, "Mick Herron",
            scope=ClaimScope.WORK, role="author",
        )
        candidate = ProviderCandidate(
            "edition", "work",
            (
                _claim(
                    "open-connection", "edition", ClaimKind.TITLE, "Real Tigers",
                    scope=ClaimScope.WORK,
                ),
                claim,
            ),
        )
        source = provider_observation(
            "open-connection",
            (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, (candidate,)),),
            authority="open_library",
        )
        baseline = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(title,),
            local_reasons=("opening_matter_isbn",),
            providers=(source,),
        )
        self.assertTrue(_target_backed_lookup_contains(baseline, source, candidate))
        creator = _local_creator("Mick Herron")
        wrong_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Other Tigers",
            canonical="Other Tigers",
            excerpt="Other Tigers",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            basis="target_metadata",
        )
        for label, local_claims, reference in (
            ("missing title ref", (creator,), creator.reference),
            ("wrong title ref", (wrong_title,), wrong_title.reference),
        ):
            with self.subTest(label=label):
                lane_identity = (
                    "lane_" + hashlib.sha256(
                        canonical_json(
                            {
                                "authors": ["Mick Herron"],
                                "input_references": [reference],
                                "kind": ProviderQueryKind.WORK.value,
                                "title": "Real Tigers",
                            }
                        ).encode()
                    ).hexdigest()
                    if label == "wrong title ref"
                    else source.lanes[0].identity
                )
                tampered = replace(
                    source,
                    input_references=(reference,),
                    lanes=(
                        replace(
                            source.lanes[0],
                            identity=lane_identity,
                            input_references=(reference,),
                            candidates=(
                                ProviderLaneCandidate(
                                    provider_lane_candidate_reference(
                                        "open-connection", lane_identity,
                                        candidate.identity, candidate.work_identity,
                                    ),
                                    candidate.identity,
                                    candidate.work_identity,
                                ),
                            ),
                        ),
                    ),
                )
                report = MetadataReport.create(
                    target=TargetFacts("primary", "ebooks", "item-1", fields),
                    file=FileFacts(0, "a" * 64, "b" * 64),
                    local_claims=local_claims,
                    local_reasons=("opening_matter_isbn",),
                    providers=(tampered,),
                )
                replayed = MetadataReport.from_json(report.to_json())
                self.assertFalse(
                    _target_backed_lookup_contains(
                        replayed, replayed.providers[0], replayed.providers[0].candidates[0],
                    )
                )

        wrong_title_candidate = replace(
            candidate,
            claims=(
                _claim(
                    "open-connection", "edition", ClaimKind.TITLE, "Other Tigers",
                    scope=ClaimScope.WORK,
                ),
                claim,
            ),
        )
        wrong_authors_candidate = replace(
            candidate,
            claims=(
                candidate.claims[0],
                _claim(
                    "open-connection", "edition", ClaimKind.CREATOR,
                    "Completely Different", scope=ClaimScope.WORK, role="author",
                ),
            ),
        )
        variant_candidate = replace(
            candidate,
            claims=(
                *candidate.claims,
                _claim(
                    "open-connection", "edition", ClaimKind.SUBTITLE, "Abridged",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        contributor_candidate = replace(
            candidate,
            claims=(
                *candidate.claims,
                _claim(
                    "open-connection", "edition", ClaimKind.CONTRIBUTOR, "Adapter",
                    scope=ClaimScope.WORK, role="adapter",
                ),
            ),
        )
        for label, rejected_candidate in (
            ("wrong title", wrong_title_candidate),
            ("wrong authors", wrong_authors_candidate),
            ("variant", variant_candidate),
            ("disqualifying contributor", contributor_candidate),
        ):
            with self.subTest(label=label):
                rejected_source = provider_observation(
                    "open-connection",
                    (ProviderLaneResult(
                        lane, ProviderStatus.CANDIDATES, (rejected_candidate,),
                    ),),
                    authority="open_library",
                )
                replayed = MetadataReport.from_json(
                    MetadataReport.create(
                        target=TargetFacts("primary", "ebooks", "item-1", fields),
                        file=FileFacts(0, "a" * 64, "b" * 64),
                        local_claims=(title,),
                        local_reasons=("opening_matter_isbn",),
                        providers=(rejected_source,),
                    ).to_json()
                )
                self.assertFalse(
                    _target_backed_lookup_contains(
                        replayed, replayed.providers[0], rejected_candidate,
                    )
                )

        second_wrong_title_candidate = ProviderCandidate(
            "hardcover-edition", "hardcover-work",
            (
                _claim(
                    "hardcover-connection", "hardcover-edition", ClaimKind.TITLE,
                    "Other Tigers", scope=ClaimScope.WORK,
                ),
                _claim(
                    "hardcover-connection", "hardcover-edition", ClaimKind.CREATOR,
                    "Mick Herron", scope=ClaimScope.WORK, role="author",
                ),
            ),
        )
        both_wrong = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(title,),
            local_reasons=("opening_matter_isbn",),
            providers=(
                provider_observation(
                    "hardcover-connection",
                    (ProviderLaneResult(
                        lane, ProviderStatus.CANDIDATES,
                        (second_wrong_title_candidate,),
                    ),),
                    authority="hardcover",
                ),
                provider_observation(
                    "open-connection",
                    (ProviderLaneResult(
                        lane, ProviderStatus.CANDIDATES, (wrong_authors_candidate,),
                    ),),
                    authority="open_library",
                ),
            ),
        )
        self.assertNotIn(
            "explicit_author_corroborates_untyped_creator",
            _result(evaluate_report(both_wrong), TargetDestination.AUTHORS).reasons,
        )

    def test_reused_creator_identity_binding_fails_closed_for_corroboration(
        self,
    ) -> None:
        report = _real_tigers_corroboration_report()
        neutral = report.providers[0].candidates[0]
        claims = (
            _claim(
                "hardcover", neutral.identity, ClaimKind.CREATOR, "Mick Herron",
                scope=ClaimScope.WORK, role=None,
            ),
            _claim(
                "hardcover", neutral.identity, ClaimKind.CREATOR, "Jane Doe",
                scope=ClaimScope.WORK, role=None, order=1,
            ),
        )
        malformed = replace(
            neutral,
            claims=claims,
            identity_bindings=tuple(
                ProviderIdentityBinding(
                    claim.reference, "hardcover-author-1", ClaimScope.WORK,
                    None, index,
                )
                for index, claim in enumerate(claims)
            ),
        )
        author_claims = (
            _claim(
                "open-library", "author-edition", ClaimKind.CREATOR,
                "Mick Herron", scope=ClaimScope.WORK, role="author",
            ),
            _claim(
                "open-library", "author-edition", ClaimKind.CREATOR,
                "Jane Doe", scope=ClaimScope.WORK, role="author", order=1,
            ),
        )
        author = ProviderCandidate(
            "author-edition", "open-library-work", author_claims, (),
            tuple(
                ProviderIdentityBinding(
                    claim.reference, f"open-library-author-{index}",
                    ClaimScope.WORK, "author", index,
                )
                for index, claim in enumerate(author_claims)
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(
                        ProviderObservation(
                            "hardcover", ProviderStatus.CANDIDATES, (),
                            (malformed,), ProviderQueryKind.WORK, "hardcover",
                        ),
                        ProviderObservation(
                            "open-library", ProviderStatus.CANDIDATES, (),
                            (author,), ProviderQueryKind.WORK, "open_library",
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertNotIn(
            claims[0].reference,
            {
                reference
                for alternative in result.alternatives
                for reference in alternative.evidence_references
            },
        )

    def test_case_detail_projects_complete_creator_corroboration_by_authority(self) -> None:
        def creator(provider: str, candidate: str, value: str, role: str | None) -> ProviderClaim:
            return _claim(
                provider, candidate, ClaimKind.CREATOR, value,
                scope=ClaimScope.WORK, role=role,
            )

        typed_provider = "misleading-connection-name"
        untyped_provider = "not-the-provider-authority"
        creators = ("Mick Herron", "Jane Doe")
        author_claims = tuple(
            creator(typed_provider, "author-edition", value, "author")
            for value in creators
        )
        neutral_claims = tuple(
            creator(untyped_provider, "neutral-edition", value, None)
            for value in creators
        )
        author_candidate = ProviderCandidate(
            "author-edition", "open-library-work", author_claims, (),
            tuple(
                ProviderIdentityBinding(
                    claim.reference, f"open-library-author-{index}",
                    ClaimScope.WORK, "author", index,
                )
                for index, claim in enumerate(author_claims)
            ),
        )
        neutral_candidate = ProviderCandidate(
            "neutral-edition", "hardcover-work",
            (
                *neutral_claims,
                *(
                    _claim(
                        untyped_provider, "neutral-edition", ClaimKind.DESCRIPTION,
                        f"Padding evidence {index}", scope=ClaimScope.WORK,
                        order=index + len(neutral_claims),
                    )
                    for index in range(51)
                ),
            ),
            (),
            tuple(
                ProviderIdentityBinding(
                    claim.reference, f"hardcover-author-{index}",
                    ClaimScope.WORK, None, index,
                )
                for index, claim in enumerate(neutral_claims)
            ),
        )
        fields = _fields(
            "Real Tigers",
            authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
        )
        isbn = _publication_local_isbn()
        lane = metadata_discovery_plan(
            TargetSnapshot("ebook", "Real Tigers", (), fields=fields), (isbn,),
        ).lanes[0]
        report = _report(
            title="Real Tigers",
            fields=fields,
            local_claims=(isbn,),
            providers=(
                _isbn_lane_observation(
                    untyped_provider, neutral_candidate, "hardcover", lane,
                ),
                _isbn_lane_observation(
                    typed_provider, author_candidate, "open_library", lane,
                ),
            ),
        )
        replayed_report = MetadataReport.from_json(report.to_json())
        reordered_report = MetadataReport.from_json(report.to_json())
        object.__setattr__(reordered_report, "providers", tuple(reversed(reordered_report.providers)))
        proposal = evaluate_report(replayed_report)
        self.assertEqual(proposal.to_json(), evaluate_report(reordered_report).to_json())
        result = _result(proposal, TargetDestination.AUTHORS)

        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(("explicit_author_corroborates_untyped_creator",), result.reasons)
        self.assertEqual(
            {claim.reference for claim in (*author_claims, *neutral_claims)},
            set(result.alternatives[0].evidence_references),
        )
        replayed_proposal = ProposalSet.from_json(proposal.to_json())
        review_case = ReviewCase.create(replayed_proposal, replayed_report)
        self.assertEqual(review_case, ReviewCase.from_json(review_case.to_json()))
        padded_references = (
            author_claims[0].reference,
            *(f"ignored-evidence-{index}" for index in range(26)),
            neutral_claims[0].reference,
            *(f"ignored-evidence-{index}" for index in range(26, 51)),
            author_claims[1].reference,
            neutral_claims[1].reference,
        )
        self.assertGreater(len(padded_references), 50)
        self.assertEqual(
            ["Hardcover", "Open Library"],
            _corroboration_source_labels(
                replayed_report,
                list(padded_references),
                list(result.reasons),
            ),
        )
        self.assertNotIn("raw_payload", report.to_json())
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(replayed_report, lambda: True)
            repository.publish_proposal(replayed_proposal)
            admitted = json.loads(repository.admit_review_case(replayed_proposal))
            detail = InspectCurrentReviewCase(repository).run(admitted["review_case_id"])
            author_field = next(
                field for field in detail["fields"]
                if field["destination"] == TargetDestination.AUTHORS.value
            )
            self.assertEqual(
                {claim.reference for claim in (*author_claims, *neutral_claims)},
                set(author_field["provenance"]),
            )
            self.assertEqual(
                ["explicit_author_corroborates_untyped_creator"],
                author_field["notices"],
            )
            self.assertEqual(
                ["Hardcover", "Open Library"],
                author_field["corroboration_sources"],
            )
            self.assertNotIn("raw_payload", json.dumps(detail))
            repository.close()

        same_authority = ProviderObservation(
            untyped_provider, ProviderStatus.CANDIDATES, (), (neutral_candidate,),
            ProviderQueryKind.WORK, "open_library",
        )
        same_authority_report = MetadataReport.create(
            target=replayed_report.target,
            file=replayed_report.file,
            local_claims=replayed_report.local_claims,
            local_reasons=replayed_report.local_reasons,
            providers=(replayed_report.providers[0], same_authority),
        )
        rejected = _result(evaluate_report(same_authority_report), TargetDestination.AUTHORS)
        self.assertNotIn(
            neutral_claims[0].reference,
            rejected.alternatives[0].evidence_references,
        )

    def test_shared_title_subtitle_destination_requires_every_owner_to_ignore(self) -> None:
        from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

        base = dict(MetadataPolicyProfile.builtin().destinations)
        cases = (
            ({**base, "title_layout": "ignore", "subtitle": "ask"}, False),
            ({**base, "title_layout": "ask", "subtitle": "ignore"}, False),
            ({**base, "title_layout": "ignore", "subtitle": "ignore"}, True),
        )
        for destinations, ignored in cases:
            with self.subTest(destinations=destinations):
                self.assertEqual(
                    ignored,
                    TargetDestination.SUBTITLE
                    in _ignored_policy_targets(destinations),
                )
    def test_exact_sixteen_results_are_deterministic_and_round_trip(self) -> None:
        proposal_set = evaluate_report(_report())
        self.assertEqual(tuple(TargetDestination), tuple(
            value.destination for value in proposal_set.results
        ))
        self.assertEqual(
            {"connection", "fields", "item", "library"},
            set(_report().to_data()["target"]),
        )
        self.assertEqual(
            {
                "policy_version",
                "resolved_policy_id",
                "relationships",
                "report",
                "results",
                "schema_version",
            },
            set(proposal_set.to_data()),
        )
        self.assertEqual(
            proposal_set.proposal_set_id,
            ProposalSet.from_json(proposal_set.to_json()).proposal_set_id,
        )
        self.assertEqual("fixed-conservative-v8", POLICY_VERSION)
        self.assertEqual(POLICY_VERSION, proposal_set.policy_version)
        self.assertRegex(proposal_set.resolved_policy_id, r"^resolved_policy_[0-9a-f]{64}$")

    def test_prior_policy_document_is_rejected_and_current_replay_is_exact(
        self,
    ) -> None:
        current = evaluate_report(_report())
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v1"

        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v2"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v4"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v6"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v7"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["schema_version"] = "proposal-set-v1"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))

        prior = json.loads(current.to_json())
        del prior["resolved_policy_id"]
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))

        self.assertEqual(current, ProposalSet.from_json(current.to_json()))
        self.assertEqual(current, evaluate_report(_report()))

    def test_linked_destinations_retain_fixed_dependencies(self) -> None:
        proposal_set = evaluate_report(_report())
        dependencies = {
            result.destination: result.dependencies for result in proposal_set.results
        }
        self.assertEqual(
            (TargetDestination.SUBTITLE, TargetDestination.SERIES),
            dependencies[TargetDestination.TITLE],
        )
        self.assertEqual(
            (TargetDestination.TITLE,), dependencies[TargetDestination.SUBTITLE]
        )
        self.assertEqual(
            (TargetDestination.TITLE,), dependencies[TargetDestination.SERIES]
        )
        self.assertEqual(
            (TargetDestination.PUBLISHED_YEAR,),
            dependencies[TargetDestination.PUBLISHED_DATE],
        )
        self.assertEqual(
            (TargetDestination.PUBLISHED_DATE,),
            dependencies[TargetDestination.PUBLISHED_YEAR],
        )
        self.assertEqual(proposal_set, ProposalSet.from_json(proposal_set.to_json()))
        self.assertEqual(proposal_set, evaluate_report(_report()))
        self.assertNotIn("excerpt", proposal_set.to_json())
        self.assertNotIn("/Users/private", proposal_set.to_json())
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(proposal_set.to_json() + " ")

    def test_result_variants_reject_contradictory_choices(self) -> None:
        proposed = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        title=TargetField(
                            TargetDestination.TITLE,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(
                        _provider(
                            "catalogue",
                            "edition-1",
                            (
                                _claim(
                                    "catalogue",
                                    "edition-1",
                                    ClaimKind.TITLE,
                                    "A title",
                                    scope=ClaimScope.WORK,
                                ),
                            ),
                        ),
                    ),
                )
            ),
            TargetDestination.TITLE,
        )
        alternative = proposed.alternatives[0]
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.PROPOSE_CHANGE,
                (),
                None,
                ("unique_typed_candidate",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                (alternative,),
                None,
                ("field_policy_acceptable_choice",),
            )
        incompatible_language = tuple(
            ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, value),
                ProposalAssessment.FILLS_EMPTY,
                ("provider_" + character * 64,),
                ("correlation_" + character * 64,),
                ProposalScope.EDITION,
            )
            for value, character in (("en", "a"), ("fr", "b"))
        )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                tuple(
                    sorted(
                        incompatible_language,
                        key=lambda value: value.alternative_id,
                    )
                ),
                None,
                ("field_policy_acceptable_choice",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                (alternative,),
                alternative.alternative_id,
                ("competing_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.DEFER,
                (alternative,),
                None,
                ("provider_unavailable",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.KEEP,
                (alternative,),
                None,
                ("positive_preservation_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.REJECT,
                (alternative,),
                None,
                ("policy_reject",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.INELIGIBLE,
                (alternative,),
                None,
                ("target_state_unusable",),
            )
        self.assertEqual(
            ProposalOutcome.REJECT,
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.REJECT,
                (),
                None,
                ("policy_reject",),
            ).outcome,
        )

    def test_language_result_validation_encodes_target_compatibility(self) -> None:
        def language_alternative(
            value: str,
            assessment: ProposalAssessment,
            character: str,
        ) -> ProposalAlternative:
            return ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, value),
                assessment,
                ("provider_" + character * 64,),
                ("correlation_" + character * 64,),
                ProposalScope.EDITION,
            )

        preserves = language_alternative(
            "en",
            ProposalAssessment.PRESERVES_TARGET,
            "a",
        )
        replaces_region = language_alternative(
            "en-GB",
            ProposalAssessment.REPLACES_TARGET,
            "b",
        )
        replaces_region_alias = language_alternative(
            "eng-GB",
            ProposalAssessment.REPLACES_TARGET,
            "c",
        )
        compatible_preserves = language_alternative(
            "eng",
            ProposalAssessment.PRESERVES_TARGET,
            "d",
        )
        mixed = tuple(
            sorted(
                (preserves, replaces_region),
                key=lambda value: value.alternative_id,
            )
        )
        replacements = tuple(
            sorted(
                (replaces_region, replaces_region_alias),
                key=lambda value: value.alternative_id,
            )
        )
        all_preserve = tuple(
            sorted(
                (preserves, compatible_preserves),
                key=lambda value: value.alternative_id,
            )
        )

        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.KEEP,
                mixed,
                None,
                ("field_policy_preservation",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.KEEP,
                (preserves,),
                None,
                ("positive_preservation_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                replacements,
                None,
                ("field_policy_acceptable_choice",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.PROPOSE_CHANGE,
                (replaces_region,),
                replaces_region.alternative_id,
                ("unique_typed_candidate",),
            )
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE,
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                mixed,
                None,
                ("field_policy_conflict",),
            ).outcome,
        )
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE,
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                (replaces_region,),
                None,
                ("field_policy_conflict",),
            ).outcome,
        )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                all_preserve,
                None,
                ("field_policy_conflict",),
            )

    def test_destination_value_scope_contract_is_exhaustive(self) -> None:
        values = {
            ProposalValueKind.TEXT: ProposalValue(ProposalValueKind.TEXT, "Value"),
            ProposalValueKind.BOOLEAN: ProposalValue(ProposalValueKind.BOOLEAN, True),
            ProposalValueKind.LIST: ProposalValue(ProposalValueKind.LIST, ("Value",)),
            ProposalValueKind.SERIES: ProposalValue(
                ProposalValueKind.SERIES, (TargetSeries("Series"),)
            ),
            ProposalValueKind.COVER: ProposalValue(
                ProposalValueKind.COVER, "cover_" + "a" * 64
            ),
        }
        expected = {
            TargetDestination.TITLE: {(ProposalValueKind.TEXT, ProposalScope.WORK)},
            TargetDestination.SUBTITLE: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.AUTHORS: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.NARRATORS: {(ProposalValueKind.LIST, ProposalScope.EDITION)},
            TargetDestination.SERIES: {(ProposalValueKind.SERIES, ProposalScope.WORK)},
            TargetDestination.GENRES: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.PUBLISHED_YEAR: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.PUBLISHED_DATE: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.PUBLISHER: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.DESCRIPTION: {(ProposalValueKind.TEXT, ProposalScope.WORK)},
            TargetDestination.ISBN: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.ASIN: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.LANGUAGE: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.EXPLICIT: {(ProposalValueKind.BOOLEAN, ProposalScope.EDITION)},
            TargetDestination.TAGS: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.COVER: {(ProposalValueKind.COVER, ProposalScope.COVER)},
        }
        self.assertEqual(set(TargetDestination), set(expected))
        for destination, allowed in expected.items():
            for kind, value in values.items():
                for scope in ProposalScope:
                    candidate_value = (
                        ProposalValue(ProposalValueKind.TEXT, "2001")
                        if (
                            kind is ProposalValueKind.TEXT
                            and destination is TargetDestination.PUBLISHED_YEAR
                        )
                        else ProposalValue(
                            ProposalValueKind.TEXT, "2001-02-03"
                        )
                        if (
                            kind is ProposalValueKind.TEXT
                            and destination is TargetDestination.PUBLISHED_DATE
                        )
                        else value
                    )
                    alternative = ProposalAlternative.create(
                        candidate_value,
                        ProposalAssessment.FILLS_EMPTY,
                        ("local_" + "a" * 64,),
                        ("correlation_" + "a" * 64,),
                        scope,
                        date_event=(
                            DateEvent.MANIFESTATION_PUBLICATION
                            if destination
                            in {
                                TargetDestination.PUBLISHED_YEAR,
                                TargetDestination.PUBLISHED_DATE,
                            }
                            else None
                        ),
                        date_precision=(
                            DatePrecision.YEAR
                            if destination is TargetDestination.PUBLISHED_YEAR
                            else DatePrecision.DAY
                            if destination is TargetDestination.PUBLISHED_DATE
                            else None
                        ),
                    )
                    arguments = (
                        destination,
                        ProposalOutcome.PROPOSE_CHANGE,
                        (alternative,),
                        alternative.alternative_id,
                        (
                            "unique_safe_cover",
                        )
                        if destination is TargetDestination.COVER
                        else ("unique_typed_candidate",),
                    )
                    with self.subTest(destination=destination, kind=kind, scope=scope):
                        if (kind, scope) in allowed:
                            ProposalResult(*arguments)
                        else:
                            with self.assertRaises(ProposalValueError):
                                ProposalResult(*arguments)

    def test_canonical_parse_rejects_forged_destination_payload_and_reasons(self) -> None:
        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(TargetDestination.TITLE, TargetFieldState.MISSING),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.TITLE,
                                "A title",
                                scope=ClaimScope.WORK,
                            ),
                        ),
                    ),
                ),
            )
        )
        document = json.loads(proposal_set.to_json())
        result = next(
            value for value in document["results"] if value["destination"] == "title"
        )
        original = _result(proposal_set, TargetDestination.TITLE).alternatives[0]
        for value, scope in (
            (ProposalValue(ProposalValueKind.BOOLEAN, True), ProposalScope.WORK),
            (ProposalValue(ProposalValueKind.TEXT, "A title"), ProposalScope.COVER),
        ):
            forged = ProposalAlternative.create(
                value,
                original.assessment,
                original.evidence_references,
                original.correlations,
                scope,
            )
            result["alternatives"] = [forged.to_data()]
            result["selected_alternative_id"] = forged.alternative_id
            with self.subTest(value=value.kind, scope=scope):
                with self.assertRaises(ProposalValueError):
                    ProposalSet.from_json(canonical_json(document))
        result["alternatives"] = []
        result["selected_alternative_id"] = None
        for outcome, reasons in (
            ("defer", ["unique_typed_candidate"]),
            ("reject", ["no_supported_evidence"]),
        ):
            result["outcome"] = outcome
            result["reasons"] = reasons
            with self.subTest(outcome=outcome, reasons=reasons):
                with self.assertRaises(ProposalValueError):
                    ProposalSet.from_json(canonical_json(document))

    def test_destination_date_semantics_reject_constructed_and_replayed_mismatch(
        self,
    ) -> None:
        reference = "provider_" + "a" * 64
        correlation = "correlation_" + "b" * 64
        invalid = (
            (
                TargetDestination.TITLE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.DAY,
            ),
            (
                TargetDestination.PUBLISHED_YEAR,
                DateEvent.WORK_FIRST_PUBLICATION,
                DatePrecision.YEAR,
            ),
            (
                TargetDestination.PUBLISHED_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.YEAR,
            ),
            (
                TargetDestination.PUBLISHED_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.MONTH,
            ),
        )
        for destination, event, precision in invalid:
            alternative = ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, "2001"),
                ProposalAssessment.FILLS_EMPTY,
                (reference,),
                (correlation,),
                ProposalScope.WORK,
                date_event=event,
                date_precision=precision,
            )
            with self.subTest(destination=destination):
                with self.assertRaises(ProposalValueError):
                    ProposalResult(
                        destination,
                        ProposalOutcome.PROPOSE_CHANGE,
                        (alternative,),
                        alternative.alternative_id,
                        ("unique_typed_candidate",),
                    )

        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(
                        TargetDestination.TITLE, TargetFieldState.MISSING
                    ),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.TITLE,
                                "A title",
                                scope=ClaimScope.WORK,
                            ),
                        ),
                    ),
                ),
            )
        )
        document = json.loads(proposal_set.to_json())
        result = next(
            value for value in document["results"] if value["destination"] == "title"
        )
        original = _result(proposal_set, TargetDestination.TITLE).alternatives[0]
        forged = ProposalAlternative.create(
            original.value,
            original.assessment,
            original.evidence_references,
            original.correlations,
            original.scope,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=DatePrecision.DAY,
        )
        result["alternatives"] = [forged.to_data()]
        result["selected_alternative_id"] = forged.alternative_id
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(document))

    def test_unique_empty_fill_and_positive_preservation_are_explainable(self) -> None:
        title_claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "A Better Title",
            scope=ClaimScope.WORK,
        )
        provider = _provider("catalogue", "edition-1", (title_claim,))
        empty = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(
                        TargetDestination.TITLE,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(provider,),
            )
        )
        result = _result(empty, TargetDestination.TITLE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertIsNotNone(result.selected_alternative_id)
        matching_claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "Current title",
            scope=ClaimScope.WORK,
        )
        preserved = evaluate_report(
            _report(
                providers=(
                    _provider("catalogue", "edition-1", (matching_claim,)),
                )
            )
        )
        self.assertEqual(
            ProposalOutcome.KEEP,
            _result(preserved, TargetDestination.TITLE).outcome,
        )

    def test_complete_authors_never_drop_a_coauthor(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Ada Author", "Bea Author"),
        )
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        )
        proposal_set = evaluate_report(
            _report(
                fields=_fields(authors=authors),
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        )
        result = _result(proposal_set, TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertIn(
            "insufficient_independent_corroboration", result.reasons
        )

    def test_duplicate_creator_sets_do_not_become_an_authors_proposal(self) -> None:
        local_claims = (
            _local_creator("Ada Author", occurrence=0),
            _local_creator("ADA  AUTHOR", occurrence=1),
        )
        provider_claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Ada Author",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "ADA  AUTHOR",
                scope=ClaimScope.WORK,
                role="author",
                order=1,
            ),
        )
        candidate = finalize_candidate_comparisons(
            ProviderCandidate(
                "edition-1",
                "catalogue-work",
                provider_claims,
            ),
            local_claims,
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=local_claims,
                    providers=(
                        ProviderObservation(
                            "catalogue",
                            ProviderStatus.CANDIDATES,
                            candidates=(candidate,),
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_identity_bound_aliases_do_not_grant_three_author_authority(
        self,
    ) -> None:
        report = _identity_bound_alias_report()
        creator_claims = tuple(
            claim
            for claim in report.providers[0].candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        )
        self.assertTrue(
            all(
                any(link.outcome is Comparison.AGREEMENT for link in claim.links)
                for claim in creator_claims
            )
        )
        result = _result(evaluate_report(report), TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (("Ada Example", "Bob Example"),),
            tuple(alternative.value.value for alternative in result.alternatives),
        )
        self.assertTrue(
            all(
                reference.startswith("local_")
                for alternative in result.alternatives
                for reference in alternative.evidence_references
            )
        )

    def test_incomplete_identity_bindings_preserve_direct_author_evidence(
        self,
    ) -> None:
        report = _partially_bound_direct_author_report()
        provider_references = {
            claim.reference
            for claim in report.providers[0].candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        }
        result = _result(evaluate_report(report), TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (("Ada Example", "Bob Example"),),
            tuple(alternative.value.value for alternative in result.alternatives),
        )
        self.assertTrue(
            provider_references.issubset(
                {
                    reference
                    for alternative in result.alternatives
                    for reference in alternative.evidence_references
                }
            )
        )

    def test_provider_typed_non_author_credits_preserve_complete_authors(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Ada Author",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Bea Author",
                scope=ClaimScope.WORK,
                role="author",
                order=1,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Ed Editor",
                scope=ClaimScope.WORK,
                role="editor",
                order=2,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Ivy Illustrator",
                scope=ClaimScope.WORK,
                role="illustrator",
                order=3,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(("Ada Author", "Bea Author"), result.alternatives[0].value.value)

    def test_ambiguous_provider_author_credits_do_not_become_partial_authors(self) -> None:
        for role in (None, "author", "pseudonym", "collective"):
            with self.subTest(role=role):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CREATOR,
                        "Ada Author",
                        scope=ClaimScope.WORK,
                        role="author",
                    ),
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CONTRIBUTOR,
                        "Ambiguous Credit",
                        scope=ClaimScope.WORK,
                        role=role,
                        order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_incomplete_provider_author_list_is_withheld(self) -> None:
        for state in (FieldState.MALFORMED, FieldState.OVERSIZED):
            with self.subTest(state=state):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CREATOR,
                        "Ada Author",
                        scope=ClaimScope.WORK,
                        role="author",
                    ),
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition-1",
                        kind=ClaimKind.CREATOR,
                        value=None,
                        state=state,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        role="author",
                        raw_order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_unrepresentable_author_like_or_unknown_contributor_withholds_authors(
        self,
    ) -> None:
        for source in ("local", "provider"):
            for role in ("author", "pseudonym", "collective", None):
                with self.subTest(source=source, role=role):
                    if source == "local":
                        claims = (
                            LocalClaim.create(
                                kind=ClaimKind.CREATOR,
                                observed="Ada Author",
                                canonical="Ada Author",
                                excerpt="Ada Author",
                                applicability="not_applicable",
                                checksum="not_applicable",
                                applicability_basis="not_applicable",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            LocalClaim.create(
                                kind=ClaimKind.CONTRIBUTOR,
                                observed="Unrepresentable Credit",
                                canonical=None,
                                excerpt="Unrepresentable Credit",
                                applicability="not_applicable",
                                checksum="not_applicable",
                                applicability_basis="not_applicable",
                                scope=ClaimScope.WORK,
                                state=FieldState.MALFORMED,
                                role=role,
                                occurrence=1,
                            ),
                        )
                        proposal_set = evaluate_report(
                            _report(
                                title=None,
                                fields=_fields(
                                    None,
                                    authors=TargetField(
                                        TargetDestination.AUTHORS,
                                        TargetFieldState.MISSING,
                                    ),
                                ),
                                local_claims=claims,
                            )
                        )
                    else:
                        claims = (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.CREATOR,
                                "Ada Author",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            ProviderClaim.create(
                                provider="catalogue",
                                candidate="edition-1",
                                kind=ClaimKind.CONTRIBUTOR,
                                value=None,
                                state=FieldState.MALFORMED,
                                scope=ClaimScope.WORK,
                                applicability="not_applicable",
                                role=role,
                                raw_order=1,
                            ),
                        )
                        proposal_set = evaluate_report(
                            _report(
                                title=None,
                                fields=_fields(
                                    None,
                                    authors=TargetField(
                                        TargetDestination.AUTHORS,
                                        TargetFieldState.MISSING,
                                    ),
                                ),
                                providers=(
                                    _provider("catalogue", "edition-1", claims),
                                ),
                            )
                        )
                    result = _result(proposal_set, TargetDestination.AUTHORS)
                    self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                    self.assertEqual((), result.alternatives)

    def test_equivalent_observations_coalesce_with_a_deterministic_display(self) -> None:
        providers = (
            _provider(
                "hardcover",
                "edition-1",
                (
                    _claim(
                        "hardcover",
                        "edition-1",
                        ClaimKind.TITLE,
                        "foo bar",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="hardcover",
            ),
            _provider(
                "open_library",
                "edition-2",
                (
                    _claim(
                        "open_library",
                        "edition-2",
                        ClaimKind.TITLE,
                        "Foo  Bar",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="open_library",
            ),
        )
        result = _result(
            evaluate_report(_report(title=None, providers=providers)),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual("Foo Bar", result.alternatives[0].value.value)
        self.assertEqual(2, len(result.alternatives[0].correlations))

    def test_language_policy_preserves_supported_regional_target(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-US",
            )
        )
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "eng",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=providers)),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"en", "eng"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertTrue(
            all(
                alternative.assessment is ProposalAssessment.PRESERVES_TARGET
                for alternative in result.alternatives
            )
        )

    def test_language_policy_rejects_candidate_that_contradicts_target_detail(
        self,
    ) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-US",
            )
        )
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "en-GB",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=providers)),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(("field_policy_conflict",), result.reasons)
        self.assertEqual(
            {
                ProposalAssessment.PRESERVES_TARGET,
                ProposalAssessment.REPLACES_TARGET,
            },
            {alternative.assessment for alternative in result.alternatives},
        )

    def test_language_policy_rejects_sole_target_detail_contradiction(
        self,
    ) -> None:
        controls = (
            ("region", "en-US", "en-GB"),
            ("script", "en-Latn", "en-Cyrl"),
        )
        for name, target, candidate in controls:
            with self.subTest(name=name):
                fields = _fields(
                    language=TargetField(
                        TargetDestination.LANGUAGE,
                        TargetFieldState.VALUE,
                        target,
                    )
                )
                providers = tuple(
                    _provider(
                        provider,
                        f"{provider}-edition",
                        (
                            _claim(
                                provider,
                                f"{provider}-edition",
                                ClaimKind.LANGUAGE,
                                candidate,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                        authority=authority,
                    )
                    for provider, authority in (
                        ("hardcover", "hardcover"),
                        ("open_library", "open_library"),
                    )
                )

                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=providers)
                    ),
                    TargetDestination.LANGUAGE,
                )

                self.assertEqual(
                    ProposalOutcome.UNRESOLVED_CHOICE,
                    result.outcome,
                )
                self.assertEqual(
                    ("field_policy_conflict",),
                    result.reasons,
                )
                self.assertEqual(1, len(result.alternatives))
                self.assertEqual(
                    ProposalAssessment.REPLACES_TARGET,
                    result.alternatives[0].assessment,
                )
                self.assertEqual(
                    2,
                    len(result.alternatives[0].correlations),
                )

    def test_language_policy_keeps_sole_less_specific_evidence(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-Latn-US",
            )
        )
        provider = _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "eng",
                    scope=ClaimScope.EDITION,
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=(provider,))),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual(
            ProposalAssessment.PRESERVES_TARGET,
            result.alternatives[0].assessment,
        )

    def test_equivalent_language_candidates_are_an_acceptable_choice(self) -> None:
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "eng",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )

        proposal_set = evaluate_report(
            _report(fields=fields, providers=providers)
        )
        result = _result(proposal_set, TargetDestination.LANGUAGE)

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(("field_policy_acceptable_choice",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"en", "eng"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertEqual(
            proposal_set,
            ProposalSet.from_json(proposal_set.to_json()),
        )

    def test_deprecated_bh_language_alias_maps_to_favored_bih(self) -> None:
        providers = tuple(
            _provider(
                provider,
                f"{provider}-edition",
                (
                    _claim(
                        provider,
                        f"{provider}-edition",
                        ClaimKind.LANGUAGE,
                        value,
                        scope=ClaimScope.EDITION,
                    ),
                ),
            )
            for provider, value in (("alpha", "bh"), ("beta", "bih"))
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )

        proposal_set = evaluate_report(
            _report(fields=fields, providers=providers)
        )
        result = _result(proposal_set, TargetDestination.LANGUAGE)

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(
            {"bh", "bih"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertEqual(
            proposal_set,
            ProposalSet.from_json(proposal_set.to_json()),
        )

    def test_language_policy_rejects_base_script_region_and_malformed_conflicts(
        self,
    ) -> None:
        controls = (
            ("different_base", "fr", "eng"),
            ("different_script", "en-Latn", "en-Cyrl"),
            ("different_region", "en-US", "en-GB"),
            ("malformed", "en", "english"),
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )
        for name, left, right in controls:
            with self.subTest(name=name):
                providers = (
                    _provider(
                        "alpha",
                        "alpha-edition",
                        (
                            _claim(
                                "alpha",
                                "alpha-edition",
                                ClaimKind.LANGUAGE,
                                left,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                    ),
                    _provider(
                        "beta",
                        "beta-edition",
                        (
                            _claim(
                                "beta",
                                "beta-edition",
                                ClaimKind.LANGUAGE,
                                right,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=providers)
                    ),
                    TargetDestination.LANGUAGE,
                )
                self.assertEqual(
                    ProposalOutcome.UNRESOLVED_CHOICE,
                    result.outcome,
                )
                self.assertEqual(("field_policy_conflict",), result.reasons)

        single_malformed = _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "english",
                    scope=ClaimScope.EDITION,
                ),
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    providers=(single_malformed,),
                )
            ),
            TargetDestination.LANGUAGE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(("field_policy_conflict",), result.reasons)

    def test_private_use_language_region_cannot_be_selected(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )
        controls = (
            (
                "private_use",
                "en-AA",
                ProposalOutcome.UNRESOLVED_CHOICE,
                ("field_policy_conflict",),
                False,
            ),
            (
                "registered",
                "en-US",
                ProposalOutcome.PROPOSE_CHANGE,
                ("unique_typed_candidate",),
                True,
            ),
        )
        for name, value, outcome, reasons, selected in controls:
            with self.subTest(name=name):
                provider = _provider(
                    "catalogue",
                    "edition-1",
                    (
                        _claim(
                            "catalogue",
                            "edition-1",
                            ClaimKind.LANGUAGE,
                            value,
                            scope=ClaimScope.EDITION,
                        ),
                    ),
                )

                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=(provider,))
                    ),
                    TargetDestination.LANGUAGE,
                )

                self.assertEqual(outcome, result.outcome)
                self.assertEqual(reasons, result.reasons)
                self.assertEqual(value, result.alternatives[0].value.value)
                self.assertEqual(
                    selected,
                    result.selected_alternative_id is not None,
                )

    def test_publisher_policy_keeps_distinct_supported_business_levels(self) -> None:
        local_publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Tor Books",
            canonical="Tor Books",
            excerpt="Tor Books",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
        )
        providers = (
            _manifestation_publisher_provider(
                "alpha",
                "alpha-edition",
                ClaimKind.IMPRINT,
                "Tor",
            ),
            _manifestation_publisher_provider(
                "beta",
                "beta-edition",
                ClaimKind.PUBLISHER,
                "Macmillan Publishing Group",
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.VALUE,
                "Tor Books",
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(
                        _explicit_local_ebook_isbn(),
                        local_publisher,
                    ),
                    providers=providers,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertEqual(
            {"Tor Books", "Tor", "Macmillan Publishing Group"},
            {alternative.value.value for alternative in result.alternatives},
        )
        references = {
            reference
            for alternative in result.alternatives
            for reference in alternative.evidence_references
        }
        self.assertIn(local_publisher.reference, references)
        self.assertTrue(
            {
                claim.reference
                for provider in providers
                for claim in provider.candidates[0].claims
                if claim.kind in {ClaimKind.PUBLISHER, ClaimKind.IMPRINT}
            }.issubset(references)
        )

    def test_distinct_publishers_without_a_supported_target_are_acceptable_choice(
        self,
    ) -> None:
        providers = (
            _manifestation_publisher_provider(
                "alpha",
                "alpha-edition",
                ClaimKind.IMPRINT,
                "Tor",
            ),
            _manifestation_publisher_provider(
                "beta",
                "beta-edition",
                ClaimKind.PUBLISHER,
                "Macmillan",
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=providers,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(("field_policy_acceptable_choice",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"Tor", "Macmillan"},
            {alternative.value.value for alternative in result.alternatives},
        )

    def test_publisher_policy_requires_valid_manifestation_bound_evidence(
        self,
    ) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        invalid_or_wrong_scope = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.ISBN,
                        "9780306406157",
                        scope=ClaimScope.EDITION,
                        applicability="ebook",
                    ),
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.PUBLISHER,
                        "<not-a-publisher>",
                        scope=ClaimScope.EDITION,
                        order=1,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-work",
                (
                    _claim(
                        "beta",
                        "beta-work",
                        ClaimKind.PUBLISHER,
                        "Associated Work Publisher",
                        scope=ClaimScope.WORK,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=invalid_or_wrong_scope,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_publisher_policy_does_not_treat_target_syntax_as_evidence(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.VALUE,
                "Macmillan",
            )
        )
        provider = _manifestation_publisher_provider(
            "alpha",
            "alpha-edition",
            ClaimKind.PUBLISHER,
            "Tor Books",
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            ("insufficient_independent_corroboration",),
            result.reasons,
        )

    def test_publisher_policy_excludes_wrong_manifestation_provider(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        provider = _manifestation_publisher_provider(
            "alpha",
            "unrelated-edition",
            ClaimKind.PUBLISHER,
            "Wrong Manifestation Press",
            isbn="9783161484100",
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_publisher_provider_requires_accepted_identifier_anchor(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        controls = (
            (
                "unresolved",
                (_publication_local_isbn(),),
                "unknown",
                IdentifierRelation.UNRESOLVED,
                ProposalOutcome.INELIGIBLE,
            ),
            (
                "associated",
                (_explicit_local_ebook_isbn(),),
                "unknown",
                IdentifierRelation.ASSOCIATED,
                ProposalOutcome.INELIGIBLE,
            ),
            (
                "accepted",
                (_explicit_local_ebook_isbn(),),
                "ebook",
                IdentifierRelation.ACCEPTED,
                ProposalOutcome.PROPOSE_CHANGE,
            ),
        )
        for name, local_claims, applicability, relation, outcome in controls:
            with self.subTest(name=name):
                provider = _manifestation_publisher_provider(
                    "catalogue",
                    "edition-1",
                    ClaimKind.PUBLISHER,
                    "Bound Press",
                    applicability=applicability,
                )
                report = _report(
                    fields=fields,
                    local_claims=local_claims,
                    providers=(provider,),
                )
                provider_relations = {
                    evidence.relation
                    for evidence in report.identifier_evidence
                    if evidence.provider == "catalogue"
                    and evidence.candidate_identity == "edition-1"
                }

                result = _result(
                    evaluate_report(report),
                    TargetDestination.PUBLISHER,
                )

                self.assertEqual({relation}, provider_relations)
                self.assertEqual(outcome, result.outcome)
                self.assertEqual(
                    ("Bound Press",)
                    if outcome is ProposalOutcome.PROPOSE_CHANGE
                    else (),
                    tuple(
                        alternative.value.value
                        for alternative in result.alternatives
                    ),
                )

    def test_publisher_anchor_must_reference_accepted_edition_isbn(self) -> None:
        provider = _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="unknown",
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.WORK,
                    applicability="ebook",
                    order=1,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.PUBLISHER,
                    "Wrong Scope Press",
                    scope=ClaimScope.EDITION,
                    order=2,
                ),
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        report = _report(
            fields=fields,
            local_claims=(_explicit_local_ebook_isbn(),),
            providers=(provider,),
        )
        isbn_claims = {
            claim.reference: claim
            for claim in provider.candidates[0].claims
            if claim.kind is ClaimKind.ISBN
        }
        provider_evidence = {
            (
                isbn_claims[evidence.reference].scope,
                isbn_claims[evidence.reference].applicability,
            ): evidence.relation
            for evidence in report.identifier_evidence
            if evidence.provider == "catalogue"
        }

        result = _result(
            evaluate_report(report),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(
            {
                (ClaimScope.EDITION, "unknown"): IdentifierRelation.ASSOCIATED,
                (ClaimScope.WORK, "ebook"): IdentifierRelation.ACCEPTED,
            },
            provider_evidence,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_selected_epub_publisher_does_not_need_provider_anchor(self) -> None:
        local_publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Selected EPUB Press",
            canonical="Selected EPUB Press",
            excerpt="Selected EPUB Press",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(local_publisher,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            "Selected EPUB Press",
            result.alternatives[0].value.value,
        )
        self.assertEqual(
            (local_publisher.reference,),
            result.alternatives[0].evidence_references,
        )

    def test_ambiguous_local_creator_does_not_become_partial_authors(self) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=name,
                canonical=name,
                excerpt=name,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role=role,
                occurrence=index,
            )
            for index, (name, role) in enumerate(
                (("Ada Author", "author"), ("Unknown Credit", None))
            )
        )
        local_result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=claims,
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, local_result.outcome)
        self.assertEqual((), local_result.alternatives)

    def test_controlled_package_placeholder_never_becomes_an_author_candidate(
        self,
    ) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        for value in (
            "Unknown",
            "UNKNOWN",
            "UnKnOwN",
            "Ｕｎｋｎｏｗｎ",
        ):
            with self.subTest(value=value):
                claim = _local_creator(value)
                report = _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(claim,),
                )
                proposal_set = evaluate_report(report)
                result = _result(proposal_set, TargetDestination.AUTHORS)
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)
                self.assertEqual(("no_supported_evidence",), result.reasons)
                self.assertEqual(claim, report.local_claims[0])
                self.assertEqual(
                    report,
                    MetadataReport.from_json(report.to_json()),
                )
                self.assertEqual(
                    proposal_set,
                    ProposalSet.from_json(proposal_set.to_json()),
                )
                self.assertEqual(proposal_set, evaluate_report(report))

    def test_controlled_placeholder_uses_existing_nonmutating_target_outcome(
        self,
    ) -> None:
        claim = _local_creator("Unknown")
        cases = (
            (
                TargetFieldState.MISSING,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.NULL,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.EMPTY,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.UNLOADED,
                None,
                ProposalOutcome.INELIGIBLE,
                "target_state_unusable",
            ),
            (
                TargetFieldState.VALUE,
                ("Current Author",),
                ProposalOutcome.DEFER,
                "no_positive_preservation_evidence",
            ),
        )
        for state, current, outcome, reason in cases:
            with self.subTest(state=state):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    state,
                                    current,
                                ),
                            ),
                            local_claims=(claim,),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(outcome, result.outcome)
                self.assertEqual((), result.alternatives)
                self.assertEqual((reason,), result.reasons)

    def test_mixed_package_creators_keep_real_author_order(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        cases = (
            (
                (
                    _local_creator("Unknown", occurrence=0),
                    _local_creator("Ada Author", occurrence=1),
                    _local_creator("Bea Writer", occurrence=2),
                ),
                ("Ada Author", "Bea Writer"),
            ),
            (
                (
                    _local_creator("Ada Author", occurrence=0),
                    _local_creator("UNKNOWN", occurrence=1),
                    _local_creator("Bea Writer", occurrence=2),
                ),
                ("Ada Author", "Bea Writer"),
            ),
        )
        for claims, expected in cases:
            with self.subTest(expected=expected):
                report = _report(
                    fields=_fields(None, authors=authors),
                    local_claims=claims,
                )
                result = _result(
                    evaluate_report(report),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
                self.assertEqual(expected, result.alternatives[0].value.value)
                self.assertEqual(
                    tuple(
                        sorted(
                            claim.reference
                            for claim in claims
                            if claim.observed.strip().casefold() != "unknown"
                        )
                    ),
                    result.alternatives[0].evidence_references,
                )
                self.assertEqual(claims, report.local_claims)

    def test_placeholder_rule_is_exact_and_creator_context_specific(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        for value in ("Anonymous", "Anon.", "Unknown Author", "The Unknown"):
            with self.subTest(value=value):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(None, authors=authors),
                            local_claims=(_local_creator(value),),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
                self.assertEqual((value,), result.alternatives[0].value.value)

        opening_unknown = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(
                        _local_creator("Unknown", basis="opening_matter"),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, opening_unknown.outcome)
        self.assertEqual(
            ("Unknown",), opening_unknown.alternatives[0].value.value
        )

        provider_unknown = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    providers=(
                        _provider(
                            "catalogue",
                            "edition-1",
                            (
                                _claim(
                                    "catalogue",
                                    "edition-1",
                                    ClaimKind.CREATOR,
                                    "Unknown",
                                    scope=ClaimScope.WORK,
                                    role="author",
                                ),
                            ),
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, provider_unknown.outcome)
        self.assertEqual(
            ("Unknown",), provider_unknown.alternatives[0].value.value
        )

        mixed_context = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(
                        _local_creator("Unknown", occurrence=0),
                        _local_creator(
                            "Real Author",
                            basis="opening_matter",
                            occurrence=1,
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, mixed_context.outcome)
        self.assertEqual(
            ("Real Author",), mixed_context.alternatives[0].value.value
        )

        unrelated_report = _report(
            title=None,
            fields=_fields(
                None,
                title=TargetField(
                    TargetDestination.TITLE,
                    TargetFieldState.MISSING,
                ),
                authors=authors,
            ),
            local_claims=(
                LocalClaim.create(
                    kind=ClaimKind.TITLE,
                    observed="Unknown",
                    canonical="Unknown",
                    excerpt="Unknown",
                    applicability="not_applicable",
                    basis="package_metadata",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        unrelated = evaluate_report(unrelated_report)
        self.assertEqual(
            ProposalOutcome.PROPOSE_CHANGE,
            _result(unrelated, TargetDestination.TITLE).outcome,
        )
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(unrelated, TargetDestination.AUTHORS).outcome,
        )

    def test_placeholder_changes_only_the_authors_policy_result(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        baseline = evaluate_report(
            _report(fields=_fields(None, authors=authors))
        )
        with_placeholder = evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                local_claims=(_local_creator("Unknown"),),
            )
        )
        self.assertEqual(
            {
                result.destination: result
                for result in baseline.results
                if result.destination is not TargetDestination.AUTHORS
            },
            {
                result.destination: result
                for result in with_placeholder.results
                if result.destination is not TargetDestination.AUTHORS
            },
        )

    def test_ambiguous_local_contributor_does_not_become_partial_narrators(
        self,
    ) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CONTRIBUTOR,
                observed=name,
                canonical=name,
                excerpt=name,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
                role=role,
                occurrence=index,
            )
            for index, (name, role) in enumerate(
                (("Nora Narrator", "narrator"), ("Unknown Credit", None))
            )
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        narrators=TargetField(
                            TargetDestination.NARRATORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=claims,
                )
            ),
            TargetDestination.NARRATORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_ambiguous_provider_contributor_does_not_become_partial_narrators(
        self,
    ) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Nora Narrator",
                scope=ClaimScope.EDITION,
                role="narrator",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Unknown Credit",
                scope=ClaimScope.EDITION,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        narrators=TargetField(
                            TargetDestination.NARRATORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.NARRATORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_populated_subjective_fields_do_not_get_replaced(self) -> None:
        genres = TargetField(
            TargetDestination.GENRES,
            TargetFieldState.VALUE,
            ("Fantasy",),
        )
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.GENRE,
            "Mystery",
            scope=ClaimScope.WORK,
        )
        proposal_set = evaluate_report(
            _report(
                fields=_fields(genres=genres),
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        )
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE,
            _result(proposal_set, TargetDestination.GENRES).outcome,
        )

    def test_independent_corroboration_can_propose_a_populated_replacement(self) -> None:
        providers = tuple(
            _provider(
                name,
                f"{name}-edition",
                (
                    _claim(
                        name,
                        f"{name}-edition",
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority=authority,
            )
            for name, authority in (
                ("hardcover_primary", "hardcover"),
                ("open_library_primary", "open_library"),
            )
        )
        result = _result(
            evaluate_report(_report(providers=providers)),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(2, len(result.alternatives[0].correlations))

    def test_same_adapter_connections_do_not_corroborate_each_other(self) -> None:
        def provider(name: str, authority: str) -> ProviderObservation:
            return _provider(
                name,
                f"{name}-edition",
                (
                    _claim(
                        name,
                        f"{name}-edition",
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority=authority,
            )

        duplicate = _result(
            evaluate_report(
                _report(
                    providers=(
                        provider("hardcover_one", "hardcover"),
                        provider("hardcover_two", "hardcover"),
                    )
                )
            ),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, duplicate.outcome)
        self.assertEqual(1, len(duplicate.alternatives[0].correlations))
        independent = _result(
            evaluate_report(
                _report(
                    providers=(
                        provider("hardcover_one", "hardcover"),
                        provider("open_library_one", "open_library"),
                    )
                )
            ),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, independent.outcome)
        self.assertEqual(2, len(independent.alternatives[0].correlations))

    def test_local_work_subtitle_and_provider_edition_subtitle_are_admitted(self) -> None:
        local = _local_subtitle("Local work subtitle", ClaimScope.WORK)
        local_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(
            {"Local work subtitle"},
            {str(value.value.value) for value in local_result.alternatives},
        )
        self.assertEqual(
            {ProposalScope.WORK},
            {value.scope for value in local_result.alternatives},
        )
        for name in ("hardcover", "open_library"):
            with self.subTest(provider=name):
                provider = _provider(
                    name,
                    "edition-1",
                    (
                        _claim(
                            name,
                            "edition-1",
                            ClaimKind.SUBTITLE,
                            "Edition subtitle",
                            scope=ClaimScope.EDITION,
                            applicability="ebook",
                        ),
                    ),
                )
                provider_result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(
                                subtitle=TargetField(
                                    TargetDestination.SUBTITLE,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(provider,),
                        )
                    ),
                    TargetDestination.SUBTITLE,
                )
                self.assertEqual(
                    {"Edition subtitle"},
                    {str(value.value.value) for value in provider_result.alternatives},
                )
                self.assertEqual(
                    {ProposalScope.EDITION},
                    {value.scope for value in provider_result.alternatives},
                )

    def test_subtitle_scope_admission_is_source_sensitive(self) -> None:
        local_edition = _local_subtitle(
            "Wrong local edition subtitle", ClaimScope.EDITION
        )
        provider_work = _provider(
            "open_library",
            "edition-1",
            (
                _claim(
                    "open_library",
                    "edition-1",
                    ClaimKind.SUBTITLE,
                    "Wrong provider work subtitle",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        local_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    local_claims=(local_edition,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, local_result.outcome)
        self.assertEqual((), local_result.alternatives)
        provider_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    providers=(provider_work,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, provider_result.outcome)
        self.assertEqual((), provider_result.alternatives)

    def test_equal_subtitles_keep_local_and_provider_scopes_separate(self) -> None:
        local = _local_subtitle("Shared subtitle", ClaimScope.WORK)
        provisional = _claim(
            "hardcover",
            "edition-1",
            ClaimKind.SUBTITLE,
            "Shared subtitle",
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        provider_claim = replace(
            provisional,
            links=comparison_links(provisional, (local,)),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    local_claims=(local,),
                    providers=(
                        _provider(
                            "hardcover",
                            "edition-1",
                            (provider_claim,),
                            authority="hardcover",
                        ),
                    ),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(2, len(result.alternatives))
        self.assertEqual(
            {ProposalScope.WORK, ProposalScope.EDITION},
            {alternative.scope for alternative in result.alternatives},
        )
        self.assertTrue(
            all(len(alternative.correlations) == 1 for alternative in result.alternatives)
        )

    def test_candidates_from_one_provider_are_one_correlation_group(self) -> None:
        candidates = tuple(
            ProviderCandidate(
                identity,
                "catalogue-work",
                (
                    _claim(
                        "catalogue",
                        identity,
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
            )
            for identity in ("edition-1", "edition-2")
        )
        provider = ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            candidates,
        )
        result = _result(
            evaluate_report(_report(providers=(provider,))),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(1, len(result.alternatives[0].correlations))
        self.assertIn(
            "insufficient_independent_corroboration", result.reasons
        )

    def test_partial_multiple_series_never_invents_pairing(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Reading order",
                scope=ClaimScope.WORK,
                order=0,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Publication order",
                scope=ClaimScope.WORK,
                order=1,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES_SEQUENCE,
                "2",
                scope=ClaimScope.WORK,
                order=2,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    providers=(_provider("catalogue", "edition-1", claims),)
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_complete_multiple_series_preserves_exact_names_and_sequences(self) -> None:
        def claims(provider: str) -> tuple[ProviderClaim, ...]:
            return (
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES,
                    "Reading Order",
                    scope=ClaimScope.WORK,
                    order=0,
                ),
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES,
                    "Publication Order",
                    scope=ClaimScope.WORK,
                    order=2,
                ),
                ProviderClaim.create(
                    provider=provider,
                    candidate="edition-1",
                    kind=ClaimKind.SERIES_SEQUENCE,
                    value=None,
                    state=FieldState.ABSENT,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    raw_order=3,
                ),
            )

        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(
                        _provider("catalogue", "edition-1", claims("catalogue")),
                        _provider(
                            "open_library",
                            "edition-1",
                            claims("open_library"),
                        ),
                    ),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(
            ProposalOutcome.PROPOSE_CHANGE,
            result.outcome,
            result.to_data(),
        )
        self.assertEqual(
            (
                TargetSeries("Reading Order", "2"),
                TargetSeries("Publication Order", None),
            ),
            result.alternatives[0].value.value,
        )

    def test_unusable_series_sequence_withholds_the_source_aggregate(self) -> None:
        for state in (FieldState.MALFORMED, FieldState.OVERSIZED):
            with self.subTest(state=state):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.SERIES,
                        "Reading order",
                        scope=ClaimScope.WORK,
                    ),
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition-1",
                        kind=ClaimKind.SERIES_SEQUENCE,
                        value=None,
                        state=state,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        raw_order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                series=TargetField(
                                    TargetDestination.SERIES,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.SERIES,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_series_preservation_normalizes_names_but_not_sequences(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Reading Order",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES_SEQUENCE,
                "2",
                scope=ClaimScope.WORK,
                order=1,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.VALUE,
                            (TargetSeries("reading   order", "2"),),
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.KEEP, result.outcome)

    def test_series_preservation_accepts_local_sequence_before_name(self) -> None:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.SERIES_SEQUENCE,
                observed="2",
                canonical="2",
                excerpt="2",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=0,
            ),
            LocalClaim.create(
                kind=ClaimKind.SERIES,
                observed="Reading Order",
                canonical="Reading Order",
                excerpt="Reading Order",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=1,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(),
                    local_claims=claims,
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (TargetSeries("Reading Order", "2"),),
            result.alternatives[0].value.value,
        )

    def test_series_without_sequence_remains_eligible(self) -> None:
        claims = (
            _claim(
                "open_library",
                "edition-1",
                ClaimKind.SERIES,
                "Reading Order",
                scope=ClaimScope.WORK,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.VALUE,
                            (TargetSeries("reading order", None),),
                        ),
                    ),
                    providers=(
                        _provider("open_library", "edition-1", claims),
                    ),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.KEEP, result.outcome)

    def test_work_publication_date_never_votes_for_manifestation_fields(self) -> None:
        evidence = (
            ProviderEvidence(
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                "2001-02-03",
                ClaimScope.WORK,
            ),
        )
        provider = _provider("catalogue", "edition-1", (), evidence)
        fields = _fields(
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR,
                TargetFieldState.VALUE,
                "1999",
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE,
                TargetFieldState.MISSING,
            ),
        )
        proposal_set = evaluate_report(
            _report(fields=fields, providers=(provider,))
        )
        date_result = _result(
            proposal_set, TargetDestination.PUBLISHED_DATE
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, date_result.outcome)

    def test_contradictory_work_relation_date_remains_non_voting(self) -> None:
        proposal_set = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(_contradictory_publication_provider(),),
            )
        )
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(
                proposal_set, TargetDestination.PUBLISHED_YEAR
            ).outcome,
        )
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(
                proposal_set, TargetDestination.PUBLISHED_DATE
            ).outcome,
        )

    def test_month_precision_derives_only_the_stated_year(self) -> None:
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "catalogue", "edition-1", ("2001-02",)
                    ),
                ),
            )
        )
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, year.outcome)
        self.assertEqual("2001", year.alternatives[0].value.value)
        self.assertEqual(DateEvent.MANIFESTATION_PUBLICATION, year.alternatives[0].date_event)
        self.assertEqual(DatePrecision.YEAR, year.alternatives[0].date_precision)
        self.assertEqual(ProposalOutcome.INELIGIBLE, published_date.outcome)

    def test_signed_offset_timestamp_reduces_to_day_without_losing_source_precision(
        self,
    ) -> None:
        lexical = "2001-02-03T04:05:06-04:00"
        provider = _publication_provider(
            "catalogue", "edition-1", (lexical,)
        )
        claim = provider.candidates[0].claims[1]
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(provider,),
            )
        )
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE, published_date.outcome
        )
        self.assertIn(
            "date_refinement_requires_corroboration",
            published_date.reasons,
        )
        self.assertEqual("2001-02-03", published_date.alternatives[0].value.value)
        self.assertEqual(DatePrecision.DAY, published_date.alternatives[0].date_precision)
        self.assertEqual(lexical, claim.date_lexical)
        self.assertEqual(DatePrecision.TIMESTAMP, claim.date_precision)

    def test_naive_timestamps_never_vote_with_any_provider_or_local_date_count(
        self,
    ) -> None:
        naive = "2001-02-03T04:05:06"
        local_date = LocalClaim.create(
            kind=ClaimKind.PUBLICATION_DATE,
            observed="2001",
            canonical="2001",
            excerpt="2001",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=DatePrecision.YEAR,
        )
        for provider_count in (1, 2):
            for include_local_date in (False, True):
                providers = tuple(
                    _publication_provider(
                        f"catalogue_{index}",
                        f"edition-{index}",
                        (naive,),
                    )
                    for index in range(provider_count)
                )
                report = _report(
                    fields=_fields(
                        published_year=TargetField(
                            TargetDestination.PUBLISHED_YEAR,
                            TargetFieldState.MISSING,
                        ),
                        published_date=TargetField(
                            TargetDestination.PUBLISHED_DATE,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(
                        _publication_local_isbn(),
                        *((local_date,) if include_local_date else ()),
                    ),
                    providers=providers,
                )
                replayed = MetadataReport.from_json(report.to_json())
                proposal = evaluate_report(replayed)
                visible_naive = [
                    evidence
                    for provider in replayed.providers
                    for candidate in provider.candidates
                    for evidence in candidate.evidence
                    if evidence.kind
                    is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                    and evidence.value == naive
                    and evidence.date_precision is DatePrecision.TIMESTAMP
                ]
                with self.subTest(
                    providers=provider_count,
                    local_date=include_local_date,
                ):
                    self.assertEqual(
                        proposal.to_json(),
                        evaluate_report(
                            MetadataReport.from_json(replayed.to_json())
                        ).to_json(),
                    )
                    self.assertEqual(provider_count, len(visible_naive))
                    date = _result(
                        proposal, TargetDestination.PUBLISHED_DATE
                    )
                    self.assertTrue(
                        all(
                            set(alternative.evidence_references)
                            <= ({local_date.reference} if include_local_date else set())
                            for alternative in date.alternatives
                        )
                    )
                    if not include_local_date:
                        self.assertEqual(
                            ProposalOutcome.INELIGIBLE,
                            date.outcome,
                        )

    def test_timestamp_offset_eligibility_is_identical_in_report_and_proposals(
        self,
    ) -> None:
        local_date = LocalClaim.create(
            kind=ClaimKind.PUBLICATION_DATE,
            observed="2001-02-03",
            canonical="2001-02-03",
            excerpt="2001-02-03",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=DatePrecision.DAY,
        )
        cases = (
            ("2001-02-03T04:05:06Z", True),
            ("2001-02-03T04:05:06+01:00", True),
            ("2001-02-03T04:05:06+00:00:30", False),
            ("2001-02-03T04:05:06+01:02:03.500000", False),
        )
        for lexical, expected_voting in cases:
            for provider_count in (1, 2):
                for include_local_date in (False, True):
                    providers = tuple(
                        _publication_provider(
                            f"catalogue_{index}",
                            f"edition-{index}",
                            (lexical,),
                        )
                        for index in range(provider_count)
                    )
                    provider_references = {
                        claim.reference
                        for provider in providers
                        for candidate in provider.candidates
                        for claim in candidate.claims
                        if claim.kind is ClaimKind.PUBLICATION_DATE
                    }
                    report = MetadataReport.from_json(
                        _report(
                            fields=_fields(
                                published_year=TargetField(
                                    TargetDestination.PUBLISHED_YEAR,
                                    TargetFieldState.MISSING,
                                ),
                                published_date=TargetField(
                                    TargetDestination.PUBLISHED_DATE,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            local_claims=(
                                _publication_local_isbn(),
                                *((local_date,) if include_local_date else ()),
                            ),
                            providers=providers,
                        ).to_json()
                    )
                    retained_claims = [
                        claim
                        for provider in report.providers
                        for candidate in provider.candidates
                        for claim in candidate.claims
                        if claim.kind is ClaimKind.PUBLICATION_DATE
                    ]
                    retained_text = [
                        evidence
                        for provider in report.providers
                        for candidate in provider.candidates
                        for evidence in candidate.evidence
                        if evidence.kind
                        is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                        and evidence.value == lexical
                    ]
                    proposal = evaluate_report(report)
                    proposal_references = {
                        reference
                        for result in proposal.results
                        for alternative in result.alternatives
                        for reference in alternative.evidence_references
                    }
                    with self.subTest(
                        lexical=lexical,
                        providers=provider_count,
                        local_date=include_local_date,
                    ):
                        self.assertEqual(
                            provider_count if expected_voting else 0,
                            len(retained_claims),
                        )
                        self.assertEqual(
                            0 if expected_voting else provider_count,
                            len(retained_text),
                        )
                        if expected_voting:
                            self.assertTrue(
                                provider_references.issubset(
                                    proposal_references
                                )
                            )
                        else:
                            self.assertTrue(
                                provider_references.isdisjoint(
                                    proposal_references
                                )
                            )

    def test_unresolved_provider_publication_text_is_visible_but_nonselectable(
        self,
    ) -> None:
        evidence = (
            ProviderEvidence(
                ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
                "2001-02-03",
                ClaimScope.EDITION,
            ),
        )
        report = _report(
            title="Target title",
            fields=_fields(
                "Target title",
                authors=TargetField(
                    TargetDestination.AUTHORS,
                    TargetFieldState.VALUE,
                    ("Ada Author",),
                ),
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(),
            providers=(
                replace(
                    _provider("catalogue", "edition-1", (), evidence),
                    query_kind=ProviderQueryKind.WORK,
                ),
            ),
        )
        proposal = evaluate_report(report)
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(proposal, TargetDestination.PUBLISHED_YEAR).outcome,
        )
        retained = report.providers[0].candidates[0].evidence[0]
        self.assertEqual(
            PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
            retained.links[0].publication_date_relation,
        )
        self.assertIsNone(retained.links[0].local_reference)
        self.assertEqual(
            DateEvent.UNKNOWN,
            evidence[0].date_event,
        )

    def test_different_years_remain_conflicting_at_the_year_destination(self) -> None:
        providers = tuple(
            _publication_provider(
                provider,
                f"{provider}-edition",
                (value,),
                authority=provider,
            )
            for provider, value in (
                ("hardcover", "2001-02-03"),
                ("open_library", "2002-03-04"),
            )
        )
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=providers,
            )
        )
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, year.outcome)
        self.assertEqual(
            ["2001", "2002"],
            sorted(str(value.value.value) for value in year.alternatives),
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, published_date.outcome)
        self.assertEqual((), published_date.alternatives)

    def test_full_date_refining_a_populated_year_needs_independent_sources(
        self,
    ) -> None:
        year = TargetField(
            TargetDestination.PUBLISHED_YEAR,
            TargetFieldState.VALUE,
            "2001",
        )
        providers = tuple(
            _publication_provider(
                provider,
                f"{provider}-edition",
                ("2001-02-03",),
                authority=authority,
            )
            for provider, authority in (
                ("alpha", "hardcover"),
                ("beta", "open_library"),
            )
        )
        one_source = _result(
            evaluate_report(
                _report(
                    fields=_fields(published_year=year),
                    local_claims=(_publication_local_isbn(),),
                    providers=(providers[0],),
                )
            ),
            TargetDestination.PUBLISHED_DATE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, one_source.outcome)
        self.assertIn(
            "date_refinement_requires_corroboration",
            one_source.reasons,
        )
        duplicate_authority = _result(
            evaluate_report(
                _report(
                    fields=_fields(published_year=year),
                    local_claims=(_publication_local_isbn(),),
                    providers=(
                        providers[0],
                        replace(providers[1], authority="hardcover"),
                    ),
                )
            ),
            TargetDestination.PUBLISHED_DATE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, duplicate_authority.outcome)
        self.assertIn(
            "date_refinement_requires_corroboration",
            duplicate_authority.reasons,
        )
        two_sources = _result(
            evaluate_report(
                _report(
                    fields=_fields(published_year=year),
                    local_claims=(_publication_local_isbn(),),
                    providers=providers,
                )
            ),
            TargetDestination.PUBLISHED_DATE,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, two_sources.outcome)

    def test_year_only_edition_evidence_proposes_only_published_year(
        self,
    ) -> None:
        proposal_set = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "catalogue", "edition-1", ("2001",)
                    ),
                ),
            )
        )
        year_result = _result(
            proposal_set, TargetDestination.PUBLISHED_YEAR
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, year_result.outcome)
        self.assertEqual("2001", year_result.alternatives[0].value.value)
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(
                proposal_set, TargetDestination.PUBLISHED_DATE
            ).outcome,
        )

    def test_day_and_year_support_year_twice_but_day_once(self) -> None:
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "alpha",
                        "alpha-edition",
                        ("2001-02-03",),
                        authority="hardcover",
                    ),
                    _publication_provider(
                        "beta",
                        "beta-edition",
                        ("2001",),
                        authority="open_library",
                    ),
                ),
            )
        )
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, year.outcome)
        self.assertEqual("2001", year.alternatives[0].value.value)
        self.assertEqual(2, len(year.alternatives[0].correlations))
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE, published_date.outcome
        )
        self.assertEqual(1, len(published_date.alternatives[0].correlations))

    def test_day_and_month_project_shared_month_with_union_support(self) -> None:
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "alpha",
                        "alpha-edition",
                        ("2001-02-03",),
                        authority="hardcover",
                    ),
                    _publication_provider(
                        "beta",
                        "beta-edition",
                        ("2001-02",),
                        authority="open_library",
                    ),
                ),
            )
        )
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, year.outcome)
        self.assertEqual(2, len(year.alternatives[0].correlations))
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, published_date.outcome)
        self.assertEqual(
            ("2001-02", DatePrecision.MONTH),
            (
                published_date.alternatives[0].value.value,
                published_date.alternatives[0].date_precision,
            ),
        )
        self.assertEqual(2, len(published_date.alternatives[0].correlations))
        self.assertEqual(
            2, len(published_date.alternatives[0].evidence_references)
        )

    def test_same_month_day_conflicts_project_only_shared_month(self) -> None:
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "alpha",
                        "alpha-edition",
                        ("2001-02-03",),
                        authority="hardcover",
                    ),
                    _publication_provider(
                        "beta",
                        "beta-edition",
                        ("2001-02-09",),
                        authority="open_library",
                    ),
                ),
            )
        )
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, published_date.outcome)
        self.assertEqual(1, len(published_date.alternatives))
        alternative = published_date.alternatives[0]
        self.assertEqual("2001-02", alternative.value.value)
        self.assertEqual(DateEvent.MANIFESTATION_PUBLICATION, alternative.date_event)
        self.assertEqual(DatePrecision.MONTH, alternative.date_precision)
        self.assertEqual(2, len(alternative.correlations))
        self.assertEqual(2, len(alternative.evidence_references))
        self.assertEqual(
            proposal,
            ProposalSet.from_json(proposal.to_json()),
        )

    def test_different_months_preserve_only_shared_year(self) -> None:
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_year=TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.MISSING,
                    ),
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE,
                        TargetFieldState.MISSING,
                    ),
                ),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    _publication_provider(
                        "alpha",
                        "alpha-edition",
                        ("2001-02-03",),
                        authority="hardcover",
                    ),
                    _publication_provider(
                        "beta",
                        "beta-edition",
                        ("2001-08-09",),
                        authority="open_library",
                    ),
                ),
            )
        )
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, year.outcome)
        self.assertEqual("2001", year.alternatives[0].value.value)
        self.assertEqual(2, len(year.alternatives[0].correlations))
        self.assertEqual(ProposalOutcome.INELIGIBLE, published_date.outcome)
        self.assertEqual((), published_date.alternatives)
    def test_cover_requires_one_complete_safe_artifact(self) -> None:
        artifact = CoverArtifact.create(
            b"cover", b"preview", "image/png", 10, 20
        )
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.RETRIEVED,
            artifact,
            provenance=CoverProvenance(
                "catalogue",
                CoverDesignation.WORK,
                "catalogue-work",
            ),
        )
        proposal_set = evaluate_report(_report(covers=(source,)))
        result = _result(proposal_set, TargetDestination.COVER)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        timeout = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:timeout"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.TIMEOUT,
            timeout_scope="attempted",
            provenance=CoverProvenance(
                "catalogue",
                CoverDesignation.WORK,
                "catalogue-work",
            ),
        )
        incomplete = _result(
            evaluate_report(_report(covers=(source, timeout))),
            TargetDestination.COVER,
        )
        self.assertEqual(ProposalOutcome.DEFER, incomplete.outcome)
        self.assertIn("cover_gallery_incomplete", incomplete.reasons)

    def test_verified_incumbent_and_alternative_require_owner_cover_choice(self) -> None:
        incumbent = CoverArtifact.create(
            b"incumbent", b"incumbent-preview", "image/png", 10, 20
        )
        alternative = CoverArtifact.create(
            b"alternative", b"alternative-preview", "image/png", 12, 24
        )
        sources = (
            CoverSource(
                cover_source_id(CoverSourceKind.ABS, "current"),
                CoverSourceKind.ABS,
                CoverSourceState.RETRIEVED,
                incumbent,
            ),
            CoverSource(
                cover_source_id(CoverSourceKind.EPUB, "embedded"),
                CoverSourceKind.EPUB,
                CoverSourceState.RETRIEVED,
                alternative,
            ),
        )
        fields = tuple(
            TargetField(
                value.destination,
                TargetFieldState.VALUE,
                incumbent.artifact_id,
            )
            if value.destination is TargetDestination.COVER
            else value
            for value in _fields("Current title")
        )
        result = _result(
            evaluate_report(_report(fields=fields, covers=sources)),
            TargetDestination.COVER,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            ("current_cover_verified", "owner_cover_choice_required"),
            result.reasons,
        )
        self.assertEqual(2, len(result.alternatives))

    def test_populated_unverified_cover_is_manual_only_with_typed_incumbent_gap(self) -> None:
        artifact = CoverArtifact.create(
            b"cover", b"preview", "image/png", 10, 20
        )
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.RETRIEVED,
            artifact,
            provenance=CoverProvenance(
                "catalogue",
                CoverDesignation.WORK,
                "catalogue-work",
            ),
        )
        fields = _fields(
            cover=TargetField(
                TargetDestination.COVER,
                TargetFieldState.VALUE,
                "current-cover",
            )
        )
        result = _result(
            evaluate_report(_report(fields=fields, covers=(source,))),
            TargetDestination.COVER,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            (
                "current_cover_unavailable",
                "current_cover_unverified",
                "owner_cover_choice_required",
            ),
            result.reasons,
        )
        self.assertEqual(1, len(result.alternatives))
        self.assertIsNone(result.selected_alternative_id)

    def test_non_google_markup_description_is_ineligible(self) -> None:
        claim = _claim("catalogue", "edition-1", ClaimKind.DESCRIPTION, "Summary <em>", scope=ClaimScope.WORK)
        result = _result(
            evaluate_report(
                _report(
                    providers=(_provider("catalogue", "edition-1", (claim,)),)
                )
            ),
            TargetDestination.DESCRIPTION,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)

    def test_transformed_google_visible_angle_characters_remain_eligible(self) -> None:
        claim = _claim(
            "google",
            "edition-1",
            ClaimKind.DESCRIPTION,
            "2 < 3",
            scope=ClaimScope.WORK,
        )
        result = _result(
            evaluate_report(
                _report(
                    providers=(
                        _provider(
                            "google",
                            "edition-1",
                            (claim,),
                            (
                                ProviderEvidence(
                                    ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                                    "google-books-html-visible-text-v1",
                                    ClaimScope.WORK,
                                ),
                            ),
                        ),
                    )
                )
            ),
            TargetDestination.DESCRIPTION,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual("2 < 3", result.alternatives[0].value.value)

    def test_provider_subtitle_and_asin_require_exact_ebook_applicability(self) -> None:
        cases = (
            (TargetDestination.SUBTITLE, ClaimKind.SUBTITLE, "Edition subtitle"),
            (TargetDestination.ASIN, ClaimKind.ASIN, "B012345678"),
        )
        for destination, kind, value in cases:
            for applicability in ("ebook", "print", "unknown", "ambiguous"):
                claim = _claim(
                    "catalogue",
                    "edition-1",
                    kind,
                    value,
                    scope=ClaimScope.EDITION,
                    applicability=applicability,
                )
                result = _result(
                    evaluate_report(
                        _report(
                            providers=(
                                _provider(
                                    "catalogue",
                                    "edition-1",
                                    (claim,),
                                ),
                            )
                        )
                    ),
                    destination,
                )
                with self.subTest(
                    destination=destination,
                    applicability=applicability,
                ):
                    self.assertEqual(
                        (
                            ProposalOutcome.PROPOSE_CHANGE
                            if applicability == "ebook"
                            else ProposalOutcome.INELIGIBLE
                        ),
                        result.outcome,
                    )

    def test_long_descriptions_compare_without_title_bounds(self) -> None:
        matching = "A complete description " + "x" * 700
        differing = "A different description " + "y" * 700
        field = TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.VALUE,
            matching,
        )
        for candidate, expected in (
            (matching, ProposalOutcome.KEEP),
            (differing, ProposalOutcome.UNRESOLVED_CHOICE),
        ):
            with self.subTest(candidate=candidate[:12]):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(description=field),
                            providers=(
                                _provider(
                                    "catalogue",
                                    "edition-1",
                                    (
                                        _claim(
                                            "catalogue",
                                            "edition-1",
                                            ClaimKind.DESCRIPTION,
                                            candidate,
                                            scope=ClaimScope.WORK,
                                        ),
                                    ),
                                ),
                            ),
                        )
                    ),
                    TargetDestination.DESCRIPTION,
                )
                self.assertEqual(expected, result.outcome)

    def test_malformed_target_is_ineligible_even_with_evidence(self) -> None:
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.LANGUAGE,
            "en",
            scope=ClaimScope.EDITION,
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MALFORMED,
            )
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    providers=(
                        _provider("catalogue", "edition-1", (claim,)),
                    ),
                )
            ),
            TargetDestination.LANGUAGE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)

    def test_identifier_and_explicit_rules_do_not_manufacture_evidence(self) -> None:
        isbn = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.ISBN,
            "0306406152",
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        fields = _fields(
            isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.MISSING
            ),
            explicit=TargetField(
                TargetDestination.EXPLICIT,
                TargetFieldState.VALUE,
                False,
            ),
        )
        proposal_set = evaluate_report(
            _report(
                fields=fields,
                providers=(_provider("catalogue", "edition-1", (isbn,)),),
            )
        )
        isbn_result = _result(proposal_set, TargetDestination.ISBN)
        self.assertEqual(ProposalOutcome.DEFER, isbn_result.outcome)
        self.assertEqual((), isbn_result.alternatives)
        self.assertIn(
            "identifier_applicability_unresolved", isbn_result.reasons
        )
        self.assertEqual(
            ProposalOutcome.DEFER,
            _result(proposal_set, TargetDestination.EXPLICIT).outcome,
        )

    def test_explicit_local_ebook_isbn_remains_eligible(self) -> None:
        isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(isbn,),
                )
            ),
            TargetDestination.ISBN,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual("0306406152", result.alternatives[0].value.value)

    def test_isbn_family_observations_form_one_empty_target_alternative(self) -> None:
        local = _explicit_local_ebook_isbn()
        provider = _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "0306406152",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                    order=1,
                ),
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                    providers=(provider,),
                )
            ),
            TargetDestination.ISBN,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual("9780306406157", result.alternatives[0].value.value)
        self.assertEqual(ProposalAssessment.FILLS_EMPTY, result.alternatives[0].assessment)
        self.assertEqual(3, len(result.alternatives[0].evidence_references))

    def test_non_review_result_cannot_carry_any_selection(self) -> None:
        result = _result(evaluate_report(_report()), TargetDestination.TITLE)
        with self.assertRaises(ProposalValueError):
            type(result)(
                result.destination,
                result.outcome,
                result.alternatives,
                "alternative_" + "f" * 64,
                result.reasons,
                result.dependencies,
            )

    def test_invalid_unicode_is_a_closed_value_error(self) -> None:
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json("\ud800")


if __name__ == "__main__":
    unittest.main()
