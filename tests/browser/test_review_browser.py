from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    expect,
    sync_playwright,
)

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import CoverConsiderationService
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from scripts.verify_playwright_browser import verify_playwright_browser
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source as _bibliographic_source,
)
from tests.unit.test_review_web import _ActivityApplication
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
    _report as _cover_report,
    _source as _cover_source,
)


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
    cover: bool = False,
):
    repository = open_repository(database, create=True, cross_thread=True)
    cover_considerations = None
    if cover:
        payloads = (_cover_preview(1), _cover_preview(2))
        sources = (
            _cover_source(1, payloads[0], incumbent=True),
            _cover_source(2, payloads[1], embedded=True),
        )
        base = _cover_report(sources)
        incumbent = sources[0].artifact
        assert incumbent is not None
        fields = tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                incumbent.artifact_id,
            )
            if field.destination is TargetDestination.COVER
            else field
            for field in base.target.fields
        )
        report = MetadataReport.create(
            target=TargetFacts(
                base.target.connection,
                base.target.library,
                base.target.item,
                fields,
            ),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=base.cover_sources,
        )
        artifacts = CoverArtifactStore(database.parent / "cover-artifacts")
        for index, (source, payload) in enumerate(zip(sources, payloads, strict=True), 1):
            assert source.artifact is not None
            artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
        cover_considerations = CoverConsiderationService(
            repository,
            read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: 1_000,
        )
    else:
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
        cover_considerations=cover_considerations,
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
    page.route("**/favicon.ico", lambda route: route.fulfill(status=204, body=""))
    page.goto(f"{origin}/login")
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


@contextmanager
def _closing_context(context: BrowserContext) -> Iterator[None]:
    try:
        yield
    finally:
        context.close()


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
                ) as (origin, case_id), _closing_context(context):
                    _login(page, origin)
                    try:
                        page.get_by_role("heading", name="Every work, with exceptions first").wait_for(
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
                    page.get_by_role("button", name="Attention queue").click()
                    attention_rail = page.locator(".attention-rail")
                    attention_rail.wait_for()
                    expect(attention_rail).to_be_focused()
                    page.get_by_role("link", name="Policy", exact=True).click()
                    page.get_by_role(
                        "heading", name="Choose meaning before automation."
                    ).wait_for()
                    chapter = page.get_by_role("combobox", name="Policy chapter")
                    self.assertTrue(chapter.is_visible())
                    chapter.select_option("curation")
                    page.wait_for_url("**/policy?section=curation")
                    expect(page.locator("#policy-curation")).to_be_focused()
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 390
                    )
                    page.get_by_role("button", name="Save draft").click()
                    page.get_by_text(
                        "Immutable draft saved. Preview its impact before activation."
                    ).wait_for()
                    page.get_by_role("button", name="Preview impact").click()
                    page.get_by_text(
                        "Impact preview is current. The active policy is unchanged."
                    ).wait_for()
                    review_activation = page.get_by_role(
                        "button", name="Review activation"
                    )
                    review_activation.click()
                    activation_dialog = page.get_by_role(
                        "dialog", name="Apply this policy to current unapplied work?"
                    )
                    cancel_activation = activation_dialog.get_by_role(
                        "button", name="Cancel"
                    )
                    activate_policy = activation_dialog.get_by_role(
                        "button", name="Activate policy"
                    )
                    self.assertTrue(
                        cancel_activation.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Tab")
                    self.assertTrue(
                        activate_policy.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Tab")
                    self.assertTrue(
                        cancel_activation.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Shift+Tab")
                    self.assertTrue(
                        activate_policy.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Escape")
                    self.assertEqual(activation_dialog.count(), 0)
                    expect(review_activation).to_be_focused()
                    page.get_by_role("button", name="Use dark mode").click()
                    self.assertEqual(
                        page.evaluate("() => document.documentElement.dataset.theme"),
                        "dark",
                    )
                    page.set_viewport_size({"width": 1440, "height": 900})
                    self.assertTrue(
                        page.get_by_role("navigation", name="Policy chapters").is_visible()
                    )
                    review_activation.click()
                    activation_dialog = page.get_by_role(
                        "dialog", name="Apply this policy to current unapplied work?"
                    )
                    cancel_activation = activation_dialog.get_by_role(
                        "button", name="Cancel"
                    )
                    activate_policy = activation_dialog.get_by_role(
                        "button", name="Activate policy"
                    )
                    self.assertTrue(
                        cancel_activation.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Shift+Tab")
                    self.assertTrue(
                        activate_policy.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.keyboard.press("Tab")
                    self.assertTrue(
                        cancel_activation.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    cancel_activation.click()
                    self.assertEqual(activation_dialog.count(), 0)
                    expect(review_activation).to_be_focused()
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 1440
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.get_by_role("button", name="Use light mode").click()
                    activity_requests = 0

                    def activity_poll(route) -> None:
                        nonlocal activity_requests
                        activity_requests += 1
                        response = route.fetch()
                        payload = response.json()
                        payload["counts"]["claimed"] = 1
                        route.fulfill(status=response.status, json=payload)

                    page.route("**/api/v1/activity", activity_poll)
                    page.get_by_role("link", name="Activity", exact=True).click()
                    page.get_by_role("heading", name="Ready work is waiting safely").wait_for()
                    self.assertGreaterEqual(activity_requests, 1)
                    page.get_by_role("link", name="Library", exact=True).click()
                    page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
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
                    self.assertEqual(
                        page.get_by_role("button", name="Choose current value:", exact=False).count(),
                        1,
                    )
                    self.assertEqual(
                        page.get_by_role("button", name="Choose proposed value", exact=False).count(),
                        0,
                    )
                    page.get_by_role("button", name="series", exact=True).click()
                    page.get_by_role("button", name="Use dark mode").click()
                    self.assertEqual(
                        page.locator("html").get_attribute("data-theme"), "dark"
                    )
                    page.get_by_role("button", name="Choose current value:", exact=False).first.click()
                    page.get_by_text("Decision recorded.", exact=False).wait_for()
                    page.get_by_role("button", name="series", exact=True).click()
                    page.get_by_text("Human decision: keep current by owner. Recorded value:", exact=False).wait_for()
                    self.assertEqual(
                        page.get_by_role("button", name="Choose current value:", exact=False).count(),
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
                browser.close()

    def test_protected_cover_round_trip_preserves_explicit_owner_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            errors: list[str] = []
            responses: list[tuple[str, int]] = []
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
                page.on(
                    "response",
                    lambda response: responses.append((response.url, response.status))
                    if "/cover-considerations" in response.url
                    or response.url.endswith("/decisions")
                    else None,
                )
                with _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                    cover=True,
                ) as (origin, case_id), _closing_context(context):
                    _login(page, origin)
                    page.get_by_role(
                        "heading", name="Every work, with exceptions first"
                    ).wait_for()
                    page.goto(f"{origin}/review/{case_id}?field=cover&subview=review")
                    page.get_by_role("button", name="Prepare cover comparison").click()
                    page.get_by_role("heading", name="Available").first.wait_for()
                    expect(page.get_by_alt_text("Current library cover")).to_be_visible()
                    expect(page.get_by_alt_text("Alternative 1 cover")).to_be_visible()
                    self.assertIn("Policy preferences", page.locator("body").inner_text())
                    self.assertIn(
                        "No optional visual preferences enabled",
                        page.locator("body").inner_text(),
                    )
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as decision_response, page.expect_response(
                        lambda response: response.url.endswith("/history")
                    ) as history_response:
                        page.get_by_role("button", name="Choose this cover").click()
                    response = decision_response.value
                    self.assertEqual(
                        200,
                        response.status,
                        f"decision response: {response.text()}",
                    )
                    history_result = history_response.value
                    self.assertEqual(
                        200,
                        history_result.status,
                        f"history response: {history_result.text()}",
                    )
                    page.get_by_text(
                        "Decision recorded from the authoritative case.", exact=True
                    ).wait_for()
                    page.get_by_text("Human decision: accept by owner", exact=False).wait_for()
                    self.assertEqual(
                        page.get_by_role("button", name="Choose this cover").count(), 0
                    )
                    history_button = page.get_by_role("button", name="History", exact=True)
                    history_button.focus()
                    page.keyboard.press("Enter")
                    selected_history = page.get_by_role(
                        "heading", name="Cover selected"
                    ).locator("..")
                    selected_history.get_by_text(
                        "Historical; it grants no current authority.", exact=False
                    ).wait_for()
                    selected_history.get_by_text(
                        "Owner outcome: accept", exact=False
                    ).wait_for()
                    expect(
                        selected_history.get_by_role(
                            "list", name="Historical cover candidates"
                        )
                    ).to_be_visible()
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 390
                    )
                    self.assertTrue(responses)
                    self.assertTrue(all(status == 200 for _, status in responses), responses)
                    self.assertEqual(errors, [])
                browser.close()

    def test_z_compact_uncached_library_detail_keeps_visible_focus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                page = context.new_page()
                with _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                ) as (origin, case_id), _closing_context(context):
                    def delay_selected_detail(route) -> None:
                        if route.request.url.endswith(f"/api/v1/review-cases/{case_id}"):
                            time.sleep(0.3)
                        route.continue_()

                    page.route("**/api/v1/review-cases/**", delay_selected_detail)
                    _login(page, origin)
                    page.locator(".work-select").first.click()
                    page.get_by_text("Loading selected work…").wait_for()
                    pending_detail = page.locator(".workspace-detail")
                    expect(pending_detail).to_be_focused()
                    self.assertIsNotNone(pending_detail.bounding_box())
                    page.get_by_role("button", name="Choose current value:", exact=False).wait_for()
                    page.unroute("**/api/v1/review-cases/**", delay_selected_detail)
                browser.close()


if __name__ == "__main__":
    unittest.main()
