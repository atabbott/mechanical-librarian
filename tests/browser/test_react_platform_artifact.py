from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetBundle,
    load_react_asset_bundle,
)
from tests.browser.fidelity_cohort import (
    BUILD_IDENTITY as _BUILD_IDENTITY,
    HOSTILE_TEXT as _HOSTILE,
    LONG_TITLE,
    UNBROKEN_TITLE_SEGMENT,
    rich_payload,
)


class ReactPlatformArtifactTests(unittest.TestCase):
    def test_exact_built_shell_supports_every_direct_route_and_client_navigation(self) -> None:
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
                ("/activity", "Activity"),
            )
            for path, heading in routes:
                fail_activity_once = path == "/activity"
                before_entry = documents
                response = page.goto(f"https://react-platform.invalid{path}")
                self.assertIsNotNone(response)
                self.assertEqual(response.status, 200)
                page.get_by_role("heading", name=heading).wait_for()
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
                    page.get_by_role(
                        "button", name="Open write-permission details"
                    ).click()
                    page.get_by_label("Household ebooks destination consent").get_by_text(
                        "Subtitle: Excluded · A field is unavailable"
                    ).wait_for()
                    page.get_by_text(
                        "Qualified automatic decision", exact=False
                    ).first.wait_for()
                    page.get_by_text("Human decision", exact=False).first.wait_for()
                    page.get_by_text(
                        "The result is being checked", exact=False
                    ).first.wait_for()
                    page.get_by_text("Affected destinations: Authors, Title").wait_for()
                    page.get_by_text("Recorded decision", exact=False).wait_for()
                    self.assertNotIn(
                        "future_provider_origin",
                        page.locator(".activity-ledger").inner_text(),
                    )
                    page.get_by_role(
                        "checkbox", name="Spoken stories · 2 prepared"
                    ).check()
                    page.get_by_role("button", name="Enable selected libraries").click()
                    page.get_by_text(
                        "Activity state refreshed from the authoritative server."
                    ).wait_for()
                self.assertEqual(
                    documents,
                    before_entry + (2 if path == "/activity" else 1),
                )

            missing_case_error_start = len(errors)
            for _ in range(2):
                response = page.goto(
                    "https://react-platform.invalid/review/missing-case"
                    "?field=title&subview=review"
                )
                self.assertIsNotNone(response)
                self.assertEqual(response.status, 200)
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
            expect(page.locator(".library-works")).to_be_visible()
            expect(page.locator(".workspace-detail")).to_be_visible()
            expect(page.locator(".field-filmstrip button")).to_have_count(2)
            self.assertIn("Title", page.locator(".field-filmstrip").inner_text())
            self.assertFalse(page.locator(".automatic-resolution-details").evaluate("node => node.open"))
            self.assertFalse(page.locator(".source-ledger").evaluate("node => node.open"))
            self.assertLess(
                page.locator(".direct-choice").bounding_box()["y"],
                page.locator(".automatic-resolution-details").bounding_box()["y"],
            )
            self.assertLess(
                page.locator(".direct-choice").bounding_box()["y"],
                page.locator(".source-ledger").bounding_box()["y"],
            )
            page.locator(".automatic-resolution-details summary").click()
            automatic_context = page.locator(".automatic-resolution-details").inner_text()
            self.assertIn("Subtitle", automatic_context)
            self.assertIn("Series", automatic_context)
            self.assertIn("Resolved automatically", automatic_context)
            self.assertIn("No eligible change", automatic_context)
            page.locator(".source-ledger summary").click()
            workspace_evidence = page.locator(".workspace-detail .source-ledger").inner_text()
            self.assertIn("Source", workspace_evidence)
            self.assertIn("Observed value", workspace_evidence)
            self.assertIn("Basis", workspace_evidence)
            self.assertIn("Local catalogue record", workspace_evidence)
            self.assertIn("Project Gutenberg record", workspace_evidence)
            self.assertIn("Google Books catalogue record", workspace_evidence)
            self.assertIn("Guarded from external update", page.locator(".field-apply-context").inner_text())
            self.assertEqual(page.get_by_role("button", name="Previous").count(), 0)
            self.assertEqual(page.get_by_role("button", name="Next").count(), 0)
            page.locator(".attention-toggle").click()
            expect(page).to_have_url("https://react-platform.invalid/library?lens=all&preview=case-1&attention=open")
            expect(page.locator(".attention-rail")).to_be_visible()
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
            page.evaluate("() => document.activeElement.blur()")
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
            self.assertEqual(
                page.locator(".source-ledger-head").evaluate(
                    "node => getComputedStyle(node).display"
                ),
                "none",
            )
            page.locator(".source-ledger summary").click()
            self.assertIn("Observed value", page.locator(".source-ledger").inner_text())
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
            expect(page.locator(".field-filmstrip button")).to_have_count(2)
            self.assertIn("Title", page.locator(".field-filmstrip").inner_text())
            self.assertFalse(page.locator(".automatic-resolution-details").evaluate("node => node.open"))
            self.assertFalse(page.locator(".source-ledger").evaluate("node => node.open"))
            self.assertLess(
                page.locator(".direct-choice").bounding_box()["y"],
                page.locator(".automatic-resolution-details").bounding_box()["y"],
            )
            page.locator(".automatic-resolution-details summary").click()
            compact_context = page.locator(".automatic-resolution-details").inner_text()
            self.assertIn("Resolved automatically", compact_context)
            self.assertIn("No eligible change", compact_context)
            page.locator(".source-ledger summary").click()
            expect(page.locator(".source-ledger")).to_be_visible()
            self.assertEqual(
                page.locator(".source-ledger-head").evaluate(
                    "node => getComputedStyle(node).display"
                ),
                "none",
            )
            self.assertIn("Observed value", page.locator(".source-ledger").inner_text())
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
            page.get_by_role("button", name="authors", exact=True).click()
            self.assertEqual(
                page.locator("#field-authors .direct-choice-grid .value-choice").count(),
                3,
            )
            page.get_by_role("button", name="subtitle", exact=True).click()
            page.get_by_text("No eligible proposed values.", exact=True).wait_for()
            page.get_by_role("button", name="Evidence", exact=True).click()
            page.get_by_role("heading", name="Evidence and source ledger").wait_for()
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
                "human decision · title",
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
                page.get_by_role("button", name=destination, exact=True).click()
                page.locator(f"#field-{destination}").get_by_text(
                    "No eligible proposed values.", exact=True
                ).wait_for()
                self.assertEqual(
                    page.locator(f"#field-{destination} .direct-choice-grid").count(),
                    0,
                )
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
            self.assertEqual(linked_choices.locator("button").count(), 4)
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
            page.get_by_text("Safe visible description <3", exact=False).wait_for()
            page.get_by_text('[{"name":"Old order","sequence":"1"}]', exact=False).wait_for()
            page.get_by_text('["Fantasy"] → ["Fantasy","Epic"]', exact=False).wait_for()
            page.get_by_text("Plus 2 more exact field outcomes", exact=False).wait_for()
            self.assertEqual(page.locator(".cm-policy-preview-list > div").count(), 6)
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
                ["Failed to load resource: net::ERR_FAILED"],
            )
            decision_request_failures = [
                failure
                for failure in failed_requests
                if failure[2] == "net::ERR_FAILED"
            ]
            self.assertEqual(len(decision_request_failures), 1, failed_requests)
            failed_method, failed_url, failed_reason = decision_request_failures[0]
            self.assertEqual(
                (
                    failed_method,
                    failed_url,
                    failed_reason,
                ),
                (
                    "POST",
                    "https://react-platform.invalid/api/v1/review-cases/"
                    "case-1/decisions",
                    "net::ERR_FAILED",
                ),
            )
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
