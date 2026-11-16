"""Owner-link vocabularies, grammars, digest and anchor predicate (#1477)."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import ClaimKind
from mechanical_librarian.domain.owner_link import (
    OwnerLinkError,
    OwnerLinkScope,
    OwnerLinkTarget,
    link_authorities,
    link_digest,
    owner_asin,
    owner_isbn,
    owner_link_anchors,
    owner_link_claim,
    record_identity_valid,
)


@pytest.mark.parametrize(
    ("scope", "provider", "identity", "valid"),
    [
        pytest.param(OwnerLinkScope.WORK, "hardcover", "4321", True, id="hc-book"),
        pytest.param(
            OwnerLinkScope.WORK, "hardcover", "0123", False, id="hc-leading-zero"
        ),
        pytest.param(
            OwnerLinkScope.WORK, "open_library", "/works/OL7W", True, id="ol-work"
        ),
        pytest.param(
            OwnerLinkScope.WORK, "open_library", "/books/OL7M", True, id="ol-edition"
        ),
        pytest.param(
            OwnerLinkScope.WORK, "open_library", "/authors/OL7A", False, id="ol-author"
        ),
        pytest.param(OwnerLinkScope.WORK, "inventaire", "wd:Q42", True, id="inv-wd"),
        pytest.param(
            OwnerLinkScope.WORK, "inventaire", "inv:" + "a" * 32, True, id="inv-own"
        ),
        pytest.param(
            OwnerLinkScope.WORK,
            "inventaire",
            "isbn:9780306406157",
            False,
            id="inv-isbn",
        ),
        pytest.param(
            OwnerLinkScope.WORK, "google_books", "9780306406157", True, id="gb-isbn"
        ),
        pytest.param(
            OwnerLinkScope.WORK,
            "google_books",
            "9780306406158",
            False,
            id="gb-checksum",
        ),
        pytest.param(
            OwnerLinkScope.WORK, "audible_catalog", "B000000001", True, id="audible"
        ),
        pytest.param(
            OwnerLinkScope.EDITION, "open_library", "9780306406157", True, id="edition"
        ),
        pytest.param(
            OwnerLinkScope.EDITION,
            "audible_catalog",
            "9780306406157",
            False,
            id="edition-audible",
        ),
        pytest.param(
            OwnerLinkScope.RECORDING,
            "audible_catalog",
            "B000000001",
            True,
            id="recording",
        ),
        pytest.param(
            OwnerLinkScope.RECORDING,
            "hardcover",
            "B000000001",
            False,
            id="recording-hc",
        ),
    ],
)
def test_record_identity_grammar(
    scope: OwnerLinkScope, provider: str, identity: str, valid: bool
) -> None:
    assert record_identity_valid(scope, provider, identity) is valid


@pytest.mark.parametrize(
    ("value", "family"),
    [
        pytest.param("9780306406157", "9780306406157", id="isbn13"),
        pytest.param("0306406152", "9780306406157", id="isbn10"),
        pytest.param("080442957X", "9780804429573", id="isbn10-x"),
    ],
)
def test_owner_isbn_returns_its_family(value: str, family: str) -> None:
    assert owner_isbn(value) == family


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("978-0306406157", id="hyphen"),
        pytest.param("9780306406158", id="checksum"),
        pytest.param("X306406152", id="x-first"),
        pytest.param(" 0306406152", id="space"),
        pytest.param(9780306406157, id="number"),
    ],
)
def test_owner_isbn_refuses_rather_than_repairs(value: object) -> None:
    with pytest.raises(OwnerLinkError):
        owner_isbn(value)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        pytest.param("B000000001", True, id="canonical"),
        pytest.param("b000000001", False, id="lower"),
        pytest.param("B00000001", False, id="short"),
    ],
)
def test_owner_asin_is_canonical_only(value: str, valid: bool) -> None:
    if valid:
        assert owner_asin(value) == value
    else:
        with pytest.raises(OwnerLinkError):
            owner_asin(value)


def test_digest_names_the_source_only_for_a_work_link() -> None:
    edition = [
        link_digest("ebook", OwnerLinkScope.EDITION, "9780306406157", provider)
        for provider in ("open_library", "hardcover")
    ]
    work = [
        link_digest("ebook", OwnerLinkScope.WORK, "9780306406157", provider)
        for provider in ("google_books", "open_library")
    ]
    assert edition[0] == edition[1]
    assert work[0] != work[1]
    assert link_digest("audiobook", OwnerLinkScope.WORK, "4321", "hardcover") != (
        link_digest("ebook", OwnerLinkScope.WORK, "4321", "hardcover")
    )


def test_every_scope_anchors_by_the_owners_choice() -> None:
    assert [owner_link_anchors(scope) for scope in OwnerLinkScope] == [True] * 3


@pytest.mark.parametrize(
    ("scope", "provider", "identity", "kind"),
    [
        pytest.param(
            OwnerLinkScope.EDITION,
            "hardcover",
            "9780306406157",
            ClaimKind.ISBN,
            id="edition",
        ),
        pytest.param(
            OwnerLinkScope.WORK,
            "google_books",
            "9780306406157",
            ClaimKind.ISBN,
            id="gb-work",
        ),
        pytest.param(
            OwnerLinkScope.RECORDING,
            "audible_catalog",
            "B000000001",
            ClaimKind.ASIN,
            id="recording",
        ),
        pytest.param(
            OwnerLinkScope.WORK,
            "audible_catalog",
            "B000000001",
            ClaimKind.ASIN,
            id="audible-work",
        ),
        pytest.param(OwnerLinkScope.WORK, "hardcover", "4321", None, id="key-lookup"),
    ],
)
def test_identifier_links_carry_a_lane_input_claim(
    scope: OwnerLinkScope, provider: str, identity: str, kind: ClaimKind | None
) -> None:
    claim = owner_link_claim(scope, provider, identity)
    assert (None if claim is None else claim.kind) is kind
    if claim is not None:
        assert claim.basis == "owner_link" and claim.canonical == identity


def test_link_lanes_go_to_the_medium_identifier_sources() -> None:
    assert link_authorities(OwnerLinkScope.RECORDING, "audible_catalog") == {
        "audible_catalog",
        "audnexus",
        "audiosilo",
    }
    assert link_authorities(OwnerLinkScope.WORK, "inventaire") == {"inventaire"}


@pytest.mark.parametrize(
    "display",
    [
        pytest.param({"display_title": ""}, id="empty"),
        pytest.param({"display_creators": ("a",) * 11}, id="too-many-creators"),
        pytest.param({"display_title": "x" * 513}, id="long"),
    ],
)
def test_display_snapshot_is_bounded(display: dict[str, object]) -> None:
    values: dict[str, object] = {
        "scope": OwnerLinkScope.WORK,
        "provider": "hardcover",
        "identity": "1",
        "display_title": "T",
        **display,
    }
    with pytest.raises(OwnerLinkError):
        OwnerLinkTarget(**values)  # type: ignore[arg-type]
