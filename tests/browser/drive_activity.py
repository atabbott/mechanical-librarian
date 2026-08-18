"""Serve the seeded Activity surface so a person can look at it.

The automated guards measure this page, but measuring is not the same as
seeing, and the seeded ledger exists precisely so the surface can be judged by
eye against the protected prototype. This starts the same application the
browser tier drives -- the same seeded projection, the same assets -- and then
waits, so the page can be opened, filtered, searched, paged and zoomed by hand.

    uv run python -m tests.browser.drive_activity

It prints the URL and the password, and serves until interrupted.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import uvicorn
from argon2 import PasswordHasher

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from tests.browser.test_review_browser import _PASSWORD, _certificate, _report
from tests.support.review_web_doubles import FakeActivityApplication
from tests.unit.review_app_fixture import create_transport_test_app

PORT = 8443


def main() -> None:
    asset_dir = os.environ.get("ML_REACT_ASSET_DIR")
    if not asset_dir:
        raise SystemExit(
            "Set ML_REACT_ASSET_DIR to a built frontend, for example:\n"
            "  (cd frontend && npm run build)\n"
            "  ML_REACT_ASSET_DIR=frontend/dist uv run python -m "
            "tests.browser.drive_activity"
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        certificate, key = _certificate(root)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        repository = open_repository(
            root / "review.sqlite3", create=True, cross_thread=True
        )
        repository.publish(_report(), lambda: True)
        origin = f"https://127.0.0.1:{PORT}"
        app = create_transport_test_app(
            repository,
            ReviewWebConfiguration(
                canonical_origin=origin, owner_password_hash_file=str(hash_path)
            ),
            clock=lambda: 1_000,
            close_repository=True,
            field_application=FakeActivityApplication(),
            react_asset_loader=ReactAssetLoader(Path(asset_dir)),
        )
        print(f"\n  Activity surface: {origin}/activity", flush=True)
        print(f"  Owner password:   {_PASSWORD}", flush=True)
        print(
            "  The certificate is self-signed; accept the browser warning.\n",
            flush=True,
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=PORT,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
        )


if __name__ == "__main__":
    main()
