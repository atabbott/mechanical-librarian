"""Replay partial author lists over one application database (#1355).

A workbench instrument, not part of the installed product. At the Book
matching level ``title_and_creator``, a provider record whose author list
contains the book's stored authors, or is contained in them, now counts
(ADR-0096's 2026-11-15 amendment); lists that overlap while each names an
author the other lacks are refused. This script measures what that change
does to a real library, using THIS checkout's code.

It opens the database read-only, writes nothing to it and contacts no
source. For every current case it reads the stored metadata report with the
current report parser; a report on any other schema is counted as not
replayed, never upgraded or guessed at. For every provider record in each
report, the live answers (``candidates``) and the refused answers retained
for the levels (``identity_refused_candidates``), it asks the level seam
(``provider_candidate_level_admission`` at ``title_and_creator``) for its
verdict, and relates the book's stored authors to the record's work authors
(``creator_list_relation`` with the dotted-initials fold) from the inputs the
seam itself reads (``level_identity_inputs``).

It prints one JSON summary to stdout, counts only: reports read, replayed
and not replayed; records per source (live, refused), author relation (same,
nested, overlap, disjoint, or no_authors when either side names none) and
verdict (admitted, refused, ambiguous, truncated); and the records newly
admitted by the widening, with the number of distinct books. A record is new
when its relation is nested, the level admits it, and the classifier's
explicit-contradiction check without partial author lists, the check the
level composed before the amendment, refuses it. A nested record that check
does not refuse (for example one whose creators are neither labelled
authors nor bound to an identity) was admitted before too; it is counted
under ``nested_admitted_before``.

Names leave the script only with ``--detail PATH``: a new owner-only JSON
file (an existing path is refused) listing each admitted nested record,
marked ``new`` true or false, for a by-hand check that it is the same book,
and each overlapping record the level refuses, so a wrong refusal can be
spotted. Keep that file private.

The application database is ``/data/application.sqlite3`` inside the
container's ``/data`` volume (docs/operations/backups-and-recovery.md). Run
against a copy, taken while the service is stopped, from a checkout beside
``compose.yaml``:

    docker compose stop mechanical-librarian
    mkdir -p ~/ml-replay
    docker compose cp mechanical-librarian:/data/application.sqlite3 \\
        ~/ml-replay/application.sqlite3
    # If an older Compose refuses to copy from a stopped service:
    # docker cp "$(docker compose ps -aq mechanical-librarian)":/data/application.sqlite3 ~/ml-replay/
    docker compose start mechanical-librarian
    uv run python scripts/partial_author_replay.py \\
        --database ~/ml-replay/application.sqlite3 \\
        --detail ~/ml-replay/partial-author-detail.json

Exit status 2 means the database is missing, unreadable or not an
application database, or the detail path already exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    MetadataReport,
    ProviderCandidate,
    ProviderObservation,
    WorkIdentityContradiction,
    creator_comparison_key,
    creator_list_relation,
    provider_candidate_work_identity_contradiction,
)
from mechanical_librarian.domain.proposal_set import (
    LevelIdentityInputs,
    level_identity_inputs,
    provider_candidate_level_admission,
    provider_candidate_represents_target_work,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

SCHEMA = "partial-author-replay-v1"
LEVEL = WorkIdentityLevel.TITLE_AND_CREATOR
SOURCES = ("live", "refused")
RELATIONS = ("same", "nested", "overlap", "disjoint", "no_authors")
VERDICTS = ("admitted", "refused", "ambiguous", "truncated")
NOT_REPLAYED = ("schema_unsupported", "replay_failed")
_REQUIRED_TABLES = frozenset(
    {"metadata_reports", "review_cases", "current_review_packages"}
)
_CURRENT_REPORTS_SQL = (
    "SELECT mr.document FROM review_cases rc "
    "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
    "WHERE rc.review_case_id IN (SELECT review_case_id FROM current_review_packages "
    "WHERE review_case_id IS NOT NULL) ORDER BY rc.review_case_id"
)

Book = tuple[str, str, str]


class Refusal(Exception):
    """A fixed refusal; the message never carries a path or a value."""


@dataclass(frozen=True, slots=True)
class RecordVerdict:
    """One provider record's verdict at the level and its author relation."""

    source: str
    relation: str
    verdict: str
    already_admitted: bool
    new: bool
    detail: dict[str, object]


@dataclass(slots=True)
class Replay:
    """Every count over one database, plus the private detail rows."""

    read: int = 0
    not_replayed: Counter[str] = field(default_factory=Counter)
    records: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    newly_admitted: Counter[str] = field(default_factory=Counter)
    newly_admitted_books: dict[str, set[Book]] = field(
        default_factory=lambda: {source: set() for source in SOURCES}
    )
    already_admitted: int = 0
    nested_before: Counter[str] = field(default_factory=Counter)
    nested_before_books: dict[str, set[Book]] = field(
        default_factory=lambda: {source: set() for source in SOURCES}
    )
    nested_admitted: list[dict[str, object]] = field(default_factory=list)
    overlap_refused: list[dict[str, object]] = field(default_factory=list)

    @property
    def replayed(self) -> int:
        return self.read - sum(self.not_replayed.values())


def open_database(path: Path) -> sqlite3.Connection:
    """The application database, opened read-only, or a refusal."""
    if not path.is_file():
        raise Refusal("database not found")
    absolute = quote(str(path.absolute()), safe="/")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{absolute}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    except sqlite3.Error:
        if connection is not None:
            connection.close()
        raise Refusal("database unreadable") from None
    if not _REQUIRED_TABLES <= tables:
        connection.close()
        raise Refusal("not an application database")
    return connection


def parse_report(text: str) -> MetadataReport | None:
    """The report under the current parser; ``None`` for any other schema."""
    document = json.loads(text)
    if not isinstance(document, dict) or document.get("schema_version") != (
        REPORT_SCHEMA
    ):
        return None
    return MetadataReport.from_json(text)


def _records(
    report: MetadataReport,
) -> Iterator[tuple[ProviderObservation, ProviderCandidate, str]]:
    for provider in report.providers:
        for candidate in provider.candidates:
            yield provider, candidate, "live"
        for candidate in provider.identity_refused_candidates:
            yield provider, candidate, "refused"


def _relation(inputs: LevelIdentityInputs) -> str:
    if not inputs.target_authors or not inputs.record_creators:
        return "no_authors"
    return creator_list_relation(
        inputs.target_authors, inputs.record_creators, fold_initials=True
    )


def _distinct(values: tuple[str, ...]) -> int:
    return len({creator_comparison_key(value) for value in values})


def _detail(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
    source: str,
    inputs: LevelIdentityInputs,
) -> dict[str, object]:
    book, record = _distinct(inputs.target_authors), _distinct(inputs.record_creators)
    return {
        "book": {
            "library": report.target.library,
            "item": report.target.item,
            "title": inputs.target_title,
            "authors": list(inputs.target_authors),
        },
        "record": {
            "provider": provider.provider,
            "identity": candidate.identity,
            "work_identity": candidate.work_identity,
            "title": inputs.record_title,
            "authors": list(inputs.record_creators),
        },
        "source": source,
        "fewer_authors": (
            "book" if book < record else "record" if record < book else "neither"
        ),
    }


def _refused_before(candidate: ProviderCandidate, inputs: LevelIdentityInputs) -> bool:
    """Whether the level's composition before partial author lists refused it.

    For a nested list the two compositions differ only in the classifier:
    the overlap refusal never applies to nested lists and every other
    contradiction is the same, so the old refusal is the classifier's
    explicit contradiction read without ``partial_authors``.
    """
    return (
        inputs.target_title is not None
        and bool(inputs.target_authors)
        and provider_candidate_work_identity_contradiction(
            candidate, inputs.target_title, inputs.target_authors, fold_initials=True
        )
        is WorkIdentityContradiction.EXPLICIT
    )


def replay_records(report: MetadataReport) -> list[RecordVerdict]:
    """Every record's verdict at ``title_and_creator`` in one report."""
    verdicts: list[RecordVerdict] = []
    for provider, candidate, source in _records(report):
        record = provider_candidate_level_admission(report, provider, candidate, LEVEL)
        if record is None:  # pragma: no cover - never at a wider level
            raise ValueError("level gave no verdict")
        inputs = level_identity_inputs(report, candidate)
        detail = _detail(report, provider, candidate, source, inputs)
        if record.admission is None:
            verdict = cast("str", record.refusal)
        else:
            verdict = "admitted"
            detail["creator_comparison"] = record.creator_comparison
        detail["verdict"] = verdict
        relation = _relation(inputs)
        new = (
            relation == "nested"
            and verdict == "admitted"
            and _refused_before(candidate, inputs)
        )
        if relation == "nested" and verdict == "admitted":
            detail["new"] = new
        verdicts.append(
            RecordVerdict(
                source,
                relation,
                verdict,
                source == "live"
                and provider_candidate_represents_target_work(
                    report, provider, candidate
                ),
                new,
                detail,
            )
        )
    return verdicts


def replay_document(text: str, replay: Replay) -> None:
    """Replay one current case's stored report into the running counts."""
    replay.read += 1
    try:
        report = parse_report(text)
        if report is None:
            replay.not_replayed["schema_unsupported"] += 1
            return
        verdicts = replay_records(report)
    except Exception:
        replay.not_replayed["replay_failed"] += 1
        return
    book: Book = (
        report.target.connection,
        report.target.library,
        report.target.item,
    )
    for value in verdicts:
        replay.records[(value.source, value.relation, value.verdict)] += 1
        if value.relation == "nested" and value.verdict == "admitted":
            if value.new:
                replay.newly_admitted[value.source] += 1
                replay.newly_admitted_books[value.source].add(book)
                replay.already_admitted += value.already_admitted
            else:
                replay.nested_before[value.source] += 1
                replay.nested_before_books[value.source].add(book)
            replay.nested_admitted.append(value.detail)
        elif value.relation == "overlap" and value.verdict != "admitted":
            replay.overlap_refused.append(value.detail)


def replay_database(connection: sqlite3.Connection) -> Replay:
    replay = Replay()
    try:
        rows = connection.execute(_CURRENT_REPORTS_SQL).fetchall()
    except sqlite3.Error:
        raise Refusal("not an application database") from None
    for (text,) in rows:
        replay_document(cast("str", text), replay)
    return replay


def summary(replay: Replay, *, detail_written: bool) -> dict[str, object]:
    """The aggregate document: counts only, never a name or identifier."""
    return {
        "schema": SCHEMA,
        "report_schema": REPORT_SCHEMA,
        "level": LEVEL.value,
        "reports": {
            "read": replay.read,
            "replayed": replay.replayed,
            "not_replayed": {
                reason: replay.not_replayed[reason] for reason in NOT_REPLAYED
            },
        },
        "records": {
            source: {
                relation: {
                    verdict: replay.records[(source, relation, verdict)]
                    for verdict in VERDICTS
                }
                for relation in RELATIONS
            }
            for source in SOURCES
        },
        "newly_admitted": {
            "live": {
                "records": replay.newly_admitted["live"],
                "books": len(replay.newly_admitted_books["live"]),
                "live_identity_already_admits": replay.already_admitted,
            },
            "refused": {
                "records": replay.newly_admitted["refused"],
                "books": len(replay.newly_admitted_books["refused"]),
            },
            "books": len(
                replay.newly_admitted_books["live"]
                | replay.newly_admitted_books["refused"]
            ),
        },
        "nested_admitted_before": {
            **{
                source: {
                    "records": replay.nested_before[source],
                    "books": len(replay.nested_before_books[source]),
                }
                for source in SOURCES
            },
            "books": len(
                replay.nested_before_books["live"]
                | replay.nested_before_books["refused"]
            ),
        },
        "detail_written": detail_written,
    }


def write_detail(path: Path, replay: Replay) -> None:
    """Create the private detail file; an existing path is never replaced."""
    document = {
        "schema": SCHEMA + "-detail",
        "level": LEVEL.value,
        "nested_admitted": replay.nested_admitted,
        "overlap_refused": replay.overlap_refused,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Refusal("detail path already exists") from None
    except OSError:
        raise Refusal("detail path cannot be created") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read a copy of the Mechanical Librarian application database "
            "without changing it, and count which provider records the Book "
            "matching level 'title_and_creator' admits now that an author "
            "list that contains the book's authors, or is contained in them, "
            "counts. Prints counts only. Nothing is sent anywhere."
        ),
        epilog=(
            "Stop the service and copy /data/application.sqlite3 out of the "
            "container first; see this script's module docstring."
        ),
    )
    parser.add_argument(
        "--database",
        required=True,
        type=Path,
        help="a copy of application.sqlite3; opened read-only",
    )
    parser.add_argument(
        "--detail",
        type=Path,
        help=(
            "also write a private JSON file naming each admitted record with "
            "a nested author list, marked new or not, and each overlapping "
            "record now refused, for a by-hand check; "
            "the file must not exist yet"
        ),
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    detail = cast("Path | None", arguments.detail)
    try:
        if detail is not None and (detail.exists() or detail.is_symlink()):
            raise Refusal("detail path already exists")
        connection = open_database(cast("Path", arguments.database))
        try:
            replay = replay_database(connection)
        finally:
            connection.close()
        if detail is not None:
            write_detail(detail, replay)
    except Refusal as refusal:
        errors.write(f"partial_author_replay: {refusal}\n")
        return 2
    output.write(
        json.dumps(summary(replay, detail_written=detail is not None), indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
