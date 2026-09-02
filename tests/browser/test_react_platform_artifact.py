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
    TERMINAL_FAILURE_COPY,
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
    transition_failures = 0
    ambiguous_decisions = 0
    fail_policy_persistently = False
    description_mode = False
    consent_payloads: list[object] = []
    return_automatic_requests = 0
    connection_state = "paused"
    failed_requests: list[tuple[str, str, str | None]] = []

    def fulfill(route: Route) -> None:
        nonlocal activity_failures, ambiguous_decisions, documents, transition_failures
        nonlocal connection_state, return_automatic_requests
        parsed = urlsplit(route.request.url)
        path = parsed.path
        if path.startswith("/api/v1/"):
            if path == "/api/v1/appearance":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"reduced_motion": "follow_system"}),
                )
                return
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
            if (
                path == "/api/v1/review-preparation/primary/check-connection"
                and route.request.method == "POST"
            ):
                connection_state = "recovered"
                recovered_payload = cast(
                    "dict[str, object]", rich_payload("/api/v1/review-preparation")
                )
                conditions = cast(
                    "list[dict[str, object]]", recovered_payload["connections"]
                )
                conditions[0] = {
                    **conditions[0],
                    "state": "recovered",
                    "affected": 0,
                    "updated_at": 2,
                }
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(recovered_payload),
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
            if path.startswith("/api/v1/activity/") and path.endswith("/transitions"):
                if transition_failures == 0:
                    transition_failures += 1
                    route.fulfill(status=503, body="")
                    return
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(rich_payload(route.request.url)),
                )
                return
            payload = rich_payload(route.request.url)
            if path == "/api/v1/review-preparation" and connection_state != "paused":
                preparation = cast("dict[str, object]", payload)
                conditions = cast("list[dict[str, object]]", preparation["connections"])
                conditions[0] = {
                    **conditions[0],
                    "state": connection_state,
                    "affected": 0
                    if connection_state == "recovered"
                    else conditions[0]["affected"],
                    "updated_at": 2,
                }
            if description_mode and path == "/api/v1/review-cases/case-1":
                detail = cast("dict[str, object]", payload)
                fields = cast("list[dict[str, object]]", detail["fields"])
                description = (
                    "First retained paragraph.\n\nSecond retained paragraph. " * 12
                )
                fields[0] = {
                    **fields[0],
                    "destination": "description",
                    "current": {"kind": "synthetic", "value": description},
                    "proposed_values": [
                        {"kind": "synthetic", "value": description + "Alternative."}
                    ],
                    "choice_contexts": [
                        fields[0]["choice_contexts"][0],
                        fields[0]["choice_contexts"][1],
                    ],
                }
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(payload),
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
                    expect(page.locator("html")).to_have_attribute("data-theme", scheme)
                    page.evaluate(
                        """() => new Promise(resolve => requestAnimationFrame(() =>
                            requestAnimationFrame(resolve)))"""
                    )
                    page.emulate_media(reduced_motion="reduce")
                    for width, height, size in (
                        (1440, 900, "desktop"),
                        (900, 900, "medium"),
                        (390, 844, "compact"),
                    ):
                        page.set_viewport_size({"width": width, "height": height})
                        if name == "activity-labels":
                            expect(
                                page.get_by_role("region", name="Provider recovery")
                                .get_by_text("Waiting for a safe retry", exact=True)
                                .locator("..")
                            ).to_contain_text(
                                "Priority: needs independent evidence, general recovery scan"
                            )
                            assert page.evaluate(
                                "() => document.documentElement.scrollWidth <= window.innerWidth"
                            )
                        assert page.evaluate(
                            """() => {
                                const probe = document.createElement('span')
                                probe.style.color = 'var(--ink)'
                                document.body.append(probe)
                                const ink = getComputedStyle(probe).color
                                probe.remove()
                                return [...document.querySelectorAll('h1, h2')]
                                  .every(heading => getComputedStyle(heading).color === ink)
                            }"""
                        )
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
            ("/library?lens=all", "Works"),
            ("/attention", "Needs your attention"),
            (
                "/review/case-1?field=title&subview=evidence",
                "Evidence",
            ),
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
            if heading is not None:
                page.get_by_role("heading", name=heading).wait_for()
            if path in {
                "/library?lens=all",
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
                    if path != "/activity":
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
                for width in (1440, 900, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = '200%' }"
                    )
                    _fits_its_container(page, ".connection-notice", width)
                    page.locator("html").evaluate(
                        "node => { node.style.fontSize = '' }"
                    )
                if path == "/library?lens=all":
                    check.click()
                    page.get_by_text(
                        "Library connection recovered", exact=True
                    ).wait_for()
                    recovered = page.get_by_label("Shared library connection status")
                    capture_visual_proof("connection-recovered")
                    for width in (1440, 900, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        page.locator("html").evaluate(
                            "node => { node.style.fontSize = '200%' }"
                        )
                        _fits_its_container(page, ".connection-notice", width)
                        page.locator("html").evaluate(
                            "node => { node.style.fontSize = '' }"
                        )
                    recovered.get_by_role("button", name="Dismiss").click()
                    expect(recovered).to_have_count(0)

                    page.goto("https://react-platform.invalid/library?lens=all")
                    page.get_by_label("Library controls").wait_for()
                    expect(
                        page.get_by_label("Shared library connection status")
                    ).to_have_count(0)

                    connection_state = "paused"
                    page.goto("https://react-platform.invalid/library?lens=all")
                    page.get_by_label("Library controls").wait_for()
                    page.get_by_text(
                        "Library connection needs attention", exact=True
                    ).first.wait_for()
                    # These two fresh documents prove recovered state is not
                    # restored after a restart; keep the route's navigation
                    # accounting scoped to its original direct entry.
                    before_entry += 2
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
                expect(
                    recovery.get_by_text(
                        "Waiting for a safe retry", exact=True
                    ).locator("..")
                ).to_contain_text(
                    "Priority: needs independent evidence, general recovery scan"
                )
                recovery_text = recovery.inner_text().lower()
                for forbidden in (
                    "provider_",
                    "query=",
                    "authorization:",
                    "bearer ",
                    "payload",
                    "/private/",
                    "x-api-key",
                    "missing_corroboration",
                    "zero_provider_evidence",
                    "settleable_disagreement",
                    "blanket_scan",
                ):
                    assert forbidden not in recovery_text
                assert (
                    recovery.get_by_role("button", name="Retry", exact=False).count()
                    == 0
                )
                activity_ledger = page.locator(".cm-activity-ledger")
                prior_viewport = page.viewport_size
                assert prior_viewport is not None

                def assert_compact_activity_priority(
                    state: str, ledger=activity_ledger
                ) -> None:
                    page.set_viewport_size({"width": 320, "height": 844})
                    assert page.viewport_size == {"width": 320, "height": 844}
                    page.evaluate("() => window.scrollTo(0, 0)")
                    heading = page.locator(".cm-page-heading")
                    ledger_heading = ledger.locator(".cm-activity-ledger-heading")
                    controls = ledger.locator(".cm-activity-tools")
                    filters = controls.locator(".cm-activity-filters")
                    first_row = ledger.locator("article").first
                    notice = page.get_by_label("Shared library connection status")
                    for locator in (
                        heading,
                        ledger_heading,
                        controls,
                        filters,
                        first_row,
                        notice,
                    ):
                        expect(locator).to_be_visible()
                    heading_box = heading.bounding_box()
                    ledger_heading_box = ledger_heading.bounding_box()
                    controls_box = controls.bounding_box()
                    first_row_box = first_row.bounding_box()
                    notice_box = notice.bounding_box()
                    brand_box = page.locator(".brand").bounding_box()
                    actions_box = page.locator(".shell-actions").bounding_box()
                    primary_navigation = page.get_by_role(
                        "navigation", name="Primary workspace"
                    )
                    navigation_box = primary_navigation.bounding_box()
                    settings_box = primary_navigation.get_by_role(
                        "link", name="Settings", exact=True
                    ).bounding_box()
                    assert (
                        heading_box is not None
                        and ledger_heading_box is not None
                        and controls_box is not None
                        and first_row_box is not None
                        and notice_box is not None
                        and brand_box is not None
                        and actions_box is not None
                        and navigation_box is not None
                        and settings_box is not None
                    )
                    assert primary_navigation.get_by_role("link").all_inner_texts() == [
                        "Library",
                        "Policy",
                        "Activity",
                        "Settings",
                    ]
                    assert settings_box["width"] >= 44
                    assert settings_box["height"] >= 44
                    assert navigation_box["y"] >= max(
                        brand_box["y"] + brand_box["height"],
                        actions_box["y"] + actions_box["height"],
                    )
                    assert navigation_box["x"] <= settings_box["x"]
                    assert settings_box["x"] + settings_box["width"] <= (
                        navigation_box["x"] + navigation_box["width"]
                    )
                    assert (
                        heading_box["y"]
                        < ledger_heading_box["y"]
                        < controls_box["y"]
                        < first_row_box["y"]
                        < notice_box["y"]
                    ), state
                    assert first_row_box["y"] < 844, state
                    assert (
                        min(first_row_box["y"] + first_row_box["height"], 844)
                        - max(first_row_box["y"], 0)
                        >= 24
                    ), state
                    first_time_box = first_row.locator("time").bounding_box()
                    assert first_time_box is not None
                    assert 0 <= first_time_box["y"], state
                    assert first_time_box["y"] + first_time_box["height"] <= 844, state
                    assert filters.evaluate(
                        "node => node.scrollWidth > node.clientWidth"
                    ), state
                    filters_box = filters.bounding_box()
                    assert filters_box is not None and filters_box["height"] <= 48, (
                        state
                    )
                    assert filters.evaluate(
                        """node => {
                          const parent = node.parentElement;
                          if (parent === null) return false;
                          const rail = node.getBoundingClientRect();
                          const tools = parent.getBoundingClientRect();
                          return rail.left >= tools.left && rail.right <= tools.right;
                        }"""
                    ), state
                    chips = filters.get_by_role("button")
                    assert chips.count() == 5, state
                    assert (
                        filters.locator("button[aria-pressed='true']").count() == 1
                    ), state
                    for index in range(chips.count()):
                        chip_box = chips.nth(index).bounding_box()
                        assert (
                            chip_box is not None
                            and min(chip_box["width"], chip_box["height"]) >= 44
                        ), state
                    chips.first.focus()
                    for _ in range(chips.count() - 1):
                        page.keyboard.press("Tab")
                    expect(chips.last).to_be_focused()
                    assert filters.evaluate(
                        """node => {
                          const last = node.querySelector('button:last-child');
                          if (last === null) return false;
                          const rail = node.getBoundingClientRect();
                          const chip = last.getBoundingClientRect();
                          return chip.right > rail.left && chip.left < rail.right;
                        }"""
                    ), state
                    assert chips.last.evaluate(
                        """node => {
                          const style = getComputedStyle(node);
                          return style.outlineStyle === 'solid' &&
                            parseFloat(style.outlineWidth) >= 3 &&
                            parseFloat(style.outlineOffset) <= 0;
                        }"""
                    ), state
                    expect(
                        ledger.get_by_text(
                            "One record for review decisions, policy changes, safeguards, writes, and verification.",
                            exact=True,
                        )
                    ).to_be_visible()
                    expect(ledger.get_by_text("Date range", exact=True)).to_be_visible()
                    expect(
                        controls.get_by_role("searchbox", name="Search activity")
                    ).to_be_visible()
                    assert page.evaluate(
                        "() => document.documentElement.scrollWidth <= 320"
                    ), state

                try:
                    assert_compact_activity_priority("paused")
                    for connection_state in ("checking",):
                        page.reload()
                        page.get_by_role("heading", name="Activity ledger").wait_for()
                        assert_compact_activity_priority(connection_state)
                    connection_state = "paused"
                    page.reload()
                    page.get_by_role("heading", name="Activity ledger").wait_for()
                    page.get_by_label("Shared library connection status").get_by_role(
                        "button", name="Check connection again"
                    ).click()
                    page.get_by_text(
                        "Library connection recovered", exact=True
                    ).wait_for()
                    assert_compact_activity_priority("recovered")
                finally:
                    page.set_viewport_size(prior_viewport)
                connection_state = "paused"
                for work, label in (
                    ("Equal automatic title", "No change required"),
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
                automatic_row = activity_ledger.get_by_text(
                    "The Glass Orchard", exact=True
                ).locator("xpath=ancestor::article")
                automatic_row.get_by_role("button", name="Details").click()
                expect(automatic_row.get_by_role("alert")).to_contain_text(
                    "Recorded transitions could not be loaded."
                )
                capture_visual_proof("activity-transition-error")
                automatic_row.get_by_role("button", name="Try again").click()
                automatic_row.get_by_role(
                    "button", name="Load more transitions"
                ).wait_for()
                automatic_row.get_by_role(
                    "button", name="Load more transitions"
                ).click()
                expect(
                    automatic_row.get_by_role("button", name="Load more transitions")
                ).to_have_count(0)
                capture_visual_proof("activity-transition-disclosure")
                expect(
                    automatic_row.get_by_text("Previous value", exact=True)
                ).to_be_visible()
                expect(
                    automatic_row.get_by_text("Paper Moons: Larkspur", exact=True)
                ).to_be_visible()
                expect(
                    automatic_row.get_by_text("New value", exact=True)
                ).to_be_visible()
                expect(
                    automatic_row.get_by_text(
                        "Paper Moons over Larkspur Station", exact=True
                    )
                ).to_be_visible()
                expect(
                    automatic_row.get_by_text("Chosen by", exact=True)
                ).to_be_visible()
                expect(
                    automatic_row.get_by_text("Automatic policy", exact=True)
                ).to_be_visible()
                capture_visual_proof("activity-automatic-values")
                automatic_row.get_by_role("button", name="Hide details").click()
                capture_visual_proof("activity-labels")
                ledger_box = activity_ledger.bounding_box()
                first_row_box = activity_ledger.locator("article").first.bounding_box()
                assert ledger_box is not None and first_row_box is not None
                # Compact Activity prioritises the searchable ledger: its first
                # row is reachable without crossing a long recovery summary.
                assert first_row_box["y"] < 844
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
                        assert (
                            page.get_by_role(
                                "link", name="Settings", exact=True
                            ).bounding_box()["width"]
                            >= 44
                        )
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
                    page_number.fill("2")
                    page_number.press("Escape")
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
                    page_number.fill("2")
                    page_number.press("Escape")
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
            assert documents == (before_entry + (4 if path == "/activity" else 1))

        for case_id, label, retry in (
            ("case-1", "Advisory visual review complete", False),
            ("case-2", "Deterministic confirmed choice remains available", True),
            ("case-3", "Deterministic confirmed choice remains available", True),
        ):
            stage[0] = f"cover-library:{case_id}"
            page.goto(
                "https://react-platform.invalid/library?lens=all"
                f"&preview={case_id}&field=cover"
            )
            comparison = page.get_by_role("region", name="Choose a cover")
            comparison.get_by_text(label, exact=True).wait_for()
            comparison.get_by_text("Recommended cover ready", exact=True).wait_for()
            assert "best confirmed match is ready" in comparison.inner_text()
            for choice in comparison.locator("button[aria-pressed]").all():
                if "is-unconfirmed" in (
                    choice.locator("xpath=..").get_attribute("class") or ""
                ):
                    expect(choice).to_be_disabled()
                else:
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
            if case_id == "case-2":
                page.set_viewport_size({"width": 390, "height": 844})
                comparison_header = comparison.locator(":scope > header")
                assert (
                    comparison_header.evaluate(
                        "node => getComputedStyle(node).flexDirection"
                    )
                    == "column"
                )
                assert (
                    comparison_header.locator("strong").evaluate(
                        "node => getComputedStyle(node).overflowWrap"
                    )
                    == "normal"
                )
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= 390
                )
                page.set_viewport_size({"width": 1440, "height": 900})
            if case_id == "case-1":
                unconfirmed = comparison.locator(".cover-choice.is-unconfirmed")
                expect(unconfirmed).to_have_count(1)
                unconfirmed.get_by_text("Match not confirmed", exact=True).wait_for()
                assert (
                    "couldn't confirm that this cover belongs"
                    in unconfirmed.inner_text()
                )
                expect(unconfirmed.get_by_role("button")).to_be_disabled()
                current_evidence = comparison.get_by_role(
                    "button", name="Evidence for Current cover"
                )
                current_evidence.click()
                expect(current_evidence).to_have_count(1)
                expect(current_evidence).to_have_attribute("aria-expanded", "true")
                expect(current_evidence).to_have_text("Hide evidence")
                comparison.get_by_text("Local library record", exact=True).wait_for()
                expect(
                    comparison.locator(".library-cover-card.is-current")
                ).to_have_attribute("aria-pressed", "false")
                alternative_evidence = comparison.get_by_role(
                    "button", name="Evidence for Alternative 1 cover"
                )
                alternative_evidence.focus()
                page.keyboard.press("Enter")
                expect(alternative_evidence).to_have_count(1)
                expect(alternative_evidence).to_have_attribute("aria-expanded", "true")
                expect(alternative_evidence).to_have_text("Hide evidence")
                comparison.get_by_text("Open Library", exact=True).wait_for()
                comparison.get_by_text("External corroboration", exact=True).wait_for()
                expect(
                    comparison.locator(".library-cover-card.is-recommended")
                ).to_have_attribute("aria-pressed", "false")
            if case_id == "case-3":
                recommended = comparison.locator(".library-cover-card.is-recommended")
                expect(recommended).to_have_count(1)
                expect(recommended).to_be_enabled()
                recommended.get_by_text("Same book", exact=True).wait_for()
                recommended_evidence = comparison.get_by_role(
                    "button", name="Evidence for Alternative 1 cover"
                )
                recommended_evidence.click()
                expect(recommended_evidence).to_have_attribute("aria-expanded", "true")
                comparison.get_by_text("Open Library", exact=True).wait_for()
                unavailable_evidence = comparison.get_by_role(
                    "button", name="Evidence for Current cover"
                )
                unavailable_evidence.focus()
                page.keyboard.press("Enter")
                expect(unavailable_evidence).to_have_count(1)
                expect(unavailable_evidence).to_have_attribute("aria-expanded", "true")
                expect(unavailable_evidence).to_have_text("Hide evidence")
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
                "https://react-platform.invalid/library?lens=all&preview=missing-case"
            )
            assert response is not None
            assert response.status == 200
            page.get_by_text(
                "This work is not available in the current Library result.", exact=True
            ).wait_for()
            assert page.get_by_role("button", name="Try again").count() == 0
            assert page.locator(".workspace-detail").count() == 1
        missing_case_errors = errors[missing_case_error_start:]
        assert len(missing_case_errors) == 2
        assert (
            sum("status of 404 (Not Found)" in error for error in missing_case_errors)
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
        # #598 keeps recorded automatic fields primary, while applicable empty
        # fields remain closed contextual evidence rather than false choices.
        expect(page.locator(".field-filmstrip button")).to_have_count(5)
        assert "Title" in page.locator(".field-filmstrip").inner_text()
        assert "Cover" in page.locator(".field-filmstrip").inner_text()
        assert "Series" in page.locator(".field-filmstrip").inner_text()
        assert "Publication date" in page.locator(".field-filmstrip").inner_text()
        assert "Subtitle" not in page.locator(".field-filmstrip").inner_text()
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
        assert "Series" not in automatic_context
        page.locator(".field-filmstrip button", has_text="Publication date").click()
        # A current-only automatic outcome stays inspect-only: it remains
        # readable in the primary field strip without fabricating owner work.
        expect(page.get_by_role("heading", name="Publication date")).to_be_visible()
        expect(page.locator(".direct-choice-grid")).to_have_count(0)
        expect(
            page.get_by_role("region", name="Publication date recorded outcome")
        ).to_be_visible()
        capture_visual_proof("canonical-field-inventory")
        page.locator(".field-filmstrip button", has_text="Title").click()
        current_evidence = page.get_by_role(
            "button", name="Evidence for Current value: Current <script> title"
        )
        current_evidence.click()
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
        assert "Open Library" in page.locator(".choice-evidence").nth(1).inner_text()
        assert page.locator(".source-ledger").count() == 0
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
        expect(page.get_by_role("button", name="Back to works")).to_be_hidden()
        expect(page.get_by_role("button", name="Attention queue")).to_be_visible()
        page.get_by_role("button", name="Attention queue").click()
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
        no_case_row = page.locator(".library-work-row.is-terminal")
        work_detail = page.locator(".workspace-detail")
        assert no_case_row.get_by_role("link").count() == 0
        # A terminal case-less failure is information, not an unavailable
        # decision target: no click, detail surface, retry, or provider record.
        assert no_case_row.evaluate("node => node.tagName") == "ARTICLE"
        assert no_case_row.get_attribute("aria-label") == "Terminal preparation failure"
        assert no_case_row.get_by_role("button").count() == 0
        assert no_case_row.locator("a, button, details, [tabindex]").count() == 0
        assert "Reference: prep-" in no_case_row.inner_text()
        assert TERMINAL_FAILURE_COPY in no_case_row.inner_text()
        assert "retry" not in no_case_row.inner_text().casefold()
        assert (
            no_case_row.locator("img, svg, video, audio, iframe, object").count() == 0
        )
        assert (
            page.evaluate("() => window.__terminalFailureExecuted === true") is not True
        )
        # This canonical fixture has long, hostile-looking terminal copy. The
        # semantic and layout assertions are unconditional; visual capture is
        # supplemental and cannot be the only proof of this terminal state.
        for scheme in ("light", "dark"):
            stage[0] = f"library:terminal:{scheme}"
            toggle = "Use dark mode" if scheme == "dark" else "Use light mode"
            if page.get_by_role("button", name=toggle).count():
                page.get_by_role("button", name=toggle).click()
            expect(page.locator("html")).to_have_attribute("data-theme", scheme)
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 844})
                page.goto("https://react-platform.invalid/library?lens=all")
                terminal = page.locator(".library-work-row.is-terminal")
                expect(terminal).to_be_visible()
                assert TERMINAL_FAILURE_COPY in terminal.inner_text()
                assert "Reference: prep-" in terminal.inner_text()
                assert terminal.locator("a, button, details, [tabindex]").count() == 0
                assert (
                    terminal.locator("img, svg, video, audio, iframe, object").count()
                    == 0
                )
                assert "retry" not in terminal.inner_text().casefold()
                _fits_its_container(page, ".library-work-row.is-terminal", width)
                assert (
                    page.evaluate("() => document.documentElement.scrollWidth") <= width
                )
                url_before_focus = page.url
                terminal.focus()
                assert page.evaluate(
                    "() => document.activeElement !== document.querySelector('.library-work-row.is-terminal')"
                )
                page.locator(".library-work-row").nth(4).focus()
                page.keyboard.press("Tab")
                assert page.evaluate(
                    "() => !document.querySelector('.library-work-row.is-terminal')?.contains(document.activeElement)"
                )
                assert page.url == url_before_focus
            if scheme == "dark":
                page.get_by_role("button", name="Use light mode").click()
        page.set_viewport_size({"width": 1440, "height": 900})
        capture_visual_proof("library-work-summary")
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
        page.get_by_text("Pre-admission observation", exact=True).click()
        work_detail.get_by_role("heading", name="Pre-admission observation").wait_for()
        assert (
            "No review decision is recorded for this work." in work_detail.inner_text()
        )
        assert "Ember Lane" in work_detail.locator(".library-work-values").inner_text()
        assert (
            "2 consulted for this work"
            in work_detail.locator(".work-sources").inner_text()
        )
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
        # At the #830 compact-workspace boundary, selection becomes a
        # single-pane composition; the explicit Back control restores the rail.
        expect(page.locator(".library-works")).to_be_hidden()
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
        expect(page.get_by_role("button", name="Back to works")).to_be_visible()
        page.get_by_role("button", name="Attention queue").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".workspace-detail")).to_be_visible()
        assert (
            page.locator(".attention-rail").evaluate(
                "node => getComputedStyle(node).position"
            )
            == "fixed"
        )

        def attention_rail_clears_masthead() -> None:
            # Visibility does not wait for the fixed rail's `top` calculation.
            # Take both measurements in one browser-side layout snapshot after
            # the masthead-to-rail clearance reaches its accepted layout state.
            snapshot = page.wait_for_function(
                """() => {
                  const masthead = document.querySelector('.masthead')
                  const rail = document.querySelector('.attention-rail')
                  if (masthead === null || rail === null) return false
                  const mastheadBottom = masthead.getBoundingClientRect().bottom
                  const railY = rail.getBoundingClientRect().y
                  return railY >= mastheadBottom + 8 ? [railY, mastheadBottom] : false
                }"""
            ).json_value()
            assert type(snapshot) is list and len(snapshot) == 2
            rail_y, masthead_bottom = snapshot
            assert type(rail_y) in {int, float}
            assert type(masthead_bottom) in {int, float}
            assert rail_y >= masthead_bottom + 8

        attention_rail_clears_masthead()
        capture_visual_proof("library-attention-rail")
        page.set_viewport_size({"width": 900, "height": 900})
        expect(page.locator(".attention-rail")).to_be_visible()
        assert (
            page.locator(".attention-rail").evaluate(
                "node => getComputedStyle(node).position"
            )
            == "fixed"
        )
        page.locator(".masthead").evaluate("node => { node.style.minHeight = '220px' }")
        attention_rail_clears_masthead()
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 900
        page.locator(".attention-rail header button").click()

        stage[0] = "library:viewport:761"
        page.set_viewport_size({"width": 761, "height": 900})
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.get_by_role("button", name="Back to works")).to_be_visible()
        expect(page.get_by_role("button", name="Attention queue")).to_be_visible()

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
        expect(page.get_by_role("button", name="Back to works")).to_be_visible()
        expect(page.get_by_role("button", name="Attention queue")).to_be_visible()
        assert (
            page.locator(".library-workspace").evaluate(
                "node => getComputedStyle(node).display"
            )
            != "block"
        )
        page.get_by_role("button", name="Attention queue").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".attention-rail")).to_be_focused()
        attention_rail_clears_masthead()
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
            if zoom_width == 900:
                # The compact selected-work route hides its rail at this
                # boundary; return explicitly before measuring rail controls.
                page.get_by_role("button", name="Back to works").click()
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
        assert "Subtitle" in compact_context
        assert "Series" not in compact_context
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
        expect(page.get_by_role("button", name="Back to works")).to_be_visible()
        for control in page.locator(".compact-workspace-controls button").all():
            assert control.bounding_box()["height"] >= 44
        page.get_by_role("button", name="Back to works").click()
        expect(page.locator(".library-works")).to_be_visible()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        # Evidence and History are read-only per-work diagnostics. Library
        # remains the only decision surface, and owns the exact route back.
        stage[0] = "diagnostic:evidence-history"
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(
            "https://react-platform.invalid/library?lens=needs_attention&preview=case-1&field=title"
        )
        diagnostic_links = page.get_by_role(
            "navigation", name="Read-only work diagnostics"
        )
        expect(diagnostic_links.get_by_role("link", name="Evidence")).to_be_visible()
        expect(diagnostic_links.get_by_role("link", name="History")).to_be_visible()
        diagnostic_links.get_by_role("link", name="Evidence").click()
        expect(page).to_have_url(
            "https://react-platform.invalid/review/case-1?lens=needs_attention&subview=evidence&field=title"
        )
        page.get_by_role("heading", name="Evidence").wait_for()
        route = page.locator(".diagnostic-route")
        back = page.get_by_role("link", name="Back to work", exact=True)
        assert route.locator(":scope > *").first.evaluate(
            "node => node === document.querySelector('.diagnostic-back-to-work')"
        )
        assert back.get_attribute("href") == (
            "/library?lens=needs_attention&preview=case-1&field=title"
        )
        assert page.get_by_role("heading", name=_HOSTILE).count() == 1
        diagnostic_text = route.inner_text()
        assert "Household ebooks · epub" in diagnostic_text
        assert "internal-case-1" not in diagnostic_text
        assert "evidence-" not in diagnostic_text
        assert "candidate-" not in diagnostic_text
        assert page.locator("pre").count() == 0
        assert "referenced observations are no longer available" in diagnostic_text
        assert (
            "Read-only diagnostics · decisions stay in Library" not in diagnostic_text
        )
        source_disclosure = page.get_by_text("Sources", exact=True)
        source_disclosure.click()
        expect(page.get_by_text("Open Library", exact=True)).to_be_visible()
        expect(page.get_by_text("Hardcover", exact=True)).to_be_visible()
        assert page.locator(".direct-choice-grid").count() == 0
        assert (
            page.get_by_role(
                "button", name=re.compile("choose|retry|return|resume", re.IGNORECASE)
            ).count()
            == 0
        )
        load_more = page.get_by_role("button", name="Load more evidence")
        assert load_more.bounding_box()["height"] >= 44
        load_more.click()
        expect(load_more).to_have_count(0)
        assert page.locator(".diagnostic-entry").count() == 6
        nav = page.get_by_role("navigation", name="Case diagnostics")

        def assert_diagnostic_layout(active_page: str) -> None:
            active_link = nav.get_by_role("link", name=active_page)

            def assert_keyboard_path() -> None:
                page.evaluate(
                    "() => document.querySelector('.diagnostic-back-to-work').focus()"
                )
                expect(back).to_be_focused()
                page.keyboard.press("Tab")
                if active_page == "History":
                    page.keyboard.press("Tab")
                expect(active_link).to_be_focused()
                assert active_link.evaluate(
                    "node => getComputedStyle(node).outlineStyle === 'solid'"
                )

            for scheme in ("light", "dark"):
                if scheme == "dark":
                    page.get_by_role("button", name="Use dark mode").click()
                for width, height in (
                    (1440, 900),
                    (884, 900),
                    (390, 844),
                    (320, 844),
                ):
                    page.set_viewport_size({"width": width, "height": height})
                    assert page.evaluate(
                        "() => document.documentElement.scrollWidth <= window.innerWidth"
                    ), (active_page, scheme, width)
                    assert back.bounding_box()["height"] >= 44
                    for link in nav.get_by_role("link").all():
                        assert link.bounding_box()["height"] >= 44
                    assert_keyboard_path()
                page.set_viewport_size({"width": 1440, "height": 900})
                page.locator("html").evaluate(
                    "node => { node.style.fontSize = '200%' }"
                )
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"
                ), (active_page, scheme, "200%")
                assert back.bounding_box()["height"] >= 44
                for link in nav.get_by_role("link").all():
                    assert link.bounding_box()["height"] >= 44
                assert_keyboard_path()
                page.locator("html").evaluate("node => { node.style.fontSize = '' }")
                if scheme == "dark":
                    page.get_by_role("button", name="Use light mode").click()

        nav.get_by_role("link", name="Evidence").focus()
        page.keyboard.press("Tab")
        expect(nav.get_by_role("link", name="History")).to_be_focused()
        assert nav.get_by_role("link", name="History").evaluate(
            "node => getComputedStyle(node).outlineStyle === 'solid'"
        )
        assert_diagnostic_layout("Evidence")
        capture_visual_proof("case-diagnostics-evidence")
        nav.get_by_role("link", name="History", exact=True).click()
        page.get_by_role("heading", name="History").wait_for()
        page.get_by_text("Owner selected a proposed value.", exact=True).wait_for()
        history_text = route.inner_text()
        assert "Owner selected a proposed value." in history_text
        assert "Automatic policy chose the current value." in history_text
        assert "From" in history_text
        assert "To" in history_text
        assert history_text.count("Mara Venn, Ivo North") == 1
        assert "Retained" in history_text
        assert "This read-only preview recorded no decision authority." in history_text
        assert "Cover automatic prepared" in history_text
        for identifier in (
            "decision-owner-internal",
            "event-owner-internal",
            "outcome-set-internal",
            "cover-consideration-internal",
            "review-transition-internal",
        ):
            assert identifier not in history_text
        assert page.locator(".direct-choice-grid").count() == 0
        assert (
            page.get_by_role(
                "button", name=re.compile("choose|retry|return|resume", re.IGNORECASE)
            ).count()
            == 0
        )
        assert_diagnostic_layout("History")
        capture_visual_proof("case-diagnostics-history")
        page.go_back()
        page.get_by_role("heading", name="Evidence").wait_for()
        page.go_forward()
        page.get_by_role("heading", name="History").wait_for()
        nav.get_by_role("link", name="Evidence", exact=True).click()
        page.get_by_role("link", name="Back to work", exact=True).click()
        page.locator(".workspace-detail .decision-canvas").wait_for()
        expect(page).to_have_url(
            "https://react-platform.invalid/library?lens=needs_attention&preview=case-1&field=title"
        )

        page.set_viewport_size({"width": 320, "height": 844})
        page.goto(
            "https://react-platform.invalid/review/case-stale?lens=no_attention_needed&subview=history&field=cover"
        )
        page.get_by_role(
            "heading", name="This diagnostic case is no longer current."
        ).wait_for()
        stale_route = page.locator(".diagnostic-route")
        stale_back = page.get_by_role("link", name="Back to work", exact=True)
        assert stale_route.locator(":scope > *").first.evaluate(
            "node => node === document.querySelector('.diagnostic-back-to-work')"
        )
        assert stale_back.get_attribute("href") == (
            "/library?lens=no_attention_needed&preview=case-stale&field=cover"
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 320

        diagnostic_error_start = len(errors)
        page.goto(
            "https://react-platform.invalid/review/missing-case?lens=needs_attention&subview=evidence&field=authors"
        )
        page.get_by_role("heading", name="Work diagnostics are unavailable.").wait_for()
        failed_route = page.locator(".diagnostic-route")
        failed_back = page.get_by_role("link", name="Back to work", exact=True)
        assert failed_route.locator(":scope > *").first.evaluate(
            "node => node === document.querySelector('.diagnostic-back-to-work')"
        )
        assert failed_back.get_attribute("href") == (
            "/library?lens=needs_attention&preview=missing-case&field=authors"
        )
        assert (
            page.get_by_role("button", name=re.compile("retry", re.IGNORECASE)).count()
            == 0
        )
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 320
        diagnostic_errors = errors[diagnostic_error_start:]
        assert len(diagnostic_errors) == 1
        assert "404 (Not Found)" in diagnostic_errors[0]
        del errors[diagnostic_error_start:]
        page.set_viewport_size({"width": 1440, "height": 900})

        stage[0] = "library:return-to-automatic"
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-4&field=title"
        )
        page.get_by_role("button", name="Resume automatic handling").wait_for()
        capture_visual_proof("library-manual-handling-collapsed")
        page.get_by_role("button", name="Resume automatic handling").click()
        page.get_by_role(
            "heading", name="Automatic handling is paused for this field"
        ).wait_for()
        expect(page.get_by_role("button", name="Keep manual handling")).to_be_focused()
        capture_visual_proof("library-manual-handling-confirmation")
        page.get_by_role("button", name="Keep manual handling").focus()
        page.keyboard.press("Escape")
        expect(
            page.get_by_role("button", name="Resume automatic handling")
        ).to_be_focused()
        assert return_automatic_requests == 0
        page.get_by_role("button", name="Resume automatic handling").click()
        page.get_by_role("button", name="Resume after fresh checks").click()
        page.get_by_text(
            "Fresh checks requested. Manual handling remains until they complete.",
            exact=True,
        ).wait_for()
        assert return_automatic_requests == 1
        page.keyboard.press("Escape")
        expect(
            page.get_by_role("button", name="Fresh checks requested")
        ).to_be_focused()
        expect(
            page.get_by_role("button", name="Fresh checks requested")
        ).to_have_attribute("aria-disabled", "true")
        assert return_automatic_requests == 1
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.get_by_role("heading", name="Evidence").count() == 0
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        for control in page.locator(".masthead nav a, .shell-actions button").all():
            assert control.bounding_box()["height"] >= 44
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390

        stage[0] = "library:description-choice"
        description_mode = True
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=description"
        )
        summary = page.locator(
            ".metadata-choice.is-current .choice-description summary"
        )
        summary.wait_for()
        page.set_viewport_size({"width": 1440, "height": 900})
        single_proposal = page.locator(
            ".proposed-choice-list.has-one-choice .metadata-choice"
        )
        expect(single_proposal).to_have_count(1)
        current_choice = page.locator(".metadata-choice.is-current")
        assert (
            abs(
                (
                    single_proposal.bounding_box()["y"]
                    + single_proposal.bounding_box()["height"] / 2
                )
                - (
                    current_choice.bounding_box()["y"]
                    + current_choice.bounding_box()["height"] / 2
                )
            )
            <= 1
        )
        for width, height in ((1440, 900), (900, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        page.locator("html").evaluate("node => { node.style.fontSize = '200%' }")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        page.locator("html").evaluate("node => { node.style.fontSize = '' }")
        before_description_choice = ambiguous_decisions
        summary.click()
        assert ambiguous_decisions == before_description_choice
        capture_visual_proof("library-long-description")
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Choose current full description"
        )
        full_description.wait_for()
        decide(
            "library long-description pointer",
            before_description_choice + 1,
            full_description.click,
        )
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=description"
        )
        summary = page.locator(
            ".metadata-choice.is-current .choice-description summary"
        )
        summary.wait_for()
        summary.click()
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Choose current full description"
        )
        full_description.wait_for()
        full_description.focus()
        decide(
            "library long-description Enter",
            before_description_choice + 2,
            lambda: page.keyboard.press("Enter"),
        )
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=description"
        )
        summary = page.locator(
            ".metadata-choice.is-current .choice-description summary"
        )
        summary.wait_for()
        summary.click()
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Choose current full description"
        )
        full_description.wait_for()
        full_description.focus()
        decide(
            "library long-description Space",
            before_description_choice + 3,
            lambda: page.keyboard.press(" "),
        )
        description_mode = False
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=title"
        )
        stacked = page.locator(
            ".proposed-choice-list:not(.has-one-choice) .metadata-choice"
        )
        expect(stacked).to_have_count(3)
        assert stacked.nth(0).bounding_box()["y"] < stacked.nth(1).bounding_box()["y"]

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
        page.get_by_role("heading", name="Works", exact=True).wait_for()
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
        page.get_by_role("heading", name="Works", exact=True).wait_for()
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
            "Choose the best identity-confirmed cover by one deterministic order.",
            exact=True,
        ).wait_for()
        automatic_cover = cover_policy.get_by_role("button", name="Automatic")
        expect(automatic_cover).to_be_enabled()
        expect(cover_policy.get_by_role("button", name="Ask me")).to_have_attribute(
            "aria-pressed", "true"
        )
        cover_representation = cover_policy.get_by_role(
            "combobox", name="Cover representation"
        )
        expect(cover_representation).to_be_disabled()
        expect(cover_representation).to_have_text("Deterministic confirmed order")
        automatic_cover.click()
        expect(automatic_cover).to_have_attribute("aria-pressed", "true")
        cover_policy.get_by_role("button", name="Ask me").click()
        expect(cover_policy.get_by_role("button", name="Ask me")).to_have_attribute(
            "aria-pressed", "true"
        )
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
        subtitle_policy = page.get_by_role("region", name="Subtitle", exact=True)
        expect(subtitle_policy.get_by_role("button", name="Automatic")).to_be_enabled()
        assert "Automatic mode not supported" not in subtitle_policy.inner_text()
        expect(
            subtitle_policy.get_by_role("combobox", name="Subtitle representation")
        ).to_have_text("Fill only when empty")
        capture_visual_proof("policy-subtitle-automatic")
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
