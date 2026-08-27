from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path

import httpx
import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GOOGLE_BOOKS_URL,
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    qualify_provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity import (
    WorkDispatchRung,
    WorkTitleTransformationFamily,
)


def _isbn(value: str = "9780306406157") -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=value,
        canonical=value,
        excerpt=f"ISBN {value}",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _volume(volume_id: str = "volume_1") -> dict[str, object]:
    return {
        "id": volume_id,
        "volumeInfo": {
            "title": "Example Book",
            "subtitle": "A Test",
            "authors": ["Ada Example"],
            "publisher": "Example Press",
            "publishedDate": "2026-Spring",
            "description": "Synthetic description",
            "industryIdentifiers": [
                {"type": "ISBN_10", "identifier": "0306406152"},
                {"type": "ISBN_13", "identifier": "9780306406157"},
            ],
            "pageCount": 321,
            "printType": "BOOK",
            "categories": ["Synthetic fiction"],
            "language": "en",
            "contentVersion": "1.2.3.0.preview.0",
        },
    }


def _lookup() -> MetadataLookup:
    claim = _isbn()
    return MetadataLookup(
        ProviderQueryKind.ISBN,
        (claim,),
        matched_family="9780306406157",
    )


def _discovery_plan() -> MetadataDiscoveryPlan:
    values = (
        "9780306406157",
        "9780140328721",
        "9780439023528",
        "9780061120084",
        "9780743273565",
        "9780316769488",
        "9780451524935",
        "9780547928227",
    )
    claims = tuple(_isbn(value) for value in values)
    lanes = [
        MetadataDiscoveryLane(
            f"lane_{index:064x}",
            ProviderQueryKind.ISBN,
            (claim.reference,),
            matched_family=value,
            canonical_isbns=(value,),
        )
        for index, (claim, value) in enumerate(zip(claims, values, strict=False))
    ]
    lanes.append(
        MetadataDiscoveryLane(
            f"lane_{len(lanes):064x}",
            ProviderQueryKind.WORK,
            (claims[0].reference,),
            title="Example Book",
            authors=("Ada Example",),
        )
    )
    return MetadataDiscoveryPlan(claims, tuple(lanes))


class _InterruptedStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x"
        raise httpx.ReadTimeout("synthetic interrupted body")


class _HostileEncodedStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self):
        yield self._payload


def test_html_description_is_rendered_and_bound_to_versioned_evidence() -> None:
    volume = _volume()
    info = volume["volumeInfo"]
    assert type(info) is dict
    info["description"] = (
        "<p>One &amp; <em>two</em></p><br/><span>Three</span><hr>2 &lt; 3"
    )
    work_lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        title="Example Book",
        authors=("Ada Example",),
    )
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    ).observe(work_lookup)
    candidate = observation.candidates[0]
    assert "One & two • Three • 2 < 3" == next(
        c.value for c in candidate.claims if c.kind is ClaimKind.DESCRIPTION
    )
    assert (
        ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
        "google-books-html-visible-text-v1",
    ) in {(e.kind, e.value) for e in candidate.evidence}
    qualified = qualify_provider_observation(observation, work_lookup)
    assert ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION in {
        value.kind for value in qualified.candidates[0].evidence
    }


def test_html_description_abstains_on_hostile_ambiguous_or_oversized_input() -> None:
    cases = (
        (
            "active content",
            "<p>Safe</p><script>unsafe()</script>",
            FieldState.MALFORMED,
        ),
        ("event attribute", '<p onclick="unsafe()">Text</p>', FieldState.MALFORMED),
        ("unclosed markup", "<p>Text", FieldState.MALFORMED),
        ("incomplete opening tag", "Summary <em", FieldState.MALFORMED),
        (
            "unterminated quoted attribute",
            'Intro <span title="broken>body</span>',
            FieldState.MALFORMED,
        ),
        ("empty visible text", "<p></p>", FieldState.MALFORMED),
        (
            "over input bound",
            "<p>" + "x" * MAX_DESCRIPTION_CHARACTERS + "</p>",
            FieldState.OVERSIZED,
        ),
    )
    for label, description, expected in cases:
        volume = _volume()
        info = volume["volumeInfo"]
        assert type(info) is dict
        info["description"] = description
        observation = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _, value=volume: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={"totalItems": 1, "items": [value]},
                    )
                )
            ),
            65_536,
        ).observe(_lookup())
        claim = next(
            value
            for value in observation.candidates[0].claims
            if value.kind is ClaimKind.DESCRIPTION
        )
        assert expected is claim.state, label
        assert claim.value is None, label
        assert ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION not in {
            value.kind for value in observation.candidates[0].evidence
        }


def test_optional_occurrences_keep_source_order_duplicates_and_replay() -> None:
    volume = _volume()
    info = volume["volumeInfo"]
    assert type(info) is dict
    info["authors"] = ["Zulu", 7, "Alpha", "Zulu"]
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    ).observe(_lookup())
    candidate = observation.candidates[0]
    authors = [
        claim.value
        for claim in candidate.claims
        if claim.kind is ClaimKind.CREATOR and claim.value is not None
    ]
    assert ["Zulu", "Alpha", "Zulu"] == authors
    assert 1 == observation.diagnostics[0].count
    assert "Zulu" not in observation.diagnostics[0].to_json()
    assert (
        MetadataReport.from_json(
            MetadataReport.create(
                target=TargetFacts(
                    "primary", "ebooks", "item-1", default_target_fields("Example Book")
                ),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=_lookup().local_claims,
                local_reasons=("opening_matter_isbn",),
                providers=(observation,),
            ).to_json()
        ).providers[0]
        == observation
    )


def test_access_time_is_captured_only_at_first_actual_dispatch() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 0, "items": []},
        )

    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        wall_clock=lambda: 1_700_000_000.125,
    ).observe(_lookup())
    assert ProviderStatus.NO_MATCH == observation.status
    assert "2023-11-14T22:13:20.125000Z" == observation.accessed_at
    assert 1 == len(requests)

    def unexpected_clock() -> float:
        pytest.fail("pre-dispatch outcome read the wall clock")

    no_input = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        wall_clock=unexpected_clock,
    ).observe(MetadataLookup(ProviderQueryKind.NONE, ()))
    assert ProviderStatus.INSUFFICIENT_INPUT == no_input.status
    assert no_input.accessed_at is None
    assert 1 == len(requests)

    overflow = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        wall_clock=unexpected_clock,
    ).observe(MetadataDiscoveryPlan((), (), True))
    assert ProviderStatus.QUERY_LIMIT_EXCEEDED == overflow.status
    assert overflow.accessed_at is None
    assert 1 == len(requests)

    expired = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        clock=lambda: 1.0,
        wall_clock=unexpected_clock,
    ).observe(_lookup(), deadline=0.0)
    assert ProviderStatus.INCOMPLETE == expired.status
    assert expired.accessed_at is None
    assert 1 == len(requests)

    invalid_clock = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        wall_clock=lambda: float("nan"),
    ).observe(_lookup())
    assert ProviderStatus.UNAVAILABLE == invalid_clock.status
    assert invalid_clock.accessed_at is None
    assert 1 == len(requests)


def test_retry_after_deadline_and_cancellation_are_fail_closed() -> None:
    responses = [
        httpx.Response(
            503,
            headers={
                "content-type": "application/json",
                "retry-after": "3",
            },
            json={"error": {}},
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 1, "items": [_volume()]},
        ),
    ]
    delays: list[float] = []
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(lambda _: responses.pop(0))),
        65_536,
        sleep=delays.append,
        jitter=lambda: 0.5,
    )
    assert ProviderStatus.CANDIDATES == provider.observe(_lookup()).status
    assert [3.0] == delays

    dispatches = 0

    def unavailable(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return httpx.Response(
            503,
            headers={
                "content-type": "application/json",
                "retry-after": "3",
            },
            json={"error": {}},
        )

    deadline_provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(unavailable)),
        65_536,
        clock=lambda: 0.0,
        sleep=lambda _: pytest.fail("deadline must refuse sleep"),
    )
    assert (
        ProviderStatus.UNAVAILABLE
        == deadline_provider.observe(_lookup(), deadline=3.0).status
    )
    assert 1 == dispatches
    assert "exhausted" == deadline_provider.attempt_records[-1].outcome
    assert 3.0 == deadline_provider.attempt_records[-1].delay_seconds

    cancelled = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
            )
        ),
        65_536,
    )
    with pytest.raises(KeyboardInterrupt):
        cancelled.observe(_lookup())
    assert 1 == len(cancelled.attempt_records)
    assert "cancelled" == cancelled.attempt_records[0].outcome

    sleep_cancelled = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    503,
                    headers={"content-type": "application/json"},
                    json={"error": {}},
                )
            )
        ),
        65_536,
        sleep=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()),
        jitter=lambda: 0.0,
    )
    with pytest.raises(KeyboardInterrupt):
        sleep_cancelled.observe(_lookup())
    assert 1 == len(sleep_cancelled.attempt_records)
    assert "cancelled" == sleep_cancelled.attempt_records[0].outcome


def test_narrow_transport_failure_retries_but_unknown_does_not() -> None:
    responses = 0

    def transient(_: httpx.Request) -> httpx.Response:
        nonlocal responses
        responses += 1
        if responses == 1:
            raise httpx.ReadTimeout("synthetic timeout")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 1, "items": [_volume()]},
        )

    recovered = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(transient)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    ).observe(_lookup())
    assert ProviderStatus.CANDIDATES == recovered.status
    assert 2 == responses

    responses = 0

    def unknown(_: httpx.Request) -> httpx.Response:
        nonlocal responses
        responses += 1
        raise httpx.ConnectError("connection reset by peer")

    failed = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(unknown)),
        65_536,
        sleep=lambda _: pytest.fail("unknown failure must not retry"),
    ).observe(_lookup())
    assert ProviderStatus.UNAVAILABLE == failed.status
    assert 1 == responses


def test_observation_and_interrupted_body_budgets_are_aggregate() -> None:
    dispatches = 0

    def unavailable(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            content=b"",
        )

    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(unavailable)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    provider.observe(_discovery_plan())
    assert 54 == dispatches
    assert 54 == len(provider.attempt_records)

    interrupted_dispatches = 0

    def interrupted(_: httpx.Request) -> httpx.Response:
        nonlocal interrupted_dispatches
        interrupted_dispatches += 1
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_InterruptedStream(),
        )

    interrupted_provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(interrupted)),
        1,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    interrupted_provider.observe(_discovery_plan())
    assert 9 == interrupted_dispatches
    assert 9 == len(interrupted_provider.attempt_records)


def test_encoded_error_bodies_are_raw_charged_or_fail_preloaded() -> None:
    for raw_body in (gzip.compress(b"error"), b"not-gzip"):
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                503,
                headers={"content-encoding": "gzip"},
                stream=_HostileEncodedStream(raw_body),  # noqa: B023 - closure is created and consumed within this iteration
            )

        provider = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(transport=httpx.MockTransport(handler)),
            1_024,
            sleep=lambda _: pytest.fail("non-identity encoding must not retry"),
        )
        assert ProviderStatus.MALFORMED == provider.observe(_lookup()).status
        assert 1 == calls
        assert 1_024 * 9 - len(raw_body) == provider._remaining_response_bytes

    for decoded_body in (b"error", b"not-gzip"):
        response = httpx.Response(503, content=decoded_body)
        response.headers["content-encoding"] = "gzip"
        provider = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: response)  # noqa: B023 - closure is created and consumed within this iteration
            ),
            1_024,
            sleep=lambda _: pytest.fail(
                "preloaded non-identity response must not retry"
            ),
        )
        assert ProviderStatus.MALFORMED == provider.observe(_lookup()).status
        assert 1_024 * 9 == provider._remaining_response_bytes


def test_retries_503_with_fresh_backoff_and_records_each_dispatch() -> None:
    responses = [
        httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": {}},
        ),
        httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": {}},
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 1, "items": [_volume()]},
        ),
    ]
    delays: list[float] = []
    jitters = iter((0.25, 0.75))
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(lambda _: responses.pop(0))),
        65_536,
        sleep=delays.append,
        jitter=lambda: next(jitters),
    )

    observation = provider.observe(_lookup())

    assert ProviderStatus.CANDIDATES == observation.status
    assert [1.25, 2.75] == delays
    assert 3 == len(provider.attempt_records)
    assert ["retry", "retry", "response_received"] == [
        record.outcome for record in provider.attempt_records
    ]
    assert ["http_503", "http_503", "none"] == [
        record.category for record in provider.attempt_records
    ]


@pytest.mark.parametrize(
    "prior_failures",
    range(6),
    ids=("none", "one", "two", "three", "four", "five"),
)
def test_recovers_at_every_permitted_503_retry_ordinal(
    prior_failures: int,
) -> None:
    responses = [
        httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": {}},
        )
        for _ in range(prior_failures)
    ]
    responses.append(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 1, "items": [_volume()]},
        )
    )
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(lambda _, values=responses: values.pop(0))
        ),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    assert ProviderStatus.CANDIDATES == provider.observe(_lookup()).status
    assert prior_failures + 1 == len(provider.attempt_records)
    assert ["retry"] * prior_failures + ["response_received"] == [
        record.outcome for record in provider.attempt_records
    ]


def test_503_retry_authority_is_capped_at_six_dispatches() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            503,
            headers={"content-type": "application/json"},
            json={"error": {}},
        )

    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )

    observation = provider.observe(_lookup())

    assert ProviderStatus.UNAVAILABLE == observation.status
    assert 6 == requests
    assert 6 == len(provider.attempt_records)
    assert "exhausted" == provider.attempt_records[-1].outcome


def test_keyless_and_keyed_requests_are_fixed_and_credentials_never_persist() -> None:
    for api_key in (None, "private-api-key"):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)  # noqa: B023 - closure is created and consumed within this iteration
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"totalItems": 1, "items": [_volume()]},
            )

        observation = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            api_key=api_key,
        ).observe(_lookup())
        assert ProviderStatus.CANDIDATES == observation.status
        assert "google_books" == observation.authority
        assert 1 == len(requests)
        assert GOOGLE_BOOKS_URL == str(requests[0].url.copy_with(query=None))
        assert "isbn:9780306406157" == requests[0].url.params["q"]
        assert "6" == requests[0].url.params["maxResults"]
        assert api_key == requests[0].url.params.get("key")
        candidate = observation.candidates[0]
        assert "google-books-volume-volume_1" == candidate.identity
        assert "unresolved" == candidate.work_identity
        assert (
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                "unresolved",
                ClaimScope.EDITION,
            )
            in candidate.evidence
        )
        assert "2026-Spring" in {
            evidence.value
            for evidence in candidate.evidence
            if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        }
        assert not any(
            claim.kind is ClaimKind.PUBLICATION_DATE for claim in candidate.claims
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields("Example Book"),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=_lookup().local_claims,
            local_reasons=("opening_matter_isbn",),
            providers=(observation,),
        )
        assert "private-api-key" not in report.to_json()


def test_work_query_requires_exact_complete_title_and_author_order() -> None:
    requests: list[httpx.Request] = []
    volume = _volume()
    volume["volumeInfo"]["publishedDate"] = "2026-04-03"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"totalItems": 1, "items": [volume]},
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Example Book",
        ("Ada Example",),
    )
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
    ).observe(lookup)
    observation = qualify_provider_observation(observation, lookup)
    assert ProviderStatus.CANDIDATES == observation.status
    assert (
        'intitle:"Example Book" inauthor:"Ada Example"' == requests[0].url.params["q"]
    )
    assert all(
        claim.scope is ClaimScope.WORK for claim in observation.candidates[0].claims
    )
    assert not any(
        claim.kind is ClaimKind.PUBLICATION_DATE
        for claim in observation.candidates[0].claims
    )
    retained = next(
        value
        for value in observation.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert () == retained.links
    assert all(
        value.scope is ClaimScope.WORK or value is retained
        for value in observation.candidates[0].evidence
    )


@pytest.mark.parametrize(
    "title",
    ("[retail]", "(1)", "(2010)", "ISBN 9780306406157", "2nd Edition", "Book 2"),
)
def test_decoration_only_work_query_is_insufficient_without_request(title: str) -> None:
    requests: list[httpx.Request] = []
    lookup = MetadataLookup(ProviderQueryKind.WORK, (), title, ("Ada Example",))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
    ).observe(lookup)

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert [] == requests
    assert (0, 0) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


def test_filename_author_prefix_shapes_google_books_query_but_never_admits() -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Ada_Example_-_Example Book",
        ("Ada Example",),
    )
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [_volume()]},
                )
            )
        ),
        65_536,
    ).observe(lookup)
    qualified = qualify_provider_observation(observation, lookup)

    assert ProviderStatus.CANDIDATES is observation.status
    assert observation.dispatch_lineage is not None
    assert WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX in (
        observation.dispatch_lineage.transformations
    )
    assert ProviderStatus.NO_MATCH is qualified.status
    assert qualified.transformation_lineage is None


def test_work_query_ladder_stops_on_first_candidates_and_accounts_retry() -> None:
    requests: list[httpx.Request] = []
    volume = _volume()
    volume["volumeInfo"]["title"] = "Example Book: A Subtitle"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=(
                {"totalItems": 0, "items": []}
                if len(requests) == 1
                else {"totalItems": 1, "items": [volume]}
            ),
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Example Book: A Subtitle (Book 2)",
        ("Ada Example",),
    )
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
    )

    observation = provider.observe(lookup)

    assert ProviderStatus.CANDIDATES is observation.status
    assert 2 == len(requests)
    assert 'intitle:"Example Book: A Subtitle"' in requests[0].url.params["q"]
    assert 'intitle:"Example Book"' in requests[1].url.params["q"]
    assert 1 == observation.request_accounting.initial_dispatches
    assert 1 == observation.request_accounting.retry_dispatches
    assert (
        observation.dispatch_lineage is not None
        and observation.dispatch_lineage.rung is WorkDispatchRung.SUBTITLE_STRIPPED
    )


def test_widened_search_cannot_bypass_work_identity_qualification() -> None:
    volume = _volume()
    volume["volumeInfo"]["title"] = "Different Work"
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Example Book: A Subtitle",
        ("Ada Example",),
    )
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    )

    qualified = qualify_provider_observation(provider.observe(lookup), lookup)

    assert ProviderStatus.NO_MATCH is qualified.status
    assert not qualified.candidates


def test_candidate_limit_gets_exactly_one_narrowing_retry() -> None:
    requests: list[httpx.Request] = []
    overflow = [_volume(f"volume_{index}") for index in range(6)]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=(
                {"totalItems": 6, "items": overflow}
                if len(requests) == 1
                else {"totalItems": 0, "items": []}
            ),
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Example Book",
        ("Ada Example", "Bea Example"),
    )
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
    ).observe(lookup)

    assert ProviderStatus.NO_MATCH is observation.status
    assert 2 == len(requests)
    assert (
        'inauthor:"Ada Example" inauthor:"Bea Example"' in requests[0].url.params["q"]
    )
    assert 'inauthor:"Ada Example Bea Example"' in requests[1].url.params["q"]
    assert 1 == observation.request_accounting.retry_dispatches


def test_successful_lane_variant_survives_canonical_report_replay() -> None:
    claim = _isbn()
    lane = MetadataDiscoveryLane(
        f"lane_{1:064x}",
        ProviderQueryKind.WORK,
        (claim.reference,),
        title="Example Book",
        authors=("Ada Example",),
    )
    provider = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [_volume()]},
                )
            )
        ),
        65_536,
    )
    observation = provider.observe(MetadataDiscoveryPlan((claim,), (lane,)))
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            tuple(
                TargetField(
                    TargetDestination.AUTHORS,
                    TargetFieldState.VALUE,
                    ("Ada Example",),
                )
                if field.destination is TargetDestination.AUTHORS
                else field
                for field in default_target_fields("Example Book")
            ),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(observation,),
    )

    replayed = MetadataReport.from_json(report.to_json())

    lineage = replayed.providers[0].lanes[0].dispatch_lineage
    assert lineage is not None
    assert WorkDispatchRung.NORMALIZED is lineage.rung


def test_exact_isbn_lane_promotes_standard_publication_date() -> None:
    volume = _volume()
    volume["volumeInfo"]["publishedDate"] = "2026-04-03"
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    ).observe(_lookup())
    publication = next(
        claim
        for claim in observation.candidates[0].claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
    )
    assert "2026-04-03" == publication.value
    assert "2026-04-03" == publication.date_lexical
    assert not any(
        evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        for evidence in observation.candidates[0].evidence
    )


def test_work_query_does_not_choose_between_unlinked_matching_volumes() -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Example Book",
        ("Ada Example",),
    )
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "totalItems": 2,
                        "items": [_volume("volume_1"), _volume("volume_2")],
                    },
                )
            )
        ),
        65_536,
    ).observe(lookup)
    qualified = qualify_provider_observation(observation, lookup)
    assert ProviderStatus.NO_MATCH == qualified.status
    assert () == qualified.candidates


def test_no_match_malformed_and_candidate_bound_are_distinct() -> None:
    cases = (
        (
            {"totalItems": 0},
            ProviderStatus.NO_MATCH,
        ),
        (
            {"totalItems": 1, "items": [{"id": "bad"}]},
            ProviderStatus.MALFORMED,
        ),
        (
            {
                "totalItems": 6,
                "items": [_volume(f"volume_{index}") for index in range(6)],
            },
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ),
    )
    for payload, expected in cases:
        observation = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=payload,  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
        ).observe(_lookup())
        assert expected == observation.status
        assert () == observation.candidates


def test_declared_quota_and_http_errors_are_typed() -> None:
    cases = (
        (
            403,
            {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}},
            ProviderStatus.RATE_LIMITED,
        ),
        (
            403,
            {"error": {"errors": [{"reason": "dailyLimitExceededUnreg"}]}},
            ProviderStatus.RATE_LIMITED,
        ),
        (
            401,
            {"error": {"errors": [{"reason": "required"}]}},
            ProviderStatus.AUTHENTICATION,
        ),
        (
            403,
            {"error": {"errors": [{"reason": "forbidden"}]}},
            ProviderStatus.AUTHORIZATION,
        ),
        (
            503,
            {"error": {"errors": [{"reason": "backendError"}]}},
            ProviderStatus.UNAVAILABLE,
        ),
    )
    for status_code, payload, expected in cases:
        observation = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        status_code,  # noqa: B023 - closure is created and consumed within this iteration
                        headers={"content-type": "application/json"},
                        json=payload,  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        ).observe(_lookup())
        assert expected == observation.status


def test_invalid_scalar_claims_are_field_local_and_keep_valid_evidence() -> None:
    volume = _volume()
    info = volume["volumeInfo"]
    assert isinstance(info, dict)
    info["subtitle"] = 7
    info["description"] = "x" * (MAX_DESCRIPTION_CHARACTERS + 1)

    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    ).observe(_lookup())

    assert ProviderStatus.CANDIDATES == observation.status
    candidate = observation.candidates[0]
    states = {
        claim.kind: claim.state
        for claim in candidate.claims
        if claim.kind in {ClaimKind.SUBTITLE, ClaimKind.DESCRIPTION}
    }
    assert {
        ClaimKind.SUBTITLE: FieldState.MALFORMED,
        ClaimKind.DESCRIPTION: FieldState.OVERSIZED,
    } == states
    assert any(
        claim.kind is ClaimKind.ISBN and claim.state is FieldState.VALID
        for claim in candidate.claims
    )
    assert any(
        claim.kind is ClaimKind.TITLE
        and claim.state is FieldState.VALID
        and claim.value == "Example Book"
        for claim in candidate.claims
    )
    assert ProviderEvidenceKind.PUBLICATION_DATE_TEXT in {
        evidence.kind for evidence in candidate.evidence
    }


def test_malformed_identifier_entries_fail_but_unsupported_types_are_ignored() -> None:
    malformed = (
        "not-an-object",
        {"type": "ISBN_13"},
        {
            "type": "ISBN_13",
            "identifier": "9780306406157",
            "unexpected": "field",
        },
    )
    for identifier in malformed:
        volume = _volume()
        info = volume["volumeInfo"]
        assert isinstance(info, dict)
        info["industryIdentifiers"] = [identifier]
        observation = GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={"totalItems": 1, "items": [volume]},  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
        ).observe(_lookup())
        assert ProviderStatus.MALFORMED == observation.status
        assert () == observation.candidates

    volume = _volume()
    info = volume["volumeInfo"]
    assert isinstance(info, dict)
    identifiers = info["industryIdentifiers"]
    assert isinstance(identifiers, list)
    identifiers.append({"type": "OTHER", "identifier": "synthetic-id"})
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [volume]},
                )
            )
        ),
        65_536,
    ).observe(_lookup())
    assert ProviderStatus.CANDIDATES == observation.status


def test_oversized_timeout_and_expired_deadline_fail_closed() -> None:
    oversized = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json",
                        "content-length": "2000",
                    },
                    content=b"{}",
                )
            )
        ),
        1024,
    ).observe(_lookup())
    assert ProviderStatus.OVERSIZED == oversized.status

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout")

    unavailable = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(transport=httpx.MockTransport(timeout)),
        65_536,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    ).observe(_lookup())
    assert ProviderStatus.UNAVAILABLE == unavailable.status

    requests: list[httpx.Request] = []
    expired = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the request and still yields the response.
                    or httpx.Response(200, json={})
                )
            )
        ),
        65_536,
    ).observe(_lookup(), deadline=0.0)
    assert ProviderStatus.INCOMPLETE == expired.status
    assert [] == requests


@pytest.mark.parametrize(
    "payload",
    (
        b"{",
        b'{"totalItems":0,"totalItems":1}',
        json.dumps({"totalItems": "secret-value"}).encode(),
    ),
    ids=("truncated", "duplicate_key", "wrong_type"),
)
def test_malformed_and_duplicate_json_never_escape_as_raw_artifacts(
    payload: bytes,
) -> None:
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=payload,  # noqa: B023 - closure is created and consumed within this iteration
                )
            )
        ),
        65_536,
    ).observe(_lookup())
    assert ProviderStatus.MALFORMED == observation.status
    assert "secret-value" not in repr(observation)


def test_undispatched_causes_are_once_per_physical_lookup_and_closed() -> None:
    def provider() -> GoogleBooksMetadataProvider:
        return GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: pytest.fail("undispatched request")
                )
            ),
            65_536,
        )

    aggregate_bytes = provider()
    aggregate_bytes._remaining_response_bytes = 0
    observation = aggregate_bytes.observe(_lookup())
    assert ProviderStatus.INCOMPLETE == observation.status
    assert ["aggregate_bytes"] == [
        record.cause.value for record in aggregate_bytes.incomplete_causes
    ]

    dispatch_count = provider()
    dispatch_count._attempt_count = 999
    dispatch_count.observe(_lookup())
    assert ["dispatch_count"] == [
        record.cause.value for record in dispatch_count.incomplete_causes
    ]

    deadline = provider()
    deadline.observe(_lookup(), deadline=0.0)
    assert ["deadline"] == [record.cause.value for record in deadline.incomplete_causes]
    serialized = repr(deadline.incomplete_causes)
    for forbidden in ("credential", "private", "secret", "query", "path"):
        assert forbidden not in serialized


def test_report_proposal_admission_and_review_packet_replay_google_evidence() -> None:
    observation = GoogleBooksMetadataProvider(
        "google_books",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"totalItems": 1, "items": [_volume()]},
                )
            )
        ),
        65_536,
    ).observe(_lookup())
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Example Book"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=_lookup().local_claims,
        local_reasons=("opening_matter_isbn",),
        providers=(observation,),
    )
    proposal = evaluate_report(report)
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            case_data = json.loads(
                repository.get_review_case(admitted["review_case_id"])
            )["case"]
            case = ReviewCase.from_json(
                json.dumps(
                    case_data,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            packet = ShadowReviewPacket.create(case, proposal, report)
        finally:
            repository.close()
    serialized = packet.to_json()
    assert "Example Press" in serialized
    assert "2026-Spring" in serialized
    assert not any(
        value["destination"] in {"publishedYear", "publishedDate"}
        for value in packet.proposals
    )
    assert "private-api-key" not in serialized


def test_over_cap_optional_collection_rejects_once_without_member_traversal() -> None:
    counts: list[int] = []
    assert () == __import__(
        "mechanical_librarian.adapters.providers.google_books_metadata",
        fromlist=["_optional_string_list"],
    )._optional_string_list([object()] * 9, 8, counts.append)
    assert [1] == counts
