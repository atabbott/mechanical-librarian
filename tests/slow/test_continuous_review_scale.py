"""Explicit installed-wheel continuous-review scale qualification."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Request,
    expect,
    sync_playwright,
)
from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from tests.support.continuous_review_scale import (
    _FOREGROUND_RESPONSE_BUDGET_MS,
    _MEASUREMENT_SAMPLES,
    _assert_distinct_phase_fractions,
    _validate_receipt_budgets,
    qualify_installed_decision_activity,
    qualify_installed_pipeline,
)

_FIXTURE_CURRENT_TITLE = "fiction-0000"
_FIXTURE_PROPOSED_TITLE = "Exact evaluator candidate"
_IDLE_PROBE_MILLISECONDS = 42 * 60 * 1_000
_IDLE_DATA_PATHS = {
    "/api/v1/attention",
    "/api/v1/library",
    "/api/v1/review-preparation",
}
_LIVE_REPLAY_SCRIPT = """
(() => {
  const sources = []
  class ProbeEventSource {
    constructor(url) {
      this.url = String(url)
      this.readyState = 1
      this.onopen = null
      this.onerror = null
      this.listeners = new Map()
      sources.push(this)
      queueMicrotask(() => this.onopen?.(new Event('open')))
    }
    addEventListener(name, listener) {
      const listeners = this.listeners.get(name) ?? []
      listeners.push(listener)
      this.listeners.set(name, listeners)
    }
    close() {
      this.readyState = 2
    }
    emit(update) {
      const event = new MessageEvent(update.topic, { data: JSON.stringify(update) })
      for (const listener of this.listeners.get(update.topic) ?? []) {
        if (typeof listener === 'function') listener(event)
        else listener.handleEvent(event)
      }
    }
  }
  ProbeEventSource.CONNECTING = 0
  ProbeEventSource.OPEN = 1
  ProbeEventSource.CLOSED = 2
  Object.defineProperty(globalThis, 'EventSource', {
    configurable: true,
    value: ProbeEventSource,
  })
  globalThis.__mlReplayFrame = (update) => {
    for (const source of sources) if (source.readyState === 1) source.emit(update)
  }
})()
"""


class _WorkspaceProbe:
    """One real owner page reused across the three installed phases."""

    def __init__(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser: Browser = self._playwright.chromium.launch(headless=True)
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._closed = False
        self._case_id: str | None = None
        self.errors: list[str] = []
        self.failures: list[int] = []
        self.idle_request_evidence: dict[str, int] | None = None
        self._record_idle_requests = False
        self._idle_requests: list[str] = []
        self._idle_inflight: set[Request] = set()

    def _observe_request(self, request: Request) -> None:
        path = urlparse(request.url).path
        if self._record_idle_requests and path in _IDLE_DATA_PATHS:
            self._idle_requests.append(path)
            self._idle_inflight.add(request)

    def _finish_request(self, request: Request) -> None:
        self._idle_inflight.discard(request)

    def _settle_idle_requests(self, page: Page) -> None:
        deadline = time.monotonic() + 5
        page.evaluate("0")
        while self._idle_inflight:
            page.evaluate("0")
            if time.monotonic() >= deadline:
                pytest.fail("installed idle request did not settle")
            time.sleep(0.005)

    def _replay_idle_baseline(self, page: Page) -> None:
        """Replay the captured busy cadence through the exact installed browser."""

        frames = sorted(
            (
                *(
                    (
                        ((index + 1) * _IDLE_PROBE_MILLISECONDS) // 99,
                        "preparation",
                    )
                    for index in range(98)
                ),
                *(
                    (((index + 1) * _IDLE_PROBE_MILLISECONDS) // 70, "library")
                    for index in range(69)
                ),
            )
        )
        expect(page.get_by_role("heading", level=2, name="Works")).to_be_visible()
        page.wait_for_load_state("networkidle")
        self._record_idle_requests = True
        try:
            # The fixed hour exceeds the bounded startup waits without racing a
            # Date.now() read against the next protocol call.
            page.clock.pause_at(datetime(2024, 1, 1, 1, tzinfo=timezone.utc))
            page.evaluate("document.dispatchEvent(new PointerEvent('pointerdown'))")
            page.clock.run_for(10_000)
            self._settle_idle_requests(page)
            self._idle_requests.clear()
            started = page.evaluate("Date.now()")
            elapsed = 0
            for sequence, (at, topic) in enumerate(frames, start=1):
                page.clock.run_for(max(0, at - elapsed))
                elapsed = max(elapsed, at)
                self._settle_idle_requests(page)
                page.evaluate(
                    "frame => globalThis.__mlReplayFrame(frame)",
                    {"sequence": sequence, "subject": None, "topic": topic},
                )
                page.clock.run_for(200)
                elapsed += 200
                self._settle_idle_requests(page)
            page.clock.run_for(max(0, _IDLE_PROBE_MILLISECONDS - elapsed))
            self._settle_idle_requests(page)
            assert page.evaluate("Date.now()") - started == _IDLE_PROBE_MILLISECONDS
        finally:
            self._record_idle_requests = False
            page.clock.resume()

        self.idle_request_evidence = {
            path: self._idle_requests.count(path) for path in sorted(_IDLE_DATA_PATHS)
        }
        self.idle_request_evidence["total"] = len(self._idle_requests)
        assert self.idle_request_evidence == {
            "/api/v1/review-preparation": 20,
            "/api/v1/library": 0,
            "/api/v1/attention": 0,
            "total": 20,
        }

    def _activity_api_evidence(self, page: Page) -> dict[str, object]:
        """Read only bounded decision/activity facts through authenticated APIs."""

        assert self._case_id is not None
        return page.evaluate(
            """async (caseId) => {
              const [detailResponse, historyResponse, activityResponse] = await Promise.all([
                fetch(`/api/v1/review-cases/${encodeURIComponent(caseId)}`),
                fetch(`/api/v1/review-cases/${encodeURIComponent(caseId)}/history`),
                fetch('/api/v1/activity?event_type=review&range_days=30&page=1&page_size=25'),
              ])
              const detail = await detailResponse.json()
              const history = await historyResponse.json()
              const activity = await activityResponse.json()
              const title = Array.isArray(detail.fields)
                ? detail.fields.find((field) => field?.destination === 'title')
                : undefined
              const decision = title?.human_decision
              const historyEvents = Array.isArray(history.events) ? history.events : []
              const events = Array.isArray(activity.events) ? activity.events : []
              return {
                activity: {
                  owner_choice_events: events.filter(
                    (event) => event?.category === 'owner' && event?.state === 'accept',
                  ).length,
                  status: activityResponse.status,
                  visible_events: events.length,
                },
                decision: {
                  actor: typeof decision?.actor === 'string' ? decision.actor : null,
                  destination: title?.destination === 'title' ? 'title' : null,
                  outcome: typeof decision?.outcome === 'string' ? decision.outcome : null,
                  present: decision !== null && decision !== undefined,
                },
                detail_status: detailResponse.status,
                history: {
                  human_decision_events: historyEvents.filter(
                    (event) => event?.kind === 'human_decision_recorded',
                  ).length,
                  status: historyResponse.status,
                },
              }
            }""",
            self._case_id,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._context is not None:
            self._context.close()
            self._context = None
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

    def observe(self, phase: str, origin: str) -> float | None:
        if self._page is None:
            self._context = self._browser.new_context(
                ignore_https_errors=True, viewport={"width": 1280, "height": 800}
            )
            self._page = self._context.new_page()
            self._page.clock.install(time=datetime(2024, 1, 1, tzinfo=timezone.utc))
            self._page.add_init_script(_LIVE_REPLAY_SCRIPT)
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
            self._page.on("request", self._observe_request)
            self._page.on("requestfinished", self._finish_request)
            self._page.on("requestfailed", self._finish_request)
            self._page.goto(f"{origin}/login")
            self._page.get_by_label("Password").fill("synthetic password")
            self._page.get_by_role("button", name="Sign in").click()
            self._page.wait_for_url(f"{origin}/library")

        page = self._page
        page.goto(f"{origin}/library?lens=all")
        if phase == "early":
            self._replay_idle_baseline(page)
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
            preview=None,
        )
        page.get_by_role("link", name="All", exact=True).click()
        self._assert_library_query(page, lens="all", query=None, preview=None)
        search = page.get_by_role(
            "searchbox", name="Search title, author, identifier, library, or format"
        )
        search.fill("fiction")
        self._assert_library_query(page, lens="all", query="fiction", preview=None)
        cohort_row = page.locator(".library-work-row").filter(
            has_text=_FIXTURE_CURRENT_TITLE
        )
        expect(cohort_row).to_have_count(1)
        cohort_title = cohort_row.locator(".library-work-title").inner_text()
        cohort_row.click()
        expect(cohort_row).to_have_attribute("aria-current", "true")
        cohort_heading = page.get_by_role("heading", name=cohort_title, exact=True)
        expect(cohort_heading).to_have_count(1)
        expect(cohort_heading).to_be_visible()
        current = page.get_by_role(
            "radio",
            name=f"Choose current value: {_FIXTURE_CURRENT_TITLE}",
            exact=True,
        )
        choice = page.get_by_role(
            "radio",
            name=f"Choose proposed value: {_FIXTURE_PROPOSED_TITLE}",
            exact=True,
        )
        if phase == "early":
            expect(current).to_have_count(1)
            expect(choice).to_have_count(1)
            expect(choice).to_be_enabled()
            preview = parse_qs(urlparse(page.url).query).get("preview")
            assert preview is not None and len(preview) == 1
            self._case_id = preview[0]
        if phase == "peak":
            assert self._case_id is not None
            expect(choice).to_have_count(1)
            started = time.monotonic()
            with page.expect_response(
                lambda response: response.url.endswith("/decisions"),
                timeout=_FOREGROUND_RESPONSE_BUDGET_MS,
            ) as decision_response:
                choice.click()
            response = decision_response.value
            elapsed_ms = (time.monotonic() - started) * 1_000
            assert response.status == 200, response.text()
            acknowledgement = response.json()
            assert acknowledgement["schema_version"] == "human-decision-ack-v1"
            assert acknowledgement["outcome"] == "accept"
            page.get_by_role("heading", name="Review complete").wait_for(
                timeout=_FOREGROUND_RESPONSE_BUDGET_MS
            )
            page.get_by_text("No library data changed.", exact=False).wait_for()
            assert elapsed_ms <= _FOREGROUND_RESPONSE_BUDGET_MS
        if phase == "late":
            assert self._case_id is not None
            page.goto(
                f"{origin}/library?lens=all&q={_FIXTURE_CURRENT_TITLE}"
                f"&preview={self._case_id}&field=title"
            )
            recorded_field = page.get_by_role(
                "button", name=re.compile(r"^Title.*Decision recorded$")
            )
            expect(recorded_field).to_have_count(1)
            recorded_field.click()
            recorded_choice = page.get_by_role(
                "radio",
                name=f"Choose proposed value: {_FIXTURE_PROPOSED_TITLE}",
                exact=True,
            )
            expect(recorded_choice).to_have_count(1)
            expect(recorded_choice).to_have_attribute("aria-checked", "true")
            activity_evidence = self._activity_api_evidence(page)
        page.get_by_role("link", name="Activity", exact=True).click()
        page.get_by_role("heading", name="Activity and recovery", exact=True).wait_for(
            timeout=_FOREGROUND_RESPONSE_BUDGET_MS
        )
        page.get_by_role("button", name="Owner choices", exact=True).click()
        expect(
            page.get_by_role("button", name="Owner choices", exact=True)
        ).to_have_attribute("aria-pressed", "true")
        if phase == "late":
            try:
                page.get_by_text("Owner choice", exact=True).wait_for(
                    timeout=_FOREGROUND_RESPONSE_BUDGET_MS
                )
                page.get_by_text("Accepted", exact=True).wait_for(
                    timeout=_FOREGROUND_RESPONSE_BUDGET_MS
                )
            except PlaywrightTimeoutError:
                location = urlparse(page.url)
                pytest.fail(
                    "installed activity owner choice unavailable: "
                    + repr(
                        {
                            "api": activity_evidence,
                            "browser": {
                                "event_type": parse_qs(location.query).get(
                                    "event_type"
                                ),
                                "path": location.path,
                            },
                        }
                    )
                )

        assert self.errors == []
        assert self.failures == []
        return elapsed_ms if phase == "peak" else None


def test_installed_pipeline_reconciles_two_thousand_then_restarts_completed_cohort() -> (
    None
):
    workspace = _WorkspaceProbe()
    try:
        qualify_installed_pipeline(
            {"fiction": 3, "history": 998, "science": 998},
            expected_total=2_000,
            memory_ceiling_kib=524_288,
            phase_observer=workspace,
        )
        assert workspace.idle_request_evidence is not None
        assert workspace.idle_request_evidence == {
            "/api/v1/review-preparation": 20,
            "/api/v1/library": 0,
            "/api/v1/attention": 0,
            "total": 20,
        }
    finally:
        workspace.close()


def test_installed_decision_activity_projection_uses_the_authoritative_event() -> None:
    """Keep the decision-to-Activity projection proof narrow and exact-wheel runnable."""

    evidence = qualify_installed_decision_activity(
        {"fiction": 3, "history": 3, "science": 3},
        expected_total=10,
    )

    assert evidence.history_events == 1
    assert evidence.category == "owner"
    assert evidence.outcome == "accept"


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
