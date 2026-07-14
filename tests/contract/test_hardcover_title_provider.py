from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.providers import hardcover_title
from mechanical_librarian.adapters.providers.hardcover_title import (
    HARDCOVER_QUERY,
    HardcoverTitleProvider,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    PageRequest,
    ProviderFact,
    ProviderFactCode,
    ProviderQuery,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.title_proposals import PROVIDER_SCHEMA
from mechanical_librarian.domain.inventory import (
    FieldName,
    MediaType,
    SecretReference,
)
from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.provider_secrets import (
    PrivateFileProviderCredentials,
)


PUBLIC_IP = "93.184.216.34"
TITLE = "A Synthetic Book"


class _Peer:
    def __init__(self, address: str = PUBLIC_IP) -> None:
        self.address = address

    def get_extra_info(self, name: str):
        return (self.address, 443) if name == "server_addr" else None


def _response(
    payload: object,
    *,
    status: int = 200,
    peer: str = PUBLIC_IP,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = (
        payload
        if type(payload) is bytes
        else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    selected_headers = {"content-type": "application/json"}
    if headers:
        selected_headers.update(headers)
    return httpx.Response(
        status,
        headers=selected_headers,
        content=body,
        extensions={"network_stream": _Peer(peer)},
    )


def _success(results: list[object]) -> dict[str, object]:
    return {
        "data": {
            "search": {
                "error": None,
                "page": 1,
                "per_page": 1,
                "query": TITLE,
                "query_type": "Book",
                "results": results,
            }
        }
    }


def _context(
    *,
    cancelled: bool = False,
    deadline_seconds: float = 5,
    cancellation_reader=None,
) -> OperationContext:
    now = datetime.now(timezone.utc)
    return OperationContext(
        Deadline(now + timedelta(seconds=deadline_seconds)),
        CancellationProbe(
            CancellationId("hardcover-contract"),
            (lambda: cancelled) if cancellation_reader is None else cancellation_reader,
        ),
        ResourceBounds(16, 65_536),
        TimeProbe(lambda: now),
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        MediaType.EBOOK,
        FieldName.TITLE,
        (ProviderFact(ProviderFactCode.TITLE, (TITLE,)),),
        PROVIDER_SCHEMA,
    )


class HardcoverTitleProviderContractTests(unittest.TestCase):
    def _provider(
        self,
        handler,
        *,
        resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
    ):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        secret = Path(directory.name) / "provider-token"
        secret.write_text("provider-token-sentinel\n", encoding="utf-8")
        secret.chmod(0o600)
        client = PrivateFileProviderCredentials().hardcover_client(
            SecretHandle(SecretReference(str(secret)), SecretScope.METADATA_PROVIDER),
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        return HardcoverTitleProvider(client, resolver=resolver)

    def test_exact_pinned_request_and_closed_candidate(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _response(_success([{"title": TITLE}]))

        result = self._provider(handler).lookup(_query(), PageRequest(None, 1), _context())
        self.assertIsNone(result.failure)
        self.assertEqual(len(result.value.items), 1)  # type: ignore[union-attr]
        candidate = result.value.items[0]  # type: ignore[union-attr]
        self.assertEqual(candidate.value.values, (TITLE,))
        self.assertEqual(candidate.provenance.source_id, "hardcover-title-poc")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), f"https://{PUBLIC_IP}/v1/graphql")
        self.assertEqual(request.headers["host"], "api.hardcover.app")
        self.assertEqual(request.headers["authorization"], "provider-token-sentinel")
        self.assertEqual(request.headers["accept-encoding"], "identity")
        self.assertEqual(request.headers["connection"], "close")
        self.assertEqual(request.extensions["sni_hostname"], "api.hardcover.app")
        body = json.loads(request.content)
        self.assertEqual(body["operationName"], "HardcoverTitlePoc")
        self.assertEqual(body["query"], HARDCOVER_QUERY)
        self.assertEqual(body["variables"], {"title": TITLE})

    def test_http_outcomes_are_typed_and_never_redirect(self) -> None:
        expected = {
            301: FailureKind.INVALID_RESPONSE,
            401: FailureKind.AUTHENTICATION,
            403: FailureKind.AUTHORIZATION,
            404: FailureKind.NOT_FOUND,
            408: FailureKind.TIMEOUT,
            429: FailureKind.RATE_LIMITED,
            500: FailureKind.UNAVAILABLE,
        }
        for status, kind in expected.items():
            with self.subTest(status=status):
                calls = []

                def handler(request, status=status):
                    calls.append(request)
                    return _response({}, status=status)

                result = self._provider(handler).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                self.assertEqual(result.failure.kind, kind)  # type: ignore[union-attr]
                self.assertEqual(len(calls), 1)

    def test_empty_and_graphql_or_shape_failures_are_distinct(self) -> None:
        cases = (
            (_success([]), None),
            ({"errors": [{"message": "private upstream detail"}]}, FailureKind.INVALID_RESPONSE),
            ({"data": {"search": {"results": []}}}, FailureKind.INVALID_RESPONSE),
            (_success([{"title": TITLE, "id": 123}]), FailureKind.INVALID_RESPONSE),
            (_success([{"title": "x" * 513}]), FailureKind.INVALID_RESPONSE),
            (_success([{"title": TITLE}, {"title": TITLE}]), FailureKind.INCOMPATIBLE),
        )
        for payload, kind in cases:
            with self.subTest(kind=kind):
                result = self._provider(lambda _request, payload=payload: _response(payload)).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                if kind is None:
                    self.assertEqual(result.value.items, ())  # type: ignore[union-attr]
                else:
                    self.assertEqual(result.failure.kind, kind)  # type: ignore[union-attr]

    def test_duplicate_deep_oversized_and_encoded_responses_fail_closed(self) -> None:
        deep: object = None
        for _ in range(10):
            deep = {"nested": deep}
        cases = (
            b'{"data":{"search":{}},"data":{"search":{}}}',
            json.dumps(deep).encode(),
            b"{" + b" " * 65_536 + b"}",
        )
        for payload in cases:
            with self.subTest(size=len(payload)):
                result = self._provider(lambda _request, payload=payload: _response(payload)).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                self.assertIn(
                    result.failure.kind,  # type: ignore[union-attr]
                    {FailureKind.INVALID_RESPONSE, FailureKind.RESOURCE_EXHAUSTED},
                )
        encoded = self._provider(
            lambda _request: _response(
                _success([]), headers={"content-encoding": "gzip"}
            )
        ).lookup(_query(), PageRequest(None, 1), _context())
        self.assertEqual(encoded.failure.kind, FailureKind.INVALID_RESPONSE)  # type: ignore[union-attr]

    def test_destination_set_and_connected_peer_are_both_enforced(self) -> None:
        calls = []

        def handler(request):
            calls.append(request)
            return _response(_success([]))

        for addresses in (
            (),
            ("127.0.0.1",),
            ("169.254.1.1",),
            ("224.0.0.1",),
            ("0.0.0.0",),
            (PUBLIC_IP, "10.0.0.1"),
            ("::ffff:93.184.216.34",),
            ("fe80::1%en0",),
            ("not-an-address",),
        ):
            with self.subTest(addresses=addresses):
                result = self._provider(
                    handler,
                    resolver=lambda _host, _port, _budget, values=addresses: values,
                ).lookup(_query(), PageRequest(None, 1), _context())
                self.assertIsNotNone(result.failure)
        self.assertEqual(calls, [])

        mismatch = self._provider(
            lambda _request: _response(_success([]), peer="93.184.216.35")
        ).lookup(_query(), PageRequest(None, 1), _context())
        self.assertEqual(mismatch.failure.kind, FailureKind.INTEGRITY_FAILURE)  # type: ignore[union-attr]

    def test_cancelled_and_invalid_application_requests_send_nothing(self) -> None:
        calls = []
        provider = self._provider(lambda request: calls.append(request) or _response(_success([])))
        cancelled = provider.lookup(_query(), PageRequest(None, 1), _context(cancelled=True))
        self.assertEqual(cancelled.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        invalid = provider.lookup(_query(), PageRequest(None, 2), _context())
        self.assertEqual(invalid.failure.kind, FailureKind.INVALID_REQUEST)  # type: ignore[union-attr]
        self.assertEqual(calls, [])

    def test_one_budget_bounds_resolution_and_clips_transport_timeouts(self) -> None:
        calls = []
        workers_before = {
            worker.ident for worker in threading.enumerate() if worker.is_alive()
        }

        def slow_resolver(_host, _port, budget):
            while budget.failure() is None:
                time.sleep(0.005)
            return (PUBLIC_IP,)

        started = time.monotonic()
        timed_out = self._provider(
            lambda request: calls.append(request) or _response(_success([])),
            resolver=slow_resolver,
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.05),
        )
        self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(calls, [])
        self.assertEqual(
            {worker.ident for worker in threading.enumerate() if worker.is_alive()},
            workers_before,
        )

        def delayed_resolver(_host, _port, _budget):
            time.sleep(0.05)
            return (PUBLIC_IP,)

        clipped = self._provider(
            lambda request: calls.append(request) or _response(_success([])),
            resolver=delayed_resolver,
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.5),
        )
        self.assertIsNone(clipped.failure)
        timeouts = calls[0].extensions["timeout"]
        self.assertTrue(all(0 < value < 0.5 for value in timeouts.values()))

    def test_deadline_and_cancellation_interrupt_stream_consumption(self) -> None:
        body = json.dumps(_success([]), separators=(",", ":")).encode("utf-8")

        class SlowStream(httpx.AsyncByteStream):
            def __init__(self, *, cancel_after_first=None) -> None:
                self.consumed = 0
                self.cancel_after_first = cancel_after_first

            async def __aiter__(self):
                for byte in body:
                    self.consumed += 1
                    if self.consumed == 1 and self.cancel_after_first is not None:
                        self.cancel_after_first.set()
                    await asyncio.sleep(0.02)
                    yield bytes((byte,))

        def response_for(stream):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=stream,
                extensions={"network_stream": _Peer()},
            )

        deadline_stream = SlowStream()
        started = time.monotonic()
        deadline = self._provider(
            lambda _request: response_for(deadline_stream)
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.08),
        )
        self.assertEqual(deadline.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertLess(deadline_stream.consumed, len(body))
        consumed_at_return = deadline_stream.consumed
        time.sleep(0.08)
        self.assertEqual(deadline_stream.consumed, consumed_at_return)

        cancelled = threading.Event()
        cancelled_stream = SlowStream(cancel_after_first=cancelled)
        result = self._provider(
            lambda _request: response_for(cancelled_stream)
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        self.assertLess(cancelled_stream.consumed, len(body))
        consumed_at_return = cancelled_stream.consumed
        time.sleep(0.08)
        self.assertEqual(cancelled_stream.consumed, consumed_at_return)

    def test_deadline_and_cancellation_interrupt_response_header_wait(self) -> None:
        completed = threading.Event()

        async def delayed_headers(_request):
            try:
                await asyncio.sleep(1)
                return _response(_success([]))
            finally:
                completed.set()

        started = time.monotonic()
        timed_out = self._provider(delayed_headers).lookup(
            _query(), PageRequest(None, 1), _context(deadline_seconds=0.05)
        )
        self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(completed.is_set())

        completed.clear()
        cancelled = threading.Event()
        timer = threading.Timer(0.05, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = self._provider(delayed_headers).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(completed.is_set())

    def test_deadline_and_cancellation_take_precedence_over_parse_failure(self) -> None:
        original_parse = hardcover_title._parse_response

        def delayed_invalid_parse(payload, requested_title):
            time.sleep(0.08)
            return original_parse(payload, requested_title)

        with patch.object(
            hardcover_title, "_parse_response", delayed_invalid_parse
        ):
            started = time.monotonic()
            timed_out = self._provider(lambda _request: _response(b"{")).lookup(
                _query(), PageRequest(None, 1), _context(deadline_seconds=0.03)
            )
            self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
            self.assertLess(time.monotonic() - started, 0.2)

            cancelled = threading.Event()
            timer = threading.Timer(0.03, cancelled.set)
            timer.start()
            try:
                result = self._provider(lambda _request: _response(b"{")).lookup(
                    _query(),
                    PageRequest(None, 1),
                    _context(
                        deadline_seconds=1,
                        cancellation_reader=cancelled.is_set,
                    ),
                )
            finally:
                timer.cancel()
            self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

    def test_cancellation_takes_precedence_over_http_failures(self) -> None:
        cancelled = threading.Event()

        def authentication_failure(_request):
            cancelled.set()
            return _response({}, status=401)

        authentication = self._provider(authentication_failure).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(authentication.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

        cancelled.clear()

        def connection_failure(request):
            cancelled.set()
            raise httpx.ConnectError("private connection detail", request=request)

        unavailable = self._provider(connection_failure).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(unavailable.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
