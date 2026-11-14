"""Book matching in Library, in the exact built shell (#1355, test 22).

At a wider Book matching level a field that needs the owner may offer a
value from a source record with the book's title and author, and its
evidence says so beside the source. While a work is checked against a new
setting, a line says so and its choices are unavailable. A choice sent as
Book matching changed the field's offer is not recorded, and the field says
so. Each is shown whole at 1440 and 390 pixels, with no sideways scroll.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import ExitStack
from typing import cast
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, Route, expect, sync_playwright

from tests.browser.fidelity_cohort import rich_payload
from tests.browser.test_react_platform_artifact import _serve_cohort

_CASE = "/api/v1/review-cases/case-1"
_URL = "https://react-platform.invalid/library?lens=all&preview=case-1"
_PROPOSED = "The Clockmaker's Cartography"
_CHECKING = "Checking this work against the new Book matching setting."
_OFFER_CHANGED = (
    "Not recorded: Book matching changed what this field offers. Choose again."
)
_WIDTHS = (1440, 390)
_NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _case(edit: Callable[[dict[str, object]], None]) -> dict[str, object]:
    payload = cast("dict[str, object]", rich_payload(_CASE))
    edit(payload)
    return payload


def _title(payload: dict[str, object]) -> dict[str, object]:
    return next(
        field
        for field in cast("list[dict[str, object]]", payload["fields"])
        if field["destination"] == "title"
    )


def _level_offer(payload: dict[str, object]) -> None:
    """Every proposed title is offered for the book's title and author."""
    for context in cast("list[dict[str, object]]", _title(payload)["choice_contexts"]):
        if context["selection"] == "proposed":
            context["match_basis"] = "title_and_author"


def _checking(payload: dict[str, object]) -> None:
    payload["checking"] = True


def _serve(
    page: Page,
    edit: Callable[[dict[str, object]], None],
    *,
    decision: Callable[[Route], None] | None = None,
) -> None:
    _serve_cohort(page)

    def fulfill(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == _CASE and route.request.method == "GET":
            route.fulfill(content_type="application/json", body=json.dumps(_case(edit)))
        elif (
            decision is not None
            and path == _CASE + "/decisions"
            and route.request.method == "POST"
        ):
            decision(route)
        else:
            route.fallback()

    # Registered after the cohort, so it is asked first.
    page.route(f"**{_CASE}**", fulfill)


def _refuse_digest_mismatch(route: Route) -> None:
    route.fulfill(
        status=409,
        content_type="application/json",
        body=json.dumps(
            {
                "error": {"code": "offer_changed", "reason": "digest_mismatch"},
                "schema_version": "review-error-v1",
            }
        ),
    )


def _in_view(page: Page, selector: str) -> dict[str, float]:
    return cast(
        "dict[str, float]",
        page.evaluate(
            """(selector) => {
              const box = document.querySelector(selector).getBoundingClientRect()
              return {left: box.left, right: box.right, width: window.innerWidth}
            }""",
            selector,
        ),
    )


@pytest.mark.parametrize("width", _WIDTHS)
def test_a_level_offer_names_same_title_and_author_beside_its_source(
    width: int,
) -> None:
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(
            viewport={"width": width, "height": 900}, locale="en-US"
        )
        _serve(page, _level_offer)
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.goto(_URL)
            page.get_by_role(
                "button", name=f"Evidence for proposed value: {_PROPOSED}", exact=True
            ).click()
            evidence = page.get_by_role("region", name="evidence")
            source = evidence.get_by_text("Open Library · same title and author")
            expect(source).to_be_visible()
            # The library's own value never carries it.
            expect(evidence.get_by_text("Local library record")).to_have_count(0)
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), (scheme, width)


@pytest.mark.parametrize("width", _WIDTHS)
def test_a_work_being_checked_says_so_and_its_choices_are_unavailable(
    width: int,
) -> None:
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(
            viewport={"width": width, "height": 900}, locale="en-US"
        )
        _serve(page, _checking)
        for scheme in ("light", "dark"):
            page.emulate_media(color_scheme=scheme)
            page.goto(_URL)
            line = page.locator(".case-checking-line")
            expect(line).to_have_text(_CHECKING)
            expect(line).to_have_attribute("role", "status")
            choice = page.get_by_role(
                "radio", name=f"Choose proposed value: {_PROPOSED}", exact=True
            )
            expect(choice).to_be_disabled()
            box = _in_view(page, ".case-checking-line")
            assert 0 <= box["left"] and box["right"] <= box["width"], (scheme, box)
            assert page.evaluate(_NO_SIDEWAYS_SCROLL), (scheme, width)


@pytest.mark.parametrize("width", _WIDTHS)
def test_a_choice_sent_as_the_offer_changed_is_not_recorded_and_says_so(
    width: int,
) -> None:
    sent: list[str] = []

    def refuse(route: Route) -> None:
        sent.append(route.request.post_data or "")
        _refuse_digest_mismatch(route)

    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        browser = playwright.chromium.launch(headless=True)
        stack.callback(browser.close)
        page = browser.new_page(
            viewport={"width": width, "height": 900}, locale="en-US"
        )
        _serve(page, lambda _payload: None, decision=refuse)
        page.goto(_URL)
        page.get_by_role(
            "radio", name=f"Choose proposed value: {_PROPOSED}", exact=True
        ).click()
        line = page.get_by_text(_OFFER_CHANGED)
        expect(line).to_be_visible()
        # One send, never repeated on its own.
        assert len(sent) == 1
        assert json.loads(sent[0])["offered_alternatives_digest"] == "d" * 64
        expect(page.get_by_text("Your choice")).to_have_count(0)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), width
