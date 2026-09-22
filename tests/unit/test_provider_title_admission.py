"""All provider title ingresses retain the same claim-local rejection precedence."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from mechanical_librarian.adapters.providers import (
    audible_catalog_metadata,
    audiosilo_metadata,
    audnexus_metadata,
    google_books_metadata,
    hardcover_metadata,
    inventaire_metadata,
    itunes_search_metadata,
    open_library_metadata,
)
from mechanical_librarian.domain.metadata_report import ClaimKind, FieldState


@pytest.mark.parametrize(
    "field",
    [
        google_books_metadata._field,
        open_library_metadata._field,
        hardcover_metadata._field,
        inventaire_metadata._field,
        audnexus_metadata._field,
        audible_catalog_metadata._field,
        audiosilo_metadata._field,
        itunes_search_metadata._field,
    ],
)
@pytest.mark.parametrize("kind", [ClaimKind.TITLE, ClaimKind.SUBTITLE])
@pytest.mark.parametrize(
    "value,state",
    [
        (None, FieldState.ABSENT),
        (123, FieldState.MALFORMED),
        ("", FieldState.MALFORMED),
        (" title ", FieldState.MALFORMED),
        ("Example\u200b Book", FieldState.MALFORMED),
        ("\u200b", FieldState.MALFORMED),
        ("Example\u2060Book", FieldState.MALFORMED),
        ("Example\x00\u200bBook", FieldState.MALFORMED),
        ("/home/person/private\u200b", FieldState.MALFORMED),
        ("Bearer secret\u200b", FieldState.MALFORMED),
        ("x" * 512 + "\u200b", FieldState.OVERSIZED),
        ("/home/person/" + "x" * 512 + "\x00\u200b", FieldState.OVERSIZED),
        ("Café — 日本語!", FieldState.VALID),
        ("Cafe\u0301", FieldState.VALID),
        ("Different Title", FieldState.VALID),
    ],
)
def test_provider_title_ingress_precedence(
    field: Callable[[ClaimKind, object], tuple[FieldState, str | None]],
    kind: ClaimKind,
    value: object,
    state: FieldState,
) -> None:
    # Private ingress is the contract: every adapter's claim builder calls it.
    assert field(kind, value) == (state, value if state is FieldState.VALID else None)
