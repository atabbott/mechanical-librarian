"""Explicit installed-wheel continuous-review scale qualification."""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.support.continuous_review_scale import (
    _MEASUREMENT_SAMPLES,
    _assert_distinct_phase_fractions,
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
        page: Page, *, lens: str, query: str | None, preview: str | None
    ) -> None:
        deadline = time.monotonic() + 5
        while True:
            location = urlparse(page.url)
            parameters = parse_qs(location.query)
            if (
                location.path == "/library"
                and parameters.get("lens") == [lens]
                and parameters.get("q") == (None if query is None else [query])
            ):
                break
            if time.monotonic() >= deadline:
                break
            page.wait_for_timeout(100)
        assert location.path == "/library"
        assert parameters.get("lens") == [lens]
        assert parameters.get("q") == (None if query is None else [query])
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
        page.goto(f"{origin}/library?lens=all&preview={review_case_id}&field=title")
        page.get_by_role("heading", name="Which title should this work use?").wait_for()
        preserved_preview = review_case_id
        if phase == "early":
            with page.expect_response(
                lambda response: response.url.startswith(
                    f"{origin}/api/v1/library?lens=needs_attention"
                ),
                timeout=2_000,
            ) as observed:
                page.get_by_role("link", name="Needs attention", exact=True).click()
            assert observed.value.status < 500
        else:
            page.get_by_role("link", name="Needs attention", exact=True).click()
        self._assert_library_query(
            page,
            lens="needs_attention",
            query=None,
            preview=preserved_preview,
        )
        page.get_by_role("link", name="All", exact=True).click()
        self._assert_library_query(
            page, lens="all", query=None, preview=preserved_preview
        )
        search = page.get_by_role(
            "searchbox", name="Search title, author, identifier, library, or format"
        )
        search.fill("fiction")
        self._assert_library_query(
            page, lens="all", query="fiction", preview=preserved_preview
        )
        search.fill("")
        self._assert_library_query(
            page, lens="all", query=None, preview=preserved_preview
        )
        cohort_row = page.locator(".library-work-row").first
        cohort_title = cohort_row.locator(".library-work-title").inner_text()
        cohort_row.click()
        expect(cohort_row).to_have_attribute("aria-current", "true")
        cohort_preview = parse_qs(urlparse(page.url).query).get("preview")
        assert cohort_preview is not None and len(cohort_preview) == 1
        assert cohort_preview[0] != review_case_id
        cohort_heading = page.get_by_role("heading", name=cohort_title, exact=True)
        expect(cohort_heading).to_have_count(1)
        expect(cohort_heading).to_be_visible()

        # The seeded manual case deliberately sits outside the reconciliation
        # cohort, so return to its direct route for the owner-decision proof.
        page.goto(f"{origin}/library?lens=all&preview={review_case_id}&field=title")
        page.get_by_role("heading", name="Which title should this work use?").wait_for()
        proposed_choice = page.get_by_role(
            "button",
            name="Choose proposed value: Exact evaluator candidate",
            exact=True,
        )

        if phase == "early":
            expect(proposed_choice).to_have_count(1)
        elif phase == "peak":
            with page.expect_response(
                lambda response: (
                    response.url
                    == f"{origin}/api/v1/review-cases/{review_case_id}/decisions"
                    and response.request.method == "POST"
                ),
                timeout=2_000,
            ) as observed:
                proposed_choice.click()
            assert observed.value.status == 200
            acknowledgement = observed.value.json()
            command = json.loads(observed.value.request.post_data or "{}")
            assert acknowledgement["schema_version"] == "human-decision-ack-v1"
            assert acknowledgement["review_case_id"] == review_case_id
            assert acknowledgement["outcome"] == command["outcome"] == "accept"
            assert (
                acknowledgement["selected_value"]
                == command["selected_value"]
                == {
                    "kind": "text",
                    "value": "Exact evaluator candidate",
                }
            )
            assert acknowledgement["destination"] == command["destination"] == "title"
            assert acknowledgement["proposal_id"] == command["proposal_id"]
            assert (
                acknowledgement["target_fingerprint"] == command["target_fingerprint"]
            )
            page.get_by_role("heading", name="Review complete").wait_for()
            page.get_by_text(
                "1 recorded field outcome. No library data changed.", exact=True
            ).wait_for()
            page.get_by_text(
                "Nothing is written yet — applying happens separately, behind your consent.",
                exact=True,
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


def test_installed_pipeline_reconciles_two_thousand_then_restarts_completed_cohort() -> (
    None
):
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


def test_phase_fractions_reject_collapsed_samples() -> None:
    with pytest.raises(AssertionError):
        _assert_distinct_phase_fractions({"early": 5.0, "peak": 5.0, "late": 5.0})
