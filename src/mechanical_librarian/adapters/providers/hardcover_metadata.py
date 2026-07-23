"""One-request, closed-envelope Hardcover metadata observation."""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from mechanical_librarian.domain.metadata_report import (
    ClaimKind, ClaimScope, FieldState, LocalClaim, MAX_CANDIDATES,
    MAX_CONTRIBUTIONS, MAX_DESCRIPTION_CHARACTERS, MAX_SCALAR_CHARACTERS,
    MAX_SERIES, MAX_TAGS, ProviderCandidate, ProviderClaim,
    ProviderObservation, ProviderStatus, canonical_json,
    comparison_links,
)
from mechanical_librarian.domain.evidence_bounds import EvidenceValueError, validate_projection_text, validate_role_projection


HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_SCHEMA_COMMIT = "2c03110845765c32af945ed550a942330c394a3e"
MAX_RESPONSE_BYTES = 262_144
_QUERY = (
    "query MetadataEvidenceByIsbn($isbns:[String!]!){editions(where:{_or:["
    f"{{isbn_13:{{_in:$isbns}}}},{{isbn_10:{{_in:$isbns}}}}]}},order_by:{{id:asc}},limit:{MAX_CANDIDATES + 1})"
    "{id book_id isbn_10 isbn_13 asin title subtitle release_date publisher{id name parent_id} language{id code2 code3 language} contributions(order_by:{id:asc},limit:17){id contribution author{id name}}} "
    "books:books(where:{editions:{_or:[{isbn_13:{_in:$isbns}},{isbn_10:{_in:$isbns}}]}},order_by:{id:asc},limit:6)"
    "{id title description contributions(order_by:{id:asc},limit:17){id contribution author{id name}} book_series(order_by:{id:asc},limit:17){id position series{id name}} taggings(order_by:{id:asc},limit:33){id tag{id tag}}}}}"
)


class HardcoverMetadataProvider:
    def __init__(self, name: str, client: httpx.Client, maximum_bytes: int) -> None:
        if type(maximum_bytes) is not int or isinstance(maximum_bytes, bool) or maximum_bytes < 1:
            raise ValueError("provider response bound is invalid")
        self._name, self._client = name, client
        self._maximum_bytes = min(maximum_bytes, MAX_RESPONSE_BYTES)
        self._called = False

    def observe(self, local_claims: tuple[LocalClaim, ...]) -> ProviderObservation:
        if self._called:
            return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE)
        self._called = True
        usable = tuple(sorted((claim for claim in local_claims if claim.kind is ClaimKind.ISBN and claim.canonical is not None and claim.applicability in {"ebook", "unknown"} and claim.basis == "opening_matter" and claim.rear_provenance is None), key=lambda claim: (claim.canonical or "", claim.reference)))
        if not usable:
            self._client.close()
            return ProviderObservation(self._name, ProviderStatus.INSUFFICIENT_INPUT)
        references = tuple(sorted(claim.reference for claim in usable))
        body = {"operationName": "MetadataEvidenceByIsbn", "query": _QUERY, "variables": {"isbns": sorted({claim.canonical for claim in usable if claim.canonical is not None})}}
        try:
            with self._client, self._client.stream("POST", HARDCOVER_URL, content=canonical_json(body).encode(), headers={"Accept": "application/json", "Content-Type": "application/json", "Accept-Encoding": "identity"}) as response:
                if response.status_code == 401:
                    return ProviderObservation(self._name, ProviderStatus.AUTHENTICATION, references)
                if response.status_code == 403:
                    return ProviderObservation(self._name, ProviderStatus.AUTHORIZATION, references)
                if response.status_code == 429:
                    return ProviderObservation(self._name, ProviderStatus.RATE_LIMITED, references)
                if response.status_code != 200:
                    return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE, references)
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    return ProviderObservation(self._name, ProviderStatus.MALFORMED, references)
                payload = _body(response, self._maximum_bytes)
        except _ResponseOversized:
            return ProviderObservation(self._name, ProviderStatus.OVERSIZED, references)
        except (httpx.HTTPError, OSError, ValueError):
            return ProviderObservation(self._name, ProviderStatus.UNAVAILABLE, references)
        try:
            candidates = _parse_candidates(payload, self._name, local_claims)
        except _CandidateLimit:
            return ProviderObservation(self._name, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, references)
        except (ValueError, TypeError, UnicodeError, RecursionError, json.JSONDecodeError):
            return ProviderObservation(self._name, ProviderStatus.MALFORMED, references)
        return ProviderObservation(self._name, ProviderStatus.CANDIDATES if candidates else ProviderStatus.NO_MATCH, references, candidates)


def _body(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > maximum:
            raise _ResponseOversized
        chunks.append(chunk)
    if not chunks:
        raise ValueError("provider response empty")
    return b"".join(chunks)


def _parse_candidates(payload: bytes, provider: str, local_claims: tuple[LocalClaim, ...]) -> tuple[ProviderCandidate, ...]:
    source = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=_unique_object, parse_constant=_reject_number, parse_float=Decimal)
    root = _closed(source, {"data"})
    data = _closed(root["data"], {"editions", "books"})
    rows, books = data["editions"], data["books"]
    if type(rows) is not list or type(books) is not list or len(rows) > MAX_CANDIDATES:
        raise _CandidateLimit if type(rows) is list and len(rows) > MAX_CANDIDATES else ValueError("provider rows invalid")
    indexed = {_identifier(_closed(book, {"id", "title", "description", "contributions", "book_series", "taggings"})["id"]): _closed(book, {"id", "title", "description", "contributions", "book_series", "taggings"}) for book in books}
    if len(indexed) != len(books):
        raise ValueError("provider work invalid")
    candidates: list[ProviderCandidate] = []
    used: set[int] = set()
    for row_value in rows:
        row = _closed(row_value, {"id", "book_id", "isbn_10", "isbn_13", "asin", "title", "subtitle", "release_date", "publisher", "language", "contributions"})
        edition_id, book_id = _identifier(row["id"]), _identifier(row["book_id"])
        if book_id not in indexed:
            raise ValueError("provider unreferenced work")
        used.add(book_id)
        identity = f"hardcover-edition-{edition_id:010d}"
        claims = _edition_claims(provider, identity, row, local_claims)
        claims.extend(_work_claims(provider, identity, indexed[book_id], local_claims, len(claims)))
        candidates.append(ProviderCandidate(identity, tuple(claims)))
    if used != set(indexed) or len({candidate.identity for candidate in candidates}) != len(candidates):
        raise ValueError("provider join invalid")
    return tuple(sorted(candidates, key=lambda candidate: candidate.identity))


def _edition_claims(provider: str, identity: str, row: dict[str, Any], local: tuple[LocalClaim, ...]) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    def add(kind: ClaimKind, value: object, scope: ClaimScope = ClaimScope.EDITION, applicability: str = "not_applicable") -> None:
        claims.append(_claim(provider, identity, kind, value, scope, applicability, None, local, len(claims)))
    for key, kind in (("isbn_10", ClaimKind.ISBN), ("isbn_13", ClaimKind.ISBN), ("asin", ClaimKind.ASIN), ("title", ClaimKind.TITLE), ("subtitle", ClaimKind.SUBTITLE), ("release_date", ClaimKind.PUBLICATION_DATE)):
        add(kind, row[key], ClaimScope.EDITION, "ebook" if kind is ClaimKind.ISBN else "not_applicable")
    publisher = _closed_or_none(row["publisher"], {"id", "name", "parent_id"})
    add(ClaimKind.PUBLISHER, None if publisher is None else publisher["name"])
    claims.append(_state_claim(provider, identity, ClaimKind.IMPRINT, FieldState.UNSUPPORTED, ClaimScope.EDITION, local, len(claims)))
    language = _closed_or_none(row["language"], {"id", "code2", "code3", "language"})
    add(ClaimKind.LANGUAGE, None if language is None else (language["code2"] or language["code3"]))
    claims.extend(_contributions(provider, identity, row["contributions"], ClaimScope.EDITION, local, len(claims)))
    return claims


def _work_claims(provider: str, identity: str, book: dict[str, Any], local: tuple[LocalClaim, ...], offset: int) -> list[ProviderClaim]:
    claims = [_claim(provider, identity, ClaimKind.TITLE, book["title"], ClaimScope.WORK, "not_applicable", None, local, offset), _claim(provider, identity, ClaimKind.DESCRIPTION, book["description"], ClaimScope.WORK, "not_applicable", None, local, offset + 1)]
    claims.extend(_contributions(provider, identity, book["contributions"], ClaimScope.WORK, local, len(claims) + offset))
    series, series_overflow = _bounded_list(book["book_series"], MAX_SERIES, {"id", "position", "series"})
    if series_overflow:
        claims.extend((_state_claim(provider, identity, ClaimKind.SERIES, FieldState.OVERSIZED, ClaimScope.WORK, local, len(claims) + offset), _state_claim(provider, identity, ClaimKind.SERIES_SEQUENCE, FieldState.OVERSIZED, ClaimScope.WORK, local, len(claims) + offset + 1)))
        series = []
    for item in series:
        series = _closed_or_none(item["series"], {"id", "name"})
        claims.append(_claim(provider, identity, ClaimKind.SERIES, None if series is None else series["name"], ClaimScope.WORK, "not_applicable", None, local, len(claims) + offset))
        claims.append(_claim(provider, identity, ClaimKind.SERIES_SEQUENCE, item["position"], ClaimScope.WORK, "not_applicable", None, local, len(claims) + offset))
    taggings, taggings_overflow = _bounded_list(book["taggings"], MAX_TAGS, {"id", "tag"})
    if taggings_overflow:
        claims.append(_state_claim(provider, identity, ClaimKind.TAG, FieldState.OVERSIZED, ClaimScope.WORK, local, len(claims) + offset))
        taggings = []
    for item in taggings:
        tag = _closed_or_none(item["tag"], {"id", "tag"})
        claims.append(_claim(provider, identity, ClaimKind.TAG, None if tag is None else tag["tag"], ClaimScope.WORK, "not_applicable", None, local, len(claims) + offset))
    claims.append(_state_claim(provider, identity, ClaimKind.GENRE, FieldState.UNSUPPORTED, ClaimScope.WORK, local, len(claims) + offset))
    return claims


def _contributions(provider: str, identity: str, value: object, scope: ClaimScope, local: tuple[LocalClaim, ...], offset: int) -> list[ProviderClaim]:
    claims: list[ProviderClaim] = []
    contributions, overflow = _bounded_list(value, MAX_CONTRIBUTIONS, {"id", "contribution", "author"})
    if overflow:
        # The admitted max+1 probe preserves every role family present without
        # retaining an unbounded contribution list.
        kinds: set[ClaimKind] = set()
        for probe in contributions:
            role = probe["contribution"]
            try:
                role = validate_role_projection(role).casefold()
            except EvidenceValueError:
                raise ValueError("provider contribution role invalid") from None
            kinds.add(ClaimKind.CREATOR if role == "author" else ClaimKind.CONTRIBUTOR)
        return [_state_claim(provider, identity, kind, FieldState.OVERSIZED, scope, local, offset + index) for index, kind in enumerate(sorted(kinds, key=lambda value: value.value))]
    for item in contributions:
        author = _closed_or_none(item["author"], {"id", "name"})
        try:
            role = validate_role_projection(item["contribution"]).casefold()
        except EvidenceValueError:
            claims.append(_state_claim(provider, identity, ClaimKind.CONTRIBUTOR, FieldState.MALFORMED, scope, local, offset + len(claims)))
            continue
        kind = ClaimKind.CREATOR if role == "author" else ClaimKind.CONTRIBUTOR
        claims.append(_claim(provider, identity, kind, None if author is None else author["name"], scope, "not_applicable", "author" if kind is ClaimKind.CREATOR else role, local, offset + len(claims)))
    return claims


def _claim(provider: str, identity: str, kind: ClaimKind, value: object, scope: ClaimScope, applicability: str, role: str | None, local: tuple[LocalClaim, ...], raw_order: int) -> ProviderClaim:
    state, text = _field(kind, value)
    bare = ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=text, state=state, scope=scope, applicability=applicability, role=role, raw_order=raw_order)
    return ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=text, state=state, scope=scope, applicability=applicability, role=role, raw_order=raw_order, links=comparison_links(bare, local))


def _state_claim(provider: str, identity: str, kind: ClaimKind, state: FieldState, scope: ClaimScope, local: tuple[LocalClaim, ...], raw_order: int) -> ProviderClaim:
    bare = ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=None, state=state, scope=scope, applicability="not_applicable", raw_order=raw_order)
    return ProviderClaim.create(provider=provider, candidate=identity, kind=kind, value=None, state=state, scope=scope, applicability="not_applicable", raw_order=raw_order, links=comparison_links(bare, local))


def _field(kind: ClaimKind, value: object) -> tuple[FieldState, str | None]:
    if value is None:
        return FieldState.ABSENT, None
    maximum = MAX_DESCRIPTION_CHARACTERS if kind is ClaimKind.DESCRIPTION else MAX_SCALAR_CHARACTERS
    try:
        if kind is ClaimKind.SERIES_SEQUENCE and type(value) in {Decimal, int} and type(value) is not bool:
            return FieldState.VALID, _position(value)
        text = validate_projection_text(value, "provider claim value", maximum)
        if kind is ClaimKind.ISBN:
            text = _isbn(text, len(text.replace("-", "").replace(" ", "")))
        if kind is ClaimKind.ASIN and (len(text) != 10 or not text.isascii() or not text.isalnum()):
            raise ValueError("provider ASIN invalid")
        if kind is ClaimKind.PUBLICATION_DATE:
            date.fromisoformat(text)
        if kind is ClaimKind.SERIES_SEQUENCE:
            text = _position(text)
        return FieldState.VALID, text
    except (ValueError, EvidenceValueError):
        return (FieldState.OVERSIZED if type(value) is str and len(value) > maximum else FieldState.MALFORMED), None


def _bounded_list(value: object, maximum: int, keys: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if type(value) is not list:
        raise ValueError("provider list invalid")
    values = [_closed(item, keys) for item in value]
    return values, len(values) > maximum


def _closed(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("provider object invalid")
    return value


def _closed_or_none(value: object, keys: set[str]) -> dict[str, Any] | None:
    return None if value is None else _closed(value, keys)


def _identifier(value: object) -> int:
    if type(value) is not int or type(value) is bool or not 1 <= value <= 2_147_483_647:
        raise ValueError("provider identity invalid")
    return value


def _text(value: object, maximum: int) -> str:
    if type(value) is not str or not value or len(value) > maximum or value.strip() != value or "\x00" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ValueError("provider text invalid")
    return value


def _isbn(value: str, length: int) -> str:
    text = _text(value, 32).replace("-", "").replace(" ", "").upper()
    if length not in {10, 13} or len(text) != length or any(not character.isdigit() and not (length == 10 and character == "X" and index == 9) for index, character in enumerate(text)):
        raise ValueError("provider ISBN invalid")
    if length == 13 and int(text[-1]) != (10 - sum((1 if index % 2 == 0 else 3) * int(character) for index, character in enumerate(text[:12])) % 10) % 10:
        raise ValueError("provider ISBN invalid")
    if length == 10 and sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(text)) % 11:
        raise ValueError("provider ISBN invalid")
    return text


def _position(value: int | str | Decimal) -> str:
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValueError("provider position invalid") from error
    if not decimal.is_finite() or decimal.adjusted() > 12 or decimal.adjusted() < -MAX_SCALAR_CHARACTERS:
        raise ValueError("provider position invalid")
    text = format(decimal.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if len(text) > MAX_SCALAR_CHARACTERS:
        raise ValueError("provider position invalid")
    return text or "0"


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


class _ResponseOversized(ValueError):
    pass


__all__ = ["HARDCOVER_SCHEMA_COMMIT", "HARDCOVER_URL", "HardcoverMetadataProvider"]
