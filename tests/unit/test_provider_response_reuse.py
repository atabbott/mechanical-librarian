"""A held provider answer is served in place of the network (#1064, ADR-0099).

The session serves a held answer exactly as the live dispatch it replaces,
minus the pacer wait and the request, and the retaining wrapper keeps an
observation's answers only when the observation and every lane of it ended
clean. These drive real sessions and adapters over `httpx.MockTransport`
against a real store in a temporary directory.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from mechanical_librarian.adapters.persistence.provider_responses import (
    SQLiteProviderResponseStore,
)
from mechanical_librarian.adapters.providers import response_reuse
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.response_reuse import (
    ProviderResponseReuse,
    RetainingProvider,
    provider_response_reuse,
)
from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderFailure,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
)
from mechanical_librarian.adapters.providers.sdk import (
    BoundedProviderSession,
    ProviderClocks,
    ProviderResponseFacts,
    provider_bounds,
)
from mechanical_librarian.application.failure_diagnostics import (
    provider_result_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    MetadataProvider,
    ProviderResult,
)
from mechanical_librarian.application.provider_evidence import (
    ProviderIncompleteCause,
    ProviderIncompleteCauseRecord,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus
from tests.support.google_books import (
    discovery_plan,
    identifier_first_plan,
    volume,
)
from tests.support.hardcover_evidence import (
    hardcover_answer,
    isbn_claim,
    isbn_lookup,
    work_lookup,
)

_URL = "https://provider.invalid/v1/records"
_HEADERS = {"Accept": "application/json", "Accept-Encoding": "identity"}
_WALL = 1_700_000_000.123456


class _Pacer:
    def __init__(self) -> None:
        self.waits = 0

    def wait(self, deadline: float | None) -> bool:
        self.waits += 1
        return True


def _store(tmp_path: Path) -> SQLiteProviderResponseStore:
    return SQLiteProviderResponseStore.open(tmp_path / "provider-responses.sqlite")


def _rows(store: SQLiteProviderResponseStore) -> int:
    (count,) = store._connection.execute(
        "SELECT count(*) FROM provider_responses"
    ).fetchone()
    return int(count)


def _clocks(wall: float = _WALL) -> ProviderClocks:
    return ProviderClocks(
        clock=lambda: 0.0,
        wall_clock=lambda: wall,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


def _session(
    handler: Callable[[httpx.Request], httpx.Response],
    reuse: ProviderResponseReuse | None,
    *,
    pacer: _Pacer | None = None,
    wall: float = _WALL,
) -> BoundedProviderSession:
    return BoundedProviderSession(
        "provider",
        httpx.Client(transport=httpx.MockTransport(handler)),
        provider_bounds(
            65_536,
            5.0,
            attempts_per_request=3,
            attempts_per_observation=9,
            aggregate_multiple=9,
            aggregate_ceiling_bytes=2_097_152,
        ),
        _DiscoveryRequestLedger("provider", "v1"),
        pacer=cast(ProviderRequestPacer, pacer),
        clocks=_clocks(wall),
        responses=reuse,
    )


def _unclassified(_: ProviderResponseFacts) -> ProviderFailure | None:
    return None


def _ask(
    session: BoundedProviderSession, query: str = "value", **arguments: Any
) -> dict[str, Any]:
    return session.request_json(
        _URL,
        params={"q": query},
        headers=_HEADERS,
        deadline=arguments.pop("deadline", None),
        classify=_unclassified,
        **arguments,
    )


def _json(payload: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(
        200, headers={"content-type": "application/json"}, json=payload
    )


def _never(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected request to {request.url.host}")


def _held(
    tmp_path: Path, payload: object = None
) -> tuple[SQLiteProviderResponseStore, ProviderResponseReuse]:
    store = _store(tmp_path)
    reuse = ProviderResponseReuse(store, "open_library", False)
    _ask(_session(_json({"value": 1} if payload is None else payload), reuse))
    reuse.settle(True)
    return store, ProviderResponseReuse(store, "open_library", False)


def test_a_held_answer_is_served_without_pacing_or_network(tmp_path: Path) -> None:
    store = _store(tmp_path)
    live_pacer, sent = _Pacer(), []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _json({"value": 1})(request)

    reuse = ProviderResponseReuse(store, "open_library", False)
    live = _session(answer, reuse, pacer=live_pacer)
    assert _ask(live) == {"value": 1}
    reuse.settle(True)
    assert (len(sent), live_pacer.waits, _rows(store)) == (1, 1, 1)

    reused_pacer = _Pacer()
    reused = _session(
        _never, ProviderResponseReuse(store, "open_library", False), pacer=reused_pacer
    )
    assert _ask(reused) == {"value": 1}
    assert reused_pacer.waits == 0
    assert (reused.attempt_count, reused.retry_count) == (1, 0)
    assert reused.remaining_response_bytes == live.remaining_response_bytes
    (record,) = reused.attempt_records
    # The attempt reads as the live one did.
    assert reused.attempt_records == live.attempt_records
    # The session reports when the held answer was fetched, not now.
    assert reused.accessed_at == live.accessed_at == "2023-11-14T22:13:20.123456Z"
    store.close()


def _google(
    handler: Callable[[httpx.Request], httpx.Response],
    store: SQLiteProviderResponseStore,
    *,
    wall: float = _WALL,
    refresh: bool = False,
    api_key: str | None = None,
) -> MetadataProvider:
    reuse = ProviderResponseReuse(store, "google_books", refresh)
    return RetainingProvider(
        GoogleBooksMetadataProvider(
            "google_books",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            api_key=api_key,
            clock=lambda: 0.0,
            wall_clock=lambda: wall,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
            responses=reuse,
        ),
        reuse,
    )


def _volumes(_: httpx.Request) -> httpx.Response:
    return _json({"totalItems": 1, "items": [volume()]})(_)


def test_a_reused_observation_reads_exactly_as_the_live_one(tmp_path: Path) -> None:
    store = _store(tmp_path)
    live = _google(_volumes, store).observe(discovery_plan())
    assert live.observation.status is ProviderStatus.CANDIDATES
    stored = _rows(store)
    assert stored == live.observation.request_accounting.initial_dispatches > 1

    # A later clock: everything the report keeps still comes from the answers.
    reused = _google(_never, store, wall=_WALL + 86_400).observe(discovery_plan())
    assert reused.observation == live.observation
    assert reused.evidence.accessed_at == live.evidence.accessed_at
    assert provider_result_diagnostics(
        reused.observation, reused.evidence
    ) == provider_result_diagnostics(live.observation, live.evidence)
    live_records = live.evidence.attempt_records or ()
    reused_records = reused.evidence.attempt_records or ()
    assert [
        (value.ordinal, value.category, value.outcome) for value in reused_records
    ] == [(value.ordinal, value.category, value.outcome) for value in live_records]
    # A hit writes nothing.
    assert _rows(store) == stored
    store.close()


@pytest.mark.parametrize("keeps_budget", (False, True), ids=("ends_budget", "keeps"))
def test_a_held_body_over_the_current_bound_is_oversized(
    tmp_path: Path, keeps_budget: bool
) -> None:
    store, reuse = _held(tmp_path, {"value": "x" * 200})
    session = _session(_never, reuse)
    before = session.remaining_response_bytes
    with pytest.raises(ProviderFailure) as raised:
        _ask(session, maximum_bytes=64, oversize_keeps_budget=keeps_budget)
    assert raised.value.status is ProviderStatus.OVERSIZED
    assert session.remaining_response_bytes == (before if keeps_budget else 0)
    (record,) = session.attempt_records
    assert (record.category, record.outcome) == ("response_oversized", "terminal")
    store.close()


def test_a_hit_past_the_deadline_stops_before_dispatch(tmp_path: Path) -> None:
    store, reuse = _held(tmp_path)
    session = _session(_never, reuse)
    with pytest.raises(ProviderFailure) as raised:
        _ask(session, deadline=0.0)
    assert raised.value.status is ProviderStatus.INCOMPLETE
    assert session.incomplete_causes == (
        ProviderIncompleteCauseRecord(ProviderIncompleteCause.DEADLINE),
    )
    assert (session.attempt_count, session.attempt_records) == (0, ())
    store.close()


def test_an_observation_with_a_failed_lane_keeps_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params["q"].startswith("isbn:"):
            other = volume()
            other["volumeInfo"]["title"] = "Different Book"
            other["volumeInfo"]["authors"] = ["Other Author"]
            return _json({"totalItems": 1, "items": [other]})(request)
        return httpx.Response(503, headers={"content-type": "application/json"})

    result = _google(answer, store).observe(identifier_first_plan())
    assert result.observation.status is ProviderStatus.CANDIDATES
    assert ProviderStatus.UNAVAILABLE in {
        lane.status for lane in result.observation.lanes
    }
    assert _rows(store) == 0
    store.close()


def test_a_refused_answer_is_never_kept_even_when_its_lane_ends_clean(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    reuse = ProviderResponseReuse(store, "hardcover", False)
    session = _session(_json({"value": 1}), reuse)
    _ask(session, "kept")
    _ask(session, "refused")
    session.mark_response_attempt("candidate_limit_exceeded")
    reuse.settle(True)
    assert _rows(store) == 1
    assert session.attempt_records[-1].outcome == "terminal"

    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["q"])
        return _json({"value": 1})(request)

    replay = _session(answer, ProviderResponseReuse(store, "hardcover", False))
    _ask(replay, "kept")
    _ask(replay, "refused")
    assert asked == ["refused"]
    store.close()


def test_a_held_answer_its_adapter_now_refuses_is_removed(tmp_path: Path) -> None:
    store, reuse = _held(tmp_path)
    session = _session(_never, reuse)
    _ask(session)
    session.mark_response_attempt("candidate_limit_exceeded")
    assert session.attempt_records[-1].outcome == "terminal"
    assert _rows(store) == 0
    store.close()


def test_a_held_body_the_reader_now_refuses_is_removed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reuse = ProviderResponseReuse(store, "open_library", False)
    digest = reuse.digest("GET", _URL, {"q": "value"}, None)
    store.commit("open_library", ((digest, 1, b"not json"),))

    with pytest.raises(ProviderFailure) as refused:
        _ask(_session(_never, reuse))
    assert refused.value.status is ProviderStatus.MALFORMED
    assert _rows(store) == 0

    # The request is asked live the next time, and its answer is kept again.
    again = ProviderResponseReuse(store, "open_library", False)
    assert _ask(_session(_json({"value": 2}), again)) == {"value": 2}
    again.settle(True)
    assert _rows(store) == 1
    store.close()


def _hardcover(
    handler: Callable[[httpx.Request], httpx.Response],
    store: SQLiteProviderResponseStore,
    sent: list[dict[str, object]],
) -> MetadataProvider:
    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return handler(request)

    reuse = ProviderResponseReuse(store, "hardcover", False)
    return RetainingProvider(
        HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(record)),
            262_144,
            clock=lambda: 0.0,
            wall_clock=lambda: _WALL,
            sleep=lambda _: None,
            jitter=lambda: 0.0,
            responses=reuse,
        ),
        reuse,
    )


def _lookup(value: object) -> MetadataDiscoveryPlan:
    # Every adapter also answers a single lookup, which the protocol, written
    # for the report's plan, does not name.
    return cast(MetadataDiscoveryPlan, value)


def test_a_hardcover_declared_error_keeps_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sent: list[dict[str, object]] = []
    observation = (
        _hardcover(_json({"errors": [{"message": "declared"}]}), store, sent)
        .observe(_lookup(isbn_lookup((isbn_claim(),))))
        .observation
    )
    assert observation.status is ProviderStatus.UNAVAILABLE
    assert (len(sent), _rows(store)) == (1, 0)
    store.close()


def _book(book_id: int) -> dict[str, object]:
    return {
        "id": book_id,
        "title": "Current title",
        "canonical_id": None,
        "release_date": "2001-02-03",
        "contributions": [
            {"id": book_id, "contribution": None, "author": {"id": 7, "name": "Ada"}}
        ],
    }


def test_a_hardcover_narrowing_retry_keeps_only_the_answer_it_read(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    def answer(request: httpx.Request) -> httpx.Response:
        authors = json.loads(request.content)["variables"]["authors"]
        books = [_book(index) for index in range(1, 7)] if len(authors) > 1 else []
        return httpx.Response(200, json=hardcover_answer(request, [], books))

    lookup = _lookup(work_lookup(authors=("Ada Example", "Bea Example")))
    sent: list[dict[str, object]] = []
    observation = _hardcover(answer, store, sent).observe(lookup).observation
    # The six-book answer was refused and narrowed; the lookup ended clean.
    assert observation.status is ProviderStatus.NO_MATCH
    assert (len(sent), _rows(store)) == (2, 1)

    again: list[dict[str, object]] = []
    assert _hardcover(answer, store, again).observe(lookup).observation == observation
    # Only the refused overflow is asked again.
    assert [body["variables"]["authors"] for body in again] == [
        ["Ada Example", "Bea Example"]
    ]
    store.close()


def test_a_google_books_key_never_reaches_the_digest_or_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is sent, but no key or digest input carries it (S4).

    Expected to fail until #1885 moves the key from the query to a header.
    """
    key = "synthetic-google-key-0123456789"
    store = _store(tmp_path)
    wire: list[httpx.Request] = []
    inputs: list[bytes] = []
    canonical = response_reuse._canonical_request

    def capture(*arguments: Any) -> bytes:
        inputs.append(canonical(*arguments))
        return inputs[-1]

    monkeypatch.setattr(response_reuse, "_canonical_request", capture)

    def answer(request: httpx.Request) -> httpx.Response:
        wire.append(request)
        return _volumes(request)

    _google(answer, store, api_key=key).observe(discovery_plan())
    store.close()
    assert wire and all(
        key in str(request.url) or request.headers.get("x-goog-api-key") == key
        for request in wire
    )
    assert inputs and not any(key.encode() in value for value in inputs)
    assert key.encode() not in (tmp_path / "provider-responses.sqlite").read_bytes()


def test_a_research_asks_live_and_replaces_the_held_answer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _google(_volumes, store).observe(identifier_first_plan())
    (old,) = store._connection.execute(
        "SELECT fetched_at_us FROM provider_responses"
    ).fetchone()

    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _volumes(request)

    later = _google(answer, store, wall=_WALL + 7 * 86_400, refresh=True)
    later.observe(identifier_first_plan())
    assert len(sent) == 1
    assert store._connection.execute(
        "SELECT count(*),max(fetched_at_us) FROM provider_responses"
    ).fetchone() == (1, old + 7 * 86_400 * 1_000_000)
    store.close()


class _Failing:
    def __init__(self, session: BoundedProviderSession) -> None:
        self.session = session

    def observe(self, plan: object, *, deadline: float | None = None) -> ProviderResult:
        _ask(self.session)
        raise RuntimeError("adapter fault")


def test_an_adapter_failure_discards_what_it_held(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reuse = ProviderResponseReuse(store, "open_library", False)
    provider = RetainingProvider(
        cast(MetadataProvider, _Failing(_session(_json({"value": 1}), reuse))), reuse
    )
    with pytest.raises(RuntimeError):
        provider.observe(discovery_plan())
    assert _rows(store) == 0
    store.close()


def _digest(
    adapter: str,
    url: str,
    params: dict[str, str] | httpx.QueryParams | None = None,
    content: bytes | None = None,
    method: str = "GET",
) -> str:
    reuse = provider_response_reuse(
        cast(SQLiteProviderResponseStore, object()), adapter, False
    )
    assert reuse is not None
    return reuse.digest(method, url, params, content)


@pytest.mark.parametrize(
    ("first", "second"),
    (
        (
            ("audible_catalog", "https://api.audible.com/1.0/catalog/products"),
            ("audible_catalog", "https://api.audible.co.uk/1.0/catalog/products"),
        ),
        (
            ("audnexus", "https://api.audnex.us/books/B0", {"region": "us"}),
            ("audnexus", "https://api.audnex.us/books/B0", {"region": "uk"}),
        ),
        (
            (
                "inventaire",
                "https://inventaire.io/api/entities",
                httpx.QueryParams([("uris", "isbn:1"), ("uris", "isbn:2")]),
            ),
            (
                "inventaire",
                "https://inventaire.io/api/entities",
                httpx.QueryParams([("uris", "isbn:1"), ("uris", "isbn:3")]),
            ),
        ),
        (
            (
                "hardcover",
                "https://api.hardcover.app/v1/graphql",
                None,
                b'{"a":1}',
                "POST",
            ),
            (
                "hardcover",
                "https://api.hardcover.app/v1/graphql",
                None,
                b'{"a":2}',
                "POST",
            ),
        ),
        (
            ("hardcover", "https://api.hardcover.app/v1/graphql", None, None, "GET"),
            ("hardcover", "https://api.hardcover.app/v1/graphql", None, None, "POST"),
        ),
    ),
    ids=(
        "audible_host",
        "audnexus_region",
        "inventaire_values",
        "hardcover_body",
        "method",
    ),
)
def test_each_request_fact_an_adapter_varies_is_in_the_key(
    first: tuple[Any, ...], second: tuple[Any, ...]
) -> None:
    assert _digest(*first) != _digest(*second)


def test_query_order_is_not_in_the_key() -> None:
    url = "https://openlibrary.org/search.json"
    assert _digest("open_library", url, {"q": "a", "fields": "b"}) == _digest(
        "open_library", url + "?fields=b", {"q": "a"}
    )
    assert _digest(
        "inventaire", url, httpx.QueryParams([("x", "1"), ("y", "2")])
    ) == _digest("inventaire", url, httpx.QueryParams([("y", "2"), ("x", "1")]))


def test_a_session_without_a_store_reads_the_wall_clock_as_before() -> None:
    reads: list[float] = []

    def wall() -> float:
        reads.append(_WALL)
        return _WALL

    session = BoundedProviderSession(
        "provider",
        httpx.Client(transport=httpx.MockTransport(_json({"value": 1}))),
        provider_bounds(
            65_536,
            5.0,
            attempts_per_request=3,
            attempts_per_observation=9,
            aggregate_multiple=9,
            aggregate_ceiling_bytes=2_097_152,
        ),
        _DiscoveryRequestLedger("provider", "v1"),
        clocks=ProviderClocks(
            clock=lambda: 0.0, wall_clock=wall, sleep=lambda _: None, jitter=lambda: 0.0
        ),
    )
    _ask(session, "one")
    _ask(session, "two")
    assert len(reads) == 1
