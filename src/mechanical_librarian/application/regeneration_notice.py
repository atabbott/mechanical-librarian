"""The upgrade notice's state after `regenerate-derived` (ADR-0108; contract S14).

Activity and Attention show one notice per regeneration until the owner
dismisses it. This module names what the notice reads and the one write it
makes; the store reads it from the ledger, and C3 (#2039) words it and
places it. Nothing here is owner-visible text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mechanical_librarian.application.metadata_report import MetadataReportError


class RegenerationNoticeDismissalInvalid(MetadataReportError):
    """The dismissal names no finished regeneration, or has bad arguments;
    a storage failure stays a plain `MetadataReportError`."""

    def __init__(self) -> None:
        super().__init__("regeneration notice dismissal is invalid")


@dataclass(frozen=True, slots=True)
class RegenerationNotice:
    """The latest regeneration and what its first new cases did with the
    owner's answers held from before it.

    `kept` counts answers carried at their book's first new case;
    `asked_again` counts those asked again there (`question_changed`,
    `target_changed`, `asked_again` or `invalid`) on a field with decision
    actions; `not_yet_checked` counts answers the record still holds from
    before the regeneration that no new case has checked yet, a Resume or a
    plan still in flight included. Pins and covers are never checked, so
    they are in no count.
    """

    regeneration_id: int
    derived_epoch: int
    finished_at: int
    kept: int
    asked_again: int
    not_yet_checked: int
    dismissed: bool


class RegenerationNoticeStore(Protocol):
    def read_regeneration_notice(self) -> RegenerationNotice | None:
        """The latest regeneration's notice, or None before the first."""
        ...

    def dismiss_regeneration_notice(
        self, regeneration_id: int, dismissed_at: int
    ) -> bool:
        """Dismiss that regeneration's notice, once; True when this call did.
        A regeneration that is unknown, or not finished at `dismissed_at`, is
        refused with `RegenerationNoticeDismissalInvalid`."""
        ...


__all__ = [
    "RegenerationNotice",
    "RegenerationNoticeDismissalInvalid",
    "RegenerationNoticeStore",
]
