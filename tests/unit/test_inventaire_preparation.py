"""Concurrent preparation callbacks retain the fixture's scoped fetch binding."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters import cover_evidence_observer
from mechanical_librarian.domain.cover_evidence import CoverSourceState
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support import inventaire_preparation
from tests.support.review_cases import _report


def test_concurrent_retrievals_cannot_restore_each_others_fetch_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = inventaire_preparation.InventairePreparationFixture(
        CoverSourceState.RETRIEVED, tmp_path
    )
    first_entered = threading.Event()
    second_attempted = threading.Event()
    lock = fixture._cover_fetch_lock
    phases: list[str] = []
    failures: list[BaseException] = []
    reports: list[MetadataReport] = []

    class ObservedLock:
        def __enter__(self) -> None:
            if threading.current_thread().name == "second":
                second_attempted.set()
            lock.acquire()

        def __exit__(self, *_: object) -> None:
            lock.release()

    @contextmanager
    def observed_patch(*args, **kwargs):
        assert lock.locked()
        with patch(*args, **kwargs) as fetch:
            phases.append("enter")
            if threading.current_thread().name == "first":
                first_entered.set()
                assert second_attempted.wait(5)
                assert cover_evidence_observer.fetch_remote_cover is fetch
                assert phases == ["enter"]
            yield fetch
        phases.append("exit")
        assert lock.locked()

    def no_external_fetch(*_args, **_kwargs):
        raise AssertionError("synthetic retrieval escaped its fetch patch")

    monkeypatch.setattr(fixture, "_cover_fetch_lock", ObservedLock())
    monkeypatch.setattr(inventaire_preparation, "patch", observed_patch)
    monkeypatch.setattr(
        cover_evidence_observer, "fetch_remote_cover", no_external_fetch
    )

    def retrieve(item: str) -> None:
        seed = _report()
        seed = MetadataReport.create(
            target=replace(seed.target, item=item),
            file=seed.file,
            local_claims=seed.local_claims,
            local_reasons=seed.local_reasons,
            providers=(),
        )
        try:
            reports.append(fixture(seed))
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=retrieve, args=("history-0000",), name="first")
    second = threading.Thread(target=retrieve, args=("science-0000",), name="second")
    first.start()
    assert first_entered.wait(5)
    second.start()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert len(reports) == 2
    assert all(
        report.cover_sources[0].state is CoverSourceState.RETRIEVED
        for report in reports
    )
    assert phases == ["enter", "exit", "enter", "exit"]
    assert cover_evidence_observer.fetch_remote_cover is no_external_fetch
