"""The identifiers file grammar, bounds, matching and identifiers (#1478, S2-S6)."""

from __future__ import annotations

import time
import unicodedata

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mechanical_librarian.domain.identifier_file import (
    COUNT_KEYS,
    MAX_ROW_BYTES,
    MAX_ROWS,
    IdentifierCandidate,
    IdentifierFileError,
    IdentifierKey,
    PathIndex,
    classify_identifier,
    classify_identifier_file,
    display_authors,
    display_text,
    parse_identifier_file,
)
from mechanical_librarian.domain.owner_link import OwnerLinkScope

EBOOK = IdentifierCandidate(
    "item-ebook",
    "Fantasy/The Hollow Crown",
    "ebook",
    "The Hollow Crown",
    ("A. Writer",),
)
AUDIO = IdentifierCandidate(
    "item-audio",
    "Fantasy/The Hollow Crown (Audio)",
    "audiobook",
    "The Hollow Crown",
    ("A. Writer", "N. Reader"),
)
ISBN13 = "9780306406157"
ISBN10 = "0306406152"


def _refusal(data: bytes) -> str:
    with pytest.raises(IdentifierFileError) as caught:
        parse_identifier_file(data)
    return caught.value.code


def _classify(text: str, candidates=(EBOOK, AUDIO), linked=frozenset()):
    return classify_identifier_file(text.encode(), candidates, linked)


def _reasons(result) -> dict[int, str]:
    return {row.row: row.reason for row in result.refused}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"item\tidentifier\n\xff\n", "file_encoding"),
        (b"item\tidentifier\nx\x00\t1\n", "file_control_character"),
        (b"item\tidentifier\rx\t1\n", "file_control_character"),
        ("item\tidentifier\nx\u0085\t1\n".encode(), "file_control_character"),
        (b"item\tidentifier\nx\x7f\t1\n", "file_control_character"),
        (b"", "file_header"),
        (b"\xef\xbb\xbf", "file_header"),
        (b"\n\n", "file_header"),
        (b"Item\tidentifier\nx\t1\n", "file_header"),
        (b"item\tidentifier \nx\t1\n", "file_header"),
        (b"item,identifier\nx,1\n", "file_header"),
        (b"item\tidentifier\n", "file_no_rows"),
        (b"\xef\xbb\xbfpath\tidentifier\r\n\r\n", "file_no_rows"),
    ],
)
def test_file_refusals_have_fixed_codes(data: bytes, code: str) -> None:
    assert _refusal(data) == code


def test_refusals_are_checked_in_their_order() -> None:
    # Encoding before control characters before the header.
    assert _refusal(b"bad\x00\xff") == "file_encoding"
    assert _refusal(b"bad\x00header") == "file_control_character"
    # Too many rows before no rows is moot; too many before any row reason.
    rows = "".join(f"x{i}\tnot-an-isbn\n" for i in range(MAX_ROWS + 1))
    assert _refusal(("item\tidentifier\n" + rows).encode()) == "file_too_many_rows"


def test_row_count_is_bounded_at_exactly_five_thousand() -> None:
    rows = "\n\n".join(f"x{i}\t{ISBN13}" for i in range(MAX_ROWS))
    key, parsed = parse_identifier_file(("item\tidentifier\n" + rows).encode())
    assert key is IdentifierKey.ITEM
    assert len(parsed) == MAX_ROWS


def test_bom_is_stripped_and_rows_keep_physical_line_numbers() -> None:
    data = "﻿\npath\tidentifier\r\n\r\nA\t1\n\nB\t2\r\nC\t3".encode()
    key, rows = parse_identifier_file(data)
    assert key is IdentifierKey.PATH
    assert [(row.number, row.key, row.identifier) for row in rows] == [
        (4, "A", "1"),
        (6, "B", "2"),
        (7, "C", "3"),
    ]


def test_row_length_is_bounded_in_utf8_bytes_excluding_the_line_end() -> None:
    fits = "é" * ((MAX_ROW_BYTES - 2) // 2) + "\t1"
    assert len(fits.encode()) == MAX_ROW_BYTES
    over = "x" + fits
    _, rows = parse_identifier_file(
        f"item\tidentifier\r\n{fits}\r\n{over}\r\n".encode()
    )
    assert [row.reason for row in rows] == [None, "row_too_long"]


@pytest.mark.parametrize(
    "line",
    ["only-one-field", "a\tb\tc", "\t" + ISBN13, "item-ebook\t", " \t "[:1] + "\t\t"],
)
def test_a_malformed_row_refuses_that_row_only(line: str) -> None:
    result = _classify(f"item\tidentifier\n{line}\nitem-ebook\t{ISBN13}\n")
    assert _reasons(result) == {2: "row_shape"}
    assert [row.row for row in result.will_link] == [3]
    assert result.counts["unreadable"] == 1


def test_fields_are_never_trimmed() -> None:
    result = _classify(
        f"item\tidentifier\nitem-ebook \t{ISBN13}\nitem-audio\t B00TEST001\n"
    )
    assert _reasons(result) == {2: "no_item", 3: "invalid_asin"}


def test_item_rows_match_the_item_id_exactly() -> None:
    result = _classify(
        f"item\tidentifier\nITEM-EBOOK\t{ISBN13}\nitem-ebook\t{ISBN13}\n"
    )
    assert _reasons(result) == {2: "no_item"}
    assert result.will_link[0].item_id == "item-ebook"


def test_path_index_matches_whole_trailing_segments() -> None:
    title = IdentifierCandidate("a", "Title", "ebook", "T", ())
    nested = IdentifierCandidate("b", "Author/Title", "ebook", "T", ())
    deep = IdentifierCandidate("c", "Shelf/Other Book", "ebook", "T", ())
    index = PathIndex((title, nested, deep))
    assert index.find("Other Book") == frozenset()
    assert index.find("Shelf/Other Book") == {"c"}
    assert index.find("/mnt/books/Shelf/Other Book/") == {"c"}
    assert index.find("Shelf\\Other Book") == {"c"}
    assert index.find("itle") == frozenset()
    assert index.find("shelf/other book") == frozenset()
    assert index.find("Title") == {"a"}
    assert index.find("Author/Title") == {"a", "b"}


def test_path_rows_are_nfc_normalised_on_both_sides() -> None:
    composed = "Café/Book"
    decomposed = unicodedata.normalize("NFD", composed)
    candidate = IdentifierCandidate("x", decomposed, "ebook", "T", ())
    assert PathIndex((candidate,)).find(composed) == {"x"}
    candidate = IdentifierCandidate("x", composed, "ebook", "T", ())
    assert PathIndex((candidate,)).find(decomposed) == {"x"}


def test_ambiguous_and_unmatched_path_rows() -> None:
    title = IdentifierCandidate("a", "Title", "ebook", "T", ())
    nested = IdentifierCandidate("b", "Author/Title", "ebook", "T", ())
    no_path = IdentifierCandidate("c", None, "ebook", "T", ())
    result = _classify(
        f"path\tidentifier\nAuthor/Title\t{ISBN13}\nNobody\t{ISBN13}\nc\t{ISBN13}\nTitle\t{ISBN13}\n",
        (title, nested, no_path),
    )
    assert _reasons(result) == {2: "ambiguous", 3: "no_item", 4: "no_item"}
    assert [(row.row, row.item_id) for row in result.will_link] == [(5, "a")]


@pytest.mark.parametrize(
    ("medium", "value", "expected"),
    [
        ("ebook", ISBN13, (OwnerLinkScope.EDITION, ISBN13)),
        ("ebook", "978-0-306-40615-7", (OwnerLinkScope.EDITION, ISBN13)),
        ("ebook", ISBN10, (OwnerLinkScope.EDITION, ISBN13)),
        ("ebook", "0-306-40615-2", (OwnerLinkScope.EDITION, ISBN13)),
        ("ebook", "080442957X", (OwnerLinkScope.EDITION, "9780804429573")),
        ("ebook", "9780306406158", "invalid_isbn"),
        ("ebook", "978--0306406157", "invalid_isbn"),
        ("ebook", "-9780306406157", "invalid_isbn"),
        ("ebook", "978 0306406157", "invalid_isbn"),
        ("ebook", "B00TEST001", "asin_for_ebook"),
        ("ebook", "080442957x", "asin_for_ebook"),
        ("ebook", "nonsense", "invalid_isbn"),
        ("audiobook", "B00TEST001", (OwnerLinkScope.RECORDING, "B00TEST001")),
        ("audiobook", "b00test001", (OwnerLinkScope.RECORDING, "B00TEST001")),
        ("audiobook", ISBN10, (OwnerLinkScope.RECORDING, ISBN10)),
        ("audiobook", ISBN13, "isbn_for_audiobook"),
        ("audiobook", "978-0-306-40615-7", "isbn_for_audiobook"),
        ("audiobook", "9780306406158", "invalid_asin"),
        ("audiobook", "B00TEST00", "invalid_asin"),
        ("audiobook", "B00TEST001 ", "invalid_asin"),
        ("audiobook", "B00-TEST01", "invalid_asin"),
    ],
)
def test_identifiers_are_classified_by_medium(
    medium: str, value: str, expected
) -> None:
    assert classify_identifier(medium, value) == expected


def test_conflicting_then_repeated_then_already_linked() -> None:
    third = IdentifierCandidate("item-third", "Third", "ebook", "Third", ())
    text = (
        "item\tidentifier\n"
        f"item-ebook\t{ISBN13}\n"
        "item-ebook\t9780804429573\n"
        "item-ebook\tnot-an-isbn\n"
        "item-audio\tB00TEST001\n"
        "item-audio\tb00test001\n"
        "item-audio\tB00TEST001\n"
        f"item-third\t{ISBN13}\n"
    )
    result = _classify(text, (EBOOK, AUDIO, third), frozenset({"item-third"}))
    assert _reasons(result) == {
        2: "conflicting",
        3: "conflicting",
        4: "invalid_isbn",
        6: "repeated",
        7: "repeated",
        8: "already_linked",
    }
    assert [
        (row.row, row.identifier, row.identifier_kind) for row in result.will_link
    ] == [(5, "B00TEST001", "asin")]


def test_counts_add_up_to_the_rows_on_the_demonstration_file() -> None:
    candidates = [
        IdentifierCandidate(
            f"e{i}", f"Ebooks/Book {i}", "ebook", f"Book {i}", (f"Writer {i}",)
        )
        for i in range(10)
    ] + [
        IdentifierCandidate(f"a{i}", f"Audio/Book {i}", "audiobook", f"Book {i}", ())
        for i in range(8)
    ]
    candidates += [
        IdentifierCandidate("d1", "Dup", "ebook", "Dup", ()),
        IdentifierCandidate("d2", "Shelf/Dup", "ebook", "Dup", ()),
    ]
    rows = [f"Ebooks/Book {i}\t{ISBN13}" for i in range(8)]
    rows += [f"Audio/Book {i}\tB00TEST00{i}" for i in range(7)]
    rows += [
        "Ebooks/Book 8\tB00TEST009",  # wrong medium
        "Audio/Book 7\tnot-asin",  # invalid identifier
        "Nowhere\t" + ISBN13,  # no item
        "Shelf/Dup\t" + ISBN13,  # ambiguous
        "broken row",  # unreadable
    ]
    result = _classify(
        "path\tidentifier\n" + "\n".join(rows), candidates, frozenset({"e0"})
    )
    assert result.rows == 20
    assert dict(result.counts) == {
        "will_link": 14,
        "already_linked": 1,
        "no_item": 1,
        "ambiguous": 1,
        "invalid_identifier": 1,
        "wrong_medium": 1,
        "repeated": 0,
        "conflicting": 0,
        "unreadable": 1,
    }
    assert tuple(result.counts) == COUNT_KEYS
    assert sum(result.counts.values()) == result.rows
    assert len(result.will_link) + len(result.refused) == result.rows


def test_will_link_rows_carry_display_values_cut_at_512_characters() -> None:
    long = IdentifierCandidate(
        "x", "X", "ebook", "T" * 600, ("A" * 300, "B" * 300, "C")
    )
    (row,) = _classify(f"item\tidentifier\nx\t{ISBN13}\n", (long,)).will_link
    assert row.title == "T" * 511 + "…"
    assert row.authors == ("A" * 300, "B" * 209 + "…")
    assert len(", ".join(row.authors)) == 512
    assert display_text("short") == "short"
    assert display_authors(("A", "B")) == ("A", "B")
    assert display_authors(("A" * 510, "BBBB")) == ("A" * 510,)


def test_the_result_keeps_no_row_text() -> None:
    sentinel = "Séntinel\\Path/ünïque"
    result = _classify(
        f"path\tidentifier\n{sentinel}\t{ISBN13}\n{sentinel}\n", (EBOOK,)
    )
    assert sentinel not in repr(result)
    assert "ünïque" not in repr(result)


@given(st.binary(max_size=2048))
@settings(max_examples=200, deadline=None)
def test_any_bytes_are_refused_or_counted(data: bytes) -> None:
    try:
        result = classify_identifier_file(data, (EBOOK, AUDIO), frozenset())
    except IdentifierFileError as error:
        assert error.code
        return
    assert sum(result.counts.values()) == result.rows


def _timed(data: bytes, candidates) -> float:
    started = time.perf_counter()
    try:
        classify_identifier_file(data, candidates, frozenset())
    except IdentifierFileError:
        pass
    return time.perf_counter() - started


def test_adversarial_megabyte_inputs_run_in_linear_time() -> None:
    deep = "/".join(["s"] * 40)
    candidates = tuple(
        IdentifierCandidate(f"i{n}", f"{deep}/{n % 50}/Leaf", "ebook", "T", ())
        for n in range(10_000)
    )
    limit = 1_048_576
    benign_row = "Shelf/" + "Some Book " * 18 + f"\t{ISBN13}\n"
    benign = "path\tidentifier\n" + benign_row * min(
        MAX_ROWS, limit // len(benign_row) - 1
    )
    separators = ("path\tidentifier\n" + ("/" * 4000 + "\tx\n") * 260)[:limit]
    suffix_row = deep + "/7/Leaf\t" + ISBN13 + "\n"
    suffixes = "path\tidentifier\n" + suffix_row * min(
        MAX_ROWS, limit // len(suffix_row) - 1
    )
    baseline = max(_timed(benign.encode(), candidates), 0.01)
    for adversarial in (separators, suffixes):
        data = adversarial.encode()[:limit]
        elapsed = _timed(data, candidates)
        assert elapsed < 5.0
        assert elapsed < 5 * baseline + 0.5
