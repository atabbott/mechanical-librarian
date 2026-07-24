"""Serial foreground batch execution behind application-owned ports."""

from __future__ import annotations

from collections.abc import Callable

from mechanical_librarian.application.batch import BatchError, BatchSelection, CatalogueSnapshot
from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueStaleError
from mechanical_librarian.application.metadata_report import CredentialError, CurrentnessError, MetadataReportError, UnsupportedMediaError


class ForegroundBatchRun:
    """Own the closed serial loop; composition supplies the concrete authority."""

    def __init__(self, *, snapshot: CatalogueSnapshot, selections: tuple[BatchSelection, ...], projection: Callable[[], str], expected_projection: str, current_entry: Callable[[int, str, tuple[str, str, str, str]], CatalogueEntry | None], report: Callable[[CatalogueEntry], str], emit: Callable[[dict[str, object]], None]) -> None:
        self._snapshot = snapshot; self._selections = selections; self._projection = projection; self._expected_projection = expected_projection; self._current_entry = current_entry; self._report = report; self._emit = emit

    def run(self) -> dict[str, int]:
        completed = skipped = failed = 0
        for selection in self._selections:
            if self._projection() != self._expected_projection:
                self._emit({"item": selection.item, "outcome": "stopped"})
                return {"completed": completed, "skipped": skipped, "failed": failed}
            key = (selection.connection, selection.library, selection.library_id, selection.item)
            try:
                entry = self._current_entry(self._snapshot.generation, self._snapshot.fingerprint, key)
            except CatalogueStaleError:
                self._emit({"item": selection.item, "outcome": "stopped"})
                return {"completed": completed, "skipped": skipped, "failed": failed}
            if entry is None or entry.file_identity != selection.file_identity or entry.classification != selection.classification or entry.eligible != selection.eligible:
                skipped += 1; self._emit({"item": selection.item, "outcome": "stale"}); continue
            if not entry.eligible or entry.file_identity is None:
                skipped += 1; self._emit({"item": selection.item, "outcome": "ineligible"}); continue
            try:
                report = self._report(entry)
            except CurrentnessError:
                skipped += 1; self._emit({"item": selection.item, "outcome": "stale"}); continue
            except CredentialError:
                self._emit({"item": selection.item, "outcome": "stopped"}); return {"completed": completed, "skipped": skipped, "failed": failed}
            except (UnsupportedMediaError, MetadataReportError):
                failed += 1; self._emit({"item": selection.item, "outcome": "item_failed"}); continue
            except Exception:
                self._emit({"item": selection.item, "outcome": "stopped"}); return {"completed": completed, "skipped": skipped, "failed": failed}
            completed += 1; self._emit({"item": selection.item, "outcome": "completed", "report": report})
        return {"completed": completed, "skipped": skipped, "failed": failed}


__all__ = ["ForegroundBatchRun"]
