"""The Covers view: every work's cover, grouped by what it asks of the owner.

Library's Covers view (#1633, #1363) reads one bounded page of current works
under a tab. A work's group is decided by the store from persisted facts:

- ``proposal`` and ``choice``: its cover field asks for the owner, exactly as
  Library's attention reads it, as a suggested switch or an open choice;
- ``auto``: its current cover is an automatic write that the owner has not
  answered since, listed while the replaced original is still retained
  (ADR-0093, Amendment 6) or when the write filled an empty cover;
- ``kept``: the current cover stays and other covers can still be chosen;
- ``none``: there is only the one cover.

Decisions are not made here: each goes through the work's existing decision
command, so this module only reads. A page names covers by preview only.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlencode

from mechanical_librarian.application.review_web import (
    _PREVIEW_DIGEST,
    ReviewWebError,
    _cursor_binding,
    _report_authors,
    _report_facts,
)

CoverView = Literal["decide", "kept", "switched", "all"]
CoverGroup = Literal["proposal", "choice", "kept", "auto", "none"]
COVER_VIEWS: tuple[CoverView, ...] = ("decide", "kept", "switched", "all")
COVER_GROUPS: tuple[CoverGroup, ...] = ("proposal", "choice", "kept", "auto", "none")
# Pages of 50 books, with "Show more covers" for the next (#1363 Q9).
COVER_PAGE_SIZE = 50
# A row's place in its tab's order, as the store sorts it: decide by
# (choice, title, item, case), kept and all by (title, item, case), switched
# by (verified at, listing sequence, plan), newest first. A page resumes after
# the last row's key, so a decision on an earlier page never shifts the next
# one (#1633 review B1).
CoverKey = tuple[int | str, ...]
_KEY_TYPES: dict[CoverView, tuple[type, ...]] = {
    "decide": (int, str, str, str),
    "kept": (str, str, str),
    "switched": (int, int, str),
    "all": (str, str, str),
}
_CASE_ID = re.compile(r"review_case_[0-9a-f]{64}")
# The largest key a cursor carries: a title key prefix of 256 code points
# (4 UTF-8 bytes each, doubled for JSON escapes), an item id of up to 512
# bytes (doubled), a case id, the binding and the integers: under 3,400
# bytes, under 4,600 characters of base64. The route accepts the same.
_CURSOR_LIMIT = 6144


@dataclass(frozen=True, slots=True)
class SwitchCover:
    """A cover one automatic switch names by preview: its retained original,
    or the cover it put in place."""

    plan_id: str
    preview_digest: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PutBack:
    """The owner's Put back of a listed switch: its write's state and failure.

    Both are the field effect's own closed values, so a Put back the target
    refused because its cover changed reads ``stale`` and ``target_changed``.
    """

    state: str
    failure: str | None


@dataclass(frozen=True, slots=True)
class CoverRow:
    review_case_id: str
    report_document: str
    group: CoverGroup
    # Whether the cover field offers the owner a choice at all.
    choosable: bool
    # When the automatic write was verified, for an ``auto`` row only.
    switched_at: int | None
    # An automatic fill replaced nothing; a replacement names its original.
    filled: bool
    replaced: SwitchCover | None
    # The cover the automatic write put in place, for an ``auto`` row: the
    # report keeps its pre-write cover until the next scan.
    replacement: SwitchCover | None
    key: CoverKey
    put_back: PutBack | None = None
    # Whether the work's current comparison offers the original, so Put back
    # can be recorded now (#1363).
    put_back_offered: bool = False
    # The measured rule's reason an automatic switch recorded, and the edition
    # language that reason names; a fill has neither (#1363).
    switch_reason: str | None = None
    switch_language: str | None = None
    # Why the measured rules suggest switching this cover, for a suggestion.
    suggestion_reason: str | None = None
    # Why the measured rules hold this cover's switch, for a hold.
    hold_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CoverPageSnapshot:
    rows: tuple[CoverRow, ...]
    counts: dict[CoverView, int]


class CoverBrowseStore(Protocol):
    def read_review_covers(
        self,
        view: CoverView,
        limit: int,
        after: CoverKey | None,
        review_case_id: str | None = None,
    ) -> CoverPageSnapshot: ...
    def read_switch_cover_previews(self, plan_id: str) -> frozenset[str]: ...


def _preview(
    path: str, query: dict[str, str], preview: str, width: int, height: int
) -> dict[str, object]:
    return {
        "height": height,
        "preview_sha256": preview,
        "url": path + "?" + urlencode({**query, "preview": preview}),
        "width": width,
    }


def _cover_item(row: CoverRow) -> dict[str, object]:
    report = _report_facts(row.report_document)
    target = report.target
    identity = {
        "connection": target.connection,
        "library": target.library,
        "item": target.item,
    }
    switched = None
    # An automatic write's Now is the cover it verified, until a scan
    # republishes the work with it.
    now = row.replacement if row.group == "auto" else None
    cover = (
        _preview(
            "/api/v1/covers/replaced",
            {"plan": now.plan_id},
            now.preview_digest,
            now.width,
            now.height,
        )
        if now is not None
        else None
        if report.cover is None
        else _preview(
            "/api/v1/library/cover",
            identity,
            report.cover.preview_digest,
            report.cover.width,
            report.cover.height,
        )
    )
    if row.group == "auto":
        if row.switched_at is None:
            raise ReviewWebError("unavailable")
        replaced = row.replaced
        switched = {
            "at": row.switched_at,
            # A fill replaced nothing, so it has no Before (#1363 Q6). A
            # replacement whose preview cannot be found shows it unavailable.
            "before": None
            if replaced is None
            else _preview(
                "/api/v1/covers/replaced",
                {"plan": replaced.plan_id},
                replaced.preview_digest,
                replaced.width,
                replaced.height,
            ),
            "filled": row.filled,
            "put_back": None
            if row.put_back is None
            else {"failure": row.put_back.failure, "state": row.put_back.state},
            "put_back_offered": row.put_back_offered,
            "reason": row.switch_reason,
            "language": row.switch_language,
        }
    return {
        "authors": _report_authors(target),
        "choosable": row.choosable,
        "cover": cover,
        "group": row.group,
        "media_kind": report.file.format,
        "review_case_id": row.review_case_id,
        "hold": None if row.hold_reason is None else {"reason": row.hold_reason},
        "suggestion": None
        if row.suggestion_reason is None
        else {"reason": row.suggestion_reason},
        "switched": switched,
        "title": target.current_title or "Untitled",
        "unreadable_cover": cover is None and report.cover_unreadable,
    }


def _encode_cover_cursor(key: CoverKey, view: CoverView) -> str:
    payload = json.dumps(
        [_cursor_binding("covers", view, None), *key],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    cursor = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if len(cursor) > _CURSOR_LIMIT:
        raise ReviewWebError("unavailable")
    return cursor


def _decode_cover_cursor(cursor: str | None, view: CoverView) -> CoverKey | None:
    if cursor is None:
        return None
    try:
        if (
            type(cursor) is not str
            or not 1 <= len(cursor) <= _CURSOR_LIMIT
            or cursor.strip() != cursor
        ):
            raise ValueError
        raw = json.loads(
            base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            ).decode("utf-8")
        )
        types = _KEY_TYPES[view]
        if (
            type(raw) is not list
            or len(raw) != len(types) + 1
            or raw[0] != _cursor_binding("covers", view, None)
            or any(
                type(value) is not kind
                for value, kind in zip(raw[1:], types, strict=True)
            )
        ):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        raise ReviewWebError("request") from None
    return tuple(raw[1:])


def cover_page(
    store: CoverBrowseStore,
    view: str,
    cursor: str | None,
    cover: str | None = None,
) -> dict[str, object]:
    """One page of a Covers tab, with every tab's count.

    ``cover`` reads only that book under the tab, so a link to a book past
    the first page opens it on its own; it takes no cursor.
    """
    if view not in COVER_VIEWS:
        raise ReviewWebError("request")
    if cover is not None and (
        cursor is not None or type(cover) is not str or not _CASE_ID.fullmatch(cover)
    ):
        raise ReviewWebError("request")
    after = _decode_cover_cursor(cursor, view)
    try:
        snapshot = store.read_review_covers(view, COVER_PAGE_SIZE + 1, after, cover)
        page = snapshot.rows[:COVER_PAGE_SIZE]
        items = [_cover_item(row) for row in page]
    except ReviewWebError:
        raise
    except Exception as error:
        # The store is bound by composition and declares no error type; a page
        # this view cannot render fails whole rather than dropping a work.
        raise ReviewWebError("unavailable") from error
    return {
        "counts": dict(snapshot.counts),
        "items": items,
        "next_cursor": _encode_cover_cursor(page[-1].key, view)
        if len(snapshot.rows) > COVER_PAGE_SIZE
        else None,
        "schema_version": "review-covers-v1",
        "view": view,
    }


def replaced_cover_preview(store: CoverBrowseStore, plan: str, preview: str) -> str:
    """Name a preview a Switched row shows: Before or Now of one switch.

    Before is the listed original, only while its copy is retained; Now is
    the cover the verified automatic write put in place. A released or
    unlisted switch, another plan's preview or a guessed digest is missing,
    so the route serves nothing a Switched row could not show.
    """
    if (
        type(plan) is not str
        or not plan.startswith("effect_plan_")
        or len(plan) != 76
        or _PREVIEW_DIGEST.fullmatch(plan.removeprefix("effect_plan_")) is None
        or type(preview) is not str
        or _PREVIEW_DIGEST.fullmatch(preview) is None
    ):
        raise ReviewWebError("missing")
    try:
        bound = store.read_switch_cover_previews(plan)
    except Exception as error:
        raise ReviewWebError("unavailable") from error
    if preview not in bound:
        raise ReviewWebError("missing")
    return preview
