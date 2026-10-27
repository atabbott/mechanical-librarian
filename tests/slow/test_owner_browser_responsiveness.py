"""Rendered owner timings with complete evidence and real preparation actors."""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
import time
from collections import deque
from contextlib import closing, nullcontext
from itertools import count
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Request, expect, sync_playwright
from scripts.verify_playwright_browser import verify_playwright_browser

from tests.support.owner_responsiveness import running_owner_fixture


def test_complete_cover_rendered_owner_journey(tmp_path: Path) -> None:
    """Five runtime/context starts and thirty same-runtime owner interactions."""
    print(json.dumps(verify_playwright_browser(), sort_keys=True))
    samples: dict[str, list[float]] = {}
    misses: list[dict[str, object]] = []
    progress: list[int] = []
    preparation_active: list[bool] = []
    external_actors: list[dict[str, object]] = []
    decision_subjects: set[str] = set()
    request_ids = count(1)

    def observe_activity(kind, request, status=None):
        if urlsplit(request.url).path != "/api/v1/activity":
            return
        if kind == "request":
            outstanding[request] = (next(request_ids), time.monotonic())
        identity, began = outstanding.get(request, (0, time.monotonic()))
        recent.append((kind, identity, round(time.monotonic() - began, 3), status))
        if kind in {"finished", "abort"}:
            outstanding.pop(request, None)

    def receipt():
        return {
            "os_cache": "uncontrolled",
            "routes": {
                name: {
                    "n": len(values),
                    "p50_ms": statistics.median(values),
                    "p95_ms": sorted(values)[math.ceil(len(values) * 0.95) - 1],
                    "max_ms": max(values),
                    "samples_ms": values,
                }
                for name, values in samples.items()
            },
            "misses": misses,
            "preparation_checked": progress,
            "preparation_active": preparation_active,
            "external_actors": external_actors,
        }

    def record(name, elapsed, observed, failure, budget):
        values = samples.setdefault(name, [])
        values.append(elapsed)
        row = {
            "route": name,
            "sample": len(values),
            "ms": elapsed,
            "observer_return_ms": observed,
            "failure": failure,
            "target_ms": budget,
        }
        if elapsed > budget or failure is not None:
            misses.append(row)
        print(json.dumps(row, sort_keys=True), flush=True)

    def measured(name, action, ready, budget=2000):
        navigation = name in {
            "cold_sign_in_shell",
            "cold_full_reload",
            "warm_full_reload",
        }
        action_epoch = None
        if not navigation:
            action_epoch = page.evaluate(
                "name => { window.__ownerTiming.arm(name); return performance.timeOrigin + window.__ownerTiming.started; }",
                name,
            )
        started = time.perf_counter()
        failure = None
        try:
            action()
            ready()
            if name in {
                "cold_full_reload",
                "warm_full_reload",
                "cold_sign_in_destination",
            }:
                expect(page.get_by_text("2300 works", exact=True)).to_be_visible()
            state = page.evaluate("() => window.__ownerTiming")
            if navigation:
                key = "sign_in" if name == "cold_sign_in_shell" else "data"
                elapsed = state["navigation"][key]
                assert elapsed is not None
                if name != "cold_sign_in_shell":
                    shell = state["navigation"]["shell"]
                    assert shell is not None
                    record(
                        name + "_shell",
                        shell,
                        (time.perf_counter() - started) * 1000,
                        None,
                        1000,
                    )
            else:
                if name in {"cold_sign_in_destination", "warm_sign_in_destination"}:
                    key = "shell" if name == "cold_sign_in_destination" else "data"
                    ready_at = state["navigation"][key]
                    assert ready_at is not None and action_epoch is not None
                    elapsed = state["time_origin"] + ready_at - action_epoch
                    if name == "warm_sign_in_destination":
                        shell = state["navigation"]["shell"]
                        assert shell is not None
                        record(
                            name + "_shell",
                            state["time_origin"] + shell - action_epoch,
                            (time.perf_counter() - started) * 1000,
                            None,
                            1000,
                        )
                else:
                    assert state["ready"] is not None
                    elapsed = state["ready"] - state["started"]
                if name == "cold_sign_in_destination":
                    data = state["navigation"]["data"]
                    assert data is not None and action_epoch is not None
                    record(
                        name + "_data",
                        state["time_origin"] + data - action_epoch,
                        (time.perf_counter() - started) * 1000,
                        None,
                        2000,
                    )
            print(
                json.dumps(
                    {
                        "route": name,
                        "raw_dom_timing": state,
                        "action_epoch_ms": action_epoch,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as error:
            failure = type(error).__name__
            elapsed = (time.perf_counter() - started) * 1000
            try:
                readers = (
                    fixture.repository,
                    fixture.application._activity_projection._repository,
                )
                note = {
                    "schema": "owner-browser-failure-v1",
                    "at": round(time.monotonic(), 3),
                    "cold": cold,
                    "sample": len(samples.get(name, [])),
                    "action": name,
                    "route": urlsplit(page.url).path,
                    "query_keys": sorted(parse_qs(urlsplit(page.url).query)),
                    "loading_activity": page.get_by_role(
                        "heading", name="Loading activity…", exact=True
                    ).count(),
                    "outstanding": [
                        (identity, round(time.monotonic() - began, 3))
                        for identity, began in list(outstanding.values())[:8]
                    ],
                    "recent": list(recent),
                    "admission": [
                        {
                            "waiting": len(reader.operation_admission._waiting),
                            "active": reader.operation_admission._active is not None,
                        }
                        for reader in readers
                    ],
                    "threads": sorted(
                        {
                            frame.f_code.co_name
                            for frame in sys._current_frames().values()
                        }
                    )[:12],
                }
                error.add_note(json.dumps(note, sort_keys=True))
            except Exception as diagnostic_error:
                error.add_note(
                    "owner-browser-failure-v1: " + type(diagnostic_error).__name__
                )
            raise
        finally:
            record(
                name, elapsed, (time.perf_counter() - started) * 1000, failure, budget
            )

    expect.set_options(timeout=60000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for cold in range(5):
                with running_owner_fixture(
                    tmp_path, 2300 if cold == 0 else 0, prepare=True
                ) as (fixture, origin):
                    with closing(
                        browser.new_context(viewport={"width": 1440, "height": 900})
                    ) as context:
                        page = context.new_page()
                        page.set_default_timeout(60000)
                        outstanding: dict[Request, tuple[int, float]] = {}
                        recent: deque[tuple[str, int, float, int | None]] = deque(
                            maxlen=8
                        )
                        page.on(
                            "request",
                            lambda request: observe_activity("request", request),
                        )
                        page.on(
                            "response",
                            lambda response: observe_activity(
                                "response", response.request, response.status
                            ),
                        )
                        page.on(
                            "requestfinished",
                            lambda request: observe_activity("finished", request),
                        )
                        page.on(
                            "requestfailed",
                            lambda request: observe_activity("abort", request),
                        )
                        page.add_init_script("""
                            (() => {
                                const visible = element => element.getClientRects().length > 0;
                                const exact = (selector, text) => Array.from(
                                    document.querySelectorAll(selector)
                                ).some(element => visible(element) && element.textContent.trim() === text);
                                const heading = text => exact('h1,h2,h3,h4', text);
                                const tests = {
                                    cold_sign_in_destination: () => heading('Works'),
                                    warm_sign_in_destination: () => exact('.library-works-heading>span', '2300 works'),
                                    search: () => exact('.library-works-heading>span', 'Found 2070 of 2300 works'),
                                    negative_search: () => new URL(location.href).searchParams.get('q') === 'absent-search-term'
                                        && exact('p', 'No exact-current works match this search and lens.'),
                                    clear_search: () => !new URL(location.href).searchParams.has('q')
                                        && exact('.library-works-heading>span', '2300 works'),
                                    pagination: () => exact('span', 'Page 2'),
                                    selected_detail: () => heading('Which cover should this work use?'),
                                    choice_confirmation: () => heading('Review complete'),
                                    policy_navigation: () => heading('Library policy'),
                                    settings_navigation: () => heading('Settings'),
                                    library_navigation: () => exact('.library-works-heading>span', '2300 works'),
                                    attention_status: () => exact('button', 'Load more attention'),
                                    preparation_status: () => heading('Background activity'),
                                    activity_navigation: () => heading('Activity ledger') && !!document.querySelector('.cm-activity-events article'),
                                    activity_negative: () => new URL(location.href).searchParams.get('q') === 'absent-search-term'
                                        && exact('strong', 'No activity matches these filters'),
                                    activity_clear: () => !new URL(location.href).searchParams.has('q')
                                        && !!document.querySelector('.cm-activity-events article'),
                                    activity_search: () => new URL(location.href).searchParams.get('q') === 'synthetic'
                                        && !!document.querySelector('.cm-activity-events article'),
                                    activity_filter: () => new URL(location.href).searchParams.get('eventType') === 'review'
                                        && !!document.querySelector('.cm-activity-events article'),
                                    activity_range: () => new URL(location.href).searchParams.get('rangeDays') === '90'
                                        && !!document.querySelector('.cm-activity-events article'),
                                    activity_deep: () => Number(document.querySelector('[aria-label="Activity page number"]')?.value) > 1
                                        && !!document.querySelector('.cm-activity-pagination-nav button:last-child:disabled'),
                                    activity_expansion: () => !!document.querySelector('.cm-activity-evidence'),
                                };
                                const timing = window.__ownerTiming = {
                                    started: null, ready: null, name: null, time_origin: performance.timeOrigin,
                                    navigation: {sign_in: null, shell: null, data: null},
                                    arm(name) {
                                        this.name = name;
                                        this.started = performance.now();
                                        this.ready = null;
                                    },
                                };
                                new MutationObserver(() => {
                                    const now = performance.now();
                                    if (exact('button', 'Sign in')) timing.navigation.sign_in ??= now;
                                    if (heading('Works')) timing.navigation.shell ??= now;
                                    if (exact('.library-works-heading>span', '2300 works')) timing.navigation.data ??= now;
                                    if (timing.name && tests[timing.name]()) timing.ready ??= now;
                                }).observe(document, {
                                    subtree: true, childList: true, characterData: true, attributes: true,
                                });
                            })();
                        """)
                        assert fixture.preparation is not None
                        for index in range(5):
                            fixture.preparation.request("primary", f"books-{index}")
                        measured(
                            "cold_sign_in_shell",
                            lambda page=page, origin=origin: page.goto(
                                origin + "/library?lens=all"
                            ),
                            lambda page=page: expect(
                                page.get_by_role("button", name="Sign in", exact=True)
                            ).to_be_visible(),
                            1000,
                        )
                        page.get_by_label("Owner password", exact=True).fill(
                            "synthetic-owner"
                        )
                        measured(
                            "cold_sign_in_destination",
                            lambda page=page: page.get_by_role(
                                "button", name="Sign in", exact=True
                            ).click(),
                            lambda page=page: expect(
                                page.get_by_role("heading", name="Works", exact=True)
                            ).to_be_visible(),
                            1000,
                        )
                        expect(
                            page.get_by_text("2300 works", exact=True)
                        ).to_be_visible()
                        measured(
                            "cold_full_reload",
                            page.reload,
                            lambda page=page: expect(
                                page.get_by_text("2300 works", exact=True)
                            ).to_be_visible(),
                        )
                        if cold < 4:
                            continue
                        for sample in range(30):
                            with (
                                fixture.hold_external_actors()
                                if sample == 0
                                else nullcontext(None)
                            ) as actors:
                                if actors is not None:
                                    external_actors.append(actors.receipt)
                                    before = context.request.get(
                                        origin + "/api/v1/review-preparation"
                                    )
                                    assert before.ok
                                    actors.receipt["preparation_before"] = before.json()
                                    assert (
                                        actors.receipt["reserved_work"] == "work-2298"
                                    )
                                assert fixture.preparation is not None
                                measured(
                                    "warm_full_reload",
                                    page.reload,
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading", name="Works", exact=True
                                        )
                                    ).to_be_visible(),
                                )
                                search = page.get_by_role("searchbox")
                                measured(
                                    "negative_search",
                                    lambda search=search: search.fill(
                                        "absent-search-term"
                                    ),
                                    lambda page=page: expect(
                                        page.get_by_text(
                                            "No exact-current works match this search and lens.",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                assert parse_qs(urlsplit(page.url).query)["q"] == [
                                    "absent-search-term"
                                ]
                                measured(
                                    "clear_search",
                                    lambda search=search: search.fill(""),
                                    lambda page=page: expect(
                                        page.get_by_text("2300 works", exact=True)
                                    ).to_be_visible(),
                                )
                                assert "q" not in parse_qs(urlsplit(page.url).query)
                                measured(
                                    "search",
                                    lambda search=search: search.fill(
                                        "Synthetic title"
                                    ),
                                    lambda page=page: expect(
                                        page.get_by_text(
                                            "Found 2070 of 2300 works", exact=True
                                        )
                                    ).to_be_visible(),
                                )
                                measured(
                                    "pagination",
                                    lambda page=page: page.get_by_role(
                                        "button", name="Next →", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_text("Page 2", exact=True)
                                    ).to_be_visible(),
                                )
                                page.get_by_role(
                                    "button", name="← Previous", exact=True
                                ).click()
                                expect(
                                    page.get_by_text("Page 1", exact=True)
                                ).to_be_visible()
                                if sample >= 20:
                                    page.get_by_role(
                                        "button", name="Next →", exact=True
                                    ).click()
                                    expect(
                                        page.get_by_text("Page 2", exact=True)
                                    ).to_be_visible()
                                row = page.get_by_role(
                                    "button",
                                    name="Synthetic title. Author details unavailable. 1 field needs attention",
                                    exact=True,
                                ).first
                                measured(
                                    "selected_detail",
                                    row.click,
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading",
                                            name="Which cover should this work use?",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                selected_case = parse_qs(urlsplit(page.url).query)[
                                    "preview"
                                ][0]
                                decision_subjects.add(selected_case)
                                assert selected_case != fixture.cases[-1].review_case_id
                                measured(
                                    "choice_confirmation",
                                    lambda page=page: page.get_by_role(
                                        "button",
                                        name="Choose Current cover",
                                        exact=True,
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading",
                                            name="Review complete",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                page.get_by_role(
                                    "link", name="History", exact=True
                                ).click()
                                expect(
                                    page.get_by_text(
                                        "Owner chose the current value.", exact=True
                                    )
                                ).to_be_visible()
                                if actors is not None:
                                    actors.confirm_unrelated(selected_case)
                                measured(
                                    "activity_navigation",
                                    lambda page=page: page.get_by_role(
                                        "link", name="Activity", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.locator(
                                            ".cm-activity-events article"
                                        ).first
                                    ).to_be_visible(),
                                )
                                activity_search = page.get_by_role(
                                    "searchbox", name="Search activity", exact=True
                                )
                                measured(
                                    "activity_search",
                                    lambda activity_search=activity_search: (
                                        activity_search.fill("synthetic")
                                    ),
                                    lambda page=page: page.wait_for_url(
                                        re.compile(r"[?&]q=synthetic(?:&|$)")
                                    ),
                                )
                                measured(
                                    "activity_negative",
                                    lambda activity_search=activity_search: (
                                        activity_search.fill("absent-search-term")
                                    ),
                                    lambda page=page: expect(
                                        page.get_by_text(
                                            "No activity matches these filters",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                measured(
                                    "activity_clear",
                                    lambda activity_search=activity_search: (
                                        activity_search.fill("")
                                    ),
                                    lambda page=page: expect(
                                        page.locator(
                                            ".cm-activity-events article"
                                        ).first
                                    ).to_be_visible(),
                                )
                                expect(activity_search).to_be_focused()
                                measured(
                                    "activity_filter",
                                    lambda page=page: page.get_by_role(
                                        "button", name="Owner choices", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "button", name="Owner choices", exact=True
                                        )
                                    ).to_have_attribute("aria-pressed", "true"),
                                )
                                page.get_by_role(
                                    "button", name="All", exact=True
                                ).click()
                                page.get_by_role(
                                    "combobox", name="Activity date range", exact=True
                                ).click()
                                measured(
                                    "activity_range",
                                    lambda page=page: page.get_by_role(
                                        "option", name="Last 90 days", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "combobox",
                                            name="Activity date range",
                                            exact=True,
                                        )
                                    ).to_contain_text("Last 90 days"),
                                )
                                measured(
                                    "activity_deep",
                                    lambda page=page: page.get_by_role(
                                        "button", name="Last", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "button", name="Last", exact=True
                                        )
                                    ).to_be_disabled(),
                                )
                                measured(
                                    "activity_expansion",
                                    lambda page=page: page.get_by_role(
                                        "button", name="Details", exact=True
                                    ).first.click(),
                                    lambda page=page: expect(
                                        page.locator(".cm-activity-evidence")
                                    ).to_be_visible(),
                                )
                                measured(
                                    "policy_navigation",
                                    lambda page=page: page.get_by_role(
                                        "link", name="Policy", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading", name="Library policy", exact=True
                                        )
                                    ).to_be_visible(),
                                    1000,
                                )
                                measured(
                                    "settings_navigation",
                                    lambda page=page: page.get_by_role(
                                        "link", name="Settings", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading", name="Settings", exact=True
                                        )
                                    ).to_be_visible(),
                                    1000,
                                )
                                measured(
                                    "library_navigation",
                                    lambda page=page: page.get_by_role(
                                        "link", name="Library", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_text("2300 works", exact=True)
                                    ).to_be_visible(),
                                )
                                measured(
                                    "attention_status",
                                    lambda page=page: page.get_by_role(
                                        "button", name=re.compile(r"^Attention ")
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "button",
                                            name="Load more attention",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                page.get_by_role(
                                    "button", name="Close", exact=True
                                ).click()
                                status_button = page.get_by_role(
                                    "button",
                                    name=re.compile(
                                        r"^(Preparing libraries|Preparation needs attention)$"
                                    ),
                                )
                                measured(
                                    "preparation_status",
                                    status_button.click,
                                    lambda page=page: expect(
                                        page.get_by_role(
                                            "heading",
                                            name="Background activity",
                                            exact=True,
                                        )
                                    ).to_be_visible(),
                                )
                                status_button.click()
                                page.get_by_role(
                                    "button", name="Sign out", exact=True
                                ).click()
                                page.get_by_label("Owner password", exact=True).fill(
                                    "synthetic-owner"
                                )
                                measured(
                                    "warm_sign_in_destination",
                                    lambda page=page: page.get_by_role(
                                        "button", name="Sign in", exact=True
                                    ).click(),
                                    lambda page=page: expect(
                                        page.get_by_text("2300 works", exact=True)
                                    ).to_be_visible(),
                                )
                                response = context.request.get(
                                    origin + "/api/v1/review-preparation"
                                )
                                assert response.ok
                                if actors is not None:
                                    actors.receipt["preparation_after"] = (
                                        response.json()
                                    )
                                    assert response.json()["active"]
                                    assert sum(
                                        row["checked"]
                                        for row in response.json()["libraries"]
                                    ) > sum(
                                        row["checked"]
                                        for row in before.json()["libraries"]
                                    )
                                preparation_active.append(response.json()["active"])
                                progress.append(
                                    sum(
                                        row["checked"]
                                        for row in response.json()["libraries"]
                                    )
                                )
        finally:
            browser.close()
            print(json.dumps(receipt(), sort_keys=True), flush=True)
    assert len(decision_subjects) == 30
    assert len(external_actors) == 1
    assert external_actors[0]["target_effects"] == 1
    assert external_actors[0]["reserved_case_id"] not in decision_subjects
    assert len(progress) == 30 and progress[-1] > progress[0]
    assert any(preparation_active)
    assert all(
        len(values) == (5 if name.startswith("cold_") else 30)
        for name, values in samples.items()
    )
    assert all(row["failure"] is None for row in misses)
