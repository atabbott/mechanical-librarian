"""Proposal corroboration: independent provider support, exact complete
creator membership, replayed target-binding tampering, and the
authority the case detail projects.
"""

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

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
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderIdentityBinding,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    provider_lane_candidate_reference,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalResult,
    ProposalSet,
    _target_backed_lookup_contains,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _isbn_lane_observation,
    _local_creator,
    _provider,
    _publication_local_isbn,
    _real_tigers_corroboration_report,
    _report,
    _result,
)


class ProposalCorroborationTests(unittest.TestCase):
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
            cast(
                set[str],
                {isbn.canonical, isbn_10.canonical, non_valid_isbn.canonical},
            )
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


if __name__ == "__main__":
    unittest.main()
