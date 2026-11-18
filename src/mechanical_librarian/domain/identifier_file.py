"""An identifiers file: the owner's list of works and their ISBN or ASIN (#1478).

The file is UTF-8 text. Its first non-blank line is `item<TAB>identifier` or
`path<TAB>identifier`, and each later non-blank line names one work, by its
Audiobookshelf item ID or its path within the library, then a tab and the
identifier: an ISBN for an ebook, an ASIN for an audiobook.

Parsing is one pass over the decoded text, and matching walks a reverse trie
of the library's item paths once per row, so the work is linear in the size of
the file. A file-level fault refuses the whole file with a fixed code; a row
fault refuses that row only. Nothing here keeps a row's text: the result holds
row numbers, reasons, the matched item and its canonical identifier.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mechanical_librarian.domain.owner_link import (
    OwnerLinkError,
    OwnerLinkScope,
    owner_asin,
    owner_isbn,
)

MAX_FILE_BYTES: Final = 1_048_576
MAX_ROWS: Final = 5_000
MAX_ROW_BYTES: Final = 4_096
MAX_DISPLAY_CHARACTERS: Final = 512

# File refusals, in the order they are checked (S2). The size refusal is the
# body limit's, before any byte reaches this module.
FILE_REFUSALS: Final = (
    "file_encoding",
    "file_control_character",
    "file_header",
    "file_too_many_rows",
    "file_no_rows",
)
# The counts a preview answers; they add up to its rows.
COUNT_KEYS: Final = (
    "will_link",
    "already_linked",
    "no_item",
    "ambiguous",
    "invalid_identifier",
    "wrong_medium",
    "repeated",
    "conflicting",
    "unreadable",
)
_COUNT_OF_REASON: Final = {
    "row_too_long": "unreadable",
    "row_shape": "unreadable",
    "no_item": "no_item",
    "ambiguous": "ambiguous",
    "invalid_isbn": "invalid_identifier",
    "invalid_asin": "invalid_identifier",
    "asin_for_ebook": "wrong_medium",
    "isbn_for_audiobook": "wrong_medium",
    "conflicting": "conflicting",
    "repeated": "repeated",
    "already_linked": "already_linked",
}

# An ISBN as the owner may write it: digits with single hyphens between them,
# ten (the last may be X) or thirteen. Any other character makes it invalid.
_ISBN_SHAPE = re.compile(r"[0-9](?:-?[0-9]){8}-?[0-9X]|[0-9](?:-?[0-9]){12}")
_ASIN_SHAPE = re.compile(r"[0-9A-Za-z]{10}")
# Unicode Cc (U+0000-001F, U+007F-009F) other than TAB, LF or the CR of a CRLF.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\r(?!\n)")


class IdentifierFileError(ValueError):
    """The whole file is refused, with one of `FILE_REFUSALS` as its code."""

    def __init__(self, code: str) -> None:
        if code not in FILE_REFUSALS:
            raise ValueError("identifier file refusal is unknown")
        self.code = code
        super().__init__(code)


class IdentifierKey(StrEnum):
    """What the file names each work by, from its header."""

    ITEM = "item"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class IdentifierCandidate:
    """One eligible catalogue entry a row may name.

    `item_path` is the item's own path within its library, or `None` when
    the catalogue kept none; `medium` is `ebook` or `audiobook`.
    """

    item_id: str
    item_path: str | None
    medium: str
    title: str
    authors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WillLink:
    """A row that will become a file link: its item, scope and identifier."""

    row: int
    item_id: str
    medium: str
    scope: OwnerLinkScope
    identifier: str
    title: str
    authors: tuple[str, ...]

    @property
    def identifier_kind(self) -> str:
        return "isbn" if self.scope is OwnerLinkScope.EDITION else "asin"


@dataclass(frozen=True, slots=True)
class RefusedRow:
    row: int
    reason: str


@dataclass(frozen=True, slots=True)
class IdentifierFileRows:
    """What a file's rows came to, row numbers and reasons only."""

    rows: int
    counts: Mapping[str, int]
    will_link: tuple[WillLink, ...]
    refused: tuple[RefusedRow, ...]


@dataclass(frozen=True, slots=True)
class FileRow:
    """One non-blank row: its physical line number and its two fields, or
    `reason` when the row could not be read (its fields are then empty)."""

    number: int
    key: str
    identifier: str
    reason: str | None


def parse_identifier_file(data: bytes) -> tuple[IdentifierKey, tuple[FileRow, ...]]:
    """The header's key and each non-blank row, or the file's refusal (S2, S3)."""
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise IdentifierFileError("file_encoding") from error
    text = text.removeprefix("﻿")
    if _CONTROL_CHARACTER.search(text) is not None:
        raise IdentifierFileError("file_control_character")
    key: IdentifierKey | None = None
    rows: list[FileRow] = []
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.removesuffix("\r")
        if not line:
            continue
        if key is None:
            if line not in {"item\tidentifier", "path\tidentifier"}:
                raise IdentifierFileError("file_header")
            key = IdentifierKey(line.split("\t", 1)[0])
            continue
        if len(rows) == MAX_ROWS:
            raise IdentifierFileError("file_too_many_rows")
        rows.append(_parse_row(number, line))
    if key is None:
        raise IdentifierFileError("file_header")
    if not rows:
        raise IdentifierFileError("file_no_rows")
    return key, tuple(rows)


def _parse_row(number: int, line: str) -> FileRow:
    if len(line.encode("utf-8")) > MAX_ROW_BYTES:
        return FileRow(number, "", "", "row_too_long")
    fields = line.split("\t")
    if len(fields) != 2 or not fields[0] or not fields[1]:
        return FileRow(number, "", "", "row_shape")
    return FileRow(number, fields[0], fields[1], None)


class PathIndex:
    """A reverse trie of item paths: each row walks it once from its last segment.

    A row names item P when the row's trailing whole segments equal all of P's
    segments, compared case-sensitively after NFC normalisation. Item paths
    are split on `/`; a row is split on `/` or `\\`, and empty segments are
    dropped from both.
    """

    __slots__ = ("_root",)

    def __init__(self, candidates: Iterable[IdentifierCandidate]) -> None:
        self._root: _Node = _Node()
        for candidate in candidates:
            if candidate.item_path is None:
                continue
            segments = _segments(candidate.item_path, "/")
            if not segments:
                continue
            node = self._root
            for segment in reversed(segments):
                node = node.children.setdefault(segment, _Node())
            node.items.add(candidate.item_id)

    def find(self, path: str) -> frozenset[str]:
        """Every item the path names; more than one is `ambiguous`."""
        found: set[str] = set()
        node = self._root
        for segment in reversed(_segments(path, "/\\")):
            following = node.children.get(segment)
            if following is None:
                break
            node = following
            found |= node.items
            if len(found) > 1:
                break
        return frozenset(found)


class _Node:
    __slots__ = ("children", "items")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.items: set[str] = set()


def _segments(path: str, separators: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", path)
    for separator in separators[1:]:
        normalized = normalized.replace(separator, separators[0])
    return [segment for segment in normalized.split(separators[0]) if segment]


def classify_identifier(medium: str, value: str) -> tuple[OwnerLinkScope, str] | str:
    """A row's identifier as its scope and canonical value, or its row reason (S5).

    An ebook takes an ISBN, with single hyphens between digits allowed, and
    keeps its ISBN-13; an audiobook takes ten letters and digits, upper-cased,
    including an ISBN-10-shaped ASIN (ADR-0061). The value is never trimmed.
    """
    isbn = _isbn(value)
    if medium == "ebook":
        if isbn is not None:
            return (OwnerLinkScope.EDITION, isbn) if isbn else "invalid_isbn"
        return "asin_for_ebook" if _ASIN_SHAPE.fullmatch(value) else "invalid_isbn"
    if _ASIN_SHAPE.fullmatch(value) is not None:
        try:
            return OwnerLinkScope.RECORDING, owner_asin(value.upper())
        except OwnerLinkError:
            return "invalid_asin"
    return "isbn_for_audiobook" if isbn else "invalid_asin"


def _isbn(value: str) -> str | None:
    """`None` when not ISBN-shaped, `""` when its checksum fails, else ISBN-13."""
    if _ISBN_SHAPE.fullmatch(value) is None:
        return None
    try:
        return owner_isbn(value.replace("-", ""))
    except OwnerLinkError:
        return ""


def display_text(value: str) -> str:
    """A title cut at 512 characters, the last one an ellipsis."""
    if len(value) <= MAX_DISPLAY_CHARACTERS:
        return value
    return value[: MAX_DISPLAY_CHARACTERS - 1] + "…"


def display_authors(authors: tuple[str, ...]) -> tuple[str, ...]:
    """Authors kept while their `, `-joined line fits 512 characters (S6)."""
    kept: list[str] = []
    used = 0
    for author in authors:
        separator = 2 if kept else 0
        if used + separator + len(author) <= MAX_DISPLAY_CHARACTERS:
            kept.append(author)
            used += separator + len(author)
            continue
        room = MAX_DISPLAY_CHARACTERS - used - separator
        if room >= 2:
            kept.append(author[: room - 1] + "…")
        break
    return tuple(kept)


def classify_identifier_file(
    data: bytes,
    candidates: Iterable[IdentifierCandidate],
    linked: frozenset[str],
) -> IdentifierFileRows:
    """Every row of an identifiers file decided against one library (S3).

    `candidates` are the library's current eligible entries and `linked` the
    item IDs that have a current link of any source. Raises
    `IdentifierFileError` when the whole file is refused.
    """
    key, rows = parse_identifier_file(data)
    entries = tuple(candidates)
    by_item = {entry.item_id: entry for entry in entries}
    index = PathIndex(entries) if key is IdentifierKey.PATH else None
    reasons: dict[int, str] = {}
    usable: list[tuple[FileRow, IdentifierCandidate, OwnerLinkScope, str]] = []
    for row in rows:
        if row.reason is not None:
            reasons[row.number] = row.reason
            continue
        resolved = _resolve(row.key, by_item, index)
        if isinstance(resolved, str):
            reasons[row.number] = resolved
            continue
        identified = classify_identifier(resolved.medium, row.identifier)
        if isinstance(identified, str):
            reasons[row.number] = identified
            continue
        usable.append((row, resolved, *identified))
    return _decide_usable(rows, reasons, usable, linked)


def _resolve(
    key: str,
    by_item: Mapping[str, IdentifierCandidate],
    index: PathIndex | None,
) -> IdentifierCandidate | str:
    if index is None:
        entry = by_item.get(key)
        return "no_item" if entry is None else entry
    found = index.find(key)
    if not found:
        return "no_item"
    if len(found) > 1:
        return "ambiguous"
    return by_item[next(iter(found))]


def _decide_usable(
    rows: tuple[FileRow, ...],
    reasons: dict[int, str],
    usable: list[tuple[FileRow, IdentifierCandidate, OwnerLinkScope, str]],
    linked: frozenset[str],
) -> IdentifierFileRows:
    """Across rows: `conflicting`, then `repeated`, `already_linked`, `will_link`."""
    identifiers: dict[str, set[str]] = {}
    for _row, entry, _scope, identifier in usable:
        identifiers.setdefault(entry.item_id, set()).add(identifier)
    seen: set[str] = set()
    will_link: list[WillLink] = []
    for row, entry, scope, identifier in usable:
        if len(identifiers[entry.item_id]) > 1:
            reasons[row.number] = "conflicting"
        elif entry.item_id in seen:
            reasons[row.number] = "repeated"
        elif entry.item_id in linked:
            seen.add(entry.item_id)
            reasons[row.number] = "already_linked"
        else:
            seen.add(entry.item_id)
            will_link.append(
                WillLink(
                    row.number,
                    entry.item_id,
                    entry.medium,
                    scope,
                    identifier,
                    display_text(entry.title),
                    display_authors(entry.authors),
                )
            )
    counted = Counter(_COUNT_OF_REASON[reason] for reason in reasons.values())
    counts = {name: counted.get(name, 0) for name in COUNT_KEYS}
    counts["will_link"] = len(will_link)
    refused = tuple(
        RefusedRow(row.number, reasons[row.number])
        for row in rows
        if row.number in reasons
    )
    return IdentifierFileRows(len(rows), counts, tuple(will_link), refused)
