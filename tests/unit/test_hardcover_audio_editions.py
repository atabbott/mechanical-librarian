"""Hardcover audio editions witness which recording an audiobook is (#962).

An audiobook WORK lane that admits a Hardcover book asks for that book's
audio-format editions and keeps those that credit a narrator and state a
runtime inside the tight band of the file's own (ADR-0090).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import MetadataDiscoveryPlan
from mechanical_librarian.domain.epub_inspection import DateEvent
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    DateLinkConfidence,
    FieldState,
    ProviderObservation,
    ProviderStatus,
    _provider_from_data,
)
from tests.support.audiobook_providers import local_runtime_claim, work_plan
from tests.support.hardcover_evidence import (
    books_envelope,
    hardcover_answer,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
# 58244.201 s on the file.
_LOCAL_SECONDS = 58_244


def _plan(*, audio: bool = True) -> MetadataDiscoveryPlan:
    plan = work_plan(local_runtime_claim())
    return replace(plan, audio=single_track_audio_facts() if audio else None)


def _work_response(book_ids: tuple[int, ...] = (10,)) -> dict[str, object]:
    return books_envelope(
        [
            {
                "id": book_id,
                "title": _TITLE,
                "contributions": [
                    {
                        "id": book_id * 10,
                        "contribution": None,
                        "author": {"id": 7, "name": _AUTHOR},
                    }
                ],
            }
            for book_id in book_ids
        ],
    )


def _edition(
    edition_id: int,
    seconds: int | None,
    narrators: tuple[str, ...] = ("Imra Vossel",),
    *,
    book_id: int = 10,
    asin: str | None = None,
) -> dict[str, object]:
    return {
        "id": edition_id,
        "book_id": book_id,
        "asin": asin,
        "audio_seconds": seconds,
        "contributions": [
            {"id": 7, "contribution": "Author", "author": {"id": 7, "name": _AUTHOR}},
            *(
                {
                    "id": 100 + index,
                    "contribution": "Narrator",
                    "author": {"id": 100 + index, "name": name},
                }
                for index, name in enumerate(narrators)
            ),
        ],
    }


def _observe(
    pages: Callable[[int, int], httpx.Response],
    plan: MetadataDiscoveryPlan | None = None,
    books: tuple[int, ...] = (10,),
) -> tuple[ProviderObservation, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body["operationName"] == "AudioEditions":
            variables = body["variables"]
            return pages(variables["book"], variables["offset"])
        return httpx.Response(200, json=_work_response(books))

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
        .observe(plan or _plan())
        .observation
    )
    return observation, sent


def _page(*rows: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": {"editions": list(rows)}})


def test_counted_editions_are_kept_with_runtime_narrator_and_asin() -> None:
    observation, sent = _observe(
        lambda _book, _offset: _page(
            _edition(3, _LOCAL_SECONDS + 40, asin="B0EDITION1"),
            # Outside the tight band: another recording, never kept.
            _edition(4, _LOCAL_SECONDS + 3_600, ("Other Reader",)),
            # No narrator credit: nothing to say which recording it is.
            _edition(5, _LOCAL_SECONDS, ()),
            # No runtime.
            _edition(6, None),
        )
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert ["BooksByTitleAuthor", "AudioEditions"] == [
        body["operationName"] for body in sent
    ]
    assert {"book": 10, "offset": 0} == sent[1]["variables"]
    (edition,) = observation.audio_editions
    assert "hardcover-edition-0000000003" == edition.identity
    assert "hardcover-work-0000000010" == edition.work_identity
    assert [
        (ClaimKind.ASIN, None, "B0EDITION1"),
        (ClaimKind.DURATION, None, f"{_LOCAL_SECONDS + 40}.000"),
        (ClaimKind.CONTRIBUTOR, "narrator", "Imra Vossel"),
    ] == [(claim.kind, claim.role, claim.value) for claim in edition.claims]
    # The editions survive a round trip through the report's provider data.
    assert observation == _provider_from_data(observation.to_data())


def test_an_ebook_lane_never_asks_for_audio_editions() -> None:
    observation, sent = _observe(
        lambda _book, _offset: _page(_edition(3, _LOCAL_SECONDS)),
        _plan(audio=False),
    )

    assert ["BooksByTitleAuthor"] == [body["operationName"] for body in sent]
    assert () == observation.audio_editions


def test_a_failed_page_keeps_the_admitted_work_and_no_edition() -> None:
    observation, sent = _observe(lambda _book, _offset: httpx.Response(500, json={}))

    assert ProviderStatus.CANDIDATES is observation.status
    assert ["hardcover-work-0000000010"] == [
        candidate.identity for candidate in observation.candidates
    ]
    assert () == observation.audio_editions
    assert "AudioEditions" == sent[-1]["operationName"]


def test_a_row_naming_another_book_drops_every_edition() -> None:
    observation, _sent = _observe(
        lambda _book, _offset: _page(
            _edition(3, _LOCAL_SECONDS), _edition(4, _LOCAL_SECONDS, book_id=11)
        )
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert () == observation.audio_editions


def test_a_full_page_reads_the_next_and_two_full_pages_keep_nothing() -> None:
    def full(book: int, offset: int) -> httpx.Response:
        return _page(
            *(
                _edition(offset + index + 1, _LOCAL_SECONDS + 3_600, book_id=book)
                for index in range(100)
            )
        )

    observation, sent = _observe(full)

    assert [0, 100] == [
        body["variables"]["offset"]
        for body in sent
        if body["operationName"] == "AudioEditions"
    ]
    assert () == observation.audio_editions

    def short_second(book: int, offset: int) -> httpx.Response:
        rows = 100 if offset == 0 else 1
        return _page(
            *(
                _edition(
                    offset + index + 1,
                    _LOCAL_SECONDS if offset else _LOCAL_SECONDS + 3_600,
                    book_id=book,
                )
                for index in range(rows)
            )
        )

    observation, _sent = _observe(short_second)
    assert ["hardcover-edition-0000000101"] == [
        edition.identity for edition in observation.audio_editions
    ]


def test_one_book_overflowing_its_pages_drops_the_other_books_editions() -> None:
    def pages(book: int, offset: int) -> httpx.Response:
        if book == 20:
            return _page(_edition(9, _LOCAL_SECONDS, book_id=20))
        return _page(
            *(
                _edition(offset + index + 1, _LOCAL_SECONDS + 3_600, book_id=book)
                for index in range(100)
            )
        )

    observation, sent = _observe(pages, books=(10, 20))

    assert ProviderStatus.CANDIDATES is observation.status
    assert 2 == len(observation.candidates)
    assert () == observation.audio_editions
    assert [(10, 0), (10, 100)] == [
        (body["variables"]["book"], body["variables"]["offset"])
        for body in sent
        if body["operationName"] == "AudioEditions"
    ]


def test_an_isbn_shaped_edition_asin_is_kept() -> None:
    # Audible gives some of its own productions an ISBN-10 as their ASIN; the
    # work-level rule refuses those, but an edition's lead needs it (#962).
    observation, _sent = _observe(
        lambda _book, _offset: _page(
            _edition(3, _LOCAL_SECONDS, asin="1400193346"),
            _edition(4, _LOCAL_SECONDS, asin="not-an-asin"),
        )
    )

    assert [("1400193346",), ()] == [
        tuple(claim.value for claim in edition.claims if claim.kind is ClaimKind.ASIN)
        for edition in observation.audio_editions
    ]


def _dated_observation(
    release_date: object, plan: MetadataDiscoveryPlan
) -> ProviderObservation:
    """One book stating `release_date`, with one edition row for an ebook lane."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["operationName"] == "AudioEditions":
            return _page()
        return httpx.Response(
            200,
            json=hardcover_answer(
                request,
                [] if plan.audio is not None else [{"id": 3, "book_id": 10}],
                [
                    {
                        "id": 10,
                        "title": _TITLE,
                        "release_date": release_date,
                        "contributions": [
                            {
                                "id": 100,
                                "contribution": None,
                                "author": {"id": 7, "name": _AUTHOR},
                            }
                        ],
                    }
                ],
            ),
        )

    return (
        HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(handler)),
            262_144,
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _seconds: None,
            jitter=lambda: 0.0,
        )
        .observe(plan)
        .observation
    )


@pytest.mark.parametrize(
    ("release_date", "precision"),
    (("1999-05-01", "day"), ("1999-05", "month"), ("1999", "year")),
)
def test_an_audiobook_work_states_its_first_publication_last(
    release_date: str, precision: str
) -> None:
    """The book's release date is the audiobook work's first publication (#1642)."""
    (work,) = _dated_observation(release_date, _plan()).candidates
    without = _dated_observation(None, _plan()).candidates[0]
    *earlier, date = work.claims
    # Every claim the work already stated keeps its place.
    assert tuple(earlier) == without.claims
    assert (ClaimKind.PUBLICATION_DATE, ClaimScope.WORK) == (date.kind, date.scope)
    assert (date.value, date.date_lexical) == (release_date, release_date)
    assert DateEvent.WORK_FIRST_PUBLICATION is date.date_event
    assert precision == date.date_precision.value  # type: ignore[union-attr]
    assert DateLinkConfidence.EXPLICIT is date.date_confidence
    assert FieldState.VALID is date.state


def test_an_unreadable_release_date_is_stated_malformed() -> None:
    (work,) = _dated_observation("sometime", _plan()).candidates
    date = work.claims[-1]
    assert (ClaimKind.PUBLICATION_DATE, FieldState.MALFORMED, None) == (
        date.kind,
        date.state,
        date.value,
    )
    assert DateEvent.WORK_FIRST_PUBLICATION is date.date_event


def test_an_ebook_lane_states_no_work_first_publication_claim() -> None:
    observation = _dated_observation("1999-05-01", _plan(audio=False))
    assert observation.candidates
    assert not any(
        claim.kind is ClaimKind.PUBLICATION_DATE and claim.scope is ClaimScope.WORK
        for candidate in observation.candidates
        for claim in candidate.claims
    )
