"""Bounded read-only Hardcover title provider for the F09b proof of concept."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
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
CONTRACT_VERSION = "hardcover-f09b-contract-v2"
MAX_REQUEST_BYTES = 8_192
MAX_RESPONSE_BYTES = 65_536
MAX_TITLE_LENGTH = 512
MAX_JSON_DEPTH = 16
MAX_JSON_NODES = 128
MAX_OBJECT_KEYS = 64
MAX_LIST_ITEMS = 16
MAX_STRING_LENGTH = 512
MAX_INTEGER = 2_147_483_647
MAX_TEXT_MATCH_INTEGER = 9_223_372_036_854_775_807
MIN_RATING = 0.0
MAX_RATING = 5.0

_RESULT_KEYS = {
    "facet_counts",
    "found",
    "hits",
    "out_of",
    "page",
    "request_params",
    "search_cutoff",
    "search_time_ms",
}
_REQUEST_PARAM_KEYS = {"collection_name", "first_q", "per_page", "q"}
_HIT_KEYS = {"document", "highlight", "highlights", "text_match", "text_match_info"}
_DOCUMENT_KEYS = {
    "activities_count",
    "alternative_titles",
    "author_names",
    "book_category_id",
    "compilation",
    "content_warnings",
    "contribution_types",
    "contributions",
    "cover_color",
    "description",
    "featured_series",
    "genres",
    "has_audiobook",
    "has_ebook",
    "id",
    "image",
    "isbns",
    "lists_count",
    "moods",
    "pages",
    "prompts_count",
    "rating",
    "ratings_count",
    "release_date",
    "release_year",
    "reviews_count",
    "series_ids",
    "series_names",
    "slug",
    "subtitle",
    "tags",
    "title",
    "users_count",
    "users_read_count",
}
_DOCUMENT_INTEGER_KEYS = {
    "activities_count",
    "book_category_id",
    "lists_count",
    "pages",
    "prompts_count",
    "ratings_count",
    "release_year",
    "reviews_count",
    "users_count",
    "users_read_count",
}
_DOCUMENT_BOOLEAN_KEYS = {"compilation", "has_audiobook", "has_ebook"}
_DOCUMENT_STRING_KEYS = {
    "cover_color",
    "description",
    "id",
    "release_date",
    "slug",
    "subtitle",
}
_DOCUMENT_STRING_LIST_KEYS = {
    "alternative_titles",
    "author_names",
    "content_warnings",
    "contribution_types",
    "genres",
    "isbns",
    "moods",
    "series_ids",
    "series_names",
    "tags",
}
_IMAGE_KEYS = {"color", "color_name", "height", "id", "url", "width"}
_TEXT_MATCH_INFO_KEYS = {
    "best_field_score",
    "best_field_weight",
    "fields_matched",
    "num_tokens_dropped",
    "score",
    "tokens_matched",
    "typo_prefix_score",
}

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
            if not _response_size_allowed(declared_length):
                return None
        except ValueError:
            raise ValueError("invalid content length") from None
    payload = bytearray()
    async for chunk in response.aiter_bytes():
        failure = budget.failure()
        if failure is not None:
            return failure
        payload.extend(chunk)
        if not _response_size_allowed(len(payload)):
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
        if not _json_within_bounds(value):
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
    ):
        return _failure(FailureKind.INCOMPATIBLE, "Hardcover response incompatible")
    parsed_title = _parse_search_results(search["results"], requested_title)
    if isinstance(parsed_title, PortFailure):
        return parsed_title
    if parsed_title is None:
        return ()
    title = parsed_title
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


def _parse_search_results(value: object, requested_title: str) -> str | None | PortFailure:
    if type(value) is not dict or set(value) != _RESULT_KEYS:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover search result invalid")
    if (
        type(value["facet_counts"]) is not list
        or value["facet_counts"]
        or not _non_negative_int(value["found"])
        or not _non_negative_int(value["out_of"])
        or type(value["page"]) is not int
        or value["page"] != 1
        or type(value["search_cutoff"]) is not bool
        or not _non_negative_int(value["search_time_ms"])
        or type(value["hits"]) is not list
        or len(value["hits"]) > 1
        or value["found"] > value["out_of"]
        or bool(value["hits"]) != (value["found"] > 0)
    ):
        return _failure(FailureKind.INCOMPATIBLE, "Hardcover search result incompatible")
    request_params = value["request_params"]
    if (
        type(request_params) is not dict
        or set(request_params) != _REQUEST_PARAM_KEYS
        or not _bounded_string(request_params["collection_name"], require_value=True)
        or type(request_params["first_q"]) is not str
        or request_params["first_q"] != requested_title
        or type(request_params["q"]) is not str
        or request_params["q"] != requested_title
        or type(request_params["per_page"]) is not int
        or request_params["per_page"] != 1
    ):
        return _failure(FailureKind.INCOMPATIBLE, "Hardcover search echo incompatible")
    if not value["hits"]:
        return None
    return _parse_hit(value["hits"][0])


def _parse_hit(value: object) -> str | PortFailure:
    if type(value) is not dict or set(value) != _HIT_KEYS:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover search hit invalid")
    document = value["document"]
    if type(document) is not dict or set(document) != _DOCUMENT_KEYS:
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover document invalid")
    if any(not _non_negative_int(document[key]) for key in _DOCUMENT_INTEGER_KEYS):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover document invalid")
    if any(type(document[key]) is not bool for key in _DOCUMENT_BOOLEAN_KEYS):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover document invalid")
    if any(not _bounded_string(document[key]) for key in _DOCUMENT_STRING_KEYS):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover document invalid")
    if any(not _string_list(document[key]) for key in _DOCUMENT_STRING_LIST_KEYS):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover document invalid")
    if (
        type(document["featured_series"]) is not dict
        or document["featured_series"]
        or not _contributions(document["contributions"])
        or not _image(document["image"])
        or type(document["rating"]) is not float
        or not math.isfinite(document["rating"])
        or not MIN_RATING <= document["rating"] <= MAX_RATING
        or not _non_negative_text_match_int(value["text_match"])
        or not _highlight(value["highlight"])
        or not _highlights(value["highlights"])
        or not _text_match_info(value["text_match_info"])
    ):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover search hit invalid")
    title = document["title"]
    if not _bounded_string(title, require_value=True, require_trimmed=True):
        return _failure(FailureKind.INVALID_RESPONSE, "Hardcover title result invalid")
    return title


def _contributions(value: object) -> bool:
    if type(value) is not list:
        return False
    for item in value:
        if type(item) is not dict or set(item) not in (
            {"author"},
            {"author", "contribution"},
        ):
            return False
        if "contribution" in item and not _bounded_string(item["contribution"]):
            return False
        author = item["author"]
        if type(author) is not dict or set(author) != {"id", "image", "name", "slug"}:
            return False
        if (
            not _non_negative_int(author["id"])
            or type(author["image"]) is not dict
            or author["image"]
            or not _bounded_string(author["name"], require_value=True)
            or not _bounded_string(author["slug"], require_value=True)
        ):
            return False
    return True


def _image(value: object) -> bool:
    return (
        type(value) is dict
        and set(value) == _IMAGE_KEYS
        and all(_bounded_string(value[key]) for key in ("color", "color_name", "url"))
        and all(_non_negative_int(value[key]) for key in ("height", "id", "width"))
    )


def _highlight(value: object) -> bool:
    if type(value) is not dict or set(value) != {"title"}:
        return False
    title = value["title"]
    return (
        type(title) is dict
        and set(title) == {"matched_tokens", "snippet"}
        and _string_list(title["matched_tokens"])
        and _bounded_string(title["snippet"])
    )


def _highlights(value: object) -> bool:
    if type(value) is not list or len(value) != 1:
        return False
    item = value[0]
    return (
        type(item) is dict
        and set(item) == {"field", "matched_tokens", "snippet"}
        and _bounded_string(item["field"], require_value=True)
        and _string_list(item["matched_tokens"])
        and _bounded_string(item["snippet"])
    )


def _text_match_info(value: object) -> bool:
    if type(value) is not dict or set(value) != _TEXT_MATCH_INFO_KEYS:
        return False
    return (
        _bounded_string(value["best_field_score"], require_value=True)
        and _bounded_string(value["score"], require_value=True)
        and all(
            _non_negative_int(value[key])
            for key in (
                "best_field_weight",
                "fields_matched",
                "num_tokens_dropped",
                "tokens_matched",
                "typo_prefix_score",
            )
        )
    )


def _string_list(value: object) -> bool:
    return type(value) is list and all(_bounded_string(item) for item in value)


def _non_negative_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_INTEGER


def _non_negative_text_match_int(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_TEXT_MATCH_INTEGER


def _response_size_allowed(size: object) -> bool:
    return type(size) is int and 0 <= size <= MAX_RESPONSE_BYTES


def _json_within_bounds(value: object) -> bool:
    nodes, depth, object_keys, list_items, string_length = _json_shape(value)
    return (
        nodes <= MAX_JSON_NODES
        and depth <= MAX_JSON_DEPTH
        and object_keys <= MAX_OBJECT_KEYS
        and list_items <= MAX_LIST_ITEMS
        and string_length <= MAX_STRING_LENGTH
    )


def _bounded_string(
    value: object, *, require_value: bool = False, require_trimmed: bool = False
) -> bool:
    if type(value) is not str or len(value) > MAX_STRING_LENGTH:
        return False
    if require_value and not value:
        return False
    if require_trimmed and value.strip() != value:
        return False
    if "\x00" in value or any(ord(character) < 32 for character in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-finite JSON value")


def _json_shape(value: object, depth: int = 1) -> tuple[int, int, int, int, int]:
    if isinstance(value, Mapping):
        nodes = 1
        maximum = depth
        object_keys = len(value)
        list_items = 0
        string_length = 0
        for item in value.values():
            (
                child_nodes,
                child_depth,
                child_object_keys,
                child_list_items,
                child_string_length,
            ) = _json_shape(item, depth + 1)
            nodes += child_nodes
            maximum = max(maximum, child_depth)
            object_keys = max(object_keys, child_object_keys)
            list_items = max(list_items, child_list_items)
            string_length = max(string_length, child_string_length)
        return nodes, maximum, object_keys, list_items, string_length
    if type(value) is list:
        nodes = 1
        maximum = depth
        object_keys = 0
        list_items = len(value)
        string_length = 0
        for item in value:
            (
                child_nodes,
                child_depth,
                child_object_keys,
                child_list_items,
                child_string_length,
            ) = _json_shape(item, depth + 1)
            nodes += child_nodes
            maximum = max(maximum, child_depth)
            object_keys = max(object_keys, child_object_keys)
            list_items = max(list_items, child_list_items)
            string_length = max(string_length, child_string_length)
        return nodes, maximum, object_keys, list_items, string_length
    return 1, depth, 0, 0, len(value) if type(value) is str else 0


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
