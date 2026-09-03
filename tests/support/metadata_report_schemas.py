"""Shared metadata-report persistence fixtures.

These build the legacy and prior on-disk schemas, the synthetic
report published against them, and the hot journal a rejected
database must be preserved beside.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import mechanical_librarian.adapters.persistence.schema as persistence_schema
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
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


def create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v36'", "= 'metadata-report-v1'"
        )
    )
    connection.execute(persistence_schema._CURRENT_SQL)
    connection.execute(persistence_schema._NO_UPDATE_SQL)
    connection.execute(persistence_schema._NO_DELETE_SQL)


def create_prior_schema(
    connection: sqlite3.Connection,
    version: str = "metadata-report-v9",
    proposal_policy: str = "fixed-conservative-v6",
) -> None:
    statements = (
        persistence_schema._REPORTS_SQL.replace(
            "= 'metadata-report-v36'", f"= '{version}'"
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
