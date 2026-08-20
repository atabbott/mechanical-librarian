"""Aggregate-only cold-start measurement report over persisted state (#743).

A workbench instrument, deliberately not part of the installed product: run it
from a checkout against the configuration file and COPIES of (or read-only
access to) the application and catalogue databases. It writes nothing and
prints one ``cold-start-report-v1`` JSON document to stdout.

The report is aggregate-only by construction. It contains connection,
library, provider, and mount names from the operator's own configuration,
destination names, closed reason codes, file extensions, and counts — never a
title, author, filename, media path, or item identifier. The attention
predicate is not re-derived: the queries compose the same shared
``review_sql`` fragments the Attention surface executes, so the report cannot
disagree with the interface about which field needs attention.

Usage:
    uv run python scripts/cold_start_report.py \
        --config /copy/config.json \
        --database /copy/application.sqlite3 \
        --catalogue /copy/catalogue.sqlite3 \
        [--mount-root books=/host/path/to/mount ...]

``--mount-root`` remaps a configured mount name to a host path for the
extension census, because the configuration records container paths.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
import time
from collections import Counter
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _REVIEW_ATTENTION_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
)
from mechanical_librarian.application.configuration import ApplicationConfiguration
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity
from mechanical_librarian.infrastructure.configuration import load_configuration_file

COLD_START_REPORT_SCHEMA = "cold-start-report-v1"

_ATTENTION_FIELD_ROWS_SQL = (
    "SELECT c.connection_name,c.library_name,"
    "json_extract(result.value,'$.destination'),"
    "json_extract(result.value,'$.outcome'),"
    "json_extract(result.value,'$.reasons'),"
    "(SELECT json_extract(outcome.value,'$.reason_code') "
    "FROM shadow_decision_outcomes shadow,"
    "json_each(shadow.document,'$.outcomes') outcome "
    "WHERE shadow.review_case_id=rc.review_case_id "
    "AND json_extract(outcome.value,'$.destination')="
    "json_extract(result.value,'$.destination') "
    "AND json_extract(outcome.value,'$.disposition')='abstention' LIMIT 1) "
    + _CURRENT_REVIEW_FROM_SQL
    + ",json_each(rc.document,'$.results') result "
    "WHERE " + _REVIEW_FIELD_ATTENTION_SQL
)
_LIBRARY_WORK_COUNTS_SQL = (
    "SELECT ml.connection_name,ml.library_name,count(*),"
    "SUM(CASE WHEN c.review_case_id IS NOT NULL AND "
    + _REVIEW_ATTENTION_SQL
    + " THEN 1 ELSE 0 END) "
    + _CURRENT_LIBRARY_FROM_SQL
    + " WHERE ml.outcome!='report_only' "
    "GROUP BY ml.connection_name,ml.library_name"
)
_PROVIDER_ROWS_SQL = (
    "SELECT ml.connection_name,ml.library_name,"
    "json_extract(provider.value,'$.provider'),"
    "json_extract(provider.value,'$.status'),"
    "json_extract(provider.value,'$.lanes') "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id,"
    "json_each(mr.document,'$.providers') provider "
    "WHERE ml.outcome!='report_only'"
)
_UNMATCHED_WORKS_SQL = (
    "SELECT ml.connection_name,ml.library_name,count(*) "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "WHERE ml.outcome!='report_only' "
    "AND NOT EXISTS (SELECT 1 FROM json_each(mr.document,'$.providers') provider "
    "WHERE json_extract(provider.value,'$.status')='candidates') "
    "GROUP BY 1,2"
)
_SCAN_ROWS_SQL = (
    "SELECT scan_id,connection_name,library_name,state,total,requested_at,"
    "updated_at FROM review_preparation_scans scans "
    "WHERE requested_at=(SELECT MAX(requested_at) FROM review_preparation_scans "
    "latest WHERE latest.connection_name=scans.connection_name "
    "AND latest.library_name=scans.library_name)"
)
# A completed work counts as ready whether or not it admitted a review case:
# a quiet work finishes at admission with no case, and on a healthy library
# that is the majority outcome, so conditioning on review_case_id would
# exclude exactly the completions throughput must count.
_WORK_STATE_COUNTS_SQL = (
    "SELECT work.connection_name,work.library_name,"
    "SUM(CASE WHEN work.state='complete' THEN 1 ELSE 0 END),"
    "SUM(CASE WHEN work.state IN ('failed','stale') THEN 1 ELSE 0 END),count(*),"
    "MIN(CASE WHEN work.state='complete' THEN work.updated_at END),"
    "MAX(CASE WHEN work.state='complete' THEN work.updated_at END) "
    "FROM review_preparation_work work "
    "JOIN review_preparation_scans scan ON scan.scan_id=work.scan_id "
    "WHERE scan.catalogue_generation IS NOT NULL "
    "GROUP BY work.connection_name,work.library_name"
)
_ABSENCE_COUNTS_SQL = (
    "SELECT work.connection_name,work.library_name,absence.kind,count(*) "
    "FROM review_preparation_absences absence "
    "JOIN review_preparation_scans work ON work.scan_id=absence.scan_id "
    "WHERE work.catalogue_generation IS NOT NULL "
    "GROUP BY 1,2,3"
)
_DUPLICATE_GROUPS_SQL = (
    "SELECT ml.connection_name,ml.library_name,mr.content_digest,"
    "ps.document,mr.document "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "JOIN proposal_sets ps ON ps.proposal_set_id=ml.proposal_set_id "
    "WHERE ml.outcome!='report_only' AND mr.content_digest IN ("
    "SELECT source.content_digest "
    "FROM current_review_packages package "
    "JOIN metadata_reports source ON source.report_id=package.report_id "
    "WHERE package.connection_name=ml.connection_name "
    "AND package.library_name=ml.library_name "
    "AND package.outcome!='report_only' "
    "GROUP BY source.content_digest HAVING count(*)>1)"
)
_AUTOMATIC_AGREEMENT_SQL = (
    "SELECT ml.connection_name,ml.library_name,decision.destination,"
    "json_extract(decision.document,'$.candidate_id'),ps.document,mr.document "
    "FROM automatic_decisions decision "
    "JOIN current_review_packages ml ON ml.review_case_id=decision.review_case_id "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "JOIN review_cases rc ON rc.review_case_id=ml.review_case_id "
    "JOIN proposal_sets ps ON ps.proposal_set_id=rc.proposal_set_id "
    "WHERE json_extract(decision.document,'$.outcome')='candidate'"
)
_ID_KEYS = frozenset(
    {
        "alternative_id",
        "proposal_id",
        "proposal_set_id",
        "report_id",
        "review_case_id",
        "accessed_at",
        "identity",
        "correlations",
    }
)

_CONFLICT_REASONS = frozenset(
    {
        "ambiguous_eligible_actions",
        "bibliographic_combination_unresolved",
        "competing_evidence",
        "date_year_conflict",
        "field_policy_conflict",
        "no_safe_bibliographic_combination",
    }
)
_SUBJECTIVE_REASONS = frozenset(
    {
        "owner_cover_choice_required",
        "populated_subjective_field",
    }
)
_CAPABILITY_REASONS = frozenset(
    {
        "unsupported_destination",
        "work_publication_cannot_replace",
        "policy_ignored",
        "target_state_unusable",
    }
)
_ABSENCE_REASONS = frozenset(
    {
        "cover_gallery_incomplete",
        "current_cover_unavailable",
        "current_cover_unverified",
        "date_refinement_requires_corroboration",
        "evidence_sufficiency_manual",
        "identifier_applicability_unresolved",
        "insufficient_independent_corroboration",
        "insufficient_support",
        "no_eligible_action",
        "no_positive_preservation_evidence",
        "no_supported_evidence",
        "target_only_preservation",
        "unique_typed_candidate",
    }
)
_SAFETY_CAUSE = "automatic_safety_suspended"
_MAX_MOUNT_WALK_FILES = 1_000_000


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    return sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)


def _without_identities(value: object) -> object:
    """Strip run-scoped identifiers so byte-identical inputs compare equal."""
    if isinstance(value, dict):
        return {
            key: _without_identities(item)
            for key, item in sorted(cast("dict[str, object]", value).items())
            if key not in _ID_KEYS
        }
    if isinstance(value, list):
        return [_without_identities(item) for item in cast("list[object]", value)]
    return value


def _rows(connection: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def _attention_field_rows(
    connection: sqlite3.Connection,
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for row in connection.execute(_ATTENTION_FIELD_ROWS_SQL).fetchall():
        reasons = row[4]
        parsed: tuple[str, ...] = ()
        if isinstance(reasons, str) and reasons:
            loaded = json.loads(reasons)
            if isinstance(loaded, list):
                parsed = tuple(
                    item
                    for item in cast("list[object]", loaded)
                    if isinstance(item, str)
                )
        rows.append((row[0], row[1], row[2], row[3], parsed, row[5]))
    return rows


def _provider_lane_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """One row per work-provider lane: (connection, library, provider, status).

    A provider with no lane array contributes its own terminal status, so a
    run recorded before lanes existed still counts one outcome per provider.
    """
    rows: list[tuple[object, ...]] = []
    for (
        connection_name,
        library_name,
        provider,
        status,
        lanes,
    ) in connection.execute(_PROVIDER_ROWS_SQL).fetchall():
        statuses: list[object] = []
        if isinstance(lanes, str) and lanes:
            loaded = json.loads(lanes)
            if isinstance(loaded, list):
                statuses = [
                    cast("dict[str, object]", lane).get("status")
                    for lane in cast("list[object]", loaded)
                    if isinstance(lane, dict)
                ]
        if not statuses:
            statuses = [status]
        for lane_status in statuses:
            rows.append((connection_name, library_name, provider, lane_status))
    return rows


def _duplicate_groups(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str, str]]:
    """Return (connection, library, digest, context, results) per duplicate.

    The context is the member's current target fields with run-scoped
    identifiers stripped — deliberately not the item identity, which differs
    between duplicates by definition. Determinism is judged only between
    members that share both bytes (the digest) and context, so a copy whose
    target already carries different values never counts as pipeline
    divergence.
    """
    groups: list[tuple[str, str, str, str, str]] = []
    for (
        connection_name,
        library_name,
        digest,
        proposal_document,
        report_document,
    ) in connection.execute(_DUPLICATE_GROUPS_SQL).fetchall():
        results: object = None
        if isinstance(proposal_document, str):
            proposal = json.loads(proposal_document)
            if isinstance(proposal, dict):
                results = _without_identities(
                    cast("dict[str, object]", proposal).get("results")
                )
        context: object = None
        if isinstance(report_document, str):
            report = json.loads(report_document)
            if isinstance(report, dict):
                target = cast("dict[str, object]", report).get("target")
                if isinstance(target, dict):
                    context = _without_identities(
                        cast("dict[str, object]", target).get("fields")
                    )
        groups.append(
            (
                cast("str", connection_name),
                cast("str", library_name),
                cast("str", digest),
                json.dumps(context, sort_keys=True, separators=(",", ":")),
                json.dumps(results, sort_keys=True, separators=(",", ":")),
            )
        )
    return groups


def _candidate_correlations(document: object, candidate_id: str) -> tuple[str, ...]:
    if not isinstance(document, dict):
        return ()
    results = cast("dict[str, object]", document).get("results")
    if not isinstance(results, list):
        return ()
    for result in cast("list[object]", results):
        if not isinstance(result, dict):
            continue
        alternatives = cast("dict[str, object]", result).get("alternatives")
        if not isinstance(alternatives, list):
            continue
        for alternative in cast("list[object]", alternatives):
            if not isinstance(alternative, dict):
                continue
            record = cast("dict[str, object]", alternative)
            if record.get("alternative_id") != candidate_id:
                continue
            correlations = record.get("correlations")
            if isinstance(correlations, list):
                return tuple(
                    item
                    for item in cast("list[object]", correlations)
                    if isinstance(item, str)
                )
    return ()


def _provider_authorities(document: object) -> tuple[str, ...]:
    if not isinstance(document, dict):
        return ()
    providers = cast("dict[str, object]", document).get("providers")
    if not isinstance(providers, list):
        return ()
    values: list[str] = []
    for provider in cast("list[object]", providers):
        if isinstance(provider, dict):
            authority = cast("dict[str, object]", provider).get("authority")
            values.append(authority if isinstance(authority, str) else "unknown")
    return tuple(values)


def _agreement_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for row in connection.execute(_AUTOMATIC_AGREEMENT_SQL).fetchall():
        connection_name, library_name, destination, candidate_id = row[:4]
        correlations: tuple[str, ...] = ()
        authorities: tuple[str, ...] = ()
        if isinstance(row[4], str) and isinstance(candidate_id, str):
            proposal_document = json.loads(row[4])
            correlations = _candidate_correlations(proposal_document, candidate_id)
        if isinstance(row[5], str):
            report_document = json.loads(row[5])
            authorities = _provider_authorities(report_document)
        rows.append(
            (connection_name, library_name, destination, correlations, authorities)
        )
    return rows


def cold_start_snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    """Read every aggregate inside one deferred read transaction."""
    connection.execute("BEGIN")
    try:
        return {
            "libraries": _rows(connection, _LIBRARY_WORK_COUNTS_SQL),
            "attention_fields": _attention_field_rows(connection),
            "provider_lanes": _provider_lane_rows(connection),
            "unmatched_works": _rows(connection, _UNMATCHED_WORKS_SQL),
            "scans": _rows(connection, _SCAN_ROWS_SQL),
            "work_states": _rows(connection, _WORK_STATE_COUNTS_SQL),
            "absences": _rows(connection, _ABSENCE_COUNTS_SQL),
            "duplicates": _duplicate_groups(connection),
            "agreement": _agreement_rows(connection),
        }
    finally:
        connection.execute("COMMIT")


def catalogue_classification_counts(
    path: Path,
) -> tuple[int, tuple[tuple[str, str, str, str, int], ...]] | None:
    """Aggregate the catalogue's current generation, or ``None`` if unusable."""
    try:
        connection = _open_read_only(path)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(
            "SELECT generation FROM catalogue_generations WHERE current=1"
        ).fetchone()
        if row is None:
            return None
        values = connection.execute(
            "SELECT connection,library,classification,COALESCE(format,''),count(*) "
            "FROM catalogue_entries WHERE generation=? GROUP BY 1,2,3,4",
            (row[0],),
        ).fetchall()
        return row[0], tuple(
            (value[0], value[1], value[2], value[3], value[4]) for value in values
        )
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _count_mount_files(root: Path) -> dict[str, int] | None:
    """Count regular files by lowercased extension under one mount root.

    Returns ``None`` when the path is not a readable directory (the report
    records the tier as unavailable rather than guessing). Counts only: no
    names or paths survive into the result.
    """
    if not root.is_dir():
        return None
    counts: dict[str, int] = {}
    seen = 0
    unavailable = False

    def mark_unavailable(_error: OSError) -> None:
        nonlocal unavailable
        unavailable = True

    for directory, _subdirectories, files in os.walk(root, onerror=mark_unavailable):
        if unavailable:
            return None
        for name in files:
            try:
                information = os.stat(
                    Path(directory) / name,
                    follow_symlinks=False,
                )
            except OSError:
                return None
            if not stat.S_ISREG(information.st_mode):
                continue
            seen += 1
            if seen > _MAX_MOUNT_WALK_FILES:
                return None
            suffix = Path(name).suffix.lower().lstrip(".")
            key = suffix if suffix else "none"
            counts[key] = counts.get(key, 0) + 1
    return None if unavailable else counts


def _cause_class(reasons: tuple[str, ...]) -> str:
    if any(reason in _CONFLICT_REASONS for reason in reasons):
        return "conflict"
    if any(reason in _SUBJECTIVE_REASONS for reason in reasons):
        return "owner_subjective"
    if any(reason in _CAPABILITY_REASONS for reason in reasons):
        return "capability"
    if any(reason in _ABSENCE_REASONS for reason in reasons):
        return "absence"
    return "other"


def _bucket(count: int) -> str:
    if count >= 3:
        return "3_plus"
    return str(count)


def _rate(ready: int, elapsed_seconds: int) -> float | None:
    if ready <= 0 or elapsed_seconds <= 0:
        return None
    return round(ready * 3600.0 / elapsed_seconds, 2)


def assemble_cold_start_report(
    configuration: ApplicationConfiguration,
    snapshot: dict[str, object],
    catalogue_counts: tuple[int, tuple[tuple[str, str, str, str, int], ...]] | None,
    mount_files: dict[str, dict[str, int] | None],
    generated_at: int,
) -> dict[str, object]:
    """Build the ``cold-start-report-v1`` document from persisted aggregates.

    Pure assembly: every input is injected, so the document is deterministic
    for identical inputs and testable without a filesystem or database.
    """
    library_keys = [
        (library.connection, library.name) for library in configuration.libraries
    ]
    provider_connections = {
        library.name: tuple(library.provider_connections)
        for library in configuration.libraries
    }
    mount_by_library = {
        library.name: library.mount for library in configuration.libraries
    }

    work_counts = {
        (row[0], row[1]): (int(cast("int", row[2])), int(cast("int", row[3]) or 0))
        for row in cast("list[tuple[object, ...]]", snapshot["libraries"])
    }
    scans = {
        (row[1], row[2]): row
        for row in cast("list[tuple[object, ...]]", snapshot["scans"])
    }
    work_states = {
        (row[0], row[1]): row
        for row in cast("list[tuple[object, ...]]", snapshot["work_states"])
    }
    absence_counts: dict[tuple[object, object], Counter[str]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["absences"]):
        absence_counts.setdefault((row[0], row[1]), Counter())[cast("str", row[2])] += (
            int(cast("int", row[3]))
        )
    unmatched = {
        (row[0], row[1]): int(cast("int", row[2]))
        for row in cast("list[tuple[object, ...]]", snapshot["unmatched_works"])
    }

    attention_by_library: dict[tuple[object, object], dict[str, Counter[str]]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["attention_fields"]):
        connection_name, library_name, destination, outcome, reasons, shadow = row
        reason_tuple = cast("tuple[str, ...]", reasons)
        if isinstance(shadow, str) and shadow:
            cause = shadow
            cause_class = _cause_class((shadow,))
        elif reason_tuple:
            cause = "+".join(sorted(reason_tuple))
            cause_class = _cause_class(reason_tuple)
        else:
            cause = _SAFETY_CAUSE
            cause_class = "safety"
        bucket = attention_by_library.setdefault(
            (connection_name, library_name),
            {
                "by_destination": Counter(),
                "by_cause": Counter(),
                "by_class": Counter(),
            },
        )
        bucket["by_destination"][cast("str", destination) or "unknown"] += 1
        bucket["by_cause"][cause] += 1
        bucket["by_class"][cause_class] += 1

    lane_counts: dict[tuple[object, object], dict[str, Counter[str]]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["provider_lanes"]):
        connection_name, library_name, provider, status = row
        by_provider = lane_counts.setdefault((connection_name, library_name), {})
        by_provider.setdefault(cast("str", provider) or "unknown", Counter())[
            cast("str", status) or "unknown"
        ] += 1

    agreement: dict[tuple[object, object], Counter[str]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["agreement"]):
        connection_name, library_name = row[0], row[1]
        correlations = set(cast("tuple[str, ...]", row[3]))
        authorities = cast("tuple[str, ...]", row[4])
        provider_identities = {
            correlation_identity("provider:" + authority) for authority in authorities
        }
        agreement.setdefault((connection_name, library_name), Counter())[
            _bucket(len(correlations & provider_identities))
        ] += 1

    catalogue_generation: int | None = None
    catalogue_by_library: dict[tuple[str, str], dict[str, Counter[str]]] = {}
    if catalogue_counts is not None:
        catalogue_generation = catalogue_counts[0]
        for (
            connection_name,
            library_name,
            classification,
            fmt,
            count,
        ) in catalogue_counts[1]:
            entry = catalogue_by_library.setdefault(
                (connection_name, library_name),
                {"by_classification": Counter(), "by_format": Counter()},
            )
            entry["by_classification"][classification] += count
            entry["by_format"][fmt or "none"] += count

    # Determinism is judged only where it is expected: members of one digest
    # group that also share a target-field context. A copy whose target
    # already differs is a different pipeline input, not nondeterminism.
    duplicate_buckets: dict[tuple[str, str, str], dict[str, list[str]]] = {}
    for connection_name, library_name, digest, context, results in cast(
        "list[tuple[str, str, str, str, str]]", snapshot["duplicates"]
    ):
        scope = (connection_name, library_name, digest)
        duplicate_buckets.setdefault(scope, {}).setdefault(context, []).append(results)
    compared: set[tuple[str, str, str]] = set()
    divergent: set[tuple[str, str, str]] = set()
    for scope, contexts in duplicate_buckets.items():
        for members in contexts.values():
            if len(members) < 2:
                continue
            compared.add(scope)
            if len(set(members)) > 1:
                divergent.add(scope)

    libraries: list[dict[str, object]] = []
    zero_lane_providers: set[str] = set()
    for connection_name, library_name in library_keys:
        key = (connection_name, library_name)
        total, attention_works = work_counts.get(key, (0, 0))
        scan = scans.get(key)
        states = work_states.get(key)
        ready = int(cast("int", states[2])) if states else 0
        blocked = int(cast("int", states[3])) if states else 0
        ledger_total = int(cast("int", states[4])) if states else 0
        first_ready = cast("int | None", states[5]) if states else None
        last_ready = cast("int | None", states[6]) if states else None
        requested_at = int(cast("int", scan[5])) if scan else None
        discovered = int(cast("int", scan[4])) if scan else None

        timing: dict[str, object] = {
            "requested_at": requested_at,
            "first_ready_seconds": None,
            "elapsed_seconds": None,
            "works_per_hour": None,
            "projected_remaining_seconds": None,
        }
        if requested_at is not None and first_ready is not None:
            timing["first_ready_seconds"] = max(0, first_ready - requested_at)
        if requested_at is not None and last_ready is not None:
            elapsed = max(0, last_ready - requested_at)
            timing["elapsed_seconds"] = elapsed
            rate = _rate(ready, elapsed)
            timing["works_per_hour"] = rate
            if rate is not None and discovered is not None and discovered > ready:
                timing["projected_remaining_seconds"] = int(
                    (discovered - ready) * 3600.0 / rate
                )

        attention = attention_by_library.get(
            key,
            {
                "by_destination": Counter(),
                "by_cause": Counter(),
                "by_class": Counter(),
            },
        )
        providers_observed = lane_counts.get(key, {})
        configured_providers = provider_connections.get(library_name, ())
        provider_rows = []
        for provider_name in configured_providers:
            statuses = providers_observed.get(provider_name, Counter())
            if not statuses:
                zero_lane_providers.add(provider_name)
            provider_rows.append(
                {
                    "provider": provider_name,
                    "lanes": dict(sorted(statuses.items())),
                    "zero_lane": not statuses,
                }
            )
        for provider_name in sorted(providers_observed):
            if provider_name not in configured_providers:
                provider_rows.append(
                    {
                        "provider": provider_name,
                        "lanes": dict(
                            sorted(providers_observed[provider_name].items())
                        ),
                        "zero_lane": False,
                    }
                )

        catalogue_entry = catalogue_by_library.get((connection_name, library_name))
        mount_name = mount_by_library.get(library_name)
        libraries.append(
            {
                "connection": connection_name,
                "library": library_name,
                "scan": {
                    "state": cast("str", scan[3]) if scan else None,
                    "discovered_total": discovered,
                    "requested_at": requested_at,
                    "updated_at": int(cast("int", scan[6])) if scan else None,
                },
                "works": {
                    "current_total": total,
                    "attention": attention_works,
                    "quiet": total - attention_works,
                    "ledger_total": ledger_total,
                    "ready": ready,
                    "blocked": blocked,
                    "absences": dict(
                        sorted(absence_counts.get(key, Counter()).items())
                    ),
                },
                "attention_fields": {
                    "total": sum(attention["by_destination"].values()),
                    "by_destination": dict(sorted(attention["by_destination"].items())),
                    "by_cause": dict(sorted(attention["by_cause"].items())),
                    "by_class": dict(sorted(attention["by_class"].items())),
                },
                "providers": provider_rows,
                "works_without_provider_candidates": unmatched.get(key, 0),
                "timing": timing,
                "scan_fidelity": {
                    "mount": mount_name,
                    "mount_files_by_extension": (
                        mount_files.get(mount_name) if mount_name else None
                    ),
                    "target_items_by_classification": (
                        dict(sorted(catalogue_entry["by_classification"].items()))
                        if catalogue_entry
                        else None
                    ),
                    "target_items_by_format": (
                        dict(sorted(catalogue_entry["by_format"].items()))
                        if catalogue_entry
                        else None
                    ),
                    "ml_ledger_works": ledger_total,
                },
                "automatic_decision_corroboration": dict(
                    sorted(agreement.get(key, Counter()).items())
                ),
            }
        )

    total_works = sum(work_counts.get(key, (0, 0))[0] for key in library_keys)
    total_attention = sum(work_counts.get(key, (0, 0))[1] for key in library_keys)
    return {
        "schema_version": COLD_START_REPORT_SCHEMA,
        "generated_at": generated_at,
        "catalogue_generation": catalogue_generation,
        "totals": {
            "works": total_works,
            "attention_works": total_attention,
            "quiet_works": total_works - total_attention,
            "quiet_fraction": (
                round((total_works - total_attention) / total_works, 4)
                if total_works
                else None
            ),
        },
        "determinism": {
            "duplicate_groups_compared": len(compared),
            "divergent_groups": len(divergent),
        },
        "zero_lane_providers": sorted(zero_lane_providers),
        "libraries": libraries,
    }


def _mount_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not name or not path:
            raise SystemExit(f"invalid --mount-root {value!r}; expected name=path")
        roots[name] = Path(path)
    return roots


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--catalogue", required=True, type=Path)
    parser.add_argument(
        "--mount-root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="remap a configured mount name to a host path for the file census",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout

    roots = _mount_roots(arguments.mount_root)
    configuration = load_configuration_file(arguments.config)
    connection = _open_read_only(arguments.database)
    try:
        snapshot = cold_start_snapshot(connection)
    finally:
        connection.close()
    counts = catalogue_classification_counts(arguments.catalogue)
    mount_files = {
        mount.name: _count_mount_files(
            roots.get(mount.name, Path(mount.container_path))
        )
        for mount in configuration.mounts
    }
    report = assemble_cold_start_report(
        configuration, snapshot, counts, mount_files, int(time.time())
    )
    output.write(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
