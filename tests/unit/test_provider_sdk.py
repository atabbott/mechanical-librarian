from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderFailure,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
)
from mechanical_librarian.adapters.providers.sdk import (
    BoundedProviderSession,
    ProviderClocks,
    ProviderResponseFacts,
    decode_json,
    provider_bounds,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus

_URL = "https://provider.invalid/v1/records"
_HEADERS = {"Accept": "application/json", "Accept-Encoding": "identity"}


def _bounds(
    maximum_bytes: int = 65_536,
    timeout_seconds: float = 5.0,
    *,
    attempts_per_request: int = 3,
    attempts_per_observation: int = 9,
    aggregate_multiple: int = 9,
    aggregate_ceiling_bytes: int = 2_097_152,
):
    return provider_bounds(
        maximum_bytes,
        timeout_seconds,
        attempts_per_request=attempts_per_request,
        attempts_per_observation=attempts_per_observation,
        aggregate_multiple=aggregate_multiple,
        aggregate_ceiling_bytes=aggregate_ceiling_bytes,
    )


def _json(payload: object, status_code: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json", **headers},
        json=payload,
    )


class _RawStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self):
        yield self._payload


def _unclassified(_: ProviderResponseFacts) -> ProviderFailure | None:
    return None


def _session(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    bounds: Any = None,
    clocks: ProviderClocks | None = None,
    pacer: ProviderRequestPacer | None = None,
    ledger: _DiscoveryRequestLedger | None = None,
    accept_content_types: tuple[str, ...] = ("application/json",),
) -> BoundedProviderSession:
    return BoundedProviderSession(
        "provider",
        httpx.Client(transport=httpx.MockTransport(handler)),
        _bounds() if bounds is None else bounds,
        _DiscoveryRequestLedger("provider", "v1") if ledger is None else ledger,
        pacer=pacer,
        clocks=clocks,
        accept_content_types=accept_content_types,
    )


def _request(
    session: BoundedProviderSession,
    *,
    deadline: float | None = None,
    query_retry: bool = False,
    classify: Callable[[ProviderResponseFacts], ProviderFailure | None] = _unclassified,
) -> dict[str, Any]:
    return session.request_json(
        _URL,
        params={"q": "value"},
        headers=_HEADERS,
        deadline=deadline,
        query_retry=query_retry,
        classify=classify,
    )


def _quiet_clocks(**overrides: Any) -> ProviderClocks:
    settings: dict[str, Any] = {
        "clock": lambda: 0.0,
        "wall_clock": lambda: 1_700_000_000.0,
        "sleep": lambda _: None,
        "jitter": lambda: 0.0,
    }
    settings.update(overrides)
    return ProviderClocks(**settings)


@pytest.mark.parametrize(
    ("maximum_bytes", "timeout_seconds"),
    ((0, 5.0), (True, 5.0), ("1024", 5.0), (1_024, 0), (1_024, 61), (1_024, True)),
    ids=(
        "zero_bytes",
        "boolean_bytes",
        "textual_bytes",
        "zero_timeout",
        "over_bound_timeout",
        "boolean_timeout",
    ),
)
def test_bounds_reject_noncanonical_values(
    maximum_bytes: object, timeout_seconds: object
) -> None:
    with pytest.raises(ValueError, match="bounds"):
        _bounds(maximum_bytes, timeout_seconds)  # type: ignore[arg-type]


def test_bounds_derive_one_aggregate_budget_under_its_ceiling() -> None:
    assert 9_216 == _bounds(1_024).aggregate_response_bytes
    assert 2_097_152 == _bounds(1_000_000).aggregate_response_bytes
    assert 5.0 == _bounds(1_024, 5).timeout_seconds


def test_attempts_per_request_bounds_one_retryable_status() -> None:
    dispatches = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return _json({"error": True}, 503)

    session = _session(handler, clocks=_quiet_clocks())
    with pytest.raises(ProviderFailure) as failure:
        _request(session)

    assert ProviderStatus.UNAVAILABLE == failure.value.status
    assert 3 == dispatches
    assert ["retry", "retry", "exhausted"] == [
        record.outcome for record in session.attempt_records
    ]
    assert ["http_503"] * 3 == [record.category for record in session.attempt_records]
    assert (1, 2, 3) == tuple(record.ordinal for record in session.attempt_records)


def test_observation_ceilings_refuse_dispatch_with_closed_causes() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("a refused dispatch must not reach the transport")

    exhausted_bytes = _session(handler)
    exhausted_bytes.remaining_response_bytes = 0
    with pytest.raises(ProviderFailure) as byte_failure:
        _request(exhausted_bytes)
    assert ProviderStatus.INCOMPLETE == byte_failure.value.status
    assert ["aggregate_bytes"] == [
        record.cause.value for record in exhausted_bytes.incomplete_causes
    ]

    exhausted_dispatches = _session(handler)
    exhausted_dispatches.attempt_count = 9
    with pytest.raises(ProviderFailure):
        _request(exhausted_dispatches)
    assert ["dispatch_count"] == [
        record.cause.value for record in exhausted_dispatches.incomplete_causes
    ]

    expired = _session(handler, clocks=_quiet_clocks(clock=lambda: 1.0))
    with pytest.raises(ProviderFailure):
        _request(expired, deadline=0.0)
    assert ["deadline"] == [record.cause.value for record in expired.incomplete_causes]
    assert () == expired.attempt_records
    assert expired.accessed_at is None


def test_registered_lane_work_reserves_the_ceiling_against_a_retry() -> None:
    dispatches = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return _json({"error": True}, 503)

    ledger = _DiscoveryRequestLedger("provider", "v1")
    for _ in range(8):
        ledger.register_detail(f"record-{dispatches}-{_}")
    session = _session(
        handler,
        clocks=_quiet_clocks(
            sleep=lambda _: pytest.fail("reserved work must not wait")
        ),
        ledger=ledger,
    )

    with pytest.raises(ProviderFailure):
        _request(session)

    assert 1 == dispatches
    assert ["exhausted"] == [record.outcome for record in session.attempt_records]


def test_503_carries_its_bounded_retry_after_and_recovers() -> None:
    responses = [
        _json({"error": True}, 503, **{"retry-after": "3"}),
        _json({"totalItems": 0}),
    ]
    delays: list[float] = []
    session = _session(
        lambda _: responses.pop(0),
        clocks=_quiet_clocks(sleep=delays.append, jitter=lambda: 0.5),
    )

    assert {"totalItems": 0} == _request(session)
    assert [3.0] == delays
    assert ["http_503", "none"] == [
        record.category for record in session.attempt_records
    ]


def test_classifier_owns_every_other_status_and_unknowns_are_terminal() -> None:
    def classify(facts: ProviderResponseFacts) -> ProviderFailure | None:
        assert 1_700_000_000.0 == facts.now
        if facts.status_code == 429:
            return ProviderFailure(
                ProviderStatus.RATE_LIMITED,
                category="burst_throttled",
                retryable=True,
                retry_after=2.0,
            )
        return None

    responses = [_json({"error": True}, 429), _json({"totalItems": 0})]
    delays: list[float] = []
    throttled = _session(
        lambda _: responses.pop(0),
        clocks=_quiet_clocks(sleep=delays.append),
    )
    assert {"totalItems": 0} == _request(throttled, classify=classify)
    assert [2.0] == delays
    assert "burst_throttled" == throttled.attempt_records[0].category

    unknown = _session(
        lambda _: _json({"error": True}, 418),
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("unknown must not retry")),
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(unknown, classify=classify)
    assert ProviderStatus.UNAVAILABLE == failure.value.status
    assert ["terminal"] == [record.outcome for record in unknown.attempt_records]
    assert ["http_418"] == [record.category for record in unknown.attempt_records]


def test_response_bytes_are_charged_once_and_oversize_closes_the_budget() -> None:
    session = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"value":"' + b"x" * 512 + b'"}',
        ),
        bounds=_bounds(1_024),
    )
    assert 9_216 == session.remaining_response_bytes
    _request(session)
    assert 9_216 - 524 == session.remaining_response_bytes

    oversized = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * 2_048,
        ),
        bounds=_bounds(1_024),
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("oversize must not retry")),
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(oversized)
    assert ProviderStatus.OVERSIZED == failure.value.status
    assert 0 == oversized.remaining_response_bytes
    with pytest.raises(ProviderFailure):
        _request(oversized)
    assert ["aggregate_bytes"] == [
        record.cause.value for record in oversized.incomplete_causes
    ]


def test_an_optional_body_over_its_bound_costs_only_the_bytes_read() -> None:
    """A dropped optional body leaves the budget for later lanes (#1308)."""
    declared = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * 2_048,
        ),
        bounds=_bounds(1_024),
    )
    with pytest.raises(ProviderFailure) as failure:
        declared.request_json(
            _URL,
            headers=_HEADERS,
            deadline=None,
            classify=_unclassified,
            oversize_keeps_budget=True,
        )
    assert ProviderStatus.OVERSIZED == failure.value.status
    assert ["response_oversized"] == [
        record.category for record in declared.attempt_records
    ]
    # The declared length refused the body before any of it was read.
    assert 9_216 == declared.remaining_response_bytes

    def streamed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=iter((b"x" * 1_000, b"x" * 1_000)),
        )

    chunked = _session(streamed, bounds=_bounds(1_024))
    with pytest.raises(ProviderFailure):
        chunked.request_json(
            _URL,
            headers=_HEADERS,
            deadline=None,
            classify=_unclassified,
            oversize_keeps_budget=True,
        )
    # Every chunk read is charged, and nothing more.
    assert 9_216 - 2_000 == chunked.remaining_response_bytes


def test_the_session_names_its_own_malformed_and_oversized_attempts() -> None:
    malformed = _session(
        lambda _: httpx.Response(
            200, headers={"content-type": "application/json"}, content=b"{"
        )
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(malformed)
    assert ProviderStatus.MALFORMED == failure.value.status
    assert ["response_malformed"] == [
        record.category for record in malformed.attempt_records
    ]


def _sized(size: int) -> Callable[[httpx.Request], httpx.Response]:
    body = b'{"value":"' + b"x" * (size - 12) + b'"}'
    assert size == len(body)
    return lambda _: httpx.Response(
        200, headers={"content-type": "application/json"}, content=body
    )


def test_a_per_request_bound_replaces_the_configured_one_for_that_request() -> None:
    """The #1256 override widens or narrows one body and nothing else."""
    wider = _session(_sized(2_048), bounds=_bounds(1_024))
    assert {"value": "x" * 2_036} == wider.request_json(
        _URL,
        headers=_HEADERS,
        deadline=None,
        classify=_unclassified,
        maximum_bytes=4_096,
    )
    assert 9_216 - 2_048 == wider.remaining_response_bytes
    # The configured bound still governs a request that names none.
    with pytest.raises(ProviderFailure) as configured:
        _request(wider)
    assert ProviderStatus.OVERSIZED == configured.value.status

    narrower = _session(_sized(600), bounds=_bounds(1_024))
    with pytest.raises(ProviderFailure) as failure:
        narrower.request_json(
            _URL,
            headers=_HEADERS,
            deadline=None,
            classify=_unclassified,
            maximum_bytes=512,
        )
    assert ProviderStatus.OVERSIZED == failure.value.status
    assert 0 == narrower.remaining_response_bytes


def test_a_per_request_bound_never_lifts_the_aggregate_budget() -> None:
    session = _session(_sized(10_000), bounds=_bounds(1_024))
    assert 9_216 == session.remaining_response_bytes
    with pytest.raises(ProviderFailure) as failure:
        session.request_json(
            _URL,
            headers=_HEADERS,
            deadline=None,
            classify=_unclassified,
            maximum_bytes=262_144,
        )
    assert ProviderStatus.OVERSIZED == failure.value.status
    assert 0 == session.remaining_response_bytes


@pytest.mark.parametrize(
    "maximum_bytes",
    (0, -1, True, "4096", 4_096.0),
    ids=("zero", "negative", "boolean", "textual", "float"),
)
def test_a_per_request_bound_outside_its_shape_is_refused_before_dispatch(
    maximum_bytes: object,
) -> None:
    session = _session(lambda _: pytest.fail("an invalid bound must not dispatch"))
    with pytest.raises(ValueError, match="bounds"):
        session.request_json(
            _URL,
            headers=_HEADERS,
            deadline=None,
            classify=_unclassified,
            maximum_bytes=maximum_bytes,  # type: ignore[arg-type]
        )
    assert 0 == session.attempt_count


def test_pacing_refusal_and_unfitting_backoff_stay_inside_the_deadline() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("a paced-out dispatch must not reach the transport")

    pacer = ProviderRequestPacer(1.0, clock=lambda: 0.0, sleep=lambda _: None)
    assert pacer.wait()
    paced = _session(handler, clocks=_quiet_clocks(), pacer=pacer)
    with pytest.raises(ProviderFailure):
        _request(paced, deadline=0.5)
    assert ["deadline"] == [record.cause.value for record in paced.incomplete_causes]

    dispatches = 0

    def unavailable(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        return _json({"error": True}, 503)

    bounded = _session(
        unavailable,
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("backoff must not overrun")),
    )
    with pytest.raises(ProviderFailure):
        _request(bounded, deadline=0.5)
    assert 1 == dispatches
    assert ["exhausted"] == [record.outcome for record in bounded.attempt_records]


def test_access_time_is_stamped_once_at_the_first_real_dispatch() -> None:
    stamps = iter((1_700_000_000.125, 1_700_000_600.5))
    session = _session(
        lambda _: _json({"totalItems": 0}),
        clocks=_quiet_clocks(wall_clock=lambda: next(stamps)),
    )
    assert session.accessed_at is None
    _request(session)
    _request(session)
    assert "2023-11-14T22:13:20.125000Z" == session.accessed_at


def test_only_narrow_transport_failures_are_retried() -> None:
    dispatches = 0

    def transient(_: httpx.Request) -> httpx.Response:
        nonlocal dispatches
        dispatches += 1
        if dispatches == 1:
            raise httpx.ReadTimeout("synthetic timeout")
        return _json({"totalItems": 0})

    recovered = _session(transient, clocks=_quiet_clocks())
    assert {"totalItems": 0} == _request(recovered)
    assert 2 == dispatches
    assert "transport_read_timeout" == recovered.attempt_records[0].category

    def unknown(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset by peer")

    failed = _session(
        unknown,
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("unknown must not retry")),
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(failed)
    assert ProviderStatus.UNAVAILABLE == failure.value.status
    assert ["terminal"] == [record.outcome for record in failed.attempt_records]


def test_cancellation_records_one_attempt_and_propagates() -> None:
    def interrupted(_: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    cancelled = _session(interrupted, clocks=_quiet_clocks())
    with pytest.raises(KeyboardInterrupt):
        _request(cancelled)
    assert ["cancelled"] == [record.outcome for record in cancelled.attempt_records]

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    sleep_cancelled = _session(
        lambda _: _json({"error": True}, 503),
        clocks=_quiet_clocks(sleep=interrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        _request(sleep_cancelled)
    assert ["cancelled"] == [
        record.outcome for record in sleep_cancelled.attempt_records
    ]


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"{}"),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"[1, 2]",
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"a": 1, "a": 2}',
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"a": NaN}',
        ),
    ),
    ids=("wrong_media_type", "not_an_object", "duplicate_field", "rejected_constant"),
)
def test_only_strict_json_objects_are_returned(response: httpx.Response) -> None:
    session = _session(
        lambda _: response,
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("malformed must not retry")),
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(session)
    assert ProviderStatus.MALFORMED == failure.value.status


def test_an_admitted_content_type_is_the_only_widening() -> None:
    """A widened set admits exactly its members and nothing adjacent to them."""
    widened = ("application/json", "text/javascript")
    accepted = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/javascript; charset=utf-8"},
            content=b'{"resultCount": 0}',
        ),
        clocks=_quiet_clocks(),
        accept_content_types=widened,
    )
    assert {"resultCount": 0} == _request(accepted)
    refused = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"{}",
        ),
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("malformed must not retry")),
        accept_content_types=widened,
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(refused)
    assert ProviderStatus.MALFORMED == failure.value.status


def test_a_widened_session_still_refuses_what_the_default_refuses() -> None:
    """The default set is unchanged, so an unwidened caller sees no new type."""
    session = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/javascript"},
            content=b"{}",
        ),
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("malformed must not retry")),
    )
    with pytest.raises(ProviderFailure) as failure:
        _request(session)
    assert ProviderStatus.MALFORMED == failure.value.status


@pytest.mark.parametrize(
    "accept_content_types",
    ((), "application/json", ("Application/JSON",), (" application/json",), (None,)),
    ids=("empty", "bare_string", "cased", "padded", "not_text"),
)
def test_a_content_type_set_outside_its_shape_is_refused(
    accept_content_types: object,
) -> None:
    with pytest.raises(ValueError):
        _session(
            lambda _: _json({}),
            accept_content_types=accept_content_types,  # type: ignore[arg-type]  # reason: the refusal under test is exactly a caller supplying the wrong shape.
        )


def test_non_identity_encoding_is_malformed_however_it_is_delivered() -> None:
    preloaded = httpx.Response(
        200, headers={"content-type": "application/json"}, content=b"{}"
    )
    preloaded.headers["content-encoding"] = "gzip"
    consumed = _session(
        lambda _: preloaded,
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("malformed must not retry")),
    )
    with pytest.raises(ProviderFailure) as consumed_failure:
        _request(consumed)
    assert ProviderStatus.MALFORMED == consumed_failure.value.status
    assert 65_536 * 9 == consumed.remaining_response_bytes

    streamed = _session(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=_RawStream(b'{"a": 1}'),
        ),
        clocks=_quiet_clocks(sleep=lambda _: pytest.fail("malformed must not retry")),
    )
    with pytest.raises(ProviderFailure) as streamed_failure:
        _request(streamed)
    assert ProviderStatus.MALFORMED == streamed_failure.value.status
    assert 65_536 * 9 - 8 == streamed.remaining_response_bytes


def test_decode_json_keeps_exact_numbers_and_rejects_ambiguity() -> None:
    assert {"a": Decimal("1.10")} == decode_json(b'{"a": 1.10}')
    with pytest.raises(ValueError, match="duplicate"):
        decode_json(b'{"a": 1, "a": 2}')
    with pytest.raises(ValueError, match="number"):
        decode_json(b'{"a": Infinity}')
