from __future__ import annotations

import pytest

from mechanical_librarian.application.batch import BatchSelection, CatalogueSnapshot
from mechanical_librarian.application.batch_run import ForegroundBatchRun
from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
    CatalogueStaleError,
)
from mechanical_librarian.application.metadata_report import (
    CredentialError,
    CurrentnessError,
    ItemInspectionError,
    MetadataReportError,
    UnsupportedMediaError,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus


def entry(item: str = "item") -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "books",
        "library",
        item,
        1,
        "title",
        (),
        True,
        False,
        "singular-supported",
        "a" * 64,
        "book.epub",
        "epub",
        "books",
    )


def _exercise(report, current=lambda *_: entry()):
    lines: list[dict[str, object]] = []
    selected = BatchSelection(
        "primary", "books", "library", "item", "a" * 64, "singular-supported", True
    )
    result = ForegroundBatchRun(
        snapshot=CatalogueSnapshot(1, "b" * 64, (entry(),)),
        selections=(selected,),
        projection=lambda: "p",
        expected_projection="p",
        current_entry=current,
        report=report,
        emit=lines.append,
    ).run()
    return lines, result


def test_source_stale_continues() -> None:
    lines, result = _exercise(
        lambda _: (_ for _ in ()).throw(CurrentnessError("changed"))
    )
    assert (lines[0]["outcome"], lines[-1]["summary"]["skipped"], result) == (
        "stale",
        1,
        True,
    )


def test_credential_and_catalogue_drift_stop() -> None:
    lines, result = _exercise(
        lambda _: (_ for _ in ()).throw(CredentialError("unavailable"))
    )
    assert (lines[0]["outcome"], result) == ("stopped", False)
    lines, result = _exercise(
        lambda _: ("report", ()),
        current=lambda *_: (_ for _ in ()).throw(CatalogueStaleError("refresh")),
    )
    assert (lines[0]["outcome"], result) == ("stopped", False)


def test_item_local_failure_and_unclassified_stop() -> None:
    lines, result = _exercise(
        lambda _: (_ for _ in ()).throw(UnsupportedMediaError("format"))
    )
    assert (lines[0]["outcome"], lines[-1]["summary"]["failed"], result) == (
        "item_failed",
        1,
        True,
    )
    lines, result = _exercise(lambda _: (_ for _ in ()).throw(RuntimeError("broken")))
    assert (lines[0]["outcome"], result) == ("stopped", False)


@pytest.mark.parametrize(
    "status",
    (
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.INCOMPLETE,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.MALFORMED,
        ProviderStatus.OVERSIZED,
    ),
    ids=(
        "authentication",
        "authorization",
        "rate_limited",
        "incomplete",
        "unavailable",
        "malformed",
        "oversized",
    ),
)
def test_provider_stops_after_durable_item(status: ProviderStatus) -> None:
    first, second = entry(), entry("item-2")
    selections = (
        BatchSelection(
            "primary", "books", "library", "item", "a" * 64, "singular-supported", True
        ),
        BatchSelection(
            "primary",
            "books",
            "library",
            "item-2",
            "a" * 64,
            "singular-supported",
            True,
        ),
    )
    lines: list[dict[str, object]] = []
    calls = []
    result = ForegroundBatchRun(
        snapshot=CatalogueSnapshot(1, "b" * 64, (first, second)),
        selections=selections,
        projection=lambda: "p",
        expected_projection="p",
        current_entry=lambda *_: second if _[-1][-1] == "item-2" else first,
        report=lambda value: calls.append(value.item_id) or ("report", (status,)),
        emit=lines.append,
    ).run()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
    assert (
        calls,
        [line.get("outcome") for line in lines[:-1]],
        lines[-1]["summary"]["stopped"],
        result,
    ) == (["item"], ["completed", "stopped"], True, False)


def test_inspection_continues_and_generic_metadata_stops() -> None:
    selected = BatchSelection(
        "primary", "books", "library", "item", "a" * 64, "singular-supported", True
    )
    second = BatchSelection(
        "primary", "books", "library", "item-2", "a" * 64, "singular-supported", True
    )
    lines: list[dict[str, object]] = []
    calls = []
    result = ForegroundBatchRun(
        snapshot=CatalogueSnapshot(1, "b" * 64, (entry(), entry("item-2"))),
        selections=(selected, second),
        projection=lambda: "p",
        expected_projection="p",
        current_entry=lambda *_: entry("item-2") if _[-1][-1] == "item-2" else entry(),
        report=lambda value: (
            calls.append(value.item_id)
            or (
                (_ for _ in ()).throw(ItemInspectionError("bad"))
                if value.item_id == "item"
                else ("report", ())
            )
        ),
        emit=lines.append,
    ).run()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
    assert (calls, lines[0]["outcome"], lines[1]["outcome"], result) == (
        ["item", "item-2"],
        "item_failed",
        "completed",
        True,
    )
    lines = []
    calls = []
    result = ForegroundBatchRun(
        snapshot=CatalogueSnapshot(1, "b" * 64, (entry(), entry("item-2"))),
        selections=(selected, second),
        projection=lambda: "p",
        expected_projection="p",
        current_entry=lambda *_: entry("item-2") if _[-1][-1] == "item-2" else entry(),
        report=lambda value: (
            calls.append(value.item_id)
            or (_ for _ in ()).throw(MetadataReportError("publication"))
        ),
        emit=lines.append,
    ).run()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
    assert (
        calls,
        [line.get("outcome") for line in lines[:-1]],
        lines[-1]["summary"]["stopped"],
        result,
    ) == (["item"], ["stopped"], True, False)
