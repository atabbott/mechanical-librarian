"""Shared metadata-report persistence fixtures.

These build the legacy and prior on-disk schemas, the synthetic
report published against them, and the hot journal a rejected
database must be preserved beside.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

import mechanical_librarian.adapters.persistence.schema as persistence_schema
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderLaneDispatch,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    _provider_lane_from_data,
    canonical_json,
    default_target_fields,
    lane_dispatch_outcome,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    admit_provider_work_identity,
    work_query_ladder,
)


def synthetic_report(title: str = "Current title") -> MetadataReport:
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(claim,),
        local_reasons=("no_opening_claims",),
        providers=(ProviderObservation("catalogue", ProviderStatus.DISABLED),),
    )


def synthetic_latin_work_report() -> MetadataReport:
    """One qualified report whose dispatch and admission lineages differ."""
    local_title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    local_author = "Jos\N{LATIN SMALL LETTER E WITH ACUTE}"
    provider_title = "Cafe"
    provider_author = "Jose"
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value=provider_title,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.CREATOR,
                value=provider_author,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
                role="author",
                raw_order=1,
            ),
        ),
    )
    admission = admit_provider_work_identity(
        local_title=local_title,
        local_authors=(local_author,),
        provider_title=provider_title,
        provider_authors=(provider_author,),
        provider_work_identity="work-1",
    )
    assert admission.transformation is not None
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            (local_author,),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(local_title)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("target_metadata",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.WORK,
                dispatch_lineage=WorkDispatchLineage.from_query(
                    work_query_ladder(local_title, (local_author,))[-1]
                ),
                transformation_lineage=admission.transformation,
            ),
        ),
    )


def synthetic_reordered_work_report() -> MetadataReport:
    """One WORK replay whose complete closed author multiset is reordered."""
    local_title = "Plain Work"
    local_authors = ("Ada Example", "Bea Example")
    provider_authors = tuple(reversed(local_authors))
    candidate = ProviderCandidate(
        "edition-1",
        "work-1",
        (
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.TITLE,
                value=local_title,
                state=FieldState.VALID,
                scope=ClaimScope.WORK,
                applicability="not_applicable",
            ),
            *(
                ProviderClaim.create(
                    provider="catalogue",
                    candidate="edition-1",
                    kind=ClaimKind.CREATOR,
                    value=author,
                    state=FieldState.VALID,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    role="author",
                    raw_order=index,
                )
                for index, author in enumerate(provider_authors, start=1)
            ),
        ),
    )
    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, local_authors)
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(local_title)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("target_metadata",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                (),
                (candidate,),
                ProviderQueryKind.WORK,
                dispatch_lineage=WorkDispatchLineage.from_query(
                    work_query_ladder(local_title, local_authors)[0]
                ),
            ),
        ),
    )


def forged_latin_work_report_json() -> tuple[str, str]:
    """A self-consistent report envelope missing required Latin evidence."""
    data = synthetic_latin_work_report().to_data()
    provider = cast(dict[str, object], cast(list[object], data["providers"])[0])
    lineage = cast(dict[str, object], provider["transformation_lineage"])
    applications = cast(list[object], lineage["applications"])
    applications.pop()
    identity_data = dict(data)
    identity_data.pop("report_id")
    report_id = (
        "report_" + hashlib.sha256(canonical_json(identity_data).encode()).hexdigest()
    )
    data["report_id"] = report_id
    return report_id, canonical_json(data)


def retained_report_as_current(document: str) -> MetadataReport:
    """Read one retained v49 report as an ephemeral current-schema report.

    The retained bytes and their digest pins stay frozen, and current code
    refuses them. For a test whose downstream semantics are current ones this
    relabels an in-memory copy, gives each lane the empty exhausted-dispatch
    record v50 added, the empty recording-lookup-hint record v53 added, and
    any audio section the empty discovery-title record v51 added (the only
    ones a v49 producer could state), and readdresses it. v52 changed what an
    Audible overflow or empty rung means, v54 what an incumbent description
    admits and v55 what an audio description, language or ASIN claim means;
    none added a field. v56 added each lane's dispatch record: null on an
    ebook lane, and on an audiobook lane the outcome its status and ladder
    allow, never starved, with no ladder depth or near-miss, since a v49
    producer kept no cause or depth. v57 changed which audiobook products a
    band-corroborated lane admits and added no field. v58 changed what an
    Audiobookshelf incumbent description admits, the visible text rather than
    the stored markup, and added each description's `restorable` flag: false
    here, so no relabelled copy can authorise a restore it was never read for.
    Nothing is migrated: the copy is never written back.
    """
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(document)
    data = json.loads(document)
    assert data["schema_version"] == "metadata-report-v49"
    data["schema_version"] = "metadata-report-v59"
    for provider in data["providers"]:
        for lane in provider["lanes"]:
            lane["exhausted_dispatch"] = []
            lane["recording_lookup_hints"] = []
            lane["dispatch"] = None
            if data.get("audio") is not None:
                outcome = lane_dispatch_outcome(
                    _provider_lane_from_data(lane), starved=False
                )
                assert outcome is not None
                lane["dispatch"] = ProviderLaneDispatch(outcome).to_data()
    for field in data["target"]["fields"]:
        if field["destination"] == "description":
            field["restorable"] = False
    if data.get("audio") is not None:
        data["audio"]["discovery_titles"] = []
    data.pop("report_id")
    data["report_id"] = report_content_address(data)
    return MetadataReport.from_json(canonical_json(data))


# Every producer before #1253 counted an immaterial work alternative as an
# unresolved choice, so its own review-case field-fact trigger lacks the clause.
PRIOR_REVIEW_CASE_FIELD_FACTS_INSERT_SQL = (
    persistence_schema._REVIEW_CASE_FIELD_FACTS_INSERT_SQL.replace(
        "AND NOT EXISTS (SELECT 1 FROM json_each(result.value,'$.reasons') reason "
        "WHERE reason.value='work_alternative_immaterial') ",
        "",
    )
)


# Every producer before #1285 admitted only the three original human outcomes.
PRIOR_HUMAN_DECISIONS_SQL = persistence_schema._HUMAN_DECISIONS_SQL.replace(
    "'reject','owner_entered','withdraw')", "'reject')"
)
# Before #1285 any owner decision settled a field; now only its latest one
# does, unless it withdrew the field.
HUMAN_DECIDED_SQL = (
    " AND COALESCE((SELECT decision.outcome FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination "
    "ORDER BY decision.ordinal DESC LIMIT 1),'withdraw')='withdraw' "
)
PRIOR_HUMAN_DECIDED_SQL = (
    " AND NOT EXISTS (SELECT 1 FROM human_decisions decision "
    "WHERE decision.review_case_id=rc.review_case_id "
    "AND decision.destination=result.destination) "
)
PRIOR_ATTENTION_FACT_OBJECTS = tuple(
    (kind, name, sql.replace(HUMAN_DECIDED_SQL, PRIOR_HUMAN_DECIDED_SQL))
    for kind, name, sql in persistence_schema._ATTENTION_FACT_OBJECTS
)


# Every producer before #1407 derived no provider match state for a report.
PRIOR_REPORT_BROWSE_FACTS_SQL = persistence_schema._REPORT_BROWSE_FACTS_SQL.replace(
    ", provider_match_state TEXT NOT NULL CHECK(provider_match_state IN "
    "('matched','refused','unavailable','no_match'))",
    "",
)


def _without_match_state(sql: str, document: str) -> str:
    value = persistence_schema._report_browse_fact_values_sql(document).partition(
        " THEN 1 ELSE 0 END,"
    )[2]
    return sql.replace(" THEN 1 ELSE 0 END," + value, " THEN 1 ELSE 0 END")


PRIOR_REPORT_BROWSE_FACTS_INSERT_SQL = _without_match_state(
    persistence_schema._REPORT_BROWSE_FACTS_INSERT_SQL.replace(
        ",provider_limited,provider_match_state)", ",provider_limited)"
    ),
    "NEW.document",
)


_CURRENT_REPORT_BROWSE_FACT_PAIRS = persistence_schema._report_browse_fact_pairs


def prior_report_browse_fact_pairs(key: str) -> tuple[tuple[str, str], ...]:
    """The open-time fact comparison those producers' stores answer."""
    (derived, stored), authors = _CURRENT_REPORT_BROWSE_FACT_PAIRS(key)
    return (
        (
            _without_match_state(derived, "mr.document"),
            stored.replace(",provider_match_state FROM", " FROM"),
        ),
        authors,
    )


def create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v59'", "= 'metadata-report-v1'"
        )
    )
    assert (
        "metadata-report-v1"
        in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'metadata_reports'"
        ).fetchone()[0]
    )
    connection.execute(persistence_schema._CURRENT_SQL)
    connection.execute(persistence_schema._NO_UPDATE_SQL)
    connection.execute(persistence_schema._NO_DELETE_SQL)


def create_prior_schema(
    connection: sqlite3.Connection,
    version: str = "metadata-report-v9",
    proposal_policy: str = "fixed-conservative-v6",
) -> None:
    assert "= 'metadata-report-v59'" in persistence_schema._REPORTS_SQL
    statements = (
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v59'", f"= '{version}'"
        ),
        persistence_schema._CURRENT_SQL,
        persistence_schema._CURRENT_LINEAGE_SQL,
        persistence_schema._COVER_REFERENCES_SQL,
        persistence_schema._COVER_ARTIFACT_INDEX_SQL,
        persistence_schema._COVER_PREVIEW_INDEX_SQL,
        persistence_schema._PROPOSAL_SETS_SQL.replace(
            f"= '{POLICY_VERSION}'",
            f"= '{proposal_policy}'",
        ),
        persistence_schema._REVIEW_CASES_SQL,
        persistence_schema._REVIEW_POINTERS_SQL,
        persistence_schema._REVIEW_TRANSITIONS_SQL,
        persistence_schema._NO_UPDATE_SQL,
        persistence_schema._COVER_NO_UPDATE_SQL,
        persistence_schema._COVER_NO_DELETE_SQL,
        persistence_schema._PROPOSAL_NO_UPDATE_SQL,
        persistence_schema._PROPOSAL_NO_DELETE_SQL,
        persistence_schema._REVIEW_NO_UPDATE_SQL,
        persistence_schema._REVIEW_NO_DELETE_SQL,
        persistence_schema._REVIEW_TRANSITION_NO_UPDATE_SQL,
        persistence_schema._REVIEW_TRANSITION_NO_DELETE_SQL,
        persistence_schema._NO_DELETE_SQL,
    )
    for statement in statements:
        connection.execute(statement)
    assert (
        f"= '{version}'"
        in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'metadata_reports'"
        ).fetchone()[0]
    )


def leave_hot_journal(path: Path) -> Path:
    child = (
        "import os,sqlite3,sys\n"
        "connection=sqlite3.connect(sys.argv[1])\n"
        "connection.execute('PRAGMA journal_mode=DELETE')\n"
        "connection.execute('PRAGMA synchronous=FULL')\n"
        "connection.execute('BEGIN IMMEDIATE')\n"
        "connection.execute('PRAGMA user_version=1')\n"
        "os._exit(0)\n"
    )
    subprocess.run(
        [sys.executable, "-c", child, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    journal = Path(f"{path}-journal")
    if not journal.is_file() or journal.stat().st_size <= 512:
        raise AssertionError("synthetic hot journal was not created")
    return journal


def create_pre_currentness_schema(connection: sqlite3.Connection) -> None:
    """Build the exact #1072 predecessor, with no derived cover facts column."""
    persistence_schema._create_schema(connection)
    # #1232's attention facts read the dropped column, so they go first.
    connection.executescript(
        "".join(
            f"DROP {kind.upper()} {name};"
            for kind, name, _ in reversed(persistence_schema._ATTENTION_FACT_OBJECTS)
        )
    )
    connection.execute(
        "ALTER TABLE cover_considerations DROP COLUMN currentness_document"
    )
    connection.commit()
