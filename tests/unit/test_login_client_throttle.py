"""Sign-in throttling per resolved client (#1384).

Every test drives the real review application's login route over a real
session database, so the window, both limits and the keyed client digest are
the shipped ones. Peers and forwarded addresses come only from the
documentation ranges.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_web import _LOGIN_WINDOW_SECONDS
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web.app import client_identity
from mechanical_librarian.interfaces.web.review_frontend import (
    LOGIN_CSS,
    LOGIN_JS,
    THEME_JS,
)
from mechanical_librarian.interfaces.web.setup_frontend import SETUP_JS
from tests.support.review_cases import _report
from tests.support.review_web_doubles import FakeReviewRepository
from tests.unit.review_app_fixture import (
    create_transport_test_app,
    font_asset_loader,
)

_PASSWORD = "correct horse"
_ORIGIN = "https://review.test"
_PROXY = "192.0.2.1"
_OWNER = "192.0.2.7"
_ATTACKER = "192.0.2.66"


@dataclass
class _Throttle:
    app: FastAPI
    store: SQLiteReviewSessionRepository
    database: Path
    clients: dict[str, TestClient] = field(default_factory=dict)

    def login(
        self,
        peer: str,
        password: str,
        forwarded: str | list[str] | None = None,
    ) -> int:
        client = self.clients.get(peer)
        if client is None:
            client = TestClient(self.app, base_url=_ORIGIN, client=(peer, 50_000))
            self.clients[peer] = client
        headers = [("origin", _ORIGIN)] + [
            ("x-forwarded-for", value)
            for value in (
                [forwarded] if isinstance(forwarded, str) else forwarded or []
            )
        ]
        return client.post(
            "/api/v1/session/login", headers=headers, json={"password": password}
        ).status_code

    def fail(self, peer: str, times: int, forwarded: str | None = None) -> None:
        for _ in range(times):
            assert self.login(peer, "wrong", forwarded) == 401


@pytest.fixture
def throttle(tmp_path: Path) -> Iterator[_Throttle]:
    owner = tmp_path / "owner"
    owner.mkdir(mode=0o700)
    hash_path = owner / "owner.hash"
    hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    # The session database gets a directory of its own (testing-strategy.md).
    sessions = tmp_path / "sessions"
    sessions.mkdir(mode=0o700)
    database = sessions / "review-sessions.sqlite"
    store = SQLiteReviewSessionRepository.open(database)
    report = _report()
    app = create_transport_test_app(
        FakeReviewRepository(
            ReviewCase.create(evaluate_report(report), report), report
        ),
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            trusted_proxy_ips=(_PROXY,),
            canonical_origin=_ORIGIN,
            owner_password_hash_file=str(hash_path),
            session_lifetime_seconds=600,
        ),
        session_store=store,
        clock=lambda: 1_000,
    )
    context = _Throttle(app, store, database)
    try:
        yield context
    finally:
        for client in context.clients.values():
            client.close()
        store.close()


def test_a_throttled_client_does_not_lock_the_owner_out(throttle: _Throttle) -> None:
    throttle.fail(_ATTACKER, 5)
    assert throttle.login(_ATTACKER, _PASSWORD) == 429

    assert throttle.login(_OWNER, _PASSWORD) == 200
    # The owner's sign-in settles only the owner's debt.
    assert throttle.login(_ATTACKER, _PASSWORD) == 429

    rows = throttle.store._connection.execute(
        "SELECT * FROM review_login_failures"
    ).fetchall()
    assert len(rows) == 5
    throttle.store.close()
    stored = throttle.database.read_bytes()
    assert _ATTACKER.encode() not in stored and _OWNER.encode() not in stored
    throttle.store = SQLiteReviewSessionRepository.open(throttle.database)


def test_a_sign_in_clears_only_the_succeeding_clients_failures(
    throttle: _Throttle,
) -> None:
    throttle.fail(_OWNER, 4)
    throttle.fail(_ATTACKER, 5)

    assert throttle.login(_OWNER, _PASSWORD) == 200

    throttle.fail(_OWNER, 5)
    assert throttle.login(_OWNER, _PASSWORD) == 429
    assert throttle.login(_ATTACKER, _PASSWORD) == 429


def test_the_global_ceiling_needs_twenty_clients_and_then_holds(
    throttle: _Throttle,
) -> None:
    for host in range(100, 120):
        throttle.fail(f"192.0.2.{host}", 5)

    assert throttle.login("192.0.2.120", _PASSWORD) == 429
    assert throttle.login("2001:db8:120::1", _PASSWORD) == 429


def test_an_untrusted_peers_forwarded_for_is_ignored(throttle: _Throttle) -> None:
    # Claiming the owner's address neither escapes this client's own debt nor
    # spends the owner's.
    throttle.fail(_ATTACKER, 5, forwarded=_OWNER)
    assert throttle.login(_ATTACKER, _PASSWORD, forwarded="192.0.2.99") == 429

    assert throttle.login(_OWNER, _PASSWORD) == 200


def test_a_trusted_proxys_rightmost_forwarded_entry_is_the_client(
    throttle: _Throttle,
) -> None:
    throttle.fail(_PROXY, 5, forwarded=f"{_OWNER}, {_ATTACKER}")
    assert throttle.login(_PROXY, _PASSWORD, f"{_OWNER}, {_ATTACKER}") == 429

    assert throttle.login(_PROXY, _PASSWORD, f"{_ATTACKER}, {_OWNER}") == 200
    # The proxy's own address is not a client either.
    assert throttle.login(_ATTACKER, _PASSWORD) == 429


def test_unresolvable_forwarding_shares_one_throttled_key(throttle: _Throttle) -> None:
    for forwarded in (None, "not-an-address", f"{_ATTACKER}:443", "", [_OWNER, _OWNER]):
        assert throttle.login(_PROXY, "wrong", forwarded) == 401
    assert throttle.login(_PROXY, _PASSWORD, f"{_OWNER}, ") == 429

    assert throttle.login(_PROXY, _PASSWORD, _OWNER) == 200


def test_addresses_in_one_ipv6_64_share_a_throttle(throttle: _Throttle) -> None:
    throttle.fail("2001:db8:1:2::a", 5)

    assert throttle.login("2001:db8:1:2:ffff:ffff:ffff:fffe", _PASSWORD) == 429
    assert throttle.login(_PROXY, _PASSWORD, "2001:db8:1:2::c") == 429


def test_addresses_in_different_ipv6_64s_do_not_share_a_throttle(
    throttle: _Throttle,
) -> None:
    throttle.fail("2001:db8:1:2::a", 5)

    assert throttle.login("2001:db8:1:3::a", _PASSWORD) == 200
    assert throttle.login(_PROXY, _PASSWORD, "2001:db8:1:1:ffff::a") == 200


def test_a_mapped_ipv4_address_is_its_plain_ipv4_client(throttle: _Throttle) -> None:
    throttle.fail(f"::ffff:{_ATTACKER}", 5)

    assert throttle.login(_ATTACKER, _PASSWORD) == 429
    assert throttle.login(_PROXY, _PASSWORD, _ATTACKER) == 429
    assert throttle.login(f"::ffff:{_OWNER}", _PASSWORD) == 200


def _request(peer: str | None, *forwarded: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", value.encode()) for value in forwarded],
            "client": None if peer is None else (peer, 50_000),
        }
    )


@pytest.mark.parametrize(
    ("peer", "forwarded", "identity"),
    (
        (_ATTACKER, (_OWNER,), f"client:{_ATTACKER}"),
        ("2001:db8:1:2::66", (), "client:2001:db8:1:2::/64"),
        ("2001:db8:1:3::66", (), "client:2001:db8:1:3::/64"),
        (f"::ffff:{_ATTACKER}", (), f"client:{_ATTACKER}"),
        (_PROXY, (f"{_ATTACKER}, {_OWNER}",), f"client:{_OWNER}"),
        (_PROXY, ("2001:DB8:0:0:1::7",), "client:2001:db8::/64"),
        (_PROXY, (f"::FFFF:{_OWNER}",), f"client:{_OWNER}"),
        (_PROXY, (), "unresolved"),
        (_PROXY, (_OWNER, _OWNER), "unresolved"),
        (_PROXY, ("not-an-address",), "unresolved"),
        (_PROXY, (f"{_OWNER}:443",), "unresolved"),
        (None, (_OWNER,), "unresolved"),
    ),
    ids=(
        "untrusted-peer-ignores-forwarding",
        "untrusted-ipv6-peer-is-its-64",
        "another-64-is-another-client",
        "untrusted-mapped-ipv4-peer",
        "trusted-rightmost-entry",
        "trusted-ipv6-entry-is-its-64",
        "trusted-mapped-ipv4-entry",
        "trusted-missing",
        "trusted-two-lines",
        "trusted-unparsable",
        "trusted-with-port",
        "no-peer",
    ),
)
def test_client_identity_comes_only_from_trusted_resolution(
    peer: str | None, forwarded: tuple[str, ...], identity: str
) -> None:
    assert client_identity(_request(peer, *forwarded), (_PROXY,)) == identity


def test_throttled_sign_in_copy_states_the_shipped_window() -> None:
    """Sign-in and setup tell a throttled client the real wait (#1287)."""
    # A changed window fails here until both sentences are rewritten for it.
    minutes = {300: "five"}[_LOGIN_WINDOW_SECONDS]
    copy = (
        f"Too many sign-in attempts from this device. Wait {minutes} minutes, "
        "then try again."
    )
    assert f"429: '{copy}'" in LOGIN_JS
    assert f"throttled:'{copy}'" in SETUP_JS


_FONTS = {
    "assets/inter-latin-wght-normal-Dx4kXJAl.woff2": b"latin",
    "assets/inter-latin-ext-wght-normal-DO1Apj_S.woff2": b"latin-ext",
}


def test_sign_in_paints_the_saved_theme_first_and_uses_the_workspace_inter(
    tmp_path: Path,
) -> None:
    hash_path = tmp_path / "owner.hash"
    hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    report = _report()
    app = create_transport_test_app(
        FakeReviewRepository(
            ReviewCase.create(evaluate_report(report), report), report
        ),
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=_ORIGIN,
            owner_password_hash_file=str(hash_path),
            session_lifetime_seconds=600,
        ),
        react_asset_loader=font_asset_loader(_FONTS),
    )
    with TestClient(app, base_url=_ORIGIN) as client:
        page = client.get("/login").text
        head = page.split("</head>", 1)[0]
        # No theme is forced on the root; a saved one is applied before paint.
        assert '<html lang="en">' in page
        assert head.index('<script src="/assets/theme.js"></script>') < head.index(
            "/assets/login.css"
        )
        theme = client.get("/assets/theme.js")
        assert theme.status_code == 200
        assert theme.text == THEME_JS
        css = client.get("/assets/login.css").text
        assert css.startswith("@font-face{font-family:Inter;")
        for path in _FONTS:
            assert f"src:url(/assets/review-app/{path}) format('woff2')" in css
        assert css.endswith(LOGIN_CSS)
        font = client.get(f"/assets/review-app/{next(iter(_FONTS))}")
        assert font.status_code == 200
        assert font.headers["content-type"] == "font/woff2"
