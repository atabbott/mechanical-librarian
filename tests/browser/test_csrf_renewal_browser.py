"""A rotated CSRF token costs the owner no second press where a resend is safe.

Every authenticated shell render rotates the session's one CSRF digest through
`issue_browser_csrf`, so a page that was open before another page of the same
session rendered holds a rotated-away token. The setup handoff rotates through
that same call, which is why a second page rendering the shell in the same
browser context stands in for it here: both leave the first page with a stale
token against one shared session and cookie jar (#1018).
"""

from __future__ import annotations

import os
import tempfile
from contextlib import closing
from pathlib import Path

from argon2 import PasswordHasher
from playwright.sync_api import BrowserContext, Page, Response, sync_playwright

from tests.browser.test_review_browser import (
    _PASSWORD,
    _certificate,
    _closing_context,
    _login,
    _server,
)
from tests.support.ports import ReservedPort


def _rotate(context: BrowserContext, origin: str) -> None:
    """Render the shell in another page of the same session, then close it."""
    other = context.new_page()
    other.goto(f"{origin}/library")
    other.get_by_role("heading", name="Works", exact=True).wait_for()
    other.close()


def _statuses(page: Page, suffix: str) -> list[int]:
    seen: list[int] = []

    def record(response: Response) -> None:
        if response.url.split("?", 1)[0].endswith(suffix):
            seen.append(response.status)

    page.on("response", record)
    return seen


def test_csrf_renewal_resends_idempotent_decision_and_pure_policy_validation() -> None:
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
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                page.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                choice = page.get_by_role(
                    "radio", name="Choose current value:", exact=False
                )
                choice.wait_for()
                decisions = _statuses(page, "/decisions")
                _rotate(context, origin)

                # One click records the decision: the stale token is refused,
                # renewed, and the idempotency-keyed command is sent once more.
                choice.click()
                page.get_by_role(
                    "heading", name="Which series should this work use?"
                ).wait_for()
                assert decisions == [403, 200]

                # In-shell navigation keeps the renewed token; the second
                # render rotates it away again before the first press.
                page.get_by_role("link", name="Policy", exact=True).click()
                page.get_by_role("heading", name="Library policy").wait_for()
                page.get_by_role("button", name="Curation").click()
                page.wait_for_url("**/policy?section=curation")
                genres = page.get_by_role("region", name="Genres")
                genres.get_by_role("button", name="Ignore").click()
                validations = _statuses(page, "/metadata-policy/validate")
                _rotate(context, origin)

                page.get_by_role("button", name="Validate and preview").click()
                page.get_by_text(
                    "Impact preview is current. The active policy is unchanged."
                ).wait_for()
                assert validations == [403, 200]
            browser.close()
