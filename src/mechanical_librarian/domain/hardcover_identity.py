"""Private, transient Hardcover ISBN/title query values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mechanical_librarian.domain.title_proposals import normalized_title_key


class PrivateQueryError(ValueError):
    """Value-free failure for a malformed private query."""


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
    canonical_isbn = _canonical_isbn(isbn)
    try:
        title_bytes = title.encode("utf-8", "strict")
    except UnicodeError:
        raise PrivateQueryError("private query is invalid") from None
    if (
        not 1 <= len(title) <= 512
        or len(title_bytes) > 2_048
        or any(_forbidden_title_character(ord(character)) for character in title)
        or not normalized_title_key(title)
    ):
        raise PrivateQueryError("private query is invalid")
    return PrivateIdentityQuery._from_validated(canonical_isbn, title)


def _canonical_isbn(value: str) -> str:
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
            return value
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
            return stem + str((-total) % 10)
    raise PrivateQueryError("private query is invalid")


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


__all__ = ["PrivateIdentityQuery", "PrivateQueryError", "validate_private_query"]
