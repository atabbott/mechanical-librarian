from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast
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
    synthetic_preview,
    synthetic_preview_key,
)
from tests.browser.test_live_updates_browser import _ROW_PROMISES, _WIDER_TEXT
from tests.browser.test_review_browser import _FOCUSED, _POLICY_MID_WORD, _TWO_FRAMES
from tests.browser.text_size import set_root_text
from tests.support.cover_images import synthetic_image

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


def _asset_type(relative: str) -> str:
    """The review server's media type for one bundle asset."""
    return {"css": "text/css", "woff2": "font/woff2"}.get(
        relative.rpartition(".")[2], "text/javascript"
    )


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
    genres_reappeared = False
    failed_requests: list[tuple[str, str, str | None]] = []

    def fulfill(route: Route) -> None:
        nonlocal activity_failures, ambiguous_decisions, documents, transition_failures
        nonlocal connection_state, genres_reappeared, return_automatic_requests
        parsed = urlsplit(route.request.url)
        path = parsed.path
        if (
            path.startswith("/api/v1/review-cases/")
            and "/cover-considerations/" in path
            and "/images/" in path
        ):
            route.fulfill(
                status=200,
                content_type="image/png",
                body=synthetic_image("PNG", (120, 180)),
            )
            return
        if path == "/api/v1/library/cover":
            key = synthetic_preview_key(path, parsed.query)
            assert key is not None, route.request.url
            route.fulfill(
                status=200, content_type="image/png", body=synthetic_preview(key)
            )
            return
        if path.startswith("/api/v1/"):
            if path == "/api/v1/reviewer-access" and route.request.method == "GET":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "schema_version": "reviewer-access-view-v2",
                            "revision": 1,
                            "mapping": {"engine": "codex"},
                            "description_mapping": None,
                            "description_metadata": None,
                            "cover_metadata": None,
                            "cost_evidence": {
                                "schema_version": "reviewer-cost-evidence-v2",
                                "models": [],
                            },
                            "description": None,
                            "openrouter_key_configured": False,
                            "codex": {
                                "engine": "codex",
                                "state": "ready",
                                "reason": None,
                                "model": "gpt-5.6-sol",
                                "tier": None,
                                "host_headroom_bytes": 2147483648,
                            },
                            "openrouter": {
                                "engine": "openrouter",
                                "state": "unconfigured",
                                "reason": None,
                                "model": None,
                                "tier": None,
                                "host_headroom_bytes": None,
                            },
                            "recommendations": [],
                            "candidates": [],
                            "free_tier_caveat": "Synthetic reviewer capability.",
                            "data_use_caveat": "Synthetic cover evidence only.",
                        }
                    ),
                )
                return
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
                # No stream is available in this stubbed cohort. A 204 closes
                # EventSource without successful opens or resource-error noise;
                # the client retains its degraded fallback and explicit retries.
                route.fulfill(status=204, body="")
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
            if genres_reappeared and path == "/api/v1/review-cases/case-1":
                detail = cast("dict[str, object]", payload)
                fields = cast("list[dict[str, object]]", detail["fields"])
                genres = next(
                    field for field in fields if field["destination"] == "genres"
                )
                genres["current"] = {
                    "kind": "synthetic",
                    "state": "value",
                    "value": ["Updated genre"],
                }
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
                    "current": {
                        "kind": "synthetic",
                        "state": "value",
                        "value": description,
                    },
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
            route.fulfill(
                status=200,
                content_type=_asset_type(relative),
                body=bundle.read(relative),
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
                    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
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
                    // Lenses scroll sideways inside their own row, so the row,
                    // not each lens, must stay within the panel's border.
                    const lenses = rail.querySelector('.lenses')?.getBoundingClientRect()
                    const lensOverrun = !lenses || lenses.left < box.left || lenses.right > box.right
                        ? [['lenses', 'overrun the panel', 0, 0, 0, 0]] : []
                    return lensOverrun.concat([...rail.querySelectorAll(
                        `.library-search input, .library-search button,
                         .library-pager button, .library-work-title,
                         .library-work-author, .library-work-origin,
                         .library-works-heading > *`)]
                        .map(node => [node, node.getBoundingClientRect()])
                        .filter(([, r]) => r.width > 0 &&
                            (r.left < left - 0.5 || r.right > right + 0.5))
                        .map(([node, r]) => [node.className || node.tagName,
                            (node.textContent || '').trim().slice(0, 28),
                            Math.round(r.left), Math.round(r.right),
                            Math.round(left), Math.round(right)]))
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
                    (1025, 3),
                    (1024, 2),
                    (761, 2),
                    (760, 1),
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
                    _fits_its_container(page, ".shell-status", width)
                    _fits_its_container(page, ".shell-actions", width)
                    _fits_its_container(page, ".masthead", width)
                for width in (1440, 900, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    set_root_text(page, "200%")
                    _fits_its_container(page, ".connection-notice", width)
                    set_root_text(page, "")
                if path == "/library?lens=all":
                    check.click()
                    page.get_by_text(
                        "Library connection recovered", exact=True
                    ).wait_for()
                    recovered = page.get_by_label("Shared library connection status")
                    capture_visual_proof("connection-recovered")
                    for width in (1440, 900, 390):
                        page.set_viewport_size({"width": width, "height": 900})
                        set_root_text(page, "200%")
                        _fits_its_container(page, ".connection-notice", width)
                        set_root_text(page, "")
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
            if path.startswith("/policy"):
                section = path.rsplit("=", 1)[-1]
                expect(page.locator(f"#policy-{section}")).to_be_focused()
                expect(
                    page.get_by_role("navigation", name="Policy chapters").locator(
                        "button[aria-current='page']"
                    )
                ).to_have_count(1)
                assert page.url.endswith(path), f"{path} restored as {page.url}"
                if section == "unsupported":
                    # The review-mode note wraps whole and never pushes an
                    # option past its card's clipped edge (#1013).
                    flag = page.get_by_role("region", name="Explicit-content flag")
                    note = flag.locator(".cm-policy-control-heading small")
                    expect(note).to_have_text(
                        "Off until you choose a review mode · Automatic not supported"
                    )
                    options = flag.get_by_role(
                        "group", name="Explicit-content flag review mode"
                    ).get_by_role("button")
                    expect(options).to_have_count(3)
                    for width in (1440, 1024, 768, 390, 320):
                        page.set_viewport_size({"width": width, "height": 844})
                        assert note.evaluate(
                            "node => node.scrollWidth <= node.clientWidth"
                        ), f"review-mode note truncated at {width}px"
                        _fits_its_container(
                            page,
                            ".cm-policy-field:has(.cm-policy-control-heading small)",
                            width,
                        )
                        for option in options.all():
                            assert option.evaluate(
                                """node => { const box = node.getBoundingClientRect();
                                for (let a = node.parentElement; a; a = a.parentElement) {
                                  if (getComputedStyle(a).overflowX === 'visible') continue;
                                  const edge = a.getBoundingClientRect();
                                  if (box.left < edge.left - 0.5 || box.right > edge.right + 0.5)
                                    return false;
                                }
                                return true; }"""
                            ), f"{option.inner_text()} clipped at {width}px"
                    page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
            # The one image a page may hold is a work's retained cover mark
            # (#1290); hostile text never becomes markup.
            assert (
                page.locator(
                    "img:not(.library-book-mark.has-cover > img), script:not([src])"
                ).count()
                == 0
            )
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
                for work, label in (
                    ("Equal automatic title", "No change required"),
                    ("Synthetic completed cover advice", "Cover advice completed"),
                    ("Synthetic unavailable cover advice", "Cover advice unavailable"),
                    ("Synthetic failed cover advice", "Cover advice failed safely"),
                    ("Synthetic stale cover advice", "Closed safely"),
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
                # The time track is as narrow as its 44 px floor allows and
                # never narrower than the time it holds, whatever the fonts.
                # The text is measured, not the element: a stretched grid item
                # is as wide as its track, which would make the bound vacuous.
                track = float(
                    compact_row.evaluate(
                        "node => getComputedStyle(node).gridTemplateColumns"
                    )
                    .split(" ", 1)[0]
                    .removesuffix("px")
                )
                content = compact_row.locator(":scope > time").evaluate(
                    """node => {
                      const text = document.createRange();
                      text.selectNodeContents(node);
                      return Math.max(text.getBoundingClientRect().width,
                        parseFloat(getComputedStyle(node).minWidth));
                    }"""
                )
                assert 44 <= track, track
                assert content - 1 <= track <= max(44, content) + 1, (track, content)
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
                        "button", name=re.compile(r"^Details for ")
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
                inspect = page.get_by_role(
                    "button", name=re.compile(r"^Details for ")
                ).first
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
                # A full 2.75rem target, no taller (#1491).
                assert (
                    inspect.evaluate("node => getComputedStyle(node).minHeight")
                    == "44px"
                )
                assert inspect_box["height"] <= 44.5
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
                            ".masthead nav a, .shell-status button, .shell-actions button"
                        ).all():
                            assert control.bounding_box()["height"] >= 44
                        # Authority controls and disclosures take no dense
                        # exception (#1288).
                        full = [
                            control
                            for control in page.locator(
                                ".cm-activity-authority-actions button, summary"
                            ).all()
                            if control.is_visible()
                        ]
                        assert full, f"{scheme} {width}: no authority controls"
                        short = [
                            (control.inner_text(), box["height"])
                            for control in full
                            if (box := control.bounding_box()) and box["height"] < 44
                        ]
                        assert short == [], f"{scheme} {width}: {short}"
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
                # One entry, in the status section rather than the disclosure.
                entry = page.get_by_role("button", name="Review write permissions")
                expect(entry).to_have_count(1)
                assert entry.evaluate("node => node.closest('details') === null")
                entry.click()
                page.get_by_role("heading", name="Enable guarded writes").wait_for()
                page.locator(".activity-consent-library").filter(
                    has_text="Household ebooks"
                ).get_by_text("5 prepared · Writes enabled").wait_for()
                spoken = (
                    page.locator(".activity-consent-library")
                    .filter(has_text="Spoken stories")
                    .get_by_role("checkbox")
                )
                spoken.check()
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
                        "combobox", name="Events per activity page"
                    )
                    page_size.click()
                    page.get_by_role("option", name="50 per page").click()
                    expect(page_size).to_be_focused()
                page.get_by_role("button", name="Owner choices").click()
                # Exact: a row's Details name carries its work title (#1286).
                page.get_by_role("button", name="All", exact=True).click()
                inspect_control = page.get_by_role(
                    "button", name=re.compile(r"^Details for ")
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
                # The history controls above refetch Activity without changing
                # its consent basis, so the reviewed draft and its tick survive.
                expect(page.locator(".activity-consent-library")).to_have_count(3)
                expect(spoken).to_be_checked()
                expect(
                    page.get_by_role("button", name="Confirm write permissions")
                ).to_be_enabled()
                assert consent_payloads == []
                stage[0] = "activity:consent-refresh"
                page.get_by_role("button", name="Confirm write permissions").click()
                page.get_by_text(
                    "Activity state refreshed from the authoritative server."
                ).wait_for()
                connection_state = "paused"
            stage[0] = f"direct-route:{path}:document-count"
            assert documents == (before_entry + (4 if path == "/activity" else 1))

        for case_id, label, retry in (
            ("case-1", "Advisory visual review complete", False),
            (
                "case-2",
                "Visual review is unavailable. Only the eligible choices shown below can be selected.",
                True,
            ),
            (
                "case-3",
                "Visual review is unavailable. Only the eligible choices shown below can be selected.",
                True,
            ),
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
            for choice in comparison.locator("[role=radio]").all():
                expect(choice).to_be_enabled()
                expect(choice).to_have_attribute("aria-checked", "false")
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
                unconfirmed_choice = unconfirmed.get_by_role(
                    "radio", name="Choose Open Library cover", exact=True
                )
                # The card states the unconfirmed match before any tap, and a
                # tap on it is the explicit choice; no dialog asks again (#1290).
                expect(unconfirmed_choice).to_be_enabled()
                expect(unconfirmed_choice).to_contain_text("Match not confirmed")
                expect(page.get_by_role("dialog")).to_have_count(0)
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
                ).to_have_attribute("aria-checked", "false")
                alternative_evidence = comparison.get_by_role(
                    "button", name="Evidence for Hardcover cover"
                )
                alternative_evidence.focus()
                page.keyboard.press("Enter")
                expect(alternative_evidence).to_have_count(1)
                expect(alternative_evidence).to_have_attribute("aria-expanded", "true")
                expect(alternative_evidence).to_have_text("Hide evidence")
                comparison.get_by_label("Hardcover cover evidence").get_by_text(
                    "Open Library", exact=True
                ).wait_for()
                comparison.get_by_text("External corroboration", exact=True).wait_for()
                expect(
                    comparison.locator(".library-cover-card.is-recommended")
                ).to_have_attribute("aria-checked", "false")
            if case_id == "case-3":
                recommended = comparison.locator(".library-cover-card.is-recommended")
                expect(recommended).to_have_count(1)
                expect(recommended).to_be_enabled()
                recommended.get_by_text("Same book", exact=True).wait_for()
                recommended_evidence = comparison.get_by_role(
                    "button", name="Evidence for Hardcover cover"
                )
                recommended_evidence.click()
                expect(recommended_evidence).to_have_attribute("aria-expanded", "true")
                comparison.get_by_label("Hardcover cover evidence").get_by_text(
                    "Open Library", exact=True
                ).wait_for()
                unavailable_evidence = comparison.get_by_role(
                    "button", name="Evidence for Current cover"
                )
                unavailable_evidence.focus()
                page.keyboard.press("Enter")
                expect(unavailable_evidence).to_have_count(1)
                expect(unavailable_evidence).to_have_attribute("aria-expanded", "true")
                expect(unavailable_evidence).to_have_text("Hide evidence")
                comparison.get_by_text("Local presence", exact=True).wait_for()
                comparison.get_by_text(
                    "Preview unavailable for this retained cover.", exact=True
                ).wait_for()

        stage[0] = "library:automatic-isbn-identity-evidence"
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-5&field=isbn"
        )
        recorded_isbn = page.get_by_role("region", name="ISBN recorded choices")
        recorded_isbn.get_by_text(
            "Deterministic identity evidence bound this ISBN to this file's work.",
            exact=True,
        ).wait_for()
        evidence = recorded_isbn.get_by_role(
            "button", name="Evidence for proposed value: 9780306406157"
        )
        evidence.click()
        recorded_isbn.locator(".choice-evidence").get_by_text(
            "Open Library", exact=True
        ).wait_for()

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
        # #598 keeps recorded automatic fields primary. #890 keeps an
        # unavailable, unproposed primary field inspectable without making it
        # a default rail distraction or a false choice.
        expect(page.locator(".field-filmstrip button")).to_have_count(6)
        assert "Title" in page.locator(".field-filmstrip").inner_text()
        assert "Cover" in page.locator(".field-filmstrip").inner_text()
        assert "Series" in page.locator(".field-filmstrip").inner_text()
        assert "Publication date" in page.locator(".field-filmstrip").inner_text()
        genres = page.get_by_role("button", name="Show 1 more field")
        expect(genres).to_be_visible()
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
        genres.click()
        expect(page.get_by_role("button", name=re.compile(r"^Genres"))).to_be_focused()
        assert [
            label.splitlines()[0]
            for label in page.locator(".field-filmstrip button").all_inner_texts()
        ] == ["Title", "Authors", "Genres", "Series", "Publication date", "Cover"]
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= window.innerWidth"
        )
        assert all(
            button.bounding_box()["height"] >= 44
            for button in page.locator(".field-filmstrip button").all()
        )
        genres_reappeared = True
        page.reload()
        page.locator(".field-filmstrip").wait_for()
        expect(page.get_by_role("button", name="Show 1 more field")).to_have_count(0)
        assert [
            label.splitlines()[0]
            for label in page.locator(".field-filmstrip button").all_inner_texts()
        ] == ["Title", "Authors", "Genres", "Series", "Publication date", "Cover"]
        genres_reappeared = False
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
            not in page.locator(".metadata-choice.is-current").inner_text()
        )
        proposed_evidence = page.get_by_role(
            "button",
            name="Evidence for proposed value: The Clockmaker's Cartography",
            exact=True,
        )
        proposed_evidence.focus()
        page.keyboard.press("Enter")
        assert "Open Library" in page.locator(".choice-evidence").nth(1).inner_text()
        assert page.locator(".source-ledger").count() == 0
        page.goto("https://react-platform.invalid/library?lens=all&preview=case-2")
        expect(page.locator(".choice-evidence")).to_have_count(0)
        page.goto("https://react-platform.invalid/library?lens=all&preview=case-1")
        # A guarded field with no recorded change has no library card (#1519).
        expect(page.locator(".field-apply-context")).to_have_count(0)
        # The workspace never grows a field stepper. The Library pager is a
        # separate, list-scoped control and is asserted where the list is.
        detail_workspace = page.locator(".workspace-detail")
        assert detail_workspace.get_by_role("button", name="Previous").count() == 0
        assert detail_workspace.get_by_role("button", name="Next").count() == 0
        expect(page.get_by_role("button", name="Back to works")).to_be_hidden()
        expect(page.get_by_role("button", name="Attention 5 works")).to_be_visible()
        page.get_by_role("button", name="Attention 5 works").click()
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
        page.get_by_text("Household ebooks · Ebook (EPUB)", exact=True).first.wait_for()
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
            "2 lookups recorded for this work"
            in work_detail.locator(".work-sources").inner_text()
        )
        # Browsing is reversible: the same pager walks the bounded window back.
        page.get_by_role("button", name="\u2190 Previous").click()
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        page.get_by_text("Page 1", exact=False).wait_for()
        capture_visual_proof("library-pager")
        # The first page offers no way back (#1520).
        assert page.get_by_role("button", name="\u2190 Previous").count() == 0
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
        # Three works are one page, which has no pager (#1520).
        expect(page.locator(".library-pager")).to_have_count(0)
        assert "cursor=" not in page.url
        assert search_box.input_value() == "Spoken stories"
        search_box.fill("no synthetic work has this phrase")
        page.get_by_text(
            "No exact-current works match this search and lens."
        ).wait_for()
        page.get_by_role("button", name="Clear search", exact=True).click()
        page.get_by_text("Catalogue-only work", exact=True).wait_for()
        assert search_box.input_value() == ""
        stage[0] = "library:touch-targets"
        page.goto("https://react-platform.invalid/library?lens=all")
        page.locator(".lenses a").first.wait_for()
        for control in page.locator(
            ".masthead nav a, .shell-status button, .shell-actions button"
        ).all():
            assert control.bounding_box()["height"] >= 44
        for control in page.locator(
            ".lenses a, .library-work-row, .library-pager button"
        ).all():
            assert control.bounding_box()["height"] >= 44
        for lens in page.locator(".lenses a").all():
            assert lens.bounding_box()["width"] >= 44
        # The lenses are one row that scrolls sideways, its far edge faded (#1520).
        lens_row = page.evaluate(
            """() => {
              const row = document.querySelector('.lenses')
              const tops = [...row.querySelectorAll('a')].map(a => a.getBoundingClientRect().top)
              return {rows: new Set(tops).size, scrolls: row.scrollWidth > row.clientWidth,
                      more: row.dataset.more}
            }"""
        )
        assert lens_row == {"rows": 1, "scrolls": True, "more": "end"}, lens_row
        page.locator(".library-work-row").first.wait_for()
        assert overrunning_rail_controls() == []
        assert page.evaluate(_SHORT_SUMMARIES) == []
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
        # The ring is Focus Blue itself, unmixed, in both themes (#1288). The
        # theme is set on the root directly so the focused row keeps focus.
        theme = page.evaluate("() => document.documentElement.dataset.theme")
        for scheme in ("dark", "light", theme):
            ring, focus = page.evaluate(
                """scheme => {
                  document.documentElement.dataset.theme = scheme;
                  const probe = document.createElement('i');
                  probe.style.color = 'var(--focus)';
                  document.body.append(probe);
                  const focus = getComputedStyle(probe).color;
                  probe.remove();
                  return [getComputedStyle(document.activeElement).outlineColor, focus];
                }""",
                scheme,
            )
            assert ring == focus, f"{scheme}: ring {ring}, Focus Blue {focus}"
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
        # The destinations take a row of their own below the brand's.
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
        page.get_by_role("button", name="Attention 5 works").click()
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
        expect(page.get_by_role("button", name="Attention 5 works")).to_be_visible()

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
        expect(page.get_by_role("button", name="Attention 5 works")).to_be_visible()
        assert (
            page.locator(".library-workspace").evaluate(
                "node => getComputedStyle(node).display"
            )
            != "block"
        )
        page.get_by_role("button", name="Attention 5 works").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".attention-rail h2")).to_be_focused()
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
            set_root_text(page, "200%")
            page.locator(".library-work-row").first.wait_for()
            assert overrunning_rail_controls() == [], zoom_width
            set_root_text(page, "")
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto("https://react-platform.invalid/library?lens=all")
        page.locator(".library-work-row").first.wait_for()
        page.locator(".attention-toggle").click()
        expect(page.locator(".attention-rail")).to_be_visible()
        expect(page.locator(".attention-rail h2")).to_be_focused()
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".workspace-detail")).to_be_hidden()
        page.locator(".attention-queue-item").first.click()
        expect(page.locator(".workspace-detail")).to_be_visible()
        expect(page.locator(".library-works")).to_be_hidden()
        expect(page.locator(".field-filmstrip button")).to_have_count(6)
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
        compact_genres = page.get_by_role("button", name="Show 1 more field")
        expect(compact_genres).to_be_visible()
        # The more-fields tab reads on one line and is never clipped, down to
        # 320 px (#1290).
        for more_width in (390, 320, 390):
            page.set_viewport_size({"width": more_width, "height": 844})
            compact_genres.scroll_into_view_if_needed()
            fit = compact_genres.evaluate(
                """button => {
                    const range = document.createRange()
                    range.selectNodeContents(button)
                    const lines = new Set(
                        [...range.getClientRects()].map((rect) => Math.round(rect.top))
                    ).size
                    const rail = button.closest('.field-filmstrip').getBoundingClientRect()
                    const box = button.getBoundingClientRect()
                    return {
                        lines,
                        overflow: button.scrollWidth - button.clientWidth,
                        inside: box.left >= rail.left - 0.5 && box.right <= rail.right + 0.5,
                    }
                }"""
            )
            assert fit == {"lines": 1, "overflow": 0, "inside": True}, (more_width, fit)
        compact_genres.click()
        revealed_genres = page.get_by_role("button", name=re.compile(r"^Genres"))
        expect(revealed_genres).to_be_focused()
        page.wait_for_timeout(500)
        rail_box = page.locator(".field-filmstrip").bounding_box()
        genres_box = revealed_genres.bounding_box()
        assert rail_box is not None and genres_box is not None
        assert genres_box["x"] >= rail_box["x"]
        assert (
            genres_box["x"] + genres_box["width"] <= rail_box["x"] + rail_box["width"]
        )
        compact_evidence = page.get_by_role(
            "button", name="Evidence for Current value: Current <script> title"
        )
        assert compact_evidence.bounding_box()["height"] >= 44
        compact_evidence.click()
        expect(page.locator("#choice-evidence-current")).to_be_visible()
        # A guarded field with no recorded change has no library card (#1519).
        expect(page.locator(".field-apply-context")).to_have_count(0)
        assert page.get_by_role("button", name="Previous").count() == 0
        assert page.get_by_role("button", name="Next").count() == 0
        # One toolbar in both states; Back to works leads the workspace (#1284).
        expect(page.locator(".library-header button")).to_have_count(1)
        expect(
            page.locator(".library-workspace > .compact-workspace-back:first-child")
        ).to_be_visible()
        expect(page.get_by_role("button", name="Back to works")).to_be_visible()
        for control in (
            page.locator(".library-header button").all()
            + page.locator(".compact-workspace-back").all()
        ):
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
            "https://react-platform.invalid/review/case-1?field=title&lens=needs_attention&subview=evidence"
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
        assert "Household ebooks · Ebook (EPUB)" in diagnostic_text
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
        assert page.get_by_role("radio").count() == 0
        load_more = page.get_by_role("button", name="Load more evidence")
        assert load_more.bounding_box()["height"] >= 44
        load_more.click()
        expect(load_more).to_have_count(0)
        assert page.locator(".diagnostic-entry").count() == 7
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
                set_root_text(page, "200%")
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"
                ), (active_page, scheme, "200%")
                assert back.bounding_box()["height"] >= 44
                for link in nav.get_by_role("link").all():
                    assert link.bounding_box()["height"] >= 44
                assert_keyboard_path()
                set_root_text(page, "")
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
        assert "Cover choices prepared" in history_text
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
        assert page.get_by_role("radio").count() == 0
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
        for control in page.locator(
            ".masthead nav a, .shell-status button, .shell-actions button"
        ).all():
            assert control.bounding_box()["height"] >= 44
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390

        stage[0] = "library:description-choice"
        description_mode = True
        page.goto(
            "https://react-platform.invalid/library?lens=all&preview=case-1&field=description"
        )
        summary = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Read full description", exact=True
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
        set_root_text(page, "200%")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        set_root_text(page, "")
        before_description_choice = ambiguous_decisions
        summary.click()
        expect(
            page.locator(".metadata-choice.is-current").get_by_role(
                "button", name="Show less description", exact=True
            )
        ).to_have_attribute("aria-expanded", "true")
        expect(
            page.locator(".metadata-choice.is-current .description-value p")
        ).to_have_count(1)
        assert ambiguous_decisions == before_description_choice
        capture_visual_proof("library-long-description")
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "radio", name="Choose current description"
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
        summary = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Read full description", exact=True
        )
        summary.wait_for()
        summary.click()
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "radio", name="Choose current description"
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
        summary = page.locator(".metadata-choice.is-current").get_by_role(
            "button", name="Read full description", exact=True
        )
        summary.wait_for()
        summary.click()
        full_description = page.locator(".metadata-choice.is-current").get_by_role(
            "radio", name="Choose current description"
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
        # Six field rows, grouped under four works (#1284).
        assert page.locator(".attention-queue-item").count() == 6
        assert page.locator(".attention-work-group").count() == 4
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
        assert page.evaluate(_SHORT_SUMMARIES) == []
        cover_policy = page.get_by_role("region", name="Cover")
        cover_policy.get_by_text(
            "Use a confirmed cover. Audiobooks keep an adequate cover and replace only a small incumbent with a materially larger match.",
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
        expect(cover_representation).to_be_enabled()
        expect(cover_representation).to_have_text("Fill from confirmed order")
        cover_representation.click()
        expect(page.get_by_role("option")).to_have_text(
            ["Fill from confirmed order", "Replace with a material improvement"]
        )
        page.get_by_role("option", name="Fill from confirmed order", exact=True).click()
        automatic_cover.click()
        expect(automatic_cover).to_have_attribute("aria-pressed", "true")
        cover_policy.get_by_role("button", name="Ask me").click()
        expect(cover_policy.get_by_role("button", name="Ask me")).to_have_attribute(
            "aria-pressed", "true"
        )
        cover_help = cover_policy.locator("details").filter(
            has=page.get_by_text("What this changes", exact=True)
        )
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
        assert "Automatic not supported" not in subtitle_policy.inner_text()
        expect(
            subtitle_policy.get_by_role("combobox", name="Subtitle representation")
        ).to_have_text("Fill only when empty")
        capture_visual_proof("policy-subtitle-automatic")
        page.locator(".cm-finish-tray").get_by_text(
            "Draft matches the active policy", exact=True
        ).wait_for()
        page.locator(".cm-policy-mode summary").click()
        page.get_by_text(
            "Preview records what Automatic would do; nothing is decided or written.",
            exact=True,
        ).wait_for()
        page.get_by_text(
            "2 recent preview results; none was decided or written.", exact=True
        ).wait_for()
        page.get_by_role(
            "heading", name="Activation completed with failures"
        ).wait_for()
        page.get_by_role("button", name="Retry failed reconciliation").wait_for()
        page.get_by_role("button", name="Recover Title suspension").wait_for()
        page.get_by_role("region", name="Title and subtitle").get_by_role(
            "button", name="Ignore"
        ).click()
        page.get_by_text("Draft has unreviewed changes", exact=False).first.wait_for()
        page.get_by_role("button", name="Validate and preview").click()
        page.get_by_text("Impact preview is current.", exact=False).wait_for()
        # Focus follows the command to its result instead of falling to the
        # page while it validates (#1288).
        expect(page.locator(".cm-policy-result")).to_be_focused()
        primary = page.locator(".cm-policy-preview-list")
        assert primary.locator(":scope > div").count() == 6
        assert (
            primary.locator(".cm-policy-impact > b").all_text_contents()
            == ["Policy change"] * 6
        )
        expect(primary.get_by_text("Tag handling is off.", exact=False)).to_have_count(
            0
        )
        page.get_by_text(
            "Ebooks: 1 sampled work · 6 changed fields", exact=False
        ).wait_for()
        expect(page.get_by_role("button", name="Show 2 more outcomes")).to_have_count(0)
        projected_context = page.locator(".cm-policy-projected-context")
        assert projected_context.evaluate("node => node.open") is False
        projected_context.get_by_text(
            "Unchanged outcomes and current library context (2)", exact=True
        ).click()
        assert projected_context.evaluate("node => node.open") is True
        expect(
            projected_context.get_by_text(re.compile(r"^Policy change\."))
        ).to_have_count(0)
        expect(
            projected_context.get_by_text("Safe visible description <3", exact=False)
        ).to_have_count(0)
        projected_context.get_by_text(
            "No policy change. Tag handling is off.", exact=False
        ).wait_for()
        projected_context.get_by_text(
            "Unchanged outcomes and current library context (2)", exact=True
        ).click()
        assert projected_context.evaluate("node => node.open") is False
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


_NAMED_CLIP = """node => {
  const clipped = node.scrollWidth - node.clientWidth;
  const box = node.getBoundingClientRect();
  const viewport = document.documentElement.clientWidth;
  const name = `${node.className || node.tagName} "${node.textContent.trim().slice(0, 32)}"`;
  return [
    ...(clipped > 0 ? [`${name} is clipped by ${clipped}px`] : []),
    ...(box.left < 0 || box.right > viewport ? [`${name} is off screen`] : []),
  ];
}"""


# Every Activity row's category pill and time, named by the row they belong to.
# A pill may wrap between words but never inside one, and never runs out of its
# box; a compact time track is never narrower than the time or day it holds;
# and no cell runs past the row that holds it.
_ROW_LABELS = """(compact) => [...document.querySelectorAll('.cm-activity-event-group article')]
  .flatMap(row => {
    const work = row.querySelector('.cm-activity-work');
    const name = `${work?.querySelector('strong')?.textContent} (${work?.querySelector('span')?.textContent})`;
    const found = [];
    const pill = row.querySelector('.cm-activity-category');
    const walker = document.createTreeWalker(pill, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/\\S+/g)) {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        const tops = new Set([...range.getClientRects()].filter(rect => rect.width > 0)
          .map(rect => Math.round(rect.top)));
        if (tops.size > 1) found.push(`the ${name} pill splits "${match[0]}"`);
      }
    const words = document.createRange();
    words.selectNodeContents(pill);
    if (pill.scrollWidth > pill.clientWidth ||
        words.getBoundingClientRect().right > pill.getBoundingClientRect().right + 1)
      found.push(`the ${name} pill "${pill.textContent}" runs out of its box`);
    const style = getComputedStyle(row);
    const edge = row.getBoundingClientRect().right - parseFloat(style.borderRightWidth) -
      parseFloat(style.paddingRight);
    for (const cell of row.children) {
      const past = Math.round(cell.getBoundingClientRect().right - edge);
      if (past > 1)
        found.push(`the ${name} ${cell.className || cell.tagName.toLowerCase()} ` +
          `"${cell.textContent.trim().slice(0, 24)}" runs ${past}px past its row`);
    }
    const time = row.querySelector(':scope > time');
    if (compact && time.scrollWidth > time.clientWidth)
      found.push(`the ${name} time "${time.textContent}" is ` +
        `${time.scrollWidth - time.clientWidth}px wider than its track`);
    return found;
  })"""


def test_activity_gathers_a_preparations_checks_and_names_an_unknown_filter() -> None:
    """One preparation's automatic checks read as one row that opens onto each
    check, and an address naming no known filter says so (#1281)."""
    bundle = load_react_asset_bundle(Path(os.environ["ML_REACT_ASSET_DIR"]))
    assert isinstance(bundle, ReactAssetBundle)
    widened = False
    checks_gone = False

    def fulfill(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/api/v1/updates":
            route.fulfill(status=204, body="")
        elif path == "/api/v1/appearance":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"reduced_motion": "follow_system"}),
            )
        elif (
            preview := synthetic_preview_key(path, urlsplit(route.request.url).query)
        ) is not None:
            route.fulfill(content_type="image/png", body=synthetic_preview(preview))
        elif path.startswith("/api/v1/"):
            payload = rich_payload(route.request.url)
            if checks_gone and path == "/api/v1/activity":
                activity = cast("dict[str, list[dict[str, object]]]", payload)
                activity["events"] = [
                    event for event in activity["events"] if not event["check_group"]
                ]
            route.fulfill(content_type="application/json", body=json.dumps(payload))
        elif path.startswith("/assets/review-app/"):
            relative = path.removeprefix("/assets/review-app/")
            css = relative.endswith(".css")
            body = bundle.read(relative)
            route.fulfill(
                content_type=_asset_type(relative),
                body=body + _WIDER_TEXT.encode() if css and widened else body,
            )
        elif route.request.resource_type == "document":
            route.fulfill(
                content_type="text/html",
                headers={
                    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                },
                body=bundle.read(bundle.entry)
                .decode("utf-8")
                .replace(
                    "<body>",
                    f'<body data-build-identity="{_BUILD_IDENTITY}" data-csrf-token="{"b" * 32}">',
                ),
            )
        else:
            route.fulfill(status=404, body="")

    capture = os.environ.get("ML_VISUAL_CAPTURE_DIR")
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.route("**/*", fulfill)
        page.goto("https://react-platform.invalid/activity")
        page.get_by_role("heading", name="Activity and recovery").wait_for()
        checks = page.locator("article.is-checks")
        toggle = checks.get_by_role("button")
        expect(checks).to_have_count(1)
        expect(checks).to_contain_text("Synthetic checked work")
        expect(checks.get_by_text("Checked 3 fields, no changes")).to_be_visible()
        assert "Checked 3 fields, no changes" in checks.aria_snapshot()
        expect(checks).to_contain_text("No change required")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(page.locator(".cm-activity-checks")).to_have_count(0)

        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        members = page.locator(".cm-activity-checks > li")
        expect(members).to_have_count(3)
        expect(members.locator("article")).to_have_count(3)
        expect(members.nth(2).locator("time")).to_contain_text("2025")
        expect(members.nth(1).locator("time")).not_to_contain_text("2025")
        assert members.locator("article").all_inner_texts()[1].count("Left ISBN") == 1
        for index in range(3):
            expect(
                members.nth(index).get_by_role("button", name="Details")
            ).to_have_count(1)
        member = members.nth(1).get_by_role("button", name="Details")
        member.click()
        expect(member).to_have_attribute("aria-expanded", "true")
        expect(members.nth(1).locator(".cm-activity-evidence")).to_be_visible()
        member.click()
        expect(members.nth(1).locator(".cm-activity-evidence")).to_have_count(0)

        toggle.click()
        expect(page.locator(".cm-activity-checks")).to_have_count(0)
        expect(toggle).to_be_focused()
        page.keyboard.press("Enter")
        expect(members).to_have_count(3)
        page.keyboard.press("Tab")
        expect(members.nth(0).get_by_role("button", name="Details")).to_be_focused()
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Enter")
        expect(page.locator(".cm-activity-checks")).to_have_count(0)
        expect(toggle).to_be_focused()

        # A tab switch blurs the focused check without moving focus. When the
        # next refetch removes its row, focus goes to the ledger heading.
        page.keyboard.press("Enter")
        members.nth(0).get_by_role("button", name="Details").focus()
        page.evaluate(
            """() => {
              document.activeElement.dispatchEvent(
                new FocusEvent('focusout', { bubbles: true }));
              window.dispatchEvent(new FocusEvent('blur'));
            }"""
        )
        checks_gone = True
        expect(page.locator("article.is-checks")).to_have_count(0, timeout=15_000)
        expect(page.locator("#activity-ledger-title")).to_be_focused()
        checks_gone = False

        notice = page.locator(".cm-activity-filter-notice")
        expect(notice).to_have_text("")
        page.goto("https://react-platform.invalid/activity?eventType=owner_choice")
        expect(
            page.get_by_role("status").filter(has_text="not recognised")
        ).to_have_text("That activity filter is not recognised. Showing all events.")
        page.get_by_role("button", name="Policy changes").click()
        expect(notice).to_have_text("")

        # Every row's promises, the new row's whole labels, the pagination and
        # every row's pill and time, as installed and with each glyph widened
        # as hosted Linux fonts render, light with motion and dark with reduced
        # motion.
        for widened, dark in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            scheme = "dark" if dark else "light"
            page.emulate_media(
                color_scheme="dark" if dark else "light",
                reduced_motion="reduce" if dark else "no-preference",
            )
            page.goto("https://react-platform.invalid/activity?eventType=owner_choice")
            page.locator("article.is-checks").get_by_role("button").click()
            expect(members).to_have_count(3)
            # An open member's indented row carries the longest label.
            members.nth(0).get_by_role("button", name="Details").click()
            expect(
                members.nth(0).get_by_role("button", name="Hide details")
            ).to_be_visible()
            expect(page.locator("html")).to_have_attribute("data-theme", scheme)
            for width in (1440, 1100, 884, 761, 760, 390, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.evaluate("() => document.documentElement.offsetWidth")
                where = f"{scheme}{' widened' if widened else ''} at {width}px"
                assert page.evaluate(_ROW_PROMISES, width <= 760) == [], where
                labels = page.evaluate(_ROW_LABELS, width <= 760)
                assert labels == [], f"{where}: {labels}"
                failures = [
                    failure
                    for selector in (
                        ".is-checks .cm-activity-change",
                        ".is-checks button",
                        ".cm-activity-filter-notice",
                        ".cm-activity-pagination-nav button",
                    )
                    for node in page.locator(selector).all()
                    for failure in node.evaluate(_NAMED_CLIP)
                ]
                assert failures == [], f"{where}: {failures}"
                # A check on another day names that day without running into
                # the category beside it.
                overlaps = page.locator(".cm-activity-checks time").evaluate_all(
                    """times => times.filter(time => {
                        const text = document.createRange();
                        text.selectNodeContents(time);
                        return text.getBoundingClientRect().right >
                            time.nextElementSibling.getBoundingClientRect().left;
                    }).map(time => time.textContent)"""
                )
                assert overlaps == [], f"{where}: {overlaps} overlap their category"
                split = page.evaluate(
                    _POLICY_MID_WORD,
                    ".is-checks .cm-activity-change, .cm-activity-filter-notice, "
                    ".cm-activity-pagination-nav",
                )
                assert split == [], f"{where}: words split {split}"
                document = page.evaluate("() => document.documentElement.scrollWidth")
                assert document <= width, f"{where}: the page is {document}px wide"
                if capture is not None:
                    Path(capture).mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(
                            Path(capture) / f"activity-checks-{width}-{scheme}"
                            f"{'-wide' if widened else ''}.png"
                        ),
                        full_page=True,
                    )


_FOCUS_ON_BODY = (
    "() => document.activeElement === null || document.activeElement === document.body"
)

# Each remedy link reads as part of its own entry -- on its sentence's line or
# the line after it, never nearer the next entry -- and every entry is spaced
# from the next by the same measured text gap, link or none (#1282 R3 F5).
_THROTTLE_ENTRY_LAYOUT = """() => {
  const textBox = (node) => {
    let top = Infinity, bottom = -Infinity;
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (!text.data.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(text);
      for (const rect of range.getClientRects())
        if (rect.width > 0) {
          top = Math.min(top, rect.top);
          bottom = Math.max(bottom, rect.bottom);
        }
    }
    return { top, bottom };
  };
  const rows = [...document.querySelectorAll('.provider-throttle-notice li')].map(item => {
    const link = item.querySelector('a');
    return {
      name: item.querySelector('strong').textContent.trim(),
      sentence: textBox(item.querySelector(':scope > span')),
      link: link && textBox(link),
      all: textBox(item),
      line: parseFloat(getComputedStyle(item.querySelector(':scope > span')).lineHeight),
      // A wrapped link is a target on every line it occupies.
      target: link && Math.min(...[...link.getClientRects()].map(rect => rect.height)),
    };
  });
  const found = [];
  rows.forEach((row, index) => {
    if (!row.link) return;
    const own = Math.max(0, row.link.top - row.sentence.bottom);
    const next = rows[index + 1];
    const toNext = next ? next.all.top - row.link.bottom : Infinity;
    if (own > row.line / 2 || own >= toNext)
      found.push(`${row.name} its link is ${Math.round(own)}px below it and ` +
        `${Math.round(toNext)}px above the next entry`);
  });
  // No two entries' padded link targets share a point (#1367 R13).
  const links = [...document.querySelectorAll('.provider-throttle-notice li a')];
  links.forEach((link, index) => {
    for (const other of links.slice(index + 1))
      for (const a of link.getClientRects())
        for (const b of other.getClientRects()) {
          const across = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const down = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (across > 0 && down > 0)
            found.push(`"${link.textContent}" overlaps "${other.textContent}" by ` +
              `${Math.round(across)}x${Math.round(down)}px`);
        }
  });
  const gaps = rows.slice(1).map((row, index) => row.all.top - rows[index].all.bottom);
  if (gaps.length && Math.max(...gaps) - Math.min(...gaps) > rows[0].line * 0.15)
    found.push(`entries are spaced unevenly: ${gaps.map(Math.round)}`);
  return {
    found,
    targets: rows.filter(row => row.link).map(row => [row.name, row.target]),
  };
}"""

# A retry time and its meridiem never wrap apart. With `sweep`, each entry is
# narrowed step by step so every line break position is tried (#1282 R3 F8).
_TIME_TOGETHER = """(sweep) => {
  const found = new Set();
  let times = 0;
  const check = (span) => {
    const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/\\d{1,2}[:.]\\d{2}(?:\\s*[AaPp]\\.?\\s?[Mm]\\.?)?/g)) {
        times += 1;
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        const tops = new Set([...range.getClientRects()].filter(rect => rect.width > 0)
          .map(rect => Math.round(rect.top)));
        if (tops.size > 1)
          found.add(`"${match[0]}" wraps apart in ${span.querySelector('strong').textContent}`);
      }
  };
  for (const span of document.querySelectorAll('.provider-throttle-notice li > span')) {
    if (!sweep) { check(span); continue; }
    const item = span.closest('li');
    // Narrower than the time itself, any break is the only way to fit it.
    const floor = Math.max(0, ...[...span.textContent.matchAll(
      /\\d{1,2}[:.]\\d{2}(?:\\s*[AaPp]\\.?\\s?[Mm]\\.?)?/g)].map(match => {
      const probe = document.createElement('span');
      probe.style.whiteSpace = 'nowrap';
      probe.textContent = match[0];
      span.append(probe);
      const width = probe.getBoundingClientRect().width;
      probe.remove();
      return width;
    }));
    for (let width = item.getBoundingClientRect().width; width > floor + 2; width -= 2) {
      item.style.maxWidth = `${width}px`;
      check(span);
    }
    item.style.removeProperty('max-width');
  }
  return { found: [...found], times };
}"""

# Each remedy link's keyboard focus ring: a 3 px outline that crosses no word
# of its entry outside the line it rings, at 3:1 against the notice (#1282 R5).
_REMEDY_RING = """() => {
  const channel = value => {
    const c = value / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance = color => {
    const values = color.match(/[\\d.]+/g).map(Number);
    const rgb = color.startsWith('color(') ? values.slice(0, 3).map(v => v * 255)
      : values.slice(0, 3);
    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
  };
  const contrast = (a, b) => {
    const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (high + 0.05) / (low + 0.05);
  };
  const found = [];
  const contrasts = [];
  for (const link of document.querySelectorAll('.provider-throttle-notice li a')) {
    link.focus({ focusVisible: true });
    const ring = [link, ...link.querySelectorAll('*')].find(node => {
      const style = getComputedStyle(node);
      return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 3;
    });
    if (!ring) {
      found.push(`"${link.textContent}" has no 3px focus ring`);
      continue;
    }
    const style = getComputedStyle(ring);
    const reach = parseFloat(style.outlineOffset) + parseFloat(style.outlineWidth);
    const item = link.closest('li');
    const words = [];
    const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/\\S+/g)) {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        for (const rect of range.getClientRects())
          if (rect.width > 0) words.push([match[0], rect]);
      }
    for (const line of [...ring.getClientRects()].filter(rect => rect.width > 0)) {
      const inside = rect => rect.left >= line.left - 0.5 && rect.right <= line.right + 0.5 &&
        rect.top >= line.top - 0.5 && rect.bottom <= line.bottom + 0.5;
      for (const [word, rect] of words) {
        if (inside(rect)) continue;
        const across = Math.min(line.right + reach, rect.right) - Math.max(line.left - reach, rect.left);
        const down = Math.min(line.bottom + reach, rect.bottom) - Math.max(line.top - reach, rect.top);
        if (across > 0.5 && down > 0.5)
          found.push(`the ring of "${link.textContent}" crosses "${word}" by ${Math.round(down)}px`);
      }
    }
    contrasts.push([link.textContent, contrast(style.outlineColor,
      getComputedStyle(link.closest('.provider-throttle-notice')).backgroundColor)]);
  }
  document.activeElement?.blur();
  return { found, contrasts };
}"""

# The Retry outcome on show: where it sits against the control and how it is
# marked (#1282 R3 F6).
_RETRY_OUTCOME = """() => {
  const notice = document.querySelector('.provider-throttle-notice');
  const button = notice.querySelector('.provider-throttle-retry button');
  const shown = [...notice.querySelectorAll('[role=status], [role=alert]')]
    .find(node => node.textContent.trim());
  const style = getComputedStyle(shown);
  const box = shown.getBoundingClientRect();
  return {
    role: shown.getAttribute('role'),
    mark: getComputedStyle(shown, '::before').content,
    color: style.color,
    line: parseFloat(style.lineHeight),
    distance: box.top - button.parentElement.getBoundingClientRect().bottom,
    aboveList: box.top < notice.querySelector('ul').getBoundingClientRect().top,
  };
}"""


def test_library_and_settings_show_provider_throttling_with_one_retry_now() -> None:
    """Each throttled source reads once per library, Retry now sends one request
    per press and answers in a mounted region, and nothing clips (#1282)."""
    bundle = load_react_asset_bundle(Path(os.environ["ML_REACT_ASSET_DIR"]))
    assert isinstance(bundle, ReactAssetBundle)
    widened = False
    slow_settings = False
    retries: list[str] = []
    held: list[Route] = []

    def fulfill(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == "/api/v1/updates":
            route.fulfill(status=204, body="")
        elif path == "/api/v1/appearance":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"reduced_motion": "follow_system"}),
            )
        elif path == "/api/v1/provider-recovery/retry":
            assert route.request.method == "POST"
            assert json.loads(route.request.post_data or "") == {
                "expected_generation": 1
            }
            retries.append(route.request.headers.get("x-csrf-token", ""))
            if len(retries) == 1:
                # Held until the test releases it, so the press stays pending.
                held.append(route)
                return
            route.fulfill(
                status=409,
                content_type="application/json",
                body=json.dumps(
                    {
                        "error": {"code": "provider_recovery_stale"},
                        "schema_version": "review-error-v1",
                    }
                ),
            )
        elif (
            preview := synthetic_preview_key(path, urlsplit(route.request.url).query)
        ) is not None:
            route.fulfill(content_type="image/png", body=synthetic_preview(preview))
        elif path.startswith("/api/v1/"):
            try:
                payload = rich_payload(route.request.url)
            except AssertionError:
                route.fulfill(status=404, body="")
                return
            if path == "/api/v1/settings" and slow_settings:
                time.sleep(0.25)
            route.fulfill(content_type="application/json", body=json.dumps(payload))
        elif path.startswith("/assets/review-app/"):
            relative = path.removeprefix("/assets/review-app/")
            css = relative.endswith(".css")
            body = bundle.read(relative)
            route.fulfill(
                content_type=_asset_type(relative),
                body=body + _WIDER_TEXT.encode() if css and widened else body,
            )
        elif route.request.resource_type == "document":
            route.fulfill(
                content_type="text/html",
                headers={
                    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
                },
                body=bundle.read(bundle.entry)
                .decode("utf-8")
                .replace(
                    "<body>",
                    f'<body data-build-identity="{_BUILD_IDENTITY}" data-csrf-token="{"b" * 32}">',
                ),
            )
        else:
            route.fulfill(status=404, body="")

    capture = os.environ.get("ML_VISUAL_CAPTURE_DIR")
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.route("**/*", fulfill)
        page.goto("https://react-platform.invalid/library?lens=all")
        notice = page.get_by_role("region", name="Provider lookups waiting to retry")
        entries = notice.get_by_role("listitem")
        expect(entries).to_have_count(3)
        expect(entries.nth(0)).to_contain_text(
            "Google Books · Synthetic ebooks: It is limiting requests. "
            "214 deferred lookups. Next automatic retry "
        )
        # The burst resets on the next day, so the time names the day.
        expect(entries.nth(0).locator(":scope > span")).to_have_text(
            re.compile(r"Next automatic retry [^.]+, [^.]+\.$")
        )
        expect(entries.nth(1)).to_contain_text(
            "Hardcover · Synthetic ebooks: It refused the configured API key. "
            "1 deferred lookup. Waits for Retry now after the source is fixed."
        )
        expect(entries.nth(2)).to_contain_text(
            "Open Library · Synthetic audiobooks with a long library name: It did "
            "not answer. 10,000 deferred lookups. No automatic retry remains."
        )
        expect(
            entries.nth(1).get_by_role("link", name="Check the source’s API key")
        ).to_have_attribute("href", "/settings?group=providers")
        expect(entries.nth(2).get_by_role("link")).to_have_count(0)
        status = notice.get_by_role("status")
        alert = notice.get_by_role("alert")
        expect(status).to_have_text("")
        expect(alert).to_have_text("")

        # Keyboard: Enter presses Retry now; while it is pending, neither a
        # second Enter nor a click sends anything.
        retry = notice.get_by_role("button", name="Retry now")
        retry.focus()
        page.keyboard.press("Enter")
        busy = notice.locator(".provider-throttle-retry button")
        expect(busy).to_have_attribute("aria-busy", "true")
        expect(busy).to_have_attribute("aria-disabled", "true")
        expect(busy).to_have_text("Retrying…")
        page.keyboard.press("Enter")
        busy.click(force=True)  # aria-disabled is "not enabled" to Playwright
        page.wait_for_timeout(500)
        assert len(retries) == 1, retries
        assert retries[0] == "b" * 32
        expect(busy).to_be_focused()
        settings = cast(
            "dict[str, dict[str, object]]", rich_payload("/api/v1/settings")
        )
        settings["document"] |= {"revision": 2, "provider_recovery_generation": 2}
        held.pop().fulfill(content_type="application/json", body=json.dumps(settings))
        expect(status).to_have_text(
            "Retry now accepted. Every deferred lookup will retry now."
        )
        expect(retry).to_be_focused()
        assert not page.evaluate(_FOCUS_ON_BODY)
        accepted = page.evaluate(_RETRY_OUTCOME)
        assert accepted["role"] == "status", accepted
        assert not accepted["aboveList"], accepted
        assert 0 <= accepted["distance"] <= accepted["line"], accepted
        assert accepted["mark"] not in ("none", "normal"), accepted

        # The second press is stale: the alert says so and nothing moves focus.
        page.keyboard.press("Enter")
        expect(alert).to_have_text(
            "Retry now was already pressed elsewhere. The notice now shows the "
            "current state."
        )
        assert len(retries) == 2, retries
        expect(status).to_have_text("")
        expect(retry).to_be_focused()
        refused = page.evaluate(_RETRY_OUTCOME)
        assert refused["role"] == "alert", refused
        assert not refused["aboveList"], refused
        assert 0 <= refused["distance"] <= refused["line"], refused
        # Marked apart by its glyph as well as its colour, never colour alone.
        assert refused["mark"] not in ("none", "normal", accepted["mark"]), refused
        assert refused["color"] != accepted["color"], (accepted, refused)
        swept = page.evaluate(_TIME_TOGETHER, True)
        assert swept["times"] > 0 and swept["found"] == [], swept

        # The remedy opens Settings at Metadata sources, which carries the same
        # notice and no generation number.
        entries.nth(0).get_by_role("link", name="Add a Google Books API key").click()
        page.wait_for_url("**/settings?group=providers")
        expect(notice).to_be_visible()
        expect(notice.get_by_role("button", name="Retry now")).to_be_visible()
        page.goto("https://react-platform.invalid/settings?group=background")
        page.get_by_role("spinbutton", name="Request timeout").wait_for()
        expect(page.get_by_text("Provider recovery generation")).to_have_count(0)

        # Arriving at Settings by a remedy or the nav link, focus stays in the
        # route after the loading state that held it unmounts (#1282 R3 F7).
        # The settings read is slowed so that state is always there.
        slow_settings = True
        arrivals = []
        for attempt in range(5):
            for via in ("remedy", "nav"):
                page.goto("https://react-platform.invalid/library?lens=all")
                link = (
                    entries.nth(1).get_by_role("link")
                    if via == "remedy"
                    else page.get_by_role("navigation").get_by_role(
                        "link", name="Settings", exact=True
                    )
                )
                link.click()
                page.locator("section.settings-route").wait_for()
                page.evaluate(_TWO_FRAMES)
                arrivals.append((attempt, via, page.evaluate(_FOCUSED)))
        assert {focused for *_, focused in arrivals} == {"SECTION.settings-route"}, (
            arrivals
        )
        slow_settings = False

        for widened, dark in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            scheme = "dark" if dark else "light"
            page.emulate_media(
                color_scheme="dark" if dark else "light",
                reduced_motion="reduce" if dark else "no-preference",
            )
            for route in ("/library?lens=all", "/settings?group=providers"):
                page.goto(f"https://react-platform.invalid{route}")
                expect(entries).to_have_count(3)
                expect(page.locator("html")).to_have_attribute("data-theme", scheme)
                for width in (1440, 1100, 884, 761, 760, 390, 320):
                    page.set_viewport_size({"width": width, "height": 900})
                    page.evaluate("() => document.documentElement.offsetWidth")
                    where = (
                        f"{route} {scheme}{' widened' if widened else ''} at {width}px"
                    )
                    failures = [
                        failure
                        for selector in (
                            ".provider-throttle-notice li",
                            ".provider-throttle-notice li > span",
                            ".provider-throttle-notice a",
                            ".provider-throttle-retry button",
                            ".provider-throttle-retry small",
                            ".preparation-activity > button",
                        )
                        for node in page.locator(selector).all()
                        for failure in node.evaluate(_NAMED_CLIP)
                    ]
                    assert failures == [], f"{where}: {failures}"
                    _fits_its_container(page, ".provider-throttle-notice", width)
                    layout = page.evaluate(_THROTTLE_ENTRY_LAYOUT)
                    assert layout["found"] == [], f"{where}: {layout['found']}"
                    small = [target for target in layout["targets"] if target[1] < 44]
                    assert small == [], f"{where}: links under 44px {small}"
                    together = page.evaluate(_TIME_TOGETHER, False)
                    assert together["found"] == [], f"{where}: {together['found']}"
                    split = page.evaluate(_POLICY_MID_WORD, ".provider-throttle-notice")
                    assert split == [], f"{where}: words split {split}"
                    document = page.evaluate(
                        "() => document.documentElement.scrollWidth"
                    )
                    assert document <= width, f"{where}: the page is {document}px wide"
                    ring = page.evaluate(_REMEDY_RING)
                    assert ring["found"] == [], f"{where}: {ring['found']}"
                    low = [item for item in ring["contrasts"] if item[1] < 3]
                    assert low == [], f"{where}: focus ring under 3:1 {low}"
                    if capture is not None:
                        Path(capture).mkdir(parents=True, exist_ok=True)
                        name = route.strip("/").split("?")[0]
                        page.screenshot(
                            path=str(
                                Path(capture) / f"throttle-{name}-{width}-{scheme}"
                                f"{'-wide' if widened else ''}.png"
                            ),
                            full_page=True,
                        )

        # With 200% root text the links wrap; each line's ring still crosses
        # no neighbouring word, in either theme, and nothing scrolls sideways.
        widened = False
        for dark in (False, True):
            page.emulate_media(color_scheme="dark" if dark else "light")
            page.goto("https://react-platform.invalid/library?lens=all")
            expect(entries).to_have_count(3)
            set_root_text(page, "200%")
            for width in (390, 320):
                page.set_viewport_size({"width": width, "height": 900})
                where = f"{'dark' if dark else 'light'} 200% text at {width}px"
                settled = page.evaluate(_REMEDY_LINKS_SETTLED)
                assert settled is not None, f"{where}: the remedy links never settled"
                wrapped = page.locator(".provider-throttle-notice li a").evaluate_all(
                    "links => links.filter(link => link.getClientRects().length > 1)"
                    ".length"
                )
                assert wrapped > 0, f"{where}: no remedy link wraps"
                ring = page.evaluate(_REMEDY_RING)
                assert ring["found"] == [], f"{where}: {ring['found']}"
                low = [item for item in ring["contrasts"] if item[1] < 3]
                assert low == [], f"{where}: focus ring under 3:1 {low}"
                layout = page.evaluate(_THROTTLE_ENTRY_LAYOUT)
                small = [target for target in layout["targets"] if target[1] < 44]
                assert small == [], f"{where}: links under 44px {small}"
                document = page.evaluate("() => document.documentElement.scrollWidth")
                assert document <= width, f"{where}: the page is {document}px wide"


# Resolves once the remedy links' line boxes agree across two consecutive
# frames, so a reflow after a text or viewport change is read only when done.
_REMEDY_LINKS_SETTLED = """async () => {
  const frame = () => new Promise(done => requestAnimationFrame(done));
  const boxes = () => JSON.stringify([
    document.documentElement.scrollWidth,
    ...[...document.querySelectorAll('.provider-throttle-notice li a')].map(link =>
      [...link.getClientRects()].map(box => [box.left, box.top, box.width, box.height])),
  ]);
  let previous = boxes();
  for (let frames = 1; frames <= 120; frames += 1) {
    await frame();
    const current = boxes();
    if (current === previous) return frames;
    previous = current;
  }
  return null;
}"""


# The review server's own policy, so nothing the shell needs is admitted here
# that production would refuse.
_PRODUCTION_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)

# Writes are off and one library may be enabled, so the Library handoff shows.
_WRITES_OFF_CONSENT = {
    "backup_ready": None,
    "configured_libraries": [{"connection": "primary", "library": "Household ebooks"}],
    "lapsed_consents": [],
    "capabilities": [
        {
            "connection": "primary",
            "library": "Household ebooks",
            "state": "allowed",
            "destinations": [
                {
                    "destination": "title",
                    "state": "allowed",
                    "consented": False,
                    "prepared": 2,
                }
            ],
        }
    ],
    "waiting": {
        "applying": 0,
        "awaiting_permission": 2,
        "held_for_recovery": 0,
        "retrying": 0,
        "scheduled": 0,
    },
}


def _serve_cohort(
    page: Page,
    *,
    stylesheet: Callable[[], str] = lambda: "",
    writes_off_handoff: bool = False,
    held_library: list[Route] | None = None,
    library_requests: list[str] | None = None,
) -> None:
    """Serve the exact built shell over the mocked cohort, under the production CSP.

    `stylesheet` is appended to the served CSS, `writes_off_handoff` serves a
    consent state and connections that show the writes-off handoff, `held_library` keeps every
    `/api/v1/library` request until the test fulfils it, and `library_requests`
    records each one's query string.
    """

    bundle = load_react_asset_bundle(Path(os.environ["ML_REACT_ASSET_DIR"]))
    assert isinstance(bundle, ReactAssetBundle)

    def fulfill(route: Route) -> None:
        parts = urlsplit(route.request.url)
        path = parts.path
        if path == "/api/v1/updates":
            route.fulfill(status=204, body="")
        elif path == "/api/v1/appearance":
            route.fulfill(
                content_type="application/json",
                body=json.dumps({"reduced_motion": "follow_system"}),
            )
        elif path == "/api/v1/activity/consent-state" and writes_off_handoff:
            route.fulfill(
                content_type="application/json", body=json.dumps(_WRITES_OFF_CONSENT)
            )
        elif path == "/api/v1/library" and (
            library_requests is not None or held_library is not None
        ):
            if library_requests is not None:
                library_requests.append(parts.query)
            if held_library is not None:
                held_library.append(route)
            else:
                route.fulfill(
                    content_type="application/json",
                    body=json.dumps(rich_payload(route.request.url)),
                )
        elif (
            preview := synthetic_preview_key(path, urlsplit(route.request.url).query)
        ) is not None:
            route.fulfill(content_type="image/png", body=synthetic_preview(preview))
        elif path.startswith("/api/v1/"):
            try:
                payload = rich_payload(route.request.url)
            except AssertionError:
                route.fulfill(status=404, body="")
                return
            if writes_off_handoff and path == "/api/v1/review-preparation":
                # A paused connection withholds the handoff, so none is paused.
                assert isinstance(payload, dict)
                payload["connections"] = []
            route.fulfill(content_type="application/json", body=json.dumps(payload))
        elif path.startswith("/assets/review-app/"):
            relative = path.removeprefix("/assets/review-app/")
            css = relative.endswith(".css")
            body = bundle.read(relative)
            route.fulfill(
                content_type=_asset_type(relative),
                body=body + stylesheet().encode() if css else body,
            )
        elif route.request.resource_type == "document":
            route.fulfill(
                content_type="text/html",
                headers={"content-security-policy": _PRODUCTION_CSP},
                body=bundle.read(bundle.entry)
                .decode("utf-8")
                .replace(
                    "<body>",
                    f'<body data-build-identity="{_BUILD_IDENTITY}" data-csrf-token="{"b" * 32}">',
                ),
            )
        else:
            route.fulfill(status=404, body="")

    page.route("**/*", fulfill)


def test_library_attention_groups_works_and_counts_never_read_zero_while_loading() -> (
    None
):
    """Attention lists each work once with its fields as keyboard rows, and no
    count reads zero before the library summary arrives (#1284)."""
    held: list[Route] = []
    requests: list[str] = []
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        _serve_cohort(page, held_library=held, library_requests=requests)
        page.goto("https://react-platform.invalid/library?lens=all")
        # B3: the library read is held, so every count is pending. The cohort
        # offers no write handoff; Vitest proves its pending heading.
        expect(page.locator(".library-works-heading [role='status']")).to_have_text(
            "Loading count…"
        )
        expect(page.locator(".attention-toggle strong")).to_have_text("…")
        body = page.locator("body").inner_text()
        assert re.search(r"\b0 works?\b", body) is None, body
        assert requests == ["lens=all"], requests
        while held:
            route = held.pop()
            route.fulfill(
                content_type="application/json",
                body=json.dumps(rich_payload(route.request.url)),
            )
        expect(page.locator(".attention-toggle strong")).to_have_text("5 works")
        expect(page).to_have_url(re.compile(r"preview=case-1"))
        expect(page.locator(".library-works-heading [role='status']")).to_have_text(
            "20 works · 1 could not finish"
        )

        # B2: the rail groups six field rows under four works.
        page.get_by_role("button", name="Attention 5 works").click()
        rail = page.get_by_role("complementary", name="Attention queue")
        expect(rail.get_by_role("heading", name="Attention")).to_be_focused()
        groups = rail.locator(".attention-work-group")
        expect(groups).to_have_count(4)
        rows = rail.locator("button.attention-queue-item")
        expect(rows).to_have_count(6)
        expect(rail.locator("header p")).to_have_text("5 works need a decision")
        titles = groups.evaluate_all(
            "groups => groups.map(group => group.querySelector('strong').textContent)"
        )
        assert len(set(titles)) == 4, titles
        for index in range(groups.count()):
            group = groups.nth(index)
            count = group.locator("button.attention-queue-item").count()
            expect(group.locator(".attention-work-count")).to_have_text(
                f"{count} field" if count == 1 else f"{count} fields"
            )
            # Each row's name is its visible line followed by its work (#1361).
            group_rows = group.locator("button.attention-queue-item")
            for row in range(count):
                line = group_rows.nth(row).locator("b").text_content() or ""
                expect(group_rows.nth(row)).to_have_accessible_name(
                    f"{line}, {titles[index]}"
                )
        current = rail.locator("button.attention-queue-item[aria-current='true']")
        expect(current).to_have_count(1)
        # Keyboard: Tab reaches each row in order, and Enter selects the one focused.
        rows.first.focus()
        visited = []
        for _ in range(6):
            visited.append(page.evaluate(_FOCUSED))
            page.keyboard.press("Tab")
        assert visited == ["BUTTON.attention-queue-item"] * 6, visited
        rows.nth(1).focus()
        expected = rows.nth(1).evaluate(
            "row => row.getAttribute('aria-label').slice(row.querySelector('b').textContent.length + 2)"
        )
        page.keyboard.press("Enter")
        expect(page).not_to_have_url(re.compile(r"attention=open"))
        expect(page.locator(".selected-work-header h2")).to_have_text(expected)
        page.evaluate(_TWO_FRAMES)
        assert not page.evaluate(_FOCUS_ON_BODY)
        page.get_by_role("button", name="Attention 5 works").click()
        expect(
            rail.locator("button.attention-queue-item[aria-current='true']")
        ).to_have_text(rows.nth(1).inner_text())
        rail.get_by_role("button", name="Close").click()
        page.evaluate(_TWO_FRAMES)
        assert not page.evaluate(_FOCUS_ON_BODY)
        assert all("q=" not in query for query in requests), requests


# Back to works, after selection: inside the viewport, below the sticky
# masthead, and the element actually under its centre.
_BACK_IN_VIEW = """() => {
  const back = document.querySelector('.compact-workspace-back');
  const masthead = document.querySelector('.masthead').getBoundingClientRect();
  if (!back || back.getClientRects().length === 0) return {visible: false};
  const box = back.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return {
    visible: box.top >= masthead.bottom - 1 && box.bottom <= innerHeight + 1 && back.contains(hit),
    top: box.top, bottom: box.bottom, masthead: masthead.bottom, height: innerHeight,
  };
}"""


_COVER_ROW_GEOMETRY = """() => {
    const row = document.querySelector('button.library-work-row:has(.has-cover)')
    const spine = document.querySelector(
        'button.library-work-row .library-book-mark:not(.has-cover)'
    )
    const mark = row.querySelector('.library-book-mark')
    const image = mark.querySelector('img')
    const box = (node) => node.getBoundingClientRect()
    const centre = (node) => box(node).left + box(node).width / 2
    const shown = box(row).height
    image.style.display = 'none'
    const hidden = box(row).height
    image.style.display = ''
    return {
        shown,
        hidden,
        mark: [box(mark).width, box(mark).height],
        image: [box(image).width, box(image).height],
        natural: [image.naturalWidth, image.naturalHeight],
        fit: getComputedStyle(image).objectFit,
        loading: image.loading,
        alt: image.alt,
        offset: Math.abs(centre(mark) - centre(spine)),
    }
}"""

# Each works row's height beside the height it has with the spine mark in the
# given base column: base's 48 px at desktop, the spine's own 42 px on phones.
_ROWS_AGAINST_SPINE_COLUMN = """(column) => [...document.querySelectorAll('.library-work-row')]
    .map((row) => {
        const mark = row.querySelector(':scope > .library-book-mark')
        const height = () => row.getBoundingClientRect().height
        const pill = row.querySelector('.status-pill')
        const lines = () => {
            const range = document.createRange()
            range.selectNodeContents(pill)
            return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size
        }
        const shown = { height: height(), pill: lines() }
        const cover = mark.classList.contains('has-cover')
        row.style.gridTemplateColumns = `${column}px minmax(0, 1fr)`
        const image = mark.querySelector('img')
        if (cover) {
            mark.classList.remove('has-cover')
            image.style.display = 'none'
        }
        const spine = { height: height(), pill: lines() }
        row.style.gridTemplateColumns = ''
        if (cover) {
            mark.classList.add('has-cover')
            image.style.display = ''
        }
        const title = row.querySelector('.library-work-title')
        return {
            title: title.innerText,
            titleLeft: title.getBoundingClientRect().left,
            column: getComputedStyle(row).gridTemplateColumns.split(' ')[0],
            cover,
            shown,
            spine,
        }
    })"""


def test_works_rows_show_a_retained_cover_without_growing() -> None:
    """A work's retained cover is its mark: whole, fitted to the spine's own
    column (48 by 72 px, 42 by 63 on phones) and centred like the spine, so
    every title starts at one edge and no row is taller than with the spine
    mark (#1290)."""
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        _serve_cohort(page, stylesheet=lambda: styles[text])
        for text in styles:
            for width in (1440, 1024, 375, 320):
                where = f"{text} at {width}px"
                page.set_viewport_size({"width": width, "height": 900})
                # The case is open, so its header shows the row's cover too;
                # where the list cannot fit beside it, the list is one step back.
                page.goto(
                    "https://react-platform.invalid/library?lens=all&preview=case-1"
                )
                header = page.locator(".selected-work-header .library-book-mark")
                expect(header).to_have_class(re.compile(r"\bhas-cover\b"))
                back = page.get_by_role("button", name="Back to works")
                if back.is_visible():
                    back.click()
                row = page.locator("button.library-work-row").filter(
                    has=page.locator(".library-book-mark.has-cover")
                )
                expect(row).to_have_count(1)
                expect(row).to_be_visible()
                row.scroll_into_view_if_needed()
                page.wait_for_function(
                    "() => document.querySelector('.has-cover img')?.naturalWidth > 0"
                )
                geometry = page.evaluate(_COVER_ROW_GEOMETRY)
                assert geometry["shown"] == geometry["hidden"], (where, geometry)
                mark_width, mark_height = geometry["mark"]
                limit = (42, 63) if width <= 760 else (48, 72)
                assert mark_width == limit[0], (where, geometry)
                assert mark_height <= limit[1], (where, geometry)
                # The image fills the mark and is contained in it, whole.
                assert geometry["image"] == geometry["mark"], (where, geometry)
                assert geometry["fit"] == "contain", (where, geometry)
                assert geometry["natural"] == [800, 1200], (where, geometry)
                assert (geometry["loading"], geometry["alt"]) == ("lazy", ""), where
                assert geometry["offset"] <= 0.5, (where, geometry)
                assert page.evaluate(
                    "() => document.documentElement.scrollWidth <= window.innerWidth"
                ), where
                # The cover fits the spine's column, so every row, the cover's
                # among them, keeps one mark column, starts its title at one
                # edge, and is no taller than with the spine mark.
                column = 42 if width <= 760 else 48
                rows = page.evaluate(_ROWS_AGAINST_SPINE_COLUMN, column)
                assert len(rows) == 6, (where, rows)
                assert {row["column"] for row in rows} == {f"{column}px"}, (where, rows)
                lefts = [row["titleLeft"] for row in rows]
                assert max(lefts) - min(lefts) <= 0.5, (where, rows)
                for row_geometry in rows:
                    assert (
                        row_geometry["shown"]["height"]
                        <= row_geometry["spine"]["height"] + 0.5
                    ), (where, row_geometry)
                    assert (
                        row_geometry["shown"]["pill"] == row_geometry["spine"]["pill"]
                    ), (where, row_geometry)
                if (width, text) == (1440, "default"):
                    catalogue = next(
                        row for row in rows if row["title"] == "Catalogue-only work"
                    )
                    assert catalogue["shown"]["pill"] == 1, (where, catalogue)


_FIELD_TAB_TEXT = """() => [...document.querySelectorAll(
    '.field-filmstrip button > strong, .field-filmstrip button > .field-status, '
    + '.field-filmstrip .field-more'
)].map((node) => ({
    text: node.innerText,
    clipped: node.scrollWidth - node.clientWidth,
}))"""


def test_compact_field_tabs_show_whole_names_and_status_words() -> None:
    """At phone widths and 200% text every field tab's name and status word
    is whole: none is clipped and none ends in an ellipsis (#1290)."""
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 844}, locale="en-US")
        _serve_cohort(page, stylesheet=lambda: styles[text])
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            for text in styles:
                for width in (390, 375, 320):
                    where = f"{scheme} {text} at {width}px"
                    page.set_viewport_size({"width": width, "height": 844})
                    page.goto(
                        "https://react-platform.invalid/library?lens=all&preview=case-1"
                    )
                    expect(
                        page.locator(".field-filmstrip .field-status").first
                    ).to_be_visible()
                    tabs = page.evaluate(_FIELD_TAB_TEXT)
                    assert any(tab["text"] == "Needs you" for tab in tabs), (
                        where,
                        tabs,
                    )
                    assert [tab for tab in tabs if tab["clipped"] > 0] == [], (
                        where,
                        tabs,
                    )
                    assert [
                        tab
                        for tab in tabs
                        if tab["text"].rstrip().endswith(("…", "..."))
                    ] == [], (where, tabs)
                    assert page.evaluate(
                        "() => document.documentElement.scrollWidth <= window.innerWidth"
                    ), where


def test_compact_selection_brings_back_to_works_into_view() -> None:
    """Choosing a work far down the compact list scrolls the workspace's top,
    where Back to works leads it, to just below the masthead (#1284)."""
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 844}, locale="en-US")
        _serve_cohort(page, stylesheet=lambda: styles[text], writes_off_handoff=True)
        for reduced in (False, True):
            page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
            for text in styles:
                for width in (390, 320):
                    where = f"{text} at {width}px{' reduced' if reduced else ''}"
                    page.set_viewport_size({"width": width, "height": 844})
                    page.goto("https://react-platform.invalid/library?lens=all")
                    # The last work that opens, far below the first screen.
                    rows = page.locator("button.library-work-row")
                    expect(rows.first).to_be_visible()
                    last = rows.nth(rows.count() - 1)
                    last.scroll_into_view_if_needed()
                    last.click()
                    expect(page).to_have_url(re.compile(r"[?&](preview|work)="))
                    page.locator(".compact-workspace-back").wait_for()
                    page.evaluate(_TWO_FRAMES)
                    back = page.evaluate(_BACK_IN_VIEW)
                    assert back["visible"], f"{where}: {back}"
                    assert not page.evaluate(_FOCUS_ON_BODY), where


_PULSE = """() => getComputedStyle(
  document.querySelector('.route-state[aria-busy="true"] h1'), '::before').animationName"""


def _busy_pulse(preference: str) -> str:
    """The busy heading dot's animation while Library waits on its consent read,
    with no system motion preference and the owner's motion setting given."""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.emulate_media(reduced_motion="no-preference")
        _serve_cohort(page)
        held: list[Route] = []
        appearance = json.dumps({"reduced_motion": preference})
        # Routes registered later take precedence over the cohort's.
        page.route(
            "**/api/v1/appearance",
            lambda route: route.fulfill(
                content_type="application/json", body=appearance
            ),
        )
        page.route("**/api/v1/activity/consent-state", lambda route: held.append(route))
        page.goto("https://react-platform.invalid/library?lens=all")
        expect(page.get_by_role("heading", name="Loading library…")).to_be_visible()
        if preference == "always_reduced":
            expect(page.locator("html")).to_have_attribute(
                "data-reduced-motion", "always-reduced"
            )
        animation = cast("str", page.evaluate(_PULSE))
        for route in held:
            route.abort()
        return animation


def test_the_loading_pulse_obeys_always_reduced_motion() -> None:
    """The busy heading's dot pulses only while motion is allowed: the owner's
    "Always reduced" setting stills it with no system preference (#1291)."""
    assert _busy_pulse("follow_system") == "live-state-pulse"
    assert _busy_pulse("always_reduced") == "none"


# Each direct choice that runs past its grid, or past its own box.
_CLIPPED_CHOICES = """() => [...document.querySelectorAll('.workspace-detail .direct-choice-grid')]
  .flatMap((grid) => {
    const bounds = grid.getBoundingClientRect();
    return [...grid.children].flatMap((choice) => {
      const box = choice.getBoundingClientRect();
      const past = Math.max(box.right - bounds.right, bounds.left - box.left);
      const inside = choice.scrollWidth - choice.clientWidth;
      return [
        ...(box.width && past > 0.5 ? [`${choice.className} past its grid by ${Math.round(past)}px`] : []),
        ...(inside > 1 ? [`${choice.className} clipped by ${inside}px`] : []),
      ];
    });
  })"""


def test_attention_beside_a_work_never_clips_its_choices() -> None:
    """With Attention open at 200% text, the selected work's choices are never
    clipped and works rows stay whole: when three columns cannot fit, Attention
    drops below the work, and opening it still brings its heading into view
    (#1291)."""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1200, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: "html { font-size: 200% !important; }")
        failures = []
        for width in (1200, 1240, 1280, 1320, 1360, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto("https://react-platform.invalid/library?lens=all&attention=open")
            page.wait_for_load_state("networkidle")
            expect(
                page.locator(".workspace-detail .direct-choice-grid").first
            ).to_be_visible()
            expect(page.locator(".attention-rail")).to_be_visible()
            page.evaluate(_TWO_FRAMES)
            failures += [
                f"{width}px: {item}" for item in page.evaluate(_CLIPPED_CHOICES)
            ]
            split = page.evaluate(_POLICY_MID_WORD, _WORKS_ROWS)
            if split:
                failures += [
                    f"{width}px: works row {word}"
                    for word in page.evaluate(_NEEDLESS_SPLITS, [_WORKS_ROWS, split])
                ]
        assert failures == [], "\n".join(failures)
        # Reopened where it sits below the work, its heading comes into view.
        page.set_viewport_size({"width": 1200, "height": 900})
        page.locator(".attention-toggle").click()
        expect(page.locator(".attention-rail")).to_have_count(0)
        page.locator(".attention-toggle").click()
        heading = page.locator(".attention-rail h2").first
        expect(heading).to_be_focused()
        expect(heading).to_be_in_viewport()


def test_closing_the_rail_keeps_back_to_works_in_view() -> None:
    """Closing the Attention rail over a compact selection, with its Close or
    the toolbar's toggle, brings the workspace's top, where Back to works leads
    it, back into view (#1284)."""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 844}, locale="en-US")
        if os.environ.get("DIAG_INIT"):  # temporary diagnostic, not for merge
            page.add_init_script(os.environ["DIAG_INIT"])
            page.on(
                "console",
                lambda m: print(
                    f"console {m.text[:200]}", file=sys.__stderr__, flush=True
                ),
            )
        _serve_cohort(
            page,
            stylesheet=lambda: "html { font-size: 200% !important; }",
            writes_off_handoff=True,
        )
        rail = page.get_by_role("complementary", name="Attention queue")
        heading = rail.get_by_role("heading", name="Attention")
        toggle = page.locator(".attention-toggle")
        closers = {
            "Close": rail.get_by_role("button", name="Close", exact=True),
            "the toggle": toggle,
        }
        for closer, control in closers.items():
            for width in (390, 320):
                where = f"{closer}, 200% text at {width}px"
                page.set_viewport_size({"width": width, "height": 844})
                page.goto("https://react-platform.invalid/library?lens=all")
                rows = page.locator("button.library-work-row")
                expect(rows.first).to_be_visible()
                last = rows.nth(rows.count() - 1)
                last.scroll_into_view_if_needed()
                last.click()
                page.locator(".compact-workspace-back").wait_for()
                toggle.click()
                expect(toggle).to_have_attribute("aria-expanded", "true")
                # The owner sees the opened rail, its heading focused, before
                # closing it; a close in the same frame as the open is the
                # Library route's Vitest case (#1563).
                if not os.environ.get("DIAG_NOWAIT"):
                    expect(heading).to_be_focused()
                print(f"console STEP {where} close", file=sys.__stderr__, flush=True)
                control.click()
                expect(rail).to_have_count(0)
                expect(page).to_have_url(re.compile(r"[?&](preview|work)="))
                page.evaluate(_TWO_FRAMES)
                back = page.evaluate(_BACK_IN_VIEW)
                assert back["visible"], f"{where}: {back}"
                assert not page.evaluate(_FOCUS_ON_BODY), where


def test_attention_drawer_holds_focus_and_returns_it() -> None:
    """Opening Attention focuses its heading. Between 761 and 1199px the rail
    is a drawer: the page behind it, but for the toolbar that toggles it, is
    inert and Tab wraps inside it, and crossing out of that range while it is
    open releases the page. Escape or Close shuts it at any width and hands
    focus back to the toggle, or at 760px and below to the selected work it
    reveals; whatever takes focus is in view (#1288)."""
    inert = """() => ['.masthead', '.library-works', '.workspace-detail']
      .filter(selector => document.querySelector(selector)?.closest('[inert]'))"""
    inside = """() => Boolean(document.activeElement?.closest('.attention-rail'))"""
    in_view = """() => {
      const bounds = document.activeElement.getBoundingClientRect();
      return bounds.top >= -1 && bounds.bottom <= innerHeight + 1;
    }"""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        _serve_cohort(page)
        rail = page.get_by_role("complementary", name="Attention queue")
        heading = rail.get_by_role("heading", name="Attention")
        toggle = page.locator(".attention-toggle")
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            for width, drawer, preview in (
                (1440, False, False),
                (1100, True, False),
                (900, True, False),
                (900, True, True),
                (390, False, False),
                (390, False, True),
                (320, False, False),
            ):
                where = f"{scheme} at {width}px{' over a work' if preview else ''}"
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(
                    "https://react-platform.invalid/library?lens=all"
                    + ("&preview=case-1" if preview else "")
                )
                page.locator(".attention-toggle strong").filter(
                    has_text=re.compile(r"works?$")
                ).wait_for()
                if preview:
                    page.locator(".selected-work-header").wait_for()
                toggle.click()
                expect(heading).to_be_focused()
                assert page.evaluate(in_view), f"{where}: heading out of view"
                assert page.evaluate(inert) == (
                    [".masthead", ".library-works", ".workspace-detail"]
                    if drawer
                    else []
                ), where
                # The toolbar that opened the drawer can still close it.
                assert not toggle.evaluate("node => Boolean(node.closest('[inert]'))")
                if drawer:
                    stops = rail.locator("button, a[href], input").count()
                    for key in ("Tab", "Shift+Tab"):
                        for _ in range(stops + 1):
                            page.keyboard.press(key)
                            assert page.evaluate(inside), (
                                f"{where}: {key} left the drawer"
                            )
                if width == 1100:
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.wait_for_function(f"() => ({inert})().length === 0")
                    page.set_viewport_size({"width": width, "height": 900})
                    page.wait_for_function(f"() => ({inert})().length === 3")
                    expect(rail).to_be_visible()
                # Each width leaves once by Escape and once by Close.
                if scheme == "light":
                    page.keyboard.press("Escape")
                else:
                    rail.get_by_role("button", name="Close", exact=True).click()
                expect(rail).to_have_count(0)
                if preview and not drawer and width <= 760:
                    expect(page.locator(".selected-work-header")).to_be_focused()
                else:
                    expect(toggle).to_be_focused()
                # The close-reveal may still be scrolling; focus must end in view.
                page.wait_for_function(in_view, timeout=2_000)
                assert page.evaluate(inert) == [], where
                assert page.locator("[inert]").count() == 0, where


# Every visible disclosure summary under a full 44px target (#1288).
_SHORT_SUMMARIES = """() => [...document.querySelectorAll('summary')]
  .filter(node => node.getClientRects().length > 0)
  .map(node => [node.textContent.trim().slice(0, 40), node.getBoundingClientRect().height])
  .filter(([, height]) => height < 44)"""


# Where the workspace starts on the page, and the toolbar's own height, read
# once layout has held still for three frames (a restyle can land late).
_WORKSPACE_GEOMETRY = """async () => {
  const read = () => {
    const workspace = document.querySelector('.library-workspace').getBoundingClientRect();
    const header = document.querySelector('header[aria-label="Library controls"]');
    return {
      top: workspace.top + window.scrollY,
      header: header.getBoundingClientRect().height,
      controls: [...header.querySelectorAll('button, a'), ...document.querySelectorAll('.compact-workspace-back')]
        .filter(node => node.getClientRects().length > 0)
        .map(node => [node.textContent.trim().slice(0, 24), node.getBoundingClientRect().height]),
    };
  };
  const frame = () => new Promise(done => requestAnimationFrame(done));
  let last = JSON.stringify(read());
  for (let still = 0, frames = 0; still < 3 && frames < 120; frames++) {
    await frame();
    const next = JSON.stringify(read());
    still = next === last ? still + 1 : 0;
    last = next;
  }
  return JSON.parse(last);
}"""


# An update stream that opens and stays open. The cohort's own `/api/v1/updates`
# answers 204, which the shell reads as a stream that is down: a second after
# every load its masthead says it is reconnecting, and at some widths that adds
# a row above everything else (#1449). A test of what selection moves holds the
# stream live, the state an owner normally sees, so nothing else moves.
_LIVE_UPDATE_STREAM = """
globalThis.EventSource = class extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.readyState = 1;
    queueMicrotask(() => this.onopen?.(new Event('open')));
  }
  close() { this.readyState = 2; }
};
"""


def test_library_toolbar_is_one_and_selection_never_moves_the_workspace() -> None:
    """Selecting or leaving a work never moves the workspace, at every width,
    theme and text size; the toolbar's labels never clip or split (#1284)."""
    styles = {
        "default": "",
        "widened": _WIDER_TEXT,
        # Important, so the shell's own root style writes keep it.
        "200%": "html { font-size: 200% !important; }",
    }
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text], writes_off_handoff=True)

        def load(route: str) -> None:
            page.goto(f"https://react-platform.invalid{route}")
            page.locator(".attention-toggle strong").filter(
                has_text=re.compile(r"works?$")
            ).wait_for()
            expect(page.locator(".cm-library-handoff h2")).to_have_text(
                "20 works are in Library. Writes are off."
            )
            page.evaluate(_TWO_FRAMES)

        def probe(where: str, width: int) -> None:
            failures = [
                failure
                for selector in (
                    ".attention-toggle",
                    ".attention-toggle strong",
                    ".library-works-heading [role='status']",
                    ".cm-library-handoff h2",
                    ".attention-rail header p",
                    ".attention-rail header button",
                    ".attention-work-count",
                    ".attention-queue-item",
                )
                for node in page.locator(selector).all()
                if node.is_visible()
                for failure in node.evaluate(_NAMED_CLIP)
            ]
            assert failures == [], f"{where}: {failures}"
            split = [
                word
                for selector in (
                    ".attention-toggle",
                    ".library-works-heading [role='status']",
                    ".attention-rail header p",
                    ".attention-rail header button",
                    ".attention-work-count",
                    # The handoff's primary link keeps its words whole (#1288).
                    ".cm-library-handoff a.button-primary",
                )
                for word in page.evaluate(_POLICY_MID_WORD, selector)
            ]
            assert split == [], f"{where}: words split {split}"
            document = page.evaluate("() => document.documentElement.scrollWidth")
            assert document <= width, f"{where}: the page is {document}px wide"
            # The primary-styled link reads as the primary button beside it would
            # (#1288): same type, no underline, a full target.
            parity = page.locator(".cm-library-handoff a.button-primary").evaluate(
                """link => {
                  const twin = document.createElement('button');
                  twin.className = 'button-primary';
                  twin.textContent = link.textContent;
                  link.after(twin);
                  const read = node => {
                    const style = getComputedStyle(node);
                    return [style.fontSize, style.fontWeight, style.fontFamily, style.lineHeight];
                  };
                  const result = {
                    same: JSON.stringify(read(link)) === JSON.stringify(read(twin)),
                    link: read(link), button: read(twin),
                    underline: getComputedStyle(link).textDecorationLine,
                    height: link.getBoundingClientRect().height,
                  };
                  twin.remove();
                  return result;
                }"""
            )
            assert parity["same"], f"{where}: primary link type {parity}"
            assert parity["underline"] == "none", f"{where}: {parity}"
            assert parity["height"] >= 44, f"{where}: {parity}"

        for dark in (False, True):
            page.emulate_media(
                color_scheme="dark" if dark else "light",
                reduced_motion="reduce" if dark else "no-preference",
            )
            for text in styles:
                for width in (1440, 1100, 884, 761, 760, 390, 320):
                    where = f"{'dark' if dark else 'light'} {text} at {width}px"
                    page.set_viewport_size({"width": width, "height": 900})
                    states = []
                    if width > 900:
                        # Wide Library selects a work on arrival; a search with
                        # no match is the landing without that selection.
                        load("/library?lens=all&q=zzz-no-match")
                        states.append(("landing", page.evaluate(_WORKSPACE_GEOMETRY)))
                        load("/library?lens=all&q=zzz-no-match&preview=case-1")
                        states.append(("selected", page.evaluate(_WORKSPACE_GEOMETRY)))
                    else:
                        load("/library?lens=all")
                        if width > 760:
                            # Selected on arrival; Back leaves it, a row returns.
                            expect(page).to_have_url(re.compile(r"preview=case-1"))
                            states.append(
                                ("selected", page.evaluate(_WORKSPACE_GEOMETRY))
                            )
                            page.get_by_role("button", name="Back to works").click()
                            page.locator(".library-work-row").first.wait_for()
                            page.evaluate(_TWO_FRAMES)
                            states.append(("back", page.evaluate(_WORKSPACE_GEOMETRY)))
                            probe(f"{where} list", width)
                            page.locator(".library-work-row").first.click()
                        else:
                            states.append(
                                ("landing", page.evaluate(_WORKSPACE_GEOMETRY))
                            )
                            probe(f"{where} list", width)
                            page.locator(".library-work-row").first.click()
                        expect(page).to_have_url(re.compile(r"preview="))
                        page.locator(".selected-work-header h2").wait_for()
                        page.evaluate(_TWO_FRAMES)
                        states.append(("selected", page.evaluate(_WORKSPACE_GEOMETRY)))
                        if width <= 760:
                            page.get_by_role("button", name="Back to works").click()
                            page.locator(".library-work-row").first.wait_for()
                            page.evaluate(_TWO_FRAMES)
                            states.append(("back", page.evaluate(_WORKSPACE_GEOMETRY)))
                    tops = [state["top"] for _, state in states]
                    assert max(tops) - min(tops) <= 1, f"{where}: {states}"
                    # The stream held live, so no reconnecting row was measured.
                    expect(page.locator(".live-state")).to_have_count(0)
                    headers = [state["header"] for _, state in states]
                    assert max(headers) - min(headers) <= 1, f"{where}: {states}"
                    small = [
                        control
                        for _, state in states
                        for control in state["controls"]
                        if control[1] < 44
                    ]
                    assert small == [], f"{where}: controls under 44px {small}"
                    probe(where, width)
                    page.locator(".attention-toggle").click()
                    page.locator(".attention-work-group").first.wait_for()
                    page.evaluate(_TWO_FRAMES)
                    probe(f"{where} rail", width)


_OPTION_BESIDE_CHECKBOX = (
    "() => [...document.querySelectorAll('.owner-option')].map(option => {"
    " const box = option.querySelector('input').getBoundingClientRect();"
    " const name = option.querySelector('span').getBoundingClientRect();"
    " return name.left >= box.right && name.top < box.bottom })"
)


def test_owner_controls_keep_every_word_whole_in_both_themes() -> None:
    """The entry and subset controls, with the previous value among their
    choices, break no word at any width or text size, in light and dark, with
    motion and reduced (#1285)."""
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        _serve_cohort(page, stylesheet=lambda: styles[text])
        # The first work opens on the cohort's case that holds every control.
        owner_case = cast(
            "dict[str, object]", rich_payload("/api/v1/review-cases/case-owner")
        )
        page.route(
            "**/api/v1/review-cases/case-1",
            lambda route: route.fulfill(
                content_type="application/json",
                body=json.dumps(owner_case | {"review_case_id": "case-1"}),
            ),
        )
        # The library's own words in the suggestions may break; no other may.
        title_field = cast("list[dict[str, Any]]", owner_case["fields"])[0]
        library = {
            word
            for suggestion in title_field["owner_control"]["entry"]["suggestions"]
            for word in suggestion["value"].split()
        }
        backgrounds = {}
        # Light with motion, then dark with reduced motion.
        for reduced in (False, True):
            scheme = "dark" if reduced else "light"
            page.emulate_media(
                color_scheme="dark" if reduced else "light",
                reduced_motion="reduce" if reduced else "no-preference",
            )
            for text in styles:
                for width in (1440, 1100, 884, 761, 760, 390, 320):
                    where = f"{scheme}, {text} text at {width}px"
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto(
                        "https://react-platform.invalid/library?lens=all"
                        "&preview=case-1&field=title"
                    )
                    title = page.get_by_role("region", name="Your value for Title")
                    title.get_by_role("button", name="Enter a value").click()
                    expect(
                        title.get_by_role(
                            "button",
                            name="Use previous value “Uncharacteristically Interdisciplinary”",
                        )
                    ).to_be_visible()
                    page.evaluate(_TWO_FRAMES)
                    # The suggestions' own copy and every other word stay whole.
                    broken = [
                        word
                        for word in page.evaluate(_POLICY_MID_WORD, ".owner-controls")
                        if word.strip("“”") not in library
                    ]
                    assert not broken, f"{where}, entry: {broken}"
                    page.goto(
                        "https://react-platform.invalid/library?lens=all"
                        "&preview=case-1&field=genres"
                    )
                    genres = page.get_by_role("region", name="Your value for Genres")
                    genres.get_by_role("button", name="Choose genres").click()
                    expect(genres.get_by_role("checkbox")).to_have_count(3)
                    page.evaluate(_TWO_FRAMES)
                    # A long genre wraps beside its checkbox; only a single
                    # word too wide for that space at enlarged text may not.
                    beside = page.evaluate(_OPTION_BESIDE_CHECKBOX)
                    assert text != "default" or all(beside), f"{where}: {beside}"
                    broken = page.evaluate(_POLICY_MID_WORD, ".owner-controls")
                    assert not broken, f"{where}: {broken}"
                    assert (
                        page.evaluate("document.documentElement.scrollWidth") <= width
                    ), where
                    # The controls never animate; reduced motion's global
                    # 0.01ms floor is the only duration they may carry.
                    transitions = page.evaluate(
                        "() => [...document.querySelectorAll('.owner-controls *')]"
                        ".map(node => getComputedStyle(node).transitionDuration)"
                        ".filter(value => parseFloat(value) > 0.0001)"
                    )
                    assert not transitions, f"{where}: {transitions}"
            backgrounds[scheme] = page.evaluate(
                "getComputedStyle(document.querySelector('.owner-controls'))"
                ".backgroundColor + getComputedStyle(document.querySelector("
                "'.owner-controls')).color"
            )
        assert backgrounds["light"] != backgrounds["dark"], backgrounds


# An update stream the test drops, restores and publishes on, so the masthead's
# status can change in place rather than across reloads.
_CONTROLLED_UPDATE_STREAM = """
globalThis.EventSource = class extends EventTarget {
  constructor(url) {
    super();
    this.url = url;
    this.readyState = 1;
    (globalThis.__streams ??= []).push(this);
    queueMicrotask(() => this.onopen?.(new Event('open')));
  }
  close() { this.readyState = 2; }
};
globalThis.__sequence = 0;
globalThis.__publish = (topic) => {
  for (const stream of globalThis.__streams ?? [])
    stream.dispatchEvent(new MessageEvent(topic, {
      data: JSON.stringify({sequence: ++globalThis.__sequence, subject: null, topic}),
    }));
};
globalThis.__drop = () => {
  for (const stream of globalThis.__streams ?? []) {
    stream.readyState = 0;
    stream.onerror?.(new Event('error'));
  }
};
globalThis.__restore = () => {
  for (const stream of globalThis.__streams ?? []) {
    stream.readyState = 1;
    stream.onopen?.(new Event('open'));
  }
};
"""

# Every layout shift that moved a node of the masthead outside its status slot.
# What the masthead's own size moves below it is read from its box instead,
# since a route's content also moves with the reads it waits on.
_SHIFTS_OUTSIDE_STATUS = """
(() => {
  globalThis.__shifts = [];
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      const moved = entry.sources.map((source) => source.node)
        .map((node) => node?.nodeType === 1 ? node : node?.parentElement)
        .filter((node) => node?.closest('.masthead') && !node.closest('.shell-status'));
      if (moved.length)
        globalThis.__shifts.push([entry.value, moved.map((node) => node.className || node.tagName)]);
    }
  }).observe({type: 'layout-shift', buffered: true});
})();
"""

_MASTHEAD_GEOMETRY = """() => {
  const box = (selector) => {
    const bounds = document.querySelector(selector).getBoundingClientRect();
    return [bounds.left, bounds.top, bounds.width, bounds.height].map(Math.round);
  };
  return {
    masthead: box('.masthead'),
    nav: box('.masthead nav'),
    static: document.documentElement.dataset.masthead === 'static',
  };
}"""


def test_masthead_holds_still_as_a_page_becomes_scrollable() -> None:
    """With classic scrollbars, a page growing tall enough to scroll moves no
    part of the masthead, even at the widths where the scrollbar's gutter would
    cross one of its steps (#1291)."""
    for width in (1025, 761):
        with ExitStack() as stack:
            playwright = stack.enter_context(sync_playwright())
            # Headless Chromium hides scrollbars unless asked not to.
            browser = playwright.chromium.launch(
                headless=True, ignore_default_args=["--hide-scrollbars"]
            )
            stack.callback(browser.close)
            page = browser.new_page(
                viewport={"width": width, "height": 900}, locale="en-US"
            )
            page.add_init_script(_LIVE_UPDATE_STREAM)
            _serve_cohort(page)
            page.goto("https://react-platform.invalid/library?lens=all")
            page.wait_for_load_state("networkidle")
            expect(page.locator("#main-content h1").first).to_be_visible()
            scrolls = "document.scrollingElement.scrollHeight > innerHeight"
            page.evaluate(
                "Object.assign(document.querySelector('main').style,"
                " {height: '0', overflow: 'hidden'})"
            )
            page.evaluate(_TWO_FRAMES)
            assert not page.evaluate(scrolls), width
            short = page.evaluate(_MASTHEAD_GEOMETRY)
            page.evaluate("document.querySelector('main').removeAttribute('style')")
            page.evaluate(_TWO_FRAMES)
            assert page.evaluate(scrolls), width
            assert page.evaluate(_MASTHEAD_GEOMETRY) == short, width


# The preparation states the masthead names, as overrides of the cohort's read.
_PREPARATION_STATES: dict[str, dict[str, object]] = {
    "absent": {"libraries": []},
    "Preparation idle": {"active": False, "actionable": False},
    "Preparing libraries": {"actionable": False},
    "Preparation needs attention": {},
    "Preparation paused": {"paused": True},
    "Preparation stopped": {"recovery_required": True},
}


def test_masthead_holds_still_while_status_changes() -> None:
    """Preparation loading, absent and in each of its states, and the update
    stream dropping and returning, never move the masthead or its
    destinations: nothing outside the status slot shifts and no label in the
    masthead is clipped, at every width of the ladder. Enlarged text is root font-size 200% (rem and container
    queries respond to it; px media queries do not): there the destinations
    keep their place, and the masthead may change size only while it is
    static, in the page's flow (#1291)."""
    styles = {"100%": "", "200%": "html { font-size: 200% !important; }"}
    text = "100%"
    state = {"preparation": "absent"}
    held: list[Route] = []

    def preparation(route: Route) -> None:
        if state["preparation"] == "loading":
            held.append(route)
            return
        payload = cast("dict[str, object]", rich_payload("/api/v1/review-preparation"))
        payload |= _PREPARATION_STATES[state["preparation"]]
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_CONTROLLED_UPDATE_STREAM)
        page.add_init_script(_SHIFTS_OUTSIDE_STATUS)
        page.clock.install()
        _serve_cohort(page, stylesheet=lambda: styles[text])
        page.route("**/api/v1/review-preparation", preparation)
        trigger = page.locator(".masthead .preparation-activity > button")
        notice = page.locator(".masthead .live-state")
        for text in styles:
            for width in (1440, 1200, 1100, 1025, 1024, 884, 761, 760, 390, 375, 320):
                where = f"{text} text at {width}px"
                page.set_viewport_size({"width": width, "height": 900})
                state["preparation"] = "loading"
                page.goto("https://react-platform.invalid/settings?group=connection")
                page.get_by_role("heading", name="Settings", exact=True).wait_for()
                page.evaluate(_TWO_FRAMES)
                expect(trigger).to_have_count(0)
                states = {"loading": page.evaluate(_MASTHEAD_GEOMETRY)}
                clipped: list[str] = []
                # The route has painted; from here only status changes.
                page.evaluate("() => { globalThis.__shifts.length = 0 }")
                state["preparation"] = "absent"
                while held:
                    preparation(held.pop())
                page.evaluate(_TWO_FRAMES)
                states["absent"] = page.evaluate(_MASTHEAD_GEOMETRY)
                # Each state live, then with the stream down; the notice waits
                # a second before it shows, which the clock runs through.
                for label in list(_PREPARATION_STATES)[1:]:
                    state["preparation"] = label
                    page.evaluate("() => globalThis.__publish('preparation')")
                    expect(trigger).to_have_text(label)
                    page.evaluate(_TWO_FRAMES)
                    states[label] = page.evaluate(_MASTHEAD_GEOMETRY)
                    clipped += [
                        f"{label}: {c}" for c in page.evaluate(_MASTHEAD_CLIPPED)
                    ]
                    page.evaluate("() => globalThis.__drop()")
                    page.clock.run_for(1_100)
                    expect(notice).to_be_visible()
                    page.evaluate(_TWO_FRAMES)
                    states[f"{label}, reconnecting"] = page.evaluate(_MASTHEAD_GEOMETRY)
                    clipped += [
                        f"{label}, reconnecting: {c}"
                        for c in page.evaluate(_MASTHEAD_CLIPPED)
                    ]
                    page.evaluate("() => globalThis.__restore()")
                    expect(notice).to_have_count(0)
                page.evaluate(_TWO_FRAMES)
                states["restored"] = page.evaluate(_MASTHEAD_GEOMETRY)
                # Whatever the status says, every control keeps its whole label.
                assert clipped == [], f"{where}: {clipped}"
                navs = {state["nav"][0] for state in states.values()}
                assert len(navs) == 1, f"{where}: the destinations moved {states}"
                boxes = {
                    json.dumps([state["masthead"], state["nav"]])
                    for state in states.values()
                }
                if text == "100%":
                    assert len(boxes) == 1, f"{where}: the masthead moved {states}"
                    shifts = page.evaluate("() => globalThis.__shifts")
                    assert shifts == [], f"{where}: {shifts}"
                elif len(boxes) > 1:
                    assert all(state["static"] for state in states.values()), (
                        f"{where}: a sticky masthead changed size {states}"
                    )


# The masthead's links and buttons whose label runs past their box.
_MASTHEAD_CLIPPED = """() => [...document.querySelectorAll('.masthead :is(a, button)')]
  .filter((node) => node.getClientRects().length > 0 && node.scrollWidth > node.clientWidth)
  .map((node) => `${node.textContent.trim()} by ${node.scrollWidth - node.clientWidth}px`)"""


# Where the masthead and the focused control are, and every sticky region.
_MASTHEAD_COVER = """() => {
  const masthead = document.querySelector('.masthead');
  const bounds = masthead.getBoundingClientRect();
  const focused = document.activeElement;
  const focus = focused && focused !== document.body && !masthead.contains(focused)
    ? focused.getBoundingClientRect() : null;
  const sticky = [...document.querySelectorAll('main *')]
    .filter((node) => getComputedStyle(node).position === 'sticky'
      && getComputedStyle(node).top !== 'auto' && node.getClientRects().length > 0)
    .map((node) => [String(node.className).slice(0, 40), Math.round(node.getBoundingClientRect().top)]);
  return {
    static: document.documentElement.dataset.masthead === 'static',
    position: getComputedStyle(masthead).position,
    share: bounds.height / innerHeight,
    height: bounds.height,
    bottom: bounds.bottom,
    focus: focus && [focused.tagName, (focused.textContent || '').trim().slice(0, 32),
      Math.round(focus.top), Math.round(focus.bottom)],
    sticky,
  };
}"""


def test_compact_and_enlarged_masthead_never_covers_content() -> None:
    """The masthead sticks only while it takes at most a quarter of the
    viewport, and otherwise scrolls with the page; whichever it is, no focused
    control and no sticky region lies beneath it, and the preparation popover
    opens inside the viewport. Enlarged text is root font-size 200%, which rem
    and container queries follow and px media queries do not (#1291)."""
    styles = {"100%": "", "200%": "html { font-size: 200% !important; }"}
    text = "100%"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text])
        for text in styles:
            for width, height in (
                (375, 812),
                (375, 667),
                (320, 568),
                (320, 640),
                (390, 844),
                (1024, 768),
                (1440, 900),
            ):
                page.set_viewport_size({"width": width, "height": height})
                for route in (
                    "/library?lens=all",
                    "/policy?section=identity",
                    "/activity?eventType=all&rangeDays=30&page=1&pageSize=25",
                ):
                    where = f"{text} text at {width}x{height} on {route}"
                    page.goto(f"https://react-platform.invalid{route}")
                    page.locator("main h1").first.wait_for()
                    page.locator(".masthead .preparation-activity > button").wait_for()
                    page.evaluate(_TWO_FRAMES)
                    cover = page.evaluate(_MASTHEAD_COVER)
                    assert cover["static"] == (cover["share"] > 0.25), (
                        f"{where}: {cover}"
                    )
                    assert cover["position"] == (
                        "relative" if cover["static"] else "sticky"
                    ), f"{where}: {cover}"
                    if text == "100%" and (width, height) == (375, 812):
                        # No phone pays more chrome than before (#1291).
                        assert cover["height"] <= 160.5, f"{where}: {cover}"
                        top = page.evaluate(
                            "() => document.querySelector('main').getBoundingClientRect().top"
                        )
                        assert top <= 176, f"{where}: content starts at {top}"
                    page.locator(".skip-link").focus()
                    for _ in range(18):
                        page.keyboard.press("Tab")
                        cover = page.evaluate(_MASTHEAD_COVER)
                        if cover["focus"] is not None:
                            assert cover["focus"][2] >= cover["bottom"] - 1, (
                                f"{where}: focus under the masthead {cover}"
                            )
                    page.evaluate(
                        "() => window.scrollTo(0, document.body.scrollHeight / 2)"
                    )
                    page.evaluate(_TWO_FRAMES)
                    cover = page.evaluate(_MASTHEAD_COVER)
                    if not cover["static"]:
                        under = [
                            region
                            for region in cover["sticky"]
                            if region[1] < cover["bottom"] - 1
                        ]
                        assert under == [], f"{where}: under the masthead {cover}"
        # The popover opens inside the viewport, however the status wraps or
        # however tall the masthead grows, and each of its controls is in view
        # when focused (#1491).
        for width, height in (
            (1440, 900),
            (1100, 900),
            (884, 900),
            (390, 844),
            (375, 812),
            (320, 640),
        ):
            for text in styles:
                where = f"{text} text at {width}x{height}"
                page.set_viewport_size({"width": width, "height": height})
                page.goto("https://react-platform.invalid/settings?group=connection")
                trigger = page.locator(".masthead .preparation-activity > button")
                trigger.click()
                popover = page.get_by_role("region", name="Background activity")
                expect(popover).to_be_visible()
                box = popover.bounding_box()
                assert box is not None
                assert box["x"] >= 0 and box["x"] + box["width"] <= width, (
                    f"{where}: {box}"
                )
                assert box["y"] >= 0 and box["y"] + box["height"] <= height + 0.5, (
                    f"{where}: {box}"
                )
                for control in popover.get_by_role("button").all():
                    control.focus()
                    place = control.bounding_box()
                    assert place is not None
                    assert place["y"] >= 0 and place["y"] + place["height"] <= height, (
                        f"{where}: {control.text_content()} at {place}"
                    )
                page.keyboard.press("Escape")
                expect(trigger).to_be_focused()


# The words an owner reads as interface: headings, controls, labels and the
# pager's status. The #1290 review choices, covers and works rows are proved
# by their own tests; works rows are walked here too, at desktop widths, where
# enlarged text must widen their column rather than split their words.
_UI_LABELS = (
    ":is(h1, h2, h3, h4, button, a, label, summary, legend, dt, th, "
    ".library-pager > span):not(:is({owned}), :is({owned}) *)".format(
        owned=".workspace-detail .value-choice, .field-filmstrip, .cover-card, "
        ".library-cover-card, .library-work-row, .metadata-choice"
    )
)
_WORKS_ROWS = ".library-work-row"
# A work's title and authors in the Attention rail and on a decision's
# choices: data words that must stay whole wherever their line could hold them,
# at every width (#1530).
_WORK_TITLES = ".attention-work-identity, .workspace-detail .value-choice strong"
# Of the split words `_POLICY_MID_WORD` found, those the layout split without
# need. A word may break only when it is wider than its whole line box. For a
# word of the interface that box must also hold at least half the page's
# width: a narrower one is a column the layout squeezed. A data token -- an
# id, a path, an address, an unbroken title -- may break in any box it
# overflows.
_NEEDLESS_SPLITS = """([selector, words]) => {
  const main = document.querySelector('#main-content');
  const page = main.clientWidth - parseFloat(getComputedStyle(main).paddingLeft)
    - parseFloat(getComputedStyle(main).paddingRight);
  const token = word => /[\\/_:.@=#?&<>\\d]/.test(word) || word.length > 24;
  const lineBox = node => {
    let box = node;
    while (['inline', 'contents'].includes(getComputedStyle(box).display)) box = box.parentElement;
    const style = getComputedStyle(box);
    return box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  };
  const needless = new Set();
  for (const node of document.querySelectorAll(selector)) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/[^\\s\\u2010-\\u2014-]+/g)) {
        if (!words.includes(match[0])) continue;
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
        if (new Set(rects.map(rect => Math.round(rect.top))).size < 2) continue;
        const width = rects.reduce((sum, rect) => sum + rect.width, 0);
        const room = lineBox(text.parentElement);
        const excused = width > room - 1 && (token(match[0]) || room >= page / 2);
        if (!excused)
          needless.add(`${match[0]} (${Math.round(width)}px in ${Math.round(room)}px, `
            + `${node.tagName.toLowerCase()} "${node.textContent.trim().slice(0, 40)}")`);
      }
  }
  return [...needless];
}"""


def test_words_break_only_where_they_must() -> None:
    """No interface word splits where the layout could have kept it whole, on
    every route state at every frozen width and both text sizes, and no page
    is wider than the window (#1291). Enlarged text is root font-size 200%.
    From 1100px up a works row's words, title included, stay whole too, and a
    work's title in the Attention rail and on its choices does at every width
    (#1530)."""
    routes = {
        "library": "/library?lens=all",
        "selected": "/library?lens=all&preview=case-1",
        "attention": "/library?lens=all&attention=open",
        "review": "/review/case-1",
        "policy": "/policy?section=identity",
        "activity": "/activity?eventType=all&rangeDays=30&page=1&pageSize=25",
        "settings": "/settings?group=connection",
    }
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text])
        failures = []
        for text in styles:
            for name, route in routes.items():
                for width in (1440, 1200, 1100, 1024, 884, 760, 390, 375, 320):
                    where = f"{name}, {text} text at {width}px"
                    page.set_viewport_size({"width": width, "height": 900})
                    page.goto("https://react-platform.invalid" + route)
                    page.wait_for_load_state("networkidle")
                    expect(page.locator("#main-content h1").first).to_be_visible()
                    page.evaluate(_TWO_FRAMES)
                    split = page.evaluate(_POLICY_MID_WORD, _UI_LABELS)
                    if split:
                        needless = page.evaluate(_NEEDLESS_SPLITS, [_UI_LABELS, split])
                        failures += [f"{where}: {word}" for word in needless]
                    rows = page.evaluate(_POLICY_MID_WORD, _WORKS_ROWS)
                    if rows and width >= 1100:
                        needless = page.evaluate(_NEEDLESS_SPLITS, [_WORKS_ROWS, rows])
                        failures += [f"{where}: works row {word}" for word in needless]
                    titles = page.evaluate(_POLICY_MID_WORD, _WORK_TITLES)
                    if titles:
                        needless = page.evaluate(
                            _NEEDLESS_SPLITS, [_WORK_TITLES, titles]
                        )
                        failures += [f"{where}: work title {word}" for word in needless]
                    overflow = page.evaluate(
                        "document.scrollingElement.scrollWidth - innerWidth"
                    )
                    if overflow > 0:
                        failures.append(f"{where}: the page is {overflow}px too wide")
        assert failures == [], "\n".join(failures)


# Cells of one Activity row that overlap another cell of the same row.
_ROW_OVERLAPS = """() => [...document.querySelectorAll('.cm-activity-event-group article')]
  .flatMap(row => {
    const cells = [...row.children].filter(cell => {
      const box = cell.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && !cell.matches('.sr-only');
    });
    return cells.flatMap((cell, index) => cells.slice(index + 1).flatMap(other => {
      const [a, b] = [cell.getBoundingClientRect(), other.getBoundingClientRect()];
      const across = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const down = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return across > 1 && down > 1
        ? [`"${cell.textContent.trim().slice(0, 20)}" overlaps "${other.textContent.trim().slice(0, 20)}"`]
        : [];
    }));
  })"""


# Each pagination or filter control cut short by its own box or by an
# ancestor that clips it. A row that scrolls, as the phone filters do, is not
# cutting what it scrolls to.
_CUT_CONTROLS = """() => [...document.querySelectorAll(
    '.cm-activity-pagination :is(button, input, .cm-dropdown-trigger, strong, span),'
    + ' .cm-activity-filters button, .cm-activity-search input,'
    + ' .cm-activity-range :is(.cm-dropdown-trigger, .cm-dropdown-trigger > span)')]
  .filter((node) => node.getClientRects().length)
  .flatMap((node) => {
    const name = `${node.className || node.tagName} "${node.textContent.trim().slice(0, 32)}"`;
    const box = node.getBoundingClientRect();
    const found = node.scrollWidth - node.clientWidth > 1
      ? [`${name} is clipped by ${node.scrollWidth - node.clientWidth}px`] : [];
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      const overflow = getComputedStyle(parent).overflowX;
      if (overflow === 'auto' || overflow === 'scroll') break;
      if (overflow === 'visible') continue;
      const bounds = parent.getBoundingClientRect();
      const past = Math.max(box.right - bounds.right, bounds.left - box.left);
      if (past > 0.5) {
        found.push(`${name} is cut by ${Math.round(past)}px`);
        break;
      }
    }
    return found;
  })"""


# How many rows the pager's pages and page jump take.
_PAGER_ROWS = """() => {
  const boxes = [...document.querySelector('.cm-activity-pagination-nav').children]
    .filter((node) => node.getClientRects().length)
    .map((node) => node.getBoundingClientRect())
    .sort((a, b) => a.top - b.top);
  let rows = 0;
  let bottom = -Infinity;
  for (const box of boxes) if (box.top >= bottom) { rows += 1; bottom = box.bottom; }
  return rows;
}"""


# Activity's filter chips and Details buttons shorter than a 2.75rem target.
_SHORT_LEDGER_TARGETS = """() => {
  const floor = 2.75 * parseFloat(getComputedStyle(document.documentElement).fontSize);
  return [...document.querySelectorAll(
      '.cm-activity-filters button, .cm-activity-event-group article > button, '
      + '.cm-activity-pagination button, .cm-activity-page-jump input')]
    .filter((node) => node.getClientRects().length)
    .map((node) => [node.textContent.trim().slice(0, 24), node.getBoundingClientRect().height])
    .filter(([, height]) => height < floor - 0.5)
    .map(([name, height]) => `"${name}" is ${Math.round(height)}px tall, under ${floor}px`);
}"""


def test_activity_ledger_rows_fit_at_every_width_and_text_size() -> None:
    """At ordinary text and with root font-size 200%, every Activity row -- a
    check's members open -- keeps its promises and whole labels, with no cell
    clipped, past its row or over another, and no pagination or filter control
    cut short, from the widest window to the narrowest phone (#1291). Every
    filter chip, Details button and pager control is a full 2.75rem target
    (#1491)."""
    styles = {"100%": "", "200%": "html { font-size: 200% !important; }"}
    text = "100%"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text])
        for text in styles:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(
                "https://react-platform.invalid/activity"
                "?eventType=all&rangeDays=30&page=1&pageSize=25"
            )
            page.locator("article.is-checks").get_by_role("button").click()
            expect(page.locator(".cm-activity-checks > li").first).to_be_visible()
            for width in (1440, 1100, 884, 761, 760, 430, 390, 375, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.evaluate(_TWO_FRAMES)
                where = f"{text} text at {width}px"
                if text == "100%" and 430 <= width <= 760:
                    # The pages and the page jump wrap only when one row cannot hold them.
                    assert page.evaluate(_PAGER_ROWS) == 1, where
                # Cards follow the ledger's own width, not the window's.
                cards = page.evaluate(
                    "() => getComputedStyle(document.querySelector('.cm-activity-columns'))"
                    ".display === 'none'"
                )
                assert page.evaluate(_ROW_PROMISES, cards) == [], where
                labels = page.evaluate(_ROW_LABELS, cards)
                assert labels == [], f"{where}: {labels}"
                overlaps = page.evaluate(_ROW_OVERLAPS)
                assert overlaps == [], f"{where}: {overlaps}"
                clipped = [
                    failure
                    for node in page.locator(
                        ".cm-activity-event-group article button"
                    ).all()
                    for failure in node.evaluate(_NAMED_CLIP)
                ]
                assert clipped == [], f"{where}: {clipped}"
                cut = page.evaluate(_CUT_CONTROLS)
                assert cut == [], f"{where}: {cut}"
                short = page.evaluate(_SHORT_LEDGER_TARGETS)
                assert short == [], f"{where}: {short}"


# Each Policy selected value cut short inside its control.
_TRUNCATED_VALUES = """() => [...document.querySelectorAll(
    '.cm-policy-route .cm-dropdown-trigger > span')]
  .filter((node) => node.getClientRects().length && node.scrollWidth > node.clientWidth + 1)
  .map((node) => `"${node.textContent.trim()}" by ${node.scrollWidth - node.clientWidth}px`)"""
# Each word split across lines although the column it sits in could hold it.
_SPLIT_THOUGH_THE_COLUMN_FITS = """([selector, column]) => {
  const room = document.querySelector(column).clientWidth;
  const found = [];
  for (const node of document.querySelectorAll(selector)) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode())
      for (const match of text.data.matchAll(/[^\\s\\u2010-\\u2014-]+/g)) {
        const range = document.createRange();
        range.setStart(text, match.index);
        range.setEnd(text, match.index + match[0].length);
        const rects = [...range.getClientRects()].filter((rect) => rect.width > 0);
        if (new Set(rects.map((rect) => Math.round(rect.top))).size < 2) continue;
        const width = rects.reduce((sum, rect) => sum + rect.width, 0);
        if (width < room) found.push(`${match[0]} (${Math.round(width)}px; the column is ${room}px)`);
      }
  }
  return found;
}"""


def test_insets_and_columns_never_cut_a_word_short() -> None:
    """A Policy value is never truncated in its control, at any width and
    either text size, and on the narrowest phones at 200% text the provider
    recovery card's insets leave its words the line the Activity column
    allows (#1291)."""
    styles = {"100%": "", "200%": "html { font-size: 200% !important; }"}
    text = "100%"
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text])
        failures = []
        for text in styles:
            for width in (1440, 1200, 1100, 1024, 884, 761, 760, 390, 320):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto("https://react-platform.invalid/policy?section=identity")
                page.wait_for_load_state("networkidle")
                expect(page.locator(".cm-policy-field").first).to_be_visible()
                page.evaluate(_TWO_FRAMES)
                failures += [
                    f"Policy, {text} text at {width}px: {value}"
                    for value in page.evaluate(_TRUNCATED_VALUES)
                ]
        text = "200%"
        for width in (390, 375, 320):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(
                "https://react-platform.invalid/activity"
                "?eventType=all&rangeDays=30&page=1&pageSize=25"
            )
            page.wait_for_load_state("networkidle")
            expect(page.locator(".activity-recovery")).to_be_visible()
            page.evaluate(_TWO_FRAMES)
            failures += [
                f"recovery, 200% text at {width}px: {word}"
                for word in page.evaluate(
                    _SPLIT_THOUGH_THE_COLUMN_FITS,
                    [".activity-recovery", ".cm-activity-route"],
                )
            ]
        assert failures == [], "\n".join(failures)


def test_policy_sidebar_keeps_its_header_in_view_on_short_screens() -> None:
    """The Policy sidebar's header and first chapter can each be brought into
    view and pressed, clear of the masthead and the finish tray, on short
    phones at both text sizes and on short windows with root font-size 200%
    (#1291)."""
    styles = {"default": "", "200%": "html { font-size: 200% !important; }"}
    text = "default"
    cases = [
        (text, width, height)
        for text in styles
        for width, height in ((320, 568), (375, 667), (320, 640))
    ] + [
        ("200%", width, height)
        for width, height in ((1024, 768), (884, 800), (761, 600))
    ]
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: styles[text])
        for text, width, height in cases:
            where = f"{text} text at {width}x{height}"
            page.set_viewport_size({"width": width, "height": height})
            page.goto("https://react-platform.invalid/policy?section=identity")
            expect(page.locator(".cm-finish-tray")).to_be_visible()
            page.evaluate(_TWO_FRAMES)
            for target in (
                ".cm-policy-sidebar > header strong",
                ".cm-policy-sidebar > header small",
                ".cm-policy-sidebar nav button",
            ):
                found = page.locator(target).first.evaluate(
                    """node => {
                      node.scrollIntoView({block: 'nearest'});
                      const box = node.getBoundingClientRect();
                      const x = box.left + Math.min(box.width / 2, 20);
                      const y = box.top + Math.min(box.height / 2, 12);
                      const hit = y >= 0 && y < innerHeight ? document.elementFromPoint(x, y) : null;
                      const tray = document.querySelector('.cm-finish-tray').getBoundingClientRect();
                      const masthead = document.querySelector('.masthead').getBoundingClientRect();
                      return {
                        text: node.textContent.trim().slice(0, 24),
                        height: Math.round(box.height),
                        pressable: !!hit && (hit === node || node.contains(hit)),
                        underTray: box.bottom > tray.top + 1 && box.top < tray.bottom - 1,
                        underMasthead: box.top < masthead.bottom - 1 && box.bottom > masthead.top + 1,
                      };
                    }"""
                )
                assert found["pressable"], f"{where}: {found}"
                assert not found["underTray"], f"{where}: {found}"
                assert not found["underMasthead"], f"{where}: {found}"


# Controls in `selector` that run past the window or past an ancestor that
# clips or scrolls them, named with how far.
_ESCAPED_CONTROLS = """selector => [...document.querySelectorAll(selector)].flatMap(node => {
  const box = node.getBoundingClientRect();
  if (!box.width || !box.height) return [];
  const name = `${node.tagName.toLowerCase()} "${(node.textContent || node.getAttribute('aria-label') || node.name || '').trim().slice(0, 30)}"`;
  const found = [];
  if (box.left < -1 || box.right > document.documentElement.clientWidth + 1)
    found.push(`${name} runs past the window (${Math.round(box.left)}..${Math.round(box.right)})`);
  for (let ancestor = node.parentElement; ancestor && ancestor !== document.body; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor);
    const edge = ancestor.getBoundingClientRect();
    if (style.overflowX !== 'visible' && (box.left < edge.left - 1 || box.right > edge.right + 1))
      found.push(`${name} runs ${Math.round(Math.max(edge.left - box.left, box.right - edge.right))}px past ${ancestor.className || ancestor.tagName}`);
  }
  return found;
})"""


# The Settings section picker's value cut short, or its trigger under 44px tall.
_CUT_SECTION_PICKER = """() => [...document.querySelectorAll(
    '.settings-compact-sections .cm-dropdown-trigger > span')]
  .filter((node) => node.getClientRects().length)
  .flatMap((node) => {
    const name = `section picker "${node.textContent.trim()}"`;
    const height = node.parentElement.getBoundingClientRect().height;
    return [
      ...(node.scrollWidth > node.clientWidth + 1
        ? [`${name} is cut by ${node.scrollWidth - node.clientWidth}px`] : []),
      ...(height < 44 ? [`${name} is ${Math.round(height)}px tall`] : []),
    ];
  })"""


def test_finish_actions_and_settings_fields_stay_inside_their_containers() -> None:
    """With root font-size 200%, no Policy finish step or action and no
    Settings field, label or control runs past the window or is clipped by
    the editor or sheet that holds it, at every frozen width (#1291)."""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page, stylesheet=lambda: "html { font-size: 200% !important; }")
        routes = {
            "/policy?section=identity": ".cm-finish-tray :is(button, ol > li)",
        } | {
            f"/settings?group={group}": (
                ".settings-sheet :is(label, input, select, button, "
                ".settings-field-control)"
            )
            for group in (
                "connection",
                "libraries",
                "providers",
                "reviewer",
                "background",
                "access",
                "appearance",
            )
        }
        failures = []
        for width in (1440, 1100, 1024, 884, 760, 390, 375, 320):
            page.set_viewport_size({"width": width, "height": 900})
            for route, controls in routes.items():
                page.goto("https://react-platform.invalid" + route)
                page.wait_for_load_state("networkidle")
                expect(page.locator("#main-content h1").first).to_be_visible()
                page.evaluate(_TWO_FRAMES)
                assert page.locator(controls).count(), f"{route} has no {controls}"
                failures += [
                    f"{route} at {width}px: {failure}"
                    for failure in page.evaluate(_ESCAPED_CONTROLS, controls)
                ]
                failures += [
                    f"{route} at {width}px: {failure}"
                    for failure in page.evaluate(_CUT_SECTION_PICKER)
                ]
        assert failures == [], "\n".join(failures)


# Finish tray steps and actions that run past the editor holding them.
_PAST_THE_EDITOR = """() => {
  const editor = document.querySelector('.cm-policy-editor').getBoundingClientRect();
  return [...document.querySelectorAll('.cm-finish-tray :is(button, ol > li)')]
    .filter((node) => node.getClientRects().length)
    .flatMap((node) => {
      const box = node.getBoundingClientRect();
      return box.left < editor.left - 1 || box.right > editor.right + 1
        ? [`"${node.textContent.trim()}" spans ${Math.round(box.left)}..${Math.round(box.right)}`
          + ` past the editor's ${Math.round(editor.left)}..${Math.round(editor.right)}`]
        : [];
    });
}"""


def _enlarged_cohort(
    stack: ExitStack, width: int, height: int, *, writes_off_handoff: bool = False
) -> Page:
    """A page over the cohort with root font-size 200% and the stream held live."""
    playwright = stack.enter_context(sync_playwright())
    browser = playwright.chromium.launch(headless=True)
    stack.callback(browser.close)
    page = browser.new_page(viewport={"width": width, "height": height}, locale="en-US")
    page.add_init_script(_LIVE_UPDATE_STREAM)
    _serve_cohort(
        page,
        stylesheet=lambda: "html { font-size: 200% !important; }",
        writes_off_handoff=writes_off_handoff,
    )
    return page


def test_write_permission_panel_fits_a_phone_at_200_percent_text() -> None:
    """With root font-size 200%, the writes-off card and the write-permission
    panel it opens keep their heading, text and controls inside themselves and
    the phone's viewport, and nothing scrolls sideways (#1490)."""

    def writes_off(route: Route) -> None:
        payload = cast("dict[str, Any]", rich_payload(route.request.url))
        payload["waiting"]["applying"] = 0
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    with ExitStack() as stack:
        page = _enlarged_cohort(stack, 390, 844, writes_off_handoff=True)
        page.route("**/api/v1/activity?*", writes_off)
        failures = []
        page.goto("https://react-platform.invalid/activity")
        entry = page.locator(".cm-write-consent").get_by_role(
            "button", name="Review write permissions"
        )
        for surface in (".cm-write-consent", "#activity-consent"):
            if surface == "#activity-consent":
                page.set_viewport_size({"width": 390, "height": 844})
                entry.click()
                page.get_by_role("heading", name="Enable guarded writes").wait_for()
            else:
                entry.wait_for()
            for width in (390, 375, 320):
                page.set_viewport_size({"width": width, "height": 844})
                page.evaluate(_TWO_FRAMES)
                where = f"{surface} at {width}px"
                failures += [
                    f"{where}: {failure}"
                    for failure in page.evaluate(
                        _ESCAPED_CONTROLS,
                        f"{surface} :is(h2, p, label, li, button, strong)",
                    )
                ]
                past = page.evaluate(_CONTENT_OVERFLOW, surface)
                if past > 0:
                    failures.append(f"{where}: content runs {past}px past it")
                sideways = page.evaluate(
                    "() => document.documentElement.scrollWidth - innerWidth"
                )
                if sideways > 0:
                    failures.append(f"{where}: the page scrolls {sideways}px sideways")
        assert failures == [], "\n".join(failures)


def test_policy_preview_actions_stay_inside_the_editor_at_200_percent_text() -> None:
    """With root font-size 200%, the Policy finish tray's preview-stage steps
    and actions stay inside the editor at 1440, 1200, 1024 and 375px (#1490).
    While it validates, the tray grows past a third of the window and scrolls
    with the page, and the focused command stays in view (#1503)."""
    held: list[Route] = []
    with ExitStack() as stack:
        page = _enlarged_cohort(stack, 1440, 900)
        page.route(
            "**/api/v1/metadata-policy/validate", lambda route: held.append(route)
        )
        failures = []
        for width, height in ((1440, 900), (1200, 900), (1024, 768), (375, 812)):
            page.set_viewport_size({"width": width, "height": height})
            page.goto("https://react-platform.invalid/policy?section=identity")
            page.get_by_role("region", name="Title and subtitle").get_by_role(
                "button", name="Ignore"
            ).click()
            page.get_by_role("button", name="Validate and preview").click()
            page.get_by_role("button", name="Validating…").wait_for()
            page.evaluate(_TWO_FRAMES)
            focus = page.evaluate(
                "() => { const box = document.activeElement.getBoundingClientRect();"
                " return [document.activeElement.textContent.trim(), box.top, box.bottom]; }"
            )
            if focus[1] < 0 or focus[2] > height + 0.5:
                failures.append(f"validating at {width}px: {focus} is off screen")
            held.pop().fallback()
            page.get_by_role("button", name="Review activation").wait_for()
            page.evaluate(_TWO_FRAMES)
            failures += [
                f"preview tray at {width}px: {failure}"
                for failure in page.evaluate(_PAST_THE_EDITOR)
                + page.evaluate(_ESCAPED_CONTROLS, ".cm-finish-tray button")
            ]
        assert failures == [], "\n".join(failures)


def test_activity_paging_keeps_the_focused_control_in_view() -> None:
    """Moving to the next Activity page or changing the page size from the
    keyboard keeps the control that has focus in view, clear of the masthead,
    once the new page's rows have rendered, on wide and short windows and
    phones (#1490)."""

    def three_pages(route: Route) -> None:
        payload = cast("dict[str, Any]", rich_payload(route.request.url))
        ledger = payload["ledger"]
        ledger |= {"page_count": 3, "total": 3 * ledger["page_size"]}
        # A full page, so a larger page size renders more rows.
        events = payload["events"]
        payload["events"] = (
            events
            + [
                {**event, "event_id": f"{event['event_id']}-{copy}"}
                for copy in range(ledger["page_size"])
                for event in events
                if not event["check_group"]
            ]
        )[: ledger["page_size"]]
        route.fulfill(content_type="application/json", body=json.dumps(payload))

    in_view = """node => {
      const box = node.getBoundingClientRect();
      const masthead = document.querySelector('.masthead').getBoundingClientRect();
      return box.top >= Math.max(0, masthead.bottom) - 1 && box.bottom <= innerHeight + 1;
    }"""
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
        page.add_init_script(_LIVE_UPDATE_STREAM)
        _serve_cohort(page)
        page.route("**/api/v1/activity?*", three_pages)
        failures = []
        for width, height in ((1440, 900), (1440, 600), (390, 844), (320, 640)):
            where = f"{width}x{height}"
            page.set_viewport_size({"width": width, "height": height})
            page.goto(
                "https://react-platform.invalid/activity"
                "?eventType=all&rangeDays=30&page=1&pageSize=25"
            )
            following = page.get_by_role("button", name="Next", exact=True)
            following.scroll_into_view_if_needed()
            following.focus()
            page.keyboard.press("Enter")
            page.wait_for_url(re.compile(r"[?&]page=2(&|$)"))
            expect(following).to_be_focused()
            page.wait_for_timeout(300)
            if not following.evaluate(in_view):
                failures.append(f"{where}: Next is out of view")
            size = page.get_by_role("combobox", name="Events per activity page")
            size.scroll_into_view_if_needed()
            size.focus()
            page.keyboard.press("Enter")
            page.get_by_role("option", name="50 per page").focus()
            page.keyboard.press("Enter")
            page.wait_for_url(re.compile(r"[?&]pageSize=50(&|$)"))
            expect(page.locator(".cm-activity-event-group article")).to_have_count(48)
            expect(size).to_be_focused()
            page.wait_for_timeout(300)
            if not size.evaluate(in_view):
                failures.append(f"{where}: the page size is out of view")
        assert failures == [], "\n".join(failures)


# Layout shifts, and where the first works row stands when it first paints.
_FIRST_ROW_AND_SHIFTS = """(() => {
  window.__shifts = [];
  new PerformanceObserver(list => {
    for (const entry of list.getEntries())
      if (!entry.hadRecentInput) window.__shifts.push([entry.startTime, entry.value]);
  }).observe({type: 'layout-shift', buffered: true});
  const look = () => {
    const row = document.querySelector('.library-work-row');
    if (row && row.getBoundingClientRect().height) {
      window.__firstRow = {at: performance.now(), top: row.getBoundingClientRect().top + scrollY};
      return;
    }
    requestAnimationFrame(look);
  };
  requestAnimationFrame(look);
})()"""


# Writes enabled for a consented destination: the handoff names its library as
# Settings does, so it also waits on the settings read.
_CONSENTED_HANDOFF = {
    **_WRITES_OFF_CONSENT,
    "capabilities": [
        {
            "connection": "primary",
            "library": "Household ebooks",
            "state": "allowed",
            "destinations": [
                {
                    "destination": "title",
                    "state": "allowed",
                    "consented": True,
                    "prepared": 2,
                }
            ],
        }
    ],
    "waiting": {
        "applying": 0,
        "awaiting_permission": 0,
        "held_for_recovery": 0,
        "retrying": 0,
        "scheduled": 2,
    },
}


def test_library_handoff_arrives_with_the_works() -> None:
    """With the handoff's consent read held for 1.5s, and, while writes are
    enabled, the settings read that names its library held 1s longer, the works
    appear with the handoff above them, so from the moment the first row paints
    nothing moves it (#1291)."""
    held: list[Route] = []
    held_settings: list[Route] = []
    for consent, width in (
        (_WRITES_OFF_CONSENT, 1440),
        (_WRITES_OFF_CONSENT, 390),
        (_CONSENTED_HANDOFF, 1440),
        (_CONSENTED_HANDOFF, 390),
    ):
        held.clear()
        held_settings.clear()
        hold_settings = consent is _CONSENTED_HANDOFF
        where = ("enabled" if hold_settings else "off", width)
        with ExitStack() as stack:
            playwright = stack.enter_context(sync_playwright())
            browser = playwright.chromium.launch(headless=True)
            stack.callback(browser.close)
            page = browser.new_page(
                viewport={"width": width, "height": 900}, locale="en-US"
            )
            page.add_init_script(_LIVE_UPDATE_STREAM)
            page.add_init_script(_FIRST_ROW_AND_SHIFTS)
            _serve_cohort(page, writes_off_handoff=True)
            page.route(
                "**/api/v1/activity/consent-state", lambda route: held.append(route)
            )
            if hold_settings:
                page.route(
                    "**/api/v1/settings", lambda route: held_settings.append(route)
                )
            page.goto("https://react-platform.invalid/library?lens=all")
            page.wait_for_timeout(1_500)
            assert held, "the consent read was never made"
            for route in held:
                route.fulfill(content_type="application/json", body=json.dumps(consent))
            if hold_settings:
                page.wait_for_timeout(1_000)
                assert held_settings, "the settings read was never made"
                page.unroute("**/api/v1/settings")
                for route in held_settings:
                    route.fallback()
            heading = page.locator(".cm-library-handoff h2")
            expect(heading).to_be_visible()
            if hold_settings:
                expect(heading).to_contain_text("Writes are enabled")
            expect(page.locator(".library-work-row").first).to_be_visible()
            page.wait_for_timeout(2_000)
            first = page.evaluate("() => window.__firstRow")
            now = page.evaluate(
                "() => document.querySelector('.library-work-row')"
                ".getBoundingClientRect().top + scrollY"
            )
            moved = sum(
                value
                for start, value in page.evaluate("() => window.__shifts")
                if start >= first["at"] - 1
            )
            assert abs(now - first["top"]) <= 1, (where, first, now)
            assert moved == 0, (where, moved)
