from __future__ import annotations

import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpcore
import httpx
import pytest

from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderFailure,
    ProviderRequestPacer,
    _DiscoveryRequestLedger,
    bounded_attempt_elapsed,
    bounded_retry_after,
    transient_transport_failure,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus


def test_shared_pacer_spaces_starts_and_refuses_a_deadline_boundary() -> None:
    now = [10.0]
    sleeps: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    pacer = ProviderRequestPacer(
        minimum_interval_seconds=1.0, clock=lambda: now[0], sleep=sleep
    )
    assert pacer.wait()
    assert pacer.wait()
    assert [1.0] == sleeps
    assert not pacer.wait(deadline=12.0)
    assert [1.0] == sleeps


@pytest.mark.parametrize("interval", (0, -1, True, float("inf")))
def test_shared_pacer_rejects_noncanonical_intervals(interval: object) -> None:
    with pytest.raises(ValueError, match="pacing"):
        ProviderRequestPacer(interval)  # type: ignore[arg-type]


def _caused(error: httpx.HTTPError, cause: BaseException) -> httpx.HTTPError:
    error.__cause__ = cause
    return error


def test_discovery_ledger_coalesces_only_identical_redacted_semantics() -> None:
    ledger = _DiscoveryRequestLedger("provider", "revision")
    calls: list[str] = []

    def execute() -> object:
        calls.append("sent")
        return object()

    first, replay = ledger.resolve(
        kind="work",
        matched_family=None,
        title="private title",
        authors=("Second", "First"),
        projection="fixed",
        execute=execute,
    )
    second, replayed = ledger.resolve(
        kind="work",
        matched_family=None,
        title="private title",
        authors=("First", "Second"),
        projection="fixed",
        execute=execute,
    )
    third, distinct = ledger.resolve(
        kind="isbn",
        matched_family="9780000000000",
        title="private title",
        authors=("First", "Second"),
        projection="fixed",
        execute=execute,
    )

    assert not replay
    assert replayed
    assert not distinct
    assert first is second
    assert first is not third
    assert ["sent", "sent"] == calls


def test_discovery_ledger_reserves_registered_initials_before_retry() -> None:
    ledger = _DiscoveryRequestLedger("provider", "revision")
    ledger.register(
        kind="isbn",
        matched_family="9780000000000",
        title=None,
        authors=(),
        projection="fixed",
    )
    ledger.register_detail("/books/OL1M")
    assert not ledger.allows_retry(47, 49)
    ledger.begin_detail("/books/OL1M")
    assert ledger.allows_retry(47, 49)


def test_detail_ledger_closes_a_predispatch_exception_without_caching_it() -> None:
    ledger = _DiscoveryRequestLedger("provider", "revision")
    failure = RuntimeError("synthetic pre-dispatch failure")

    with pytest.raises(RuntimeError) as raised:
        ledger.resolve_detail("record", execute=lambda: (_ for _ in ()).throw(failure))

    assert raised.value is failure
    assert ledger.accounting().logical_requests == 1
    assert ledger.accounting().initial_dispatches == 0
    assert ledger.accounting().replays == 0
    assert ledger.accounting().undispatched_incomplete == 1

    def succeed() -> str:
        ledger.dispatch(retry=False)
        return "fresh result"

    result, replay = ledger.resolve_detail("record", execute=succeed)

    assert result == "fresh result"
    assert replay is False
    assert ledger.accounting().logical_requests == 2
    assert ledger.accounting().initial_dispatches == 1
    assert ledger.accounting().replays == 0
    assert ledger.accounting().undispatched_incomplete == 1


def test_attempt_elapsed_is_finite_and_bounded() -> None:
    assert 0.0 == bounded_attempt_elapsed(2.0, 1.0)
    assert 0.0 == bounded_attempt_elapsed(0.0, float("inf"))
    assert 60.0 == bounded_attempt_elapsed(0.0, 600.0)
    assert 1.25 == bounded_attempt_elapsed(2.0, 3.25)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (httpx.ConnectTimeout("redacted"), "transport_connect_timeout"),
        (httpx.ReadTimeout("redacted"), "transport_read_timeout"),
        (
            _caused(
                httpx.ConnectError("redacted"),
                ConnectionResetError(),
            ),
            "transport_connect_error",
        ),
        (
            _caused(httpx.ReadError("redacted"), TimeoutError()),
            "transport_read_error",
        ),
        (
            _caused(httpx.WriteError("redacted"), BrokenPipeError()),
            "transport_write_error",
        ),
        (
            _caused(
                httpx.RemoteProtocolError("redacted"),
                httpcore.RemoteProtocolError("redacted"),
            ),
            "transport_remote_protocol_error",
        ),
        (
            _caused(
                httpx.ConnectError("redacted"),
                socket.gaierror(socket.EAI_AGAIN, "redacted"),
            ),
            "transport_connect_error",
        ),
    ),
    ids=(
        "connect_timeout",
        "read_timeout",
        "connect_error_reset",
        "read_error_timeout",
        "write_error_broken_pipe",
        "remote_protocol_error",
        "connect_error_dns_again",
    ),
)
def test_accepts_only_narrow_typed_transient_transport_failures(
    error: httpx.HTTPError, expected: str
) -> None:
    assert expected == transient_transport_failure(error)


@pytest.mark.parametrize(
    "error",
    (
        httpx.PoolTimeout("redacted"),
        httpx.WriteTimeout("redacted"),
        httpx.ProxyError("redacted"),
        httpx.LocalProtocolError("redacted"),
        httpx.DecodingError("redacted"),
        httpx.InvalidURL("redacted"),
        httpx.ConnectError("connection reset by peer"),
        _caused(httpx.ConnectError("redacted"), ssl.SSLError()),
        _caused(httpx.ConnectTimeout("redacted"), ssl.SSLError()),
        _caused(
            httpx.ConnectError("redacted"),
            socket.gaierror(socket.EAI_NONAME, "redacted"),
        ),
    ),
    ids=(
        "pool_timeout",
        "write_timeout",
        "proxy_error",
        "local_protocol_error",
        "decoding_error",
        "invalid_url",
        "connect_error_uncaused",
        "connect_error_tls",
        "connect_timeout_tls",
        "connect_error_dns_noname",
    ),
)
def test_rejects_unsafe_transport_types_causes_and_unknowns(
    error: httpx.HTTPError,
) -> None:
    assert transient_transport_failure(error) is None


# One TLS failure buried under a transport error the ledger would otherwise
# retry, and the same failure buried under a protocol error.
_TLS_UNDER_RESET = ConnectionResetError()
_TLS_UNDER_RESET.__cause__ = ssl.SSLError()
_TLS_UNDER_PROTOCOL = httpcore.RemoteProtocolError("redacted")
_TLS_UNDER_PROTOCOL.__cause__ = ssl.SSLError()


@pytest.mark.parametrize(
    "error",
    (
        _caused(httpx.ConnectError("redacted"), _TLS_UNDER_RESET),
        _caused(httpx.ConnectTimeout("redacted"), _TLS_UNDER_RESET),
        _caused(httpx.ReadTimeout("redacted"), _TLS_UNDER_RESET),
        _caused(
            httpx.RemoteProtocolError("redacted"),
            _TLS_UNDER_PROTOCOL,
        ),
    ),
    ids=(
        "connect_error",
        "connect_timeout",
        "read_timeout",
        "remote_protocol_error",
    ),
)
def test_tls_anywhere_in_bounded_cause_chain_vetoes_retry(
    error: httpx.HTTPError,
) -> None:
    assert transient_transport_failure(error) is None


@pytest.mark.parametrize(
    "value",
    (
        "61",
        "-1",
        "3, 4",
        "Mon, 20 Jul 2026 13:20:30 GMT, 4",
        "Mon, 20 Jul 2026 14:20:30 GMT",
        "Monday, 20-Jul-26 13:20:30 GMT",
        "redacted",
    ),
    ids=(
        "over_bound",
        "negative",
        "list_of_seconds",
        "list_of_dates",
        "date_over_bound",
        "obsolete_date_format",
        "unparseable",
    ),
)
def test_retry_after_accepts_bounded_canonical_values_only(value: str) -> None:
    now = 1_784_553_600.0
    assert 0.0 == bounded_retry_after("0", now=now)
    assert 60.0 == bounded_retry_after("60", now=now)
    assert 30.0 == bounded_retry_after("Mon, 20 Jul 2026 13:20:30 GMT", now=now)
    assert bounded_retry_after(value, now=now) is None


def test_burst_hold_uses_max_deadline_and_never_renews_on_refusal() -> None:

    now = [0.0]
    pacer = ProviderRequestPacer(
        clock=lambda: now[0], sleep=lambda _: pytest.fail("held")
    )
    assert pacer.remaining_burst() is None
    assert pacer.wait()
    pacer.hold_burst(100)
    now[0] = 18
    pacer.hold_burst(10)
    assert pacer.remaining_burst() == 82
    for _ in range(3):
        with pytest.raises(ProviderFailure) as raised:
            pacer.wait()
        assert raised.value.status is ProviderStatus.RATE_LIMITED
        assert raised.value.category == "shared_burst_throttled"
        assert raised.value.retry_after == 82
    now[0] = 99.1
    assert pacer.remaining_burst() == 1
    now[0] = 100
    assert pacer.remaining_burst() == 0
    assert pacer.wait()


def test_burst_update_serializes_with_admission_but_not_http() -> None:

    admitted = Event()
    release = Event()
    pacer = ProviderRequestPacer(clock=lambda: 0.0)

    def request() -> None:
        assert pacer.wait()
        admitted.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(request)
        assert admitted.wait(5)
        try:
            pacer.hold_burst(100)
            with pytest.raises(ProviderFailure):
                pacer.wait()
        finally:
            release.set()
        future.result(timeout=5)
