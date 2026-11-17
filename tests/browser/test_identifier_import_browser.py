"""Import identifiers, in the exact built shell (#1478).

Each saved library's record in Settings ends with Import identifiers, which
opens a page in the libraries list's place. A chosen file is sent as text
for its preview, which shows what will be linked and why each other row will
not; Link sends one request under an Idempotency-Key. A preview the library
changed after says so and previews again. The import's Activity Details read
how many of its links remain and withdraw them after asking. Every state
reads whole at 1440, 390 and 320 pixels with 200% text in both themes,
identifiers never break inside a number, and the keyboard reaches each
control in order.

The served shell and cohort are `_serve_cohort`'s; the identifiers file
routes, Settings and the Activity row are answered here on top, in the wire
shapes the server sends.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Locator, Page, Route, expect, sync_playwright

from tests.browser.fidelity_cohort import rich_payload
from tests.browser.test_react_platform_artifact import _serve_cohort
from tests.browser.text_size import set_root_text

_ORIGIN = "https://react-platform.invalid"
_SETTINGS = f"{_ORIGIN}/settings?group=libraries"
_ACTIVITY = f"{_ORIGIN}/activity?eventType=all&rangeDays=30&page=1&pageSize=25"
# One library holding both ebooks and audiobooks, so ISBN and ASIN rows sit
# side by side.
_LIBRARY, _LIBRARY_LABEL = "household-books", "Household books"
_PREVIEWS = f"/api/v1/libraries/primary/{_LIBRARY}/identifier-previews"
_PREVIEW_ID = "4YDGHS-aBmRjDIJPP4PX4HpKc56D-QaSl_0CahS6atc"
_IMPORT = "identifier_import_" + "a" * 64
_KEY = re.compile(r"^[\x21-\x7e]{16,128}$")
_NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"

# The issue's 20 rows, with invented titles: 15 will be linked and 5 will not.
_TITLES = (
    ("The Lantern Keeper's Year", "Maren Ostrow"),
    ("A Map of Quiet Rivers", "Tobiah Fenn"),
    ("Salt and Cedar", "Ilse Varga"),
    ("The Orchard Under Glass", "Pell Hartigan"),
    ("Notes from a Northern Station", "Odile Brannock"),
    ("The Clockmaker's Apprentice", "Rafe Linden"),
    ("Small Hours in Larkspur", "Ada Quill"),
    ("Weathering", "Sunniva Holt"),
    ("The Paper Archipelago", "Mirela Stone, Jonah Pike"),
    ("Field Notes on Borrowed Light", "Corin Ashby"),
    ("The Long Tide Home", "Wren Callow"),
    ("Harbour Songs", "Elio Marsh"),
    ("The Winter Cartographer", "Thea Rook"),
    ("Echoes of the Fen", "Bram Ellery"),
    ("A Quiet Kind of Thunder Road", "Nell Avery"),
)
_REFUSED = (
    (6, "no_item"),
    (9, "ambiguous"),
    (13, "invalid_isbn"),
    (17, "asin_for_ebook"),
    (20, "no_item"),
)
_FILE = b"path\tidentifier\n" + b"".join(
    f"Shelf/Book {row}\t978030640{row:04d}\n".encode() for row in range(2, 22)
)


def _isbn13(stem: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(stem))
    return stem + str((10 - total % 10) % 10)


def _preview() -> dict[str, Any]:
    refused = {row for row, _ in _REFUSED}
    rows = [row for row in range(2, 22) if row not in refused]
    will_link = []
    for index, (row, (title, authors)) in enumerate(zip(rows, _TITLES, strict=True)):
        audio = index >= 10
        will_link.append(
            {
                "row": row,
                "title": title,
                "authors": authors.split(", "),
                "identifier_kind": "asin" if audio else "isbn",
                "identifier": f"B00TEST{index:03d}"
                if audio
                else _isbn13(f"978030640{index:03d}"),
            }
        )
    return {
        "preview_id": _PREVIEW_ID,
        "expires_at": 1_900_000_000,
        "rows": 20,
        "counts": {
            "will_link": 15,
            "already_linked": 0,
            "no_item": 2,
            "ambiguous": 1,
            "invalid_identifier": 1,
            "wrong_medium": 1,
            "repeated": 0,
            "conflicting": 0,
            "unreadable": 0,
        },
        "will_link": will_link,
        "refused": [{"row": row, "reason": reason} for row, reason in _REFUSED],
        "schema_version": "identifier-preview-v1",
    }


def _refusal(code: str) -> dict[str, Any]:
    return {"error": {"code": code}, "schema_version": "review-error-v1"}


def _import_event(occurred_at: int) -> dict[str, Any]:
    """The import's Activity row, as the server's projection sends it."""
    return {
        "author_label": "Author details are not recorded",
        "category": "owner",
        "change": "Linked 15 works",
        "change_id": "identifier_import_event_" + "b" * 64,
        "change_detail": "Linked 15 works from an identifiers file.",
        "check_group": None,
        "destination": "links",
        "diagnostic": None,
        "event_id": "identifier_import_event_" + "b" * 64,
        "event_type": "review",
        "evidence": [
            {"label": "Rows in the file", "value": "20"},
            {"label": "Linked", "value": "15"},
            {"label": "Not linked", "value": "5"},
            {"label": "Chosen by", "value": "You"},
            {
                "label": "What happens next",
                "value": "Linking writes nothing to Audiobookshelf. Each work is "
                "prepared again with its link.",
            },
        ],
        "failure": None,
        "field_label": "Links",
        "identifier_import_id": _IMPORT,
        "policy_changes": [],
        "library": _LIBRARY,
        "note": None,
        "occurred_at": occurred_at,
        "origin": "human",
        "state": "imported",
        "transition_id": None,
        "work_label": "Identifiers file",
        "work_id": "Identifiers file",
    }


@dataclass
class _Served:
    """What the identifiers file routes answer, and every request they were sent."""

    preview: tuple[int, dict[str, Any]] = field(
        default_factory=lambda: (200, _preview())
    )
    imported: tuple[int, dict[str, Any]] = (
        200,
        {
            "import_id": _IMPORT,
            "linked": 15,
            "scan_requested": True,
            "schema_version": "identifier-import-v1",
        },
    )
    preview_status: dict[str, Any] = field(
        default_factory=lambda: {
            "preview_id": _PREVIEW_ID,
            "state": "open",
            "import_id": None,
            "linked": None,
            "schema_version": "identifier-preview-status-v1",
        }
    )
    still_linked: int = 13
    withdrawn: bool = False
    requests: list[tuple[str, dict[str, str], bytes]] = field(default_factory=list)


def _fulfill(route: Route, body: Any, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _serve(page: Page, served: _Served) -> None:
    _serve_cohort(page)

    def handler(route: Route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        if request.method == "POST":
            served.requests.append(
                (path, dict(request.headers), request.post_data_buffer or b"")
            )
        if path == _PREVIEWS and request.method == "POST":
            _fulfill(route, served.preview[1], served.preview[0])
        elif path == f"/api/v1/identifier-previews/{_PREVIEW_ID}/import":
            _fulfill(route, served.imported[1], served.imported[0])
        elif path == f"/api/v1/identifier-previews/{_PREVIEW_ID}":
            _fulfill(route, served.preview_status)
        elif path == f"/api/v1/identifier-imports/{_IMPORT}/withdraw":
            withdrawn = served.still_linked
            served.still_linked, served.withdrawn = 0, True
            _fulfill(
                route,
                {
                    "import_id": _IMPORT,
                    "withdrawn": withdrawn,
                    "scan_requested": True,
                    "schema_version": "identifier-import-withdrawal-v1",
                },
            )
        elif path == f"/api/v1/identifier-imports/{_IMPORT}":
            _fulfill(
                route,
                {
                    "import_id": _IMPORT,
                    "linked": 15,
                    "still_linked": served.still_linked,
                    "withdrawn": served.withdrawn,
                    "schema_version": "identifier-import-v1",
                },
            )
        elif path == "/api/v1/settings" and request.method == "GET":
            settings = cast("dict[str, Any]", rich_payload(request.url))
            (library,) = settings["document"]["libraries"]
            library |= {
                "name": _LIBRARY,
                "display_name": _LIBRARY_LABEL,
                "media_kind": "book",
            }
            _fulfill(route, settings)
        elif path == "/api/v1/activity" and request.method == "GET":
            payload = cast("dict[str, Any]", rich_payload(request.url))
            events = payload["events"]
            payload["events"] = [_import_event(events[0]["occurred_at"]), *events]
            payload["ledger"]["total"] += 1
            _fulfill(route, payload)
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


def _sheet(page: Page) -> Locator:
    return page.locator("section.identifier-import-sheet")


def _open_import(page: Page) -> Locator:
    page.goto(_SETTINGS)
    page.get_by_role("button", name="Import identifiers").click()
    sheet = _sheet(page)
    expect(
        sheet.get_by_role("heading", name=f"Import identifiers for {_LIBRARY_LABEL}")
    ).to_be_focused()
    return sheet


def _choose(sheet: Locator, data: bytes = _FILE) -> None:
    sheet.locator("input.identifier-import-input").set_input_files(
        {"name": "identifiers.tsv", "mimeType": "text/plain", "buffer": data}
    )
    expect(sheet.locator(".identifier-import-name")).to_have_text("identifiers.tsv")


def _previewed(sheet: Locator) -> None:
    _choose(sheet)
    sheet.get_by_role("button", name="Preview", exact=True).click()
    expect(sheet.get_by_text("20 rows in the file.")).to_be_focused()


def _import_details(page: Page) -> Locator:
    page.goto(_ACTIVITY)
    page.get_by_role(
        "button", name=re.compile(r"^Details for Links, Identifiers file")
    ).click()
    return page.get_by_role(
        "button",
        name=re.compile(
            rf"^Withdraw this import, Identifiers file, ({_LIBRARY}|{_LIBRARY_LABEL})"
        ),
    )


def test_each_saved_library_ends_with_import_identifiers_and_back_returns_focus(
    browser: Browser,
) -> None:
    page = _page(browser, _Served())
    try:
        page.goto(_SETTINGS)
        entry = page.get_by_role("button", name="Import identifiers")
        expect(entry).to_have_count(1)
        # The entry is the last thing in the library's record.
        assert entry.evaluate("button => !button.parentElement.nextElementSibling"), (
            "Import identifiers is not at the foot of its record"
        )
        entry.click()
        sheet = _sheet(page)
        expect(
            sheet.get_by_role(
                "heading", name=f"Import identifiers for {_LIBRARY_LABEL}"
            )
        ).to_be_focused()
        # The sheet takes the list's place; the list is not on the page.
        expect(page.get_by_role("button", name="Import identifiers")).to_have_count(0)
        expect(sheet.get_by_role("button", name="Preview", exact=True)).to_be_disabled()
        sheet.get_by_role("button", name="Back to libraries").click()
        expect(page.get_by_role("button", name="Import identifiers")).to_be_focused()
    finally:
        page.close()


def test_a_file_is_previewed_as_text_and_linked_under_one_idempotency_key(
    browser: Browser,
) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        sheet = _open_import(page)
        _previewed(sheet)
        (path, headers, body) = served.requests[-1]
        assert path == _PREVIEWS
        assert headers["content-type"] == "text/plain; charset=utf-8"
        assert body == _FILE
        assert "idempotency-key" not in headers
        # Counts that are not zero, the rows that will be linked, and why the
        # others will not.
        expect(sheet.get_by_text("2 match no work")).to_be_visible()
        expect(sheet.get_by_text("0 repeat an earlier row")).to_have_count(0)
        # Will be linked is counted on the switch and the button, not as a chip.
        expect(sheet.get_by_text("15 will be linked", exact=True)).to_have_count(0)
        expect(sheet.get_by_text("The Lantern Keeper's Year")).to_be_visible()
        sheet.get_by_role("button", name="Not linked (5)").click()
        expect(
            sheet.get_by_text("This is an ASIN, and an ebook needs an ISBN")
        ).to_be_visible()
        sheet.get_by_role("button", name="Link 15 works").click()
        expect(sheet.locator(".identifier-import-success")).to_contain_text(
            "Linked 15 works."
        )
        expect(sheet.locator(".identifier-import-success")).to_be_focused()
        imports = [r for r in served.requests if r[0].endswith("/import")]
        assert len(imports) == 1
        assert _KEY.match(imports[0][1]["idempotency-key"])
        assert imports[0][2] == b""
    finally:
        page.close()


def test_a_preview_the_library_changed_after_says_so_and_previews_again(
    browser: Browser,
) -> None:
    served = _Served(imported=(409, _refusal("preview_stale")))
    page = _page(browser, served)
    try:
        sheet = _open_import(page)
        _previewed(sheet)
        sheet.get_by_role("button", name="Link 15 works").click()
        alert = sheet.get_by_role("alert")
        expect(alert).to_have_text("This library's works changed after the preview.")
        expect(alert).to_be_focused()
        served.imported = (
            200,
            {
                "import_id": _IMPORT,
                "linked": 15,
                "scan_requested": True,
                "schema_version": "identifier-import-v1",
            },
        )
        sheet.get_by_role("button", name="Preview again").click()
        expect(sheet.get_by_text("20 rows in the file.")).to_be_focused()
        assert [path for path, _, _ in served.requests].count(_PREVIEWS) == 2
    finally:
        page.close()


def test_withdraw_this_import_reads_what_remains_and_asks_first(
    browser: Browser,
) -> None:
    served = _Served()
    page = _page(browser, served)
    try:
        withdraw = _import_details(page)
        expect(page.get_by_text("Still linked")).to_be_visible()
        withdraw.click()
        question = page.get_by_text(
            "Withdraw 13 links from this import? Each work is prepared again without its link."
        )
        expect(question).to_be_focused()
        page.get_by_role("button", name="Keep links").click()
        expect(withdraw).to_be_focused()
        assert not [r for r in served.requests if r[0].endswith("/withdraw")]
        withdraw.click()
        page.get_by_role("button", name="Withdraw links").click()
        done = page.get_by_role("status").filter(has_text="Withdrew 13 links.")
        expect(done).to_be_focused()
        (sent,) = [r for r in served.requests if r[0].endswith("/withdraw")]
        assert _KEY.match(sent[1]["idempotency-key"])
    finally:
        page.close()


def test_the_keyboard_reaches_the_file_chooser_preview_and_link_in_order(
    browser: Browser,
) -> None:
    page = _page(browser, _Served())
    try:
        sheet = _open_import(page)
        # From the focused heading, Back is behind and the chooser is next.
        page.keyboard.press("Shift+Tab")
        expect(sheet.get_by_role("button", name="Back to libraries")).to_be_focused()
        page.keyboard.press("Tab")
        chooser = sheet.locator("input.identifier-import-input")
        expect(chooser).to_be_focused()
        # The hidden input's focus shows on its visible label.
        outline = sheet.locator(
            ".identifier-import-choose > span:first-child"
        ).evaluate("span => getComputedStyle(span).outlineStyle")
        assert outline not in {"none", ""}
        with page.expect_file_chooser() as chosen:
            page.keyboard.press("Space")
        chosen.value.set_files(
            {"name": "identifiers.tsv", "mimeType": "text/plain", "buffer": _FILE}
        )
        page.keyboard.press("Tab")
        expect(sheet.get_by_role("button", name="Preview", exact=True)).to_be_focused()
        page.keyboard.press("Enter")
        expect(sheet.get_by_text("20 rows in the file.")).to_be_focused()
    finally:
        page.close()


_IDENTIFIERS_WHOLE = """() => [...document.querySelectorAll('.identifier-import-id > span')]
  .every(node => node.getClientRects().length === 1)"""


@pytest.mark.parametrize(
    ("width", "text", "scheme"),
    [
        (1440, "", "light"),
        (390, "", "light"),
        (390, "", "dark"),
        (320, "200%", "light"),
        (320, "200%", "dark"),
    ],
)
def test_every_state_reads_whole_at_each_width_and_text_size(
    browser: Browser, width: int, text: str, scheme: str
) -> None:
    page = _page(browser, _Served(), width=width, height=800, scheme=scheme)
    try:
        sheet = _open_import(page)
        set_root_text(page, text)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "file step scrolls sideways"
        _previewed(sheet)
        set_root_text(page, text)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "preview scrolls sideways"
        assert page.evaluate(_IDENTIFIERS_WHOLE), "an identifier breaks inside"
        sheet.get_by_role("button", name="Not linked (5)").click()
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "not linked scrolls sideways"
        sheet.get_by_role("button", name="Will be linked (15)").click()
        sheet.get_by_role("button", name="Link 15 works").click()
        expect(sheet.locator(".identifier-import-success")).to_contain_text(
            "Linked 15 works."
        )
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "success scrolls sideways"
        withdraw = _import_details(page)
        set_root_text(page, text)
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "Activity details scroll sideways"
        withdraw.click()
        assert page.evaluate(_NO_SIDEWAYS_SCROLL), "the question scrolls sideways"
    finally:
        page.close()
