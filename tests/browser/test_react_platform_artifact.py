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


_BUILD_IDENTITY = "a" * 64
_HOSTILE = "Hostile <img src=x onerror=window.__metadataExecuted=true>"
_LONG_HOSTILE_TITLE = (
    "The Cartographer's Archive: "
    + "UnbrokenHostileCatalogueTitle" * 12
    + " <script> remains readable"
)


def _payload(path: str) -> object:
    if path.startswith("/api/v1/review-cases/case-") and not path.endswith(("/evidence", "/history")):
        path = "/api/v1/review-cases/case-1"
    if path == "/api/v1/review-preparation":
        return {"schema_version": "review-preparation-v1", "active": True, "actionable": False, "paused": False, "libraries": [{"blocked": 0, "checked": 1, "connection": "primary", "library": "Synthetic library", "library_id": "library-1", "ready": 0, "rescan_requested": False, "scan_id": "scan-1", "state": "scanning", "total": 2}]}
    if path == "/api/v1/library":
        return {"schema_version": "review.v1", "lens": "all", "query": None, "next_cursor": "cursor-2", "counts": {"needs_attention": 1, "all": 3, "no_attention_needed": 2}, "items": [{"work_id": "library_work_" + "1" * 64, "review_case_id": "case-1", "title": _LONG_HOSTILE_TITLE, "authors": ["Synthetic Author"], "library": "Synthetic library", "media_kind": "epub", "item": "item-1", "attention_count": 1, "attention_state": "needs_attention", "detail_url": "/review/case-1", "dispositions": ["unresolved_choice"]}, {"work_id": "library_work_" + "2" * 64, "review_case_id": None, "title": "Catalogue-only work", "authors": ["Catalogue Author"], "library": "Synthetic library", "media_kind": "epub", "item": "item-2", "attention_count": 0, "attention_state": "no_attention_needed", "detail_url": None, "dispositions": []}, {"work_id": "library_work_" + "3" * 64, "review_case_id": "case-3", "title": "Resolved work", "authors": ["Resolved Author"], "library": "Synthetic library", "media_kind": "epub", "item": "item-3", "attention_count": 0, "attention_state": "no_attention_needed", "detail_url": "/review/case-3", "dispositions": ["ineligible"]}]}
    if path == "/api/v1/attention":
        return {"schema_version": "attention-v1", "query": None, "next_cursor": None, "total": 1, "items": [{"review_case_id": "case-1", "destination": "title", "title": _HOSTILE, "authors": ["Synthetic Author"], "library": "Synthetic library", "media_kind": "epub", "item": "item-1", "detail_url": "/review/case-1?field=title", "notices": [], "provenance": [], "work_attention_count": 3}]}
    if path == "/api/v1/review-cases/case-1":
        return {"schema_version": "review-case-v1", "review_case_id": "case-1", "current": True, "target": {"connection": "primary", "library": "Synthetic library", "media_kind": "epub", "authors": ["Synthetic Author"], "item": _HOSTILE}, "fields": [{"destination": "title", "deterministic_disposition": "review", "effect_capability": "manual", "needs_attention": True, "manual_hold": False, "manual_required": True, "current": {"kind": "text", "value": "Current <script>"}, "proposed_values": [{"kind": "text", "value": "First proposed <img>"}, {"kind": "text", "value": "Second proposed"}, {"kind": "text", "value": "Third proposed"}], "proposal_id": "proposal-1", "target_fingerprint": "f" * 64, "human_decision": None, "automatic_decision": None, "notices": ["Synthetic reviewer fact"], "provenance": ["provider_" + "a" * 64, "<img>" + "long evidence " * 100], "uncertainty": ["Synthetic uncertainty"]}, {"destination": "subtitle", "deterministic_disposition": "automatic", "effect_capability": "manual", "needs_attention": False, "manual_hold": False, "manual_required": False, "current": {"kind": "text", "value": "Current subtitle"}, "proposed_values": [], "proposal_id": "proposal-2", "target_fingerprint": "f" * 64, "human_decision": None, "automatic_decision": {"decision_id": "automatic-1", "outcome": "keep_current", "origin": "automatic_policy"}, "notices": [], "provenance": [], "uncertainty": []}]}
    if path == "/api/v1/review-cases/case-1/evidence":
        return {"schema_version": "review-case-evidence-v2", "review_case_id": "case-1", "next_cursor": None, "items": [{"reference": "local-1", "kind": "title", "source": "provider_claim", "provider": "catalogue", "candidate": "candidate-1", "fact": _HOSTILE}]}
    if path == "/api/v1/review-cases/case-1/history":
        return {"schema_version": "review-case-history-v1", "review_case_id": "case-1", "next_cursor": None, "items": []}
    if path == "/api/v1/metadata-policy/state":
        return {"schema_version": "metadata-policy-notebook-v1", "policy_id": "policy-1", "profile": {"schema_version": "metadata-policy-profile-v1", "name": _HOSTILE, "options": {}, "destinations": {}}}
    if path == "/api/v1/metadata-policy/shadow":
        return {"schema_version": "shadow-posture-v1", "posture": "review", "authority": "none", "can_leave_preview": False}
    if path == "/api/v1/metadata-policy/automatic-safety":
        return {"paused": False, "global_suspension": None, "category_suspensions": {}}
    if path == "/api/v1/metadata-policy/shadow/outcomes":
        return {"schema_version": "shadow-outcome-sample-v1", "authority": "none", "items": [], "next_cursor": None}
    if path == "/api/v1/metadata-policy/transition":
        return None
    if path == "/api/v1/activity":
        return {
            "schema_version": "continuous-field-activity-v1", "backup_ready": True,
            "paused": False,
            "configured_libraries": [{"connection": "primary", "library": "fiction", "prepared": {"automatic": 1, "human": 1, "total": 2}}],
            "consented_libraries": [],
            "enabled_libraries": [],
            "capabilities": [{
                "connection": "primary", "library": "fiction", "state": "partially_allowed",
                "destinations": [
                    {"destination": "authors", "state": "allowed", "consented": False},
                    {"destination": "subtitle", "state": "field_unavailable", "consented": False},
                    {"destination": "title", "state": "allowed", "consented": True},
                ],
            }],
            "events": [
                {"work_id": "book-1", "library": "fiction", "destination": "title", "state": "verified", "origin": "automatic_policy"},
                {"work_id": "book-2", "library": "fiction", "destination": "subtitle", "state": "stale", "origin": "unrecognised_origin"},
                {"work_id": "book-3", "library": "fiction", "destination": "title", "state": "applying", "origin": "human"},
                {"work_id": "book-4", "library": "fiction", "destination": "title", "state": "recovered", "origin": "human"},
                {"work_id": "book-5", "library": "fiction", "destination": "title", "state": "running", "origin": "human"},
                {"work_id": "book-6", "library": "fiction", "destination": "title", "state": "paused", "origin": "human"},
                {"work_id": "book-7", "library": "fiction", "destination": "title", "state": "resolved", "origin": "human"},
            ],
            "counts": {},
            "example": None,
            "last_failure": None,
            "prepared": {"automatic": 1, "human": 1, "total": 2},
            "suspensions": [],
        }
    raise AssertionError(f"unexpected API path: {path}")


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
        fail_policy_persistently = False
        consent_payloads: list[object] = []

        def fulfill(route: Route) -> None:
            nonlocal activity_failures, documents
            parsed = urlsplit(route.request.url)
            path = parsed.path
            if path.startswith("/api/v1/"):
                if path == "/api/v1/session/logout":
                    route.fulfill(status=204, headers={"x-ml-build-identity": "c" * 64}, body="")
                    return
                if path == "/api/v1/activity/consent" and route.request.method == "POST":
                    consent_payloads.append(route.request.post_data_json)
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(_payload("/api/v1/activity")))
                    return
                route.fulfill(status=200, content_type="application/json", body=json.dumps(_payload(path)))
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
            page.route("**/*", fulfill)
            routes = (
                ("/library?lens=all", "Every work, with exceptions first"),
                ("/attention", "Needs your attention"),
                ("/review/case-1?field=title&subview=review", "Current review case"),
                ("/policy?section=identity", "Choose meaning before automation."),
                ("/policy?section=publication", "Choose meaning before automation."),
                ("/policy?section=curation", "Choose meaning before automation."),
                ("/policy?section=unsupported", "Choose meaning before automation."),
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
                    self.assertEqual(
                        page.get_by_role("combobox", name="Policy chapter").input_value(),
                        section,
                    )
                    self.assertTrue(page.url.endswith(path), f"{path} restored as {page.url}")
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
                self.assertEqual(page.locator("img, script:not([src])").count(), 0)
                self.assertIsNone(page.evaluate("() => window.__metadataExecuted"))
                if path == "/activity":
                    page.get_by_label("fiction destination consent").get_by_text(
                        "Subtitle: Excluded · A field is unavailable"
                    ).wait_for()
                    page.get_by_text("Qualified automatic decision", exact=False).wait_for()
                    page.get_by_text("Recorded decision", exact=False).wait_for()
                    self.assertNotIn(
                        "unrecognised_origin",
                        page.locator(".activity-ledger").inner_text(),
                    )
                    page.get_by_role("checkbox", name="fiction · 2 prepared").check()
                    page.get_by_role("button", name="Enable guarded writes").click()
                    page.get_by_text(
                        "Activity state refreshed from the authoritative server."
                    ).wait_for()
                self.assertEqual(
                    documents,
                    before_entry + (2 if path == "/activity" else 1),
                )

            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all")
            page.get_by_text("Catalogue-only work", exact=True).wait_for()
            page.locator(".library-work-row").first.get_by_text("Synthetic Author", exact=False).wait_for()
            page.get_by_text("Synthetic library · Epub", exact=True).first.wait_for()
            self.assertNotIn("item-1", page.locator(".library-work-row").first.inner_text())
            no_case_row = page.locator(".library-work-row").filter(has_text="Catalogue-only work")
            self.assertEqual(no_case_row.get_by_role("link").count(), 0)
            self.assertEqual(no_case_row.get_by_role("button", name="Preview").count(), 0)
            for control in page.locator(".masthead nav a, .shell-actions button").all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            for control in page.locator(
                ".lenses a, .preview-button, .library-pager a, .library-work-copy h3 a"
            ).all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            long_title = page.locator(".library-work-copy h3 a").first
            self.assertLessEqual(
                long_title.evaluate("node => node.scrollWidth"),
                long_title.evaluate("node => node.parentElement.clientWidth"),
            )
            self.assertLessEqual(long_title.bounding_box()["height"], 44)
            self.assertEqual(long_title.evaluate("node => getComputedStyle(node).whiteSpace"), "normal")
            self.assertEqual(long_title.evaluate("node => getComputedStyle(node).webkitLineClamp"), "2")
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
                if page.evaluate("() => document.activeElement.matches('.library-work-copy h3 a')"):
                    break
            self.assertTrue(
                page.evaluate("() => document.activeElement.matches('.library-work-copy h3 a')")
            )
            self.assertEqual(
                page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle"),
                "solid",
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 1440)
            page.goto("https://react-platform.invalid/review/case-1?field=title&subview=review")
            page.get_by_text("Synthetic reviewer fact", exact=True).wait_for()
            choices = page.locator(".direct-choice-grid")
            self.assertIn("Current <script>", choices.inner_text())
            self.assertIn("First proposed", choices.inner_text())
            self.assertIn("Second proposed", choices.inner_text())
            self.assertIn("Third proposed", choices.inner_text())
            self.assertEqual(page.get_by_role("button", name="Reject proposed change").count(), 0)
            self.assertEqual(page.get_by_role("button", name="Keep current value").count(), 0)
            self.assertIn("Evidence record 1", page.locator(".field-notices").inner_text())
            self.assertNotIn("provider_", page.locator(".field-notices").inner_text())
            page.get_by_text("Synthetic Author", exact=False).first.wait_for()
            page.set_viewport_size({"width": 390, "height": 844})
            for control in page.locator(".masthead nav a, .shell-actions button").all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)

            self.assertEqual(activity_failures, 1)
            self.assertEqual(consent_payloads, [{
                "grants": [{"library": "fiction", "destinations": ["authors", "title"]}]
            }])

            before_navigation = documents
            page.get_by_role("link", name="Library").click()
            page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
            page.get_by_role("link", name="Attention", exact=True).click()
            page.get_by_role("heading", name="Needs your attention").wait_for()
            attention_header = page.locator(".attention-work > header").first
            self.assertEqual(
                attention_header.evaluate("node => getComputedStyle(node).flexDirection"),
                "column",
            )
            self.assertEqual(
                attention_header.locator(":scope > span").evaluate(
                    "node => getComputedStyle(node).whiteSpace"
                ),
                "nowrap",
            )
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
            attention_link = page.locator(".attention-field-row").first
            self.assertIn("field=title", attention_link.get_attribute("href") or "")
            self.assertNotIn("item-1", page.locator(".attention-groups").inner_text())
            page.go_back()
            page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
            for control in page.locator(
                ".lenses a, .preview-button, .library-pager a, .library-work-copy h3 a"
            ).all():
                self.assertGreaterEqual(control.bounding_box()["height"], 44)
            compact_title = page.locator(".library-work-copy h3 a").first
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
            self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
            self.assertEqual(documents, before_navigation)
            page.get_by_role("link", name="Policy", exact=True).click()
            page.get_by_role(
                "heading", name="Choose meaning before automation."
            ).wait_for()
            page.locator(".policy-impact").get_by_text(
                "Draft matches active policy", exact=True
            ).wait_for()
            page.get_by_label("Policy name").fill("Edited synthetic policy")
            page.get_by_text("Draft differs from active policy", exact=True).wait_for()
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
            self.assertGreaterEqual(len(expected_chunk_errors), 2)
            self.assertEqual(
                [error for error in errors if error not in expected_chunk_errors],
                [],
            )
            browser.close()


if __name__ == "__main__":
    unittest.main()
