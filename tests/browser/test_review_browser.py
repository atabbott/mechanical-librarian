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
from typing import Any
from urllib.parse import parse_qs, urlparse

import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import (
    BrowserContext,
    Page,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from scripts.verify_playwright_browser import verify_playwright_browser

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web.react_assets import ReactAssetLoader
from tests.support.review_web_doubles import FakeActivityApplication
from tests.unit.review_app_fixture import create_transport_test_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
)
from tests.unit.test_cover_packets import (
    _report as _cover_report,
)
from tests.unit.test_cover_packets import (
    _source as _cover_source,
)

_PASSWORD = "synthetic browser password"


class _BrowserActivityApplication(FakeActivityApplication):
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
    cover: bool = False, automatic_fixture: bool = False,
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
            providers=(
                _bibliographic_source(
                    "catalogue", "edition-1", titles=("Synthetic title revised",),
                    series="Mistborn",
                ),
            ) if automatic_fixture else base.providers,
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
                    page.get_by_role("heading", name="Library policy").wait_for()
                    page.get_by_role("button", name="Curation").click()
                    page.wait_for_url("**/policy?section=curation")
                    expect(page.locator("#policy-curation")).to_be_focused()
                    policy_chapters = page.get_by_role(
                        "navigation", name="Policy chapters"
                    )
                    self.assertEqual(
                        policy_chapters.locator("button b").all_inner_texts(),
                        ["0", "0", "0", "0"],
                    )
                    self.assertEqual(
                        policy_chapters.evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                        ),
                        2,
                    )
                    self.assertEqual(
                        policy_chapters.get_by_role("button", name="Curation")
                        .get_attribute("aria-describedby"),
                        "policy-curation-changes",
                    )
                    page.get_by_text(
                        "Choose how metadata is stored and when you want to review it.",
                        exact=True,
                    ).wait_for()
                    unsupported_description = policy_chapters.get_by_role(
                        "button", name="Unsupported"
                    ).get_by_text(
                        "Fields Mechanical Librarian can detect but cannot change yet.",
                        exact=True,
                    )
                    self.assertEqual(unsupported_description.count(), 1)
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        390,
                        page.evaluate(
                            """() => [...document.querySelectorAll('*')]
                            .filter(node => node.scrollWidth > node.clientWidth)
                            .map(node => [node.tagName, node.className, node.textContent?.slice(0, 40), node.clientWidth, node.scrollWidth, getComputedStyle(node).gridTemplateColumns])"""
                        ),
                    )
                    genres = page.get_by_role("region", name="Genres")
                    representation = genres.get_by_role(
                        "combobox", name="Genres representation"
                    )
                    representation.focus()
                    page.keyboard.press("Enter")
                    page.get_by_role("listbox", name="Genres representation").wait_for()
                    page.keyboard.press("Escape")
                    expect(representation).to_be_focused()
                    representation.click()
                    menu_box = page.get_by_role(
                        "listbox", name="Genres representation"
                    )
                    expect(menu_box).to_have_css("scale", "none")
                    menu_box = menu_box.bounding_box()  # type: ignore[assignment]  # reason: the name is deliberately rebound from the Locator to its measured bounding box, which the next lines assert on.
                    self.assertIsNotNone(menu_box)
                    assert menu_box is not None
                    self.assertGreaterEqual(menu_box["x"], 8)
                    self.assertLessEqual(menu_box["x"] + menu_box["width"], 382)
                    for option in page.get_by_role("option").all():
                        self.assertGreaterEqual(option.bounding_box()["height"], 44)
                    page.keyboard.press("Escape")
                    for control in page.locator(
                        ".cm-policy-sidebar nav button, .cm-policy-posture button, "
                        ".cm-dropdown-trigger, .cm-policy-finish-actions button"
                    ).all():
                        self.assertGreaterEqual(control.bounding_box()["height"], 44)
                    self.assertEqual(
                        page.locator(".cm-policy-field").first.evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                        ),
                        1,
                    )
                    self.assertEqual(
                        page.locator(".cm-policy-finish").evaluate(
                            "node => getComputedStyle(node).position"
                        ),
                        "static",
                    )
                    page.emulate_media(reduced_motion="reduce")
                    self.assertEqual(
                        page.locator(".cm-dropdown-menu").count(),
                        0,
                    )
                    genres.get_by_role("button", name="Ignore").click()
                    self.assertEqual(
                        policy_chapters.locator("button b").all_inner_texts(),
                        ["0", "0", "1", "0"],
                    )

                    def assert_text_zoom_policy_layout(width: int) -> None:
                        page.set_viewport_size({"width": width, "height": 844})
                        page.wait_for_function(
                            """() => getComputedStyle(document.querySelector(
                            '.cm-policy-sidebar nav')).gridTemplateColumns.split(' ').length === 1"""
                        )
                        self.assertLessEqual(
                            page.evaluate("() => document.documentElement.scrollWidth"),
                            width,
                        )
                        self.assertEqual(
                            page.evaluate(
                                """() => [...document.querySelectorAll(
                                `.masthead nav, .masthead nav a, .cm-policy-route,
                                .cm-policy-editor, .cm-policy-finish,
                                .cm-policy-finish-actions, .cm-policy-finish-actions button`)]
                                .filter(node => node.scrollWidth > node.clientWidth &&
                                getComputedStyle(node).overflowX === 'visible')
                                .map(node => [node.tagName, node.className, node.clientWidth, node.scrollWidth])"""
                            ),
                            [],
                        )
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = '200%'; }"
                    )
                    assert_text_zoom_policy_layout(390)
                    self.assertEqual(
                        page.locator(".cm-policy-sidebar nav").evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                        ),
                        1,
                    )
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        390,
                    )
                    self.assertEqual(
                        page.get_by_role("link", name="Policy", exact=True)
                        .is_visible(),
                        True,
                    )
                    self.assertEqual(
                        page.get_by_role("link", name="Activity", exact=True)
                        .is_visible(),
                        True,
                    )
                    self.assertFalse(
                        page.evaluate(
                            """() => [...document.querySelectorAll('*')].some(node =>
                            node.scrollWidth > node.clientWidth &&
                            !node.classList.contains('brand-mark') &&
                            getComputedStyle(node).overflowX === 'visible')"""
                        )
                    )
                    assert_text_zoom_policy_layout(430)
                    page.get_by_role("button", name="Use dark mode").click()
                    page.get_by_role("button", name="Use light mode").wait_for()
                    assert_text_zoom_policy_layout(390)
                    assert_text_zoom_policy_layout(430)
                    page.get_by_role("button", name="Use light mode").click()
                    page.get_by_role("button", name="Use dark mode").wait_for()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = ''; }"
                    )
                    page.get_by_role("button", name="Validate and preview").click()
                    page.get_by_text(
                        "Impact preview is current. The active policy is unchanged."
                    ).wait_for()
                    review_activation = page.get_by_role(
                        "button", name="Review activation"
                    )
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = '200%'; }"
                    )
                    assert_text_zoom_policy_layout(430)
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = ''; }"
                    )
                    review_activation.click()
                    page.get_by_text("Activate this reviewed draft?").wait_for()
                    self.assertEqual(page.get_by_role("dialog").count(), 0)
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = '200%'; }"
                    )
                    assert_text_zoom_policy_layout(430)
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = ''; }"
                    )
                    page.get_by_role("button", name="Back to preview").click()
                    review_activation.wait_for()
                    dark_mode = page.get_by_role("button", name="Use dark mode")
                    dark_mode.click()
                    page.get_by_role("button", name="Use light mode").wait_for()
                    page.set_viewport_size({"width": 1440, "height": 900})
                    unsupported_description.wait_for()
                    self.assertTrue(
                        page.get_by_role("navigation", name="Policy chapters").is_visible()
                    )
                    review_activation.click()
                    page.get_by_role("button", name="Back to preview").click()
                    review_activation.wait_for()
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 1440
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    light_mode = page.get_by_role("button", name="Use light mode")
                    light_mode.click()
                    page.get_by_role("button", name="Use dark mode").wait_for()
                    activity_requests = 0
                    held_page_size_response: list[tuple[Any, Any, Any]] = []

                    def activity_poll(route) -> None:
                        nonlocal activity_requests
                        activity_requests += 1
                        response = route.fetch()
                        payload = response.json()
                        payload["counts"]["claimed"] = 1
                        query = parse_qs(urlparse(route.request.url).query)
                        payload["ledger"].update(
                            page=int(query.get("page", ["1"])[0]),
                            page_count=3,
                            page_size=int(query.get("page_size", ["25"])[0]),
                            total=75,
                        )
                        if query.get("page_size") == ["50"] and not held_page_size_response:
                            held_page_size_response.append((route, response, payload))
                            return
                        route.fulfill(status=response.status, json=payload)

                    page.route("**/api/v1/activity?*", activity_poll)
                    page.get_by_role("link", name="Activity", exact=True).click()
                    page.get_by_role("heading", name="No intervention needed").wait_for()
                    self.assertGreaterEqual(activity_requests, 1)
                    activity_search = page.get_by_role("searchbox", name="Search activity")
                    activity_search.click()
                    activity_search.press_sequentially("ab")
                    self.assertEqual(activity_search.input_value(), "ab")
                    page.wait_for_url("**/activity?*q=ab*")
                    expect(activity_search).to_be_focused()
                    next_page = page.get_by_role("button", name="Next", exact=True)
                    next_page.click()
                    page.wait_for_url("**/activity?*page=2*")
                    expect(next_page).to_be_focused()
                    page_number = page.get_by_role("spinbutton", name="Activity page number")
                    expect(page_number).to_have_value("2")
                    page_number.fill("99")
                    page_number.press("Enter")
                    page.wait_for_url("**/activity?*page=3*")
                    page_number = page.get_by_role("spinbutton", name="Activity page number")
                    self.assertEqual(page_number.input_value(), "3")
                    expect(page_number).to_be_focused()
                    page_number.fill("2")
                    page_number.press("Escape")
                    self.assertEqual(page_number.input_value(), "3")
                    expect(page_number).to_be_focused()
                    previous_page = page.get_by_role("button", name="Previous", exact=True)
                    previous_page.click()
                    page.wait_for_url("**/activity?*page=2*")
                    previous_page.click()
                    page.wait_for_url("**/activity?*page=1*")
                    expect(page.get_by_role("spinbutton", name="Activity page number")).to_be_focused()
                    page.get_by_role("button", name="Last", exact=True).click()
                    page.wait_for_url("**/activity?*page=3*")
                    expect(page.get_by_role("spinbutton", name="Activity page number")).to_be_focused()
                    rows = page.get_by_role("combobox", name="Rows per activity page")
                    rows.click()
                    page.get_by_role("option", name="50 per page").click()
                    expect(rows).to_be_focused()
                    page.set_viewport_size({"width": 1440, "height": 900})
                    activity_search = page.get_by_role("searchbox", name="Search activity")
                    search_node = activity_search.element_handle()
                    self.assertIsNotNone(search_node)
                    activity_search.fill("")
                    self.assertEqual(activity_search.input_value(), "")
                    expect(activity_search).to_be_focused()
                    self.assertEqual(len(held_page_size_response), 1)
                    held_route, held_response, held_payload = held_page_size_response[0]
                    held_route.fulfill(status=held_response.status, json=held_payload)
                    self.assertEqual(activity_search.input_value(), "")
                    expect(activity_search).to_be_focused()
                    self.assertTrue(search_node.evaluate("node => node.isConnected"))
                    activity_search.press_sequentially("desktop")
                    self.assertEqual(activity_search.input_value(), "desktop")
                    page.wait_for_url("**/activity?*q=desktop*")
                    expect(activity_search).to_be_focused()
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.get_by_role("link", name="Library", exact=True).click()
                    page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
                    settled_activity_requests = activity_requests
                    page.wait_for_timeout(5_500)
                    self.assertEqual(activity_requests, settled_activity_requests)
                    page.unroute("**/api/v1/activity?*", activity_poll)
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
                    page.get_by_role("button", name="Use light mode").wait_for()
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
                    automatic_fixture=True,
                ) as (origin, case_id), _closing_context(context):
                    _login(page, origin)
                    page.get_by_role(
                        "heading", name="Every work, with exceptions first"
                    ).wait_for()
                    # This is a closed presentation fixture, not a new automatic
                    # authority path: its projections retain the exact candidate
                    # values already supplied by the current case.  It protects
                    # restoration of recorded automatic choices, including cover
                    # values, without widening the qualified-policy envelope.
                    def automatic_projection(route) -> None:
                        response = route.fetch()
                        payload = response.json()
                        fields = payload["fields"]
                        outcomes = {
                            "title": "candidate",
                            "series": "keep_current",
                            "tags": "ignore",
                            "cover": "candidate",
                        }
                        for index, field in enumerate(fields, 1):
                            outcome = outcomes.get(field["destination"])
                            if outcome is None:
                                continue
                            if (
                                field["destination"] == "cover"
                                and field["cover_consideration"] is None
                            ):
                                continue
                            selected = None
                            if outcome == "candidate":
                                selected = (
                                    next(
                                        {
                                            "kind": "cover",
                                            "value": candidate["artifact_id"],
                                        }
                                        for candidate in field["cover_consideration"]["candidates"]
                                        if candidate["selectable"]
                                    )
                                    if field["destination"] == "cover"
                                    else field["proposed_values"][0]
                                )
                            field.update(
                                automatic_decision={
                                    "candidate_id": (
                                        None
                                        if outcome != "candidate"
                                        else "alternative_" + f"{index:064x}"
                                    ),
                                    "decision_id": "automatic_decision_"
                                    + f"{index:064x}",
                                    "destination": field["destination"],
                                    "occurred_at": 1_000,
                                    "outcome": outcome,
                                    "proposal_id": field["proposal_id"],
                                    "review_case_id": payload["review_case_id"],
                                    "selected_value": selected,
                                    "target_fingerprint": field["target_fingerprint"],
                                },
                                classification="automatic_recorded",
                                classification_reason=(
                                    "Automatic handling recorded this field; "
                                    "you may inspect or override it."
                                ),
                                editable=True,
                                needs_attention=False,
                                navigation_role="inspect",
                            )
                        route.fulfill(status=response.status, json=payload)

                    page.route(
                        f"**/api/v1/review-cases/{case_id}", automatic_projection
                    )
                    page.goto(f"{origin}/review/{case_id}?field=title&subview=review")
                    title_current = page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    )
                    title_candidate = page.get_by_role(
                        "button", name="Choose proposed value", exact=False
                    )
                    expect(title_candidate).to_have_attribute("aria-pressed", "true")
                    self.assertTrue(
                        title_candidate.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    expect(title_current).to_have_attribute("aria-pressed", "false")
                    self.assertFalse(
                        title_current.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    page.reload()
                    expect(title_candidate).to_have_attribute("aria-pressed", "true")
                    self.assertTrue(
                        title_candidate.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    with page.expect_response(
                        lambda response: response.url.endswith("/decisions")
                    ) as override_response:
                        title_current.focus()
                        page.keyboard.press("Enter")
                    self.assertEqual(200, override_response.value.status)
                    page.get_by_text(
                        "Human decision: keep current by owner", exact=False
                    ).wait_for()
                    page.goto(f"{origin}/review/{case_id}?field=series&subview=review")
                    series_current = page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    )
                    expect(series_current).to_have_attribute("aria-pressed", "true")
                    self.assertTrue(
                        series_current.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    page.goto(f"{origin}/review/{case_id}?field=tags&subview=review")
                    ignored_current = page.get_by_role(
                        "button", name="Choose current value:", exact=False
                    )
                    expect(ignored_current).to_have_attribute("aria-pressed", "false")
                    self.assertFalse(
                        ignored_current.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    ignored_no_action = page.get_by_role(
                        "button", name="Take no action", exact=True
                    )
                    expect(ignored_no_action).to_have_attribute(
                        "aria-pressed", "true"
                    )
                    self.assertTrue(
                        ignored_no_action.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    page.goto(f"{origin}/review/{case_id}?field=cover&subview=review")
                    page.get_by_role("button", name="Prepare cover comparison").click()
                    page.get_by_role("heading", name="Available").first.wait_for()
                    expect(page.get_by_alt_text("Current library cover")).to_be_visible()
                    expect(page.get_by_alt_text("Alternative 1 cover")).to_be_visible()
                    current_cover = page.get_by_role("button", name="Keep current cover")
                    automatic_cover = page.get_by_role("button", name="Choose this cover")
                    expect(automatic_cover).to_have_attribute("aria-pressed", "true")
                    self.assertTrue(
                        automatic_cover.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    expect(current_cover).to_have_attribute("aria-pressed", "false")
                    self.assertFalse(
                        current_cover.evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
                    expect(
                        page.get_by_role("button", name="Take no action", exact=True)
                    ).to_have_attribute("aria-pressed", "false")
                    page.reload()
                    expect(page.get_by_role("button", name="Choose this cover")).to_have_attribute("aria-pressed", "true")
                    self.assertTrue(
                        page.get_by_role("button", name="Choose this cover").evaluate(
                            "node => node.classList.contains('is-selected')"
                        )
                    )
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
