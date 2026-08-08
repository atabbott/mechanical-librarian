from __future__ import annotations

import json
import os
import re
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
from playwright.sync_api import Locator, Page, sync_playwright

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.web import create_review_app
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source as _bibliographic_source,
)
from tests.unit.test_proposal_set import (
    _claim as _proposal_claim,
    _provider as _proposal_provider,
    _report as _proposal_report,
)
from tests.unit.test_review_case_persistence import _report as _simple_report
from scripts.verify_playwright_browser import verify_playwright_browser


_PASSWORD = "synthetic browser password"


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "browser-test.crt"
    key = root / "browser-test.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def _manual_report():
    return _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _bibliographic_source(
                "catalogue",
                "edition-1",
                titles=(),
                series="Mistborn",
            ),
        ),
    )


def _hostile_report():
    return _simple_report(
        title='<img src="https://invalid.test/private" onerror="alert(1)">'
    )


def _automatic_report():
    providers = tuple(
        _proposal_provider(
            name,
            f"{name}-edition",
            (
                _proposal_claim(
                    name,
                    f"{name}-edition",
                    ClaimKind.TITLE,
                    "Qualified automatic title",
                    scope=ClaimScope.WORK,
                ),
            ),
            authority=authority,
        )
        for name, authority in (
            ("hardcover_primary", "hardcover"),
            ("open_library_primary", "open_library"),
        )
    )
    return _proposal_report(providers=providers)


@contextmanager
def _server(
    *,
    database: Path,
    hash_path: Path,
    certificate: Path,
    key: Path,
    port: int,
    report=None,
    policy_libraries=(),
):
    repository = open_repository(
        database,
        create=report is not None,
        cross_thread=True,
    )
    case_id: str | None = None
    if report is not None:
        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
    origin = f"https://127.0.0.1:{port}"
    app = create_review_app(
        repository,
        ReviewWebConfiguration(
            canonical_origin=origin,
            owner_password_hash_file=str(hash_path),
        ),
        clock=lambda: 1_000,
        close_repository=True,
        policy_libraries=policy_libraries,
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
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("synthetic review server did not start")
    try:
        yield origin, case_id
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("synthetic review server did not stop")


def _login(page: Page, origin: str) -> None:
    page.goto(f"{origin}/login")
    brand = page.locator(".brand")
    brand.wait_for()
    assert brand.evaluate("node => node.tagName") == "SPAN"
    assert page.get_by_role(
        "link", name="Mechanical Librarian review library"
    ).count() == 0
    page.get_by_label("Password").fill(_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/library")


def _capture_errors(page: Page, errors: list[str]) -> None:
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text)
        if message.type == "error"
        else None,
    )


def _contrast_ratio(locator: Locator) -> float:
    return locator.evaluate(
        """node => {
          const channel = value => {
            value /= 255;
            return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
          };
          const luminance = color => {
            const values = color.match(/\\d+/g).slice(0, 3).map(Number);
            return .2126 * channel(values[0]) + .7152 * channel(values[1]) + .0722 * channel(values[2]);
          };
          const style = getComputedStyle(node);
          const values = [luminance(style.color), luminance(style.backgroundColor)].sort((a, b) => b - a);
          return (values[0] + .05) / (values[1] + .05);
        }"""
    )


def _press_and_wait_for_navigation(page: Page, control) -> None:
    previous = page.url
    control.press("Enter")
    page.wait_for_function(
        "previous => location.href !== previous", arg=previous
    )
    page.locator(".toast").wait_for()


class ReviewBrowserQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        verify_playwright_browser()

    def test_policy_notebook_initializes_active_draft_and_responsive_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            database = root / "application.sqlite3"
            port = _unused_port()
            errors: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                _capture_errors(page, errors)
                with _server(database=database, hash_path=hash_path, certificate=certificate, key=key, port=port, report=_manual_report(), policy_libraries=(("ebooks", None),)) as (origin, _):
                    _login(page, origin)
                    page.goto(f"{origin}/policy")
                    page.get_by_role("heading", name="Choose how prepared metadata reaches you").wait_for()
                    self.assertEqual("title_and_subtitle", page.locator('select[name="option-title_layout"]').input_value())
                    self.assertEqual("ask", page.locator('select[name="destination-tags"]').input_value())
                    self.assertTrue(page.locator('select[name="destination-tags"] option[value="automatic"]').is_disabled())
                    self.assertEqual("unsupported", page.locator('select[name="destination-explicit"]').input_value())
                    page.get_by_text("Human review is active.", exact=False).wait_for()
                    before_preview = page.request.get(
                        f"{origin}/api/v1/library?lens=all&limit=10"
                    ).json()
                    page.get_by_role("button", name="Enter Preview").click()
                    page.get_by_text("Preview is on.", exact=False).wait_for()
                    self.assertTrue(
                        page.get_by_role("button", name="Enter Preview").is_disabled()
                    )
                    after_preview = page.request.get(
                        f"{origin}/api/v1/library?lens=all&limit=10"
                    ).json()
                    self.assertEqual(before_preview, after_preview)
                    page.get_by_role("button", name="Preview leaving").click()
                    page.get_by_text(
                        "Leaving Preview cannot be confirmed", exact=False
                    ).wait_for()
                    self.assertEqual(
                        0,
                        page.get_by_role("button", name=re.compile("confirm", re.I)).count(),
                    )
                    stale_rotation = page.request.get(f"{origin}/library")
                    self.assertEqual(200, stale_rotation.status)
                    page.locator("#policy-name").fill("<hostile draft>")
                    page.locator('select[name="option-date_precision"]').select_option("full_date")
                    page.get_by_role("button", name="Validate").click()
                    page.get_by_text("Valid draft:").wait_for()
                    page.get_by_role("button", name="Create immutable draft").click()
                    page.get_by_text("Immutable draft created:").wait_for()
                    policy_id = page.locator("#policy-status").inner_text().split("Immutable draft created: ")[1].split(".")[0]
                    read = page.request.get(f"{origin}/api/v1/metadata-policy/drafts/{policy_id}")
                    self.assertEqual(200, read.status)
                    self.assertEqual(policy_id, read.json()["policy_id"])
                    page.get_by_role("button", name="Preview effects").click()
                    page.get_by_text(re.compile(r"ebooks: date_precision; 1 current unapplied item"), exact=False).wait_for()
                    self.assertTrue(page.get_by_role("button", name="Activate reviewed draft").is_enabled())
                    page.get_by_role("button", name="Activate reviewed draft").click()
                    page.get_by_text("Policy activated:", exact=False).wait_for()
                    page.get_by_text("Complete with 1 failure", exact=False).wait_for()
                    transition = page.evaluate(
                        "async () => (await fetch('/api/v1/metadata-policy/transition')).json()"
                    )
                    polling_calls: list[int] = []

                    def transition_poll(route) -> None:
                        polling_calls.append(1)
                        processing = len(polling_calls) == 1
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    **transition,
                                    "state": "processing" if processing else "completed",
                                    "completed": 0 if processing else transition["total"],
                                    "failed": 0,
                                }
                            ),
                        )

                    page.route(
                        "**/api/v1/metadata-policy/transition", transition_poll
                    )
                    page.route(
                        "**/api/v1/metadata-policy/transitions/*/retry",
                        lambda route: route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    **transition,
                                    "state": "processing",
                                    "completed": 0,
                                    "failed": 0,
                                }
                            ),
                        ),
                    )
                    page.get_by_role("button", name="Retry").click()
                    page.get_by_text("Applying updated policy: 0/1").wait_for()
                    page.get_by_text("Complete: 1/1").wait_for(timeout=6_000)
                    self.assertEqual(2, len(polling_calls))
                    page.wait_for_timeout(1_750)
                    self.assertEqual(2, len(polling_calls))
                    library = page.evaluate(
                        "async () => { const response = await fetch('/api/v1/library?limit=10'); "
                        "return {status: response.status, body: await response.json()}; }"
                    )
                    self.assertEqual(200, library["status"])
                    self.assertEqual(0, library["body"]["counts"]["all"])
                    self.assertEqual([], library["body"]["items"])
                    self.assertNotIn("<hostile draft>", page.locator("#policy-status").inner_html())
                    page.set_viewport_size({"width": 1440, "height": 900})
                    self.assertEqual("sticky", page.locator(".policy-chapters").evaluate("node => getComputedStyle(node).position"))
                    desktop = page.locator(".policy-content").bounding_box()
                    chapters = page.locator(".policy-chapters").bounding_box()
                    assert desktop is not None and chapters is not None
                    self.assertGreater(desktop["width"], 700)
                    self.assertLessEqual(chapters["width"], 190)
                    self.assertGreater(desktop["x"], chapters["x"] + chapters["width"])
                    page.locator('.policy-chapters a[href="#descriptions"]').click()
                    self.assertEqual("Effects", page.locator("#policy-chapter-select").input_value() and page.locator("#policy-chapter-select option:checked").inner_text())
                    page.set_viewport_size({"width": 390, "height": 844})
                    self.assertEqual("sticky", page.locator(".policy-chapters").evaluate("node => getComputedStyle(node).position"))
                    self.assertEqual("block", page.locator("#policy-chapter-select").evaluate("node => getComputedStyle(node).display"))
                    self.assertLessEqual(page.locator(".policy-content").evaluate("node => node.scrollWidth"), 390)
                    self.assertLessEqual(page.locator("body").evaluate("node => node.scrollWidth"), 390)
                    page.get_by_label("Policy chapter", exact=True).select_option("availability")
                    page.locator("#availability").wait_for()
                    self.assertTrue(page.locator("#availability").evaluate("node => node === document.activeElement"))
                    expected_403 = [error for error in errors if "403 (Forbidden)" in error]
                    self.assertEqual(1, len(expected_403))
                    errors.remove(expected_403[0])
                    self.assertEqual([], errors)
                    context.close()
                browser.close()

    def test_preparation_progress_refreshes_library_without_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            database = root / "application.sqlite3"
            port = _unused_port()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                with _server(
                    database=database,
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    report=_manual_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    responses = [
                        ("queued", 0, 0),
                        ("processing", 1, 0),
                        ("processing", 1, 1),
                        ("processing", 1, 1),
                        ("processing", 1, 1),
                        ("processing", 1, 1),
                        ("completed_with_failures", 1, 1),
                    ]
                    requests = 0
                    library_requests = 0
                    finished = False

                    def library_page(route) -> None:
                        response = route.fetch()
                        body = response.text().replace(
                            '<section id="library"',
                            '<section id="library" data-review-preparation="true"',
                            1,
                        )
                        route.fulfill(response=response, body=body)

                    def library_api(route) -> None:
                        nonlocal library_requests
                        response = route.fetch()
                        payload = response.json()
                        library_requests += 1
                        if library_requests > 1 and payload["items"]:
                            payload["items"][0]["title"] += (
                                f" refreshed {library_requests}"
                            )
                        route.fulfill(status=200, json=payload)

                    def preparation(route) -> None:
                        nonlocal requests, finished
                        state, checked, ready = (
                            ("completed", 1, 1)
                            if finished
                            else responses[min(requests, len(responses) - 1)]
                        )
                        requests += 1
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "schema_version": "review-preparation-v1",
                                    "active": state in {"queued", "discovering", "processing"},
                                    "actionable": state == "completed_with_failures",
                                    "paused": False,
                                    "libraries": [
                                        {
                                            "connection": "primary",
                                            "library": "fiction",
                                            "library_id": "lib-1",
                                            "state": state,
                                            "checked": checked,
                                            "total": 1,
                                            "ready": ready,
                                            "blocked": 0,
                                        },
                                        {
                                            "connection": "primary",
                                            "library": "history",
                                            "library_id": "lib-2",
                                            "state": state,
                                            "checked": checked,
                                            "total": 1,
                                            "ready": 0,
                                            "blocked": 1 if state == "completed_with_failures" else 0,
                                        },
                                    ],
                                }
                            ),
                        )

                    page.route("**/library", library_page)
                    page.route("**/api/v1/library*", library_api)
                    page.route("**/api/v1/review-preparation", preparation)
                    _login(page, origin)
                    page.get_by_role("heading", name="Review library").wait_for()
                    search = page.get_by_role("searchbox")
                    search.fill("unsent search")
                    work = page.locator(".work-list a.work-row").first
                    work.focus()
                    work_href = work.get_attribute("href")
                    navigations: list[str] = []
                    page.on("framenavigated", lambda frame: navigations.append(frame.url))
                    page.get_by_text("refreshed 2", exact=False).wait_for(timeout=8_000)
                    self.assertTrue(
                        page.locator(f'.work-list a.work-row[href="{work_href}"]').evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    activity = page.locator(".activity-button")
                    activity.wait_for(state="visible")
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    activity.click()
                    activity_region = page.get_by_role("region", name="Background activity")
                    activity_region.wait_for()
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 1440
                    )
                    self.assertLessEqual(activity_region.bounding_box()["width"], 390)
                    self.assertEqual(
                        page.locator(".topbar").evaluate(
                            "node => getComputedStyle(node).position"
                        ),
                        "sticky",
                    )
                    self.assertEqual(
                        page.locator(".workspace-navigation").evaluate(
                            "node => getComputedStyle(node).position"
                        ),
                        "sticky",
                    )
                    activity_region.locator(".activity-library").first.get_by_text(
                        re.compile(r"\d+ checked · \d+ remaining")
                    ).wait_for(timeout=8_000)
                    page.set_viewport_size({"width": 390, "height": 844})
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"), 390
                    )
                    self.assertLessEqual(activity_region.bounding_box()["width"], 366)
                    activity_region.locator(".activity-library").first.get_by_text(
                        "1 checked · 0 remaining", exact=True
                    ).wait_for(
                        timeout=8_000
                    )
                    activity_region.locator(".activity-library").first.get_by_text(
                        "1 ready · 0 blocked", exact=True
                    ).wait_for()
                    retries = activity_region.get_by_role("button", name="Retry")
                    retries.first.wait_for(timeout=8_000)
                    self.assertEqual(retries.count(), 2)
                    retries.nth(1).focus()
                    page.wait_for_timeout(1_750)
                    self.assertTrue(
                        retries.nth(1).evaluate("node => node === document.activeElement")
                    )
                    page.route(
                        "**/api/v1/review-preparation/**",
                        lambda route: route.fulfill(
                            status=503,
                            content_type="application/json",
                            body='{"error":{"code":"persistence"}}',
                        ),
                    )
                    retries.nth(1).click()
                    failure = activity_region.get_by_role("alert")
                    failure.wait_for()
                    self.assertIn("Retry failed safely", failure.inner_text())
                    self.assertEqual(page.locator(".preparation-panel").count(), 0)
                    page.get_by_role("button", name="Close").click()
                    self.assertTrue(
                        activity.evaluate("node => node === document.activeElement")
                    )
                    lens_link = page.locator('.lenses a[aria-current="page"]')
                    lens_link.focus()
                    activity.click()
                    activity_region.get_by_role("button", name="Close").focus()
                    finished = True
                    activity.wait_for(state="hidden", timeout=8_000)
                    self.assertTrue(
                        lens_link.evaluate("node => node === document.activeElement")
                    )
                    self.assertGreaterEqual(requests, len(responses))
                    self.assertGreaterEqual(library_requests, 2)
                    self.assertEqual(navigations, [])
                    self.assertEqual(search.input_value(), "unsent search")
                    self.assertTrue(
                        lens_link.evaluate("node => node === document.activeElement")
                    )
                    for path, heading, focus_target in (
                        (
                            "/attention",
                            re.compile(r"^Fields needing attention"),
                            '.workspace-tab.attention-tab[aria-current="page"]',
                        ),
                        (
                            f"/review/{case_id}",
                            "Current review case",
                            '.workspace-tab[href="/library"]',
                        ),
                    ):
                        finished = False
                        requests = 0
                        page.goto(f"{origin}{path}")
                        page.get_by_role("heading", name=heading).wait_for()
                        activity = page.locator(".activity-button")
                        activity.wait_for(state="visible", timeout=8_000)
                        activity.click()
                        activity_region = page.get_by_role(
                            "region", name="Background activity"
                        )
                        activity_region.get_by_role("button", name="Close").focus()
                        target = page.locator(focus_target)
                        finished = True
                        activity.wait_for(state="hidden", timeout=8_000)
                        self.assertTrue(
                            target.evaluate("node => node === document.activeElement")
                        )
                    context.close()

    def test_desktop_round_trip_is_explicit_responsive_and_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            database = root / "application.sqlite3"
            port = _unused_port()
            errors: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 1280, "height": 900},
                )
                page = context.new_page()
                _capture_errors(page, errors)
                with _server(
                    database=database,
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    report=_manual_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(page, origin)
                    response = page.goto(f"{origin}/review/{case_id}?field=series")
                    self.assertIsNotNone(response)
                    self.assertIn(
                        "default-src 'none'",
                        response.headers["content-security-policy"],
                    )
                    page.get_by_role("heading", name="Current review case").wait_for()
                    current_field = page.locator(
                        '.field-navigation a[href="#field-series"]'
                    )
                    self.assertEqual(
                        current_field.get_attribute("aria-current"), "true"
                    )
                    first_field = page.locator(".field-navigation a").first
                    first_field.click()
                    self.assertEqual(
                        first_field.get_attribute("aria-current"), "true"
                    )
                    self.assertEqual(current_field.get_attribute("aria-current"), None)
                    page.go_back()
                    self.assertEqual(
                        current_field.get_attribute("aria-current"), "true"
                    )
                    self.assertTrue(
                        page.locator("#field-series").evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.go_forward()
                    self.assertEqual(
                        first_field.get_attribute("aria-current"), "true"
                    )
                    current_field.click()
                    page.go_back()
                    self.assertEqual(
                        first_field.get_attribute("aria-current"), "true"
                    )
                    self.assertTrue(
                        page.locator(first_field.get_attribute("href")).evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    page.go_forward()
                    self.assertEqual(
                        current_field.get_attribute("aria-current"), "true"
                    )
                    page.reload()
                    page.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    current_field = page.locator(
                        '.field-navigation a[href="#field-series"]'
                    )
                    self.assertEqual(
                        current_field.get_attribute("aria-current"), "true"
                    )
                    self.assertEqual(page.locator("img,script:not([src])").count(), 0)
                    self.assertEqual(
                        page.evaluate(
                            "() => [Object.keys(localStorage).length, Object.keys(sessionStorage).length, document.cookie]"
                        ),
                        [0, 0, ""],
                    )
                    self.assertFalse(
                        page.locator("#review").evaluate(
                            "node => node.hasAttribute('data-csrf-token')"
                        )
                    )
                    self.assertLess(page.locator("body *").count(), 500)
                    self.assertLess(
                        len(page.request.get(f"{origin}/assets/review.js").body()),
                        40_000,
                    )
                    strip = page.locator(".field-strip")
                    self.assertEqual(strip.locator(":scope > .field").count(), 3)
                    self.assertEqual(strip.locator("details").count(), 0)
                    self.assertTrue(
                        page.get_by_text("Model advice: not requested").evaluate(
                            "(advice, strip) => Boolean(strip.compareDocumentPosition(advice) & Node.DOCUMENT_POSITION_FOLLOWING)",
                            strip.element_handle(),
                        )
                    )
                    sibling = context.new_page()
                    sibling.goto(f"{origin}/review/{case_id}")
                    sibling.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    sibling.close()
                    previous = page.url
                    page.get_by_role(
                        "button", name=re.compile(r"^Accept proposed value")
                    ).first.press("Enter")
                    retry = page.get_by_role("button", name="Try again")
                    retry.wait_for()
                    page.wait_for_timeout(50)
                    self.assertEqual(page.url, previous)
                    self.assertTrue(
                        retry.evaluate("node => node === document.activeElement")
                    )
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn("status of 403", errors[0])
                    errors.clear()
                    started = time.monotonic()
                    _press_and_wait_for_navigation(
                        page,
                        retry,
                    )
                    self.assertLess(time.monotonic() - started, 5)
                    accepted_url = page.url
                    page.go_back()
                    page.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    self.assertEqual(page.locator(".decision-recorded").count(), 1)
                    resolved_decision = page.locator(".resolved-field").filter(
                        has=page.locator(".decision-recorded")
                    )
                    self.assertIn(
                        "Outcome value:", resolved_decision.text_content() or ""
                    )
                    self.assertEqual(page.locator(".choices").count(), 2)
                    page.go_forward()
                    page.wait_for_url(accepted_url)
                    page.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    focused = page.locator(":focus").get_attribute("id")
                    self.assertIsNotNone(focused)
                    self.assertTrue(focused.startswith("field-"), focused)
                    _press_and_wait_for_navigation(
                        page,
                        page.locator(f"#{focused}").get_by_role(
                            "button", name="Keep current value"
                        ),
                    )
                    focused = page.locator(":focus").get_attribute("id")
                    self.assertIsNotNone(focused)
                    _press_and_wait_for_navigation(
                        page,
                        page.locator(f"#{focused}").get_by_role(
                            "button", name="Reject proposed change"
                        ),
                    )
                    self.assertEqual(page.locator(".choices").count(), 0)
                    self.assertEqual(page.locator(".decision-recorded").count(), 3)
                    decided_url = page.url
                    context.close()
                with _server(
                    database=database,
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                ) as (origin, _):
                    fresh = browser.new_context(
                        ignore_https_errors=True,
                        viewport={"width": 1280, "height": 900},
                    )
                    reopened = fresh.new_page()
                    _capture_errors(reopened, errors)
                    _login(reopened, origin)
                    reopened.goto(decided_url)
                    reopened.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    self.assertEqual(reopened.locator(".decision-recorded").count(), 3)
                    self.assertEqual(reopened.locator(".choices").count(), 0)
                    fresh.close()
                browser.close()
            self.assertEqual(errors, [])

    def test_decision_response_loss_and_conflict_reload_authoritative_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)

                loss_errors: list[str] = []
                loss_context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                loss_page = loss_context.new_page()
                _capture_errors(loss_page, loss_errors)
                with _server(
                    database=root / "response-loss.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                    report=_manual_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(loss_page, origin)
                    loss_page.goto(f"{origin}/review/{case_id}")
                    loss_page.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    attempts = 0
                    keys: list[str] = []
                    statuses: list[int] = []

                    def lose_first_response(route) -> None:
                        nonlocal attempts
                        attempts += 1
                        keys.append(route.request.headers["idempotency-key"])
                        response = route.fetch()
                        statuses.append(response.status)
                        if attempts == 1:
                            route.abort("connectionreset")
                        else:
                            route.fulfill(
                                status=503,
                                content_type="application/json",
                                body='{"error":{"code":"persistence"}}',
                            )

                    loss_page.route("**/decisions", lose_first_response)
                    with loss_page.expect_navigation():
                        loss_page.get_by_role(
                            "button", name="Keep current value"
                        ).first.press("Enter")
                    loss_page.get_by_text(
                        "Human decision: keep current by owner."
                    ).wait_for(state="attached")
                    self.assertEqual(attempts, 2)
                    self.assertEqual(statuses, [200, 200])
                    self.assertEqual(len(set(keys)), 1)
                    self.assertEqual(loss_page.locator(".decision-recorded").count(), 1)
                    self.assertEqual(loss_page.get_by_role("button", name="Try again").count(), 0)
                    self.assertEqual(len(loss_errors), 2, loss_errors)
                    loss_context.close()

                conflict_errors: list[str] = []
                conflict_context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                conflict_page = conflict_context.new_page()
                _capture_errors(conflict_page, conflict_errors)
                with _server(
                    database=root / "conflict.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                    report=_manual_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(conflict_page, origin)
                    conflict_page.goto(f"{origin}/review/{case_id}")
                    conflict_page.get_by_role(
                        "heading", name="Current review case"
                    ).wait_for()
                    conflicts = 0

                    def commit_then_conflict(route) -> None:
                        nonlocal conflicts
                        conflicts += 1
                        response = route.fetch()
                        self.assertEqual(response.status, 200)
                        route.fulfill(
                            status=409,
                            content_type="application/json",
                            body='{"error":{"code":"already_decided"}}',
                        )

                    conflict_page.route("**/decisions", commit_then_conflict)
                    with conflict_page.expect_navigation():
                        conflict_page.get_by_role(
                            "button", name="Keep current value"
                        ).first.press("Enter")
                    conflict_page.get_by_text(
                        "Human decision: keep current by owner."
                    ).wait_for(state="attached")
                    self.assertEqual(conflicts, 1)
                    self.assertEqual(conflict_page.locator(".decision-recorded").count(), 1)
                    self.assertEqual(conflict_page.locator(".choices").count(), 2)
                    self.assertEqual(conflict_page.get_by_role("button", name="Try again").count(), 0)
                    conflict_page.unroute("**/decisions")
                    conflict_page.route(
                        "**/decisions",
                        lambda route: route.fulfill(
                            status=409,
                            content_type="application/json",
                            body='{"error":{"code":"stale"}}',
                        ),
                    )
                    with conflict_page.expect_navigation():
                        conflict_page.get_by_role(
                            "button", name="Keep current value"
                        ).first.press("Enter")
                    conflict_page.get_by_role(
                        "heading", name="Review library"
                    ).wait_for()
                    self.assertIn("/library", conflict_page.url)
                    self.assertEqual(len(conflict_errors), 2, conflict_errors)
                    conflict_context.close()

                browser.close()

    def test_compact_failure_retry_and_automatic_only_detail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            hash_path = root / "owner.hash"
            hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
            os.chmod(hash_path, 0o600)
            certificate, key = _certificate(root)
            port = _unused_port()
            errors: list[str] = []
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                page = context.new_page()
                _capture_errors(page, errors)
                with _server(
                    database=root / "manual.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    report=_hostile_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(page, origin)
                    page.set_viewport_size({"width": 320, "height": 700})
                    for lens in ("all", "no_attention_needed"):
                        page.goto(f"{origin}/library?lens={lens}")
                        page.get_by_role("heading", name="Review library").wait_for()
                        page.get_by_label("Search title, library, or item").fill(
                            "ÅNGSTRÖM"
                        )
                        with page.expect_navigation():
                            page.get_by_role("button", name="Search").press("Enter")
                        self.assertIn(f"lens={lens}", page.url)
                        self.assertIn("q=%C3%85NGSTR%C3%96M", page.url)
                        self.assertLessEqual(
                            page.evaluate(
                                "() => document.documentElement.scrollWidth"
                            ),
                            320,
                        )
                    page.goto(f"{origin}/attention")
                    page.get_by_role(
                        "heading", name=re.compile(r"^Fields needing attention")
                    ).wait_for()
                    page.get_by_label("Search title, library, or item").fill("title")
                    with page.expect_navigation():
                        page.get_by_role("button", name="Search").press("Enter")
                    self.assertIn("/attention?q=title", page.url)
                    self.assertNotIn("lens=", page.url)
                    page.set_viewport_size({"width": 390, "height": 844})

                    evidence_failures = 0

                    def evidence_pages(route) -> None:
                        nonlocal evidence_failures
                        second_page = "cursor=" in route.request.url
                        if second_page and evidence_failures == 0:
                            evidence_failures += 1
                            route.fulfill(
                                status=503,
                                content_type="application/json",
                                body='{"error":{"code":"persistence"}}',
                            )
                            return
                        start = 50 if second_page else 0
                        count = 8 if second_page else 50
                        items = [
                            {
                                "candidate": "candidate-1",
                                "fact": json.dumps(
                                    {
                                        "observed": (
                                            f"hostile <img src=x> evidence {index}"
                                        )
                                    }
                                ),
                                "kind": "title",
                                "provider": "catalogue",
                                "reference": f"local_{index:064x}",
                                "source": "provider_claim",
                            }
                            for index in range(start, start + count)
                        ]
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "items": items,
                                    "next_cursor": None if second_page else "page2",
                                    "review_case_id": case_id,
                                    "schema_version": "review-case-evidence-v2",
                                }
                            ),
                        )

                    page.route("**/evidence*", evidence_pages)
                    page.goto(
                        f"{origin}/review/{case_id}?decided=subtitle&field=unknown%5B"
                    )
                    page.get_by_role("heading", name="Current review case").wait_for()
                    self.assertEqual(page.locator(".toast").count(), 0)
                    self.assertTrue(
                        page.locator(".field").first.evaluate(
                            "node => node === document.activeElement"
                        )
                    )
                    self.assertEqual(page.locator("section pre").count(), 50)
                    page.get_by_role("button", name="Load more evidence").click()
                    evidence_status = page.locator(
                        ".ledger .details-inner > p[role]"
                    )
                    page.get_by_text(
                        "More evidence could not be loaded. Try again."
                    ).wait_for()
                    self.assertEqual(evidence_status.get_attribute("role"), "alert")
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn("status of 503", errors[0])
                    errors.clear()
                    page.get_by_role("button", name="Load more evidence").click()
                    page.get_by_text("All referenced evidence is shown.").wait_for()
                    self.assertEqual(evidence_status.get_attribute("role"), "status")
                    self.assertEqual(page.locator("section pre").count(), 58)
                    self.assertEqual(
                        page.get_by_role("button", name="Load more evidence").count(),
                        0,
                    )
                    self.assertEqual(page.locator("img").count(), 0)
                    self.assertIn("<img", page.locator("body").inner_text())
                    self.assertLessEqual(
                        page.evaluate("() => document.documentElement.scrollWidth"),
                        390,
                    )
                    page.route(
                        "**/decisions",
                        lambda route: route.fulfill(
                            status=401,
                            content_type="application/json",
                            body='{"error":{"code":"authentication_required"}}',
                        ),
                    )
                    page.get_by_role("button", name="Keep current value").first.press(
                        "Enter"
                    )
                    sign_in = page.get_by_role("link", name="Sign in again")
                    sign_in.wait_for()
                    page.wait_for_timeout(50)
                    self.assertTrue(
                        sign_in.evaluate("node => node === document.activeElement")
                    )
                    self.assertIn(
                        "Your choice is unchanged", page.get_by_role("alert").inner_text()
                    )
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn("status of 401", errors[0])
                    errors.clear()
                    page.unroute("**/decisions")
                    page.reload()
                    page.get_by_role("heading", name="Current review case").wait_for()
                    failures = 0
                    network_statuses: list[int] = []

                    def fail_once(route) -> None:
                        nonlocal failures
                        failures += 1
                        if failures == 1:
                            route.fulfill(
                                status=503,
                                content_type="application/json",
                                body='{"error":{"code":"persistence"}}',
                            )
                        else:
                            response = route.fetch()
                            network_statuses.append(response.status)
                            route.fulfill(response=response)

                    page.route("**/decisions", fail_once)
                    page.get_by_role("button", name="Reject proposed change").first.press(
                        "Enter"
                    )
                    retry = page.get_by_role("button", name="Try again")
                    retry.wait_for()
                    page.wait_for_timeout(50)
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn("status of 503", errors[0])
                    errors.clear()
                    self.assertTrue(retry.evaluate("node => node === document.activeElement"))
                    self.assertIn("Your choice is unchanged", page.get_by_role("alert").inner_text())
                    previous = page.url
                    retry.click()
                    page.wait_for_timeout(1_000)
                    self.assertNotEqual(
                        page.url,
                        previous,
                        f"statuses={network_statuses}\n{page.locator('body').inner_text()}",
                    )
                    page.locator(".toast").wait_for()
                    self.assertEqual(failures, 2)
                    context.close()

                automatic_context = browser.new_context(
                    ignore_https_errors=True,
                    viewport={"width": 390, "height": 844},
                )
                automatic = automatic_context.new_page()
                _capture_errors(automatic, errors)
                with _server(
                    database=root / "automatic.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=port,
                    report=_automatic_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(automatic, origin)
                    automatic.goto(f"{origin}/review/{case_id}")
                    automatic.get_by_role("heading", name="Current review case").wait_for()
                    self.assertEqual(automatic.locator(".choices").count(), 0)
                    self.assertEqual(
                        automatic.get_by_text("No human decision recorded.").count(), 0
                    )
                    self.assertLessEqual(
                        automatic.evaluate(
                            "() => document.documentElement.scrollWidth"
                        ),
                        390,
                    )
                    automatic_context.close()
                browser.close()
            self.assertEqual(errors, [])

    def test_variant_m_light_dark_visual_matrix_is_bounded_and_persistent(
        self,
    ) -> None:
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
                    viewport={"width": 1440, "height": 1000},
                )
                page = context.new_page()
                _capture_errors(page, errors)
                with _server(
                    database=root / "visual.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=_unused_port(),
                    report=_manual_report(),
                ) as (origin, case_id):
                    assert case_id is not None
                    _login(page, origin)

                    for width in (1440, 884, 390, 320):
                        page.set_viewport_size({"width": width, "height": 1000})
                        for selected_theme in ("light", "dark"):
                            page.goto(f"{origin}/library")
                            page.get_by_role(
                                "heading", name="Review library"
                            ).wait_for()
                            current_theme = page.locator("html").get_attribute(
                                "data-theme"
                            )
                            if current_theme != selected_theme:
                                page.locator(".theme-toggle").press("Enter")
                            self.assertEqual(
                                page.locator("html").get_attribute("data-theme"),
                                selected_theme,
                            )
                            self.assertEqual(
                                page.locator(".theme-toggle").get_attribute(
                                    "aria-pressed"
                                ),
                                str(selected_theme == "dark").lower(),
                            )

                            for path, heading_name in (
                                ("/library", "Review library"),
                                ("/attention", "Fields needing attention"),
                                (f"/review/{case_id}", "Current review case"),
                            ):
                                page.goto(f"{origin}{path}")
                                page.get_by_role(
                                    "heading",
                                    name=(
                                        re.compile(r"^Fields needing attention")
                                        if path == "/attention"
                                        else heading_name
                                    ),
                                ).wait_for()
                                self.assertEqual(
                                    page.locator("html").get_attribute("data-theme"),
                                    selected_theme,
                                )
                                self.assertLessEqual(
                                    page.evaluate(
                                        "() => document.documentElement.scrollWidth"
                                    ),
                                    width,
                                )
                                screenshot = page.screenshot(
                                    full_page=True,
                                    animations="disabled",
                                    caret="hide",
                                )
                                self.assertTrue(screenshot.startswith(b"\x89PNG\r\n\x1a\n"))
                                self.assertGreater(len(screenshot), 1_000)
                                if path.startswith("/review/") and selected_theme == "dark":
                                    contrast = _contrast_ratio(
                                        page.locator(".choices button").first
                                    )
                                    self.assertGreaterEqual(contrast, 4.5)

                            page.reload()
                            page.get_by_role(
                                "heading", name="Current review case"
                            ).wait_for()
                            self.assertEqual(
                                page.locator("html").get_attribute("data-theme"),
                                selected_theme,
                            )
                            self.assertEqual(
                                page.evaluate(
                                    "() => [Object.keys(localStorage).length, Object.keys(sessionStorage).length]"
                                ),
                                [0, 0],
                            )
                    sibling = context.new_page()
                    sibling.goto(f"{origin}/library")
                    sibling.get_by_role(
                        "heading", name="Review library"
                    ).wait_for()
                    sibling.close()
                    with page.expect_navigation():
                        page.get_by_role("button", name="Sign out").press("Enter")
                    page.get_by_role(
                        "heading", name="Review your library with confidence."
                    ).wait_for()
                    self.assertEqual(len(errors), 1, errors)
                    self.assertIn("status of 403", errors[0])
                    errors.clear()
                    login_contrast = _contrast_ratio(page.locator(".primary-button"))
                    self.assertGreaterEqual(login_contrast, 4.5)
                    context.close()
                browser.close()
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
