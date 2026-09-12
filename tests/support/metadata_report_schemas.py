"""Shared metadata-report persistence fixtures.

These build the legacy and prior on-disk schemas, the synthetic
report published against them, and the hot journal a rejected
database must be preserved beside.
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

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
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    canonical_json,
    default_target_fields,
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


def create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v41'", "= 'metadata-report-v1'"
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
    assert "= 'metadata-report-v41'" in persistence_schema._REPORTS_SQL
    statements = (
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v41'", f"= '{version}'"
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
