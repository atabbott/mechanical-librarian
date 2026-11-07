"""An audiobook's companion EPUB helps find and confirm its book (#1468).

The EPUB beside the tracks is the publisher's own record of the book. Its
ISBN asks Hardcover for that book, whose audio editions are compared with the
file; its title is the first alternative search title; and when the target
names no complete work, its title and creators are the search. None of it is
ever a value for the recording, and the ISBN confirms the work, never the
recording: runtime, narrators and variant still decide which recording the
file is.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.application import metadata_report as app
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    TargetSnapshot,
    audiobook_discovery_plan,
    companion_binding_plan,
    qualify_provider_observation,
)
from mechanical_librarian.domain import metadata_report as dom
from mechanical_librarian.domain.audiobook_corroboration import (
    HardcoverEditionWitness,
    companion_linked_asins,
    hardcover_edition_leads,
    hardcover_edition_witness,
)
from mechanical_librarian.domain.metadata_report import (
    COMPANION_TITLE_BASIS,
    COMPANION_WORK_BASIS,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    CompanionEbook,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    claim_scope,
    companion_isbn_lane,
    companion_work_payload,
    default_target_fields,
    work_discovery_lane_payloads,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_ISBN = "9780306406157"
_OTHER_ISBN = "9780140328721"
_LOCAL_SECONDS = 37_560
_BOOK = "hardcover-work-0000000010"
_ASIN = "B0LINKED01"


def _dispatch(lane: ProviderLaneObservation) -> dom.ProviderLaneDispatch:
    """How a lane's dispatch ended, as the generator records it."""
    outcome = dom.lane_dispatch_outcome(lane, starved=False)
    assert outcome is not None
    return dom.ProviderLaneDispatch(outcome)


def _companion_claim(kind: ClaimKind, value: str, **fields: object) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability=str(fields.pop("applicability", "not_applicable")),
        basis="package_metadata",
        checksum=str(fields.pop("checksum", "not_applicable")),
        applicability_basis=str(fields.pop("applicability_basis", "not_applicable")),
        **fields,  # type: ignore[arg-type]
    )


def _isbn_claim(value: str = _ISBN) -> LocalClaim:
    return _companion_claim(
        ClaimKind.ISBN,
        value,
        applicability="ebook",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _companion(
    title: str | None = "Companion Title",
    creators: tuple[str, ...] = ("Ada Author",),
    isbns: tuple[str, ...] = (_ISBN,),
) -> CompanionEbook:
    claims: list[LocalClaim] = []
    if title is not None:
        claims.append(_companion_claim(ClaimKind.TITLE, title))
    claims.extend(
        _companion_claim(ClaimKind.CREATOR, name, role="author", occurrence=index)
        for index, name in enumerate(creators)
    )
    claims.extend(_isbn_claim(value) for value in isbns)
    return CompanionEbook("e" * 64, "c" * 64, tuple(claims))


def _fields(title: str | None, authors: tuple[str, ...]) -> tuple[TargetField, ...]:
    populated = (
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, authors)
        if authors
        else TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING)
    )
    return tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields(title)
    )


def _target(title: str | None, authors: tuple[str, ...]) -> TargetSnapshot:
    return TargetSnapshot("audiobook", title, (), fields=_fields(title, authors))


def _target_title_claim(title: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def _duration_claim(seconds: int = _LOCAL_SECONDS) -> LocalClaim:
    value = f"{seconds}.000"
    return LocalClaim.create(
        kind=ClaimKind.DURATION,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        state=FieldState.VALID,
        occurrence=1,
    )


def _narrator_claim(name: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.CONTRIBUTOR,
        observed=name,
        canonical=name,
        excerpt=name,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="narrator",
        occurrence=2,
    )


def _provider_claims(
    provider: str,
    identity: str,
    values: tuple[tuple[ClaimKind, str, str | None], ...],
) -> tuple[ProviderClaim, ...]:
    return tuple(
        ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=claim_scope(kind),
            applicability="not_applicable",
            role=role,
            raw_order=order,
        )
        for order, (kind, value, role) in enumerate(values)
    )


def _edition(
    edition_id: int, seconds: int, *narrators: str, asin: str | None = _ASIN
) -> ProviderCandidate:
    identity = f"hardcover-edition-{edition_id:010d}"
    return ProviderCandidate(
        identity,
        _BOOK,
        _provider_claims(
            "catalogue",
            identity,
            (
                *(((ClaimKind.ASIN, asin, None),) if asin is not None else ()),
                (ClaimKind.DURATION, f"{seconds}.000", None),
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
            ),
        ),
    )


def _book(title: str = "Companion Title") -> ProviderCandidate:
    return ProviderCandidate(
        _BOOK,
        _BOOK,
        _provider_claims("catalogue", _BOOK, ((ClaimKind.TITLE, title, None),)),
    )


def _hardcover(
    companion: CompanionEbook,
    editions: tuple[ProviderCandidate, ...],
    book: ProviderCandidate | None = None,
    *,
    authority: str = "hardcover",
) -> ProviderObservation:
    lane = companion_isbn_lane(companion)
    assert lane is not None
    kept = _book() if book is None else book
    record = ProviderLaneObservation(
        lane.identity,
        ProviderQueryKind.ISBN,
        ProviderStatus.CANDIDATES,
        lane.input_references,
        lane.matched_family,
        (
            ProviderLaneCandidate(
                dom.provider_lane_candidate_reference(
                    "catalogue", lane.identity, kept.identity, kept.identity
                ),
                kept.identity,
                kept.identity,
            ),
        ),
    )
    record = replace(
        record,
        dispatch=_dispatch(record),
    )
    return ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        lane.input_references,
        (kept,),
        ProviderQueryKind.ISBN,
        authority,
        lanes=(record,),
        audio_editions=tuple(sorted(editions, key=lambda value: value.identity)),
    )


# --- The companion ISBN lane --------------------------------------------------


def test_one_isbn_family_plans_one_lane_from_the_companion_claims() -> None:
    companion = _companion()
    lane = companion_isbn_lane(companion)
    assert lane is not None
    isbn = next(c for c in companion.claims if c.kind is ClaimKind.ISBN)
    assert (lane.matched_family, lane.canonical_isbns, lane.input_references) == (
        _ISBN,
        (_ISBN,),
        (isbn.reference,),
    )
    assert lane.identity == dom.discovery_lane_identity(
        {
            "canonical_isbns": [_ISBN],
            "input_references": [isbn.reference],
            "kind": "isbn",
            "matched_family": _ISBN,
        }
    )


@pytest.mark.parametrize("isbns", [(), (_ISBN, _OTHER_ISBN)])
def test_no_family_or_several_plan_no_lane(isbns: tuple[str, ...]) -> None:
    assert companion_isbn_lane(_companion(isbns=isbns)) is None
    assert companion_isbn_lane(None) is None


def test_the_audiobook_plan_adds_the_isbn_lane_before_its_search() -> None:
    target = _target("Asserted Title", ("Ada Author",))
    claims = (_target_title_claim("Asserted Title"),)
    companion = _companion(title=None)
    without = audiobook_discovery_plan(target, claims, audio=single_track_audio_facts())
    plan = audiobook_discovery_plan(
        target, claims, audio=single_track_audio_facts(), companion=companion
    )
    isbn = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
    assert {lane.identity for lane in plan.lanes} == {
        *(lane.identity for lane in without.lanes),
        isbn.identity,
    }
    order = app.provider_discovery_lane_order(plan)
    assert order[0] is isbn
    assert all(lane.kind is ProviderQueryKind.WORK for lane in order[1:])
    # The ISBN never suppresses the search, even when it found this very work:
    # the ebook's identifier cannot stand in for a search for the recording.
    found = [
        app.ProviderLaneResult(
            isbn, ProviderStatus.CANDIDATES, (_book("Asserted Title"),)
        )
    ]
    searches = [
        lane
        for lane in plan.lanes
        if lane.kind is ProviderQueryKind.WORK and lane.title_basis is None
    ]
    assert searches
    assert not any(
        app.identifier_suppresses_discovery(plan, found, lane) for lane in searches
    )


def test_an_ebook_plan_never_reads_a_companion() -> None:
    target = TargetSnapshot("ebook", "Title", ())
    plan = app.metadata_discovery_plan(target, ())
    assert not any(lane.kind is ProviderQueryKind.ISBN for lane in plan.lanes)


@pytest.mark.parametrize(
    ("authority", "asked"),
    [
        ("hardcover", True),
        ("open_library", False),
        ("audible_catalog", False),
        ("audnexus", False),
        ("audiosilo", False),
        ("itunes_search", False),
        ("google_books", False),
        ("inventaire", False),
    ],
)
def test_only_hardcover_is_asked_the_companion_isbn(
    authority: str, asked: bool
) -> None:
    """Only a book that lists audio editions can be compared with the file.

    Open Library lists none, and the Audible family and iTunes would be asked
    an ebook's identifier for a recording it cannot name (ADR-0061).
    """
    plan = audiobook_discovery_plan(
        _target("Asserted Title", ("Ada Author",)),
        (_target_title_claim("Asserted Title"),),
        audio=single_track_audio_facts(),
        companion=_companion(title=None),
    )
    handed = companion_binding_plan(plan, authority)
    assert asked == any(lane.kind is ProviderQueryKind.ISBN for lane in handed.lanes)
    assert {
        lane.identity
        for lane in handed.lanes
        if lane.kind is not ProviderQueryKind.ISBN
    } == {
        lane.identity for lane in plan.lanes if lane.kind is not ProviderQueryKind.ISBN
    }


def test_a_companion_isbn_is_a_query_claim_only_on_its_own_lane() -> None:
    companion = _companion(title=None)
    lane = companion_isbn_lane(companion)
    assert lane is not None
    audio = single_track_audio_facts()
    own = MetadataLookup(
        ProviderQueryKind.ISBN,
        (),
        matched_family=lane.matched_family,
        audio=audio,
        companion=companion,
    )
    other = MetadataLookup(
        ProviderQueryKind.ISBN,
        (),
        matched_family=_OTHER_ISBN,
        audio=audio,
        companion=companion,
    )
    assert [claim.kind for claim in own.query_claims] == [ClaimKind.ISBN]
    assert own.local_claims == ()
    assert other.query_claims == ()


# --- The companion title and the fallback work ---------------------------------


def test_the_companion_title_is_the_first_alternative_title() -> None:
    companion = _companion()
    fields = _fields("Asserted Title", ("Ada Author",))
    claims = (_target_title_claim("Asserted Title"),)
    leaf = dom.AudioDiscoveryTitle("path_leaf", "The Folder Title")
    asserted, alternative = work_discovery_lane_payloads(
        fields, claims, (leaf,), companion
    )
    title = next(c for c in companion.claims if c.kind is ClaimKind.TITLE)
    assert asserted.title_basis is None
    assert (alternative.title, alternative.title_basis) == (
        "Companion Title",
        COMPANION_TITLE_BASIS,
    )
    assert alternative.authors == ("Ada Author",)
    assert alternative.input_references == tuple(
        sorted((*asserted.input_references, title.reference))
    )


def test_a_companion_title_equal_to_the_target_s_adds_no_lane() -> None:
    """Folded equal, it would only repeat the asserted query."""
    fields = _fields("Companion Title", ("Ada Author",))
    claims = (_target_title_claim("Companion Title"),)
    for companion in (_companion("Companion Title"), _companion("COMPANION TITLE")):
        payloads = work_discovery_lane_payloads(fields, claims, (), companion)
        assert [value.title_basis for value in payloads] == [None]


def test_the_plan_asks_the_companion_title_before_the_folder_leaf() -> None:
    target = _target("Asserted Title", ("Ada Author",))
    claims = (_target_title_claim("Asserted Title"),)
    leaf = dom.AudioDiscoveryTitle("path_leaf", "The Folder Title")
    plan = audiobook_discovery_plan(
        target,
        claims,
        audio=replace(single_track_audio_facts(), discovery_titles=(leaf,)),
        companion=_companion(isbns=()),
    )
    alternative = next(lane for lane in plan.lanes if lane.title_basis is not None)
    assert (alternative.title, alternative.title_basis) == (
        "Companion Title",
        COMPANION_TITLE_BASIS,
    )
    assert plan.audio is not None
    assert [value.basis for value in plan.audio.discovery_titles] == [
        COMPANION_TITLE_BASIS,
        "path_leaf",
    ]


def test_a_target_with_no_authors_searches_with_the_companion_work() -> None:
    companion = _companion(creators=("Ada Author", "Bea Writer"))
    fields = _fields("Track 01", ())
    (fallback,) = work_discovery_lane_payloads(fields, (), (), companion)
    assert fallback == companion_work_payload(companion)
    assert (fallback.title, fallback.authors, fallback.title_basis) == (
        "Companion Title",
        ("Ada Author", "Bea Writer"),
        COMPANION_WORK_BASIS,
    )
    assert set(fallback.input_references) == {
        claim.reference
        for claim in companion.claims
        if claim.kind in {ClaimKind.TITLE, ClaimKind.CREATOR}
    }
    # An ebook never falls back: it has no companion.
    assert work_discovery_lane_payloads(fields, (), None, companion) == ()


def test_the_fallback_needs_a_title_and_authors() -> None:
    fields = _fields("Track 01", ())
    for companion in (_companion(title=None), _companion(creators=())):
        assert work_discovery_lane_payloads(fields, (), (), companion) == ()
    illustrated = CompanionEbook(
        "e" * 64,
        "c" * 64,
        (
            _companion_claim(ClaimKind.TITLE, "Companion Title"),
            _companion_claim(ClaimKind.CREATOR, "Ivy Illustrator", role="ill"),
        ),
    )
    assert companion_work_payload(illustrated) is None


def test_the_fallback_lane_is_the_plan_s_only_search() -> None:
    plan = audiobook_discovery_plan(
        _target("Track 01", ()),
        (),
        audio=single_track_audio_facts(),
        companion=_companion(),
    )
    works = [lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK]
    assert [(lane.title, lane.title_basis) for lane in works] == [
        ("Companion Title", COMPANION_WORK_BASIS)
    ]
    # With no asserted lane, the fallback is dispatched outright.
    assert app.alternative_work_lane_permitted(plan, None)


# --- Audio editions from the companion's book ----------------------------------


def test_editions_of_the_companion_book_link_their_asins() -> None:
    companion = _companion()
    observation = _hardcover(companion, (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),))
    assert companion_linked_asins(
        (_duration_claim(),), single_track_audio_facts(), (observation,), companion
    ) == (_ASIN,)


def test_an_edition_outside_the_band_links_nothing() -> None:
    companion = _companion()
    observation = _hardcover(
        companion, (_edition(1, _LOCAL_SECONDS + 600, "Nia Narrator"),)
    )
    assert (
        companion_linked_asins(
            (_duration_claim(),), single_track_audio_facts(), (observation,), companion
        )
        == ()
    )


def test_editions_that_disagree_on_the_cast_link_nothing() -> None:
    companion = _companion()
    observation = _hardcover(
        companion,
        (
            _edition(1, _LOCAL_SECONDS, "Nia Narrator"),
            _edition(2, _LOCAL_SECONDS + 30, "Other Reader", asin="B0LINKED02"),
        ),
    )
    assert (
        companion_linked_asins(
            (_duration_claim(),), single_track_audio_facts(), (observation,), companion
        )
        == ()
    )


def _companion_report(
    companion: CompanionEbook,
    editions: tuple[ProviderCandidate, ...],
    product: ProviderCandidate | None = None,
    *,
    book: ProviderCandidate | None = None,
    authority: str = "hardcover",
    title: str | None = "Asserted Title",
) -> MetadataReport:
    providers = [_hardcover(companion, editions, book, authority=authority)]
    if product is not None:
        providers.append(
            ProviderObservation(
                "audible",
                ProviderStatus.CANDIDATES,
                candidates=(product,),
                query_kind=ProviderQueryKind.DISCOVERY,
                authority="audible_catalog",
            )
        )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audiobooks", "item-1", _fields(title, ("Ada Author",))
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(_duration_claim(),),
        local_reasons=("no_local_audio_claims",),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        audio=single_track_audio_facts(),
        companion=companion,
    )


def _audible(minutes: int, *narrators: str) -> ProviderCandidate:
    identity = "audible-product-B0PRODUCT1"
    return ProviderCandidate(
        identity,
        "unresolved",
        _provider_claims(
            "audible",
            identity,
            (
                (ClaimKind.DURATION, f"{minutes * 60}.000", None),
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
            ),
        ),
    )


def test_the_witness_challenges_a_match_from_the_companion_book_s_editions() -> None:
    """The existing witness covers the ISBN lane: no second veto is needed."""
    companion = _companion()
    report = _companion_report(
        companion,
        (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),),
        _audible(_LOCAL_SECONDS // 60 + 30, "Someone Else"),
    )
    assert hardcover_edition_witness(report) is HardcoverEditionWitness.CHALLENGED
    assert hardcover_edition_leads(
        report.local_claims, report.audio, report.providers
    ) == (_ASIN,)


def test_a_report_keeps_the_companion_book_its_editions_link() -> None:
    companion = _companion()
    report = _companion_report(
        companion,
        (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),),
        book=_book("A Different Title"),
    )
    assert MetadataReport.from_json(report.to_json()) == report


def test_no_proposal_carries_a_companion_value() -> None:
    """The companion finds the book; none of its values is offered (#1468).

    Two companions that differ in title and creators give the same results:
    nothing the proposal set offers or reasons from reads them.
    """
    from mechanical_librarian.domain.proposal_set import evaluate_report

    def results(companion: CompanionEbook) -> tuple[object, str]:
        report = _companion_report(
            companion,
            (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),),
            book=_book("A Different Title"),
        )
        proposal_set = evaluate_report(report)
        return proposal_set.to_data()["results"], proposal_set.to_json()

    first, text = results(
        _companion(title="Only In The Epub", creators=("Ada Author", "Epub Editor"))
    )
    second, _ = results(_companion(title="Another Epub Title"))
    assert first and first == second
    for value in ("Only In The Epub", "Epub Editor", _ISBN):
        assert value not in text


def test_a_report_refuses_a_companion_book_that_is_not_this_work() -> None:
    """Neither this work by title and authors, nor linked by an edition."""
    with pytest.raises(ReportValueError, match="companion books"):
        _companion_report(companion=_companion(), editions=(), book=_book("Elsewhere"))


def test_a_report_refuses_the_companion_lane_at_another_authority() -> None:
    with pytest.raises(ReportValueError, match="companion lane"):
        _companion_report(
            _companion(),
            (),
            book=_book("Asserted Title"),
            authority="open_library",
        )


def test_a_report_refuses_an_isbn_lane_its_companion_did_not_plan() -> None:
    planned = _companion()
    observation = _hardcover(planned, (), _book("Asserted Title"))
    with pytest.raises(ReportValueError, match="companion lane"):
        MetadataReport.create(
            target=TargetFacts(
                "primary",
                "audiobooks",
                "item-1",
                _fields("Asserted Title", ("Ada Author",)),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=(_duration_claim(),),
            local_reasons=("no_local_audio_claims",),
            providers=(observation,),
            audio=single_track_audio_facts(),
            companion=_companion(isbns=(_OTHER_ISBN,)),
        )


def test_a_report_s_audio_never_states_the_companion_title() -> None:
    companion = _companion()
    with pytest.raises(ReportValueError, match="discovery titles"):
        MetadataReport.create(
            target=TargetFacts(
                "primary",
                "audiobooks",
                "item-1",
                _fields("Asserted Title", ("Ada Author",)),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=(_duration_claim(),),
            local_reasons=("no_local_audio_claims",),
            providers=(),
            audio=replace(
                single_track_audio_facts(),
                discovery_titles=(
                    dom.AudioDiscoveryTitle(COMPANION_TITLE_BASIS, "Companion Title"),
                ),
            ),
            companion=companion,
        )


@pytest.mark.parametrize(
    ("title", "authors", "basis"),
    [
        ("Not The Companion Title", ("Ada Author",), COMPANION_TITLE_BASIS),
        ("Companion Title", ("Somebody Else",), COMPANION_WORK_BASIS),
    ],
)
def test_a_report_refuses_a_companion_search_it_did_not_plan(
    title: str, authors: tuple[str, ...], basis: str
) -> None:
    companion = _companion()
    target_authors = ("Ada Author",) if basis == COMPANION_TITLE_BASIS else ()
    references = tuple(
        sorted(
            claim.reference
            for claim in companion.claims
            if claim.kind is not ClaimKind.ISBN
        )
    )
    payload = dom.WorkLanePayload(title, authors, references, basis)
    record = ProviderLaneObservation(
        payload.identity,
        ProviderQueryKind.WORK,
        ProviderStatus.NO_MATCH,
        payload.input_references,
        None,
        (),
    )
    record = replace(
        record,
        dispatch=_dispatch(record),
    )
    with pytest.raises(ReportValueError, match="work lane"):
        MetadataReport.create(
            target=TargetFacts(
                "primary",
                "audiobooks",
                "item-1",
                _fields("Asserted Title", target_authors),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
            local_claims=(_duration_claim(),),
            local_reasons=("no_local_audio_claims",),
            providers=(
                ProviderObservation(
                    "audible",
                    ProviderStatus.NO_MATCH,
                    payload.input_references,
                    (),
                    ProviderQueryKind.WORK,
                    "audible_catalog",
                    lanes=(record,),
                ),
            ),
            audio=single_track_audio_facts(),
            companion=companion,
        )


# --- Replay requires the planned companion lane (#1724) ----------------------


def _asin_claims(count: int) -> tuple[LocalClaim, ...]:
    from tests.support.audiobook_providers import audio_claim

    return tuple(
        audio_claim(ClaimKind.ASIN, f"B0LOCAL{index:03d}", 4 + index)
        for index in range(count)
    )


def _audiobook_plan(
    title: str | None,
    authors: tuple[str, ...],
    claims: tuple[LocalClaim, ...],
    companion: CompanionEbook | None,
) -> app.MetadataDiscoveryPlan:
    return audiobook_discovery_plan(
        _target(title, authors),
        claims,
        audio=single_track_audio_facts(),
        companion=companion,
    )


def _observed(
    lanes: tuple[app.MetadataDiscoveryLane, ...],
    status: ProviderStatus = ProviderStatus.NO_MATCH,
    *,
    provider: str = "hardcover-main",
    authority: str = "hardcover",
) -> ProviderObservation:
    from tests.support.audiobook_reports import recorded_dispatch

    return recorded_dispatch(
        app.provider_observation(
            provider,
            tuple(app.ProviderLaneResult(lane, status) for lane in lanes),
            authority=authority,
        )
    )


def _lane_report(
    title: str | None,
    authors: tuple[str, ...],
    claims: tuple[LocalClaim, ...],
    providers: tuple[ProviderObservation, ...],
    companion: CompanionEbook | None,
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audiobooks", "item-1", _fields(title, authors)),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("no_local_audio_claims",),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        audio=single_track_audio_facts(),
        companion=companion,
    )


def _lanes_of(
    plan: app.MetadataDiscoveryPlan, kind: ProviderQueryKind
) -> tuple[app.MetadataDiscoveryLane, ...]:
    # Hardcover suppresses alternative titles, so it asks the asserted WORK
    # lane (or the fallback) and the companion lane only.
    return tuple(
        lane
        for lane in companion_binding_plan(plan, "hardcover").lanes
        if lane.kind is kind
        and (kind is not ProviderQueryKind.WORK or lane.title_basis is None)
    )


# Each case: the target's title and authors, its local claims, and what
# Hardcover is handed beside the companion lane.
_ASSERTED = (
    "Asserted Title",
    ("Ada Author",),
    (_target_title_claim("Asserted Title"), _duration_claim()),
)
_ASIN_ONLY = (
    "Asserted Title",
    ("Ada Author",),
    (_target_title_claim("Asserted Title"), _duration_claim(), *_asin_claims(1)),
)
_FALLBACK: tuple[str | None, tuple[str, ...], tuple[LocalClaim, ...]] = (
    None,
    (),
    (_duration_claim(),),
)


@pytest.mark.parametrize(
    "with_search", [True, False], ids=["asserted_work_lane_only", "no_lanes"]
)
def test_a_report_refuses_a_hardcover_observation_missing_the_companion_lane(
    with_search: bool,
) -> None:
    title, authors, claims = _ASSERTED
    companion = _companion()
    plan = _audiobook_plan(title, authors, claims, companion)
    assert _lanes_of(plan, ProviderQueryKind.ISBN)
    lanes = _lanes_of(plan, ProviderQueryKind.WORK) if with_search else ()
    assert bool(lanes) is with_search
    with pytest.raises(ReportValueError, match="companion lane is missing"):
        _lane_report(title, authors, claims, (_observed(lanes),), companion)


@pytest.mark.parametrize(
    ("case", "kinds"),
    [
        (_ASSERTED, {ProviderQueryKind.WORK, ProviderQueryKind.ISBN}),
        (_ASIN_ONLY, {ProviderQueryKind.ISBN}),
        (_FALLBACK, {ProviderQueryKind.ISBN}),
    ],
    ids=["beside_its_search", "alone_beside_a_local_asin", "alone_beside_fallback"],
)
def test_a_report_accepts_the_companion_lane_alone_or_beside_its_search(
    case: tuple[str | None, tuple[str, ...], tuple[LocalClaim, ...]],
    kinds: set[ProviderQueryKind],
) -> None:
    title, authors, claims = case
    companion = _companion()
    plan = _audiobook_plan(title, authors, claims, companion)
    lanes = (
        *_lanes_of(plan, ProviderQueryKind.WORK),
        *_lanes_of(plan, ProviderQueryKind.ISBN),
    )
    assert {lane.kind for lane in lanes} == kinds
    report = _lane_report(title, authors, claims, (_observed(lanes),), companion)
    assert MetadataReport.from_json(report.to_json()) == report


@pytest.mark.parametrize(
    "status",
    [ProviderStatus.UNAVAILABLE, ProviderStatus.DISABLED, ProviderStatus.NO_MATCH],
    ids=["unavailable", "disabled", "no_match"],
)
def test_the_companion_lane_is_required_whatever_its_status(
    status: ProviderStatus,
) -> None:
    title, authors, claims = _ASIN_ONLY
    companion = _companion()
    plan = _audiobook_plan(title, authors, claims, companion)
    isbn = _lanes_of(plan, ProviderQueryKind.ISBN)
    report = _lane_report(title, authors, claims, (_observed(isbn, status),), companion)
    assert MetadataReport.from_json(report.to_json()) == report
    with pytest.raises(ReportValueError, match="companion lane is missing"):
        _lane_report(title, authors, claims, (_observed((), status),), companion)


@pytest.mark.parametrize(
    ("title", "authors", "claims", "companion", "planned"),
    [
        (*_ASIN_ONLY, _companion(), True),
        (
            "Asserted Title",
            ("Ada Author",),
            (_target_title_claim("Asserted Title"), *_asin_claims(4)),
            _companion(),
            True,
        ),
        (
            "Asserted Title",
            ("Ada Author",),
            (_target_title_claim("Asserted Title"), *_asin_claims(5)),
            _companion(),
            False,
        ),
        (*_ASSERTED, _companion(title=None), True),
        (*_ASSERTED, _companion(), True),
        (
            "Part II",
            ("Ada Author",),
            (_target_title_claim("Part II"), _duration_claim()),
            _companion(),
            True,
        ),
        (*_FALLBACK, _companion(), True),
        # Zero local ASINs and no target title claim: no WORK lane at all.
        ("Asserted Title", ("Ada Author",), (_duration_claim(),), _companion(), True),
        (*_ASSERTED, _companion(isbns=()), False),
        (*_ASSERTED, _companion(isbns=(_ISBN, _OTHER_ISBN)), False),
    ],
    ids=[
        "one_local_asin",
        "four_local_asins",
        "five_local_asins_overflow",
        "asserted_only",
        "asserted_and_alternative",
        "collapsed_alternative",
        "companion_work_fallback",
        "no_work_lane",
        "no_isbn_family",
        "two_isbn_families",
    ],
)
def test_plan_and_replay_agree_on_the_companion_lane(
    title: str | None,
    authors: tuple[str, ...],
    claims: tuple[LocalClaim, ...],
    companion: CompanionEbook,
    planned: bool,
) -> None:
    plan = _audiobook_plan(title, authors, claims, companion)
    isbn = [lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN]
    replayed = dom.planned_companion_isbn_lane(
        _fields(title, authors),
        claims,
        single_track_audio_facts().discovery_titles,
        companion,
    )
    assert bool(isbn) is planned
    assert (replayed is not None) is planned
    if replayed is not None:
        assert [lane.identity for lane in isbn] == [replayed.identity]


def test_the_plan_matrix_reaches_each_shape() -> None:
    """The matrix above plans the lane shapes its names say."""
    companion = _companion()
    collapsed = _audiobook_plan(
        "Part II",
        ("Ada Author",),
        (_target_title_claim("Part II"), _duration_claim()),
        companion,
    )
    works = [lane for lane in collapsed.lanes if lane.kind is ProviderQueryKind.WORK]
    assert [lane.title_basis for lane in works] == [COMPANION_TITLE_BASIS]
    both = _audiobook_plan(*_ASSERTED, companion)
    assert sorted(
        str(lane.title_basis)
        for lane in both.lanes
        if lane.kind is ProviderQueryKind.WORK
    ) == sorted([str(None), COMPANION_TITLE_BASIS])
    fallback = _audiobook_plan(*_FALLBACK, companion)
    assert [
        lane.title_basis
        for lane in fallback.lanes
        if lane.kind is ProviderQueryKind.WORK
    ] == [COMPANION_WORK_BASIS]
    overflow = _audiobook_plan(
        "Asserted Title",
        ("Ada Author",),
        (_target_title_claim("Asserted Title"), *_asin_claims(5)),
        companion,
    )
    assert overflow.overflow and overflow.lanes == ()


def test_an_exhausted_budget_plans_and_requires_no_companion_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title, authors, claims = _ASSERTED
    companion = _companion(title=None)
    roomy = _audiobook_plan(title, authors, claims, companion)
    (isbn,) = _lanes_of(roomy, ProviderQueryKind.ISBN)
    works = tuple(lane for lane in roomy.lanes if lane.kind is ProviderQueryKind.WORK)
    base = app._metadata_discovery_query_count(works, audiobook=True)
    monkeypatch.setattr(dom, "MAX_PROVIDER_DISCOVERY_QUERIES", base)

    plan = _audiobook_plan(title, authors, claims, companion)
    assert not plan.overflow
    assert plan.lanes == works
    search = _lanes_of(plan, ProviderQueryKind.WORK)
    _lane_report(title, authors, claims, (_observed(search),), companion)
    with pytest.raises(ReportValueError, match="companion lane is invalid"):
        _lane_report(title, authors, claims, (_observed((*search, isbn)),), companion)


def test_a_budget_with_exactly_one_query_of_room_plans_and_requires_the_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title, authors, claims = _ASSERTED
    companion = _companion(title=None)
    roomy = _audiobook_plan(title, authors, claims, companion)
    (isbn,) = _lanes_of(roomy, ProviderQueryKind.ISBN)
    works = tuple(lane for lane in roomy.lanes if lane.kind is ProviderQueryKind.WORK)
    base = app._metadata_discovery_query_count(works, audiobook=True)
    monkeypatch.setattr(dom, "MAX_PROVIDER_DISCOVERY_QUERIES", base + 1)

    plan = _audiobook_plan(title, authors, claims, companion)
    assert not plan.overflow
    assert _lanes_of(plan, ProviderQueryKind.ISBN) == (isbn,)
    search = _lanes_of(plan, ProviderQueryKind.WORK)
    _lane_report(title, authors, claims, (_observed((*search, isbn)),), companion)
    with pytest.raises(ReportValueError, match="companion lane is missing"):
        _lane_report(title, authors, claims, (_observed(search),), companion)


def test_no_companion_lane_is_required_where_none_is_planned() -> None:
    from mechanical_librarian.application.failure_diagnostics import (
        provider_attempt_diagnostics,
    )

    companion = _companion()
    title, authors = "Asserted Title", ("Ada Author",)
    overflowing = (_target_title_claim("Asserted Title"), *_asin_claims(5))
    exceeded = ProviderObservation(
        "hardcover-main",
        ProviderStatus.QUERY_LIMIT_EXCEEDED,
        authority="hardcover",
        diagnostics=provider_attempt_diagnostics(
            (), status=ProviderStatus.QUERY_LIMIT_EXCEEDED.value
        ),
    )
    _lane_report(title, authors, overflowing, (exceeded,), companion)
    roomy = _audiobook_plan(*_ASSERTED, companion)
    (isbn,) = _lanes_of(roomy, ProviderQueryKind.ISBN)
    with pytest.raises(ReportValueError, match="companion lane is invalid"):
        _lane_report(title, authors, overflowing, (_observed((isbn,)),), companion)
    # Only Hardcover is asked the lane; another authority never carries it.
    title, authors, claims = _ASSERTED
    plan = _audiobook_plan(title, authors, claims, companion)
    searches = tuple(
        lane
        for lane in companion_binding_plan(plan, "audible_catalog").lanes
        if lane.kind is ProviderQueryKind.WORK
    )
    other = _observed(searches, provider="audible", authority="audible_catalog")
    _lane_report(title, authors, claims, (other,), companion)
    # A report with no Hardcover observation has nothing to check.
    _lane_report(title, authors, claims, (), companion)


class _FailingProvider:
    def observe(
        self, plan: app.MetadataDiscoveryPlan, *, deadline: float | None = None
    ) -> app.ProviderResult:
        raise RuntimeError("adapter fault")


@pytest.mark.parametrize(
    "case",
    [_ASSERTED, _ASIN_ONLY, _FALLBACK],
    ids=["asserted_work", "local_asin", "companion_work_fallback"],
)
def test_a_disabled_or_failed_hardcover_binding_records_the_companion_lane(
    case: tuple[str | None, tuple[str, ...], tuple[LocalClaim, ...]],
) -> None:
    title, authors, claims = case
    companion = _companion()
    plan = companion_binding_plan(
        _audiobook_plan(title, authors, claims, companion), "hardcover"
    )
    binding = app.ProviderBinding(
        "hardcover-main", False, lambda: _FailingProvider(), "hardcover"
    )
    disabled = app._undispatched_observation(
        binding,
        plan,
        ProviderStatus.DISABLED,
        tuple(lane for lane in plan.lanes if lane.title_basis is None),
    )
    failed = app.observe_provider(
        "hardcover-main", "hardcover", _FailingProvider(), plan
    ).observation
    planned = companion_isbn_lane(companion)
    assert planned is not None
    for observation in (disabled, failed):
        assert planned.identity in {lane.identity for lane in observation.lanes}
        _lane_report(title, authors, claims, (observation,), companion)


# --- Admission: the ISBN confirms the work, never the recording ---------------


def _product(
    minutes: int,
    *narrators: str,
    title: str = "A Mangled Folder Title",
    creator: str = "Zed Stranger",
    language: str | None = None,
    edition_statement: str | None = None,
) -> ProviderCandidate:
    identity = f"audible-product-{_ASIN}"
    return ProviderCandidate(
        identity,
        "audible-work-unresolved",
        _provider_claims(
            "audible",
            identity,
            (
                (ClaimKind.ASIN, _ASIN, None),
                (ClaimKind.TITLE, title, None),
                (ClaimKind.CREATOR, creator, "author"),
                (ClaimKind.DURATION, f"{minutes * 60}.000", None),
                *((ClaimKind.CONTRIBUTOR, name, "narrator") for name in narrators),
                *(((ClaimKind.LANGUAGE, language, None),) if language else ()),
            ),
        ),
        evidence=(
            (
                dom.ProviderEvidence(
                    dom.ProviderEvidenceKind.EDITION_STATEMENT,
                    edition_statement,
                    ClaimScope.EDITION,
                ),
            )
            if edition_statement
            else ()
        ),
    )


def _asin_answer(
    product: ProviderCandidate,
    *,
    linked: tuple[str, ...] = (_ASIN,),
    local: tuple[LocalClaim, ...] = (),
    work_title: str | None = "Asserted Title",
    work_authors: tuple[str, ...] = ("Ada Author",),
    audio: AudioFacts | None = None,
) -> ProviderObservation:
    observation = ProviderObservation(
        "audible",
        ProviderStatus.CANDIDATES,
        (),
        (product,),
        ProviderQueryKind.ASIN,
        "audible_catalog",
    )
    return qualify_provider_observation(
        observation,
        MetadataLookup(
            ProviderQueryKind.ASIN,
            (_duration_claim(), *local),
            audio=single_track_audio_facts() if audio is None else audio,
            work_title=work_title,
            work_authors=work_authors,
            requested_asin=_ASIN,
            companion=_companion(),
            companion_linked_asins=linked,
        ),
    )


def _kept(observation: ProviderObservation) -> bool:
    """Whether the product kept more than its identifier."""
    (candidate,) = observation.candidates
    return any(claim.kind is not ClaimKind.ASIN for claim in candidate.claims)


def test_a_linked_product_in_the_band_is_admitted_whatever_its_title() -> None:
    assert _kept(_asin_answer(_product(_LOCAL_SECONDS // 60, "Nia Narrator")))


def test_a_linked_product_is_admitted_with_no_target_identity() -> None:
    """An ISBN-only plan feeds a lead with no title or authors to admit by."""
    answer = _asin_answer(
        _product(_LOCAL_SECONDS // 60, "Nia Narrator"), work_title=None, work_authors=()
    )
    assert _kept(answer)
    assert not _kept(
        _asin_answer(
            _product(_LOCAL_SECONDS // 60, "Nia Narrator"),
            linked=(),
            work_title=None,
            work_authors=(),
        )
    )
    report = _companion_report(
        _companion(),
        (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),),
        answer.candidates[0],
        title=None,
    )
    assert MetadataReport.from_json(report.to_json()) == report


def test_the_companion_work_requalifies_a_fed_product_with_no_target() -> None:
    """A fallback-search recording keeps its enrichment when fed (#1468).

    With no target work, the search asks with the companion's title and
    creators, so its promoted product is looked up again against that same
    identity, and is kept only as the tight-band recording.
    """

    def answer(minutes: int, title: str) -> ProviderObservation:
        return _asin_answer(
            _product(minutes, "Nia Narrator", title=title, creator="Ada Author"),
            linked=(),
            work_title=None,
            work_authors=(),
        )

    kept = answer(_LOCAL_SECONDS // 60, "Companion Title")
    assert _kept(kept)
    assert not _kept(answer(_LOCAL_SECONDS // 60 + 30, "Companion Title"))
    assert not _kept(answer(_LOCAL_SECONDS // 60, "Another Work Entirely"))
    report = MetadataReport.create(
        target=TargetFacts("primary", "audiobooks", "item-1", _fields(None, ())),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(_duration_claim(),),
        local_reasons=("no_local_audio_claims",),
        providers=(replace(kept, query_kind=ProviderQueryKind.DISCOVERY),),
        audio=single_track_audio_facts(),
        companion=_companion(isbns=()),
    )
    assert MetadataReport.from_json(report.to_json()) == report


def test_the_companion_title_requalifies_a_fed_product() -> None:
    """A companion-title recording keeps its enrichment when fed (#1468).

    The alternative lane asked the companion's title with the target's
    authors, so its promoted product is looked up again against that work,
    and is kept only as the tight-band recording.
    """
    title = dom.companion_discovery_title(_companion())
    assert title is not None
    asked = replace(single_track_audio_facts(), discovery_titles=(title,))

    def answer(minutes: int, audio: AudioFacts) -> ProviderObservation:
        return _asin_answer(
            _product(
                minutes, "Nia Narrator", title="Companion Title", creator="Ada Author"
            ),
            linked=(),
            audio=audio,
        )

    assert _kept(answer(_LOCAL_SECONDS // 60, asked))
    assert not _kept(answer(_LOCAL_SECONDS // 60 + 30, asked))
    # Without the companion-title lane the product names another work.
    assert not _kept(answer(_LOCAL_SECONDS // 60, single_track_audio_facts()))


def test_an_unlinked_product_is_only_its_identifier() -> None:
    assert not _kept(
        _asin_answer(_product(_LOCAL_SECONDS // 60, "Nia Narrator"), linked=())
    )


def test_a_linked_product_outside_the_band_is_refused() -> None:
    assert not _kept(_asin_answer(_product(_LOCAL_SECONDS // 60 + 30, "Nia Narrator")))


def test_a_linked_product_with_another_cast_is_refused() -> None:
    assert not _kept(
        _asin_answer(
            _product(_LOCAL_SECONDS // 60, "Someone Else"),
            local=(_narrator_claim("Nia Narrator"),),
        )
    )


def test_a_linked_product_in_another_language_is_refused() -> None:
    language = LocalClaim.create(
        kind=ClaimKind.LANGUAGE,
        observed="en",
        canonical="en",
        excerpt="en",
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=3,
    )
    assert not _kept(
        _asin_answer(
            _product(_LOCAL_SECONDS // 60, "Nia Narrator", language="de"),
            local=(language,),
        )
    )


def test_a_linked_product_of_another_abridgement_is_refused() -> None:
    def answer(statement: str) -> ProviderObservation:
        return _asin_answer(
            _product(_LOCAL_SECONDS // 60, "Nia Narrator", edition_statement=statement),
            work_title="Asserted Title (Unabridged)",
        )

    assert _kept(answer("Unabridged"))
    assert not _kept(answer("Abridged"))


def test_a_plan_with_only_the_companion_isbn_feeds_its_edition_leads() -> None:
    """No search, only the ISBN: its book's editions still lead (Codex R1)."""
    target = _target(None, ())
    companion = _companion(title=None)
    plan = audiobook_discovery_plan(
        target,
        (_duration_claim(),),
        audio=single_track_audio_facts(),
        companion=companion,
    )
    assert [lane.kind for lane in plan.lanes] == [ProviderQueryKind.ISBN]
    observation = _hardcover(companion, (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),))
    feed = app.audiobook_asin_feed_plan(target, plan, (observation,))
    assert feed is not None
    (lane,) = feed.lanes
    assert lane.canonical_asin == _ASIN
    # The lead stands on the companion ISBN claims that found it.
    assert lane.input_references == plan.lanes[0].input_references


def test_hardcover_is_handed_exactly_the_lanes_it_serves() -> None:
    """A product lane beside the companion lane is not Hardcover's (Codex R1).

    A retry rebuilds this plan and reconciles it with the observation, which
    records only the lanes Hardcover asked.
    """
    from tests.support.audiobook_providers import audio_claim

    target = _target("Asserted Title", ("Ada Author",))
    plan = audiobook_discovery_plan(
        target,
        (
            _target_title_claim("Asserted Title"),
            _duration_claim(),
            audio_claim(ClaimKind.ASIN, "B0LOCAL001", 4),
        ),
        audio=single_track_audio_facts(),
        companion=_companion(),
    )
    kinds = {lane.kind for lane in plan.lanes}
    assert kinds == {ProviderQueryKind.ASIN, ProviderQueryKind.ISBN}
    hardcover = companion_binding_plan(plan, "hardcover")
    assert [lane.kind for lane in hardcover.lanes] == [ProviderQueryKind.ISBN]
    audible = companion_binding_plan(plan, "audible_catalog")
    assert {lane.kind for lane in audible.lanes} == {ProviderQueryKind.ASIN}
    # Without a companion lane Hardcover's plan is untouched.
    plain = audiobook_discovery_plan(
        target,
        (
            _target_title_claim("Asserted Title"),
            _duration_claim(),
            audio_claim(ClaimKind.ASIN, "B0LOCAL001", 4),
        ),
        audio=single_track_audio_facts(),
    )
    assert companion_binding_plan(plain, "hardcover") is plain


def test_editions_that_disagree_admit_nothing_through_the_gate() -> None:
    """From the report's editions to the gate: disagreeing casts link nothing."""
    companion = _companion()
    observation = _hardcover(
        companion,
        (
            _edition(1, _LOCAL_SECONDS, "Nia Narrator"),
            _edition(2, _LOCAL_SECONDS + 30, "Other Reader", asin="B0LINKED02"),
        ),
    )
    linked = companion_linked_asins(
        (_duration_claim(),), single_track_audio_facts(), (observation,), companion
    )
    assert not _kept(
        _asin_answer(_product(_LOCAL_SECONDS // 60, "Nia Narrator"), linked=linked)
    )
    agreeing = _hardcover(companion, (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),))
    linked = companion_linked_asins(
        (_duration_claim(),), single_track_audio_facts(), (agreeing,), companion
    )
    assert _kept(
        _asin_answer(_product(_LOCAL_SECONDS // 60, "Nia Narrator"), linked=linked)
    )


def test_the_feed_plan_asks_for_the_linked_product_and_carries_the_link() -> None:
    target = _target("Asserted Title", ("Ada Author",))
    companion = _companion()
    plan = audiobook_discovery_plan(
        target,
        (_target_title_claim("Asserted Title"), _duration_claim()),
        audio=single_track_audio_facts(),
        companion=companion,
    )
    observation = _hardcover(companion, (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),))
    feed = app.audiobook_asin_feed_plan(target, plan, (observation,))
    assert feed is not None
    assert feed.companion == companion
    assert feed.companion_linked_asins == (_ASIN,)
    assert _ASIN in {lane.canonical_asin for lane in feed.lanes}


# --- Measurement ----------------------------------------------------------------


def test_the_cold_start_report_counts_what_the_companion_did() -> None:
    import scripts.cold_start_report as cold_start

    report = _companion_report(
        _companion(),
        (_edition(1, _LOCAL_SECONDS, "Nia Narrator"),),
        book=_book("A Different Title"),
    )
    use = cold_start._companion_use(report)
    # This report states no target title claim, so it planned no search.
    assert use == ("read", True, True, None)

    def row(value: tuple[object, ...], outcome: str) -> tuple[object, ...]:
        return (None,) * 3 + (outcome,) + (None,) * 4 + (value,)

    counts = cold_start._companion_counts(
        [
            row(use, "recording_admitted"),
            row(use, "recording_admitted"),
            row(use, "work_only"),
        ]
    )
    # How a linked recording was admitted is now counted by its admission
    # basis (#1723), so the companion block no longer guesses at it.
    assert counts == {
        "reports": 3,
        "unreadable": 0,
        "isbn_lane_kept_book": 3,
        "linked_edition": 3,
        "title_alternative": 0,
        "work_fallback": 0,
        "recording_admitted": 2,
    }
