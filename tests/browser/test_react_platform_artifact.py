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


class ReactPlatformArtifactTests(unittest.TestCase):
    def test_exact_built_shell_supports_direct_library_entry(self) -> None:
        root_value = os.environ.get("ML_REACT_ASSET_DIR")
        self.assertIsNotNone(root_value, "ML_REACT_ASSET_DIR is required")
        bundle = load_react_asset_bundle(Path(root_value or ""))
        self.assertIsInstance(bundle, ReactAssetBundle)
        errors: list[str] = []

        library = {
            "schema_version": "review.v1",
            "lens": "all",
            "query": None,
            "next_cursor": None,
            "counts": {
                "needs_attention": 1,
                "all": 1,
                "no_attention_needed": 0,
            },
            "items": [
                {
                    "review_case_id": "case-1",
                    "title": "Hostile <img src=x onerror=window.__metadataExecuted=true>",
                    "library": "Synthetic library",
                    "item": "item-1",
                    "attention_count": 1,
                    "attention_state": "needs_attention",
                    "detail_url": "/review/case-1",
                    "dispositions": ["review"],
                }
            ],
        }

        def fulfill(route: Route) -> None:
            path = urlsplit(route.request.url).path
            if path == "/api/v1/library":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(library),
                )
                return
            prefix = "/assets/review-app/"
            if path.startswith(prefix):
                relative = path.removeprefix(prefix)
                content_type = "text/css" if relative.endswith(".css") else "text/javascript"
                route.fulfill(status=200, content_type=content_type, body=bundle.read(relative))
                return
            if route.request.resource_type == "document":
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    headers={
                        "content-security-policy": (
                            "default-src 'none'; script-src 'self'; style-src 'self'; "
                            "connect-src 'self'; img-src 'self'; object-src 'none'; "
                            "base-uri 'none'; frame-ancestors 'none'"
                        )
                    },
                    body=bundle.read(bundle.entry),
                )
                return
            route.fulfill(status=404, body="")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", fulfill)
            response = page.goto("https://react-platform.invalid/library?lens=all")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            page.get_by_role("heading", name="Review library").wait_for()
            self.assertEqual(page.get_by_text("Hostile <img", exact=False).count(), 1)
            self.assertEqual(page.locator("img").count(), 0)
            self.assertIsNone(page.evaluate("() => window.__metadataExecuted"))
            self.assertTrue(page.get_by_role("main").evaluate("node => node === document.activeElement"))
            self.assertEqual(errors, [])
            browser.close()


if __name__ == "__main__":
    unittest.main()
