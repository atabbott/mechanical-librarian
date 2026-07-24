"""Serial foreground batch execution behind application-owned ports."""

from __future__ import annotations

from collections.abc import Callable

from mechanical_librarian.application.batch import BatchError, BatchSelection, CatalogueSnapshot
from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueStaleError
from mechanical_librarian.application.metadata_report import CredentialError, CurrentnessError, ItemInspectionError, MetadataReportError, UnsupportedMediaError
from mechanical_librarian.domain.metadata_report import ProviderStatus


class ForegroundBatchRun:
    """Own the closed serial loop; composition supplies the concrete authority."""

    def __init__(self, *, snapshot: CatalogueSnapshot, selections: tuple[BatchSelection, ...], projection: Callable[[], str], expected_projection: str, current_entry: Callable[[int, str, tuple[str, str, str, str]], CatalogueEntry | None], report: Callable[[CatalogueEntry], tuple[str, tuple[ProviderStatus, ...]]], emit: Callable[[dict[str, object]], None]) -> None:
        self._snapshot = snapshot; self._selections = selections; self._projection = projection; self._expected_projection = expected_projection; self._current_entry = current_entry; self._report = report; self._emit = emit

    def run(self) -> bool:
        completed = skipped = failed = 0
        for selection in self._selections:
            if self._projection() != self._expected_projection:
                self._emit({"item": selection.item, "outcome": "stopped"})
                return self._finish(completed, skipped, failed, True)
            key = (selection.connection, selection.library, selection.library_id, selection.item)
            try:
                entry = self._current_entry(self._snapshot.generation, self._snapshot.fingerprint, key)
            except CatalogueStaleError:
                self._emit({"item": selection.item, "outcome": "stopped"})
                return self._finish(completed, skipped, failed, True)
            if entry is None or entry.file_identity != selection.file_identity or entry.classification != selection.classification or entry.eligible != selection.eligible:
                skipped += 1; self._emit({"item": selection.item, "outcome": "stale"}); continue
            if not entry.eligible or entry.file_identity is None:
                skipped += 1; self._emit({"item": selection.item, "outcome": "ineligible"}); continue
            try:
                report = self._report(entry)
            except CurrentnessError:
                skipped += 1; self._emit({"item": selection.item, "outcome": "stale"}); continue
            except CredentialError:
                self._emit({"item": selection.item, "outcome": "stopped"}); return self._finish(completed, skipped, failed, True)
            except (UnsupportedMediaError, ItemInspectionError):
                failed += 1; self._emit({"item": selection.item, "outcome": "item_failed"}); continue
            except MetadataReportError:
                self._emit({"item": selection.item, "outcome": "stopped"}); return self._finish(completed, skipped, failed, True)
            except Exception:
                self._emit({"item": selection.item, "outcome": "stopped"}); return self._finish(completed, skipped, failed, True)
            report_id, statuses = report
            completed += 1; self._emit({"item": selection.item, "outcome": "completed", "report": report_id})
            if any(status in {ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION, ProviderStatus.RATE_LIMITED, ProviderStatus.UNAVAILABLE} for status in statuses):
                self._emit({"item": selection.item, "outcome": "stopped"}); return self._finish(completed, skipped, failed, True)
        return self._finish(completed, skipped, failed, False)

    def _finish(self, completed: int, skipped: int, failed: int, stopped: bool) -> bool:
        self._emit({"summary": {"completed": completed, "skipped": skipped, "failed": failed, "entries": len(self._selections), "stopped": stopped}})
        return not stopped


__all__ = ["ForegroundBatchRun"]
