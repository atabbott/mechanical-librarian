"""Owner search admission, holding and choosing on fake clocks (#1477 S11-S16).

The service is driven with a fake wall clock, a fake monotonic clock, the
real shared pacer on that clock, a scripted adapter and an in-memory work
store, so each limit is proved at its exact edge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderRequestPacer,
)
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
)
from mechanical_librarian.application.owner_search import (
    SEARCH_TTL_SECONDS,
    SEARCHES_PER_HOUR,
    OwnerSearchLimits,
    OwnerSearchRefused,
    OwnerSearchRequest,
    OwnerSearchResult,
    OwnerSearchService,
)
from mechanical_librarian.application.review_application import ReviewApplication
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerSearchAnswer,
    OwnerSearchCover,
    OwnerSearchFailure,
    OwnerSearchQuery,
    OwnerSearchRecord,
)

CONNECTION, LIBRARY, ITEM = "abs", "books", "item-1"
TOKEN, OTHER = "session-token-a", "session-token-b"
WALL = 1_800_000_000.0


def _report(item: str = ITEM, title: str = "Current title") -> str:
    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada",))
        if value.destination is TargetDestination.AUTHORS
        else value
        for value in default_target_fields(title)
    )
    return MetadataReport.create(
        target=TargetFacts(CONNECTION, LIBRARY, item, fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed=title,
                canonical=title,
                excerpt=title,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        ),
        local_reasons=("opening_matter_title",),
        providers=(),
    ).to_json()


def _record(identity: str, *, cover: bool = True) -> OwnerSearchRecord:
    candidate = f"open-library-work-{identity.removeprefix('/works/')}"
    claims = (
        ProviderClaim.create(
            provider="open_library",
            candidate=candidate,
            kind=ClaimKind.TITLE,
            value="Current title",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            raw_order=0,
        ),
        ProviderClaim.create(
            provider="open_library",
            candidate=candidate,
            kind=ClaimKind.CREATOR,
            value="Ada",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=1,
        ),
    )
    return OwnerSearchRecord(
        ProviderCandidate(candidate, candidate, claims),
        identity,
        cover=OwnerSearchCover("https://covers.openlibrary.org/b/id/1-L.jpg")
        if cover
        else None,
    )


@dataclass
class _Clocks:
    wall: float = WALL
    mono: float = 100.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


@dataclass
class _Works:
    documents: dict[str, str] = field(default_factory=lambda: {ITEM: _report()})
    link_row_id: str | None = None
    could_not_finish: bool = False
    reads: int = 0

    def read_owner_search_work(
        self, connection: str, library: str, item: str
    ) -> tuple[str, str | None, bool] | None:
        self.reads += 1
        if (connection, library) != (CONNECTION, LIBRARY) or item not in self.documents:
            return None
        return self.documents[item], self.link_row_id, self.could_not_finish


@dataclass
class _Source:
    answer: Callable[[OwnerSearchQuery], OwnerSearchAnswer]
    queries: list[OwnerSearchQuery] = field(default_factory=list)

    def owner_search(
        self, query: OwnerSearchQuery, *, deadline: float
    ) -> OwnerSearchAnswer:
        self.queries.append(query)
        return self.answer(query)


_PROVIDERS = (
    ProviderConnectionConfiguration("gb", True, False, None, adapter="google_books"),
    ProviderConnectionConfiguration("inv", True, True, None, adapter="inventaire"),
    ProviderConnectionConfiguration(
        "ol", True, True, None, adapter="open_library", contact_email="o@example.org"
    ),
)


class _Configuration:
    limits = SimpleNamespace(timeout_seconds=30)

    def __init__(self, providers: tuple[ProviderConnectionConfiguration, ...]):
        self.providers = providers

    def select(self, connection: str, library: str) -> tuple[Any, ...]:
        if (connection, library) != (CONNECTION, LIBRARY):
            raise ConfigurationError("requested connection or library is unavailable")
        selected = LibraryConfiguration(
            LIBRARY,
            CONNECTION,
            "library-1",
            "book",
            None,
            tuple(sorted(value.name for value in self.providers)),
        )
        return None, selected, None, self.providers


@dataclass
class _Harness:
    service: OwnerSearchService
    clocks: _Clocks
    source: _Source
    works: _Works
    pacer: ProviderRequestPacer
    fetched: list[OwnerSearchCover]
    throttling: dict[str, object]


def _harness(
    answer: Callable[[OwnerSearchQuery], OwnerSearchAnswer] | None = None,
    *,
    providers: tuple[ProviderConnectionConfiguration, ...] = _PROVIDERS,
    preview: Callable[[OwnerSearchCover], bytes] = lambda _: b"preview-bytes",
    limits: OwnerSearchLimits | None = None,
) -> _Harness:
    clocks = _Clocks()
    source = _Source(
        answer or (lambda _: OwnerSearchAnswer((_record("/works/OL1W"),), more=False))
    )
    works = _Works()
    pacer = ProviderRequestPacer(
        60.0, clock=lambda: clocks.mono, sleep=lambda seconds: clocks.advance(seconds)
    )
    fetched: list[OwnerSearchCover] = []
    throttling: dict[str, object] = {"entries": []}

    def fetch(cover: OwnerSearchCover, deadline: float) -> bytes:
        fetched.append(cover)
        return preview(cover)

    service = OwnerSearchService(
        configuration=cast(Any, _Configuration(providers)),
        works=works,
        adapter=lambda provider, *, marketplaces: source,
        pacers={"open_library": pacer},
        throttling=lambda: throttling,
        fetch_preview=fetch,
        clock=lambda: clocks.wall,
        monotonic=lambda: clocks.mono,
        limits=limits,
    )
    return _Harness(service, clocks, source, works, pacer, fetched, throttling)


def _request(**changes: Any) -> OwnerSearchRequest:
    values: dict[str, Any] = {
        "connection": CONNECTION,
        "library": LIBRARY,
        "item": ITEM,
        "provider": "open_library",
        "lane": "title_authors",
        "title": "Current title",
        "authors": ("Ada",),
    }
    values.update(changes)
    return OwnerSearchRequest(**values)


def _refusal(call: Callable[[], object]) -> OwnerSearchRefused:
    with pytest.raises(OwnerSearchRefused) as raised:
        call()
    return raised.value


def _search(harness: _Harness, token: str = TOKEN) -> OwnerSearchResult:
    # Each search starts after the pacer's interval, so only the limit under
    # test can refuse it.
    harness.clocks.advance(61)
    return harness.service.search(token, _request())


# -- Admission ---------------------------------------------------------------


def test_a_search_is_one_request_and_its_cards_are_held() -> None:
    harness = _harness()
    result = _search(harness)
    assert len(harness.source.queries) == 1
    assert result.search_id.startswith("search_")
    assert len(result.search_id) == len("search_") + 43
    assert [card.identity for card in result.cards] == ["/works/OL1W"]
    held = harness.service.memory.get(TOKEN)
    assert held is not None and held.search_id == result.search_id


def test_the_sources_are_the_librarys_enabled_dispatch_sources() -> None:
    subject = _harness().service.subject(CONNECTION, LIBRARY, ITEM)
    offer = subject.to_offer()
    assert [value["provider"] for value in offer["sources"]] == [
        "google_books",
        "inventaire",
        "open_library",
    ]
    assert offer["prefill"] == {
        "title": "Current title",
        "authors": ["Ada"],
        "isbn": None,
        "asin": None,
    }


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"item": "missing"}, "work_not_found"),
        ({"library": "elsewhere"}, "work_not_found"),
        ({"provider": "itunes_search"}, "provider_not_offered"),
        ({"lane": "asin", "asin": "B000TEST01"}, "provider_not_offered"),
        ({"marketplace": "uk"}, "provider_not_offered"),
        ({"title": "  "}, "title_invalid"),
        ({"authors": ()}, "author_invalid"),
        ({"authors": ("a", "b", "c", "d", "e")}, "author_invalid"),
        ({"lane": "isbn", "isbn": "9780306406158"}, "isbn_invalid"),
    ],
    ids=[
        "unknown-item",
        "unknown-library",
        "source-not-offered",
        "lane-not-offered",
        "marketplace-not-offered",
        "blank-title",
        "no-authors",
        "five-authors",
        "bad-isbn-checksum",
    ],
)
def test_a_search_is_refused_before_any_request(
    changes: dict[str, object], code: str
) -> None:
    harness = _harness()
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request(**changes)))
    assert refusal.code == code
    assert harness.source.queries == []


def test_a_work_that_could_not_finish_is_not_offered() -> None:
    harness = _harness()
    harness.works.could_not_finish = True
    assert _refusal(lambda: _search(harness)).code == "find_not_offered"
    assert harness.source.queries == []


@pytest.mark.parametrize("could_not_finish", [False, True], ids=["ready", "failed"])
@pytest.mark.parametrize(
    "providers",
    [
        _PROVIDERS,
        (),
        (
            ProviderConnectionConfiguration(
                "gb", False, False, None, adapter="google_books"
            ),
        ),
    ],
    ids=["sources", "no-sources", "disabled-source"],
)
def test_a_library_row_is_offered_by_the_link_views_own_rule(
    providers: tuple[ProviderConnectionConfiguration, ...], could_not_finish: bool
) -> None:
    harness = _harness(providers=providers)
    harness.works.could_not_finish = could_not_finish
    offered = harness.service.subject(CONNECTION, LIBRARY, ITEM).offered
    reads = harness.works.reads
    find_offer = harness.service.find_offer()
    assert find_offer(CONNECTION, LIBRARY, "epub", could_not_finish) is offered
    assert offered is (not could_not_finish and providers == _PROVIDERS)
    # The row reads no work: what the store says is already on the row.
    assert harness.works.reads == reads


def test_a_library_row_of_an_unknown_library_is_not_offered() -> None:
    find_offer = _harness().service.find_offer()
    assert find_offer(CONNECTION, "elsewhere", "epub", False) is False
    assert find_offer(CONNECTION, LIBRARY, "epub", False) is True


def test_a_library_page_selects_each_librarys_sources_once() -> None:
    harness = _harness()
    selected: list[tuple[str, str]] = []
    configuration = harness.service._configuration
    select = configuration.select

    def counted(connection: str, library: str) -> tuple[Any, ...]:
        selected.append((connection, library))
        return select(connection, library)

    configuration.select = counted  # type: ignore[method-assign]
    find_offer = harness.service.find_offer()
    for _ in range(3):
        assert find_offer(CONNECTION, LIBRARY, "epub", False) is True
    assert find_offer(CONNECTION, LIBRARY, "epub", True) is False
    assert selected == [(CONNECTION, LIBRARY)]


def test_one_search_in_flight_per_session() -> None:
    harness = _harness()
    inner: list[str] = []

    def nested(_: OwnerSearchQuery) -> OwnerSearchAnswer:
        if not inner:
            inner.append(
                _refusal(lambda: harness.service.search(TOKEN, _request())).code
            )
            # Another session is not held up by this one.
            harness.service.search(OTHER, _request(provider="inventaire"))
        return OwnerSearchAnswer((_record("/works/OL1W"),))

    harness.source.answer = nested
    _search(harness)
    assert inner == ["search_in_flight"]
    # The session is free again once its search returned.
    _search(harness)


def test_thirty_requests_an_hour_per_source() -> None:
    harness = _harness()
    first = harness.clocks.wall + 61
    for _ in range(SEARCHES_PER_HOUR):
        _search(harness)
    refusal = _refusal(lambda: _search(harness))
    assert refusal.code == "search_limit"
    assert refusal.retry_at == int(first) + 3600
    assert len(harness.source.queries) == SEARCHES_PER_HOUR
    harness.clocks.wall = first + 3600
    harness.clocks.mono += 3600
    harness.service.search(TOKEN, _request())
    assert len(harness.source.queries) == SEARCHES_PER_HOUR + 1


def _fill_the_hour_but_one(harness: _Harness) -> float:
    """Send 29 unpaced searches; return when the first was counted."""
    first = harness.clocks.wall + 1
    for _ in range(SEARCHES_PER_HOUR - 1):
        harness.clocks.advance(1)
        harness.service.search(TOKEN, _request(provider="inventaire"))
    return first


def test_two_sessions_at_once_cannot_pass_the_hourly_limit() -> None:
    # Inventaire has no pacer here, so only the hourly limit can refuse.
    harness = _harness()
    first = _fill_the_hour_but_one(harness)
    inner: list[OwnerSearchRefused] = []

    def concurrent(_: OwnerSearchQuery) -> OwnerSearchAnswer:
        if not inner:
            # The thirtieth search holds the last slot while its request is out.
            inner.append(
                _refusal(
                    lambda: harness.service.search(
                        OTHER, _request(provider="inventaire")
                    )
                )
            )
        return OwnerSearchAnswer((_record("/works/OL1W"),))

    harness.source.answer = concurrent
    harness.service.search(TOKEN, _request(provider="inventaire"))
    assert [refusal.code for refusal in inner] == ["search_limit"]
    assert inner[0].retry_at == int(first) + 3600
    assert len(harness.source.queries) == SEARCHES_PER_HOUR
    assert (
        _refusal(
            lambda: harness.service.search(OTHER, _request(provider="inventaire"))
        ).code
        == "search_limit"
    )


def test_a_held_slot_whose_search_sent_nothing_is_given_back() -> None:
    harness = _harness()
    _fill_the_hour_but_one(harness)
    inner: list[str] = []

    def unsent(_: OwnerSearchQuery) -> OwnerSearchAnswer:
        inner.append(
            _refusal(
                lambda: harness.service.search(OTHER, _request(provider="inventaire"))
            ).code
        )
        return OwnerSearchAnswer(
            failure=OwnerSearchFailure.UNAVAILABLE, requested=False
        )

    harness.source.answer = unsent
    assert (
        _refusal(
            lambda: harness.service.search(TOKEN, _request(provider="inventaire"))
        ).code
        == "search_failed"
    )
    assert inner == ["search_limit"]
    # The slot it held is free again and leaves no count: one more is sent,
    # and only then is the hour full.
    harness.source.answer = lambda _: OwnerSearchAnswer((_record("/works/OL1W"),))
    harness.service.search(OTHER, _request(provider="inventaire"))
    assert (
        _refusal(
            lambda: harness.service.search(OTHER, _request(provider="inventaire"))
        ).code
        == "search_limit"
    )


def test_the_hourly_count_and_in_flight_rule_outlive_a_new_generation() -> None:
    # A Settings save builds a new service on the process's limits (#1477).
    limits = OwnerSearchLimits()
    harness = _harness(limits=limits)
    _fill_the_hour_but_one(harness)
    successor = _harness(limits=limits)
    inner: list[str] = []

    def across(_: OwnerSearchQuery) -> OwnerSearchAnswer:
        # The same session, mid-search in the old generation, is still in
        # flight in the new one.
        inner.append(
            _refusal(
                lambda: successor.service.search(TOKEN, _request(provider="inventaire"))
            ).code
        )
        return OwnerSearchAnswer((_record("/works/OL1W"),))

    harness.source.answer = across
    harness.service.search(TOKEN, _request(provider="inventaire"))
    assert inner == ["search_in_flight"]
    assert (
        _refusal(
            lambda: successor.service.search(OTHER, _request(provider="inventaire"))
        ).code
        == "search_limit"
    )
    assert successor.source.queries == []


def test_a_search_that_sent_nothing_does_not_count_toward_the_hour() -> None:
    harness = _harness(
        lambda _: OwnerSearchAnswer(
            failure=OwnerSearchFailure.UNAVAILABLE, requested=False
        )
    )
    for _ in range(SEARCHES_PER_HOUR + 1):
        assert _refusal(lambda: _search(harness)).code == "search_failed"


def test_the_pacer_wait_is_busy_at_exactly_ten_seconds_and_not_before() -> None:
    harness = _harness()
    harness.service.search(TOKEN, _request())  # starts the pacer's interval
    harness.clocks.advance(50)  # the next start is exactly ten seconds away
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert refusal.code == "provider_busy"
    assert len(harness.source.queries) == 1
    harness.clocks.advance(0.001)  # 9.999 seconds away: waited, then sent
    harness.service.search(TOKEN, _request())
    assert len(harness.source.queries) == 2


def test_a_burst_hold_on_the_shared_pacer_is_a_deferral() -> None:
    harness = _harness()
    harness.pacer.hold_burst(30)
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert (refusal.code, refusal.reason) == ("provider_deferred", "rate_limited")
    assert refusal.retry_at == int(WALL) + 30
    assert harness.source.queries == []


@pytest.mark.parametrize(
    ("reasons", "reason"),
    [
        (["rate_limited"], "rate_limited"),
        (["quota_exhausted", "rate_limited"], "quota_exhausted"),
        (["authorization"], "key"),
    ],
    ids=["rate-limited", "quota-first", "key"],
)
def test_a_recovery_deferral_of_this_library_and_source_refuses(
    reasons: list[str], reason: str
) -> None:
    harness = _harness()
    harness.throttling["entries"] = [
        {
            "library": LIBRARY,
            "adapter": "open_library",
            "state": "waiting",
            "reasons": reasons,
            "next_retry_at": int(WALL) + 500,
        }
    ]
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert (refusal.code, refusal.reason) == ("provider_deferred", reason)
    assert refusal.retry_at == int(WALL) + 500
    assert harness.source.queries == []


def test_a_deferral_of_another_source_or_a_passed_one_does_not_refuse() -> None:
    harness = _harness()
    harness.throttling["entries"] = [
        {
            "library": LIBRARY,
            "adapter": "inventaire",
            "state": "waiting",
            "reasons": ["rate_limited"],
            "next_retry_at": int(WALL) + 500,
        },
        {
            "library": LIBRARY,
            "adapter": "open_library",
            "state": "waiting",
            "reasons": ["rate_limited"],
            "next_retry_at": int(WALL),
        },
    ]
    harness.service.search(TOKEN, _request())
    assert len(harness.source.queries) == 1


@pytest.mark.parametrize(
    ("failure", "retry_after", "reason", "wait"),
    [
        (OwnerSearchFailure.RATE_LIMITED, 120, "rate_limited", 120),
        (OwnerSearchFailure.RATE_LIMITED, None, "rate_limited", 60),
        (OwnerSearchFailure.QUOTA_EXHAUSTED, None, "quota_exhausted", 3600),
    ],
    ids=["rate-limited-with-wait", "rate-limited-default", "quota"],
)
def test_a_live_throttle_is_a_deferral_and_writes_nothing(
    failure: OwnerSearchFailure, retry_after: int | None, reason: str, wait: int
) -> None:
    harness = _harness(
        lambda _: OwnerSearchAnswer(failure=failure, retry_after=retry_after)
    )
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert (refusal.code, refusal.reason) == ("provider_deferred", reason)
    assert refusal.retry_at == int(WALL) + wait
    assert harness.service.memory.get(TOKEN) is None
    assert harness.throttling == {"entries": []}


@pytest.mark.parametrize(
    "failure",
    [
        OwnerSearchFailure.UNAVAILABLE,
        OwnerSearchFailure.UNREADABLE,
        OwnerSearchFailure.KEY,
    ],
    ids=["unavailable", "unreadable", "key"],
)
def test_a_failed_search_names_its_kind_and_holds_nothing(
    failure: OwnerSearchFailure,
) -> None:
    harness = _harness(lambda _: OwnerSearchAnswer(failure=failure))
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert (refusal.code, refusal.kind) == ("search_failed", failure.value)
    assert harness.service.memory.get(TOKEN) is None
    assert harness.throttling == {"entries": []}


def test_an_adapter_that_raises_is_an_unavailable_search() -> None:
    def broken(_: OwnerSearchQuery) -> OwnerSearchAnswer:
        raise RuntimeError("transport")

    harness = _harness(broken)
    refusal = _refusal(lambda: harness.service.search(TOKEN, _request()))
    assert (refusal.code, refusal.kind) == ("search_failed", "unavailable")


# -- Holding -----------------------------------------------------------------


def test_the_search_expires_one_millisecond_after_fifteen_minutes() -> None:
    harness = _harness()
    result = _search(harness)
    harness.clocks.advance(SEARCH_TTL_SECONDS)
    harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
    harness.clocks.advance(0.001)
    refusal = _refusal(
        lambda: harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
    )
    assert refusal.code == "search_not_found"


def test_another_sessions_search_is_not_found() -> None:
    harness = _harness()
    result = _search(harness)
    refusal = _refusal(
        lambda: harness.service.resolve_choice(OTHER, result.search_id, 0, "work")
    )
    assert refusal.code == "search_not_found"
    assert _refusal(
        lambda: harness.service.preview(OTHER, result.search_id, 0)
    ).code == ("search_not_found")


def test_a_new_search_replaces_the_sessions_last_one() -> None:
    harness = _harness()
    first = _search(harness)
    second = _search(harness)
    assert first.search_id != second.search_id
    assert (
        _refusal(
            lambda: harness.service.resolve_choice(TOKEN, first.search_id, 0, "work")
        ).code
        == "search_not_found"
    )
    harness.service.resolve_choice(TOKEN, second.search_id, 0, "work")


def test_logout_discards_the_sessions_search() -> None:
    harness = _harness()
    result = _search(harness)
    logged_out: list[tuple[str, str]] = []
    application = ReviewApplication.__new__(ReviewApplication)
    application._sessions = SimpleNamespace(  # type: ignore[assignment]
        logout=lambda token, csrf: logged_out.append((token, csrf))
    )
    application._owner_search = harness.service
    application._identifier_import = None
    application.logout(TOKEN, "csrf")
    assert logged_out == [(TOKEN, "csrf")]
    assert application.owner_search is harness.service
    assert (
        _refusal(
            lambda: harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
        ).code
        == "search_not_found"
    )


# -- Choosing ----------------------------------------------------------------


def test_a_choice_records_the_retained_card_as_a_new_link() -> None:
    harness = _harness()
    result = _search(harness)
    choice = harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
    assert (choice.action, choice.expected_current) == (OwnerLinkAction.LINK, None)
    assert (choice.target.scope, choice.target.identity) == (
        OwnerLinkScope.WORK,
        "/works/OL1W",
    )


@pytest.mark.parametrize(
    ("position", "scope", "code"),
    [
        (5, "work", "card_not_choosable"),
        (0, "edition", "scope_not_offered"),
        (0, "recording", "scope_not_offered"),
        (0, "everything", "scope_not_offered"),
    ],
    ids=["no-such-card", "edition-withheld", "recording", "unknown-scope"],
)
def test_a_choice_outside_the_card_is_refused(
    position: int, scope: str, code: str
) -> None:
    harness = _harness()
    result = _search(harness)
    refusal = _refusal(
        lambda: harness.service.resolve_choice(TOKEN, result.search_id, position, scope)
    )
    assert refusal.code == code


def test_a_choice_after_the_work_changed_is_refused() -> None:
    harness = _harness()
    result = _search(harness)
    del harness.works.documents[ITEM]
    refusal = _refusal(
        lambda: harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
    )
    assert refusal.code == "work_changed"


def test_a_choice_after_the_link_changed_is_refused() -> None:
    harness = _harness()
    result = _search(harness)
    harness.works.link_row_id = "link-row-elsewhere"
    refusal = _refusal(
        lambda: harness.service.resolve_choice(TOKEN, result.search_id, 0, "work")
    )
    assert refusal.code == "link_changed"


def test_a_withdraw_needs_the_current_link_row() -> None:
    harness = _harness()
    harness.works.link_row_id = "link-row-1"
    harness.service.resolve_withdraw(CONNECTION, LIBRARY, ITEM, "link-row-1")
    refusal = _refusal(
        lambda: harness.service.resolve_withdraw(CONNECTION, LIBRARY, ITEM, "old")
    )
    assert refusal.code == "link_changed"


# -- Previews ----------------------------------------------------------------


def test_a_preview_is_fetched_once_and_held_in_memory() -> None:
    harness = _harness()
    result = _search(harness)
    harness.clocks.advance(61)
    assert harness.service.preview(TOKEN, result.search_id, 0) == b"preview-bytes"
    assert harness.service.preview(TOKEN, result.search_id, 0) == b"preview-bytes"
    assert len(harness.fetched) == 1
    assert harness.fetched[0].url.startswith("https://covers.openlibrary.org/")


def test_a_card_without_an_admitted_cover_is_never_fetched() -> None:
    harness = _harness(
        lambda _: OwnerSearchAnswer((_record("/works/OL1W", cover=False),))
    )
    result = _search(harness)
    refusal = _refusal(lambda: harness.service.preview(TOKEN, result.search_id, 0))
    assert refusal.code == "preview_unavailable"
    assert harness.fetched == []


def test_covers_turned_off_for_the_source_means_no_preview() -> None:
    providers = (
        ProviderConnectionConfiguration(
            "ol",
            True,
            False,
            None,
            adapter="open_library",
            contact_email="o@example.org",
        ),
    )
    harness = _harness(providers=providers)
    result = _search(harness)
    refusal = _refusal(lambda: harness.service.preview(TOKEN, result.search_id, 0))
    assert refusal.code == "preview_unavailable"
    assert harness.fetched == []


def test_a_failed_preview_is_held_as_unavailable() -> None:
    def refuse(_: OwnerSearchCover) -> bytes:
        raise RuntimeError("decode")

    harness = _harness(preview=refuse)
    result = _search(harness)
    harness.clocks.advance(61)
    for _ in range(2):
        refusal = _refusal(lambda: harness.service.preview(TOKEN, result.search_id, 0))
        assert refusal.code == "preview_unavailable"
    assert len(harness.fetched) == 1


def test_a_preview_waits_on_the_shared_pacer() -> None:
    harness = _harness()
    result = _search(harness)  # the search just started the pacer's interval
    refusal = _refusal(lambda: harness.service.preview(TOKEN, result.search_id, 0))
    assert refusal.code == "preview_unavailable"
    assert harness.fetched == []
