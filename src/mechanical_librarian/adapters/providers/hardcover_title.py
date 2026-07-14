"""Bounded read-only Hardcover title provider for the F09b proof of concept."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import subprocess
import sys
import time
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, TypeVar

import httpx

from mechanical_librarian.application.ports import (
    FailureKind,
    MetadataCandidate,
    OperationContext,
    Page,
    PageRequest,
    PortFailure,
    PortResult,
    ProviderFactCode,
    ProviderProvenance,
    ProviderQuery,
    RetryAdvice,
    SafeSummary,
    provider_response_preflight,
)
from mechanical_librarian.application.title_proposals import PROVIDER_SCHEMA
from mechanical_librarian.domain.evidence import FieldValue
from mechanical_librarian.domain.evidence_bounds import validate_projection_text
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.inventory import FieldName, MediaType


HARDCOVER_HOST = "api.hardcover.app"
HARDCOVER_PATH = "/v1/graphql"
HARDCOVER_ENDPOINT = f"https://{HARDCOVER_HOST}{HARDCOVER_PATH}"
HARDCOVER_QUERY = """query HardcoverTitlePoc($title: String!) {
  search(query: $title, query_type: \"Book\", per_page: 1, page: 1, fields: \"title\", weights: \"1\", typos: \"0\") {
    error
    page
    per_page
    query
    query_type
    results
  }
}"""
CONTRACT_VERSION = "hardcover-f09b-contract-v1"
MAX_REQUEST_BYTES = 8_192
MAX_RESPONSE_BYTES = 65_536
MAX_TITLE_LENGTH = 512
MAX_JSON_DEPTH = 8
MAX_JSON_NODES = 64

AddressResolver = Callable[[str, int, "_OperationBudget"], tuple[str, ...]]
_ResultT = TypeVar("_ResultT")


class _OperationBudget:
    """Enforce one logical and wall-clock budget across blocking adapter work."""

    def __init__(self, context: OperationContext) -> None:
        self._context = context
        initial = max(
            0.0,
            (context.deadline.at - context.time.now()).total_seconds(),
        )
        self._wall_deadline = time.monotonic() + initial

    def remaining(self) -> float:
        logical = (
            self._context.deadline.at - self._context.time.now()
        ).total_seconds()
        wall = self._wall_deadline - time.monotonic()
        return max(0.0, min(logical, wall))

    def failure(self) -> PortFailure | None:
        if self._context.cancellation.is_cancelled():
            return _failure(FailureKind.CANCELLED, "Hardcover request cancelled")
        if self.remaining() <= 0:
            return _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
        return None


class _ResolverTimeout(Exception):
    pass


class _ResolverCancelled(Exception):
    pass


class HardcoverTitleProvider:
    """Implement only ``MetadataProvider`` with one fixed credentialed operation."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        resolver: AddressResolver | None = None,
    ) -> None:
        if type(client) is not httpx.AsyncClient or (
            resolver is not None and not callable(resolver)
        ):
            raise ValueError("Hardcover provider construction is invalid")
        self._client = client
        self._resolver = _system_resolver if resolver is None else resolver

    def lookup(
        self, query: ProviderQuery, page: PageRequest, context: OperationContext
    ) -> PortResult[Page[MetadataCandidate]]:
        if (
            type(query) is not ProviderQuery
            or type(page) is not PageRequest
            or type(context) is not OperationContext
        ):
            return PortResult.failed(
                _failure(FailureKind.INVALID_REQUEST, "Hardcover provider request invalid")
            )
        preflight = query.preflight_result(page, context)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        title = _query_title(query, page)
        if title is None:
            return PortResult.failed(
                _failure(FailureKind.INVALID_REQUEST, "Hardcover provider request invalid")
            )
        if context.cancellation.is_cancelled():
            return PortResult.failed(
                _failure(FailureKind.CANCELLED, "Hardcover request cancelled")
            )
        if context.deadline.expired_at(context.time.now()):
            return PortResult.failed(
                _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
            )

        budget = _OperationBudget(context)
        try:
            addresses = _validated_addresses(
                self._resolver(HARDCOVER_HOST, 443, budget)
            )
        except (KeyboardInterrupt, _ResolverCancelled):
            return PortResult.failed(
                _failure(FailureKind.CANCELLED, "Hardcover request cancelled")
            )
        except _ResolverTimeout:
            return PortResult.failed(
                _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
            )
        except Exception:
            return PortResult.failed(
                _failure(FailureKind.UNAVAILABLE, "Hardcover destination unavailable")
            )
        if addresses is None:
            return PortResult.failed(
                _failure(FailureKind.INTEGRITY_FAILURE, "Hardcover destination rejected")
            )
        budget_failure = budget.failure()
        if budget_failure is not None:
            return PortResult.failed(budget_failure)
        selected = addresses[0]
        request_body = json.dumps(
            {
                "operationName": "HardcoverTitlePoc",
                "query": HARDCOVER_QUERY,
                "variables": {"title": title},
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(request_body) > MAX_REQUEST_BYTES:
            return PortResult.failed(
                _failure(FailureKind.RESOURCE_EXHAUSTED, "Hardcover request too large")
            )
        remaining = budget.remaining()
        if remaining <= 0:
            return PortResult.failed(
                _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
            )
        request = self._client.build_request(
            "POST",
            _ip_endpoint(selected),
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Content-Type": "application/json",
                "Host": HARDCOVER_HOST,
                "User-Agent": "Mechanical-Librarian/0.2 Hardcover-F09b-PoC",
            },
            content=request_body,
            extensions={
                "sni_hostname": HARDCOVER_HOST,
                "timeout": {
                    "connect": remaining,
                    "read": remaining,
                    "write": remaining,
                    "pool": remaining,
                },
            },
        )
        try:
            payload = _run_async(
                self._perform_request_bounded(request, selected, budget)
            )
        except KeyboardInterrupt:
            return PortResult.failed(
                _failure(FailureKind.CANCELLED, "Hardcover request cancelled")
            )
        except httpx.TimeoutException:
            payload = _failure(FailureKind.TIMEOUT, "Hardcover request timed out")
        except httpx.DecodingError:
            payload = _failure(
                FailureKind.INVALID_RESPONSE, "Hardcover response encoding invalid"
            )
        except httpx.HTTPError:
            payload = _failure(FailureKind.UNAVAILABLE, "Hardcover request unavailable")
        except Exception:
            payload = _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
        budget_failure = budget.failure()
        if budget_failure is not None:
            return PortResult.failed(budget_failure)
        if isinstance(payload, PortFailure):
            return PortResult.failed(payload)
        if type(payload) is not bytes:
            return PortResult.failed(
                _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
            )
        parsed = _parse_response(payload, title)
        budget_failure = budget.failure()
        if budget_failure is not None:
            return PortResult.failed(budget_failure)
        if isinstance(parsed, PortFailure):
            return PortResult.failed(parsed)
        result = Page(parsed, None)
        checked = provider_response_preflight(result, page, context, MetadataCandidate)
        if checked.failure is not None:
            return PortResult.failed(checked.failure)
        budget_failure = budget.failure()
        if budget_failure is not None:
            return PortResult.failed(budget_failure)
        return PortResult.success(result)

    async def _perform_request_bounded(
        self,
        request: httpx.Request,
        selected: ipaddress.IPv4Address | ipaddress.IPv6Address,
        budget: _OperationBudget,
    ) -> bytes | PortFailure:
        remaining = budget.remaining()
        if remaining <= 0:
            return _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
        task = asyncio.create_task(self._perform_request(request, selected, budget))
        try:
            async with asyncio.timeout(remaining):
                while not task.done():
                    failure = budget.failure()
                    if failure is not None:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        return failure
                    await asyncio.wait(
                        (task,), timeout=min(0.01, budget.remaining())
                    )
                return task.result()
        except TimeoutError:
            return _failure(FailureKind.TIMEOUT, "Hardcover request deadline expired")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _perform_request(
        self,
        request: httpx.Request,
        selected: ipaddress.IPv4Address | ipaddress.IPv6Address,
        budget: _OperationBudget,
    ) -> bytes | PortFailure:
        response = await self._client.send(request, stream=True)
        try:
            if not _peer_matches(response, selected):
                return _failure(FailureKind.INTEGRITY_FAILURE, "Hardcover peer rejected")
            status_failure = _status_failure(response.status_code)
            if status_failure is not None:
                return status_failure
            payload = await _bounded_response_bytes(response, budget)
            if isinstance(payload, PortFailure):
                return payload
            if payload is None:
                return _failure(
                    FailureKind.RESOURCE_EXHAUSTED,
                    "Hardcover response too large",
                )
            return payload
        finally:
            await response.aclose()


def _query_title(query: ProviderQuery, page: PageRequest) -> str | None:
    if (
        query.media_type is not MediaType.EBOOK
        or query.field is not FieldName.TITLE
        or query.schema != PROVIDER_SCHEMA
        or page.cursor is not None
        or page.limit != 1
        or len(query.facts) != 1
        or query.facts[0].code is not ProviderFactCode.TITLE
        or len(query.facts[0].values) != 1
    ):
        return None
    title = query.facts[0].values[0]
    if (
        not title
        or title.strip() != title
        or len(title) > MAX_TITLE_LENGTH
        or "\x00" in title
        or any(ord(character) < 32 for character in title)
    ):
        return None
    return title


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


def _system_resolver(
    host: str,
    port: int,
    budget: _OperationBudget,
) -> tuple[str, ...]:
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
            failure = budget.failure()
            if failure is not None:
                _terminate_process(process)
                if failure.kind is FailureKind.CANCELLED:
                    raise _ResolverCancelled
                raise _ResolverTimeout
            time.sleep(min(0.01, budget.remaining()))
        output, _ = process.communicate(timeout=0.1)
    finally:
        if process.poll() is None:
            _terminate_process(process)
    if process.returncode != 0 or not 1 <= len(output) <= 8_192:
        raise OSError("resolver failed")
    try:
        values = json.loads(output.decode("ascii"))
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise OSError("resolver output invalid") from None
    if type(values) is not list or any(type(value) is not str for value in values):
        raise OSError("resolver output invalid")
    return tuple(values)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        process.wait()


def _validated_addresses(values: object) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] | None:
    if type(values) is not tuple or not values:
        return None
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in values:
        if type(value) is not str or "%" in value:
            return None
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return None
        if (
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or (
                isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None
            )
        ):
            return None
        addresses.add(address)
    return tuple(sorted(addresses, key=lambda item: (item.version, int(item))))


def _ip_endpoint(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    host = f"[{address}]" if isinstance(address, ipaddress.IPv6Address) else str(address)
    return f"https://{host}{HARDCOVER_PATH}"


def _peer_matches(
    response: httpx.Response,
    expected: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    stream = response.extensions.get("network_stream")
    if stream is None or not hasattr(stream, "get_extra_info"):
        return False
    try:
        peer = stream.get_extra_info("server_addr")
        if type(peer) is not tuple or not peer or type(peer[0]) is not str:
            return False
        return ipaddress.ip_address(peer[0]) == expected
    except (OSError, TypeError, ValueError):
        return False


def _status_failure(status: int) -> PortFailure | None:
    if status == 200:
        return None
    if status == 401:
        return _failure(FailureKind.AUTHENTICATION, "Hardcover authentication failed")
    if status == 403:
        return _failure(FailureKind.AUTHORIZATION, "Hardcover authorization failed")
    if status == 404:
        return _failure(FailureKind.NOT_FOUND, "Hardcover title not found")
    if status == 408:
        return _failure(FailureKind.TIMEOUT, "Hardcover request timed out")
    if status == 429:
        return _failure(FailureKind.RATE_LIMITED, "Hardcover rate limit reached")
    if 300 <= status < 400:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover redirect rejected")
    if 500 <= status < 600:
        return _failure(FailureKind.UNAVAILABLE, "Hardcover service unavailable")
    return _failure(FailureKind.INCOMPATIBLE, "Hardcover response status unsupported")


async def _bounded_response_bytes(
    response: httpx.Response,
    budget: _OperationBudget,
) -> bytes | PortFailure | None:
    failure = budget.failure()
    if failure is not None:
        return failure
    content_type = response.headers.get("content-type", "")
    content_encoding = response.headers.get("content-encoding", "identity")
    if content_type.partition(";")[0].strip().casefold() != "application/json":
        raise ValueError("unexpected content type")
    if content_encoding.casefold() != "identity":
        raise ValueError("unexpected content encoding")
    length_header = response.headers.get("content-length")
    declared_length: int | None = None
    if length_header is not None:
        try:
            declared_length = int(length_header)
            if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
                return None
        except ValueError:
            raise ValueError("invalid content length") from None
    payload = bytearray()
    async for chunk in response.aiter_bytes():
        failure = budget.failure()
        if failure is not None:
            return failure
        payload.extend(chunk)
        if len(payload) > MAX_RESPONSE_BYTES:
            return None
        failure = budget.failure()
        if failure is not None:
            return failure
    if declared_length is not None and declared_length != len(payload):
        raise ValueError("content length mismatch")
    return bytes(payload)


def _run_async(awaitable: Coroutine[Any, Any, _ResultT]) -> _ResultT:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    awaitable.close()
    raise RuntimeError("Hardcover provider requires a synchronous application caller")


def _parse_response(
    payload: bytes, requested_title: str
) -> tuple[MetadataCandidate, ...] | PortFailure:
    try:
        text = payload.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        nodes, depth = _json_shape(value)
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError
    except (UnicodeError, ValueError, TypeError, RecursionError, json.JSONDecodeError):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
    if type(value) is not dict:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
    if "errors" in value:
        if set(value) not in ({"errors"}, {"data", "errors"}) or type(value["errors"]) is not list or not value["errors"]:
            return _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover GraphQL request failed")
    if set(value) != {"data"} or type(value["data"]) is not dict or set(value["data"]) != {"search"}:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
    search = value["data"]["search"]
    if type(search) is not dict or set(search) != {
        "error", "page", "per_page", "query", "query_type", "results"
    }:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover response invalid")
    if search["error"] is not None:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover search failed")
    if (
        type(search["page"]) is not int
        or type(search["per_page"]) is not int
        or type(search["query"]) is not str
        or type(search["query_type"]) is not str
        or search["page"] != 1
        or search["per_page"] != 1
        or search["query"] != requested_title
        or search["query_type"] != "Book"
        or type(search["results"]) is not list
        or len(search["results"]) > 1
    ):
        return _failure(FailureKind.INCOMPATIBLE, "Hardcover response incompatible")
    if not search["results"]:
        return ()
    item = search["results"][0]
    if type(item) is not dict or set(item) != {"title"}:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover title result invalid")
    title = item["title"]
    if (
        type(title) is not str
        or not title
        or title.strip() != title
        or len(title) > MAX_TITLE_LENGTH
        or "\x00" in title
        or any(ord(character) < 32 for character in title)
    ):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover title result invalid")
    try:
        title.encode("utf-8")
        validate_projection_text(title, "Hardcover title", MAX_TITLE_LENGTH)
        fingerprint = Fingerprint.from_canonical_value(
            {"contract": CONTRACT_VERSION, "title": title},
            schema=SchemaVersion("hardcover-title-candidate-v1"),
        )
        correlation = f"hardcover-poc-{fingerprint.digest[:48]}"
        candidate = MetadataCandidate(
            FieldName.TITLE,
            FieldValue((title,)),
            ProviderProvenance(
                "hardcover-title-poc",
                correlation,
                fingerprint,
            ),
        )
    except (UnicodeError, TypeError, ValueError):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover title result invalid")
    return (candidate,)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON value")


def _json_shape(value: object, depth: int = 1) -> tuple[int, int]:
    if isinstance(value, Mapping):
        nodes = 1
        maximum = depth
        for item in value.values():
            child_nodes, child_depth = _json_shape(item, depth + 1)
            nodes += child_nodes
            maximum = max(maximum, child_depth)
        return nodes, maximum
    if type(value) is list:
        nodes = 1
        maximum = depth
        for item in value:
            child_nodes, child_depth = _json_shape(item, depth + 1)
            nodes += child_nodes
            maximum = max(maximum, child_depth)
        return nodes, maximum
    return 1, depth


def _failure(kind: FailureKind, summary: str) -> PortFailure:
    return PortFailure(kind, RetryAdvice(False), SafeSummary(summary))


__all__ = [
    "CONTRACT_VERSION",
    "HARDCOVER_ENDPOINT",
    "HARDCOVER_HOST",
    "HARDCOVER_PATH",
    "HARDCOVER_QUERY",
    "HardcoverTitleProvider",
]
