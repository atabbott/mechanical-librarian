"""Hardcover transport: query shape, typed GraphQL failure, retry
authority, shared pacing, and deadline and attempt bounds.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
import unittest

import httpx

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
    HardcoverPacer,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from tests.support.hardcover_evidence import (
    HostileEncodedProviderStream,
    InterruptedProviderStream,
    hardcover_envelope,
    isbn_claim,
    isbn_lookup,
    title_claim,
    work_lookup,
)


class HardcoverTransportTests(unittest.TestCase):
    def test_hardcover_publication_field_preserves_supported_precision(self) -> None:
        for lexical in (
            "2024",
            "2024-03",
            "2024-03-05",
            "2024-03-05T10:30:00-04:00",
        ):
            with self.subTest(lexical=lexical):
                self.assertEqual(
                    (FieldState.VALID, lexical),
                    hardcover_metadata._field(
                        ClaimKind.PUBLICATION_DATE,
                        lexical,
                    ),
                )

    def test_rich_schema_query_and_field_order_are_closed(self) -> None:
        captured: list[dict[str, object]] = []
        payload = hardcover_envelope(
            [{"id": 1, "book_id": 1, "isbn_10": None, "isbn_13": "9780306406157", "asin": "b012345678", "title": "Edition title", "subtitle": "Edition subtitle", "release_date": "2026-01-01", "publisher": {"id": 2, "name": "Example Press", "parent_id": 9}, "language": {"id": 3, "code2": "en", "code3": "eng", "language": "English"}, "contributions": [{"id": 7, "contribution": "Author", "author": {"id": 4, "name": "Ada Example"}}]}],
            [{"id": 1, "title": "Work title", "description": "Description", "contributions": [{"id": 8, "contribution": "Translator", "author": {"id": 4, "name": "Ada Example"}}], "book_series": [{"id": 9, "position": "1", "series": {"id": 5, "name": "Series"}}], "taggings": [{"id": 10, "tag": {"id": 6, "tag": "Speculative"}}]}],
        )
        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, headers={"content-type": "application/json"}, json=payload)
        observation = HardcoverMetadataProvider("catalogue", httpx.Client(transport=httpx.MockTransport(handler)), 65_536).observe(isbn_lookup((isbn_claim(), title_claim())))
        self.assertEqual(observation.status, ProviderStatus.CANDIDATES)
        claims = observation.candidates[0].claims
        self.assertEqual(
            "B012345678",
            next(
                claim.value
                for claim in claims
                if claim.kind is ClaimKind.ASIN
            ),
        )
        self.assertEqual(
            {"ebook"},
            {
                claim.applicability
                for claim in claims
                if claim.kind in {ClaimKind.ASIN, ClaimKind.SUBTITLE}
            },
        )
        self.assertEqual([claim.raw_order for claim in claims], list(range(len(claims))))
        self.assertEqual({claim.kind for claim in claims}, {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLICATION_DATE, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE, ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR, ClaimKind.DESCRIPTION, ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE, ClaimKind.TAG, ClaimKind.GENRE})
        self.assertEqual(next(claim.scope for claim in claims if claim.kind is ClaimKind.PUBLISHER), ClaimScope.EDITION)
        self.assertNotIn("parent_id", {claim.value for claim in claims if claim.value is not None})
        self.assertIn("contributions(order_by:{id:asc},limit:17)", captured[0]["query"])
        self.assertIn("taggings(order_by:{id:asc},limit:33)", captured[0]["query"])

    def test_hardcover_operations_keep_relationship_selections_within_depth_three(
        self,
    ) -> None:
        captured: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            )

        for lookup in (
            isbn_lookup((isbn_claim(),)),
            work_lookup(),
        ):
            with self.subTest(kind=lookup.kind):
                captured.clear()
                observation = HardcoverMetadataProvider(
                    "catalogue",
                    httpx.Client(transport=httpx.MockTransport(handler)),
                    65_536,
                ).observe(lookup)
                self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
                operation = captured[0]
                self.assertNotIn(
                    "taggings(order_by:{id:asc},limit:33)"
                    "{id tag{id tag tag_category",
                    operation["query"],
                )
                self.assertIn(
                    "taggings(order_by:{id:asc},limit:33){id tag_id}",
                    operation["query"],
                )
                self.assertIn("tags:tags(", operation["query"])
                self.assertIn(
                    "{id tag tag_category{id category}}",
                    operation["query"],
                )
                if lookup.kind is ProviderQueryKind.ISBN:
                    self.assertEqual(
                        {"isbns": ["9780306406157"]},
                        operation["variables"],
                    )
                else:
                    self.assertEqual(
                        {
                            "authors": ["Ada Example"],
                            "title": "Current title",
                        },
                        operation["variables"],
                    )

    def test_http_success_graphql_errors_are_typed_before_data(self) -> None:
        error = {
            "message": "query exceeds the provider depth limit",
            "extensions": {
                "code": "validation-failed",
                "path": "$.selectionSet.books.selectionSet",
            },
        }
        for payload in (
            {"errors": [error]},
            {
                "data": hardcover_envelope(
                    [
                        {
                            "id": 1,
                            "book_id": 1,
                            "isbn_10": None,
                            "isbn_13": "9780306406157",
                        }
                    ],
                    [{"id": 1, "title": "Current title"}],
                )["data"],
                "errors": [error],
            },
        ):
            with self.subTest(partial="data" in payload):
                provider = HardcoverMetadataProvider(
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
                )
                observation = provider.observe(
                    isbn_lookup((isbn_claim(), title_claim()))
                )
                self.assertEqual(ProviderStatus.UNAVAILABLE, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertEqual((), provider.cover_descriptors)

    def test_graphql_error_envelope_bounds_are_closed_and_do_not_leak(self) -> None:
        def observe(payload: object) -> ProviderObservation:
            return HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            content=(
                                payload
                                if type(payload) is bytes
                                else json.dumps(payload).encode()
                            ),
                        )
                    )
                ),
                65_536,
            ).observe(isbn_lookup((isbn_claim(),)))

        exact_message = "x" * hardcover_metadata.MAX_GRAPHQL_ERROR_MESSAGE_CHARACTERS
        for errors in (
            [{"message": exact_message}],
            [{"message": "declared"}] * hardcover_metadata.MAX_GRAPHQL_ERRORS,
            [
                {
                    "message": "Authorization: Bearer synthetic-private-token",
                    "locations": [{"line": 1, "column": 2}],
                    "path": ["books", 0],
                    "extensions": {
                        "code": "validation-failed",
                        "path": "$.selectionSet.books",
                    },
                }
            ],
        ):
            with self.subTest(valid=len(errors)):
                observation = observe({"errors": errors})
                self.assertEqual(ProviderStatus.UNAVAILABLE, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertNotIn("synthetic-private-token", repr(observation))

        malformed: tuple[object, ...] = (
            {"errors": []},
            {"errors": [{"message": "x" * (len(exact_message) + 1)}]},
            {
                "errors": [{"message": "declared"}]
                * (hardcover_metadata.MAX_GRAPHQL_ERRORS + 1)
            },
            {"errors": [{"message": "declared", "unexpected": True}]},
            {"errors": [{"message": "declared", "extensions": {"nested": {}}}]},
            b'{"errors":[{"message":"\xff"}]}',
            b'{"errors":',
        )
        for payload in malformed:
            with self.subTest(payload=repr(payload)[:80]):
                observation = observe(payload)
                self.assertEqual(ProviderStatus.MALFORMED, observation.status)
                self.assertEqual((), observation.candidates)

    def test_graphql_error_transport_timeout_and_expired_deadline_are_unavailable(
        self,
    ) -> None:
        timed_out = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: (_ for _ in ()).throw(
                        httpx.ReadTimeout("synthetic timeout")
                    )
                )
            ),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        ).observe(isbn_lookup((isbn_claim(),)))
        self.assertEqual(ProviderStatus.UNAVAILABLE, timed_out.status)

        requests: list[httpx.Request] = []
        expired = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: requests.append(request)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the request and still yields the response.
                    or httpx.Response(500)
                )
            ),
            65_536,
        ).observe(isbn_lookup((isbn_claim(),)), deadline=0)
        self.assertEqual(ProviderStatus.INCOMPLETE, expired.status)
        self.assertEqual([], requests)

    def test_hardcover_retries_declared_transients_with_exact_authority(
        self,
    ) -> None:
        for status_code in (500, 502, 503, 504):
            responses = [
                httpx.Response(
                    status_code,
                    headers={"retry-after": "60"},
                ),
                httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=hardcover_envelope([], []),
                ),
            ]
            delays: list[float] = []
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, values=responses: values.pop(0)
                    )
                ),
                65_536,
                sleep=delays.append,
                jitter=lambda: 0.0,
            )
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(isbn_lookup((isbn_claim(),))).status,
                )
                self.assertEqual(
                    [60.0] if status_code == 503 else [1.0],
                    delays,
                )
                self.assertEqual(2, len(provider.attempt_records))

        dispatches = 0

        def unavailable(_: httpx.Request) -> httpx.Response:
            nonlocal dispatches
            dispatches += 1
            return httpx.Response(503, headers={"retry-after": "7"})

        exhausted = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(unavailable)),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            exhausted.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual(3, dispatches)
        self.assertEqual("exhausted", exhausted.attempt_records[-1].outcome)
        self.assertEqual(7.0, exhausted.attempt_records[-1].delay_seconds)

    def test_hardcover_recovers_at_every_permitted_retry_ordinal(
        self,
    ) -> None:
        for prior_failures in range(3):
            responses = [
                httpx.Response(503) for _ in range(prior_failures)
            ]
            responses.append(
                httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=hardcover_envelope([], []),
                )
            )
            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _, values=responses: values.pop(0)
                    )
                ),
                65_536,
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(prior_failures=prior_failures):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(
                        isbn_lookup((isbn_claim(),))
                    ).status,
                )
                self.assertEqual(
                    prior_failures + 1, len(provider.attempt_records)
                )
                self.assertEqual(
                    ["retry"] * prior_failures + ["response_received"],
                    [record.outcome for record in provider.attempt_records],
                )

    def test_hardcover_charges_error_bodies_before_status_retry(self) -> None:
        oversized_calls = 0

        def oversized(_: httpx.Request) -> httpx.Response:
            nonlocal oversized_calls
            oversized_calls += 1
            return httpx.Response(503, content=b"x" * 2_000)

        oversized_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(oversized)),
            1_024,
            sleep=lambda _: self.fail("oversized body must not retry"),
        )
        self.assertEqual(
            ProviderStatus.OVERSIZED,
            oversized_provider.observe(
                isbn_lookup((isbn_claim(),))
            ).status,
        )
        self.assertEqual(1, oversized_calls)
        self.assertEqual(0, oversized_provider._remaining_response_bytes)

        success = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=hardcover_envelope([], []),
        )
        control = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: success)
            ),
            1_024,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            control.observe(isbn_lookup((isbn_claim(),))).status,
        )
        responses = [
            httpx.Response(503, stream=InterruptedProviderStream()),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            ),
        ]
        interrupted = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: responses.pop(0))
            ),
            1_024,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            interrupted.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual(
            control._remaining_response_bytes - 1,
            interrupted._remaining_response_bytes,
        )
        for raw_body in (gzip.compress(b"error"), b"not-gzip"):
            calls = 0

            def encoded_error(_: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                return httpx.Response(
                    503,
                    headers={"content-encoding": "gzip"},
                    stream=HostileEncodedProviderStream(raw_body),  # noqa: B023 - closure is created and consumed within this iteration
                )

            encoded = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(encoded_error)
                ),
                1_024,
                sleep=lambda _: self.fail(
                    "non-identity encoding must not retry"
                ),
            )
            with self.subTest(streamed_encoding=raw_body):
                self.assertEqual(
                    ProviderStatus.MALFORMED,
                    encoded.observe(
                        isbn_lookup((isbn_claim(),))
                    ).status,
                )
                self.assertEqual(1, calls)
                self.assertEqual(
                    1_024 * 9 - len(raw_body),
                    encoded._remaining_response_bytes,
                )

        for decoded_body in (b"error", b"not-gzip"):
            response = httpx.Response(503, content=decoded_body)
            response.headers["content-encoding"] = "gzip"
            encoded = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(lambda _: response)  # noqa: B023 - closure is created and consumed within this iteration
                ),
                1_024,
                sleep=lambda _: self.fail(
                    "preloaded non-identity response must not retry"
                ),
            )
            with self.subTest(preloaded_encoding=decoded_body):
                self.assertEqual(
                    ProviderStatus.MALFORMED,
                    encoded.observe(
                        isbn_lookup((isbn_claim(),))
                    ).status,
                )
                self.assertEqual(
                    1_024 * 9, encoded._remaining_response_bytes
                )

    def test_hardcover_429_retries_only_valid_retry_after_and_graphql_never(
        self,
    ) -> None:
        responses = [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            ),
        ]
        delays: list[float] = []
        recovered = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: responses.pop(0))
            ),
            65_536,
            sleep=delays.append,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            recovered.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual([2.0], delays)

        for retry_after in (None, "61", "2, 3"):
            calls = 0

            def rate_limited(_: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                headers = (
                    {}
                    if retry_after is None  # noqa: B023 - closure is created and consumed within this iteration
                    else {"retry-after": retry_after}  # noqa: B023 - closure is created and consumed within this iteration
                )
                return httpx.Response(429, headers=headers)

            provider = HardcoverMetadataProvider(
                "catalogue",
                httpx.Client(
                    transport=httpx.MockTransport(rate_limited)
                ),
                65_536,
                sleep=lambda _: self.fail("invalid 429 must not retry"),
            )
            with self.subTest(retry_after=retry_after):
                self.assertEqual(
                    ProviderStatus.RATE_LIMITED,
                    provider.observe(
                        isbn_lookup((isbn_claim(),))
                    ).status,
                )
                self.assertEqual(1, calls)

        graphql_calls = 0

        def graphql_error(_: httpx.Request) -> httpx.Response:
            nonlocal graphql_calls
            graphql_calls += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"errors": [{"message": "declared"}]},
            )

        graphql = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(graphql_error)),
            65_536,
            sleep=lambda _: self.fail("GraphQL errors must not retry"),
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            graphql.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual(1, graphql_calls)
        self.assertEqual(
            "provider_declared_error",
            graphql.attempt_records[0].category,
        )
        self.assertEqual("terminal", graphql.attempt_records[0].outcome)

    def test_hardcover_pacer_shares_one_second_dispatch_starts(self) -> None:
        now = [0.0]
        delays: list[float] = []

        def sleep(delay: float) -> None:
            delays.append(delay)
            now[0] += delay

        pacer = HardcoverPacer(clock=lambda: now[0], sleep=sleep)
        self.assertTrue(pacer.wait(deadline=None))
        self.assertTrue(pacer.wait(deadline=None))
        self.assertEqual([1.0], delays)
        self.assertFalse(pacer.wait(deadline=now[0] + 1.0))
        self.assertEqual([1.0], delays)

    def test_hardcover_pacer_serializes_concurrent_callers(self) -> None:
        active = 0
        maximum = 0
        guard = threading.Lock()
        barrier = threading.Barrier(3)

        def sleep(_: float) -> None:
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with guard:
                active -= 1

        pacer = HardcoverPacer(clock=lambda: 0.0, sleep=sleep)
        self.assertTrue(pacer.wait(deadline=None))

        def wait() -> None:
            barrier.wait()
            self.assertTrue(pacer.wait(deadline=None))

        threads = [threading.Thread(target=wait) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(maximum, 1)

    def test_hardcover_requests_retries_and_instances_share_pacing(
        self,
    ) -> None:
        now = [0.0]
        sleeps: list[float] = []
        dispatch_times: list[float] = []

        def sleep(delay: float) -> None:
            sleeps.append(delay)
            now[0] += delay

        pacer = HardcoverPacer(clock=lambda: now[0], sleep=sleep)
        responses = [
            httpx.Response(503),
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=hardcover_envelope([], []),
            ),
        ]

        def retrying(_: httpx.Request) -> httpx.Response:
            dispatch_times.append(now[0])
            return responses.pop(0)

        first = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(retrying)),
            65_536,
            pacer=pacer,
            clock=lambda: now[0],
            sleep=sleep,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            first.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual([0.0, 1.0], dispatch_times)
        self.assertEqual([1.0], sleeps)

        second = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: dispatch_times.append(now[0])  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the dispatch time and still yields the response.
                    or httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=hardcover_envelope([], []),
                    )
                )
            ),
            65_536,
            pacer=pacer,
            clock=lambda: now[0],
            sleep=sleep,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            second.observe(isbn_lookup((isbn_claim(),))).status,
        )
        self.assertEqual([0.0, 1.0, 2.0], dispatch_times)
        self.assertEqual([1.0, 1.0], sleeps)

    def test_hardcover_deadline_and_aggregate_attempt_bounds_are_exact(
        self,
    ) -> None:
        rate_limit_calls = 0

        def rate_limited(_: httpx.Request) -> httpx.Response:
            nonlocal rate_limit_calls
            rate_limit_calls += 1
            return httpx.Response(429, headers={"retry-after": "2"})

        deadline_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(rate_limited)),
            65_536,
            clock=lambda: 0.0,
            sleep=lambda _: self.fail("deadline must reject Retry-After"),
        )
        self.assertEqual(
            ProviderStatus.RATE_LIMITED,
            deadline_provider.observe(
                isbn_lookup((isbn_claim(),)), deadline=2.0
            ).status,
        )
        self.assertEqual(1, rate_limit_calls)

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
        claims = tuple(
            LocalClaim.create(
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
            for value in values
        )
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
            TargetSnapshot(
                "ebook",
                "Current title",
                (),
                fields=fields,
            ),
            (title, *claims),
        )
        dispatches = 0

        def unavailable(_: httpx.Request) -> httpx.Response:
            nonlocal dispatches
            dispatches += 1
            return httpx.Response(503)

        provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(unavailable)),
            65_536,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        provider.observe(plan)
        self.assertEqual(27, dispatches)
        self.assertEqual(27, len(provider.attempt_records))

        interrupted_dispatches = 0

        def interrupted(_: httpx.Request) -> httpx.Response:
            nonlocal interrupted_dispatches
            interrupted_dispatches += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=InterruptedProviderStream(),
            )

        interrupted_provider = HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(interrupted)),
            1,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        interrupted_provider.observe(plan)
        self.assertEqual(9, interrupted_dispatches)
        self.assertEqual(9, len(interrupted_provider.attempt_records))


if __name__ == "__main__":
    unittest.main()
