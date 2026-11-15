"""The rebuild checks run every read-only check over one folder of copies.

The checks run in-process through their own entry points, so the first test
uses a real application store with settings saved in the app; the others
replace single entry points to pin mode picking, failure isolation, the
partial-author replay seam and the refusals before anything is created.
"""

from __future__ import annotations

import io
import json
import socket
import stat
import types
from pathlib import Path
from typing import NoReturn, TextIO, cast

import pytest
import scripts.rebuild_checks as rebuild_checks
import scripts.work_route_measurement as work_route

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.review_cases import _report as _plain_report
from tests.unit.test_cold_start_report import _STORE_SECRETS, _write_bootstrap
from tests.unit.test_owner_settings_store import _seed_full_store

_SYNTHETIC_CONFIG = (
    Path(__file__).resolve().parents[2] / "docs" / "examples" / "synthetic-config.json"
)
_SKIPPED = (
    "Skipped partial-author-replay.json: this checkout has no "
    "scripts/partial_author_replay.py."
)
_NO_SETTINGS = "the --copies folder has neither config.json nor review-bootstrap.json."


def _store_copies(copies: Path) -> None:
    copies.mkdir(mode=0o700)
    repository, _ = _seed_full_store(copies / "application.sqlite3")
    store = cast(SQLiteMetadataReportRepository, repository)
    work = _plain_report("Runner work", "5", "6", actionable=False)
    store.publish(work, lambda: True)
    proposal = evaluate_report(work)
    store.publish_proposal(proposal)
    store.admit_review_case(proposal)
    store.close()
    _write_bootstrap(copies / "review-bootstrap.json")
    SQLiteProviderRecoveryStore.open(copies / "provider-recovery.sqlite").close()


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def _no_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rebuild_checks, "_partial_author_replay", lambda: None)


def _run(arguments: list[str]) -> tuple[int, str]:
    output = io.StringIO()
    code = rebuild_checks.main(arguments, stdout=output)
    return code, output.getvalue()


def test_rebuild_checks_writes_every_result_for_saved_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copies = tmp_path / "copies"
    _store_copies(copies)
    mount_root = tmp_path / "books"
    mount_root.mkdir()
    (mount_root / "one.epub").write_bytes(b"a")
    before = _snapshot(copies)
    results = tmp_path / "results"
    _no_replay(monkeypatch)

    def no_network(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("a check opened a socket")

    monkeypatch.setattr(socket, "socket", no_network)

    code, printed = _run(
        [
            "--copies",
            str(copies),
            "--output",
            str(results),
            "--mount-root",
            f"books={mount_root}",
        ]
    )

    summary = (
        "Wrote cold-start-report.json.\n"
        "Wrote work-route-measurement.json.\n"
        "Wrote work-route-provenance.json.\n"
        "Wrote hardcover-series-sample.txt.\n"
        f"{_SKIPPED}\n"
        f"Every check finished. The results are in {results}.\n"
    )
    assert code == 0
    assert printed == summary
    assert _snapshot(copies) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "books",
        "copies",
        "results",
    ]
    assert stat.S_IMODE(results.stat().st_mode) == 0o700
    assert sorted(path.name for path in results.iterdir()) == [
        "cold-start-report.json",
        "hardcover-series-sample.txt",
        "summary.txt",
        "work-route-measurement.json",
        "work-route-provenance.json",
    ]
    for path in results.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        text = path.read_text(encoding="utf-8")
        assert "Runner work" not in text
        for secret in _STORE_SECRETS:
            assert secret not in text
    assert (results / "summary.txt").read_text(encoding="utf-8") == summary
    report = json.loads((results / "cold-start-report.json").read_text())
    assert report["totals"]["works"] == 1
    (library,) = report["libraries"]
    assert library["scan_fidelity"]["mount_files_by_extension"] == {"epub": 1}
    assert report["recovery_jobs"] == {"jobs": 0, "groups": []}
    measurement = json.loads((results / "work-route-measurement.json").read_text())
    assert measurement["schema"] == "work-route-measurement-v1"
    provenance = json.loads((results / "work-route-provenance.json").read_text())
    assert provenance[work_route.PROVENANCE_SECTION]["schema"] == (
        "work-route-measurement-v1"
    )
    assert (results / "hardcover-series-sample.txt").read_text() == (
        "No ebook has a series that only Hardcover disputes.\n"
    )


def _recording(
    calls: dict[str, list[list[str]]], name: str, text: str = "{}\n"
) -> rebuild_checks._Main:
    def main(argv: list[str], stdout: TextIO) -> int:
        calls.setdefault(name, []).append(argv)
        stdout.write(text)
        return 0

    return main


def _record_checks(
    monkeypatch: pytest.MonkeyPatch, calls: dict[str, list[list[str]]]
) -> None:
    for module, name in (
        (rebuild_checks.cold_start_report, "cold_start"),
        (rebuild_checks.work_route_measurement, "work_route"),
        (rebuild_checks.hardcover_series_sample, "hardcover"),
    ):
        monkeypatch.setattr(module, "main", _recording(calls, name))


@pytest.mark.parametrize("mode", ("config", "bootstrap"))
def test_checks_receive_the_install_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    copies = tmp_path / "copies"
    copies.mkdir(mode=0o700)
    application = copies / "application.sqlite3"
    application.write_bytes(b"")
    if mode == "config":
        source = json.loads(_SYNTHETIC_CONFIG.read_text(encoding="utf-8"))
        source["review"] = {
            "canonical_origin": "http://review.test",
            "owner_password_hash_file": "/synthetic/owner.hash",
        }
        (copies / "config.json").write_text(json.dumps(source), encoding="utf-8")
        # config.json decides even beside a bootstrap copy.
        _write_bootstrap(copies / "review-bootstrap.json")
        settings = ["--config", str(copies / "config.json")]
        origin = "http://review.test"
    else:
        _write_bootstrap(copies / "review-bootstrap.json")
        (copies / "provider-recovery.sqlite").write_bytes(b"")
        settings = ["--bootstrap", str(copies / "review-bootstrap.json")]
        origin = "http://localhost:8080"
    calls: dict[str, list[list[str]]] = {}
    _record_checks(monkeypatch, calls)
    _no_replay(monkeypatch)

    code, _ = _run(
        [
            "--copies",
            str(copies),
            "--output",
            str(tmp_path / "results"),
            "--mount-root",
            "books=/host/books",
            "--mount-root",
            "audio=/host/audio",
            "--limit",
            "7",
        ]
    )

    assert code == 0
    recovery = (
        ["--recovery-database", str(copies / "provider-recovery.sqlite")]
        if mode == "bootstrap"
        else []
    )
    assert calls == {
        "cold_start": [
            [
                "--database",
                str(application),
                "--catalogue",
                str(copies / "catalogue.sqlite3"),
                *settings,
                *recovery,
                "--mount-root",
                "books=/host/books",
                "--mount-root",
                "audio=/host/audio",
            ]
        ],
        "work_route": [
            ["--database", str(application)],
            ["--database", str(application), "--provenance"],
        ],
        "hardcover": [
            ["--database", str(application), "--origin", origin, "--limit", "7"]
        ],
    }


def test_a_failing_check_does_not_stop_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copies = tmp_path / "copies"
    _store_copies(copies)
    results = tmp_path / "results"
    _no_replay(monkeypatch)

    def broken(_argv: list[str], _stdout: TextIO) -> int:
        raise RuntimeError("synthetic failure with target-secret-1234")

    monkeypatch.setattr(rebuild_checks.work_route_measurement, "main", broken)

    code, printed = _run(["--copies", str(copies), "--output", str(results)])

    assert code == 1
    assert printed == (
        "Wrote cold-start-report.json.\n"
        "Did not write work-route-measurement.json: it stopped with "
        "RuntimeError. Run scripts/work_route_measurement.py on its own to see "
        "why.\n"
        "Did not write work-route-provenance.json: it stopped with "
        "RuntimeError. Run scripts/work_route_measurement.py on its own to see "
        "why.\n"
        "Wrote hardcover-series-sample.txt.\n"
        f"{_SKIPPED}\n"
        f"2 of 4 checks did not finish. The results that were written are in "
        f"{results}.\n"
    )
    assert sorted(path.name for path in results.iterdir()) == [
        "cold-start-report.json",
        "hardcover-series-sample.txt",
        "summary.txt",
    ]
    assert (results / "summary.txt").read_text(encoding="utf-8") == printed


def test_an_argument_error_is_reported_as_its_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    copies = tmp_path / "copies"
    _store_copies(copies)
    results = tmp_path / "results"
    _no_replay(monkeypatch)

    code, printed = _run(
        ["--copies", str(copies), "--output", str(results), "--limit", "0"]
    )

    assert code == 1
    assert (
        "Did not write hardcover-series-sample.txt: argument --limit: give a "
        "whole number from 1 to 500\n"
    ) in printed
    assert printed.endswith(
        f"1 of 4 checks did not finish. The results that were written are in "
        f"{results}.\n"
    )
    assert not (results / "hardcover-series-sample.txt").exists()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("outcome", "line"),
    (
        (SystemExit("A fixed refusal."), "Did not write x.json: A fixed refusal."),
        (SystemExit(2), "Did not write x.json: it stopped with exit code 2."),
        (3, "Did not write x.json: it stopped with exit code 3."),
        (
            KeyError("secret"),
            "Did not write x.json: it stopped with KeyError. Run "
            "scripts/sample.py on its own to see why.",
        ),
    ),
)
def test_each_failure_kind_is_reported_without_its_detail(
    outcome: BaseException | int, line: str
) -> None:
    def main(_argv: list[str], stdout: TextIO) -> int:
        stdout.write("partial")
        if isinstance(outcome, int):
            return outcome
        raise outcome

    assert rebuild_checks._run(main, [], "x.json", "sample") == ("", line)


def test_a_result_that_cannot_be_saved_is_reported(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text("before", encoding="utf-8")

    line = rebuild_checks._save(tmp_path, "x.json", "after")

    assert line == "Did not write x.json: saving it stopped with FileExistsError."
    assert (tmp_path / "x.json").read_text(encoding="utf-8") == "before"


def test_a_copies_folder_without_settings_still_runs_the_work_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copies = tmp_path / "copies"
    _store_copies(copies)
    (copies / "review-bootstrap.json").unlink()
    results = tmp_path / "results"
    _no_replay(monkeypatch)

    code, printed = _run(["--copies", str(copies), "--output", str(results)])

    assert code == 1
    assert printed == (
        f"Did not write cold-start-report.json: {_NO_SETTINGS}\n"
        "Wrote work-route-measurement.json.\n"
        "Wrote work-route-provenance.json.\n"
        f"Did not write hardcover-series-sample.txt: {_NO_SETTINGS}\n"
        f"{_SKIPPED}\n"
        f"2 of 4 checks did not finish. The results that were written are in "
        f"{results}.\n"
    )


def test_an_unreadable_bootstrap_stops_only_the_checks_that_need_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copies = tmp_path / "copies"
    _store_copies(copies)
    (copies / "review-bootstrap.json").write_text("{", encoding="utf-8")
    results = tmp_path / "results"
    _no_replay(monkeypatch)

    code, printed = _run(["--copies", str(copies), "--output", str(results)])

    assert code == 1
    assert printed == (
        "Did not write cold-start-report.json: This checkout cannot read the "
        "--bootstrap file. Give it a copy of the review-bootstrap.json this "
        "install starts with.\n"
        "Wrote work-route-measurement.json.\n"
        "Wrote work-route-provenance.json.\n"
        "Did not write hardcover-series-sample.txt: this checkout cannot read "
        "the address in review-bootstrap.json.\n"
        f"{_SKIPPED}\n"
        f"2 of 4 checks did not finish. The results that were written are in "
        f"{results}.\n"
    )


def test_partial_author_replay_runs_when_present_and_is_skipped_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This checkout has no replay script, so the real seam finds none.
    assert rebuild_checks._partial_author_replay() is None

    calls: dict[str, list[list[str]]] = {}
    _record_checks(monkeypatch, calls)
    replay = types.ModuleType("partial_author_replay")
    replay.main = _recording(calls, "replay", '{"replay": 1}\n')
    monkeypatch.setattr(rebuild_checks, "_partial_author_replay", lambda: replay)
    copies = tmp_path / "copies"
    copies.mkdir(mode=0o700)
    (copies / "application.sqlite3").write_bytes(b"")
    _write_bootstrap(copies / "review-bootstrap.json")
    results = tmp_path / "results"

    code, printed = _run(["--copies", str(copies), "--output", str(results)])

    assert code == 0
    assert calls["replay"] == [["--database", str(copies / "application.sqlite3")]]
    assert printed.splitlines()[-2:] == [
        "Wrote partial-author-replay.json.",
        f"Every check finished. The results are in {results}.",
    ]
    assert (results / "partial-author-replay.json").read_text() == '{"replay": 1}\n'

    def broken_import() -> NoReturn:
        raise ImportError("synthetic")

    monkeypatch.setattr(rebuild_checks, "_partial_author_replay", broken_import)
    code, printed = _run(["--copies", str(copies), "--output", str(tmp_path / "again")])

    assert code == 1
    assert printed.splitlines()[-2:] == [
        "Did not write partial-author-replay.json: it stopped with ImportError. "
        "Run scripts/partial_author_replay.py on its own to see why.",
        "1 of 5 checks did not finish. The results that were written are in "
        f"{tmp_path / 'again'}.",
    ]


def test_the_replay_seam_raises_when_a_module_it_needs_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependency(name: str) -> NoReturn:
        raise ModuleNotFoundError("synthetic", name="some_dependency")

    monkeypatch.setattr(rebuild_checks.importlib, "import_module", missing_dependency)
    with pytest.raises(ModuleNotFoundError):
        rebuild_checks._partial_author_replay()


def test_an_existing_output_folder_is_refused(tmp_path: Path) -> None:
    copies = tmp_path / "copies"
    copies.mkdir(mode=0o700)
    open_repository(copies / "application.sqlite3").close()
    results = tmp_path / "results"
    results.mkdir()
    (results / "summary.txt").write_text("earlier", encoding="utf-8")

    with pytest.raises(
        SystemExit,
        match=(
            r"^The --output folder already exists\. Give a new folder name for "
            r"this run\.$"
        ),
    ):
        _run(["--copies", str(copies), "--output", str(results)])
    assert (results / "summary.txt").read_text(encoding="utf-8") == "earlier"

    with pytest.raises(
        SystemExit,
        match=(
            r"^Cannot create the --output folder\. Give a path inside a folder "
            r"you own\.$"
        ),
    ):
        _run(["--copies", str(copies), "--output", str(tmp_path / "no" / "out")])
    assert not (tmp_path / "no").exists()


@pytest.mark.parametrize(
    ("layout", "message"),
    (
        (
            "missing",
            r"^No folder at the --copies path\. Give it the folder that holds "
            r"your copies\.$",
        ),
        (
            "empty",
            r"^The --copies folder has no application\.sqlite3\. Copy it there "
            r"and run again\.$",
        ),
    ),
)
def test_a_copies_folder_it_cannot_use_is_refused_before_anything_is_created(
    tmp_path: Path, layout: str, message: str
) -> None:
    copies = tmp_path / "copies"
    if layout == "empty":
        copies.mkdir(mode=0o700)
    results = tmp_path / "results"

    with pytest.raises(SystemExit, match=message):
        _run(["--copies", str(copies), "--output", str(results)])
    assert not results.exists()
