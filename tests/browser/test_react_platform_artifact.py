from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from playwright.sync_api import Response, Route, expect, sync_playwright

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetBundle,
    load_react_asset_bundle,
)
from tests.browser.fidelity_cohort import (
    BUILD_IDENTITY as _BUILD_IDENTITY,
)
from tests.browser.fidelity_cohort import (
    HOSTILE_TEXT as _HOSTILE,
)
from tests.browser.fidelity_cohort import (
    LONG_TITLE,
    UNBROKEN_TITLE_SEGMENT,
    rich_payload,
)


class ReactPlatformArtifactTests(unittest.TestCase):
    def test_exact_built_shell_supports_every_direct_route_and_client_navigation(self) -> None:
        self._browser_stage = "test-setup"
        try:
            self._exercise_exact_built_shell_supports_every_direct_route_and_client_navigation()
        except Exception:
            print(
                json.dumps(
                    {
                        "schema": "protected-browser-failure-stage-v1",
                        "stage": self._browser_stage,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                file=sys.stderr,
                flush=True,
            )
            raise

    def _exercise_exact_built_shell_supports_every_direct_route_and_client_navigation(
        self,
    ) -> None:
        root_value = os.environ.get("ML_REACT_ASSET_DIR")
        self.assertIsNotNone(root_value, "ML_REACT_ASSET_DIR is required")
        bundle = load_react_asset_bundle(Path(root_value or ""))
        self.assertIsInstance(bundle, ReactAssetBundle)
        errors: list[str] = []
        documents = 0
        fail_activity_once = False
        activity_failures = 0
        ambiguous_decisions = 0
        fail_policy_persistently = False
        consent_payloads: list[object] = []
        return_automatic_requests = 0
        failed_requests: list[tuple[str, str, str | None]] = []

        def fulfill(route: Route) -> None:
            nonlocal activity_failures, ambiguous_decisions, documents
            nonlocal return_automatic_requests
            parsed = urlsplit(route.request.url)
            path = parsed.path
            if path.startswith("/api/v1/"):
                if path == "/api/v1/review-cases/missing-case":
                    route.fulfill(
                        status=404,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "error": {"code": "not_found"},
                                "schema_version": "review-error-v1",
                            }
                        ),
                    )
                    return
                if path.endswith("/decisions") and route.request.method == "POST":
                    ambiguous_decisions += 1
                    route.abort("failed")
                    return
                if (
                    path.endswith("/return-to-automatic")
                    and route.request.method == "POST"
                ):
                    return_automatic_requests += 1
                    route.fulfill(status=204, body="")
                    return
                if path == "/api/v1/session/logout":
                    route.fulfill(status=204, headers={"x-ml-build-identity": "c" * 64}, body="")
                    return
                if path == "/api/v1/activity/consent" and route.request.method == "POST":
                    consent_payloads.append(route.request.post_data_json)
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps(rich_payload("/api/v1/activity")),
                    )
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(rich_payload(route.request.url)),
                )
                return
            prefix = "/assets/review-app/"
            if path.startswith(prefix):
                relative = path.removeprefix(prefix)
                if fail_activity_once and "ActivityPage-" in relative and activity_failures == 0:
                    activity_failures += 1
                    route.fulfill(status=503, body="")
                    return
                if fail_policy_persistently and "PolicyPage-" in relative:
                    route.fulfill(status=503, body="")
                    return
                content_type = "text/css" if relative.endswith(".css") else "text/javascript"
                route.fulfill(status=200, content_type=content_type, body=bundle.read(relative))
                return
            if route.request.resource_type == "document":
                documents += 1
                shell = bundle.read(bundle.entry).decode("utf-8").replace("<body>", f'<body data-build-identity="{_BUILD_IDENTITY}" data-csrf-token="{"b" * 32}">')
                route.fulfill(status=200, content_type="text/html", headers={"content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"}, body=shell)
                return
            route.fulfill(status=404, body="")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            capture_directory = os.environ.get("ML_VISUAL_CAPTURE_DIR")

            def capture_visual_proof(name: str) -> None:
                if capture_directory is None:
                    return
                output = Path(capture_directory)
                output.mkdir(parents=True, exist_ok=True)
                viewport = page.viewport_size
                try:
                    for scheme in ("light", "dark"):
                        control = "Use dark mode" if scheme == "dark" else "Use light mode"
                        if page.get_by_role("button", name=control).count():
                            page.get_by_role("button", name=control).click()
                        page.emulate_media(reduced_motion="reduce")
                        for width, height, size in (
                            (1440, 900, "desktop"),
                            (390, 844, "compact"),
                        ):
                            page.set_viewport_size({"width": width, "height": height})
                            page.screenshot(
                                path=str(output / f"{name}-{size}-{scheme}.png"),
                                full_page=True,
                            )
                finally:
                    page.emulate_media(
                        color_scheme="light", reduced_motion="no-preference"
                    )
                    if page.get_by_role("button", name="Use light mode").count():
                        page.get_by_role("button", name="Use light mode").click()
                    if viewport is not None:
                        page.set_viewport_size(viewport)

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(
                    (request.method, request.url, request.failure)
                ),
            )
            page.route("**/*", fulfill)
            routes = (
                ("/library?lens=all", "Every work, with exceptions first"),
                ("/attention", "Needs your attention"),
                ("/review/case-1?field=title&subview=review", "Current review case"),
                ("/policy?section=identity", "Library policy"),
                ("/policy?section=publication", "Library policy"),
                ("/policy?section=curation", "Library policy"),
                ("/policy?section=unsupported", "Library policy"),
                ("/activity", "Activity and recovery"),
            )
            for path, heading in routes:
                self._browser_stage = f"direct-route:{path}"
                fail_activity_once = path == "/activity"
                before_entry = documents
                response = page.goto(f"https://react-platform.invalid{path}")
                self.assertIsNotNone(response)
                self.assertEqual(cast(Response, response).status, 200)
                page.get_by_role("heading", name=heading).wait_for()
                if path in {
                    "/library?lens=all",
                    "/review/case-1?field=title&subview=review",
                    "/activity",
                }:
                    page.get_by_text(
                        "Library connection needs attention", exact=True
                    ).first.wait_for()
                    notice = page.get_by_label("Shared library connection status")
                    check = notice.get_by_role("button", name="Check connection again")
                    expect(check).to_be_visible()
                    self.assertNotIn("primary", notice.inner_text())
                    self.assertNotIn("http", notice.inner_text())
                    for width, expected_columns in (
                        (1440, 3),
                        (1044, 2),
                        (800, 1),
                        (390, 1),
                    ):
                        page.set_viewport_size({"width": width, "height": 844})
                        self.assertEqual(
                            expected_columns,
                            len(
                                notice.evaluate(
                                    "node => getComputedStyle(node).gridTemplateColumns.split(' ')"
                                )
                            ),
                        )
                        self.assertLessEqual(
                            page.evaluate("() => document.documentElement.scrollWidth"),
                            width,
                        )
                        self.assertTrue(
                            check.evaluate(
                                "node => { const box = node.getBoundingClientRect(); "
                                "const top = document.elementFromPoint(box.x + box.width / 2, "
                                "box.y + box.height / 2); return top === node || node.contains(top); }"
                            )
                        )
                if path == "/attention":
                    page.get_by_text(
                        "This field needs your review.", exact=True
                    ).first.wait_for()
                    self.assertEqual(
                        0,
                        page.get_by_text(
                            "Conflicting retained evidence", exact=True
                        ).count(),
                    )
                if path.startswith("/policy"):
                    section = path.rsplit("=", 1)[-1]
                    expect(page.locator(f"#policy-{section}")).to_be_focused()
                    expect(
                        page.get_by_role("navigation", name="Policy chapters")
                        .locator("button[aria-current='page']")
                    ).to_have_count(1)
                    self.assertTrue(page.url.endswith(path), f"{path} restored as {page.url}")
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
                self.assertEqual(page.locator("img, script:not([src])").count(), 0)
                self.assertIsNone(page.evaluate("() => window.__metadataExecuted"))
                if path == "/activity":
                    self._browser_stage = "activity:compact-initial-geometry"
                    expect(page.locator(".cm-page-heading")).to_be_focused()
                    recovery = page.get_by_role("region", name="Provider recovery")
                    expected_recovery = {
                        "Waiting for a safe retry": "2 affected scopes",
                        "Checking provider health": "1 affected scope",
                        "Retrying affected work": "3 affected works",
                        "Recovered": "1 affected scope",
                        "Needs a configuration change": "2 affected scopes",
                        "No further retry scheduled": "1 affected scope",
                    }
                    for label, units in expected_recovery.items():
                        row = recovery.get_by_text(label, exact=True).locator("..")
                        expect(row).to_contain_text(units)
                    recovery_text = recovery.inner_text().lower()
                    for forbidden in (
                        "provider_", "query=", "authorization:", "bearer ",
                        "payload", "/private/", "x-api-key",
                    ):
                        self.assertNotIn(forbidden, recovery_text)
                    self.assertEqual(
                        recovery.get_by_role("button", name="Retry", exact=False).count(),
                        0,
                    )
                    activity_ledger = page.locator(".cm-activity-ledger")
                    for work, label in (
                        ("Synthetic completed cover advice", "Cover advice completed"),
                        ("Synthetic unavailable cover advice", "Cover advice unavailable"),
                        ("Synthetic failed cover advice", "Cover advice failed safely"),
                        ("Synthetic stale cover advice", "Stale"),
                        ("Synthetic superseded cover advice", "Superseded"),
                    ):
                        row = activity_ledger.get_by_text(
                            work, exact=True
                        ).locator("xpath=ancestor::article")
                        expect(row).to_have_count(1)
                        expect(row.get_by_text(label, exact=True)).to_have_count(1)
                    capture_visual_proof("activity-labels")
                    self.assertTrue(
                        page.locator(".cm-activity-ledger").evaluate(
                            "node => node.previousElementSibling?.classList.contains('cm-activity-status')"
                        )
                    )
                    long_activity_title = page.locator(
                        ".cm-activity-outcome > strong"
                    ).filter(has_text=LONG_TITLE)
                    self.assertEqual(long_activity_title.get_attribute("title"), LONG_TITLE)
                    self.assertEqual(
                        long_activity_title.evaluate(
                            "node => getComputedStyle(node).webkitLineClamp"
                        ),
                        "2",
                    )
                    title_box = long_activity_title.bounding_box()
                    row_box = long_activity_title.locator(
                        "xpath=ancestor::article"
                    ).bounding_box()
                    self.assertIsNotNone(title_box)
                    self.assertIsNotNone(row_box)
                    compact_geometry = {
                        "title_height": title_box["height"],
                        "row_height": row_box["height"],
                    }
                    compact_row = long_activity_title.locator(
                        "xpath=ancestor::article"
                    )
                    self.assertEqual(
                        compact_row.evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns"
                        ).split(" ", 1)[0],
                        "44px",
                    )
                    self.assertEqual(
                        compact_row.locator("b").evaluate(
                            "node => getComputedStyle(node).gridRow"
                        ),
                        "3",
                    )
                    self.assertEqual(
                        compact_row.get_by_role("button", name="Inspect").evaluate(
                            "node => getComputedStyle(node).gridRow"
                        ),
                        "4",
                    )
                    self.assertLessEqual(title_box["height"], 36, compact_geometry)
                    self.assertLessEqual(
                        row_box["height"],
                        182,
                        compact_geometry,
                    )
                    page.set_viewport_size({"width": 1440, "height": 900})
                    inspect = page.get_by_role("button", name="Inspect").first
                    self._browser_stage = "activity:desktop-media-state"
                    page.wait_for_function(
                        """() => {
                            const inspect = document.querySelector('.cm-activity-events article > button');
                            const title = document.querySelector('.cm-activity-outcome > strong');
                            return window.matchMedia('(min-width: 761px)').matches
                                && inspect instanceof HTMLElement
                                && title instanceof HTMLElement
                                && getComputedStyle(inspect).whiteSpace === 'nowrap'
                                && getComputedStyle(title).whiteSpace === 'nowrap';
                        }"""
                    )
                    self._browser_stage = "activity:desktop-geometry"
                    inspect_box = inspect.bounding_box()
                    row_box = inspect.locator("xpath=ancestor::article").bounding_box()
                    self.assertIsNotNone(inspect_box)
                    self.assertIsNotNone(row_box)
                    self.assertEqual(inspect.evaluate("node => getComputedStyle(node).whiteSpace"), "nowrap")
                    self.assertLessEqual(row_box["height"], 72)
                    self.assertEqual(
                        inspect.evaluate("node => getComputedStyle(node).minHeight"),
                        "40px",
                    )
                    self.assertLessEqual(inspect_box["height"], 40.5)
                    page.set_viewport_size({"width": 390, "height": 844})
                    self._browser_stage = "activity:compact-media-state"
                    page.wait_for_function(
                        """() => {
                            const title = document.querySelector('.cm-activity-outcome > strong');
                            return window.matchMedia('(max-width: 760px)').matches
                                && title instanceof HTMLElement
                                && getComputedStyle(title).webkitLineClamp === '2';
                        }"""
                    )
                    self._browser_stage = "activity:authority-and-recovery"
                    theme_backgrounds: dict[str, str] = {}
                    for scheme in ("light", "dark"):
                        page.emulate_media(reduced_motion="reduce")
                        wanted_control = "Use dark mode" if scheme == "dark" else "Use light mode"
                        if page.get_by_role("button", name=wanted_control).count():
                            page.get_by_role("button", name=wanted_control).click()
                        for width in (1440, 1024, 390):
                            page.set_viewport_size({"width": width, "height": 900})
                            expect(recovery).to_be_visible()
                            self.assertLessEqual(
                                page.evaluate("() => document.documentElement.scrollWidth"),
                                width,
                            )
                            for control in page.locator(
                                ".cm-activity-filters button, .cm-activity-pagination button"
                            ).all():
                                self.assertGreaterEqual(control.bounding_box()["height"], 40)
                            for control in page.locator(
                                ".masthead nav a, .shell-actions button"
                            ).all():
                                self.assertGreaterEqual(control.bounding_box()["height"], 44)
                        theme_backgrounds[scheme] = page.locator("body").evaluate(
                            "node => getComputedStyle(node).backgroundColor"
                        )
                    self.assertNotEqual(
                        theme_backgrounds["light"], theme_backgrounds["dark"]
                    )
                    self.assertTrue(
                        page.evaluate(
                            "() => matchMedia('(prefers-reduced-motion: reduce)').matches"
                        )
                    )
                    self.assertEqual(
                        recovery.evaluate("node => getComputedStyle(node).animationName"),
                        "none",
                    )
                    page.get_by_role("button", name="Use light mode").click()
                    page.emulate_media(reduced_motion="no-preference")
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.get_by_text("Authority and recovery", exact=True).click()
                    page.get_by_role(
                        "button", name="Review write permissions"
                    ).click()
                    page.get_by_role("heading", name="Enable guarded writes").wait_for()
                    page.locator(".activity-consent-library").filter(
                        has_text="Household ebooks"
                    ).get_by_text("5 prepared · Some destinations are ready").wait_for()
                    page.get_by_role("heading", name="Activity ledger").wait_for()
                    page.get_by_label("Search activity").wait_for()
                    for width, height in ((1440, 900), (390, 844)):
                        self._browser_stage = f"activity:escape-dropdown-focus:{width}"
                        page.set_viewport_size({"width": width, "height": height})
                        page_number = page.get_by_label("Activity page number")
                        page_url = page.url
                        page_number.focus()
                        page_number.fill("2")
                        page.keyboard.press("Escape")
                        self.assertEqual("1", page_number.input_value())
                        self.assertEqual(page_url, page.url)
                        expect(page_number).to_be_focused()
                        date_range = page.get_by_role(
                            "combobox", name="Activity date range"
                        )
                        date_range.click()
                        page.get_by_role("option", name="Last 90 days").click()
                        expect(date_range).to_be_focused()
                        page_number = page.get_by_label("Activity page number")
                        page_url = page.url
                        page_number.focus()
                        page_number.fill("2")
                        page.keyboard.press("Escape")
                        self.assertEqual("1", page_number.input_value())
                        self.assertEqual(page_url, page.url)
                        expect(page_number).to_be_focused()
                        page_size = page.get_by_role(
                            "combobox", name="Rows per activity page"
                        )
                        page_size.click()
                        page.get_by_role("option", name="50 per page").click()
                        expect(page_size).to_be_focused()
                    page.get_by_role("button", name="Reviews").click()
                    page.get_by_role("button", name="All").click()
                    inspect_control = page.get_by_role("button", name="Inspect").first
                    inspect_control.wait_for()
                    expect(inspect_control).to_be_visible()
                    inspect_control.focus()
                    page.keyboard.press("Enter")
                    page.get_by_text("Durable evidence").wait_for()
                    self.assertEqual(
                        page.locator(".cm-activity-evidence").evaluate(
                            "node => getComputedStyle(node).gridRow"
                        ),
                        "5",
                    )
                    page.get_by_text("Recorded origin").wait_for()
                    page.locator(".cm-activity-evidence").get_by_text(
                        "Outcome", exact=True
                    ).wait_for()
                    self.assertNotIn(
                        "future_provider_origin",
                        page.locator(".cm-activity-ledger").inner_text(),
                    )
                    page.locator(".activity-consent-library").filter(
                        has_text="Spoken stories"
                    ).get_by_role("checkbox").check()
                    self._browser_stage = "activity:consent-refresh"
                    page.get_by_role("button", name="Enable selected libraries").click()
                    page.get_by_text(
                        "Activity state refreshed from the authoritative server."
                    ).wait_for()
                self._browser_stage = f"direct-route:{path}:document-count"
                self.assertEqual(
                    documents,
                    before_entry + (2 if path == "/activity" else 1),
                )

            for case_id, status, retry in (
                ("case-1", "Advisory visual review complete", False),
                ("case-2", "Manual comparison available", True),
                ("case-3", "Manual comparison available", True),
            ):
                self._browser_stage = f"cover-detail:{case_id}"
                page.goto(
                    f"https://react-platform.invalid/review/{case_id}"
                    "?field=cover&subview=review"
                )
                page.get_by_text(status, exact=True).wait_for()
                expect(page.get_by_role("button", name="Keep current cover")).to_have_attribute(
                    "aria-pressed", "false"
                )
                expect(page.get_by_role("button", name="Choose this cover")).to_have_attribute(
                    "aria-pressed", "false"
                )
                expect(page.get_by_role("button", name="Take no action")).to_have_attribute(
                    "aria-pressed", "false"
                )
                self.assertEqual(
                    1 if retry else 0,
                    page.get_by_role("button", name="Retry advisory review").count(),
                )
                if case_id in {"case-1", "case-2"}:
                    capture_visual_proof(
                        "detail-completed" if case_id == "case-1" else "detail-failed"
                    )

            for case_id, label, retry in (
                ("case-1", "Advisory visual review complete", False),
                ("case-2", "Manual comparison available", True),
                ("case-3", "Manual comparison available", True),
            ):
                self._browser_stage = f"cover-library:{case_id}"
                page.goto(
                    "https://react-platform.invalid/library?lens=all"
                    f"&preview={case_id}&field=cover"
                )
                comparison = page.get_by_role("region", name="Choose a cover")
                comparison.get_by_text(label, exact=True).wait_for()
                self.assertIn(
                    "advice only" if not retry else "comparison remains manual",
                    comparison.inner_text(),
                )
                for choice in comparison.locator("button[aria-pressed]").all():
                    expect(choice).to_be_enabled()
                    expect(choice).to_have_attribute("aria-pressed", "false")
                for evidence in comparison.get_by_role("button", name=re.compile(r"^Evidence for .+ cover$")).all():
                    expect(evidence).to_be_enabled()
                    expect(evidence).to_have_attribute("aria-expanded", "false")
                self.assertEqual(
                    1 if retry else 0,
                    comparison.get_by_role(
                        "button", name="Retry advisory review"
                    ).count(),
                )
                if case_id in {"case-1", "case-2"}:
                    capture_visual_proof(
                        "library-completed" if case_id == "case-1" else "library-failed"
                    )
                if case_id == "case-1":
                    current_evidence = comparison.get_by_role("button", name="Evidence for Current cover")
                    current_evidence.click()
                    expect(current_evidence).to_have_attribute("aria-expanded", "true")
                    comparison.get_by_text("Local library record", exact=True).wait_for()
                    expect(comparison.locator(".library-cover-card.is-current")).to_have_attribute("aria-pressed", "false")
                    alternative_evidence = comparison.get_by_role("button", name="Evidence for Alternative 1 cover")
                    alternative_evidence.focus()
                    page.keyboard.press("Enter")
                    expect(alternative_evidence).to_be_focused()
                    expect(alternative_evidence).to_have_attribute("aria-expanded", "true")
                    comparison.get_by_text("Open Library", exact=True).wait_for()
                    comparison.get_by_text("External corroboration", exact=True).wait_for()
                    expect(comparison.locator(".library-cover-card:not(.is-current)")).to_have_attribute("aria-pressed", "false")
                if case_id == "case-3":
                    unavailable_evidence = comparison.get_by_role("button", name="Evidence for Current cover")
                    unavailable_evidence.focus()
                    page.keyboard.press("Enter")
                    expect(unavailable_evidence).to_have_attribute("aria-expanded", "true")
                    comparison.get_by_text("Local presence", exact=True).wait_for()
                    comparison.get_by_text("Preview unavailable", exact=True).wait_for()

            self._browser_stage = "cover-library:cached-work-reset"
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all&preview=case-1&field=cover")
            page.locator(".library-works").wait_for()
            page.locator(".workspace-detail").wait_for()
            page.get_by_role("button", name="Evidence for Current cover").click()
            page.get_by_text("Local library record", exact=True).wait_for()
            work_b = page.locator(".library-work-row").filter(has_text=LONG_TITLE).locator(".work-select")
            self.assertEqual(1, work_b.count())
            work_b.click()
            page.locator(".field-filmstrip").wait_for()
            page.locator(".field-filmstrip").get_by_role("button", name=re.compile(r"Cover")).click()
            page.get_by_role("region", name="Choose a cover").wait_for()
            self.assertEqual(0, page.locator(".choice-evidence").count())
            work_a = page.locator(".library-work-row").filter(has_text=_HOSTILE).locator(".work-select")
            self.assertEqual(1, work_a.count())
            work_a.click()
            page.locator(".field-filmstrip").wait_for()
            page.locator(".field-filmstrip").get_by_role("button", name=re.compile(r"Cover")).click()
            page.get_by_role("region", name="Choose a cover").wait_for()
            self.assertEqual(0, page.locator(".choice-evidence").count())

            missing_case_error_start = len(errors)
            for _ in range(2):
                response = page.goto(
                    "https://react-platform.invalid/review/missing-case"
                    "?field=title&subview=review"
                )
                self.assertIsNotNone(response)
                self.assertEqual(cast(Response, response).status, 200)
                page.get_by_role(
                    "heading", name="This workspace could not be loaded."
                ).wait_for()
                page.get_by_role("button", name="Try again").wait_for()
            missing_case_errors = errors[missing_case_error_start:]
            self.assertEqual(len(missing_case_errors), 4)
            self.assertEqual(
                sum(
                    "status of 404 (Not Found)" in error
                    for error in missing_case_errors
                ),
                2,
            )
            self.assertEqual(
                sum(
                    "Review API request failed (404: not_found)" in error
                    for error in missing_case_errors
                ),
                2,
            )
            del errors[missing_case_error_start:]

            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all")
            page.get_by_text("Catalogue-only work", exact=True).wait_for()
            expect(page).to_have_url("https://react-platform.invalid/library?lens=all&preview=case-1")
            self.assertEqual(
                page.locator(".masthead nav").get_by_text("Attention", exact=True).count(),
                0,
            )
            expect(page.locator(".workspace-detail .direct-choice-grid .value-choice")).to_have_count(4)
            expect(page.locator(".workspace-detail .choice-evidence-toggle")).to_have_count(4)
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_visible()
            # #598 keeps automatic, unsupported, and recorded siblings in the
            # primary strip; only server-classified attention advances normally.
            expect(page.locator(".field-filmstrip button")).to_have_count(5)
            self.assertIn("Title", page.locator(".field-filmstrip").inner_text())
            self.assertIn("Cover", page.locator(".field-filmstrip").inner_text())
            self.assertFalse(page.locator(".automatic-resolution-details").evaluate("node => node.open"))
            self.assertLess(
                page.locator(".direct-choice").bounding_box()["y"],
                page.locator(".automatic-resolution-details").bounding_box()["y"],
            )
            page.locator(".automatic-resolution-details summary").click()
            automatic_context = page.locator(".automatic-resolution-details").inner_text()
            self.assertIn("Subtitle", automatic_context)
            self.assertIn("Series", automatic_context)
            self.assertIn("Resolved automatically", automatic_context)
            self.assertIn("No eligible change", automatic_context)
            current_evidence = page.get_by_role(
                "button", name="Evidence for Current value: Current <script> title"
            )
            current_evidence.click()
            expect(current_evidence).to_be_focused()
            expect(current_evidence).to_have_attribute("aria-expanded", "true")
            expect(page.locator("#choice-evidence-current")).to_be_visible()
            workspace_evidence = page.locator("#choice-evidence-current").inner_text()
            self.assertIn("Local library record", workspace_evidence)
            self.assertIn("Local presence", workspace_evidence)
            self.assertIn("No recommendation retained", page.locator(".metadata-choice.is-current").inner_text())
            proposed_evidence = page.get_by_role(
                "button", name="Evidence for Alternative 1: The Clockmaker's Cartography"
            )
            proposed_evidence.focus()
            page.keyboard.press("Enter")
            expect(proposed_evidence).to_be_focused()
            expect(proposed_evidence).to_have_attribute("aria-expanded", "true")
            self.assertIn("Open Library", page.locator(".choice-evidence").nth(1).inner_text())
            self.assertEqual(page.locator(".source-ledger").count(), 0)
            self.assertGreaterEqual(current_evidence.bounding_box()["height"], 40)
            page.goto("https://react-platform.invalid/library?lens=all&preview=case-2")
            expect(page.locator(".choice-evidence")).to_have_count(0)
            page.goto("https://react-platform.invalid/library?lens=all&preview=case-1")
            self.assertIn("Guarded from external update", page.locator(".field-apply-context").inner_text())
            self.assertEqual(page.get_by_role("button", name="Previous").count(), 0)
            self.assertEqual(page.get_by_role("button", name="Next").count(), 0)
            page.locator(".attention-toggle").click()
            expect(page).to_have_url("https://react-platform.invalid/library?lens=all&preview=case-1&attention=open")
            expect(page.locator(".attention-rail")).to_be_visible()
            expect(
                page.locator(".attention-rail .attention-queue-copy b").first
            ).to_have_text("Title · This field needs your review.")
            self.assertEqual(
                0,
                page.locator(".attention-rail").get_by_text(
                    "Conflicting retained evidence", exact=True
                ).count(),
            )
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_visible()
            self.assertEqual(
                page.locator(".library-workspace").evaluate(
                    "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
                ),
                3,
            )
            page.locator(".attention-rail header button").click()
            self.assertEqual(page.locator(".library-work-row").count(), 6)
            page.locator(".library-work-row").first.get_by_text(
                "Mara Venn", exact=False
            ).wait_for()
            page.get_by_text("Household ebooks · Epub", exact=True).first.wait_for()
            self.assertNotIn(
                "synthetic-item-1", page.locator(".library-work-row").first.inner_text()
            )
            no_case_row = page.locator(".library-work-row").filter(has_text="Catalogue-only work")
            self.assertEqual(no_case_row.get_by_role("link").count(), 0)
            self.assertEqual(no_case_row.locator(".work-select").count(), 0)
            no_case_row.get_by_text("Preparation needs owner recovery").click()
            self.assertIn("Authentication at Provider", no_case_row.inner_text())
            self.assertIn("hardcover-main · Work · Authentication", no_case_row.inner_text())
            self.assertIn("1 bounded provider request", no_case_row.inner_text())
            self.assertIn("Retry review preparation", no_case_row.inner_text())
            page.get_by_role("link", name="Needs attention", exact=True).click()
            expect(page.locator(".library-work-row")).to_have_count(5)
            page.get_by_role("link", name="No attention needed", exact=True).click()
            expect(page.locator(".library-work-row")).to_have_count(6)
            page.get_by_role("link", name="All", exact=True).click()
            expect(page.locator(".library-work-row")).to_have_count(6)
            page.get_by_role("link", name="Next page").click()
            page.get_by_text("Unicode shelves:", exact=False).wait_for()
            self.assertEqual(page.locator(".library-work-row").count(), 6)
            self.assertEqual(
                page.get_by_text("Library could not be loaded", exact=False).count(),
                0,
            )
            page.get_by_label("Search title, author, library, or media").fill(
                "Spoken stories"
            )
            page.get_by_role("button", name="Search").click()
            page.get_by_text("The Glass Orchard", exact=True).wait_for()
            self.assertEqual(page.locator(".library-work-row").count(), 3)
            page.get_by_label("Search title, author, library, or media").fill(
                "no synthetic work has this phrase"
            )
            page.get_by_role("button", name="Search").click()
            page.get_by_text(
                "No exact-current works match this search and lens."
            ).wait_for()
            page.goto("https://react-platform.invalid/library?lens=all")
            for control in page.locator(".masthead nav a, .shell-actions button").all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            for control in page.locator(
                ".lenses a, .work-select, .library-pager a"
            ).all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            for lens in page.locator(".lenses a").all():
                self.assertGreaterEqual(lens.bounding_box()["width"], 44)
            long_title = page.locator(".library-work-row").filter(
                has_text=LONG_TITLE
            ).locator(".library-work-copy .work-select")
            self.assertEqual(long_title.inner_text(), LONG_TITLE)
            self.assertIn(UNBROKEN_TITLE_SEGMENT, long_title.inner_text())
            self.assertLessEqual(
                long_title.evaluate("node => node.scrollWidth"),
                long_title.evaluate("node => node.parentElement.clientWidth"),
            )
            self.assertLessEqual(long_title.bounding_box()["height"], 44)
            self.assertEqual(long_title.evaluate("node => getComputedStyle(node).whiteSpace"), "normal")
            self.assertEqual(long_title.evaluate("node => getComputedStyle(node).webkitLineClamp"), "2")
            self.assertEqual(
                long_title.evaluate("node => getComputedStyle(node).overflowWrap"),
                "anywhere",
            )
            self.assertEqual(
                long_title.evaluate("node => getComputedStyle(node.parentElement).overflow"),
                "visible",
            )
            page.goto("https://react-platform.invalid/library?lens=all")
            page.get_by_text("Catalogue-only work", exact=True).wait_for()
            page.evaluate("() => { document.body.tabIndex = -1; document.body.focus(); }")
            self.assertTrue(page.evaluate("() => document.activeElement === document.body"))
            for _ in range(20):
                page.keyboard.press("Tab")
                if page.evaluate("() => document.activeElement.matches('.lenses a')"):
                    break
            self.assertTrue(page.evaluate("() => document.activeElement.matches('.lenses a')"))
            self.assertEqual(
                page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle"),
                "solid",
            )
            self.assertEqual(
                page.evaluate("() => getComputedStyle(document.activeElement).outlineOffset"),
                "3px",
            )
            for _ in range(10):
                page.keyboard.press("Tab")
                if page.evaluate("() => document.activeElement.matches('.library-work-copy .work-select')"):
                    break
            self.assertTrue(
                page.evaluate("() => document.activeElement.matches('.library-work-copy .work-select')")
            )
            self.assertEqual(
                page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle"),
                "solid",
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 1440)

            page.set_viewport_size({"width": 900, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all")
            expect(page).to_have_url(
                "https://react-platform.invalid/library?lens=all&preview=case-1"
            )
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_visible()
            self.assertNotEqual(
                page.locator(".library-workspace").evaluate(
                    "node => getComputedStyle(node).display"
                ),
                "block",
            )
            for item in page.locator(".masthead nav a").all():
                self.assertEqual(
                    item.evaluate("node => getComputedStyle(node).whiteSpace"),
                    "nowrap",
                )
            self.assertEqual(
                page.locator(".masthead").evaluate(
                    "node => getComputedStyle(node).flexWrap"
                ),
                "wrap",
            )
            self.assertGreaterEqual(
                page.locator(".masthead nav").bounding_box()["y"],
                page.locator(".brand").bounding_box()["y"]
                + page.locator(".brand").bounding_box()["height"],
            )
            compact_evidence = page.get_by_role(
                "button", name="Evidence for Current value: Current <script> title"
            )
            self.assertGreaterEqual(compact_evidence.bounding_box()["height"], 44)
            compact_evidence.click()
            expect(page.locator("#choice-evidence-current")).to_be_visible()
            page.locator(".attention-toggle").click()
            expect(page.locator(".attention-rail")).to_be_visible()
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_visible()
            self.assertEqual(
                page.locator(".attention-rail").evaluate(
                    "node => getComputedStyle(node).position"
                ),
                "fixed",
            )
            self.assertGreaterEqual(
                page.locator(".attention-rail").bounding_box()["y"],
                page.locator(".masthead").bounding_box()["y"]
                + page.locator(".masthead").bounding_box()["height"]
                + 8,
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 900)
            page.locator(".attention-rail header button").click()

            page.set_viewport_size({"width": 760, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all")
            expect(page).to_have_url(
                "https://react-platform.invalid/library?lens=all"
            )
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_hidden()
            page.locator(".work-select").first.click()
            expect(page).to_have_url(
                "https://react-platform.invalid/library?lens=all&preview=case-1"
            )
            expect(page.locator(".library-works")).to_be_hidden()
            expect(page.locator(".workspace-detail")).to_be_visible()
            self.assertNotEqual(
                page.locator(".library-workspace").evaluate(
                    "node => getComputedStyle(node).display"
                ),
                "block",
            )
            page.get_by_role("button", name="Attention queue").click()
            expect(page.locator(".attention-rail")).to_be_visible()
            expect(page.locator(".attention-rail")).to_be_focused()
            self.assertGreaterEqual(
                page.locator(".attention-rail").bounding_box()["y"],
                page.locator(".masthead").bounding_box()["y"]
                + page.locator(".masthead").bounding_box()["height"]
                + 8,
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 760)

            page.set_viewport_size({"width": 390, "height": 844})
            page.goto("https://react-platform.invalid/library?lens=all")
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_hidden()
            page.locator(".attention-toggle").click()
            expect(page.locator(".attention-rail")).to_be_visible()
            expect(page.locator(".attention-rail")).to_be_focused()
            expect(page.locator(".library-works")).to_be_hidden()
            expect(page.locator(".workspace-detail")).to_be_hidden()
            page.locator(".attention-queue-item").first.click()
            expect(page.locator(".workspace-detail")).to_be_visible()
            expect(page.locator(".library-works")).to_be_hidden()
            expect(page.locator(".field-filmstrip button")).to_have_count(5)
            self.assertIn("Title", page.locator(".field-filmstrip").inner_text())
            self.assertIn("Cover", page.locator(".field-filmstrip").inner_text())
            self.assertFalse(page.locator(".automatic-resolution-details").evaluate("node => node.open"))
            self.assertLess(
                page.locator(".direct-choice").bounding_box()["y"],
                page.locator(".automatic-resolution-details").bounding_box()["y"],
            )
            page.locator(".automatic-resolution-details summary").click()
            compact_context = page.locator(".automatic-resolution-details").inner_text()
            self.assertIn("Resolved automatically", compact_context)
            self.assertIn("No eligible change", compact_context)
            compact_evidence = page.get_by_role(
                "button", name="Evidence for Current value: Current <script> title"
            )
            self.assertGreaterEqual(compact_evidence.bounding_box()["height"], 44)
            compact_evidence.click()
            expect(page.locator("#choice-evidence-current")).to_be_visible()
            self.assertIn("Guarded from external update", page.locator(".field-apply-context").inner_text())
            self.assertEqual(page.get_by_role("button", name="Previous").count(), 0)
            self.assertEqual(page.get_by_role("button", name="Next").count(), 0)
            expect(page.locator(".compact-workspace-controls button")).to_have_count(2)
            for control in page.locator(".compact-workspace-controls button").all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            page.get_by_role("button", name="Back to works").click()
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_hidden()
            page.set_viewport_size({"width": 1440, "height": 900})
            expect(page).to_have_url("https://react-platform.invalid/library?lens=all&preview=case-1")
            expect(page.locator(".workspace-detail")).to_be_visible()
            page.goto("https://react-platform.invalid/review/case-1?field=title&subview=review")
            page.get_by_text(
                "Three independent catalogue observations disagree on presentation.",
                exact=True,
            ).wait_for()
            desktop_field_rail = page.locator(".detail-layout > .field-navigation")
            self.assertEqual(
                desktop_field_rail.evaluate("node => getComputedStyle(node).position"),
                "sticky",
            )
            self.assertEqual(
                desktop_field_rail.evaluate("node => getComputedStyle(node).top"),
                "82px",
            )
            choices = page.locator("#field-title .direct-choice-grid")
            self.assertIn("Current <script> title", choices.inner_text())
            self.assertIn("The Clockmaker's Cartography", choices.inner_text())
            self.assertIn("Cartography of Impossible Libraries", choices.inner_text())
            self.assertIn(LONG_TITLE, choices.inner_text())
            self.assertEqual(page.get_by_role("button", name="Reject proposed change").count(), 0)
            self.assertEqual(page.get_by_role("button", name="Keep current value").count(), 0)
            title_notices = page.locator("#field-title .field-notices")
            self.assertIn("Local catalogue record", title_notices.inner_text())
            self.assertIn("Project Gutenberg record", title_notices.inner_text())
            self.assertIn("Google Books catalogue record", title_notices.inner_text())
            self.assertIn("Open Library record", title_notices.inner_text())
            self.assertNotIn("provider_", title_notices.inner_text())
            page.locator("#field-title .proposed-value").first.click()
            page.get_by_text(
                "The response was lost. The current case was reloaded before any retry.",
                exact=True,
            ).wait_for()
            self.assertEqual(ambiguous_decisions, 1)
            page.get_by_text("Mara Venn", exact=False).first.wait_for()
            page.get_by_role("button", name="Authors", exact=True).click()
            self.assertEqual(
                page.locator("#field-authors .direct-choice-grid .value-choice").count(),
                3,
            )
            author_notices = page.locator("#field-authors .field-notices")
            expect(author_notices).to_be_visible()
            expect(author_notices).to_contain_text("Corroborated creator sources")
            expect(author_notices).to_contain_text("Hardcover · Open Library")
            self.assertNotIn("provider_", author_notices.inner_text())
            page.get_by_role("button", name="Subtitle", exact=True).click()
            subtitle = page.locator("#field-subtitle")
            subtitle.get_by_text(
                "This field is unavailable for the current service capability.", exact=True
            ).wait_for()
            self.assertIn("Unavailable", subtitle.inner_text())
            self.assertEqual(subtitle.locator(".direct-choice-grid").count(), 0)
            self.assertEqual(subtitle.get_by_text("Not set", exact=True).count(), 0)
            self.assertEqual(page.get_by_role("button", name="Subtitle", exact=True).count(), 1)
            page.get_by_role("button", name="Evidence", exact=True).click()
            page.get_by_role("heading", name="Evidence and source ledger").wait_for()
            page.locator(".evidence-item").first.wait_for()
            self.assertEqual(page.locator(".evidence-item").count(), 3)
            page.get_by_role("button", name="Load more evidence").click()
            expect(page.locator(".evidence-item")).to_have_count(6)
            self.assertEqual(
                page.get_by_text("Evidence could not be loaded", exact=False).count(),
                0,
            )
            page.get_by_role("button", name="History", exact=True).click()
            page.get_by_role("heading", name="Current-case history").wait_for()
            page.get_by_text("Case admitted", exact=False).wait_for()
            self.assertIn(
                "Human decision · title",
                page.locator(".progressive-panel").inner_text(),
            )
            page.goto("https://react-platform.invalid/review/case-stale?subview=review")
            page.get_by_role("heading", name="Case superseded").wait_for()
            page.goto(
                "https://react-platform.invalid/review/case-11"
                "?field=authors&subview=review"
            )
            page.get_by_role("heading", name="Current review case").wait_for()
            for destination in ("title", "authors", "subtitle", "series"):
                page.get_by_role("button", name=destination.capitalize(), exact=True).click()
                page.locator(f"#field-{destination}").get_by_text(
                    "There is no owner decision available for this field.", exact=True
                ).wait_for()
                self.assertEqual(
                    page.locator(f"#field-{destination} .direct-choice-grid").count(),
                    0,
                )
                self.assertIn(
                    "Current value", page.locator(f"#field-{destination}").inner_text()
                )

            page.goto(
                "https://react-platform.invalid/review/case-5?field=authors&subview=review"
            )
            page.get_by_role("heading", name="Current review case").wait_for()
            automatic_rail = page.locator(".detail-layout > .field-navigation")
            expect(automatic_rail.get_by_role("button")).to_have_count(4)
            page.get_by_role("button", name="Title", exact=True).click()
            automatic_title = page.locator("#field-title")
            expect(automatic_title.locator(".direct-choice-grid .value-choice")).to_have_count(4)
            expect(automatic_title.locator(".current-value")).to_have_attribute("aria-pressed", "true")
            self.assertIn(
                "is-selected", automatic_title.locator(".current-value").get_attribute("class")
            )
            page.get_by_role("button", name="Authors", exact=True).click()
            automatic_authors = page.locator("#field-authors")
            expect(automatic_authors.locator(".direct-choice-grid .value-choice")).to_have_count(3)
            expect(automatic_authors.locator(".current-value")).to_have_attribute("aria-pressed", "true")
            automatic_authors.locator(".proposed-value").first.focus()
            page.keyboard.press("Space")
            page.get_by_text(
                "The response was lost. The current case was reloaded before any retry.",
                exact=True,
            ).wait_for()
            self.assertEqual(ambiguous_decisions, 2)

            page.goto(
                "https://react-platform.invalid/review/case-equal?field=title&subview=review"
            )
            page.get_by_role("heading", name="Current review case").wait_for()
            equal_title = page.locator("#field-title")
            self.assertIn(
                "preservation-only",
                equal_title.locator(".direct-choice-grid").get_attribute("class"),
            )
            expect(equal_title.locator(".direct-choice-grid .value-choice")).to_have_count(1)
            expect(equal_title.locator(".direct-choice button")).to_have_count(2)
            self.assertEqual(equal_title.locator(".proposed-value").count(), 0)
            self.assertIn("Current <script> title", equal_title.locator(".current-value").inner_text())
            page.goto(
                "https://react-platform.invalid/review/case-4"
                "?field=title&subview=review"
            )
            page.get_by_role("heading", name="Current review case").wait_for()
            page.get_by_role("button", name="Return to automatic handling").click()
            page.get_by_text(
                "Automatic policy resumes after fresh checks.", exact=True
            ).wait_for()
            self.assertEqual(return_automatic_requests, 1)
            page.set_viewport_size({"width": 390, "height": 844})
            compact_field_rail = page.locator(".detail-layout > .field-navigation")
            self.assertEqual(
                compact_field_rail.evaluate("node => getComputedStyle(node).position"),
                "static",
            )
            self.assertEqual(
                compact_field_rail.evaluate("node => getComputedStyle(node).top"),
                "auto",
            )
            for control in page.locator(".masthead nav a, .shell-actions button").all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)

            self.assertEqual(activity_failures, 1)
            self.assertEqual(consent_payloads, [{
                "grants": [{
                    "library": "Spoken stories",
                    "destinations": ["authors", "title"],
                }]
            }])

            before_navigation = documents
            page.get_by_role("link", name="Library", exact=True).click()
            page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
            page.locator(".attention-toggle").click()
            page.locator(".attention-rail h2").get_by_text("Attention", exact=True).wait_for()
            self.assertEqual(page.locator(".attention-queue-item").count(), 6)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
            attention_link = page.locator(".attention-queue-item").first
            self.assertNotIn("item-1", page.locator(".attention-rail").inner_text())
            page.evaluate(
                "() => document.body.dataset.persistentDocument = 'rich-attention'"
            )
            before_detail = documents
            attention_link.click()
            page.locator(".workspace-detail .decision-canvas").wait_for()
            self.assertEqual(
                page.evaluate("() => document.body.dataset.persistentDocument"),
                "rich-attention",
            )
            self.assertEqual(documents, before_detail)
            linked_choices = page.locator(
                ".workspace-detail .direct-choice-grid"
            )
            self.assertIn("Current <script> title", linked_choices.inner_text())
            self.assertEqual(linked_choices.locator(".choice-select").count(), 4)
            self.assertEqual(linked_choices.locator(".choice-evidence-toggle").count(), 4)
            page.go_back()
            page.locator(".attention-rail").wait_for()
            page.go_back()
            page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
            for control in page.locator(
                ".lenses a, .library-pager a, .work-select"
            ).all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            for lens in page.locator(".lenses a").all():
                self.assertGreaterEqual(lens.bounding_box()["width"], 44)
            compact_title = page.locator(".library-work-row").filter(
                has_text=LONG_TITLE
            ).locator(".library-work-copy .work-select")
            self.assertEqual(compact_title.inner_text(), LONG_TITLE)
            self.assertIn(UNBROKEN_TITLE_SEGMENT, compact_title.inner_text())
            self.assertLessEqual(
                compact_title.evaluate("node => node.scrollWidth"),
                compact_title.evaluate("node => node.parentElement.clientWidth"),
            )
            self.assertLessEqual(compact_title.bounding_box()["height"], 44)
            self.assertEqual(
                compact_title.evaluate("node => getComputedStyle(node).whiteSpace"),
                "normal",
            )
            self.assertEqual(
                compact_title.evaluate("node => getComputedStyle(node).webkitLineClamp"),
                "2",
            )
            self.assertEqual(
                compact_title.evaluate("node => getComputedStyle(node).overflowWrap"),
                "anywhere",
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
            self.assertEqual(documents, before_navigation)
            page.get_by_role("link", name="Policy", exact=True).click()
            page.get_by_role("heading", name="Library policy").wait_for()
            page.get_by_role("navigation", name="Policy chapters").get_by_role(
                "button", name="Curation"
            ).click()
            expect(page.locator("#policy-curation")).to_be_focused()
            cover_policy = page.get_by_role("region", name="Cover")
            cover_policy.get_by_text(
                "Cover review uses the fixed supported source order.", exact=True
            ).wait_for()
            expect(
                cover_policy.get_by_role("button", name="Automatic")
            ).to_be_disabled()
            expect(
                cover_policy.get_by_role("button", name="Ask me")
            ).to_have_attribute("aria-pressed", "true")
            cover_representation = cover_policy.get_by_role(
                "combobox", name="Cover representation"
            )
            expect(cover_representation).to_be_disabled()
            expect(cover_representation).to_have_text("Fixed supported source order")
            self.assertIn("Manual review only", cover_policy.inner_text())
            self.assertNotIn("Automatic writes", cover_policy.inner_text())
            capture_visual_proof("policy-cover")
            page.get_by_role("navigation", name="Policy chapters").get_by_role(
                "button", name="Identity"
            ).click()
            expect(page.locator("#policy-identity")).to_be_focused()
            page.locator(".cm-policy-finish").get_by_text(
                "Draft matches the active policy", exact=True
            ).wait_for()
            page.locator(".cm-policy-mode summary").click()
            page.get_by_text("Outcomes remain non-authoritative.", exact=True).wait_for()
            page.get_by_text(
                "2 recent non-authoritative outcome sets.", exact=True
            ).wait_for()
            page.get_by_role("heading", name="Activation completed with failures").wait_for()
            page.get_by_role("button", name="Retry failed reconciliation").wait_for()
            page.get_by_role("button", name="Recover Title suspension").wait_for()
            page.get_by_role("region", name="Title and subtitle").get_by_role(
                "button", name="Ignore"
            ).click()
            page.get_by_text("1 draft change", exact=True).wait_for()
            page.get_by_role("button", name="Validate and preview").click()
            page.get_by_text("Impact preview is current.", exact=False).wait_for()
            page.get_by_text(
                "· fills the empty field with A bounded subtitle", exact=True
            ).wait_for()
            page.get_by_text("Safe visible description <3", exact=False).wait_for()
            page.get_by_text('[{"name":"Old order","sequence":"1"}]', exact=False).wait_for()
            page.get_by_text('["Fantasy"] → ["Fantasy","Epic"]', exact=False).wait_for()
            page.get_by_text(
                "· remains empty — this policy leaves the field unchanged", exact=True
            ).wait_for()
            page.get_by_text("Plus 2 more exact field outcomes", exact=False).wait_for()
            self.assertEqual(page.locator(".cm-policy-preview-list > div").count(), 6)
            impact_labels = page.locator(".cm-policy-impact > b").all_text_contents()
            self.assertEqual(
                impact_labels,
                ["Change", "Change", "Change", "No change", "Change", "Change"],
            )
            for detail in page.locator(".cm-policy-impact > small").all_text_contents():
                if "→" in detail:
                    self.assertNotIn("remains", detail)
                    self.assertNotIn("empty", detail)
            for label in impact_labels:
                self.assertNotIn(label, {"projected", "abstain", "missing", "policy_off"})
            page.get_by_role("button", name="Sign out").click()
            page.get_by_text("Writes are blocked", exact=False).wait_for()
            fail_policy_persistently = True
            page.goto("https://react-platform.invalid/policy?section=identity")
            page.get_by_role(
                "heading", name="The current workspace assets could not be loaded."
            ).wait_for()
            self.assertEqual(documents, before_navigation + 2)
            page.get_by_role("button", name="Reload the current application").click()
            page.get_by_role(
                "heading", name="The current workspace assets could not be loaded."
            ).wait_for()
            self.assertEqual(documents, before_navigation + 3)
            expected_chunk_errors = [
                error
                for error in errors
                if "503 (Service Unavailable)" in error
                or "ChunkRecoveryError" in error
            ]
            expected_ambiguous_errors = [
                error for error in errors if "net::ERR_FAILED" in error
            ]
            self.assertGreaterEqual(len(expected_chunk_errors), 2)
            self.assertEqual(
                [
                    error
                    for error in errors
                    if error not in expected_chunk_errors
                    and error not in expected_ambiguous_errors
                ],
                [],
            )
            self.assertEqual(
                expected_ambiguous_errors,
                ["Failed to load resource: net::ERR_FAILED"] * ambiguous_decisions,
            )
            decision_request_failures = [
                failure
                for failure in failed_requests
                if failure[2] == "net::ERR_FAILED"
            ]
            self.assertEqual(len(decision_request_failures), ambiguous_decisions, failed_requests)
            for failed_method, failed_url, failed_reason in decision_request_failures:
                self.assertEqual(failed_method, "POST")
                self.assertTrue(
                    failed_url.startswith(
                        "https://react-platform.invalid/api/v1/review-cases/"
                    )
                    and failed_url.endswith("/decisions"),
                    failed_url,
                )
                self.assertEqual(failed_reason, "net::ERR_FAILED")
            self.assertEqual(
                [
                    failure
                    for failure in failed_requests
                    if failure[2] not in {"net::ERR_ABORTED", "net::ERR_FAILED"}
                ],
                [],
            )
            browser.close()


if __name__ == "__main__":
    unittest.main()
