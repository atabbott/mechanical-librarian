"""One-request, closed-envelope Hardcover metadata observation."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

import httpx

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    LocalClaim,
    MAX_CANDIDATES,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    canonical_json,
    compare_claim,
    stable_reference,
)


HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
_QUERY = (
    "query MetadataEvidenceByIsbn($isbns:[String!]!){editions(where:{_or:["
    f"{{isbn_13:{{_in:$isbns}}}},{{isbn_10:{{_in:$isbns}}}}]}},order_by:{{id:asc}},limit:{MAX_CANDIDATES + 1})"
    "{id book_id isbn_10 isbn_13} books:books_through_editions(where:{editions:{_or:[{isbn_13:{_in:$isbns}},{isbn_10:{_in:$isbns}}]}}},order_by:{id:asc},limit:6){id title}}}"
)


class HardcoverMetadataProvider:
    def __init__(self, name: str, client: httpx.Client, maximum_bytes: int) -> None:
        self._name = name
        self._client = client
        self._maximum_bytes = maximum_bytes
        self._called = False

    def observe(self, local_claims: tuple[LocalClaim, ...]) -> ProviderObservation:
        if self._called:
            return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE)
        self._called = True
        usable = tuple(
            sorted(
                (
                    claim
                    for claim in local_claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.canonical is not None
                    and claim.applicability in {"ebook", "unknown"}
                    and claim.basis == "opening_matter"
                    and claim.rear_provenance is None
                ),
                key=lambda value: (value.canonical or "", value.reference),
            )
        )
        if not usable:
            self._client.close()
            return ProviderObservation(self._name, ProviderStatus.INSUFFICIENT_INPUT)
        isbns = sorted({claim.canonical for claim in usable if claim.canonical is not None})
        references = tuple(sorted(claim.reference for claim in usable))
        body = {"operationName": "MetadataEvidenceByIsbn", "query": _QUERY, "variables": {"isbns": isbns}}
        try:
            with self._client, self._client.stream(
                "POST",
                HARDCOVER_URL,
                content=canonical_json(body).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                status = response.status_code
                if status == 401:
                    return ProviderObservation(self._name, ProviderStatus.AUTHENTICATION, references)
                if status == 403:
                    return ProviderObservation(self._name, ProviderStatus.AUTHORIZATION, references)
                if status == 429:
                    return ProviderObservation(self._name, ProviderStatus.RATE_LIMITED, references)
                if status != 200:
                    return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE, references)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    return ProviderObservation(self._name, ProviderStatus.MALFORMED, references)
                payload = _body(response, self._maximum_bytes)
        except (httpx.HTTPError, OSError, ValueError):
            return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE, references)
        try:
            candidates = _parse_candidates(payload, self._name, local_claims)
        except _CandidateLimit:
            return ProviderObservation(self._name, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, references)
        except (ValueError, TypeError, UnicodeError, RecursionError, json.JSONDecodeError):
            return ProviderObservation(self._name, ProviderStatus.MALFORMED, references)
        return ProviderObservation(
            self._name,
            ProviderStatus.CANDIDATES if candidates else ProviderStatus.NO_MATCH,
            references,
            candidates,
        )


def _body(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > maximum:
            raise ValueError("provider response oversized")
        chunks.append(chunk)
    if not chunks:
        raise ValueError("provider response empty")
    return b"".join(chunks)


def _parse_candidates(payload: bytes, provider: str, local_claims: tuple[LocalClaim, ...]) -> tuple[ProviderCandidate, ...]:
    source = json.loads(
        payload.decode("utf-8", "strict"),
        object_pairs_hook=_unique_object,
        parse_constant=_reject_number,
        parse_float=_reject_number,
    )
    root = _closed(source, {"data"})
    data = _closed(root["data"], {"editions", "books"})
    rows = data["editions"]
    books = data["books"]
    if type(rows) is not list or type(books) is not list:
        raise ValueError("provider rows invalid")
    if len(rows) > MAX_CANDIDATES:
        raise _CandidateLimit
    indexed_books: dict[int, dict[str, Any]] = {}
    for value in books:
        book = _closed(value, {"id", "title"})
        if type(book["id"]) is not int or type(book["id"]) is bool or book["id"] in indexed_books:
            raise ValueError("provider work invalid")
        indexed_books[book["id"]] = book
    candidates: list[ProviderCandidate] = []
    referenced_books: set[int] = set()
    for row_value in rows:
        row = _closed(row_value, {"book_id", "id", "isbn_10", "isbn_13"})
        identity_value = row["id"]
        if type(identity_value) is not int or type(identity_value) is bool or not 1 <= identity_value <= 2_147_483_647:
            raise ValueError("provider identity invalid")
        identity = f"hardcover-edition-{identity_value:010d}"
        claims: list[ProviderClaim] = []
        book_id = row["book_id"]
        if type(book_id) is not int or type(book_id) is bool or book_id not in indexed_books:
            raise ValueError("provider unreferenced work")
        referenced_books.add(book_id)
        book = indexed_books[book_id]
        if book["title"] is not None:
            claims.append(_claim(provider, identity, ClaimKind.TITLE, _text(book["title"], 512), local_claims))
        for key in ("isbn_10", "isbn_13"):
            value = row[key]
            if value is not None:
                value = _isbn(value, 10 if key == "isbn_10" else 13)
                claims.append(_claim(provider, identity, ClaimKind.ISBN, value, local_claims))
        claims.sort(key=lambda value: (value.kind.value, value.value, value.reference))
        candidates.append(ProviderCandidate(identity, tuple(claims)))
    candidates.sort(key=lambda value: value.identity)
    if referenced_books != set(indexed_books):
        raise ValueError("provider unreferenced work")
    if len({value.identity for value in candidates}) != len(candidates):
        raise ValueError("provider identities duplicated")
    return tuple(candidates)


def _claim(provider: str, identity: str, kind: ClaimKind, value: str, local: tuple[LocalClaim, ...]) -> ProviderClaim:
    applicability = "ebook" if kind is ClaimKind.ISBN else "not_applicable"
    comparison, references = compare_claim(kind, value, tuple(item for item in local if item.applicability == applicability and item.role is None))
    reference = stable_reference(
        "provider",
        {"candidate": identity, "kind": kind.value, "provider": provider, "value": value},
    )
    return ProviderClaim(reference, kind, value, comparison, references, applicability)


def _closed(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("provider object invalid")
    return value


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or value.strip() != value or "\x00" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ValueError("provider text invalid")
    return value


def _isbn(value: object, length: int) -> str:
    text = _text(value, 32).replace("-", "").replace(" ", "").upper()
    if len(text) != length or any(not character.isdigit() and not (length == 10 and character == "X" and index == 9) for index, character in enumerate(text)):
        raise ValueError("provider ISBN invalid")
    if length == 13:
        expected = (10 - sum((1 if index % 2 == 0 else 3) * int(character) for index, character in enumerate(text[:12])) % 10) % 10
        if int(text[-1]) != expected:
            raise ValueError("provider ISBN invalid")
    else:
        total = sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(text))
        if total % 11:
            raise ValueError("provider ISBN invalid")
    return text


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("provider field duplicated")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ValueError("provider number invalid")


class _CandidateLimit(ValueError):
    pass


__all__ = ["HARDCOVER_URL", "HardcoverMetadataProvider"]
