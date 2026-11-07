"""Hardcover answers an audiobook's companion EPUB ISBN end to end (#1468).

The companion ISBN lane runs through the real adapter: the edition rows name
the book, which is kept at work shape only when it is this work or lists an
audio edition counted against the file, and that book's audio editions join
the observation. The report built from the observation replays the rule.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import httpx

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    audiobook_discovery_plan,
    companion_binding_plan,
)
from mechanical_librarian.application.provider_retry_successor import (
    GenerateProviderRetrySuccessor,
)
from mechanical_librarian.domain import metadata_report as dom
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    CompanionEbook,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetFacts,
)
from tests.support.audiobook_providers import (
    audio_target,
    local_runtime_claim,
    target_title_claim,
)
from tests.support.hardcover_evidence import hardcover_answer
from tests.support.metadata_report_doubles import single_track_audio_facts

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
_LOCAL_SECONDS = 58_244
_ISBN = "9780306406157"
_OTHER_ISBN = "9780140328721"
_COMPANION_BOOK = 20
_SEARCH_BOOK = 10


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


def _companion() -> CompanionEbook:
    return CompanionEbook(
        "e" * 64,
        "c" * 64,
        (
            _companion_claim(
                ClaimKind.ISBN,
                _ISBN,
                applicability="ebook",
                checksum="valid_isbn_13",
                applicability_basis="ebook",
                scope=ClaimScope.EDITION,
            ),
        ),
    )


def _plan() -> MetadataDiscoveryPlan:
    return companion_binding_plan(
        audiobook_discovery_plan(
            audio_target(_TITLE, (_AUTHOR,)),
            (target_title_claim(_TITLE), local_runtime_claim()),
            audio=single_track_audio_facts(),
            companion=_companion(),
        ),
        "hardcover",
    )


def _book(book_id: int, title: str) -> dict[str, object]:
    return {
        "id": book_id,
        "title": title,
        "contributions": [
            {
                "id": book_id * 10,
                "contribution": None,
                "author": {"id": 7, "name": _AUTHOR},
            }
        ],
    }


def _edition(
    edition_id: int,
    seconds: int,
    *,
    book_id: int = _COMPANION_BOOK,
    asin: str | None = None,
) -> dict[str, object]:
    return {
        "id": edition_id,
        "book_id": book_id,
        "asin": asin,
        "audio_seconds": seconds,
        "contributions": [
            {"id": 7, "contribution": "Author", "author": {"id": 7, "name": _AUTHOR}},
            {
                "id": 100,
                "contribution": "Narrator",
                "author": {"id": 100, "name": "Imra Vossel"},
            },
        ],
    }


def _observe(
    *,
    companion_title: str = "A Different Title",
    isbn: str = _ISBN,
    isbn_rows: bool = True,
    search_books: tuple[int, ...] = (),
    editions: Callable[[int, int], httpx.Response],
    plan: MetadataDiscoveryPlan | None = None,
    isbn_fails: bool = False,
) -> tuple[ProviderObservation, list[str]]:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["operationName"])
        if body["operationName"] == "AudioEditions":
            return editions(body["variables"]["book"], body["variables"]["offset"])
        if body["operationName"] == "MetadataEvidenceByIsbn":
            if isbn_fails:
                return httpx.Response(500, json={})
            return httpx.Response(
                200,
                json=hardcover_answer(
                    request,
                    [
                        {
                            "id": 900,
                            "book_id": _COMPANION_BOOK,
                            "isbn_13": isbn,
                            "title": companion_title,
                        }
                    ]
                    if isbn_rows
                    else [],
                    [_book(_COMPANION_BOOK, companion_title)],
                ),
            )
        return httpx.Response(
            200,
            json=hardcover_answer(
                request, [], [_book(book_id, _TITLE) for book_id in search_books]
            ),
        )

    observation = (
        HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            262_144,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _seconds: None,
            jitter=lambda: 0.0,
        )
        .observe(_plan() if plan is None else plan)
        .observation
    )
    return observation, sent


def _page(*rows: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": {"editions": list(rows)}})


def _report(observation: ProviderObservation) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "audiobooks",
            "item-1",
            audio_target(_TITLE, (_AUTHOR,)).fields,
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(target_title_claim(_TITLE), local_runtime_claim()),
        local_reasons=("no_local_audio_claims",),
        # The generator records how each audio lane's dispatch ended.
        providers=(
            replace(
                observation,
                # An adapter's answer is bound to its authority after it returns.
                authority="hardcover",
                lanes=tuple(
                    replace(
                        lane,
                        dispatch=_dispatch(lane),
                    )
                    for lane in observation.lanes
                ),
            ),
        ),
        audio=single_track_audio_facts(),
        companion=_companion(),
    )


def _isbn_lane(observation: ProviderObservation):
    return next(
        lane for lane in observation.lanes if lane.kind is ProviderQueryKind.ISBN
    )


def test_the_companion_book_and_its_counted_edition_are_kept() -> None:
    observation, sent = _observe(
        editions=lambda book, _offset: _page(
            _edition(3, _LOCAL_SECONDS + 40, book_id=book, asin="B0LINKED01")
        )
    )

    assert sent[:2] == ["MetadataEvidenceByIsbn", "AudioEditions"]
    lane = _isbn_lane(observation)
    assert lane.status is ProviderStatus.CANDIDATES
    # The book, at work shape: never the ebook edition the ISBN names.
    assert [value.edition_identity for value in lane.candidates] == [
        "hardcover-work-0000000020"
    ]
    (edition,) = observation.audio_editions
    assert edition.work_identity == "hardcover-work-0000000020"
    assert any(
        claim.kind is ClaimKind.ASIN and claim.value == "B0LINKED01"
        for claim in edition.claims
    )
    assert not any(
        claim.value == _ISBN
        for candidate in observation.candidates
        for claim in candidate.claims
    )
    # The report replays the companion rule against the observation.
    report = _report(observation)
    assert MetadataReport.from_json(report.to_json()) == report


def test_another_book_with_no_counted_edition_is_not_kept() -> None:
    observation, _sent = _observe(
        editions=lambda book, _offset: _page(
            # Outside the tight band: another recording links nothing.
            _edition(3, _LOCAL_SECONDS + 3_600, book_id=book, asin="B0OTHER001")
        )
    )

    assert _isbn_lane(observation).status is ProviderStatus.NO_MATCH
    assert observation.audio_editions == ()
    _report(observation)


def test_a_failed_edition_page_keeps_another_book_out() -> None:
    observation, _sent = _observe(
        editions=lambda _book, _offset: httpx.Response(500, json={})
    )

    assert _isbn_lane(observation).status is ProviderStatus.NO_MATCH
    assert observation.audio_editions == ()
    _report(observation)


def test_this_work_is_kept_by_title_and_authors_without_an_edition() -> None:
    observation, _sent = _observe(
        companion_title=_TITLE,
        editions=lambda _book, _offset: httpx.Response(500, json={}),
    )

    lane = _isbn_lane(observation)
    assert lane.status is ProviderStatus.CANDIDATES
    assert observation.audio_editions == ()
    _report(observation)


def test_an_edition_row_of_another_isbn_names_nothing() -> None:
    observation, _sent = _observe(
        isbn=_OTHER_ISBN,
        editions=lambda book, _offset: _page(
            _edition(3, _LOCAL_SECONDS, book_id=book, asin="B0LINKED01")
        ),
    )

    assert _isbn_lane(observation).status is not ProviderStatus.CANDIDATES
    assert observation.audio_editions == ()
    _report(observation)


def test_a_book_no_edition_row_of_the_isbn_names_is_not_kept() -> None:
    """A book with no edition row joining it to the ISBN names nothing."""
    observation, _sent = _observe(
        isbn_rows=False,
        editions=lambda book, _offset: _page(
            _edition(3, _LOCAL_SECONDS, book_id=book, asin="B0LINKED01")
        ),
    )

    assert _isbn_lane(observation).status is not ProviderStatus.CANDIDATES
    assert observation.audio_editions == ()
    _report(observation)


def test_a_book_linked_only_by_editions_the_observation_drops_is_dropped() -> None:
    """Past the bound the observation keeps no edition, so no link stands."""
    # 10 editions per book: 20 together pass the bound of 16.

    def editions(book: int, _offset: int) -> httpx.Response:
        return _page(
            *(
                _edition(
                    book * 100 + index,
                    _LOCAL_SECONDS + index,
                    book_id=book,
                    asin=f"B0E{book:03d}{index:04d}",
                )
                for index in range(10)
            )
        )

    observation, _sent = _observe(search_books=(_SEARCH_BOOK,), editions=editions)

    assert _isbn_lane(observation).status is ProviderStatus.NO_MATCH
    # Dropping the companion book drops its editions, so the search's own
    # book keeps its editions again under the bound.
    assert {value.work_identity for value in observation.audio_editions} == {
        "hardcover-work-0000000010"
    }
    # The search's own book stays; the report replays honestly.
    assert [value.identity for value in observation.candidates] == [
        "hardcover-work-0000000010"
    ]
    _report(observation)


def test_a_retry_refits_the_companion_book_against_the_merged_editions() -> None:
    """A retry refits the companion books against what its merge keeps.

    The search's book holds sixteen audio editions and the companion lookup
    failed. Its retry finds another book linked by one counted edition, so the
    merge reads seventeen, past the bound, and keeps none. The companion book
    stood only on that edition, so it is dropped and the report publishes
    (#1468).
    """

    def editions(book: int, offset: int) -> httpx.Response:
        if offset:
            return _page()
        if book == _COMPANION_BOOK:
            return _page(_edition(3, _LOCAL_SECONDS + 40, book_id=book))
        return _page(
            *(
                _edition(1_000 + index, _LOCAL_SECONDS + index, book_id=book)
                for index in range(16)
            )
        )

    plan = _plan()
    original, _sent = _observe(
        search_books=(_SEARCH_BOOK,), editions=editions, isbn_fails=True
    )
    assert _isbn_lane(original).status is not ProviderStatus.CANDIDATES
    assert len(original.audio_editions) == 16
    selected = tuple(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
    replacement, _sent = _observe(editions=editions, plan=replace(plan, lanes=selected))
    assert _isbn_lane(replacement).status is ProviderStatus.CANDIDATES

    merged = GenerateProviderRetrySuccessor._merged(
        cast("Any", GenerateProviderRetrySuccessor),
        plan,
        replace(original, authority="hardcover"),
        selected,
        replace(replacement, authority="hardcover"),
    )

    assert _isbn_lane(merged).status is ProviderStatus.NO_MATCH
    # Dropping the companion book drops its edition, so the search's book
    # keeps its sixteen again under the bound.
    assert {value.work_identity for value in merged.audio_editions} == {
        "hardcover-work-0000000010"
    }
    assert len(merged.audio_editions) == 16
    assert [value.identity for value in merged.candidates] == [
        "hardcover-work-0000000010"
    ]
    report = _report(merged)
    assert MetadataReport.from_json(report.to_json()) == report
