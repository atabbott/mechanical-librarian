"""Leaving Automatic with an unchanged policy keeps every work's review case.

``enter_shadow_preview`` reactivates the unchanged policy, which retires every
current case for its successor. With the policy unchanged that successor is
the same proposal set, so preparation admits the same case again, starting a
new episode (#1568). An automatic decision recorded under Automatic stays on
that case and plans and writes nothing outside the Automatic posture.

The harness drives a real SQLite repository and the real
``ReviewPreparationPipeline`` with stages that call the real report, proposal,
admission and projection seams.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewAdmissionResult, ReviewCase
from tests.support.author_evidence import author_report

_PRODUCER = "f" * 64
_LINEAGE = "connection_name='primary' AND library_name='ebooks' AND item_id='item-1'"


@dataclass
class _Library:
    root: SQLiteMetadataReportRepository
    pipeline: ReviewPreparationPipeline
    report: MetadataReport
    admissions: list[tuple[str, str | None]]
    evaluations: list[str]
    now: list[int]

    def drain(self) -> None:
        for _ in range(32):
            if not self.pipeline.run_next():
                return
        raise AssertionError("preparation did not settle")

    def leave_automatic(self) -> None:
        assert self.root.enter_shadow_preview(20, ("ebooks",)) == "preview"
        self.now[0] = 30
        self.drain()

    def case_id(self) -> str:
        (case_id,) = self.root._connection.execute(
            "SELECT review_case_id FROM current_review_case_lineages WHERE " + _LINEAGE
        ).fetchone()
        assert type(case_id) is str
        return case_id

    def case(self) -> ReviewCase:
        return ReviewCase.create_from_data(
            json.loads(self.root.get_review_case(self.case_id()))["case"]
        )

    def automatic(self) -> AutomaticDecision:
        (document,) = self.root._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='authors'"
        ).fetchone()
        return AutomaticDecision.from_json(document)

    def pending(self) -> list[str]:
        return [
            json.loads(document)["decision_id"]
            for document in self.root.pending_field_effect_decision_documents(100)
        ]

    def attention(self) -> list[tuple[object, object]]:
        page = BrowseReviewLibrary(self.root).attention(
            search=None, limit=50, cursor=None
        )
        return [
            (item.get("review_case_id"), item.get("destination"))
            for item in cast(list[dict[str, object]], page["items"])
        ]

    def library(self, lens: str) -> list[dict[str, object]]:
        page = BrowseReviewLibrary(self.root).library(
            lens=lens, search=None, limit=50, cursor=None
        )
        return cast(list[dict[str, object]], page["items"])


def _resolved(
    repository: SQLiteMetadataReportRepository, library: str
) -> ResolvedMetadataPolicy:
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state"
    ).fetchone()[0]
    document = repository._connection.execute(
        "SELECT document FROM metadata_policy_resolutions WHERE generation=? AND library_name=?",
        (generation, library),
    ).fetchone()[0]
    return ResolvedMetadataPolicy.from_data(json.loads(document))


@pytest.fixture
def library(tmp_path: Path) -> Iterator[_Library]:
    """One work prepared under a qualified Automatic contributor-names policy."""
    database = tmp_path / "application.sqlite3"
    report = author_report(tmp_path / "media", "supported")
    root = open_repository(database, cross_thread=True)
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    destinations["contributor_names"] = "automatic"
    profile = MetadataPolicyProfile.create("Leave automatic", options, destinations)
    resolved = resolve_policy(profile, "ebooks")
    root.enter_shadow_preview(1, ())
    root.create_metadata_policy_draft(profile.canonical_json)
    root.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        (("ebooks", resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    root.activate_qualified_automatic(3)
    root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 4)
    discovery = root.claim_review_pipeline_action(5)
    assert discovery is not None
    root.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (
            CatalogueEntry(
                "primary",
                "ebooks",
                "lib-1",
                "item-1",
                1,
                "Synthetic work",
                ("Ada Example",),
                True,
                False,
                "singular-supported",
                report.file.identity,
                "/book.epub",
                "epub",
                "books",
            ),
        ),
        6,
    )
    now = [10]
    evaluations: list[str] = []
    admissions: list[tuple[str, str | None]] = []

    def extract(
        repository: ReviewPipelineRepository, _: PipelineAction, __: bool
    ) -> tuple[str, bool]:
        cast(SQLiteMetadataReportRepository, repository).publish(report, lambda: True)
        return report.report_id, False

    def propose(repository: ReviewPipelineRepository, report_id: str) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        value = MetadataReport.from_json(concrete.get(report_id))
        policy = _resolved(concrete, value.target.library)
        proposal = evaluate_report(
            value, policy.resolved_id, human_review_destinations(policy)
        )
        concrete.publish_proposal(proposal)
        evaluations.append(proposal.proposal_set_id)
        return proposal.proposal_set_id

    def admit(
        repository: ReviewPipelineRepository, proposal_set_id: str
    ) -> tuple[str, str | None]:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        proposal = ProposalSet.from_json(concrete.get_proposal(proposal_set_id))
        result = ReviewAdmissionResult.from_json(concrete.admit_review_case(proposal))
        admissions.append((result.status, result.review_case_id))
        return result.status, result.review_case_id

    def project(repository: ReviewPipelineRepository, action: Any) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        # As ReviewPipelineStages.project does.
        if type(action) is str:
            outcome = concrete.persist_shadow_outcome(action, now[0])
        else:
            outcome = concrete.persist_shadow_outcome(
                action.review_case_id, now[0], staged_action=action
            )
        return "stale" if outcome == "stale" else "complete"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(database, authority=root.authority),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=lambda *_: (_ for _ in ()).throw(AssertionError()),
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        clock=lambda: now[0],
    )
    value = _Library(root, pipeline, report, admissions, evaluations, now)
    try:
        value.drain()
        assert root.shadow_posture() == "automatic"
        assert value.automatic().outcome == "candidate"
        yield value
    finally:
        root.close()


def _accept(case: ReviewCase, destination: str, key: str, at: int) -> HumanDecision:
    proposal = next(
        value for value in case.proposals if value["destination"] == destination
    )
    result = proposal["result"]
    assert type(result) is dict
    alternatives = result["alternatives"]
    assert type(alternatives) is list and alternatives
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination,
        actor="owner",
        outcome="accept",
        selected_value=alternatives[0]["value"],
        idempotency_key=key,
        occurred_at=at,
    )


def _applier(
    library: _Library, tmp_path: Path
) -> tuple[ContinuousGuardedApplication, GuardedFieldApplication, FakeTargetService]:
    repository = library.root
    target = FakeTargetService()
    authority = PersistedFieldEffectAuthority(repository)
    capability = FakeCapabilityObserver("c" * 64)
    service = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        capability,
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(repository),
        PrivateCoverRecoveryStore(tmp_path / "recovery", lambda _: b"old"),
        FakeForwardReevaluation(),
        lambda: library.now[0],
    )
    scheduler = ContinuousGuardedApplication(
        repository,
        {("primary", "ebooks"): service},
        {("primary", "ebooks"): capability},
        authority,
        (("primary", "ebooks"),),
        lambda: library.now[0],
        lambda *_: None,
    )
    return scheduler, service, target


def _planned(library: _Library) -> dict[str, FieldEffectOperation]:
    return {
        operation.plan.authority.decision_id: operation
        for operation in (
            FieldEffectOperation.from_json(document)
            for (document,) in library.root._connection.execute(
                "SELECT document FROM field_effects"
            ).fetchall()
        )
    }


def test_leaving_automatic_with_an_unchanged_policy_keeps_the_review_case(
    library: _Library,
) -> None:
    connection = library.root._connection
    case_before = library.case_id()
    attention_before = library.attention()
    needs_before = [item["item"] for item in library.library("needs_attention")]
    automatic_count = connection.execute(
        "SELECT count(*) FROM automatic_decisions"
    ).fetchone()
    assert attention_before and needs_before == ["item-1"]
    assert "authors" not in {destination for _, destination in attention_before}

    library.leave_automatic()

    assert library.admissions[-1] == ("admitted", case_before)
    assert library.evaluations[-1] == library.evaluations[0]
    assert connection.execute(
        "SELECT review_case_id,checkpoint,outcome FROM current_review_case_lineages "
        "WHERE " + _LINEAGE
    ).fetchone() == (case_before, 3, "admitted")
    assert connection.execute(
        "SELECT checkpoint,actor,from_review_case_id,to_review_case_id,kind,reason "
        "FROM review_case_transitions WHERE item_id='item-1' ORDER BY checkpoint"
    ).fetchall() == [
        (1, "review-admission-v1", None, case_before, "admitted", "actionable"),
        (2, "policy-activation-v1", case_before, None, "retired", "policy_changed"),
        (3, "review-admission-v1", None, case_before, "admitted", "actionable"),
    ]
    assert connection.execute(
        "SELECT stage,state,outcome,review_case_id FROM review_preparation_work "
        "WHERE item_id='item-1'"
    ).fetchone() == ("complete", "complete", "case", case_before)
    assert connection.execute(
        "SELECT state,total FROM metadata_policy_transitions "
        "ORDER BY generation DESC LIMIT 1"
    ).fetchone() == ("completed", 1)
    assert connection.execute(
        "SELECT state FROM metadata_policy_transition_items"
    ).fetchall() == [("complete",)]

    # Attention and Library read the work as before the switch.
    assert library.attention() == attention_before
    assert [item["item"] for item in library.library("needs_attention")] == needs_before
    (every,) = library.library("all")
    assert (every["detail_kind"], every["review_case_id"]) == ("case", case_before)

    # The automatic decision stays (append-only) and keeps its field out of
    # Attention, as a Preview successor's Automatic field is kept out.
    assert (
        connection.execute("SELECT count(*) FROM automatic_decisions").fetchone()
        == automatic_count
    )
    assert library.automatic().review_case_id == case_before

    # Admitting the same proposal set again replays and records nothing.
    transitions = connection.execute(
        "SELECT count(*) FROM review_case_transitions"
    ).fetchone()
    proposal = ProposalSet.from_json(library.root.get_proposal(library.evaluations[-1]))
    replay = ReviewAdmissionResult.from_json(library.root.admit_review_case(proposal))
    assert (replay.status, replay.review_case_id) == ("replayed", case_before)
    assert (
        connection.execute("SELECT count(*) FROM review_case_transitions").fetchone()
        == transitions
    )


def test_an_owner_decision_from_before_leaving_automatic_asks_again(
    library: _Library, tmp_path: Path
) -> None:
    case = library.case()
    destination = next(
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    )
    decision = _accept(case, destination, "before-leaving-automatic", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents("primary", ("ebooks",), (destination,), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    unclaimed = _planned(library)[decision.decision_id]
    assert unclaimed.state == "planned"
    assert (case.review_case_id, destination) not in library.attention()

    library.leave_automatic()

    # The case is the same; the owner's decision is history of its earlier
    # episode, so the field asks again and its unclaimed plan never applies.
    assert library.case_id() == case.review_case_id
    assert (case.review_case_id, destination) in library.attention()
    assert decision.decision_id not in library.pending()
    assert not library.root.field_effect_authority_is_current(unclaimed.plan.authority)
    target.seed(unclaimed.plan)
    applied = service.apply(unclaimed.plan.plan_id)
    assert applied.state != "verified"
    assert not [
        json.loads(event)["kind"]
        for event in library.root.field_effect_event_documents(unclaimed.plan.plan_id)
        if json.loads(event)["kind"] in {"claimed", "verified"}
    ]


def test_an_automatic_decision_writes_only_in_the_automatic_posture(
    library: _Library, tmp_path: Path
) -> None:
    automatic = library.automatic()
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), ("authors", "title", "language"), 12
    )
    assert automatic.decision_id in library.pending()

    library.leave_automatic()

    assert library.root.shadow_posture() == "preview"
    assert library.case_id() == automatic.review_case_id
    # Write permission outlives the switch, so only posture keeps the earlier
    # automatic decision from planning and writing.
    assert (
        library.root.current_field_effect_consent("primary", "ebooks", "authors")
        is not None
    )
    assert automatic.decision_id not in library.pending()
    assert not [
        row
        for row in library.root.pending_field_effect_decision_counts()
        if row["origin"] == "automatic"
    ]

    # An owner decision in the new episode still plans and writes in Preview.
    case = library.case()
    destination = next(
        value for _, value in library.attention() if value in {"title", "language"}
    )
    owner = _accept(case, str(destination), "after-leaving-automatic", 31)
    library.root.record_human_decision(owner)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 32
    scheduler.schedule()
    planned = _planned(library)
    assert automatic.decision_id not in planned
    target.seed(planned[owner.decision_id].plan)
    assert service.apply(planned[owner.decision_id].plan.plan_id).state == "verified"

    # Confirming Automatic again makes the unchanged decision pending again.
    library.root.activate_qualified_automatic(40)
    assert automatic.decision_id in library.pending()


def test_an_automatic_plan_from_before_leaving_automatic_writes_nothing(
    library: _Library, tmp_path: Path
) -> None:
    automatic = library.automatic()
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    assert library.root.field_effect_authority_is_current(plan.authority)

    library.leave_automatic()

    assert library.case_id() == automatic.review_case_id
    assert not library.root.field_effect_authority_is_current(plan.authority)
    target.seed(plan)
    before = target.read(plan).value
    applied = service.apply(plan.plan_id)
    assert (applied.state, applied.failure) == ("stale", "authority")
    assert target.read(plan).value == before


def _read(library: _Library, scheduler: ContinuousGuardedApplication) -> dict[str, Any]:
    """Read the case page and Activity as the review API does."""
    detail = InspectCurrentReviewCase(library.root).run(library.case_id())
    activity = scheduler.activity(
        search=None, event_type="all", range_days=30, page=1, page_size=25
    )
    fields = {
        str(field["destination"]): field
        for field in cast(list[dict[str, Any]], detail["fields"])
    }
    return {"fields": fields, "activity": activity}


def test_the_case_and_activity_read_after_leaving_automatic(
    library: _Library, tmp_path: Path
) -> None:
    scheduler, _, _ = _applier(library, tmp_path)
    automatic = library.automatic()
    before = _read(library, scheduler)
    authors = before["fields"]["authors"]
    assert authors["automatic_decision"]["decision_id"] == automatic.decision_id
    assert authors["classification"] == "automatic_recorded"

    library.leave_automatic()

    # The earlier automatic decision decides nothing in the new generation, so
    # the case reads its field as any case of that generation does, and
    # Activity keeps it as history with nothing waiting to write.
    for posture in ("preview", "automatic"):
        if posture == "automatic":
            library.root.activate_qualified_automatic(40)
            library.now[0] = 41
        assert library.root.shadow_posture() == posture
        read = _read(library, scheduler)
        fields = read["fields"]
        assert fields["authors"]["automatic_decision"] is None
        assert fields["authors"]["classification"] != "automatic_recorded"
        assert fields["authors"]["needs_attention"] is False
        assert {
            destination
            for destination, field in fields.items()
            if field["needs_attention"]
        } == {destination for _, destination in library.attention()}
        activity = read["activity"]
        (recorded,) = (
            event
            for event in cast(list[dict[str, Any]], activity["events"])
            if event["event_id"] == automatic.decision_id
        )
        assert (recorded["state"], recorded["failure"]) == ("recorded", None)
        if posture == "preview":
            (configured,) = cast(list[dict[str, Any]], activity["configured_libraries"])
            assert configured["prepared"]["automatic"] == 0
