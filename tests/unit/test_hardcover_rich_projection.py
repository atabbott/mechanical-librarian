"""Hardcover rich-evidence projection: contributor roles, creator
identity binding, series slots, covers, and field-local bounds.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from typing import cast

import httpx

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    complete_creator_identity_bindings,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    evaluate_report,
)
from tests.support.hardcover_evidence import (
    hardcover_envelope,
    isbn_claim,
    isbn_lookup,
    observe_contributions,
    title_claim,
    work_lookup,
)


class HardcoverRichProjectionTests(unittest.TestCase):
    def test_edition_overflow_rejects_hostile_creator_identity_bindings(self) -> None:
        rows = [{"id": index, "book_id": 10} for index in range(1, 7)]
        base = {
            "id": 10,
            "title": "Anchored work",
            "contributions": [
                {"id": 1, "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}},
            ],
        }
        variants = (
            [
                {"id": 1, "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}},
                {"id": 1, "contribution": "Author", "author": {"id": 8, "name": "Bea Example"}},
            ],
            [
                {"id": 1, "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}},
                {"id": 2, "contribution": "Author", "author": {"id": 7, "name": "Ada Other"}},
            ],
            [
                {"id": 1, "contribution": "Translator", "author": {"id": 7, "name": "Ada Example"}},
            ],
            [
                {"id": 1, "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}},
                {"id": 2, "contribution": "Author", "author": {"id": 8, "name": "ada example"}},
            ],
        )
        for contributions in variants:
            payload = hardcover_envelope(rows, [{**base, "contributions": contributions}])
            with self.subTest(contributions=contributions), self.assertRaises(ValueError):
                hardcover_metadata._parse_edition_enrichment_overflow(
                    json.dumps(payload).encode(), "catalogue"
                )
        for malformed_rows in (
            [{"id": 1, "book_id": 10}] * 6,
            [{"id": index, "book_id": 11} for index in range(1, 7)],
        ):
            with self.subTest(rows=malformed_rows), self.assertRaises(ValueError):
                hardcover_metadata._parse_edition_enrichment_overflow(
                    json.dumps(hardcover_envelope(malformed_rows, [base])).encode(),
                    "catalogue",
                )

    def test_edition_overflow_probe_has_exact_five_six_seven_boundary(self) -> None:
        book = [{
            "id": 10,
            "title": "Anchored work",
            "contributions": [{
                "id": 1, "contribution": "Author",
                "author": {"id": 7, "name": "Ada Example"},
            }],
        }]
        for count in (5, 6, 7):
            payload = json.dumps(hardcover_envelope(
                [{"id": index, "book_id": 10} for index in range(1, count + 1)],
                book,
            )).encode()
            with self.subTest(count=count):
                if count == 7:
                    with self.assertRaises(hardcover_metadata._CandidateLimit):
                        hardcover_metadata._parse_edition_enrichment_overflow(payload, "catalogue")
                else:
                    value = hardcover_metadata._parse_edition_enrichment_overflow(payload, "catalogue")
                    self.assertEqual(count == 6, value is not None)

    def test_mixed_discovery_retains_only_the_work_overflow_witness(self) -> None:
        fields = tuple(
            TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada Example",))
            if field.destination is TargetDestination.AUTHORS else field
            for field in default_target_fields("Current title")
        )
        title, isbn = title_claim(), isbn_claim()
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields),
            (title, isbn),
        )
        witness = hardcover_metadata._parse_edition_enrichment_overflow(
            json.dumps(hardcover_envelope(
                [{"id": index, "book_id": 10} for index in range(1, 7)],
                [{"id": 10, "title": "Current title", "contributions": [{"id": 1, "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}}]}],
            )).encode(),
            "catalogue",
        )
        assert witness is not None
        work = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK)
        isbn_lane = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
        observation = provider_observation(
            "catalogue",
            (
                ProviderLaneResult(work, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, edition_enrichment_overflow=witness),
                ProviderLaneResult(isbn_lane, ProviderStatus.NO_MATCH),
            ),
        )
        self.assertEqual(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, observation.status)
        self.assertEqual(witness, observation.edition_enrichment_overflow)
        self.assertEqual((), observation.candidates)
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(title, isbn),
            local_reasons=("opening_matter_isbn",),
            providers=(observation,),
        )
        self.assertEqual(report, MetadataReport.from_json(report.to_json()))

    def test_rich_field_boundaries_are_local_and_public_safe(self) -> None:
        row: dict[str, object] = {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", "asin": "B012345678", "title": "T" * 513, "subtitle": "Edition subtitle", "release_date": "not-a-date", "publisher": None, "language": None, "contributions": [{"id": 1, "contribution": "Author", "author": {"id": 1, "name": "Ada"}}, {"id": 2, "contribution": "/Users/private", "author": {"id": 2, "name": "Bea"}}]}
        book: dict[str, object] = {"id": 1, "title": "/Users/private/title", "description": "D" * 16385, "contributions": [{"id": 3, "contribution": "Translator", "author": {"id": 3, "name": "Cy"}}], "book_series": [{"id": 4, "position": 1.5, "series": {"id": 4, "name": "Series"}}], "taggings": []}
        payload = json.dumps(hardcover_envelope([row], [book])).encode()
        claims = hardcover_metadata._parse_candidates(payload, "catalogue", (isbn_claim(),)).__getitem__(0).claims
        by_kind = {claim.kind: claim for claim in claims}
        self.assertEqual(by_kind[ClaimKind.ASIN].state.value, "valid")
        self.assertEqual(by_kind[ClaimKind.PUBLICATION_DATE].state.value, "malformed")
        self.assertEqual(by_kind[ClaimKind.DESCRIPTION].state.value, "oversized")
        self.assertEqual(by_kind[ClaimKind.TITLE].state.value, "malformed")
        self.assertEqual(by_kind[ClaimKind.SERIES_SEQUENCE].value, "1.5")
        self.assertEqual(by_kind[ClaimKind.IMPRINT].state.value, "unsupported")
        self.assertEqual(by_kind[ClaimKind.GENRE].state.value, "absent")
        contributors = [claim for claim in claims if claim.kind is ClaimKind.CONTRIBUTOR]
        self.assertIn("translator", {claim.role for claim in contributors})
        self.assertTrue(any(claim.state.value == "malformed" for claim in contributors))
        print_claim = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="Print ISBN: 9780306406157",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        print_claims = hardcover_metadata._parse_candidates(
            payload,
            "catalogue",
            (print_claim,),
        )[0].claims
        self.assertEqual(
            {"print"},
            {
                claim.applicability
                for claim in print_claims
                if claim.kind is ClaimKind.ISBN and claim.value is not None
            },
        )
        self.assertEqual(
            {"print"},
            {
                claim.applicability
                for claim in print_claims
                if claim.kind in {ClaimKind.ASIN, ClaimKind.SUBTITLE}
            },
        )
        unknown_claims = hardcover_metadata._parse_candidates(
            payload,
            "catalogue",
            (title_claim(),),
        )[0].claims
        self.assertEqual(
            {"unknown"},
            {
                claim.applicability
                for claim in unknown_claims
                if claim.kind in {ClaimKind.ASIN, ClaimKind.SUBTITLE}
            },
        )

    def test_response_cap_and_candidate_probe_are_terminal(self) -> None:
        capped = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}" * 20))), 1)
        self.assertEqual(capped.observe(isbn_lookup((isbn_claim(),))).status, ProviderStatus.OVERSIZED)
        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=hardcover_envelope(rows, [{"id": index, "title": None} for index in range(1, 7)])))), 65_536)
        self.assertEqual(overflow.observe(isbn_lookup((isbn_claim(),))).status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        for size, expected in ((262_144, ProviderStatus.MALFORMED), (262_145, ProviderStatus.OVERSIZED)):
            provider = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request, length=size: httpx.Response(200, headers={"content-type": "application/json"}, content=b"x" * length))), 262_144)
            self.assertEqual(provider.observe(isbn_lookup((isbn_claim(),))).status, expected)

    def test_edition_overflow_retains_only_work_scoped_witness(self) -> None:
        rows = [
            {
                "id": index,
                "book_id": 10,
                "isbn_10": "0306406152",
                "isbn_13": "9780306406157",
                "title": f"Discarded edition {index}",
                "release_date": "2001-02-03",
                "image": f"https://example.test/edition-{index}.jpg",
                "publisher": {"name": "Discarded publisher"},
                "language": "en",
            }
            for index in range(1, 7)
        ]
        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=hardcover_envelope(
                            rows,
                            [
                                {
                                    "id": 10,
                                    "title": "Anchored work",
                                    "contributions": [
                                        {
                                            "id": 1,
                                            "contribution": "Author",
                                            "author": {
                                                "id": 7,
                                                "name": "Ada Example",
                                            },
                                        }
                                    ],
                                }
                            ],
                        ),
                    )
                )
            ),
            65_536,
        )

        observation = provider.observe(work_lookup())

        self.assertEqual(ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, observation.status)
        self.assertEqual((), provider.cover_descriptors)
        self.assertEqual((), observation.diagnostics)
        self.assertEqual((), observation.candidates)
        witness = observation.edition_enrichment_overflow
        self.assertIsNotNone(witness)
        assert witness is not None
        self.assertEqual("hardcover-work-0000000010", witness.work_identity)
        self.assertEqual("Anchored work", witness.title)
        self.assertEqual(1, len(witness.authors))
        self.assertEqual(
            "Ada Example", witness.authors[0].value,
        )
        self.assertFalse(
            {
                "Discarded edition 1",
                "Discarded publisher",
                "2001-02-03",
                "en",
                "9780306406157",
            }
            & {witness.title, *(claim.value for claim in witness.authors)}
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary", "ebooks", "item-1", tuple(
                    TargetField(
                        TargetDestination.AUTHORS,
                        TargetFieldState.VALUE,
                        ("Ada Example",),
                    )
                    if field.destination is TargetDestination.AUTHORS
                    else field
                    for field in default_target_fields("Anchored work")
                )
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(observation,),
        )
        self.assertEqual(report, MetadataReport.from_json(report.to_json()))
        for mutate in (
            lambda document: next(
                field.update({"value": "Other work"})
                for field in document["target"]["fields"]
                if field["destination"] == "title"
            ),
            lambda document: next(
                field.update({"value": ["Other author"]})
                for field in document["target"]["fields"]
                if field["destination"] == "authors"
            ),
            lambda document: document["providers"][0]["edition_enrichment_overflow"].update({"title": "Anchored work (Expanded)"}),
        ):
            document = report.to_data()
            mutate(document)
            with self.assertRaises(ReportValueError):
                MetadataReport.from_json(hardcover_metadata.canonical_json(document))

    def test_exact_rich_caps_and_pinned_schema_are_admitted(self) -> None:
        self.assertEqual(hardcover_metadata.HARDCOVER_SCHEMA_COMMIT, "1c5bdbd3244e02b03ccc658a792793e39cba17a8")
        self.assertIn("contributions(order_by:{id:asc},limit:17){id contribution author{id name}}", hardcover_metadata._QUERY)
        self.assertIn("books:books(", hardcover_metadata._QUERY)
        self.assertNotIn("books_through_editions", hardcover_metadata._QUERY)
        self.assertIn("book_series(order_by:{id:asc},limit:17){id position series{id name}}", hardcover_metadata._QUERY)
        self.assertIn(
            "taggings(order_by:{id:asc},limit:33){id tag_id}",
            hardcover_metadata._QUERY,
        )
        self.assertIn(
            "tags:tags(",
            hardcover_metadata._QUERY,
        )
        self.assertIn(
            "{id tag tag_category{id category}}",
            hardcover_metadata._QUERY,
        )
        for scalar, description, expected in (("x" * 512, "d" * 16384, "valid"), ("x" * 513, "d" * 16385, "oversized")):
            with self.subTest(expected=expected):
                payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "title": scalar}], [{"id": 1, "title": None, "description": description}])
                claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", () )[0].claims
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.TITLE), expected)
                self.assertEqual(next(claim.state.value for claim in claims if claim.kind is ClaimKind.DESCRIPTION), expected)
        for count, kind, scope, key in ((16, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (17, ClaimKind.CREATOR, ClaimScope.EDITION, "edition"), (16, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (17, ClaimKind.CREATOR, ClaimScope.WORK, "work"), (16, ClaimKind.SERIES, ClaimScope.WORK, "series"), (17, ClaimKind.SERIES, ClaimScope.WORK, "series"), (32, ClaimKind.TAG, ClaimScope.WORK, "tag"), (33, ClaimKind.TAG, ClaimScope.WORK, "tag")):
            contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": f"A{index}"}} for index in range(1, count + 1)]
            series = [{"id": index, "position": str(index), "series": {"id": index, "name": f"S{index}"}} for index in range(1, count + 1)]
            tags = [{"id": index, "tag": {"id": index, "tag": f"T{index}"}} for index in range(1, count + 1)]
            row: dict[str, object] = {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions if key == "edition" else []}
            book: dict[str, object] = {"id": 1, "title": None, "contributions": contributions if key == "work" else [], "book_series": series if key == "series" else [], "taggings": tags if key == "tag" else []}
            claims = hardcover_metadata._parse_candidates(json.dumps(hardcover_envelope([row], [book])).encode(), "catalogue", ())[0].claims
            relevant = [claim for claim in claims if claim.kind is kind and claim.scope is scope]
            if count in {17, 33}:
                self.assertEqual(len(relevant), 1)
                self.assertEqual(relevant[0].state, FieldState.OVERSIZED)
            else:
                self.assertEqual(len(relevant), count)
                self.assertTrue(all(claim.state is FieldState.VALID for claim in relevant))

    def test_contribution_probe_preserves_roles_and_overflow_validates_rows(self) -> None:
        for roles, expected in (((["Author"] * 16) + ["Translator"], {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}), ((["Translator"] * 16) + ["Author"], {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR})):
            contributions = [{"id": index, "contribution": role, "author": {"id": index, "name": f"A{index}"}} for index, role in enumerate(roles, 1)]
            payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions}], [{"id": 1, "title": None}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            overflow = {claim.kind for claim in claims if claim.state is FieldState.OVERSIZED and claim.scope is ClaimScope.EDITION}
            self.assertEqual(overflow, expected)
        for bad in (0, 16):
            contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": "A"}} for index in range(17)]
            contributions[bad] = {"id": bad, "contribution": "Author"}
            payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "contributions": contributions}], [{"id": 1, "title": None}])
            claims = hardcover_metadata._parse_candidates(
                json.dumps(payload).encode(), "catalogue", ()
            )[0].claims
            self.assertTrue(
                any(
                    claim.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
                    and claim.state is FieldState.OVERSIZED
                    for claim in claims
                )
            )

    def test_nullable_contribution_roles_preserve_neutral_identity_bound_creators(self) -> None:
        contribution: dict[str, object] = {
            "id": 1,
            "contribution": None,
            "author": {"id": 1, "name": "Synthetic Contributor"},
        }
        observation = observe_contributions([contribution])

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        creators = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
            and claim.state is FieldState.VALID
            and claim.role is None
        ]
        self.assertEqual(
            {ClaimScope.EDITION, ClaimScope.WORK},
            {claim.scope for claim in creators},
        )
        work_creator = next(
            claim for claim in creators if claim.scope is ClaimScope.WORK
        )
        self.assertEqual(
            ((work_creator.reference, "hardcover-author-0000000001", None),),
            tuple(
                (binding.claim_reference, binding.identity, binding.role)
                for binding in observation.candidates[0].identity_bindings
            ),
        )
        qualified = qualify_provider_observation(
            observation,
            work_lookup("Synthetic title", ("Synthetic Contributor",)),
        )
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        self.assertEqual(
            (None,),
            tuple(
                claim.role
                for claim in qualified.candidates[0].claims
                if claim.kind is ClaimKind.CREATOR
            ),
        )
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )
        sibling_claims = observation.candidates[0].claims
        self.assertEqual(
            {"Synthetic title", "9780306406157"},
            {
                claim.value
                for claim in sibling_claims
                if claim.kind in {ClaimKind.TITLE, ClaimKind.ISBN}
                and claim.state is FieldState.VALID
            },
        )
        self.assertIn(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

    def test_mixed_author_and_neutral_creators_reject_author_projection(self) -> None:
        """A complete mixed creator collection cannot donate its typed subset."""
        observation = observe_contributions(
            [
                {
                    "id": 1,
                    "contribution": "Author",
                    "author": {"id": 1, "name": "Synthetic One"},
                },
                {
                    "id": 2,
                    "contribution": None,
                    "author": {"id": 2, "name": "Synthetic Two"},
                },
            ]
        )

        qualified = qualify_provider_observation(
            observation,
            work_lookup("Synthetic title", ("Synthetic One",)),
        )
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        candidate = qualified.candidates[0]
        self.assertFalse(
            any(
                claim.scope is ClaimScope.WORK
                and claim.kind is ClaimKind.CREATOR
                for claim in candidate.claims
            )
        )
        self.assertEqual((), candidate.identity_bindings)
        self.assertEqual(
            {"Synthetic title", "9780306406157"},
            {
                claim.value
                for claim in candidate.claims
                if claim.kind in {ClaimKind.TITLE, ClaimKind.ISBN}
                and claim.state is FieldState.VALID
            },
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary", "ebooks", "item-1",
                default_target_fields("Synthetic title"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(qualified,),
        )
        authors = next(
            result
            for result in evaluate_report(report).results
            if result.destination is TargetDestination.AUTHORS
        )
        self.assertIs(ProposalOutcome.INELIGIBLE, authors.outcome)
        self.assertEqual((), authors.alternatives)

    def test_mixed_creator_candidate_with_different_typed_author_is_not_admitted(self) -> None:
        """Mixed roles do not bypass the exact typed-author work qualification."""
        observation = observe_contributions(
            [
                {
                    "id": 1,
                    "contribution": "Author",
                    "author": {"id": 1, "name": "Completely Different"},
                },
                {
                    "id": 2,
                    "contribution": None,
                    "author": {"id": 2, "name": "Synthetic Two"},
                },
            ]
        )

        qualified = qualify_provider_observation(
            observation,
            work_lookup("Synthetic title", ("Synthetic One",)),
        )
        self.assertEqual(ProviderStatus.NO_MATCH, qualified.status)
        self.assertEqual((), qualified.candidates)

    def test_reused_neutral_creator_identity_binding_fails_qualification_locally(
        self,
    ) -> None:
        observation = observe_contributions(
            [
                {
                    "id": 1,
                    "contribution": None,
                    "author": {"id": 1, "name": "Synthetic One"},
                },
                {
                    "id": 2,
                    "contribution": None,
                    "author": {"id": 1, "name": "Synthetic Two"},
                },
            ]
        )
        candidate = observation.candidates[0]
        creators = tuple(
            claim
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.CREATOR
            and claim.role is None
        )
        observation = replace(
            observation,
            candidates=(
                replace(
                    candidate,
                    identity_bindings=tuple(
                        ProviderIdentityBinding(
                            claim.reference, "hardcover-author-0000000001",
                            ClaimScope.WORK, None, index,
                        )
                        for index, claim in enumerate(creators)
                    ),
                ),
            ),
        )
        self.assertNotEqual(
            len({value.identity for value in observation.candidates[0].identity_bindings}),
            len(observation.candidates[0].identity_bindings),
        )
        self.assertIsNotNone(
            complete_creator_identity_bindings(
                observation.candidates[0], creators,
            )
        )

        qualified = qualify_provider_observation(
            observation,
            work_lookup("Synthetic title", ("Synthetic One", "Synthetic Two")),
        )
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        self.assertEqual(
            ("hardcover-author-0000000001", "hardcover-author-0000000001"),
            tuple(value.identity for value in observation.candidates[0].identity_bindings),
        )
        self.assertEqual(
            2,
            len(
                tuple(
                    claim
                    for claim in observation.candidates[0].claims
                    if claim.scope is ClaimScope.WORK
                    and claim.kind is ClaimKind.CREATOR
                    and claim.role is None
                )
            ),
        )
        self.assertFalse(
            any(
                claim.kind is ClaimKind.CREATOR
                and claim.scope is ClaimScope.WORK
                for claim in qualified.candidates[0].claims
            )
        )
        self.assertEqual((), qualified.candidates[0].identity_bindings)
        sibling_claims = observation.candidates[0].claims
        self.assertEqual(
            {"Synthetic title", "9780306406157"},
            {
                claim.value
                for claim in sibling_claims
                if claim.kind in {ClaimKind.TITLE, ClaimKind.ISBN}
                and claim.state is FieldState.VALID
            },
        )
        self.assertIn(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            {evidence.kind for evidence in qualified.candidates[0].evidence},
        )
        replayed = MetadataReport.from_json(
            MetadataReport.create(
                target=TargetFacts(
                    "primary", "ebooks", "item-1",
                    default_target_fields("Synthetic title"),
                ),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(),
                local_reasons=("no_opening_claims",),
                providers=(qualified,),
            ).to_json()
        )
        replayed_claims = replayed.providers[0].candidates[0].claims
        self.assertEqual(
            {"Synthetic title", "9780306406157"},
            {
                claim.value
                for claim in replayed_claims
                if claim.kind in {ClaimKind.TITLE, ClaimKind.ISBN}
                and claim.state is FieldState.VALID
            },
        )
        authors = next(
            result
            for result in evaluate_report(replayed).results
            if result.destination is TargetDestination.AUTHORS
        )
        self.assertFalse(
            any(
                reference in {claim.reference for claim in creators}
                for alternative in authors.alternatives
                for reference in alternative.evidence_references
            )
        )

    def test_nullable_and_malformed_contribution_authors_are_field_local(self) -> None:
        for label, role, author, expected_kind in (
            ("both-null", None, None, ClaimKind.CREATOR),
            ("null-author", "Author", None, ClaimKind.CREATOR),
            ("malformed-author", "Translator", "invalid", ClaimKind.CONTRIBUTOR),
            ("malformed-role-and-author", {}, "invalid", ClaimKind.CONTRIBUTOR),
        ):
            with self.subTest(label=label):
                contribution: dict[str, object] = {
                    "id": 1,
                    "contribution": role,
                    "author": author,
                }
                observation = observe_contributions([contribution])

                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                malformed = [
                    claim
                    for claim in observation.candidates[0].claims
                    if claim.kind is expected_kind
                    and claim.state is FieldState.MALFORMED
                ]
                self.assertEqual(
                    {ClaimScope.EDITION, ClaimScope.WORK},
                    {claim.scope for claim in malformed},
                )
                self.assertEqual(
                    {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
                    {
                        evidence.kind
                        for evidence in observation.candidates[0].evidence
                    },
                )

    def test_malformed_author_identity_is_omitted_without_losing_valid_name_claims(self) -> None:
        contribution = {
            "id": 1,
            "contribution": "Author",
            "author": {"id": None, "name": "Synthetic Contributor"},
        }
        observation = observe_contributions([contribution])

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        creators = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        ]
        self.assertEqual(
            {ClaimScope.EDITION, ClaimScope.WORK},
            {claim.scope for claim in creators},
        )
        self.assertTrue(all(claim.state is FieldState.VALID for claim in creators))
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

    def test_nullable_contribution_overflow_remains_bounded_without_author_inference(self) -> None:
        contributions = [
            {"id": index, "contribution": None, "author": None}
            for index in range(1, 18)
        ]
        observation = observe_contributions(contributions)

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        contribution_claims = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
        ]
        self.assertEqual(
            {
                (ClaimKind.CREATOR, ClaimScope.EDITION, FieldState.OVERSIZED),
                (ClaimKind.CREATOR, ClaimScope.WORK, FieldState.OVERSIZED),
                (ClaimKind.CONTRIBUTOR, ClaimScope.EDITION, FieldState.OVERSIZED),
                (ClaimKind.CONTRIBUTOR, ClaimScope.WORK, FieldState.OVERSIZED),
            },
            {
                (claim.kind, claim.scope, claim.state)
                for claim in contribution_claims
            },
        )
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE},
            {evidence.kind for evidence in observation.candidates[0].evidence},
        )

    def test_nested_overflow_probe_objects_are_structurally_validated(self) -> None:
        cases: list[tuple[dict[str, object], dict[str, object]]] = []
        contributions = [{"id": index, "contribution": "Author", "author": {"id": index, "name": "A"}} for index in range(17)]
        contributions[16]["author"] = {"id": 17, "unexpected": "A"}
        cases.append(({"contributions": contributions}, {}))
        series = [{"id": index, "position": str(index), "series": {"id": index, "name": "S"}} for index in range(17)]
        series[16]["series"] = {"id": 17, "unexpected": "S"}
        cases.append(({}, {"book_series": series}))
        tags = [{"id": index, "tag": {"id": index, "tag": "T"}} for index in range(33)]
        tags[32]["tag"] = {"id": 33, "unexpected": "T"}
        cases.append(({}, {"taggings": tags}))
        for edition, work in cases:
            with self.subTest(edition=bool(edition)):
                payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", **edition}], [{"id": 1, "title": None, **work}])
                provider = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(lambda request, value=payload: httpx.Response(200, headers={"content-type": "application/json"}, json=value))), 65_536)
                observation = provider.observe(isbn_lookup((isbn_claim(),)))
                self.assertEqual(observation.status, ProviderStatus.CANDIDATES)

    def test_series_positions_and_code3_are_retained_at_projection_bounds(self) -> None:
        for position, expected in (("10", "10"), ("20.0", "20"), ("100", "100"), (10, "10")):
            payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None, "language": {"id": 1, "code2": None, "code3": "eng", "language": "English"}}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": position, "series": {"id": 1, "name": "S"}}]}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), expected)
            self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.LANGUAGE), "eng")
        boundary = "1" * 512
        payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": boundary, "series": {"id": 1, "name": "S"}}]}])
        claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
        self.assertEqual(next(claim.value for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), boundary)
        for position, state in (("9" * 513, FieldState.OVERSIZED), ("1e-999", FieldState.MALFORMED)):
            payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None, "book_series": [{"id": 1, "position": position, "series": {"id": 1, "name": "S"}}]}])
            claims = hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())[0].claims
            self.assertEqual(next(claim.state for claim in claims if claim.kind is ClaimKind.SERIES_SEQUENCE), state)

    def test_hardcover_multiple_series_keep_exact_correlated_sequence_slots(self) -> None:
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Current title",
                    "book_series": [
                        {
                            "id": 1,
                            "position": "2",
                            "series": {"id": 1, "name": "Reading Order"},
                        },
                        {
                            "id": 2,
                            "position": None,
                            "series": {
                                "id": 2,
                                "name": "Publication Order",
                            },
                        },
                    ],
                }
            ],
        )
        claims = hardcover_metadata._parse_candidates(
            json.dumps(payload).encode(),
            "catalogue",
            (isbn_claim(),),
        )[0].claims
        series = tuple(
            (claim.kind, claim.state, claim.value)
            for claim in claims
            if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
        )
        self.assertEqual(
            (
                (ClaimKind.SERIES, FieldState.VALID, "Reading Order"),
                (ClaimKind.SERIES_SEQUENCE, FieldState.VALID, "2"),
                (ClaimKind.SERIES, FieldState.VALID, "Publication Order"),
                (ClaimKind.SERIES_SEQUENCE, FieldState.ABSENT, None),
            ),
            series,
        )

    def test_optional_hardcover_projection_members_are_field_local(self) -> None:
        row: dict[str, object] = {
            "id": 1, "book_id": 1, "isbn_10": None,
            "isbn_13": "9780306406157", "title": "Edition",
            "release_date": "2024-02-30",
            "publisher": {"name": "Independent Press", "parent_id": {}},
            "language": {"code2": {}, "code3": "eng"},
            "contributions": [{"id": 1, "contribution": "Author", "author": {"name": "Ada"}}],
        }
        book: dict[str, object] = {
            "id": 1, "title": "Work", "release_date": "2020",
            "contributions": [{"id": 1, "contribution": "Translator", "author": {"id": 2, "name": "Bea"}}, "bad"],
            "book_series": [{"id": 1, "position": "1", "series": {"name": "Series"}}, "bad"],
            "taggings": [],
        }
        candidate = hardcover_metadata._parse_candidates(
            json.dumps(hardcover_envelope([row], [book])).encode(), "catalogue", ()
        )[0]
        claims = candidate.claims
        self.assertEqual(
            "Independent Press",
            next(value.value for value in claims if value.kind is ClaimKind.PUBLISHER),
        )
        self.assertEqual(
            "eng", next(value.value for value in claims if value.kind is ClaimKind.LANGUAGE)
        )
        self.assertEqual(
            FieldState.MALFORMED,
            next(value.state for value in claims if value.kind is ClaimKind.PUBLICATION_DATE),
        )
        self.assertEqual(
            {ProviderEvidenceKind.FIRST_PUBLICATION_DATE, ProviderEvidenceKind.CONTRIBUTOR_IDENTITY},
            {value.kind for value in candidate.evidence},
        )

    def test_hardcover_optional_cap_is_cardinality_first(self) -> None:
        values, overflow, invalid = hardcover_metadata._optional_bounded_list(
            [object()] * 17, 16, {"id"}
        )
        self.assertEqual(([], True, False), (values, overflow, invalid))

    def test_hardcover_optional_projection_diagnostics_only_count_unknown_additive_keys(self) -> None:
        diagnostic = hardcover_metadata._optional_projection_rejections
        tag = {"id": 1, "tag_id": 1, "tag": {"id": 1, "tag": "T", "tag_category": {"id": 1, "category": "Mood"}}}
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([], [{"id": 1, "title": "T", "taggings": [tag]}])).encode()))
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([], [{"id": 1, "title": "T", "taggings": [tag] * 18}])).encode()))
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([], [{"id": 1, "title": "T", "taggings": [tag] * 34}])).encode()))
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([{"id": 1, "book_id": 1, "contributions": ["hostile" for _ in range(18)]}], [])).encode()))
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([], [{"id": 1, "title": "T", "release_date": "not-a-date"}])).encode()))
        subject = {**tag, "tag": {"id": 1, "tag": "T", "tag_category": {"id": 1, "category": "Subject"}}}
        self.assertEqual(0, diagnostic(json.dumps(hardcover_envelope([], [{"id": 1, "title": "T", "taggings": [subject]}])).encode()))
        self.assertEqual(0, diagnostic(b'{"data":{"editions":[],"books":[{"id":1,"taggings":[{"id":1,"tag_id":"bad"}]}],"tags":[]}}'))
        self.assertEqual(0, diagnostic(b'{"data":{"editions":[{"id":1,"book_id":1,"contributions":[{"id":1,"author":{"id":{}}}]}],"books":[],"tags":[]} }'))
        self.assertEqual(0, diagnostic(b'{"data":{"editions":[],"books":[{"id":1,"book_series":[{"id":1,"series":"bad"}]}],"tags":[]} }'))
        self.assertEqual(0, diagnostic(b'{"data":{"editions":[{"id":1,"publisher":{"id":1,"parent_id":{}},"image":{"url":7}}],"books":[],"tags":[]} }'))
        self.assertEqual(1, diagnostic(b'{"data":{"editions":[],"books":[{"id":1,"new_optional_value":true}],"tags":[]} }'))

    def test_hardcover_actual_parser_diagnostics_count_source_rejections_once(self) -> None:
        valid_contribution = {
            "id": 1,
            "contribution": "Author",
            "author": {"id": 1, "name": "Ada"},
        }
        valid_series = {
            "id": 1,
            "position": "1",
            "series": {"id": 1, "name": "Series"},
        }
        valid_tagging = {
            "id": 1,
            "tag": {
                "id": 1,
                "tag": "Atmospheric",
                "tag_category": {"id": 1, "category": "Mood"},
            },
        }
        base_rows: list[dict[str, object]] = [
            {
                "id": 1,
                "book_id": 1,
                "isbn_10": None,
                "isbn_13": "9780306406157",
                "title": "Edition",
                "release_date": "2024-01-02",
                "image": {"url": "https://example.invalid/edition.jpg"},
                "publisher": {
                    "id": 1,
                    "name": "Example Press",
                    "parent_id": None,
                },
                "language": {
                    "id": 1,
                    "code2": "en",
                    "code3": "eng",
                    "language": "English",
                },
                "contributions": [valid_contribution],
            },
            {
                "id": 2,
                "book_id": 1,
                "isbn_10": None,
                "isbn_13": "9780306406157",
                "title": "Second edition",
            },
        ]
        base_books: list[dict[str, object]] = [
            {
                "id": 1,
                "title": "Work",
                "description": "Description",
                "release_date": "2020",
                "image": {"url": "https://example.invalid/work.jpg"},
                "contributions": [valid_contribution],
                "book_series": [valid_series],
                "taggings": [valid_tagging],
            }
        ]

        def observe(
            rows: list[dict[str, object]],
            books: list[dict[str, object]],
            *,
            additive_work_key: bool = False,
        ) -> ProviderObservation:
            payload = hardcover_envelope(rows, books)
            if additive_work_key:
                payload["data"]["books"][0]["new_optional_value"] = True
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=payload,
                        )
                    )
                ),
                65_536,
            )
            return provider.observe(isbn_lookup((isbn_claim(),)))

        clean = observe(base_rows, base_books)
        self.assertEqual(ProviderStatus.CANDIDATES, clean.status)
        self.assertEqual((), clean.diagnostics)

        cases: tuple[
            tuple[str, list[dict[str, object]], list[dict[str, object]], bool],
            ...,
        ] = (
            (
                "edition contributions over cap",
                [{**base_rows[0], "contributions": [valid_contribution] * 17}],
                base_books,
                False,
            ),
            (
                "work contributions over cap",
                base_rows,
                [{**base_books[0], "contributions": [valid_contribution] * 17}],
                False,
            ),
            (
                "series over cap",
                base_rows,
                [{**base_books[0], "book_series": [valid_series] * 17}],
                False,
            ),
            (
                "taggings over cap",
                base_rows,
                [
                    {
                        **base_books[0],
                        "taggings": [
                            {
                                "id": index,
                                "tag": {
                                    "id": 1,
                                    "tag": "Atmospheric",
                                    "tag_category": {
                                        "id": 1,
                                        "category": "Mood",
                                    },
                                },
                            }
                            for index in range(33)
                        ],
                    }
                ],
                False,
            ),
            (
                "shared work release date",
                base_rows,
                [{**base_books[0], "release_date": "not-a-date"}],
                False,
            ),
            (
                "edition author identity",
                [
                    {
                        **base_rows[0],
                        "contributions": [
                            {
                                **valid_contribution,
                                "author": {"id": {}, "name": "Ada"},
                            }
                        ],
                    }
                ],
                base_books,
                False,
            ),
            (
                "shared work author identity",
                base_rows,
                [
                    {
                        **base_books[0],
                        "contributions": [
                            {
                                **valid_contribution,
                                "author": {"id": {}, "name": "Ada"},
                            }
                        ],
                    }
                ],
                False,
            ),
            (
                "series object",
                base_rows,
                [{**base_books[0], "book_series": [{**valid_series, "series": "bad"}]}],
                False,
            ),
            (
                "series position",
                base_rows,
                [{**base_books[0], "book_series": [{**valid_series, "position": "1e-999"}]}],
                False,
            ),
            (
                "taxonomy enum",
                base_rows,
                [
                    {
                        **base_books[0],
                        "taggings": [
                            {
                                **valid_tagging,
                                "tag": {
                                    **cast(
                                        dict[str, object], valid_tagging["tag"]
                                    ),
                                    "tag_category": {
                                        "id": 1,
                                        "category": "Subject",
                                    },
                                },
                            }
                        ],
                    }
                ],
                False,
            ),
            (
                "edition image URL",
                [{**base_rows[0], "image": {"url": 7}}],
                base_books,
                False,
            ),
            (
                "work image URL",
                base_rows,
                [{**base_books[0], "image": {"url": 7}}],
                False,
            ),
            (
                "publisher name",
                [{**base_rows[0], "publisher": {"name": {}, "parent_id": None}}],
                base_books,
                False,
            ),
            (
                "publisher parent",
                [
                    {
                        **base_rows[0],
                        "publisher": {
                            "name": "Example Press",
                            "parent_id": {},
                        },
                    }
                ],
                base_books,
                False,
            ),
            (
                "language code",
                [
                    {
                        **base_rows[0],
                        "language": {"code2": {}, "code3": "eng"},
                    }
                ],
                base_books,
                False,
            ),
            ("unknown additive work key", base_rows, base_books, True),
        )
        for name, rows, books, additive in cases:
            with self.subTest(name=name):
                observation = observe(
                    rows,
                    books,
                    additive_work_key=additive,
                )
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(
                    () if name == "taxonomy enum" else (1,),
                    tuple(value.count for value in observation.diagnostics),
                )

    def test_hardcover_over_cap_fields_retain_no_prefix(self) -> None:
        contribution = {
            "id": 1,
            "contribution": "Author",
            "author": {"id": 1, "name": "Ada"},
        }
        series = {
            "id": 1,
            "position": "1",
            "series": {"id": 1, "name": "Series"},
        }
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_13": "9780306406157",
                    "contributions": [contribution] * 17,
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Work",
                    "contributions": [contribution] * 17,
                    "book_series": [series] * 17,
                    "taggings": [],
                }
            ],
        )
        payload["data"]["editions"][0]["contributions"] = [
            {**contribution, "untraversed": True}
        ] * 17
        payload["data"]["books"][0]["book_series"] = [
            {**series, "untraversed": True}
        ] * 17
        self.assertEqual(
            0,
            hardcover_metadata._optional_projection_rejections(
                json.dumps(payload).encode()
            ),
        )
        candidate = hardcover_metadata._parse_candidates(
            json.dumps(payload).encode(),
            "catalogue",
            (),
        )[0]
        self.assertFalse(any(claim.value == "Ada" for claim in candidate.claims))
        self.assertFalse(any(claim.value == "Series" for claim in candidate.claims))
        self.assertEqual((), candidate.evidence)
        self.assertTrue(
            all(
                claim.state is FieldState.OVERSIZED
                for claim in candidate.claims
                if claim.kind
                in {
                    ClaimKind.CREATOR,
                    ClaimKind.CONTRIBUTOR,
                    ClaimKind.SERIES,
                    ClaimKind.SERIES_SEQUENCE,
                }
            )
        )

    def test_hardcover_invalid_cover_links_and_author_names_are_field_local(self) -> None:
        valid_author = {"id": 7, "name": "Ada Author"}
        base_rows = [
            {
                "id": 1,
                "book_id": 1,
                "isbn_13": "9780306406157",
                "title": "Edition title",
                "publisher": {"name": "Example Press", "parent_id": None},
                "image": {"url": "https://example.invalid/edition.jpg"},
                "contributions": [
                    {
                        "id": 1,
                        "contribution": "Author",
                        "author": valid_author,
                    }
                ],
            },
            {
                "id": 2,
                "book_id": 1,
                "isbn_13": "9780306406157",
                "title": "Second edition",
            },
        ]
        base_books = [
            {
                "id": 1,
                "title": "Work title",
                "description": "Description",
                "image": {"url": "https://example.invalid/work.jpg"},
                "contributions": [
                    {
                        "id": 2,
                        "contribution": "Author",
                        "author": valid_author,
                    }
                ],
                "book_series": [],
                "taggings": [],
            }
        ]

        def observe(
            rows: list[dict[str, object]],
            books: list[dict[str, object]],
        ) -> tuple[ProviderObservation, HardcoverMetadataProvider]:
            payload = hardcover_envelope(rows, books)
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=payload,
                        )
                    )
                ),
                65_536,
            )
            return provider.observe(isbn_lookup((isbn_claim(),))), provider

        for name, rows, books, designation in (
            (
                "edition cover",
                [{**base_rows[0], "image": {"url": "not a URL"}}],
                base_books,
                "exact_edition",
            ),
            (
                "shared work cover",
                base_rows,
                [{**base_books[0], "image": {"url": "not a URL"}}],
                "default",
            ),
        ):
            with self.subTest(name=name):
                observation, provider = observe(rows, books)
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(
                    (1,),
                    tuple(value.count for value in observation.diagnostics),
                )
                self.assertTrue(
                    any(
                        claim.kind is ClaimKind.TITLE
                        and claim.value in {"Edition title", "Work title"}
                        for candidate in observation.candidates
                        for claim in candidate.claims
                    )
                )
                descriptor = next(
                    value
                    for value in provider.cover_descriptors
                    if value.provenance is not None
                    and value.provenance.designation.value == designation
                )
                self.assertIsNone(descriptor.url)
                self.assertIs(
                    CoverSourceState.UNSAFE_REMOTE,
                    descriptor.terminal_state,
                )

        for name, rows, books, scope in (
            (
                "edition author name",
                [
                    {
                        **base_rows[0],
                        "contributions": [
                            {
                                "id": 1,
                                "contribution": "Author",
                                "author": {"id": 7, "name": {}},
                            }
                        ],
                    }
                ],
                base_books,
                ClaimScope.EDITION,
            ),
            (
                "shared work author name",
                base_rows,
                [
                    {
                        **base_books[0],
                        "contributions": [
                            {
                                "id": 2,
                                "contribution": "Author",
                                "author": {"id": 7, "name": {}},
                            }
                        ],
                    }
                ],
                ClaimScope.WORK,
            ),
        ):
            with self.subTest(name=name):
                observation, _ = observe(rows, books)
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(
                    (1,),
                    tuple(value.count for value in observation.diagnostics),
                )
                affected = [
                    claim
                    for candidate in observation.candidates
                    for claim in candidate.claims
                    if claim.kind is ClaimKind.CREATOR and claim.scope is scope
                ]
                self.assertTrue(affected)
                self.assertTrue(
                    all(
                        claim.state is FieldState.MALFORMED
                        and claim.value is None
                        for claim in affected
                    )
                )
                self.assertTrue(
                    any(
                        claim.kind is ClaimKind.TITLE
                        and claim.state is FieldState.VALID
                        for candidate in observation.candidates
                        for claim in candidate.claims
                    )
                )


if __name__ == "__main__":
    unittest.main()
