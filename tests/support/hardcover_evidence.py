"""Shared Hardcover provider evidence builders.

These construct the GraphQL envelopes, lookups, and local claims the
Hardcover cohorts assert against. They live here rather than in one test
module so a concern-scoped module can use them without importing another
module's tests.
"""

from __future__ import annotations

import json
from typing import cast

import httpx

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderObservation,
    ProviderQueryKind,
)


class InterruptedProviderStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x"
        raise httpx.ReadTimeout("synthetic interrupted body")


class HostileEncodedProviderStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self):
        yield self._payload


def isbn_lookup(claims: tuple[LocalClaim, ...]) -> MetadataLookup:
    return MetadataLookup(ProviderQueryKind.ISBN, claims)


def work_lookup(
    title: str = "Current title",
    authors: tuple[str, ...] = ("Ada Example",),
) -> MetadataLookup:
    return MetadataLookup(ProviderQueryKind.WORK, (), title, authors)


def hardcover_envelope(
    rows: list[dict[str, object]],
    books: list[dict[str, object]],
) -> dict[str, object]:
    editions = [
        {
            "id": row["id"],
            "book_id": row["book_id"],
            "isbn_10": row.get("isbn_10"),
            "isbn_13": row.get("isbn_13"),
            "asin": row.get("asin"),
            "title": row.get("title"),
            "subtitle": row.get("subtitle"),
            "release_date": row.get("release_date"),
            "image": row.get("image"),
            "publisher": row.get("publisher"),
            "language": row.get("language"),
            "contributions": row.get("contributions", []),
        }
        for row in rows
    ]
    works: list[dict[str, object]] = []
    tags: list[dict[str, object]] = []
    seen_tags: set[object] = set()
    for book in books:
        taggings: list[dict[str, object]] = []
        for raw_tagging in book.get("taggings", []):
            tagging = cast(dict[str, object], raw_tagging)
            raw_tag = tagging.get("tag")
            if type(raw_tag) is dict:
                tag = {
                    **raw_tag,
                    "tag_category": raw_tag.get(
                        "tag_category",
                        {"id": 1, "category": "Mood"},
                    ),
                }
                tag_id = tag.get("id")
                if tag_id not in seen_tags:
                    seen_tags.add(tag_id)
                    tags.append(tag)
            else:
                tag_id = tagging.get("tag_id")
            taggings.append(
                {
                    "id": tagging.get("id"),
                    "tag_id": tag_id,
                }
            )
        works.append(
            {
                "id": book["id"],
                "title": book.get("title"),
                "description": book.get("description"),
                "release_date": book.get("release_date"),
                "image": book.get("image"),
                "contributions": book.get("contributions", []),
                "book_series": book.get("book_series", []),
                "taggings": taggings,
            }
        )
    return {
        "data": {
            "editions": editions,
            "books": works,
            "tags": tags,
        }
    }


def books_envelope(books: list[dict[str, object]]) -> dict[str, object]:
    """The answer to the books-first WORK search: books and tags, no editions."""
    data = cast(dict[str, object], hardcover_envelope([], books)["data"])
    return {
        "data": {
            "books": [
                {
                    **work,
                    "canonical_id": book.get("canonical_id"),
                    "compilation": book.get("compilation", False),
                }
                for work, book in zip(
                    cast(list[dict[str, object]], data["books"]), books, strict=True
                )
            ],
            "tags": data["tags"],
        }
    }


def hardcover_answer(
    request: httpx.Request,
    rows: list[dict[str, object]],
    books: list[dict[str, object]],
) -> dict[str, object]:
    """The envelope a request's own document returns.

    The books-first WORK search returns books alone; every other document
    returns the edition rows beside them.
    """
    if json.loads(request.content)["operationName"] == "BooksByTitleAuthor":
        return books_envelope(books)
    return hardcover_envelope(rows, books)


def isbn_claim() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="eBook ISBN: 9780306406157",
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def title_claim() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Current title",
        canonical="Current title",
        excerpt="Current title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def observe_contributions(
    contributions: list[dict[str, object]],
    lookup: MetadataLookup | None = None,
) -> ProviderObservation:
    rows: list[dict[str, object]] = [
        {
            "id": 1,
            "book_id": 1,
            "isbn_10": None,
            "isbn_13": "9780306406157",
            "contributions": contributions,
        }
    ]
    books: list[dict[str, object]] = [
        {
            "id": 1,
            "title": "Synthetic title",
            "release_date": "2001-02-03",
            "contributions": contributions,
        }
    ]
    return (
        HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=hardcover_answer(request, rows, books),
                    )
                )
            ),
            65_536,
        )
        .observe(lookup or isbn_lookup((isbn_claim(),)))
        .observation
    )
