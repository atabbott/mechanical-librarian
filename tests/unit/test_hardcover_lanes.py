"""Hardcover discovery lanes: candidate identity and order, aggregate
request cardinality, lane overflow, and cross-connection references.
"""

from __future__ import annotations

import json
import unittest
from typing import cast

import httpx

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HARDCOVER_URL,
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    isbn_family,
)
from tests.support.hardcover_evidence import (
    hardcover_envelope,
    isbn_claim,
    isbn_lookup,
    title_claim,
)


class HardcoverLaneTests(unittest.TestCase):
    def test_one_request_retains_all_candidates_in_identity_order(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope(
                    [{"id": 2, "book_id": 20, "isbn_10": None, "isbn_13": "9780306406157"}, {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": "9780306406157"}],
                    [{"id": 20, "title": "Other title"}, {"id": 10, "title": None}],
                ),
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = HardcoverMetadataProvider(
            "catalogue",
            client,
            65_536,
            wall_clock=lambda: 1_700_000_000.125,
        )
        observation = provider.observe(isbn_lookup((isbn_claim(), title_claim())))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertEqual(
            "2023-11-14T22:13:20.125000Z", observation.accessed_at
        )
        self.assertEqual([item.identity for item in observation.candidates], ["hardcover-edition-0000000001", "hardcover-edition-0000000002"])
        claims = {claim.kind: claim for claim in observation.candidates[1].claims}
        self.assertEqual(claims[ClaimKind.ISBN].links[0].outcome, Comparison.AGREEMENT)
        self.assertEqual(claims[ClaimKind.TITLE].links[0].outcome, Comparison.DISAGREEMENT)
        self.assertEqual(observation.input_references, (isbn_claim().reference,))
        self.assertEqual(len(requests), 1)
        self.assertEqual(4, len(provider.cover_descriptors))
        self.assertTrue(
            all(descriptor.url is None for descriptor in provider.cover_descriptors)
        )
        self.assertEqual(str(requests[0].url), HARDCOVER_URL)
        self.assertIn("limit:6", json.loads(requests[0].content)["query"])
        self.assertEqual(provider.observe(isbn_lookup((isbn_claim(), title_claim()))).status, ProviderStatus.UNAVAILABLE)
        self.assertEqual(len(requests), 1)

    def test_malformed_optional_cover_descriptor_does_not_discard_metadata(self) -> None:
        payload = hardcover_envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
            [{"id": 1, "title": "Current title"}],
        )
        payload["data"]["editions"][0]["image"] = {"url": "x" * 2049}
        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=payload))),
            65_536,
        )
        observation = provider.observe(isbn_lookup((isbn_claim(), title_claim())))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(2, len(provider.cover_descriptors))
        edition = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.designation.value == "exact_edition"
        )
        self.assertEqual(CoverSourceState.OVERSIZED, edition.terminal_state)
        self.assertIsNone(edition.url)

    def test_agreement_references_only_matching_title_and_isbn_claims(self) -> None:
        other_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Other title",
            canonical="Other title",
            excerpt="Other title",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        )
        other_isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="ISBN: 0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
        )
        payload = hardcover_envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
            [{"id": 1, "title": "Current title"}],
        )
        observation = HardcoverMetadataProvider(
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
        ).observe(isbn_lookup((isbn_claim(), other_isbn, title_claim(), other_title)))
        claims = {
            claim.kind: claim for claim in observation.candidates[0].claims
        }
        self.assertEqual(
            {link.local_reference: link.outcome for link in claims[ClaimKind.TITLE].links},
            {
                title_claim().reference: Comparison.AGREEMENT,
                other_title.reference: Comparison.DISAGREEMENT,
            },
        )
        self.assertEqual(
            {link.local_reference: link.outcome for link in claims[ClaimKind.ISBN].links},
            {
                isbn_claim().reference: Comparison.AGREEMENT,
                other_isbn.reference: Comparison.NOT_COMPARABLE,
            },
        )

    def test_multiple_inputs_are_selected_deterministically_and_statuses_do_not_retry(self) -> None:
        second = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="ISBN: 0306406152",
            applicability="unknown",
            checksum="valid_isbn_10",
            applicability_basis="unlabelled",
        )
        print_only = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780306406157",
            canonical="9780306406157",
            excerpt="Print ISBN: 9780306406157",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        observed_bodies: list[dict[str, object]] = []

        def success(request: httpx.Request) -> httpx.Response:
            observed_bodies.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "application/json"}, json=hardcover_envelope([], []))

        observation = HardcoverMetadataProvider(
            "catalogue", httpx.Client(transport=httpx.MockTransport(success)), 65_536
        ).observe(isbn_lookup((print_only, second, isbn_claim())))
        self.assertEqual(observation.status, ProviderStatus.NO_MATCH)
        self.assertEqual(
            observed_bodies[0]["variables"],
            {"isbns": ["0306406152", "9780306406157"]},
        )
        self.assertEqual(
            observation.input_references,
            tuple(sorted((second.reference, isbn_claim().reference))),
        )

    def test_hardcover_executes_every_family_and_work_lane_in_one_aggregate(self) -> None:
        title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Current title",
            canonical="Current title",
            excerpt="Current title",
            applicability="not_applicable",
            basis="target_metadata",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        second = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="Print ISBN 9780140328721",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                ("Ada Example",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("Current title")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields),
            (title, isbn_claim(), second),
        )
        operations: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            operations.append(json.loads(request.content)["operationName"])
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            )

        observation = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        ).observe(plan)
        self.assertEqual(
            ["MetadataEvidenceByIsbn", "MetadataEvidenceByIsbn", "MetadataEvidenceByWork"],
            sorted(operations),
        )
        self.assertEqual(3, len(observation.lanes))
        self.assertEqual(
            {ProviderStatus.NO_MATCH},
            {lane.status for lane in observation.lanes},
        )

    def test_hardcover_oversized_lane_stops_remaining_requests_at_exact_bound(
        self,
    ) -> None:
        claims = (
            isbn_claim(),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780140328721",
                canonical="9780140328721",
                excerpt="Print ISBN 9780140328721",
                applicability="print",
                checksum="valid_isbn_13",
                applicability_basis="print",
            ),
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), claims
        )
        payload = json.dumps(
            hardcover_envelope([], []),
            separators=(",", ":"),
        ).encode()

        for oversized in (False, True):
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)  # noqa: B023 - closure is created and consumed within this iteration
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=payload + (b" " if oversized else b""),  # noqa: B023 - closure is created and consumed within this iteration
                )

            observation = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(transport=httpx.MockTransport(handler)),
                len(payload),
            ).observe(plan)
            with self.subTest(oversized=oversized):
                self.assertEqual(1 if oversized else 2, len(requests))
                self.assertEqual(
                    (
                        {ProviderStatus.OVERSIZED, ProviderStatus.INCOMPLETE}
                        if oversized
                        else {ProviderStatus.NO_MATCH}
                    ),
                    {lane.status for lane in observation.lanes},
                )

    def test_hardcover_conflicting_lane_contributes_no_cover_descriptors(
        self,
    ) -> None:
        claims = (
            isbn_claim(),
            LocalClaim.create(
                kind=ClaimKind.ISBN,
                observed="9780140328721",
                canonical="9780140328721",
                excerpt="Print ISBN 9780140328721",
                applicability="print",
                checksum="valid_isbn_13",
                applicability_basis="print",
            ),
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), claims
        )
        shared = {
            "id": 1,
            "book_id": 10,
            "isbn_10": "0306406152",
            "isbn_13": "9780140328721",
            "image": {"url": "https://images.example/accepted.jpg"},
        }
        accepted = hardcover_envelope(
            [shared],
            [
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                }
            ],
        )
        conflicting = hardcover_envelope(
            [
                {
                    "id": 2,
                    "book_id": 20,
                    "isbn_10": "0306406152",
                    "isbn_13": "9780140328721",
                    "image": {"url": "https://images.example/orphan.jpg"},
                },
                {
                    **shared,
                    "image": {"url": "https://images.example/conflict.jpg"},
                },
            ],
            [
                {
                    "id": 20,
                    "title": "Other title",
                    "image": {"url": "https://images.example/orphan-work.jpg"},
                },
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                },
            ],
        )
        responses = iter((accepted, conflicting))
        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=next(responses),
                    )
                )
            ),
            65_536,
        )

        observation = provider.observe(plan)

        self.assertEqual(
            {ProviderStatus.CANDIDATES, ProviderStatus.MALFORMED},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(
            {
                cover_source_id(
                    CoverSourceKind.HARDCOVER, "catalogue:edition:1"
                ),
                cover_source_id(
                    CoverSourceKind.HARDCOVER, "catalogue:work:10:default"
                ),
            },
            {descriptor.source_id for descriptor in provider.cover_descriptors},
        )

    def test_hardcover_converges_differing_isbn_applicability_and_shared_cover(
        self,
    ) -> None:
        title = title_claim()
        ebook = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="eBook ISBN 0306406152",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
        )
        print_only = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="9780140328721",
            canonical="9780140328721",
            excerpt="Print ISBN 9780140328721",
            applicability="print",
            checksum="valid_isbn_13",
            applicability_basis="print",
        )
        fields = tuple(
            TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Example",),
            )
            if field.destination is TargetDestination.AUTHORS
            else field
            for field in default_target_fields("Current title")
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields),
            (title, ebook, print_only),
        )
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 10,
                    "isbn_10": "0306406152",
                    "isbn_13": "9780140328721",
                    "asin": "B012345678",
                    "subtitle": "Edition subtitle",
                    "image": {"url": "https://images.example/edition.jpg"},
                }
            ],
            [
                {
                    "id": 10,
                    "title": "Current title",
                    "image": {"url": "https://images.example/work.jpg"},
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {"id": 1, "name": "Ada Example"},
                        }
                    ],
                }
            ],
        )
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
        observation = provider.observe(plan)

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual(
            {ProviderStatus.CANDIDATES},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(
            tuple(sorted(lane.identity for lane in plan.lanes)),
            observation.convergence[0].lane_identities,
        )
        lanes = {lane.matched_family: lane for lane in observation.lanes}
        self.assertEqual(
            (ebook.reference,),
            lanes["9780306406157"].input_references,
        )
        self.assertEqual(
            (print_only.reference,),
            lanes["9780140328721"].input_references,
        )
        self.assertEqual((title.reference,), lanes[None].input_references)
        self.assertEqual(
            3,
            len(
                {
                    candidate.reference
                    for lane in observation.lanes
                    for candidate in lane.candidates
                }
            ),
        )

        isbn_claims = tuple(
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.ISBN and claim.value is not None
        )
        self.assertEqual(
            {
                "9780306406157": {"ebook"},
                "9780140328721": {"print"},
            },
            {
                family: {
                    claim.applicability
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertEqual(
            {
                "9780306406157": {ebook.reference},
                "9780140328721": {print_only.reference},
            },
            {
                family: {
                    link.local_reference
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                    for link in claim.links
                    if link.outcome is Comparison.AGREEMENT
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertTrue(
            all(
                {ebook.reference, print_only.reference}
                == {link.local_reference for link in claim.links}
                for claim in isbn_claims
            )
        )
        self.assertEqual(
            {"ambiguous"},
            {
                claim.applicability
                for claim in observation.candidates[0].claims
                if claim.kind in {ClaimKind.ASIN, ClaimKind.SUBTITLE}
            },
        )
        self.assertEqual(2, len(provider.cover_descriptors))
        self.assertEqual(
            2, len({value.source_id for value in provider.cover_descriptors})
        )
        edition_cover = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.edition_identity is not None
        )
        self.assertEqual(
            CoverDesignation.EXACT_EDITION,
            cast(CoverProvenance, edition_cover.provenance).designation,
        )

        for status_code, expected in (
            (401, ProviderStatus.AUTHENTICATION),
            (403, ProviderStatus.AUTHORIZATION),
            (429, ProviderStatus.RATE_LIMITED),
            (503, ProviderStatus.UNAVAILABLE),
        ):
            calls: list[int] = []

            def handler(request: httpx.Request, code: int = status_code) -> httpx.Response:
                calls.append(1)  # noqa: B023 - closure is created and consumed within this iteration
                return httpx.Response(code)

            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(transport=httpx.MockTransport(handler)),
                65_536,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(status=status_code):
                self.assertEqual(provider.observe(isbn_lookup((isbn_claim(),))).status, expected)
                self.assertEqual(provider.observe(isbn_lookup((isbn_claim(),))).status, ProviderStatus.UNAVAILABLE)
                self.assertEqual(
                    calls,
                    [1, 1, 1] if status_code == 503 else [1],
                )

    def test_zero_one_and_multiple_candidates_are_neutral(self) -> None:
        for rows, expected in (
            ([], ProviderStatus.NO_MATCH),
            ([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], ProviderStatus.CANDIDATES),
            ([
                {"id": 2, "book_id": 2, "isbn_10": None, "isbn_13": None},
                {"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None},
            ], ProviderStatus.CANDIDATES),
        ):
            client = httpx.Client(transport=httpx.MockTransport(lambda request, value=rows: httpx.Response(200, headers={"content-type": "application/json"}, json=hardcover_envelope(value, [{"id": row["book_id"], "title": None} for row in value]))))
            observation = HardcoverMetadataProvider("catalogue", client, 65_536).observe(isbn_lookup((isbn_claim(),)))
            with self.subTest(count=len(rows)):
                self.assertEqual(observation.status, expected)
                self.assertEqual(len(observation.candidates), len(rows))
                self.assertEqual(
                    [candidate.identity for candidate in observation.candidates],
                    sorted(candidate.identity for candidate in observation.candidates),
                )

    def test_candidate_work_identity_is_joined_bounded_and_provider_local(self) -> None:
        same_work = hardcover_metadata._parse_candidates(
            json.dumps(hardcover_envelope(
                [
                    {"id": 2, "book_id": 10, "isbn_10": None, "isbn_13": None},
                    {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": None},
                ],
                [{"id": 10, "title": None}],
            )).encode(),
            "catalogue",
            (),
        )
        self.assertEqual(
            [candidate.identity for candidate in same_work],
            ["hardcover-edition-0000000001", "hardcover-edition-0000000002"],
        )
        self.assertEqual(
            {candidate.work_identity for candidate in same_work},
            {"hardcover-work-0000000010"},
        )
        structurally_bound = hardcover_metadata._parse_candidates(
            json.dumps(
                hardcover_envelope(
                    [
                        {
                            "id": 1,
                            "book_id": 10,
                            "isbn_10": None,
                            "isbn_13": None,
                        }
                    ],
                    [
                        {
                            "id": 10,
                            "title": None,
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
                )
            ).encode(),
            "catalogue",
            (),
        )[0]
        binding = structurally_bound.identity_bindings[0]
        bound_claim = next(
            claim
            for claim in structurally_bound.claims
            if claim.reference == binding.claim_reference
        )
        self.assertEqual(
            (
                binding.identity,
                binding.scope,
                binding.role,
                bound_claim.value,
            ),
            (
                "hardcover-author-0000000007",
                ClaimScope.WORK,
                "author",
                "Ada Example",
            ),
        )
        different_works = hardcover_metadata._parse_candidates(
            json.dumps(hardcover_envelope(
                [
                    {"id": 1, "book_id": 10, "isbn_10": None, "isbn_13": None},
                    {"id": 2, "book_id": 20, "isbn_10": None, "isbn_13": None},
                ],
                [{"id": 10, "title": None}, {"id": 20, "title": None}],
            )).encode(),
            "catalogue",
            (),
        )
        self.assertEqual(
            {candidate.work_identity for candidate in different_works},
            {"hardcover-work-0000000010", "hardcover-work-0000000020"},
        )
        for malformed in (0, True, 2_147_483_648, "10"):
            with self.subTest(malformed=malformed):
                payload = hardcover_envelope(
                    [{"id": 1, "book_id": malformed, "isbn_10": None, "isbn_13": None}],
                    [{"id": 1, "title": None}],
                )
                with self.assertRaises(ValueError):
                    hardcover_metadata._parse_candidates(json.dumps(payload).encode(), "catalogue", ())

    def test_sparse_candidate_is_valid_but_result_cap_overflow_is_malformed(self) -> None:
        sparse = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], [{"id": 1, "title": None}]))))
        observation = HardcoverMetadataProvider("catalogue", sparse, 65_536).observe(isbn_lookup((isbn_claim(),)))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        self.assertTrue(observation.candidates[0].claims)

        rows = [{"id": index, "book_id": index, "isbn_10": None, "isbn_13": None} for index in range(1, 7)]
        overflow = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json=hardcover_envelope(rows, [{"id": index, "title": None} for index in range(1, 7)]))))
        observation = HardcoverMetadataProvider("catalogue", overflow, 65_536).observe(isbn_lookup((isbn_claim(),)))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED)
        self.assertEqual(observation.candidates, ())

    def test_same_candidate_from_two_provider_connections_has_globally_unique_refs(self) -> None:
        payload = hardcover_envelope([{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}], [{"id": 1, "title": "Current title"}])

        def observation(
            name: str,
        ) -> tuple[HardcoverMetadataProvider, ProviderObservation]:
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,
                    )
                )
            )
            provider = HardcoverMetadataProvider(name, client, 65_536)
            result = provider.observe(
                isbn_lookup((isbn_claim(), title_claim()))
            )
            return provider, result

        first_provider, first = observation("catalogue")
        second_provider, second = observation("secondary")
        first_references = {
            claim.reference
            for candidate in first.candidates
            for claim in candidate.claims
        }
        second_references = {
            claim.reference
            for candidate in second.candidates
            for claim in candidate.claims
        }
        self.assertTrue(first_references)
        self.assertTrue(first_references.isdisjoint(second_references))
        self.assertTrue(
            {value.source_id for value in first_provider.cover_descriptors}.isdisjoint(
                {value.source_id for value in second_provider.cover_descriptors}
            )
        )
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Current title")),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(isbn_claim(), title_claim()),
            local_reasons=("opening_isbn",),
            providers=(first, second),
        )
        self.assertEqual(MetadataReport.from_json(report.to_json()), report)

    def test_credential_and_private_path_provider_titles_are_malformed_without_leakage(self) -> None:
        for hostile in (
            "api_key=supersecretvalue",
            "/Users/private/library/book.epub",
        ):
            client = httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, title=hostile: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={
                            "data": {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}], "books": [{"id": 1, "title": title}]}
                        },
                    )
                )
            )
            with self.subTest(hostile=hostile):
                observation = HardcoverMetadataProvider(
                    "catalogue", client, 65_536
                ).observe(isbn_lookup((isbn_claim(),)))
                self.assertEqual(observation.status, ProviderStatus.MALFORMED)
                self.assertNotIn(hostile, repr(observation))


if __name__ == "__main__":
    unittest.main()
