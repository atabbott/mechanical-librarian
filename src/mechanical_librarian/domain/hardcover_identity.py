"""Private, transient Hardcover ISBN/title query values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.title_proposals import normalized_title_key


class PrivateQueryError(ValueError):
    """Value-free failure for a malformed private query."""


class IdentityDecisionOutcome(StrEnum):
    MATCHED = "matched"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE = "incompatible"
    MALFORMED = "malformed"


class TitleComparison(StrEnum):
    EQUAL = "equal"
    DIFFERENT = "different"
    NOT_COMPARED = "not_compared"


@dataclass(frozen=True, slots=True)
class IsbnIdentity:
    canonical_isbn_13: str
    equivalent_isbn_10: str | None


@dataclass(frozen=True, repr=False, slots=True)
class ProviderBookObservation:
    id: object
    canonical_id: object
    book_status_id: object
    state: object
    title: object
    parent_book_id: object
    compilation: object
    is_partial_book: object

    def __repr__(self) -> str:
        return "ProviderBookObservation(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class ProviderIdentityObservation:
    id: object
    book_id: object
    canonical_id: object
    isbn_10: object
    isbn_10_valid: object
    isbn_13: object
    isbn_13_valid: object
    isbns_match: object
    reading_format_id: object
    state: object
    compilation: object
    book: object

    def __repr__(self) -> str:
        return "ProviderIdentityObservation(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class BookIdentity:
    edition_id: int
    book_id: int
    isbn: IsbnIdentity
    provider_title: str

    def __repr__(self) -> str:
        return "BookIdentity(<redacted>)"


@dataclass(frozen=True, repr=False, slots=True)
class IdentityDecision:
    outcome: IdentityDecisionOutcome
    rows_validated: int
    aliases_collapsed: int
    comparison: TitleComparison
    identity: BookIdentity | None = None

    def __repr__(self) -> str:
        return (
            "IdentityDecision("
            f"outcome={self.outcome.value!r}, rows_validated={self.rows_validated}, "
            f"aliases_collapsed={self.aliases_collapsed}, "
            f"comparison={self.comparison.value!r}, identity=<redacted>)"
        )


_PRIVATE_QUERY_CONSTRUCTION = object()


@dataclass(frozen=True, repr=False, eq=False, init=False)
class PrivateIdentityQuery:
    """A validated query whose local title must remain transient."""

    canonical_isbn: str
    title: str

    def __init__(
        self,
        canonical_isbn: str,
        title: str,
        *,
        _construction: object | None = None,
    ) -> None:
        if _construction is not _PRIVATE_QUERY_CONSTRUCTION:
            raise PrivateQueryError("private query is invalid")
        object.__setattr__(self, "canonical_isbn", canonical_isbn)
        object.__setattr__(self, "title", title)

    @classmethod
    def _from_validated(cls, canonical_isbn: str, title: str) -> PrivateIdentityQuery:
        return cls(canonical_isbn, title, _construction=_PRIVATE_QUERY_CONSTRUCTION)

    def __repr__(self) -> str:
        return "PrivateIdentityQuery(<redacted>)"


_BIDI_CONTROLS = frozenset(
    {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)}
)


def validate_private_query(source: Mapping[str, object]) -> PrivateIdentityQuery:
    """Validate the exact private-query object without retaining projections."""

    if not isinstance(source, Mapping) or set(source) != {"isbn", "title"}:
        raise PrivateQueryError("private query is invalid")
    isbn = source.get("isbn")
    title = source.get("title")
    if type(isbn) is not str or type(title) is not str:
        raise PrivateQueryError("private query is invalid")
    canonical_isbn = isbn_identity(isbn).canonical_isbn_13
    if not _valid_title(title):
        raise PrivateQueryError("private query is invalid")
    return PrivateIdentityQuery._from_validated(canonical_isbn, title)


def isbn_identity(value: str) -> IsbnIdentity:
    try:
        value.encode("ascii", "strict")
    except UnicodeError:
        raise PrivateQueryError("private query is invalid") from None
    if len(value) == 13 and value.isdigit() and value.startswith(("978", "979")):
        checksum = sum(
            (1 if index % 2 == 0 else 3) * int(digit)
            for index, digit in enumerate(value)
        )
        if checksum % 10 == 0:
            return IsbnIdentity(
                value,
                _isbn_10_from_978(value) if value.startswith("978") else None,
            )
    elif (
        len(value) == 10
        and value[:9].isdigit()
        and (value[9].isdigit() or value[9] == "X")
    ):
        check = 10 if value[9] == "X" else int(value[9])
        checksum = sum(
            (10 - index) * int(digit)
            for index, digit in enumerate(value[:9])
        )
        if (checksum + check) % 11 == 0:
            stem = "978" + value[:9]
            total = sum(
                (1 if index % 2 == 0 else 3) * int(digit)
                for index, digit in enumerate(stem)
            )
            return IsbnIdentity(stem + str((-total) % 10), value)
    raise PrivateQueryError("private query is invalid")


def decide_book_identity(
    query: PrivateIdentityQuery,
    observations: tuple[ProviderIdentityObservation, ...],
) -> IdentityDecision:
    """Select one compatible identity without retaining or exporting its inputs."""

    if (
        type(query) is not PrivateIdentityQuery
        or type(observations) is not tuple
        or len(observations) > 4
        or any(type(row) is not ProviderIdentityObservation for row in observations)
    ):
        return _decision(IdentityDecisionOutcome.MALFORMED, 0)
    if not observations:
        return _decision(IdentityDecisionOutcome.NO_MATCH, 0)

    identity = isbn_identity(query.canonical_isbn)
    if any(not _compatible(row, identity) for row in observations):
        return _decision(IdentityDecisionOutcome.INCOMPATIBLE, len(observations))

    edition_graph = _alias_graph(
        observations,
        lambda row: row.id,
        lambda row: row.canonical_id,
        _semantic_projection,
    )
    book_graph = _alias_graph(
        observations,
        lambda row: row.book.id,
        lambda row: row.book.canonical_id,
        lambda row: _semantic_projection(row),
    )
    if edition_graph is None or book_graph is None:
        return _decision(IdentityDecisionOutcome.MALFORMED, len(observations))
    edition_roots, editions = edition_graph
    book_roots, books = book_graph
    if len(observations) == 4 or len(edition_roots) != 1 or len(book_roots) != 1:
        return _decision(IdentityDecisionOutcome.AMBIGUOUS, len(observations))

    edition_id = next(iter(edition_roots))
    book_id = next(iter(book_roots))
    provider_title = books[book_id].book.title
    selected = BookIdentity(edition_id, book_id, identity, provider_title)
    comparison = (
        TitleComparison.EQUAL
        if normalized_title_key(query.title) == normalized_title_key(provider_title)
        else TitleComparison.DIFFERENT
    )
    return IdentityDecision(
        IdentityDecisionOutcome.MATCHED,
        len(observations),
        len(observations) - 1,
        comparison,
        selected,
    )


def _decision(outcome: IdentityDecisionOutcome, rows: int) -> IdentityDecision:
    return IdentityDecision(outcome, rows, 0, TitleComparison.NOT_COMPARED)


def _isbn_10_from_978(value: str) -> str:
    stem = value[3:12]
    total = sum((10 - index) * int(digit) for index, digit in enumerate(stem))
    check = (-total) % 11
    return stem + ("X" if check == 10 else str(check))


def _valid_title(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        title_bytes = value.encode("utf-8", "strict")
    except UnicodeError:
        return False
    return (
        1 <= len(value) <= 512
        and len(title_bytes) <= 2_048
        and not any(_forbidden_title_character(ord(character)) for character in value)
        and bool(normalized_title_key(value))
    )


def _positive_int32(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _optional_positive_int32(value: object) -> bool:
    return value is None or _positive_int32(value)


def _compatible(row: ProviderIdentityObservation, query: IsbnIdentity) -> bool:
    if type(row.book) is not ProviderBookObservation:
        return False
    book = row.book
    if not (
        _positive_int32(row.id)
        and _positive_int32(row.book_id)
        and row.book_id == book.id
        and _optional_positive_int32(row.canonical_id)
        and type(row.reading_format_id) is int
        and row.reading_format_id == 4
        and row.state == "normalized"
        and type(row.compilation) is bool
        and row.compilation is False
        and _positive_int32(book.id)
        and _optional_positive_int32(book.canonical_id)
        and type(book.book_status_id) is int
        and book.book_status_id == 1
        and book.state in {"normalized", "processed"}
        and _valid_title(book.title)
        and book.parent_book_id is None
        and type(book.compilation) is bool
        and book.compilation is False
        and type(book.is_partial_book) is bool
        and book.is_partial_book is False
    ):
        return False
    if row.isbn_10 is None:
        if row.isbn_10_valid is not None:
            return False
    elif not (
        type(row.isbn_10) is str
        and row.isbn_10_valid is True
        and _same_isbn(row.isbn_10, query)
        and query.equivalent_isbn_10 is not None
    ):
        return False
    if row.isbn_13 is None:
        if row.isbn_13_valid is not None:
            return False
    elif not (
        type(row.isbn_13) is str
        and row.isbn_13_valid is True
        and row.isbn_13 == query.canonical_isbn_13
        and _same_isbn(row.isbn_13, query)
    ):
        return False
    if row.isbn_10 is None and row.isbn_13 is None:
        return False
    if row.isbn_10 is not None and row.isbn_13 is not None:
        return row.isbns_match is True
    return row.isbns_match is None or row.isbns_match is True


def _same_isbn(value: str, query: IsbnIdentity) -> bool:
    try:
        return isbn_identity(value).canonical_isbn_13 == query.canonical_isbn_13
    except PrivateQueryError:
        return False


def _semantic_projection(row: ProviderIdentityObservation) -> tuple[object, ...]:
    book = row.book
    return (
        isbn_identity(row.isbn_13 or row.isbn_10).canonical_isbn_13,
        row.reading_format_id,
        row.state,
        book.book_status_id,
        book.state,
        row.compilation,
        book.compilation,
        book.is_partial_book,
        book.parent_book_id,
        book.title,
    )


def _alias_graph(
    rows: tuple[ProviderIdentityObservation, ...],
    identifier,
    canonical_identifier,
    semantic_projection,
) -> tuple[set[int], dict[int, ProviderIdentityObservation]] | None:
    by_id: dict[int, ProviderIdentityObservation] = {}
    for row in rows:
        row_id = identifier(row)
        prior = by_id.get(row_id)
        if prior is not None and (
            canonical_identifier(prior) != canonical_identifier(row)
            or semantic_projection(prior) != semantic_projection(row)
        ):
            return None
        by_id[row_id] = row
    roots: set[int] = set()
    for row in rows:
        row_id = identifier(row)
        canonical_id = canonical_identifier(row)
        if canonical_id is None:
            roots.add(row_id)
            continue
        target = by_id.get(canonical_id)
        if (
            canonical_id == row_id
            or target is None
            or canonical_identifier(target) is not None
            or semantic_projection(row) != semantic_projection(target)
        ):
            return None
        roots.add(canonical_id)
    return roots, by_id


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


__all__ = [
    "BookIdentity",
    "IdentityDecision",
    "IdentityDecisionOutcome",
    "IsbnIdentity",
    "PrivateIdentityQuery",
    "PrivateQueryError",
    "ProviderBookObservation",
    "ProviderIdentityObservation",
    "TitleComparison",
    "decide_book_identity",
    "isbn_identity",
    "validate_private_query",
]
