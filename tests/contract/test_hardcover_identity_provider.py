from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.adapters.providers.hardcover_identity import (
    HARDCOVER_IDENTITY_OPERATION,
    HARDCOVER_IDENTITY_QUERY,
    HARDCOVER_IDENTITY_URL,
    HardcoverIdentityProvider,
    RawIdentityResponse,
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
        response = RawIdentityResponse(status, headers, (payload,), peer)
        return StructuralIdentityByteChannel(
            expected_method="POST",
            expected_url=HARDCOVER_IDENTITY_URL,
            expected_body=self.body(),
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

    def test_sensitive_request_response_and_result_representations_are_redacted(
        self,
    ) -> None:
        query = self.query()
        request = adapter._build_request(query, "1.1.1.1")
        authorized = authorize_identity_request(
            request, query, b"synthetic-noncredential"
        )
        response = RawIdentityResponse(
            b"HTTP/1.1 200 OK\r\n",
            (),
            (self.response_body(),),
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
