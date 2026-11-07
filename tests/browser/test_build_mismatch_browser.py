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

import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from playwright.sync_api import Page, Route, ViewportSize, expect, sync_playwright

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
                    "Mechanical Librarian was updated."
                ).is_visible()
                page.get_by_role("heading", name="Activity was not loaded").wait_for()
                page.get_by_role("button", name="Reload", exact=True).wait_for()
                assert page.get_by_role("button", name="Try again").count() == 0

                # An expired session names no build, so the tab signs in again
                # rather than reloading into a page that would refuse it.
                context.clear_cookies()
                page.get_by_role("link", name="Policy", exact=True).click()
                page.wait_for_url(re.compile(r".*/login(\?.*)?$"))
                page.get_by_role("button", name="Sign in").wait_for()
            browser.close()


def _answer_as_next_build(route: Route) -> None:
    response = route.fetch()
    headers = dict(response.headers)
    headers["x-ml-build-identity"] = _NEXT_BUILD
    route.fulfill(response=response, headers=headers)


@contextmanager
def _signed_in(
    viewport: ViewportSize, **server: Any
) -> Iterator[tuple[Page, str, str]]:
    """A signed-in page on the real review app, with the `_server` fixture asked for."""
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
                ignore_https_errors=True, viewport=viewport, reduced_motion="reduce"
            )
            with (
                closing(ReservedPort()) as reserved_port,
                _server(
                    database=root / "review.sqlite3",
                    hash_path=hash_path,
                    certificate=certificate,
                    key=key,
                    port=reserved_port,
                    **server,
                ) as (origin, case_id),
                _closing_context(context),
            ):
                page = context.new_page()
                _login(page, origin)
                yield page, origin, case_id
            browser.close()


def _held(page: Page, held: list[Route]) -> Route:
    """Wait for the page to make the request a test is holding, and return it."""
    for _ in range(200):
        if held:
            return held.pop(0)
        page.wait_for_timeout(25)
    raise AssertionError("the held request was never made")


# Each retry that leaves the page hands focus to what replaced it (#1742).


@pytest.mark.parametrize(
    "viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
)
def test_a_selected_work_retry_that_meets_a_newer_build_focuses_reload(
    viewport: ViewportSize,
) -> None:
    with _signed_in(viewport) as (page, origin, case_id):
        selected_reads = re.compile(
            rf".*/api/v1/(library\?.*|review-cases/{re.escape(case_id)})$"
        )
        page.route(selected_reads, lambda route: route.abort("failed"))
        page.goto(f"{origin}/library?lens=all&preview={case_id}")
        workspace = page.locator("#main-content .workspace-detail")
        failure = workspace.get_by_role("alert").filter(
            has_text="Selected work could not be loaded."
        )
        expect(failure).to_be_visible()
        # Every read the retry makes now meets the next build.
        page.unroute(selected_reads)
        _upgrade_under(page)
        retry = failure.get_by_role("button", name="Try again")
        retry.focus()
        page.keyboard.press("Enter")
        reload = workspace.get_by_role("button", name="Reload", exact=True)
        expect(reload).to_be_focused()
        assert page.evaluate("() => document.activeElement !== document.body")


@pytest.mark.parametrize("plain_mismatch", [False, True])
def test_load_more_transitions_keeps_focus_while_it_reads_and_its_retry_focuses_reload(
    plain_mismatch: bool,
) -> None:
    def has_more(route: Route) -> None:
        response = route.fetch()
        body = response.json()
        body["has_more"] = True
        route.fulfill(response=response, body=json.dumps(body))

    with _signed_in({"width": 1440, "height": 900}) as (page, origin, _case_id):
        page.route(re.compile(r".*/api/v1/activity/[^/?]+/transitions$"), has_more)
        held: list[Route] = []
        page.route(
            re.compile(r".*/transitions\?after_sequence=.*"),
            lambda route: held.append(route),
        )
        page.goto(f"{origin}/activity")
        page.get_by_role("heading", name="Activity ledger").wait_for()
        row = page.locator("article").filter(
            has=page.get_by_role("list", name="Recorded lifecycle transitions")
        )
        details = page.locator("article").filter(
            has=page.get_by_role("button", name=re.compile(r"^Details for "))
        )
        details.first.get_by_role("button", name=re.compile(r"^Details for ")).click()
        more = row.get_by_role("button", name="Load more transitions")
        expect(more).to_be_visible()
        answers: list[str] = ["mismatch"] if plain_mismatch else ["success", "abort"]
        for answer in answers:
            more.focus()
            page.keyboard.press("Enter")
            pending = _held(page, held)
            # A read longer than a frame: the busy button keeps focus.
            page.wait_for_timeout(800)
            busy = row.get_by_role("button", name="Loading transitions…")
            expect(busy).to_be_focused()
            expect(busy).to_have_attribute("aria-disabled", "true")
            if answer == "success":
                has_more(pending)
            elif answer == "abort":
                pending.abort("failed")
            else:
                _answer_as_next_build(pending)
            expect(more).to_be_focused()
        if plain_mismatch:
            expect(row.get_by_role("button", name="Reload", exact=True)).to_be_visible()
            return
        # Try again moves focus to the busy Load more, then to the reload
        # that replaces it on a newer build.
        row.get_by_role("alert").get_by_role("button", name="Try again").click()
        pending = _held(page, held)
        page.wait_for_timeout(800)
        expect(row.get_by_role("button", name="Loading transitions…")).to_be_focused()
        _answer_as_next_build(pending)
        expect(row.get_by_role("button", name="Reload", exact=True)).to_be_focused()


_SENTENCE_CLEARANCE = """
paragraph => {
  const button = paragraph.querySelector('button')
  const range = document.createRange()
  range.setStartBefore(paragraph.firstChild)
  range.setEndBefore(button)
  const lines = [...range.getClientRects()].filter(rect => rect.width > 0)
  const last = lines.reduce((lowest, rect) => (rect.bottom > lowest.bottom ? rect : lowest))
  const box = button.getBoundingClientRect()
  return { wrapped: box.top >= last.bottom - 1, gap: box.top - last.bottom }
}
"""


_FULL_STOP_CLEARANCE = """
paragraph => {
  const button = paragraph.querySelector('button')
  // React renders the lead and the sentences after it as separate text nodes.
  const text = [...paragraph.childNodes].filter(
    node => node.nodeType === Node.TEXT_NODE && node.textContent.includes('.'),
  ).pop()
  const stop = text.textContent.lastIndexOf('.')
  const range = document.createRange()
  range.setStart(text, stop)
  range.setEnd(text, stop + 1)
  const dot = range.getBoundingClientRect()
  const box = button.getBoundingClientRect()
  return { sameLine: box.top < dot.bottom && box.bottom > dot.top, gap: box.left - dot.right }
}
"""


def test_more_attention_try_again_focuses_the_added_row_and_reload_clears_its_sentence() -> (
    None
):
    with _signed_in(
        {"width": 1440, "height": 900}, provider_recovery=True, cohort=30
    ) as (page, origin, _case_id):
        page.get_by_role("button", name=re.compile(r"^Attention ")).click()
        rail = page.get_by_label("Attention queue", exact=True)
        rows = rail.locator(".attention-queue-item")
        expect(rows.first).to_be_visible()
        loaded = rows.count()
        later = re.compile(r".*/api/v1/attention\?(.*&)?cursor=.*")
        page.route(later, lambda route: route.abort("failed"))
        rail.get_by_role("button", name="Load more attention", exact=True).click()
        retry = rail.get_by_role("alert").get_by_role("button", name="Try again")
        expect(retry).to_be_visible()
        page.unroute(later)
        retry.focus()
        page.keyboard.press("Enter")
        expect(rows.nth(loaded)).to_be_focused()

        # At phone width the reload wraps under its sentence; its focus ring
        # (3px at 3px offset) clears the sentence's last line.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(f"{origin}/library?lens=all&attention=open")
        rail = page.get_by_label("Attention queue", exact=True)
        expect(rail.locator(".attention-queue-item").first).to_be_visible()
        page.route(later, _answer_as_next_build)
        rail.get_by_role("button", name="Load more attention", exact=True).click()
        stale = rail.locator(".stale-build-read").filter(
            has_text="More attention was not loaded."
        )
        reload = stale.get_by_role("button", name="Reload", exact=True)
        expect(reload).to_be_visible()
        reload.focus()
        expect(reload).to_be_focused()
        clearance = stale.evaluate(_SENTENCE_CLEARANCE)
        assert clearance["wrapped"], clearance
        assert clearance["gap"] >= 6, clearance


def test_review_current_comparison_keeps_focus_while_it_reads_then_focuses_reload() -> (
    None
):
    with _signed_in(
        {"width": 1440, "height": 900}, cover=True, stale_cover_successor=True
    ) as (page, origin, case_id):
        page.goto(f"{origin}/library?lens=all&preview={case_id}&field=cover")
        page.get_by_role("button", name="Prepare cover comparison").click()
        comparison = page.get_by_role("region", name="Choose a cover")
        comparison.wait_for()
        comparison.locator(".library-cover-card").filter(
            has=page.get_by_alt_text("EPUB file cover")
        ).click()
        review = page.get_by_role("button", name="Review current comparison")
        expect(review).to_be_visible()
        held: list[Route] = []
        page.route(
            re.compile(r".*/api/v1/review-cases/[^/]+/successor$"),
            lambda route: held.append(route),
        )
        review.focus()
        page.keyboard.press("Enter")
        pending = _held(page, held)
        page.wait_for_timeout(800)
        busy = page.get_by_role("button", name="Loading current comparison…")
        expect(busy).to_be_focused()
        expect(busy).to_have_attribute("aria-disabled", "true")
        _answer_as_next_build(pending)
        reload = page.locator(".comparison-notice").get_by_role(
            "button", name="Reload", exact=True
        )
        expect(reload).to_be_focused()
        expect(
            page.get_by_role("button", name="Review current comparison")
        ).to_have_count(0)
        # At this width the reload shares the line with the sentence's full
        # stop; its focus ring (3px at 3px offset) clears it.
        clearance = page.locator(".comparison-notice .stale-build-read").evaluate(
            _FULL_STOP_CLEARANCE
        )
        assert clearance["sameLine"], clearance
        assert clearance["gap"] >= 6, clearance


# The focused element is inside the viewport and nothing covers its centre:
# not above it, not under the sticky masthead, not below the fold.
_IN_VIEW = """
element => {
  const box = element.getBoundingClientRect()
  const x = box.left + box.width / 2
  const y = box.top + box.height / 2
  const top = document.elementFromPoint(x, y)
  return {
    top: box.top,
    bottom: box.bottom,
    height: window.innerHeight,
    inside: box.top >= 0 && box.bottom <= window.innerHeight,
    uncovered: top !== null && (top === element || element.contains(top)),
  }
}
"""


def _expect_in_view(page: Page) -> None:
    placement = page.evaluate_handle("() => document.activeElement").evaluate(_IN_VIEW)
    assert placement["inside"], placement
    assert placement["uncovered"], placement


@pytest.mark.parametrize(
    "viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
)
def test_the_reload_a_confirm_re_read_focuses_is_scrolled_into_view(
    viewport: ViewportSize,
) -> None:
    with _signed_in(viewport) as (page, origin, _case_id):
        # Confirm re-reads the consent basis, which meets the next build: the
        # Reload at the top of the permission panel is brought into view.
        page.goto(f"{origin}/activity")
        page.get_by_role("heading", name="Activity and recovery").wait_for()
        review = page.locator("#activity-consent")
        page.get_by_role("button", name="Review write permissions").click()
        page.get_by_text(
            "Backup readiness checked. Review the included and excluded destinations.",
            exact=True,
        ).wait_for()
        review.get_by_role("checkbox", name="fiction").check()
        page.route(
            re.compile(r".*/api/v1/activity/consent-state.*"), _answer_as_next_build
        )
        confirm = page.get_by_role("button", name="Confirm write permissions")
        confirm.scroll_into_view_if_needed()
        confirm.focus()
        page.keyboard.press("Enter")
        reload = review.get_by_role("button", name="Reload", exact=True)
        expect(reload).to_be_focused()
        _expect_in_view(page)


@pytest.mark.parametrize(
    "viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
)
def test_the_reload_a_more_attention_retry_focuses_is_scrolled_into_view(
    viewport: ViewportSize,
) -> None:
    with _signed_in(viewport, provider_recovery=True, cohort=30) as (
        page,
        origin,
        _case_id,
    ):
        # The rail's Load more attention fails, then its Try again meets the
        # next build: the Reload at the foot of the rail is brought into view.
        page.goto(f"{origin}/library?lens=all&attention=open")
        rail = page.get_by_label("Attention queue", exact=True)
        expect(rail.locator(".attention-queue-item").first).to_be_visible()
        later = re.compile(r".*/api/v1/attention\?(.*&)?cursor=.*")
        page.route(later, lambda route: route.abort("failed"))
        rail.get_by_role("button", name="Load more attention", exact=True).click()
        retry = rail.get_by_role("alert").get_by_role("button", name="Try again")
        expect(retry).to_be_visible()
        page.unroute(later)
        page.route(later, _answer_as_next_build)
        retry.focus()
        page.keyboard.press("Enter")
        reload = rail.get_by_role("button", name="Reload", exact=True)
        expect(reload).to_be_focused()
        _expect_in_view(page)
