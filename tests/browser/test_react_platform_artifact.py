from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Route, sync_playwright

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetBundle,
    load_react_asset_bundle,
)


_BUILD_IDENTITY = "a" * 64
_HOSTILE = "Hostile <img src=x onerror=window.__metadataExecuted=true>"


def _payload(path: str) -> object:
    if path == "/api/v1/review-preparation":
        return {"schema_version": "review-preparation-v1", "active": False, "actionable": False, "paused": False, "libraries": []}
    if path == "/api/v1/library":
        return {"schema_version": "review.v1", "lens": "all", "query": None, "next_cursor": None, "counts": {"needs_attention": 1, "all": 1, "no_attention_needed": 0}, "items": [{"review_case_id": "case-1", "title": _HOSTILE, "library": "Synthetic library", "item": "item-1", "attention_count": 1, "attention_state": "needs_attention", "detail_url": "/review/case-1", "dispositions": ["review"]}]}
    if path == "/api/v1/attention":
        return {"schema_version": "attention-v1", "query": None, "next_cursor": None, "total": 1, "items": [{"review_case_id": "case-1", "destination": "title", "title": _HOSTILE, "library": "Synthetic library", "item": "item-1", "detail_url": "/review/case-1?field=title", "notices": [], "provenance": []}]}
    if path == "/api/v1/review-cases/case-1":
        return {"schema_version": "review-case-v1", "review_case_id": "case-1", "current": True, "target": {"connection": "primary", "library": "Synthetic library", "item": _HOSTILE}, "fields": [{"destination": "title", "deterministic_disposition": "review", "effect_capability": "manual", "needs_attention": True, "manual_hold": False, "manual_required": True, "current": {"kind": "text", "value": "Current <script>"}, "proposed_values": [{"kind": "text", "value": "Proposed <img>"}], "proposal_id": "proposal-1", "target_fingerprint": "f" * 64, "human_decision": None, "automatic_decision": None, "notices": [], "provenance": [], "uncertainty": []}]}
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
        return {"schema_version": "continuous-field-activity-v1", "backup_ready": True, "paused": False, "configured_libraries": [], "enabled_libraries": [], "capabilities": [], "events": [], "counts": {}}
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

        def fulfill(route: Route) -> None:
            nonlocal activity_failures, documents
            parsed = urlsplit(route.request.url)
            path = parsed.path
            if path.startswith("/api/v1/"):
                if path == "/api/v1/session/logout":
                    route.fulfill(status=204, headers={"x-ml-build-identity": "c" * 64}, body="")
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
                ("/library?lens=all", "Review library"),
                ("/attention", "Fields needing attention"),
                ("/review/case-1?field=title&subview=review", "Current review case"),
                ("/policy?section=identity", "Choose how prepared metadata reaches you"),
                ("/activity", "Ready when you are"),
            )
            for path, heading in routes:
                fail_activity_once = path == "/activity"
                before_entry = documents
                response = page.goto(f"https://react-platform.invalid{path}")
                self.assertIsNotNone(response)
                self.assertEqual(response.status, 200)
                page.get_by_role("heading", name=heading).wait_for()
                self.assertLessEqual(page.evaluate("() => document.documentElement.scrollWidth"), 390)
                self.assertEqual(page.locator("img, script:not([src])").count(), 0)
                self.assertIsNone(page.evaluate("() => window.__metadataExecuted"))
                self.assertEqual(
                    documents,
                    before_entry + (2 if path == "/activity" else 1),
                )

            self.assertEqual(activity_failures, 1)

            before_navigation = documents
            page.get_by_role("link", name="Library").click()
            page.get_by_role("heading", name="Review library").wait_for()
            page.get_by_role("link", name="Attention", exact=True).click()
            page.get_by_role("heading", name="Fields needing attention").wait_for()
            page.go_back()
            page.get_by_role("heading", name="Review library").wait_for()
            self.assertEqual(documents, before_navigation)
            page.get_by_role("button", name="Sign out").click()
            page.get_by_text("Writes are blocked", exact=False).wait_for()
            fail_policy_persistently = True
            page.get_by_role("link", name="Policy", exact=True).click()
            page.get_by_role(
                "heading", name="The current workspace assets could not be loaded."
            ).wait_for()
            self.assertEqual(documents, before_navigation)
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
