from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from tests.support.review_cases import _report
from tests.support.review_web_doubles import FakeReviewRepository
from tests.unit.review_app_fixture import create_transport_test_app


def _write_bundle(root: Path) -> str:
    values = {
        "index.html": (
            b'<!doctype html><html><body><div id="root"></div>'
            b'<script src="/assets/review-app/assets/index-deadbeef.js"></script>'
            b"</body></html>"
        ),
        "assets/index-deadbeef.js": b"window.reviewReady=true;",
        "assets/index-deadbeef.css": b":root{color-scheme:light}",
    }
    records: list[dict[str, object]] = []
    for path, payload in values.items():
        destination = root.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        records.append(
            {
                "kind": "shell" if path == "index.html" else (
                    "style" if path.endswith(".css") else "script"
                ),
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    records.sort(key=lambda item: str(item["path"]))
    identity = hashlib.sha256(
        json.dumps(
            {
                "schema": "review-react-assets-v1",
                "entry": "index.html",
                "assets": records,
            },
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest = {
        "schema": "review-react-assets-v1",
        "build_identity": identity,
        "entry": "index.html",
        "assets": records,
    }
    (root / "review-assets-v1.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return identity


class _Case:
    """The temporary tree, repository and client a `setUp` used to carry."""

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        password_hash = root / "owner.hash"
        password_hash.write_text(
            PasswordHasher().hash("correct horse"), encoding="utf-8"
        )
        os.chmod(password_hash, 0o600)
        report = _report()
        case = ReviewCase.create(evaluate_report(report), report)
        self.case_id = case.review_case_id
        self.repository = FakeReviewRepository(case, report)
        asset_root = root / "assets"
        asset_root.mkdir()
        self.asset_root = asset_root
        self.identity = _write_bundle(asset_root)
        configuration = ReviewWebConfiguration(
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password_hash),
            session_lifetime_seconds=600,
        )
        self.client = TestClient(
            create_transport_test_app(
                self.repository,
                configuration,
                clock=lambda: 1_000,
                react_asset_loader=ReactAssetLoader(asset_root),
            ),
            base_url="https://review.test",
        )
        self.origin = {"Origin": "https://review.test"}

    def close(self) -> None:
        self.client.close()
        self.temporary.cleanup()


@pytest.fixture
def case() -> Iterator[_Case]:
    prepared = _Case()
    try:
        yield prepared
    finally:
        prepared.close()


def _login(case: _Case) -> dict[str, object]:
    response = case.client.post(
        "/api/v1/session/login",
        headers=case.origin,
        json={"password": "correct horse"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_authenticated_routes_share_shell_and_rotate_csrf(case: _Case) -> None:
    login = _login(case)
    paths = (
        "/library",
        "/attention",
        f"/review/{case.case_id}",
        "/policy",
        "/activity",
    )
    normalized: list[str] = []
    tokens: list[str] = []
    for path in paths:
        response = case.client.get(path)
        assert response.status_code == 200, response.text
        assert f'data-build-identity="{case.identity}"' in response.text
        match = re.search(
            r'data-csrf-token="([A-Za-z0-9_-]{32,128})"', response.text
        )
        assert match is not None
        tokens.append(match.group(1))
        normalized.append(
            re.sub(r'data-csrf-token="[A-Za-z0-9_-]+"', "data-csrf-token", response.text)
        )
    assert len(set(normalized)) == 1
    assert len(set(tokens)) == len(paths)
    assert login["csrf_token"] not in tokens


def test_root_and_unknown_review_entry_preserve_client_recovery(case: _Case) -> None:
    root = case.client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"] == "/login"

    _login(case)
    for _ in range(2):
        page = case.client.get("/review/case-1")
        assert page.status_code == 200, page.text
        assert f'data-build-identity="{case.identity}"' in page.text

    missing = case.client.get("/api/v1/review-cases/case-1")
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("retired", ("activity.js", "policy.js", "review.js"))
def test_only_manifest_admitted_non_shell_assets_are_served(
    case: _Case, retired: str
) -> None:
    script = case.client.get(
        "/assets/review-app/assets/index-deadbeef.js"
    )
    assert script.status_code == 200
    assert script.content == b"window.reviewReady=true;"
    assert script.headers["content-type"].startswith("text/javascript")
    assert case.client.get("/assets/review-app/index.html").status_code == 404
    assert case.client.get("/assets/review-app/assets/other.js").status_code == 404
    assert case.client.get(f"/assets/{retired}").status_code == 404


def test_build_identity_fails_closed_before_logout_authority(case: _Case) -> None:
    _login(case)
    page = case.client.get("/library")
    csrf = re.search(r'data-csrf-token="([A-Za-z0-9_-]+)"', page.text)
    assert csrf is not None
    base_headers = {**case.origin, "X-CSRF-Token": csrf.group(1)}
    missing = case.client.post("/api/v1/session/logout", headers=base_headers)
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "browser_build_mismatch"
    assert case.client.get("/library").status_code == 200
    mismatch = case.client.post(
        "/api/v1/session/logout",
        headers={**base_headers, "x-ml-build-identity": "0" * 64},
    )
    assert mismatch.status_code == 409
    current_page = case.client.get("/library")
    current_csrf = re.search(
        r'data-csrf-token="([A-Za-z0-9_-]+)"', current_page.text
    )
    assert current_csrf is not None
    accepted = case.client.post(
        "/api/v1/session/logout",
        headers={
            **case.origin,
            "X-CSRF-Token": current_csrf.group(1),
            "x-ml-build-identity": case.identity,
        },
    )
    assert accepted.status_code == 204, accepted.text


def test_deep_corrupt_manifest_is_one_stable_workspace_recovery(case: _Case) -> None:
    (case.asset_root / "review-assets-v1.json").write_text(
        "[" * 20_000 + "]" * 20_000,
        encoding="utf-8",
    )
    _login(case)

    first = case.client.get("/library")
    second = case.client.get("/library")

    assert first.status_code == 503
    assert second.status_code == 503
    assert first.text == second.text
    assert "Review workspace unavailable" in first.text
