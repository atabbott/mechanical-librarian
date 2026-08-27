"""The scale harness must outwait the login store, not race it.

`tests/support/continuous_review_scale.py` drives the installed server over
HTTP while the same qualification writes concurrently. Its clients used
httpx's default read timeout, which is exactly the lock wait the login store
allows, so a contended login was recorded as an `httpx.ReadTimeout` instead of
whichever answer the server was about to return (#909).

These are source-level assertions on purpose. The defect they bound is not a
behaviour the harness can be asked about at runtime -- it is a client
constructed without a timeout, anywhere in the module -- and the margin only
holds while it is read from the server's real budget rather than restated as a
second magic number.
"""

import re
from pathlib import Path

import httpx

from tests.support.continuous_review_scale import (
    _CLIENT_TIMEOUT_SECONDS,
    _HEALTH_POLL_TIMEOUT_SECONDS,
    _review_client,
)

_ROOT = Path(__file__).resolve().parents[2]
_HARNESS = _ROOT / "tests" / "support" / "continuous_review_scale.py"
_LOGIN_STORE = (
    _ROOT
    / "src"
    / "mechanical_librarian"
    / "adapters"
    / "persistence"
    / "metadata_reports.py"
)


def _login_store_lock_wait_seconds() -> float:
    """The lock wait the store backing `login` actually allows."""

    pragmas = re.findall(
        r"PRAGMA busy_timeout=(\d+)", _LOGIN_STORE.read_text(encoding="utf-8")
    )
    assert len(pragmas) == 1, (
        "expected exactly one busy_timeout in the login store; the harness "
        f"margin is derived from it, found {pragmas}"
    )
    return int(pragmas[0]) / 1000.0


def test_harness_constructs_no_client_outside_the_factory() -> None:
    """A bare client silently inherits the default this module exists to avoid.

    Asserting the count rather than the absence of a known-bad string is what
    makes the rule survive a new call site: the tenth client cannot be added
    without either routing through the factory or failing here.
    """

    source = _HARNESS.read_text(encoding="utf-8")
    assert source.count("httpx.Client(") == 1, (
        "construct harness clients through `_review_client` so every request "
        "carries an explicit timeout"
    )
    factory = source.index("def _review_client(")
    assert factory < source.index("httpx.Client("), (
        "the single construction must be the factory's own"
    )


def test_client_margin_exceeds_the_login_store_lock_wait() -> None:
    """The client must still be waiting when the server stops waiting.

    Equal budgets are the defect: the client aborts at the moment the server
    would have answered, so the qualification observes its own impatience
    rather than a `200` or an honest `503 persistence`.
    """

    lock_wait = _login_store_lock_wait_seconds()
    assert _CLIENT_TIMEOUT_SECONDS > lock_wait, (
        f"client timeout {_CLIENT_TIMEOUT_SECONDS}s does not outwait the "
        f"login store's {lock_wait}s lock wait"
    )
    # A margin that merely clears the lock wait leaves nothing for the verify
    # and fsync that follow it, so the request still loses on a slow host.
    assert _CLIENT_TIMEOUT_SECONDS >= lock_wait * 2


def test_factory_applies_the_declared_timeouts() -> None:
    """The constants are only a contract if the client is actually built with them."""

    with _review_client() as client:
        assert client.timeout == httpx.Timeout(_CLIENT_TIMEOUT_SECONDS)
    with _review_client(_HEALTH_POLL_TIMEOUT_SECONDS) as client:
        assert client.timeout == httpx.Timeout(_HEALTH_POLL_TIMEOUT_SECONDS)


def test_health_poll_does_not_spend_the_start_budget_on_one_request() -> None:
    """The start-up poll retries, so it must not adopt the long request margin.

    It treats a timeout as one more not-yet-serving attempt against its own
    deadline; waiting the request margin per attempt would spend that budget
    instead of retrying inside it.
    """

    from tests.support.continuous_review_scale import _REVIEW_SERVER_START_SECONDS

    assert _HEALTH_POLL_TIMEOUT_SECONDS < _CLIENT_TIMEOUT_SECONDS
    assert _HEALTH_POLL_TIMEOUT_SECONDS < _REVIEW_SERVER_START_SECONDS
