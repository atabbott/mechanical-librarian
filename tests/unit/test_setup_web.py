from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, Callable, Literal, TypeVar, cast
from unittest.mock import Mock

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.application.review_web import OwnerSessionService
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
    ReviewerAccessRepositoryError,
)
from mechanical_librarian.application.setup_connection import SetupConnectionError
from mechanical_librarian.application.setup_journey import SetupJourneyOperation
from mechanical_librarian.domain.setup_state import (
    SetupLibraryCandidate,
    SetupStateDocument,
)
from mechanical_librarian.interfaces.web.app import (
    owner_password_binding,
    owner_password_verifier,
    reviewer_failure_response,
)
from mechanical_librarian.interfaces.web.setup import create_setup_app
from mechanical_librarian.interfaces.web.setup_frontend import (
    SETUP_CSS,
    SETUP_HANDOFF_CSS,
    SETUP_JS,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            ReviewerAccessApplicationError("access_changed"),
            (409, "reviewer_access_stale"),
        ),
        (
            ReviewerAccessApplicationError("credential_clear_failed"),
            (503, "credential_clear_failed"),
        ),
        (
            ReviewerAccessRepositoryError("reviewer access is unavailable"),
            (503, "reviewer_access_unavailable"),
        ),
        (ValueError("invalid"), (422, "reviewer_access_invalid")),
    ),
)
def test_reviewer_failure_translation_is_shared_and_closed(
    error: Exception, expected: tuple[int, str]
) -> None:
    assert reviewer_failure_response(error) == expected


def test_setup_reviewer_read_translates_repository_unavailability() -> None:
    reviewer = Mock(spec=ReviewerAccessOperations)
    reviewer.read.side_effect = ReviewerAccessRepositoryError(
        "reviewer access is unavailable"
    )
    client, _state, _connection = _client(reviewer)
    session = _claim(client)
    response = client.get(
        "/api/setup/reviewer-access",
        headers={"cookie": _session_cookie(session)},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": {"error": "reviewer_access_unavailable"}}


def test_setup_generation_serializes_reviewer_operations() -> None:
    reviewer = object.__new__(ReviewerAccessOperations)
    reviewer._serialized = lambda call: call()
    _client(reviewer)
    entered = Event()
    release = Event()
    second_done = Event()
    calls: list[str] = []

    def view() -> object:
        calls.append("entered")
        entered.set()
        release.wait(timeout=2)
        return object()

    cast("Any", reviewer)._view = view

    def second_read() -> None:
        reviewer.read()
        second_done.set()

    first = Thread(target=reviewer.read)
    second = Thread(target=second_read)
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    assert not second_done.wait(timeout=0.05)
    assert calls == ["entered"]
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert calls == ["entered", "entered"]


class _Sessions:
    def __init__(self) -> None:
        self.failures = 0
        self.fail_create = False
        self.records: dict[str, tuple[str, int]] = {}

    def begin_login(self, _now: int, _window: int, maximum: int) -> bool:
        if self.failures >= maximum:
            return False
        self.failures += 1
        return True

    def create_review_session(
        self, token: str, csrf: str, _now: int, expires: int
    ) -> None:
        if self.fail_create:
            raise RuntimeError("synthetic session persistence failure")
        self.records[token] = (csrf, expires)
        self.failures = 0

    def authenticate_review_session(self, token: str, now: int) -> tuple[str, str, int]:
        csrf, expires = self.records[token]
        if now >= expires:
            raise KeyError(token)
        return "owner", csrf, expires

    def rotate_review_session_csrf(self, token: str, csrf: str, now: int) -> str:
        record = self.records.get(token)
        if record is None:
            return "inactive"
        _old_csrf, expires = record
        if now >= expires:
            return "inactive"
        self.records[token] = (csrf, expires)
        return "rotated"

    def revoke_review_session(self, token: str, csrf: str, now: int) -> str:
        record = self.records.get(token)
        if record is None or record[1] <= now:
            return "unauthorized"
        if record[0] != csrf:
            return "csrf"
        del self.records[token]
        return "revoked"


class _State:
    def __init__(self, sessions: _Sessions | None = None) -> None:
        self.sessions = sessions
        self.document = SetupStateDocument(
            schema_version="setup-state-v2",
            revision=1,
            bootstrap_verifier_digest="0" * 64,
        )
        self.promoted = False
        self.owner_verifier: str | None = None

    def claim_owner(
        self,
        *,
        expected_revision: int,
        owner_verifier: str,
    ) -> SetupStateDocument:
        assert owner_verifier.startswith("$argon2id$")
        self.owner_verifier = owner_verifier
        assert expected_revision == self.document.revision
        self.document = SetupStateDocument.model_validate(
            {
                **self.document.model_dump(),
                "revision": expected_revision + 1,
                "completed_steps": ["claim"],
                "claimed": True,
            }
        )
        return self.document

    def read(self, _bootstrap: str) -> SetupStateDocument:
        return self.document

    def use_owner_verifier(self, consumer: Callable[[str], _T]) -> _T:
        assert self.owner_verifier is not None
        return consumer(self.owner_verifier)

    def write(
        self,
        document: SetupStateDocument,
        _expected_revision: int,
        _bootstrap_verifier: str,
        _secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument:
        self.document = document
        return document

    def promote(
        self,
        _bootstrap_verifier: str,
        _owner_verifier: str,
        _expected_revision: int | None = None,
    ) -> None:
        self.promoted = True

    def transition_reviewer(
        self,
        document: SetupStateDocument,
        _expected_revision: int,
        _reviewer_revision: int,
        _outcome: Literal["skipped", "configured"],
    ) -> SetupStateDocument:
        self.document = document
        return document


class _FailingState(_State):
    def read(self, _bootstrap: str) -> SetupStateDocument:
        raise RuntimeError("private failure detail")


class _Connection:
    def __init__(self, state: _State) -> None:
        self.state = state
        self.requests: list[tuple[int, str, str, str]] = []
        self.failure: str | None = None

    def connect(
        self, expected_revision: int, name: str, endpoint: str, key: str
    ) -> SetupStateDocument:
        self.requests.append((expected_revision, name, endpoint, key))
        if self.failure is not None:
            raise SetupConnectionError(self.failure)
        current = self.state.document
        self.state.document = SetupStateDocument.model_validate(
            {
                **current.model_dump(),
                "revision": current.revision + 1,
                "completed_steps": ["claim", "connect"],
                "connection": {"name": name, "endpoint": endpoint},
            }
        )
        return self.state.document


_T = TypeVar("_T")


def _client(
    reviewer_access: ReviewerAccessOperations | None = None,
) -> tuple[TestClient, _State, _Connection]:
    verifier = PasswordHasher().hash("correct horse battery staple")
    session_store = _Sessions()
    state = _State(session_store)
    sessions = OwnerSessionService(
        session_store,
        # Mirror the composition root: verify against the claimed verifier,
        # which is what serves the first login right after the claim (#912).
        lambda password: state.use_owner_verifier(
            lambda encoded: owner_password_verifier(encoded)(password)
        ),
        lambda token: state.use_owner_verifier(
            lambda encoded: owner_password_binding(encoded)[1](token)
        ),
        lambda: 1_000,
        3,
        lambda _n: "x" * 32,
    )
    connection = _Connection(state)
    journey = SetupJourneyOperation(
        state,
        verifier,
        lambda _document: (
            SetupLibraryCandidate(library_id="books", name="Books", media_kind="book"),
            SetupLibraryCandidate(
                library_id="podcasts", name="Podcasts", media_kind="unsupported"
            ),
        ),
        {"/library": ("books",)},
    )
    return (
        TestClient(
            create_setup_app(
                sessions,
                state,
                verifier,
                connection,
                "https://setup.invalid",
                journey,
                reviewer_access,
            )
        ),
        state,
        connection,
    )


def _claim(client: TestClient, password: str = "correct horse battery staple") -> Any:
    nonce = client.get("/api/setup/claim")
    nonce_cookie = nonce.headers["set-cookie"].split(";", 1)[0]
    return client.post(
        "/api/setup/session",
        json={
            "password": password,
            "confirmation": password,
            "expected_revision": nonce.json()["revision"],
        },
        headers={
            "cookie": nonce_cookie,
            "origin": "https://setup.invalid",
            "x-setup-nonce": nonce.json()["nonce"],
        },
    )


def _session_cookie(response: Any) -> str:
    return next(
        value.strip().split(";", 1)[0]
        for value in response.headers["set-cookie"].split(",")
        if value.strip().startswith("ml_session=")
    )


def test_setup_surface_exposes_only_health_shell_session_and_redacted_state() -> None:
    client, _state, _connection = _client()

    assert client.get("/health/ready").text == "ready\n"
    assert client.get("/api/library").status_code == 404
    shell = client.get("/")
    assert shell.status_code == 200
    assert 'id="setup-ledger"' in shell.text
    assert "https://openlibrary.org/account/create" not in shell.text
    assert '<script type="module" src="/assets/setup.js"></script>' in shell.text
    assert 'rel="preload" href="/assets/setup-handoff.css" as="style"' in shell.text
    assert 'id="setup-handoff-motion"' in shell.text
    assert 'href="/assets/setup-handoff.css" disabled' in shell.text
    assert "content-security-policy" in shell.headers
    assert shell.headers["cache-control"] == "private, no-store"
    css = client.get("/assets/setup.css")
    handoff_css = client.get("/assets/setup-handoff.css")
    script = client.get("/assets/setup.js")
    assert css.status_code == 200
    assert "@media(max-width:760px)" in css.text
    assert "prefers-reduced-motion" in css.text
    assert handoff_css.status_code == 200
    assert handoff_css.headers["content-type"].startswith("text/css")
    assert handoff_css.text == "@view-transition{navigation:auto}"
    assert script.status_code == 200
    assert "https://openlibrary.org/account/create" in script.text
    assert "https://hardcover.app/account/api" in script.text
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text
    assert "csrf=state.csrf_token" in script.text
    assert "location.assign('/library')" not in script.text
    assert "const retired=visible?.state==='dismissed'" in script.text
    assert "let open=null;let observed=true" in script.text
    assert "if(!visible||retired){if(observed||retired)retire(retired)" in script.text
    assert "dismissed?'Preparation was dismissed'" in script.text
    assert "dismissed?'Open the ordinary workspace.'" in script.text
    assert "if(!open){open=document.createElement('a')" in script.text
    assert "open.href='/library?lens=all'" in script.text
    assert (
        "['completed_clean','completed_with_failures'].includes(visible.state)"
        in script.text
    )
    assert "pollDelay=terminal?5000:1500" in script.text
    assert "if(terminal)stop()" not in script.text
    assert "encodeURIComponent(visible.run_id)" in script.text
    assert "setInterval" not in script.text
    assert "const poll=async()=>" in script.text
    assert "finally{if(!stopped)timer=setTimeout(poll,pollDelay)}" in script.text
    assert ".field[hidden]{display:none}" in css.text
    assert "MutationObserver" not in script.text
    assert "Writes stay off until you turn them on in Activity." in script.text
    assert client.get("/api/setup/state").status_code == 401

    login = _claim(client)

    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Secure" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert "Path=/" in login.headers["set-cookie"]
    assert "Max-Age=2592000" in login.headers["set-cookie"]
    state = client.get(
        "/api/setup/state",
        headers={"cookie": _session_cookie(login)},
    )
    assert state.json() == {
        "csrf_token": "x" * 32,
        "completed_steps": ["claim"],
        "next_step": "connect",
        "revision": 2,
        "connection": None,
        "providers": [],
        "libraries": [],
        "mounts": [],
    }
    assert "bootstrap_verifier" not in state.text


def test_wrong_setup_password_is_rate_limited() -> None:
    client, _state, _connection = _client()
    _claim(client)
    for _ in range(5):
        assert (
            client.post("/api/setup/session", json={"password": "wrong"}).status_code
            == 401
        )
    assert (
        client.post("/api/setup/session", json={"password": "wrong"}).status_code == 429
    )


def test_session_failure_after_owner_claim_is_retryable_unavailability() -> None:
    client, state, _connection = _client()
    assert state.sessions is not None
    state.sessions.fail_create = True

    failed = _claim(client)

    assert failed.status_code == 503
    assert failed.json() == {"detail": {"error": "persistence"}}
    assert state.document.claimed
    assert state.document.revision == 2
    assert state.sessions.records == {}

    state.sessions.fail_create = False
    retried = client.post(
        "/api/setup/session",
        json={"password": "correct horse battery staple"},
    )

    assert retried.status_code == 200
    assert "ml_session=" in retried.headers["set-cookie"]


@pytest.mark.parametrize(
    "path",
    (
        "/api/setup/session",
        "/api/setup/connect",
        "/api/setup/providers",
        "/api/setup/libraries",
        "/api/setup/mounts",
        "/api/setup/change",
        "/api/setup/kick-off",
    ),
)
def test_setup_json_bodies_are_bounded_before_parsing_or_authentication(
    path: str,
) -> None:
    client, _state, _connection = _client()

    response = client.post(
        path,
        content=b'{"private":"' + b"x" * 262_145 + b'"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "body_too_large"


def test_oversized_setup_login_does_not_consume_the_login_throttle() -> None:
    client, _state, _connection = _client()
    _claim(client)
    oversized = client.post(
        "/api/setup/session",
        content=b"{" + b"x" * 12_416,
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413

    for _ in range(5):
        assert (
            client.post("/api/setup/session", json={"password": "wrong"}).status_code
            == 401
        )
    assert (
        client.post("/api/setup/session", json={"password": "wrong"}).status_code == 429
    )


def test_maximum_setup_password_pair_reaches_owner_claim() -> None:
    client, state, _connection = _client()
    password = "\0" * 1_024

    response = _claim(client, password)

    assert response.status_code == 200
    assert state.document.claimed


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/health/ready"),
        ("GET", "/"),
        ("GET", "/assets/setup.css"),
        ("GET", "/assets/setup.js"),
        ("GET", "/missing"),
        ("GET", "/api/setup/state"),
        ("POST", "/api/setup/session"),
    ),
)
def test_every_setup_response_carries_the_normal_hsts_policy(
    method: str, path: str
) -> None:
    client, _state, _connection = _client()

    response = client.request(method, path)

    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_unexpected_setup_failure_returns_fixed_secure_response() -> None:
    verifier = PasswordHasher().hash("correct horse battery staple")
    sessions = OwnerSessionService(
        _Sessions(),
        owner_password_verifier(verifier),
        owner_password_binding(verifier)[1],
        lambda: 1_000,
        3,
        lambda _n: "x" * 32,
    )
    client = TestClient(
        create_setup_app(sessions, _FailingState(), verifier),
        raise_server_exceptions=False,
    )

    response = client.get(
        "/api/setup/state", headers={"cookie": "ml_session=surviving-session"}
    )

    assert response.status_code == 500
    assert response.text == '{"detail":{"error":"unavailable"}}'
    assert "private failure detail" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_connect_requires_session_csrf_and_origin_and_never_returns_key() -> None:
    client, _state, connection = _client()
    login = _claim(client)
    cookie = _session_cookie(login)
    request = {
        "expected_revision": 2,
        "name": "primary",
        "endpoint": "https://abs.invalid",
        "key": "secret-value",
    }

    assert client.post("/api/setup/connect", json=request).status_code == 401
    assert (
        client.post(
            "/api/setup/connect",
            json=request,
            headers={"cookie": cookie, "origin": "https://setup.invalid"},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/setup/connect",
        json=request,
        headers={
            "cookie": cookie,
            "origin": "https://setup.invalid",
            "x-csrf-token": login.json()["csrf_token"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "completed_steps": ["claim", "connect"],
        "next_step": "providers",
        "revision": 3,
    }
    assert "secret-value" not in response.text
    assert connection.requests == [
        (2, "primary", "https://abs.invalid", "secret-value")
    ]


def test_surviving_session_rotates_csrf_for_mutation_after_restart() -> None:
    verifier = PasswordHasher().hash("correct horse battery staple")
    session_store = _Sessions()
    state = _State(session_store)
    connection = _Connection(state)
    login_tokens = iter(("s" * 32, "c" * 32))
    first_sessions = OwnerSessionService(
        session_store,
        owner_password_verifier(verifier),
        lambda token: state.use_owner_verifier(
            lambda encoded: owner_password_binding(encoded)[1](token)
        ),
        lambda: 1_000,
        3,
        lambda _n: next(login_tokens),
    )
    first_client = TestClient(
        create_setup_app(
            first_sessions,
            state,
            verifier,
            connection,
            "https://setup.invalid",
        )
    )
    login = _claim(first_client)
    cookie = _session_cookie(login)
    old_csrf = login.json()["csrf_token"]

    resumed_sessions = OwnerSessionService(
        session_store,
        owner_password_verifier(verifier),
        lambda token: state.use_owner_verifier(
            lambda encoded: owner_password_binding(encoded)[1](token)
        ),
        lambda: 1_000,
        3,
        lambda _n: "r" * 32,
    )
    resumed_client = TestClient(
        create_setup_app(
            resumed_sessions,
            state,
            verifier,
            connection,
            "https://setup.invalid",
        )
    )
    resumed_state = resumed_client.get("/api/setup/state", headers={"cookie": cookie})
    recovered_csrf = resumed_state.json()["csrf_token"]
    assert resumed_state.status_code == 200
    assert resumed_state.headers["cache-control"] == "private, no-store"
    assert recovered_csrf == "r" * 32
    assert recovered_csrf != old_csrf
    request = {
        "expected_revision": 2,
        "name": "primary",
        "endpoint": "https://abs.invalid",
        "key": "secret-value",
    }

    for csrf in (None, "wrong" * 8, old_csrf):
        headers = {"cookie": cookie, "origin": "https://setup.invalid"}
        if csrf is not None:
            headers["x-csrf-token"] = csrf
        assert (
            resumed_client.post(
                "/api/setup/connect", json=request, headers=headers
            ).status_code
            == 403
        )
    assert (
        resumed_client.post(
            "/api/setup/connect",
            json=request,
            headers={
                "cookie": cookie,
                "origin": "https://setup.invalid",
                "x-csrf-token": recovered_csrf,
            },
        ).status_code
        == 200
    )
    assert connection.requests == [
        (2, "primary", "https://abs.invalid", "secret-value")
    ]


def test_connect_failure_returns_closed_code_and_does_not_advance_state() -> None:
    client, state, connection = _client()
    login = _claim(client)
    connection.failure = "timeout"

    response = client.post(
        "/api/setup/connect",
        json={
            "expected_revision": 2,
            "name": "primary",
            "endpoint": "https://abs.invalid",
            "key": "secret-value",
        },
        headers={
            "cookie": _session_cookie(login),
            "origin": "https://setup.invalid",
            "x-csrf-token": login.json()["csrf_token"],
        },
    )

    assert response.status_code == 502
    assert response.json() == {"detail": {"error": "timeout"}}
    assert state.document.next_step() == "connect"
    assert "secret-value" not in response.text


def test_duplicate_local_library_names_return_closed_validation_without_mutation() -> (
    None
):
    client, state, _connection = _client()
    login = _claim(client)
    headers = {
        "cookie": _session_cookie(login),
        "origin": "https://setup.invalid",
        "x-csrf-token": login.json()["csrf_token"],
    }
    assert (
        client.post(
            "/api/setup/connect",
            json={
                "expected_revision": 2,
                "name": "primary",
                "endpoint": "https://abs.invalid",
                "key": "secret-value",
            },
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/setup/providers",
            json={"expected_revision": 3, "providers": []},
            headers=headers,
        ).status_code
        == 200
    )
    duplicate = {
        "name": "books",
        "library_id": "books",
        "media_kind": "book",
        "provider_connections": [],
    }

    response = client.post(
        "/api/setup/libraries",
        json={"expected_revision": 4, "libraries": [duplicate, duplicate]},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"error": "invalid_libraries"}}
    assert state.document.revision == 4
    assert state.document.next_step() == "libraries"


def test_seven_step_journey_closes_setup_routes_after_exact_kickoff_copy() -> None:
    reviewer_access = Mock(spec=ReviewerAccessOperations)
    reviewer_access.read.return_value = SimpleNamespace(
        revision=7,
        mapping=SimpleNamespace(engine="openrouter"),
    )
    client, state, _connection = _client(reviewer_access)
    login = _claim(client)
    headers = {
        "cookie": _session_cookie(login),
        "origin": "https://setup.invalid",
        "x-csrf-token": login.json()["csrf_token"],
    }
    connect = client.post(
        "/api/setup/connect",
        json={
            "expected_revision": 2,
            "name": "primary",
            "endpoint": "https://abs.invalid",
            "key": "secret-value",
        },
        headers=headers,
    )
    providers = client.post(
        "/api/setup/providers",
        json={"expected_revision": connect.json()["revision"], "providers": []},
        headers=headers,
    )

    discovery = client.get(
        "/api/setup/libraries",
        params={"expected_revision": providers.json()["revision"]},
        headers={"cookie": headers["cookie"]},
    )
    libraries = client.post(
        "/api/setup/libraries",
        json={
            "expected_revision": providers.json()["revision"],
            "libraries": [
                {
                    "name": "books",
                    "display_name": "Owner's Ebooks & Audiobooks",
                    "library_id": "books",
                    "media_kind": "book",
                    "provider_connections": [],
                }
            ],
        },
        headers=headers,
    )
    mounts = client.post(
        "/api/setup/mounts",
        json={
            "expected_revision": libraries.json()["revision"],
            "service_paths": {"books": "/library"},
        },
        headers=headers,
    )
    stale_reviewer = client.post(
        "/api/setup/reviewer",
        json={"expected_revision": mounts.json()["revision"] - 1, "outcome": "skipped"},
        headers=headers,
    )
    assert stale_reviewer.status_code == 409
    reviewer_access.select_none.assert_not_called()
    reviewer = client.post(
        "/api/setup/reviewer",
        json={"expected_revision": mounts.json()["revision"], "outcome": "skipped"},
        headers=headers,
    )
    kickoff = client.post(
        "/api/setup/kick-off",
        json={"expected_revision": reviewer.json()["revision"]},
        headers=headers,
    )

    assert discovery.json() == {
        "libraries": [
            {
                "library_id": "books",
                "name": "Books",
                "media_kind": "book",
                "service_path": None,
                "service_path_state": "unavailable",
            },
            {
                "library_id": "podcasts",
                "name": "Podcasts",
                "media_kind": "unsupported",
                "service_path": None,
                "service_path_state": "unavailable",
            },
        ]
    }
    assert kickoff.status_code == 202
    reviewer_access.select_none.assert_not_called()
    assert state.document.reviewer_setup == "skipped"
    assert state.document.libraries[0].display_name == "Owner's Ebooks & Audiobooks"
    assert kickoff.json() == {
        "status": "active",
        "message": "Nothing in your library changes. Writes stay off until you turn them on in Activity.",
    }
    assert state.promoted is True
    closed = client.get("/")
    assert closed.status_code == 404
    assert closed.headers["strict-transport-security"] == "max-age=31536000"
    assert client.get("/health/ready").status_code == 200


def test_setup_library_step_uses_audiobookshelf_book_kind_without_owner_choice() -> (
    None
):
    assert "Library kind" not in SETUP_JS
    assert "media_kind:'book'" in SETUP_JS
    assert "displayName.value=item.name" in SETUP_JS
    assert "field('Library name',displayName)" in SETUP_JS
    assert "display_name:row.querySelector('input[name^=display-]').value" in SETUP_JS
    assert "row.dataset.name=stableName" in SETUP_JS
    assert "name:row.dataset.name" in SETUP_JS
    assert "Preparation will begin in the workspace." in SETUP_JS
    assert "className='provider-access'" in SETUP_JS
    assert "`${evidence?.name||humanName(item.library)}:" in SETUP_JS
    assert (
        "field(`${evidence?.name||humanName(library.name)} service path`,path)"
        in SETUP_JS
    )
    assert "state.mounts.length?'Retest mounts':'Check mounts'" in SETUP_JS
    assert (
        "const codexState=access.codex.state==='host_unsupported'?access.codex:"
        "(codexTerminal||access.codex)" in SETUP_JS
    )
    assert "codexTerminal=device;await render()" in SETUP_JS
    assert "Check existing sign-in" in SETUP_JS
    assert "check.disabled=codexState.state==='host_unsupported'" in SETUP_JS
    assert "/api/setup/reviewer/codex/recheck" in SETUP_JS
    assert "reviewer_access_stale:'Reviewer access changed" in SETUP_JS
    assert "invalid_key:'OpenRouter rejected this key." in SETUP_JS


def test_setup_enables_cross_document_motion_only_for_the_workspace_handoff() -> None:
    assert "@view-transition{navigation:auto}" not in SETUP_CSS
    assert SETUP_HANDOFF_CSS == "@view-transition{navigation:auto}"
    assert "sessionStorage" not in SETUP_JS
    assert "createElement('style')" not in SETUP_JS
    assert "target.pathname!=='/library'" in SETUP_JS
    assert "/^preparation_run_[0-9a-f]{64}$/.test(run)" in SETUP_JS
    assert "motion.disabled=false" in SETUP_JS
    assert "addEventListener('pageswap'" in SETUP_JS
    assert "handoff.viewTransition.finished.then(disable,disable)" in SETUP_JS
    assert "addEventListener('pageshow',disable,{once:true})" in SETUP_JS
    assert "fallback=setTimeout(disable,2000)" in SETUP_JS
    assert ".finally(" not in SETUP_JS


def test_setup_admits_the_keyless_audiobook_providers_with_no_credential() -> None:
    client, state, _connection = _client()
    login = _claim(client)
    headers = {
        "cookie": _session_cookie(login),
        "origin": "https://setup.invalid",
        "x-csrf-token": login.json()["csrf_token"],
    }
    assert (
        client.post(
            "/api/setup/connect",
            json={
                "expected_revision": 2,
                "name": "primary",
                "endpoint": "https://abs.invalid",
                "key": "secret-value",
            },
            headers=headers,
        ).status_code
        == 200
    )

    accepted = client.post(
        "/api/setup/providers",
        json={
            "expected_revision": 3,
            "providers": [
                {"name": name, "adapter": adapter, "enabled": True}
                for name, adapter in (
                    ("audible", "audible_catalog"),
                    ("audiosilo", "audiosilo"),
                    ("audnexus", "audnexus"),
                    ("itunes", "itunes_search"),
                )
            ],
        },
        headers=headers,
    )

    assert accepted.status_code == 200
    assert [item.adapter for item in state.document.providers] == [
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "itunes_search",
    ]
    # Keyless throughout: nothing was collected, so nothing is required to
    # resume or promote the journey beyond the target connection's own key.
    assert state.document.secret_fields() == frozenset({"connection.primary.key"})
    assert state.document.required_secret_fields() == frozenset(
        {"connection.primary.key"}
    )


def test_setup_providers_step_offers_the_audiobook_providers_without_a_key() -> None:
    assert "['audible','audible_catalog','Audible']" in SETUP_JS
    assert "['audnexus','audnexus','Audnexus']" in SETUP_JS
    assert "['audiosilo','audiosilo','AudioSilo']" in SETUP_JS
    assert "['itunes','itunes_search','iTunes']" in SETUP_JS
    # No credential control and no account link is constructed for one.
    assert (
        "const key=adapter==='open_library'||settingsProviderKeyless(adapter)"
        "?null:input(`${name}-key`,'password');" in SETUP_JS
    )
    assert "if(accessHref){" in SETUP_JS
    assert (
        "const settingsProviderKeyless=adapter=>"
        "settingsVocabulary.keyless_provider_adapters.includes(adapter);" in SETUP_JS
    )
