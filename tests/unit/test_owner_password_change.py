"""The password-change service order and the live verifier it replaces."""

from __future__ import annotations

import json
import threading
from typing import cast

import pytest
from argon2 import PasswordHasher

from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.review_web import (
    OwnerSessionService,
    ReviewSessionStore,
    ReviewWebError,
)
from mechanical_librarian.interfaces.web import StoreOwnerPassword
from mechanical_librarian.interfaces.web import app as web_app

_CURRENT = "the current owner password"
_NEW = "the replacement owner password"
_CLIENT = "client:198.51.100.7"


class _Store:
    """A stored verifier that records each compare-and-set it answers."""

    def __init__(self, value: str, steps: list[str]) -> None:
        self.value = value
        self.steps = steps
        self.entered: threading.Event | None = None
        self.release: threading.Event | None = None

    def replace_owner_verifier(self, expected: str, replacement: str) -> bool:
        if self.entered is not None and self.release is not None:
            self.entered.set()
            assert self.release.wait(5)
        self.steps.append("replace")
        if expected != self.value:
            return False
        self.value = replacement
        return True


class _Sessions:
    """The real session store, recording when the purge runs."""

    def __init__(self, steps: list[str]) -> None:
        self.inner = SQLiteReviewSessionRepository.open_ephemeral()
        self.steps = steps
        self.purge_failure = False

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def end_review_sessions_except(self, session_digest: str) -> int:
        self.steps.append("purge")
        if self.purge_failure:
            raise RuntimeError("synthetic purge failure")
        return self.inner.end_review_sessions_except(session_digest)


def _service() -> tuple[OwnerSessionService, StoreOwnerPassword, _Store, _Sessions]:
    steps: list[str] = []
    store = _Store(PasswordHasher().hash(_CURRENT), steps)
    holder = StoreOwnerPassword(store)
    holder.seed(store.value)
    sessions = _Sessions(steps)
    service = OwnerSessionService(
        cast("ReviewSessionStore", sessions),
        holder.verify,
        holder.session_digest,
        lambda: 1_000,
        600,
        client_digest=holder.client_digest,
        password_change=holder,
    )
    return service, holder, store, sessions


def _rows(sessions: _Sessions) -> tuple[int, int]:
    connection = sessions.inner._connection
    return (
        connection.execute("SELECT count(*) FROM review_sessions").fetchone()[0],
        connection.execute("SELECT count(*) FROM review_login_failures").fetchone()[0],
    )


def test_the_purge_runs_only_after_the_replacement_commits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, holder, store, sessions = _service()
    changer = service.login(_CURRENT, _CLIENT)
    other = service.login(_CURRENT, "client:203.0.113.9")
    with pytest.raises(ReviewWebError):
        service.login("wrong", "client:192.0.2.1")
    capsys.readouterr()

    tokens = service.change_password(
        changer.session_token, changer.csrf_token, _CURRENT, _NEW, _NEW, _CLIENT
    )

    assert store.steps == ["replace", "purge"]
    assert holder.current().encoded == store.value
    assert _rows(sessions) == (1, 0)
    assert service.authenticate(tokens.session_token) == "owner"
    for ended in (changer.session_token, other.session_token):
        with pytest.raises(ReviewWebError, match="unauthorized"):
            service.authenticate(ended)
    assert json.loads(capsys.readouterr().err) == {
        "event": "owner_password_change",
        "level": "info",
        "outcome": "changed",
        "sessions_ended": 2,
    }


def test_a_failed_purge_is_still_a_change(capsys: pytest.CaptureFixture[str]) -> None:
    service, holder, store, sessions = _service()
    changer = service.login(_CURRENT, _CLIENT)
    sessions.purge_failure = True
    capsys.readouterr()

    tokens = service.change_password(
        changer.session_token, changer.csrf_token, _CURRENT, _NEW, _NEW, _CLIENT
    )

    assert holder.verify(_NEW) and not holder.verify(_CURRENT)
    assert service.authenticate(tokens.session_token) == "owner"
    with pytest.raises(ReviewWebError, match="unauthorized"):
        service.authenticate(changer.session_token)
    event = capsys.readouterr().err
    assert json.loads(event) == {
        "event": "owner_password_change",
        "level": "warning",
        "outcome": "changed",
        "sessions_ended": None,
    }
    for secret in (_CURRENT, _NEW, store.value, tokens.session_token, _CLIENT):
        assert secret not in event


def test_a_refused_change_writes_one_value_free_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _holder, store, _sessions = _service()
    changer = service.login(_CURRENT, _CLIENT)
    capsys.readouterr()

    with pytest.raises(ReviewWebError, match="invalid_password"):
        service.change_password(
            changer.session_token,
            changer.csrf_token,
            "wrong",
            "short",
            "short",
            _CLIENT,
        )
    assert capsys.readouterr().err == ""
    with pytest.raises(ReviewWebError, match="current_password_invalid"):
        service.change_password(
            changer.session_token, changer.csrf_token, "wrong", _NEW, _NEW, _CLIENT
        )
    event = capsys.readouterr().err
    assert json.loads(event)["outcome"] == "current_password_invalid"
    assert "wrong" not in event and store.value not in event


def test_adopt_refuses_a_snapshot_that_is_no_longer_current() -> None:
    _service_unused, holder, store, _sessions = _service()
    snapshot = holder.current()
    first = holder.successor(_NEW)
    assert holder.adopt(snapshot, first) is True

    assert holder.adopt(snapshot, holder.successor("a third owner password")) is False
    assert store.steps == ["replace"]
    assert holder.current() is first


def test_store_owner_password_binds_only_its_first_seed() -> None:
    store = _Store(PasswordHasher().hash(_CURRENT), [])
    holder = StoreOwnerPassword(store)
    with pytest.raises(ReviewWebError, match="password_hash"):
        holder.current()

    holder.seed(store.value)
    holder.seed(PasswordHasher().hash(_NEW))

    assert holder.verify(_CURRENT) and not holder.verify(_NEW)


def test_store_owner_password_swaps_only_after_the_store_commits() -> None:
    store = _Store(PasswordHasher().hash(_CURRENT), [])
    holder = StoreOwnerPassword(store)
    holder.seed(store.value)
    expected = holder.current()
    successor = holder.successor(_NEW)
    store.value = PasswordHasher().hash("written elsewhere")

    assert holder.adopt(expected, successor) is False
    assert holder.current() is expected

    class _Failing(_Store):
        def replace_owner_verifier(self, expected: str, replacement: str) -> bool:
            raise RuntimeError("synthetic write failure")

    failing = StoreOwnerPassword(_Failing(expected.encoded, []))
    failing.seed(expected.encoded)
    current = failing.current()
    with pytest.raises(RuntimeError):
        failing.adopt(current, successor)
    assert failing.current() is current


def test_concurrent_adopts_let_exactly_one_win() -> None:
    store = _Store(PasswordHasher().hash(_CURRENT), [])
    holder = StoreOwnerPassword(store)
    holder.seed(store.value)
    expected = holder.current()
    successors = [holder.successor(_NEW), holder.successor("another owner password")]
    store.entered, store.release = threading.Event(), threading.Event()
    results: list[bool] = []

    def adopt(index: int) -> None:
        results.append(holder.adopt(expected, successors[index]))

    first = threading.Thread(target=adopt, args=(0,))
    first.start()
    assert store.entered.wait(5)
    second = threading.Thread(target=adopt, args=(1,))
    second.start()
    store.release.set()
    first.join(5)
    second.join(5)

    assert sorted(results) == [False, True]
    assert holder.current() is successors[0]
    assert store.value == successors[0].encoded


def test_a_sign_in_that_straddles_a_change_gets_no_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, holder, _store, _sessions = _service()
    verify = web_app._StoreOwnerCredential.verify
    swapped: list[bool] = []

    def verify_then_change(credential, password: str) -> bool:
        verified = verify(credential, password)
        if not swapped:
            # The change commits while this sign-in is still verifying.
            swapped.append(holder.adopt(holder.current(), holder.successor(_NEW)))
        return verified

    monkeypatch.setattr(web_app._StoreOwnerCredential, "verify", verify_then_change)

    tokens = service.login(_CURRENT, _CLIENT)

    assert swapped == [True]
    with pytest.raises(ReviewWebError, match="unauthorized"):
        service.authenticate(tokens.session_token)
