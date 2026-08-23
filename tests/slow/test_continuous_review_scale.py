"""Explicit installed-wheel continuous-review scale qualification."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.support.continuous_review_scale import (
    _MEASUREMENT_SAMPLES,
    _validate_receipt_budgets,
    qualify_installed_pipeline,
)


class _WorkspaceProbe:
    """One real owner page reused across the three installed phases."""

    def __init__(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser: Browser = self._playwright.chromium.launch(headless=True)
        self._page: Page | None = None
        self.errors: list[str] = []
        self.failures: list[int] = []

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()

    @staticmethod
    def _assert_library_query(
        page: Page, *, query: str | None, preview: str | None
    ) -> None:
        page.wait_for_function(
            """({query}) => {
                const location = new URL(window.location.href)
                return location.pathname === '/library'
                    && location.searchParams.get('lens') === 'all'
                    && (query === null
                        ? !location.searchParams.has('q')
                        : location.searchParams.get('q') === query)
            }""",
            arg={"query": query},
        )
        parameters = parse_qs(urlparse(page.url).query)
        assert parameters.get("lens") == ["all"]
        assert parameters.get("q") == ([] if query is None else [query])
        if preview is not None:
            assert parameters.get("preview") == [preview]

    def observe(self, phase: str, origin: str, review_case_id: str) -> None:
        if self._page is None:
            context = self._browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            self._page = context.new_page()
            self._page.on("pageerror", lambda error: self.errors.append(str(error)))
            self._page.on(
                "console",
                lambda message: (
                    self.errors.append(message.text)
                    if message.type == "error"
                    else None
                ),
            )
            self._page.on(
                "response",
                lambda response: (
                    self.failures.append(response.status)
                    if response.status >= 500
                    else None
                ),
            )
            self._page.goto(f"{origin}/login")
            self._page.get_by_label("Password").fill("synthetic password")
            self._page.get_by_role("button", name="Sign in").click()
            self._page.wait_for_url(f"{origin}/library")

        page = self._page
        preserved_preview = review_case_id if phase != "early" else None
        page.get_by_role("link", name="All", exact=True).click()
        self._assert_library_query(page, query=None, preview=preserved_preview)
        search = page.get_by_role(
            "searchbox", name="Search title, author, identifier, library, or format"
        )
        search.fill("fiction")
        self._assert_library_query(page, query="fiction", preview=preserved_preview)
        search.fill("")
        self._assert_library_query(page, query=None, preview=preserved_preview)
        page.locator(".library-work-row").first.click()
        page.goto(f"{origin}/library?lens=all&preview={review_case_id}&field=title")
        page.get_by_role("heading", name="Which title should this work use?").wait_for()

        if phase == "early":
            assert (
                page.get_by_role(
                    "button", name="Choose current value:", exact=False
                ).count()
                == 1
            )
        elif phase == "peak":
            page.get_by_role(
                "button", name="Choose current value:", exact=False
            ).click()
            page.get_by_text(
                "Decision recorded from the authoritative case.", exact=True
            ).wait_for()
        else:
            page.reload()
            page.get_by_role(
                "heading", name="Which title should this work use?"
            ).wait_for()
            expect(
                page.get_by_role("button", name="Title Decision recorded", exact=True)
            ).to_have_attribute("aria-current", "true")
            page.get_by_role("link", name="Activity", exact=True).click()
            page.get_by_role("button", name="Owner choices", exact=True).click()
            expect(
                page.get_by_role("button", name="Owner choices", exact=True)
            ).to_have_attribute("aria-pressed", "true")

        assert self.errors == []
        assert self.failures == []


def test_installed_pipeline_reconciles_two_thousand_fairly_across_restart() -> None:
    workspace = _WorkspaceProbe()
    try:
        qualify_installed_pipeline(
            {"fiction": 3, "history": 998, "science": 998},
            expected_total=2_000,
            memory_ceiling_kib=524_288,
            phase_callback=workspace.observe,
        )
    finally:
        workspace.close()


def test_receipt_budget_rejects_two_over_budget_active_samples() -> None:
    """Twenty samples preserve the 2-second foreground cap at the boundary."""

    def samples(value: float) -> dict[str, float | int]:
        return {
            "n": _MEASUREMENT_SAMPLES,
            "p50_ms": value,
            "p95_ms": value,
            "max_ms": value,
            "5xx_or_transport": 0,
        }

    receipt: dict[str, object] = {
        "phases": {
            "quiescent": {"route": samples(1)},
            "early": {"route": samples(2_001)},
            "peak": {"route": samples(2_001)},
            "late": {"route": samples(1)},
        }
    }
    with pytest.raises(AssertionError):
        _validate_receipt_budgets(receipt)
