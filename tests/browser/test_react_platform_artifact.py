from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from playwright.sync_api import Page, Route, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from mechanical_librarian.interfaces.web.react_assets import (
    ReactAssetBundle,
    load_react_asset_bundle,
)
from tests.browser.fidelity_cohort import (
    BUILD_IDENTITY as _BUILD_IDENTITY,
)
from tests.browser.fidelity_cohort import (
    HOSTILE_TEXT as _HOSTILE,
)
from tests.browser.fidelity_cohort import (
    LONG_TITLE,
    UNBROKEN_TITLE_SEGMENT,
    rich_payload,
)

# The outcome the review controller renders when a decision request is lost
# before any response, which is the only outcome an aborted request can reach.
_AMBIGUOUS_NOTICE = (
    "The response was lost. The current case was reloaded before any retry."
)
_ACTIVE_ELEMENT = """() => {
  const node = document.activeElement
  if (!node) return 'none'
  return [node.tagName, node.id || '-', node.className || '-'].join(' ')
}"""
# A decision request is dispatched from the activation handler itself, so it is
# observable in milliseconds whenever a handler ran at all. This window is far
# longer than that and far shorter than the locator timeout it protects, so it
# separates "no handler ran" from "the outcome rendered differently" without
# ever failing an activation that merely had not been serviced yet.
_LOST_ACTIVATION_MILLISECONDS = 5_000


def _report(record: dict[str, object]) -> None:
    """Emit one diagnostic record on the only stream that survives the tier.

    `scripts/run_test_tier.py` redirects `sys.stdout` and `sys.stderr` to
    `os.devnull` around `pytest.main`, and `--capture=sys` replaces them again,
    so a diagnostic written to `sys.stderr` reaches nothing: the tier reports
    `report.longreprtext` alone and never the captured sections. Every record
    below existed for a failure no traceback could explain, so each one has to
    reach the same seam as the tier's own stall breadcrumb.
    """

    print(
        json.dumps(record, sort_keys=True, separators=(",", ":")),
        file=sys.__stderr__,
        flush=True,
    )


_CONTENT_OVERFLOW = """
(selector) => {
  const root = document.querySelector(selector);
  if (root === null) return null;
  let worst = 0;
  for (const node of [root, ...root.querySelectorAll('*')]) {
    worst = Math.max(worst, node.scrollWidth - node.clientWidth);
    const style = getComputedStyle(node);
    const box = node.getBoundingClientRect();
    const right = box.right - parseFloat(style.borderRightWidth || 0) -
      parseFloat(style.paddingRight || 0);
    for (const child of node.children)
      worst = Math.max(worst, Math.round(child.getBoundingClientRect().right - right));
  }
  return worst;
}
"""


def _fits_its_container(page: Page, selector: str, width: int) -> None:
    """Assert a section's content stays inside the box that holds it.

    Measured against the container rather than the document -- both what
    overflows a box and what sticks out past one -- because a document-level
    assertion only notices an overrun once it is already wider than the
    viewport. By then the layout has been wrong for many pixels; this is wrong
    at one. Whether it stays inside when the same text renders wider is proved
    against a real server in `test_live_updates_browser` (#641, #639).
    """
    overflow = page.evaluate(_CONTENT_OVERFLOW, selector)
    assert overflow is not None, f"{selector} is absent at {width}px"
    assert overflow <= 0, (
        f"{selector} overflows its container by {overflow}px at {width}px"
    )


def test_exact_built_shell_supports_every_direct_route_and_client_navigation() -> None:
    # The stage record is emitted from inside the browser context by
    # `report_stage` below, which is the only place the page is still
    # answerable. Anything that fails ahead of that carries its own message.
    _exercise_exact_built_shell_supports_every_direct_route_and_client_navigation(
        ["test-setup"]
    )


def _exercise_exact_built_shell_supports_every_direct_route_and_client_navigation(
    stage: list[str],
) -> None:
    root_value = os.environ.get("ML_REACT_ASSET_DIR")
    assert root_value is not None, "ML_REACT_ASSET_DIR is required"
    bundle = load_react_asset_bundle(Path(root_value or ""))
    assert isinstance(bundle, ReactAssetBundle)
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
            if path.endswith("/return-to-automatic") and route.request.method == "POST":
                return_automatic_requests += 1
                route.fulfill(status=204, body="")
                return
            if path == "/api/v1/updates":
                # The artifact cohort measures the shell against stubbed reads;
                # a stream would never complete and there is nothing here to
                # announce. It is answered with a stream that ends immediately,
                # which leaves the surfaces on their polling fallback -- the
                # state every assertion in this module was written against.
                route.fulfill(
                    status=200,
                    content_type="text/event-stream",
                    body="retry: 3000\n\n",
                )
                return
            if path == "/api/v1/session/logout":
                route.fulfill(
                    status=204, headers={"x-ml-build-identity": "c" * 64}, body=""
                )
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
            if (
                fail_activity_once
                and "ActivityPage-" in relative
                and activity_failures == 0
            ):
                activity_failures += 1
                route.fulfill(status=503, body="")
                return
            if fail_policy_persistently and "PolicyPage-" in relative:
                route.fulfill(status=503, body="")
                return
            content_type = (
                "text/css" if relative.endswith(".css") else "text/javascript"
            )
            route.fulfill(
                status=200, content_type=content_type, body=bundle.read(relative)
            )
            return
        if route.request.resource_type == "document":
            documents += 1
            shell = (
                bundle.read(bundle.entry)
                .decode("utf-8")
                .replace(
                    "<body>",
                    f'<body data-build-identity="{_BUILD_IDENTITY}" data-csrf-token="{"b" * 32}">',
                )
            )
            route.fulfill(
                status=200,
                content_type="text/html",
                headers={
                    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                },
                body=shell,
            )
            return
        route.fulfill(status=404, body="")

    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        # Closed on the failing path too, so a failure tears the browser down
        # the same way a pass does and its teardown output stays comparable.
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        capture_directory = os.environ.get("ML_VISUAL_CAPTURE_DIR")

        def capture_visual_proof(name: str) -> None:
            if capture_directory is None:
                return
            output = Path(capture_directory)
            output.mkdir(parents=True, exist_ok=True)
            viewport = page.viewport_size
            try:
                for scheme in ("light", "dark"):
                    control = "Use dark mode" if scheme == "dark" else "Use light mode"
                    if page.get_by_role("button", name=control).count():
                        page.get_by_role("button", name=control).click()
                    page.emulate_media(reduced_motion="reduce")
                    for width, height, size in (
                        (1440, 900, "desktop"),
                        (390, 844, "compact"),
                    ):
                        page.set_viewport_size({"width": width, "height": height})
                        page.screenshot(
                            path=str(output / f"{name}-{size}-{scheme}.png"),
                            full_page=True,
                        )
            finally:
                page.emulate_media(color_scheme="light", reduced_motion="no-preference")
                if page.get_by_role("button", name="Use light mode").count():
                    page.get_by_role("button", name="Use light mode").click()
                if viewport is not None:
                    page.set_viewport_size(viewport)

        # A control that runs past the Works rail is invisible without moving
        # the whole document, so `document.scrollWidth` never reports it. Every
        # control is measured against its own container's content box instead.
        def overrunning_rail_controls() -> list[list[object]]:
            return cast(
                "list[list[object]]",
                page.evaluate(
                    """() => {
                    const rail = document.querySelector('.library-works')
                    // A silent [] for a missing rail would make this assertion
                    // pass without measuring anything.
                    if (!rail) return [['.library-works', 'is missing', 0, 0, 0, 0]]
                    const box = rail.getBoundingClientRect()
                    const style = getComputedStyle(rail)
                    const left = box.left + parseFloat(style.paddingLeft)
                    const right = box.right - parseFloat(style.paddingRight)
                    return [...rail.querySelectorAll(
                        `.lenses a, .library-search input, .library-search button,
                         .library-pager button, .library-work-title,
                         .library-work-author, .library-work-origin,
                         .library-works-heading > *`)]
                        .map(node => [node, node.getBoundingClientRect()])
                        .filter(([, r]) => r.width > 0 &&
                            (r.left < left - 0.5 || r.right > right + 0.5))
                        .map(([node, r]) => [node.className || node.tagName,
                            (node.textContent || '').trim().slice(0, 28),
                            Math.round(r.left), Math.round(r.right),
                            Math.round(left), Math.round(right)])
                    }"""
                ),
            )

        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: (
                errors.append(message.text) if message.type == "error" else None
            ),
        )
        page.on(
            "requestfailed",
            lambda request: failed_requests.append(
                (request.method, request.url, request.failure)
            ),
        )

        def observed_state() -> dict[str, object]:
            # Read defensively: this only ever runs while a failure is already
            # being reported, and a closed target must not replace the real
            # error with a second one raised from the diagnostic itself.
            state: dict[str, object] = {}
            for name, read in (
                ("live_regions", lambda: page.locator("p[role]").all_inner_texts()),
                ("active_element", lambda: page.evaluate(_ACTIVE_ELEMENT)),
                ("url", lambda: page.url),
            ):
                try:
                    state[name] = read()
                except Exception as unreadable:  # noqa: BLE001 - diagnostic only
                    state[name] = f"unreadable: {type(unreadable).__name__}"
            return state

        def report_stage(
            failure: type[BaseException] | None,
            _value: BaseException | None,
            _traceback: object,
        ) -> None:
            if failure is not None:
                _report(
                    {
                        "schema": "protected-browser-failure-stage-v1",
                        "stage": stage[0],
                        **observed_state(),
                    }
                )

        # Unwinds before the Playwright context it was pushed inside, so the
        # page is still answerable when the record is taken. Reporting from
        # outside that context reaches a stopped driver and can only say the
        # interface was unreadable.
        stack.push(report_stage)

        def decide(site: str, expected: int, activate: Callable[[], None]) -> None:
            """Activate one decision control and await its ambiguous-outcome notice.

            The notice can only appear once the decision request has actually
            been dispatched, so a lost activation and a wrong rendered outcome
            are indistinguishable from a bare wait on the notice: both report
            only that the text never became visible. Establish the request
            first, so the failure names which of the two happened and carries
            the state that explains it. A lost activation is the defect this
            case exists to catch, so it is reported and never recovered from.
            """
            stage[0] = f"ambiguous-decision:{site}"
            started = time.monotonic()
            activate()
            if ambiguous_decisions < expected:
                try:
                    page.wait_for_event(
                        "requestfailed",
                        predicate=lambda request: request.method == "POST",
                        timeout=_LOST_ACTIVATION_MILLISECONDS,
                    )
                except PlaywrightTimeoutError:
                    pass
            if ambiguous_decisions < expected:
                # The activation reached no handler, so nothing can render the
                # notice and waiting for it can only spend the locator timeout.
                # Fail here instead, carrying the state that says so: repeating
                # the activation would recover the exact defect this covers.
                state = observed_state()
                _report(
                    {
                        "schema": "protected-browser-lost-activation-v1",
                        "site": site,
                        "observed_decision_requests": ambiguous_decisions,
                        "expected_decision_requests": expected,
                        **state,
                    }
                )
                raise AssertionError(
                    f"The {site} activation reached no handler: no decision "
                    f"request was dispatched within "
                    f"{_LOST_ACTIVATION_MILLISECONDS} ms, so the ambiguous "
                    f"outcome it should report can never be rendered and "
                    f"awaiting that notice could only spend the locator "
                    f"timeout. Observed {ambiguous_decisions} decision "
                    f"requests, expected {expected}; focus was on "
                    f"{state['active_element']!r}, not the control the "
                    f"activation targeted."
                )
            dispatched = time.monotonic()
            try:
                page.get_by_text(_AMBIGUOUS_NOTICE, exact=True).wait_for()
            except PlaywrightTimeoutError:
                _report(
                    {
                        "schema": "protected-browser-ambiguous-decision-v1",
                        "site": site,
                        "observed_decision_requests": ambiguous_decisions,
                        "expected_decision_requests": expected,
                        "failed_decision_requests": [
                            url
                            for method, url, _ in failed_requests
                            if method == "POST"
                        ],
                        **observed_state(),
                    }
                )
                raise
            assert ambiguous_decisions == expected
            # Recorded on the passing run too. A timeout says only that the
            # notice never arrived; what separates a step that is drifting
            # towards its ceiling from one that was never going to arrive is
            # how long the same step normally takes, and that is knowable only
            # from the runs that succeed.
            settled = time.monotonic()
            _report(
                {
                    "schema": "protected-browser-decision-timing-v1",
                    "site": site,
                    "dispatch_milliseconds": round((dispatched - started) * 1000),
                    "notice_milliseconds": round((settled - dispatched) * 1000),
                }
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
            ("/activity", "Activity and recovery"),
        )
        for path, heading in routes:
            stage[0] = f"direct-route:{path}"
            fail_activity_once = path == "/activity"
            before_entry = documents
            response = page.goto(f"https://react-platform.invalid{path}")
            assert response is not None
            assert response.status == 200
            page.get_by_role("heading", name=heading).wait_for()
            if path in {
                "/library?lens=all",
                "/review/case-1?field=title&subview=review",
                "/activity",
            }:
                page.get_by_text(
                    "Library connection needs attention", exact=True
                ).first.wait_for()
                notice = page.get_by_label("Shared library connection status")
                check = notice.get_by_role("button", name="Check connection again")
                expect(check).to_be_visible()
                assert "primary" not in notice.inner_text()
                assert "http" not in notice.inner_text()
                for width, expected_columns in (
                    (1440, 3),
                    (1044, 2),
                    (800, 1),
                    (390, 1),
                ):
                    page.set_viewport_size({"width": width, "height": 844})
                    assert expected_columns == len(
                        notice.evaluate(
                            "node => getComputedStyle(node).gridTemplateColumns.split(' ')"
                        )
                    )
                    assert (
                        page.evaluate("() => document.documentElement.scrollWidth")
                        <= width
                    )
                    assert check.evaluate(
                        "node => { const box = node.getBoundingClientRect(); "
                        "const top = document.elementFromPoint(box.x + box.width / 2, "
                        "box.y + box.height / 2); return top === node || node.contains(top); }"
                    )
                    # The masthead now carries a status message whose width is
                    # its text, and this cohort's stream ends immediately, so
                    # the degraded notice is present on every route here.
                    # The masthead now carries a status message whose width
                    # is its text, and this cohort's stream ends at once, so
                    # the degraded notice is present on every route here. The
                    # row it sits in is measured with the text enlarged; the
                    # masthead around it is measured as installed.
                    # The masthead now carries a status message whose width
                    # is its own text, and this cohort's stream ends at once,
                    # so the degraded notice is present on every route here.
                    _fits_its_container(page, ".shell-actions", width)
                    _fits_its_container(page, ".masthead", width)
            if path == "/attention":
                page.get_by_text(
                    "This field needs your review.", exact=True
                ).first.wait_for()
                assert (
                    0
                    == page.get_by_text(
                        "Conflicting retained evidence", exact=True
                    ).count()
                )
            if path.startswith("/policy"):
                section = path.rsplit("=", 1)[-1]
                expect(page.locator(f"#policy-{section}")).to_be_focused()
                expect(
                    page.get_by_role("navigation", name="Policy chapters").locator(
                        "button[aria-current='page']"
                    )
                ).to_have_count(1)
                assert page.url.endswith(path), f"{path} restored as {page.url}"
            assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
            assert page.locator("img, script:not([src])").count() == 0
            assert page.evaluate("() => window.__metadataExecuted") is None
            if path == "/activity":
                stage[0] = "activity:compact-initial-geometry"
                expect(page.locator(".cm-page-heading")).to_be_focused()
                recovery = page.get_by_role("region", name="Provider recovery")
                expected_recovery = {
                    "Waiting for a safe retry": "2 affected scopes",
                    "Checking provider health": "1 affected scope",
                    "Retrying affected work": "3 affected works",
                    "Recovered": "1 affected scope",
                    "Needs a configuration change": "2 affected scopes",
                    "No further retry scheduled": "1 affected scope",
                }
                for label, units in expected_recovery.items():
                    row = recovery.get_by_text(label, exact=True).locator("..")
                    expect(row).to_contain_text(units)
                recovery_text = recovery.inner_text().lower()
                for forbidden in (
                    "provider_",
                    "query=",
                    "authorization:",
                    "bearer ",
                    "payload",
                    "/private/",
                    "x-api-key",
                ):
                    assert forbidden not in recovery_text
                assert (
                    recovery.get_by_role("button", name="Retry", exact=False).count()
                    == 0
                )
                activity_ledger = page.locator(".cm-activity-ledger")
                for work, label in (
                    ("Synthetic completed cover advice", "Cover advice completed"),
                    ("Synthetic unavailable cover advice", "Cover advice unavailable"),
                    ("Synthetic failed cover advice", "Cover advice failed safely"),
                    ("Synthetic stale cover advice", "Stale"),
                    ("Synthetic superseded cover advice", "Superseded"),
                ):
                    row = activity_ledger.get_by_text(work, exact=True).locator(
                        "xpath=ancestor::article"
                    )
                    expect(row).to_have_count(1)
                    expect(row.get_by_text(label, exact=True)).to_have_count(1)
                capture_visual_proof("activity-labels")
                assert page.locator(".cm-activity-ledger").evaluate(
                    "node => node.previousElementSibling?.classList.contains('cm-activity-status')"
                )
                long_activity_title = page.locator(".cm-activity-work > strong").filter(
                    has_text=LONG_TITLE
                )
                assert long_activity_title.get_attribute("title") == LONG_TITLE
                assert (
                    long_activity_title.evaluate(
                        "node => getComputedStyle(node).webkitLineClamp"
                    )
                    == "2"
                )
                title_box = long_activity_title.bounding_box()
                row_box = long_activity_title.locator(
                    "xpath=ancestor::article"
                ).bounding_box()
                assert title_box is not None
                assert row_box is not None
                compact_geometry = {
                    "title_height": title_box["height"],
                    "row_height": row_box["height"],
                }
                # The Policy record is the only one carrying an exact From/To,
                # and its three-column table is the widest thing the compact
                # card ever opens. It fits the card rather than sliding out of
                # it, and every other category keeps the compact fact density
                # beside it rather than inheriting this bulk (#636).
                policy_row = (
                    activity_ledger.locator("article")
                    .filter(has_text="3 changes activated")
                    .first
                )
                policy_row.get_by_role("button", name="Details").click()
                policy_changes = policy_row.locator(".cm-activity-policy-changes")
                expect(policy_changes).to_have_count(1)
                expect(policy_changes.locator("li")).to_have_count(3)
                expect(policy_changes.get_by_text("Ask me").first).to_be_visible()
                expect(policy_changes.get_by_text("Automatic").first).to_be_visible()
                assert (
                    policy_changes.evaluate(
                        "node => node.scrollWidth - node.clientWidth"
                    )
                    <= 0
                )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                other_row = (
                    activity_ledger.get_by_text("The Glass Orchard", exact=True)
                    .locator("xpath=ancestor::article")
                    .first
                )
                other_row.get_by_role("button", name="Details").click()
                expect(other_row.locator(".cm-activity-policy-changes")).to_have_count(
                    0
                )
                expect(other_row.locator(".cm-activity-evidence dl div")).to_have_count(
                    3
                )
                # One record is open at a time, so opening the second closed
                # the first: only the open one is closed here.
                expect(policy_row.locator(".cm-activity-evidence")).to_have_count(0)
                other_row.get_by_role("button", name="Hide details").click()

                compact_row = long_activity_title.locator("xpath=ancestor::article")
                assert (
                    compact_row.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns"
                    ).split(" ", 1)[0]
                    == "44px"
                )
                assert (
                    compact_row.locator("b").evaluate(
                        "node => getComputedStyle(node).gridRow"
                    )
                    == "3"
                )
                # The accepted compact card is three rows -- time/type/action,
                # work/field, then change/result -- so the disclosure sits
                # beside the type on the first row rather than under the card,
                # and the change has the row the action used to occupy (#636).
                assert (
                    compact_row.get_by_role(
                        "button", name="Details", exact=True
                    ).evaluate("node => getComputedStyle(node).gridRow")
                    == "1"
                )
                assert (
                    compact_row.locator(".cm-activity-change").evaluate(
                        "node => getComputedStyle(node).gridRow"
                    )
                    == "3"
                )
                assert title_box["height"] <= 36, compact_geometry
                assert row_box["height"] <= 182, compact_geometry
                page.set_viewport_size({"width": 1440, "height": 900})
                inspect = page.get_by_role("button", name="Details", exact=True).first
                stage[0] = "activity:desktop-media-state"
                page.wait_for_function(
                    """() => {
                            const inspect = document.querySelector('.cm-activity-events article > button');
                            const title = document.querySelector('.cm-activity-work > strong');
                            return window.matchMedia('(min-width: 761px)').matches
                                && inspect instanceof HTMLElement
                                && title instanceof HTMLElement
                                && getComputedStyle(inspect).whiteSpace === 'nowrap'
                                && getComputedStyle(title).whiteSpace === 'nowrap';
                        }"""
                )
                stage[0] = "activity:desktop-geometry"
                inspect_box = inspect.bounding_box()
                row_box = inspect.locator("xpath=ancestor::article").bounding_box()
                assert inspect_box is not None
                assert row_box is not None
                assert (
                    inspect.evaluate("node => getComputedStyle(node).whiteSpace")
                    == "nowrap"
                )
                assert row_box["height"] <= 72
                assert (
                    inspect.evaluate("node => getComputedStyle(node).minHeight")
                    == "40px"
                )
                assert inspect_box["height"] <= 40.5
                page.set_viewport_size({"width": 390, "height": 844})
                stage[0] = "activity:compact-media-state"
                page.wait_for_function(
                    """() => {
                            const title = document.querySelector('.cm-activity-work > strong');
                            return window.matchMedia('(max-width: 760px)').matches
                                && title instanceof HTMLElement
                                && getComputedStyle(title).webkitLineClamp === '2';
                        }"""
                )
                stage[0] = "activity:authority-and-recovery"
                theme_backgrounds: dict[str, str] = {}
                for scheme in ("light", "dark"):
                    page.emulate_media(reduced_motion="reduce")
                    wanted_control = (
                        "Use dark mode" if scheme == "dark" else "Use light mode"
                    )
                    if page.get_by_role("button", name=wanted_control).count():
                        page.get_by_role("button", name=wanted_control).click()
                    for width in (1440, 1024, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        expect(recovery).to_be_visible()
                        assert (
                            page.evaluate("() => document.documentElement.scrollWidth")
                            <= width
                        )
                        for control in page.locator(
                            ".cm-activity-filters button, .cm-activity-pagination button"
                        ).all():
                            assert control.bounding_box()["height"] >= 40
                        for control in page.locator(
                            ".masthead nav a, .shell-actions button"
                        ).all():
                            assert control.bounding_box()["height"] >= 44
                    theme_backgrounds[scheme] = page.locator("body").evaluate(
                        "node => getComputedStyle(node).backgroundColor"
                    )
                assert theme_backgrounds["light"] != theme_backgrounds["dark"]
                assert page.evaluate(
                    "() => matchMedia('(prefers-reduced-motion: reduce)').matches"
                )
                assert (
                    recovery.evaluate("node => getComputedStyle(node).animationName")
                    == "none"
                )
                page.get_by_role("button", name="Use light mode").click()
                page.emulate_media(reduced_motion="no-preference")
                page.set_viewport_size({"width": 390, "height": 844})
                page.get_by_text("Authority and recovery", exact=True).click()
                page.get_by_role("button", name="Review write permissions").click()
                page.get_by_role("heading", name="Enable guarded writes").wait_for()
                page.locator(".activity-consent-library").filter(
                    has_text="Household ebooks"
                ).get_by_text("5 prepared · Some destinations are ready").wait_for()
                page.get_by_role("heading", name="Activity ledger").wait_for()
                page.get_by_label("Search activity").wait_for()
                for width, height in ((1440, 900), (390, 844)):
                    stage[0] = f"activity:escape-dropdown-focus:{width}"
                    page.set_viewport_size({"width": width, "height": height})
                    page_number = page.get_by_label("Activity page number")
                    page_url = page.url
                    page_number.focus()
                    page_number.fill("2")
                    page.keyboard.press("Escape")
                    assert "1" == page_number.input_value()
                    assert page_url == page.url
                    expect(page_number).to_be_focused()
                    date_range = page.get_by_role(
                        "combobox", name="Activity date range"
                    )
                    date_range.click()
                    page.get_by_role("option", name="Last 90 days").click()
                    expect(date_range).to_be_focused()
                    page_number = page.get_by_label("Activity page number")
                    page_url = page.url
                    page_number.focus()
                    page_number.fill("2")
                    page.keyboard.press("Escape")
                    assert "1" == page_number.input_value()
                    assert page_url == page.url
                    expect(page_number).to_be_focused()
                    page_size = page.get_by_role(
                        "combobox", name="Rows per activity page"
                    )
                    page_size.click()
                    page.get_by_role("option", name="50 per page").click()
                    expect(page_size).to_be_focused()
                page.get_by_role("button", name="Owner choices").click()
                page.get_by_role("button", name="All").click()
                inspect_control = page.get_by_role(
                    "button", name="Details", exact=True
                ).first
                inspect_control.wait_for()
                expect(inspect_control).to_be_visible()
                inspect_control.focus()
                page.keyboard.press("Enter")
                page.get_by_text("Previous value").first.wait_for()
                assert (
                    page.locator(".cm-activity-evidence").evaluate(
                        "node => getComputedStyle(node).gridRow"
                    )
                    == "4"
                )
                page.locator(".cm-activity-evidence").get_by_text(
                    "Chosen by", exact=True
                ).wait_for()
                assert (
                    "future_provider_origin"
                    not in page.locator(".cm-activity-ledger").inner_text()
                )
                page.locator(".activity-consent-library").filter(
                    has_text="Spoken stories"
                ).get_by_role("checkbox").check()
                stage[0] = "activity:consent-refresh"
                page.get_by_role("button", name="Enable selected libraries").click()
                page.get_by_text(
                    "Activity state refreshed from the authoritative server."
                ).wait_for()
            stage[0] = f"direct-route:{path}:document-count"
            assert documents == (before_entry + (2 if path == "/activity" else 1))

        for case_id, status, retry in (
            ("case-1", "Advisory visual review complete", False),
            ("case-2", "Manual comparison available", True),
            ("case-3", "Manual comparison available", True),
        ):
            stage[0] = f"cover-detail:{case_id}"
            page.goto(
                f"https://react-platform.invalid/review/{case_id}"
                "?field=cover&subview=review"
            )
            page.get_by_text(status, exact=True).wait_for()
            expect(
                page.get_by_role("button", name="Keep current cover")
            ).to_have_attribute("aria-pressed", "false")
            expect(
                page.get_by_role("button", name="Choose this cover")
            ).to_have_attribute("aria-pressed", "false")
            expect(page.get_by_role("button", name="Take no action")).to_have_attribute(
                "aria-pressed", "false"
            )
            assert (1 if retry else 0) == page.get_by_role(
                "button", name="Retry advisory review"
            ).count()
            if case_id in {"case-1", "case-2"}:
                capture_visual_proof(
                    "detail-completed" if case_id == "case-1" else "detail-failed"
                )

        for case_id, label, retry in (
            ("case-1", "Advisory visual review complete", False),
            ("case-2", "Manual comparison available", True),
            ("case-3", "Manual comparison available", True),
        ):
            stage[0] = f"cover-library:{case_id}"
            page.goto(
                "https://react-platform.invalid/library?lens=all"
                f"&preview={case_id}&field=cover"
            )
            comparison = page.get_by_role("region", name="Choose a cover")
            comparison.get_by_text(label, exact=True).wait_for()
            assert (
                "advice only" if not retry else "comparison remains manual"
            ) in comparison.inner_text()
            for choice in comparison.locator("button[aria-pressed]").all():
                expect(choice).to_be_enabled()
                expect(choice).to_have_attribute("aria-pressed", "false")
            for evidence in comparison.get_by_role(
                "button", name=re.compile(r"^Evidence for .+ cover$")
            ).all():
                expect(evidence).to_be_enabled()
                expect(evidence).to_have_attribute("aria-expanded", "false")
            assert (1 if retry else 0) == comparison.get_by_role(
                "button", name="Retry advisory review"
            ).count()
            if case_id in {"case-1", "case-2"}:
                capture_visual_proof(
                    "library-completed" if case_id == "case-1" else "library-failed"
                )
            if case_id == "case-1":
                current_evidence = comparison.get_by_role(
                    "button", name="Evidence for Current cover"
                )
                current_evidence.click()
                expect(current_evidence).to_have_attribute("aria-expanded", "true")
                comparison.get_by_text("Local library record", exact=True).wait_for()
                expect(
                    comparison.locator(".library-cover-card.is-current")
                ).to_have_attribute("aria-pressed", "false")
                alternative_evidence = comparison.get_by_role(
                    "button", name="Evidence for Alternative 1 cover"
                )
                alternative_evidence.focus()
                page.keyboard.press("Enter")
                expect(alternative_evidence).to_be_focused()
                expect(alternative_evidence).to_have_attribute("aria-expanded", "true")
                comparison.get_by_text("Open Library", exact=True).wait_for()
                comparison.get_by_text("External corroboration", exact=True).wait_for()
                expect(
                    comparison.locator(".library-cover-card:not(.is-current)")
                ).to_have_attribute("aria-pressed", "false")
            if case_id == "case-3":
                unavailable_evidence = comparison.get_by_role(
                    "button", name="Evidence for Current cover"
                )
                unavailable_evidence.focus()
                page.keyboard.press("Enter")
                expect(unavailable_evidence).to_have_attribute("aria-expanded", "true")
                comparison.get_by_text("Local presence", exact=True).wait_for()
                comparison.get_by_text("Preview unavailable", exact=True).wait_for()

        stage[0] = "cover-library:cached-work-reset"
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=cover"
        )
        page.locator(".library-works").wait_for()
        page.locator(".workspace-detail").wait_for()
        page.get_by_role("button", name="Evidence for Current cover").click()
        page.get_by_text("Local library record", exact=True).wait_for()
        work_b = page.locator(".library-work-row").filter(has_text=LONG_TITLE)
        assert 1 == work_b.count()
        work_b.click()
        page.locator(".field-filmstrip").wait_for()
        page.locator(".field-filmstrip").get_by_role(
            "button", name=re.compile(r"Cover")
        ).click()
        page.get_by_role("region", name="Choose a cover").wait_for()
        assert 0 == page.locator(".choice-evidence").count()
        work_a = page.locator(".library-work-row").filter(has_text=_HOSTILE)
        assert 1 == work_a.count()
        work_a.click()
        page.locator(".field-filmstrip").wait_for()
        page.locator(".field-filmstrip").get_by_role(
            "button", name=re.compile(r"Cover")
        ).click()
        page.get_by_role("region", name="Choose a cover").wait_for()
        assert 0 == page.locator(".choice-evidence").count()

        missing_case_error_start = len(errors)
        for _ in range(2):
            response = page.goto(
                "https://react-platform.invalid/review/missing-case"
                "?field=title&subview=review"
            )
            assert response is not None
            assert response.status == 200
            page.get_by_role(
                "heading", name="This workspace could not be loaded."
            ).wait_for()
            page.get_by_role("button", name="Try again").wait_for()
        missing_case_errors = errors[missing_case_error_start:]
        assert len(missing_case_errors) == 4
        assert (
            sum("status of 404 (Not Found)" in error for error in missing_case_errors)
            == 2
        )
        assert (
            sum(
                "Review API request failed (404: not_found)" in error
                for error in missing_case_errors
            )
            == 2
        )
        del errors[missing_case_error_start:]

        stage[0] = "library:desktop-workspace"
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto("https://react-platform.invalid/library?lens=all")
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=all&preview=case-1"
        )
        assert (
            page.locator(".masthead nav").get_by_text("Attention", exact=True).count()
            == 0
        )
        expect(
            page.locator(".workspace-detail .direct-choice-grid .value-choice")
        ).to_have_count(4)
        expect(page.locator(".workspace-detail .choice-evidence-toggle")).to_have_count(
            4
        )
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_visible()
        # #598 keeps automatic, unsupported, and recorded siblings in the
        # primary strip; only server-classified attention advances normally.
        expect(page.locator(".field-filmstrip button")).to_have_count(5)
        assert "Title" in page.locator(".field-filmstrip").inner_text()
        assert "Cover" in page.locator(".field-filmstrip").inner_text()
        assert not (
            page.locator(".automatic-resolution-details").evaluate("node => node.open")
        )
        assert (
            page.locator(".direct-choice").bounding_box()["y"]
            < page.locator(".automatic-resolution-details").bounding_box()["y"]
        )
        page.locator(".automatic-resolution-details summary").click()
        automatic_context = page.locator(".automatic-resolution-details").inner_text()
        assert "Subtitle" in automatic_context
        assert "Series" in automatic_context
        assert "Resolved automatically" in automatic_context
        assert "No eligible change" in automatic_context
        current_evidence = page.get_by_role(
            "button", name="Evidence for Current value: Current <script> title"
        )
        current_evidence.click()
        expect(current_evidence).to_be_focused()
        expect(current_evidence).to_have_attribute("aria-expanded", "true")
        expect(page.locator("#choice-evidence-current")).to_be_visible()
        workspace_evidence = page.locator("#choice-evidence-current").inner_text()
        assert "Local library record" in workspace_evidence
        assert "Local presence" in workspace_evidence
        assert (
            "No recommendation retained"
            in page.locator(".metadata-choice.is-current").inner_text()
        )
        proposed_evidence = page.get_by_role(
            "button", name="Evidence for Alternative 1: The Clockmaker's Cartography"
        )
        proposed_evidence.focus()
        page.keyboard.press("Enter")
        expect(proposed_evidence).to_be_focused()
        expect(proposed_evidence).to_have_attribute("aria-expanded", "true")
        assert "Open Library" in page.locator(".choice-evidence").nth(1).inner_text()
        assert page.locator(".source-ledger").count() == 0
        assert current_evidence.bounding_box()["height"] >= 40
        page.goto("https://react-platform.invalid/library?lens=all&preview=case-2")
        expect(page.locator(".choice-evidence")).to_have_count(0)
        page.goto("https://react-platform.invalid/library?lens=all&preview=case-1")
        assert (
            "Guarded from external update"
            in page.locator(".field-apply-context").inner_text()
        )
        # The workspace never grows a field stepper. The Library pager is a
        # separate, list-scoped control and is asserted where the list is.
        detail_workspace = page.locator(".workspace-detail")
        assert detail_workspace.get_by_role("button", name="Previous").count() == 0
        assert detail_workspace.get_by_role("button", name="Next").count() == 0
        page.locator(".attention-toggle").click()
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=all&preview=case-1&attention=open"
        )
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(
            page.locator(".attention-rail .attention-queue-copy b").first
        ).to_have_text("Title · This field needs your review.")
        assert (
            0
            == page.locator(".attention-rail")
            .get_by_text("Conflicting retained evidence", exact=True)
            .count()
        )
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_visible()
        assert (
            page.locator(".library-workspace").evaluate(
                "node => getComputedStyle(node).gridTemplateColumns.split(' ').length"
            )
            == 3
        )
        page.locator(".attention-rail header button").click()
        assert page.locator(".library-work-row").count() == 6
        page.locator(".library-work-row").first.get_by_text(
            "Mara Venn", exact=False
        ).wait_for()
        page.get_by_text("Household ebooks · Epub", exact=True).first.wait_for()
        assert (
            "synthetic-item-1"
            not in page.locator(".library-work-row").first.inner_text()
        )
        no_case_row = page.locator(".library-work-row").filter(
            has_text="Catalogue-only work"
        )
        assert no_case_row.get_by_role("link").count() == 0
        # A work with no review case is still openable: the row is one target
        # and the detail pane names the condition and every source consulted.
        no_case_row.click()
        work_detail = page.locator(".workspace-detail")
        work_detail.get_by_role("heading", name="Catalogue-only work").wait_for()
        assert "Authentication at Provider" in work_detail.inner_text()
        assert "Retry review preparation" in work_detail.inner_text()
        sources = work_detail.locator(".work-sources")
        assert sources.count() == 1
        assert sources.evaluate("node => node.open") is False
        sources.get_by_text("Sources", exact=True).click()
        assert sources.evaluate("node => node.open") is True
        assert "hardcover-main" in sources.inner_text()
        assert "Work lookup · 1 bounded request" in sources.inner_text()
        capture_visual_proof("library-work-summary")
        work_detail.get_by_role("button", name="Back to works").click()
        page.get_by_role("link", name="Needs attention", exact=True).click()
        expect(page.locator(".library-work-row")).to_have_count(5)
        page.get_by_role("link", name="No attention needed", exact=True).click()
        expect(page.locator(".library-work-row")).to_have_count(6)
        page.get_by_role("link", name="All", exact=True).click()
        expect(page.locator(".library-work-row")).to_have_count(6)
        page.get_by_role("button", name="Next \u2192").click()
        page.get_by_text("Unicode shelves:", exact=False).wait_for()
        assert page.locator(".library-work-row").count() == 6
        page.get_by_text("Page 2", exact=False).wait_for()
        # A published work with no review case still opens, and says so.
        page.locator(".library-work-row").filter(
            has_text="Pre-admission observation"
        ).click()
        work_detail.get_by_role("heading", name="Pre-admission observation").wait_for()
        assert (
            "No review decision is recorded for this work." in work_detail.inner_text()
        )
        assert "Ember Lane" in work_detail.locator(".library-work-values").inner_text()
        assert (
            "2 consulted for this work"
            in work_detail.locator(".work-sources").inner_text()
        )
        work_detail.get_by_role("button", name="Back to works").click()
        # Browsing is reversible: the same pager walks the bounded window back.
        page.get_by_role("button", name="\u2190 Previous").click()
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        page.get_by_text("Page 1", exact=False).wait_for()
        capture_visual_proof("library-pager")
        assert page.get_by_role("button", name="\u2190 Previous").is_disabled()
        page.get_by_role("button", name="Next \u2192").click()
        page.get_by_text("Unicode shelves:", exact=False).wait_for()
        page.get_by_role("button", name="\u2190 Previous").click()
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        assert page.get_by_text("Library could not be loaded", exact=False).count() == 0
        search_box = page.get_by_label(
            "Search title, author, identifier, library, or format"
        )
        # Searching narrows as the owner types and returns to the first page.
        search_box.fill("Spoken stories")
        expect(page.locator(".library-work-row")).to_have_count(3)
        page.get_by_text("Page 1", exact=False).wait_for()
        assert search_box.input_value() == "Spoken stories"
        search_box.fill("no synthetic work has this phrase")
        page.get_by_text(
            "No exact-current works match this search and lens."
        ).wait_for()
        page.get_by_role("button", name="Clear search").click()
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        assert search_box.input_value() == ""
        stage[0] = "library:touch-targets"
        page.goto("https://react-platform.invalid/library?lens=all")
        for control in page.locator(".masthead nav a, .shell-actions button").all():
            assert control.bounding_box()["height"] >= 44
        for control in page.locator(
            ".lenses a, .library-work-row, .library-pager button"
        ).all():
            assert control.bounding_box()["height"] >= 44
        for lens in page.locator(".lenses a").all():
            assert lens.bounding_box()["width"] >= 44
        page.locator(".library-work-row").first.wait_for()
        assert overrunning_rail_controls() == []
        long_title = (
            page.locator(".library-work-row")
            .filter(has_text=LONG_TITLE)
            .locator(".library-work-title")
        )
        assert long_title.inner_text() == LONG_TITLE
        assert UNBROKEN_TITLE_SEGMENT in long_title.inner_text()
        assert long_title.evaluate("node => node.scrollWidth") <= long_title.evaluate(
            "node => node.parentElement.clientWidth"
        )
        assert long_title.bounding_box()["height"] <= 44
        assert (
            long_title.evaluate("node => getComputedStyle(node).whiteSpace") == "normal"
        )
        assert (
            long_title.evaluate("node => getComputedStyle(node).webkitLineClamp") == "2"
        )
        assert (
            long_title.evaluate("node => getComputedStyle(node).overflowWrap")
            == "anywhere"
        )
        assert (
            long_title.evaluate("node => getComputedStyle(node.parentElement).overflow")
            == "visible"
        )
        stage[0] = "library:keyboard-traversal"
        page.goto("https://react-platform.invalid/library?lens=all")
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        page.evaluate("() => { document.body.tabIndex = -1; document.body.focus(); }")
        assert page.evaluate("() => document.activeElement === document.body")
        for _ in range(20):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.matches('.lenses a')"):
                break
        assert page.evaluate("() => document.activeElement.matches('.lenses a')")
        assert (
            page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
            == "solid"
        )
        assert (
            page.evaluate(
                "() => getComputedStyle(document.activeElement).outlineOffset"
            )
            == "3px"
        )
        for _ in range(10):
            page.keyboard.press("Tab")
            if page.evaluate(
                "() => document.activeElement.matches('.library-work-row')"
            ):
                break
        assert page.evaluate(
            "() => document.activeElement.matches('.library-work-row')"
        )
        assert (
            page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
            == "solid"
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 1440

        stage[0] = "library:viewport:900"
        page.set_viewport_size({"width": 900, "height": 900})
        page.goto("https://react-platform.invalid/library?lens=all")
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=all&preview=case-1"
        )
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_visible()
        assert overrunning_rail_controls() == []
        assert (
            page.locator(".library-workspace").evaluate(
                "node => getComputedStyle(node).display"
            )
            != "block"
        )
        for item in page.locator(".masthead nav a").all():
            assert (
                item.evaluate("node => getComputedStyle(node).whiteSpace") == "nowrap"
            )
        assert (
            page.locator(".masthead").evaluate(
                "node => getComputedStyle(node).flexWrap"
            )
            == "wrap"
        )
        assert page.locator(".masthead nav").bounding_box()["y"] >= (
            page.locator(".brand").bounding_box()["y"]
            + page.locator(".brand").bounding_box()["height"]
        )
        compact_evidence = page.get_by_role(
            "button", name="Evidence for Current value: Current <script> title"
        )
        assert compact_evidence.bounding_box()["height"] >= 44
        compact_evidence.click()
        expect(page.locator("#choice-evidence-current")).to_be_visible()
        page.locator(".attention-toggle").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_visible()
        assert (
            page.locator(".attention-rail").evaluate(
                "node => getComputedStyle(node).position"
            )
            == "fixed"
        )
        assert page.locator(".attention-rail").bounding_box()["y"] >= (
            page.locator(".masthead").bounding_box()["y"]
            + page.locator(".masthead").bounding_box()["height"]
            + 8
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 900
        page.locator(".attention-rail header button").click()

        stage[0] = "library:viewport:760"
        page.set_viewport_size({"width": 760, "height": 900})
        page.goto("https://react-platform.invalid/library?lens=all")
        expect(page).to_have_url("https://react-platform.invalid/library?lens=all")
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        assert overrunning_rail_controls() == []
        page.locator(".library-work-row").first.click()
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=all&preview=case-1"
        )
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".workspace-detail")).to_be_visible()
        assert (
            page.locator(".library-workspace").evaluate(
                "node => getComputedStyle(node).display"
            )
            != "block"
        )
        page.get_by_role("button", name="Attention queue").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".attention-rail")).to_be_focused()
        assert page.locator(".attention-rail").bounding_box()["y"] >= (
            page.locator(".masthead").bounding_box()["y"]
            + page.locator(".masthead").bounding_box()["height"]
            + 8
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 760

        stage[0] = "library:viewport:390"
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("https://react-platform.invalid/library?lens=all")
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        page.locator(".library-work-row").first.wait_for()
        assert overrunning_rail_controls() == []
        # Text zoom narrows the rail without narrowing its labels, which is
        # where a fixed single-row lens strip fails first.
        for zoom_width, zoom_height in ((1440, 900), (900, 900), (390, 844)):
            page.set_viewport_size({"width": zoom_width, "height": zoom_height})
            page.goto("https://react-platform.invalid/library?lens=all")
            page.locator(".library-work-row").first.wait_for()
            page.locator("html").evaluate("node => { node.style.fontSize = '200%' }")
            page.locator(".library-work-row").first.wait_for()
            assert overrunning_rail_controls() == [], zoom_width
            page.locator("html").evaluate("node => { node.style.fontSize = '' }")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("https://react-platform.invalid/library?lens=all")
        page.locator(".library-work-row").first.wait_for()
        page.locator(".attention-toggle").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".attention-rail")).to_be_focused()
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        page.locator(".attention-queue-item").first.click()
        expect(page.locator(".workspace-detail")).to_be_visible()
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".field-filmstrip button")).to_have_count(5)
        assert "Title" in page.locator(".field-filmstrip").inner_text()
        assert "Cover" in page.locator(".field-filmstrip").inner_text()
        assert not (
            page.locator(".automatic-resolution-details").evaluate("node => node.open")
        )
        assert (
            page.locator(".direct-choice").bounding_box()["y"]
            < page.locator(".automatic-resolution-details").bounding_box()["y"]
        )
        page.locator(".automatic-resolution-details summary").click()
        compact_context = page.locator(".automatic-resolution-details").inner_text()
        assert "Resolved automatically" in compact_context
        assert "No eligible change" in compact_context
        compact_evidence = page.get_by_role(
            "button", name="Evidence for Current value: Current <script> title"
        )
        assert compact_evidence.bounding_box()["height"] >= 44
        compact_evidence.click()
        expect(page.locator("#choice-evidence-current")).to_be_visible()
        assert (
            "Guarded from external update"
            in page.locator(".field-apply-context").inner_text()
        )
        assert page.get_by_role("button", name="Previous").count() == 0
        assert page.get_by_role("button", name="Next").count() == 0
        expect(page.locator(".compact-workspace-controls button")).to_have_count(2)
        for control in page.locator(".compact-workspace-controls button").all():
            assert control.bounding_box()["height"] >= 44
        page.get_by_role("button", name="Back to works").click()
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        stage[0] = "review:case-1:desktop-decision"
        page.set_viewport_size({"width": 1440, "height": 900})
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=all&preview=case-1"
        )
        expect(page.locator(".workspace-detail")).to_be_visible()
        page.goto(
            "https://react-platform.invalid/review/case-1?field=title&subview=review"
        )
        page.get_by_text(
            "Three independent catalogue observations disagree on presentation.",
            exact=True,
        ).wait_for()
        desktop_field_rail = page.locator(".detail-layout > .field-navigation")
        assert (
            desktop_field_rail.evaluate("node => getComputedStyle(node).position")
            == "sticky"
        )
        assert (
            desktop_field_rail.evaluate("node => getComputedStyle(node).top") == "82px"
        )
        choices = page.locator("#field-title .direct-choice-grid")
        assert "Current <script> title" in choices.inner_text()
        assert "The Clockmaker's Cartography" in choices.inner_text()
        assert "Cartography of Impossible Libraries" in choices.inner_text()
        assert LONG_TITLE in choices.inner_text()
        assert page.get_by_role("button", name="Reject proposed change").count() == 0
        assert page.get_by_role("button", name="Keep current value").count() == 0
        title_notices = page.locator("#field-title .field-notices")
        assert "Local catalogue record" in title_notices.inner_text()
        assert "Project Gutenberg record" in title_notices.inner_text()
        assert "Google Books catalogue record" in title_notices.inner_text()
        assert "Open Library record" in title_notices.inner_text()
        assert "provider_" not in title_notices.inner_text()
        decide(
            "pointer",
            1,
            lambda: page.locator("#field-title .proposed-value").first.click(),
        )
        page.get_by_text("Mara Venn", exact=False).first.wait_for()
        page.get_by_role("button", name="Authors", exact=True).click()
        assert (
            page.locator("#field-authors .direct-choice-grid .value-choice").count()
            == 3
        )
        author_notices = page.locator("#field-authors .field-notices")
        expect(author_notices).to_be_visible()
        expect(author_notices).to_contain_text("Corroborated creator sources")
        expect(author_notices).to_contain_text("Hardcover · Open Library")
        assert "provider_" not in author_notices.inner_text()
        page.get_by_role("button", name="Subtitle", exact=True).click()
        subtitle = page.locator("#field-subtitle")
        subtitle.get_by_text(
            "This field is unavailable for the current service capability.", exact=True
        ).wait_for()
        assert "Unavailable" in subtitle.inner_text()
        assert subtitle.locator(".direct-choice-grid").count() == 0
        assert subtitle.get_by_text("Not set", exact=True).count() == 0
        assert page.get_by_role("button", name="Subtitle", exact=True).count() == 1
        page.get_by_role("button", name="Evidence", exact=True).click()
        page.get_by_role("heading", name="Evidence and source ledger").wait_for()
        page.locator(".evidence-item").first.wait_for()
        assert page.locator(".evidence-item").count() == 3
        page.get_by_role("button", name="Load more evidence").click()
        expect(page.locator(".evidence-item")).to_have_count(6)
        assert (
            page.get_by_text("Evidence could not be loaded", exact=False).count() == 0
        )
        page.get_by_role("button", name="History", exact=True).click()
        page.get_by_role("heading", name="Current-case history").wait_for()
        page.get_by_text("Case admitted", exact=False).wait_for()
        assert (
            "Human decision · title" in page.locator(".progressive-panel").inner_text()
        )
        stage[0] = "review:case-stale"
        page.goto("https://react-platform.invalid/review/case-stale?subview=review")
        page.get_by_role("heading", name="Case superseded").wait_for()
        stage[0] = "review:case-11:no-owner-decision"
        page.goto(
            "https://react-platform.invalid/review/case-11?field=authors&subview=review"
        )
        page.get_by_role("heading", name="Current review case").wait_for()
        for destination in ("title", "authors", "subtitle", "series"):
            page.get_by_role(
                "button", name=destination.capitalize(), exact=True
            ).click()
            page.locator(f"#field-{destination}").get_by_text(
                "There is no owner decision available for this field.", exact=True
            ).wait_for()
            assert (
                page.locator(f"#field-{destination} .direct-choice-grid").count() == 0
            )
            assert "Current value" in page.locator(f"#field-{destination}").inner_text()

        stage[0] = "review:case-5:automatic-selection"
        page.goto(
            "https://react-platform.invalid/review/case-5?field=authors&subview=review"
        )
        page.get_by_role("heading", name="Current review case").wait_for()
        automatic_rail = page.locator(".detail-layout > .field-navigation")
        expect(automatic_rail.get_by_role("button")).to_have_count(4)
        page.get_by_role("button", name="Title", exact=True).click()
        automatic_title = page.locator("#field-title")
        expect(
            automatic_title.locator(".direct-choice-grid .value-choice")
        ).to_have_count(4)
        expect(automatic_title.locator(".current-value")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert "is-selected" in (
            automatic_title.locator(".current-value").get_attribute("class") or ""
        )
        page.get_by_role("button", name="Authors", exact=True).click()
        automatic_authors = page.locator("#field-authors")
        expect(
            automatic_authors.locator(".direct-choice-grid .value-choice")
        ).to_have_count(3)
        expect(automatic_authors.locator(".current-value")).to_have_attribute(
            "aria-pressed", "true"
        )
        # One driver-side action, so the route-ready focus effect cannot land
        # between focusing the control and pressing the key and send the press
        # to the field container instead. Keyboard activation stays covered.
        decide(
            "keyboard",
            2,
            lambda: automatic_authors.locator(".proposed-value").first.press("Space"),
        )

        stage[0] = "review:case-equal:preservation-only"
        page.goto(
            "https://react-platform.invalid/review/case-equal?field=title&subview=review"
        )
        page.get_by_role("heading", name="Current review case").wait_for()
        equal_title = page.locator("#field-title")
        assert "preservation-only" in (
            equal_title.locator(".direct-choice-grid").get_attribute("class") or ""
        )
        expect(equal_title.locator(".direct-choice-grid .value-choice")).to_have_count(
            1
        )
        expect(equal_title.locator(".direct-choice button")).to_have_count(2)
        assert equal_title.locator(".proposed-value").count() == 0
        assert (
            "Current <script> title"
            in equal_title.locator(".current-value").inner_text()
        )
        stage[0] = "review:case-4:return-to-automatic"
        page.goto(
            "https://react-platform.invalid/review/case-4?field=title&subview=review"
        )
        page.get_by_role("heading", name="Current review case").wait_for()
        page.get_by_role("button", name="Return to automatic handling").click()
        page.get_by_text(
            "Automatic policy resumes after fresh checks.", exact=True
        ).wait_for()
        assert return_automatic_requests == 1
        stage[0] = "review:case-4:compact-layout"
        page.set_viewport_size({"width": 390, "height": 844})
        compact_field_rail = page.locator(".detail-layout > .field-navigation")
        assert (
            compact_field_rail.evaluate("node => getComputedStyle(node).position")
            == "static"
        )
        assert (
            compact_field_rail.evaluate("node => getComputedStyle(node).top") == "auto"
        )
        for control in page.locator(".masthead nav a, .shell-actions button").all():
            assert control.bounding_box()["height"] >= 44
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390

        assert activity_failures == 1
        assert consent_payloads == [
            {
                "grants": [
                    {
                        "library": "Spoken stories",
                        "destinations": ["authors", "title"],
                    }
                ]
            }
        ]

        before_navigation = documents
        page.get_by_role("link", name="Library", exact=True).click()
        page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
        page.locator(".attention-toggle").click()
        page.locator(".attention-rail h2").get_by_text(
            "Attention", exact=True
        ).wait_for()
        assert page.locator(".attention-queue-item").count() == 6
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        attention_link = page.locator(".attention-queue-item").first
        assert "item-1" not in page.locator(".attention-rail").inner_text()
        page.evaluate(
            "() => document.body.dataset.persistentDocument = 'rich-attention'"
        )
        before_detail = documents
        attention_link.click()
        page.locator(".workspace-detail .decision-canvas").wait_for()
        assert (
            page.evaluate("() => document.body.dataset.persistentDocument")
            == "rich-attention"
        )
        assert documents == before_detail
        linked_choices = page.locator(".workspace-detail .direct-choice-grid")
        assert "Current <script> title" in linked_choices.inner_text()
        assert linked_choices.locator(".choice-select").count() == 4
        assert linked_choices.locator(".choice-evidence-toggle").count() == 4
        page.go_back()
        page.locator(".attention-rail").wait_for()
        page.go_back()
        page.get_by_role("heading", name="Every work, with exceptions first").wait_for()
        for control in page.locator(
            ".lenses a, .library-pager button, .library-work-row"
        ).all():
            assert control.bounding_box()["height"] >= 44
        for lens in page.locator(".lenses a").all():
            assert lens.bounding_box()["width"] >= 44
        compact_title = (
            page.locator(".library-work-row")
            .filter(has_text=LONG_TITLE)
            .locator(".library-work-title")
        )
        assert compact_title.inner_text() == LONG_TITLE
        assert UNBROKEN_TITLE_SEGMENT in compact_title.inner_text()
        assert compact_title.evaluate(
            "node => node.scrollWidth"
        ) <= compact_title.evaluate("node => node.parentElement.clientWidth")
        assert compact_title.bounding_box()["height"] <= 44
        assert (
            compact_title.evaluate("node => getComputedStyle(node).whiteSpace")
            == "normal"
        )
        assert (
            compact_title.evaluate("node => getComputedStyle(node).webkitLineClamp")
            == "2"
        )
        assert (
            compact_title.evaluate("node => getComputedStyle(node).overflowWrap")
            == "anywhere"
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        assert documents == before_navigation
        stage[0] = "policy:chapters-and-preview"
        page.get_by_role("link", name="Policy", exact=True).click()
        page.get_by_role("heading", name="Library policy").wait_for()
        page.get_by_role("navigation", name="Policy chapters").get_by_role(
            "button", name="Curation"
        ).click()
        expect(page.locator("#policy-curation")).to_be_focused()
        cover_policy = page.get_by_role("region", name="Cover")
        cover_policy.get_by_text(
            "Cover review uses the fixed supported source order.", exact=True
        ).wait_for()
        expect(cover_policy.get_by_role("button", name="Automatic")).to_be_disabled()
        expect(cover_policy.get_by_role("button", name="Ask me")).to_have_attribute(
            "aria-pressed", "true"
        )
        cover_representation = cover_policy.get_by_role(
            "combobox", name="Cover representation"
        )
        expect(cover_representation).to_be_disabled()
        expect(cover_representation).to_have_text("Fixed supported source order")
        # Unsupported automatic mode says so plainly; gate mechanics are gone.
        assert "Automatic mode not supported" in cover_policy.inner_text()
        assert "Automatic writes" not in cover_policy.inner_text()
        assert "qualified" not in cover_policy.inner_text()
        assert "Enter Preview" not in cover_policy.inner_text()
        cover_help = cover_policy.locator(".cm-policy-field-guidance")
        assert cover_help.evaluate("node => node.open") is False
        assert (
            cover_help.evaluate(
                "node => getComputedStyle(node.querySelector('summary'), '::after')"
                ".content"
            )
            == '"\u25b8"'
        )
        cover_help.get_by_text("What this changes", exact=True).click()
        capture_visual_proof("policy-field-guidance")
        assert cover_help.evaluate("node => node.open") is True
        assert cover_help.locator("li").count() >= 1
        assert (
            cover_help.evaluate(
                "node => getComputedStyle(node.querySelector('summary'), '::after')"
                ".content"
            )
            == '"\u25be"'
        )
        cover_help.get_by_text("What this changes", exact=True).click()
        capture_visual_proof("policy-cover")
        page.get_by_role("navigation", name="Policy chapters").get_by_role(
            "button", name="Identity"
        ).click()
        expect(page.locator("#policy-identity")).to_be_focused()
        page.locator(".cm-policy-finish").get_by_text(
            "Draft matches the active policy", exact=True
        ).wait_for()
        page.locator(".cm-policy-mode summary").click()
        page.get_by_text("Outcomes remain non-authoritative.", exact=True).wait_for()
        page.get_by_text(
            "2 recent non-authoritative outcome sets.", exact=True
        ).wait_for()
        page.get_by_role(
            "heading", name="Activation completed with failures"
        ).wait_for()
        page.get_by_role("button", name="Retry failed reconciliation").wait_for()
        page.get_by_role("button", name="Recover Title suspension").wait_for()
        page.get_by_role("region", name="Title and subtitle").get_by_role(
            "button", name="Ignore"
        ).click()
        page.get_by_text("1 draft change", exact=True).wait_for()
        page.get_by_role("button", name="Validate and preview").click()
        page.get_by_text("Impact preview is current.", exact=False).wait_for()
        page.get_by_text(
            "· fills the empty field with A bounded subtitle", exact=True
        ).wait_for()
        page.get_by_text("Safe visible description <3", exact=False).wait_for()
        page.get_by_text(
            '[{"name":"Old order","sequence":"1"}]', exact=False
        ).wait_for()
        page.get_by_text('["Fantasy"] → ["Fantasy","Epic"]', exact=False).wait_for()
        page.get_by_text(
            "· remains empty — this policy leaves the field unchanged", exact=True
        ).wait_for()
        page.get_by_text("Plus 2 more exact field outcomes", exact=False).wait_for()
        assert page.locator(".cm-policy-preview-list > div").count() == 6
        impact_labels = page.locator(".cm-policy-impact > b").all_text_contents()
        assert impact_labels == [
            "Change",
            "Change",
            "Change",
            "No change",
            "Change",
            "Change",
        ]
        for detail in page.locator(".cm-policy-impact > small").all_text_contents():
            if "→" in detail:
                assert "remains" not in detail
                assert "empty" not in detail
        for label in impact_labels:
            assert label not in {"projected", "abstain", "missing", "policy_off"}
        page.get_by_role("button", name="Sign out").click()
        page.get_by_text("Writes are blocked", exact=False).wait_for()
        fail_policy_persistently = True
        stage[0] = "policy:asset-failure-recovery"
        page.goto("https://react-platform.invalid/policy?section=identity")
        page.get_by_role(
            "heading", name="The current workspace assets could not be loaded."
        ).wait_for()
        assert documents == (before_navigation + 2)
        page.get_by_role("button", name="Reload the current application").click()
        page.get_by_role(
            "heading", name="The current workspace assets could not be loaded."
        ).wait_for()
        assert documents == (before_navigation + 3)
        expected_chunk_errors = [
            error
            for error in errors
            if "503 (Service Unavailable)" in error or "ChunkRecoveryError" in error
        ]
        expected_ambiguous_errors = [
            error for error in errors if "net::ERR_FAILED" in error
        ]
        assert len(expected_chunk_errors) >= 2
        assert [
            error
            for error in errors
            if error not in expected_chunk_errors
            and error not in expected_ambiguous_errors
        ] == []
        assert expected_ambiguous_errors == (
            ["Failed to load resource: net::ERR_FAILED"] * ambiguous_decisions
        )
        decision_request_failures = [
            failure for failure in failed_requests if failure[2] == "net::ERR_FAILED"
        ]
        assert len(decision_request_failures) == ambiguous_decisions, failed_requests
        for failed_method, failed_url, failed_reason in decision_request_failures:
            assert failed_method == "POST"
            assert failed_url.startswith(
                "https://react-platform.invalid/api/v1/review-cases/"
            ) and failed_url.endswith("/decisions"), failed_url
            assert failed_reason == "net::ERR_FAILED"
        assert [
            failure
            for failure in failed_requests
            if failure[2] not in {"net::ERR_ABORTED", "net::ERR_FAILED"}
        ] == []
