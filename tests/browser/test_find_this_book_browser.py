"""Find this book, in the exact built shell (#1477 increment 2).

The selected work's header offers Find this book on a work with a case and
on a Not matched work, and nothing on a Could not finish work. The dialog
searches one source, shows its cards (with the results it could not show and
whether there are more), confirms a record with what contradicts it, and
links the work with one request that carries an Idempotency-Key. The link's
pill opens the link view, where Change searches again and Withdraw asks
first. More holds History. The header's entry comes with the Library row, so
it is there in the header's first frame and the link view is read only to
open the dialog. Find this book and its pill keep the field tabs where they
were, and every state reads whole at 320 pixels with 200% text in both themes.

The served shell and cohort are `_serve_cohort`'s; the owner-link reads and
writes are answered here on top, in the wire shapes the server sends.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from playwright.sync_api import Browser, Locator, Page, Route, expect, sync_playwright

from mechanical_librarian.application.review_web import REVIEW_REPORT_DETAIL_SCHEMA
from tests.browser.fidelity_cohort import rich_payload, synthetic_preview
from tests.browser.test_react_platform_artifact import _serve_cohort
from tests.browser.text_size import set_root_text

_ORIGIN = "https://react-platform.invalid"
_CASE_URL = f"{_ORIGIN}/library?lens=all&preview=case-1"
_CASE = "/api/v1/review-cases/case-1"
_ROW = "owner_link_" + "a" * 64
_NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"
_KEY = re.compile(r"^[\x21-\x7e]{16,128}$")
_PROPOSED = "The Clockmaker's Cartography"

# The cohort's Not matched work (Plato) and its Could not finish work
# (Catalogue-only work), each opened through its report.
_NOT_MATCHED = {
    "work_id": f"library_work_{19:064d}",
    "item": "synthetic-item-19",
    "library": "Google Books catalogue",
    "title": "Plato",
    "lens": "not_matched",
}
_COULD_NOT_FINISH = {
    "work_id": f"library_work_{6:064d}",
    "item": "synthetic-item-6",
    "library": "Household ebooks",
    "title": "Catalogue-only work",
    "lens": "preparation_failed",
}

_SOURCES = [
    {"lanes": ["title_authors", "isbn"], "marketplaces": None, "provider": "hardcover"},
    {
        "lanes": ["title_authors", "isbn"],
        "marketplaces": None,
        "provider": "open_library",
    },
    {
        "lanes": ["title_authors", "asin"],
        "marketplaces": ["us", "uk", "de"],
        "provider": "audible_catalog",
    },
]


def _current(**overrides: Any) -> dict[str, Any]:
    return {
        "creators": ["Frank Herbert"],
        "identifier": None,
        "link_row_id": _ROW,
        "paused": False,
        "provider": "hardcover",
        "reason": None,
        "retry_at": None,
        "scope": "work",
        "state": "linked",
        "title": "Dune",
    } | overrides


def _view(
    link: dict[str, Any] | None = None, *, offered: bool = True
) -> dict[str, Any]:
    return {
        "link": link,
        "offered": offered,
        "prefill": {
            "asin": None,
            "authors": ["Frank Herbert"],
            "isbn": None,
            "title": "Dune",
        },
        "schema_version": "owner-link-view-v1",
        "sources": _SOURCES if offered else [],
    }


def _card(position: int, **overrides: Any) -> dict[str, Any]:
    return {
        "asin": None,
        "choosable": True,
        "contradictions": [],
        "creators": ["Frank Herbert"],
        "isbn": "9780441172719",
        "language": "en",
        "narrators": [],
        "position": position,
        "preview": True,
        "publisher": "Ace",
        "runtime_seconds": None,
        "scopes": ["work", "edition"],
        "subtitle": None,
        "title": "Dune",
        "withheld": [],
        "year": 1990,
    } | overrides


def _search(cards: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    return {
        "cards": cards,
        "dropped": 0,
        "lane": "title_authors",
        "marketplace": None,
        "more": False,
        "provider": "hardcover",
        "schema_version": "owner-search-v1",
        "search_id": "search-1",
    } | overrides


_RESULTS = _search(
    [
        _card(0),
        _card(1, title="Dune Messiah", year=1969, isbn="9780441172696", preview=False),
        _card(
            2,
            title="Dune: Deluxe Edition",
            publisher="Penguin Galaxy",
            year=2016,
            isbn=None,
            scopes=["work"],
            withheld=[{"scope": "edition", "reason": "no_isbn"}],
        ),
        _card(
            3,
            title="The Dune Encyclopedia",
            creators=["Willis E. McNelly"],
            choosable=False,
            scopes=[],
            year=1984,
            isbn=None,
            contradictions=[
                {
                    "code": "compilation",
                    "identifier": None,
                    "language": None,
                    "record": None,
                    "work": None,
                }
            ],
        ),
    ],
    dropped=2,
    more=True,
)
_CONFIRM = _search(
    [
        _card(
            0,
            title="Der Wüstenplanet",
            language="de",
            publisher="Heyne",
            isbn="9783453317178",
            scopes=["work"],
            withheld=[{"scope": "edition", "reason": "format_print"}],
            contradictions=[
                {
                    "code": "identifier",
                    "identifier": "isbn",
                    "language": None,
                    "record": "9783453317178",
                    "work": "9780441172719",
                },
                {
                    "code": "language",
                    "identifier": None,
                    "language": "de",
                    "record": None,
                    "work": None,
                },
            ],
        )
    ]
)


def _refusal(code: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, **extra}, "schema_version": "review-error-v1"}


def _report_url(work: dict[str, str]) -> str:
    query = urlencode(
        {"connection": "synthetic", "library": work["library"], "item": work["item"]}
    )
    return f"/api/v1/library/report?{query}"


def _report(query: dict[str, list[str]], title: str) -> dict[str, Any]:
    """`GET /api/v1/library/report` 200, as `frontend/tests/wireFixtures.ts`
    builds it."""
    return {
        "audiobook_review_scope": None,
        "chapters": [],
        "chapters_truncated": False,
        "connection": query["connection"][0],
        "embedded_cover": {"present": False, "surface": None},
        "evidence": [],
        "evidence_truncated": False,
        "item": query["item"][0],
        "library": query["library"][0],
        "library_display_name": None,
        "media_health": [],
        "media_kind": "epub",
        "provider_evidence": [],
        "provider_evidence_truncated": False,
        "provider_sources": [],
        "recording_admission_basis": None,
        "recording_identity_basis": None,
        "report_id": "report_" + "a" * 64,
        "schema_version": REVIEW_REPORT_DETAIL_SCHEMA,
        "title": title,
    }


@dataclass
class _Served:
    """What the owner-link routes answer, and every write they were sent."""

    view: dict[str, Any] = field(default_factory=_view)
    # A view per catalogue item, before `view`.
    views: dict[str, dict[str, Any]] = field(default_factory=dict)
    search: tuple[int, dict[str, Any]] = (200, _RESULTS)
    decision: tuple[int, dict[str, Any]] | None = None
    # The view a write leaves behind, read by the dialog's refetch.
    after_write: dict[str, Any] | None = None
    searches: list[dict[str, Any]] = field(default_factory=list)
    writes: list[tuple[str, dict[str, str], dict[str, Any]]] = field(
        default_factory=list
    )
    link_reads: int = 0
    # The case work's title on its Library row, in place of the cohort's.
    case_title: str | None = None


def _fulfill(route: Route, body: Any, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _serve(page: Page, served: _Served) -> None:
    _serve_cohort(page)
    titles = {work["title"] for work in (_NOT_MATCHED, _COULD_NOT_FINISH)}

    def handler(route: Route) -> None:
        request = route.request
        parts = urlsplit(request.url)
        path, query = parts.path, parse_qs(parts.query)
        if path == "/api/v1/library" and request.method == "GET":
            payload = cast("dict[str, Any]", rich_payload(request.url))
            for work in payload["items"]:
                # The row says what the work's link view says, as the server's
                # row and view do: the header shows its entry from the row.
                view = served.views.get(work["item"], served.view)
                work["find_offered"] = view["offered"]
                work["link"] = (
                    None
                    if view["link"] is None
                    else {
                        "state": view["link"]["state"],
                        "provider": view["link"]["provider"],
                    }
                )
                if served.case_title and work.get("review_case_id") == "case-1":
                    work["title"] = served.case_title
                for chosen in (_NOT_MATCHED, _COULD_NOT_FINISH):
                    if work["work_id"] == chosen["work_id"]:
                        work["detail_kind"] = "report"
                        work["detail_url"] = _report_url(chosen)
            _fulfill(route, payload)
        elif path == "/api/v1/library/report":
            item = query["item"][0]
            title = next(
                (
                    w["title"]
                    for w in (_NOT_MATCHED, _COULD_NOT_FINISH)
                    if w["item"] == item
                ),
                "Recorded work",
            )
            assert title in titles
            _fulfill(route, _report(query, title))
        elif path == "/api/v1/library/link":
            served.link_reads += 1
            _fulfill(route, served.views.get(query["item"][0], served.view))
        elif path == "/api/v1/library/find/searches" and request.method == "POST":
            sent = json.loads(request.post_data or "{}")
            served.searches.append(sent)
            status, body = served.search
            if status == 200:
                body = body | {
                    "provider": sent["provider"],
                    "lane": sent["lane"],
                    "marketplace": sent.get("marketplace"),
                }
            _fulfill(route, body, status)
        elif "/previews/" in path:
            route.fulfill(
                content_type="image/png", body=synthetic_preview("cover-ref-choice")
            )
        elif path.endswith("/choice") or path == "/api/v1/library/link/withdraw":
            withdraw = path.endswith("withdraw")
            served.writes.append(
                (path, dict(request.headers), json.loads(request.post_data or "{}"))
            )
            if served.after_write is not None:
                served.view = served.after_write
            _fulfill(
                route,
                {
                    "action": "withdraw" if withdraw else "link",
                    "link_row_id": _ROW,
                    "scan_requested": True,
                    "schema_version": "owner-link-change-v1",
                },
            )
        elif (
            path == _CASE + "/decisions"
            and request.method == "POST"
            and served.decision is not None
        ):
            served.writes.append(
                (path, dict(request.headers), json.loads(request.post_data or "{}"))
            )
            status, body = served.decision
            _fulfill(route, body, status)
        else:
            route.fallback()

    # Registered after the cohort, so it is asked first.
    page.route("**/api/v1/**", handler)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with ExitStack() as stack:
        playwright = stack.enter_context(sync_playwright())
        launched = playwright.chromium.launch(headless=True)
        stack.callback(launched.close)
        yield launched


def _page(
    browser: Browser,
    served: _Served,
    width: int = 1440,
    height: int = 900,
    scheme: str = "light",
) -> Page:
    page = browser.new_page(
        viewport={"width": width, "height": height},
        locale="en-US",
        timezone_id="UTC",
        color_scheme=cast("Any", scheme),
    )
    _serve(page, served)
    return page


def _actions(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Work actions")


def _dialog(page: Page) -> Locator:
    return page.locator("dialog.find-book-dialog")


def _open_case(page: Page, url: str = _CASE_URL) -> None:
    page.goto(url)
    page.locator(".field-filmstrip").wait_for()


# Every state the Work actions nav is committed in, before it is painted: the
# number of its actions and whether the link view had been read by then.
_WATCH_ACTIONS = """() => {
  window.__workActions = []
  const seen = () => {
    const nav = document.querySelector('nav[aria-label="Work actions"]')
    if (!nav) return
    const names = Array.from(
      nav.querySelectorAll(':scope > a, :scope > button, :scope > div > button.work-action'),
      (node) => node.textContent.trim(),
    )
    const last = window.__workActions.at(-1)
    if (last === undefined || last.join('|') !== names.join('|')) window.__workActions.push(names)
  }
  new MutationObserver(seen).observe(document, { childList: true, subtree: true })
}"""


def _open_find(page: Page) -> Locator:
    _actions(page).get_by_role("button", name="Find this book", exact=True).click()
    dialog = _dialog(page)
    expect(dialog).to_be_visible()
    expect(dialog.get_by_role("heading", name="Find this book")).to_be_focused()
    return dialog


def _search_now(dialog: Locator) -> None:
    dialog.get_by_role("button", name="Search", exact=True).click()


def _work_url(work: dict[str, str]) -> str:
    return f"{_ORIGIN}/library?lens={work['lens']}&work={work['work_id']}"


def test_a_case_work_and_a_not_matched_work_offer_find_this_book_and_could_not_finish_does_not(
    browser: Browser,
) -> None:
    served = _Served(views={_COULD_NOT_FINISH["item"]: _view(offered=False)})
    page = _page(browser, served)
    try:
        page.add_init_script(f"({_WATCH_ACTIONS})()")
        _open_case(page)
        find = _actions(page).get_by_role("button", name="Find this book", exact=True)
        expect(find).to_be_visible()
        expect(find).to_have_attribute("aria-haspopup", "dialog")
        # The entry is in the nav's first committed state, before any paint,
        # and nothing read the link view to decide it (#1477).
        assert page.evaluate("() => window.__workActions") == [
            ["Evidence", "Find this book", "More"]
        ]
        assert served.link_reads == 0

        page.goto(_work_url(_NOT_MATCHED))
        expect(page.get_by_role("heading", name="Plato", exact=True)).to_be_visible()
        find = _actions(page).get_by_role("button", name="Find this book", exact=True)
        expect(find).to_be_visible()
        assert page.evaluate("() => window.__workActions") == [["Find this book"]]
        assert served.link_reads == 0
        with page.expect_response(
            lambda r: urlsplit(r.url).path == "/api/v1/library/link"
        ) as read:
            find.click()
        assert parse_qs(urlsplit(read.value.url).query)["item"] == [
            _NOT_MATCHED["item"]
        ]
        assert served.link_reads == 1
        expect(
            _dialog(page).get_by_role("heading", name="Find this book")
        ).to_be_focused()
        _dialog(page).get_by_role("button", name="Close", exact=True).click()
        expect(_dialog(page)).to_have_count(0)
        expect(find).to_be_focused()

        page.goto(_work_url(_COULD_NOT_FINISH))
        expect(
            page.get_by_role("heading", name="Catalogue-only work", exact=True)
        ).to_be_visible()
        page.evaluate(
            "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
        )
        expect(page.get_by_role("button", name="Find this book")).to_have_count(0)
        expect(_actions(page)).to_have_count(0)
        assert served.link_reads == 1
    finally:
        page.close()


def test_results_show_their_cards_what_was_dropped_and_that_there_are_more(
    browser: Browser,
) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        expect(dialog.get_by_label("Title", exact=True)).to_have_value("Dune")
        expect(dialog.get_by_role("textbox", name="Author 1")).to_have_value(
            "Frank Herbert"
        )
        expect(
            dialog.get_by_text("Search sends the title and authors to Hardcover.")
        ).to_be_visible()
        _search_now(dialog)
        results = dialog.get_by_role("region", name="Results")
        count = results.get_by_role("status")
        expect(count).to_have_text("4 results")
        expect(count).to_be_focused()
        assert served.searches == [
            {
                "connection": served.searches[0]["connection"],
                "library": served.searches[0]["library"],
                "item": served.searches[0]["item"],
                "provider": "hardcover",
                "lane": "title_authors",
                "title": "Dune",
                "authors": ["Frank Herbert"],
            }
        ]
        expect(results.get_by_role("listitem")).to_have_count(4)
        expect(
            results.get_by_role("button", name=re.compile("^Choose "))
        ).to_have_count(3)
        expect(
            results.get_by_role("button", name="Choose The Dune Encyclopedia")
        ).to_have_count(0)
        expect(
            results.get_by_text(
                "Hardcover lists no ISBN or ID for this record, so it cannot be linked."
            )
        ).to_be_visible()
        expect(results.get_by_text("More results. Refine the search.")).to_be_visible()
        expect(results.get_by_text("2 results could not be shown.")).to_be_visible()
        # The disclosure has been read once a search was sent.
        expect(dialog.get_by_text("Search sends the title")).to_have_count(0)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL)
    finally:
        page.close()


def test_the_confirmation_says_what_contradicts_the_record_and_offers_only_its_scopes(
    browser: Browser,
) -> None:
    served = _Served(search=(200, _CONFIRM))
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        _search_now(dialog)
        dialog.get_by_role("button", name="Choose Der Wüstenplanet").click()
        confirm = dialog.get_by_role("region", name="Confirm the link")
        question = confirm.get_by_text(
            "Link this work to Der Wüstenplanet by Frank Herbert from Hardcover?"
        )
        expect(question).to_be_focused()
        expect(confirm.locator(".find-book-differs li")).to_have_text(
            [
                "The record lists ISBN 9783453317178. This work has ISBN 9780441172719.",
                "The record is in German (de), which is not one of this library’s"
                " languages.",
            ]
        )
        expect(
            confirm.get_by_text(
                "This edition is not offered because Hardcover lists it as a print"
                " edition."
            )
        ).to_be_visible()
        scopes = confirm.get_by_role("group", name="Link as")
        expect(scopes.get_by_role("button")).to_have_text(
            ["This book", "Back to results"]
        )
        scopes.get_by_role("button", name="Back to results").click()
        expect(
            dialog.get_by_role("region", name="Results").get_by_role("status")
        ).to_be_focused()
        assert served.writes == []
    finally:
        page.close()


def test_none_of_these_closes_without_a_request_and_returns_focus(
    browser: Browser,
) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        _search_now(dialog)
        dialog.get_by_role("button", name="None of these", exact=True).click()
        expect(_dialog(page)).to_have_count(0)
        expect(
            _actions(page).get_by_role("button", name="Find this book", exact=True)
        ).to_be_focused()
        assert served.writes == []
        assert len(served.searches) == 1
    finally:
        page.close()


# Where focus is after a Tab: a control in the dialog, the browser's own
# controls (the page's body holds it), or something on the page behind.
_FOCUS_PLACE = """() => {
  const active = document.activeElement
  if (active === null || active === document.body) return 'browser'
  return active.closest('dialog.find-book-dialog') ? 'dialog' : 'page'
}"""


def test_tab_never_leaves_the_dialog_for_the_page_behind_it(browser: Browser) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        close = dialog.get_by_role("button", name="Close", exact=True)
        # The modal dialog leaves the page behind it inert. Past its last
        # control focus may go to the browser's own controls, then comes back
        # to the dialog's first; it never reaches the page, either way round.
        closes = 0
        for _ in range(24):
            page.keyboard.press("Tab")
            assert page.evaluate(_FOCUS_PLACE) != "page"
            closes += close.evaluate("e => e === document.activeElement")
        # Close is first, and focus came round to it again.
        assert closes >= 2
        for _ in range(24):
            page.keyboard.press("Shift+Tab")
            assert page.evaluate(_FOCUS_PLACE) != "page"
    finally:
        page.close()


def test_choosing_sends_only_position_and_scope_under_an_idempotency_key(
    browser: Browser,
) -> None:
    served = _Served(
        after_write=_view(_current(scope="edition", identifier="9780441172719"))
    )
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        _search_now(dialog)
        dialog.get_by_role("button", name="Choose Dune", exact=True).click()
        scopes = dialog.get_by_role("group", name="Link as")
        expect(scopes.get_by_role("button")).to_have_text(
            ["This book", "This edition", "Back to results"]
        )
        scopes.get_by_role("button", name="This edition", exact=True).click()
        expect(_dialog(page)).to_have_count(0)
        pill = _actions(page).get_by_role(
            "button", name="Linked on Hardcover", exact=True
        )
        expect(pill).to_be_focused()
        expect(
            _actions(page).get_by_role("button", name="Find this book")
        ).to_have_count(0)
        assert len(served.writes) == 1
        path, headers, body = served.writes[0]
        assert path == "/api/v1/library/find/searches/search-1/choice"
        assert _KEY.match(headers["idempotency-key"]), headers
        assert body == {"position": 0, "scope": "edition"}
    finally:
        page.close()


def test_the_pill_opens_the_link_where_change_searches_and_withdraw_asks_first(
    browser: Browser,
) -> None:
    served = _Served(view=_view(_current()), after_write=_view())
    page = _page(browser, served)
    try:
        _open_case(page)
        pill = _actions(page).get_by_role(
            "button", name="Linked on Hardcover", exact=True
        )
        expect(pill).to_have_attribute("aria-haspopup", "dialog")
        pill.click()
        dialog = _dialog(page)
        expect(dialog.get_by_role("heading", name="Book link")).to_be_focused()
        link = dialog.get_by_role("region", name="Book link")
        expect(
            link.get_by_text("Linked to Dune by Frank Herbert on Hardcover")
        ).to_be_visible()
        expect(link.get_by_text("As this book", exact=True)).to_be_visible()

        # Change: the search form, in the same dialog.
        link.get_by_role("button", name="Change the link").click()
        expect(dialog.get_by_role("heading", name="Find this book")).to_be_focused()
        expect(dialog.get_by_role("form", name="Search")).to_be_visible()
        page.keyboard.press("Escape")
        expect(_dialog(page)).to_have_count(0)
        expect(pill).to_be_focused()

        # Withdraw asks in the dialog; Keep the link hands focus back.
        pill.click()
        withdraw = dialog.get_by_role("button", name="Withdraw the link")
        withdraw.click()
        question = dialog.get_by_text(
            "Withdraw the link to Dune? Its evidence leaves this work at the next"
            " preparation."
        )
        expect(question).to_be_focused()
        dialog.get_by_role("button", name="Keep the link").click()
        expect(withdraw).to_be_focused()
        assert served.writes == []

        withdraw.click()
        dialog.get_by_role("group").get_by_role(
            "button", name="Withdraw", exact=True
        ).click()
        expect(_dialog(page)).to_have_count(0)
        find = _actions(page).get_by_role("button", name="Find this book", exact=True)
        expect(find).to_be_focused()
        expect(
            page.get_by_role("status").filter(has_text="Link withdrawn.")
        ).to_have_count(1)
        assert len(served.writes) == 1
        path, headers, body = served.writes[0]
        assert path == "/api/v1/library/link/withdraw"
        assert _KEY.match(headers["idempotency-key"]), headers
        assert set(body) == {"connection", "library", "item", "link_row_id"}
        assert body["link_row_id"] == _ROW
    finally:
        page.close()


_RETRY_AT = 1_800_000_000


@pytest.mark.parametrize(
    ("status", "body", "said"),
    [
        (
            503,
            _refusal("provider_busy"),
            re.compile(
                r"^Hardcover is busy with other lookups\. Search again in a minute\.$"
            ),
        ),
        (
            429,
            _refusal("provider_deferred", reason="rate_limited", retry_at=_RETRY_AT),
            re.compile(r"^Hardcover is limiting requests\. Search again after .+\.$"),
        ),
        (
            429,
            _refusal("provider_deferred", reason="quota_exhausted", retry_at=_RETRY_AT),
            re.compile(
                r"^Hardcover’s request quota is used up\. Search again after .+\.$"
            ),
        ),
        (
            429,
            _refusal("search_limit", retry_at=_RETRY_AT),
            re.compile(
                r"^Hardcover has been searched 30 times in the last hour\."
                r" Search again after .+\.$"
            ),
        ),
    ],
    ids=["busy", "deferred", "quota-exhausted", "search-limit"],
)
def test_a_refused_search_says_why_beside_search(
    browser: Browser, status: int, body: dict[str, Any], said: re.Pattern[str]
) -> None:
    served = _Served(search=(status, body))
    page = _page(browser, served)
    try:
        _open_case(page)
        dialog = _open_find(page)
        _search_now(dialog)
        alert = dialog.get_by_role("form", name="Search").get_by_role("alert")
        expect(alert).to_have_text(said)
        expect(dialog.get_by_role("region", name="Results")).to_have_count(0)
        expect(dialog.get_by_role("button", name="Search", exact=True)).to_be_visible()
        # Sent once, never repeated on its own.
        assert len(served.searches) == 1
        assert served.writes == []
    finally:
        page.close()


def test_more_holds_history_and_answers_the_keyboard(browser: Browser) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        _open_case(page)
        actions = _actions(page)
        expect(actions.get_by_role("button", name="Find this book")).to_be_visible()
        # Evidence first, then Find this book, then More (WORK_ACTIONS).
        assert actions.evaluate(
            "nav => Array.from(nav.children, node =>"
            " (node.querySelector(':scope > button') ?? node).textContent.trim())"
            ".filter(Boolean)"
        ) == ["Evidence", "Find this book", "More"]
        more = actions.get_by_role("button", name="More for this work")
        history = actions.get_by_role("link", name="History", exact=True)
        expect(more).to_have_attribute("aria-expanded", "false")
        expect(history).to_be_hidden()
        more.focus()
        page.keyboard.press("Enter")
        expect(more).to_have_attribute("aria-expanded", "true")
        expect(history).to_be_visible()
        page.keyboard.press("Escape")
        expect(history).to_be_hidden()
        expect(more).to_have_attribute("aria-expanded", "false")
        expect(more).to_be_focused()
        page.keyboard.press("Enter")
        expect(history).to_be_visible()
        page.keyboard.press("Tab")
        expect(history).to_be_focused()
        page.keyboard.press("Enter")
        page.get_by_role("heading", name="History", exact=True).wait_for()
        query = parse_qs(urlsplit(page.url).query)
        assert urlsplit(page.url).path == "/review/case-1"
        assert query["subview"] == ["history"]
    finally:
        page.close()


_UNDER_CHOICES_IN_VIEW = """notice => {
  const box = notice.getBoundingClientRect()
  return {
    underChoices: notice.previousElementSibling?.classList.contains('direct-choice') ?? false,
    inView: box.top >= 0 && box.bottom <= window.innerHeight,
  }
}"""


def test_a_decision_refused_because_the_link_changed_says_so(
    browser: Browser,
) -> None:
    served = _Served(decision=(409, _refusal("link_changed")))
    page = _page(browser, served)
    try:
        _open_case(page)
        page.get_by_role(
            "radio", name=f"Choose proposed value: {_PROPOSED}", exact=True
        ).click()
        notice = page.get_by_text(
            "The book link changed. This work is being prepared again."
        )
        expect(notice).to_be_visible()
        # Said right under the choices and brought into the window, so the
        # owner sees why nothing was chosen.
        expect(notice).to_have_attribute("role", "alert")
        assert notice.evaluate(_UNDER_CHOICES_IN_VIEW) == {
            "underChoices": True,
            "inView": True,
        }
        # One send, never repeated on its own.
        assert len(served.writes) == 1
        expect(page.get_by_text("Your choice")).to_have_count(0)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL)
    finally:
        page.close()


_DIALOG_FITS = """() => {
  const dialog = document.querySelector('dialog.find-book-dialog')
  const body = dialog.querySelector('.find-book-body')
  const box = dialog.getBoundingClientRect()
  return {
    dialog: dialog.scrollWidth <= dialog.clientWidth,
    body: body.scrollWidth <= body.clientWidth,
    inside: box.left >= 0 && box.right <= window.innerWidth,
  }
}"""
_FITS = {"dialog": True, "body": True, "inside": True}
_SCHEMES = pytest.mark.parametrize("scheme", ["light", "dark"], ids=["light", "dark"])


@_SCHEMES
def test_320_pixels_with_200_percent_text_reads_whole_in_the_header_confirmation_and_link(
    browser: Browser, scheme: str
) -> None:
    served = _Served()
    page = _page(browser, served, 320, 900, scheme)
    try:
        _open_case(page)
        set_root_text(page, "200%")
        expect(
            _actions(page).get_by_role("button", name="Find this book")
        ).to_be_visible()
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "header"
        dialog = _open_find(page)
        _search_now(dialog)
        expect(dialog.get_by_role("region", name="Results")).to_be_visible()
        dialog.get_by_role("button", name="Choose Dune", exact=True).click()
        expect(dialog.get_by_role("region", name="Confirm the link")).to_be_visible()
        assert page.evaluate(_DIALOG_FITS) == _FITS, "confirmation"
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "confirmation"
    finally:
        page.close()

    served = _Served(view=_view(_current(state="waiting", retry_at=_RETRY_AT)))
    page = _page(browser, served, 320, 900, scheme)
    try:
        _open_case(page)
        set_root_text(page, "200%")
        pill = _actions(page).get_by_role(
            "button", name="Linked, waiting for Hardcover", exact=True
        )
        expect(pill).to_be_visible()
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "pill"
        pill.click()
        # The link view is read when the pill is pressed, so the dialog opens
        # once it answers.
        expect(_dialog(page).get_by_role("heading", name="Book link")).to_be_focused()
        assert page.evaluate(_DIALOG_FITS) == _FITS, "link view"
        _dialog(page).get_by_role("button", name="Withdraw the link").click()
        assert page.evaluate(_DIALOG_FITS) == _FITS, "withdraw question"
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "withdraw question"
    finally:
        page.close()


@_SCHEMES
def test_320_pixels_with_200_percent_text_reads_whole_in_the_search_form_and_results(
    browser: Browser, scheme: str
) -> None:
    served = _Served()
    page = _page(browser, served, 320, 900, scheme)
    try:
        _open_case(page)
        set_root_text(page, "200%")
        dialog = _open_find(page)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "form"
        assert page.evaluate(_DIALOG_FITS) == _FITS, "form"
        _search_now(dialog)
        expect(dialog.get_by_role("region", name="Results")).to_be_visible()
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "results"
        assert page.evaluate(_DIALOG_FITS) == _FITS, "results"
    finally:
        page.close()


_FIELD_TAB_Y = """() => {
  const box = document.querySelector('.field-filmstrip').getBoundingClientRect()
  return box.top + window.scrollY
}"""


_HEADER_STATES = {
    "not-linked": (_view(), "Find this book"),
    "linked": (_view(_current()), "Linked on Hardcover"),
    "not-offered": (_view(offered=False), None),
}


def _field_tab_ys(
    browser: Browser,
    width: int,
    text: str = "",
    *,
    states: dict[str, tuple[dict[str, Any], str | None]] = _HEADER_STATES,
    title: str | None = None,
) -> dict[str, float]:
    measured: dict[str, float] = {}
    for name, (view, control) in states.items():
        page = _page(browser, _Served(view=view, case_title=title), width)
        try:
            _open_case(page)
            if text:
                set_root_text(page, text)
            actions = _actions(page)
            if control is None:
                expect(
                    actions.get_by_role("button", name="More for this work")
                ).to_be_visible()
                expect(actions.locator(".work-action-pill")).to_have_count(0)
                expect(
                    actions.get_by_role("button", name="Find this book")
                ).to_have_count(0)
            else:
                expect(
                    actions.get_by_role("button", name=control, exact=True)
                ).to_be_visible()
            page.evaluate("() => document.fonts.ready")
            measured[name] = page.evaluate(_FIELD_TAB_Y)
        finally:
            page.close()
    return measured


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_the_field_tabs_stay_put_whether_the_work_is_linked_and_rise_only_when_not_offered(
    browser: Browser, width: int
) -> None:
    """Find this book and the pill that replaces it hold the tabs still. A work
    that is not offered has one action fewer, so its tabs may sit higher (at
    1440 the cohort's long case-1 title wraps three actions but not two), and
    never lower. Each state is the header's first and only one: the row says
    which, so nothing moves after load (#1477)."""
    measured = _field_tab_ys(browser, width)
    assert measured["linked"] == measured["not-linked"], measured
    assert measured["not-offered"] <= measured["not-linked"], measured


@pytest.mark.parametrize("width", [1280, 1100], ids=["1280", "1100"])
def test_a_long_link_pill_takes_no_width_from_an_ordinary_title(
    browser: Browser, width: int
) -> None:
    """The work actions' column is as wide whatever the link's state, so the
    longest pill, the notice-toned waiting one, wraps inside itself rather than
    narrowing an ordinary title and moving the field tabs (#1477)."""
    measured = _field_tab_ys(
        browser,
        width,
        states={
            "not-linked": _HEADER_STATES["not-linked"],
            "linked": _HEADER_STATES["linked"],
            "waiting": (
                _view(_current(provider="google_books", state="waiting")),
                "Linked, waiting for Google Books",
            ),
        },
        title="The Lighthouse Keeper's Daughter",
    )
    assert measured["waiting"] == measured["linked"] == measured["not-linked"], measured
