"""The review-case aggregate.

Owns `review_cases`, the `current_review_case_lineages` pointer that names the
one live case for a target, the `current_review_packages` projection the
pipeline and the browse read through, and the `review_case_transitions`
history.

`admit_review_case` is the aggregate's only writer and it writes across six
aggregates' tables in one transaction -- the proposal set, the report lineage,
the case, its transition, the package and any owner hold it carries forward. Under the ownership rule it belongs
here because the case is what it creates; the other writes travel with it, as
`publish`'s package write and `activate_metadata_policy`'s review writes do,
until the composition root owns dispatch. It is also the method that exercises
the re-entrant scope for real: callers admit inside an open pipeline
transaction, so its scope nests on a savepoint rather than beginning one.

Two seams cross the boundary and both are injected as narrow typed callables
rather than imported, so the module graph stays acyclic and this module never
depends on a whole aggregate to reach one method:

- `current_resolved_metadata_policy` and `_policy_destinations_for` belong to
  the metadata-policy aggregate. The browse and the case envelope read the
  resolved policy to classify destinations; they do not write it.

Four review projections stay on the composing repository deliberately.
`read_current_review_case`, `read_review_library` and `read_review_attention`
fan out across the decision, cover and field-effect aggregates and hold one
snapshot open over `_consistent_review_read`; `read_staged_cover_preparation`
and `get_review_case_history` read the shadow projection the same way. They
also call `get_review_case`, `read_current_library_page` and
`read_current_attention_page` through this repository's own attributes,
and `test_review_reads_are_isolated_from_a_concurrent_decision` substitutes
exactly those three attributes to interleave a competing write. Calling the
delegating stub keeps the substitution reachable; reaching past it into this
module would silently stop the interleave from happening.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.catalogue import CatalogueReadAdmission
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.proposal_sets import (
    _validate_proposal_row,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_CURRENT_SQL,
    _ATTENTION_FACTS_JOIN_SQL,
    _AUTOMATIC_SAFETY_ATTENTION_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_CASE_COUNT_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _LATEST_PREPARATION_FAILED_SQL,
    _LIBRARY_ATTENTION_SQL,
    _NOT_MATCHED_REASON_SQL,
    _POLICY_ASK_ATTENTION_SQL,
    _REPORT_BROWSE_FACTS_JOIN_SQL,
    _REVIEW_SEARCH_SQL,
    _TERMINAL_LIBRARY_SEARCH_SQL,
    _terminal_library_sql,
)
from mechanical_librarian.adapters.persistence.schema import (
    _proposal_browse_fact_pair,
    _require_equal_facts,
    _validate_report_row,
    _validated_report_row,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    LibraryLabels,
    LibraryLensFilter,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
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
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)
from mechanical_librarian.domain.review_browse import (
    ReviewBrowseValueError,
    browse_case,
    browse_case_with_id,
    browse_report,
    require_admitted_lineage,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionResult,
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
    ReviewTransition,
    is_actionable,
)
from mechanical_librarian.domain.review_field_classification import ReviewFieldCause

# What each filtering Library lens selects from the candidate relation. A
# work no provider matched has no case and so no attention, and it is neither
# needing attention nor needing none (#1407). Nor is a work whose latest
# preparation failed for good, with or without an earlier report: its row reads
# as that failure, so it is counted as one, and the four lenses partition All.
# A decision still open on a retained case keeps it in Needs attention (#1413).
_PREPARATION_FAILED_SQL = "attention=0 AND preparation_failed=1"
_LIBRARY_LENS_CONDITIONS = {
    "needs_attention": "attention=1",
    "no_attention_needed": "attention=0 AND match_reason IS NULL "
    "AND preparation_failed=0",
    "not_matched": "attention=0 AND match_reason IS NOT NULL",
    "preparation_failed": _PREPARATION_FAILED_SQL,
}
_MATCH_REASONS = frozenset(
    {None, "refused", "unavailable", "adapter_fault", "no_match"}
)

# The exact column shape of the current_review_case_lineages pointer selected
# by _validated_review_pointer.  Nullability follows the schema below:
# review_case_id is the only nullable column of the six.
_ReviewPointer = tuple[str, str, str | None, str, int, str]


@dataclass(frozen=True)
class ReviewLibraryPage:
    """Validated Library rows and totals from one filtered population."""

    rows: tuple[tuple[str | None, str], ...]
    total: int
    attention_total: int
    # #1407: the works no provider matched, and each row's reason, None for a
    # matched, case or terminal row.
    not_matched_total: int
    match_reasons: tuple[str | None, ...]
    # #1413: the works with nothing open whose latest preparation failed.
    preparation_failed_total: int
    # Each row's case (with its identity) and report as this read validated
    # them, None for a terminal row, so a projection of the same read uses them
    # rather than parsing the same bytes again (#1202).
    envelopes: tuple[
        tuple[dict[str, object] | None, str | None, dict[str, object]] | None, ...
    ] = field(compare=False, repr=False)


@dataclass(frozen=True)
class ReviewAttentionPage:
    """Validated Attention fields and their total from one filtered population."""

    rows: tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool], ...
    ]
    total: int


class SQLiteReviewCaseRepository(ConnectionScoped):
    """Admit, read and browse the review cases bound to one target lineage."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        current_resolved_metadata_policy: Callable[
            [str], ResolvedMetadataPolicy | None
        ],
        policy_destinations_for: Callable[
            [MetadataReport, ProposalSet], dict[str, str] | None
        ],
        current_cover_authority: Callable[[], tuple[str | None, str | None]],
        record_browse_unavailable_exception: Callable[[sqlite3.Error], None],
    ) -> None:
        super().__init__(connection, binding)
        # Both belong to the metadata-policy aggregate and are read, never
        # written, from here.  They keep the names their moved call sites use.
        self.current_resolved_metadata_policy = current_resolved_metadata_policy
        self._policy_destinations_for = policy_destinations_for
        self._current_cover_authority = current_cover_authority
        self._record_browse_unavailable_exception = record_browse_unavailable_exception
        self.catalogue: CatalogueReadAdmission | None = None

    @local_operation
    def admit_review_case(self, proposal_set: ProposalSet) -> str:
        """Atomically bind one exact current ProposalSet to its target lineage."""
        if type(proposal_set) is not ProposalSet:
            raise MetadataReportError("review admission failed")
        owns_transaction = not self._connection.in_transaction
        transaction = self._transaction("admit_review_case")
        try:
            transaction.begin()
            if self._binding is not None:
                self._binding.require_current()
            proposal_row = self._connection.execute(
                "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets WHERE proposal_set_id=?",
                (proposal_set.proposal_set_id,),
            ).fetchone()
            if (
                proposal_row is None
                or _validate_proposal_row(proposal_row) != proposal_set.to_json()
            ):
                raise MetadataReportError("review proposal is unavailable")
            # Admission can bind a stored proposal again, making its browse
            # facts readable, so they are checked as open checks them (#1199).
            _require_equal_facts(
                self._connection,
                (_proposal_browse_fact_pair("=:key"),),
                parameters={"key": proposal_set.proposal_set_id},
            )
            report_row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (proposal_set.report_id,),
            ).fetchone()
            if report_row is None:
                raise MetadataReportError("review report is unavailable")
            report = MetadataReport.from_json(_validate_report_row(report_row))
            lineage = (
                report.target.connection,
                report.target.library,
                report.target.item,
            )
            generation = self._connection.execute(
                "SELECT generation FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            current_policy = self.current_resolved_metadata_policy(
                report.target.library
            )
            if (
                proposal_set.report_id != report.report_id
                or proposal_set.target_fingerprint != report.target.fingerprint
                or proposal_set.to_json()
                != evaluate_report(
                    report,
                    proposal_set.resolved_policy_id,
                    None
                    if current_policy is None
                    else human_review_destinations(current_policy),
                ).to_json()
            ):
                raise ReviewAdmissionIncompatibleError
            if (
                current_policy is not None
                or (generation is not None and generation[0] > 1)
            ) and (
                current_policy is None
                or proposal_set.resolved_policy_id != current_policy.resolved_id
            ):
                raise ReviewAdmissionStaleError
            if self._connection.execute(
                "SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?",
                lineage,
            ).fetchone() != (report.report_id,):
                raise ReviewAdmissionStaleError
            if self._connection.execute(
                "SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
                (*lineage, report.file.identity),
            ).fetchone() != (report.report_id,):
                raise ReviewAdmissionStaleError
            validated = self._validated_review_pointer(lineage)
            prior = (
                None
                if validated is None
                else (
                    validated[0],
                    validated[1],
                    validated[2],
                    validated[4],
                    validated[5],
                )
            )
            prior_case_id = None if prior is None else prior[2]
            checkpoint = 1 if prior is None else prior[3] + 1
            # The same proposal set replays its recorded admission answer,
            # unless the pointer's last actor retired the case without
            # answering: preparation tombstoning it or finding it ineligible,
            # or a policy activation retiring it for its successor. Leaving
            # Automatic reactivates the unchanged policy, so that successor is
            # this same proposal set, admitted again as its same case (#1568).
            if (
                prior is not None
                and prior[0] == proposal_set.proposal_set_id
                and self._connection.execute(
                    "SELECT actor FROM review_case_transitions WHERE transition_id=?",
                    # ``prior`` is None exactly when ``validated`` is None, so
                    # reaching here proves the pointer was found.
                    (cast(_ReviewPointer, validated)[3],),
                ).fetchone()
                not in {("review-preparation-v1",), ("policy-activation-v1",)}
            ):
                if self._binding is not None:
                    self._binding.require_current()
                transaction.commit()
                replay_status = (
                    "replayed"
                    if cast(_ReviewPointer, validated)[5] in {"admitted", "superseded"}
                    else cast(_ReviewPointer, validated)[5]
                )
                return _admission_json(
                    replay_status, cast(_ReviewPointer, validated)[2]
                )
            if not is_actionable(proposal_set):
                outcome = "retired" if prior_case_id is not None else "no_case"
                transition = ReviewTransition(
                    *lineage,
                    checkpoint,
                    proposal_set.proposal_set_id,
                    report.report_id,
                    outcome,
                    prior_case_id,
                    None,
                    "nonactionable",
                )
                self._connection.execute(
                    "INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition.transition_id,
                        *lineage,
                        checkpoint,
                        transition.actor,
                        transition.from_review_case_id,
                        None,
                        proposal_set.proposal_set_id,
                        report.report_id,
                        outcome,
                        "nonactionable",
                    ),
                )
                self._connection.execute(
                    "INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?, ?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=NULL,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (
                        *lineage,
                        proposal_set.proposal_set_id,
                        report.report_id,
                        None,
                        transition.transition_id,
                        checkpoint,
                        outcome,
                    ),
                )
                if owns_transaction:
                    self._connection.execute(
                        "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                        (
                            *lineage,
                            proposal_set.proposal_set_id,
                            report.report_id,
                            None,
                            checkpoint,
                            outcome,
                        ),
                    )
                if self._binding is not None:
                    self._binding.require_current()
                transaction.commit()
                return _admission_json(outcome, None)
            case = ReviewCase.create(proposal_set, report)
            existing = self._connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (case.review_case_id,),
            ).fetchone()
            if existing is not None and existing != (case.to_json(),):
                raise MetadataReportError("review case identity conflicts")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)",
                    (
                        case.review_case_id,
                        case.proposal_set_id,
                        case.report_id,
                        case.to_json(),
                    ),
                )
            else:
                # The work returned to this case's content, so the case starts
                # a new episode: the owner decisions recorded on it so far are
                # history and word, settle and hold nothing now (#1358).
                self._connection.execute(
                    "INSERT INTO review_case_episodes(review_case_id,decision_floor,"
                    "effect_floor) VALUES(?,COALESCE((SELECT MAX(ordinal) FROM "
                    "human_decisions WHERE review_case_id=?),0),COALESCE((SELECT "
                    "MAX(rowid) FROM field_effects),0)) ON CONFLICT(review_case_id) "
                    "DO UPDATE SET decision_floor=excluded.decision_floor,"
                    "effect_floor=excluded.effect_floor",
                    (case.review_case_id, case.review_case_id),
                )
            outcome = "superseded" if prior_case_id is not None else "admitted"
            # The owner's hold follows the work, not the case (ADR-0085): the
            # latest hold per destination across the item's cases carries
            # forward while it is active and no return is pending, keeping
            # its decision as the token "Return to automatic" names. A case
            # admitted again takes that latest state into its own row, so a
            # hold released or returning since never resurrects with it.
            for destination, active, document in self._connection.execute(
                "SELECT destination,active,document FROM (SELECT hold.destination,"
                "hold.active,hold.document,row_number() OVER (PARTITION BY "
                "hold.destination ORDER BY hold.updated_at DESC,hold.rowid DESC) "
                "position FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id IN (SELECT to_review_case_id FROM "
                "review_case_transitions WHERE connection_name=? AND "
                "library_name=? AND item_id=? AND to_review_case_id IS NOT NULL)) "
                "WHERE position=1",
                lineage,
            ).fetchall():
                hold = json.loads(document)
                if hold["review_case_id"] == case.review_case_id:
                    continue
                hold["review_case_id"] = case.review_case_id
                if not active or hold["pending_return"] is not None:
                    if existing is not None:
                        self._connection.execute(
                            "UPDATE field_effect_manual_holds SET active=?,"
                            "updated_at=?,document=? WHERE review_case_id=? "
                            "AND destination=?",
                            (
                                active,
                                hold["updated_at"],
                                canonical_effect_json(hold),
                                case.review_case_id,
                                destination,
                            ),
                        )
                    continue
                self._connection.execute(
                    "INSERT INTO field_effect_manual_holds(review_case_id,destination,"
                    "active,updated_at,document) VALUES(?,?,1,?,?) "
                    "ON CONFLICT(review_case_id,destination) DO UPDATE SET active=1,"
                    "updated_at=excluded.updated_at,document=excluded.document",
                    (
                        case.review_case_id,
                        destination,
                        hold["updated_at"],
                        canonical_effect_json(hold),
                    ),
                )
            transition = ReviewTransition(
                *lineage,
                checkpoint,
                proposal_set.proposal_set_id,
                report.report_id,
                outcome,
                prior_case_id,
                case.review_case_id,
                "actionable",
            )
            self._connection.execute(
                "INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transition.transition_id,
                    *lineage,
                    checkpoint,
                    transition.actor,
                    transition.from_review_case_id,
                    case.review_case_id,
                    proposal_set.proposal_set_id,
                    report.report_id,
                    outcome,
                    "actionable",
                ),
            )
            self._connection.execute(
                "INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                (
                    *lineage,
                    proposal_set.proposal_set_id,
                    report.report_id,
                    case.review_case_id,
                    transition.transition_id,
                    checkpoint,
                    outcome,
                ),
            )
            if owns_transaction:
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (
                        *lineage,
                        proposal_set.proposal_set_id,
                        report.report_id,
                        case.review_case_id,
                        checkpoint,
                        outcome,
                    ),
                )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return _admission_json(outcome, case.review_case_id)
        except (
            MetadataReportError,
            ReviewAdmissionIncompatibleError,
            ReviewAdmissionStaleError,
        ):
            transaction.rollback()
            raise
        except (
            sqlite3.Error,
            ProposalValueError,
            ReportValueError,
            ReviewCaseValueError,
        ) as error:
            transaction.rollback()
            raise MetadataReportError("review admission failed") from error

    @local_operation
    def review_case_exists(self, review_case_id: str) -> bool:
        try:
            return (
                self._connection.execute(
                    "SELECT 1 FROM review_cases WHERE review_case_id=?",
                    (review_case_id,),
                ).fetchone()
                is not None
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review case availability is unavailable"
            ) from error

    @local_operation
    def current_review_case_successor(self, review_case_id: str) -> str | None:
        """Read the current case for this exact persisted target lineage."""
        try:
            if re.fullmatch(r"review_case_[0-9a-f]{64}", review_case_id) is None:
                raise MetadataReportError("review case is unavailable")
            row = self._connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            if row is None or type(row[0]) is not str:
                raise MetadataReportError("review case is unavailable")
            target = ReviewCase.from_json(row[0]).target
            current = self._connection.execute(
                "SELECT lineage.review_case_id,successor.document "
                "FROM current_review_case_lineages lineage LEFT JOIN review_cases successor "
                "ON successor.review_case_id=lineage.review_case_id "
                "WHERE lineage.connection_name=? AND lineage.library_name=? AND lineage.item_id=?",
                (target["connection"], target["library"], target["item"]),
            ).fetchone()
            if current is None or current[0] is None:
                return None
            if (
                type(current[0]) is not str
                or re.fullmatch(r"review_case_[0-9a-f]{64}", current[0]) is None
                or type(current[1]) is not str
            ):
                raise MetadataReportError("review case is unavailable")
            successor = ReviewCase.from_json(current[1])
            if successor.review_case_id != current[0] or (
                successor.target["connection"],
                successor.target["library"],
                successor.target["item"],
            ) != (target["connection"], target["library"], target["item"]):
                raise MetadataReportError("review case is unavailable")
            return None if current[0] == review_case_id else current[0]
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    @local_operation
    def get_review_case(self, review_case_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT review_case_id,proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            if row is None or len(row) != 4:
                raise MetadataReportError("review case is unavailable")
            case = ReviewCase.from_json(row[3])
            if (case.review_case_id, case.proposal_set_id, case.report_id) != row[:3]:
                raise MetadataReportError("review case is invalid")
            self._validated_review_pointer(
                cast(
                    "tuple[str, str, str]",
                    (
                        case.target["connection"],
                        case.target["library"],
                        case.target["item"],
                    ),
                )
            )
            proposal_row = self._connection.execute(
                "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets WHERE proposal_set_id=?",
                (case.proposal_set_id,),
            ).fetchone()
            report_row = self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?",
                (case.report_id,),
            ).fetchone()
            if proposal_row is None or report_row is None:
                raise MetadataReportError("review case source is unavailable")
            proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
            report = _validated_report_row(report_row)
            policy_destinations = self._policy_destinations_for(report, proposal)
            if (
                proposal.proposal_set_id != case.proposal_set_id
                or proposal.report_id != case.report_id
                or proposal.target_fingerprint != case.target_fingerprint
                or proposal.to_json()
                != evaluate_report(
                    report, proposal.resolved_policy_id, policy_destinations
                ).to_json()
                or report.target.to_data() != case.target
                or report.target.fingerprint != case.target_fingerprint
                or report.file.identity != case.file_identity
                or tuple(value.to_data() for value in proposal.results) != case.results
            ):
                raise MetadataReportError("review case source disagrees")
            package = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id FROM current_review_packages WHERE connection_name=? AND library_name=? AND item_id=?",
                (
                    case.target["connection"],
                    case.target["library"],
                    case.target["item"],
                ),
            ).fetchone()
            current = package == (
                case.proposal_set_id,
                case.report_id,
                case.review_case_id,
            )
            return _canonical_json(
                {
                    "case": case.to_data(),
                    "current": current,
                    "schema_version": "review-case-get-v1",
                }
            )
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    @local_operation
    def get_review_case_report(self, review_case_id: str) -> str:
        return self.get_review_case_source_report(review_case_id).to_json()

    @local_operation
    def get_review_case_source_report(self, review_case_id: str) -> MetadataReport:
        try:
            return self._source_report_for_review_case(review_case_id)
        except (sqlite3.Error, ReviewCaseValueError, ReportValueError) as error:
            raise MetadataReportError("review case evidence is unavailable") from error

    @local_operation
    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        lens: LibraryLensFilter | None,
        library_labels: LibraryLabels = (),
    ) -> tuple[tuple[str | None, str], ...]:
        return self.read_current_library_page(
            search, limit, offset, lens, library_labels
        ).rows

    @local_operation
    def read_current_library_page(
        self,
        search: str | None,
        limit: int,
        offset: int,
        lens: LibraryLensFilter | None,
        library_labels: LibraryLabels = (),
    ) -> ReviewLibraryPage:
        try:
            query, pattern = _review_search_pattern(search)
            cover_authority = self._current_cover_authority()
            # None reads every work, the `all` lens; any other value refuses.
            if lens is None:
                condition = "1"
            elif type(lens) is str and lens in _LIBRARY_LENS_CONDITIONS:
                condition = _LIBRARY_LENS_CONDITIONS[lens]
            else:
                raise MetadataReportError("review library lens is invalid")
            attached = self.catalogue is not None and self.catalogue.active
            parameters: list[object] = [
                *(
                    [self.catalogue.fingerprint]
                    if attached and self.catalogue is not None
                    else []
                ),
                *cover_authority,
                query,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
                json.dumps(library_labels),
                pattern,
            ]
            terminal_union = (
                " UNION ALL SELECT NULL,NULL,NULL,connection_name,library_name,"
                "item_id,NULL,0,0,NULL,1,ml_casefold(display_title),'',work_id,"
                "attempt,stage,failure_root,recovery_attempt,failure_code,"
                "display_title,display_authors,display_format FROM terminal"
                + _TERMINAL_LIBRARY_SEARCH_SQL
            )
            parameters.extend(
                (
                    query,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    json.dumps(library_labels),
                    pattern,
                )
            )
            parameters.extend((limit, offset))
            rows = self._connection.execute(
                "WITH "
                + _terminal_library_sql(attached)
                + ",candidates AS MATERIALIZED (SELECT rc.review_case_id AS case_id,"
                "mr.report_id,"
                "CASE WHEN "
                "rc.proposal_set_id=ml.proposal_set_id AND rc.report_id=ml.report_id "
                "THEN rc.proposal_set_id ELSE NULL END AS proposal_set_id,"
                "ml.connection_name AS connection_name,ml.library_name AS library_name,"
                "ml.item_id AS item_id,c.outcome AS outcome,"
                "CASE WHEN "
                + _LIBRARY_ATTENTION_SQL
                + " THEN 1 ELSE 0 END AS attention,"
                # Provider completeness matters only inside the attention
                # group: the no-attention lens remains alphabetical.
                "facts.provider_limited AS provider_limited,"
                + _NOT_MATCHED_REASON_SQL
                + " AS match_reason,"
                + _LATEST_PREPARATION_FAILED_SQL
                + " AS preparation_failed,"
                "facts.title_key AS title_order,"
                "COALESCE(rc.review_case_id,'') AS case_order,"
                "NULL AS work_id,NULL AS attempt,NULL AS stage,NULL AS failure_root,"
                "NULL AS recovery_attempt,NULL AS failure_code, NULL AS display_title,"
                "NULL AS display_authors,NULL AS display_format "
                + _CURRENT_LIBRARY_FROM_SQL
                + _REPORT_BROWSE_FACTS_JOIN_SQL
                + "WHERE COALESCE(c.outcome,'')<>'tombstoned'"
                + _REVIEW_SEARCH_SQL
                + terminal_union
                + "),totals AS (SELECT count(*) AS total,COALESCE(sum(attention),0) "
                "AS attention_total,COALESCE(sum(CASE WHEN attention=0 AND "
                "match_reason IS NOT NULL THEN 1 ELSE 0 END),0) AS not_matched_total,"
                "COALESCE(sum(CASE WHEN "
                + _PREPARATION_FAILED_SQL
                + " THEN 1 ELSE 0 END),0) AS preparation_failed_total "
                "FROM candidates),page AS MATERIALIZED (SELECT "
                "1 AS present,case_id,report_id,proposal_set_id,"
                "connection_name,library_name,item_id,outcome,"
                "CASE WHEN attention=1 THEN 0 ELSE 1 END AS attention_order,"
                "CASE WHEN attention=1 AND provider_limited=1 THEN 1 ELSE 0 END "
                "AS provider_order,"
                # Failed preparations, then unmatched works, follow the
                # attention group as blocks, so the `all` lens keeps each
                # together ahead of clear works.
                "CASE WHEN "
                + _PREPARATION_FAILED_SQL
                + " THEN 0 WHEN attention=0 AND match_reason IS NOT NULL THEN 1 "
                "ELSE 2 END AS match_order,CASE WHEN attention=0 THEN match_reason END "
                "AS match_reason,title_order,case_order,work_id,attempt,stage,"
                "failure_root,recovery_attempt,failure_code,display_title,display_authors,display_format FROM candidates WHERE "
                + condition
                + " ORDER BY attention_order,provider_order,match_order,title_order,"
                "library_name,item_id,connection_name,case_order LIMIT ? OFFSET ?) "
                "SELECT rc.document,CASE WHEN page.present=1 AND page.report_id IS NULL THEN json_object("
                "'attempt',page.attempt,'connection',page.connection_name,"
                "'failure_root',page.failure_root,'item',page.item_id,'lane',page.stage,"
                "'library',page.library_name,'recovery_attempt',page.recovery_attempt,"
                "'schema_version','review-terminal-work-v1','status',page.failure_code,"
                "'work_id',page.work_id,'title',page.display_title,'authors',json(page.display_authors),"
                "'media_kind',page.display_format) ELSE mr.document END,page.proposal_set_id,"
                "page.connection_name,page.library_name,page.item_id,page.outcome, "
                "page.match_reason,COALESCE(page.present,0),totals.total,"
                "totals.attention_total,totals.not_matched_total,"
                "totals.preparation_failed_total "
                "FROM totals LEFT JOIN page ON 1 LEFT JOIN review_cases rc ON rc.review_case_id=page.case_id "
                "LEFT JOIN metadata_reports mr ON mr.report_id=page.report_id "
                "ORDER BY page.attention_order,page.provider_order,page.match_order,"
                "page.title_order,page.library_name,page.item_id,page.connection_name,"
                "page.case_order",
                tuple(parameters),
            ).fetchall()
            if not rows:
                raise MetadataReportError("review library count is invalid")
            totals = rows[0][-4:]
            total, attention_total, not_matched_total, failed_total = totals
            if (
                type(total) is not int
                or total < 0
                or type(attention_total) is not int
                or type(not_matched_total) is not int
                or not 0 <= attention_total <= total
                or type(failed_total) is not int
                or not 0 <= not_matched_total <= total - attention_total
                or not 0 <= failed_total <= total - attention_total - not_matched_total
            ):
                raise MetadataReportError("review library count is invalid")
            values: list[tuple[str | None, str]] = []
            reasons: list[str | None] = []
            envelopes: list[
                tuple[dict[str, object] | None, str | None, dict[str, object]] | None
            ] = []
            for row in rows:
                if (
                    len(row) != 13
                    or type(row[8]) is not int
                    or row[8] not in (0, 1)
                    or row[9:] != totals
                    or row[7] not in _MATCH_REASONS
                ):
                    raise MetadataReportError("review browse row is invalid")
                if row[8] == 0:
                    if len(rows) != 1 or any(value is not None for value in row[:8]):
                        raise MetadataReportError("review browse row is invalid")
                    continue
                # A reason belongs only to a work with no case, so a case or
                # terminal row that carries one disagrees with its package.
                if row[7] is not None and (row[0] is not None or row[6] is None):
                    raise MetadataReportError("review browse row disagrees")
                reasons.append(row[7])
                row = row[:7]
                if (
                    len(row) != 7
                    or type(row[1]) is not str
                    or any(type(value) is not str for value in row[3:6])
                    or (row[6] is not None and type(row[6]) is not str)
                ):
                    raise MetadataReportError("review browse row is invalid")
                if row[0] is None and row[6] is None:
                    terminal = json.loads(row[1])
                    if (
                        type(terminal) is dict
                        and terminal.get("schema_version") == "review-terminal-work-v1"
                    ):
                        values.append((None, _canonical_json(terminal)))
                        envelopes.append(None)
                        continue
                lineage = (row[3], row[4], row[5])
                report = browse_report(row[1])
                target = report["target"]
                if type(target) is not dict:
                    raise MetadataReportError("review browse row is invalid")
                if (
                    target.get("connection"),
                    target.get("library"),
                    target.get("item"),
                ) != lineage:
                    raise MetadataReportError("review browse row disagrees")
                if row[6] is None:
                    if row[0] is not None:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, row[1]))
                    envelopes.append((None, None, report))
                    continue
                if row[0] is None:
                    if row[6] not in {
                        "report_only",
                        "evidence_only",
                        "no_case",
                        "retired",
                    }:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, row[1]))
                    envelopes.append((None, None, report))
                    continue
                # The report above is this row's; only the case is still unread.
                case, case_id = browse_case_with_id(row[0])
                require_admitted_lineage(case, report, row[2])
                values.append((row[0], row[1]))
                envelopes.append((case, case_id, report))
            return ReviewLibraryPage(
                tuple(values),
                total,
                attention_total,
                not_matched_total,
                tuple(reasons),
                failed_total,
                tuple(envelopes),
            )
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            ReviewBrowseValueError,
            TypeError,
            ValueError,
        ) as error:
            if isinstance(error, sqlite3.Error):
                self._record_browse_unavailable_exception(error)
            raise MetadataReportError("review library is unavailable") from error

    @local_operation
    def count_current_review_cases(
        self,
        search: str | None,
        library_labels: LibraryLabels = (),
    ) -> tuple[int, int]:
        try:
            query, pattern = _review_search_pattern(search)
            cover_authority = self._current_cover_authority()
            row = self._connection.execute(
                _CURRENT_REVIEW_CASE_COUNT_SQL,
                (
                    *cover_authority,
                    query,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    json.dumps(library_labels),
                    pattern,
                ),
            ).fetchone()
            if (
                row is None
                or len(row) != 2
                or any(type(value) is not int or value < 0 for value in row)
                or row[1] > row[0]
            ):
                raise MetadataReportError("review library count is invalid")
            attached = self.catalogue is not None and self.catalogue.active
            terminal = self._connection.execute(
                "WITH "
                + _terminal_library_sql(attached)
                + " SELECT count(*) FROM terminal"
                + _TERMINAL_LIBRARY_SEARCH_SQL,
                (
                    *(
                        [self.catalogue.fingerprint]
                        if attached and self.catalogue is not None
                        else []
                    ),
                    query,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    json.dumps(library_labels),
                    pattern,
                ),
            ).fetchone()
            if terminal is None or type(terminal[0]) is not int or terminal[0] < 0:
                raise MetadataReportError("review library count is invalid")
            return row[0] + terminal[0], row[1]
        except (sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, sqlite3.Error):
                self._record_browse_unavailable_exception(error)
            raise MetadataReportError("review library is unavailable") from error

    @local_operation
    def list_current_attention_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool], ...
    ]:
        return self.read_current_attention_page(
            search, limit, offset, library_labels
        ).rows

    @local_operation
    def read_current_attention_page(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> ReviewAttentionPage:
        # The cause column is a `CASE` expression over
        # `_AUTOMATIC_SAFETY_ATTENTION_SQL` whose only non-null result is the
        # literal `'automatic_safety_suspended'`, so the cause is narrower than
        # the `str` the row validation below proves.
        try:
            query, pattern = _review_search_pattern(search)
            cover_authority = self._current_cover_authority()
            rows = self._connection.execute(
                "WITH search_matches AS MATERIALIZED (SELECT "
                "c.connection_name,c.library_name,c.item_id "
                + _CURRENT_REVIEW_FROM_SQL
                + _REPORT_BROWSE_FACTS_JOIN_SQL
                + "WHERE 1"
                + _REVIEW_SEARCH_SQL
                + "),candidates AS MATERIALIZED (SELECT rc.review_case_id AS review_case_id,"
                "mr.report_id AS report_id,CASE WHEN "
                "rc.proposal_set_id=c.proposal_set_id AND rc.report_id=c.report_id "
                "THEN rc.proposal_set_id ELSE NULL END AS proposal_set_id,"
                "f.destination AS destination,"
                "facts.title_key "
                "AS title_order,c.library_name AS library_name,c.item_id AS item_id "
                + _CURRENT_REVIEW_FROM_SQL
                + _REPORT_BROWSE_FACTS_JOIN_SQL
                + _ATTENTION_FACTS_JOIN_SQL
                + "WHERE (c.connection_name,c.library_name,c.item_id) IN "
                "(SELECT connection_name,library_name,item_id FROM search_matches)"
                # Only attentive fields have facts (#1232); a cover field's
                # live authority is the one term left to test per request.
                " AND "
                + _ATTENTION_FACT_CURRENT_SQL
                + "),attentive AS MATERIALIZED (SELECT review_case_id,report_id,"
                "proposal_set_id,destination,count(*) OVER (PARTITION BY review_case_id) "
                "AS attention_count,title_order,library_name,item_id "
                "FROM candidates),totals AS (SELECT count(*) AS total "
                "FROM attentive),page AS MATERIALIZED (SELECT 1 AS present,* "
                "FROM attentive" + " ORDER BY "
                "title_order,library_name,item_id,destination,review_case_id "
                "LIMIT ? OFFSET ?) "
                "SELECT rc.document,mr.document,page.proposal_set_id,page.destination,"
                "page.attention_count,"
                "(SELECT outcome.disposition FROM shadow_decision_outcomes shadow "
                "JOIN shadow_outcome_field_facts outcome "
                "ON outcome.outcome_set_id=shadow.outcome_set_id "
                "WHERE outcome.review_case_id=rc.review_case_id "
                "AND outcome.destination=result.destination "
                "ORDER BY shadow.evaluated_at DESC,shadow.outcome_set_id DESC LIMIT 1) "
                "AS disposition,"
                "(CASE WHEN "
                + _AUTOMATIC_SAFETY_ATTENTION_SQL
                + " THEN 'automatic_safety_suspended' ELSE NULL END) AS cause,"
                "CASE WHEN "
                + _POLICY_ASK_ATTENTION_SQL
                + " THEN 1 ELSE 0 END AS policy_attention,"
                "COALESCE(page.present,0),totals.total FROM totals LEFT JOIN page ON 1 "
                "LEFT JOIN review_cases rc ON rc.review_case_id=page.review_case_id "
                "LEFT JOIN metadata_reports mr ON mr.report_id=page.report_id "
                "LEFT JOIN review_case_field_facts result "
                "ON result.review_case_id=page.review_case_id AND result.destination=page.destination "
                "ORDER BY page.title_order,page.library_name,page.item_id,"
                "page.destination,page.review_case_id",
                (
                    query,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    json.dumps(library_labels),
                    pattern,
                    *cover_authority,
                    limit,
                    offset,
                ),
            ).fetchall()
            if not rows:
                raise MetadataReportError("review attention count is invalid")
            total = rows[0][-1]
            if type(total) is not int or total < 0:
                raise MetadataReportError("review attention count is invalid")
            values = []
            documents: tuple[str, str] | None = None
            envelope: tuple[dict[str, object], dict[str, object]]
            for row in rows:
                if (
                    len(row) != 10
                    or type(row[8]) is not int
                    or row[8] not in (0, 1)
                    or row[9] != total
                ):
                    raise MetadataReportError("review attention field is invalid")
                if row[8] == 0:
                    if (
                        len(rows) != 1
                        or any(value is not None for value in row[:6])
                        or row[6:8] != (None, 0)
                    ):
                        raise MetadataReportError("review attention field is invalid")
                    continue
                if type(row[0]) is not str or type(row[1]) is not str:
                    raise MetadataReportError("review attention field is invalid")
                # A work's attentive fields are adjacent rows of the same two
                # documents: read them once, and prove each row's lineage.
                if (row[0], row[1]) != documents:
                    envelope = browse_case(row[0]), browse_report(row[1])
                    documents = row[0], row[1]
                require_admitted_lineage(*envelope, row[2])
                case_document, report_document = row[0], row[1]
                if (
                    type(row[2]) is not str
                    or type(row[3]) is not str
                    or type(row[4]) is not int
                    or row[4] < 1
                    or (row[5] is not None and type(row[5]) is not str)
                    or (row[6] is not None and type(row[6]) is not str)
                    or type(row[7]) is not int
                    or row[7] not in (0, 1)
                ):
                    raise MetadataReportError("review attention field is invalid")
                values.append(
                    (
                        case_document,
                        report_document,
                        row[3],
                        row[4],
                        row[5],
                        cast("ReviewFieldCause | None", row[6]),
                        bool(row[7]),
                    )
                )
            return ReviewAttentionPage(tuple(values), total)
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            ReviewBrowseValueError,
            TypeError,
            ValueError,
        ) as error:
            if isinstance(error, sqlite3.Error):
                self._record_browse_unavailable_exception(error)
            raise MetadataReportError("review attention is unavailable") from error

    @local_operation
    def count_current_attention_fields(
        self,
        search: str | None,
        library_labels: LibraryLabels = (),
    ) -> int:
        try:
            query, pattern = _review_search_pattern(search)
            cover_authority = self._current_cover_authority()
            row = self._connection.execute(
                "SELECT count(*) "
                + _CURRENT_REVIEW_FROM_SQL
                + _REPORT_BROWSE_FACTS_JOIN_SQL
                + _ATTENTION_FACTS_JOIN_SQL
                + "WHERE "
                + _ATTENTION_FACT_CURRENT_SQL
                + _REVIEW_SEARCH_SQL,
                (
                    *cover_authority,
                    query,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    json.dumps(library_labels),
                    pattern,
                ),
            ).fetchone()
            if row is None or len(row) != 1 or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("review attention count is invalid")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
            if isinstance(error, sqlite3.Error):
                self._record_browse_unavailable_exception(error)
            raise MetadataReportError("review attention is unavailable") from error

    def _review_case_accepts_authority(self, review_case_id: str) -> bool:
        return cast(
            "tuple[object, ...] | None",
            self._connection.execute(
                "SELECT 1 FROM current_review_case_lineages staged "
                "JOIN current_review_packages published ON "
                "published.connection_name=staged.connection_name AND "
                "published.library_name=staged.library_name AND "
                "published.item_id=staged.item_id AND "
                "published.proposal_set_id=staged.proposal_set_id AND "
                "published.report_id=staged.report_id AND "
                "published.review_case_id=staged.review_case_id AND "
                "published.checkpoint=staged.checkpoint "
                "WHERE staged.review_case_id=?",
                (review_case_id,),
            ).fetchone(),
        ) == (1,)

    def _source_report_for_review_case(self, review_case_id: str) -> MetadataReport:
        row = self._connection.execute(
            "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,"
            "r.file_identity,r.content_digest,r.schema_version,"
            "r.document_digest,r.document "
            "FROM review_cases c JOIN metadata_reports r "
            "ON r.report_id=c.report_id WHERE c.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review source is unavailable")
        return _validated_report_row(row)

    def _source_pair_for_review_case(
        self, review_case_id: str
    ) -> tuple[MetadataReport, ProposalSet]:
        row = self._connection.execute(
            "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,"
            "r.file_identity,r.content_digest,r.schema_version,r.document_digest,"
            "r.document,p.proposal_set_id,p.report_id,p.policy_version,p.document "
            "FROM review_cases c JOIN metadata_reports r ON r.report_id=c.report_id "
            "JOIN proposal_sets p ON p.proposal_set_id=c.proposal_set_id "
            "WHERE c.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review source is unavailable")
        return (
            MetadataReport.from_json(_validate_report_row(row[:9])),
            ProposalSet.from_json(_validate_proposal_row(row[9:])),
        )

    def _validated_review_pointer(
        self, lineage: tuple[str, str, str]
    ) -> _ReviewPointer | None:
        pointer: _ReviewPointer | None = self._connection.execute(
            "SELECT proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?",
            lineage,
        ).fetchone()
        if pointer is None:
            return None
        proposal_id, report_id, case_id, transition_id, checkpoint, outcome = pointer
        transition = self._connection.execute(
            "SELECT connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason FROM review_case_transitions WHERE transition_id=?",
            (transition_id,),
        ).fetchone()
        if transition is None:
            raise MetadataReportError("review pointer is invalid")
        try:
            value = ReviewTransition(
                transition[0],
                transition[1],
                transition[2],
                transition[3],
                transition[7],
                transition[8],
                transition[9],
                transition[5],
                transition[6],
                transition[10],
                transition[4],
            )
        except (TypeError, ReviewCaseValueError) as error:
            raise MetadataReportError("review pointer is invalid") from error
        if (
            value.transition_id != transition_id
            or transition[:4] != (*lineage, checkpoint)
            or (value.proposal_set_id, value.report_id, value.kind)
            != (proposal_id, report_id, outcome)
            or value.to_review_case_id != case_id
        ):
            raise MetadataReportError("review pointer is invalid")
        if case_id is not None:
            case_source = self._connection.execute(
                "SELECT proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?",
                (case_id,),
            ).fetchone()
            if case_source is None:
                raise MetadataReportError("review pointer is invalid")
            try:
                case = ReviewCase.from_json(case_source[2])
            except (TypeError, ReviewCaseValueError) as error:
                raise MetadataReportError("review pointer is invalid") from error
            if (
                case_source[:2] != (proposal_id, report_id)
                or (case.review_case_id, case.proposal_set_id, case.report_id)
                != (case_id, proposal_id, report_id)
                or (
                    case.target["connection"],
                    case.target["library"],
                    case.target["item"],
                )
                != lineage
            ):
                raise MetadataReportError("review pointer is invalid")
        elif outcome not in {"no_case", "retired", "tombstoned"}:
            raise MetadataReportError("review pointer is invalid")
        return pointer

    @local_operation
    def current_review_package(
        self, connection: str, library: str, item: str
    ) -> tuple[str | None, str, str | None, int | None, str] | None:
        try:
            row = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id,checkpoint,outcome FROM current_review_packages WHERE connection_name=? AND library_name=? AND item_id=?",
                (connection, library, item),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted review package is invalid") from error
        if row is None:
            return None
        if (
            len(row) != 5
            or (row[0] is not None and type(row[0]) is not str)
            or type(row[1]) is not str
            or (row[2] is not None and type(row[2]) is not str)
            or (row[3] is not None and type(row[3]) is not int)
            or type(row[4]) is not str
        ):
            raise MetadataReportError("persisted review package is invalid")
        proposal_id, report_id, case_id, checkpoint, outcome = row
        if outcome in {"report_only", "evidence_only"}:
            if proposal_id is not None or case_id is not None or checkpoint is not None:
                raise MetadataReportError("persisted review package is invalid")
            # The five per-column type tests above prove the declared shape.
            return cast("tuple[str | None, str, str | None, int | None, str]", row)
        expected_case = outcome in {"admitted", "superseded"}
        if (
            outcome not in {"admitted", "superseded", "no_case", "retired"}
            or proposal_id is None
            or (case_id is not None) != expected_case
            or checkpoint is None
        ):
            raise MetadataReportError("persisted review package is invalid")
        try:
            valid = self._connection.execute(
                "SELECT 1 FROM proposal_sets proposal JOIN current_review_case_lineages lineage ON "
                "lineage.connection_name=? AND lineage.library_name=? AND lineage.item_id=? "
                "AND lineage.proposal_set_id=proposal.proposal_set_id AND lineage.report_id=proposal.report_id "
                "WHERE proposal.proposal_set_id=? AND proposal.report_id=? AND "
                "lineage.review_case_id IS ? AND lineage.checkpoint=? AND lineage.outcome=? "
                "AND (? IS NULL OR EXISTS(SELECT 1 FROM review_cases review WHERE review.review_case_id=? "
                "AND review.proposal_set_id=? AND review.report_id=?))",
                (
                    connection,
                    library,
                    item,
                    proposal_id,
                    report_id,
                    case_id,
                    checkpoint,
                    outcome,
                    case_id,
                    case_id,
                    proposal_id,
                    report_id,
                ),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted review package is invalid") from error
        if valid != (1,):
            raise MetadataReportError("persisted review package is invalid")
        # The five per-column type tests above prove the declared shape.
        return cast("tuple[str | None, str, str | None, int | None, str]", row)

    @local_operation
    def current_review_package_report(
        self, connection: str, library: str, item: str
    ) -> str | None:
        package = self.current_review_package(connection, library, item)
        return None if package is None else package[1]


def _admission_json(status: str, review_case_id: str | None) -> str:
    return ReviewAdmissionResult(status, review_case_id).to_json()


def _review_search_pattern(search: str | None) -> tuple[str | None, str]:
    if search is None:
        return None, "%"
    if (
        type(search) is not str
        or not search
        or len(search) > 128
        or search.strip() != search
    ):
        raise MetadataReportError("review search is invalid")
    escaped = (
        search.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return search, f"%{escaped}%"
