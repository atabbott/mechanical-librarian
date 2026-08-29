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
import re
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
    // A node with no layout box has no width to overrun, and measuring one
    // reports its own padding as an overflow: the rect is all zeros while
    // `getComputedStyle` still answers with the padding it would have had.
    // A surface that hides at a breakpoint is not a surface that overflows.
    if (node.getClientRects().length === 0) continue;
    const permittedScroller = node.matches('.cm-activity-filters') &&
      getComputedStyle(node).overflowX === 'auto' &&
      node.scrollWidth > node.clientWidth;
    if (permittedScroller) continue;
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


def _activity_surface_order(
    page: Page, *, width: int, semantic: list[str], visual: list[str]
) -> None:
    """Keep Activity's semantic order separate from its responsive layout."""
    page.set_viewport_size({"width": width, "height": 844})
    order = page.evaluate(
        """() => {
          const root = document.querySelector('.cm-activity-route');
          if (root === null) return null;
          const name = (node) => node.className;
          const children = [...root.children].filter((node) =>
            node.matches('.cm-page-heading, .cm-activity-ledger, .cm-write-consent, .cm-activity-status, .connection-notice, .activity-command-status')
          );
          return {
            dom: children.map(name),
            visual: children
              .map((node, index) => ({ index, name: name(node), order: Number(getComputedStyle(node).order) }))
              .sort((left, right) => left.order - right.order || left.index - right.index)
              .map((item) => item.name),
          };
        }"""
    )
    assert order is not None
    assert order["dom"] == semantic
    assert order["visual"] == visual


# The same measurement, reported as the node that lost rather than as the
# number it lost by. A bare pixel count says a surface is wrong; this says
# which element inside it is, which is the difference between a finding and a
# hunt (#636).
_WORST_OVERFLOWING_NODE = r"""
(selector) => {
  const root = document.querySelector(selector);
  if (root === null) return null;
  let worst = 0;
  let where = null;
  const name = (node) => node.tagName.toLowerCase() +
    (node.className && typeof node.className === 'string'
      ? '.' + node.className.trim().split(/\s+/).join('.') : '');
  for (const node of [root, ...root.querySelectorAll('*')]) {
    if (node.getClientRects().length === 0) continue;
    const permittedScroller = node.matches('.cm-activity-filters') &&
      getComputedStyle(node).overflowX === 'auto' &&
      node.scrollWidth > node.clientWidth;
    if (permittedScroller) continue;
    if (node.scrollWidth - node.clientWidth > worst) {
      worst = node.scrollWidth - node.clientWidth;
      where = name(node) + ' scrolls its own content (' +
        node.scrollWidth + ' in ' + node.clientWidth + ', parent ' +
        name(node.parentElement) + ', text ' +
        JSON.stringify((node.textContent || '').slice(0, 40)) + ')';
    }
    const style = getComputedStyle(node);
    const right = node.getBoundingClientRect().right -
      parseFloat(style.borderRightWidth || 0) - parseFloat(style.paddingRight || 0);
    for (const child of node.children) {
      const past = Math.round(child.getBoundingClientRect().right - right);
      if (past > worst) { worst = past; where = name(child) + ' inside ' + name(node); }
    }
  }
  return where;
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
            f"{selector} overflows its container by {overflow}px at {width}px: "
            f"{page.evaluate(_WORST_OVERFLOWING_NODE, selector)}"
        )
        document = page.evaluate("() => document.documentElement.scrollWidth")
        assert document <= width, (
            f"{selector}'s page is {document}px wide in a {width}px viewport"
        )


# An Activity row is built on ellipsis: a long title and a long change are
# meant to be cut at desktop, and `_fits_every_width` would read every one of
# those as an overrun. What must never be cut is different and smaller -- the
# grid must not push past the ledger, the category pill must show its whole
# word rather than wrapping or clipping, and the disclosure must keep its whole
# label on screen. Those are measured against the boxes that constrain them,
# and the two things the compact card promises to show whole are measured only
# where it promises them (#636).
_ROW_PROMISES = """
(compact) => {
  const failures = [];
  const ledger = document.querySelector('.cm-activity-ledger');
  if (ledger === null) return ['the ledger is absent'];
  const ledgerStyle = getComputedStyle(ledger);
  const ledgerRight = ledger.getBoundingClientRect().right -
    parseFloat(ledgerStyle.borderRightWidth) - parseFloat(ledgerStyle.paddingRight);
  const viewport = document.documentElement.clientWidth;
  const clipped = (node) => node.scrollWidth - node.clientWidth;
  const rows = [...document.querySelectorAll('.cm-activity-event-group article')];
  if (rows.length === 0) return ['the ledger has no rows'];
  for (const row of rows) {
    const past = Math.round(row.getBoundingClientRect().right - ledgerRight);
    if (past > 0) failures.push(`a row runs ${past}px past the ledger`);
    const pill = row.querySelector('.cm-activity-category');
    if (pill === null) failures.push('a row has no category');
    else {
      if (clipped(pill) > 0) failures.push(`the ${pill.textContent} pill is clipped`);
      if (pill.getClientRects().length !== 1)
        failures.push(`the ${pill.textContent} pill wrapped internally`);
    }
    const action = row.querySelector('button');
    if (action === null) failures.push('a row has no disclosure');
    else {
      const box = action.getBoundingClientRect();
      if (clipped(action) > 0)
        failures.push(`the ${action.textContent} label is clipped`);
      if (box.right > viewport || box.left < 0)
        failures.push(`the ${action.textContent} action is off screen`);
      if (box.width < 44 || box.height < 40)
        failures.push(`the ${action.textContent} target is ${box.width}x${box.height}`);
    }
    if (compact)
      for (const selector of ['.cm-activity-work > strong', '.cm-activity-change'])
        for (const node of row.querySelectorAll(selector))
          if (clipped(node) > 0)
            failures.push(`${selector} is clipped on a compact card`);
  }
  return failures;
}
"""


def _rows_keep_their_promises(page: Page) -> None:
    """Assert what an Activity row may never do, at each protected width."""
    for width in (1440, 1100, 1044, 800, 761, 760, 390):
        page.set_viewport_size({"width": width, "height": 844})
        page.evaluate("() => document.documentElement.offsetWidth")
        failures = page.evaluate(_ROW_PROMISES, width <= 760)
        assert failures == [], f"at {width}px: {failures}"
        document = page.evaluate("() => document.documentElement.scrollWidth")
        assert document <= width, (
            f"the Activity page is {document}px wide in a {width}px viewport"
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
                watcher.get_by_role("heading", name="Works", exact=True).wait_for(
                    timeout=8_000
                )

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
                actor.goto(f"{origin}/library?lens=all&preview={case_id}&field=title")
                actor.get_by_role("heading", name="Works", exact=True).wait_for()
                actor.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).first.click()
                # The authoritative acknowledgement advances to the next exact
                # field; it is not retained as a cross-field status message.
                actor.get_by_role(
                    "heading", name="Which series should this work use?"
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


def test_preparation_run_shares_library_workspace_and_compacts_without_losing_state() -> None:
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
                    preparation=True,
                ) as (origin, _case_id),
                _closing_context(context),
            ):
                _login(page, origin)
                works = page.get_by_role("heading", name="Works", exact=True)
                summary = page.get_by_role("button", name="Preparing your library")
                works.wait_for()
                summary.wait_for()
                assert summary.bounding_box()["y"] < works.bounding_box()["y"]

                summary.click()
                page.get_by_role("heading", name="Now preparing").wait_for()
                page.get_by_text("Preparation book 00").wait_for()
                assert page.get_by_text(re.compile(r"more active books follow")).count() == 0
                assert page.locator(".preparation-active-books li").count() == 1
                assert page.locator("[data-route-focus]").evaluate(
                    "element => element === document.activeElement"
                )

                page.get_by_role("button", name=re.compile(r"^Mistborn")).click()
                page.get_by_role("heading", name=re.compile(r"^Mistborn")).first.wait_for()
                assert page.get_by_role("heading", name="Now preparing").count() == 0

                page.get_by_role("button", name="Preparing your library").click()
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.emulate_media(reduced_motion="reduce")
                assert page.locator(".preparation-run-state").evaluate(
                    "element => getComputedStyle(element).animationName"
                ) == "none"
            browser.close()


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

                page.get_by_role("heading", name="Works", exact=True).wait_for(
                    timeout=8_000
                )
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
                page.get_by_role("button", name="Enable writes").wait_for(timeout=8_000)
                consent_semantic = [
                    "cm-page-heading",
                    "cm-activity-ledger",
                    "cm-write-consent is-off",
                    "cm-activity-status",
                    "activity-command-status",
                ]
                _activity_surface_order(
                    page,
                    width=1440,
                    semantic=consent_semantic,
                    visual=[
                        "cm-page-heading",
                        "cm-write-consent is-off",
                        "cm-activity-status",
                        "cm-activity-ledger",
                        "activity-command-status",
                    ],
                )
                _activity_surface_order(
                    page,
                    width=390,
                    semantic=consent_semantic,
                    visual=[
                        "cm-page-heading",
                        "cm-activity-ledger",
                        "cm-write-consent is-off",
                        "cm-activity-status",
                        "activity-command-status",
                    ],
                )
                page.set_viewport_size({"width": 1440, "height": 900})
                _fits_every_width(page, ".cm-write-consent")
                _fits_every_width(page, ".cm-activity-status")
                # The Activity row gained two flexible columns and a policy
                # table, and every one of them is sized by its own text: an
                # exact change, a category pill, a setting name and two policy
                # values. Measured against their own containers under the same
                # widened glyphs, because a six-track grid whose content is
                # text is exactly the shape that fits on one platform's fonts
                # and overruns on another's (#636).
                ledger = page.locator(".cm-activity-ledger")
                ledger.wait_for(timeout=8_000)
                page.locator(".cm-activity-event-group article").first.wait_for(
                    timeout=8_000
                )
                # Everything measured below is only worth measuring if it is
                # real. This surface used to be served one hand-written event,
                # so the widths this guard proved were the widths of a string
                # no event produces. Assert the ledger is the seeded projection
                # -- past a page boundary, with a real work and a real change
                # -- before believing any measurement taken from it.
                assert (
                    "of 1 events"
                    not in page.locator(".cm-activity-pagination").inner_text()
                ), "the ledger is serving a single stub row again"
                page.get_by_text("Paper Moons: Larkspur", exact=False).first.wait_for(
                    timeout=8_000
                )
                assert page.locator(".cm-activity-event-group article").count() > 1, (
                    "a seeded ledger should render many rows"
                )
                _fits_every_width(page, ".cm-activity-columns")
                _fits_every_width(page, ".cm-activity-tools")
                _fits_every_width(page, ".cm-activity-pagination")
                _rows_keep_their_promises(page)
                # Expanded, the widest record on the page is a Policy one, and
                # its three-column From/To table is measured open rather than
                # closed.
                page.get_by_role("button", name="Details").first.click()
                page.locator(".cm-activity-evidence").first.wait_for(timeout=8_000)
                _fits_every_width(page, ".cm-activity-evidence")
                _rows_keep_their_promises(page)
                _an_open_record_survives_a_viewport_change(page)
                _reflows_at_two_hundred_percent_zoom(page)
                _long_values_stay_inside_the_narrowest_card(page)
                page.route(
                    "**/api/v1/review-preparation",
                    lambda route: route.fulfill(
                        json={
                            "schema_version": "review-preparation-v2",
                            "active": True,
                            "actionable": False,
                            "paused": True,
                            "libraries": [],
                            "connections": [
                                {
                                    "connection": "primary",
                                    "code": "unavailable",
                                    "state": "paused",
                                    "affected": 2,
                                    "updated_at": 1,
                                }
                            ],
                        }
                    ),
                )
                page.reload()
                page.locator(".connection-notice").wait_for(timeout=8_000)
                semantic = [
                    "cm-page-heading",
                    "cm-activity-ledger",
                    "cm-activity-status",
                    "connection-notice is-paused",
                    "activity-command-status",
                ]
                _activity_surface_order(
                    page,
                    width=1440,
                    semantic=semantic,
                    visual=[
                        "cm-page-heading",
                        "connection-notice is-paused",
                        "cm-activity-status",
                        "cm-activity-ledger",
                        "activity-command-status",
                    ],
                )
                _activity_surface_order(
                    page,
                    width=390,
                    semantic=semantic,
                    visual=[
                        "cm-page-heading",
                        "cm-activity-ledger",
                        "cm-activity-status",
                        "connection-notice is-paused",
                        "activity-command-status",
                    ],
                )


def _an_open_record_survives_a_viewport_change(page: Page) -> None:
    """An expansion the owner opened stays open, and stays whole, when the
    layout changes underneath it.

    The row switches between a six-track grid and a compact card at 760px, and
    the expansion is a child of the row in both. Rotating a phone or splitting
    a window mid-read is the ordinary way to reach that switch with a record
    already open, and it is the case a layout built for the closed row is most
    likely to have missed.
    """
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("() => document.documentElement.offsetWidth")
    evidence = page.locator(".cm-activity-evidence").first
    assert evidence.is_visible(), "the record was not open before the change"
    opened = evidence.inner_text()

    for width in (760, 390, 1100, 1440):
        page.set_viewport_size({"width": width, "height": 844})
        page.evaluate("() => document.documentElement.offsetWidth")
        assert evidence.is_visible(), f"the open record closed itself at {width}px"
        assert evidence.inner_text() == opened, (
            f"the open record's facts changed at {width}px"
        )
        _fits_every_width(page, ".cm-activity-evidence")
        failures = page.evaluate(_ROW_PROMISES, width <= 760)
        assert failures == [], f"at {width}px with a record open: {failures}"


def _reflows_at_two_hundred_percent_zoom(page: Page) -> None:
    """The page reflows rather than scrolling sideways at 200% zoom.

    Doubling the zoom halves the usable CSS viewport, so it is applied here as
    the width an owner is actually left with rather than as a scale factor --
    the layout cannot tell the difference, and stating it as a width keeps the
    assertion measurable. WCAG 1.4.10 is the reason this is a promise and not a
    preference: content must reflow without a horizontal scrollbar.
    """
    for device_width, zoomed in ((1440, 720), (1280, 640), (780, 390)):
        page.set_viewport_size({"width": zoomed, "height": 844})
        page.evaluate("() => document.documentElement.offsetWidth")
        sideways = page.evaluate(
            "() => document.documentElement.scrollWidth -"
            " document.documentElement.clientWidth"
        )
        assert sideways <= 0, (
            f"{device_width}px at 200% zoom scrolls {sideways}px sideways"
        )
        failures = page.evaluate(_ROW_PROMISES, zoomed <= 760)
        assert failures == [], f"at {device_width}px zoomed to 200%: {failures}"


def _long_values_stay_inside_the_narrowest_card(page: Page) -> None:
    """The unbounded description and the longest label, on the smallest screen.

    A catalogue description has no length an interface may assume, and the
    seeded ledger carries a real one. At 390px the compact card promises to
    show the work and the change whole, so the description is the value most
    able to break that promise.
    """
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("() => document.documentElement.offsetWidth")
    search = page.get_by_role("searchbox")
    search.fill("description")
    page.wait_for_timeout(400)
    rows = page.locator(".cm-activity-event-group article")
    rows.first.wait_for(timeout=8_000)
    failures = page.evaluate(_ROW_PROMISES, True)
    assert failures == [], f"a long description breaks the compact card: {failures}"
    sideways = page.evaluate(
        "() => document.documentElement.scrollWidth -"
        " document.documentElement.clientWidth"
    )
    assert sideways <= 0, f"a long description scrolls the page {sideways}px sideways"
    search.fill("")
    page.wait_for_timeout(400)


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
