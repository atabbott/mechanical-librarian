"""One real store and the guarded fake pipeline for owner commands (#1285).

Every plan here is made the way the scheduler makes one: the persisted
authority resolves the decision against the current case, so `pre_value` is
the case's target value or a predecessor's desired value, never a test's
guess. Only the library is fake.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase

CAPABILITY = "c" * 64


def rewritten(
    report: MetadataReport, **values: str | tuple[str, ...]
) -> MetadataReport:
    """The work as the library reads it back after a write: the same report
    with those target fields now holding these values."""
    fields = tuple(
        TargetField(field.destination, TargetFieldState.VALUE, values[name])
        if (name := field.destination.value) in values
        else field
        for field in report.target.fields
    )
    target = report.target
    return MetadataReport.create(
        target=TargetFacts(target.connection, target.library, target.item, fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


class OwnerStore:
    """A store, its current case and a guarded service over a fake library."""

    def __init__(self, root: Path, report: MetadataReport | None) -> None:
        self.repository = open_repository(root / "owner.sqlite", cross_thread=True)
        self.target = FakeTargetService()
        self.patches: list[str] = []
        apply = self.target.apply

        def counted(plan):
            self.patches.append(plan.plan_id)
            return apply(plan)

        self.target.apply = counted  # type: ignore[method-assign]  # reason: the fake's write is the seam these tests count, so a refused or staled plan is proven to have issued no request.
        self.consent = FakeConsentCurrentness(
            self.repository.field_effect_consent_envelope_identity
        )
        self.ticks = 1_000
        self.service = GuardedFieldApplication(
            self.repository,
            self.target,
            FakeBackupReadiness(),
            FakeCapabilityObserver(CAPABILITY),
            PersistedFieldEffectAuthority(self.repository),
            FakeDependencyCurrentness(),
            self.consent,
            PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover"),
            FakeForwardReevaluation(),
            self.clock,
        )
        self.case: ReviewCase
        if report is not None:
            self.publish(report)

    def clock(self) -> int:
        self.ticks += 1
        return self.ticks

    def publish(self, report: MetadataReport) -> ReviewCase:
        """Publish one report of the work and return its current case."""
        current = self.repository._connection.execute(
            "SELECT report_id FROM current_metadata_reports WHERE connection_name=? "
            "AND library_name=? AND item_id=? AND file_identity=?",
            (
                report.target.connection,
                report.target.library,
                report.target.item,
                report.file.identity,
            ),
        ).fetchone()
        self.repository.publish(report, lambda: True, current and current[0])
        proposal = evaluate_report(report)
        self.repository.publish_proposal(proposal)
        admitted = json.loads(self.repository.admit_review_case(proposal))
        assert admitted["status"] in {"admitted", "superseded", "replayed"}, admitted
        return self.load(admitted["review_case_id"])

    def load(self, review_case_id: str) -> ReviewCase:
        """Make one persisted case the one later commands are recorded on."""
        self.case = ReviewCase.create_from_data(
            json.loads(self.repository.get_review_case(review_case_id))["case"]
        )
        return self.case

    def decide(
        self,
        outcome: str,
        selected_value: Mapping[str, object] | None = None,
        *,
        destination: str = "title",
        case: ReviewCase | None = None,
        reverts_plan_id: str | None = None,
    ) -> HumanDecision:
        """Record one owner command on the case, through the repository."""
        case = case or self.case
        proposal = next(
            value for value in case.proposals if value["destination"] == destination
        )
        decision = HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=destination,
            actor="owner",
            outcome=outcome,
            selected_value=None if selected_value is None else dict(selected_value),
            idempotency_key=f"owner-command-{self.ticks:08d}",
            occurred_at=self.clock(),
            reverts_plan_id=reverts_plan_id,
        )
        self.repository.record_human_decision(decision)
        return decision

    def plan(
        self, decision: HumanDecision | AutomaticDecision, *, seed: bool = True
    ) -> FieldEffectOperation:
        """Plan the decision from its persisted authority, as the scheduler does.

        `seed` gives the fake library the plan's pre-image and version. A revert
        plan reseeds only after its caller has shown the fake already holds that
        value, because the fake's version token is not the case's.
        """
        target = self.case.target
        self.consent.allow(
            connection=str(target["connection"]),
            library=str(target["library"]),
            destination=decision.destination,
        )
        snapshot = PersistedFieldEffectAuthority(self.repository).resolve(decision)
        operation = self.service.plan(
            decision,
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination=snapshot.destination,
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest=CAPABILITY,
        )
        assert operation is not None
        if seed:
            self.target.seed(operation.plan)
        return operation

    def write(
        self, decision: HumanDecision | AutomaticDecision
    ) -> FieldEffectOperation:
        """Plan, apply and verify one decision, returning the verified effect."""
        operation = self.plan(decision)
        verified = self.service.apply(operation.plan.plan_id)
        assert (verified.state, verified.failure) == ("verified", None), verified
        return verified

    def candidates(self) -> dict[str, dict[str, object]]:
        target = self.case.target
        return {
            str(value["destination"]): value
            for value in self.repository.revert_candidates(
                str(target["connection"]), str(target["library"]), str(target["item"])
            )
        }

    def close(self) -> None:
        self.repository.close()
