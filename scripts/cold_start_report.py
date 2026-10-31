"""Aggregate-only cold-start measurement report over persisted state (#743).

A workbench instrument, deliberately not part of the installed product: run it
from a checkout against the configuration file and COPIES of (or read-only
access to) the application and catalogue databases. It writes nothing and
prints one ``cold-start-report-v5`` JSON document to stdout.

The report is aggregate-only by construction. It contains connection,
library, provider, and mount names from the operator's own configuration,
destination names, closed reason codes, file extensions, and counts — never a
title, author, filename, media path, or item identifier. The attention
predicate is not re-derived: the queries compose the same shared
``review_sql`` fragments the Attention surface executes. The offline report
cannot establish the live cover policy and replay authority, so a library with
a current cover consideration reports its attention aggregates as unavailable
instead of guessing that its cover fields are quiet. Every library also
carries ``attention_stored_basis``: the same attention read on the persisted
cover authority alone, which the offline report can establish.

Each library also carries an ``audiobook_dispatch`` census of how every
current audiobook report's provider lanes ended (#1258): counts per lane
outcome, provider class, Audible ladder depth, runtime band and near-miss
bucket, over every current audiobook including report-only ones. Beside it,
``audiobook_outcomes`` splits those reports into a recording-admitted case, a
work-only case and evidence only by its runtime reason, ``audiobook_cover_reads``
counts their Audiobookshelf cover-read states, ``dispatches`` counts every
current item's initial, retry and replayed provider dispatches with a row for
every configured provider, and ``automatic_decisions`` counts the decisions on
current cases. ``not_matched`` counts the works Library's Not matched lens
holds by reason, every one of which No attention needed counted before #1407.
An optional stage-timing file adds the retained tail's per-stage
percentiles, and an optional provider recovery database adds its job counts and
its global job-attempt and health-outcome counts.

Usage:
    uv run python scripts/cold_start_report.py \
        --config /copy/config.json \
        --database /copy/application.sqlite3 \
        --catalogue /copy/catalogue.sqlite3 \
        [--mount-root books=/host/path/to/mount ...] \
        [--stage-timings /copy/stage-timings.json] \
        [--recovery-database /copy/provider-recovery.sqlite]

``--mount-root`` remaps a configured mount name to a host path for the
extension census, because the configuration records container paths.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import stat
import sys
import time
from collections import Counter
from pathlib import Path
from typing import TextIO, cast
from urllib.parse import quote

from mechanical_librarian.adapters.operational_evidence import LocalTimingStore
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_COVER_ATTENTION_AUTHORITY_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _NOT_MATCHED_REASON_SQL,
    _REVIEW_ATTENTION_SQL,
    _REVIEW_FIELD_ATTENTION_CLEAR_SQL,
    _REVIEW_FIELD_ATTENTION_REASON_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
    _STORED_COVER_ATTENTION_AUTHORITY_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _report_provider_match_state_sql,
)
from mechanical_librarian.application.configuration import (
    AUDIOBOOK_DISPATCH_ADAPTERS,
    ApplicationConfiguration,
)
from mechanical_librarian.application.operations import OperationsError, StageTiming
from mechanical_librarian.domain.audiobook_corroboration import (
    CorroborationOutcome,
    HardcoverEditionWitness,
    hardcover_edition_witness,
    lane_band,
    local_claim_runtime_seconds,
    stated_runtime_agreement,
)
from mechanical_librarian.domain.cover_evidence import CoverSourceKind
from mechanical_librarian.domain.failure_diagnostics import FailureCategory
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_PROVIDER_AUTHORITIES,
    AudioFacts,
    MetadataReport,
    ProviderLaneDispatch,
    ProviderQueryKind,
    ProviderStatus,
    provider_dispatch_class,
    work_discovery_lane_payloads,
)
from mechanical_librarian.domain.proposal_set import (
    AudiobookReviewScope,
    audiobook_review_scope,
)
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity
from mechanical_librarian.infrastructure.configuration import load_configuration_file

COLD_START_REPORT_SCHEMA = "cold-start-report-v5"

_ATTENTION_FIELD_ROWS_SQL = (
    "SELECT c.connection_name,c.library_name,"
    "result.destination,"
    "json_extract(canonical_result.value,'$.outcome'),"
    "json_extract(canonical_result.value,'$.reasons'),"
    "(SELECT json_extract(outcome.value,'$.reason_code') "
    "FROM shadow_decision_outcomes shadow,"
    "json_each(shadow.document,'$.outcomes') outcome "
    "WHERE shadow.review_case_id=rc.review_case_id "
    "AND json_extract(outcome.value,'$.destination')="
    "result.destination "
    "AND json_extract(outcome.value,'$.disposition')='abstention' LIMIT 1) "
    + _CURRENT_REVIEW_FROM_SQL
    + " JOIN review_case_field_facts result "
    "ON result.review_case_id=rc.review_case_id,"
    "json_each(rc.document,'$.results') canonical_result WHERE "
    "json_extract(canonical_result.value,'$.destination')=result.destination AND "
    + _REVIEW_FIELD_ATTENTION_SQL
)
# The same field and work attention read on the persisted cover authority alone,
# which needs no live policy or replay reference, so it is available for every
# library: a cover field counts when its stored consideration is manually ready
# and agrees with its case and report (#1299).
_STORED_ATTENTION_FIELD_ROWS_SQL = _ATTENTION_FIELD_ROWS_SQL.replace(
    _REVIEW_FIELD_ATTENTION_SQL,
    _REVIEW_FIELD_ATTENTION_REASON_SQL
    + " AND "
    + _STORED_COVER_ATTENTION_AUTHORITY_SQL
    + _REVIEW_FIELD_ATTENTION_CLEAR_SQL,
)
_STORED_ATTENTION_WORKS_SQL = (
    "SELECT ml.connection_name,ml.library_name,"
    "SUM(CASE WHEN c.review_case_id IS NOT NULL AND "
    + _REVIEW_ATTENTION_SQL.replace(
        _CURRENT_COVER_ATTENTION_AUTHORITY_SQL, _STORED_COVER_ATTENTION_AUTHORITY_SQL
    )
    + " THEN 1 ELSE 0 END) "
    + _CURRENT_LIBRARY_FROM_SQL
    + " WHERE ml.outcome!='report_only' "
    "GROUP BY ml.connection_name,ml.library_name"
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
_CURRENT_COVER_POINTER_LIBRARIES_SQL = (
    "SELECT DISTINCT ml.connection_name,ml.library_name "
    + _CURRENT_LIBRARY_FROM_SQL
    + " JOIN current_cover_considerations current_cover "
    "ON current_cover.review_case_id=rc.review_case_id "
    "WHERE ml.outcome!='report_only'"
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
# The provider rows' join without their report-only exclusion: discovery
# reaches every audiobook, so its dispatch census counts every current one.
_AUDIO_REPORTS_SQL = (
    "SELECT ml.connection_name,ml.library_name,mr.document,"
    "ml.review_case_id IS NOT NULL "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "WHERE json_extract(mr.document,'$.audio') IS NOT NULL"
)
# Every current item, report-only ones included: each one spent its dispatches.
_DISPATCH_ROWS_SQL = (
    "SELECT ml.connection_name,ml.library_name,"
    "json_extract(mr.document,'$.providers'),"
    "json_extract(mr.document,'$.audio') IS NOT NULL "
    "FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id"
)
_RECOVERY_JOBS_SQL = (
    "SELECT connection,adapter,target_connection,library,state,count(*) "
    "FROM provider_recovery_jobs GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5"
)
# The event table has no library column, so recovery events are global.
_RECOVERY_EVENT_ROWS_SQL = (
    "SELECT connection,adapter,outcome,request_count_status,count(*),"
    "COALESCE(sum(physical_requests),0),max(occurred_at) "
    "FROM provider_recovery_events WHERE job_attempt IS NOT NULL "
    "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4"
)
_RECOVERY_HEALTH_OUTCOME_SQL = (
    "SELECT connection,adapter,outcome,count(*) FROM provider_recovery_events "
    "WHERE job_attempt IS NULL AND outcome IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3"
)
_RECOVERY_LAST_EVENT_SQL = "SELECT max(occurred_at) FROM provider_recovery_events"
_AUTOMATIC_DECISION_COUNTS_SQL = (
    "SELECT ml.connection_name,ml.library_name,decision.destination,"
    "json_extract(decision.document,'$.outcome'),count(*) "
    "FROM automatic_decisions decision "
    "JOIN current_review_packages ml ON ml.review_case_id=decision.review_case_id "
    "GROUP BY 1,2,3,4"
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
# #1407: the works Library's Not matched lens holds, by reason, under the
# lens's own predicate. The match state is derived from each current report
# rather than read from its stored browse fact, so a snapshot that predates the
# fact still answers.
_NOT_MATCHED_WORKS_SQL = (
    "SELECT connection_name,library_name,reason,count(*) FROM ("
    "SELECT ml.connection_name,ml.library_name,"
    + _NOT_MATCHED_REASON_SQL
    + " AS reason FROM current_review_packages ml "
    "JOIN (SELECT report_id,"
    + _report_provider_match_state_sql("document")
    + " AS provider_match_state FROM metadata_reports) facts "
    "ON facts.report_id=ml.report_id WHERE ml.outcome<>'tombstoned') "
    "WHERE reason IS NOT NULL GROUP BY 1,2,3"
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
        "audiobook_cover_withheld",
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
# Upper bounds, inclusive, of the near-miss magnitude buckets past the loose band.
_NEAR_MISS_RANGES = ((900, "600-900"), (1800, "900-1800"), (3600, "1800-3600"))
_DISPATCH_RANGES = (
    (0, "0"),
    (1, "1"),
    (2, "2"),
    (5, "3-5"),
    (10, "6-10"),
    (20, "11-20"),
)
_EVIDENCE_ONLY_REASONS = (
    "no_retained_recording_candidate",
    "tight_product_refused_by_title",
    "no_comparable_runtime",
    "runtime_contradiction",
    "runtime_near_miss_only_or_mixed",
)
_MAX_MOUNT_WALK_FILES = 1_000_000


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    connection = sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)
    connection.create_function(
        "ml_cover_consideration_current", 2, lambda _document, _replay: 0
    )
    return connection


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


def _rows(
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    return [tuple(row) for row in connection.execute(sql, parameters).fetchall()]


def _attention_field_rows(
    connection: sqlite3.Connection,
    parameters: tuple[object, ...] = (),
    *,
    sql: str = _ATTENTION_FIELD_ROWS_SQL,
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for row in connection.execute(sql, parameters).fetchall():
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


def _evidence_only_reason(bands: frozenset[str], refused_tight: bool = False) -> str:
    """Name why an unadmitted, work-quiet audiobook stayed evidence only.

    `bands` holds the best runtime band of each audiobook-authority lane that
    retained a candidate, so an empty set means no lane retained one. With no
    composed local runtime every band is `unstated`. A tight band cannot reach
    here under current admission, which admits it, so it falls in the last
    reason with every other mix.

    Every band `unstated` is mostly an identifier lane whose products work
    admission refused and cut to their ASIN (#1257). `refused_tight` says one
    of the refused products the report retains (#1307) stated a runtime inside
    the tight band: that is a tight product refused by title, and only the
    rest have no comparable runtime.
    """
    if not bands:
        return "no_retained_recording_candidate"
    if bands == {"unstated"}:
        return (
            "tight_product_refused_by_title"
            if refused_tight
            else "no_comparable_runtime"
        )
    if bands == {"outside"}:
        return "runtime_contradiction"
    return "runtime_near_miss_only_or_mixed"


def _audiobook_rows(
    connection: sqlite3.Connection,
) -> list[tuple[object, ...]]:
    """One row per current audiobook report: its dispatch, outcome and cover.

    Each provider is (provider, class, lanes, refused) and each lane is (lane
    kind, outcome, rungs, near-miss, band); `refused` marks a no-match provider
    stating a work-identity refusal. The outcome is the report's review scope
    or its evidence-only reason, the reason is named for every report a
    recording did not admit, and each Audiobookshelf cover read is (state,
    timeout scope). The last member is what Hardcover's audio editions say
    about the accepted recording (#962): closed vocabulary, flags and integers
    only.
    """
    rows: list[tuple[object, ...]] = []
    for connection_name, library_name, document, has_case in connection.execute(
        _AUDIO_REPORTS_SQL
    ).fetchall():
        report = MetadataReport.from_json(document)
        audio = cast("AudioFacts", report.audio)
        alternatives = {
            payload.identity
            for payload in work_discovery_lane_payloads(
                report.target.fields, report.local_claims, audio.discovery_titles
            )
            if payload.title_basis is not None
        }
        local = local_claim_runtime_seconds(report.local_claims)
        providers = []
        for provider in report.providers:
            lanes = []
            for lane in provider.lanes:
                dispatch = cast("ProviderLaneDispatch", lane.dispatch)
                lanes.append(
                    (
                        "work_alternative"
                        if lane.kind is ProviderQueryKind.WORK
                        and lane.identity in alternatives
                        else lane.kind.value,
                        dispatch.outcome.value,
                        dispatch.rungs,
                        dispatch.near_miss_seconds,
                        lane_band(provider, lane, local),
                    )
                )
            providers.append(
                (
                    provider.provider,
                    provider_dispatch_class(provider).value,
                    tuple(lanes),
                    provider.status is ProviderStatus.NO_MATCH
                    and any(
                        value.category is FailureCategory.WORK_IDENTITY_REFUSED
                        for value in provider.diagnostics
                    ),
                )
            )
        scope = audiobook_review_scope(report)
        reason = (
            None
            if scope is AudiobookReviewScope.RECORDING
            else _evidence_only_reason(
                frozenset(
                    lane_band(provider, lane, local)
                    for provider in report.providers
                    if provider.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
                    for lane in provider.lanes
                )
                - {"none"},
                local is not None
                and any(
                    stated_runtime_agreement(local, product).outcome
                    is CorroborationOutcome.CORROBORATED
                    for provider in report.providers
                    if provider.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
                    for product in provider.refused_products
                ),
            )
        )
        outcome = (
            "recording_admitted"
            if scope is AudiobookReviewScope.RECORDING
            else "work_only"
            if scope is AudiobookReviewScope.WORK
            else reason
        )
        cover_reads = tuple(
            (source.state.value, source.timeout_scope)
            for source in report.cover_sources
            if source.kind is CoverSourceKind.ABS
        )
        rows.append(
            (
                connection_name,
                library_name,
                tuple(providers),
                outcome,
                cover_reads,
                reason,
                bool(has_case),
                hardcover_edition_witness(report).value,
            )
        )
    return rows


def _dispatch_rows(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """One row per current item: (connection, library, dispatches, audio).

    Each provider is (provider, initial dispatches, retry dispatches, replays,
    starved), read from its persisted status and request accounting. A
    provider starved when the deadline stopped every request it would have
    sent on an audiobook, which an empty answer never is.
    """
    rows: list[tuple[object, ...]] = []
    for connection_name, library_name, providers, audio in connection.execute(
        _DISPATCH_ROWS_SQL
    ).fetchall():
        rows.append(
            (
                connection_name,
                library_name,
                tuple(
                    (
                        provider["provider"],
                        accounting["initial_dispatches"],
                        accounting["retry_dispatches"],
                        accounting["replays"],
                        bool(audio)
                        and provider["status"] == "incomplete"
                        and accounting["initial_dispatches"] == 0
                        and accounting["undispatched_incomplete"] > 0,
                    )
                    for provider in json.loads(providers)
                    for accounting in (provider["request_accounting"],)
                ),
                bool(audio),
            )
        )
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
            "libraries": _rows(connection, _LIBRARY_WORK_COUNTS_SQL, (None, None)),
            "attention_fields": _attention_field_rows(connection, (None, None)),
            "stored_attention_fields": _attention_field_rows(
                connection, sql=_STORED_ATTENTION_FIELD_ROWS_SQL
            ),
            "stored_attention_works": _rows(connection, _STORED_ATTENTION_WORKS_SQL),
            "automatic_decisions": _rows(connection, _AUTOMATIC_DECISION_COUNTS_SQL),
            "current_cover_pointer_libraries": _rows(
                connection, _CURRENT_COVER_POINTER_LIBRARIES_SQL
            ),
            "provider_lanes": _provider_lane_rows(connection),
            "unmatched_works": _rows(connection, _UNMATCHED_WORKS_SQL),
            "not_matched_works": _rows(connection, _NOT_MATCHED_WORKS_SQL),
            "scans": _rows(connection, _SCAN_ROWS_SQL),
            "work_states": _rows(connection, _WORK_STATE_COUNTS_SQL),
            "absences": _rows(connection, _ABSENCE_COUNTS_SQL),
            "duplicates": _duplicate_groups(connection),
            "agreement": _agreement_rows(connection),
            "audiobooks": _audiobook_rows(connection),
            "dispatches": _dispatch_rows(connection),
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


def _not_matched(reasons: Counter[str]) -> dict[str, object]:
    """The Not matched lens's census for one library (#1407).

    Before #1407 every one of these works was counted under No attention
    needed, so the whole lens is what moved out of it.
    """
    total = sum(reasons.values())
    return {
        "total": total,
        "by_reason": {
            reason: reasons.get(reason, 0)
            for reason in ("refused", "unavailable", "no_match")
        },
        "moved_from_no_attention_needed": total,
    }


def _bucket(count: int) -> str:
    if count >= 3:
        return "3_plus"
    return str(count)


def _near_miss_bucket(value: int) -> tuple[str, str]:
    magnitude = abs(value)
    return (
        "+" if value > 0 else "-",
        next(
            (name for bound, name in _NEAR_MISS_RANGES if magnitude <= bound), "3600+"
        ),
    )


def _audiobook_dispatch(rows: list[tuple[object, ...]]) -> dict[str, object]:
    """Count one library's audiobook dispatch facts into the census block."""
    lanes: Counter[tuple[str, str, str]] = Counter()
    classes: Counter[tuple[str, str]] = Counter()
    rungs: Counter[tuple[str, str, int]] = Counter()
    bands: Counter[tuple[str, str, str]] = Counter()
    misses: Counter[tuple[str, str]] = Counter()
    refused: Counter[str] = Counter()
    for row in rows:
        providers = cast(
            "tuple[tuple[str, str, tuple[tuple[str, str, int | None, int | None, str], ...], bool], ...]",
            row[2],
        )
        for provider, dispatch_class, provider_lanes, refusal in providers:
            classes[(provider, dispatch_class)] += 1
            refused[provider] += refusal
            for kind, outcome, depth, miss, band in provider_lanes:
                lanes[(provider, kind, outcome)] += 1
                bands[(provider, kind, band)] += 1
                if depth is not None:
                    rungs[(kind, outcome, depth)] += 1
                if miss is not None:
                    misses[_near_miss_bucket(miss)] += 1
    return {
        "reports": len(rows),
        "lanes": [
            {"provider": provider, "lane": kind, "outcome": outcome, "count": count}
            for (provider, kind, outcome), count in sorted(lanes.items())
        ],
        "providers": [
            {"provider": provider, "class": value, "items": count}
            for (provider, value), count in sorted(classes.items())
        ],
        "audible_rungs": [
            {"lane": kind, "outcome": outcome, "rungs": depth, "count": count}
            for (kind, outcome, depth), count in sorted(rungs.items())
        ],
        "band": [
            {"provider": provider, "lane": kind, "band": band, "count": count}
            for (provider, kind, band), count in sorted(bands.items())
        ],
        # A no-match provider that stated a work-identity refusal, whether or
        # not its refusing lane walked its whole ladder.
        "refusal_diagnostic": {
            "items": sum(
                any(
                    provider[3]
                    for provider in cast("tuple[tuple[object, ...]]", row[2])
                )
                for row in rows
            ),
            "providers": [
                {"provider": provider, "items": count}
                for provider, count in sorted(refused.items())
                if count
            ],
        },
        "near_miss": {
            "items": sum(misses.values()),
            "buckets": [
                {"sign": sign, "range": name, "count": count}
                for (sign, name), count in sorted(misses.items())
            ],
        },
    }


def _audiobook_outcomes(rows: list[tuple[object, ...]]) -> dict[str, object]:
    """Split one library's audiobook reports by the case their evidence earns.

    Every report a recording did not admit is also named by its runtime
    reason, so a work-only case counts beside evidence only by the same basis.
    """
    counts = Counter(cast("str", row[3]) for row in rows)
    reasons = Counter(row[5] for row in rows)
    work_only = Counter(row[5] for row in rows if row[3] == "work_only")
    return {
        "reports": len(rows),
        "recording_admitted": counts["recording_admitted"],
        "work_only": counts["work_only"],
        "work_only_cases": sum(1 for row in rows if row[3] == "work_only" and row[6]),
        "evidence_only": {
            "total": sum(counts[reason] for reason in _EVIDENCE_ONLY_REASONS),
            **{reason: counts[reason] for reason in _EVIDENCE_ONLY_REASONS},
        },
        "not_recording_admitted": {
            "total": sum(reasons[reason] for reason in _EVIDENCE_ONLY_REASONS),
            **{reason: reasons[reason] for reason in _EVIDENCE_ONLY_REASONS},
        },
        "work_only_by_reason": {
            reason: work_only[reason] for reason in _EVIDENCE_ONLY_REASONS
        },
        "hardcover_witness": {
            witness.value: sum(1 for row in rows if row[7] == witness.value)
            for witness in HardcoverEditionWitness
        },
    }


def _audiobook_cover_reads(rows: list[tuple[object, ...]]) -> dict[str, object]:
    """Count one library's Audiobookshelf cover reads by state and timeout scope."""
    states: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for state, scope in cast("tuple[tuple[str, str | None], ...]", row[4]):
            states[(state, scope or "")] += 1
    return {
        "reports": len(rows),
        "without_read": sum(1 for row in rows if not row[4]),
        "states": [
            {"state": state, "timeout_scope": scope or None, "count": count}
            for (state, scope), count in sorted(states.items())
        ],
    }


def _dispatches(
    rows: list[tuple[object, ...]],
    dispatchable: tuple[str, ...],
    configured: tuple[str, ...],
) -> dict[str, object]:
    """Count one library's initial, retry and replayed dispatches per item.

    Every configured provider has a row, zeros included, beside every provider
    a report names, split by medium. `dispatchable` names the library's
    enabled providers in the audiobook dispatch set; an audiobook that sent any
    of them no initial request is counted against it as never dispatched, and
    as starved where the deadline stopped every request it would have sent.
    """
    sent_by: dict[tuple[str, bool], Counter[str]] = {}
    per_item: Counter[str] = Counter()
    never: Counter[str] = Counter()
    starved: Counter[str] = Counter()
    never_items = starved_items = 0
    for row in rows:
        spent = 0
        sent: Counter[str] = Counter()
        hungry: set[str] = set()
        for provider, first, again, replayed, starving in cast(
            "tuple[tuple[str, int, int, int, bool], ...]", row[2]
        ):
            sent_by.setdefault((provider, bool(row[3])), Counter()).update(
                initial=first, retry=again, replays=replayed
            )
            sent[provider] += first
            spent += first + again
            if starving:
                hungry.add(provider)
        if row[3]:
            missed = [name for name in dispatchable if not sent[name]]
            never.update(missed)
            never_items += bool(missed)
            starved.update(hungry)
            starved_items += bool(hungry)
        per_item[
            next((name for bound, name in _DISPATCH_RANGES if spent <= bound), "21+")
        ] += 1
    total = Counter[str]()
    for counts in sent_by.values():
        total.update(counts)
    spent_total = total["initial"] + total["retry"]
    return {
        "items": len(rows),
        "initial": total["initial"],
        "retry": total["retry"],
        "replays": total["replays"],
        "mean_per_item": round(spent_total / len(rows), 2) if rows else None,
        "mean_per_item_with_replays": (
            round((spent_total + total["replays"]) / len(rows), 2) if rows else None
        ),
        "per_item": [
            {"range": name, "items": per_item[name]}
            for name in (*(name for _, name in _DISPATCH_RANGES), "21+")
        ],
        "providers": [
            {
                "provider": provider,
                "configured": provider in configured,
                **{
                    medium: {
                        key: sent_by.get((provider, audio), Counter())[key]
                        for key in ("initial", "retry", "replays")
                    }
                    for medium, audio in (("audiobook", True), ("ebook", False))
                },
            }
            for provider in sorted(set(configured) | {name for name, _ in sent_by})
        ],
        "never_dispatched": {
            "audiobook_items": sum(1 for row in rows if row[3]),
            "items": never_items,
            "starved_items": starved_items,
            "providers": [
                {
                    "provider": provider,
                    "items": never[provider],
                    "starved_items": starved[provider],
                }
                for provider in sorted(dispatchable)
            ],
        },
    }


def _recovery_jobs(rows: list[tuple[object, ...]]) -> dict[str, object]:
    """Count provider recovery jobs by provider, library and state."""
    return {
        "jobs": sum(int(cast("int", row[5])) for row in rows),
        "groups": [
            {
                "provider": provider,
                "adapter": adapter,
                "connection": target,
                "library": library,
                "state": state,
                "count": count,
            }
            for provider, adapter, target, library, state, count in rows
        ],
    }


def _recovery_class(outcome: str) -> str:
    """Name an attempt outcome's class; `completed` and `no_match` published."""
    if outcome in {"rate_limited", "burst_throttled", "quota_exhausted"}:
        return "throttle"
    return outcome if outcome in {"no_match", "completed"} else "source_failure"


def _recovery_events(
    recovery: dict[str, list[tuple[object, ...]]],
) -> dict[str, object]:
    """Count recovery job attempts and health outcomes per provider.

    A `completed` or `no_match` attempt publishes a successor report whose
    request accounting already holds its requests; a `throttle` or
    `source_failure` attempt publishes none. `physical_requests.by_class`
    keeps them apart so a reader adds only the unpublished ones.
    """
    classes = ("throttle", "source_failure", "no_match", "completed")
    providers: dict[tuple[str, str], tuple[Counter[str], ...]] = {}
    for provider, adapter, outcome, status, count, requests, _ in recovery["events"]:
        outcomes, kinds, physical, statuses = providers.setdefault(
            (cast("str", provider), cast("str", adapter)),
            (Counter(), Counter(), Counter(), Counter()),
        )
        kind = _recovery_class(cast("str", outcome))
        outcomes[cast("str", outcome)] += cast("int", count)
        kinds[kind] += cast("int", count)
        physical[kind] += cast("int", requests)
        statuses[cast("str", status)] += cast("int", count)
    return {
        "last_occurred_at": recovery["last"][0][0],
        "last_job_attempt_at": max(
            (cast("int", row[6]) for row in recovery["events"]), default=None
        ),
        "providers": [
            {
                "provider": provider,
                "adapter": adapter,
                "job_attempts": kinds.total(),
                "physical_requests": {
                    "total": physical.total(),
                    "by_class": {kind: physical[kind] for kind in classes},
                },
                "request_count_status": dict(sorted(statuses.items())),
                "outcomes": dict(sorted(outcomes.items())),
                "classes": {kind: kinds[kind] for kind in classes},
            }
            for (provider, adapter), (outcomes, kinds, physical, statuses) in sorted(
                providers.items()
            )
        ],
        "health_outcomes": [
            {
                "provider": provider,
                "adapter": adapter,
                "outcome": outcome,
                "count": count,
            }
            for provider, adapter, outcome, count in recovery["health"]
        ],
    }


def _attention_counts(
    rows: list[tuple[object, ...]],
) -> dict[tuple[object, object], dict[str, object]]:
    """Count attention field rows per library by destination, cause and class."""
    buckets: dict[tuple[object, object], dict[str, Counter[str]]] = {}
    for connection_name, library_name, destination, _, reasons, shadow in rows:
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
        bucket = buckets.setdefault(
            (connection_name, library_name),
            {"by_destination": Counter(), "by_cause": Counter(), "by_class": Counter()},
        )
        bucket["by_destination"][cast("str", destination) or "unknown"] += 1
        bucket["by_cause"][cause] += 1
        bucket["by_class"][cause_class] += 1
    return {
        key: {
            "total": sum(bucket["by_destination"].values()),
            **{name: dict(sorted(counts.items())) for name, counts in bucket.items()},
        }
        for key, bucket in buckets.items()
    }


_NO_ATTENTION = {"total": 0, "by_destination": {}, "by_cause": {}, "by_class": {}}


def _stage_timings(values: tuple[StageTiming, ...]) -> dict[str, object]:
    """Nearest-rank percentiles per stage over the retained timing tail."""
    durations: dict[tuple[str, str], list[int]] = {}
    for value in values:
        durations.setdefault((value.stage, value.wait_class), []).append(
            value.duration_ms
        )

    def rank(ordered: list[int], quantile: float) -> int:
        return ordered[math.ceil(len(ordered) * quantile) - 1]

    stages = []
    for (stage, wait_class), recorded in sorted(durations.items()):
        ordered = sorted(recorded)
        stages.append(
            {
                "stage": stage,
                "wait_class": wait_class,
                "count": len(ordered),
                "p50": rank(ordered, 0.5),
                "p95": rank(ordered, 0.95),
                "max": ordered[-1],
            }
        )
    return {
        "basis": "retained_tail",
        "records": len(values),
        "first_recorded_at": min((value.recorded_at for value in values), default=None),
        "last_recorded_at": max((value.recorded_at for value in values), default=None),
        "stages": stages,
    }


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
    stage_timings: tuple[StageTiming, ...] | None = None,
    recovery: dict[str, list[tuple[object, ...]]] | None = None,
) -> dict[str, object]:
    """Build the ``cold-start-report-v5`` document from persisted aggregates.

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
    dispatchable_providers = {
        provider.name
        for provider in configuration.providers
        if provider.enabled and provider.adapter in AUDIOBOOK_DISPATCH_ADAPTERS
    }
    mount_by_library = {
        library.name: library.mount for library in configuration.libraries
    }
    unavailable_attention = {
        (row[0], row[1])
        for row in cast(
            "list[tuple[object, ...]]",
            snapshot["current_cover_pointer_libraries"],
        )
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
    not_matched: dict[tuple[object, object], Counter[str]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["not_matched_works"]):
        not_matched.setdefault((row[0], row[1]), Counter())[cast("str", row[2])] += int(
            cast("int", row[3])
        )

    attention_by_library = _attention_counts(
        cast("list[tuple[object, ...]]", snapshot["attention_fields"])
    )
    stored_rows = cast("list[tuple[object, ...]]", snapshot["stored_attention_fields"])
    stored_attention = _attention_counts(
        [row for row in stored_rows if row[2] != "cover"]
    )
    stored_covers = Counter(
        (row[0], row[1]) for row in stored_rows if row[2] == "cover"
    )
    stored_works = {
        (row[0], row[1]): int(cast("int", row[2]) or 0)
        for row in cast("list[tuple[object, ...]]", snapshot["stored_attention_works"])
    }
    decisions: dict[tuple[object, object], list[dict[str, object]]] = {}
    for row in sorted(
        cast("list[tuple[object, ...]]", snapshot["automatic_decisions"]),
        key=lambda row: tuple(str(value) for value in row),
    ):
        decisions.setdefault((row[0], row[1]), []).append(
            {"destination": row[2], "outcome": row[3], "count": row[4]}
        )

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

    audiobook_rows: dict[tuple[object, object], list[tuple[object, ...]]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["audiobooks"]):
        audiobook_rows.setdefault((row[0], row[1]), []).append(row)
    dispatch_rows: dict[tuple[object, object], list[tuple[object, ...]]] = {}
    for row in cast("list[tuple[object, ...]]", snapshot["dispatches"]):
        dispatch_rows.setdefault((row[0], row[1]), []).append(row)

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
        attention_unavailable = key in unavailable_attention
        attention_unavailable_reason = (
            "current_cover_authority_unavailable" if attention_unavailable else None
        )
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
                    "attention": None if attention_unavailable else attention_works,
                    "quiet": None if attention_unavailable else total - attention_works,
                    "ledger_total": ledger_total,
                    "ready": ready,
                    "blocked": blocked,
                    "absences": dict(
                        sorted(absence_counts.get(key, Counter()).items())
                    ),
                },
                "attention_fields": None
                if attention_unavailable
                else attention_by_library.get(key, _NO_ATTENTION),
                "attention_unavailable_reason": attention_unavailable_reason,
                "attention_stored_basis": {
                    "basis": "stored_cover_authority",
                    "non_cover_fields": stored_attention.get(key, _NO_ATTENTION),
                    "cover_fields_with_owner_actions": stored_covers[key],
                    "works": stored_works.get(key, 0),
                },
                "providers": provider_rows,
                "works_without_provider_candidates": unmatched.get(key, 0),
                "not_matched": _not_matched(not_matched.get(key, Counter())),
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
                "automatic_decisions": decisions.get(key, []),
                "audiobook_dispatch": _audiobook_dispatch(audiobook_rows.get(key, [])),
                "audiobook_outcomes": _audiobook_outcomes(audiobook_rows.get(key, [])),
                "audiobook_cover_reads": _audiobook_cover_reads(
                    audiobook_rows.get(key, [])
                ),
                "dispatches": _dispatches(
                    dispatch_rows.get(key, []),
                    tuple(
                        name
                        for name in configured_providers
                        if name in dispatchable_providers
                    ),
                    configured_providers,
                ),
            }
        )

    total_works = sum(work_counts.get(key, (0, 0))[0] for key in library_keys)
    total_attention = sum(work_counts.get(key, (0, 0))[1] for key in library_keys)
    configured_attention_unavailable = any(
        key in unavailable_attention for key in library_keys
    )
    total_attention_unavailable_reason = (
        "current_cover_authority_unavailable"
        if configured_attention_unavailable
        else None
    )
    return {
        "schema_version": COLD_START_REPORT_SCHEMA,
        "generated_at": generated_at,
        "catalogue_generation": catalogue_generation,
        "totals": {
            "works": total_works,
            "attention_works": (
                None if configured_attention_unavailable else total_attention
            ),
            "quiet_works": (
                None
                if configured_attention_unavailable
                else total_works - total_attention
            ),
            "quiet_fraction": (
                round((total_works - total_attention) / total_works, 4)
                if total_works and not configured_attention_unavailable
                else None
            ),
            "attention_unavailable_reason": total_attention_unavailable_reason,
        },
        "determinism": {
            "duplicate_groups_compared": len(compared),
            "divergent_groups": len(divergent),
        },
        "zero_lane_providers": sorted(zero_lane_providers),
        "libraries": libraries,
        "stage_timings": (
            None if stage_timings is None else _stage_timings(stage_timings)
        ),
        "recovery_jobs": None if recovery is None else _recovery_jobs(recovery["jobs"]),
        "recovery_events": None if recovery is None else _recovery_events(recovery),
    }


def _read_stage_timings(path: Path) -> tuple[StageTiming, ...]:
    """Read one stage-timing file through the product's own parser.

    That parser answers an absent file with no records, which here would read
    as a measured empty tail, so a missing file is refused before it is asked.
    """
    if not path.is_file():
        raise SystemExit("--stage-timings names no stage-timing file")
    try:
        return LocalTimingStore(path, enabled=True).list()
    except OperationsError as error:
        raise SystemExit("--stage-timings file is unreadable") from error


def _read_recovery(path: Path) -> dict[str, list[tuple[object, ...]]]:
    """Read a provider recovery database's jobs and events in one transaction."""
    if not path.is_file():
        raise SystemExit("--recovery-database names no recovery database")
    connection = _open_read_only(path)
    try:
        connection.execute("BEGIN")
        try:
            return {
                name: _rows(connection, sql)
                for name, sql in (
                    ("jobs", _RECOVERY_JOBS_SQL),
                    ("events", _RECOVERY_EVENT_ROWS_SQL),
                    ("health", _RECOVERY_HEALTH_OUTCOME_SQL),
                    ("last", _RECOVERY_LAST_EVENT_SQL),
                )
            }
        finally:
            connection.execute("COMMIT")
    except sqlite3.Error as error:
        raise SystemExit("--recovery-database is unreadable") from error
    finally:
        connection.close()


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
    parser.add_argument(
        "--stage-timings",
        type=Path,
        metavar="PATH",
        help="a stage-timings-v1 file whose retained tail is summarized",
    )
    parser.add_argument(
        "--recovery-database",
        type=Path,
        metavar="PATH",
        help="a provider recovery database whose jobs and events are summarized",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout

    roots = _mount_roots(arguments.mount_root)
    timings = (
        None
        if arguments.stage_timings is None
        else _read_stage_timings(arguments.stage_timings)
    )
    recovery = (
        None
        if arguments.recovery_database is None
        else _read_recovery(arguments.recovery_database)
    )
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
        configuration,
        snapshot,
        counts,
        mount_files,
        int(time.time()),
        timings,
        recovery,
    )
    output.write(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
