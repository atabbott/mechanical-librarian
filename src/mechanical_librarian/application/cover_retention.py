"""Keep the original an automatic cover switch replaces (#1363, ADR-0093).

Before an automatic plan replaces a populated cover, the original is bound to
the plan and kept in the owner-private retained store, so every automatic
switch can be put back. The kept bytes are the plan's own recovery copy and
must hash to the plan's pre-value; anything else refuses the write. A plan that
ends unverified owes the removal of its copy, which ``run_removals`` retries.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.domain.field_effect import FieldEffectPlan


class CoverRetentionError(RuntimeError):
    """The original could not be kept, so the plan may not write."""


class RetainedCoverFiles(Protocol):
    def keep(self, digest: str, original: bytes) -> None: ...
    def remove(self, digest: str) -> None: ...


class RetainedCoverRecords(Protocol):
    def record_retained_cover(
        self, plan_id: str, original_digest: str, byte_count: int, retained_at: int
    ) -> None: ...
    def pending_retained_cover_removals(
        self, limit: int
    ) -> tuple[tuple[str, str, bool], ...]: ...
    def complete_retained_cover_removal(
        self, plan_id: str, changed_at: int
    ) -> None: ...
    def fail_retained_cover_removal(self, plan_id: str, changed_at: int) -> None: ...


def retention_required(plan: FieldEffectPlan) -> bool:
    """Whether a plan is an automatic write replacing a populated cover."""
    return (
        plan.destination == "cover"
        and plan.authority.origin == "automatic_policy"
        and plan.pre_value is not None
    )


class CoverRetention:
    def __init__(
        self,
        files: RetainedCoverFiles,
        records: RetainedCoverRecords,
        read_recovery: Callable[[str], bytes | None],
        clock: Callable[[], int],
    ) -> None:
        self._files = files
        self._records = records
        self._read_recovery = read_recovery
        self._clock = clock
        # One retained store serves every library's application, and a removal
        # must never delete a file that a concurrent retention just bound.
        self._lock = threading.Lock()

    def retain(self, plan: FieldEffectPlan) -> None:
        """Bind and keep the plan's captured original, or refuse the write."""
        if not retention_required(plan):
            raise CoverRetentionError("retention does not apply")
        try:
            original = self._read_recovery(plan.plan_id)
            if original is None:
                raise CoverRetentionError("no original was captured")
            digest = hashlib.sha256(original).hexdigest()
            if "cover_" + digest != plan.pre_value:
                raise CoverRetentionError("the original does not match the plan")
            with self._lock:
                # The binding comes first, so kept bytes are never unbound: a
                # refusal after it stales the plan, which obliges the removal.
                self._records.record_retained_cover(
                    plan.plan_id, digest, len(original), self._clock()
                )
                self._files.keep(digest, original)
        except CoverRetentionError:
            raise
        except Exception as error:
            # The seams declare no error type; any fault means nothing was kept.
            raise CoverRetentionError("the original could not be kept") from error

    def run_removals(self, *, limit: int = 100) -> int:
        """Remove the originals of plans that ended unverified; count successes."""
        completed = 0
        with self._lock:
            for (
                plan_id,
                digest,
                shared,
            ) in self._records.pending_retained_cover_removals(limit):
                try:
                    # Another binding of the same bytes still needs the file.
                    if not shared:
                        self._files.remove(digest)
                except Exception:
                    # Any removal fault keeps the obligation pending with one
                    # more attempt, so restart recovery retries it.
                    self._records.fail_retained_cover_removal(plan_id, self._clock())
                    continue
                self._records.complete_retained_cover_removal(plan_id, self._clock())
                completed += 1
        return completed


__all__ = [
    "CoverRetention",
    "CoverRetentionError",
    "RetainedCoverFiles",
    "RetainedCoverRecords",
    "retention_required",
]
