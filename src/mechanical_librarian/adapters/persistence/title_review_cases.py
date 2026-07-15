"""Atomic SQLite admission for the closed F10a synthetic review-case preview."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from mechanical_librarian.application.title_review_cases import (
    ReviewAdmission,
    ReviewTransitionKind,
    SyntheticTitleReviewCaseReport,
    render_title_review_case_text,
)
from mechanical_librarian.domain.title_proposals import (
    TitleProposalSet,
    TitleProposalValueError,
)
from mechanical_librarian.domain.title_review_cases import (
    SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
    SyntheticReviewTarget,
    SyntheticTitleReviewCase,
    TitleReviewCaseValueError,
    review_case_from_proposal_set,
    review_target_from_proposal_set,
)


class TitleReviewCasePersistenceError(RuntimeError):
    """Review-case state is unavailable, corrupt, or inconsistent."""


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$"
)
_MAX_REVISIONS = 2
_ACTOR = "synthetic review admission"


class SQLiteTitleReviewCaseRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        after_case_insert: Callable[[], None] | None = None,
        after_transition_insert: Callable[[], None] | None = None,
        after_pointer_update: Callable[[], None] | None = None,
        before_readback: Callable[[], None] | None = None,
    ) -> None:
        self._connection = connection
        self._after_case_insert = after_case_insert
        self._after_transition_insert = after_transition_insert
        self._after_pointer_update = after_pointer_update
        self._before_readback = before_readback

    def admit_current(self, lineage: str) -> ReviewAdmission:
        if type(lineage) is not str or _IDENTIFIER.fullmatch(lineage) is None:
            raise TitleReviewCasePersistenceError("review lineage is invalid")
        try:
            with _transaction(self._connection):
                source = _current_proposal(self._connection, lineage)
                if source is None:
                    raise TitleReviewCasePersistenceError("current proposal state is missing")
                target = review_target_from_proposal_set(source)
                projected_case = review_case_from_proposal_set(source)
                if projected_case is not None:
                    projected_case = SyntheticTitleReviewCase.from_json(projected_case.to_json())

                previous = self._validated_state(source, target)
                if previous is not None and previous.source_set_id == source.set_id:
                    self._validate_projection(previous, source, projected_case)
                    return previous

                prior_case = None if previous is None else previous.current_case
                if previous is not None:
                    predecessor = _proposal_by_id(self._connection, previous.source_set_id)
                    if (
                        source.previous_set_id != predecessor.set_id
                        or source.lineage_revision != predecessor.lineage_revision + 1
                    ):
                        raise TitleReviewCasePersistenceError(
                            "review admission source is not the exact successor"
                        )
                    if prior_case is None:
                        raise TitleReviewCasePersistenceError(
                            "review no-case state is terminal"
                        )

                transition_kind, reason = _transition(prior_case, projected_case)
                sequence = 1 if previous is None else previous.transition_count + 1
                transition_id = _transition_id(
                    target,
                    lineage,
                    sequence,
                    source.set_id,
                    None if prior_case is None else prior_case.case_id,
                    None if projected_case is None else projected_case.case_id,
                    transition_kind,
                    reason,
                )
                case_count = (0 if previous is None else previous.case_history_count) + (
                    1 if projected_case is not None else 0
                )
                admission = ReviewAdmission(
                    source_set_id=source.set_id,
                    source_result=source.result,
                    current_case=projected_case,
                    previous_case=prior_case,
                    current_case_id=(None if projected_case is None else projected_case.case_id),
                    transition_id=transition_id,
                    transition_kind=transition_kind,
                    transition_reason=reason,
                    replayed=False,
                    case_history_count=case_count,
                    transition_count=sequence,
                )
                _validate_report(admission)

                if projected_case is not None:
                    self._insert_case(projected_case)
                    if self._after_case_insert is not None:
                        self._after_case_insert()
                self._insert_transition(
                    target,
                    lineage,
                    sequence,
                    source.set_id,
                    prior_case,
                    projected_case,
                    transition_id,
                    transition_kind,
                    reason,
                )
                if self._after_transition_insert is not None:
                    self._after_transition_insert()
                self._advance_pointer(
                    target,
                    lineage,
                    source.set_id,
                    projected_case,
                    transition_id,
                    sequence,
                    previous,
                )
                if self._after_pointer_update is not None:
                    self._after_pointer_update()
                if self._before_readback is not None:
                    self._before_readback()

                current_source = _current_proposal(self._connection, lineage)
                if current_source != source:
                    raise TitleReviewCasePersistenceError(
                        "current proposal changed during review admission"
                    )
                stored = self._validated_state(source, target)
                if stored is None:
                    raise TitleReviewCasePersistenceError("review admission readback is missing")
                readback = ReviewAdmission(
                    **{
                        **stored.__dict__,
                        "replayed": False,
                    }
                )
                if readback != admission:
                    raise TitleReviewCasePersistenceError("review admission readback failed")
                _validate_report(readback)
                return readback
        except TitleReviewCasePersistenceError:
            raise
        except (
            sqlite3.Error,
            TitleProposalValueError,
            TitleReviewCaseValueError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            raise TitleReviewCasePersistenceError("review admission failed safely") from None

    def _validated_state(
        self,
        source: TitleProposalSet,
        target: SyntheticReviewTarget,
    ) -> ReviewAdmission | None:
        rows = self._connection.execute(
            "SELECT binding_version, adapter_id, connection_id, library_id, external_item, lineage, "
            "latest_set_id, current_case_id, sequence, last_transition_id "
            "FROM current_synthetic_title_review_cases WHERE binding_version = ? AND lineage = ?",
            (SYNTHETIC_TITLE_REVIEW_BINDING_VERSION, source.lineage),
        ).fetchall()
        if not rows:
            orphan_cases = self._connection.execute(
                "SELECT COUNT(*) FROM synthetic_title_review_cases WHERE binding_version = ? AND lineage = ?",
                (SYNTHETIC_TITLE_REVIEW_BINDING_VERSION, source.lineage),
            ).fetchone()
            orphan_transitions = self._connection.execute(
                "SELECT COUNT(*) FROM synthetic_title_review_case_transitions WHERE binding_version = ? AND lineage = ?",
                (SYNTHETIC_TITLE_REVIEW_BINDING_VERSION, source.lineage),
            ).fetchone()
            if orphan_cases != (0,) or orphan_transitions != (0,):
                raise TitleReviewCasePersistenceError("review history has no current state")
            return None
        if len(rows) != 1:
            raise TitleReviewCasePersistenceError("review lineage has multiple current states")
        row = rows[0]
        expected_scope = (
            SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
            target.adapter_id,
            target.connection_id,
            target.library_id,
            target.external_item,
            source.lineage,
        )
        if tuple(row[:6]) != expected_scope:
            raise TitleReviewCasePersistenceError("review current scope is invalid")
        latest_set_id, current_case_id, sequence, transition_id = row[6:]
        if (
            type(latest_set_id) is not str
            or (current_case_id is not None and type(current_case_id) is not str)
            or type(sequence) is not int
            or type(transition_id) is not str
        ):
            raise TitleReviewCasePersistenceError("review current state is malformed")

        latest = _proposal_by_id(self._connection, latest_set_id)
        if latest.lineage != source.lineage:
            raise TitleReviewCasePersistenceError("review current source crosses lineages")
        transitions = self._connection.execute(
            "SELECT transition_id, sequence, successor_set_id, prior_case_id, replacement_case_id, kind, actor, reason, occurred_at "
            "FROM synthetic_title_review_case_transitions WHERE binding_version = ? AND lineage = ? "
            "ORDER BY sequence, transition_id LIMIT ?",
            (SYNTHETIC_TITLE_REVIEW_BINDING_VERSION, source.lineage, _MAX_REVISIONS + 1),
        ).fetchall()
        wrong_transition_scope = self._connection.execute(
            "SELECT COUNT(*) FROM synthetic_title_review_case_transitions "
            "WHERE binding_version = ? AND lineage = ? AND "
            "(adapter_id != ? OR connection_id != ? OR library_id != ? OR external_item != ?)",
            (
                SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
                source.lineage,
                target.adapter_id,
                target.connection_id,
                target.library_id,
                target.external_item,
            ),
        ).fetchone()
        if wrong_transition_scope != (0,):
            raise TitleReviewCasePersistenceError("review transition scope is invalid")
        if len(transitions) > _MAX_REVISIONS or len(transitions) != sequence:
            raise TitleReviewCasePersistenceError("review transition history is invalid")
        if tuple(item[1] for item in transitions) != tuple(range(1, len(transitions) + 1)):
            raise TitleReviewCasePersistenceError("review transition sequence is invalid")
        last = transitions[-1]
        if last[0] != transition_id or last[2] != latest.set_id or last[6] != _ACTOR:
            raise TitleReviewCasePersistenceError("review current transition is invalid")

        cases = self._connection.execute(
            "SELECT case_id, identity_digest, binding_version, adapter_id, connection_id, library_id, "
            "external_item, target_digest, lineage, source_set_id, canonical_case "
            "FROM synthetic_title_review_cases WHERE binding_version = ? AND lineage = ? "
            "ORDER BY source_set_id, case_id LIMIT ?",
            (SYNTHETIC_TITLE_REVIEW_BINDING_VERSION, source.lineage, _MAX_REVISIONS + 1),
        ).fetchall()
        if len(cases) > _MAX_REVISIONS:
            raise TitleReviewCasePersistenceError("review case history exceeds its bound")
        hydrated = {item.case_id: item for item in (_hydrate_case(row) for row in cases)}
        if len(hydrated) != len(cases):
            raise TitleReviewCasePersistenceError("review case identities collide")
        if any(item.target != target or item.lineage != source.lineage for item in hydrated.values()):
            raise TitleReviewCasePersistenceError("review case history crosses scopes")
        for case in hydrated.values():
            case_source = _proposal_by_id(self._connection, case.source_set_id)
            if review_case_from_proposal_set(case_source) != case:
                raise TitleReviewCasePersistenceError(
                    "review case disagrees with its proposal source"
                )
        transition_sources = tuple(_proposal_by_id(self._connection, item[2]) for item in transitions)
        if (
            any(item.lineage != source.lineage for item in transition_sources)
            or tuple(item.lineage_revision for item in transition_sources)
            != tuple(sorted({item.lineage_revision for item in transition_sources}))
            or transition_sources[-1] != latest
        ):
            raise TitleReviewCasePersistenceError("review transition sources are invalid")
        expected_prior: SyntheticTitleReviewCase | None = None
        referenced_case_ids: set[str] = set()
        for index, transition in enumerate(transitions):
            _validate_transition(transition, hydrated)
            if index > 0 and (
                transition_sources[index].previous_set_id
                != transition_sources[index - 1].set_id
                or transition_sources[index].lineage_revision
                != transition_sources[index - 1].lineage_revision + 1
            ):
                raise TitleReviewCasePersistenceError(
                    "review transition source chain is invalid"
                )
            if index > 0 and expected_prior is None:
                raise TitleReviewCasePersistenceError(
                    "review transition follows terminal no-case state"
                )
            projected_case = review_case_from_proposal_set(transition_sources[index])
            expected_kind, expected_reason = _transition(
                expected_prior, projected_case
            )
            expected_prior_id = (
                None if expected_prior is None else expected_prior.case_id
            )
            expected_replacement_id = (
                None if projected_case is None else projected_case.case_id
            )
            if (
                transition[3],
                transition[4],
                transition[5],
                transition[7],
            ) != (
                expected_prior_id,
                expected_replacement_id,
                expected_kind.value,
                expected_reason,
            ):
                raise TitleReviewCasePersistenceError(
                    "review transition disagrees with lifecycle state"
                )
            if expected_prior_id is not None:
                referenced_case_ids.add(expected_prior_id)
            if expected_replacement_id is not None:
                referenced_case_ids.add(expected_replacement_id)
            if transition[0] != _transition_id(
                target,
                source.lineage,
                transition[1],
                transition[2],
                transition[3],
                transition[4],
                expected_kind,
                transition[7],
            ):
                raise TitleReviewCasePersistenceError(
                    "review transition identity is invalid"
                )
            expected_prior = projected_case
        if referenced_case_ids != set(hydrated):
            raise TitleReviewCasePersistenceError(
                "review case history is not exactly referenced"
            )
        current_case = None if current_case_id is None else hydrated.get(current_case_id)
        if current_case_id is not None and current_case is None:
            raise TitleReviewCasePersistenceError("review current case is dangling")
        if current_case_id != last[4]:
            raise TitleReviewCasePersistenceError("review current case disagrees with transition")
        previous_case = None if last[3] is None else hydrated.get(last[3])
        if last[3] is not None and previous_case is None:
            raise TitleReviewCasePersistenceError("review predecessor case is dangling")
        try:
            transition_kind = ReviewTransitionKind(last[5])
        except ValueError:
            raise TitleReviewCasePersistenceError("review transition kind is invalid") from None
        admission = ReviewAdmission(
            source_set_id=latest.set_id,
            source_result=latest.result,
            current_case=current_case,
            previous_case=previous_case,
            current_case_id=current_case_id,
            transition_id=transition_id,
            transition_kind=transition_kind,
            transition_reason=last[7],
            replayed=latest.set_id == source.set_id,
            case_history_count=len(cases),
            transition_count=len(transitions),
        )
        _validate_report(admission)
        return admission

    @staticmethod
    def _validate_projection(
        admission: ReviewAdmission,
        source: TitleProposalSet,
        projected_case: SyntheticTitleReviewCase | None,
    ) -> None:
        if admission.source_result is not source.result:
            raise TitleReviewCasePersistenceError("review source result changed")
        if admission.current_case != projected_case:
            raise TitleReviewCasePersistenceError("review current case changed")

    def _insert_case(self, case: SyntheticTitleReviewCase) -> None:
        self._connection.execute(
            "INSERT INTO synthetic_title_review_cases("
            "case_id, identity_digest, binding_version, adapter_id, connection_id, library_id, "
            "external_item, target_digest, lineage, source_set_id, canonical_case"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case.case_id,
                case.identity_digest,
                case.binding_version,
                case.target.adapter_id,
                case.target.connection_id,
                case.target.library_id,
                case.target.external_item,
                case.target.digest,
                case.lineage,
                case.source_set_id,
                case.to_json(),
            ),
        )

    def _insert_transition(
        self,
        target: SyntheticReviewTarget,
        lineage: str,
        sequence: int,
        source_set_id: str,
        prior_case: SyntheticTitleReviewCase | None,
        replacement_case: SyntheticTitleReviewCase | None,
        transition_id: str,
        kind: ReviewTransitionKind,
        reason: str,
    ) -> None:
        occurred_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self._connection.execute(
            "INSERT INTO synthetic_title_review_case_transitions("
            "transition_id, binding_version, adapter_id, connection_id, library_id, external_item, "
            "lineage, sequence, successor_set_id, prior_case_id, replacement_case_id, kind, actor, reason, occurred_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transition_id,
                SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
                target.adapter_id,
                target.connection_id,
                target.library_id,
                target.external_item,
                lineage,
                sequence,
                source_set_id,
                None if prior_case is None else prior_case.case_id,
                None if replacement_case is None else replacement_case.case_id,
                kind.value,
                _ACTOR,
                reason,
                occurred_at,
            ),
        )

    def _advance_pointer(
        self,
        target: SyntheticReviewTarget,
        lineage: str,
        source_set_id: str,
        current_case: SyntheticTitleReviewCase | None,
        transition_id: str,
        sequence: int,
        previous: ReviewAdmission | None,
    ) -> None:
        current_case_id = None if current_case is None else current_case.case_id
        if previous is None:
            self._connection.execute(
                "INSERT INTO current_synthetic_title_review_cases("
                "binding_version, adapter_id, connection_id, library_id, external_item, lineage, "
                "latest_set_id, current_case_id, sequence, last_transition_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
                    target.adapter_id,
                    target.connection_id,
                    target.library_id,
                    target.external_item,
                    lineage,
                    source_set_id,
                    current_case_id,
                    sequence,
                    transition_id,
                ),
            )
            return
        changed = self._connection.execute(
            "UPDATE current_synthetic_title_review_cases SET latest_set_id = ?, current_case_id = ?, "
            "sequence = ?, last_transition_id = ? WHERE binding_version = ? AND lineage = ? "
            "AND latest_set_id = ? AND sequence = ? AND last_transition_id = ?",
            (
                source_set_id,
                current_case_id,
                sequence,
                transition_id,
                SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
                lineage,
                previous.source_set_id,
                previous.transition_count,
                previous.transition_id,
            ),
        ).rowcount
        if changed != 1:
            raise TitleReviewCasePersistenceError("review current state changed concurrently")


def _proposal_by_id(connection: sqlite3.Connection, set_id: str) -> TitleProposalSet:
    rows = connection.execute(
        "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, mode, canonical_set "
        "FROM synthetic_title_proposal_sets WHERE set_id = ?",
        (set_id,),
    ).fetchall()
    if len(rows) != 1:
        raise TitleReviewCasePersistenceError("review proposal source is missing")
    proposal_set = _hydrate_proposal(rows[0])
    if proposal_set.set_id != set_id:
        raise TitleReviewCasePersistenceError("review proposal source identity is invalid")
    return proposal_set


def _current_proposal(
    connection: sqlite3.Connection, lineage: str
) -> TitleProposalSet | None:
    rows = connection.execute(
        "SELECT set_id, identity_digest, lineage, lineage_revision, previous_set_id, mode, canonical_set "
        "FROM synthetic_title_proposal_sets WHERE lineage = ? "
        "ORDER BY lineage_revision, set_id LIMIT ?",
        (lineage, _MAX_REVISIONS + 1),
    ).fetchall()
    pointers = connection.execute(
        "SELECT set_id FROM current_synthetic_title_proposals WHERE lineage = ?",
        (lineage,),
    ).fetchall()
    if not rows:
        if pointers:
            raise TitleReviewCasePersistenceError("proposal current pointer is dangling")
        return None
    if len(rows) > _MAX_REVISIONS or len(pointers) != 1:
        raise TitleReviewCasePersistenceError("proposal lineage is invalid")
    history = tuple(_hydrate_proposal(row) for row in rows)
    if tuple(item.lineage_revision for item in history) != tuple(range(1, len(history) + 1)):
        raise TitleReviewCasePersistenceError("proposal lineage revisions are invalid")
    for index, item in enumerate(history):
        if item.lineage != lineage:
            raise TitleReviewCasePersistenceError("proposal history crosses lineages")
        expected_previous = None if index == 0 else history[index - 1].set_id
        if item.previous_set_id != expected_previous:
            raise TitleReviewCasePersistenceError("proposal predecessor chain is invalid")
    if pointers[0][0] != history[-1].set_id:
        raise TitleReviewCasePersistenceError("proposal current pointer is not latest")
    return history[-1]


def _hydrate_proposal(row: tuple[object, ...]) -> TitleProposalSet:
    if len(row) != 7 or type(row[6]) is not str:
        raise TitleReviewCasePersistenceError("proposal source row is malformed")
    proposal_set = TitleProposalSet.from_json(row[6])
    if tuple(row[:6]) != (
        proposal_set.set_id,
        proposal_set.identity_digest,
        proposal_set.lineage,
        proposal_set.lineage_revision,
        proposal_set.previous_set_id,
        proposal_set.mode.value,
    ):
        raise TitleReviewCasePersistenceError("proposal source row failed identity validation")
    return proposal_set


def _hydrate_case(row: tuple[object, ...]) -> SyntheticTitleReviewCase:
    if len(row) != 11 or type(row[10]) is not str:
        raise TitleReviewCasePersistenceError("review case row is malformed")
    case = SyntheticTitleReviewCase.from_json(row[10])
    if tuple(row[:10]) != (
        case.case_id,
        case.identity_digest,
        case.binding_version,
        case.target.adapter_id,
        case.target.connection_id,
        case.target.library_id,
        case.target.external_item,
        case.target.digest,
        case.lineage,
        case.source_set_id,
    ):
        raise TitleReviewCasePersistenceError("review case row failed identity validation")
    return case


def _validate_transition(
    row: tuple[object, ...], cases: dict[str, SyntheticTitleReviewCase]
) -> None:
    if (
        len(row) != 9
        or type(row[0]) is not str
        or type(row[1]) is not int
        or type(row[2]) is not str
        or (row[3] is not None and type(row[3]) is not str)
        or (row[4] is not None and type(row[4]) is not str)
        or type(row[5]) is not str
        or row[6] != _ACTOR
        or type(row[7]) is not str
        or type(row[8]) is not str
    ):
        raise TitleReviewCasePersistenceError("review transition is malformed")
    if _TIMESTAMP.fullmatch(row[8]) is None:
        raise TitleReviewCasePersistenceError("review transition timestamp is invalid")
    try:
        datetime.strptime(row[8], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        raise TitleReviewCasePersistenceError(
            "review transition timestamp is invalid"
        ) from None
    prior = row[3]
    replacement = row[4]
    if prior is not None and prior not in cases:
        raise TitleReviewCasePersistenceError("review transition predecessor is missing")
    if replacement is not None and replacement not in cases:
        raise TitleReviewCasePersistenceError("review transition replacement is missing")
    if replacement is not None and cases[replacement].source_set_id != row[2]:
        raise TitleReviewCasePersistenceError("review transition replacement source is invalid")
    try:
        kind = ReviewTransitionKind(row[5])
    except (TypeError, ValueError):
        raise TitleReviewCasePersistenceError("review transition kind is invalid") from None
    expected = {
        ReviewTransitionKind.CREATED: (None, True, "admit current synthetic review case"),
        ReviewTransitionKind.SUPERSEDED: (True, True, "supersede current synthetic review case"),
        ReviewTransitionKind.RETIRED: (True, None, "retire current synthetic review case"),
        ReviewTransitionKind.NO_CASE: (None, None, "record current synthetic no-case result"),
    }[kind]
    prior_present = True if prior is not None else None
    replacement_present = True if replacement is not None else None
    if (prior_present, replacement_present, row[7]) != expected:
        raise TitleReviewCasePersistenceError("review transition semantics are invalid")


def _transition(
    prior: SyntheticTitleReviewCase | None,
    replacement: SyntheticTitleReviewCase | None,
) -> tuple[ReviewTransitionKind, str]:
    if prior is None and replacement is not None:
        return ReviewTransitionKind.CREATED, "admit current synthetic review case"
    if prior is not None and replacement is not None:
        return ReviewTransitionKind.SUPERSEDED, "supersede current synthetic review case"
    if prior is not None:
        return ReviewTransitionKind.RETIRED, "retire current synthetic review case"
    return ReviewTransitionKind.NO_CASE, "record current synthetic no-case result"


def _transition_id(
    target: SyntheticReviewTarget,
    lineage: str,
    sequence: int,
    source_set_id: str,
    prior_case_id: str | None,
    replacement_case_id: str | None,
    kind: ReviewTransitionKind,
    reason: str,
) -> str:
    payload = json.dumps(
        {
            "actor": _ACTOR,
            "binding_version": SYNTHETIC_TITLE_REVIEW_BINDING_VERSION,
            "kind": kind.value,
            "lineage": lineage,
            "prior_case_id": prior_case_id,
            "reason": reason,
            "replacement_case_id": replacement_case_id,
            "sequence": sequence,
            "source_set_id": source_set_id,
            "target": target.to_data(),
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"transition-{digest[:53]}"


def _validate_report(admission: ReviewAdmission) -> None:
    report = SyntheticTitleReviewCaseReport(admission)
    report.to_json()
    render_title_review_case_text(report)


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise TitleReviewCasePersistenceError("nested review admission is unsupported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


__all__ = ["SQLiteTitleReviewCaseRepository", "TitleReviewCasePersistenceError"]
