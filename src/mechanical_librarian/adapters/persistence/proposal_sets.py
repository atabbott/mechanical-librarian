"""The ProposalSet aggregate.

Owns the append-only `proposal_sets` table: one exact evaluated proposal per
report, admitted only after a transaction-local recheck that the report is
still current and the policy that produced it is still the active one.

The policy read is injected rather than reached for, so this module owns its
own table and depends on the metadata-policy aggregate through one narrow
callable instead of importing it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.schema import _validate_report_row
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    ProposalNotFoundError,
)
from mechanical_librarian.domain.metadata_policy import (
    ResolvedMetadataPolicy,
    human_review_destinations,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)


class SQLiteProposalSetRepository(ConnectionScoped):
    """Persist and read exact evaluated ProposalSets."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        current_resolved_metadata_policy: Callable[
            [str], ResolvedMetadataPolicy | None
        ],
    ) -> None:
        super().__init__(connection, binding)
        self.current_resolved_metadata_policy = current_resolved_metadata_policy

    @local_operation
    def publish_proposal(self, proposal_set: ProposalSet) -> str:
        """Insert one exact ProposalSet after transaction-local report recheck."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("proposal publication failed")
        document = proposal_set.to_json()
        values = (
            proposal_set.proposal_set_id,
            proposal_set.report_id,
            proposal_set.policy_version,
            document,
        )
        transaction = self._transaction("publish_proposal")
        try:
            transaction.begin()
            if self._binding is not None:
                self._binding.require_current()
            existing = self._connection.execute(
                "SELECT report_id,policy_version,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set.proposal_set_id,),
            ).fetchone()
            expected = values[1:]
            if existing is not None and existing != expected:
                raise MetadataReportError("persisted proposal identity conflicts")
            row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document "
                "FROM metadata_reports WHERE report_id=?",
                (proposal_set.report_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("proposal report is unavailable")
            report_document = _validate_report_row(row)
            report = MetadataReport.from_json(report_document)
            current = self._connection.execute(
                "SELECT report_id FROM current_metadata_reports "
                "WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                    report.file.identity,
                ),
            ).fetchone()
            policy_generation = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            current_policy = self.current_resolved_metadata_policy(
                report.target.library
            )
            policy_is_bound = current_policy is not None or (
                policy_generation is not None and policy_generation[0] > 1
            )
            if (
                report.report_id != proposal_set.report_id
                or report.target.fingerprint != proposal_set.target_fingerprint
                or current != (proposal_set.report_id,)
                or (
                    policy_is_bound
                    and (
                        current_policy is None
                        or proposal_set.resolved_policy_id != current_policy.resolved_id
                        or proposal_set.to_json()
                        != evaluate_report(
                            report,
                            current_policy.resolved_id,
                            human_review_destinations(current_policy),
                        ).to_json()
                    )
                )
            ):
                raise MetadataReportError("proposal report or policy changed")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO proposal_sets(proposal_set_id,report_id,policy_version,document) VALUES(?,?,?,?)",
                    values,
                )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return self.get_proposal(proposal_set.proposal_set_id)
        except MetadataReportError:
            transaction.rollback()
            raise
        except (sqlite3.Error, ProposalValueError, ReportValueError) as error:
            transaction.rollback()
            raise MetadataReportError("proposal publication failed") from error
        except Exception as error:
            transaction.rollback()
            raise MetadataReportError(
                "proposal publication precondition failed"
            ) from error

    @local_operation
    def get_proposal(self, proposal_set_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT proposal_set_id,report_id,policy_version,document "
                "FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set_id,),
            ).fetchone()
            if row is None:
                raise ProposalNotFoundError("persisted proposal is unavailable")
            return _validate_proposal_row(row)
        except MetadataReportError:
            raise
        except (sqlite3.Error, ProposalValueError, UnicodeError) as error:
            raise MetadataReportError("persisted proposal is invalid") from error


def _validate_proposal_row(row: tuple[object, ...]) -> str:
    if len(row) != 4:
        raise MetadataReportError("persisted proposal is invalid")
    proposal_set_id, report_id, policy_version, document = row
    if (
        type(proposal_set_id) is not str
        or policy_version != POLICY_VERSION
        or type(document) is not str
    ):
        raise MetadataReportError("persisted proposal is invalid")
    try:
        proposal_set = ProposalSet.from_json(document)
    except ProposalValueError as error:
        raise MetadataReportError("persisted proposal is invalid") from error
    if (
        proposal_set.proposal_set_id,
        proposal_set.report_id,
        proposal_set.policy_version,
    ) != (
        proposal_set_id,
        report_id,
        policy_version,
    ):
        raise MetadataReportError("persisted proposal envelope disagrees")
    return document


__all__ = ["SQLiteProposalSetRepository", "_validate_proposal_row"]
