"""Rendered owner timings with complete evidence and real preparation actors."""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from contextlib import closing
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from scripts.verify_playwright_browser import verify_playwright_browser

from tests.support.owner_responsiveness import running_owner_fixture


def test_complete_cover_rendered_owner_journey(tmp_path: Path) -> None:
    """Five runtime/context starts and thirty same-runtime owner interactions."""
    print(json.dumps(verify_playwright_browser(), sort_keys=True))
    samples: dict[str, list[float]] = {}
    misses: list[dict[str, object]] = []
    progress: list[int] = []

    def measured(name, action, ready, budget=2000):
        started = time.perf_counter()
        failure = None
        try:
            action()
            ready()
        except Exception as error:
            failure = type(error).__name__
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            values = samples.setdefault(name, [])
            values.append(elapsed)
            row = {
                "route": name,
                "sample": len(values),
                "ms": elapsed,
                "failure": failure,
            }
            if elapsed > budget or failure is not None:
                misses.append(row)
            print(json.dumps(row, sort_keys=True), flush=True)

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
                        page.set_default_timeout(15000)
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
                            page.get_by_text("2300 total", exact=True)
                        ).to_be_visible()
                        measured(
                            "cold_full_reload",
                            page.reload,
                            lambda page=page: expect(
                                page.get_by_text("2300 total", exact=True)
                            ).to_be_visible(),
                        )
                        if cold < 4:
                            continue
                        for sample in range(30):
                            assert fixture.preparation is not None
                            if sample == 0:
                                for index in range(5):
                                    fixture.preparation.request(
                                        "primary", f"books-{index}"
                                    )
                            measured(
                                "warm_full_reload",
                                page.reload,
                                lambda page=page: expect(
                                    page.get_by_role(
                                        "heading", name="Works", exact=True
                                    )
                                ).to_be_visible(),
                                1000,
                            )
                            search = page.get_by_role("searchbox")
                            measured(
                                "search",
                                lambda search=search: search.fill("Synthetic title"),
                                lambda page=page: expect(
                                    page.get_by_text("2070 total", exact=True)
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
                            measured(
                                "choice_confirmation",
                                lambda page=page: page.get_by_role(
                                    "button",
                                    name="Current cover Current cover Same edition Keep the cover currently held by the local library. No recommendation retained",
                                    exact=True,
                                ).click(),
                                lambda page=page: expect(
                                    page.get_by_role(
                                        "heading", name="Review complete", exact=True
                                    )
                                ).to_be_visible(),
                            )
                            page.get_by_role("link", name="History", exact=True).click()
                            expect(
                                page.get_by_text(
                                    "Owner chose the current value.", exact=True
                                )
                            ).to_be_visible()
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
                                    page.get_by_text("2300 total", exact=True)
                                ).to_be_visible(),
                            )
                            measured(
                                "attention_status",
                                lambda page=page: page.get_by_role(
                                    "button", name="Attention queue", exact=True
                                ).click(),
                                lambda page=page: expect(
                                    page.get_by_role(
                                        "button", name="Load more attention", exact=True
                                    )
                                ).to_be_visible(),
                            )
                            page.get_by_role("button", name="Close", exact=True).click()
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
                                    page.get_by_text("2300 total", exact=True)
                                ).to_be_visible(),
                            )
                            response = context.request.get(
                                origin + "/api/v1/review-preparation"
                            )
                            assert response.ok
                            progress.append(
                                sum(
                                    row["checked"]
                                    for row in response.json()["libraries"]
                                )
                            )
        finally:
            browser.close()
    receipt = {
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
    }
    print(json.dumps(receipt, sort_keys=True))
    assert progress[-1] > progress[0]
    assert misses == []
