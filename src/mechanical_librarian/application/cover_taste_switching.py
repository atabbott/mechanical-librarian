"""Switch covers by taste: lane 1's switching steps (#1372 PR 5, K4, K6).

Each wake first revokes unwritten taste switches nothing stands behind any
more, then tries to admit `choose` verdicts as automatic decisions, at most
one admitted per wake. Nothing runs before the taste identity has its
record, so until #1418 records a qualification every verdict stays a
suggestion.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.domain.cover_qualification_policy import (
    taste_replacement_record,
)

# Unwritten switches revoked per wake (L3).
RETIRE_LIMIT = 50
# Verdicts tried per wake; each refused one is passed over until the scan
# reaches the end and, after `_IDLE_SECONDS`, starts again.
_ADMIT_BATCH = 16
_IDLE_SECONDS = 60


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


__all__ = ["CoverTasteSwitching", "CoverTasteSwitchingRepository", "RETIRE_LIMIT"]
