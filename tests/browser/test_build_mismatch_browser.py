"""A tab left on the previous build learns it on its next read (#1577).

The server names its build on every `/api/v1` response to a request that
authenticated an owner session (ADR-0091). Rewriting that header in flight
stands in for an upgrade under an open tab: the page holds the old identity
in memory and the server now answers with another one. A read is enough to
raise the reload notice, and the route that failed offers a reload rather than
another try. Once the session has expired, the 401 names no build, so the same
tab goes to sign-in instead, which serves the current shell.
"""

from __future__ import annotations

import os
import re
import tempfile
from contextlib import closing
from pathlib import Path

from argon2 import PasswordHasher
from playwright.sync_api import Page, Route, sync_playwright

from tests.browser.test_review_browser import (
    _PASSWORD,
    _certificate,
    _closing_context,
    _login,
    _server,
)
from tests.support.ports import ReservedPort

_NEXT_BUILD = "0" * 64


def _upgrade_under(page: Page) -> None:
    """Answer every identified response as the next build would."""

    def next_build(route: Route) -> None:
        response = route.fetch()
        headers = dict(response.headers)
        if "x-ml-build-identity" in headers:
            headers["x-ml-build-identity"] = _NEXT_BUILD
        route.fulfill(response=response, headers=headers)

    page.route("**/api/v1/**", next_build)


def test_an_old_tab_reloads_on_its_next_read_and_an_expired_one_signs_in() -> None:
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
                viewport={"width": 1440, "height": 900},
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                with page.expect_response(
                    lambda response: "/api/v1/library" in response.url
                ) as read:
                    _login(page, origin)
                identity = read.value.headers.get("x-ml-build-identity")
                assert identity is not None
                assert re.fullmatch(r"[0-9a-f]{64}", identity)
                assert identity != _NEXT_BUILD
                page.get_by_role("heading", name="Works", exact=True).wait_for()

                _upgrade_under(page)
                page.get_by_role("link", name="Activity", exact=True).click()

                notice = page.locator(".build-mismatch")
                notice.wait_for()
                assert notice.get_by_text(
                    "This page is from a different application build."
                ).is_visible()
                page.get_by_role(
                    "button", name="Reload the current application"
                ).wait_for()
                assert page.get_by_role("button", name="Try again").count() == 0

                # An expired session names no build, so the tab signs in again
                # rather than reloading into a page that would refuse it.
                context.clear_cookies()
                page.get_by_role("link", name="Policy", exact=True).click()
                page.wait_for_url(re.compile(r".*/login(\?.*)?$"))
                page.get_by_role("button", name="Sign in").wait_for()
            browser.close()
