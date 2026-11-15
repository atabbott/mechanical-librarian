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
verdict twice: as this checkout composes the level now, and as it was
composed before the amendment. It also relates the book's stored authors to
the record's work authors (``creator_list_relation`` with the
dotted-initials fold) from the inputs the seam itself reads
(``level_identity_inputs``).

The "before" verdict is the whole seam, set rule included, with only the
contradiction check swapped: before the amendment ``title_and_creator`` read
the contradictions that ``title_and_creator_exact`` still reads, word for
word (ADR-0096 amendment, contract S3). So a record can change because of
its own author list or because a sibling in its set changed: a record that
was ambiguous beside an overlapping one is admitted once that one is
refused.

It prints one JSON summary to stdout, counts only: reports read, replayed
and not replayed; records now per source (live, refused), author relation
(same, nested, overlap, disjoint, or no_authors when either side names none)
and verdict (admitted, refused, ambiguous, truncated); and, per source with
distinct books and by relation, the records newly admitted (admitted now,
not before), no longer admitted (admitted before, not now) and still
admitted (both).

Names leave the script only with ``--detail PATH``: a new owner-only JSON
file (an existing path is refused) listing each newly admitted record, for a
by-hand check that it is the same book, each record no longer admitted, and
each overlapping record the level now refuses, so a wrong refusal can be
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
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.domain import proposal_set
from mechanical_librarian.domain.metadata_report import (
    REPORT_SCHEMA,
    MetadataReport,
    ProviderCandidate,
    ProviderObservation,
    creator_comparison_key,
    creator_list_relation,
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
PREVIOUS_CONTRADICTION_LEVEL = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
SOURCES = ("live", "refused")
CHANGES = ("newly_admitted", "no_longer_admitted", "still_admitted")
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
    """One provider record's verdicts now and before, and its author relation."""

    source: str
    relation: str
    verdict: str
    before: str
    already_admitted: bool
    detail: dict[str, object]

    @property
    def change(self) -> str | None:
        now, then = self.verdict == "admitted", self.before == "admitted"
        if now and then:
            return "still_admitted"
        if now:
            return "newly_admitted"
        return "no_longer_admitted" if then else None


@dataclass(slots=True)
class ChangeTally:
    """The records of one change, per source, with their books."""

    records: Counter[tuple[str, str]] = field(default_factory=Counter)
    books: dict[str, set[Book]] = field(
        default_factory=lambda: {source: set() for source in SOURCES}
    )
    rows: list[dict[str, object]] = field(default_factory=list)


@dataclass(slots=True)
class Replay:
    """Every count over one database, plus the private detail rows."""

    read: int = 0
    not_replayed: Counter[str] = field(default_factory=Counter)
    records: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    changes: dict[str, ChangeTally] = field(
        default_factory=lambda: {change: ChangeTally() for change in CHANGES}
    )
    already_admitted: int = 0
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


@contextmanager
def _previous_composition() -> Iterator[None]:
    """The level seam as composed before the amendment, for this pass only.

    Before the amendment, ``title_and_creator`` read the contradictions
    ``title_and_creator_exact`` still reads, word for word (contract S3);
    the predicate and the set rule did not change. The seam looks
    ``level_contradiction`` up in its module on every call, so replacing it
    there for the duration of one single-threaded pass replays the previous
    verdicts, set rule included, without a product option no product code
    would use. It is restored before the pass returns, even on failure.
    """
    current = proposal_set.level_contradiction

    def previous(
        report: MetadataReport,
        provider: ProviderObservation,
        candidate: ProviderCandidate,
        level: WorkIdentityLevel,
    ) -> bool:
        return current(
            report,
            provider,
            candidate,
            PREVIOUS_CONTRADICTION_LEVEL if level is LEVEL else level,
        )

    proposal_set.level_contradiction = previous
    try:
        yield
    finally:
        proposal_set.level_contradiction = current


def _verdicts(report: MetadataReport) -> list[str]:
    """Each record's verdict at ``title_and_creator``, in `_records` order."""
    verdicts: list[str] = []
    for provider, candidate, _source in _records(report):
        record = provider_candidate_level_admission(report, provider, candidate, LEVEL)
        if record is None:  # pragma: no cover - never at a wider level
            raise ValueError("level gave no verdict")
        verdicts.append(
            "admitted" if record.admission is not None else cast("str", record.refusal)
        )
    return verdicts


def replay_records(report: MetadataReport) -> list[RecordVerdict]:
    """Every record's verdicts at ``title_and_creator``, now and before."""
    now = _verdicts(report)
    with _previous_composition():
        before = _verdicts(report)
    verdicts: list[RecordVerdict] = []
    for (provider, candidate, source), verdict, then in zip(
        _records(report), now, before, strict=True
    ):
        inputs = level_identity_inputs(report, candidate)
        relation = _relation(inputs)
        detail = _detail(report, provider, candidate, source, inputs)
        detail.update(relation=relation, verdict=verdict, before=then)
        verdicts.append(
            RecordVerdict(
                source,
                relation,
                verdict,
                then,
                source == "live"
                and provider_candidate_represents_target_work(
                    report, provider, candidate
                ),
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
        change = value.change
        if change is not None:
            tally = replay.changes[change]
            tally.records[(value.source, value.relation)] += 1
            tally.books[value.source].add(book)
            if change != "still_admitted":
                tally.rows.append(value.detail)
            if change == "newly_admitted":
                replay.already_admitted += value.already_admitted
        if value.relation == "overlap" and value.verdict != "admitted":
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


def _change(tally: ChangeTally) -> dict[str, object]:
    return {
        **{
            source: {
                "records": sum(
                    tally.records[(source, relation)] for relation in RELATIONS
                ),
                "books": len(tally.books[source]),
                "relations": {
                    relation: tally.records[(source, relation)]
                    for relation in RELATIONS
                },
            }
            for source in SOURCES
        },
        "books": len(tally.books["live"] | tally.books["refused"]),
    }


def summary(replay: Replay, *, detail_written: bool) -> dict[str, object]:
    """The aggregate document: counts only, never a name or identifier."""
    changes = {change: _change(replay.changes[change]) for change in CHANGES}
    cast("dict[str, object]", changes["newly_admitted"]["live"])[
        "live_identity_already_admits"
    ] = replay.already_admitted
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
        **changes,
        "detail_written": detail_written,
    }


def write_detail(path: Path, replay: Replay) -> None:
    """Create the private detail file; an existing path is never replaced."""
    document = {
        "schema": SCHEMA + "-detail",
        "level": LEVEL.value,
        "newly_admitted": replay.changes["newly_admitted"].rows,
        "no_longer_admitted": replay.changes["no_longer_admitted"].rows,
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
            "counts, against what it admitted before that change: newly "
            "admitted, no longer admitted and still admitted. Prints counts "
            "only. Nothing is sent anywhere."
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
            "also write a private JSON file naming each record admitted now "
            "but not before, each admitted before but not now, and each "
            "overlapping record now refused, for a by-hand check; "
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
