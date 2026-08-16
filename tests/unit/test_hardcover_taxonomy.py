"""Hardcover taxonomy and work publication: the sibling tag join, its
terminal states, and their independence from series evidence.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import httpx
import pytest

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


def test_work_lookup_retains_typed_taxonomy_and_first_publication() -> None:
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
    observation = qualify_provider_observation(provider.observe(lookup), lookup)
    assert ProviderQueryKind.WORK == observation.query_kind
    assert "MetadataEvidenceByWork" == captured[0]["operationName"]
    assert {"authors": ["Ada Example"], "title": "Current title"} == captured[0][
        "variables"
    ]
    work_claims = [
        claim
        for claim in observation.candidates[0].claims
        if claim.scope is ClaimScope.WORK and claim.state is FieldState.VALID
    ]
    assert {"Fantasy"} == {
        claim.value for claim in work_claims if claim.kind is ClaimKind.GENRE
    }
    assert {"Hopeful", "Violence"} == {
        claim.value for claim in work_claims if claim.kind is ClaimKind.TAG
    }
    assert {
        ProviderEvidenceKind.AUTHOR_IDENTITY,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
        ProviderEvidenceKind.TAXONOMY,
    } == {value.kind for value in observation.candidates[0].evidence}
    taxonomy = sorted(
        (
            value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.TAXONOMY
        ),
        key=lambda value: value.source_ordinal,
    )
    assert [
        ("Genre", "Fantasy", 0),
        ("Mood", "Hopeful", 1),
        ("ContentWarning", "Violence", 2),
    ] == [
        (value.taxonomy_type, value.value, value.source_ordinal) for value in taxonomy
    ]
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
    assert observation == replayed
    publication = next(
        value
        for value in observation.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
    )
    assert (ClaimScope.WORK, "2001-02-03") == (publication.scope, publication.value)
    retained = next(
        value
        for value in observation.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert () == retained.links
    default_cover = next(
        value
        for value in provider.cover_descriptors
        if value.provenance is not None
        and value.provenance.designation.value == "default"
    )
    assert [("publication_date", "2001-02-03")] == [
        (value.field.value, value.value)
        for value in cast(CoverProvenance, default_cover.provenance).context
    ]


def test_equal_taxonomy_values_keep_type_and_source_order_through_replay() -> None:
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
    assert [
        ("Mood", "Shared", 0),
        ("Genre", "Shared", 1),
        ("Mood", "Shared", 2),
    ] == [
        (value.taxonomy_type, value.value, value.source_ordinal) for value in taxonomy
    ]
    selected_taxonomy = tuple(
        claim
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    )
    assert {
        (ClaimKind.GENRE, FieldState.VALID, "Shared"),
        (ClaimKind.TAG, FieldState.VALID, "Shared"),
    } == {(claim.kind, claim.state, claim.value) for claim in selected_taxonomy}
    assert () == observation.diagnostics
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
    assert report == MetadataReport.from_json(report.to_json())


def _observe_book(book: dict[str, object]) -> ProviderObservation:
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


_TITLED_WORK: dict[str, object] = {
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


@pytest.mark.parametrize(
    "taggings",
    (
        [
            {
                "id": 1,
                "tag": {
                    "id": 2,
                    "tag": "Unknown",
                    "tag_category": {"id": 9, "category": "Subject"},
                },
            }
        ],
        [
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
    ),
    ids=("unknown_category", "shared_value_across_categories"),
)
def test_hardcover_unusable_taxonomy_keeps_the_work_title_valid(
    taggings: list[dict[str, object]],
) -> None:
    observation = _observe_book({**_TITLED_WORK, "taggings": taggings})
    assert ProviderStatus.CANDIDATES == observation.status
    assert any(
        claim.kind is ClaimKind.TITLE
        and claim.value == "Current title"
        and claim.state is FieldState.VALID
        for claim in observation.candidates[0].claims
    )


def test_hardcover_taxonomy_and_work_publication_fail_closed() -> None:
    release_observation = _observe_book({**_TITLED_WORK, "release_date": "not-a-date"})
    assert ProviderStatus.CANDIDATES == release_observation.status
    assert not any(
        evidence.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
        for evidence in release_observation.candidates[0].evidence
    )

    overflow = {
        **_TITLED_WORK,
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
    observation = _observe_book(overflow)
    assert ProviderStatus.CANDIDATES == observation.status
    states = {
        claim.kind: claim.state
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    }
    assert {
        ClaimKind.GENRE: FieldState.OVERSIZED,
        ClaimKind.TAG: FieldState.OVERSIZED,
    } == states


def test_no_usable_input_has_no_egress_and_malformed_is_all_or_nothing() -> None:
    calls: list[int] = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(1) or httpx.Response(200)
        )
    )  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the call and still yields the response.
    provider = HardcoverMetadataProvider("catalogue", client, 65_536)
    assert provider.observe(isbn_lookup(())).status == ProviderStatus.INSUFFICIENT_INPUT
    assert calls == []

    malformed = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "data": {
                        "editions": [
                            {
                                "id": 1,
                                "book_id": 1,
                                "isbn_10": None,
                                "isbn_13": "9780306406157",
                            },
                            {
                                "id": "bad",
                                "book_id": 2,
                                "isbn_10": None,
                                "isbn_13": None,
                            },
                        ],
                        "books": [{"id": 1, "title": "Fine"}, {"id": 2, "title": None}],
                    }
                },
            )
        )
    )
    observation = HardcoverMetadataProvider("catalogue", malformed, 65_536).observe(
        isbn_lookup((isbn_claim(),))
    )
    assert observation.status == ProviderStatus.MALFORMED
    assert observation.candidates == ()


@pytest.mark.parametrize(
    "data",
    (
        {
            "editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}],
            "books": [],
        },
        {
            "editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}],
            "books": [{"id": 1, "title": None}, {"id": 1, "title": None}],
        },
        {
            "editions": [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": None}],
            "books": [{"id": 1, "title": None}, {"id": 2, "title": None}],
        },
    ),
    ids=("missing_work", "duplicate_work", "unreferenced_work"),
)
def test_separate_root_join_rejects_missing_duplicate_and_unreferenced_work(
    data: dict[str, object],
) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, json={"data": data}
            )
        )
    )
    assert (
        HardcoverMetadataProvider("catalogue", client, 65_536)
        .observe(isbn_lookup((isbn_claim(),)))
        .status
        == ProviderStatus.MALFORMED
    )


@pytest.mark.parametrize(
    "tags",
    (
        lambda tag: [],
        lambda tag: [tag, tag],
        lambda tag: [
            tag,
            {
                "id": 99,
                "tag": "Unreferenced",
                "tag_category": {"id": 3, "category": "Mood"},
            },
        ],
    ),
    ids=("missing", "duplicate", "unreferenced"),
)
def test_separate_taxonomy_root_join_rejects_missing_duplicate_and_extra_tags(
    tags: Callable[[dict[str, object]], list[dict[str, object]]],
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
    payload = json.loads(json.dumps(base))
    payload["data"]["tags"] = tags(tag)
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
    assert ProviderStatus.CANDIDATES == observation.status
    assert 1 == len(observation.candidates)


@pytest.mark.parametrize(
    "duplicates",
    (
        lambda ambiguous: [
            ambiguous,
            {
                **ambiguous,
                "tag": "Collision",
                "tag_category": {"id": 4, "category": "Mood"},
            },
        ],
        lambda ambiguous: [
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
    ),
    ids=("two_rows", "three_rows"),
)
def test_duplicate_taxonomy_ids_are_permanently_poisoned(
    duplicates: Callable[[dict[str, object]], list[dict[str, object]]],
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
    rows = duplicates(tags[0])
    payload = json.loads(json.dumps(base))
    payload["data"]["tags"] = [*rows, tags[1]]
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
    taxonomy = tuple(
        claim
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
        and claim.state is FieldState.VALID
    )
    assert {"Hopeful"} == {claim.value for claim in taxonomy}
    assert len(rows) == observation.diagnostics[0].count
    assert FieldState.ABSENT not in {
        claim.state
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    }


@pytest.mark.parametrize(
    ("taggings", "expected"),
    (
        ([], {FieldState.ABSENT}),
        ([{"id": 1, "tag_id": 99}], {FieldState.MALFORMED}),
    ),
    ids=("empty", "malformed"),
)
def test_taxonomy_absence_is_synthesized_only_without_terminal_claims(
    taggings: list[dict[str, object]], expected: set[FieldState]
) -> None:
    payload = hardcover_envelope(
        [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157"}],
        [{"id": 1, "title": "Current title", "taggings": taggings}],
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
    states = {
        claim.state
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    }
    assert expected == states


def test_series_and_taxonomy_terminal_states_are_independent() -> None:
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

    assert ProviderStatus.CANDIDATES == observation.status
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
    assert {
        ClaimKind.SERIES: FieldState.MALFORMED,
        ClaimKind.SERIES_SEQUENCE: FieldState.MALFORMED,
        ClaimKind.GENRE: FieldState.OVERSIZED,
        ClaimKind.TAG: FieldState.OVERSIZED,
    } == states
    assert 2 == observation.diagnostics[0].count
    assert any(
        claim.kind is ClaimKind.TITLE
        and claim.value == "Current title"
        and claim.state is FieldState.VALID
        for claim in observation.candidates[0].claims
    )


def test_malformed_taxonomy_rows_are_local_to_referencing_works() -> None:
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
        candidate.work_identity: candidate for candidate in observation.candidates
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
    assert {FieldState.MALFORMED} == first_states
    assert FieldState.MALFORMED not in {claim.state for claim in second_claims}
    assert "Hopeful" in {claim.value for claim in second_claims}
    assert 2 == observation.diagnostics[0].count


def test_unreferenced_malformed_taxonomy_rows_are_diagnostic_only() -> None:
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

    assert 2 == observation.diagnostics[0].count
    assert {FieldState.ABSENT} == {
        claim.state
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    }


@pytest.mark.parametrize(
    ("tags", "expected"),
    (
        (None, FieldState.MALFORMED),
        (
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
    ),
    ids=("unavailable", "oversized"),
)
def test_unavailable_or_oversized_taxonomy_root_is_global(
    tags: list[dict[str, object]] | None, expected: FieldState
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
    payload["data"]["tags"] = tags
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
    assert {expected} == {
        claim.state
        for claim in observation.candidates[0].claims
        if claim.kind in {ClaimKind.GENRE, ClaimKind.TAG}
    }
    assert 1 == observation.diagnostics[0].count
