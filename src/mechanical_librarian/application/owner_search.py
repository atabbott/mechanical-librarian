"""Find this book: one owner search of one source, held for choosing (#1477).

An owner search asks one enabled source of the work's library with the
owner's title and authors, ISBN or ASIN, in the source's existing request
shape (S8). It is admitted in the contract's order (S11 steps 3-11; the web
layer answers the session, CSRF, Origin, build and body shape first), waits
once on the same shared pacer preparation uses (S12), sends one request with
no retry, and writes nothing: no provider recovery, no held provider answer,
no report. Its cards are judged against the work's current report in the
domain (S15) and held in session memory for fifteen minutes (S13), where a
choice and a preview find them again. The search results are never evidence.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    ProviderConnectionConfiguration,
    audiobook_marketplaces,
    dispatch_adapters,
)
from mechanical_librarian.application.failure_diagnostics import (
    emit_owner_events,
    owner_search_event,
)
from mechanical_librarian.application.session_memory import SessionMemory
from mechanical_librarian.domain.human_decision import owner_text
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderStatus,
)
from mechanical_librarian.domain.owner_link import (
    LINK_SOURCES_BY_MEDIUM,
    MAX_OWNER_SEARCH_AUTHORS,
    OwnerLinkAction,
    OwnerLinkError,
    OwnerLinkScope,
    OwnerLinkTarget,
    OwnerSearchAnswer,
    OwnerSearchCard,
    OwnerSearchCover,
    OwnerSearchFailure,
    OwnerSearchLane,
    OwnerSearchQuery,
    OwnerSearchWork,
    owner_asin,
    owner_isbn,
    owner_search_cards,
    owner_search_medium,
)

SEARCH_TTL_SECONDS = 15 * 60
SEARCHES_PER_HOUR = 30
PACER_WAIT_SECONDS = 10.0
REQUEST_SECONDS = 20.0
_HOUR = 3600
# The one recovery reason each deferral answers with, in precedence order.
_DEFERRAL_REASONS = (
    ("quota_exhausted", frozenset({"quota_exhausted"})),
    ("rate_limited", frozenset({"rate_limited", "burst_throttled"})),
    ("key", frozenset({"authentication", "authorization"})),
)
# A live throttle that states no wait is asked again after these.
_DEFAULT_RETRY = {"rate_limited": 60, "quota_exhausted": _HOUR}
_LANES: Mapping[str, tuple[OwnerSearchLane, ...]] = {
    "audible_catalog": ("title_authors", "asin"),
    "hardcover": ("title_authors", "isbn"),
    "open_library": ("title_authors", "isbn"),
    "google_books": ("title_authors", "isbn"),
    "inventaire": ("title_authors", "isbn"),
}


class OwnerSearchRefused(Exception):
    """One closed refusal: the first admission or choice step that refused.

    `retry_at` is a wall-clock second; `reason` names a deferral's cause and
    `kind` a failed search's (`unavailable`, `unreadable` or `key`).
    """

    def __init__(
        self,
        code: str,
        *,
        retry_at: int | None = None,
        reason: str | None = None,
        kind: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retry_at = retry_at
        self.reason = reason
        self.kind = kind


class OwnerSearchSource(Protocol):
    """An adapter's owner search: one request in its existing shape."""

    def owner_search(
        self, query: OwnerSearchQuery, *, deadline: float
    ) -> OwnerSearchAnswer: ...


class OwnerSearchPacer(Protocol):
    def wait(self, deadline: float | None = None) -> bool: ...


class OwnerSearchWorks(Protocol):
    """The work's current report, current link row and Could not finish."""

    def read_owner_search_work(
        self, connection: str, library: str, item: str
    ) -> tuple[str, str | None, bool] | None: ...


@dataclass(frozen=True, slots=True)
class OwnerSearchRequest:
    """What the browser asked, already of the right types (S11 step 2)."""

    connection: str
    library: str
    item: str
    provider: str
    lane: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    isbn: str | None = None
    asin: str | None = None
    marketplace: str | None = None


@dataclass(frozen=True, slots=True)
class SearchableSource:
    """One source a work may be searched at, as the link view lists it."""

    provider: ProviderConnectionConfiguration
    lanes: tuple[OwnerSearchLane, ...]
    marketplaces: tuple[str, ...] = ()


def find_offered(could_not_finish: bool, sources: tuple[SearchableSource, ...]) -> bool:
    """Whether Find this book is offered for a work (S7, L4).

    The one rule both the link view's `offered` and the Library row's
    `find_offered` read: never on a work that could not finish, and only where
    the work's library has a source it can be searched at.
    """
    return not could_not_finish and bool(sources)


@dataclass(frozen=True, slots=True)
class OwnerSearchSubject:
    """The work a search or choice is about, read from the store (S13)."""

    connection: str
    library: str
    library_id: str
    item: str
    medium: str
    link_row_id: str | None
    could_not_finish: bool
    work: OwnerSearchWork
    sources: tuple[SearchableSource, ...]

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        """The catalogue identity a choice must still find (`work_changed`)."""
        return (self.connection, self.library, self.library_id, self.item, self.medium)

    @property
    def offered(self) -> bool:
        return find_offered(self.could_not_finish, self.sources)

    def to_offer(self) -> dict[str, object]:
        """`offered`, `sources` and `prefill` for the link view (S7, U6)."""
        work = self.work
        return {
            "offered": self.offered,
            "sources": [
                {
                    "provider": source.provider.adapter,
                    "lanes": list(source.lanes),
                    **(
                        {"marketplaces": list(source.marketplaces)}
                        if source.marketplaces
                        else {}
                    ),
                }
                for source in self.sources
            ],
            "prefill": {
                "title": work.title,
                "authors": list(work.authors[:MAX_OWNER_SEARCH_AUTHORS]),
                "isbn": work.isbn,
                "asin": work.asin,
            },
        }


@dataclass(frozen=True, slots=True)
class OwnerSearchState:
    """One session's last search, as session memory holds it."""

    search_id: str
    subject: OwnerSearchSubject
    provider: str
    cover_enabled: bool
    cards: tuple[OwnerSearchCard, ...]
    # Position -> preview bytes, or `None` once a fetch failed. Guarded by
    # the service's lock; never written anywhere else.
    previews: dict[int, bytes | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OwnerSearchResult:
    search_id: str
    provider: str
    lane: str
    marketplace: str | None
    cards: tuple[OwnerSearchCard, ...]
    more: bool
    dropped: int

    def to_data(self) -> dict[str, object]:
        return {
            "search_id": self.search_id,
            "provider": self.provider,
            "lane": self.lane,
            "marketplace": self.marketplace,
            "cards": [card.to_data() for card in self.cards],
            "more": self.more,
            "dropped": self.dropped,
        }


@dataclass(frozen=True, slots=True)
class OwnerLinkChoice:
    """What a choose records, from the retained card alone (S16)."""

    connection: str
    library: str
    item: str
    medium: str
    action: OwnerLinkAction
    expected_current: str | None
    target: OwnerLinkTarget


class OwnerSearchLimits:
    """The one-at-a-time rule and the hourly counts, for the whole process.

    The composition root owns one and gives it to every runtime generation, so
    a settings change, which builds a new service, resets neither (S11 steps
    7 and 8). A search takes its slot here under one lock, before anything is
    sent, so concurrent sessions cannot together pass the hourly limit; the
    slot becomes a count only once its request is sent, and is given back
    otherwise.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._sent: dict[str, deque[float]] = {}
        self._reserved: dict[str, int] = {}

    def admit(self, session: str, adapter: str, now: float) -> None:
        """Steps 7 and 8, decided and reserved together, or refused."""
        with self._lock:
            if session in self._in_flight:
                raise OwnerSearchRefused("search_in_flight")
            sent = self._sent.setdefault(adapter, deque())
            while sent and now - sent[0] >= _HOUR:
                sent.popleft()
            reserved = self._reserved.get(adapter, 0)
            over = len(sent) + reserved - SEARCHES_PER_HOUR
            if over >= 0:
                # The earliest a slot frees if every reserved search is sent.
                frees = sent[over] + _HOUR if over < len(sent) else now + _HOUR
                raise OwnerSearchRefused("search_limit", retry_at=math.ceil(frees))
            self._reserved[adapter] = reserved + 1
            self._in_flight.add(session)

    def settle(self, session: str, adapter: str, requested: bool, now: float) -> None:
        """End an admitted search: count it if its request was sent."""
        with self._lock:
            self._reserved[adapter] -= 1
            if requested:
                self._sent.setdefault(adapter, deque()).append(now)
            self._in_flight.discard(session)


class OwnerSearchService:
    """Admit, send and hold owner searches for one runtime generation.

    `adapter(provider, marketplaces=...)` constructs the source's adapter
    per call from this generation's configuration (L7); `pacers` are the
    shared pacers the running preparation uses (S12); `throttling` is the
    generation's provider-throttling read, the rows recovery deferrals come
    from. `limits` outlives the generation; its search memory does not.
    """

    def __init__(
        self,
        *,
        configuration: ApplicationConfiguration,
        works: OwnerSearchWorks,
        adapter: Callable[..., object],
        pacers: Mapping[str, object],
        throttling: Callable[[], dict[str, object]],
        fetch_preview: Callable[[OwnerSearchCover, float], bytes],
        clock: Callable[[], float],
        monotonic: Callable[[], float],
        memory: SessionMemory[OwnerSearchState] | None = None,
        limits: OwnerSearchLimits | None = None,
    ) -> None:
        self._configuration = configuration
        self._works = works
        self._adapter = adapter
        self._pacers = pacers
        self._throttling = throttling
        self._fetch_preview = fetch_preview
        self._clock = clock
        self._monotonic = monotonic
        self._memory = memory or SessionMemory(SEARCH_TTL_SECONDS, clock)
        self._lock = threading.Lock()
        self._limits = limits or OwnerSearchLimits()

    @property
    def memory(self) -> SessionMemory[OwnerSearchState]:
        return self._memory

    def forget(self, session_token: str) -> None:
        """Discard the session's search, as logout does (S13)."""
        self._memory.discard(session_token)

    def subject(self, connection: str, library: str, item: str) -> OwnerSearchSubject:
        """Read the work, or refuse `work_not_found` (S11 step 3)."""
        try:
            read = self._works.read_owner_search_work(connection, library, item)
            _, selected, _, providers = self._configuration.select(connection, library)
        except ConfigurationError:
            read = None
        if read is None:
            raise OwnerSearchRefused("work_not_found")
        document, link_row_id, could_not_finish = read
        try:
            report = MetadataReport.from_json(document)
        except Exception as error:
            raise OwnerSearchRefused("work_not_found") from error
        target = report.target
        if (target.connection, target.library, target.item) != (
            connection,
            library,
            item,
        ):
            raise OwnerSearchRefused("work_not_found")
        work = OwnerSearchWork.from_report(report)
        return OwnerSearchSubject(
            connection,
            library,
            selected.library_id,
            item,
            work.medium,
            link_row_id,
            could_not_finish,
            work,
            _offered_sources(work.medium, providers, selected.languages),
        )

    def find_offer(self) -> Callable[[str, str, str, bool], bool]:
        """Whether Find this book is offered, for one Library page's rows (#1477).

        Called with a row's connection, library, report file format and
        whether its latest preparation could not finish; the same rule as the
        link view's `offered`, from this generation's configuration alone, with
        no store read. Each library's sources are selected once per page.
        """
        sources: dict[tuple[str, str, str], tuple[SearchableSource, ...]] = {}

        def offered(
            connection: str, library: str, file_format: str, could_not_finish: bool
        ) -> bool:
            if could_not_finish:
                return find_offered(True, ())
            medium = owner_search_medium(file_format)
            key = (connection, library, medium)
            if key not in sources:
                try:
                    _, selected, _, providers = self._configuration.select(
                        connection, library
                    )
                except ConfigurationError:
                    sources[key] = ()
                else:
                    sources[key] = _offered_sources(
                        medium, providers, selected.languages
                    )
            return find_offered(could_not_finish, sources[key])

        return offered

    def search(
        self, session_token: str, request: OwnerSearchRequest
    ) -> OwnerSearchResult:
        """Admit and send one search, then hold its cards (S11 steps 3-11)."""
        adapter = request.provider
        try:
            subject = self.subject(request.connection, request.library, request.item)
            if not subject.offered:
                raise OwnerSearchRefused("find_not_offered")
            source = next(
                (s for s in subject.sources if s.provider.adapter == adapter), None
            )
            if source is None:
                raise OwnerSearchRefused("provider_not_offered")
            query = _query(request, source)
            result = self._admitted(session_token, subject, source, query)
        except OwnerSearchRefused as refusal:
            _emit_search(adapter, request.lane, refusal.code)
            raise
        _emit_search(
            adapter,
            request.lane,
            "cards",
            len(result.cards),
            result.dropped,
            result.more,
        )
        return result

    def _admitted(
        self,
        session_token: str,
        subject: OwnerSearchSubject,
        source: SearchableSource,
        query: OwnerSearchQuery,
    ) -> OwnerSearchResult:
        adapter = source.provider.adapter
        session = _session_key(session_token)
        self._limits.admit(session, adapter, self._clock())
        requested = False
        try:
            self._require_not_deferred(subject.library, adapter)
            self._wait_for_pacer(adapter)
            answer = self._send(source, query)
            requested = answer.requested
        finally:
            self._limits.settle(session, adapter, requested, self._clock())
        return self._hold(session_token, subject, source, query, answer)

    def _require_not_deferred(self, library: str, adapter: str) -> None:
        """Step 9: a current recovery deferral of this library and source."""
        now = self._clock()
        entries = cast("list[dict[str, object]]", self._throttling()["entries"])
        for entry in entries:
            due = entry["next_retry_at"]
            if (
                entry["library"] != library
                or entry["adapter"] != adapter
                or entry["state"] != "waiting"
                or type(due) is not int
                or due <= now
            ):
                continue
            reasons = set(cast("list[str]", entry["reasons"]))
            for reason, members in _DEFERRAL_REASONS:
                if reasons & members:
                    raise OwnerSearchRefused(
                        "provider_deferred", reason=reason, retry_at=due
                    )

    def _wait_for_pacer(self, adapter: str) -> None:
        """Steps 9 and 10: the shared pacer's one wait (S12, L8).

        A burst hold the pacer raises is a deferral with its own wait; a wait
        that cannot start the request within ten seconds is `provider_busy`.
        """
        pacer = cast("OwnerSearchPacer | None", self._pacers.get(adapter))
        if pacer is None:
            return
        try:
            started = pacer.wait(self._monotonic() + PACER_WAIT_SECONDS)
        except Exception as error:
            retry_after = getattr(error, "retry_after", None)
            if getattr(error, "status", None) is not ProviderStatus.RATE_LIMITED or (
                type(retry_after) not in {int, float}
            ):
                raise
            raise OwnerSearchRefused(
                "provider_deferred",
                reason="rate_limited",
                retry_at=math.ceil(self._clock() + cast(float, retry_after)),
            ) from None
        if not started:
            raise OwnerSearchRefused("provider_busy")

    def _send(
        self, source: SearchableSource, query: OwnerSearchQuery
    ) -> OwnerSearchAnswer:
        """Step 11: one request, no retry, whatever the adapter answers."""
        instance = self._adapter(
            source.provider, marketplaces=source.marketplaces or ("us",)
        )
        if isinstance(instance, str):
            return OwnerSearchAnswer(
                failure=OwnerSearchFailure.KEY
                if instance == "credential unavailable"
                else OwnerSearchFailure.UNAVAILABLE,
                requested=False,
            )
        timeout = min(self._configuration.limits.timeout_seconds, REQUEST_SECONDS)
        try:
            return cast("OwnerSearchSource", instance).owner_search(
                query, deadline=self._monotonic() + timeout
            )
        except Exception:
            return OwnerSearchAnswer(failure=OwnerSearchFailure.UNAVAILABLE)

    def _hold(
        self,
        session_token: str,
        subject: OwnerSearchSubject,
        source: SearchableSource,
        query: OwnerSearchQuery,
        answer: OwnerSearchAnswer,
    ) -> OwnerSearchResult:
        failure = answer.failure
        if failure in {
            OwnerSearchFailure.RATE_LIMITED,
            OwnerSearchFailure.QUOTA_EXHAUSTED,
        }:
            reason = cast("OwnerSearchFailure", failure).value
            raise OwnerSearchRefused(
                "provider_deferred",
                reason=reason,
                retry_at=math.ceil(
                    self._clock() + (answer.retry_after or _DEFAULT_RETRY[reason])
                ),
            )
        if failure is not None:
            raise OwnerSearchRefused("search_failed", kind=failure.value)
        adapter = source.provider.adapter
        cards, dropped = owner_search_cards(adapter, answer.records, subject.work)
        state = OwnerSearchState(
            "search_" + secrets.token_urlsafe(32),
            subject,
            adapter,
            source.provider.cover_enabled,
            cards,
        )
        self._memory.put(session_token, state)
        return OwnerSearchResult(
            state.search_id,
            adapter,
            query.lane,
            query.marketplace,
            cards,
            answer.more,
            min(answer.dropped + dropped, 11),
        )

    def preview(self, session_token: str, search_id: str, position: int) -> bytes:
        """A card's preview, fetched on its first read and then held (S14)."""
        state = self._state(session_token, search_id)
        if (
            type(position) is not int
            or not 0 <= position < len(state.cards)
            or not state.cover_enabled
        ):
            raise OwnerSearchRefused("preview_unavailable")
        cover = state.cards[position].cover
        if cover is None:
            raise OwnerSearchRefused("preview_unavailable")
        with self._lock:
            if position in state.previews:
                held = state.previews[position]
                if held is None:
                    raise OwnerSearchRefused("preview_unavailable")
                return held
        pacer = self._pacers.get(state.provider)
        wait = getattr(pacer, "asset_wait", None) or getattr(pacer, "wait", None)
        deadline = self._monotonic() + PACER_WAIT_SECONDS
        try:
            admitted = wait is None or wait(deadline)
        except Exception:
            # A burst hold on the shared pacer: no preview now, none fetched.
            admitted = False
        if not admitted:
            raise OwnerSearchRefused("preview_unavailable")
        try:
            preview: bytes | None = self._fetch_preview(
                cover, self._monotonic() + REQUEST_SECONDS
            )
        except Exception:
            preview = None
        with self._lock:
            state.previews.setdefault(position, preview)
            held = state.previews[position]
        if held is None:
            raise OwnerSearchRefused("preview_unavailable")
        return held

    def resolve_choice(
        self, session_token: str, search_id: str, position: int, scope: str
    ) -> OwnerLinkChoice:
        """Steps 4 to 8 of a choose (S16), from the retained card only.

        The idempotency replay (step 3) and the write itself belong to the
        caller, which records `target` against `expected_current` and so
        refuses `link_changed` again inside its own transaction.
        """
        state = self._state(session_token, search_id)
        if type(position) is not int or not 0 <= position < len(state.cards):
            raise OwnerSearchRefused("card_not_choosable")
        card = state.cards[position]
        if not card.choosable:
            raise OwnerSearchRefused("card_not_choosable")
        try:
            chosen = OwnerLinkScope(scope)
            target = card.target(chosen)
        except (OwnerLinkError, ValueError) as error:
            raise OwnerSearchRefused("scope_not_offered") from error
        seen = state.subject
        try:
            current = self.subject(seen.connection, seen.library, seen.item)
        except OwnerSearchRefused as error:
            raise OwnerSearchRefused("work_changed") from error
        if current.identity != seen.identity:
            raise OwnerSearchRefused("work_changed")
        if current.link_row_id != seen.link_row_id:
            raise OwnerSearchRefused("link_changed")
        return OwnerLinkChoice(
            seen.connection,
            seen.library,
            seen.item,
            seen.medium,
            OwnerLinkAction.LINK
            if seen.link_row_id is None
            else OwnerLinkAction.REPLACE,
            seen.link_row_id,
            target,
        )

    def resolve_withdraw(
        self, connection: str, library: str, item: str, link_row_id: str
    ) -> OwnerSearchSubject:
        """A withdraw's work, refused `link_changed` unless the row is current."""
        subject = self.subject(connection, library, item)
        if subject.link_row_id is None or subject.link_row_id != link_row_id:
            raise OwnerSearchRefused("link_changed")
        return subject

    def _state(self, session_token: str, search_id: str) -> OwnerSearchState:
        """The session's search, refused alike when foreign or expired."""
        state = self._memory.get(session_token)
        if state is None or not secrets.compare_digest(
            state.search_id.encode(), str(search_id).encode()
        ):
            raise OwnerSearchRefused("search_not_found")
        return state


def _offered_sources(
    medium: str,
    providers: tuple[ProviderConnectionConfiguration, ...],
    languages: tuple[str, ...],
) -> tuple[SearchableSource, ...]:
    """The library's enabled dispatch sources that have a search shape (U6).

    Preparation's own selection: the library's provider connections
    (`ApplicationConfiguration.select`), enabled, inside the medium's dispatch
    set (`dispatch_adapters`). Audible lists the library's marketplace order.
    """
    audio = medium == "audiobook"
    marketplaces = audiobook_marketplaces(providers, languages) if audio else ()
    sources: dict[str, SearchableSource] = {}
    for provider in providers:
        if (
            provider.enabled
            and provider.adapter in dispatch_adapters(audio=audio)
            and provider.adapter in LINK_SOURCES_BY_MEDIUM[medium]
            and provider.adapter not in sources
        ):
            sources[provider.adapter] = SearchableSource(
                provider,
                _LANES[provider.adapter],
                marketplaces if provider.adapter == "audible_catalog" else (),
            )
    return tuple(sources[name] for name in sorted(sources))


def _query(request: OwnerSearchRequest, source: SearchableSource) -> OwnerSearchQuery:
    """Step 6, after the source: the text rule, ISBN checksum, ASIN grammar.

    A lane or marketplace the source does not have is the source not being
    offered for that search.
    """
    if request.lane not in source.lanes:
        raise OwnerSearchRefused("provider_not_offered")
    marketplace = None
    if source.marketplaces:
        marketplace = request.marketplace or source.marketplaces[0]
        if marketplace not in source.marketplaces:
            raise OwnerSearchRefused("provider_not_offered")
    elif request.marketplace is not None:
        raise OwnerSearchRefused("provider_not_offered")
    lane = request.lane
    if lane == "title_authors":
        if not owner_text(request.title):
            raise OwnerSearchRefused("title_invalid")
        if not 1 <= len(request.authors) <= MAX_OWNER_SEARCH_AUTHORS or not all(
            owner_text(value) for value in request.authors
        ):
            raise OwnerSearchRefused("author_invalid")
        return OwnerSearchQuery(
            lane,
            title=request.title,
            authors=request.authors,
            marketplace=marketplace,
        )
    try:
        if lane == "isbn":
            return OwnerSearchQuery(lane, isbn=owner_isbn(request.isbn))
        return OwnerSearchQuery(
            lane, asin=owner_asin(request.asin), marketplace=marketplace
        )
    except OwnerLinkError as error:
        raise OwnerSearchRefused(f"{lane}_invalid") from error


def _session_key(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8", "surrogatepass")).hexdigest()


def _emit_search(
    adapter: str,
    lane: str,
    outcome: str,
    cards: int = 0,
    dropped: int = 0,
    more: bool = False,
) -> None:
    emit_owner_events(
        owner_search_event,
        adapter=adapter,
        lane=lane,
        outcome=outcome,
        cards=cards,
        dropped=dropped,
        more=more,
    )


__all__ = [
    "OwnerLinkChoice",
    "OwnerSearchRefused",
    "OwnerSearchRequest",
    "OwnerSearchResult",
    "OwnerSearchService",
    "OwnerSearchState",
    "OwnerSearchSubject",
    "SearchableSource",
    "find_offered",
]
