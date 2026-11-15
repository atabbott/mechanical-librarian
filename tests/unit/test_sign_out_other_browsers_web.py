"""Signing out every other browser over HTTP, in file mode and store mode."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator

import httpx2
import pytest
from fastapi.testclient import TestClient

from tests.support.review_web_doubles import (
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)
from tests.unit.review_app_fixture import TEST_REACT_BUILD_IDENTITY

_PATH = "/api/v1/session/logout-others"
_PASSWORD = "correct horse"
_OTHER_ADDRESS = "127.0.0.2"

Web = ReviewWebContext[FakeReviewRepository]


@pytest.fixture(params=["file", "store"])
def web(request: pytest.FixtureRequest) -> Iterator[Web]:
    yield from review_web_context(
        FakeReviewRepository, store_mode=request.param == "store"
    )


@pytest.fixture
def store_web() -> Iterator[Web]:
    yield from review_web_context(FakeReviewRepository, store_mode=True)


def _browser(web: Web, address: str, port: int) -> TestClient:
    browser = TestClient(
        web.client.app, base_url=str(web.client.base_url), client=(address, port)
    )
    web.cleanup.callback(browser.close)
    return browser


def _other_browser(web: Web, address: str = _OTHER_ADDRESS) -> TestClient:
    """Another browser, from another address, signed in before the sign-out."""
    other = _browser(web, address, 23456)
    signed_in = other.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _PASSWORD}
    )
    assert signed_in.status_code == 200
    other.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    return other


def _sign_out_others(web: Web, csrf: object, client: TestClient | None = None):
    return (client or web.client).post(
        _PATH, headers={**web.origin, "x-csrf-token": str(csrf)}
    )


def _code(response: httpx2.Response) -> str:
    return str(response.json()["error"]["code"])


def test_signing_out_other_browsers_answers_204_without_a_cookie_and_keeps_this_session(
    web: Web,
) -> None:
    login = web.login()
    _other_browser(web)

    signed_out = _sign_out_others(web, login["csrf_token"])

    assert signed_out.status_code == 204, signed_out.text
    assert signed_out.content == b""
    assert "set-cookie" not in signed_out.headers
    assert signed_out.headers["cache-control"] == "no-store, private"
    assert signed_out.headers["x-ml-build-identity"]
    assert web.client.get("/api/v1/library").status_code == 200
    assert _sign_out_others(web, login["csrf_token"]).status_code == 204
    signed_off = web.client.post(
        "/api/v1/session/logout",
        headers={**web.origin, "x-csrf-token": str(login["csrf_token"])},
    )
    assert signed_off.status_code == 204


def test_every_other_browser_is_refused_afterwards(web: Web) -> None:
    login = web.login()
    others = (_other_browser(web), _other_browser(web, "127.0.0.4"))
    for other in others:
        assert other.get("/api/v1/library").status_code == 200

    assert _sign_out_others(web, login["csrf_token"]).status_code == 204

    for other in others:
        refused = other.get("/api/v1/library")
        assert refused.status_code == 401
        assert _code(refused) == "unauthorized"
    assert web.client.get("/api/v1/library").status_code == 200


def _wrong_origin_without_cookie(web: Web, _csrf: str) -> httpx2.Response:
    return _browser(web, "127.0.0.5", 45678).post(
        _PATH,
        headers={
            "Origin": "https://elsewhere.test",
            "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            "x-csrf-token": "x" * 43,
        },
    )


def _no_cookie_and_no_csrf(web: Web, _csrf: str) -> httpx2.Response:
    return _browser(web, "127.0.0.5", 45678).post(
        _PATH,
        headers={**web.origin, "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY},
    )


def _ended_cookie(web: Web, _csrf: str) -> httpx2.Response:
    ended = _browser(web, "127.0.0.5", 45678)
    login = ended.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _PASSWORD}
    ).json()
    ended.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
    cookie = ended.cookies["ml_session"]
    assert (
        ended.post(
            "/api/v1/session/logout",
            headers={**web.origin, "x-csrf-token": login["csrf_token"]},
        ).status_code
        == 204
    )
    ended.cookies.set("ml_session", cookie)
    return _sign_out_others(web, login["csrf_token"], ended)


def _no_build_and_no_csrf(web: Web, _csrf: str) -> httpx2.Response:
    del web.client.headers["x-ml-build-identity"]
    try:
        return web.client.post(_PATH, headers=web.origin)
    finally:
        web.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY


def _build_and_no_csrf(web: Web, _csrf: str) -> httpx2.Response:
    return web.client.post(_PATH, headers=web.origin)


def _malformed_csrf(web: Web, _csrf: str) -> httpx2.Response:
    return _sign_out_others(web, "short")


def _wrong_csrf(web: Web, _csrf: str) -> httpx2.Response:
    return _sign_out_others(web, "y" * 43)


def _oversized_body(web: Web, csrf: str) -> httpx2.Response:
    return web.client.post(
        _PATH,
        headers={**web.origin, "x-csrf-token": csrf},
        content=b"x" * 2049,
    )


def _oversized_body_without_cookie(web: Web, _csrf: str) -> httpx2.Response:
    return _browser(web, "127.0.0.5", 45678).post(
        _PATH,
        headers={**web.origin, "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY},
        content=b"x" * 2049,
    )


@pytest.mark.parametrize(
    ("send", "status", "code"),
    [
        pytest.param(_wrong_origin_without_cookie, 403, "origin", id="wrong_origin"),
        pytest.param(
            _oversized_body_without_cookie,
            413,
            "body_too_large",
            id="oversized_body_before_the_session",
        ),
        pytest.param(_no_cookie_and_no_csrf, 401, "unauthorized", id="no_cookie"),
        pytest.param(_ended_cookie, 401, "unauthorized", id="ended_cookie"),
        pytest.param(
            _no_build_and_no_csrf, 409, "browser_build_mismatch", id="no_build"
        ),
        pytest.param(_build_and_no_csrf, 422, "invalid", id="no_csrf"),
        pytest.param(_malformed_csrf, 403, "csrf", id="malformed_csrf"),
        pytest.param(_wrong_csrf, 403, "csrf", id="wrong_csrf"),
        pytest.param(_oversized_body, 413, "body_too_large", id="oversized_body"),
    ],
)
def test_refusals_come_in_order(
    store_web: Web,
    send: Callable[[Web, str], httpx2.Response],
    status: int,
    code: str,
) -> None:
    login = store_web.login()
    other = _other_browser(store_web)

    refused = send(store_web, str(login["csrf_token"]))

    assert refused.status_code == status, refused.text
    assert _code(refused) == code
    assert "set-cookie" not in refused.headers
    assert other.get("/api/v1/library").status_code == 200
    assert store_web.client.get("/api/v1/library").status_code == 200


def test_a_session_store_fault_answers_persistence_and_signs_out_no_one(
    store_web: Web,
) -> None:
    login = store_web.login()
    other = _other_browser(store_web)
    store_web.repository.end_others_failure = True

    refused = _sign_out_others(store_web, login["csrf_token"])

    assert refused.status_code == 503
    assert _code(refused) == "persistence"
    assert other.get("/api/v1/library").status_code == 200


@pytest.mark.parametrize(("outcome", "status"), [("unauthorized", 401), ("csrf", 403)])
def test_a_session_ended_or_token_rotated_meanwhile_is_refused(
    store_web: Web, outcome: str, status: int
) -> None:
    login = store_web.login()
    other = _other_browser(store_web)
    store_web.repository.end_others_result = outcome

    refused = _sign_out_others(store_web, login["csrf_token"])

    assert refused.status_code == status
    assert _code(refused) == outcome
    assert other.get("/api/v1/library").status_code == 200


def test_signing_out_other_browsers_leaves_the_sign_in_throttle(web: Web) -> None:
    login = web.login()
    throttled = _browser(web, "127.0.0.6", 56789)
    for _ in range(5):
        wrong = throttled.post(
            "/api/v1/session/login", headers=web.origin, json={"password": "wrong"}
        )
        assert wrong.status_code == 401
    refused = throttled.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _PASSWORD}
    )
    assert refused.status_code == 429
    attempts = list(web.repository.failures)
    assert len(attempts) == 5

    assert _sign_out_others(web, login["csrf_token"]).status_code == 204

    assert web.repository.failures == attempts
    still = throttled.post(
        "/api/v1/session/login", headers=web.origin, json={"password": _PASSWORD}
    )
    assert still.status_code == 429
    assert _code(still) == "throttled"
    assert web.repository.failures == attempts


def test_signing_out_other_browsers_leaves_the_password(web: Web) -> None:
    login = web.login()
    _other_browser(web)
    hash_file = web.hash_path.read_bytes()
    stored = None if web.verifier_store is None else web.verifier_store.value

    assert _sign_out_others(web, login["csrf_token"]).status_code == 204

    assert web.hash_path.read_bytes() == hash_file
    if web.verifier_store is not None:
        assert web.verifier_store.value == stored
    signed_in = _browser(web, "127.0.0.7", 34567).post(
        "/api/v1/session/login", headers=web.origin, json={"password": _PASSWORD}
    )
    assert signed_in.status_code == 200


def test_responses_and_the_event_carry_no_secret(
    web: Web, capsys: pytest.CaptureFixture[str]
) -> None:
    login = web.login()
    other = _other_browser(web)
    csrf = str(login["csrf_token"])
    secrets = [
        web.client.cookies["ml_session"],
        other.cookies["ml_session"],
        csrf,
        _PASSWORD,
        _OTHER_ADDRESS,
        *web.repository.sessions,
        *(entry[1] for entry in web.repository.sessions.values()),
    ]
    capsys.readouterr()

    responses = [_sign_out_others(web, csrf), other.get("/api/v1/library")]

    assert [response.status_code for response in responses] == [204, 401]
    stderr = capsys.readouterr().err
    events = [
        json.loads(line)
        for line in stderr.splitlines()
        if '"owner_sign_out_others"' in line
    ]
    assert events == [
        {
            "event": "owner_sign_out_others",
            "level": "info",
            "outcome": "signed_out",
            "sessions_ended": 1,
        }
    ]
    for secret in secrets:
        assert secret not in stderr
        for response in responses:
            assert secret not in response.text
            assert all(
                secret not in name and secret not in value
                for name, value in response.headers.items()
            )
