"""Owner-link vocabularies, grammars, digest and anchor predicate (#1477)."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    OwnerLinkFacts,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLink,
    OwnerLinkError,
    OwnerLinkScope,
    OwnerLinkState,
    OwnerLinkTarget,
    OwnerSearchContradiction,
    OwnerSearchRecord,
    OwnerSearchWork,
    WithheldScopeReason,
    link_authorities,
    link_digest,
    link_state,
    owner_asin,
    owner_isbn,
    owner_link_anchors,
    owner_link_claim,
    owner_search_cards,
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


# -- Owner search cards, eligibility and contradictions (S9, S15) ------------


def _claim(
    candidate: str,
    kind: ClaimKind,
    value: str,
    order: int,
    *,
    scope: ClaimScope = ClaimScope.WORK,
    role: str | None = None,
    applicability: str = "not_applicable",
) -> ProviderClaim:
    return ProviderClaim.create(
        provider="search",
        candidate=candidate,
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=order,
    )


def _record(
    identity: str | None,
    *,
    title: str | None = "Current title",
    creators: tuple[str, ...] = ("Ada Example",),
    isbn: str | None = None,
    language: str | None = None,
    compilation: bool = False,
) -> OwnerSearchRecord:
    name = "candidate-1"
    claims = [] if title is None else [_claim(name, ClaimKind.TITLE, title, 0)]
    claims += [
        _claim(name, ClaimKind.CREATOR, value, len(claims) + index, role="author")
        for index, value in enumerate(creators)
    ]
    if language is not None:
        claims.append(_claim(name, ClaimKind.LANGUAGE, language, len(claims)))
    return OwnerSearchRecord(
        ProviderCandidate(name, name, tuple(claims)),
        identity,
        isbn,
        compilation=compilation,
    )


def _work(**changes: object) -> OwnerSearchWork:
    values: dict[str, object] = {
        "medium": "ebook",
        "title": "Current title",
        "authors": ("Ada Example",),
        "isbn": None,
        "asin": None,
        "runtime_seconds": None,
        "library_languages": (),
        "local_claims": (),
    }
    values.update(changes)
    return OwnerSearchWork(**values)  # type: ignore[arg-type]


def test_a_card_carries_text_only_and_no_identity_to_the_browser() -> None:
    (card,), dropped = owner_search_cards(
        "open_library", (_record("/works/OL7W"),), _work()
    )
    assert dropped == 0
    data = card.to_data()
    assert "/works/OL7W" not in repr(data)
    assert (data["title"], data["creators"], data["choosable"]) == (
        "Current title",
        ["Ada Example"],
        True,
    )
    assert data["preview"] is False


def test_records_outside_the_grammar_or_repeated_are_dropped_and_counted() -> None:
    records = (
        _record("/works/OL7W"),
        _record("/works/OL7W"),
        _record("/authors/OL7A"),
        _record("/works/OL8W", title=None),
    )
    cards, dropped = owner_search_cards("open_library", records, _work())
    assert [card.identity for card in cards] == ["/works/OL7W"]
    assert dropped == 3


def test_a_record_with_no_identity_is_a_card_that_cannot_be_chosen() -> None:
    (card,), _ = owner_search_cards("google_books", (_record(None),), _work())
    assert (card.choosable, card.scopes) == (False, ())
    with pytest.raises(OwnerLinkError):
        card.target(OwnerLinkScope.WORK)


@pytest.mark.parametrize(
    ("provider", "isbn", "scopes", "withheld"),
    [
        (
            "open_library",
            "9780306406157",
            (OwnerLinkScope.WORK, OwnerLinkScope.EDITION),
            (),
        ),
        (
            "open_library",
            None,
            (OwnerLinkScope.WORK,),
            ((OwnerLinkScope.EDITION, WithheldScopeReason.NO_ISBN),),
        ),
        (
            "inventaire",
            "9780306406157",
            (OwnerLinkScope.WORK, OwnerLinkScope.EDITION),
            (),
        ),
        ("audible_catalog", "9780306406157", (OwnerLinkScope.WORK,), ()),
    ],
    ids=[
        "edition-offered",
        "edition-without-isbn",
        "inventaire-edition",
        "not-an-edition-authority",
    ],
)
def test_this_edition_is_offered_only_when_the_card_names_one(
    provider: str,
    isbn: str | None,
    scopes: tuple[OwnerLinkScope, ...],
    withheld: tuple[tuple[OwnerLinkScope, WithheldScopeReason], ...],
) -> None:
    identity = {
        "open_library": "/works/OL7W",
        "inventaire": "wd:Q7",
        "audible_catalog": "B000000001",
    }[provider]
    (card,), _ = owner_search_cards(provider, (_record(identity, isbn=isbn),), _work())
    assert (card.scopes, card.withheld) == (scopes, withheld)
    if OwnerLinkScope.EDITION in scopes:
        target = card.target(OwnerLinkScope.EDITION)
        assert (target.scope, target.identity) == (OwnerLinkScope.EDITION, isbn)


@pytest.mark.parametrize(
    ("stated", "reason"),
    [
        pytest.param("Paperback", WithheldScopeReason.FORMAT_PRINT, id="print"),
        pytest.param("Audio CD", WithheldScopeReason.FORMAT_AUDIO, id="audio"),
        pytest.param("Audiobook", WithheldScopeReason.FORMAT_AUDIO, id="audiobook"),
        pytest.param("Ebook", None, id="ebook"),
    ],
)
def test_this_edition_is_withheld_for_a_stated_print_or_audio_format(
    stated: str, reason: WithheldScopeReason | None
) -> None:
    isbn = "9780306406157"
    name = "candidate-1"
    claims = (
        _claim(name, ClaimKind.TITLE, "Current title", 0),
        _claim(name, ClaimKind.CREATOR, "Ada Example", 1, role="author"),
        _claim(
            name,
            ClaimKind.ISBN,
            isbn,
            2,
            scope=ClaimScope.EDITION,
            applicability="unknown",
        ),
    )
    evidence = (
        ProviderEvidence(
            ProviderEvidenceKind.PHYSICAL_FORMAT, stated, ClaimScope.EDITION
        ),
    )
    record = OwnerSearchRecord(
        ProviderCandidate(name, name, claims, evidence), "/works/OL7W", isbn
    )
    (card,), _ = owner_search_cards("open_library", (record,), _work())
    if reason is None:
        assert (card.scopes, card.withheld) == (
            (OwnerLinkScope.WORK, OwnerLinkScope.EDITION),
            (),
        )
    else:
        assert (card.scopes, card.withheld) == (
            (OwnerLinkScope.WORK,),
            ((OwnerLinkScope.EDITION, reason),),
        )
        assert card.to_data()["withheld"] == [
            {"scope": "edition", "reason": reason.value}
        ]


def test_an_audible_card_without_a_runtime_withholds_this_recording() -> None:
    (card,), _ = owner_search_cards(
        "audible_catalog", (_record("B000000001"),), _work(medium="audiobook")
    )
    assert card.scopes == (OwnerLinkScope.WORK,)
    assert card.withheld == (
        (OwnerLinkScope.RECORDING, WithheldScopeReason.NO_RUNTIME),
    )


def test_contradictions_name_only_what_applies_with_their_values() -> None:
    record = _record(
        "/works/OL7W",
        creators=("Someone Else",),
        isbn="9780306406157",
        language="fre",
        compilation=True,
    )
    (card,), _ = owner_search_cards(
        "open_library",
        (record,),
        _work(isbn="9781861972712", library_languages=("en",)),
    )
    assert card.contradictions == (
        (
            OwnerSearchContradiction.IDENTIFIER,
            {"identifier": "isbn", "record": "9780306406157", "work": "9781861972712"},
        ),
        (OwnerSearchContradiction.COMPILATION, {}),
        (OwnerSearchContradiction.LANGUAGE, {"language": "fre"}),
        (OwnerSearchContradiction.CREATORS_DISJOINT, {}),
    )
    # A contradiction informs the confirmation; the card stays choosable.
    assert card.choosable


def test_a_card_that_agrees_with_the_work_states_no_contradiction() -> None:
    (card,), _ = owner_search_cards(
        "open_library",
        (_record("/works/OL7W", language="eng"),),
        _work(library_languages=("en",)),
    )
    assert card.contradictions == ()


# -- Link state (S29) ----------------------------------------------------------

_LINK = OwnerLink(
    "owner_link_" + "0" * 64, "ebook", OwnerLinkScope.WORK, "hardcover", "4321"
)


def _facts(outcome: str, reason: str | None = None) -> OwnerLinkFacts:
    return OwnerLinkFacts("ebook", "work", "hardcover", "4321", outcome, reason=reason)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (None, OwnerLinkState("preparing", paused=True)),
        (_facts("admitted"), OwnerLinkState("linked", paused=True)),
        (_facts("unavailable"), OwnerLinkState("waiting", retry_at=900, paused=True)),
        (
            _facts("contradicted", "format"),
            OwnerLinkState("contradicted", "format", paused=True),
        ),
        (_facts("missing"), OwnerLinkState("needs_replacing", "missing", paused=True)),
        (_facts("merged"), OwnerLinkState("needs_replacing", "merged", paused=True)),
        (
            _facts("source_disabled"),
            OwnerLinkState("needs_replacing", "source_disabled", paused=True),
        ),
        (
            _facts("medium_changed"),
            OwnerLinkState("needs_replacing", "medium_changed", paused=True),
        ),
    ],
    ids=[
        "preparing",
        "linked",
        "waiting",
        "contradicted",
        "missing",
        "merged",
        "source-disabled",
        "medium-changed",
    ],
)
def test_link_state_reads_the_reports_outcome(
    facts: OwnerLinkFacts | None, expected: OwnerLinkState
) -> None:
    assert link_state(_LINK, facts, paused=True, next_retry_at=900) == expected


def test_a_report_for_another_link_reads_as_preparing() -> None:
    other = OwnerLinkFacts("ebook", "work", "hardcover", "9999", "admitted")
    assert link_state(_LINK, other, paused=False) == OwnerLinkState("preparing")
