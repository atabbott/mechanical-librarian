"""Shared review-web test doubles and the authenticated transport fixture.

The fake repository, preparation control, and activity application are
used by the review-web cohorts and by three modules outside them, which
previously reached into another module's tests to get them.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.metadata_policy import (
    PolicyContentionError,
    PolicyPreviewCapture,
    PolicyPreviewLibraryCapture,
    PolicyTransition,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineProgress,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    LibraryLabels,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ValidatedDocumentCache,
)
from mechanical_librarian.domain.description_advice import DescriptionAdviceBinding
from mechanical_librarian.domain.field_effect import LIST_DESTINATIONS
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.preparation_presentation import (
    PreparationPresentation,
    PreparationPresentationError,
    PreparationTrigger,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldCause,
)
from tests.support.activity_seed import seed_activity  # noqa: E402
from tests.support.review_cases import _report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

# The exact attention row `ReviewAttentionSnapshot` declares. `rows` below is
# annotated with it so a narrowed row fails the gate rather than reaching the
# reader as padding (#703).
_AttentionRow = tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool]

# The exact library row `ReviewLibrarySnapshot` declares, annotated for the
# same reason (#711). The double emitted five of these seven columns behind a
# standing `type: ignore[misc]`, so `suspended_destinations`,
# `shadow_abstention_destinations` and `terminal_document` reached
# `BrowseReviewLibrary.library()` as the padding its `len(row)` guards
# supplied rather than as anything the double had said.
_LibraryRow = tuple[
    str | None,
    str,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    str | None,
    int,
]


class FakeReviewRepository:
    def __init__(self, case: ReviewCase, report) -> None:
        self.case = case
        self.report = report
        self.browse_cases = [(case, report)]
        self.browse_without_cases: list[MetadataReport] = []
        self.advice: dict[str, object] = {
            "results": [],
            "state": "not_requested",
        }
        self.failures: list[int] = []
        self.sessions: dict[str, tuple[str, str, int, int | None]] = {}
        self.decisions: dict[str, str] = {}
        self.automatic_selected_values: tuple[tuple[str, dict[str, object]], ...] = ()
        self.history: list[dict[str, object]] = [
            {
                "checkpoint": 1,
                "kind": "admitted",
                "reason": "actionable",
                "transition_id": "review_transition_" + "a" * 64,
            }
        ]
        # Destinations whose recorded decision has advanced past the reversible
        # effect states, and can therefore no longer be changed.
        self.override_blocked_destinations: tuple[str, ...] = ()
        # Capability suspensions keyed by `(connection, destination)`, read by
        # the detail composition whenever a real applier is attached.
        self.capability_suspensions: dict[tuple[str, str], str] = {}
        self.revoke_failure = False
        self.revoke_result = "revoked"
        self.rotate_failure = False
        self.rotate_result = "rotated"
        self.existence_failure = False
        self.case_current = True
        self.current_successor: str | None = None
        self.successor_failure = False
        self.policy_documents: dict[str, str] = {}
        self.policy_count_calls: list[str] = []
        builtin = MetadataPolicyProfile.builtin()
        self.active_policy_id = builtin.policy_id
        self.policy_transition: PolicyTransition | None = None
        self.shadow_mode = "review"
        self.shadow_outcomes: tuple[tuple[str, int], ...] = ()
        self.case_ready_at = 990
        # The two trailing attention columns the real adapter selects, keyed by
        # destination so a work's rows carry different values: the shadow
        # disposition recorded for that destination, and the cause when
        # automatic handling is suspended for it.
        self.attention_dispositions: dict[str, str] = {}
        self.attention_causes: dict[str, ReviewFieldCause] = {}
        self.policy_attention_destinations: tuple[str, ...] = ()
        # The four library columns the real adapter derives per row, keyed so a
        # page carries a different value for each work: the first three by the
        # review case the adapter looks them up by, the terminal document by
        # the target item the adapter reads it for. A work absent from a map
        # gets the empty answer, which is the adapter's answer for a row with
        # no case or no failed work.
        self.library_automatic_attention: dict[str, tuple[str, ...]] = {}
        self.library_suspensions: dict[str, tuple[str, ...]] = {}
        self.library_shadow_abstentions: dict[str, tuple[str, ...]] = {}
        self.library_terminal_documents: dict[str, str] = {}
        self.library_attention_counts: dict[str, int] = {}
        self.preparation_presentation_value: PreparationPresentation | None = None
        self.dismissed_preparation_runs: list[str] = []
        self.dismiss_preparation_error: Exception | None = None

    def preparation_presentation(self, _now: int) -> PreparationPresentation | None:
        return self.preparation_presentation_value

    def dismiss_preparation_presentation(self, run_id: str, _dismissed_at: int) -> None:
        if self.dismiss_preparation_error is not None:
            raise self.dismiss_preparation_error
        presentation = self.preparation_presentation_value
        if presentation is None or presentation.run_id != run_id:
            raise PreparationPresentationError("stale")
        if not presentation.dismissible:
            raise PreparationPresentationError("not_dismissible")
        self.dismissed_preparation_runs.append(run_id)
        self.preparation_presentation_value = None

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        if review_case_id != self.case.review_case_id:
            return None
        return self.case_ready_at

    def capture_policy_preview(
        self, libraries: tuple[str, ...], limit: int
    ) -> PolicyPreviewCapture:
        return PolicyPreviewCapture(
            MetadataPolicyProfile.from_data(json.loads(self.active_metadata_policy())),
            1 if self.policy_transition is None else self.policy_transition.generation,
            tuple(
                PolicyPreviewLibraryCapture(
                    library, None, self.current_unapplied_count(library), ()
                )
                for library in libraries
            ),
        )

    def current_unapplied_count(self, library: str) -> int:
        self.policy_count_calls.append(library)
        return 0

    def create_metadata_policy_draft(self, document: str) -> str:
        policy_id = "policy_" + hashlib.sha256(document.encode()).hexdigest()
        self.policy_documents[policy_id] = document
        return policy_id

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        return self.policy_documents[policy_id]

    def active_metadata_policy(self) -> str:
        if self.active_policy_id == MetadataPolicyProfile.builtin().policy_id:
            return MetadataPolicyProfile.builtin().canonical_json
        return self.policy_documents[self.active_policy_id]

    def activate_metadata_policy(
        self,
        policy_id,
        expected_active_policy_id,
        resolutions,
        activated_at,
        *,
        expected_active_generation,
    ):
        generation = (
            1 if self.policy_transition is None else self.policy_transition.generation
        )
        if (
            expected_active_policy_id != self.active_policy_id
            or expected_active_generation != generation
        ):
            # The real seam signals contention with a typed condition, and this
            # double only ever satisfied the old message search by accident of
            # the word it chose.
            raise PolicyContentionError("stale", "policy state moved")
        if policy_id == self.active_policy_id:
            return None
        previous = self.active_policy_id
        self.active_policy_id = policy_id
        self.policy_transition = PolicyTransition(
            "policy_transition_" + "a" * 64,
            previous,
            policy_id,
            generation + 1,
            "completed",
            0,
            0,
            0,
            False,
        )
        return self.policy_transition

    def metadata_policy_transition(self):
        return self.policy_transition

    def retry_metadata_policy_transition(self, transition_id, retried_at):
        return self.policy_transition

    def acknowledge_metadata_policy_transition(self, transition_id, acknowledged_at):
        value = cast(PolicyTransition, self.policy_transition)
        self.policy_transition = PolicyTransition(
            value.transition_id,
            value.from_policy_id,
            value.to_policy_id,
            value.generation,
            value.state,
            value.completed,
            value.total,
            value.failed,
            True,
        )
        return self.policy_transition

    def current_resolved_policy_id(self, library):
        return resolve_policy(
            MetadataPolicyProfile.from_data(json.loads(self.active_metadata_policy())),
            library,
        ).resolved_id

    def shadow_posture(self):
        return self.shadow_mode

    def enter_shadow_preview(self, updated_at):
        self.shadow_mode = "preview"
        return self.shadow_mode

    def list_shadow_outcomes(self, limit):
        return self.shadow_outcomes[:limit]

    def begin_login(self, now: int, window_seconds: int, maximum_failures: int) -> bool:
        self.failures = [
            value for value in self.failures if value >= now - window_seconds
        ]
        permitted = len(self.failures) < maximum_failures
        if permitted:
            self.failures.append(now)
        return permitted

    def create_review_session(
        self, session_digest: str, csrf_digest: str, created_at: int, expires_at: int
    ) -> None:
        self.sessions[session_digest] = ("owner", csrf_digest, expires_at, None)
        self.failures.clear()

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        actor, csrf, expires, revoked = self.sessions[session_digest]
        if revoked is not None or expires <= now:
            raise RuntimeError
        return actor, csrf, expires

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        if self.rotate_failure:
            raise RuntimeError
        if self.rotate_result != "rotated":
            return self.rotate_result
        actor, _, expires, revoked = self.sessions[session_digest]
        if revoked is not None or expires <= now:
            return "unauthorized"
        self.sessions[session_digest] = (actor, csrf_digest, expires, None)
        return "rotated"

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        if self.revoke_failure:
            raise RuntimeError
        if self.revoke_result != "revoked":
            return self.revoke_result
        session = self.sessions.get(session_digest)
        if session is None:
            return "unauthorized"
        actor, csrf, expires, revoked = session
        if revoked is not None or expires <= now:
            return "unauthorized"
        if csrf != csrf_digest:
            return "csrf"
        self.sessions[session_digest] = (actor, csrf, expires, now)
        return "revoked"

    def review_case_exists(self, review_case_id: str) -> bool:
        if self.existence_failure:
            raise RuntimeError
        return review_case_id == self.case.review_case_id

    def get_review_case(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return json.dumps(
            {
                "case": self.case.to_data(),
                "current": self.case_current,
                "schema_version": "review-case-get-v1",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def get_review_case_history(
        self, review_case_id: str, limit: int
    ) -> tuple[dict[str, object], ...]:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return tuple(self.history[-limit:])

    def get_review_case_decisions(self, review_case_id: str) -> tuple[str, ...]:
        return tuple(self.decisions.values())

    def record_authored_description_decision(
        self,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_digest: str,
        now: int,
    ) -> str:
        # Authored admission is exercised against the real persisted service;
        # ordinary fake decisions cannot mint authored authority.
        raise ValueError("authored description candidate is not configured")

    def request_description_retry(
        self, review_case_id: str, now: int
    ) -> tuple[str, str]:
        raise ValueError("description preparation is not configured")

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        return next(
            (
                document
                for document in self.decisions.values()
                if HumanDecision.from_json(document).idempotency_digest
                == idempotency_digest
            ),
            None,
        )

    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
        cover_confirmation: dict[str, object] | None = None,
    ) -> str:
        del cover_consideration_id
        existing = self.decisions.get(decision.destination)
        if existing is not None:
            return existing
        self.decisions[decision.destination] = decision.to_json()
        event_id = (
            "human_decision_event_"
            + hashlib.sha256(
                ("human-decision-event-v1:" + decision.decision_id).encode("utf-8")
            ).hexdigest()
        )
        target = next(
            field
            for field in self.case.target["fields"]
            if field["destination"] == decision.destination
        )
        current = target.get("value") if target.get("state") == "value" else None
        kind = (
            "list"
            if decision.destination in LIST_DESTINATIONS
            else "series"
            if decision.destination == "series"
            else "text"
        )
        self.history.append(
            {
                "actor": decision.actor,
                "decision_id": decision.decision_id,
                "destination": decision.destination,
                "event_id": event_id,
                "kind": "human_decision_recorded",
                "occurred_at": decision.occurred_at,
                "outcome": decision.outcome,
                "previous_value": None
                if current is None
                else {"kind": kind, "value": current},
                "review_case_id": decision.review_case_id,
                "schema_version": "human-decision-event-v1",
                "selected_value": decision.selected_value
                if decision.outcome == "accept"
                else None
                if decision.outcome == "reject" or current is None
                else {"kind": kind, "value": current},
            }
        )
        return decision.to_json()

    def get_review_case_report(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return self.report.to_json()

    def current_review_case_successor(self, review_case_id: str) -> str | None:
        if self.successor_failure:
            raise RuntimeError
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return self.current_successor

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        return CurrentReviewCaseSnapshot(
            case_document=self.get_review_case(review_case_id),
            report_document=self.get_review_case_report(review_case_id),
            report=self.report,
            decision_documents=self.get_review_case_decisions(review_case_id),
            automatic_selected_values=self.automatic_selected_values,
            history=(
                ()
                if history_limit is None
                else self.get_review_case_history(review_case_id, history_limit)
            ),
            override_blocked_destinations=self.override_blocked_destinations,
            policy_attention_destinations=self.policy_attention_destinations,
            resolved_policy_document=None,
        )

    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None:
        return self.capability_suspensions.get((connection, destination))

    @staticmethod
    def _field_attention(case: ReviewCase, proposal: dict[str, object]) -> bool:
        result = proposal["result"]
        assert isinstance(result, dict)
        return result["outcome"] == "unresolved_choice" or (
            proposal["destination"] in {"title", "subtitle", "series"}
            and any(
                sibling["result"]["reasons"] == ["no_safe_bibliographic_combination"]
                for sibling in case.proposals
            )
        )

    @staticmethod
    def _attention(case: ReviewCase) -> bool:
        return any(
            FakeReviewRepository._field_attention(case, proposal)
            for proposal in case.proposals
        )

    def _searched(self, search: str | None, library_labels: LibraryLabels = ()):
        if search is None:
            return self.browse_cases
        query = search.casefold()
        return [
            (case, report)
            for case, report in self.browse_cases
            if query
            in " ".join(
                (
                    report.target.current_title or "",
                    " ".join(
                        value
                        for field in report.target.fields
                        if field.destination is TargetDestination.AUTHORS
                        and type(field.value) is tuple
                        for value in field.value
                    ),
                    next(
                        (
                            label
                            for connection, library, label in library_labels
                            if (connection, library)
                            == (report.target.connection, report.target.library)
                        ),
                        "",
                    ),
                    report.target.library,
                    report.target.item,
                    report.file.format,
                )
            ).casefold()
        ]

    def _current_review_cases(
        self,
        search: str | None,
        attention: bool | None,
        library_labels: LibraryLabels = (),
    ) -> list[tuple[ReviewCase | None, MetadataReport]]:
        rows: list[tuple[ReviewCase | None, MetadataReport]] = [
            (case, report)
            for case, report in self._searched(search, library_labels)
            if attention is None or self._attention(case) is attention
        ]
        if attention is not True:
            query = None if search is None else search.casefold()
            rows.extend(
                (None, report)
                for report in self.browse_without_cases
                if query is None
                or query
                in " ".join(
                    (
                        report.target.current_title or "",
                        " ".join(
                            value
                            for field in report.target.fields
                            if field.destination is TargetDestination.AUTHORS
                            and type(field.value) is tuple
                            for value in cast(tuple[str, ...], field.value)
                        ),
                        next(
                            (
                                label
                                for connection, library, label in library_labels
                                if (connection, library)
                                == (report.target.connection, report.target.library)
                            ),
                            "",
                        ),
                        report.target.library,
                        report.target.item,
                        report.file.format,
                    )
                ).casefold()
            )
        rows.sort(
            key=lambda value: (
                value[0] is None or not self._attention(value[0]),
                (value[1].target.current_title or "").casefold(),
                "" if value[0] is None else value[0].review_case_id,
            )
        )
        return rows

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
        library_labels: LibraryLabels = (),
    ) -> tuple[tuple[str | None, str], ...]:
        return tuple(
            (None if case is None else case.to_json(), report.to_json())
            for case, report in self._current_review_cases(
                search, attention, library_labels
            )[offset : offset + limit]
        )

    def read_current_report_document(
        self, connection: str, library: str, item: str
    ) -> str | None:
        """Answer for exactly the reports the library page already lists.

        `None` where no such work is listed; the store-failure path is driven
        by tests that replace this method with one that raises.
        """
        for report in (
            *self.browse_without_cases,
            *(value for _case, value in self.browse_cases),
        ):
            target = report.target
            if (target.connection, target.library, target.item) == (
                connection,
                library,
                item,
            ):
                return report.to_json()
        return None

    def count_current_review_cases(
        self,
        search: str | None,
        library_labels: LibraryLabels = (),
    ) -> tuple[int, int]:
        rows = self._current_review_cases(search, None, library_labels)[:100_000]
        return len(rows), sum(
            case is not None and self._attention(case) for case, _ in rows
        )

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
        library_labels: LibraryLabels = (),
        validated: ValidatedDocumentCache | None = None,
    ) -> ReviewLibrarySnapshot:
        rows = self.list_current_review_case_documents(
            search, limit, offset, attention, library_labels
        )
        total, attention_total = self.count_current_review_cases(search, library_labels)
        identified = [
            (
                None
                if case_document is None
                else ReviewCase.from_json(case_document).review_case_id,
                case_document,
                report_document,
            )
            for case_document, report_document in rows
        ]
        # Annotated so a narrowed row fails the gate. This once had to be one
        # comprehension as well, because that form reports `misc` while
        # appending reports `arg-type`, which was disabled for `tests.*`; #693
        # enforces `arg-type` here, so both forms are now guarded (#703, #711).
        projected: list[_LibraryRow] = [
            (
                case_document,
                report_document,
                () if case_id is None else self.get_review_case_decisions(case_id),
                ()
                if case_id is None
                else self.library_automatic_attention.get(case_id, ()),
                () if case_id is None else self.library_suspensions.get(case_id, ()),
                ()
                if case_id is None
                else self.library_shadow_abstentions.get(case_id, ()),
                self.library_terminal_documents.get(
                    MetadataReport.from_json(report_document).target.item
                ),
                0
                if case_id is None
                else self.library_attention_counts.get(
                    case_id,
                    sum(
                        self._field_attention(
                            ReviewCase.from_json(cast(str, case_document)), proposal
                        )
                        for proposal in ReviewCase.from_json(
                            cast(str, case_document)
                        ).proposals
                    ),
                ),
            )
            for case_id, case_document, report_document in identified
        ]
        return ReviewLibrarySnapshot(
            rows=tuple(projected),
            total=total,
            attention_total=attention_total,
        )

    def list_current_attention_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> tuple[_AttentionRow, ...]:
        rows = [
            (case, report, proposal)
            for case, report in self._searched(search, library_labels)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
            or proposal["destination"] in self.policy_attention_destinations
        ]
        projected: list[_AttentionRow] = [
            (
                case.to_json(),
                report.to_json(),
                cast(str, proposal["destination"]),
                sum(self._field_attention(case, other) for other in case.proposals),
                self.attention_dispositions.get(cast(str, proposal["destination"])),
                self.attention_causes.get(cast(str, proposal["destination"])),
                proposal["destination"] in self.policy_attention_destinations,
            )
            for case, report, proposal in rows[offset : offset + limit]
        ]
        return tuple(projected)

    def count_current_attention_fields(
        self,
        search: str | None,
        library_labels: LibraryLabels = (),
    ) -> int:
        return sum(
            1
            for case, _ in self._searched(search, library_labels)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
        )

    def read_review_attention(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> ReviewAttentionSnapshot:
        return ReviewAttentionSnapshot(
            rows=self.list_current_attention_documents(
                search, limit, offset, library_labels
            ),
            total=self.count_current_attention_fields(search, library_labels),
        )


class FakePreparation:
    recovery_required = False
    recovery_incidents = ()
    libraries = (("primary", "fiction", "lib-1"),)

    def __init__(self) -> None:
        self.is_paused = False
        self.status_calls = 0
        self.requests: list[tuple[str, str, PreparationTrigger]] = []

    def attach_updates(self, announce: object) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def progress(self, connection: str, library: str):
        if (connection, library) != ("primary", "fiction"):
            return None
        return PipelineProgress(
            "review_scan_" + "a" * 64,
            connection,
            library,
            "processing",
            2,
            5,
            1,
            0,
            0,
            False,
            0,
            (
                ("queued", 1),
                ("extracting", 1),
                ("providers", 1),
                ("proposals", 0),
                ("admission", 0),
                ("projection", 0),
                ("ready", 1),
                ("recorded", 0),
                ("failed", 1),
            ),
        )

    def request(
        self,
        connection: str,
        library: str,
        *,
        trigger: PreparationTrigger,
    ):
        self.requests.append((connection, library, trigger))
        return self.progress(connection, library)

    def status(self):
        self.status_calls += 1
        return ((self.progress("primary", "fiction"),), self.is_paused)

    def connection_conditions(self):
        return ()

    def check_connection(self, connection: str) -> bool:
        return connection == "primary"

    def paused(self) -> bool:
        return self.is_paused

    def set_paused(self, paused: bool) -> None:
        self.is_paused = paused

    def retry(self, connection: str, library: str):
        return self.progress(connection, library)


# A ledger the installed surface can actually be looked at.
#
# The row this harness used to serve was one hand-written display literal with
# no work title, no field, and no values, so every Activity proof that drove a
# browser -- including the glyph-width guard -- was measuring an eight-character
# change column and a fabricated evidence label. It could not have shown the row
# #636 exists to produce, and it could not have failed if that row were wrong.
#
# So the events below are not display text. They are the durable records each
# source kind actually carries, run through the same `readable_activity_event`
# the application layer calls, against a report the decision records name by
# fingerprint. Break the projection and this ledger changes; the fixture cannot
# quietly disagree with the product because it does not restate any of its
# words. The values are chosen to be the widest realistic ones a real library
# produces -- a long title, a series with an index, unicode contributors, an
# unbounded description -- so a layout measured here is measured against
# something that can overflow.
_LEDGER_DESCRIPTION = (
    "A quiet, unhurried account of the salt marshes east of the station, "
    "collected over eleven winters and set down without much hurry, which is "
    "the sort of description a real catalogue supplies and a row column has "
    "to survive without growing to meet it."
)


def _ledger_report() -> MetadataReport:
    """One report the decision records below are pinned to by fingerprint."""
    values: dict[TargetDestination, object] = {
        TargetDestination.TITLE: "Paper Moons: Larkspur",
        TargetDestination.AUTHORS: ("Ursula K. Le Guin", "雨の地図"),
        TargetDestination.SERIES: (TargetSeries("Earthsea Cycle", "1"),),
        TargetDestination.DESCRIPTION: _LEDGER_DESCRIPTION,
    }
    fields = tuple(
        TargetField(destination, TargetFieldState.VALUE, values[destination])  # type: ignore[arg-type]
        if destination in values
        else TargetField(destination, TargetFieldState.MISSING)
        for destination in TargetDestination
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "fiction", "book-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


def _seeded_projection() -> ContinuousGuardedApplication:
    """One real, seeded Activity projection shared by every fake application.

    The ledger this serves is written through the real guarded application and
    read through the real activity query, so the installed surface shows rows
    an owner could actually have produced. Before this, the page was served a
    single hand-written literal, which meant every width measurement, filter
    chip, search box, date range and page control on it had only ever been
    exercised against text no event produces (#636).

    Seeding costs well under a tenth of a second, but it is built once per
    process because the ledger is immutable once written and every caller
    wants the identical one.
    """
    global _SEEDED_PROJECTION
    if _SEEDED_PROJECTION is None:
        root = Path(tempfile.mkdtemp(prefix="ml-activity-seed-"))
        atexit.register(shutil.rmtree, root, True)
        seeded = seed_activity(root)
        _SEEDED_PROJECTION = ContinuousGuardedApplication(
            seeded.repository,
            {},
            {},
            PersistedFieldEffectAuthority(seeded.repository),
            (("primary", "books"),),
            seeded.reader_now,
            lambda *_: None,
            activity_only=True,
        )
    return _SEEDED_PROJECTION


_SEEDED_PROJECTION: ContinuousGuardedApplication | None = None


class FakeActivityApplication(ContinuousGuardedApplication):
    # The base `__init__` never runs here, so the real consent read has no
    # state to read. A review surface asks this to choose between "nothing is
    # written yet" and "each decision began applying"; the double answers for
    # the whole fake so a test can put the surface on either branch.
    writes_consented = False

    def __init__(self) -> None:
        self.enabled_libraries: set[str] = set()
        self.paused = False

    def destinations_consented(
        self, connection: str, library: str, destinations: tuple[str, ...]
    ) -> bool:
        del connection, library, destinations
        return self.writes_consented

    def attach_serialization(self, serialize) -> None:
        self.serialize = serialize

    def attach_timing(self, timing) -> None:
        return None

    def start(self):
        return (0, 0, 0)

    # Every production call site invokes `begin_drain()` with no argument, so
    # this fake deliberately narrows the base signature.
    def begin_drain(self) -> None:  # type: ignore[override]  # reason: the fake narrows the base signature; every production call site invokes it with no argument
        return None

    def close_admission(self) -> None:
        return None

    def wait_for_drain(self, deadline=None) -> None:
        return None

    def activity(self, **query):
        # Only the ledger halves are answered by the real projection. Consent,
        # capabilities and the waiting counts stay with the double because
        # they are what #639's proofs drive, and moving them would put that
        # unit's coverage at risk to improve this one's.
        projected = _seeded_projection().activity(**query)
        return {
            "backup_ready": None,
            "configured_libraries": [
                {
                    "connection": "primary",
                    "library": "fiction",
                    "prepared": {"automatic": 1, "human": 1, "total": 2},
                },
                {
                    "connection": "primary",
                    "library": "history",
                    "prepared": {"automatic": 1, "human": 0, "total": 1},
                },
            ],
            "consented_libraries": [
                {"connection": "primary", "library": library}
                for library in sorted(self.enabled_libraries)
            ],
            "capabilities": [
                {
                    "connection": "primary",
                    "library": "fiction",
                    "state": "allowed",
                    "destinations": [
                        {
                            "destination": "title",
                            "state": "allowed",
                            "consented": "fiction" in self.enabled_libraries,
                            "prepared": 2,
                        }
                    ],
                },
                {
                    "connection": "primary",
                    "library": "history",
                    "state": "allowed",
                    "destinations": [
                        {
                            "destination": "title",
                            "state": "allowed",
                            "consented": "history" in self.enabled_libraries,
                            "prepared": 1,
                        }
                    ],
                },
            ],
            "counts": {},
            "enabled_libraries": [
                {"connection": "primary", "library": library}
                for library in sorted(self.enabled_libraries)
            ],
            "events": projected["events"],
            "example": None,
            "last_failure": None,
            "paused": self.paused,
            "ledger": projected["ledger"],
            "current_waiting": None,
            "prepared": {"automatic": 1, "human": 1, "total": 2},
            "schema_version": "continuous-field-activity-v3",
            "suspensions": [],
            "waiting": {
                "applying": 0,
                "awaiting_permission": 0 if self.enabled_libraries else 3,
                "held_for_recovery": 0,
                "retrying": 0,
                "scheduled": 3 if self.enabled_libraries else 0,
            },
        }

    def activity_permission_readiness(self):
        history = self.activity()
        return {
            **{
                key: history[key]
                for key in ("configured_libraries", "capabilities", "prepared")
            },
            "backup_ready": True,
            "example": {
                "after": "After",
                "before": "Before",
                "destination": "title",
                "library": "fiction",
                "work_id": "book-1",
            },
        }

    def activity_transitions(
        self, event_id: str, after_sequence: int | None = None
    ) -> dict[str, object]:
        return _seeded_projection().activity_transitions(event_id, after_sequence)

    def enable(self, grants):
        self.enabled_libraries.update(library for library, _ in grants)
        return self.activity()

    def revoke(self, connection, library):
        self.enabled_libraries.discard(library)
        return self.activity()

    def set_paused(self, paused):
        self.paused = paused

    def activate_human_override(self, review_case_id, destination):
        return None

    def return_to_automatic(self, review_case_id, destination):
        return None


RepositoryT = TypeVar("RepositoryT", bound=FakeReviewRepository)


@dataclass
class ReviewWebContext(Generic[RepositoryT]):
    """One authenticated transport-test application over the fake repository.

    The shared builder both review-web cohorts use, rather than the
    `TestCase` base they inherited: a support-tier helper keeps them
    independent of each other, which
    `docs/development/testing-strategy.md` requires. `now` and `client`
    stay rebindable because the transport cohort advances the clock and
    substitutes applications mid-test, and the clock reads `now` back
    through the instance so a test that advances it is seen.
    """

    temporary: tempfile.TemporaryDirectory[str]
    hash_path: Path
    case: ReviewCase
    repository: RepositoryT
    now: int
    client: TestClient
    origin: dict[str, str]
    cleanup: ExitStack

    def login(self) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/session/login",
            headers=self.origin,
            json={"password": "correct horse"},
        )
        assert response.status_code == 200, response.text
        self.client.headers["x-ml-build-identity"] = TEST_REACT_BUILD_IDENTITY
        return response.json()

    def close(self) -> None:
        # The stack exits any client a test entered before the field is
        # replaced, so a substituted application still shuts down.
        self.cleanup.close()
        self.client.close()
        self.temporary.cleanup()


def review_web_context(
    repository_factory: Callable[[ReviewCase, MetadataReport], RepositoryT],
    *,
    mode: str = "direct",
) -> Iterator[ReviewWebContext[RepositoryT]]:
    """Yield one signed-out transport application over `repository_factory`."""

    temporary = tempfile.TemporaryDirectory()
    hash_path = Path(temporary.name) / "owner.hash"
    hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    repository = repository_factory(case, report)
    origin = "http://review.test" if mode == "http" else "https://review.test"
    configuration = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt" if mode == "direct" else None,
        tls_private_key_file="/synthetic/review.key" if mode == "direct" else None,
        trusted_proxy_ips=("127.0.0.1",) if mode == "proxy" else (),
        canonical_origin=origin,
        owner_password_hash_file=str(hash_path),
        session_lifetime_seconds=600,
    )
    # The clock reads `context.now` when it is called, not when it is built,
    # so a test that advances the field is seen by the running application.
    context: ReviewWebContext[RepositoryT]
    client = TestClient(
        create_transport_test_app(repository, configuration, clock=lambda: context.now),
        base_url="http://review.test" if mode == "proxy" else origin,
        client=("127.0.0.1", 12345),
        headers={"x-forwarded-host": "review.test", "x-forwarded-proto": "https"}
        if mode == "proxy"
        else {},
    )
    context = ReviewWebContext(
        temporary=temporary,
        hash_path=hash_path,
        case=case,
        repository=repository,
        now=1_000,
        client=client,
        origin={"Origin": origin},
        cleanup=ExitStack(),
    )
    yield context
    context.close()
