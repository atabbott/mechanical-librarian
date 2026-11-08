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

from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlencode

from mechanical_librarian.application.review_web import (
    _PREVIEW_DIGEST,
    ReviewWebError,
    _decode_cursor,
    _encode_cursor,
    _report_authors,
    _report_facts,
)

CoverView = Literal["decide", "kept", "switched", "all"]
CoverGroup = Literal["proposal", "choice", "kept", "auto", "none"]
COVER_VIEWS: tuple[CoverView, ...] = ("decide", "kept", "switched", "all")
COVER_GROUPS: tuple[CoverGroup, ...] = ("proposal", "choice", "kept", "auto", "none")
# Pages of 50 books, with "Show more covers" for the next (#1363 Q9).
COVER_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class ReplacedCover:
    """The retained original an automatic switch replaced, named by preview."""

    plan_id: str
    preview_digest: str
    width: int
    height: int


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
    replaced: ReplacedCover | None


@dataclass(frozen=True, slots=True)
class CoverPageSnapshot:
    rows: tuple[CoverRow, ...]
    counts: dict[CoverView, int]


class CoverBrowseStore(Protocol):
    def read_review_covers(
        self, view: CoverView, limit: int, offset: int
    ) -> CoverPageSnapshot: ...
    def read_replaced_cover_preview(self, plan_id: str) -> str | None: ...


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
        }
    return {
        "authors": _report_authors(target),
        "choosable": row.choosable,
        "cover": None
        if report.cover is None
        else _preview(
            "/api/v1/library/cover",
            identity,
            report.cover.preview_digest,
            report.cover.width,
            report.cover.height,
        ),
        "group": row.group,
        "media_kind": report.file.format,
        "review_case_id": row.review_case_id,
        "switched": switched,
        "title": target.current_title or "Untitled",
    }


def cover_page(
    store: CoverBrowseStore, view: str, cursor: str | None
) -> dict[str, object]:
    """One page of a Covers tab, with every tab's count."""
    if view not in COVER_VIEWS:
        raise ReviewWebError("request")
    offset = _decode_cursor(cursor, "covers", view, None)
    try:
        snapshot = store.read_review_covers(view, COVER_PAGE_SIZE + 1, offset)
        items = [_cover_item(row) for row in snapshot.rows[:COVER_PAGE_SIZE]]
    except ReviewWebError:
        raise
    except Exception as error:
        # The store is bound by composition and declares no error type; a page
        # this view cannot render fails whole rather than dropping a work.
        raise ReviewWebError("unavailable") from error
    return {
        "counts": dict(snapshot.counts),
        "items": items,
        "next_cursor": _encode_cursor(offset + COVER_PAGE_SIZE, "covers", view, None)
        if len(snapshot.rows) > COVER_PAGE_SIZE
        else None,
        "schema_version": "review-covers-v1",
        "view": view,
    }


def replaced_cover_preview(store: CoverBrowseStore, plan: str, preview: str) -> str:
    """Name the preview of a listed original, only while its copy is retained.

    A released or unlisted switch, another plan's preview or a guessed digest
    is missing, so the route serves nothing a Switched row could not show.
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
        bound = store.read_replaced_cover_preview(plan)
    except Exception as error:
        raise ReviewWebError("unavailable") from error
    if bound is None or bound != preview:
        raise ReviewWebError("missing")
    return preview
