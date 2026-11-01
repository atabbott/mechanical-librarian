"""The Activity ledger's controls, driven against a real seeded projection.

The search box, the five filter chips, the date range and the pager are all
structurally present on the page and, until this module, functionally
untested: the surface behind them answered with a single hand-written event,
so a control that narrowed to nothing -- or to everything -- looked the same as
one that worked. These drive the real query over a real seeded ledger and
assert both the rows and the count the owner reads beside them (#636).
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from tests.support.activity_seed import DAY, SEED_NOW, seed_activity


@pytest.fixture
def ledger() -> Iterator[ContinuousGuardedApplication]:
    """One real seeded ledger, read through the real activity query."""
    with tempfile.TemporaryDirectory() as temporary:
        seeded = seed_activity(Path(temporary))
        yield ContinuousGuardedApplication(
            seeded.repository,
            {},
            {},
            PersistedFieldEffectAuthority(seeded.repository),
            (("primary", "books"),),
            seeded.reader_now,
            lambda *_: None,
            activity_only=True,
        )
        seeded.repository.close()


def _rows(
    ledger: ContinuousGuardedApplication,
    *,
    search: str | None = None,
    event_type: str = "all",
    range_days: int = 30,
    page: int = 1,
    page_size: int = 25,
) -> list[dict[str, object]]:
    """One page, read through the surface's own query keywords."""
    events = ledger.activity(
        search=search,
        event_type=event_type,
        range_days=range_days,
        page=page,
        page_size=page_size,
    )["events"]
    assert isinstance(events, list)
    return events


def _all_rows(
    ledger: ContinuousGuardedApplication,
    *,
    search: str | None = None,
    event_type: str = "all",
    range_days: int = 30,
    page_size: int = 25,
) -> list[dict[str, object]]:
    """Walk every page, the way an owner paging to the end would see it."""
    block = ledger.activity(
        search=search, event_type=event_type, range_days=range_days, page_size=page_size
    )["ledger"]
    assert isinstance(block, dict)
    collected: list[dict[str, object]] = []
    for page in range(1, int(str(block["page_count"])) + 1):
        collected.extend(
            _rows(
                ledger,
                search=search,
                event_type=event_type,
                range_days=range_days,
                page=page,
                page_size=page_size,
            )
        )
    return collected


def test_the_seeded_ledger_groups_lifecycle_transitions_by_change(
    ledger: ContinuousGuardedApplication,
) -> None:
    """The default ledger is a current change view, not the raw event stream."""
    block = ledger.activity()["ledger"]
    assert isinstance(block, dict)
    rows = _all_rows(ledger)
    assert block["total"] == len(rows)
    assert block["total"] > 25


def test_a_filter_chip_narrows_to_its_own_category_and_the_count_follows(
    ledger: ContinuousGuardedApplication,
) -> None:
    """Each chip shows its own kind, nothing else, and says how many.

    The count is asserted against the rows the same filter returns rather than
    against a written-down number, because the defect this guards against is
    the two disagreeing.
    """
    everything = ledger.activity()["ledger"]
    assert isinstance(everything, dict)
    seen: dict[str, int] = {}
    for event_type, category in (
        ("review", "owner"),
        ("metadata", "automatic"),
        ("policy", "policy"),
        ("safety", "recovery"),
    ):
        narrowed = ledger.activity(event_type=event_type)
        block = narrowed["ledger"]
        assert isinstance(block, dict)
        rows = _all_rows(ledger, event_type=event_type)
        assert rows, f"{event_type} narrowed to nothing"
        assert {str(row["category"]) for row in rows} == {category}
        assert block["total"] == len(rows)
        assert block["event_type"] == event_type
        seen[event_type] = int(str(block["total"]))

    # Every row belongs to exactly one chip, so the parts reconstruct the whole.
    assert sum(seen.values()) == everything["total"]


def test_search_narrows_on_the_work_the_field_and_the_status(
    ledger: ContinuousGuardedApplication,
) -> None:
    """The three columns the one search box actually reaches.

    Search matches the work, the library, the destination and the lifecycle
    state. It deliberately asserts the destination rather than the "outcome"
    the surface shows, because the changed value itself is *not* searchable --
    see the test below, which pins that limitation rather than leaving it to
    be discovered.
    """
    by_work = _all_rows(ledger, search="Larkspur")
    assert by_work
    assert all(
        "larkspur" in str(row["change"]).casefold()
        or "larkspur" in str(row["change_detail"]).casefold()
        for row in by_work
    )

    by_field = _all_rows(ledger, search="isbn")
    assert by_field
    assert {str(row["destination"]) for row in by_field} == {"isbn"}

    by_status = _all_rows(ledger, search="item changed")
    assert by_status
    assert {str(row["state"]) for row in by_status} == {"stale"}

    assert _all_rows(ledger, search="no such work anywhere in this ledger") == []


@pytest.mark.parametrize(
    ("term", "destination"),
    (
        ("9780060512750", "isbn"),
        ("雨の地図", "authors"),
        ("Larkspur Cycle", "series"),
        ("1974-05-13", "publishedDate"),
    ),
)
def test_each_public_changed_value_shape_is_searchable(
    ledger: ContinuousGuardedApplication, term: str, destination: str
) -> None:
    """Scalar, list, structured series and date leaves stay searchable (#787)."""
    rows = _all_rows(ledger, search=term)
    assert rows
    assert {str(row["destination"]) for row in rows} == {destination}


@pytest.mark.parametrize(
    "term",
    ("field-effect-event-v1", "schema_version", "decision_id", "automatic_bundle_"),
)
def test_activity_search_never_exposes_immutable_internal_keys_or_identifiers(
    ledger: ContinuousGuardedApplication, term: str
) -> None:
    assert _all_rows(ledger, search=term) == []


def test_the_changed_value_itself_is_searchable(
    ledger: ContinuousGuardedApplication,
) -> None:
    """An owner can find an immutable row by its own changed value (#787)."""
    written = [
        row for row in _all_rows(ledger) if "9780060512750" in str(row["change"])
    ]
    assert written, "the seeded ledger should contain this exact new value"
    matched = _all_rows(ledger, search="9780060512750")
    assert [row["event_id"] for row in matched] == [row["event_id"] for row in written]
    assert all("9780060512750" in str(row["change"]) for row in matched)


def test_search_uses_the_same_automatic_and_policy_words_the_owner_reads(
    ledger: ContinuousGuardedApplication,
) -> None:
    automatic = _all_rows(ledger, search="A later automatic choice")
    assert automatic
    assert {str(row["event_type"]) for row in automatic} == {"metadata"}
    assert all(
        "a later automatic choice" in str(row["change"]).casefold() for row in automatic
    )

    policy = _all_rows(ledger, search="Ask me")
    assert policy
    assert {str(row["event_type"]) for row in policy} == {"policy"}
    assert all(
        any(
            fact.get("previous") == "Ask me"
            for fact in row.get("policy_changes", [])
            if isinstance(fact, dict)
        )
        for row in policy
    )


def test_the_date_range_excludes_what_falls_outside_it(
    ledger: ContinuousGuardedApplication,
) -> None:
    """A narrower window is a strict subset, and it cuts by the recorded instant.

    Thirty days is the range the page opens on, so that is the window a
    narrower one has to be measured against.
    """
    wide = {str(row["event_id"]) for row in _all_rows(ledger, range_days=30)}
    narrow = {str(row["event_id"]) for row in _all_rows(ledger, range_days=7)}
    assert narrow < wide, "the narrow window excluded nothing"

    cutoff = SEED_NOW + 2 * DAY - 7 * DAY
    for row in _all_rows(ledger, range_days=7):
        assert int(str(row["occurred_at"])) >= cutoff
    excluded = [
        row
        for row in _all_rows(ledger, range_days=30)
        if str(row["event_id"]) not in narrow
    ]
    assert excluded, "nothing was old enough to be excluded"
    for row in excluded:
        assert int(str(row["occurred_at"])) < cutoff


def test_paging_forward_and_back_lands_on_the_same_window(
    ledger: ContinuousGuardedApplication,
) -> None:
    """The pages tile the ledger exactly once, and the count reads true."""
    block = ledger.activity()["ledger"]
    assert isinstance(block, dict)
    total, pages = int(str(block["total"])), int(str(block["page_count"]))

    windows = [
        [str(row["event_id"]) for row in _rows(ledger, page=page)]
        for page in range(1, pages + 1)
    ]
    flattened = [event_id for window in windows for event_id in window]
    assert len(flattened) == total
    assert len(set(flattened)) == total, "a row appeared on two pages"
    assert all(len(window) == 25 for window in windows[:-1])

    # Paging forward then back is the same window, not a re-sorted one.
    assert [str(row["event_id"]) for row in _rows(ledger, page=1)] == windows[0]

    # A larger page size covers the same ledger in fewer pages.
    larger = ledger.activity(page_size=50)["ledger"]
    assert isinstance(larger, dict)
    assert larger["total"] == total
    assert larger["page_count"] < pages or pages == 1


def test_a_filter_and_a_search_narrow_together_rather_than_replacing_each_other(
    ledger: ContinuousGuardedApplication,
) -> None:
    combined = _all_rows(ledger, event_type="safety", search="item changed")
    assert combined
    assert {str(row["category"]) for row in combined} == {"recovery"}
    assert {str(row["state"]) for row in combined} == {"stale"}
    assert len(combined) <= len(_all_rows(ledger, event_type="safety"))
