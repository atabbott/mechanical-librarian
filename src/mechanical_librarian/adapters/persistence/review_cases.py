"""The review-case aggregate.

Owns `review_cases`, the `current_review_case_lineages` pointer that names the
one live case for a target, the `current_review_packages` projection the
pipeline and the browse read through, and the `review_case_transitions`
history.

`admit_review_case` is the aggregate's only writer and it writes across five
aggregates' tables in one transaction -- the proposal set, the report lineage,
the case, its transition and the package. Under the ownership rule it belongs
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
also call `get_review_case`, `list_current_review_case_documents` and
`list_current_attention_documents` through this repository's own attributes,
and `test_review_reads_are_isolated_from_a_concurrent_decision` substitutes
exactly those three attributes to interleave a competing write. Calling the
delegating stub keeps the substitution reachable; reaching past it into this
module would silently stop the interleave from happening.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.proposal_sets import (
    _validate_proposal_row,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _AUTOMATIC_SAFETY_ATTENTION_SQL,
    _CURRENT_LIBRARY_FROM_SQL,
    _CURRENT_REVIEW_FROM_SQL,
    _REVIEW_ATTENTION_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
    _REVIEW_SEARCH_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _validate_report_row,
    _validate_review_browse_row,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
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

# The exact column shape of the current_review_case_lineages pointer selected
# by _validated_review_pointer.  Nullability follows the schema below:
# review_case_id is the only nullable column of the six.
_ReviewPointer = tuple[str, str, str | None, str, int, str]


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
    ) -> None:
        super().__init__(connection, binding)
        # Both belong to the metadata-policy aggregate and are read, never
        # written, from here.  They keep the names their moved call sites use.
        self.current_resolved_metadata_policy = current_resolved_metadata_policy
        self._policy_destinations_for = policy_destinations_for

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
            proposal_row = self._connection.execute("SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets WHERE proposal_set_id=?", (proposal_set.proposal_set_id,)).fetchone()
            if proposal_row is None or _validate_proposal_row(proposal_row) != proposal_set.to_json():
                raise MetadataReportError("review proposal is unavailable")
            report_row = self._connection.execute("SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id=?", (proposal_set.report_id,)).fetchone()
            if report_row is None:
                raise MetadataReportError("review report is unavailable")
            report = MetadataReport.from_json(_validate_report_row(report_row))
            lineage = (report.target.connection, report.target.library, report.target.item)
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
                (current_policy is not None or (generation is not None and generation[0] > 1))
                and (
                    current_policy is None
                    or proposal_set.resolved_policy_id != current_policy.resolved_id
                )
            ):
                raise ReviewAdmissionStaleError
            if self._connection.execute("SELECT report_id FROM current_metadata_report_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone() != (report.report_id,):
                raise ReviewAdmissionStaleError
            if self._connection.execute("SELECT report_id FROM current_metadata_reports WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?", (*lineage, report.file.identity)).fetchone() != (report.report_id,):
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
            if (
                prior is not None
                and prior[0] == proposal_set.proposal_set_id
                and self._connection.execute(
                    "SELECT actor FROM review_case_transitions "
                    "WHERE transition_id=?",
                    # ``prior`` is None exactly when ``validated`` is None, so
                    # reaching here proves the pointer was found.
                    (cast(_ReviewPointer, validated)[3],),
                ).fetchone()
                != ("review-preparation-v1",)
            ):
                if self._binding is not None:
                    self._binding.require_current()
                transaction.commit()
                replay_status = (
                    "replayed"
                    if cast(_ReviewPointer, validated)[5] in {"admitted", "superseded"}
                    else cast(_ReviewPointer, validated)[5]
                )
                return _admission_json(replay_status, cast(_ReviewPointer, validated)[2])
            if not is_actionable(proposal_set):
                outcome = "retired" if prior_case_id is not None else "no_case"
                transition = ReviewTransition(*lineage, checkpoint, proposal_set.proposal_set_id, report.report_id, outcome, prior_case_id, None, "nonactionable")
                self._connection.execute("INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (transition.transition_id, *lineage, checkpoint, transition.actor, transition.from_review_case_id, None, proposal_set.proposal_set_id, report.report_id, outcome, "nonactionable"))
                self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?, ?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=NULL,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome", (*lineage, proposal_set.proposal_set_id, report.report_id, None, transition.transition_id, checkpoint, outcome))
                if owns_transaction:
                    self._connection.execute(
                        "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                        (*lineage, proposal_set.proposal_set_id, report.report_id, None, checkpoint, outcome),
                    )
                if self._binding is not None:
                    self._binding.require_current()
                transaction.commit()
                return _admission_json(outcome, None)
            case = ReviewCase.create(proposal_set, report)
            existing = self._connection.execute("SELECT document FROM review_cases WHERE review_case_id=?", (case.review_case_id,)).fetchone()
            if existing is not None and existing != (case.to_json(),):
                raise MetadataReportError("review case identity conflicts")
            if existing is None:
                self._connection.execute("INSERT INTO review_cases(review_case_id,proposal_set_id,report_id,document) VALUES(?,?,?,?)", (case.review_case_id, case.proposal_set_id, case.report_id, case.to_json()))
            outcome = "superseded" if prior_case_id is not None else "admitted"
            transition = ReviewTransition(*lineage, checkpoint, proposal_set.proposal_set_id, report.report_id, outcome, prior_case_id, case.review_case_id, "actionable")
            self._connection.execute("INSERT INTO review_case_transitions(transition_id,connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (transition.transition_id, *lineage, checkpoint, transition.actor, transition.from_review_case_id, case.review_case_id, proposal_set.proposal_set_id, report.report_id, outcome, "actionable"))
            self._connection.execute("INSERT INTO current_review_case_lineages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,transition_id=excluded.transition_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome", (*lineage, proposal_set.proposal_set_id, report.report_id, case.review_case_id, transition.transition_id, checkpoint, outcome))
            if owns_transaction:
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) VALUES(?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (*lineage, proposal_set.proposal_set_id, report.report_id, case.review_case_id, checkpoint, outcome),
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
        except (sqlite3.Error, ProposalValueError, ReportValueError, ReviewCaseValueError) as error:
            transaction.rollback()
            raise MetadataReportError("review admission failed") from error

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

    def get_review_case(self, review_case_id: str) -> str:
        try:
            row = self._connection.execute("SELECT review_case_id,proposal_set_id,report_id,document FROM review_cases WHERE review_case_id=?", (review_case_id,)).fetchone()
            if row is None or len(row) != 4:
                raise MetadataReportError("review case is unavailable")
            case = ReviewCase.from_json(row[3])
            if (case.review_case_id, case.proposal_set_id, case.report_id) != row[:3]:
                raise MetadataReportError("review case is invalid")
            self._validated_review_pointer(cast("tuple[str, str, str]", (case.target["connection"], case.target["library"], case.target["item"])))
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
            report = MetadataReport.from_json(_validate_report_row(report_row))
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
                (case.target["connection"], case.target["library"], case.target["item"]),
            ).fetchone()
            current = package == (case.proposal_set_id, case.report_id, case.review_case_id)
            return _canonical_json({"case": case.to_data(), "current": current, "schema_version": "review-case-get-v1"})
        except (sqlite3.Error, ReviewCaseValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    def get_review_case_report(self, review_case_id: str) -> str:
        try:
            return self._source_report_for_review_case(review_case_id).to_json()
        except (sqlite3.Error, ReviewCaseValueError, ReportValueError) as error:
            raise MetadataReportError("review case evidence is unavailable") from error

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str | None, str], ...]:
        try:
            query, pattern = _review_search_pattern(search)
            condition = _REVIEW_ATTENTION_SQL
            if attention is False:
                condition = f"NOT ({condition})"
            elif attention is None:
                condition = "1"
            terminal_union = ""
            parameters: list[object] = [
                query,
                pattern,
                pattern,
                pattern,
                pattern,
                pattern,
            ]
            if attention is not True:
                terminal_union = (
                    " UNION ALL SELECT NULL,json_object("
                    "'attempt',attempt,'connection',connection_name,"
                    "'failure_root',failure_root,'item',item_id,'lane',stage,"
                    "'library',library_name,'recovery_attempt',recovery_attempt,"
                    "'schema_version','review-terminal-work-v1','status',failure_code,"
                    "'work_id',work_id),connection_name,library_name,item_id,NULL,"
                    "1,'preparation unavailable','' FROM review_preparation_work "
                    "WHERE state='failed' AND failure_code IS NOT NULL AND NOT EXISTS ("
                    "SELECT 1 FROM current_review_packages p WHERE "
                    "p.connection_name=review_preparation_work.connection_name AND "
                    "p.library_name=review_preparation_work.library_name AND "
                    "p.item_id=review_preparation_work.item_id) AND "
                    "(?='' OR ml_casefold(item_id) LIKE ? ESCAPE '\\' OR "
                    "ml_casefold(library_name) LIKE ? ESCAPE '\\' OR "
                    "ml_casefold('Preparation unavailable') LIKE ? ESCAPE '\\' OR "
                    "ml_casefold('unknown') LIKE ? ESCAPE '\\')"
                )
                parameters.extend((query, pattern, pattern, pattern, pattern))
            parameters.extend((limit, offset))
            rows = self._connection.execute(
                "SELECT case_document,report_document,connection_name,library_name,"
                "item_id,outcome FROM (SELECT rc.document AS case_document,"
                "mr.document AS report_document,ml.connection_name AS connection_name,"
                "ml.library_name AS library_name,ml.item_id AS item_id,c.outcome AS outcome,"
                "CASE WHEN "
                + _REVIEW_ATTENTION_SQL
                + " THEN 0 ELSE 1 END AS attention_order,"
                "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')) AS title_order,"
                "COALESCE(rc.review_case_id,'') AS case_order "
                + _CURRENT_LIBRARY_FROM_SQL
                + " WHERE COALESCE(c.outcome,'')<>'tombstoned' AND "
                + condition
                + _REVIEW_SEARCH_SQL
                + terminal_union
                + ") ORDER BY attention_order,title_order,library_name,item_id,"
                "connection_name,case_order LIMIT ? OFFSET ?",
                tuple(parameters),
            ).fetchall()
            values: list[tuple[str | None, str]] = []
            for row in rows:
                if (
                    len(row) != 6
                    or type(row[1]) is not str
                    or any(type(value) is not str for value in row[2:5])
                    or (row[5] is not None and type(row[5]) is not str)
                ):
                    raise MetadataReportError("review browse row is invalid")
                if row[0] is None and row[5] is None:
                    terminal = json.loads(row[1])
                    if (
                        type(terminal) is dict
                        and terminal.get("schema_version")
                        == "review-terminal-work-v1"
                    ):
                        values.append((None, _canonical_json(terminal)))
                        continue
                lineage = (row[2], row[3], row[4])
                report = MetadataReport.from_json(row[1])
                if (
                    (
                        report.target.connection,
                        report.target.library,
                        report.target.item,
                    )
                    != lineage
                ):
                    raise MetadataReportError("review browse row disagrees")
                if row[5] is None:
                    if row[0] is not None:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, report.to_json()))
                    continue
                if row[0] is None:
                    if row[5] not in {"report_only", "no_case", "retired"}:
                        raise MetadataReportError("review browse row disagrees")
                    values.append((None, report.to_json()))
                    continue
                case_document, report_document = _validate_review_browse_row(
                    row[:2],
                    self._policy_destinations_for(
                        report,
                        ProposalSet.from_json(
                            ReviewCase.from_json(row[0]).proposal_document
                        ),
                    ),
                )
                values.append((case_document, report_document))
            return tuple(values)
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("review library is unavailable") from error

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        try:
            query, pattern = _review_search_pattern(search)
            row = self._connection.execute(
                "SELECT count(*),COALESCE(sum(CASE WHEN "
                + _REVIEW_ATTENTION_SQL
                + " THEN 1 ELSE 0 END),0) "
                + _CURRENT_LIBRARY_FROM_SQL
                + " WHERE COALESCE(c.outcome,'')<>'tombstoned'"
                + _REVIEW_SEARCH_SQL,
                (query, pattern, pattern, pattern, pattern, pattern),
            ).fetchone()
            if (
                row is None
                or len(row) != 2
                or any(type(value) is not int or value < 0 for value in row)
                or row[1] > row[0]
            ):
                raise MetadataReportError("review library count is invalid")
            terminal = self._connection.execute(
                "SELECT count(*) FROM review_preparation_work WHERE state='failed' AND failure_code IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM current_review_packages p WHERE p.connection_name=review_preparation_work.connection_name AND p.library_name=review_preparation_work.library_name AND p.item_id=review_preparation_work.item_id) "
                "AND (?='' OR ml_casefold(item_id) LIKE ? ESCAPE '\\' OR ml_casefold(library_name) LIKE ? ESCAPE '\\' "
                "OR ml_casefold('Preparation unavailable') LIKE ? ESCAPE '\\' OR ml_casefold('unknown') LIKE ? ESCAPE '\\')",
                (query, pattern, pattern, pattern, pattern),
            ).fetchone()
            if terminal is None or type(terminal[0]) is not int or terminal[0] < 0:
                raise MetadataReportError("review library count is invalid")
            return row[0] + terminal[0], row[1]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("review library is unavailable") from error

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None], ...
    ]:
        # The last column is a `CASE` expression over
        # `_AUTOMATIC_SAFETY_ATTENTION_SQL` whose only non-null result is the
        # literal `'automatic_safety_suspended'`, so the cause is narrower than
        # the `str` the row validation below proves.
        try:
            query, pattern = _review_search_pattern(search)
            rows = self._connection.execute(
                "SELECT rc.document,mr.document,"
                "json_extract(result.value,'$.destination'),"
                "(SELECT count(*) FROM json_each(rc.document,'$.results') result "
                "WHERE " + _REVIEW_FIELD_ATTENTION_SQL + "),"
                "(SELECT json_extract(outcome.value,'$.disposition') FROM shadow_decision_outcomes shadow, "
                "json_each(shadow.document,'$.outcomes') outcome WHERE shadow.review_case_id=rc.review_case_id "
                "AND json_extract(outcome.value,'$.destination')=json_extract(result.value,'$.destination') LIMIT 1),"
                "(CASE WHEN " + _AUTOMATIC_SAFETY_ATTENTION_SQL + " THEN 'automatic_safety_suspended' ELSE NULL END) "
                + _CURRENT_REVIEW_FROM_SQL
                + ",json_each(rc.document,'$.results') result "
                "WHERE "
                + _REVIEW_FIELD_ATTENTION_SQL
                + _REVIEW_SEARCH_SQL
                + " ORDER BY "
                "ml_casefold(COALESCE(json_extract(mr.document,'$.target.fields[0].value'),'')),"
                "c.library_name,c.item_id,"
                "json_extract(result.value,'$.destination'),rc.review_case_id "
                "LIMIT ? OFFSET ?",
                (query, pattern, pattern, pattern, pattern, pattern, limit, offset),
            ).fetchall()
            values = []
            for row in rows:
                case_document, report_document = _validate_review_browse_row(
                    row[:2],
                    self._policy_destinations_for(
                        MetadataReport.from_json(row[1]),
                        ProposalSet.from_json(
                            ReviewCase.from_json(row[0]).proposal_document
                        ),
                    ),
                )
                if (
                    type(row[2]) is not str
                    or type(row[3]) is not int
                    or row[3] < 1
                    or (row[4] is not None and type(row[4]) is not str)
                    or (row[5] is not None and type(row[5]) is not str)
                ):
                    raise MetadataReportError("review attention field is invalid")
                values.append((
                    case_document, report_document, row[2], row[3], row[4],
                    cast("ReviewFieldCause | None", row[5]),
                ))
            return tuple(values)
        except (
            sqlite3.Error,
            MetadataReportError,
            ReviewCaseValueError,
            ReportValueError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("review attention is unavailable") from error

    def count_current_attention_fields(self, search: str | None) -> int:
        try:
            query, pattern = _review_search_pattern(search)
            row = self._connection.execute(
                "SELECT count(*) "
                + _CURRENT_REVIEW_FROM_SQL
                + ",json_each(rc.document,'$.results') result "
                "WHERE "
                + _REVIEW_FIELD_ATTENTION_SQL
                + _REVIEW_SEARCH_SQL,
                (query, pattern, pattern, pattern, pattern, pattern),
            ).fetchone()
            if (
                row is None
                or len(row) != 1
                or type(row[0]) is not int
                or row[0] < 0
            ):
                raise MetadataReportError("review attention count is invalid")
            return row[0]
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError("review attention is unavailable") from error

    def _review_case_accepts_authority(self, review_case_id: str) -> bool:
        return cast("tuple[object, ...] | None", self._connection.execute(
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
        ).fetchone()) == (1,)

    def _source_report_for_review_case(
        self, review_case_id: str
    ) -> MetadataReport:
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
        return MetadataReport.from_json(_validate_report_row(row))

    def _source_pair_for_review_case(
        self, review_case_id: str
    ) -> tuple[MetadataReport, ProposalSet]:
        row = self._connection.execute(
            "SELECT r.report_id,r.connection_name,r.library_name,r.item_id,"
            "r.file_identity,r.content_digest,r.schema_version,r.document_digest,"
            "r.document,p.proposal_set_id,p.report_id,p.policy_version,p.document "
            "FROM review_cases c JOIN metadata_reports r ON r.report_id=c.report_id "
            "JOIN proposal_sets p ON p.proposal_set_id=c.proposal_set_id "
            "WHERE c.review_case_id=?", (review_case_id,)
        ).fetchone()
        if row is None:
            raise MetadataReportError("shadow review source is unavailable")
        return (
            MetadataReport.from_json(_validate_report_row(row[:9])),
            ProposalSet.from_json(_validate_proposal_row(row[9:])),
        )

    def _validated_review_pointer(self, lineage: tuple[str, str, str]) -> _ReviewPointer | None:
        pointer: _ReviewPointer | None = self._connection.execute("SELECT proposal_set_id,report_id,review_case_id,transition_id,checkpoint,outcome FROM current_review_case_lineages WHERE connection_name=? AND library_name=? AND item_id=?", lineage).fetchone()
        if pointer is None:
            return None
        proposal_id, report_id, case_id, transition_id, checkpoint, outcome = pointer
        transition = self._connection.execute("SELECT connection_name,library_name,item_id,checkpoint,actor,from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason FROM review_case_transitions WHERE transition_id=?", (transition_id,)).fetchone()
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
        if outcome == "report_only":
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
                    connection, library, item, proposal_id, report_id, case_id,
                    checkpoint, outcome, case_id, case_id, proposal_id, report_id,
                ),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError("persisted review package is invalid") from error
        if valid != (1,):
            raise MetadataReportError("persisted review package is invalid")
        # The five per-column type tests above prove the declared shape.
        return cast("tuple[str | None, str, str | None, int | None, str]", row)

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
        search.casefold()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return search, f"%{escaped}%"
