"""Fixed, bounded Hardcover ISBN identity adapter.

The module is deliberately deep: callers supply one ISBN query, one operation
context, one resolver, one byte channel, and one already-loaded token. Request
construction, address policy, authorization, status handling, and hostile
response parsing stay behind the application-owned provider port.
"""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from mechanical_librarian.application.hardcover_identity import (
    AddressPolicyOutcome,
    FixedAdapterAttemptEvidence,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IsbnIdentityQuery,
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

    def lookup(
        self, query: IsbnIdentityQuery, context: OperationContext
    ) -> IdentityAttemptResult:
        if type(query) is not IsbnIdentityQuery or type(context) is not OperationContext:
            return IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INCOMPATIBLE
            )
        preflight = _safe_preflight(context)
        if preflight is not None:
            return IdentityAttemptResult.failed(preflight)
        try:
            resolved = self._resolver(HARDCOVER_IDENTITY_HOST, 443)
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
        try:
            response = self._channel.exchange(authorized, context)
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
            if not _peer_equal(response.connected_peer_address, addresses[0]):
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
        result = provider.lookup(query, context)
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
    parsed_headers = _parse_headers(lines)
    if isinstance(parsed_headers, IdentityAttemptFailureKind):
        return finish(parsed_headers)
    headers = parsed_headers

    content_type = headers.get(b"content-type", [])
    if (
        len(content_type) != 1
        or content_type[0].strip(b" \t") != content_type[0]
        or _CONTENT_TYPE.fullmatch(content_type[0]) is None
    ):
        return finish(IdentityAttemptFailureKind.MALFORMED)
    encoding = headers.get(b"content-encoding", [])
    if len(encoding) > 1 or (
        encoding
        and (
            encoding[0].strip(b" \t") != encoding[0]
            or encoding[0].lower() != b"identity"
        )
    ):
        return finish(IdentityAttemptFailureKind.MALFORMED)
    declared = headers.get(b"content-length", [])
    if len(declared) > 1:
        return finish(IdentityAttemptFailureKind.MALFORMED)
    declared_length: int | None = None
    if declared:
        raw = declared[0]
        if raw.strip(b" \t") != raw or not _canonical_decimal(raw):
            return finish(IdentityAttemptFailureKind.MALFORMED)
        if len(raw) > 5 or (len(raw) == 5 and raw > b"65536"):
            return finish(IdentityAttemptFailureKind.OVERSIZED)
        declared_length = int(raw)
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
    "FrozenIdentityRequest",
    "HARDCOVER_IDENTITY_HOST",
    "HARDCOVER_IDENTITY_OPERATION",
    "HARDCOVER_IDENTITY_QUERY",
    "HARDCOVER_IDENTITY_URL",
    "HardcoverIdentityProvider",
    "IdentityChannelFailure",
    "RawIdentityResponseHead",
    "StructuralHardcoverIdentityPreview",
    "StructuralIdentityBodyReader",
    "StructuralIdentityByteChannel",
    "address_is_public",
    "authorize_identity_request",
    "identity_query_from_canonical_body",
]
