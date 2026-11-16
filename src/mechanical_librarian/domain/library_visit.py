"""The owner's Library visit: which works count as new, and when that moves.

One mark for the owner, on the server, so every device agrees (#1351,
ADR-0103). A position is how far Library's listing had got: the last row of
the listed works, of the works that could not be read, and of the reports. A
work is new while it was first listed after the `seen` positions. `shown` is
the furthest listing a device has opened in the New lens; it becomes `seen`
at the first use of Library after a gap, so what one visit showed stops being
new at the next. Nothing here keeps a history: one time, overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Library unused for this long ends a visit, on any device.
VISIT_GAP_SECONDS: Final = 1_800


class LibraryVisitValueError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ListingPositions:
    """How far Library's listing had got, as the last row of each table."""

    packages: int
    works: int
    reports: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.packages, self.works, self.reports)
        ):
            raise LibraryVisitValueError("library listing positions are invalid")

    def furthest(self, other: ListingPositions) -> ListingPositions:
        return ListingPositions(
            max(self.packages, other.packages),
            max(self.works, other.works),
            max(self.reports, other.reports),
        )

    def clamped(self, end: ListingPositions) -> ListingPositions:
        return ListingPositions(
            min(self.packages, end.packages),
            min(self.works, end.works),
            min(self.reports, end.reports),
        )


@dataclass(frozen=True, slots=True)
class LibraryVisit:
    seen: ListingPositions
    shown: ListingPositions
    active_at: int

    def __post_init__(self) -> None:
        if (
            type(self.seen) is not ListingPositions
            or type(self.shown) is not ListingPositions
            or self.shown.furthest(self.seen) != self.shown
            or type(self.active_at) is not int
            or self.active_at < 0
        ):
            raise LibraryVisitValueError("library visit is invalid")


def record_library_use(
    visit: LibraryVisit | None,
    *,
    end: ListingPositions,
    listing: ListingPositions | None,
    now: int,
    baseline_ready: bool,
) -> LibraryVisit | None:
    """The visit after one recorded use of Library, or None to record nothing.

    `end` is the listing's current end and `listing` what a device showed in
    the New lens, if it showed one. With no visit yet the first mark is the
    current end, whatever was shown, and only once `baseline_ready`: before
    the first preparation has finished nothing is new. Otherwise a use after
    the gap starts the next visit, in which what the last one showed is seen;
    `shown` only rises, and never past the current end. A clock set back
    reads as no gap.
    """
    if type(now) is not int or now < 0 or type(baseline_ready) is not bool:
        raise LibraryVisitValueError("library visit use is invalid")
    if visit is None:
        return LibraryVisit(end, end, now) if baseline_ready else None
    seen = visit.shown if now - visit.active_at >= VISIT_GAP_SECONDS else visit.seen
    shown = (
        visit.shown if listing is None else visit.shown.furthest(listing.clamped(end))
    )
    return LibraryVisit(seen, shown, now)


__all__ = [
    "VISIT_GAP_SECONDS",
    "LibraryVisit",
    "LibraryVisitValueError",
    "ListingPositions",
    "record_library_use",
]
