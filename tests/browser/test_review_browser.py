from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from scripts.verify_playwright_browser import verify_playwright_browser
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source as _bibliographic_source,
)
from tests.unit.test_review_web import _ActivityApplication


_PASSWORD = "synthetic browser password"


class _BrowserActivityApplication(_ActivityApplication):
    def wake(self) -> None:
        return None


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "browser-test.crt"
    key = root / "browser-test.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(certificate), "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def _report():
    return _bibliographic_report(
        current_title="Mistborn <img src=x onerror=alert(1)>",
        providers=(
            _bibliographic_source(
                "catalogue", "edition-1", titles=(), series="Mistborn",
            ),
        ),
    )


@contextmanager
def _server(
    *, database: Path, hash_path: Path, certificate: Path, key: Path, port: int,
):
    repository = open_repository(database, create=True, cross_thread=True)
    report = _report()
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    origin = f"https://127.0.0.1:{port}"
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            canonical_origin=origin,
            owner_password_hash_file=str(hash_path),
        ),
        clock=lambda: 1_000,
        close_repository=True,
        field_application=_BrowserActivityApplication(),
        react_asset_loader=ReactAssetLoader(
            Path(os.environ["ML_REACT_ASSET_DIR"])
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
            timeout_keep_alive=1,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("synthetic review server did not start")
    try:
        yield origin, str(case_id)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)


def _login(page: Page, origin: str) -> None:
    page.goto(f"{origin}/login")
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


class ReviewBrowserQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        verify_playwright_browser()

    def test_persistent_workspace_direct_entry_navigation_and_safe_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            errors: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: errors.append(message.text)
                    if message.type == "error"
                    else None,
                )
                with _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                ) as (origin, case_id):
                    _login(page, origin)
                    try:
                        page.get_by_role("heading", name="Review library").wait_for(
                            timeout=8_000
                        )
                    except PlaywrightTimeoutError:
                        document = page.request.get(page.url)
                        self.fail(
                            f"url={page.url!r}; errors={errors!r}; "
                            f"status={document.status}; html={document.text()[:1200]!r}; "
                            f"dom={page.content()[:1200]!r}"
                        )
                    page.set_viewport_size({"width": 1440, "height": 900})
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        1440,
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    documents = 0

                    def count_document(request) -> None:
                        nonlocal documents
                        if request.resource_type == "document":
                            documents += 1

                    page.on("request", count_document)
                    initial_documents = documents
                    page.get_by_role("link", name="Attention", exact=True).click()
                    page.get_by_role("heading", name="Fields needing attention").wait_for()
                    page.get_by_role("link", name="Policy", exact=True).click()
                    page.get_by_role(
                        "heading", name="Choose how prepared metadata reaches you"
                    ).wait_for()
                    activity_requests = 0

                    def activity_poll(route) -> None:
                        nonlocal activity_requests
                        activity_requests += 1
                        response = route.fetch()
                        payload = response.json()
                        payload["counts"] = {"claimed": 1}
                        route.fulfill(status=response.status, json=payload)

                    page.route("**/api/v1/activity", activity_poll)
                    page.get_by_role("link", name="Activity", exact=True).click()
                    page.get_by_role("heading", name="Ready when you are").wait_for()
                    self.assertGreaterEqual(activity_requests, 1)
                    page.get_by_role("link", name="Library", exact=True).click()
                    page.get_by_role("heading", name="Review library").wait_for()
                    settled_activity_requests = activity_requests
                    page.wait_for_timeout(5_500)
                    self.assertEqual(activity_requests, settled_activity_requests)
                    page.unroute("**/api/v1/activity", activity_poll)
                    self.assertEqual(documents, initial_documents)
                    page.goto(f"{origin}/review/{case_id}?field=title&subview=review")
                    page.get_by_role("heading", name="Current review case").wait_for()
                    self.assertTrue(
                        page.locator("#field-title").evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    self.assertEqual(page.locator("img,script:not([src])").count(), 0)
                    self.assertIn("<img", page.locator("body").inner_text())
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 390
                    )
                    page.get_by_role("button", name="Dark mode").click()
                    self.assertEqual(
                        page.locator("html").get_attribute("data-theme"), "dark"
                    )
                    page.get_by_role("button", name="Keep current value").first.click()
                    page.get_by_text(
                        "Decision recorded from the authoritative case."
                    ).wait_for()
                    page.get_by_text("Human decision: keep current by owner.").wait_for()
                    self.assertEqual(
                        page.get_by_role("button", name="Keep current value").count(),
                        0,
                    )
                    page.get_by_role("button", name="Evidence").click()
                    page.get_by_role("heading", name="Evidence and source ledger").wait_for()
                    page.go_back()
                    page.get_by_role("heading", name="Current review case").wait_for()
                    page.go_forward()
                    page.get_by_role("heading", name="Evidence and source ledger").wait_for()
                    self.assertEqual(documents, initial_documents + 1)
                    self.assertEqual(errors, [])
                context.close()
                browser.close()


if __name__ == "__main__":
    unittest.main()
