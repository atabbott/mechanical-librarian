"""Switch covers by taste: lane 1's switching steps and the preview read
(#1372 PR 5, K4, K6, K7).

Each wake first revokes unwritten taste switches nothing stands behind any
more, then tries to admit `choose` verdicts as automatic decisions, at most
one admitted per wake. Nothing runs before the taste identity has its
record, so until #1418 records a qualification every verdict stays a
suggestion.

The preview reads what the judge already stored under the version in use:
at most 100 checked covers, oldest verdict first, stopping at the fifth
switch the profile would make under the protection asked about. It stores
nothing and asks the judge for nothing, so it is honest about what has been
checked so far.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol
from urllib.parse import urlencode

from mechanical_librarian.application.review_web import (
    ReviewWebError,
    _report_authors,
    _report_facts,
)
from mechanical_librarian.domain.cover_qualification_policy import (
    taste_replacement_record,
)

# Unwritten switches revoked per wake (L3).
RETIRE_LIMIT = 50
# Verdicts tried per wake; each refused one is passed over until the scan
# reaches the end and, after `_IDLE_SECONDS`, starts again.
_ADMIT_BATCH = 16
_IDLE_SECONDS = 60

# The preview's bounds (S25): switches shown and covers checked.
PREVIEW_SWITCHES = 5
PREVIEW_CHECKED = 100
ReviewTastePreviewSchemaVersion = Literal["review-taste-preview-v1"]
REVIEW_TASTE_PREVIEW_SCHEMA: Final[ReviewTastePreviewSchemaVersion] = (
    "review-taste-preview-v1"
)
TasteProtection = Literal["protected", "unprotected"]
TASTE_PROTECTIONS: tuple[TasteProtection, ...] = ("protected", "unprotected")
TastePreviewAnswer = Literal["looks_right", "kept_current"]


@dataclass(frozen=True, slots=True)
class TastePreviewSwitch:
    """One switch the profile would make, as the walk found it."""

    review_case_id: str
    consideration_id: str
    report_document: str
    # The candidate's reference and preview, or no preview when it has none.
    reference: str
    preview_digest: str | None
    width: int | None
    height: int | None
    # The fault the switch cures; none for a preferred cover.
    reason: str | None
    # The cited rules, as (number, section, text) in the version in use.
    rules: tuple[tuple[int, str, str], ...]
    # The owner's answer since the verdict, if it is one the preview shows.
    answer: TastePreviewAnswer | None
    # Whether this comparison was chosen for under an earlier version.
    rerun: bool


@dataclass(frozen=True, slots=True)
class TastePreviewWalk:
    version: int | None
    checked: int
    switches: tuple[TastePreviewSwitch, ...]
    # Whether any field is still due a check under the version.
    due: bool


class CoverTasteSwitchingRepository(Protocol):
    def taste_switching(self) -> bool: ...

    def retire_switches(self, now: int, limit: int) -> int: ...

    def switch_candidates(
        self, after: tuple[int, int], limit: int
    ) -> tuple[tuple[str, str, int, int], ...]: ...

    def admit_switch(
        self, consideration_id: str, verdict_id: str, now: int
    ) -> bool: ...


class CoverTasteSwitching:
    """Retire, then admit, at most one action per wake."""

    def __init__(self, repository: Callable[[], CoverTasteSwitchingRepository]) -> None:
        self._repository = repository
        # The admission cursor: (created_at, rowid) of the last verdict tried.
        self._after = (-1, -1)
        self._idle_until = 0

    def wake(self, now: int) -> bool:
        """Return whether a switch was revoked or admitted."""
        if taste_replacement_record() is None:
            return False
        repository = self._repository()
        if repository.retire_switches(now, RETIRE_LIMIT):
            return True
        if not repository.taste_switching() or now < self._idle_until:
            return False
        candidates = repository.switch_candidates(self._after, _ADMIT_BATCH)
        for consideration_id, verdict_id, created_at, rowid in candidates:
            self._after = (created_at, rowid)
            if repository.admit_switch(consideration_id, verdict_id, now):
                return True
        if len(candidates) < _ADMIT_BATCH:
            self._after = (-1, -1)
            self._idle_until = now + _IDLE_SECONDS
        return False


def _preview_item(switch: TastePreviewSwitch) -> dict[str, object]:
    report = _report_facts(switch.report_document)
    target = report.target
    current = (
        None
        if report.cover is None
        else {
            "height": report.cover.height,
            "preview_sha256": report.cover.preview_digest,
            "url": "/api/v1/library/cover?"
            + urlencode(
                {
                    "connection": target.connection,
                    "library": target.library,
                    "item": target.item,
                    "preview": report.cover.preview_digest,
                }
            ),
            "width": report.cover.width,
        }
    )
    new = (
        None
        if switch.preview_digest is None
        or switch.width is None
        or switch.height is None
        else {
            "height": switch.height,
            "preview_sha256": switch.preview_digest,
            # The candidate image the work's case detail shows (K8).
            "url": f"/api/v1/review-cases/{switch.review_case_id}"
            f"/cover-considerations/{switch.consideration_id}"
            f"/images/{switch.reference}",
            "width": switch.width,
        }
    )
    return {
        "answer": switch.answer,
        "authors": _report_authors(target),
        "current": current,
        "media_kind": report.file.format,
        "new": new,
        "reason": switch.reason,
        "review_case_id": switch.review_case_id,
        "rules": [
            {"number": number, "section": section, "text": text}
            for number, section, text in switch.rules
        ],
        "title": target.current_title or "Untitled",
    }


def taste_preview(walk: TastePreviewWalk) -> dict[str, object]:
    """The preview document (K7): `done` once five switches are found, 100
    covers are checked or nothing under the version is still due; `rerun`
    when a found switch was chosen under an earlier version too."""
    if walk.version is None:
        raise ReviewWebError("unavailable")
    switches = walk.switches[:PREVIEW_SWITCHES]
    done = (
        len(switches) >= PREVIEW_SWITCHES
        or walk.checked >= PREVIEW_CHECKED
        or not walk.due
    )
    return {
        "checked": walk.checked,
        "found": [_preview_item(switch) for switch in switches],
        "rerun": any(switch.rerun for switch in switches),
        "schema_version": REVIEW_TASTE_PREVIEW_SCHEMA,
        "state": "done" if done else "checking",
        "version": walk.version,
    }


__all__ = [
    "PREVIEW_CHECKED",
    "PREVIEW_SWITCHES",
    "REVIEW_TASTE_PREVIEW_SCHEMA",
    "RETIRE_LIMIT",
    "TASTE_PROTECTIONS",
    "CoverTasteSwitching",
    "CoverTasteSwitchingRepository",
    "TastePreviewSwitch",
    "TastePreviewWalk",
    "taste_preview",
]
