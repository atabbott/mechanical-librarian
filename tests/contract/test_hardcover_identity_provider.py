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
    StructuralHardcoverIdentityPreview,
    StructuralIdentityBodyReader,
    StructuralIdentityByteChannel,
    address_is_public,
    authorize_identity_request,
    identity_query_from_canonical_body,
)
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
    IdentityReportError,
    IsbnIdentityQuery,
    RunHardcoverFixedAdapterPreview,
    RunHardcoverIdentityReport,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.domain.hardcover_identity import validate_private_query


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

        interrupted_calls = 0

        def interrupt_probe() -> bool:
            nonlocal interrupted_calls
            interrupted_calls += 1
            if interrupted_calls <= 2:
                return False
            raise KeyboardInterrupt("private-interrupt-detail")

        cancelled = OperationContext(
            Deadline(now + timedelta(seconds=5)),
            CancellationProbe(
                CancellationId("hardcover-c3-interrupted"), interrupt_probe
            ),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: now),
        )
        result = self.provider(self.channel()).lookup(self.query(), cancelled)
        self.assertEqual(result.failure.kind, IdentityAttemptFailureKind.CANCELLED)
        self.assertNotIn("private-interrupt-detail", repr(result))

    def test_each_context_authority_stage_handles_every_base_failure(self) -> None:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for failure_call in range(1, 6):
            for error, expected in (
                (SystemExit("private-system-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
                (GeneratorExit("private-generator-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
                (KeyboardInterrupt("private-cancel"), IdentityAttemptFailureKind.CANCELLED),
            ):
                with self.subTest(
                    failure_call=failure_call, error=type(error).__name__
                ):
                    calls = 0

                    def time_probe() -> datetime:
                        nonlocal calls
                        calls += 1
                        # TimeProbe validates one value at construction; the
                        # matrix counts only attempt-time observations.
                        if calls - 1 == failure_call:
                            raise error
                        return now

                    context = OperationContext(
                        Deadline(now + timedelta(seconds=5)),
                        CancellationProbe(
                            CancellationId("hardcover-c3-stage"), lambda: False
                        ),
                        ResourceBounds(4, 65_536),
                        TimeProbe(time_probe),
                    )
                    payload = self.response_body()
                    reader = StructuralIdentityBodyReader(iter((payload,)))
                    response = RawIdentityResponseHead(
                        b"HTTP/1.1 200 OK\r\n",
                        (
                            b"Content-Type: application/json\r\n",
                            f"Content-Length: {len(payload)}\r\n".encode(),
                        ),
                        reader,
                        "1.1.1.1",
                    )
                    channel = StructuralIdentityByteChannel(
                        expected_method="POST",
                        expected_url=HARDCOVER_IDENTITY_URL,
                        expected_body=self.body(),
                        expected_token=b"synthetic-noncredential",
                        selected_address="1.1.1.1",
                        connected_peer_address="1.1.1.1",
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
                    result = self.provider(channel).lookup(self.query(), context)
                    self.assertEqual(result.failure.kind, expected)
                    self.assertEqual(channel.called, failure_call == 5)
                    self.assertEqual(reader.closed, failure_call == 5)
                    self.assertNotIn("private-", repr(result))

    def test_every_returned_malformed_head_releases_body_ownership(self) -> None:
        class ResponseSubclass(RawIdentityResponseHead):
            pass

        class MalformedHead:
            def __init__(self, body_reader: object) -> None:
                self.body_reader = body_reader

        class ReturningChannel:
            def __init__(self, response: object) -> None:
                self.response = response

            def exchange(self, request: object, context: object) -> object:
                return self.response

        for kind in (
            "subclass",
            "malformed",
            "close_failure",
            "close_accessor_runtime",
            "close_accessor_base",
            "close_accessor_cancel",
        ):
            with self.subTest(kind=kind):
                if kind == "close_failure":
                    class FailedCloseReader:
                        close_calls = 0

                        def close(self) -> None:
                            self.close_calls += 1
                            raise RuntimeError("private-close-detail")

                    reader: object = FailedCloseReader()
                elif kind.startswith("close_accessor"):
                    error = {
                        "close_accessor_runtime": RuntimeError(
                            "private-close-access"
                        ),
                        "close_accessor_base": SystemExit("private-close-base"),
                        "close_accessor_cancel": KeyboardInterrupt(
                            "private-close-cancel"
                        ),
                    }[kind]

                    class FailedCloseAccessor:
                        access_calls = 0

                        @property
                        def close(self) -> object:
                            self.access_calls += 1
                            raise error

                    reader = FailedCloseAccessor()
                else:
                    reader = StructuralIdentityBodyReader(iter((b"must-not-read",)))
                if kind == "subclass":
                    response: object = ResponseSubclass(
                        b"HTTP/1.1 200 OK\r\n", (), reader, "1.1.1.1"
                    )
                else:
                    response = MalformedHead(reader)
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    ReturningChannel(response),
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(
                    result.failure.kind,
                    IdentityAttemptFailureKind.UNAVAILABLE
                    if kind.startswith("close_")
                    else IdentityAttemptFailureKind.MALFORMED,
                )
                if kind == "close_failure":
                    self.assertEqual(reader.close_calls, 1)
                    self.assertNotIn("private-close-detail", repr(result))
                elif kind.startswith("close_accessor"):
                    self.assertEqual(reader.access_calls, 1)
                    self.assertNotIn("private-close", repr(result))
                else:
                    self.assertTrue(reader.closed)
                    self.assertEqual(reader.read_count, 0)

    def test_exact_malformed_head_suppresses_hostile_close_accessors(self) -> None:
        class ReturningChannel:
            def __init__(self, response: object) -> None:
                self.response = response

            def exchange(self, request: object, context: object) -> object:
                return self.response

        for error in (
            RuntimeError("private-close-access"),
            SystemExit("private-close-base"),
            KeyboardInterrupt("private-close-cancel"),
        ):
            with self.subTest(error=type(error).__name__):
                class HostileCloseReader:
                    access_calls = 0

                    def read_chunk(self, max_bytes: int) -> bytes | None:
                        return b"must-not-read"

                    @property
                    def close(self) -> object:
                        self.access_calls += 1
                        raise error

                reader = HostileCloseReader()
                response = RawIdentityResponseHead(
                    b"malformed-status\r\n", (), reader, "1.1.1.1"
                )
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    ReturningChannel(response),
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(
                    result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
                )
                self.assertEqual(reader.access_calls, 1)

    def test_injected_authority_failures_are_typed_value_free_and_no_send(self) -> None:
        errors = (
            (RuntimeError("private-runtime"), IdentityAttemptFailureKind.UNAVAILABLE),
            (SystemExit("private-system-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
            (GeneratorExit("private-generator-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
            (KeyboardInterrupt("private-cancel"), IdentityAttemptFailureKind.CANCELLED),
        )

        class CountingChannel:
            def __init__(self, error: BaseException | None = None) -> None:
                self.error = error
                self.calls = 0

            def exchange(self, request: object, context: object) -> object:
                self.calls += 1
                if self.error is not None:
                    raise self.error
                raise AssertionError("unexpected exchange")

        for error, expected in errors:
            for stage in ("resolver", "authenticator", "exchange"):
                with self.subTest(stage=stage, error=type(error).__name__):
                    channel = CountingChannel(error if stage == "exchange" else None)

                    def resolver(host: str, port: int) -> tuple[str, ...]:
                        if stage == "resolver":
                            raise error
                        return ("1.1.1.1",)

                    provider = HardcoverIdentityProvider(
                        resolver, channel, b"synthetic-noncredential"
                    )
                    patcher = (
                        patch.object(
                            adapter,
                            "authorize_identity_request",
                            side_effect=error,
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
                    self.assertEqual(result.failure.kind, expected)
                    self.assertNotIn("private-", repr(result))
                    self.assertEqual(channel.calls, 1 if stage == "exchange" else 0)

        class StagedExchangeChannel:
            def __init__(self, value: object) -> None:
                self.value = value
                self.accesses = 0

            @property
            def exchange(self) -> object:
                self.accesses += 1
                if self.accesses == 1:
                    return lambda request, context: None
                if isinstance(self.value, BaseException):
                    raise self.value
                return self.value

        for value, expected in (
            (None, IdentityAttemptFailureKind.UNAVAILABLE),
            (SystemExit("private-exchange-access"), IdentityAttemptFailureKind.UNAVAILABLE),
            (GeneratorExit("private-exchange-base"), IdentityAttemptFailureKind.UNAVAILABLE),
            (KeyboardInterrupt("private-exchange-cancel"), IdentityAttemptFailureKind.CANCELLED),
        ):
            with self.subTest(exchange_accessor=type(value).__name__):
                channel = StagedExchangeChannel(value)
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    channel,
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(result.failure.kind, expected)
                self.assertEqual(channel.accesses, 2)
                self.assertNotIn("private-", repr(result))

    def test_application_attempt_accessors_and_calls_are_typed_value_free(self) -> None:
        private_query = validate_private_query(
            {"isbn": "9780000000019", "title": "Synthetic Title"}
        )
        for error, expected in (
            (SystemExit("private-system-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
            (GeneratorExit("private-generator-exit"), IdentityAttemptFailureKind.UNAVAILABLE),
            (KeyboardInterrupt("private-cancel"), IdentityAttemptFailureKind.CANCELLED),
        ):
            for method, runner in (
                (
                    "lookup",
                    lambda owner: RunHardcoverIdentityReport().run(
                        owner, private_query, self.context()
                    ),
                ),
                (
                    "run",
                    lambda owner: RunHardcoverFixedAdapterPreview().run(owner, {}),
                ),
            ):
                with self.subTest(method=method, error=type(error).__name__):
                    class HostileAccessor:
                        def __getattribute__(self, name: str) -> object:
                            if name == method:
                                raise error
                            return super().__getattribute__(name)

                    class HostileCall:
                        def lookup(self, query: object, context: object) -> object:
                            raise error

                        def run(self, source: object) -> object:
                            raise error

                    for owner in (HostileAccessor(), HostileCall()):
                        with self.assertRaises(IdentityReportError) as caught:
                            runner(owner)
                        self.assertIs(caught.exception.kind, expected)
                        self.assertNotIn("private-", str(caught.exception))
                        self.assertIsNone(caught.exception.__cause__)

    def test_response_release_is_conclusive_on_every_post_exchange_exit(self) -> None:
        class Reader:
            def __init__(
                self,
                chunks: tuple[bytes | None | BaseException, ...],
                close_value: object = False,
            ) -> None:
                self.chunks = iter(chunks)
                self.close_value = close_value
                self.close_calls = 0

            def read_chunk(self, max_bytes: int) -> bytes | None:
                value = next(self.chunks)
                if isinstance(value, BaseException):
                    raise value
                return value

            def close(self) -> object:
                self.close_calls += 1
                if isinstance(self.close_value, BaseException):
                    raise self.close_value
                return self.close_value

        class Channel:
            def __init__(self, response: object, release_value: object) -> None:
                self.response = response
                self.release_value = release_value
                self.release_calls = 0

            def exchange(self, request: object, context: object) -> object:
                return self.response

            def release(self, response: object) -> object:
                self.release_calls += 1
                if isinstance(self.release_value, BaseException):
                    raise self.release_value
                return self.release_value

        payload = self.response_body()
        paths = (
            (
                "status",
                b"HTTP/1.1 401 Synthetic\r\n",
                (),
                (b"must-not-read",),
                IdentityAttemptFailureKind.AUTHENTICATION,
            ),
            (
                "malformed",
                b"malformed\r\n",
                (),
                (b"must-not-read",),
                IdentityAttemptFailureKind.MALFORMED,
            ),
            (
                "overflow",
                b"HTTP/1.1 200 OK\r\n",
                (b"Content-Type: application/json\r\n",),
                (b"A" * 65_537,),
                IdentityAttemptFailureKind.OVERSIZED,
            ),
            (
                "read_failure",
                b"HTTP/1.1 200 OK\r\n",
                (b"Content-Type: application/json\r\n",),
                (SystemExit("private-read"),),
                IdentityAttemptFailureKind.UNAVAILABLE,
            ),
            (
                "cancellation",
                b"HTTP/1.1 200 OK\r\n",
                (b"Content-Type: application/json\r\n",),
                (KeyboardInterrupt("private-read-cancel"),),
                IdentityAttemptFailureKind.CANCELLED,
            ),
            (
                "success",
                b"HTTP/1.1 200 OK\r\n",
                (
                    b"Content-Type: application/json\r\n",
                    f"Content-Length: {len(payload)}\r\n".encode(),
                ),
                (payload, None),
                None,
            ),
        )
        for name, status, headers, chunks, expected in paths:
            for released in (True, False):
                with self.subTest(path=name, fallback=released):
                    reader = Reader(chunks)
                    response = RawIdentityResponseHead(
                        status, headers, reader, "1.1.1.1"
                    )
                    channel = Channel(response, released)
                    result = HardcoverIdentityProvider(
                        lambda host, port: ("1.1.1.1",),
                        channel,
                        b"synthetic-noncredential",
                    ).lookup(self.query(), self.context())
                    if not released:
                        self.assertEqual(
                            result.failure.kind,
                            IdentityAttemptFailureKind.UNAVAILABLE,
                        )
                    elif expected is None:
                        self.assertIsNone(result.failure)
                    else:
                        self.assertEqual(result.failure.kind, expected)
                    self.assertEqual(reader.close_calls, 1)
                    self.assertEqual(channel.release_calls, 1)
                    self.assertNotIn("private-", repr(result))

    def test_release_fallback_covers_every_close_and_release_capability_shape(self) -> None:
        class MissingClose:
            pass

        class NoncallableClose:
            close = False

        def hostile_close(error: BaseException) -> object:
            class HostileClose:
                @property
                def close(self) -> object:
                    raise error

            return HostileClose()

        class FallbackChannel:
            def __init__(self, reader: object, fallback: object) -> None:
                self.response = RawIdentityResponseHead(
                    b"malformed\r\n", (), reader, "1.1.1.1"
                )
                self.fallback = fallback
                self.release_calls = 0

            def exchange(self, request: object, context: object) -> object:
                return self.response

            @property
            def release(self) -> object:
                if isinstance(self.fallback, BaseException):
                    raise self.fallback

                def invoke(response: object) -> object:
                    self.release_calls += 1
                    return self.fallback

                return invoke

        readers = (
            MissingClose(),
            NoncallableClose(),
            hostile_close(RuntimeError("private-close-access")),
            hostile_close(SystemExit("private-close-base")),
            hostile_close(GeneratorExit("private-close-generator")),
            hostile_close(KeyboardInterrupt("private-close-cancel")),
        )
        for reader in readers:
            with self.subTest(reader=type(reader).__name__):
                channel = FallbackChannel(reader, True)
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    channel,
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(result.failure.kind, IdentityAttemptFailureKind.MALFORMED)
                self.assertEqual(channel.release_calls, 1)
                self.assertNotIn("private-", repr(result))

        for fallback in (
            None,
            False,
            RuntimeError("private-release-access"),
            SystemExit("private-release-base"),
            GeneratorExit("private-release-generator"),
            KeyboardInterrupt("private-release-cancel"),
        ):
            with self.subTest(fallback=type(fallback).__name__):
                channel = FallbackChannel(MissingClose(), fallback)
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    channel,
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(
                    result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
                )
                self.assertNotIn("private-", repr(result))

        class DeclaredClose:
            closed = True

            def __init__(self, value: object) -> None:
                self.value = value
                self.calls = 0

            def close(self) -> object:
                self.calls += 1
                return self.value

        for close_value in (None, False, 0, 1, "true", object()):
            for fallback, expected in (
                (True, IdentityAttemptFailureKind.MALFORMED),
                (False, IdentityAttemptFailureKind.UNAVAILABLE),
            ):
                with self.subTest(
                    close_value=type(close_value).__name__, fallback=fallback
                ):
                    reader = DeclaredClose(close_value)
                    channel = FallbackChannel(reader, fallback)
                    result = HardcoverIdentityProvider(
                        lambda host, port: ("1.1.1.1",),
                        channel,
                        b"synthetic-noncredential",
                    ).lookup(self.query(), self.context())
                    self.assertEqual(result.failure.kind, expected)
                    self.assertEqual(reader.calls, 1)
                    self.assertEqual(channel.release_calls, 1)

        reader = DeclaredClose(True)
        channel = FallbackChannel(reader, False)
        result = HardcoverIdentityProvider(
            lambda host, port: ("1.1.1.1",),
            channel,
            b"synthetic-noncredential",
        ).lookup(self.query(), self.context())
        self.assertEqual(result.failure.kind, IdentityAttemptFailureKind.MALFORMED)
        self.assertEqual(reader.calls, 1)
        self.assertEqual(channel.release_calls, 0)

        class Reader:
            def __init__(self, chunks: tuple[bytes | None, ...], close_value: object) -> None:
                self.chunks = iter(chunks)
                self.close_value = close_value
                self.close_calls = 0

            def read_chunk(self, max_bytes: int) -> bytes | None:
                return next(self.chunks)

            def close(self) -> object:
                self.close_calls += 1
                if isinstance(self.close_value, BaseException):
                    raise self.close_value
                return self.close_value

        class Channel:
            def __init__(self, response: object, fallback: object) -> None:
                self.response = response
                self.fallback = fallback
                self.release_calls = 0

            def exchange(self, request: object, context: object) -> object:
                return self.response

            def release(self, response: object) -> object:
                self.release_calls += 1
                if isinstance(self.fallback, BaseException):
                    raise self.fallback
                return self.fallback

        payload = self.response_body()
        normal_reader = Reader((payload, None), True)
        normal_response = RawIdentityResponseHead(
            b"HTTP/1.1 200 OK\r\n",
            (
                b"Content-Type: application/json\r\n",
                f"Content-Length: {len(payload)}\r\n".encode(),
            ),
            normal_reader,
            "1.1.1.1",
        )
        normal_channel = Channel(normal_response, False)
        normal_result = HardcoverIdentityProvider(
            lambda host, port: ("1.1.1.1",),
            normal_channel,
            b"synthetic-noncredential",
        ).lookup(self.query(), self.context())
        self.assertIsNone(normal_result.failure)
        self.assertEqual(normal_reader.close_calls, 1)
        self.assertEqual(normal_channel.release_calls, 0)

        for close_value in (
            SystemExit("private-close"),
            GeneratorExit("private-close-base"),
            KeyboardInterrupt("private-close-cancel"),
        ):
            for fallback in (
                True,
                False,
                SystemExit("private-release"),
                KeyboardInterrupt("private-release-cancel"),
            ):
                with self.subTest(
                    close=type(close_value).__name__, fallback=type(fallback).__name__
                ):
                    reader = Reader((payload, None), close_value)
                    response = RawIdentityResponseHead(
                        b"HTTP/1.1 200 OK\r\n",
                        (b"Content-Type: application/json\r\n",),
                        reader,
                        "1.1.1.1",
                    )
                    channel = Channel(response, fallback)
                    result = HardcoverIdentityProvider(
                        lambda host, port: ("1.1.1.1",),
                        channel,
                        b"synthetic-noncredential",
                    ).lookup(self.query(), self.context())
                    if fallback is True:
                        self.assertIsNone(result.failure)
                    else:
                        self.assertEqual(
                            result.failure.kind, IdentityAttemptFailureKind.UNAVAILABLE
                        )
                    self.assertNotIn("private-", repr(result))

    def test_channel_failure_metadata_is_exact_sealed_and_value_free(self) -> None:
        class RaisingChannel:
            def __init__(self, error: BaseException) -> None:
                self.error = error

            def exchange(self, request: object, context: object) -> object:
                raise self.error

        exact = IdentityChannelFailure(IdentityAttemptFailureKind.CANCELLED)
        cases: list[tuple[BaseException, IdentityAttemptFailureKind]] = [
            (exact, IdentityAttemptFailureKind.CANCELLED),
        ]
        for value in (None, True, "cancelled", object()):
            mutated = IdentityChannelFailure(IdentityAttemptFailureKind.CANCELLED)
            mutated.kind = value
            cases.append((mutated, IdentityAttemptFailureKind.UNAVAILABLE))
        missing = IdentityChannelFailure(IdentityAttemptFailureKind.TIMEOUT)
        del missing.kind
        cases.append((missing, IdentityAttemptFailureKind.UNAVAILABLE))

        class HostileSubclass(IdentityChannelFailure):
            @property
            def kind(self) -> object:
                raise KeyboardInterrupt("private-kind-access")

            @kind.setter
            def kind(self, value: object) -> None:
                self.__dict__["shadow_kind"] = value

        subclass = HostileSubclass(IdentityAttemptFailureKind.CANCELLED)
        subclass.args = ("private-args",)
        subclass.add_note("private-note")
        subclass.__dict__["private"] = "private-dict"
        cases.append((subclass, IdentityAttemptFailureKind.UNAVAILABLE))

        mutated_args = IdentityChannelFailure(IdentityAttemptFailureKind.TIMEOUT)
        mutated_args.args = ("private-args",)
        mutated_args.add_note("private-note")
        mutated_args.__dict__["private"] = "private-dict"
        cases.append((mutated_args, IdentityAttemptFailureKind.TIMEOUT))

        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                result = HardcoverIdentityProvider(
                    lambda host, port: ("1.1.1.1",),
                    RaisingChannel(error),
                    b"synthetic-noncredential",
                ).lookup(self.query(), self.context())
                self.assertEqual(result.failure.kind, expected)
                self.assertNotIn("private-", repr(result))

    def test_translated_private_exceptions_clear_context_and_traceback_locals(self) -> None:
        forbidden_text = "private-title-token-path"

        class PrivateExit(SystemExit):
            pass

        private_error = PrivateExit(forbidden_text)
        private_error.private = forbidden_text
        private_error.add_note(forbidden_text)

        def assert_clean(
            error: BaseException,
            expected_args: tuple[object, ...],
            caught_private: BaseException = private_error,
        ) -> None:
            self.assertEqual(error.args, expected_args)
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertFalse(getattr(error, "__notes__", ()))
            traceback = error.__traceback__
            while traceback is not None:
                frame = traceback.tb_frame
                if "/src/mechanical_librarian/" in frame.f_code.co_filename:
                    for value in frame.f_locals.values():
                        self.assertIsNot(value, caught_private)
                        self.assertNotIn(forbidden_text, repr(value))
                traceback = traceback.tb_next

        private_query = validate_private_query(
            {"isbn": "9780000000019", "title": forbidden_text}
        )

        class FailedProvider:
            def lookup(self, query: object, context: object) -> object:
                raise private_error

        with self.assertRaises(IdentityReportError) as provider_failure:
            RunHardcoverIdentityReport().run(
                FailedProvider(), private_query, self.context()
            )
        assert_clean(
            provider_failure.exception,
            ("identity provider attempt is unavailable",),
        )

        class FailedPreview:
            def run(self, source: object) -> object:
                raise private_error

        with self.assertRaises(IdentityReportError) as preview_failure:
            RunHardcoverFixedAdapterPreview().run(
                FailedPreview(), {"private": forbidden_text}
            )
        assert_clean(
            preview_failure.exception,
            ("Hardcover structural preview is unavailable",),
        )

        private_generator = GeneratorExit(forbidden_text)
        private_generator.private = forbidden_text
        private_generator.add_note(forbidden_text)

        class FailedGeneratorPreview:
            def run(self, source: object) -> object:
                raise private_generator

        with self.assertRaises(IdentityReportError) as generator_failure:
            RunHardcoverFixedAdapterPreview().run(
                FailedGeneratorPreview(), {"private": forbidden_text}
            )
        assert_clean(
            generator_failure.exception,
            ("Hardcover structural preview is unavailable",),
            private_generator,
        )

        def failed_observer(channel: str, values: tuple[object, ...]) -> None:
            raise private_error

        with self.assertRaises(IdentityReportError) as observer_failure:
            RunHardcoverIdentityReport().run(
                self.provider(self.channel()),
                private_query,
                self.context(),
                privacy_observer=failed_observer,
            )
        assert_clean(
            observer_failure.exception,
            ("Hardcover privacy observer is unavailable",),
        )

        source = {
            "schema": "hardcover-synthetic-input-v3",
            "scenario": "fixed-adapter-v3",
            "request": {},
            "response": {"private": forbidden_text},
            "transport": {},
        }
        with self.assertRaises(ValueError) as structural_failure:
            StructuralHardcoverIdentityPreview().run(source)
        assert_clean(structural_failure.exception, ("synthetic input is invalid",))

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
