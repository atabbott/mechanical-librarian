"""The installed build, proved to be live rather than merely built to be.

Every other proof of this seam is at a layer that can be right while the browser
is still polling: the channel is unit-tested, the endpoint is cohort-tested, and
the client's invalidation is tested against a fake stream. What none of them can
answer is whether the shipped bundle actually opens the stream, and whether a
change made in one tab reaches another one without a reload. That is what this
module drives a real server and a real browser to find out (#641).
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import closing
from pathlib import Path

from argon2 import PasswordHasher
from playwright.sync_api import Page, Response, sync_playwright

from tests.browser.test_review_browser import (
    _PASSWORD,
    _certificate,
    _closing_context,
    _login,
    _server,
)
from tests.support.ports import ReservedPort


def _wait_until(page: Page, condition, *, timeout: float = 15.0) -> bool:
    """Wait on a condition rather than on a duration.

    The wait is spent inside Playwright rather than in `time.sleep`, because the
    synchronous API dispatches its `response` callbacks only while it is
    executing an operation. A Python sleep here would let every response this
    module measures arrive unobserved, and the condition would never become
    true no matter how long it waited.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        page.wait_for_timeout(50)
    return condition()


# Wider glyphs than any font this suite can rely on being installed, applied by
# rewriting the application's own stylesheet on the way to the browser. It has
# to arrive as a same-origin stylesheet because the application's
# Content-Security-Policy refuses an injected one -- which is correct, and is
# why the stress is served rather than evaluated.
_WIDER_TEXT = (
    "\n* { letter-spacing: 0.08em !important; word-spacing: 0.12em !important; }"
)

_CONTENT_OVERFLOW = """
(selector) => {
  const root = document.querySelector(selector);
  if (root === null) return null;
  let worst = 0;
  for (const node of [root, ...root.querySelectorAll('*')]) {
    worst = Math.max(worst, node.scrollWidth - node.clientWidth);
    const style = getComputedStyle(node);
    const right = node.getBoundingClientRect().right -
      parseFloat(style.borderRightWidth || 0) - parseFloat(style.paddingRight || 0);
    for (const child of node.children)
      worst = Math.max(worst, Math.round(child.getBoundingClientRect().right - right));
  }
  return worst;
}
"""


def _widen_text(page: Page) -> None:
    """Serve the real stylesheet with every glyph advance widened."""

    def rewrite(route) -> None:
        response = route.fetch()
        route.fulfill(
            response=response,
            body=response.text() + _WIDER_TEXT,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() != "content-length"
            },
        )

    page.route("**/*.css", rewrite)


def _fits_every_width(page: Page, selector: str) -> None:
    """Assert one surface stays inside its container, and the page inside the
    viewport, at each width the protected contract names.

    The stress above is the point: a row whose width is the width of its text,
    or a grid with a declared track count, fits or overflows depending on the
    glyph metrics of whichever machine ran it. Without it this reads as a pass
    on one platform and a 66-pixel document overrun on another (#641, #639).
    """
    for width in (1440, 1044, 800, 390):
        page.set_viewport_size({"width": width, "height": 844})
        page.evaluate("() => document.documentElement.offsetWidth")
        overflow = page.evaluate(_CONTENT_OVERFLOW, selector)
        assert overflow is not None, f"{selector} is absent at {width}px"
        assert overflow <= 0, (
            f"{selector} overflows its container by {overflow}px at {width}px"
        )
        document = page.evaluate("() => document.documentElement.scrollWidth")
        assert document <= width, (
            f"{selector}'s page is {document}px wide in a {width}px viewport"
        )


def test_a_change_in_one_tab_reaches_another_without_a_reload() -> None:
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
            watcher = context.new_page()
            streams: list[str] = []
            library_reads: list[str] = []
            documents: list[str] = []

            def record(response: Response) -> None:
                path = response.url.split("?", 1)[0]
                if path.endswith("/api/v1/updates"):
                    streams.append(response.headers.get("content-type", ""))
                elif path.endswith("/api/v1/library"):
                    library_reads.append(response.url)

            watcher.on("response", record)
            watcher.on(
                "request",
                lambda request: (
                    documents.append(request.url)
                    if request.resource_type == "document"
                    else None
                ),
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
                _login(watcher, origin)
                watcher.get_by_role(
                    "heading", name="Every work, with exceptions first"
                ).wait_for(timeout=8_000)

                # One stream, opened by the shipped bundle itself, carrying the
                # media type the contract publishes.
                assert _wait_until(watcher, lambda: bool(streams)), (
                    "no stream was opened"
                )
                assert streams[0].startswith("text/event-stream")
                assert len(streams) == 1

                # Nothing about Library polls, so this number stops moving on
                # its own. Anything it does after this line is a consequence of
                # the announcement below and of nothing else.
                assert _wait_until(watcher, lambda: bool(library_reads))
                settled = len(library_reads)
                watcher.wait_for_timeout(1_500)
                assert len(library_reads) == settled
                documents_before = len(documents)

                actor = context.new_page()
                actor.goto(f"{origin}/review/{case_id}?field=title&subview=review")
                actor.get_by_role("heading", name="Current review case").wait_for()
                actor.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).first.click()
                actor.get_by_text(
                    "Decision recorded from the authoritative case.", exact=True
                ).wait_for()

                # The other tab looked again, at the same bounded page it was
                # already showing, because it was told to -- not because it was
                # polling, and not because anything reloaded it.
                assert _wait_until(watcher, lambda: len(library_reads) > settled), (
                    "the watching tab never refetched after the decision"
                )
                watcher.wait_for_timeout(1_500)
                # Exactly one refetch: a decision announces the case, its row
                # and the write queue, and coalescing turns that into one read
                # of the page rather than three.
                assert len(library_reads) == settled + 1
                assert library_reads[-1].endswith("/api/v1/library?lens=all")
                assert len(documents) == documents_before
                assert "Reconnecting" not in watcher.locator("body").inner_text()

                # Dark is a first-class theme for these surfaces, not a filter
                # over the light one: the shell repaints from the dark tokens.
                watcher.get_by_role("button", name="Use dark mode").click()
                watcher.get_by_role("button", name="Use light mode").wait_for()
                assert (
                    watcher.evaluate("() => document.documentElement.dataset.theme")
                    == "dark"
                )
                assert (
                    watcher.evaluate(
                        "() => getComputedStyle(document.body).backgroundColor"
                    )
                    == "rgb(15, 21, 18)"
                )
                assert len(documents) == documents_before


def test_the_write_surfaces_reflow_rather_than_depend_on_how_wide_their_text_renders() -> (
    None
):
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
            page = context.new_page()
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
                _login(page, origin)
                _widen_text(page)
                # Refused, so the shell falls back to polling and says so: the
                # notice it then shows is a row whose width is its own text,
                # which is exactly the shape that fits on one platform's fonts
                # and overruns on another's.
                page.route(
                    "**/api/v1/updates",
                    lambda route: route.fulfill(status=503, body=""),
                )
                page.reload()

                page.get_by_role(
                    "heading", name="Every work, with exceptions first"
                ).wait_for(timeout=8_000)
                page.locator(".cm-library-handoff").wait_for(timeout=8_000)
                page.get_by_text("Reconnecting", exact=False).wait_for(timeout=8_000)
                _fits_every_width(page, ".cm-library-handoff")
                _costs_the_masthead_no_width(page)
                page.reload()
                page.get_by_text("Reconnecting", exact=False).wait_for(timeout=8_000)

                page.set_viewport_size({"width": 1440, "height": 900})
                page.goto(f"{origin}/activity")
                page.get_by_role("heading", name="Activity and recovery").wait_for()
                page.locator(".cm-write-consent").wait_for(timeout=8_000)
                _fits_every_width(page, ".cm-write-consent")
                _fits_every_width(page, ".cm-activity-status")


def _masthead_overflow(page: Page) -> list[int]:
    """The masthead's own content overflow at each protected width."""
    measured = []
    for width in (1440, 1044, 800, 390):
        page.set_viewport_size({"width": width, "height": 844})
        page.evaluate("() => document.documentElement.offsetWidth")
        measured.append(page.evaluate(_CONTENT_OVERFLOW, ".masthead"))
    return measured


def _costs_the_masthead_no_width(page: Page) -> None:
    """Assert the degraded notice adds nothing to the masthead's width.

    Stated as a comparison rather than as a number, because the alternative is a
    tolerance -- and a tolerance is the same font-dependent guess as the layout
    it would be excusing. The notice is a row of text in a row that cannot grow;
    whether it fits is a fact about glyph metrics, so the contract has to be
    that its presence costs nothing, at every width, with the text widened.
    """
    with_notice = _masthead_overflow(page)
    removed = page.evaluate(
        "() => { const node = document.querySelector('.live-state');"
        " if (node === null) return false; node.remove(); return true; }"
    )
    assert removed, "the degraded notice was not present to compare against"
    assert _masthead_overflow(page) == with_notice, (
        f"the degraded notice widens the masthead: {with_notice} with it, "
        f"{_masthead_overflow(page)} without"
    )
