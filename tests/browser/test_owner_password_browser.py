"""Settings changes the owner password in store mode, and only there.

The change keeps the browser that made it signed in on a new session, and
ends every other session: a second browser context signed in beforehand is
sent to sign-in on its next request. The new password signs in afterwards and
the old one is refused. File mode keeps its hash file and shows no row.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal

from argon2 import PasswordHasher
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.browser.test_review_browser import (
    _PASSWORD,
    _certificate,
    _closing_context,
    _login,
    _server,
)
from tests.support.ports import ReservedPort

_NEW_PASSWORD = "synthetic replacement password"
_LIBRARY = re.compile(r".*/library(\?.*)?$")


@contextmanager
def _owner_server(
    mode: Literal["file", "store"],
) -> Iterator[tuple[Browser, str]]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        hash_path = root / "owner.hash"
        hash_path.write_text(PasswordHasher().hash(_PASSWORD), encoding="utf-8")
        os.chmod(hash_path, 0o600)
        certificate, key = _certificate(root)
        with (
            sync_playwright() as playwright,
            closing(ReservedPort()) as reserved_port,
            _server(
                database=root / "review.sqlite3",
                hash_path=hash_path,
                certificate=certificate,
                key=key,
                port=reserved_port,
                owner_password=mode,
            ) as (origin, _case_id),
        ):
            browser = playwright.chromium.launch(headless=True)
            try:
                yield browser, origin
            finally:
                browser.close()


def _sign_in(page: Page, origin: str, password: str) -> None:
    page.goto(f"{origin}/login")
    page.get_by_label("Owner password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in").click()


def _access(page: Page, origin: str) -> None:
    page.goto(f"{origin}/settings?group=access")
    page.get_by_role("heading", name="Access and diagnostics", exact=True).wait_for()


def test_file_mode_shows_no_owner_password_row() -> None:
    with _owner_server("file") as (browser, origin):
        context = browser.new_context(ignore_https_errors=True)
        with _closing_context(context):
            page = context.new_page()
            _login(page, origin)
            _access(page, origin)
            expect(page.get_by_text("Diagnostic timings")).to_be_visible()
            expect(page.get_by_text("Owner password")).to_have_count(0)
            expect(page.get_by_role("button", name="Change password")).to_have_count(0)


def test_store_mode_changes_the_password_and_signs_out_every_other_browser() -> None:
    with _owner_server("store") as (browser, origin):
        changer = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1180, "height": 900}
        )
        other = browser.new_context(ignore_https_errors=True)
        with _closing_context(changer), _closing_context(other):
            page = changer.new_page()
            _login(page, origin)
            elsewhere = other.new_page()
            _login(elsewhere, origin)

            _access(page, origin)
            titles = page.locator(".settings-sheet .settings-field-title")
            expect(titles.first).to_have_text("Owner password")
            page.get_by_role("button", name="Change password").click()
            current = page.get_by_label("Current password", exact=True)
            new = page.get_by_label("New password", exact=True)
            confirmation = page.get_by_label("Confirm new password", exact=True)
            expect(current).to_be_focused()

            # A wrong current password is refused beside it, keeping every value.
            current.fill("not the synthetic password")
            new.fill(_NEW_PASSWORD)
            confirmation.fill(_NEW_PASSWORD)
            page.get_by_role("button", name="Change password").click()
            refusal = page.get_by_text(
                "That password was not accepted. Check it and try again."
            )
            expect(refusal).to_be_visible()
            expect(current).to_have_attribute("aria-invalid", "true")
            expect(current).to_be_focused()
            expect(current).to_have_value("not the synthetic password")
            expect(new).to_have_value(_NEW_PASSWORD)
            expect(confirmation).to_have_value(_NEW_PASSWORD)

            # Too short marks the hint under New password and adds no line.
            current.fill(_PASSWORD)
            new.fill("too short")
            confirmation.fill("too short")
            page.get_by_role("button", name="Change password").click()
            hint = page.get_by_text("At least 12 characters.")
            expect(hint).to_have_class(re.compile(r"\bis-refused\b"))
            expect(new).to_have_attribute("aria-invalid", "true")
            expect(new).to_be_focused()
            expect(
                page.locator(".settings-password .settings-field-error")
            ).to_have_count(0)
            expect(page.get_by_text("Use at least 12 characters.")).to_have_count(0)
            # The refused hint is drawn in the danger colour and larger.
            assert hint.evaluate("e => getComputedStyle(e).fontSize") == "14px"
            assert new.evaluate(
                "e => getComputedStyle(e).borderColor"
            ) == hint.evaluate("e => getComputedStyle(e).color")

            new.fill(_NEW_PASSWORD)
            expect(hint).not_to_have_class(re.compile(r"\bis-refused\b"))
            confirmation.fill(_NEW_PASSWORD)
            page.get_by_role("button", name="Change password").click()
            status = page.get_by_role("status").filter(
                has_text="Password changed. Every other browser was signed out."
            )
            expect(status).to_be_visible()
            expect(status).to_be_focused()
            expect(current).to_have_count(0)

            # This browser stays signed in on its new session, and the token
            # renewed after the change carries its next unsafe request at once.
            page.get_by_role("group", name="Diagnostic timings").get_by_role(
                "button", name="On"
            ).click()
            page.get_by_role("button", name="Save settings").click()
            expect(page.get_by_text("Settings saved and in use.")).to_be_visible()

            # A Settings save leaves an open password form as it was typed.
            page.get_by_role("button", name="Change password").click()
            current.fill("typed before the save")
            page.get_by_role("group", name="Diagnostic timings").get_by_role(
                "button", name="Off"
            ).click()
            save = page.get_by_role("button", name="Save settings")
            save.click()
            expect(save).to_have_count(0)
            expect(current).to_have_value("typed before the save")
            page.get_by_role("button", name="Cancel").click()
            expect(current).to_have_count(0)

            page.get_by_role("link", name="Library", exact=True).click()
            page.wait_for_url(_LIBRARY)
            page.get_by_role("heading", name="Works", exact=True).wait_for()
            _access(page, origin)
            expect(page.get_by_text("Owner password")).to_be_visible()

            # The other browser is sent to sign-in on its next request.
            elsewhere.get_by_role("link", name="Settings", exact=True).click()
            elsewhere.wait_for_url(re.compile(r".*/login(\?.*)?$"))

            # The old password is refused and the new one signs in.
            _sign_in(elsewhere, origin, _PASSWORD)
            expect(
                elsewhere.get_by_text(
                    "That password was not accepted. Check it and try again."
                )
            ).to_be_visible()
            assert "/login" in elsewhere.url
            _sign_in(elsewhere, origin, _NEW_PASSWORD)
            elsewhere.wait_for_url(_LIBRARY)
