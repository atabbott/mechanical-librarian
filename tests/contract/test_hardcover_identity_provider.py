from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.adapters.providers.hardcover_identity import (
    AuthorizedIdentityRequest,
    HARDCOVER_IDENTITY_OPERATION,
    HARDCOVER_IDENTITY_QUERY,
    HARDCOVER_IDENTITY_URL,
    HardcoverIdentityProvider,
    IdentityChannelFailure,
    RawIdentityResponseHead,
    StructuralIdentityBodyReader,
    StructuralIdentityByteChannel,
    address_is_public,
    authorize_identity_request,
    identity_query_from_canonical_body,
)
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
    IsbnIdentityQuery,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    ResourceBounds,
    TimeProbe,
)


class HardcoverIdentityProviderContractTests(unittest.TestCase):
    def query(self, isbn: str = "9780000000019") -> IsbnIdentityQuery:
        return IsbnIdentityQuery(
            isbn,
            "0000000019" if isbn.startswith("978") else None,
        )

    def context(
        self,
        *,
        cancelled: bool = False,
        expired: bool = False,
        items: int = 4,
        response_bytes: int = 65_536,
    ) -> OperationContext:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        return OperationContext(
            Deadline(now - timedelta(seconds=1) if expired else now + timedelta(seconds=5)),
            CancellationProbe(CancellationId("hardcover-c3"), lambda: cancelled),
            ResourceBounds(items, response_bytes),
            TimeProbe(lambda: now),
        )

    def body(self, query: IsbnIdentityQuery | None = None) -> bytes:
        query = self.query() if query is None else query
        isbns = [query.canonical_isbn_13]
        if query.equivalent_isbn_10 is not None:
            isbns.append(query.equivalent_isbn_10)
        return json.dumps(
            {
                "operationName": HARDCOVER_IDENTITY_OPERATION,
                "query": HARDCOVER_IDENTITY_QUERY,
                "variables": {"isbns": isbns},
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def row(self) -> dict[str, object]:
        return {
            "id": 1001,
            "book_id": 2001,
            "canonical_id": None,
            "isbn_10": "0000000019",
            "isbn_10_valid": True,
            "isbn_13": "9780000000019",
            "isbn_13_valid": True,
            "isbns_match": True,
            "reading_format_id": 4,
            "state": "normalized",
            "compilation": False,
            "book": {
                "id": 2001,
                "canonical_id": None,
                "book_status_id": 1,
                "state": "processed",
                "title": "Synthetic Provider Title",
                "parent_book_id": None,
                "compilation": False,
                "is_partial_book": False,
            },
        }

    def response_body(self, rows: list[dict[str, object]] | None = None) -> bytes:
        return json.dumps(
            {"data": {"editions": [self.row()] if rows is None else rows}},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def channel(
        self,
        *,
        body: bytes | None = None,
        status: bytes = b"HTTP/1.1 200 OK\r\n",
        headers: tuple[bytes, ...] | None = None,
        selected: str = "1.1.1.1",
        peer: str = "1.1.1.1",
    ) -> StructuralIdentityByteChannel:
        payload = self.response_body() if body is None else body
        if headers is None:
            headers = (
                b"Content-Type: application/json; charset=utf-8\r\n",
                b"Content-Encoding: identity\r\n",
                f"Content-Length: {len(payload)}\r\n".encode(),
            )
        response = RawIdentityResponseHead(
            status, headers, StructuralIdentityBodyReader(iter((payload,))), peer
        )
        return StructuralIdentityByteChannel(
            expected_method="POST",
            expected_url=HARDCOVER_IDENTITY_URL,
            expected_body=self.body(),
            expected_token=b"synthetic-noncredential",
            selected_address=selected,
            connected_peer_address=peer,
            hostname="api.hardcover.app",
            resolution_count=1,
            tls_sni="api.hardcover.app",
            tls_peer_name="api.hardcover.app",
            proxy_mode="disabled",
            address_policy_capability="validated-public-address",
            actual_network=False,
            byte_channel="post-policy-in-memory",
            response=response,
        )

    def provider(
        self,
        channel: StructuralIdentityByteChannel,
        *,
        address: str = "1.1.1.1",
    ) -> HardcoverIdentityProvider:
        return HardcoverIdentityProvider(
            lambda host, port: (address,),
            channel,
            b"synthetic-noncredential",
        )

    def test_frozen_request_auth_and_structural_success(self) -> None:
        query = self.query()
        body = self.body(query)
        self.assertEqual(identity_query_from_canonical_body(body), query)
        channel = self.channel()
        result = self.provider(channel).lookup(query, self.context())
        self.assertIsNone(result.failure)
        self.assertEqual(len(result.observations or ()), 1)
        self.assertTrue(channel.called)
        self.assertTrue(channel.peer_equal)

        request = adapter._build_request(query, "1.1.1.1")
        self.assertIsNotNone(
            authorize_identity_request(
                request, query, b"synthetic-noncredential"
            )
        )
        near_misses = (
            replace(request, method="GET"),
            replace(request, path="/near-miss"),
            replace(request, host_header="near-miss.invalid"),
            replace(request, tls_sni="near-miss.invalid"),
            replace(request, body=body + b" "),
            replace(request, selected_address="127.0.0.1"),
        )
        for near_miss in near_misses:
            with self.subTest(field=near_miss):
                self.assertIsNone(
                    authorize_identity_request(
                        near_miss, query, b"synthetic-noncredential"
                    )
                )

    def test_address_policy_rejects_nonpublic_before_channel_open(self) -> None:
        self.assertTrue(address_is_public("1.1.1.1"))
        for address in (
            "10.0.0.1",
            "127.0.0.1",
            "192.0.2.44",
            "169.254.1.1",
            "224.0.0.1",
            "::1",
            "::ffff:1.1.1.1",
        ):
            with self.subTest(address=address):
                channel = self.channel(selected=address, peer=address)
                result = self.provider(channel, address=address).lookup(
                    self.query(), self.context()
                )
                self.assertEqual(
                    result.failure.kind,
                    IdentityAttemptFailureKind.INCOMPATIBLE,
                )
                self.assertFalse(channel.called)

    def test_closed_status_and_body_handling(self) -> None:
        cases = (
            (401, IdentityAttemptFailureKind.AUTHENTICATION),
            (403, IdentityAttemptFailureKind.AUTHORIZATION),
            (408, IdentityAttemptFailureKind.TIMEOUT),
            (429, IdentityAttemptFailureKind.RATE_LIMITED),
            (302, IdentityAttemptFailureKind.INCOMPATIBLE),
            (500, IdentityAttemptFailureKind.UNAVAILABLE),
            (418, IdentityAttemptFailureKind.INCOMPATIBLE),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                channel = self.channel(
                    status=f"HTTP/1.1 {status} Synthetic\r\n".encode(),
                    body=b"not-json-and-must-not-be-parsed",
                    headers=(),
                )
                result = self.provider(channel).lookup(self.query(), self.context())
                self.assertEqual(result.failure.kind, expected)

        empty = self.channel(body=b"")
        result = self.provider(empty).lookup(self.query(), self.context())
        self.assertEqual(result.failure.kind, IdentityAttemptFailureKind.EMPTY)

        no_match = self.channel(body=self.response_body([]))
        result = self.provider(no_match).lookup(self.query(), self.context())
        self.assertEqual(result.observations, ())

    def test_hostile_parser_rejects_headers_json_and_peer_mismatches(self) -> None:
        malformed_headers = (
            (b"Content-Type: application/json\r\n",) * 2,
            (b"Content-Type: application/json \r\n",),
            (b"Content-Type: application/json; charset=\"utf-8\"\r\n",),
            (b"Content-Type: application/json\r\n", b"Content-Length: 01\r\n"),
        )
        for headers in malformed_headers:
            with self.subTest(headers=len(headers)):
                result = self.provider(self.channel(headers=headers)).lookup(
                    self.query(), self.context()
                )
                self.assertEqual(
                    result.failure.kind, IdentityAttemptFailureKind.MALFORMED
                )

        malformed_bodies = (
            b"\xef\xbb\xbf{}",
            b'{"data":{"editions":[]},"data":{"editions":[]}}',
            b'{"data":{"editions":[]},"extra":null}',
            b'{"errors":[{}],"data":{"editions":[]}}',
            b'{"data":{"editions":1.0}}',
            b'{"data":{"editions":2147483648}}',
            b'{"data":{"editions":' + b"[" * 8 + b"]" * 8 + b"}}",
        )
        for body in malformed_bodies:
            with self.subTest(size=len(body)):
                result = self.provider(self.channel(body=body)).lookup(
                    self.query(), self.context()
                )
                self.assertEqual(
                    result.failure.kind, IdentityAttemptFailureKind.MALFORMED
                )

        peer_mismatch = self.provider(
            self.channel(peer="8.8.8.8")
        ).lookup(self.query(), self.context())
        self.assertEqual(
            peer_mismatch.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
        )

    def test_context_and_construction_fail_before_attempt(self) -> None:
        cases = (
            (self.context(cancelled=True), IdentityAttemptFailureKind.CANCELLED),
            (self.context(expired=True), IdentityAttemptFailureKind.TIMEOUT),
            (self.context(items=3), IdentityAttemptFailureKind.OVERSIZED),
            (self.context(response_bytes=65_535), IdentityAttemptFailureKind.OVERSIZED),
        )
        for context, expected in cases:
            with self.subTest(expected=expected):
                channel = self.channel()
                resolver_calls: list[tuple[str, int]] = []

                def resolver(host: str, port: int) -> tuple[str, ...]:
                    resolver_calls.append((host, port))
                    return ("1.1.1.1",)

                result = HardcoverIdentityProvider(
                    resolver, channel, b"synthetic-noncredential"
                ).lookup(self.query(), context)
                self.assertEqual(result.failure.kind, expected)
                self.assertEqual(resolver_calls, [])
                self.assertFalse(channel.called)

        for token in (b"", b"contains space", b"\x7f", b"A" * 2_049):
            with self.subTest(token_size=len(token)):
                with self.assertRaisesRegex(ValueError, "construction is invalid"):
                    HardcoverIdentityProvider(
                        lambda host, port: ("1.1.1.1",), self.channel(), token
                    )

    def test_resolver_observations_revalidate_before_authorization(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for transition, expected in (
            ("cancelled", IdentityAttemptFailureKind.CANCELLED),
            ("expired", IdentityAttemptFailureKind.TIMEOUT),
        ):
            with self.subTest(transition=transition):
                state = {"cancelled": False, "now": now}
                context = OperationContext(
                    Deadline(now + timedelta(seconds=5)),
                    CancellationProbe(
                        CancellationId("hardcover-c3-transition"),
                        lambda: state["cancelled"],
                    ),
                    ResourceBounds(4, 65_536),
                    TimeProbe(lambda: state["now"]),
                )
                channel = self.channel()

                def resolver(host: str, port: int) -> tuple[str, ...]:
                    if transition == "cancelled":
                        state["cancelled"] = True
                    else:
                        state["now"] = now + timedelta(seconds=6)
                    return ("1.1.1.1",)

                with patch.object(
                    adapter,
                    "authorize_identity_request",
                    wraps=authorize_identity_request,
                ) as authorize:
                    result = HardcoverIdentityProvider(
                        resolver, channel, b"synthetic-noncredential"
                    ).lookup(self.query(), context)
                self.assertEqual(result.failure.kind, expected)
                authorize.assert_not_called()
                self.assertFalse(channel.called)

    def test_channel_revalidates_context_before_consuming_attempt(self) -> None:
        state = {"cancelled": False}
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        context = OperationContext(
            Deadline(now + timedelta(seconds=5)),
            CancellationProbe(
                CancellationId("hardcover-c3-channel"),
                lambda: state["cancelled"],
            ),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: now),
        )
        channel = self.channel()
        real_authorize = authorize_identity_request

        def cancel_after_authorization(*args: object) -> object:
            authorized = real_authorize(*args)
            state["cancelled"] = True
            return authorized

        with patch.object(
            adapter,
            "authorize_identity_request",
            side_effect=cancel_after_authorization,
        ):
            result = self.provider(channel).lookup(self.query(), context)
        self.assertEqual(
            result.failure.kind, IdentityAttemptFailureKind.CANCELLED
        )
        self.assertFalse(channel.called)

    def test_time_and_cancellation_probe_failures_are_value_free(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)

        failed_calls = 0

        def fail_probe() -> bool:
            nonlocal failed_calls
            failed_calls += 1
            if failed_calls <= 2:
                return False
            raise RuntimeError("private-probe-detail")

        unavailable = OperationContext(
            Deadline(now + timedelta(seconds=5)),
            CancellationProbe(CancellationId("hardcover-c3-failed"), fail_probe),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: now),
        )
        result = self.provider(self.channel()).lookup(self.query(), unavailable)
        self.assertEqual(
            result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
        )
        self.assertNotIn("private-probe-detail", repr(result))

    def test_external_exception_failures_are_closed_value_free_and_no_send(self) -> None:
        class FailedChannel:
            calls = 0

            def exchange(self, request: object, context: object) -> object:
                self.calls += 1
                raise RuntimeError("private-exchange-detail")

            def release(self, response: object) -> bool:
                return False

        for stage in ("resolver", "authenticator", "exchange"):
            with self.subTest(stage=stage):
                channel = FailedChannel()

                def resolver(host: str, port: int) -> tuple[str, ...]:
                    if stage == "resolver":
                        raise RuntimeError("private-resolver-detail")
                    return ("1.1.1.1",)

                provider = HardcoverIdentityProvider(
                    resolver, channel, b"synthetic-noncredential"
                )
                patcher = (
                    patch.object(
                        adapter,
                        "authorize_identity_request",
                        side_effect=RuntimeError("private-auth-detail"),
                    )
                    if stage == "authenticator"
                    else patch.object(
                        adapter,
                        "authorize_identity_request",
                        wraps=authorize_identity_request,
                    )
                )
                with patcher:
                    result = provider.lookup(self.query(), self.context())
                self.assertEqual(
                    result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
                )
                self.assertEqual(channel.calls, 1 if stage == "exchange" else 0)
                self.assertNotIn("private-", repr(result))

    def test_response_release_requires_normal_close_or_fallback(self) -> None:
        class Reader:
            def __init__(self, payload: bytes, close_result: object) -> None:
                self.chunks = iter((payload, None))
                self.close_result = close_result
                self.close_calls = 0

            def read_chunk(self, max_bytes: int) -> bytes | None:
                return next(self.chunks)

            def close(self) -> object:
                self.close_calls += 1
                if isinstance(self.close_result, Exception):
                    raise self.close_result
                return self.close_result

        class Channel:
            def __init__(self, response: RawIdentityResponseHead, fallback: object) -> None:
                self.response = response
                self.fallback = fallback
                self.release_calls = 0

            def exchange(self, request: object, context: object) -> RawIdentityResponseHead:
                return self.response

            def release(self, response: object) -> object:
                self.release_calls += 1
                if isinstance(self.fallback, Exception):
                    raise self.fallback
                return self.fallback

        payload = self.response_body()
        for close_result, fallback, available, release_calls in (
            (True, False, True, 0),
            (False, True, True, 1),
            (RuntimeError("private-close"), True, True, 1),
            (False, False, False, 1),
            (False, RuntimeError("private-release"), False, 1),
        ):
            with self.subTest(
                close=type(close_result).__name__,
                fallback=type(fallback).__name__,
            ):
                reader = Reader(payload, close_result)
                response = RawIdentityResponseHead(
                    b"HTTP/1.1 200 OK\r\n",
                    (
                        b"Content-Type: application/json\r\n",
                        f"Content-Length: {len(payload)}\r\n".encode(),
                    ),
                    reader,
                    "1.1.1.1",
                )
                channel = Channel(response, fallback)
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    channel,
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                if available:
                    self.assertIsNone(result.failure)
                else:
                    self.assertEqual(
                        result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
                    )
                self.assertEqual(reader.close_calls, 1)
                self.assertEqual(channel.release_calls, release_calls)
                self.assertNotIn("private-", repr(result))

    def test_channel_failure_uses_trusted_typed_metadata(self) -> None:
        class RaisingChannel:
            def __init__(self, kind: IdentityAttemptFailureKind) -> None:
                self.kind = kind

            def exchange(self, request: object, context: object) -> object:
                raise IdentityChannelFailure(self.kind)

            def release(self, response: object) -> bool:
                return False

        for kind in (
            IdentityAttemptFailureKind.CANCELLED,
            IdentityAttemptFailureKind.TIMEOUT,
            IdentityAttemptFailureKind.UNAVAILABLE,
        ):
            with self.subTest(kind=kind):
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    RaisingChannel(kind),
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(result.failure.kind, kind)

    def test_structural_channel_requires_the_exact_ordered_header_tuple(self) -> None:
        query = self.query()
        request = adapter._build_request(query, "1.1.1.1")
        authorized = authorize_identity_request(
            request, query, b"synthetic-noncredential"
        )
        self.assertIsNotNone(authorized)
        headers = authorized.headers
        mutations = {
            "missing": headers[:-1],
            "altered": ((b"Accept", b"text/plain"), *headers[1:]),
            "duplicated": (*headers[:3], headers[2], *headers[3:]),
            "reordered": (headers[1], headers[0], *headers[2:]),
            "extra": (*headers, (b"Authorization-Extra", b"Bearer extra")),
            "token": (
                *headers[:2],
                (b"Authorization", b"Bearer synthetic-noncredentiaL"),
                *headers[3:],
            ),
        }
        for name, changed in mutations.items():
            with self.subTest(mutation=name):
                channel = self.channel()
                with self.assertRaises(IdentityChannelFailure):
                    channel.exchange(
                        AuthorizedIdentityRequest(request, changed), self.context()
                    )
                self.assertFalse(channel.called)

    def test_sensitive_request_response_and_result_representations_are_redacted(
        self,
    ) -> None:
        query = self.query()
        request = adapter._build_request(query, "1.1.1.1")
        authorized = authorize_identity_request(
            request, query, b"synthetic-noncredential"
        )
        response = RawIdentityResponseHead(
            b"HTTP/1.1 200 OK\r\n",
            (),
            StructuralIdentityBodyReader(iter((self.response_body(),))),
            "1.1.1.1",
        )
        for value in (request, authorized, response):
            rendered = repr(value)
            for forbidden in (
                "9780000000019",
                "0000000019",
                "synthetic-noncredential",
                "Synthetic Provider Title",
            ):
                self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
