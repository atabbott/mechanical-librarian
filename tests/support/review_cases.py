"""Shared review-case report builder.

Seven test modules published the same synthetic report through this
builder by importing it from another module's tests. It lives here so a
cohort can build a review case without importing a sibling cohort.
"""

import hashlib
import sqlite3

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)


def _report(
    title: str = "Title",
    content_digest: str = "a",
    file_identity: str = "b",
    *,
    actionable: bool = True,
) -> MetadataReport:
    claims: tuple[LocalClaim, ...] = ()
    reasons = ("no_opening_claims",)
    if actionable:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Exact evaluator candidate",
                canonical="Exact evaluator candidate",
                excerpt="Exact evaluator candidate",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        )
        reasons = ("opening_matter_title",)
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(title),
        ),
        file=FileFacts(0, content_digest * 64, file_identity * 64),
        local_claims=claims,
        local_reasons=reasons,
        providers=(),
    )


def record_terminal_work(
    connection: sqlite3.Connection, work_id: str, updated_at: int
) -> None:
    """End one enrolled work on the recorded terminal outcome, evidence and all.

    A completed work names the outcome it ended on, and a recorded one names
    the report its evidence is in, so a fixture that set only `state='complete'`
    describes a row the schema no longer has. Publishing one synthetic report
    beside the work is what makes the completion real (#966).
    """
    report = _report(title=f"Recorded {work_id[-8:]}", file_identity=work_id[-1])
    document = report.to_json()
    connection.execute(
        "INSERT OR IGNORE INTO metadata_reports(report_id,connection_name,library_name,"
        "item_id,file_identity,content_digest,schema_version,document_digest,document) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            report.report_id,
            report.target.connection,
            report.target.library,
            report.target.item,
            report.file.identity,
            report.file.content_digest,
            report.schema_version,
            hashlib.sha256(document.encode()).hexdigest(),
            document,
        ),
    )
    connection.execute(
        "UPDATE review_preparation_work SET stage='complete',state='complete',"
        "report_id=?,outcome='evidence_only',updated_at=? WHERE work_id=?",
        (report.report_id, updated_at, work_id),
    )
