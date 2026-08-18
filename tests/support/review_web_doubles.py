"""Shared review-web test doubles and the authenticated transport fixture.

The fake repository, preparation control, and activity application are
used by the review-web cohorts and by three modules outside them, which
previously reached into another module's tests to get them.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.metadata_policy import (
    PolicyContentionError,
    PolicyTransition,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineProgress,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.review_field_classification import (
    ReviewFieldCause,
)
from tests.support.review_cases import _report
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

# The exact attention row `ReviewAttentionSnapshot` declares. `rows` below is
# annotated with it so a narrowed row fails the gate rather than reaching the
# reader as padding (#703).
_AttentionRow = tuple[str, str, str, int, str | None, ReviewFieldCause | None]

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
        self.revoke_failure = False
        self.revoke_result = "revoked"
        self.rotate_failure = False
        self.rotate_result = "rotated"
        self.existence_failure = False
        self.case_current = True
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

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        if review_case_id != self.case.review_case_id:
            return None
        return self.case_ready_at

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
        self, policy_id, expected_active_policy_id, resolutions, activated_at
    ):
        if expected_active_policy_id != self.active_policy_id:
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
            2,
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
        return (
            {
                "checkpoint": 1,
                "kind": "admitted",
                "reason": "actionable",
                "transition_id": "review_transition_" + "a" * 64,
            },
        )

    def get_review_case_decisions(self, review_case_id: str) -> tuple[str, ...]:
        return tuple(self.decisions.values())

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
    ) -> str:
        del cover_consideration_id
        existing = self.decisions.get(decision.destination)
        if existing is not None:
            return existing
        self.decisions[decision.destination] = decision.to_json()
        return decision.to_json()

    def get_review_case_report(self, review_case_id: str) -> str:
        if review_case_id != self.case.review_case_id:
            raise RuntimeError
        return self.report.to_json()

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        return CurrentReviewCaseSnapshot(
            case_document=self.get_review_case(review_case_id),
            report_document=self.get_review_case_report(review_case_id),
            decision_documents=self.get_review_case_decisions(review_case_id),
            history=(
                ()
                if history_limit is None
                else self.get_review_case_history(review_case_id, history_limit)
            ),
        )

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

    def _searched(self, search: str | None):
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
                    report.target.library,
                    report.target.item,
                    report.file.format,
                )
            ).casefold()
        ]

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str | None, str], ...]:
        rows = [
            (case, report)
            for case, report in self._searched(search)
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
        return tuple(
            (None if case is None else case.to_json(), report.to_json())
            for case, report in rows[offset : offset + limit]
        )

    def count_current_review_cases(self, search: str | None) -> tuple[int, int]:
        rows = self.list_current_review_case_documents(search, 100_000, 0, None)
        return len(rows), sum(
            case_document is not None
            and self._attention(ReviewCase.from_json(case_document))
            for case_document, _ in rows
        )

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        rows = self.list_current_review_case_documents(search, limit, offset, attention)
        total, attention_total = self.count_current_review_cases(search)
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
            )
            for case_id, case_document, report_document in identified
        ]
        return ReviewLibrarySnapshot(
            rows=tuple(projected),
            total=total,
            attention_total=attention_total,
        )

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[_AttentionRow, ...]:
        rows: list[_AttentionRow] = [
            (
                case.to_json(),
                report.to_json(),
                cast(str, proposal["destination"]),
                sum(self._field_attention(case, other) for other in case.proposals),
                self.attention_dispositions.get(cast(str, proposal["destination"])),
                self.attention_causes.get(cast(str, proposal["destination"])),
            )
            for case, report in self._searched(search)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
        ]
        return tuple(rows[offset : offset + limit])

    def count_current_attention_fields(self, search: str | None) -> int:
        return sum(
            1
            for case, _ in self._searched(search)
            for proposal in case.proposals
            if self._field_attention(case, proposal)
        )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        return ReviewAttentionSnapshot(
            rows=self.list_current_attention_documents(search, limit, offset),
            total=self.count_current_attention_fields(search),
        )


class FakePreparation:
    libraries = (("primary", "fiction", "lib-1"),)

    def __init__(self) -> None:
        self.is_paused = False
        self.status_calls = 0

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
            False,
        )

    def request(self, connection: str, library: str):
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


class FakeActivityApplication(ContinuousGuardedApplication):
    def __init__(self) -> None:
        self.enabled_libraries: set[str] = set()
        self.paused = False

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
        return {
            "backup_ready": True,
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
                        }
                    ],
                },
            ],
            "counts": {},
            "enabled_libraries": [
                {"connection": "primary", "library": library}
                for library in sorted(self.enabled_libraries)
            ],
            "events": [
                {
                    "destination": "title",
                    "event_id": "event-1",
                    "event_type": "metadata",
                    "evidence": [
                        {"label": "Recorded automatic decision", "value": "verified"}
                    ],
                    "failure": None,
                    "library": "fiction",
                    "occurred_at": 1,
                    "origin": "automatic_policy",
                    "state": "verified",
                    "work_id": "book-1",
                }
            ],
            "example": {
                "after": "After",
                "before": "Before",
                "destination": "title",
                "library": "fiction",
                "work_id": "book-1",
            },
            "last_failure": None,
            "paused": self.paused,
            "ledger": {
                "event_type": query.get("event_type", "all"),
                "page": query.get("page", 1),
                "page_count": 1,
                "page_size": query.get("page_size", 25),
                "query": query.get("search"),
                "range_days": query.get("range_days", 30),
                "total": 1,
            },
            "current_waiting": None,
            "prepared": {"automatic": 1, "human": 1, "total": 2},
            "schema_version": "continuous-field-activity-v2",
            "suspensions": [],
        }

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
) -> Iterator[ReviewWebContext[RepositoryT]]:
    """Yield one signed-out transport application over `repository_factory`."""

    temporary = tempfile.TemporaryDirectory()
    hash_path = Path(temporary.name) / "owner.hash"
    hash_path.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(hash_path, 0o600)
    report = _report()
    case = ReviewCase.create(evaluate_report(report), report)
    repository = repository_factory(case, report)
    configuration = ReviewWebConfiguration(
        canonical_origin="https://review.test",
        owner_password_hash_file=str(hash_path),
        session_lifetime_seconds=600,
    )
    # The clock reads `context.now` when it is called, not when it is built,
    # so a test that advances the field is seen by the running application.
    context: ReviewWebContext[RepositoryT]
    client = TestClient(
        create_transport_test_app(repository, configuration, clock=lambda: context.now),
        base_url="https://review.test",
    )
    context = ReviewWebContext(
        temporary=temporary,
        hash_path=hash_path,
        case=case,
        repository=repository,
        now=1_000,
        client=client,
        origin={"Origin": "https://review.test"},
        cleanup=ExitStack(),
    )
    yield context
    context.close()
