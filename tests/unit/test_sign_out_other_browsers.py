"""Signing out every other browser: the service over the real session store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from typing import cast

import pytest
from argon2 import PasswordHasher

from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.live_updates import LiveUpdateChannel
from mechanical_librarian.application.review_web import (
    OwnerSessionService,
    ReviewSessionStore,
    ReviewWebError,
    SessionTokens,
)
from mechanical_librarian.interfaces.web import StoreOwnerPassword, live_stream
from mechanical_librarian.interfaces.web.app import owner_password_binding

_CURRENT = "the current owner password"
_NEW = "the replacement owner password"
_CLIENT = "client:198.51.100.7"
_OTHER_CLIENT = "client:203.0.113.9"
_THIRD_CLIENT = "client:192.0.2.44"


class _Store:
    """A stored verifier whose compare-and-set can be held open by the test."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.entered: threading.Event | None = None
        self.release: threading.Event | None = None

    def replace_owner_verifier(self, expected: str, replacement: str) -> bool:
        if self.entered is not None and self.release is not None:
            self.entered.set()
            assert self.release.wait(5)
        if expected != self.value:
            return False
        self.value = replacement
        return True


class _Sessions:
    """The real session store, whose next session insert can be held open."""

    def __init__(self) -> None:
        self.inner = SQLiteReviewSessionRepository.open_ephemeral()
        self.entered: threading.Event | None = None
        self.release: threading.Event | None = None

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
        client_digest: str,
    ) -> None:
        self.inner.create_review_session(
            session_digest, csrf_digest, created_at, expires_at, client_digest
        )
        entered, release = self.entered, self.release
        if entered is not None and release is not None:
            self.entered = self.release = None
            entered.set()
            assert release.wait(5)


def _store_service() -> tuple[OwnerSessionService, _Store, _Sessions]:
    store = _Store(PasswordHasher().hash(_CURRENT))
    holder = StoreOwnerPassword(store)
    holder.seed(store.value)
    sessions = _Sessions()
    service = OwnerSessionService(
        cast("ReviewSessionStore", sessions),
        holder.verify,
        holder.session_digest,
        lambda: 1_000,
        600,
        client_digest=holder.client_digest,
        password_change=holder,
    )
    return service, store, sessions


def _file_service() -> tuple[OwnerSessionService, _Sessions]:
    """Compose as file mode does: the hash-file binding and no change port."""
    verify, digest, client = owner_password_binding(PasswordHasher().hash(_CURRENT))
    sessions = _Sessions()
    service = OwnerSessionService(
        cast("ReviewSessionStore", sessions),
        verify,
        digest,
        lambda: 1_000,
        600,
        client_digest=client,
    )
    return service, sessions


def _service(mode: str) -> tuple[OwnerSessionService, _Sessions]:
    if mode == "file":
        return _file_service()
    service, _store, sessions = _store_service()
    return service, sessions


def _three_browsers(
    service: OwnerSessionService,
) -> tuple[SessionTokens, SessionTokens, SessionTokens]:
    return (
        service.login(_CURRENT, _CLIENT),
        service.login(_CURRENT, _OTHER_CLIENT),
        service.login(_CURRENT, _THIRD_CLIENT),
    )


def _session_count(sessions: _Sessions) -> int:
    row = sessions.inner._connection.execute(
        "SELECT count(*) FROM review_sessions"
    ).fetchone()
    return int(row[0])


@pytest.mark.parametrize("mode", ["file", "store"])
def test_signing_out_other_browsers_keeps_this_session_and_its_csrf_token(
    mode: str,
) -> None:
    service, sessions = _service(mode)
    own, second, third = _three_browsers(service)

    assert service.logout_others(own.session_token, own.csrf_token) == 2

    assert service.authenticate(own.session_token, own.csrf_token) == "owner"
    for other in (second, third):
        with pytest.raises(ReviewWebError, match="unauthorized"):
            service.authenticate(other.session_token)
    assert _session_count(sessions) == 1


def test_file_mode_signs_out_other_browsers_without_a_password_holder() -> None:
    service, sessions = _file_service()
    assert service._password_change is None
    own, second, _third = _three_browsers(service)
    service.logout(second.session_token, second.csrf_token)

    # The revoked row is removed too, and this browser's token still works.
    assert service.logout_others(own.session_token, own.csrf_token) == 2
    assert _session_count(sessions) == 1
    service.logout(own.session_token, own.csrf_token)
    with pytest.raises(ReviewWebError, match="unauthorized"):
        service.authenticate(own.session_token)


@pytest.mark.parametrize(
    "outcome", ["signed_out", "csrf", "unauthorized", "persistence"]
)
def test_each_outcome_writes_one_value_free_event(
    outcome: str, capsys: pytest.CaptureFixture[str]
) -> None:
    service, sessions = _file_service()
    own, second, third = _three_browsers(service)
    session_token, csrf_token = own.session_token, own.csrf_token
    if outcome == "csrf":
        csrf_token = second.csrf_token
    elif outcome == "unauthorized":
        service.logout(own.session_token, own.csrf_token)
    elif outcome == "persistence":
        sessions.inner.close()
    capsys.readouterr()

    if outcome == "signed_out":
        assert service.logout_others(session_token, csrf_token) == 2
    else:
        with pytest.raises(ReviewWebError) as refused:
            service.logout_others(session_token, csrf_token)
        assert refused.value.code == outcome

    line = capsys.readouterr().err
    assert line.endswith("\n") and line.count("\n") == 1
    assert json.loads(line) == {
        "event": "owner_sign_out_others",
        "level": "warning" if outcome == "persistence" else "info",
        "outcome": outcome,
        "sessions_ended": 2 if outcome == "signed_out" else None,
    }
    for tokens in (own, second, third):
        for secret in (tokens.session_token, tokens.csrf_token):
            assert secret not in line
            assert hashlib.sha256(secret.encode()).hexdigest() not in line
    for secret in (_CURRENT, _CLIENT, _OTHER_CLIENT, _THIRD_CLIENT, "client:"):
        assert secret not in line


def test_a_signed_out_browser_stream_ends_at_its_next_session_check() -> None:
    service, _sessions = _file_service()
    own, other, _third = _three_browsers(service)

    def authenticates() -> bool:
        try:
            return service.authenticate(other.session_token) == "owner"
        except ReviewWebError:
            return False

    async def exercise() -> tuple[list[bytes], float]:
        channel = LiveUpdateChannel()
        frames = live_stream.stream_frames(
            channel.subscribe(0),
            channel.epoch,
            still_authenticated=authenticates,
            heartbeat_seconds=0.01,
            maximum_seconds=5,
        )
        received = [await anext(frames)]
        started = time.monotonic()
        service.logout_others(own.session_token, own.csrf_token)
        received.extend([value async for value in frames])
        return received, time.monotonic() - started

    received, elapsed = asyncio.run(exercise())

    assert received[0] == f"retry: {live_stream.RECONNECT_MILLISECONDS}\n\n".encode()
    assert set(received[1:]) <= {b": heartbeat\n\n"}
    assert elapsed < 1.0


@pytest.mark.parametrize("held_at", ["swap", "session_insert"])
def test_signing_out_other_browsers_waits_for_a_password_change_in_flight(
    held_at: str,
) -> None:
    service, store, sessions = _store_service()
    changer = service.login(_CURRENT, _CLIENT)
    other = service.login(_CURRENT, _OTHER_CLIENT)
    entered, release = threading.Event(), threading.Event()
    # Held inside the verifier swap, or just after the change stored its new
    # session and before the swap begins.
    holding: _Store | _Sessions = store if held_at == "swap" else sessions
    holding.entered, holding.release = entered, release
    changed: list[object] = []
    signed_out: list[object] = []

    def change() -> None:
        changed.append(
            service.change_password(
                changer.session_token,
                changer.csrf_token,
                _CURRENT,
                _NEW,
                _NEW,
                _CLIENT,
            )
        )

    def sign_out_others() -> None:
        try:
            signed_out.append(
                service.logout_others(other.session_token, other.csrf_token)
            )
        except Exception as error:
            signed_out.append(error)

    changer_thread = threading.Thread(target=change)
    other_thread = threading.Thread(target=sign_out_others)
    changer_thread.start()
    try:
        # The change has stored its new session and has not finished.
        assert entered.wait(5)
        other_thread.start()
        other_thread.join(0.2)
        assert other_thread.is_alive(), signed_out
    finally:
        release.set()
        changer_thread.join(5)
        if other_thread.ident is not None:
            other_thread.join(5)

    assert not changer_thread.is_alive() and not other_thread.is_alive()
    (tokens,) = changed
    assert service.authenticate(tokens.session_token) == "owner"
    (refused,) = signed_out
    assert isinstance(refused, ReviewWebError) and refused.code == "unauthorized"
