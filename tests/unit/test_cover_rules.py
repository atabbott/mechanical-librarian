from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from functools import partial

import pytest

from mechanical_librarian.domain.cover_rules import (
    COLOUR_BINS,
    COVER_FLOOR_SHORT_EDGE,
    LAYOUT_VALUES,
    SAME_ART_MAX_ASPECT_DISTANCE,
    SAME_ART_MAX_COLOUR_DISTANCE,
    SAME_ART_MAX_DHASH_DISTANCE,
    SAME_ART_MAX_LAYOUT_DISTANCE,
    SAME_ART_MAX_PHASH_DISTANCE,
    SHARPER_COPY_MIN_AREA_RATIO,
    SQUARE_MAX_ASPECT,
    SQUARE_MIN_ASPECT,
    CoverExclusion,
    CoverIssue,
    CoverMeasures,
    CoverOutcome,
    CoverProtection,
    CoverRuleCandidate,
    CurrentCover,
    MeasuredCoverElection,
    SameArtGroup,
    elect_measured_covers,
    same_art,
)

WORK = "verified_work_match"
EDITION = "verified_manifestation_match"
COLOUR_TOTAL = 10_000
# Book-shaped, square and an audiobook portrait well inside every bound.
PORTRAIT = (400, 600)
SQUARE = (500, 500)


def _hash(bits: int) -> str:
    return f"{(1 << bits) - 1:016x}"


def _measures(
    *,
    art: int = 0,
    phash_bits: int = 0,
    dhash_bits: int = 0,
    colour_moved: int = 0,
    layout_step: int = 0,
) -> CoverMeasures:
    """One art's measures, or a copy displaced by exact amounts on each measure.

    ``art`` (0 to 3) selects a distinct art: each sets its own 16 hash bits and
    puts its colour in its own bin, so two different arts never meet the hash or
    colour bounds. Art 0 is the one copies are displaced from.
    """
    block = 0 if art == 0 else ((1 << 16) - 1) << (16 * (art - 1))
    phash = int(_hash(phash_bits), 16) ^ block
    dhash = int(_hash(dhash_bits), 16) ^ block
    colour = [0] * COLOUR_BINS
    if art:
        colour[2 * art] = COLOUR_TOTAL
    else:
        colour[0] = COLOUR_TOTAL - colour_moved
        colour[1] = colour_moved
    layout = tuple(layout_step for _ in range(LAYOUT_VALUES))
    return CoverMeasures(f"{phash:016x}", f"{dhash:016x}", tuple(colour), layout)


def _candidate(
    reference: str,
    size: tuple[int, int] = PORTRAIT,
    *,
    identity: str = WORK,
    ordinal: int = 0,
    measures: CoverMeasures | None = None,
    non_english_edition: bool = False,
    omnibus_volume: bool = False,
    title_differs: bool = False,
) -> CoverRuleCandidate:
    return CoverRuleCandidate(
        reference=reference,
        identity=identity,
        width=size[0],
        height=size[1],
        packet_ordinal=ordinal,
        measures=measures,
        non_english_edition=non_english_edition,
        omnibus_volume=omnibus_volume,
        title_differs=title_differs,
    )


def _current(
    size: tuple[int, int] | None = PORTRAIT,
    *,
    identity: str | None = WORK,
    measures: CoverMeasures | None = None,
    non_english_edition: bool = False,
) -> CurrentCover:
    if size is None:
        return CurrentCover("current", identity, readable=False)
    return CurrentCover(
        "current",
        identity,
        readable=True,
        width=size[0],
        height=size[1],
        measures=measures,
        non_english_edition=non_english_edition,
    )


def _elect(
    candidates: tuple[CoverRuleCandidate, ...],
    *,
    medium: str = "ebook",
    current: CurrentCover | None = None,
    protection: CoverProtection = CoverProtection.PROTECTED,
    prefer_edition: bool = False,
    title_conflict: bool = False,
    omnibus: bool = False,
) -> MeasuredCoverElection:
    return elect_measured_covers(
        candidates,
        medium=medium,
        current=current,
        protection=protection,
        prefer_edition=prefer_edition,
        title_conflict=title_conflict,
        omnibus=omnibus,
    )


def _sized(short: int, aspect: float) -> tuple[int, int]:
    return (short, round(short * aspect))


SQUARE_EDGE_LOW = math.ceil(1000 * SQUARE_MIN_ASPECT)
SQUARE_EDGE_HIGH = math.floor(1000 * SQUARE_MAX_ASPECT)


@pytest.mark.parametrize(
    ("size", "excluded"),
    [
        ((1000, 1000), True),
        ((1000, SQUARE_EDGE_LOW), True),
        ((1000, SQUARE_EDGE_HIGH), True),
        ((1000, SQUARE_EDGE_HIGH + 1), False),
        ((1000, SQUARE_EDGE_LOW - 1), False),
        (PORTRAIT, False),
    ],
    ids=["square", "low_edge", "high_edge", "just_portrait", "just_landscape", "book"],
)
def test_an_ebook_never_takes_square_art(size: tuple[int, int], excluded: bool) -> None:
    election = _elect((_candidate("art", size),))

    assert election.excluded == (
        (("art", CoverExclusion.WRONG_SHAPE),) if excluded else ()
    )
    assert election.ranked == (() if excluded else ("art",))


def test_an_audiobook_takes_portrait_art_only_without_takeable_square_art() -> None:
    with_square = _elect(
        (_candidate("portrait", PORTRAIT), _candidate("square", SQUARE)),
        medium="audiobook",
    )
    assert with_square.excluded == (("portrait", CoverExclusion.WRONG_SHAPE),)
    assert with_square.ranked == ("square",)

    without_square = _elect((_candidate("portrait", PORTRAIT),), medium="audiobook")
    assert without_square.ranked == ("portrait",)
    assert without_square.outcome is CoverOutcome.FILL

    small = COVER_FLOOR_SHORT_EDGE - 1
    untakeable_square = _elect(
        (_candidate("portrait", PORTRAIT), _candidate("square", (small, small))),
        medium="audiobook",
    )
    assert untakeable_square.excluded == (("square", CoverExclusion.BELOW_FLOOR),)
    assert untakeable_square.ranked == ("portrait",)

    square_current = _elect(
        (_candidate("portrait", PORTRAIT),),
        medium="audiobook",
        current=_current(SQUARE),
    )
    assert square_current.excluded == (("portrait", CoverExclusion.WRONG_SHAPE),)
    assert square_current.outcome is CoverOutcome.KEEP


@pytest.mark.parametrize(
    ("short", "eligible"),
    [(COVER_FLOOR_SHORT_EDGE - 1, False), (COVER_FLOOR_SHORT_EDGE, True)],
    ids=["under_floor", "at_floor"],
)
def test_the_floor_reads_the_short_edge(short: int, eligible: bool) -> None:
    election = _elect((_candidate("art", (short, 3 * short)),))
    assert election.ranked == (("art",) if eligible else ())

    landscape = _elect((_candidate("wide", (3 * short, short)),))
    assert landscape.ranked == (("wide",) if eligible else ())

    current = _elect(
        (_candidate("other", PORTRAIT, measures=_measures(art=1)),),
        current=_current((short, 3 * short)),
    )
    assert (CoverIssue.BELOW_FLOOR in current.issues) is not eligible


def test_same_art_from_three_sources_collapses_to_the_sharpest_copy() -> None:
    election = _elect(
        (
            _candidate("small", (400, 600), ordinal=0, measures=_measures()),
            _candidate("large", (800, 1200), ordinal=2, measures=_measures()),
            _candidate("medium", (600, 900), ordinal=1, measures=_measures()),
            _candidate("other", (300, 450), measures=_measures(art=1)),
        )
    )

    assert election.groups == (
        SameArtGroup(("large", "medium", "small"), representative="large"),
    )
    assert election.ranked == ("large", "other")
    assert election.choice == "large"


def test_collapse_stays_inside_one_call_and_never_admits_a_candidate() -> None:
    template = _measures()
    first = _elect(
        (_candidate("first-a", measures=template),),
        current=_current(measures=template),
    )
    second = _elect(
        (_candidate("second-a", (800, 1200), measures=template),),
        current=_current(measures=_measures(art=1)),
    )

    assert first.groups == (SameArtGroup(("current", "first-a"), "current"),)
    assert first.outcome is CoverOutcome.KEEP
    assert second.groups == ()
    assert second.ranked == ("second-a",)
    assert second.outcome is CoverOutcome.KEEP


def test_images_without_measures_never_collapse() -> None:
    election = _elect(
        (
            _candidate("measured", measures=_measures()),
            _candidate("unmeasured", (800, 1200)),
        ),
        current=_current(measures=_measures()),
    )

    assert election.groups == (SameArtGroup(("current", "measured"), "current"),)
    assert election.outcome is CoverOutcome.KEEP


def _margin_widths() -> tuple[int, int]:
    """Even widths of 2:3 copies just at and just under the margin over PORTRAIT."""
    floor = SHARPER_COPY_MIN_AREA_RATIO * PORTRAIT[0] * PORTRAIT[1]
    width = PORTRAIT[0]
    while width * (3 * width // 2) < floor:
        width += 2
    return width, width - 2


@pytest.mark.parametrize("at_margin", [True, False], ids=["at_margin", "under_margin"])
def test_a_copy_replaces_the_current_cover_only_past_the_margin(
    at_margin: bool,
) -> None:
    at, under = _margin_widths()
    width = at if at_margin else under
    area = width * (3 * width // 2)
    current_area = PORTRAIT[0] * PORTRAIT[1]
    assert current_area < area
    assert (area >= SHARPER_COPY_MIN_AREA_RATIO * current_area) is at_margin

    election = _elect(
        (_candidate("copy", (width, 3 * width // 2), measures=_measures()),),
        current=_current(measures=_measures()),
    )

    if at_margin:
        assert election.issues == (CoverIssue.SHARPER_COPY,)
        assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "copy")
        assert election.groups == (SameArtGroup(("copy", "current"), "copy"),)
    else:
        assert election.issues == ()
        assert (election.outcome, election.choice) == (CoverOutcome.KEEP, None)
        assert election.groups == (SameArtGroup(("copy", "current"), "current"),)


def test_a_sharper_copy_is_taken_over_a_better_ranked_other_art() -> None:
    width = _margin_widths()[0]
    election = _elect(
        (
            _candidate("copy", (width, 3 * width // 2), measures=_measures()),
            _candidate(
                "edition", (1000, 1500), identity=EDITION, measures=_measures(art=1)
            ),
        ),
        current=_current((COVER_FLOOR_SHORT_EDGE - 1, 600), measures=None),
    )
    assert election.ranked == ("edition", "copy")
    assert election.choice == "edition"

    sharper = _elect(
        (
            _candidate("copy", (width, 3 * width // 2), measures=_measures()),
            _candidate(
                "edition", (1000, 1500), identity=EDITION, measures=_measures(art=1)
            ),
        ),
        current=_current(measures=_measures()),
    )
    assert sharper.choice == "copy"


def _bound_cases() -> list[tuple[str, Callable[[bool], tuple[CoverMeasures, int]]]]:
    def phash(over: bool) -> tuple[CoverMeasures, int]:
        return _measures(phash_bits=SAME_ART_MAX_PHASH_DISTANCE + over), PORTRAIT[1]

    def dhash(over: bool) -> tuple[CoverMeasures, int]:
        return _measures(dhash_bits=SAME_ART_MAX_DHASH_DISTANCE + over), PORTRAIT[1]

    def colour(over: bool) -> tuple[CoverMeasures, int]:
        moved = math.floor(SAME_ART_MAX_COLOUR_DISTANCE * COLOUR_TOTAL) + over
        return _measures(colour_moved=moved), PORTRAIT[1]

    def layout(over: bool) -> tuple[CoverMeasures, int]:
        step = math.floor(SAME_ART_MAX_LAYOUT_DISTANCE * 255) + over
        return _measures(layout_step=step), PORTRAIT[1]

    def aspect(over: bool) -> tuple[CoverMeasures, int]:
        limit = PORTRAIT[1] * math.exp(SAME_ART_MAX_ASPECT_DISTANCE)
        return _measures(), math.floor(limit) + over

    return [
        ("phash", phash),
        ("dhash", dhash),
        ("colour", colour),
        ("layout", layout),
        ("aspect", aspect),
    ]


@pytest.mark.parametrize(
    ("name", "case"), _bound_cases(), ids=[name for name, _ in _bound_cases()]
)
@pytest.mark.parametrize("over", [False, True], ids=["within", "over"])
def test_every_same_art_bound_must_hold(
    name: str, case: Callable[[bool], tuple[CoverMeasures, int]], over: bool
) -> None:
    del name
    measures, height = case(over)

    assert same_art(_measures(), PORTRAIT, measures, (PORTRAIT[0], height)) is not over
    assert same_art(measures, (PORTRAIT[0], height), _measures(), PORTRAIT) is not over


def test_a_chain_of_near_matches_never_makes_another_art_a_copy() -> None:
    near = _measures(phash_bits=SAME_ART_MAX_PHASH_DISTANCE)
    far = _measures(phash_bits=2 * SAME_ART_MAX_PHASH_DISTANCE)
    width = _margin_widths()[0]
    large = (width, 3 * width // 2)
    assert same_art(_measures(), PORTRAIT, near, PORTRAIT)
    assert same_art(near, PORTRAIT, far, large)
    assert not same_art(_measures(), PORTRAIT, far, large)
    candidates = (
        _candidate("near", PORTRAIT, measures=near),
        _candidate("far", large, measures=far),
    )

    fault_free = _elect(candidates, current=_current(measures=_measures()))
    assert (fault_free.outcome, fault_free.choice) == (CoverOutcome.KEEP, None)
    assert fault_free.issues == ()
    assert fault_free.groups == (SameArtGroup(("current", "near"), "current"),)
    assert set(fault_free.ranked) == {"near", "far"}

    faulty = _elect(
        candidates, current=_current(measures=_measures(), non_english_edition=True)
    )
    assert faulty.issues == (CoverIssue.EDITION_LANGUAGE,)
    assert (faulty.outcome, faulty.choice) == (CoverOutcome.REPLACE, "far")


def test_a_fault_of_the_art_is_not_replaced_by_another_copy_of_it() -> None:
    copy = _candidate("copy", PORTRAIT, identity=EDITION, measures=_measures())
    other = _candidate("other", (1000, 1500), measures=_measures(art=1))
    current = _current((800, 1200), measures=_measures(), non_english_edition=True)

    election = _elect((copy, other), current=current)
    assert election.ranked == ("copy", "other")
    assert election.issues == (CoverIssue.EDITION_LANGUAGE,)
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "other")

    only_copy = _elect((copy,), current=current)
    assert (only_copy.outcome, only_copy.choice) == (CoverOutcome.KEEP, None)
    assert only_copy.issues == (CoverIssue.EDITION_LANGUAGE,)


def test_a_square_cover_at_the_band_edge_does_not_take_its_own_copy() -> None:
    edge = (1000, SQUARE_EDGE_HIGH)
    outside = (1000, SQUARE_EDGE_HIGH + 1)
    assert same_art(_measures(), edge, _measures(), outside)
    election = _elect(
        (
            _candidate("copy", outside, identity=EDITION, measures=_measures()),
            _candidate("other", PORTRAIT, measures=_measures(art=1)),
        ),
        current=_current(edge, measures=_measures()),
    )

    assert election.ranked == ("copy", "other")
    assert election.issues == (CoverIssue.WRONG_SHAPE,)
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "other")


def _art_fault_cases() -> list[tuple[str, CurrentCover, tuple[int, int], CoverIssue]]:
    edge = (1000, SQUARE_EDGE_HIGH)
    return [
        (
            "edition_language",
            _current((800, 1200), measures=_measures(), non_english_edition=True),
            (1200, 1800),
            CoverIssue.EDITION_LANGUAGE,
        ),
        (
            "wrong_shape",
            _current(edge, measures=_measures()),
            (1500, 3 * SQUARE_EDGE_HIGH // 2 + 1),
            CoverIssue.WRONG_SHAPE,
        ),
    ]


@pytest.mark.parametrize(
    ("current", "copy_size", "fault"),
    [case[1:] for case in _art_fault_cases()],
    ids=[case[0] for case in _art_fault_cases()],
)
def test_an_art_fault_takes_other_art_before_a_sharper_copy(
    current: CurrentCover, copy_size: tuple[int, int], fault: CoverIssue
) -> None:
    assert current.size is not None
    copy = _candidate("copy", copy_size, identity=EDITION, measures=_measures())
    assert copy.width * copy.height >= SHARPER_COPY_MIN_AREA_RATIO * (
        current.size[0] * current.size[1]
    )
    other = _candidate("other", PORTRAIT, measures=_measures(art=1))

    election = _elect((copy, other), current=current)

    assert election.ranked == ("copy", "other")
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "other")
    assert election.issues == (fault,)
    assert election.reason is fault


@pytest.mark.parametrize(
    ("current", "copy_size", "fault"),
    [case[1:] for case in _art_fault_cases()],
    ids=[case[0] for case in _art_fault_cases()],
)
def test_an_art_fault_with_no_other_art_takes_the_sharper_copy_as_such(
    current: CurrentCover, copy_size: tuple[int, int], fault: CoverIssue
) -> None:
    copy = _candidate("copy", copy_size, measures=_measures())

    for protection, outcome in (
        (CoverProtection.PROTECTED, CoverOutcome.REPLACE),
        (CoverProtection.PROPOSALS_ONLY, CoverOutcome.SUGGEST),
    ):
        election = _elect((copy,), current=current, protection=protection)
        assert (election.outcome, election.choice) == (outcome, "copy")
        assert election.issues == (fault, CoverIssue.SHARPER_COPY)
        assert election.reason is CoverIssue.SHARPER_COPY


def test_a_reason_outside_the_issues_is_refused() -> None:
    election = _elect((_candidate("art"),), current=_current(SQUARE))
    assert election.reason is CoverIssue.WRONG_SHAPE

    with pytest.raises(ValueError):
        replace(election, reason=CoverIssue.SHARPER_COPY)
    with pytest.raises(ValueError):
        replace(election, reason=None)
    with pytest.raises(ValueError):
        replace(election, issues=())


def test_an_undersized_cover_is_cured_by_a_copy_that_clears_the_floor() -> None:
    edge = COVER_FLOOR_SHORT_EDGE
    small = (edge - 1, round((edge - 1) * 1.5))
    copy = _candidate(
        "copy", (edge, edge * 3 // 2), identity=EDITION, measures=_measures()
    )
    other = _candidate("other", PORTRAIT, measures=_measures(art=1))
    assert same_art(_measures(), small, _measures(), (copy.width, copy.height))

    election = _elect((copy, other), current=_current(small, measures=_measures()))
    assert election.issues == (CoverIssue.BELOW_FLOOR,)
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "copy")

    foreign = _elect(
        (copy, other),
        current=_current(small, measures=_measures(), non_english_edition=True),
    )
    assert foreign.issues == (CoverIssue.BELOW_FLOOR, CoverIssue.EDITION_LANGUAGE)
    assert foreign.choice == "other"


def test_square_art_under_the_floor_never_keeps_portrait_art_out() -> None:
    small = COVER_FLOOR_SHORT_EDGE - 1
    election = _elect(
        (_candidate("portrait", (1000, 1500)),),
        medium="audiobook",
        current=_current((small, small)),
    )

    assert election.excluded == ()
    assert election.issues == (CoverIssue.BELOW_FLOOR,)
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "portrait")


def test_the_order_keeps_adr_0059_without_its_portrait_sane_step() -> None:
    election = _elect(
        (
            _candidate("work-large", (1000, 2500), measures=_measures(art=1)),
            _candidate("edition-small", (400, 600), identity=EDITION),
            _candidate("work-wide", (900, 1500), ordinal=0),
            _candidate("work-tall", (750, 1800), ordinal=0),
            _candidate("work-late", (900, 1500), ordinal=1, measures=_measures()),
        )
    )

    assert election.ranked == (
        "edition-small",
        "work-large",
        "work-wide",
        "work-late",
        "work-tall",
    )
    assert (election.outcome, election.choice) == (CoverOutcome.FILL, "edition-small")


def test_omnibus_excludes_a_member_volume_only_for_an_omnibus() -> None:
    candidates = (
        _candidate("volume", (800, 1200), omnibus_volume=True),
        _candidate("whole", PORTRAIT, measures=_measures(art=1)),
    )

    omnibus = _elect(candidates, omnibus=True)
    assert omnibus.excluded == (("volume", CoverExclusion.OMNIBUS_VOLUME),)
    assert omnibus.choice == "whole"

    single = _elect(candidates, omnibus=False)
    assert single.excluded == ()
    assert single.choice == "volume"


def test_a_non_english_edition_is_excluded_and_faults_the_current_cover() -> None:
    election = _elect(
        (
            _candidate("foreign", (800, 1200), non_english_edition=True),
            _candidate("english", PORTRAIT, measures=_measures(art=1)),
        ),
        current=_current(non_english_edition=True),
    )

    assert election.excluded == (("foreign", CoverExclusion.EDITION_LANGUAGE),)
    assert election.issues == (CoverIssue.EDITION_LANGUAGE,)
    assert (election.outcome, election.choice) == (CoverOutcome.REPLACE, "english")


def test_the_first_failing_check_names_an_exclusion() -> None:
    small = COVER_FLOOR_SHORT_EDGE - 1
    election = _elect(
        (
            _candidate("all", (small, small), non_english_edition=True),
            _candidate(
                "language-volume", omnibus_volume=True, non_english_edition=True
            ),
            _candidate("square-volume", SQUARE, omnibus_volume=True),
        ),
        omnibus=True,
    )

    assert election.excluded == (
        ("all", CoverExclusion.BELOW_FLOOR),
        ("language-volume", CoverExclusion.EDITION_LANGUAGE),
        ("square-volume", CoverExclusion.OMNIBUS_VOLUME),
    )


@pytest.mark.parametrize(
    ("differing", "held"),
    [(2, True), (1, False)],
    ids=["majority", "half"],
)
def test_a_title_that_names_another_work_holds_selection(
    differing: int, held: bool
) -> None:
    candidates = tuple(
        _candidate(
            f"c{index}",
            measures=_measures(art=index % 2),
            title_differs=index < differing,
        )
        for index in range(3 if held else 2)
    )

    for protection in CoverProtection:
        election = _elect(
            candidates,
            current=_current(SQUARE),
            protection=protection,
            title_conflict=True,
        )
        if held:
            assert (election.outcome, election.choice) == (CoverOutcome.HOLD, None)
            assert election.reason is CoverIssue.TITLE_NAMES_OTHER_WORK
        else:
            assert election.outcome is not CoverOutcome.HOLD

    without_conflict = _elect(candidates, current=_current(SQUARE))
    assert without_conflict.outcome is not CoverOutcome.HOLD

    empty_target = _elect(candidates, title_conflict=True)
    assert empty_target.outcome is (CoverOutcome.HOLD if held else CoverOutcome.FILL)


def test_an_unreadable_current_cover_is_only_ever_suggested() -> None:
    for protection in CoverProtection:
        election = _elect(
            (_candidate("art"),), current=_current(None), protection=protection
        )
        assert election.issues == (CoverIssue.UNREADABLE,)
        assert (election.outcome, election.choice) == (CoverOutcome.SUGGEST, "art")


def test_a_fault_with_nothing_eligible_keeps_the_current_cover() -> None:
    election = _elect((_candidate("square", SQUARE),), current=_current(SQUARE))

    assert election.issues == (CoverIssue.WRONG_SHAPE,)
    assert (election.outcome, election.choice) == (CoverOutcome.KEEP, None)


def test_issues_are_recorded_in_priority_order() -> None:
    small = COVER_FLOOR_SHORT_EDGE - 1
    election = _elect(
        (_candidate("art", measures=_measures(art=1)),),
        current=_current((small, small), non_english_edition=True),
    )

    assert election.issues == (
        CoverIssue.WRONG_SHAPE,
        CoverIssue.BELOW_FLOOR,
        CoverIssue.EDITION_LANGUAGE,
    )
    assert election.reason is CoverIssue.WRONG_SHAPE


def test_a_known_empty_target_fills_or_stays_empty() -> None:
    assert _elect((_candidate("art"),)).outcome is CoverOutcome.FILL
    empty = _elect((_candidate("square", SQUARE),))
    assert (empty.outcome, empty.choice) == (CoverOutcome.EMPTY, None)


FAULTY = _current(SQUARE)
FAULT_FREE = _current(measures=_measures(art=2))
ALTERNATIVE = _candidate(
    "edition", (800, 1200), identity=EDITION, measures=_measures(art=1)
)


@pytest.mark.parametrize(
    ("protection", "faulty_outcome"),
    [
        (CoverProtection.PROTECTED, CoverOutcome.REPLACE),
        (CoverProtection.UNPROTECTED, CoverOutcome.REPLACE),
        (CoverProtection.PROPOSALS_ONLY, CoverOutcome.SUGGEST),
    ],
    ids=["protected", "unprotected", "proposals_only"],
)
def test_each_mode_with_and_without_an_issue(
    protection: CoverProtection, faulty_outcome: CoverOutcome
) -> None:
    faulty = _elect((ALTERNATIVE,), current=FAULTY, protection=protection)
    assert (faulty.outcome, faulty.choice) == (faulty_outcome, "edition")
    assert faulty.reason is CoverIssue.WRONG_SHAPE
    assert faulty.edition_preferred is False

    fault_free = _elect((ALTERNATIVE,), current=FAULT_FREE, protection=protection)
    assert (fault_free.outcome, fault_free.choice) == (CoverOutcome.KEEP, None)
    assert fault_free.issues == ()


@pytest.mark.parametrize(
    ("protection", "outcome"),
    [
        (CoverProtection.PROTECTED, CoverOutcome.SUGGEST),
        (CoverProtection.UNPROTECTED, CoverOutcome.REPLACE),
        (CoverProtection.PROPOSALS_ONLY, CoverOutcome.SUGGEST),
    ],
    ids=["protected", "unprotected", "proposals_only"],
)
def test_the_edition_option_in_each_mode(
    protection: CoverProtection, outcome: CoverOutcome
) -> None:
    election = _elect(
        (ALTERNATIVE, _candidate("work", (1000, 1500), measures=_measures(art=3))),
        current=FAULT_FREE,
        protection=protection,
        prefer_edition=True,
    )
    assert (election.outcome, election.choice) == (outcome, "edition")
    assert election.edition_preferred is True
    assert election.issues == ()

    edition_current = _elect(
        (ALTERNATIVE,),
        current=replace(FAULT_FREE, identity=EDITION),
        protection=protection,
        prefer_edition=True,
    )
    assert edition_current.outcome is CoverOutcome.KEEP

    same_art_edition = _elect(
        (
            replace(
                ALTERNATIVE,
                width=PORTRAIT[0],
                height=PORTRAIT[1],
                measures=FAULT_FREE.measures,
            ),
        ),
        current=FAULT_FREE,
        protection=protection,
        prefer_edition=True,
    )
    assert same_art_edition.outcome is CoverOutcome.KEEP


def _fixtures() -> list[tuple[str, Callable[..., MeasuredCoverElection]]]:
    width = _margin_widths()[0]
    small = COVER_FLOOR_SHORT_EDGE - 1
    return [
        ("empty", partial(_elect, (ALTERNATIVE,))),
        ("fault_free", partial(_elect, (ALTERNATIVE,), current=FAULT_FREE)),
        ("wrong_shape", partial(_elect, (ALTERNATIVE,), current=FAULTY)),
        (
            "below_floor",
            partial(_elect, (ALTERNATIVE,), current=_current((small, 2 * small))),
        ),
        ("unreadable", partial(_elect, (ALTERNATIVE,), current=_current(None))),
        (
            "edition_language",
            partial(_elect, (ALTERNATIVE,), current=_current(non_english_edition=True)),
        ),
        (
            "sharper_copy",
            partial(
                _elect,
                (_candidate("copy", (width, 3 * width // 2), measures=_measures()),),
                current=_current(measures=_measures()),
            ),
        ),
        (
            "audiobook_portrait",
            partial(
                _elect,
                (_candidate("square", SQUARE),),
                current=_current(PORTRAIT),
                medium="audiobook",
            ),
        ),
        (
            "title_hold",
            partial(
                _elect,
                (_candidate("other", title_differs=True),),
                current=FAULTY,
                title_conflict=True,
            ),
        ),
        (
            "omnibus",
            partial(
                _elect,
                (_candidate("volume", omnibus_volume=True),),
                current=FAULTY,
                omnibus=True,
            ),
        ),
    ]


@pytest.mark.parametrize(
    "run", [run for _, run in _fixtures()], ids=[name for name, _ in _fixtures()]
)
def test_unprotected_decides_as_protected_without_a_preference_source(
    run: Callable[..., MeasuredCoverElection],
) -> None:
    protected = run(protection=CoverProtection.PROTECTED, prefer_edition=False)
    unprotected = run(protection=CoverProtection.UNPROTECTED, prefer_edition=False)

    assert unprotected == protected


def test_the_result_does_not_depend_on_candidate_order() -> None:
    candidates = (
        _candidate("a", (400, 600), measures=_measures()),
        _candidate("b", (800, 1200), measures=_measures(phash_bits=3)),
        _candidate("c", (600, 900), measures=_measures(art=1)),
        _candidate("d", SQUARE),
    )
    forward = _elect(candidates, current=_current(measures=_measures(dhash_bits=2)))
    backward = _elect(
        tuple(reversed(candidates)), current=_current(measures=_measures(dhash_bits=2))
    )

    assert forward == backward


@pytest.mark.parametrize(
    "build",
    [
        lambda: CoverMeasures(
            "0" * 15, "0" * 16, (1,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
        ),
        lambda: CoverMeasures(
            "0" * 16, "G" * 16, (1,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
        ),
        lambda: CoverMeasures(
            "0" * 16, "0" * 16, (0,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
        ),
        lambda: CoverMeasures("0" * 16, "0" * 16, (1,) * 63, (0,) * LAYOUT_VALUES),
        lambda: CoverMeasures(
            "0" * 16, "0" * 16, (1,) * COLOUR_BINS, (256,) * LAYOUT_VALUES
        ),
        lambda: CoverMeasures(
            "0" * 16, "0" * 16, (True,) * COLOUR_BINS, (0,) * LAYOUT_VALUES
        ),
        lambda: _candidate("", PORTRAIT),
        lambda: _candidate("x", PORTRAIT, identity="unverified"),
        lambda: _candidate("x", (0, 600)),
        lambda: _candidate("x", PORTRAIT, ordinal=-1),
        lambda: _candidate("x", PORTRAIT, title_differs=1),  # type: ignore[arg-type]  # reason: proves the exact-type check
        lambda: CurrentCover("current", WORK, readable=True),
        lambda: CurrentCover("current", WORK, readable=False, width=1, height=1),
        lambda: CurrentCover("current", WORK, readable=False, non_english_edition=True),
        lambda: CurrentCover("current", "unverified", readable=True, width=1, height=1),
        lambda: _candidate("x", PORTRAIT, identity=["verified_work_match"]),  # type: ignore[arg-type]  # reason: proves the exact-type check
        lambda: CurrentCover("current", {}, readable=True, width=1, height=1),  # type: ignore[arg-type]  # reason: as above
    ],
    ids=[
        "short_phash",
        "bad_dhash",
        "empty_colour",
        "colour_bins",
        "layout_range",
        "bool_colour",
        "empty_reference",
        "identity",
        "zero_width",
        "negative_ordinal",
        "bool_flag",
        "readable_without_size",
        "unreadable_with_size",
        "unreadable_language",
        "current_identity",
        "list_identity",
        "object_identity",
    ],
)
def test_malformed_values_are_refused(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        build()


def test_malformed_stage_input_is_refused() -> None:
    with pytest.raises(ValueError):
        _elect((_candidate("x"), _candidate("x")))
    with pytest.raises(ValueError):
        _elect((_candidate("current"),), current=_current())
    with pytest.raises(ValueError):
        _elect((_candidate("x"),), medium="book")
    with pytest.raises(ValueError):
        _elect([_candidate("x")])  # type: ignore[arg-type]  # reason: proves the exact-type check
    with pytest.raises(ValueError):
        _elect((_candidate("x"),), protection="protected")  # type: ignore[arg-type]  # reason: as above
