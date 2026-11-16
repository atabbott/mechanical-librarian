"""Owner search and owner link events are value-free (#1477 S17).

The query text, the work's title and item, a record identity, a cover URL and
a path never reach stderr: only the allow-listed keys with closed values do.
"""

from __future__ import annotations

import json

import pytest

from mechanical_librarian.application.failure_diagnostics import (
    emit_owner_events,
    owner_link_event,
    owner_search_event,
)
from mechanical_librarian.application.owner_search import OwnerSearchRefused
from mechanical_librarian.domain.owner_link import (
    OwnerSearchAnswer,
    OwnerSearchCover,
    OwnerSearchFailure,
)
from tests.unit.test_owner_search_service import (
    TOKEN,
    _harness,
    _record,
    _request,
)

_SENTINELS = (
    "Sentinel Query Title",
    "Sentinel Author",
    "sentinel-item-identity",
    "/works/OL424242W",
    "https://covers.openlibrary.org/b/id/424242-L.jpg",
    "/srv/sentinel/path",
)


def _lines(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines() if line]


def test_a_searchs_events_carry_no_query_title_identity_or_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = _record("/works/OL424242W")
    harness = _harness(
        lambda _: OwnerSearchAnswer(
            (
                type(record)(
                    record.candidate,
                    record.identity,
                    cover=OwnerSearchCover(_SENTINELS[4]),
                ),
            ),
            more=True,
            dropped=2,
        )
    )
    harness.service.search(
        TOKEN, _request(title=_SENTINELS[0], authors=(_SENTINELS[1],))
    )
    with pytest.raises(OwnerSearchRefused):
        harness.service.search(TOKEN, _request(item=_SENTINELS[2]))
    stderr = capsys.readouterr().err
    assert not any(value in stderr for value in _SENTINELS)
    assert _lines(stderr) == [
        {
            "adapter": "open_library",
            "cards": 1,
            "dropped": 2,
            "event": "owner_search",
            "lane": "title_authors",
            "more": 1,
            "outcome": "cards",
        },
        {
            "adapter": "open_library",
            "cards": 0,
            "dropped": 0,
            "event": "owner_search",
            "lane": "title_authors",
            "more": 0,
            "outcome": "work_not_found",
        },
    ]


def test_a_failed_search_names_only_its_closed_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness = _harness(
        lambda _: OwnerSearchAnswer(failure=OwnerSearchFailure.UNREADABLE)
    )
    with pytest.raises(OwnerSearchRefused):
        harness.service.search(TOKEN, _request(title=_SENTINELS[0]))
    (line,) = _lines(capsys.readouterr().err)
    assert line["outcome"] == "search_failed"


def test_a_value_outside_the_allow_list_is_written_as_null(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_owner_events(
        owner_search_event,
        adapter=_SENTINELS[5],
        lane=_SENTINELS[0],
        outcome=_SENTINELS[1],
        cards=99,
    )
    emit_owner_events(
        owner_link_event,
        adapter="hardcover",
        scope=_SENTINELS[3],
        action="link",
        outcome="recorded",
    )
    stderr = capsys.readouterr().err
    assert not any(value in stderr for value in _SENTINELS)
    search, link = _lines(stderr)
    assert (search["adapter"], search["lane"], search["outcome"], search["cards"]) == (
        None,
        None,
        None,
        None,
    )
    assert link == {
        "action": "link",
        "adapter": "hardcover",
        "event": "owner_link",
        "outcome": "recorded",
        "scope": None,
    }


def test_a_withdraw_event_names_no_source(capsys: pytest.CaptureFixture[str]) -> None:
    emit_owner_events(
        owner_link_event,
        adapter=None,
        scope=None,
        action="withdraw",
        outcome="recorded",
    )
    (line,) = _lines(capsys.readouterr().err)
    assert (line["adapter"], line["scope"], line["action"]) == (None, None, "withdraw")
