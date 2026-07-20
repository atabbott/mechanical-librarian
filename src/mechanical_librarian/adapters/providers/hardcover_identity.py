"""Fixed, bounded Hardcover ISBN identity adapter.

The module is deliberately deep: callers supply one ISBN query, one operation
context, one resolver, one byte channel, and one already-loaded token. Request
construction, address policy, authorization, status handling, and hostile
response parsing stay behind the application-owned provider port.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Coroutine, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, TypeVar

import httpx

from mechanical_librarian.application.hardcover_identity import (
    AddressPolicyOutcome,
    FixedAdapterAttemptEvidence,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IdentityExchangeGuardError,
    IsbnIdentityQuery,
    PreparedIdentityExchange,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.domain.hardcover_identity import (
    ProviderBookObservation,
    ProviderIdentityObservation,
)


HARDCOVER_IDENTITY_HOST = "api.hardcover.app"
HARDCOVER_IDENTITY_PATH = "/v1/graphql"
HARDCOVER_IDENTITY_URL = (
    f"https://{HARDCOVER_IDENTITY_HOST}{HARDCOVER_IDENTITY_PATH}"
)
HARDCOVER_IDENTITY_OPERATION = "HardcoverEditionByIsbn"
HARDCOVER_IDENTITY_QUERY = (
    "query HardcoverEditionByIsbn($isbns:[String!]!){editions(where:{_or:["
    "{isbn_13:{_in:$isbns}},{isbn_10:{_in:$isbns}}]},order_by:{id:asc},"
    "limit:4){id book_id canonical_id isbn_10 isbn_10_valid isbn_13 "
    "isbn_13_valid isbns_match reading_format_id state compilation book{id "
    "canonical_id book_status_id state title parent_book_id compilation "
    "is_partial_book}}}"
)
OPERATION_CONTRACT_SHA256 = (
    "218aedb44bab603697bdb51dfab9ede9f6530a6031af6d992f9f8b10b99bf2ce"
)
MAX_REQUEST_BYTES = 8_192
MAX_RESPONSE_BYTES = 65_536
MAX_RESOLVER_ADDRESSES = 4
MAX_IP_TEXT_SCALARS = 39
MAX_IP_TEXT_BYTES = 39
MAX_STATUS_LINE_BYTES = 128
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 128
MAX_JSON_MEMBERS = 96
MAX_JSON_LIST_ELEMENTS = 4
MAX_STRING_SCALARS = 512
MAX_STRING_BYTES = 2_048
MAX_TOTAL_STRING_SCALARS = 4_096
MAX_TOTAL_STRING_BYTES = 16_384
MIN_INT32 = -2_147_483_648
MAX_INT32 = 2_147_483_647
_TRANSPORT_LOG_LOCK = threading.RLock()

_EDITION_KEYS = {
    "id",
    "book_id",
    "canonical_id",
    "isbn_10",
    "isbn_10_valid",
    "isbn_13",
    "isbn_13_valid",
    "isbns_match",
    "reading_format_id",
    "state",
    "compilation",
    "book",
}
_BOOK_KEYS = {
    "id",
    "canonical_id",
    "book_status_id",
    "state",
    "title",
    "parent_book_id",
    "compilation",
    "is_partial_book",
}
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_CONTENT_TYPE = re.compile(
    rb"application/json(?:[ \t]*;[ \t]*charset[ \t]*=[ \t]*utf-8)?",
    re.IGNORECASE,
)
_STATUS_LINE = re.compile(rb"HTTP/1\.1 ([0-9]{3}) [\x20-\x7e]*\r\n")
_RETRY_AFTER = re.compile(rb"(?:0|[1-9][0-9]?)")
_BIDI_CONTROLS = frozenset(
    {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
)
_ASYNC_RESULT = TypeVar("_ASYNC_RESULT")
_RESOLVER_SCRIPT = """\
import json
import socket
import sys

answers = socket.getaddrinfo(
    sys.argv[1],
    int(sys.argv[2]),
    family=socket.AF_UNSPEC,
    type=socket.SOCK_STREAM,
    proto=socket.IPPROTO_TCP,
)
sys.stdout.write(json.dumps([answer[4][0] for answer in answers]))
"""


class IdentityAddressResolver(Protocol):
    def __call__(self, host: str, port: int) -> tuple[str, ...]: ...


class IdentityByteChannel(Protocol):
    def exchange(
        self, request: AuthorizedIdentityRequest, context: OperationContext
    ) -> RawIdentityResponseHead: ...

    def release(self, response: object) -> bool: ...


class IdentityBodyReader(Protocol):
    def read_chunk(self, max_bytes: int) -> bytes | None: ...

    def close(self) -> bool: ...


@dataclass(frozen=True, repr=False, slots=True)
class FrozenIdentityRequest:
    method: str
    scheme: str
    host: str
    port: int
    path: str
    query_string: str
    fragment: str
    host_header: str
    tls_sni: str
    selected_address: str
    operation_name: str
    query_bytes: bytes
    body: bytes
    operation_contract_sha256: str

    def __repr__(self) -> str:
        return "FrozenIdentityRequest(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class AuthorizedIdentityRequest:
    request: FrozenIdentityRequest
    headers: tuple[tuple[bytes, bytes], ...]

    def __repr__(self) -> str:
        return "AuthorizedIdentityRequest(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class RawIdentityResponseHead:
    status_line: bytes
    header_lines: tuple[bytes, ...]
    body_reader: IdentityBodyReader
    connected_peer_address: str

    def __repr__(self) -> str:
        return "RawIdentityResponseHead(<redacted>)"


class StructuralIdentityBodyReader:
    """Single-use bounded-reader seam for an already-authorized response body."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self._read_disabled = False
        self._finalization_attempted = False
        self._release_confirmed = False
        self._read_count = 0

    @property
    def closed(self) -> bool:
        return self._release_confirmed

    @property
    def read_disabled(self) -> bool:
        return self._read_disabled

    @property
    def finalization_attempted(self) -> bool:
        return self._finalization_attempted

    @property
    def read_count(self) -> int:
        return self._read_count

    def read_chunk(self, max_bytes: int) -> bytes | None:
        if self._read_disabled:
            return None
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RESPONSE_BYTES + 1:
            raise ValueError("Hardcover identity body request is invalid")
        try:
            chunk = next(self._chunks)
        except StopIteration:
            self._read_disabled = True
            self.close()
            return None
        self._read_count += 1
        if chunk == b"":
            self.close()
            return None
        return chunk

    def close(self) -> bool:
        if self._release_confirmed:
            return True
        self._read_disabled = True
        self._finalization_attempted = True
        confirmed = _close_owned_iterator(self._chunks)
        self._release_confirmed = confirmed is True
        return self._release_confirmed

    def abort(self) -> bool:
        """Transport-owned fallback for the structural in-memory producer."""

        return self.close()


class IdentityChannelFailure(RuntimeError):
    """Value-free typed transport failure."""

    def __init__(self, kind: IdentityAttemptFailureKind) -> None:
        if kind not in {
            IdentityAttemptFailureKind.CANCELLED,
            IdentityAttemptFailureKind.TIMEOUT,
            IdentityAttemptFailureKind.UNAVAILABLE,
        }:
            raise ValueError("identity channel failure is malformed")
        super().__init__("Hardcover identity channel failed")
        self.kind = kind


class BoundedIdentityAddressResolver:
    """Resolve once in an isolated process under the attempt context budget."""

    def __init__(self, context: OperationContext) -> None:
        if type(context) is not OperationContext:
            raise TypeError("identity resolver context is malformed")
        self._context = context
        self._wall_deadline = time.monotonic() + _logical_remaining(context)

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        if host != HARDCOVER_IDENTITY_HOST or port != 443:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.INCOMPATIBLE)
        failure = _bounded_context_failure(self._context, self._wall_deadline)
        if failure is not None:
            raise IdentityChannelFailure(failure)
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _RESOLVER_SCRIPT, host, str(port)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            cwd="/",
            env={},
        )
        try:
            while process.poll() is None:
                failure = _bounded_context_failure(self._context, self._wall_deadline)
                if failure is not None:
                    _terminate_resolver(process)
                    raise IdentityChannelFailure(failure)
                time.sleep(min(0.01, _remaining(self._context, self._wall_deadline)))
            output, _ = process.communicate(timeout=0.1)
        finally:
            if process.poll() is None:
                _terminate_resolver(process)
        if process.returncode != 0 or not 1 <= len(output) <= 8_192:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)
        try:
            values = json.loads(output.decode("ascii", "strict"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise IdentityChannelFailure(
                IdentityAttemptFailureKind.UNAVAILABLE
            ) from None
        if type(values) is not list or any(type(value) is not str for value in values):
            raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)
        return tuple(values)


@contextmanager
def _transport_logging_boundary():
    """Prevent the HTTP dependency from exporting transport-derived values."""

    with _TRANSPORT_LOG_LOCK:
        names = {"httpx", "httpcore"}
        names.update(
            name
            for name in logging.root.manager.loggerDict
            if name == "httpx"
            or name.startswith("httpx.")
            or name == "httpcore"
            or name.startswith("httpcore.")
        )
        states: list[
            tuple[logging.Logger, int, bool, bool, list[logging.Handler]]
        ] = []
        sink = logging.NullHandler()
        try:
            for name in sorted(names):
                logger = logging.getLogger(name)
                states.append(
                    (
                        logger,
                        logger.level,
                        logger.propagate,
                        logger.disabled,
                        list(logger.handlers),
                    )
                )
                logger.setLevel(logging.CRITICAL + 1)
                logger.propagate = False
                logger.disabled = False
                logger.handlers[:] = [sink]
            yield
        finally:
            for logger, level, propagate, disabled, handlers in reversed(states):
                logger.handlers[:] = handlers
                logger.disabled = disabled
                logger.propagate = propagate
                logger.setLevel(level)


class HttpxIdentityByteChannel:
    """Perform one selected-IP request and return a closed bounded raw response."""

    def __init__(
        self, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if transport is not None and not isinstance(
            transport, httpx.AsyncBaseTransport
        ):
            raise TypeError("identity transport is malformed")
        self._transport = transport
        self._called = False

    def exchange(
        self, request: AuthorizedIdentityRequest, context: OperationContext
    ) -> RawIdentityResponseHead:
        if self._called or type(request) is not AuthorizedIdentityRequest or type(
            context
        ) is not OperationContext:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)
        self._called = True
        wall_deadline = time.monotonic() + _logical_remaining(context)
        return _run_identity_async(
            self._exchange_bounded(request, context, wall_deadline)
        )

    def release(self, response: object) -> bool:
        if type(response) is not RawIdentityResponseHead:
            return False
        try:
            return response.body_reader.close() is True
        except Exception:
            return False

    async def _exchange_bounded(
        self,
        request: AuthorizedIdentityRequest,
        context: OperationContext,
        wall_deadline: float,
    ) -> RawIdentityResponseHead:
        task = asyncio.create_task(self._perform(request, context, wall_deadline))
        try:
            while not task.done():
                failure = _bounded_context_failure(context, wall_deadline)
                if failure is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise IdentityChannelFailure(failure)
                await asyncio.wait(
                    (task,), timeout=min(0.01, _remaining(context, wall_deadline))
                )
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _perform(
        self,
        request: AuthorizedIdentityRequest,
        context: OperationContext,
        wall_deadline: float,
    ) -> RawIdentityResponseHead:
        with _transport_logging_boundary():
            return await self._perform_without_transport_logs(
                request, context, wall_deadline
            )

    async def _perform_without_transport_logs(
        self,
        request: AuthorizedIdentityRequest,
        context: OperationContext,
        wall_deadline: float,
    ) -> RawIdentityResponseHead:
        frozen = request.request
        if (
            frozen.method != "POST"
            or frozen.scheme != "https"
            or frozen.host != HARDCOVER_IDENTITY_HOST
            or frozen.port != 443
            or frozen.path != HARDCOVER_IDENTITY_PATH
            or frozen.query_string
            or frozen.fragment
            or frozen.host_header != HARDCOVER_IDENTITY_HOST
            or frozen.tls_sni != HARDCOVER_IDENTITY_HOST
            or not _public_address(frozen.selected_address)
        ):
            raise IdentityChannelFailure(IdentityAttemptFailureKind.INCOMPATIBLE)
        remaining = _remaining(context, wall_deadline)
        if remaining <= 0:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.TIMEOUT)
        endpoint = _ip_endpoint(frozen.selected_address)
        timeout = httpx.Timeout(
            remaining,
            connect=remaining,
            read=remaining,
            write=remaining,
            pool=remaining,
        )
        try:
            async with httpx.AsyncClient(
                cookies=None,
                follow_redirects=False,
                http2=False,
                limits=httpx.Limits(
                    max_connections=1,
                    max_keepalive_connections=0,
                    keepalive_expiry=0.0,
                ),
                timeout=timeout,
                transport=self._transport,
                trust_env=False,
                verify=True,
            ) as client:
                outbound = client.build_request(
                    "POST",
                    endpoint,
                    headers=request.headers,
                    content=frozen.body,
                    extensions={
                        "sni_hostname": HARDCOVER_IDENTITY_HOST,
                        "timeout": {
                            "connect": remaining,
                            "read": remaining,
                            "write": remaining,
                            "pool": remaining,
                        },
                    },
                )
                response = await client.send(outbound, stream=True)
                try:
                    peer = _response_peer(response)
                    status_line = (
                        f"HTTP/1.1 {response.status_code} {response.reason_phrase}\r\n"
                    ).encode("ascii", "strict")
                    header_lines = tuple(
                        name + b": " + value + b"\r\n"
                        for name, value in response.headers.raw
                    )
                    payload = bytearray()
                    header_failure = (
                        _bounded_body_header_failure(header_lines)
                        if response.status_code == 200
                        else None
                    )
                    if response.status_code == 200 and header_failure is None:
                        async for chunk in response.aiter_raw():
                            failure = _bounded_context_failure(
                                context, wall_deadline
                            )
                            if failure is not None:
                                raise IdentityChannelFailure(failure)
                            needed = MAX_RESPONSE_BYTES + 1 - len(payload)
                            payload.extend(chunk[:needed])
                            if len(payload) > MAX_RESPONSE_BYTES:
                                break
                    return RawIdentityResponseHead(
                        status_line,
                        header_lines,
                        StructuralIdentityBodyReader((bytes(payload),)),
                        peer,
                    )
                finally:
                    await response.aclose()
        except IdentityChannelFailure:
            raise
        except httpx.TimeoutException:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.TIMEOUT) from None
        except (httpx.HTTPError, OSError, UnicodeError, ValueError):
            raise IdentityChannelFailure(
                IdentityAttemptFailureKind.UNAVAILABLE
            ) from None


class LiveHardcoverIdentityProvider:
    """Prepare credential and network capabilities before durable start."""

    def __init__(
        self,
        token_reader: Callable[[], bytes],
        *,
        channel_factory: Callable[[], IdentityByteChannel] = HttpxIdentityByteChannel,
    ) -> None:
        if not callable(token_reader) or not callable(channel_factory):
            raise TypeError("live identity provider construction is malformed")
        self._token_reader = token_reader
        self._channel_factory = channel_factory

    def prepare(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
    ) -> PreparedIdentityExchange | IdentityAttemptResult:
        preflight = _safe_preflight(context)
        if preflight is not None:
            return IdentityAttemptResult.failed(preflight)
        try:
            token = self._token_reader()
        except Exception:
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.CREDENTIAL_UNAVAILABLE
            )
        if not _valid_token(token):
            token = b""
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.CREDENTIAL_UNAVAILABLE
            )
        preflight = _safe_preflight(context)
        if preflight is not None:
            token = b""
            return IdentityAttemptResult.failed(preflight)
        try:
            return HardcoverIdentityProvider(
                BoundedIdentityAddressResolver(context),
                self._channel_factory(),
                token,
            ).prepare(query, context)
        except Exception:
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        finally:
            token = b""


class HardcoverIdentityProvider:
    """Implement one fixed read-only ISBN attempt through injected capabilities."""

    def __init__(
        self,
        resolver: IdentityAddressResolver,
        channel: IdentityByteChannel,
        token: bytes,
    ) -> None:
        exchange = getattr(channel, "exchange", None)
        if not callable(resolver) or not callable(exchange):
            resolver = None
            channel = None
            token = b""
            exchange = None
            raise ValueError("Hardcover identity provider construction is invalid")
        if not _valid_token(token):
            resolver = None
            channel = None
            token = b""
            exchange = None
            raise ValueError("Hardcover identity provider construction is invalid")
        self._resolver = resolver
        self._channel = channel
        self._token = token

    def prepare(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
    ) -> PreparedIdentityExchange | IdentityAttemptResult:
        if type(query) is not IsbnIdentityQuery or type(context) is not OperationContext:
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INCOMPATIBLE
            )
        preflight = _safe_preflight(context)
        if preflight is not None:
            return IdentityAttemptResult.failed(preflight)
        try:
            resolved = self._resolver(HARDCOVER_IDENTITY_HOST, 443)
        except IdentityChannelFailure as error:
            return IdentityAttemptResult.failed(error.kind)
        except Exception:
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        addresses = _validated_addresses(resolved)
        if not addresses:
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INCOMPATIBLE
            )
        preflight = _safe_preflight(context)
        if preflight is not None:
            return IdentityAttemptResult.failed(preflight)
        request = _build_request(query, addresses[0])
        preflight = _safe_preflight(context)
        if preflight is not None:
            return IdentityAttemptResult.failed(preflight)
        try:
            authorized = authorize_identity_request(request, query, self._token)
        except Exception:
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        if authorized is None:
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INCOMPATIBLE
            )
        return _PreparedHardcoverIdentityExchange(
            self._channel,
            authorized,
            addresses[0],
        )


class _PreparedHardcoverIdentityExchange:
    """Single-use prepared request whose first action is durable start."""

    def __init__(
        self,
        channel: IdentityByteChannel,
        authorized: AuthorizedIdentityRequest,
        selected_address: str,
    ) -> None:
        self._channel = channel
        self._authorized = authorized
        self._selected_address = selected_address
        self._used = False

    def exchange(
        self,
        context: OperationContext,
        *,
        begin_transport: Callable[[], OperationContext],
    ) -> IdentityAttemptResult:
        if (
            self._used
            or type(context) is not OperationContext
            or not callable(begin_transport)
        ):
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.INCOMPATIBLE)
        self._used = True
        try:
            context = begin_transport()
            if type(context) is not OperationContext:
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                )
            response = self._channel.exchange(self._authorized, context)
        except IdentityExchangeGuardError:
            raise
        except IdentityChannelFailure as error:
            return IdentityAttemptResult.failed(error.kind)
        except Exception:
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        body_reader = response.body_reader
        candidate: (
            tuple[ProviderIdentityObservation, ...]
            | IdentityAttemptFailureKind
            | IdentityAttemptResult
        )
        try:
            if not _peer_equal(response.connected_peer_address, self._selected_address):
                candidate = IdentityAttemptFailureKind.UNAVAILABLE
            else:
                preflight = _safe_preflight(context)
                if preflight is not None:
                    candidate = preflight
                else:
                    status = _parse_status(response.status_line)
                    if status is None:
                        candidate = IdentityAttemptFailureKind.MALFORMED
                    else:
                        mapped = _status_failure(status)
                        payload = (
                            _rate_limited_result(response.header_lines)
                            if mapped is IdentityAttemptFailureKind.RATE_LIMITED
                            else mapped
                            if mapped is not None
                            else _bounded_body(
                                response.header_lines,
                                body_reader,
                                _close_after=False,
                            )
                        )
                        candidate = (
                            payload
                            if isinstance(
                                payload,
                                (IdentityAttemptFailureKind, IdentityAttemptResult),
                            )
                            else _parse_observations(payload)
                        )
        finally:
            released = _release_response(self._channel, response, body_reader)
        if not released:
            return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        if type(candidate) is IdentityAttemptResult:
            return candidate
        if isinstance(candidate, IdentityAttemptFailureKind):
            return IdentityAttemptResult.failed(candidate)
        return IdentityAttemptResult.success(candidate)


class StructuralIdentityByteChannel:
    """Post-policy in-memory byte channel that can never open a network socket."""

    def __init__(
        self,
        *,
        expected_method: str,
        expected_url: str,
        expected_body: bytes,
        expected_token: bytes,
        selected_address: str,
        connected_peer_address: str,
        hostname: str,
        resolution_count: int,
        tls_sni: str,
        tls_peer_name: str,
        proxy_mode: str,
        address_policy_capability: str,
        actual_network: bool,
        byte_channel: str,
        response: RawIdentityResponseHead,
    ) -> None:
        if not _valid_token(expected_token):
            raise ValueError("structural identity channel construction is invalid")
        self._expected = (expected_method, expected_url, expected_body)
        self._expected_headers = (
            (b"Accept", b"application/json"),
            (b"Accept-Encoding", b"identity"),
            (b"Authorization", b"Bearer " + expected_token),
            (b"Connection", b"close"),
            (b"Content-Type", b"application/json"),
            (b"Host", HARDCOVER_IDENTITY_HOST.encode("ascii")),
        )
        self._transport = (
            selected_address,
            connected_peer_address,
            hostname,
            resolution_count,
            tls_sni,
            tls_peer_name,
            proxy_mode,
            address_policy_capability,
            actual_network,
            byte_channel,
        )
        self._response = response
        self._called = False
        self._released = False

    @property
    def called(self) -> bool:
        return self._called

    @property
    def peer_equal(self) -> bool:
        return _peer_equal(self._transport[0], self._transport[1])

    def exchange(
        self, request: AuthorizedIdentityRequest, context: OperationContext
    ) -> RawIdentityResponseHead:
        if self._called or type(context) is not OperationContext:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)
        frozen = request.request if type(request) is AuthorizedIdentityRequest else None
        transport_valid = self._transport == (
            frozen.selected_address if frozen is not None else None,
            self._response.connected_peer_address,
            HARDCOVER_IDENTITY_HOST,
            1,
            HARDCOVER_IDENTITY_HOST,
            HARDCOVER_IDENTITY_HOST,
            "disabled",
            "validated-public-address",
            False,
            "post-policy-in-memory",
        )
        request_valid = (
            frozen is not None
            and self._expected
            == (frozen.method, HARDCOVER_IDENTITY_URL, frozen.body)
            and request.headers == self._expected_headers
        )
        if not transport_valid or not request_valid:
            raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)
        preflight = _safe_preflight(context)
        if preflight is not None:
            raise IdentityChannelFailure(preflight)
        self._called = True
        return self._response

    def release(self, response: object) -> bool:
        if response is not self._response:
            return False
        reader = self._response.body_reader
        try:
            confirmed = reader.abort()
        except Exception:
            return False
        self._released = confirmed is True
        return self._released


class StructuralHardcoverIdentityPreview:
    """Decode and run the fixed contributor fixture behind the adapter seam."""

    def run(self, source: Mapping[str, object]) -> FixedAdapterAttemptEvidence:
        evidence = _structural_preview_attempt(self, source)
        if evidence is None:
            source = {}
            raise ValueError("synthetic input is invalid")
        return evidence

    def _run(self, source: Mapping[str, object]) -> FixedAdapterAttemptEvidence:
        if (
            not isinstance(source, Mapping)
            or set(source) != {"schema", "scenario", "request", "response", "transport"}
            or source.get("schema") != "hardcover-synthetic-input-v3"
            or source.get("scenario") != "fixed-adapter-v3"
            or not isinstance(source.get("request"), Mapping)
            or not isinstance(source.get("response"), Mapping)
            or not isinstance(source.get("transport"), Mapping)
        ):
            raise ValueError("synthetic input is invalid")
        request = source["request"]
        response = source["response"]
        transport = source["transport"]
        if (
            set(request) != {"method", "url", "body_bytes_utf8"}
            or request.get("method") != "POST"
            or request.get("url") != HARDCOVER_IDENTITY_URL
            or type(request.get("body_bytes_utf8")) is not str
            or set(response)
            != {
                "raw_status_line_bytes_utf8",
                "raw_header_lines_bytes_utf8",
                "raw_body_bytes_utf8",
            }
            or type(response.get("raw_status_line_bytes_utf8")) is not str
            or type(response.get("raw_header_lines_bytes_utf8")) is not list
            or any(
                type(line) is not str
                for line in response.get("raw_header_lines_bytes_utf8", [])
            )
            or type(response.get("raw_body_bytes_utf8")) is not str
            or set(transport)
            != {
                "attempt_started_wall",
                "attempt_cutoff_wall",
                "hostname",
                "selected_address",
                "connected_peer_address",
                "resolution_count",
                "sni",
                "tls_peer_name",
                "proxy_mode",
                "address_policy_capability",
                "actual_network",
                "byte_channel",
            }
            or transport.get("hostname") != HARDCOVER_IDENTITY_HOST
            or type(transport.get("selected_address")) is not str
            or not _bounded_ip_text(transport.get("selected_address"))
            or not _bounded_ip_text(transport.get("connected_peer_address"))
            or type(transport.get("resolution_count")) is not int
            or transport.get("resolution_count") != 1
            or transport.get("sni") != HARDCOVER_IDENTITY_HOST
            or transport.get("tls_peer_name") != HARDCOVER_IDENTITY_HOST
            or transport.get("proxy_mode") != "disabled"
            or transport.get("address_policy_capability")
            != "validated-public-address"
            or transport.get("actual_network") is not False
            or transport.get("byte_channel") != "post-policy-in-memory"
        ):
            raise ValueError("synthetic input is invalid")
        request_body = request["body_bytes_utf8"].encode("utf-8", "strict")
        status_line = response["raw_status_line_bytes_utf8"].encode(
            "utf-8", "strict"
        )
        header_lines = tuple(
            line.encode("utf-8", "strict")
            for line in response["raw_header_lines_bytes_utf8"]
        )
        response_body = response["raw_body_bytes_utf8"].encode(
            "utf-8", "strict"
        )
        started = _strict_preview_time(transport.get("attempt_started_wall"))
        cutoff = _strict_preview_time(transport.get("attempt_cutoff_wall"))
        if (
            len(request_body) > MAX_REQUEST_BYTES
            or len(status_line) > 128
            or len(header_lines) > 16
            or any(len(line) > 256 for line in header_lines)
            or len(response_body) > MAX_RESPONSE_BYTES
            or cutoff <= started
            or cutoff - started > timedelta(seconds=30)
        ):
            raise ValueError("synthetic input is invalid")

        query = identity_query_from_canonical_body(request_body)
        selected = transport["selected_address"]
        peer = transport["connected_peer_address"]
        validated_selected = _validated_addresses((selected,))
        channel_selected = (
            validated_selected[0] if validated_selected is not None else selected
        )
        body_reader = StructuralIdentityBodyReader(iter((response_body,)))
        raw_response = RawIdentityResponseHead(
            status_line,
            header_lines,
            body_reader,
            peer,
        )
        channel = StructuralIdentityByteChannel(
            expected_method=request["method"],
            expected_url=request["url"],
            expected_body=request_body,
            expected_token=b"structural-preview-noncredential",
            selected_address=channel_selected,
            connected_peer_address=peer,
            hostname=transport["hostname"],
            resolution_count=transport["resolution_count"],
            tls_sni=transport["sni"],
            tls_peer_name=transport["tls_peer_name"],
            proxy_mode=transport["proxy_mode"],
            address_policy_capability=transport["address_policy_capability"],
            actual_network=transport["actual_network"],
            byte_channel=transport["byte_channel"],
            response=raw_response,
        )

        def structural_resolver(host: str, port: int) -> tuple[str, ...]:
            if host != HARDCOVER_IDENTITY_HOST or port != 443:
                raise ValueError("synthetic resolver invocation is invalid")
            return (selected,)

        provider = HardcoverIdentityProvider(
            structural_resolver,
            channel,
            b"structural-preview-noncredential",
        )
        context = OperationContext(
            Deadline(cutoff),
            CancellationProbe(
                CancellationId("hardcover-fixed-adapter"), lambda: False
            ),
            ResourceBounds(4, MAX_RESPONSE_BYTES),
            TimeProbe(lambda: started),
        )
        policy = (
            AddressPolicyOutcome.ACCEPTED
            if address_is_public(selected)
            else AddressPolicyOutcome.REJECTED
        )
        prepared = provider.prepare(query, context)
        result = (
            prepared
            if type(prepared) is IdentityAttemptResult
            else prepared.exchange(context, begin_transport=lambda: context)
        )
        if not channel.called:
            channel.release(raw_response)
        return FixedAdapterAttemptEvidence(
            result,
            query,
            channel.peer_equal if policy is AddressPolicyOutcome.ACCEPTED else False,
            policy,
        )


def _structural_preview_attempt(
    preview: StructuralHardcoverIdentityPreview,
    source: Mapping[str, object],
) -> FixedAdapterAttemptEvidence | None:
    try:
        return preview._run(source)
    except Exception:
        return None


def _strict_preview_time(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("synthetic time is invalid")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if canonical.replace("+00:00", "Z") != value:
        raise ValueError("synthetic time is invalid")
    return parsed


def identity_query_from_canonical_body(body: bytes) -> IsbnIdentityQuery:
    """Recover the redacted typed query only from an exact canonical request body."""

    if type(body) is not bytes or not 1 <= len(body) <= MAX_REQUEST_BYTES:
        body = b""
        raise ValueError("Hardcover identity request body is invalid")
    query = _canonical_query_or_none(body)
    if query is None:
        body = b""
        raise ValueError("Hardcover identity request body is invalid")
    return query


def _canonical_query_or_none(body: bytes) -> IsbnIdentityQuery | None:
    try:
        text = body.decode("utf-8", "strict")
        if text.startswith("\ufeff"):
            raise ValueError
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=_parse_int32,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except Exception:
        return None
    if (
        type(value) is not dict
        or set(value) != {"operationName", "query", "variables"}
        or value["operationName"] != HARDCOVER_IDENTITY_OPERATION
        or value["query"] != HARDCOVER_IDENTITY_QUERY
        or type(value["variables"]) is not dict
        or set(value["variables"]) != {"isbns"}
        or type(value["variables"]["isbns"]) is not list
    ):
        return None
    isbns = value["variables"]["isbns"]
    if len(isbns) not in {1, 2} or any(type(item) is not str for item in isbns):
        return None
    try:
        query = IsbnIdentityQuery(isbns[0], isbns[1] if len(isbns) == 2 else None)
    except Exception:
        return None
    try:
        canonical = _canonical_body(query)
    except Exception:
        return None
    if canonical != body:
        return None
    return query


def authorize_identity_request(
    request: FrozenIdentityRequest,
    query: IsbnIdentityQuery,
    token: bytes,
) -> AuthorizedIdentityRequest | None:
    """Attach bearer bytes only after exact immutable request revalidation."""

    if (
        type(request) is not FrozenIdentityRequest
        or type(query) is not IsbnIdentityQuery
        or not _valid_token(token)
        or request.method != "POST"
        or request.scheme != "https"
        or request.host != HARDCOVER_IDENTITY_HOST
        or request.port != 443
        or request.path != HARDCOVER_IDENTITY_PATH
        or request.query_string
        or request.fragment
        or request.host_header != HARDCOVER_IDENTITY_HOST
        or request.tls_sni != HARDCOVER_IDENTITY_HOST
        or not _public_address(request.selected_address)
        or request.operation_name != HARDCOVER_IDENTITY_OPERATION
        or request.query_bytes != HARDCOVER_IDENTITY_QUERY.encode("ascii")
        or request.body != _canonical_body(query)
        or request.operation_contract_sha256 != OPERATION_CONTRACT_SHA256
    ):
        return None
    headers = (
        (b"Accept", b"application/json"),
        (b"Accept-Encoding", b"identity"),
        (b"Authorization", b"Bearer " + token),
        (b"Connection", b"close"),
        (b"Content-Type", b"application/json"),
        (b"Host", HARDCOVER_IDENTITY_HOST.encode("ascii")),
    )
    return AuthorizedIdentityRequest(request, headers)


def address_is_public(value: object) -> bool:
    return type(value) is str and _public_address(value)


def _build_request(query: IsbnIdentityQuery, selected: str) -> FrozenIdentityRequest:
    return FrozenIdentityRequest(
        "POST",
        "https",
        HARDCOVER_IDENTITY_HOST,
        443,
        HARDCOVER_IDENTITY_PATH,
        "",
        "",
        HARDCOVER_IDENTITY_HOST,
        HARDCOVER_IDENTITY_HOST,
        selected,
        HARDCOVER_IDENTITY_OPERATION,
        HARDCOVER_IDENTITY_QUERY.encode("ascii"),
        _canonical_body(query),
        OPERATION_CONTRACT_SHA256,
    )


def _canonical_body(query: IsbnIdentityQuery) -> bytes:
    isbns = [query.canonical_isbn_13]
    if query.equivalent_isbn_10 is not None:
        isbns.append(query.equivalent_isbn_10)
    body = json.dumps(
        {
            "operationName": HARDCOVER_IDENTITY_OPERATION,
            "query": HARDCOVER_IDENTITY_QUERY,
            "variables": {"isbns": isbns},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(HARDCOVER_IDENTITY_QUERY.encode("ascii")) != 353 or len(body) > MAX_REQUEST_BYTES:
        raise ValueError("Hardcover identity request contract is invalid")
    return body


def _validated_addresses(values: object) -> tuple[str, ...] | None:
    if (
        type(values) is not tuple
        or not values
        or len(values) > MAX_RESOLVER_ADDRESSES
    ):
        return None
    parsed: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in values:
        if not _bounded_ip_text(value) or not _public_address(value):
            return None
        parsed.add(ipaddress.ip_address(value))
    return tuple(str(item) for item in sorted(parsed, key=lambda x: (x.version, int(x))))


def _public_address(value: str) -> bool:
    if "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
        )
    )


def _bounded_ip_text(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= MAX_IP_TEXT_SCALARS:
        return False
    try:
        return len(value.encode("ascii", "strict")) <= MAX_IP_TEXT_BYTES
    except UnicodeError:
        return False


def _ip_endpoint(value: str) -> str:
    address = ipaddress.ip_address(value)
    host = f"[{address}]" if isinstance(address, ipaddress.IPv6Address) else str(address)
    return f"https://{host}{HARDCOVER_IDENTITY_PATH}"


def _response_peer(response: httpx.Response) -> str:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return ""
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:
        return ""
    return peer[0] if type(peer) is tuple and peer and type(peer[0]) is str else ""


def _logical_remaining(context: OperationContext) -> float:
    try:
        return max(0.0, (context.deadline.at - context.time.now()).total_seconds())
    except Exception:
        return 0.0


def _remaining(context: OperationContext, wall_deadline: float) -> float:
    return max(
        0.0,
        min(_logical_remaining(context), wall_deadline - time.monotonic()),
    )


def _bounded_context_failure(
    context: OperationContext, wall_deadline: float
) -> IdentityAttemptFailureKind | None:
    try:
        if context.cancellation.is_cancelled():
            return IdentityAttemptFailureKind.CANCELLED
    except Exception:
        return IdentityAttemptFailureKind.UNAVAILABLE
    if _remaining(context, wall_deadline) <= 0:
        return IdentityAttemptFailureKind.TIMEOUT
    return None


def _terminate_resolver(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        process.wait()


def _run_identity_async(
    awaitable: Coroutine[Any, Any, _ASYNC_RESULT],
) -> _ASYNC_RESULT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    awaitable.close()
    raise IdentityChannelFailure(IdentityAttemptFailureKind.UNAVAILABLE)


def _peer_equal(observed: object, expected: object) -> bool:
    if not _bounded_ip_text(observed) or not _bounded_ip_text(expected):
        return False
    try:
        return ipaddress.ip_address(observed) == ipaddress.ip_address(expected)
    except ValueError:
        return False


def _valid_token(token: object) -> bool:
    return (
        type(token) is bytes
        and 1 <= len(token) <= 2_048
        and all(0x21 <= value <= 0x7E for value in token)
    )


def _context_failure(kind: FailureKind) -> IdentityAttemptFailureKind:
    if kind is FailureKind.CANCELLED:
        return IdentityAttemptFailureKind.CANCELLED
    if kind is FailureKind.TIMEOUT:
        return IdentityAttemptFailureKind.TIMEOUT
    if kind is FailureKind.RESOURCE_EXHAUSTED:
        return IdentityAttemptFailureKind.OVERSIZED
    return IdentityAttemptFailureKind.INCOMPATIBLE


def _safe_preflight(context: OperationContext) -> IdentityAttemptFailureKind | None:
    try:
        result = context.preflight_result(
            item_count=MAX_JSON_LIST_ELEMENTS,
            serialized_bytes=MAX_RESPONSE_BYTES,
        )
    except Exception:
        return IdentityAttemptFailureKind.UNAVAILABLE
    if result.failure is None:
        return None
    return _context_failure(result.failure.kind)


def _close_body(reader: object) -> None:
    try:
        reader.close()
    except Exception:
        pass


def _close_owned_iterator(iterator: object) -> bool:
    """Conclude an owned iterator, treating absence as no external resource."""

    close = getattr(iterator, "close", None)
    if close is None:
        return True
    if not callable(close):
        return False
    try:
        close()
    except Exception:
        return False
    return True


def _parse_status(line: object) -> int | None:
    if type(line) is not bytes or len(line) > MAX_STATUS_LINE_BYTES:
        return None
    match = _STATUS_LINE.fullmatch(line)
    return int(match.group(1)) if match is not None else None


def _status_failure(status: int) -> IdentityAttemptFailureKind | None:
    if status == 200:
        return None
    if status == 401:
        return IdentityAttemptFailureKind.AUTHENTICATION
    if status == 403:
        return IdentityAttemptFailureKind.AUTHORIZATION
    if status == 408:
        return IdentityAttemptFailureKind.TIMEOUT
    if status == 429:
        return IdentityAttemptFailureKind.RATE_LIMITED
    if 500 <= status <= 599:
        return IdentityAttemptFailureKind.UNAVAILABLE
    return IdentityAttemptFailureKind.INCOMPATIBLE


def _rate_limited_result(lines: object) -> IdentityAttemptResult:
    parsed = _parse_headers(lines)
    if isinstance(parsed, IdentityAttemptFailureKind):
        return IdentityAttemptResult.failed(
            IdentityAttemptFailureKind.INVALID_RETRY_AFTER
        )
    values = parsed.get(b"retry-after", [])
    if not values:
        return IdentityAttemptResult.failed(IdentityAttemptFailureKind.RATE_LIMITED)
    if (
        len(values) != 1
        or _RETRY_AFTER.fullmatch(values[0]) is None
        or int(values[0]) > 30
    ):
        return IdentityAttemptResult.failed(
            IdentityAttemptFailureKind.INVALID_RETRY_AFTER
        )
    return IdentityAttemptResult.failed(
        IdentityAttemptFailureKind.RATE_LIMITED,
        retry_after_seconds=int(values[0]),
    )


def _release_response(channel: object, response: object, reader: object) -> bool:
    """Confirm response ownership release through close or transport fallback."""

    try:
        if reader.close() is True:
            return True
    except Exception:
        pass
    try:
        return channel.release(response) is True
    except Exception:
        return False


def _bounded_body(
    lines: object,
    reader: object,
    *,
    _close_after: bool = True,
) -> bytes | IdentityAttemptFailureKind:
    def finish(
        value: bytes | IdentityAttemptFailureKind,
    ) -> bytes | IdentityAttemptFailureKind:
        if _close_after:
            _close_body(reader)
        return value
    header_result = _bounded_body_headers(lines)
    if isinstance(header_result, IdentityAttemptFailureKind):
        return finish(header_result)
    declared_length = header_result
    payload = bytearray()
    try:
        while True:
            remaining = MAX_RESPONSE_BYTES - len(payload)
            requested = remaining + 1
            chunk = reader.read_chunk(requested)
            if chunk is None:
                break
            if type(chunk) is not bytes or not chunk:
                return finish(IdentityAttemptFailureKind.MALFORMED)
            if len(chunk) > requested or len(chunk) > remaining:
                return finish(IdentityAttemptFailureKind.OVERSIZED)
            payload.extend(chunk)
    except Exception:
        return finish(IdentityAttemptFailureKind.UNAVAILABLE)
    if _close_after:
        _close_body(reader)
    if declared_length is not None and declared_length != len(payload):
        return IdentityAttemptFailureKind.MALFORMED
    if not payload:
        return IdentityAttemptFailureKind.EMPTY
    return bytes(payload)


def _bounded_body_header_failure(
    lines: object,
) -> IdentityAttemptFailureKind | None:
    """Classify hostile 200 response headers without consuming body bytes."""

    result = _bounded_body_headers(lines)
    return result if isinstance(result, IdentityAttemptFailureKind) else None


def _bounded_body_headers(
    lines: object,
) -> int | None | IdentityAttemptFailureKind:
    parsed_headers = _parse_headers(lines)
    if isinstance(parsed_headers, IdentityAttemptFailureKind):
        return parsed_headers
    content_type = parsed_headers.get(b"content-type", [])
    if (
        len(content_type) != 1
        or content_type[0].strip(b" \t") != content_type[0]
        or _CONTENT_TYPE.fullmatch(content_type[0]) is None
    ):
        return IdentityAttemptFailureKind.MALFORMED
    encoding = parsed_headers.get(b"content-encoding", [])
    if len(encoding) > 1 or (
        encoding
        and (
            encoding[0].strip(b" \t") != encoding[0]
            or encoding[0].lower() != b"identity"
        )
    ):
        return IdentityAttemptFailureKind.MALFORMED
    declared = parsed_headers.get(b"content-length", [])
    if len(declared) > 1:
        return IdentityAttemptFailureKind.MALFORMED
    if not declared:
        return None
    raw = declared[0]
    if raw.strip(b" \t") != raw or not _canonical_decimal(raw):
        return IdentityAttemptFailureKind.MALFORMED
    if len(raw) > 5 or (len(raw) == 5 and raw > b"65536"):
        return IdentityAttemptFailureKind.OVERSIZED
    return int(raw)


def _parse_headers(
    lines: object,
) -> dict[bytes, list[bytes]] | IdentityAttemptFailureKind:
    if (
        type(lines) is not tuple
        or len(lines) > 16
        or any(type(line) is not bytes or len(line) > 256 for line in lines)
    ):
        return IdentityAttemptFailureKind.MALFORMED
    headers: dict[bytes, list[bytes]] = {}
    for line in lines:
        if not line.endswith(b"\r\n"):
            return IdentityAttemptFailureKind.MALFORMED
        content = line[:-2]
        if b"\r" in content or b"\n" in content or b":" not in content:
            return IdentityAttemptFailureKind.MALFORMED
        name, value = content.split(b":", 1)
        if _HEADER_NAME.fullmatch(name) is None:
            return IdentityAttemptFailureKind.MALFORMED
        if any((byte < 0x20 and byte != 0x09) or byte == 0x7F for byte in value):
            return IdentityAttemptFailureKind.MALFORMED
        if value.startswith(b" "):
            value = value[1:]
        normalized = name.lower()
        if normalized in {
            b"content-type",
            b"content-encoding",
            b"content-length",
        } and normalized in headers:
            return IdentityAttemptFailureKind.MALFORMED
        headers.setdefault(normalized, []).append(value)
    return headers


def _canonical_decimal(value: bytes) -> bool:
    return value == b"0" or (
        bool(value) and value[:1] in b"123456789" and value.isdigit()
    )


def _parse_observations(
    payload: bytes,
) -> tuple[ProviderIdentityObservation, ...] | IdentityAttemptFailureKind:
    try:
        text = payload.decode("utf-8", "strict")
        if text.startswith("\ufeff") or not text.strip() or not _depth_allowed(text):
            raise ValueError
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_int=_parse_int32,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        if (
            type(value) is dict
            and set(value) == {"data"}
            and type(value["data"]) is dict
            and set(value["data"]) == {"editions"}
            and type(value["data"]["editions"]) is list
            and len(value["data"]["editions"]) > 4
        ):
            return IdentityAttemptFailureKind.OVERSIZED
        if not _structure_allowed(value):
            raise ValueError
    except (
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
        json.JSONDecodeError,
    ):
        return IdentityAttemptFailureKind.MALFORMED
    if type(value) is not dict:
        return IdentityAttemptFailureKind.MALFORMED
    if "errors" in value:
        return IdentityAttemptFailureKind.MALFORMED
    if set(value) != {"data"} or type(value["data"]) is not dict:
        return IdentityAttemptFailureKind.MALFORMED
    data = value["data"]
    if set(data) != {"editions"} or type(data["editions"]) is not list:
        return IdentityAttemptFailureKind.MALFORMED
    rows = data["editions"]
    if len(rows) > 4:
        return IdentityAttemptFailureKind.OVERSIZED
    observations: list[ProviderIdentityObservation] = []
    for row in rows:
        if type(row) is not dict or set(row) != _EDITION_KEYS:
            return IdentityAttemptFailureKind.MALFORMED
        book = row["book"]
        if type(book) is not dict or set(book) != _BOOK_KEYS:
            return IdentityAttemptFailureKind.MALFORMED
        if not _provider_title_allowed(book["title"]):
            return IdentityAttemptFailureKind.MALFORMED
        observations.append(
            ProviderIdentityObservation(
                row["id"],
                row["book_id"],
                row["canonical_id"],
                row["isbn_10"],
                row["isbn_10_valid"],
                row["isbn_13"],
                row["isbn_13_valid"],
                row["isbns_match"],
                row["reading_format_id"],
                row["state"],
                row["compilation"],
                ProviderBookObservation(
                    book["id"],
                    book["canonical_id"],
                    book["book_status_id"],
                    book["state"],
                    book["title"],
                    book["parent_book_id"],
                    book["compilation"],
                    book["is_partial_book"],
                ),
            )
        )
    return tuple(observations)


def _depth_allowed(text: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return False
        elif character in "]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string and not escaped


def _structure_allowed(value: object) -> bool:
    nodes = members = elements = scalars = encoded = 0
    pending = [value]
    while pending:
        current = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            return False
        if type(current) is dict:
            members += len(current)
            if members > MAX_JSON_MEMBERS or any(type(key) is not str for key in current):
                return False
            pending.extend(current.values())
        elif type(current) is list:
            elements += len(current)
            if elements > MAX_JSON_LIST_ELEMENTS:
                return False
            pending.extend(current)
        elif type(current) is str:
            size = len(current.encode("utf-8", "strict"))
            if len(current) > MAX_STRING_SCALARS or size > MAX_STRING_BYTES:
                return False
            scalars += len(current)
            encoded += size
            if scalars > MAX_TOTAL_STRING_SCALARS or encoded > MAX_TOTAL_STRING_BYTES:
                return False
        elif current is not None and type(current) not in {bool, int}:
            return False
    return True


def _provider_title_allowed(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return bool(
        1 <= len(value) <= MAX_STRING_SCALARS
        and len(encoded) <= MAX_STRING_BYTES
        and value.strip() == value
        and unicodedata.normalize("NFC", value) == value
        and not any(_forbidden_title_character(ord(character)) for character in value)
    )


def _forbidden_title_character(codepoint: int) -> bool:
    return (
        codepoint <= 0x1F
        or 0x7F <= codepoint <= 0x9F
        or 0xD800 <= codepoint <= 0xDFFF
        or 0xFDD0 <= codepoint <= 0xFDEF
        or (codepoint & 0xFFFF) in {0xFFFE, 0xFFFF}
        or codepoint in _BIDI_CONTROLS
        or codepoint in {0x2028, 0x2029}
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _parse_int32(token: str) -> int:
    value = int(token)
    if not MIN_INT32 <= value <= MAX_INT32:
        raise ValueError("JSON integer exceeds the supported range")
    return value


def _reject_number(_: str) -> int:
    raise ValueError("JSON number type is unsupported")


__all__ = [
    "AuthorizedIdentityRequest",
    "BoundedIdentityAddressResolver",
    "FrozenIdentityRequest",
    "HARDCOVER_IDENTITY_HOST",
    "HARDCOVER_IDENTITY_OPERATION",
    "HARDCOVER_IDENTITY_QUERY",
    "HARDCOVER_IDENTITY_URL",
    "HttpxIdentityByteChannel",
    "HardcoverIdentityProvider",
    "IdentityChannelFailure",
    "LiveHardcoverIdentityProvider",
    "RawIdentityResponseHead",
    "StructuralHardcoverIdentityPreview",
    "StructuralIdentityBodyReader",
    "StructuralIdentityByteChannel",
    "address_is_public",
    "authorize_identity_request",
    "identity_query_from_canonical_body",
]
