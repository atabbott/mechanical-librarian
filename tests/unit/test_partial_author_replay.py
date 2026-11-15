"""The offline partial-author replay (#1355) over a synthetic application store.

Every report is built here with the level tests' own builders and stored
through the real repository in ``tmp_path``; no snapshot data is used.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from scripts import partial_author_replay as replay_script

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    ProviderLaneResult,
    provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderStatus,
    canonical_json,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support import ebook_synopsis
from tests.unit import test_identity_refused_retention as retained
from tests.unit import test_level_proposal_sets as level_sets

_FIRST = retained.AUTHOR
_SECOND = "Quill Second"
_THIRD = "Tamsin Third"


def _record(
    identity: str,
    authors: tuple[str, ...],
    *,
    title: str = retained.TITLE,
    role: str | None = "author",
) -> ProviderCandidate:
    """A refused WORK answer stating one title and its creators.

    `role=None` gives neutral creators bound to no identity, so the record
    states no classifier-complete claim set.
    """
    specifications = (
        (ClaimKind.TITLE, title, None),
        *((ClaimKind.CREATOR, author, role) for author in authors),
    )
    return ProviderCandidate(
        identity,
        "unresolved",
        tuple(
            ProviderClaim.create(
                provider=retained.PROVIDER,
                candidate=identity,
                kind=kind,
                value=value,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role=role,
                raw_order=order,
            )
            for order, (kind, value, role) in enumerate(specifications)
        ),
    )


def _as_item(report: MetadataReport, item: str) -> MetadataReport:
    """The same report for another library item, so each is its own case."""
    data = json.loads(report.to_json())
    data["target"]["item"] = item
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return MetadataReport.from_json(canonical_json(data))


def _truncated() -> MetadataReport:
    """Six refused answers, so the observation keeps five and is truncated."""
    records = tuple(
        retained._candidate(f"volume-{index}", title="Another Book")
        for index in ("b", "c", "d", "e")
    ) + (retained._candidate("volume-a"), retained._candidate("volume-z"))
    report = level_sets._report()
    lane = ProviderLaneResult(
        retained._work_lane(),
        ProviderStatus.NO_MATCH,
        identity_refused_candidates=records,
    )
    observation = provider_observation(
        retained.PROVIDER, (lane,), authority=retained.PROVIDER
    )
    stored = MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=(observation,),
    )
    return MetadataReport.from_json(stored.to_json())


def _reports() -> dict[str, MetadataReport]:
    return {
        # The file names the first author; the source names both.
        "nested": level_sets._report(_record("nested-1", (_FIRST, _SECOND))),
        # The same, with neutral unbound creators: the classifier could not
        # contradict it, so the level admitted it before the amendment too.
        "nested_before": level_sets._report(
            _record("nested-2", (_FIRST, _SECOND), role=None)
        ),
        "overlap": level_sets._report(
            _record("overlap-1", (_FIRST, _THIRD)), authors=(_FIRST, _SECOND)
        ),
        # The same author, respelled so only the initials fold matches.
        "equal": level_sets._report(_record("equal-1", (retained.FOLDED,))),
        "truncated": _truncated(),
        "live": ebook_synopsis.synopsis_report(),
    }


def _store(path: Path, reports: dict[str, MetadataReport]) -> Path:
    repository = open_repository(path)
    try:
        for name, report in reports.items():
            stored = _as_item(report, f"item-{name}")
            repository.publish(stored, lambda: True)
            proposal = evaluate_report(stored)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
    finally:
        repository.close()
    return path


def _run(*argv: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = replay_script.main(list(argv), stdout, stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_replay_counts_each_relation_and_lists_the_nested_admissions(
    tmp_path: Path,
) -> None:
    database = _store(tmp_path / "application.sqlite3", _reports())
    before = _digest(database)
    detail = tmp_path / "detail.json"

    code, stdout, stderr = _run("--database", str(database), "--detail", str(detail))

    assert (code, stderr) == (0, "")
    assert _digest(database) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "application.sqlite3",
        "detail.json",
    ]
    summary = json.loads(stdout)
    assert summary["reports"] == {
        "read": 6,
        "replayed": 6,
        "not_replayed": {"schema_unsupported": 0, "replay_failed": 0},
    }
    refused = summary["records"]["refused"]
    assert refused["nested"] == {
        "admitted": 2,
        "refused": 0,
        "ambiguous": 0,
        "truncated": 0,
    }
    assert refused["overlap"] == {
        "admitted": 0,
        "refused": 1,
        "ambiguous": 0,
        "truncated": 0,
    }
    assert refused["same"] == {
        "admitted": 1,
        "refused": 0,
        "ambiguous": 0,
        "truncated": 5,
    }
    assert (
        refused["disjoint"]
        == refused["no_authors"]
        == dict.fromkeys(replay_script.VERDICTS, 0)
    )
    live = summary["records"]["live"]
    # The synopsis report's two live answers name the item's one author.
    assert live["same"] == {
        "admitted": 2,
        "refused": 0,
        "ambiguous": 0,
        "truncated": 0,
    }
    assert live["nested"] == live["overlap"] == dict.fromkeys(replay_script.VERDICTS, 0)
    assert summary["newly_admitted"] == {
        "live": {"records": 0, "books": 0, "live_identity_already_admits": 0},
        "refused": {"records": 1, "books": 1},
        "books": 1,
    }
    assert summary["nested_admitted_before"] == {
        "live": {"records": 0, "books": 0},
        "refused": {"records": 1, "books": 1},
        "books": 1,
    }
    assert summary["detail_written"] is True
    # The summary carries counts only: no title, author or item reaches stdout.
    for value in (_FIRST, _SECOND, _THIRD, retained.TITLE, "item-nested"):
        assert value not in stdout

    document = json.loads(detail.read_text(encoding="utf-8"))
    assert detail.stat().st_mode & 0o777 == 0o600
    rows = {row["record"]["identity"]: row for row in document["nested_admitted"]}
    assert set(rows) == {"nested-1", "nested-2"}
    assert (rows["nested-2"]["book"]["item"], rows["nested-2"]["new"]) == (
        "item-nested_before",
        False,
    )
    assert [rows["nested-1"]] == [
        {
            "book": {
                "library": "ebooks",
                "item": "item-nested",
                "title": retained.TITLE,
                "authors": [_FIRST],
            },
            "record": {
                "provider": retained.PROVIDER,
                "identity": "nested-1",
                "work_identity": "unresolved",
                "title": retained.TITLE,
                "authors": [_FIRST, _SECOND],
            },
            "source": "refused",
            "fewer_authors": "book",
            "verdict": "admitted",
            "creator_comparison": "closed",
            "new": True,
        }
    ]
    (overlap,) = document["overlap_refused"]
    assert (overlap["book"]["authors"], overlap["record"]["authors"]) == (
        [_FIRST, _SECOND],
        [_FIRST, _THIRD],
    )
    assert (overlap["source"], overlap["verdict"], overlap["fewer_authors"]) == (
        "refused",
        "refused",
        "neither",
    )


def test_the_detail_file_is_never_replaced(tmp_path: Path) -> None:
    database = _store(
        tmp_path / "application.sqlite3", {"nested": _reports()["nested"]}
    )
    detail = tmp_path / "detail.json"
    detail.write_text("kept", encoding="utf-8")

    code, stdout, stderr = _run("--database", str(database), "--detail", str(detail))

    assert (code, stdout) == (2, "")
    assert stderr == "partial_author_replay: detail path already exists\n"
    assert detail.read_text(encoding="utf-8") == "kept"


def test_without_detail_nothing_is_written(tmp_path: Path) -> None:
    database = _store(
        tmp_path / "application.sqlite3", {"nested": _reports()["nested"]}
    )
    code, stdout, _stderr = _run("--database", str(database))
    assert code == 0
    assert json.loads(stdout)["detail_written"] is False
    assert [path.name for path in tmp_path.iterdir()] == ["application.sqlite3"]


@pytest.mark.parametrize(
    ("name", "message"),
    (
        ("missing.sqlite3", "database not found"),
        ("other.sqlite3", "not an application database"),
        ("text.sqlite3", "database unreadable"),
    ),
    ids=("missing", "not_application", "not_sqlite"),
)
def test_a_missing_or_foreign_database_is_refused(
    tmp_path: Path, name: str, message: str
) -> None:
    sqlite3.connect(tmp_path / "other.sqlite3").execute(
        "CREATE TABLE unrelated(value TEXT)"
    ).connection.close()
    (tmp_path / "text.sqlite3").write_text("not a database " * 100, encoding="utf-8")
    code, stdout, stderr = _run("--database", str(tmp_path / name))
    assert (code, stdout) == (2, "")
    assert stderr == f"partial_author_replay: {message}\n"


@pytest.mark.parametrize(
    ("text", "reason"),
    (
        ('{"schema_version": "metadata-report-v80"}', "schema_unsupported"),
        ("[]", "schema_unsupported"),
        (f'{{"schema_version": "{replay_script.REPORT_SCHEMA}"}}', "replay_failed"),
        ("not json", "replay_failed"),
    ),
    ids=("previous_schema", "not_an_object", "unparseable_current", "not_json"),
)
def test_a_report_off_the_current_schema_is_not_replayed(
    text: str, reason: str
) -> None:
    replay = replay_script.Replay()
    replay_script.replay_document(text, replay)
    assert (replay.read, replay.replayed) == (1, 0)
    assert dict(replay.not_replayed) == {reason: 1}
    assert not replay.records


def test_the_help_says_what_it_does(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        replay_script.main(["--help"])
    assert raised.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "without changing it" in text
    assert "Nothing is sent anywhere" in text


def test_one_report_gives_one_verdict_per_record() -> None:
    (verdict,) = replay_script.replay_records(_reports()["nested"])
    assert (verdict.source, verdict.relation, verdict.verdict) == (
        "refused",
        "nested",
        "admitted",
    )
    assert (verdict.already_admitted, verdict.new) == (False, True)


def test_a_nested_record_the_classifier_could_not_contradict_is_not_new() -> None:
    """Neutral unbound creators: admitted today and before the amendment."""
    (verdict,) = replay_script.replay_records(_reports()["nested_before"])
    assert (verdict.source, verdict.relation, verdict.verdict, verdict.new) == (
        "refused",
        "nested",
        "admitted",
        False,
    )
    assert verdict.detail["new"] is False
