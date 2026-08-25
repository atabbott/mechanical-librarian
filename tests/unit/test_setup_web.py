from __future__ import annotations

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.application.review_web import OwnerSessionService
from mechanical_librarian.application.setup_connection import SetupConnectionError
from mechanical_librarian.application.setup_journey import SetupJourneyOperation
from mechanical_librarian.domain.setup_state import (
    SetupLibraryCandidate,
    SetupStateDocument,
)
from mechanical_librarian.interfaces.web.app import owner_password_verifier
from mechanical_librarian.interfaces.web.setup import create_setup_app


class _Sessions:
    def __init__(self) -> None:
        self.failures = 0
        self.records: dict[str, tuple[str, int]] = {}

    def begin_login(self, _now: int, _window: int, maximum: int) -> bool:
        if self.failures >= maximum:
            return False
        self.failures += 1
        return True

    def create_review_session(
        self, token: str, csrf: str, _now: int, expires: int
    ) -> None:
        self.records[token] = (csrf, expires)

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
    def __init__(self) -> None:
        self.document = SetupStateDocument(
            schema_version="setup-state-v1",
            revision=1,
            bootstrap_verifier_digest="0" * 64,
        )
        self.promoted = False

    def read(self, _bootstrap: str) -> SetupStateDocument:
        return self.document

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


def _client() -> tuple[TestClient, _State, _Connection]:
    verifier = PasswordHasher().hash("correct horse battery staple")
    sessions = OwnerSessionService(
        _Sessions(),
        owner_password_verifier(verifier),
        lambda: 1_000,
        3,
        lambda _n: "x" * 32,
    )
    state = _State()
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
            )
        ),
        state,
        connection,
    )


def test_setup_surface_exposes_only_health_shell_session_and_redacted_state() -> None:
    client, _state, _connection = _client()

    assert client.get("/health/ready").text == "ready\n"
    assert client.get("/api/library").status_code == 404
    shell = client.get("/")
    assert shell.status_code == 200
    assert 'id="setup-ledger"' in shell.text
    assert "https://openlibrary.org/account/create" in shell.text
    assert "https://hardcover.app/account/api" in shell.text
    assert '<script type="module" src="/assets/setup.js"></script>' in shell.text
    assert "content-security-policy" in shell.headers
    assert shell.headers["cache-control"] == "private, no-store"
    css = client.get("/assets/setup.css")
    script = client.get("/assets/setup.js")
    assert css.status_code == 200
    assert "@media(max-width:760px)" in css.text
    assert "prefers-reduced-motion" in css.text
    assert script.status_code == 200
    assert "localStorage" not in script.text
    assert "sessionStorage" not in script.text
    assert "csrf=state.csrf_token" in script.text
    assert "location.assign('/library')" not in script.text
    assert "open.href='/library?lens=all'" in script.text
    assert "preparation.first_boot_complete" in script.text
    assert ".field[hidden]{display:none}" in css.text
    assert "MutationObserver" not in script.text
    assert "Writes stay off until you turn them on in Activity." in script.text
    assert client.get("/api/setup/state").status_code == 401

    login = client.post(
        "/api/setup/session", json={"password": "correct horse battery staple"}
    )

    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "Secure" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    state = client.get(
        "/api/setup/state",
        headers={"cookie": login.headers["set-cookie"].split(";", 1)[0]},
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
    for _ in range(5):
        assert (
            client.post("/api/setup/session", json={"password": "wrong"}).status_code
            == 401
        )
    assert (
        client.post("/api/setup/session", json={"password": "wrong"}).status_code == 429
    )


def test_connect_requires_session_csrf_and_origin_and_never_returns_key() -> None:
    client, _state, connection = _client()
    login = client.post(
        "/api/setup/session", json={"password": "correct horse battery staple"}
    )
    cookie = login.headers["set-cookie"].split(";", 1)[0]
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
    state = _State()
    connection = _Connection(state)
    login_tokens = iter(("s" * 32, "c" * 32))
    first_sessions = OwnerSessionService(
        session_store,
        owner_password_verifier(verifier),
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
    login = first_client.post(
        "/api/setup/session", json={"password": "correct horse battery staple"}
    )
    cookie = login.headers["set-cookie"].split(";", 1)[0]
    old_csrf = login.json()["csrf_token"]

    resumed_sessions = OwnerSessionService(
        session_store,
        owner_password_verifier(verifier),
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
    login = client.post(
        "/api/setup/session", json={"password": "correct horse battery staple"}
    )
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
            "cookie": login.headers["set-cookie"].split(";", 1)[0],
            "origin": "https://setup.invalid",
            "x-csrf-token": login.json()["csrf_token"],
        },
    )

    assert response.status_code == 502
    assert response.json() == {"detail": {"error": "timeout"}}
    assert state.document.next_step() == "connect"
    assert "secret-value" not in response.text


def test_six_step_journey_closes_setup_routes_after_exact_kickoff_copy() -> None:
    client, state, _connection = _client()
    login = client.post(
        "/api/setup/session", json={"password": "correct horse battery staple"}
    )
    headers = {
        "cookie": login.headers["set-cookie"].split(";", 1)[0],
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
                    "library_id": "books",
                    "media_kind": "ebook",
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
    kickoff = client.post(
        "/api/setup/kick-off",
        json={"expected_revision": mounts.json()["revision"]},
        headers=headers,
    )

    assert discovery.json() == {
        "libraries": [
            {"library_id": "books", "name": "Books", "media_kind": "book"},
            {
                "library_id": "podcasts",
                "name": "Podcasts",
                "media_kind": "unsupported",
            },
        ]
    }
    assert kickoff.status_code == 202
    assert kickoff.json() == {
        "status": "restart_required",
        "message": "Nothing in your library changes. Writes stay off until you turn them on in Activity.",
    }
    assert state.promoted is True
    assert client.get("/").status_code == 404
    assert client.get("/health/ready").status_code == 200
