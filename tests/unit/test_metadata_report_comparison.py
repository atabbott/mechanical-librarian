"""Metadata-report comparison: closed title methods, conservative
creator order and alias collapse, and date relations that keep both
lexicals and their shared precision.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

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
    canonical_json,
    comparison_links,
    default_target_fields,
    finalize_candidate_comparisons,
    normalize_date_value,
    normalize_title,
    publication_date_relation,
)


class MetadataReportComparisonTests(unittest.TestCase):
    def test_comparison_links_are_complete_per_local_claim_and_canonical(self) -> None:
        agree = LocalClaim.create(kind=ClaimKind.TITLE, observed="Shared title", canonical="Shared title", excerpt="Shared title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        disagree = LocalClaim.create(kind=ClaimKind.TITLE, observed="Other title", canonical="Other title", excerpt="Other title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        invalid = LocalClaim.create(kind=ClaimKind.TITLE, observed="unsafe-source-text-sha256:" + "a" * 64, canonical=None, excerpt="unsafe-source-text-sha256:" + "a" * 64, applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, state=FieldState.UNSUPPORTED)
        edition = LocalClaim.create(kind=ClaimKind.TITLE, observed="Shared title", canonical="Shared title", excerpt="Shared title", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.EDITION)
        author = LocalClaim.create(kind=ClaimKind.CREATOR, observed="Ada", canonical="Ada", excerpt="Ada", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, role="author")
        provider = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.TITLE, value="Shared title", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable")
        links = tuple(sorted((ComparisonLink(agree.reference, Comparison.AGREEMENT, ComparisonMethod.EXACT), ComparisonLink(disagree.reference, Comparison.DISAGREEMENT), ComparisonLink(invalid.reference, Comparison.NOT_COMPARABLE), ComparisonLink(edition.reference, Comparison.NOT_COMPARABLE)), key=lambda link: cast(str, link.local_reference)))
        linked = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.TITLE, value="Shared title", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable", links=links)
        report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "work-candidate", (linked,)),)),))
        changed_work = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(agree, disagree, invalid, edition, author), local_reasons=("opening_title",), providers=(ProviderObservation("catalogue", ProviderStatus.CANDIDATES, candidates=(ProviderCandidate("candidate", "different-work", (linked,)),)),))
        self.assertEqual(provider.links, ())
        self.assertEqual({link.outcome for link in linked.links}, {Comparison.AGREEMENT, Comparison.DISAGREEMENT, Comparison.NOT_COMPARABLE})
        self.assertNotIn(author.reference, {link.local_reference for link in linked.links})
        self.assertNotEqual(report.report_id, changed_work.report_id)
        self.assertEqual(MetadataReport.from_json(changed_work.to_json()), changed_work)
        contributor = LocalClaim.create(kind=ClaimKind.CREATOR, observed="Ada", canonical="Ada", excerpt="Ada", applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK, role="translator")
        creator = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.CREATOR, value="Ada", state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable", role="author")
        self.assertEqual(
            {link.local_reference: link.outcome for link in comparison_links(creator, (author, contributor))},
            {author.reference: Comparison.AGREEMENT, contributor.reference: Comparison.NOT_COMPARABLE},
        )
        data = report.to_data()
        data["providers"][0]["candidates"][0]["claims"][0]["links"] = []
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))
        data = report.to_data()
        data["providers"][0]["candidates"][0]["claims"][0]["links"][0]["outcome"] = "disagreement"
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(data))

    def test_title_comparison_methods_are_closed_and_preserve_diacritics(self) -> None:
        def local(
            value: str, kind: ClaimKind = ClaimKind.TITLE
        ) -> LocalClaim:
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

        cases = (
            ("Exact Title", "Exact Title", ComparisonMethod.EXACT),
            (
                "The Road—Translated",
                "The Road—Translated",
                ComparisonMethod.EXACT,
            ),
            ("CAFE\u0301   SOCIETY", "café society", ComparisonMethod.UNICODE_CASE_WHITESPACE),
            ("The “Long\u00a0Road”—Home", 'the "long road"-home', ComparisonMethod.TYPOGRAPHIC_PUNCTUATION),
        )
        for provider_value, local_value, method in cases:
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
            with self.subTest(method=method):
                self.assertEqual(
                    comparison_links(provider, (claim,)),
                    (
                        ComparisonLink(
                            claim.reference, Comparison.AGREEMENT, method
                        ),
                    ),
                )

        subtitle_claim = local(
            "the road-abridgment", ClaimKind.SUBTITLE
        )
        subtitle_provider = ProviderClaim.create(
            provider="catalogue",
            candidate="candidate",
            kind=ClaimKind.SUBTITLE,
            value="The Road—Abridgment",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
        )
        self.assertIs(
            comparison_links(subtitle_provider, (subtitle_claim,))[0].outcome,
            Comparison.DISAGREEMENT,
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
            with self.subTest(provider=provider_value, local=local_value):
                self.assertEqual(
                    comparison_links(provider, (claim,))[0].outcome,
                    Comparison.DISAGREEMENT,
                )

        for provider_value, local_value in (
            ("The ‘Road’", "the 'road'"),
            ("The “Road”", 'the "road"'),
            ("The “Long ‘Road’”", 'the "long \'road\'"'),
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
            with self.subTest(paired=provider_value):
                self.assertIs(link.outcome, Comparison.AGREEMENT)
                self.assertIs(
                    link.method, ComparisonMethod.TYPOGRAPHIC_PUNCTUATION
                )

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
        self.assertEqual(reopened, replay_report)
        self.assertIs(
            reopened.providers[0].candidates[0].claims[0].links[0].method,
            ComparisonMethod.TYPOGRAPHIC_PUNCTUATION,
        )

    def test_complete_creator_order_and_comma_inversion_are_conservative(self) -> None:
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
        self.assertEqual(
            tuple(
                link.method
                for claim in ordered.claims
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            ),
            (
                ComparisonMethod.UNICODE_CASE_WHITESPACE,
                ComparisonMethod.EXACT,
            ),
        )
        for values in (
            ("Ada Example",),
            ("Bob Example", "Ada Example"),
            ("Ada Example", "Bob Example", "Bob Example"),
        ):
            candidate = finalize_candidate_comparisons(
                ProviderCandidate(
                    "candidate",
                    "work",
                    tuple(provider(value, index) for index, value in enumerate(values)),
                ),
                locals_,
            )
            with self.subTest(values=values):
                self.assertFalse(
                    any(
                        link.outcome is Comparison.AGREEMENT
                        for claim in candidate.claims
                        for link in claim.links
                    )
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
            with self.subTest(value=value):
                self.assertIs(link.method, expected)
                self.assertIs(
                    link.outcome,
                    (
                        Comparison.AGREEMENT
                        if expected is not ComparisonMethod.NONE
                        else Comparison.DISAGREEMENT
                    ),
                )
        for organization in ("Library", "Libraries"):
            organization_local = (
                local(f"British {organization}"),
            )
            organization_candidate = finalize_candidate_comparisons(
                ProviderCandidate(
                    "candidate",
                    "work",
                    (provider(f"{organization}, British", 0),),
                ),
                organization_local,
            )
            with self.subTest(organization=organization):
                self.assertIs(
                    organization_candidate.claims[0].links[0].outcome,
                    Comparison.DISAGREEMENT,
                )

    def test_identity_bound_aliases_collapse_only_one_complete_creator_set(self) -> None:
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
            for index, value in enumerate(
                ("A. Example", "Ada Example", "Bob Example")
            )
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
        self.assertEqual(
            tuple(
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
            ),
            (
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
        self.assertEqual(replay, MetadataReport.from_json(replay.to_json()))
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
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in contradictory.claims
                for link in claim.links
            )
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
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in reversed_bound.claims
                for link in claim.links
            )
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
        self.assertEqual(
            (
                ComparisonMethod.EXACT,
                ComparisonMethod.EXACT,
            ),
            tuple(
                link.method
                for claim in partial.claims
                for link in claim.links
                if link.outcome is Comparison.AGREEMENT
            )
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
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in partial_alias.claims
                for link in claim.links
            )
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
        self.assertEqual(
            partial_replay,
            MetadataReport.from_json(partial_replay.to_json()),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(partial_replay, lambda: True)
            self.assertEqual(
                partial_replay,
                MetadataReport.from_json(
                    repository.get(partial_replay.report_id)
                ),
            )
            repository.close()
        duplicate_ordinal = replace(bindings[1], source_ordinal=0)
        with self.assertRaises(ReportValueError):
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
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(canonical_json(tampered))
        ordinal_tampered = replay.to_data()
        ordinal_tampered["providers"][0]["candidates"][0][
            "identity_bindings"
        ][1]["source_ordinal"] = 0
        serialized_tamper = canonical_json(ordinal_tampered)
        with self.assertRaises(ReportValueError):
            MetadataReport.from_json(serialized_tamper)
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(replay, lambda: True)
            repository._connection.execute(
                "DROP TRIGGER metadata_reports_no_update"
            )
            repository._connection.execute(
                "UPDATE metadata_reports SET document=?,document_digest=?",
                (
                    serialized_tamper,
                    hashlib.sha256(serialized_tamper.encode()).hexdigest(),
                ),
            )
            with self.assertRaises(MetadataReportError):
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
            for index, value in enumerate(
                ("Ada Example", "ADA  EXAMPLE", "Bob Example")
            )
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
        self.assertFalse(
            any(
                link.outcome is Comparison.AGREEMENT
                for claim in duplicated.claims
                for link in claim.links
            )
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
            with self.subTest(
                provider_count=len(provider_values),
                local_count=len(local_values),
            ):
                self.assertFalse(
                    any(
                        link.outcome is Comparison.AGREEMENT
                        for claim in duplicate_set.claims
                        for link in claim.links
                    )
                )

    def test_date_comparison_normalizes_both_lexicals_without_losing_precision(
        self,
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
        with self.assertRaises(ReportValueError):
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
        self.assertEqual(
            {
                zulu.reference: Comparison.AGREEMENT,
                different.reference: Comparison.AGREEMENT,
                day.reference: Comparison.AGREEMENT,
            },
            {
                link.local_reference: link.outcome
                for link in comparison_links(
                    negative_offset,
                    (zulu, different, day),
                )
            },
        )
        coarse_links = {
            link.local_reference: link
            for link in comparison_links(negative_offset, (month, year))
        }
        self.assertEqual(
            ComparisonLink(
                month.reference,
                Comparison.AGREEMENT,
                ComparisonMethod.DATE_PRECISION_COMPATIBILITY,
                PublicationDateRelation.MONTH_COMPATIBLE,
                DatePrecision.MONTH,
            ),
            coarse_links[month.reference],
        )
        self.assertEqual(
            ComparisonLink(
                year.reference,
                Comparison.AGREEMENT,
                ComparisonMethod.DATE_PRECISION_COMPATIBILITY,
                PublicationDateRelation.YEAR_COMPATIBLE,
                DatePrecision.YEAR,
            ),
            coarse_links[year.reference],
        )
        self.assertEqual(
            (
                ComparisonLink(
                    positive_offset.reference,
                    Comparison.AGREEMENT,
                    ComparisonMethod.EXACT,
                    PublicationDateRelation.EXACT,
                    DatePrecision.DAY,
                ),
            ),
            comparison_links(
                provider("2024-03-05T10:30:00Z"),
                (positive_offset,),
            ),
        )
        self.assertEqual(
            (
                ComparisonLink(
                    rollover.reference,
                    Comparison.DISAGREEMENT,
                    ComparisonMethod.NONE,
                    PublicationDateRelation.FINER_CONFLICT,
                    DatePrecision.MONTH,
                ),
            ),
            comparison_links(
                provider("2024-03-06T00:30:00+01:00"),
                (rollover,),
            ),
        )
        stated_day_cases = (
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
        )
        for (
            left,
            left_precision,
            right,
            right_precision,
            relation,
            shared,
        ) in stated_day_cases:
            with self.subTest(left=left, right=right):
                expected = (relation, shared)
                self.assertEqual(
                    expected,
                    publication_date_relation(
                        left_value=left,
                        left_event=DateEvent.MANIFESTATION_PUBLICATION,
                        left_precision=left_precision,
                        right_value=right,
                        right_event=DateEvent.MANIFESTATION_PUBLICATION,
                        right_precision=right_precision,
                    ),
                )
                self.assertEqual(
                    expected,
                    publication_date_relation(
                        left_value=right,
                        left_event=DateEvent.MANIFESTATION_PUBLICATION,
                        left_precision=right_precision,
                        right_value=left,
                        right_event=DateEvent.MANIFESTATION_PUBLICATION,
                        right_precision=left_precision,
                    ),
                )
        self.assertEqual((), comparison_links(negative_offset, (naive,)))
        with self.assertRaises(ReportValueError):
            comparison_links(
                provider("2024-03-05T14:30:00"),
                (zulu,),
            )
        self.assertEqual("2024-03-05T14:30:00Z", zulu.observed)
        self.assertEqual("2024-03-05T14:30:00+00:00", zulu.canonical)
        self.assertEqual(
            "2024-03-05T10:30:00-04:00",
            negative_offset.date_lexical,
        )

    def test_publication_date_relation_preserves_shared_precision_and_conflict(
        self,
    ) -> None:
        cases = (
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
        )
        for (
            left,
            left_precision,
            right,
            right_precision,
            expected_relation,
            expected_shared,
        ) in cases:
            with self.subTest(left=left, right=right):
                self.assertEqual(
                    (expected_relation, expected_shared),
                    publication_date_relation(
                        left_value=left,
                        left_event=DateEvent.MANIFESTATION_PUBLICATION,
                        left_precision=left_precision,
                        right_value=right,
                        right_event=DateEvent.MANIFESTATION_PUBLICATION,
                        right_precision=right_precision,
                    ),
                )
        self.assertEqual(
            (PublicationDateRelation.EVENT_INCOMPATIBLE, None),
            publication_date_relation(
                left_value="2021",
                left_event=DateEvent.WORK_FIRST_PUBLICATION,
                left_precision=DatePrecision.YEAR,
                right_value="2021",
                right_event=DateEvent.MANIFESTATION_PUBLICATION,
                right_precision=DatePrecision.YEAR,
            ),
        )
        self.assertEqual(
            (PublicationDateRelation.MANIFESTATION_INCOMPATIBLE, None),
            publication_date_relation(
                left_value="2021",
                left_event=DateEvent.MANIFESTATION_PUBLICATION,
                left_precision=DatePrecision.YEAR,
                right_value="2021",
                right_event=DateEvent.MANIFESTATION_PUBLICATION,
                right_precision=DatePrecision.YEAR,
                manifestation_compatible=False,
            ),
        )

    def test_maximum_description_comparison_is_field_appropriate(self) -> None:
        local_value = "d" * 512
        provider_value = "p" * MAX_DESCRIPTION_CHARACTERS
        local = LocalClaim.create(kind=ClaimKind.DESCRIPTION, observed=local_value, canonical=local_value, excerpt=local_value, applicability="not_applicable", checksum="not_applicable", applicability_basis="not_applicable", scope=ClaimScope.WORK)
        bare = ProviderClaim.create(provider="catalogue", candidate="candidate", kind=ClaimKind.DESCRIPTION, value=provider_value, state=FieldState.VALID, scope=ClaimScope.WORK, applicability="not_applicable")
        self.assertEqual(comparison_links(bare, (local,)), (ComparisonLink(local.reference, Comparison.DISAGREEMENT),))


if __name__ == "__main__":
    unittest.main()
