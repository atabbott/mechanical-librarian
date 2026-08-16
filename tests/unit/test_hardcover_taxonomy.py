"""Hardcover taxonomy and work publication: the sibling tag join, its
terminal states, and their independence from series evidence.
"""

from __future__ import annotations

import json
import unittest
from typing import cast

import httpx

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverProvenance,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    MetadataReport,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from tests.support.hardcover_evidence import (
    hardcover_envelope,
    isbn_claim,
    isbn_lookup,
    work_lookup,
)


class HardcoverTaxonomyTests(unittest.TestCase):
    def test_work_lookup_retains_typed_taxonomy_and_first_publication(self) -> None:
        captured: list[dict[str, object]] = []
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                    "release_date": "2001-02-03",
                }
            ],
            [
                {
                    "id": 1,
                    "title": "Current title",
                    "release_date": "2001-02-03",
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": "Author",
                            "author": {"id": 7, "name": "Ada Example"},
                        }
                    ],
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 11,
                                "tag": "Fantasy",
                                "tag_category": {"id": 2, "category": "Genre"},
                            },
                        },
                        {
                            "id": 2,
                            "tag": {
                                "id": 12,
                                "tag": "Hopeful",
                                "tag_category": {"id": 3, "category": "Mood"},
                            },
                        },
                        {
                            "id": 3,
                            "tag": {
                                "id": 13,
                                "tag": "Violence",
                                "tag_category": {
                                    "id": 4,
                                    "category": "ContentWarning",
                                },
                            },
                        },
                    ],
                }
            ],
        )

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=payload,
            )

        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        )
        lookup = work_lookup()
        observation = qualify_provider_observation(
            provider.observe(lookup), lookup
        )
        self.assertEqual(ProviderQueryKind.WORK, observation.query_kind)
        self.assertEqual("MetadataEvidenceByWork", captured[0]["operationName"])
        self.assertEqual(
            {"authors": ["Ada Example"], "title": "Current title"},
            captured[0]["variables"],
        )
        work_claims = [
            claim
            for claim in observation.candidates[0].claims
            if claim.scope is ClaimScope.WORK and claim.state is FieldState.VALID
        ]
        self.assertEqual(
            {"Fantasy"},
            {claim.value for claim in work_claims if claim.kind is ClaimKind.GENRE},
        )
        self.assertEqual(
            {"Hopeful", "Violence"},
            {claim.value for claim in work_claims if claim.kind is ClaimKind.TAG},
        )
        self.assertEqual(
            {
                ProviderEvidenceKind.AUTHOR_IDENTITY,
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
                ProviderEvidenceKind.TAXONOMY,
            },
            {value.kind for value in observation.candidates[0].evidence},
        )
        taxonomy = sorted(
            (
                value
                for value in observation.candidates[0].evidence
                if value.kind is ProviderEvidenceKind.TAXONOMY
            ),
            key=lambda value: value.source_ordinal,
        )
        self.assertEqual(
            [
                ("Genre", "Fantasy", 0),
                ("Mood", "Hopeful", 1),
                ("ContentWarning", "Violence", 2),
            ],
            [
                (value.taxonomy_type, value.value, value.source_ordinal)
                for value in taxonomy
            ],
        )
        replayed = MetadataReport.from_json(
            MetadataReport.create(
                target=TargetFacts(
                    "primary",
                    "ebooks",
                    "item-1",
                    default_target_fields("Current title"),
                ),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(),
                local_reasons=("no_opening_claims",),
                providers=(observation,),
            ).to_json()
        ).providers[0]
        self.assertEqual(observation, replayed)
        publication = next(
            value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
        )
        self.assertEqual((ClaimScope.WORK, "2001-02-03"), (publication.scope, publication.value))
        retained = next(
            value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        )
        self.assertEqual((), retained.links)
        default_cover = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.designation.value == "default"
        )
        self.assertEqual(
            [("publication_date", "2001-02-03")],
            [
                (value.field.value, value.value)
                for value in cast(CoverProvenance, default_cover.provenance).context
            ],
        )

    def test_equal_taxonomy_values_keep_type_and_source_order_through_replay(self) -> None:
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
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 1,
                                "tag": "Shared",
                                "tag_category": {
                                    "id": 1,
                                    "category": "Mood",
                                },
                            },
                        },
                        {
                            "id": 2,
                            "tag": {
                                "id": 2,
                                "tag": "Shared",
                                "tag_category": {
                                    "id": 2,
                                    "category": "Genre",
                                },
                            },
                        },
                        {
                            "id": 3,
                            "tag": {
                                "id": 3,
                                "tag": "Shared",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Mood",
                                },
                            },
                        },
                    ],
                }
            ],
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
        ).observe(isbn_lookup((isbn_claim(),)))
        taxonomy = sorted(
            (
                value
                for value in observation.candidates[0].evidence
                if value.kind is ProviderEvidenceKind.TAXONOMY
            ),
            key=lambda value: value.source_ordinal,
        )
        self.assertEqual(
            [
                ("Mood", "Shared", 0),
                ("Genre", "Shared", 1),
                ("Mood", "Shared", 2),
            ],
            [
                (value.taxonomy_type, value.value, value.source_ordinal)
                for value in taxonomy
            ],
        )
        selected_taxonomy = tuple(
            claim
            for claim in observation.candidates[0].claims
            if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        )
        self.assertEqual(
            {
                (ClaimKind.GENRE, FieldState.VALID, "Shared"),
                (ClaimKind.TAG, FieldState.VALID, "Shared"),
            },
            {
                (claim.kind, claim.state, claim.value)
                for claim in selected_taxonomy
            },
        )
        self.assertEqual((), observation.diagnostics)
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Current title"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(isbn_claim(),),
            local_reasons=("opening_matter_isbn",),
            providers=(observation,),
        )
        self.assertEqual(report, MetadataReport.from_json(report.to_json()))

    def test_hardcover_taxonomy_and_work_publication_fail_closed(self) -> None:
        def observe(book: dict[str, object]) -> ProviderObservation:
            payload = hardcover_envelope(
                [
                    {
                        "id": 1,
                        "book_id": 1,
                        "isbn_10": None,
                        "isbn_13": None,
                    }
                ],
                [book],
            )
            return HardcoverMetadataProvider(
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
            ).observe(work_lookup())

        base: dict[str, object] = {
            "id": 1,
            "title": "Current title",
            "contributions": [
                {
                    "id": 1,
                    "contribution": "Author",
                    "author": {"id": 7, "name": "Ada Example"},
                }
            ],
        }
        for value in (
            {
                **base,
                "taggings": [
                    {
                        "id": 1,
                        "tag": {
                            "id": 2,
                            "tag": "Unknown",
                            "tag_category": {"id": 9, "category": "Subject"},
                        },
                    }
                ],
            },
            {
                **base,
                "taggings": [
                    {
                        "id": 1,
                        "tag": {
                            "id": 2,
                            "tag": "Shared",
                            "tag_category": {"id": 9, "category": "Genre"},
                        },
                    },
                    {
                        "id": 2,
                        "tag": {
                            "id": 3,
                            "tag": "Shared",
                            "tag_category": {"id": 10, "category": "Mood"},
                        },
                    },
                ],
            },
        ):
            with self.subTest(value=value):
                observation = observe(value)
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertTrue(
                    any(
                        claim.kind is ClaimKind.TITLE
                        and claim.value == "Current title"
                        and claim.state is FieldState.VALID
                        for claim in observation.candidates[0].claims
                    )
                )
        release_observation = observe({**base, "release_date": "not-a-date"})
        self.assertEqual(ProviderStatus.CANDIDATES, release_observation.status)
        self.assertFalse(
            any(
                evidence.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
                for evidence in release_observation.candidates[0].evidence
            )
        )

        overflow = {
            **base,
            "taggings": [
                {
                    "id": index,
                    "tag": {
                        "id": index,
                        "tag": f"Tag {index}",
                        "tag_category": {"id": 1, "category": "Mood"},
                    },
                }
                for index in range(1, 34)
            ],
        }
        observation = observe(overflow)
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        states = {
            claim.kind: claim.state
            for claim in observation.candidates[0].claims
            if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        }
        self.assertEqual(
            {ClaimKind.GENRE: FieldState.OVERSIZED, ClaimKind.TAG: FieldState.OVERSIZED},
            states,
        )

    def test_no_usable_input_has_no_egress_and_malformed_is_all_or_nothing(self) -> None:
        calls: list[int] = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(1) or httpx.Response(200)))  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the call and still yields the response.
        provider = HardcoverMetadataProvider("catalogue", client, 65_536)
        self.assertEqual(provider.observe(isbn_lookup(())).status, ProviderStatus.INSUFFICIENT_INPUT)
        self.assertEqual(calls, [])

        malformed = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}, {"id": "bad", "book_id": 2, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": "Fine"}, {"id": 2, "title": None}]}})))
        observation = HardcoverMetadataProvider("catalogue", malformed, 65_536).observe(isbn_lookup((isbn_claim(),)))
        self.assertEqual(observation.status, ProviderStatus.MALFORMED)
        self.assertEqual(observation.candidates, ())

    def test_separate_root_join_rejects_missing_duplicate_and_unreferenced_work(self) -> None:
        envelopes = (
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": []},
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": None}, {"id": 1, "title": None}]},
            {"editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}], "books": [{"id": 1, "title": None}, {"id": 2, "title": None}]},
        )
        for data in envelopes:
            with self.subTest(data=data):
                client = httpx.Client(transport=httpx.MockTransport(lambda request, value=data: httpx.Response(200, headers={"content-type": "application/json"}, json={"data": value})))
                self.assertEqual(HardcoverMetadataProvider("catalogue", client, 65_536).observe(isbn_lookup((isbn_claim(),))).status, ProviderStatus.MALFORMED)

    def test_separate_taxonomy_root_join_rejects_missing_duplicate_and_extra_tags(
        self,
    ) -> None:
        base = hardcover_envelope(
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
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 2,
                                "tag": "Hopeful",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Mood",
                                },
                            },
                        }
                    ],
                }
            ],
        )
        tag = cast(
            list[dict[str, object]],
            cast(dict[str, object], base["data"])["tags"],
        )[0]
        variants = []
        missing = json.loads(json.dumps(base))
        missing["data"]["tags"] = []
        variants.append(missing)
        duplicate = json.loads(json.dumps(base))
        duplicate["data"]["tags"].append(tag)
        variants.append(duplicate)
        extra = json.loads(json.dumps(base))
        extra["data"]["tags"].append(
            {
                "id": 99,
                "tag": "Unreferenced",
                "tag_category": {"id": 3, "category": "Mood"},
            }
        )
        variants.append(extra)
        for payload in variants:
            with self.subTest(tags=len(payload["data"]["tags"])):
                observation = HardcoverMetadataProvider(
                    "catalogue",
                    httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request, value=payload: httpx.Response(
                                200,
                                headers={"content-type": "application/json"},
                                json=value,
                            )
                        )
                    ),
                    65_536,
                ).observe(isbn_lookup((isbn_claim(),)))
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(1, len(observation.candidates))

    def test_duplicate_taxonomy_ids_are_permanently_poisoned(self) -> None:
        base = hardcover_envelope(
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
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 2,
                                "tag": "Ambiguous",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Genre",
                                },
                            },
                        },
                        {
                            "id": 2,
                            "tag": {
                                "id": 9,
                                "tag": "Hopeful",
                                "tag_category": {
                                    "id": 4,
                                    "category": "Mood",
                                },
                            },
                        },
                    ],
                }
            ],
        )
        tags = cast(
            list[dict[str, object]],
            cast(dict[str, object], base["data"])["tags"],
        )
        ambiguous = tags[0]
        for duplicates in (
            [
                ambiguous,
                {
                    **ambiguous,
                    "tag": "Collision",
                    "tag_category": {"id": 4, "category": "Mood"},
                },
            ],
            [
                {
                    **ambiguous,
                    "tag": "Collision",
                    "tag_category": {"id": 4, "category": "Mood"},
                },
                ambiguous,
                {
                    **ambiguous,
                    "tag": "Third",
                    "tag_category": {"id": 3, "category": "Genre"},
                },
            ],
        ):
            payload = json.loads(json.dumps(base))
            payload["data"]["tags"] = [*duplicates, tags[1]]
            observation = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request, value=payload: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=value,
                        )
                    )
                ),
                65_536,
            ).observe(isbn_lookup((isbn_claim(),)))
            taxonomy = tuple(
                claim
                for claim in observation.candidates[0].claims
                if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
                and claim.state is FieldState.VALID
            )
            with self.subTest(duplicates=len(duplicates)):
                self.assertEqual({"Hopeful"}, {claim.value for claim in taxonomy})
                self.assertEqual(
                    len(duplicates),
                    observation.diagnostics[0].count,
                )
                self.assertNotIn(
                    FieldState.ABSENT,
                    {
                        claim.state
                        for claim in observation.candidates[0].claims
                        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
                    },
                )

    def test_taxonomy_absence_is_synthesized_only_without_terminal_claims(
        self,
    ) -> None:
        empty = hardcover_envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
            [{"id": 1, "title": "Current title", "taggings": []}],
        )
        malformed = json.loads(json.dumps(empty))
        malformed["data"]["books"][0]["taggings"] = [{"id": 1, "tag_id": 99}]
        for label, payload, expected in (
            ("empty", empty, {FieldState.ABSENT}),
            ("malformed", malformed, {FieldState.MALFORMED}),
        ):
            observation = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request, value=payload: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=value,
                        )
                    )
                ),
                65_536,
            ).observe(isbn_lookup((isbn_claim(),)))
            states = {
                claim.state
                for claim in observation.candidates[0].claims
                if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
            }
            with self.subTest(label=label):
                self.assertEqual(expected, states)

    def test_series_and_taxonomy_terminal_states_are_independent(self) -> None:
        book = {
            "id": 1,
            "title": "Current title",
            "book_series": [{"id": 1}],
            "taggings": [
                {
                    "id": index + 1,
                    "tag": {
                        "id": index + 1,
                        "tag": f"Mood {index}",
                        "tag_category": {
                            "id": 1,
                            "category": "Mood",
                        },
                    },
                }
                for index in range(hardcover_metadata.MAX_TAGS + 1)
            ],
        }
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [book],
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
        ).observe(isbn_lookup((isbn_claim(),)))

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        states = {
            claim.kind: claim.state
            for claim in observation.candidates[0].claims
            if claim.kind
            in {
                ClaimKind.SERIES,
                ClaimKind.SERIES_SEQUENCE,
                ClaimKind.GENRE,
                ClaimKind.TAG,
            }
        }
        self.assertEqual(
            {
                ClaimKind.SERIES: FieldState.MALFORMED,
                ClaimKind.SERIES_SEQUENCE: FieldState.MALFORMED,
                ClaimKind.GENRE: FieldState.OVERSIZED,
                ClaimKind.TAG: FieldState.OVERSIZED,
            },
            states,
        )
        self.assertEqual(2, observation.diagnostics[0].count)
        self.assertTrue(
            any(
                claim.kind is ClaimKind.TITLE
                and claim.value == "Current title"
                and claim.state is FieldState.VALID
                for claim in observation.candidates[0].claims
            )
        )

    def test_malformed_taxonomy_rows_are_local_to_referencing_works(self) -> None:
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                },
                {
                    "id": 2,
                    "book_id": 2,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                },
            ],
            [
                {
                    "id": 1,
                    "title": "First work",
                    "taggings": [
                        {
                            "id": 1,
                            "tag": {
                                "id": 2,
                                "tag": "Broken",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Mood",
                                },
                            },
                        }
                    ],
                },
                {
                    "id": 2,
                    "title": "Second work",
                    "taggings": [
                        {
                            "id": 2,
                            "tag": {
                                "id": 9,
                                "tag": "Hopeful",
                                "tag_category": {
                                    "id": 3,
                                    "category": "Mood",
                                },
                            },
                        }
                    ],
                },
            ],
        )
        payload["data"]["tags"][0]["tag_category"] = {"id": 3}
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
        ).observe(isbn_lookup((isbn_claim(),)))

        candidates = {
            candidate.work_identity: candidate
            for candidate in observation.candidates
        }
        first_states = {
            claim.state
            for claim in candidates["hardcover-work-0000000001"].claims
            if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        }
        second_claims = tuple(
            claim
            for claim in candidates["hardcover-work-0000000002"].claims
            if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        )
        self.assertEqual({FieldState.MALFORMED}, first_states)
        self.assertNotIn(
            FieldState.MALFORMED,
            {claim.state for claim in second_claims},
        )
        self.assertIn(
            "Hopeful",
            {claim.value for claim in second_claims},
        )
        self.assertEqual(2, observation.diagnostics[0].count)

    def test_unreferenced_malformed_taxonomy_rows_are_diagnostic_only(
        self,
    ) -> None:
        payload = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [{"id": 1, "title": "Current title", "taggings": []}],
        )
        payload["data"]["tags"] = [
            {
                "id": 99,
                "tag": "Broken",
                "tag_category": {"id": 3},
            },
            {"tag": "Unidentifiable", "tag_category": {"id": 3}},
        ]
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
        ).observe(isbn_lookup((isbn_claim(),)))

        self.assertEqual(2, observation.diagnostics[0].count)
        self.assertEqual(
            {FieldState.ABSENT},
            {
                claim.state
                for claim in observation.candidates[0].claims
                if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
            },
        )

    def test_unavailable_or_oversized_taxonomy_root_is_global(self) -> None:
        base = hardcover_envelope(
            [
                {
                    "id": 1,
                    "book_id": 1,
                    "isbn_10": None,
                    "isbn_13": "9780306406157",
                }
            ],
            [{"id": 1, "title": "Current title", "taggings": []}],
        )
        for label, tags, expected in (
            ("unavailable", None, FieldState.MALFORMED),
            (
                "oversized",
                [
                    {
                        "id": index + 1,
                        "tag": f"Tag {index}",
                        "tag_category": {
                            "id": 3,
                            "category": "Mood",
                        },
                    }
                    for index in range(hardcover_metadata._MAX_TAG_ROWS + 1)
                ],
                FieldState.OVERSIZED,
            ),
        ):
            payload = json.loads(json.dumps(base))
            payload["data"]["tags"] = tags
            observation = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request, value=payload: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            json=value,
                        )
                    )
                ),
                65_536,
            ).observe(isbn_lookup((isbn_claim(),)))
            with self.subTest(label=label):
                self.assertEqual(
                    {expected},
                    {
                        claim.state
                        for claim in observation.candidates[0].claims
                        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
                    },
                )
                self.assertEqual(1, observation.diagnostics[0].count)


if __name__ == "__main__":
    unittest.main()
