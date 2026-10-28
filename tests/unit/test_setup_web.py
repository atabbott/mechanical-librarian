from __future__ import annotations

import re
from pathlib import Path
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from typing import Any, Callable, Literal, TypeVar, cast
from unittest.mock import Mock

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthState,
)
from mechanical_librarian.application.review_web import OwnerSessionService
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
    ReviewerAccessRepositoryError,
)
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.application.setup_connection import SetupConnectionError
from mechanical_librarian.application.setup_journey import SetupJourneyOperation
from mechanical_librarian.domain.owner_settings import PROVIDER_LABELS
from mechanical_librarian.domain.setup_state import (
    SetupClaimTokenError,
    SetupLibraryCandidate,
    SetupStateDocument,
    claim_token_digest,
    claim_token_matches,
)
from mechanical_librarian.interfaces.web.app import (
    owner_password_binding,
    owner_password_verifier,
    reviewer_failure_response,
)
from mechanical_librarian.interfaces.web.review_frontend import (
    THEME_JS,
    inter_font_faces,
)
from mechanical_librarian.interfaces.web.setup import create_setup_app
from mechanical_librarian.interfaces.web.setup_frontend import (
    SETUP_CSS,
    SETUP_HANDOFF_CSS,
    SETUP_ORIGIN_REFUSAL_HTML,
)
from mechanical_librarian.interfaces.web.setup_frontend import (
    SETUP_JS as SHIPPED_SETUP_JS,
)
from tests.unit.review_app_fixture import font_asset_loader

# The shipped script sets one statement per indented line. The substring pins
# below read it with that layout removed, so a pin can span statements.
SETUP_JS = re.sub(r"\n *", "", SHIPPED_SETUP_JS)


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
        (ReviewerAccessApplicationError("unreachable"), (503, "unreachable")),
        (ReviewerAccessApplicationError("timed_out"), (503, "timed_out")),
        *(
            (ReviewerAccessApplicationError(code), (422, code))
            for code in (
                "response_too_large",
                "data_policy_refused",
                "image_limit_exceeded",
            )
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


@pytest.mark.parametrize("boundary", ("snapshot", "replace"))
def test_setup_generation_serializes_reviewer_local_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    repository = open_repository(tmp_path / "review.sqlite", cross_thread=True)
    store = repository.reviewer_access_store()
    reviewer = ReviewerAccessOperations(
        store,
        codex_auth=Mock(
            passive_status=Mock(return_value=CodexDeviceAuthState("ready"))
        ),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            True, None, model="gpt-5.6-sol"
        ),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_binding=Mock(),
        openrouter_factory=Mock(),
    )
    _client(reviewer)
    entered = Event()
    release = Event()
    second_started = Event()
    second_done = Event()
    calls: list[str] = []
    errors: list[BaseException] = []
    original = getattr(store, boundary)

    def held(*args: object, **kwargs: object) -> object:
        calls.append(boundary)
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, boundary, held)

    def first_call() -> None:
        try:
            if boundary == "replace":
                reviewer.select_none(expected_revision=1)
            else:
                reviewer.read()
        except BaseException as error:
            errors.append(error)

    def second_call() -> None:
        second_started.set()
        try:
            reviewer.read()
        except BaseException as error:
            errors.append(error)
        finally:
            second_done.set()

    first = Thread(target=first_call)
    second = Thread(target=second_call)
    try:
        first.start()
        assert entered.wait(timeout=5)
        second.start()
        assert second_started.wait(timeout=5)
        assert not second_done.wait(timeout=0.05)
        assert calls == [boundary]
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
        assert not first.is_alive() and not second.is_alive()
        assert errors == []
        assert store.snapshot()[0].revision == (2 if boundary == "replace" else 1)
    finally:
        release.set()
        first.join(timeout=5)
        if second.ident is not None:
            second.join(timeout=5)
        repository.close()


def test_setup_external_reviewer_observation_allows_commit_and_refuses_stale_view(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "review.sqlite", cross_thread=True)
    store = repository.reviewer_access_store()
    entered = Event()
    release = Event()
    errors: list[BaseException] = []

    def host() -> ReviewerHostStatus:
        if current_thread().name == "held-reviewer-observation":
            assert not repository._connection.in_transaction
            entered.set()
            assert release.wait(timeout=5)
        return ReviewerHostStatus(True, None, 2 * 1024**3)

    reviewer = ReviewerAccessOperations(
        store,
        codex_auth=Mock(
            passive_status=Mock(return_value=CodexDeviceAuthState("ready"))
        ),
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            True, None, model="gpt-5.6-sol"
        ),
        codex_host=host,
        codex_binding=Mock(),
        openrouter_factory=Mock(),
    )
    _client(reviewer)

    def read() -> None:
        try:
            reviewer.read()
        except BaseException as error:
            errors.append(error)

    worker = Thread(target=read, name="held-reviewer-observation")
    try:
        worker.start()
        assert entered.wait(timeout=5)
        assert reviewer.select_none(expected_revision=1).revision == 2
        with pytest.raises(ReviewerAccessRepositoryError, match="stale"):
            reviewer.select_none(expected_revision=1)
        assert store.snapshot()[0].revision == 2
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], ReviewerAccessApplicationError)
        assert str(errors[0]) == "access_changed"
    finally:
        release.set()
        worker.join(timeout=5)
        repository.close()


class _Sessions:
    def __init__(self) -> None:
        # One entry per recorded attempt, naming its client digest.
        self.failures: list[str] = []
        self.fail_create = False
        self.records: dict[str, tuple[str, int]] = {}

    def begin_login(
        self, _now: int, client: str, _window: int, maximum: int, ceiling: int
    ) -> bool:
        if len(self.failures) >= ceiling or self.failures.count(client) >= maximum:
            return False
        self.failures.append(client)
        return True

    def create_review_session(
        self, token: str, csrf: str, _now: int, expires: int, client: str
    ) -> None:
        if self.fail_create:
            raise RuntimeError("synthetic session persistence failure")
        self.records[token] = (csrf, expires)
        self.failures = [value for value in self.failures if value != client]

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


_CLAIM_TOKEN = "deployment-held-claim-token-0123456789abcdef"


class _State:
    def __init__(self, sessions: _Sessions | None = None) -> None:
        self.sessions = sessions
        self.document = SetupStateDocument(
            schema_version="setup-state-v4",
            revision=1,
            bootstrap_verifier_digest="0" * 64,
            claim_token_digest=claim_token_digest(_CLAIM_TOKEN),
        )
        self.promoted = False
        self.owner_verifier: str | None = None
        self.claim_attempts = 0

    def claim_owner(
        self,
        *,
        expected_revision: int,
        owner_verifier: str,
        claim_token: str,
    ) -> SetupStateDocument:
        self.claim_attempts += 1
        assert owner_verifier.startswith("$argon2id$")
        if not claim_token_matches(claim_token, self.document.claim_token_digest):
            raise SetupClaimTokenError("setup claim token is unavailable")
        self.owner_verifier = owner_verifier
        assert expected_revision == self.document.revision
        self.document = SetupStateDocument.model_validate(
            {
                **self.document.model_dump(),
                "revision": expected_revision + 1,
                "completed_steps": ["claim"],
                "claimed": True,
                "claim_token_digest": None,
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
    *,
    mode: str = "direct",
    retire_claim_token: Callable[[], None] = lambda: None,
) -> tuple[TestClient, _State, _Connection]:
    origin = "http://setup.invalid" if mode == "http" else "https://setup.invalid"
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
        client_digest=lambda identity: state.use_owner_verifier(
            lambda encoded: owner_password_binding(encoded)[2](identity)
        ),
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
                origin,
                journey,
                reviewer_access,
                trusted_proxy_ips=("127.0.0.1",) if mode == "proxy" else (),
                require_forwarding=mode == "proxy",
                retire_claim_token=retire_claim_token,
            ),
            base_url="http://setup.invalid" if mode == "proxy" else origin,
            client=("127.0.0.1", 12345),
            headers={"x-forwarded-host": "setup.invalid", "x-forwarded-proto": "https"}
            if mode == "proxy"
            else {},
        ),
        state,
        connection,
    )


def _claim(
    client: TestClient,
    password: str = "correct horse battery staple",
    claim_token: str | None = _CLAIM_TOKEN,
    nonce: Any = None,
) -> Any:
    nonce = client.get("/api/setup/claim") if nonce is None else nonce
    nonce_cookie = nonce.headers["set-cookie"].split(";", 1)[0]
    return client.post(
        "/api/setup/session",
        json={
            "password": password,
            "confirmation": password,
            "expected_revision": nonce.json()["revision"],
            **({} if claim_token is None else {"claim_token": claim_token}),
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
    assert 'class="prerequisites"' in shell.text
    # What setup needs is read before the steps that need it.
    assert shell.text.index('class="prerequisites"') < shell.text.index(
        'id="setup-ledger"'
    )
    # The saved theme is applied by a blocking same-origin script before the
    # stylesheet, so the first frame is already in that theme.
    head = shell.text.split("</head>", 1)[0]
    assert '<script src="/assets/theme.js"></script>' in head
    assert head.index("/assets/theme.js") < head.index("/assets/setup.css")
    assert "Before you start" in shell.text
    assert "Bare Compose without this prepared bootstrap is not supported" in shell.text
    assert shell.text.count("<li>") == 5
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
    # The stacking breakpoint is stated in rem, so browser text zoom moves it
    # and the step card never collapses beside a fixed-width aside.
    assert "@media(max-width:47.5rem)" in css.text
    assert "max-width:760px" not in css.text
    assert "prefers-reduced-motion" in css.text
    assert handoff_css.status_code == 200
    assert handoff_css.headers["content-type"].startswith("text/css")
    assert handoff_css.text == "@view-transition{navigation:auto}"
    assert script.status_code == 200
    assert script.text == SHIPPED_SETUP_JS
    # Open Library asks only for a contact email, so it links to no account.
    assert "openlibrary.org" not in SETUP_JS
    assert "https://hardcover.app/account/api" in SETUP_JS
    assert "localStorage" not in SETUP_JS
    assert "sessionStorage" not in SETUP_JS
    assert "csrf=state.csrf_token" in SETUP_JS
    assert "location.assign('/library')" not in SETUP_JS
    assert "const retired=visible?.state==='dismissed'" in SETUP_JS
    assert "let open=null;let observed=true" in SETUP_JS
    assert "if(!visible||retired){if(observed||retired)retire(retired)" in SETUP_JS
    assert "dismissed?'Preparation was dismissed'" in SETUP_JS
    assert "dismissed?'Open the ordinary workspace.'" in SETUP_JS
    assert "if(!open){open=document.createElement('a')" in SETUP_JS
    assert "open.href='/library?lens=all'" in SETUP_JS
    assert (
        "['completed_clean','completed_with_failures'].includes(visible.state)"
        in SETUP_JS
    )
    assert "pollDelay=terminal?5000:1500" in SETUP_JS
    assert "if(terminal)stop()" not in SETUP_JS
    assert "encodeURIComponent(visible.run_id)" in SETUP_JS
    assert "setInterval" not in SETUP_JS
    assert "const poll=async()=>" in SETUP_JS
    assert "finally{if(!stopped)timer=setTimeout(poll,pollDelay)}" in SETUP_JS
    assert ".field[hidden]{display:none}" in css.text
    assert "input[aria-invalid=true]" in css.text
    assert "MutationObserver" not in SETUP_JS
    assert "Writes stay off until you turn them on in Activity." in SETUP_JS
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
            client.post(
                "/api/setup/session",
                json={"password": "wrong"},
                headers={"origin": "https://setup.invalid"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/setup/session",
            json={"password": "wrong"},
            headers={"origin": "https://setup.invalid"},
        ).status_code
        == 429
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
        headers={"origin": "https://setup.invalid"},
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
        content=b"{" + b"x" * 13_184,
        headers={"content-type": "application/json"},
    )
    assert oversized.status_code == 413

    for _ in range(5):
        assert (
            client.post(
                "/api/setup/session",
                json={"password": "wrong"},
                headers={"origin": "https://setup.invalid"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/setup/session",
            json={"password": "wrong"},
            headers={"origin": "https://setup.invalid"},
        ).status_code
        == 429
    )


def test_maximum_setup_password_pair_reaches_owner_claim() -> None:
    client, state, _connection = _client()
    password = "\0" * 1_024

    # The largest token the request model admits, escaped sixfold, still
    # reaches the token check rather than the transport limit.
    refused = _claim(client, password, "\0" * 128)
    assert refused.status_code == 403
    assert refused.json() == {"detail": {"error": "claim_token"}}

    response = _claim(client, password)

    assert response.status_code == 200
    assert state.document.claimed


@pytest.mark.parametrize(
    ("password", "status"),
    (
        ("a" * 11, 422),
        ("a" * 12, 200),
        ("\u00e9" * 11, 422),
        ("\u00e9" * 12, 200),
        ("\U0001f600" * 11, 422),
        ("\U0001f600" * 12, 200),
        ("\u20ac" * 342, 422),
    ),
    ids=(
        "eleven-characters",
        "twelve-characters",
        "eleven-characters-in-twenty-two-bytes",
        "twelve-two-byte-characters",
        "eleven-astral-characters-in-forty-four-bytes",
        "twelve-astral-characters",
        "over-the-byte-bound",
    ),
)
def test_a_new_owner_password_needs_twelve_characters_within_1024_bytes(
    password: str, status: int
) -> None:
    client, state, _connection = _client()

    response = _claim(client, password)

    assert response.status_code == status
    if status == 200:
        assert state.document.claimed
        return
    # The fixed envelope names no value, the claim is untouched and the token
    # still claims once the owner chooses a longer password.
    assert response.json() == {"detail": {"error": "invalid_password"}}
    assert not state.document.claimed and state.claim_attempts == 0
    assert _claim(client, "b" * 12).status_code == 200


def test_an_established_short_owner_password_still_signs_in() -> None:
    client, state, _connection = _client()
    assert _claim(client).status_code == 200
    # An owner whose verifier predates the length rule (#1384).
    state.owner_verifier = PasswordHasher().hash("short")

    response = client.post(
        "/api/setup/session",
        json={"password": "short"},
        headers={"origin": "https://setup.invalid"},
    )

    assert response.status_code == 200


def test_a_reloading_owner_retires_their_oldest_nonce_instead_of_being_refused() -> (
    None
):
    client, state, _connection = _client()

    issued = [client.get("/api/setup/claim") for _ in range(11)]

    # A reload replaces the cookie without spending the nonce it held, so the
    # eleventh page load retires the oldest rather than refusing the owner.
    assert [response.status_code for response in issued] == [200] * 11
    stale = _claim(client, nonce=issued[0])
    assert stale.status_code == 403
    assert stale.json() == {"detail": {"error": "csrf"}}
    assert _claim(client, nonce=issued[10]).status_code == 200
    assert state.document.claimed


def test_one_client_cannot_exhaust_the_claim_nonce_pool() -> None:
    client, _state, _connection = _client()
    for _ in range(15):
        assert client.get("/api/setup/claim").status_code == 200
    others = [
        TestClient(
            client.app, base_url="https://setup.invalid", client=(f"192.0.2.{host}", 1)
        )
        for host in range(20, 30)
    ]
    # However often it asks, one client holds only its ten, so nine more clients
    # fill the unchanged global pool of 100 and a tenth is refused for the pool.
    for other in others[:9]:
        for _ in range(10):
            assert other.get("/api/setup/claim").status_code == 200

    refused = others[9].get("/api/setup/claim")

    assert refused.status_code == 503
    assert refused.json() == {"detail": {"error": "unavailable"}}
    assert "192.0.2." not in refused.text
    # A client already at its share still renews within it at a full pool.
    assert client.get("/api/setup/claim").status_code == 200


@pytest.mark.parametrize(
    ("first", "second"),
    (
        ("2001:db8:1:2::a", "2001:db8:1:2:ffff::b"),
        ("::ffff:192.0.2.40", "192.0.2.40"),
    ),
    ids=("one-ipv6-64", "mapped-and-plain-ipv4"),
)
def test_one_client_holds_one_nonce_share_across_its_addresses(
    first: str, second: str
) -> None:
    client, _state, _connection = _client()
    holders = [
        TestClient(client.app, base_url="https://setup.invalid", client=(peer, 1))
        for peer in (first, second, "2001:db8:1:3::a")
    ]
    issued = [holders[0].get("/api/setup/claim") for _ in range(10)]
    for _ in range(10):
        assert holders[2].get("/api/setup/claim").status_code == 200

    # The second address renews the first one's share, retiring its oldest
    # nonce; another /64's ten retired none of them.
    assert holders[1].get("/api/setup/claim").status_code == 200
    assert _claim(holders[0], nonce=issued[0]).status_code == 403
    assert _claim(holders[0], nonce=issued[1]).status_code == 200


def test_first_owner_claim_requires_the_deployment_claim_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired: list[str] = []
    client, state, _connection = _client(
        retire_claim_token=lambda: retired.append("retired")
    )
    assert state.sessions is not None
    before = state.document
    hashed: list[str] = []
    original_hash = PasswordHasher.hash

    def counted_hash(self: PasswordHasher, password: str | bytes, **kwargs: Any) -> str:
        hashed.append("argon2")
        return original_hash(self, password, **kwargs)

    monkeypatch.setattr(PasswordHasher, "hash", counted_hash)

    for token in (None, "", "wrong-token", _CLAIM_TOKEN + "x", _CLAIM_TOKEN[:-1]):
        refused = _claim(client, claim_token=token)
        assert refused.status_code == 403
        assert refused.json() == {"detail": {"error": "claim_token"}}
        assert "set-cookie" not in refused.headers

    # A refusal records nothing, runs no Argon2, leaves the token valid and
    # owes the login throttle nothing.
    assert state.document == before
    assert state.document.claim_token_digest == claim_token_digest(_CLAIM_TOKEN)
    assert state.claim_attempts == 0
    assert hashed == []
    assert state.sessions.failures == []
    assert retired == []

    # The refused request consumed its nonce, so the same nonce cannot carry a
    # corrected token; a fresh nonce can.
    nonce = client.get("/api/setup/claim")
    cookie = nonce.headers["set-cookie"].split(";", 1)[0]
    body = {
        "password": "correct horse battery staple",
        "confirmation": "correct horse battery staple",
        "expected_revision": nonce.json()["revision"],
    }
    headers = {
        "cookie": cookie,
        "origin": "https://setup.invalid",
        "x-setup-nonce": nonce.json()["nonce"],
    }
    typo = client.post(
        "/api/setup/session",
        json={**body, "claim_token": _CLAIM_TOKEN.upper()},
        headers=headers,
    )
    assert typo.json() == {"detail": {"error": "claim_token"}}
    reused = client.post(
        "/api/setup/session",
        json={**body, "claim_token": _CLAIM_TOKEN},
        headers=headers,
    )
    assert reused.status_code == 403
    assert reused.json() == {"detail": {"error": "csrf"}}
    assert not state.document.claimed

    claimed = _claim(client)

    assert claimed.status_code == 200
    assert state.document.claimed
    assert state.document.claim_token_digest is None
    assert hashed == ["argon2"]
    assert retired == ["retired"]
    # A retry after the claim lands in the claimed branch and needs no token.
    assert (
        client.post(
            "/api/setup/session",
            json={"password": "correct horse battery staple"},
            headers={"origin": "https://setup.invalid"},
        ).status_code
        == 200
    )
    assert retired == ["retired"]


def test_concurrent_correct_claim_loser_still_receives_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, state, _connection = _client()
    original = state.claim_owner

    def lose_the_race(**kwargs: Any) -> SetupStateDocument:
        # A competing request with the same token commits first; this request
        # passed its pre-check against the unclaimed document it read.
        original(**kwargs)
        return original(**kwargs)

    monkeypatch.setattr(state, "claim_owner", lose_the_race)

    response = _claim(client)

    assert response.status_code == 409
    assert response.json() == {"detail": {"error": "claimed"}}
    assert state.document.claimed and state.claim_attempts == 2


def test_setup_claim_token_never_reaches_a_response_or_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, state, _connection = _client()
    caplog.set_level("DEBUG")
    responses = []

    claim = client.get("/api/setup/claim")
    assert set(claim.json()) == {"claimed", "revision", "nonce"}
    responses.append(claim)
    for body in (
        # Each of these is refused by request validation, whose default answer
        # would echo the submitted input back.
        {"password": _CLAIM_TOKEN * 30, "claim_token": _CLAIM_TOKEN},
        {"password": "x", "claim_token": _CLAIM_TOKEN * 3},
        {"password": "x", "claim_token": [_CLAIM_TOKEN]},
        {"password": "x", "unexpected": _CLAIM_TOKEN},
    ):
        refused = client.post(
            "/api/setup/session",
            json=body,
            headers={"origin": "https://setup.invalid"},
        )
        assert refused.status_code == 422
        assert refused.json() == {"detail": {"error": "invalid_request"}}
        responses.append(refused)
    responses.append(_claim(client, claim_token=_CLAIM_TOKEN[::-1]))
    responses.append(_claim(client))
    assert responses[-1].status_code == 200
    responses.append(
        client.get(
            "/api/setup/state",
            headers={"cookie": _session_cookie(responses[-1])},
        )
    )

    digest = claim_token_digest(_CLAIM_TOKEN)
    for response in responses:
        exposed = response.text + repr(dict(response.headers))
        assert _CLAIM_TOKEN not in exposed and _CLAIM_TOKEN[::-1] not in exposed
        assert digest not in exposed
    assert _CLAIM_TOKEN not in caplog.text and digest not in caplog.text
    assert _CLAIM_TOKEN not in repr(state.document)


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
        client_digest=owner_password_binding(verifier)[2],
    )
    client = TestClient(
        create_setup_app(sessions, _FailingState(), verifier),
        raise_server_exceptions=False,
        base_url="https://testserver",
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
        "img-src 'self'; font-src 'self'; connect-src 'self'; form-action 'self'; "
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

    client.cookies.clear()  # This assertion deliberately sends no owner session.
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
        client_digest=owner_password_binding(verifier)[2],
    )
    first_client = TestClient(
        create_setup_app(
            first_sessions,
            state,
            verifier,
            connection,
            "https://setup.invalid",
        ),
        base_url="https://setup.invalid",
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
        client_digest=owner_password_binding(verifier)[2],
    )
    resumed_client = TestClient(
        create_setup_app(
            resumed_sessions,
            state,
            verifier,
            connection,
            "https://setup.invalid",
        ),
        base_url="https://setup.invalid",
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
    # The providers step reads the libraries too, to suggest what fits them.
    suggestions = client.get(
        "/api/setup/libraries",
        params={"expected_revision": connect.json()["revision"]},
        headers={"cookie": headers["cookie"]},
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

    assert suggestions.status_code == 200
    assert suggestions.json() == discovery.json()
    assert discovery.json() == {
        "libraries": [
            {
                "library_id": "books",
                "name": "Books",
                "media_kind": "book",
                "audiobooks_only": False,
                "service_path": None,
                "service_path_state": "unavailable",
            },
            {
                "library_id": "podcasts",
                "name": "Podcasts",
                "media_kind": "unsupported",
                "audiobooks_only": False,
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
    assert "field(`New name for ${item.name}`,displayName)" in SETUP_JS
    assert "display_name:row.querySelector('input[name^=display-]').value" in SETUP_JS
    assert "row.dataset.name=stableName" in SETUP_JS
    assert "name:row.dataset.name" in SETUP_JS
    assert "Preparation will begin in the workspace." in SETUP_JS
    assert "className='provider-access'" in SETUP_JS
    assert "['inventaire','inventaire']" in SETUP_JS
    # Nothing is ticked for the owner; suggestions only name the libraries.
    assert "enabled.checked=" not in SETUP_JS
    assert "Suggested for ${suggested[group].join(', ')}" in SETUP_JS
    assert "cover_enabled:row.adapter==='inventaire'" in SETUP_JS
    assert (
        "`${selectedLibrary?shown(selectedLibrary):humanName(item.library)}:"
        in SETUP_JS
    )
    assert "field(`${shown(library)} service path`,path)" in SETUP_JS
    # One name per library: the recorded display name the summary also uses.
    assert (
        "return library?.display_name||evidence?.name||"
        "humanName(library?.name||'library')" in SETUP_JS
    )
    assert "state.mounts.length?'Retest mounts':'Check mounts'" in SETUP_JS
    # Each comparison leads with the library and one short state, and only a
    # failing state carries its remedy.
    assert "const mountState={matched:'Matched',missing:'No mapping'," in SETUP_JS
    assert "const remedy=mountRemedy[item.state];line.className=remedy?" in SETUP_JS
    assert "label.className='mount-state'" in SETUP_JS
    assert "mountCopy" not in SETUP_JS
    assert (
        "const codexState=blocked?access.codex:"
        "(codexTerminal||access.codex)" in SETUP_JS
    )
    assert "codexTerminal=device;await render()" in SETUP_JS
    assert "Check existing sign-in" in SETUP_JS
    assert "check.disabled=blocked" in SETUP_JS
    assert "/api/setup/reviewer/codex/recheck" in SETUP_JS
    assert "reviewer_access_stale:'Reviewer access changed" in SETUP_JS
    assert "invalid_key:'OpenRouter rejected this key." in SETUP_JS


def test_first_run_states_its_rules_transport_and_recoveries_without_raw_codes() -> (
    None
):
    """Pin the #1008 disclosure, association and recovery surface.

    These are substring pins on the shipped asset because the behaviour they
    guard is proved in `frontend/tests/setup.first-run.test.ts` against the
    same string; what stays here is the copy a person reads and the exact
    seams a later `.replace` layer could silently drop.
    """
    # UX-002: the rules and the reveal control come from the vocabulary.
    assert "settingsVocabulary.owner_password.minimum_characters" in SETUP_JS
    assert "settingsVocabulary.owner_password.maximum_bytes" in SETUP_JS
    assert "minimum_bytes" not in SETUP_JS
    assert "invalid_password:settingsVocabulary.errors.owner_password" in SETUP_JS
    # #1384: the length is counted in characters, as the server counts it, and
    # the byte ceiling stays a byte ceiling.
    assert "const length=[...password.value].length" in SETUP_JS
    assert "new TextEncoder().encode(password.value).length" in SETUP_JS
    # One sentence per refusal: empty, mismatched and out-of-range are distinct.
    assert (
        "error(form,length===0?'invalid_password_empty':long?'invalid_password_long'"
        ":'invalid_password',password)" in SETUP_JS
    )
    assert (
        "invalid_password_long:settingsVocabulary.errors.owner_password_long"
        in SETUP_JS
    )
    assert "error(form,'invalid_password_mismatch',confirmation)" in SETUP_JS
    assert "settingsVocabulary.errors.owner_password_mismatch" in SETUP_JS
    assert "settingsVocabulary.errors.owner_password_empty" in SETUP_JS
    assert "toggle.setAttribute('aria-pressed','false')" in SETUP_JS
    assert "control.type=reveal?'text':'password'" in SETUP_JS
    assert "secretField('Owner password',password,'the owner password')" in SETUP_JS
    # #1384: the rules follow the password field they govern, are linked to it,
    # and are not stated under the claim-token field.
    assert (
        "form.append(secretField('Owner password',password,'the owner password'));"
        "if(!claim.claimed){const list=rules([`At least "
        "${settingsVocabulary.owner_password.minimum_characters} characters. Any "
        "characters are allowed, including spaces.`,'Both fields must match "
        "exactly.'," in SETUP_JS
    )
    assert (
        "list.id='setup-password-rules';list.children[1].id='setup-password-match';"
        "password.dataset.hint=list.id;describe(password,false);form.append(list)}"
        in SETUP_JS
    )
    assert (
        "confirmation.dataset.hint='setup-password-match';describe(confirmation,false)"
        in SETUP_JS
    )
    # ADR-0086: the unclaimed form asks for the deployment-held token, says
    # where it is and that a restart replaces it, and recovers a typo in place.
    assert (
        "form.append(source,secretField('Setup claim token',claimToken,"
        "'the setup claim token'))" in SETUP_JS
    )
    assert (
        "Copy the setup claim token from the container log, or from "
        "private/setup-claim-token on the data volume. A restart replaces the "
        "token, so use the newest one." in SETUP_JS
    )
    assert "claim_token:claimToken.value.trim()" in SETUP_JS
    assert (
        "if(claimToken&&['claim_token','invalid_password'].includes(e.message))"
        "{try{const fresh=await api('/api/setup/claim');" in SETUP_JS
    )
    assert "claim.nonce=fresh.nonce;claim.revision=fresh.revision" in SETUP_JS
    # #1384: a refused password spent the nonce too, so it is refreshed the
    # same way and the refusal lands on the password, not on the token.
    assert (
        "error(form,e.message,e.message==='claim_token'?claimToken:password);return}"
        in SETUP_JS
    )
    assert "claim_token:'That setup claim token was not accepted." in SETUP_JS
    assert "The first visitor creates the only owner password" not in SETUP_JS
    assert "toggle.setAttribute('aria-label',`Show ${subject}`)" in SETUP_JS
    assert (
        "secretField('Audiobookshelf API key',key,'the Audiobookshelf API key')"
        in SETUP_JS
    )

    # UX-001: one alert node, associated with the control it refuses.
    assert "completed_steps:[]};drawLedger()" in SETUP_JS
    assert "node.id='setup-error'" in SETUP_JS
    assert "control.setAttribute('aria-invalid','true')" in SETUP_JS
    assert "describe(control,true)" in SETUP_JS
    assert "`${refused?'setup-error ':''}${control.dataset.hint||''}`" in SETUP_JS
    # #1384: the refusal sits with the field it refuses, not above the form.
    assert (
        "const anchor=control?.closest('.field');if(anchor)anchor.after(node);"
        "else form.prepend(node)" in SETUP_JS
    )
    assert "control.addEventListener('input',()=>{" in SETUP_JS
    assert "{once:true})" in SETUP_JS

    # ADR-0072: both browser modes are stated, and the endpoint scheme is explained.
    assert "const insecureTransport=()=>location.protocol!=='https:'" in SETUP_JS
    assert "travel in clear text across this network" in SETUP_JS
    assert "https that your browser already trusts" in SETUP_JS
    assert "never skips that check, never follows a redirect with your key" in SETUP_JS
    assert "preserve TLS verification" not in SETUP_JS

    # UX-004/005: every named state carries a safe cause and a remedy.
    assert "failure.status=response.status" in SETUP_JS
    assert (
        "const refusal=e=>e.status===401?'session_expired':"
        "e.status===403&&e.message==='csrf'?'stale':e.message" in SETUP_JS
    )
    assert "const code=refusal(e)" in SETUP_JS
    for code in (
        "invalid_endpoint",
        "unavailable",
        "activation_failed",
        "invalid_mounts",
        "invalid_providers",
        "session_expired",
        "invalid_credentials",
        "throttled",
    ):
        assert f"{code}:'" in SETUP_JS
    # The origin refusal is authored once, in the shared vocabulary, because a
    # refused navigation is answered in Python before any asset can load.
    assert "invalid_origin:settingsVocabulary.errors.invalid_origin" in SETUP_JS
    # An address or contact email the browser itself rejects is refused in the
    # page's own words instead of a silent native bubble, and a stray space
    # never reaches the probe.
    assert "form.noValidate=true" in SETUP_JS
    assert (
        "const address=endpoint.value.trim();if(address!==endpoint.value)"
        "endpoint.value=address;if(!address||!endpoint.checkValidity()||"
        "!bareOrigin(address))"
        "{error(form,'invalid_endpoint',endpoint);return}" in SETUP_JS
    )
    assert "endpoint:address,key:key.value" in SETUP_JS
    # A key the contract cannot carry is refused on the key, and a refusal
    # that is about neither field marks neither.
    assert (
        "if(!settingsSecretValid(key.value)){error(form,'invalid_secret',key);return}"
        in SETUP_JS
    )
    assert (
        "control:(code,reached)=>code==='unauthorized'?key:"
        "reached||addressCodes.includes(code)?endpoint:null,address" in SETUP_JS
    )
    assert (
        "const addressCodes=['invalid_endpoint','invalid_origin','name_resolution',"
        "'refused','tls','timeout','redirect','malformed','oversize']" in SETUP_JS
    )
    assert (
        "const emailInvalid=control=>!control.checkValidity()||"
        "!settingsOpenLibraryEmailValid(control.value)" in SETUP_JS
    )
    # Every reopen control names its own step, and the prerequisites stop
    # applying once preparation has started.
    assert "change.setAttribute('aria-label',`Change ${label}`)" in SETUP_JS
    assert (
        "document.querySelector('.prerequisites')?.classList.add('complete')"
        in SETUP_JS
    )
    assert "canonical forwarded host and protocol pair" in SETUP_JS
    assert "Mechanical Librarian never skips verification." in SETUP_JS
    assert (
        "reviewer_unconfigured:'The selected reviewer is no longer ready. "
        "Check its access and select it again, or continue without a reviewer.'"
        in SETUP_JS
    )

    # UX-006: per-provider relevance and disambiguated credential labels.
    assert "settingsVocabulary.provider_contributions[adapter]" in SETUP_JS
    assert "secretField(`${label} API key`,key,`the ${label} API key`)" in SETUP_JS
    assert "field(`${label} contact email`,email)" in SETUP_JS
    assert (
        "settingsVocabulary.cover_switch_provider_adapters.includes(adapter)"
        in SETUP_JS
    )
    assert "stays off until you turn it on in Settings." in SETUP_JS

    # UX-008/009: the entered service paths survive a refused comparison.
    assert "path.value=mountDraft&&library.name in mountDraft" in SETUP_JS
    assert "mountDraft=draft;" in SETUP_JS
    assert "if(state.next_step!=='mounts')mountDraft=null" in SETUP_JS
    assert "the path inside that service" in SETUP_JS
    assert "Correct the Audiobookshelf service path and compare again" in SETUP_JS
    assert "Audiobookshelf reported no folder path to compare." in SETUP_JS

    # UX-013: the kickoff summary reuses one reopen seam and hides no secret.
    assert "change.addEventListener('click',()=>reopen(step))" in SETUP_JS
    assert "change.addEventListener('click',()=>reopen(id))" in SETUP_JS
    assert "'Change the Audiobookshelf address'" in SETUP_JS
    assert "'Change the cover reviewer'" in SETUP_JS
    assert "summary.className='summary'" in SETUP_JS
    assert "if(submit.disabled)return;submit.disabled=true" in SETUP_JS
    assert "if(mutating)return;mutating=true" in SETUP_JS
    assert "already=(await api('/api/setup/claim')).claimed" in SETUP_JS
    # One refusal listener at a time, and no draft discarded for a change the
    # server refused.
    assert "refusalGuard=new AbortController()" in SETUP_JS
    # A refusal carried by a fresh render keeps the focus it asked for: the
    # card's own frame would otherwise move it to the heading.
    assert (
        "control.focus();const settle=requestAnimationFrame(()=>{if("
        "control.isConnected)control.focus({preventScroll:true})})" in SETUP_JS
    )
    # That frame never takes focus back from another field the owner has moved
    # to since, or typing there lands in the refused one.
    assert (
        "form.addEventListener('focusin',event=>{if(event.target!==control&&"
        "event.target.matches('input,select,textarea'))cancelAnimationFrame("
        "settle)},{signal:refusalGuard.signal})" in SETUP_JS
    )
    assert "{once:true,signal:refusalGuard.signal}" in SETUP_JS
    assert "mutation:true});mountDraft=null;confirmation=null;await load()" in SETUP_JS
    assert "for(const action of actions)action.disabled=true" in SETUP_JS
    assert "submit.disabled=false;if(e.status===403&&e.message==='csrf')" in SETUP_JS
    assert "error(form,refusal(e))" in SETUP_JS


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
    assert "['audible','audible_catalog']" in SETUP_JS
    assert "['audnexus','audnexus']" in SETUP_JS
    assert "['audiosilo','audiosilo']" in SETUP_JS
    assert "['itunes','itunes_search']" in SETUP_JS
    # The step names a provider from the one shared label map (#967), so no
    # spec row, account link, or heading carries a display name of its own.
    assert (
        "for(const [name,adapter] of specs){"
        "const label=settingsVocabulary.provider_labels[adapter];" in SETUP_JS
    )
    _, separator, authored = SETUP_JS.partition(";const settingsTextValid=")
    assert separator
    for label in PROVIDER_LABELS.values():
        assert label not in authored, label
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


@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
def test_setup_transport_claim_cookie_deletion_and_security(mode: str) -> None:
    client, _state, _connection = _client(mode=mode)
    origin = "http://setup.invalid" if mode == "http" else "https://setup.invalid"
    secure = mode != "http"
    with client:
        nonce = client.get("/api/setup/claim")
        assert nonce.status_code == 200
        nonce_cookie = nonce.headers["set-cookie"]
        assert ("; Secure" in nonce_cookie) is secure
        assert "HttpOnly" not in nonce_cookie
        login = client.post(
            "/api/setup/session",
            json={
                "password": "correct horse battery staple",
                "confirmation": "correct horse battery staple",
                "expected_revision": nonce.json()["revision"],
                "claim_token": _CLAIM_TOKEN,
            },
            headers={
                "origin": origin,
                "cookie": nonce_cookie.split(";", 1)[0],
                "x-setup-nonce": nonce.json()["nonce"],
            },
        )
        assert login.status_code == 200
        cookies = login.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert all(("; Secure" in cookie) is secure for cookie in cookies)
        assert "Max-Age=0" in cookies[0] and "ml_setup_nonce=" in cookies[0]
        assert "HttpOnly" in cookies[1] and "SameSite=strict" in cookies[1]
        client.headers["cookie"] = _session_cookie(login)
        assert client.get("/api/setup/state").status_code == 200
        for path, expected in [("/health/ready", 200), ("/missing", 404)]:
            response = client.get(path)
            assert response.status_code == expected
            assert ("strict-transport-security" in response.headers) is secure
        overflow = client.post(
            "/api/setup/session", content=iter([b"x" * (2 * 1024 * 6 + 128 * 6 + 129)])
        )
        assert overflow.status_code == 413
        assert ("strict-transport-security" in overflow.headers) is secure
        denied = client.get(
            "/api/setup/state",
            headers={"host": "evil.invalid", "x-forwarded-host": "evil.invalid"},
        )
        assert denied.status_code == 400
        assert ("strict-transport-security" in denied.headers) is secure


def test_refused_navigation_reads_the_origin_remedy_and_callers_keep_json() -> None:
    """A browser sent to the wrong host reads the remedy it can act on (#1008).

    The wizard's own script and stylesheet are refused on that origin too, so a
    JSON body renders as the whole page and the recovery sentence the loaded
    wizard would have shown is never reachable. The refusal keeps its status,
    its headers and its JSON shape for every caller that parses one.
    """
    client, _state, _connection = _client()
    wrong_host = {"host": "evil.invalid", "x-forwarded-host": "evil.invalid"}
    # One authored sentence reaches both the refused navigation and the loaded
    # wizard, through the same shared vocabulary.
    remedy = "canonical forwarded host and protocol pair"
    assert remedy in SETUP_JS

    for headers in (
        # A real Chromium navigation, which states no quality for HTML at all.
        {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        {"accept": "*/*", "sec-fetch-dest": "document"},
        # Preference is stated by quality value, never by listing order.
        {"accept": "application/json;q=0.8, text/html;q=0.9"},
        {"accept": "text/html;q=0.9, application/json;q=0.8"},
    ):
        navigation = client.get("/", headers={**wrong_host, **headers})
        assert navigation.status_code == 400
        assert navigation.headers["content-type"].startswith("text/html")
        assert navigation.text == SETUP_ORIGIN_REFUSAL_HTML
        assert remedy in navigation.text
        assert "invalid_origin" not in navigation.text
        # Nothing this page needs can load from a refused origin, and nothing
        # about the owner's session may appear on it.
        assert "<script" not in navigation.text
        assert "<style" not in navigation.text
        assert "src=" not in navigation.text and "href=" not in navigation.text
        # It carries no stylesheet, so it follows the reader's own theme
        # instead of arriving stark white.
        assert '<meta name="color-scheme" content="light dark">' in navigation.text
        assert "csrf" not in navigation.text.lower()
        assert "set-cookie" not in navigation.headers
        assert navigation.headers["cache-control"] == "private, no-store"
        assert "content-security-policy" in navigation.headers

    for headers in (
        {"accept": "application/json"},
        # Equal quality is no preference, and a wildcard states nothing about
        # HTML in particular; both keep the shape a caller can parse.
        {"accept": "application/json, text/html"},
        {"accept": "text/html, application/json"},
        {"accept": "*/*"},
        {"accept": "text/*"},
        # `q=0` states that HTML is not acceptable at all.
        {"accept": "text/html;q=0, application/json"},
        {"accept": "text/html;q=0"},
        {},
    ):
        refused = client.get("/api/setup/state", headers={**wrong_host, **headers})
        assert refused.status_code == 400
        assert refused.headers["content-type"].startswith("application/json")
        assert refused.json() == {"detail": {"error": "invalid_origin"}}
    client.close()


@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
def test_claimed_setup_login_still_requires_canonical_origin(mode: str) -> None:
    client, _state, _connection = _client(mode=mode)
    origin = "http://setup.invalid" if mode == "http" else "https://setup.invalid"
    nonce = client.get("/api/setup/claim")
    claimed = client.post(
        "/api/setup/session",
        json={
            "password": "correct horse battery staple",
            "confirmation": "correct horse battery staple",
            "expected_revision": nonce.json()["revision"],
            "claim_token": _CLAIM_TOKEN,
        },
        headers={
            "origin": origin,
            "cookie": nonce.headers["set-cookie"].split(";", 1)[0],
            "x-setup-nonce": nonce.json()["nonce"],
        },
    )
    assert claimed.status_code == 200
    for headers in ({}, {"origin": "https://wrong.invalid"}):
        denied = client.post(
            "/api/setup/session",
            json={"password": "correct horse battery staple"},
            headers=headers,
        )
        assert denied.status_code == 403
        assert "set-cookie" not in denied.headers
    assert (
        client.post(
            "/api/setup/session",
            json={"password": "correct horse battery staple"},
            headers={"origin": origin},
        ).status_code
        == 200
    )
    client.close()


def test_setup_states_a_rate_only_while_preparation_moves() -> None:
    # A finished, blocked or dismissed run has no current rate; printing one
    # read as "0.0 books/min" on a finished run (#1366, #1286).
    guard = (
        "if((visible.state==='starting'||visible.state==='running')"
        "&&typeof visible.throughput_per_minute==='number'){"
    )
    assert guard in SETUP_JS
    assert SETUP_JS.count("books/min") == 1
    assert "if(typeof visible.throughput_per_minute==='number')" not in SETUP_JS


_SETUP_FONTS = {
    "assets/inter-latin-wght-normal-Dx4kXJAl.woff2": b"latin",
    "assets/inter-latin-ext-wght-normal-DO1Apj_S.woff2": b"latin-ext",
}


def test_setup_paints_the_saved_theme_first_and_serves_only_the_workspace_fonts() -> (
    None
):
    state = SimpleNamespace(
        read=lambda _verifier: SetupStateDocument(
            schema_version="setup-state-v4",
            revision=1,
            bootstrap_verifier_digest="0" * 64,
            claim_token_digest=claim_token_digest(_CLAIM_TOKEN),
        )
    )
    with TestClient(
        create_setup_app(
            cast(Any, object()),
            cast(Any, state),
            "",
            react_asset_loader=font_asset_loader(_SETUP_FONTS),
        ),
        base_url="https://testserver",
    ) as client:
        shell = client.get("/")
        assert shell.headers["content-security-policy"] == (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; font-src 'self'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        theme = client.get("/assets/theme.js")
        assert theme.status_code == 200
        assert theme.headers["content-type"].startswith("text/javascript")
        assert theme.text == THEME_JS
        css = client.get("/assets/setup.css").text
        assert css.startswith("@font-face{font-family:Inter;")
        assert css.endswith(SETUP_CSS)
        for path, payload in _SETUP_FONTS.items():
            assert f"src:url(/assets/review-app/{path}) format('woff2')" in css
            font = client.get(f"/assets/review-app/{path}")
            assert font.status_code == 200
            assert font.headers["content-type"] == "font/woff2"
            assert font.content == payload
        # The workspace's shell and scripts stay unreachable before setup ends.
        for path in ("index.html", "assets/index-deadbeef.js", "assets/missing.woff2"):
            assert client.get(f"/assets/review-app/{path}").status_code == 404


def test_setup_without_a_workspace_build_keeps_the_fallback_stack() -> None:
    assert inter_font_faces(()) == ""
    assert inter_font_faces(("assets/inter-latin-wght-normal.woff2",)) == ""


def test_sign_in_and_setup_state_the_workspace_inter_ranges_exactly() -> None:
    styles = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles.css"
    ).read_text(encoding="utf-8")
    workspace = dict(
        re.findall(
            r"url\('\./fonts/([a-z-]+)\.woff2'\) format\('woff2'\); "
            r"unicode-range:([^;]+);",
            styles,
        )
    )
    served = dict(
        re.findall(
            r"assets/([a-z-]+)-[A-Za-z0-9_-]{8}\.woff2\) format\('woff2'\);"
            r"unicode-range:([^}]+)\}",
            inter_font_faces(_SETUP_FONTS),
        )
    )
    assert len(workspace) == 2
    assert served == workspace
