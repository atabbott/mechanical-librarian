"""Aggregate-only replay of Hardcover's planned dispatches over stored reports.

A workbench instrument (#1380), deliberately not part of the installed
product: run it from a checkout against a COPY of an application database.
It opens that copy read-only, writes nothing and prints one
``hardcover-dispatch-replay-v1`` JSON document to stdout.

For every current ebook report it rebuilds the discovery plan with this
checkout's code and compares the Hardcover dispatches the plan would send
first with the ones the report recorded. It reads, by explicit key paths,
only the target title and authors, the kind, basis, role, state, canonical
value and reference of each ISBN, title and creator claim, the persisted
Hardcover observation (its status, lanes, lane candidates and the scopes of
its candidates' claims, its request accounting and overflow witness), and
which Hardcover candidates identifier evidence names. A report missing any of
them counts as ``unreadable`` and adds nothing to any other figure; nothing is
converted. An ebook whose Hardcover binding was disabled was never asked and
would not be asked now, so it is counted in ``ebooks_with_hardcover_disabled``
and left out of every dispatch figure.

The document reports:

- ``planned_initial_dispatches``: one per planned ISBN lane and one for the
  WORK lane unless the report shows an ISBN answer suppressed it, per ebook
  (distribution and total), against ``baseline_initial_dispatches``: the
  recorded initial dispatches plus the recorded lanes left undispatched.
- ``narrowing_retries_removed``: recorded WORK lanes still over the candidate
  limit whose only author has no other spelling, so no retry is sent now. It
  is a lower bound: a retry that found an answer leaves no such lane.
- ``epub_fallback``: ebooks whose target names no author and that now plan a
  WORK lane with their EPUB creators, and the dispatches that adds. These are
  the only ebooks allowed to plan more than their baseline.
- ``edition_fetch_ceiling``: four pages per Hardcover observation with an
  accepted book, the most an edition fetch could add.
- ``author_form_rungs``: planned WORK rungs that also send an inverted or an
  initials spelling of a stored author.
- ``isbn_lane_overflow_witnesses``: ISBN lanes over the candidate limit, the
  only lanes that can still produce an overflow witness.
- ``ebooks_losing_work_lane_edition_claim``: ebooks whose recorded WORK lane
  holds a candidate with an edition-scope claim, which a book never carries.
- ``q5_reports_losing_work_lane_neutral_isbn_evidence``: reports whose
  Hardcover identifier evidence names a candidate only a WORK lane returned.
- ``q6_work_lane_refusals_only_by_edition_title_variant_veto``: not
  computable from a stored report, which holds no refused candidate; it is
  ``null`` with its reason under ``not_computable``.

An audiobook report is not re-planned, since its plan needs audio facts no
report stores: its section counts the recorded Hardcover WORK lanes and the
narrowing retries the shared ladder no longer sends.

The output holds closed names and counts only: never a title, author, value,
path, name or identifier. The exit status is 1 when the planned library total
exceeds the baseline total, or when an ebook other than an EPUB-fallback one
plans more than its baseline.

Usage:
    uv run python scripts/hardcover_dispatch_replay.py \
        --database /copy/application.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    LocalClaim,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.work_identity import (
    WorkQuery,
    hardcover_query_spellings,
    work_query_ladder,
)

SCHEMA = "hardcover-dispatch-replay-v1"
EDITION_FETCH_PAGES = 4
_CLAIM_KINDS = frozenset({ClaimKind.ISBN, ClaimKind.TITLE, ClaimKind.CREATOR})
_REPORTS = (
    "SELECT m.document FROM current_metadata_reports c "
    "JOIN metadata_reports m ON m.report_id = c.report_id ORDER BY m.report_id"
)


class Unreadable(Exception):
    """A report lacks a key path the replay reads."""


@dataclass
class EbookTally:
    planned: Counter[int] = field(default_factory=Counter)
    baseline: Counter[int] = field(default_factory=Counter)
    recorded_initial: int = 0
    recorded_undispatched: int = 0
    plan_overflow: int = 0
    suppressed_work_lanes: int = 0
    increased_fallback: int = 0
    increased_other: int = 0
    narrowing_removed: int = 0
    candidate_limit_work_lanes: int = 0
    fallback_items: int = 0
    fallback_dispatches: int = 0
    accepted_observations: int = 0
    inverted_rungs: int = 0
    initials_rungs: int = 0
    isbn_witnesses: int = 0
    persisted_witnesses: int = 0
    losing_edition_claim: int = 0
    q5: int = 0
    disabled: int = 0


@dataclass
class AudiobookTally:
    reports: int = 0
    work_lanes: int = 0
    candidate_limit_work_lanes: int = 0


def _merge(
    total: EbookTally | AudiobookTally, part: EbookTally | AudiobookTally
) -> None:
    """Add one report's tally to the running total once the report is readable."""
    for member in fields(total):
        name = member.name
        setattr(total, name, getattr(total, name) + getattr(part, name))


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    return sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)


def _at(value: object, *keys: str | int) -> object:
    for key in keys:
        if isinstance(key, int):
            if not isinstance(value, list) or not 0 <= key < len(value):
                raise Unreadable
            value = value[key]
        else:
            if not isinstance(value, dict) or key not in value:
                raise Unreadable
            value = value[key]
    return value


def _rows(value: object, *keys: str) -> list[dict[str, object]]:
    rows = _at(value, *keys)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise Unreadable
    return cast("list[dict[str, object]]", rows)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise Unreadable
    return value


def _count(value: object, key: str) -> int:
    number = _at(value, key)
    if type(number) is not int or number < 0:
        raise Unreadable
    return number


def _target_fields(document: object) -> tuple[str, tuple[TargetField, ...]]:
    stored = {
        _text(_at(row, "destination")): row
        for row in _rows(document, "target", "fields")
    }
    title_row = stored.get(TargetDestination.TITLE.value)
    authors_row = stored.get(TargetDestination.AUTHORS.value)
    if title_row is None or authors_row is None:
        raise Unreadable
    title_state = TargetFieldState(_text(_at(title_row, "state")))
    title = _at(title_row, "value")
    authors_state = TargetFieldState(_text(_at(authors_row, "state")))
    authors = _at(authors_row, "value")
    title_field = (
        TargetField(TargetDestination.TITLE, title_state, _text(title))
        if title_state is TargetFieldState.VALUE
        else TargetField(TargetDestination.TITLE, title_state)
    )
    if authors_state is TargetFieldState.VALUE:
        if not isinstance(authors, list):
            raise Unreadable
        authors_field = TargetField(
            TargetDestination.AUTHORS,
            authors_state,
            tuple(_text(value) for value in authors),
        )
    else:
        authors_field = TargetField(TargetDestination.AUTHORS, authors_state)
    named = title if isinstance(title, str) else None
    fields = tuple(
        title_field
        if value.destination is TargetDestination.TITLE
        else authors_field
        if value.destination is TargetDestination.AUTHORS
        else value
        for value in default_target_fields(named)
    )
    return named or "", fields


def _local_claims(document: object) -> tuple[LocalClaim, ...]:
    """Rebuild only what planning reads; every other member is a placeholder."""
    claims: list[LocalClaim] = []
    for row in _rows(document, "local", "claims"):
        kind = ClaimKind(_text(_at(row, "kind")))
        basis = _text(_at(row, "basis"))
        state = FieldState(_text(_at(row, "state")))
        role = _at(row, "role")
        canonical = _at(row, "canonical")
        _text(_at(row, "reference"))
        if kind not in _CLAIM_KINDS or canonical is None:
            continue
        canonical = _text(canonical)
        if role is not None:
            role = _text(role)
        isbn = kind is ClaimKind.ISBN
        if isbn and state is not FieldState.VALID:
            continue
        claims.append(
            LocalClaim.create(
                kind=kind,
                observed=canonical,
                canonical=canonical,
                excerpt=canonical,
                applicability="unknown" if isbn else "not_applicable",
                basis=basis,
                checksum=(f"valid_isbn_{len(canonical)}" if isbn else "not_applicable"),
                applicability_basis="unlabelled" if isbn else "not_applicable",
                state=state,
                role=role,
                occurrence=len(claims) + 1,
            )
        )
    return tuple(claims)


def _hardcover(document: object) -> dict[str, object]:
    providers = [
        row
        for row in _rows(document, "providers")
        if _text(_at(row, "authority")) == "hardcover"
    ]
    if len(providers) != 1:
        raise Unreadable
    return providers[0]


def _inverted(name: str) -> str | None:
    if name.count(",") != 1:
        return None
    family, given = (side.split() for side in name.split(","))
    if not 1 <= len(family) <= 4 or not 1 <= len(given) <= 4:
        return None
    return " ".join((*given, *family))


def _form_kinds(query: WorkQuery) -> tuple[bool, bool]:
    authors = hardcover_query_spellings(query)[1]
    stored = set(query.authors)
    inverted = {value for name in query.authors if (value := _inverted(name))}
    forms = [value for value in authors if value not in stored]
    return (
        any(value in inverted for value in forms),
        any(value not in inverted for value in forms),
    )


def _narrowing_skipped(query: WorkQuery) -> bool:
    return hardcover_query_spellings(query)[1] == (query.authors[0],)


def _lanes(observation: dict[str, object]) -> list[dict[str, object]]:
    lanes = _rows(observation, "lanes")
    for lane in lanes:
        ProviderQueryKind(_text(_at(lane, "kind")))
        _text(_at(lane, "status"))
        for candidate in _rows(lane, "candidates"):
            _at(candidate, "reference")
            _at(candidate, "edition_identity")
            _text(_at(candidate, "work_identity"))
    return lanes


def _candidate_identity(candidate: dict[str, object]) -> str:
    """The observation candidate a lane candidate stands for."""
    edition = candidate["edition_identity"]
    return _text(candidate["work_identity"] if edition is None else edition)


def _replay_ebook(document: object, tally: EbookTally) -> None:
    title, target = _target_fields(document)
    claims = _local_claims(document)
    observation = _hardcover(document)
    if ProviderStatus(_text(_at(observation, "status"))) is ProviderStatus.DISABLED:
        # A disabled binding is never asked, before or after this change.
        tally.disabled += 1
        return
    accounting = _at(observation, "request_accounting")
    recorded_initial = _count(accounting, "initial_dispatches")
    recorded_undispatched = _count(accounting, "undispatched_incomplete")
    lanes = _lanes(observation)
    witness = _at(observation, "edition_enrichment_overflow")
    candidate_scopes = {
        _text(_at(row, "identity")): {
            _at(claim, "scope") for claim in _rows(row, "claims")
        }
        for row in _rows(observation, "candidates")
    }
    provider_name = _text(_at(observation, "provider"))
    evidence = [
        (_at(row, "provider"), _at(row, "candidate_identity"))
        for row in _rows(document, "identifier_evidence")
    ]

    try:
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", title or None, (), fields=target), claims
        )
    except (MetadataReportError, ReportValueError, ValueError) as error:
        raise Unreadable from error

    recorded_work = [lane for lane in lanes if lane["kind"] == "work"]
    recorded_isbn = [lane for lane in lanes if lane["kind"] == "isbn"]
    isbn_answered = any(lane["status"] == "candidates" for lane in recorded_isbn)
    planned_work = [lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK]
    planned_isbn = sum(lane.kind is ProviderQueryKind.ISBN for lane in plan.lanes)
    fallback = any(lane.author_basis is not None for lane in planned_work)

    planned = planned_isbn
    for lane in planned_work:
        if lane.author_basis is not None:
            # No baseline lane to read: counted as sent, the upper bound.
            planned += 1
            tally.fallback_dispatches += 1
        elif not recorded_work and isbn_answered:
            tally.suppressed_work_lanes += 1
        else:
            planned += 1
        ladder = work_query_ladder(cast("str", lane.title), lane.authors)
        for query in ladder:
            inverted, initials = _form_kinds(query)
            tally.inverted_rungs += inverted
            tally.initials_rungs += initials
        for recorded in recorded_work:
            if recorded["status"] == "candidate_limit_exceeded":
                tally.candidate_limit_work_lanes += 1
                tally.narrowing_removed += _narrowing_skipped(ladder[0])
    if plan.overflow:
        tally.plan_overflow += 1
    baseline = recorded_initial + recorded_undispatched
    tally.planned[planned] += 1
    tally.baseline[baseline] += 1
    tally.recorded_initial += recorded_initial
    tally.recorded_undispatched += recorded_undispatched
    if planned > baseline:
        if fallback:
            tally.increased_fallback += 1
        else:
            tally.increased_other += 1
    tally.fallback_items += fallback
    tally.accepted_observations += any(lane["status"] == "candidates" for lane in lanes)
    tally.isbn_witnesses += sum(
        lane["status"] == "candidate_limit_exceeded" for lane in recorded_isbn
    )
    tally.persisted_witnesses += witness is not None

    work_candidates = {
        _candidate_identity(candidate)
        for lane in recorded_work
        for candidate in _rows(lane, "candidates")
    }
    if not work_candidates <= candidate_scopes.keys():
        raise Unreadable
    tally.losing_edition_claim += any(
        "edition" in candidate_scopes[identity] for identity in work_candidates
    )
    work_editions = {
        _at(candidate, "edition_identity")
        for lane in recorded_work
        for candidate in _rows(lane, "candidates")
    }
    isbn_editions = {
        _at(candidate, "edition_identity")
        for lane in recorded_isbn
        for candidate in _rows(lane, "candidates")
    }
    tally.q5 += any(
        provider == provider_name
        and identity is not None
        and identity in work_editions
        and identity not in isbn_editions
        for provider, identity in evidence
    )


def _replay_audiobook(document: object, tally: AudiobookTally) -> None:
    lanes = _lanes(_hardcover(document))
    tally.reports += 1
    work = [lane for lane in lanes if lane["kind"] == "work"]
    tally.work_lanes += len(work)
    # The audiobook path never sent a narrowing retry, before or after this
    # change, so an audiobook overflow removes none.
    tally.candidate_limit_work_lanes += sum(
        lane["status"] == "candidate_limit_exceeded" for lane in work
    )


def _distribution(values: Counter[int]) -> dict[str, int]:
    return {str(key): values[key] for key in sorted(values)}


def replay(connection: sqlite3.Connection) -> dict[str, object]:
    """Replay every current report and return the aggregate document."""
    ebook = EbookTally()
    audiobook = AudiobookTally()
    read = unreadable = 0
    for (raw,) in connection.execute(_REPORTS):
        read += 1
        try:
            document = json.loads(raw)
            media = _text(_at(document, "file", "format"))
            if media == "epub":
                ebook_part = EbookTally()
                _replay_ebook(document, ebook_part)
                _merge(ebook, ebook_part)
            else:
                audiobook_part = AudiobookTally()
                _replay_audiobook(document, audiobook_part)
                _merge(audiobook, audiobook_part)
        except (Unreadable, ValueError, TypeError):
            unreadable += 1
    planned_total = sum(key * count for key, count in ebook.planned.items())
    baseline_total = sum(key * count for key, count in ebook.baseline.items())
    within_bound = planned_total <= baseline_total and ebook.increased_other == 0
    return {
        "schema": SCHEMA,
        "reports": {
            "read": read,
            "unreadable": unreadable,
            "ebooks": sum(ebook.planned.values()),
            "ebooks_with_hardcover_disabled": ebook.disabled,
            "audiobooks": audiobook.reports,
        },
        "ebook": {
            "planned_initial_dispatches": {
                "total": planned_total,
                "distribution": _distribution(ebook.planned),
            },
            "baseline_initial_dispatches": {
                "total": baseline_total,
                "distribution": _distribution(ebook.baseline),
                "recorded_initial": ebook.recorded_initial,
                "recorded_undispatched": ebook.recorded_undispatched,
            },
            "within_bound": within_bound,
            "increased_ebooks": {
                "epub_fallback": ebook.increased_fallback,
                "other": ebook.increased_other,
            },
            "plan_overflow": ebook.plan_overflow,
            "work_lanes_suppressed_as_recorded": ebook.suppressed_work_lanes,
            "narrowing_retries_removed": {
                "lower_bound": ebook.narrowing_removed,
                "recorded_candidate_limit_work_lanes": (
                    ebook.candidate_limit_work_lanes
                ),
            },
            "epub_fallback": {
                "ebooks": ebook.fallback_items,
                "added_initial_dispatches": ebook.fallback_dispatches,
            },
            "edition_fetch_ceiling": {
                "observations_with_accepted_book": ebook.accepted_observations,
                "maximum_added_requests": (
                    EDITION_FETCH_PAGES * ebook.accepted_observations
                ),
            },
            "author_form_rungs": {
                "inverted": ebook.inverted_rungs,
                "initials": ebook.initials_rungs,
            },
            "isbn_lane_overflow_witnesses": ebook.isbn_witnesses,
            "persisted_overflow_witnesses": ebook.persisted_witnesses,
            "ebooks_losing_work_lane_edition_claim": ebook.losing_edition_claim,
            "q5_reports_losing_work_lane_neutral_isbn_evidence": ebook.q5,
            "q6_work_lane_refusals_only_by_edition_title_variant_veto": None,
            "not_computable": {
                "q6_work_lane_refusals_only_by_edition_title_variant_veto": (
                    "stored_reports_hold_no_refused_candidate"
                ),
            },
        },
        "audiobook": {
            "plan_rebuilt": False,
            "recorded_work_lanes": audiobook.work_lanes,
            "narrowing_retries_removed": {
                "lower_bound": 0,
                "recorded_candidate_limit_work_lanes": (
                    audiobook.candidate_limit_work_lanes
                ),
            },
        },
    }


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    if not arguments.database.is_file():
        raise SystemExit("--database names no application database")
    connection = _open_read_only(arguments.database)
    try:
        document = replay(connection)
    except sqlite3.Error as error:
        raise SystemExit(
            "--database is not an application store this checkout can read"
        ) from error
    finally:
        connection.close()
    output.write(json.dumps(document, sort_keys=True, indent=1) + "\n")
    ebook = cast("dict[str, object]", document["ebook"])
    return 0 if ebook["within_bound"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
