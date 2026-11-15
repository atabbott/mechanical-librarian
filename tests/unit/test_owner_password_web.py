"""Changing the owner password over HTTP: one new session, every other one ended."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from tests.support.review_web_doubles import (
    FakeReviewRepository,
    MemoryOwnerVerifierStore,
    ReviewWebContext,
    review_web_context,
)
from tests.unit.review_app_fixture import TEST_REACT_BUILD_IDENTITY

_PATH = "/api/v1/session/password"
_CURRENT = "correct horse"
_NEW = "a new owner passphrase"


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository, store_mode=True)


def _store(web: ReviewWebContext[FakeReviewRepository]) -> MemoryOwnerVerifierStore:
    assert web.verifier_store is not None
    return web.verifier_store


def _change(
    web: ReviewWebContext[FakeReviewRepository],
    csrf: object,
    current: str = _CURRENT,
    new: str = _NEW,
    confirmation: str | None = None,
    client: TestClient | None = None,
):
    return (client or web.client).post(
        _PATH,
        headers={**web.origin, "x-csrf-token": str(csrf)},
        json={
            "current_password": current,
            "new_password": new,
            "confirmation": new if confirmation is None else confirmation,
        },
    )


def _other_browser(web: ReviewWebContext[FakeReviewRepository]) -> TestClient:
    """A second browser, from another address, signed in before the change."""
    other = TestClient(
        web.client.app,
        base_url=str(web.client.base_url),
        client=("127.0.0.2", 23456),
    )
    signed_in = other.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _CURRENT}
    )
    assert signed_in.status_code == 200
    other.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    web.cleanup.callback(other.close)
    return other


def _sign_in(web: ReviewWebContext[FakeReviewRepository], password: str) -> int:
    probe = TestClient(
        web.client.app,
        base_url=str(web.client.base_url),
        client=("127.0.0.3", 34567),
    )
    with probe:
        return probe.post(
            "/api/v1/session/login", headers=web.origin, json={"password": password}
        ).status_code


def _code(response) -> str:
    return response.json()["error"]["code"]


def _session_count(web: ReviewWebContext[FakeReviewRepository]) -> int:
    return sum(
        revoked is None and expires > web.now
        for _actor, _csrf, expires, revoked in web.repository.sessions.values()
    )


def test_change_keeps_this_browser_on_a_new_session_and_refuses_its_old_cookie(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    old_cookie = web.client.cookies["ml_session"]

    changed = _change(web, login["csrf_token"])

    assert changed.status_code == 204, changed.text
    assert changed.content == b""
    assert changed.headers["cache-control"] == "no-store, private"
    cookie = changed.headers["set-cookie"]
    assert re.match(r"ml_session=[A-Za-z0-9_-]{32,128};", cookie)
    attributes = {part.strip().lower() for part in cookie.split(";")[1:]}
    assert attributes == {
        "httponly",
        "max-age=600",
        "path=/",
        "samesite=strict",
        "secure",
    }
    new_cookie = web.client.cookies["ml_session"]
    assert new_cookie != old_cookie
    assert web.client.get("/api/v1/library").status_code == 200

    stale = TestClient(web.client.app, base_url=str(web.client.base_url))
    stale.cookies.set("ml_session", old_cookie)
    stale.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    with stale:
        refused = stale.get("/api/v1/library")
    assert refused.status_code == 401
    assert _code(refused) == "unauthorized"

    # The new session's CSRF token was never sent, so the page renews it.
    page = web.client.get("/settings")
    token = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
    assert token is not None
    signed_out = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "x-csrf-token": token.group(1)},
    )
    assert signed_out.status_code == 204


def test_change_ends_every_other_session(
    web: ReviewWebContext[FakeReviewRepository],
    capsys: pytest.CaptureFixture[str],
) -> None:
    login = web.login()
    other = _other_browser(web)
    assert other.get("/api/v1/library").status_code == 200
    capsys.readouterr()

    assert _change(web, login["csrf_token"]).status_code == 204

    refused = other.get("/api/v1/library")
    assert refused.status_code == 401
    assert _code(refused) == "unauthorized"
    assert _session_count(web) == 1
    assert web.repository.failures == []
    assert _sign_in(web, _NEW) == 200
    assert _sign_in(web, _CURRENT) == 401
    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events == [
        {
            "event": "owner_password_change",
            "level": "info",
            "outcome": "changed",
            "sessions_ended": 2,
        }
    ]


def test_the_same_password_ends_other_sessions(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    other = _other_browser(web)
    before = _store(web).value

    changed = _change(web, login["csrf_token"], new=_CURRENT)

    assert changed.status_code == 204
    assert _store(web).value != before
    assert PasswordHasher().verify(_store(web).value, _CURRENT)
    assert other.get("/api/v1/library").status_code == 401
    assert web.client.get("/api/v1/library").status_code == 200


def test_a_wrong_current_password_changes_nothing_and_spends_a_sign_in_attempt(
    web: ReviewWebContext[FakeReviewRepository],
    capsys: pytest.CaptureFixture[str],
) -> None:
    login = web.login()
    other = _other_browser(web)
    before = _store(web).value
    capsys.readouterr()

    refused = _change(web, login["csrf_token"], current="not the password")

    assert refused.status_code == 403
    assert _code(refused) == "current_password_invalid"
    assert refused.headers["cache-control"] == "no-store, private"
    assert "set-cookie" not in refused.headers
    assert _store(web).value == before
    assert web.client.get("/api/v1/library").status_code == 200
    assert other.get("/api/v1/library").status_code == 200
    assert len(web.repository.failures) == 1
    assert json.loads(capsys.readouterr().err) == {
        "event": "owner_password_change",
        "level": "info",
        "outcome": "current_password_invalid",
        "sessions_ended": None,
    }

    for _ in range(4):
        assert _change(web, login["csrf_token"], current="wrong").status_code == 403
    throttled = _change(web, login["csrf_token"])
    assert throttled.status_code == 429
    assert _code(throttled) == "throttled"
    signed_in = web.client.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _CURRENT}
    )
    assert signed_in.status_code == 429
    assert _store(web).value == before


def test_refusal_precedence(web: ReviewWebContext[FakeReviewRepository]) -> None:
    login = web.login()
    csrf = login["csrf_token"]
    for new, confirmation in (
        ("short", "short"),
        ("x" * 1025, "x" * 1025),
        ("é" * 513, "é" * 513),
        (_NEW, _NEW + "!"),
        ("", ""),
    ):
        refused = _change(web, csrf, "wrong", new, confirmation)
        assert refused.status_code == 422, (new[:8], refused.text)
        assert _code(refused) in {"invalid_password", "invalid"}
        if len(new) <= 1024:
            assert _code(refused) == "invalid_password"
    assert web.repository.failures == []

    wrong_csrf = _change(web, "x" * 43, "wrong", "short", "short")
    assert wrong_csrf.status_code == 403
    assert _code(wrong_csrf) == "csrf"

    web.repository.failures = [(web.now, "f" * 64)] * 100
    throttled = _change(web, csrf)
    assert throttled.status_code == 429
    assert _code(throttled) == "throttled"


def test_file_mode_refuses_and_changes_nothing() -> None:
    for web in review_web_context(FakeReviewRepository):
        login = web.login()
        before = web.hash_path.read_bytes()

        refused = _change(web, login["csrf_token"])

        assert refused.status_code == 409
        assert refused.json()["error"] == {
            "code": "settings_managed_by_file",
            "managed_by_file": "/data/config.json",
        }
        assert web.hash_path.read_bytes() == before
        assert web.client.get("/api/v1/library").status_code == 200
        assert web.repository.failures == []


def test_a_failed_verifier_write_leaves_every_session_working(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    other = _other_browser(web)
    before = _store(web).value
    _store(web).replace_failure = True

    failed = _change(web, login["csrf_token"])

    assert failed.status_code == 503
    assert _code(failed) == "persistence"
    assert "set-cookie" not in failed.headers
    assert _store(web).value == before
    assert web.client.get("/api/v1/library").status_code == 200
    assert other.get("/api/v1/library").status_code == 200
    assert _session_count(web) == 2
    assert _sign_in(web, _CURRENT) == 200
    assert _sign_in(web, _NEW) == 401


def test_a_lost_compare_and_set_answers_unauthorized(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    login = web.login()
    concurrent = PasswordHasher().hash("another browser's choice")
    _store(web).value = concurrent

    lost = _change(web, login["csrf_token"])

    assert lost.status_code == 401
    assert _code(lost) == "unauthorized"
    assert _store(web).value == concurrent
    assert _session_count(web) == 1
    assert web.client.get("/api/v1/library").status_code == 200


@pytest.mark.parametrize("fault", ("throttle", "session"))
def test_session_store_faults_change_nothing(
    web: ReviewWebContext[FakeReviewRepository],
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    login = web.login()
    before = _store(web).value

    def unavailable(*_arguments: object) -> None:
        raise RuntimeError("synthetic session store fault")

    monkeypatch.setattr(
        web.repository,
        "begin_login" if fault == "throttle" else "create_review_session",
        unavailable,
    )

    failed = _change(web, login["csrf_token"])

    assert failed.status_code == 503
    assert _code(failed) == "persistence"
    assert _store(web).value == before
    assert web.client.get("/api/v1/library").status_code == 200


def test_body_ceiling_admits_three_escaped_maximum_passwords(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # Control characters are the widest JSON escape: six bytes each.
    widest = "\x01" * 1024
    body = json.dumps(
        {"current_password": widest, "new_password": widest, "confirmation": widest}
    ).encode()
    assert 18_000 < len(body) <= 3 * 1_024 * 6 + 128
    login = web.login()

    reached = web.client.post(
        _PATH,
        headers={
            **web.origin,
            "x-csrf-token": str(login["csrf_token"]),
            "content-type": "application/json",
        },
        content=body,
    )

    assert reached.status_code == 403
    assert _code(reached) == "current_password_invalid"
    quoted = _change(web, login["csrf_token"], current='"' * 1024)
    assert _code(quoted) == "current_password_invalid"

    anonymous = TestClient(web.client.app, base_url=str(web.client.base_url))
    with anonymous:
        oversized = anonymous.post(
            _PATH,
            headers={**web.origin, "content-type": "application/json"},
            content=b" " * (3 * 1_024 * 6 + 129),
        )
    assert oversized.status_code == 413
    assert _code(oversized) == "body_too_large"
