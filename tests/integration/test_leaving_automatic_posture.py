"""Leaving Automatic with an unchanged policy keeps every work's review case.

``enter_shadow_preview`` reactivates the unchanged policy, which retires every
current case for its successor. With the policy unchanged that successor is
the same proposal set, so preparation admits the same case again, and it
continues its episode with the owner's answers (#1568). An automatic decision recorded under Automatic stays on
that case and plans and writes nothing outside the Automatic posture.
It decides only in the generation it was recorded under: confirming Automatic
requeues the prepared works, so each is decided again from what Preview
showed (#1573), and an earlier decision keeps no field out of Attention
(#1601).

The harness drives a real SQLite repository and the real
``ReviewPreparationPipeline`` with stages that call the real report, proposal,
admission and projection seams.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import patch

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
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.cover_evidence import CoverSource
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
    field_value_version,
)
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderObservation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewAdmissionResult, ReviewCase
from tests.support.author_evidence import author_report

_PRODUCER = "f" * 64
_LINEAGE = "connection_name='primary' AND library_name='ebooks' AND item_id='item-1'"


@dataclass
class _Held:
    """What Audiobookshelf holds for the work now: the opt-in library model.

    Discovery lists the work with these authors, extraction reads these
    fields into its report, and proposal and admission refuse a report whose
    target differs from them, as `require_report_current` does in production.
    """

    target: TargetFacts
    # Every extraction claimed after preparation, and every report proposed.
    extractions: list[PipelineAction] = field(default_factory=list)
    proposed: list[str] = field(default_factory=list)
    # Failure codes the next extractions raise, in order.
    failures: list[str] = field(default_factory=list)

    def write(self, destination: TargetDestination, value: tuple[str, ...]) -> None:
        """Record a value the library now holds, as a verified write leaves it."""
        self.target = TargetFacts(
            self.target.connection,
            self.target.library,
            self.target.item,
            tuple(
                TargetField(destination, TargetFieldState.VALUE, value)
                if held.destination is destination
                else held
                for held in self.target.fields
            ),
        )

    @property
    def authors(self) -> tuple[str, ...]:
        (authors,) = (
            held.value
            for held in self.target.fields
            if held.destination is TargetDestination.AUTHORS
        )
        assert type(authors) is tuple
        return cast(tuple[str, ...], authors)

    def require_current(self, report: MetadataReport) -> None:
        if report.target.fields != self.target.fields:
            raise ReviewPipelineError("stale")


@dataclass
class _Library:
    root: SQLiteMetadataReportRepository
    pipeline: ReviewPreparationPipeline
    report: MetadataReport
    admissions: list[tuple[str, str | None]]
    evaluations: list[str]
    now: list[int]
    # Every call that would read the file, Audiobookshelf or a provider in
    # production, by stage, once the harness has prepared its work.
    external: list[str] = field(default_factory=list)
    held: _Held | None = None

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

    def automatic(self, generation: int = 2) -> AutomaticDecision:
        """The authors decision recorded under `generation`."""
        (document,) = self.root._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='authors' "
            "AND policy_generation=?",
            (generation,),
        ).fetchone()
        return AutomaticDecision.from_json(document)

    def decisions(self) -> list[tuple[str, int]]:
        """Every automatic decision, as (field, generation), in recorded order."""
        return [
            (str(destination), int(generation))
            for destination, generation in self.root._connection.execute(
                "SELECT destination,policy_generation FROM automatic_decisions "
                "ORDER BY policy_generation,destination"
            )
        ]

    def generation(self) -> int:
        (generation,) = self.root._connection.execute(
            "SELECT generation FROM metadata_policy_state"
        ).fetchone()
        return int(generation)

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


@contextmanager
def _prepared(
    tmp_path: Path,
    *,
    automatic: bool,
    covers: tuple[CoverSource, ...] = (),
    providers: tuple[ProviderObservation, ...] | None = None,
    before_projection: Callable[[ReviewPipelineRepository, Any], None] | None = None,
    cover_order: str | None = None,
    library_model: Literal["publication", "advance"] | None = None,
) -> Iterator[_Library]:
    """One work prepared under a qualified Automatic contributor-names policy.

    With `automatic` false the owner stays in Preview, so the work is first
    prepared there and nothing is decided automatically. `covers` become the
    report's cover sources and `providers`, when given, its providers.
    `before_projection`, when given, runs before each projection as the cover
    stage does in production. `cover_order`, when given, makes covers
    automatic under that cover-order option.

    `library_model`, when given, turns on the library model (`_Held`):
    discovery runs, extraction reads what the library holds now, and proposal
    and admission check currentness as production does. Extraction completes
    through `complete_review_extraction_publication`, as production does,
    with "publication", and through the pipeline's advance with "advance".
    """
    database = tmp_path / "application.sqlite3"
    report = author_report(tmp_path / "media", "supported")
    if covers or providers is not None:
        report = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers if providers is None else providers,
            cover_sources=covers,
        )
    root = open_repository(database, cross_thread=True)
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    destinations["contributor_names"] = "automatic"
    if cover_order is not None:
        options["cover_order"] = cover_order
        destinations["cover_order"] = "automatic"
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
    if automatic:
        root.activate_qualified_automatic(3)
    held = None if library_model is None else _Held(report.target)

    def entry(authors: tuple[str, ...]) -> CatalogueEntry:
        return CatalogueEntry(
            "primary",
            "ebooks",
            "lib-1",
            "item-1",
            1,
            "Synthetic work",
            authors,
            True,
            False,
            "singular-supported",
            report.file.identity,
            "/book.epub",
            "epub",
            "books",
        )

    root.request_review_scan("primary", "ebooks", "lib-1", _PRODUCER, 4)
    discovery = root.claim_review_pipeline_action(5)
    assert discovery is not None
    root.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (entry(("Ada Example",) if held is None else held.authors),),
        6,
    )
    now = [10]
    evaluations: list[str] = []
    admissions: list[tuple[str, str | None]] = []

    external: list[str] = []
    prepared = [False]

    def extract(
        repository: ReviewPipelineRepository, action: PipelineAction, claim_next: bool
    ) -> tuple[str, bool]:
        if prepared[0]:
            external.append("extraction")
        if held is None:
            cast(SQLiteMetadataReportRepository, repository).publish(
                report, lambda: True
            )
            return report.report_id, False
        return _extract_held(
            cast(SQLiteMetadataReportRepository, repository),
            action,
            claim_next,
            held,
            report,
            library_model == "publication",
            now[0],
            prepared[0],
        )

    discoveries = [1]

    def discover(
        _connection: str, _library: str
    ) -> tuple[int, str, tuple[CatalogueEntry, ...]]:
        if held is None:
            raise AssertionError()
        external.append("discovery")
        discoveries[0] += 1
        return discoveries[0], f"{discoveries[0]:064x}", (entry(held.authors),)

    def propose(repository: ReviewPipelineRepository, report_id: str) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        value = MetadataReport.from_json(concrete.get(report_id))
        if held is not None:
            held.proposed.append(report_id)
            held.require_current(value)
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
        if held is not None:
            held.require_current(
                MetadataReport.from_json(concrete.get(proposal.report_id))
            )
        result = ReviewAdmissionResult.from_json(concrete.admit_review_case(proposal))
        admissions.append((result.status, result.review_case_id))
        return result.status, result.review_case_id

    def project(repository: ReviewPipelineRepository, action: Any) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        # As ReviewPipelineStages.project does: Book matching first (#1355).
        if type(action) is str:
            concrete.reconcile_case_level(action, now[0])
            outcome = concrete.persist_shadow_outcome(action, now[0])
        else:
            concrete.reconcile_case_level(action.review_case_id, now[0])
            outcome = concrete.persist_shadow_outcome(
                action.review_case_id, now[0], staged_action=action
            )
        return "stale" if outcome == "stale" else "complete"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(database, authority=root.authority),
        libraries=(("primary", "ebooks", "lib-1", _PRODUCER),),
        discover=discover,
        extract=extract,
        providers=lambda *_: (),
        propose=propose,
        admit=admit,
        project=project,
        before_projection=(
            (lambda *_args: None) if before_projection is None else before_projection
        ),
        clock=lambda: now[0],
    )
    value = _Library(
        root, pipeline, report, admissions, evaluations, now, external, held
    )
    try:
        value.drain()
        prepared[0] = True
        if automatic:
            assert root.shadow_posture() == "automatic"
            # Replaced providers carry no authors candidate to decide.
            if providers is None:
                assert value.automatic().outcome == "candidate"
        else:
            assert root.shadow_posture() == "preview"
        yield value
    finally:
        root.close()


def _extract_held(
    repository: SQLiteMetadataReportRepository,
    action: PipelineAction,
    claim_next: bool,
    held: _Held,
    prepared: MetadataReport,
    publication: bool,
    now: int,
    recorded: bool,
) -> tuple[str, bool]:
    """Read the work as the library holds it now and publish that report."""
    if recorded:
        held.extractions.append(action)
    if held.failures:
        raise ReviewPipelineError(held.failures.pop(0))
    report = (
        prepared
        if held.target == prepared.target
        else MetadataReport.create(
            target=held.target,
            file=prepared.file,
            local_claims=prepared.local_claims,
            local_reasons=prepared.local_reasons,
            providers=prepared.providers,
            cover_sources=prepared.cover_sources,
        )
    )
    current = repository._connection.execute(
        "SELECT report_id FROM current_metadata_reports WHERE " + _LINEAGE
    ).fetchone()
    repository.publish(
        report,
        lambda: True,
        None if current is None else current[0],
        after_publication=(
            (
                lambda bound, published: cast(
                    SQLiteMetadataReportRepository, bound
                ).complete_review_extraction_publication(
                    action, published.report_id, now, claim_next=claim_next
                )
            )
            if publication
            else None
        ),
    )
    return report.report_id, publication


@pytest.fixture
def library(tmp_path: Path) -> Iterator[_Library]:
    """One work prepared and decided in the Automatic posture."""
    with _prepared(tmp_path, automatic=True) as value:
        yield value


@pytest.fixture
def preview_library(tmp_path: Path) -> Iterator[_Library]:
    """One work first prepared in Preview, before Automatic is confirmed."""
    with _prepared(tmp_path, automatic=False) as value:
        yield value


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
    assert library.external == []
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


def test_owner_answers_from_before_leaving_automatic_are_kept(
    library: _Library, tmp_path: Path
) -> None:
    case = library.case()
    asked = [
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    ]
    assert len(asked) >= 2
    planned_destination, waiting_destination = asked[:2]
    decision = _accept(case, planned_destination, "before-leaving-automatic", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), (planned_destination,), 12
    )
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    unclaimed = _planned(library)[decision.decision_id]
    assert unclaimed.state == "planned"
    # Answered after the applier ran, so nothing has planned it yet.
    waiting = _accept(case, waiting_destination, "before-leaving-automatic-2", 14)
    library.root.record_human_decision(waiting)
    assert waiting.decision_id in library.pending()
    answered = {(case.review_case_id, planned_destination)} | {
        (case.review_case_id, waiting_destination)
    }
    assert not answered & set(library.attention())

    library.leave_automatic()

    # The same case continues its episode, so the owner's answers stay
    # current: nothing asks again, and both still write (#1568).
    assert library.case_id() == case.review_case_id
    assert library.root._connection.execute(
        "SELECT count(*) FROM review_case_episodes WHERE review_case_id=?",
        (case.review_case_id,),
    ).fetchone() == (0,)
    assert not answered & set(library.attention())
    read = _read(library, scheduler)
    for destination, recorded in (
        (planned_destination, decision),
        (waiting_destination, waiting),
    ):
        field = read["fields"][destination]
        assert field["human_decision"]["decision_id"] == recorded.decision_id
        assert field["classification"] == "human_recorded"
        assert field["needs_attention"] is False
    assert waiting.decision_id in library.pending()
    assert library.root.field_effect_authority_is_current(unclaimed.plan.authority)
    target.seed(unclaimed.plan)
    assert service.apply(unclaimed.plan.plan_id).state == "verified"
    assert (unclaimed.plan.plan_id, "verified", planned_destination) in {
        (event["change_id"], event["state"], event["destination"])
        for event in cast(
            list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
        )
    }

    # Confirming Automatic decides the work again, and the owner's answers
    # stay: no automatic decision is recorded on a field the owner decided,
    # and the waiting answer still plans and writes (#1573).
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    assert not answered & set(library.attention())
    read = _read(library, scheduler)
    for destination, recorded in (
        (planned_destination, decision),
        (waiting_destination, waiting),
    ):
        field = read["fields"][destination]
        assert field["human_decision"]["decision_id"] == recorded.decision_id
        assert field["classification"] == "human_recorded"
    assert waiting.decision_id in library.pending()
    library.root.grant_field_effect_consents(
        "primary", ("ebooks",), (waiting_destination,), 42
    )
    library.now[0] = 43
    scheduler.schedule()
    later = _planned(library)[waiting.decision_id].plan
    target.seed(later)
    assert service.apply(later.plan_id).state == "verified"


def test_an_owner_answer_on_an_automatic_field_outlasts_confirming_automatic(
    library: _Library, tmp_path: Path
) -> None:
    """The owner's answer outlasts confirming Automatic and still writes.

    Automatic decides the field again in the new generation, as on a later
    case of the work, and the answer's hold keeps that decision unwritten,
    so the field shows the hold and Resume as it did before leaving.
    """
    automatic = library.automatic()
    owner = _accept(library.case(), "authors", "override-before-leaving", 11)
    library.root.record_human_decision(owner)
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)

    library.leave_automatic()
    # The owner's answer, not the earlier automatic choice, decides the
    # field in Preview, so the case shows no earlier choice (#1575).
    preview = _read_fields(library)["authors"]
    assert preview["human_decision"]["decision_id"] == owner.decision_id
    assert preview["earlier_automatic_value"] is None
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()

    assert library.decisions() == [("authors", 2), ("authors", 3)]
    assert automatic.decision_id not in library.pending()
    assert library.automatic(3).decision_id not in library.pending()
    field = _read_fields(library)["authors"]
    assert field["human_decision"]["decision_id"] == owner.decision_id
    assert field["classification"] == "human_recorded"
    assert field["manual_hold"] is True
    assert owner.decision_id in library.pending()
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 42
    scheduler.schedule()
    plan = _planned(library)[owner.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"


@pytest.mark.parametrize(
    "episode",
    ["current", "earlier", "released"],
    ids=["current", "earlier", "released"],
)
def test_a_held_field_keeps_its_hold_and_resume_after_automatic_is_left_and_confirmed(
    library: _Library, episode: str
) -> None:
    """Leaving Automatic and confirming it again keeps the field's hold and
    Resume, and Resume still hands the field back to Automatic.

    `earlier` is an answer from an earlier episode of the case (a work whose
    content went away and came back, #1358), which the case reads as no
    answer: before this fix the field then read No change, with no hold and
    no Resume, while the answer still kept Automatic off it. `released` is an
    answer whose hold Resume already released before leaving: Automatic's
    decision was waiting to be written, and it still is after confirming.
    """
    library.automatic()
    owner = _accept(library.case(), "authors", "held-before-leaving", 11)
    library.root.record_human_decision(owner)
    case_id = library.case_id()
    if episode == "earlier":
        # The work returned to this content: a new episode starts after the
        # answer, as admission records it (#1358).
        library.root._connection.execute(
            "INSERT INTO review_case_episodes(review_case_id,decision_floor,"
            "event_floor) VALUES(?,(SELECT MAX(ordinal) FROM human_decisions "
            "WHERE review_case_id=?),0)",
            (case_id, case_id),
        )
        library.root._connection.commit()
    before = _read_fields(library)["authors"]
    assert before["manual_hold"] is True
    if episode == "released":
        assert library.root.clear_field_effect_manual_hold(
            case_id, "authors", 12, owner.decision_id
        )
        assert library.automatic().decision_id in library.pending()

    library.leave_automatic()
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()

    assert library.case_id() == case_id
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    again = library.automatic(3)
    assert again.review_case_id == case_id
    if episode == "released":
        assert again.decision_id in library.pending()
        assert _read_fields(library)["authors"]["manual_hold"] is False
        return
    assert again.decision_id not in library.pending()
    field = _read_fields(library)["authors"]
    assert field["manual_hold"] is True
    assert field["classification"] == "human_recorded"
    assert field["classification_cause"] == "human_decided"
    assert (field["human_decision"] is None) is (episode == "earlier")

    assert library.root.clear_field_effect_manual_hold(
        case_id, "authors", 42, owner.decision_id
    )
    assert again.decision_id in library.pending()
    assert _read_fields(library)["authors"]["manual_hold"] is False


def test_an_answer_with_no_earlier_automatic_decision_still_keeps_automatic_off(
    preview_library: _Library,
) -> None:
    """Only a field Automatic had decided on the case is decided again under
    its hold. An answer given in Preview, before Automatic decided anything on
    the case, still keeps Automatic from recording a decision there (ADR-0085,
    Amendment 1: it shows as an ordinary answer until a later case)."""
    library = preview_library
    owner = _accept(library.case(), "authors", "answered-in-preview", 11)
    library.root.record_human_decision(owner)
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    assert ("authors", library.generation()) not in library.decisions()
    assert _read_fields(library)["authors"]["manual_hold"] is False


def test_an_owner_plan_waits_while_its_case_is_retired_for_reconciliation(
    library: _Library, tmp_path: Path
) -> None:
    """A plan picked between the retirement and re-admission still writes."""
    case = library.case()
    destination = next(
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    )
    decision = _accept(case, destination, "during-reconciliation", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents("primary", ("ebooks",), (destination,), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[decision.decision_id].plan
    target.seed(plan)

    # Leaving Automatic retires the case; preparation has not re-admitted it.
    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"
    assert library.root._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages WHERE " + _LINEAGE
    ).fetchone() == (None,)
    assert library.root.next_field_effect_plan_id(None) is None
    with pytest.raises(GuardedApplicationError, match="deferred"):
        service.apply(plan.plan_id)
    assert _planned(library)[decision.decision_id].state == "planned"

    library.now[0] = 30
    library.drain()
    assert library.case_id() == case.review_case_id
    assert library.root.next_field_effect_plan_id(None) == (plan.plan_id, "ebooks")
    assert service.apply(plan.plan_id).state == "verified"


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

    # Confirming Automatic again leaves the earlier decision as history: it
    # decides nothing in this generation, so it is not pending and still
    # authorizes no write (#1573).
    library.root.activate_qualified_automatic(40)
    assert automatic.decision_id not in library.pending()
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(library.root).resolve(automatic)
    library.now[0] = 41
    scheduler.schedule()
    assert automatic.decision_id not in _planned(library)

    # Confirming requeued the work, and projecting it in Automatic decides the
    # field again in this generation from the outcome Preview showed.
    library.drain()
    assert library.decisions() == [("authors", 2), ("authors", 3)]
    fresh = library.automatic(3)
    assert fresh.review_case_id == automatic.review_case_id
    assert fresh.decision_id in library.pending()
    PersistedFieldEffectAuthority(library.root).resolve(fresh)
    library.now[0] = 42
    scheduler.schedule()
    planned = _planned(library)
    assert automatic.decision_id not in planned
    renewed = planned[fresh.decision_id].plan
    target.seed(renewed)
    assert service.apply(renewed.plan_id).state == "verified"
    assert fresh.decision_id not in library.pending()
    assert (renewed.plan_id, "verified", "authors") in {
        (event["change_id"], event["state"], event["destination"])
        for event in cast(
            list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
        )
    }


@pytest.mark.parametrize("confirm_automatic", [False, True])
def test_an_automatic_plan_from_before_leaving_automatic_writes_nothing(
    library: _Library, tmp_path: Path, confirm_automatic: bool
) -> None:
    """The plan waits in Preview and goes stale after Confirm Automatic."""
    automatic = library.automatic()
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    assert library.root.field_effect_authority_is_current(plan.authority)
    assert _waiting(scheduler) == (1, "authors")

    library.leave_automatic()

    assert library.case_id() == automatic.review_case_id
    assert not library.root.field_effect_authority_is_current(plan.authority)
    if confirm_automatic:
        # The plan was never claimed, and its decision is pending again in the
        # Automatic posture, but it belongs to the earlier generation.
        library.root.activate_qualified_automatic(40)
        assert library.root.shadow_posture() == "automatic"
        assert not library.root.field_effect_authority_is_current(plan.authority)
    target.seed(plan)
    before = target.read(plan).value
    if not confirm_automatic:
        # Outside Automatic the plan waits rather than being consumed, and
        # Activity neither counts it as ready nor names it as the next write.
        assert _waiting(scheduler) == (0, None)
        assert library.root.next_field_effect_plan_id(None) is None
        # A plan selected just before the posture changed is an empty poll.
        with patch.object(
            library.root,
            "next_field_effect_plan_id",
            return_value=(plan.plan_id, "ebooks"),
        ):
            assert scheduler.run_cycle() is True
        with pytest.raises(GuardedApplicationError, match="deferred"):
            service.apply(plan.plan_id)
        assert _planned(library)[automatic.decision_id].state == "planned"
        library.root.activate_qualified_automatic(40)
    # In Automatic it is ready again, and is selected and goes stale: it
    # belongs to the earlier generation.
    assert _waiting(scheduler) == (1, "authors")
    assert library.root.next_field_effect_plan_id(None) == (plan.plan_id, "ebooks")
    applied = service.apply(plan.plan_id)
    assert (applied.state, applied.failure) == ("stale", "authority")
    assert target.read(plan).value == before

    # Confirming requeued the work, so it is decided again in this generation
    # and that decision plans and writes; the earlier plan stays stale (#1573).
    library.now[0] = 50
    library.drain()
    fresh = library.automatic(3)
    scheduler.schedule()
    operations = _planned(library)
    assert operations[automatic.decision_id].state == "stale"
    renewed = operations[fresh.decision_id].plan
    assert renewed.plan_id != plan.plan_id
    target.seed(renewed)
    assert service.apply(renewed.plan_id).state == "verified"


def _waiting(scheduler: ContinuousGuardedApplication) -> tuple[int, object]:
    """Activity's ready count and the write it names as next."""
    activity = scheduler.activity(
        search=None, event_type="all", range_days=30, page=1, page_size=25
    )
    waiting = cast(dict[str, Any], activity["waiting"])
    current = cast(dict[str, Any] | None, activity["current_waiting"])
    return waiting["scheduled"], None if current is None else current["destination"]


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
    assert authors["earlier_automatic_value"] is None

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
        # Only Preview shows what Automatic chose before (#1575): after Turn
        # on Automatic the field is about to be decided again.
        assert fields["authors"]["earlier_automatic_value"] == (
            {"kind": "list", "value": ["Ada Example"]} if posture == "preview" else None
        )
        assert {
            destination
            for destination, field in fields.items()
            if field["earlier_automatic_value"] is not None
        } == ({"authors"} if posture == "preview" else set())
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
        (configured,) = cast(list[dict[str, Any]], activity["configured_libraries"])
        assert configured["prepared"]["automatic"] == 0

    # Confirming requeued the work: projecting it in Automatic decides authors
    # in this generation, and the case, Attention and Activity agree (#1573).
    library.drain()
    fresh = library.automatic(3)
    read = _read(library, scheduler)
    fields = read["fields"]
    assert fields["authors"]["automatic_decision"]["decision_id"] == fresh.decision_id
    assert fields["authors"]["classification"] == "automatic_recorded"
    assert fields["authors"]["earlier_automatic_value"] is None
    assert fields["authors"]["needs_attention"] is False
    assert {
        destination for destination, field in fields.items() if field["needs_attention"]
    } == {destination for _, destination in library.attention()}
    (configured,) = cast(list[dict[str, Any]], read["activity"]["configured_libraries"])
    assert configured["prepared"]["automatic"] == 1


def _asked(library: _Library) -> tuple[set[str], set[str]]:
    """The fields the case page asks for, and the ones Attention lists."""
    fields = _read_fields(library)
    return (
        {
            destination
            for destination, field in fields.items()
            if field["needs_attention"]
        },
        {str(destination) for _, destination in library.attention()},
    )


def _read_fields(library: _Library) -> dict[str, dict[str, Any]]:
    detail = InspectCurrentReviewCase(library.root).run(library.case_id())
    return {
        str(field["destination"]): field
        for field in cast(list[dict[str, Any]], detail["fields"])
    }


def test_pausing_after_leaving_automatic_lists_every_field_the_case_asks_for(
    library: _Library,
) -> None:
    """An earlier generation's decision keeps no field out of Attention (#1601)."""
    library.leave_automatic()
    assert library.root.shadow_posture() == "preview"

    library.root.pause_automatic_safety(True, 31)

    case_needs, attention = _asked(library)
    assert case_needs == attention == {"authors", "language", "title"}
    (row,) = library.library("all")
    assert row["attention_count"] == 3
    # A field that needs attention shows no earlier automatic choice (#1575).
    assert _read_fields(library)["authors"]["earlier_automatic_value"] is None

    # Resuming in Preview: an Automatic field without abstention is never
    # asked for, so authors leaves both again.
    library.root.pause_automatic_safety(False, 32)
    case_needs, attention = _asked(library)
    assert case_needs == attention == {"language", "title"}
    (row,) = library.library("all")
    assert row["attention_count"] == 2
    assert _read_fields(library)["authors"]["earlier_automatic_value"] == {
        "kind": "list",
        "value": ["Ada Example"],
    }


def test_pausing_in_automatic_keeps_a_decided_field_out_of_attention(
    library: _Library,
) -> None:
    """The current generation's decision settles its field while paused."""
    library.root.pause_automatic_safety(True, 31)
    case_needs, attention = _asked(library)
    assert case_needs == attention == {"language", "title"}


def test_returning_to_a_policy_decides_its_case_again_in_the_new_generation(
    library: _Library,
) -> None:
    """Policy A, then B, then A again, all in Automatic (#1573)."""
    root = library.root
    automatic = library.automatic()
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    destinations["contributor_names"] = "automatic"
    destinations["series"] = "ignore"
    other = MetadataPolicyProfile.create("Other policy", options, destinations)
    other_resolved = resolve_policy(other, "ebooks")
    root.create_metadata_policy_draft(other.canonical_json)
    (first_policy,) = root._connection.execute(
        "SELECT active_policy_id FROM metadata_policy_state"
    ).fetchone()
    first_resolution = root._connection.execute(
        "SELECT resolved_policy_id,document FROM metadata_policy_resolutions "
        "WHERE generation=2 AND library_name='ebooks'"
    ).fetchone()
    root.activate_metadata_policy(
        other.policy_id,
        first_policy,
        (("ebooks", other_resolved.resolved_id, other_resolved.canonical_json),),
        20,
        expected_active_generation=2,
    )
    library.now[0] = 21
    library.drain()
    assert library.case_id() != automatic.review_case_id
    assert library.decisions() == [("authors", 2), ("authors", 3)]

    root.activate_metadata_policy(
        first_policy,
        other.policy_id,
        (("ebooks", *first_resolution),),
        30,
        expected_active_generation=3,
    )
    library.now[0] = 31
    library.drain()

    assert root.shadow_posture() == "automatic"
    assert library.case_id() == automatic.review_case_id
    assert library.generation() == 4
    assert library.decisions() == [("authors", 2), ("authors", 3), ("authors", 4)]
    fresh = library.automatic(4)
    assert fresh.review_case_id == automatic.review_case_id
    PersistedFieldEffectAuthority(root).resolve(fresh)
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(root).resolve(automatic)
    assert fresh.decision_id in library.pending()
    assert automatic.decision_id not in library.pending()
    case_needs, attention = _asked(library)
    assert "authors" not in case_needs
    assert case_needs == attention


def test_confirming_automatic_decides_the_works_first_prepared_in_preview(
    preview_library: _Library, tmp_path: Path
) -> None:
    """Start in Preview and confirm Automatic later (#1573)."""
    library = preview_library
    root = library.root
    assert root._connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes"
    ).fetchone() == (1,)
    assert library.decisions() == []

    root.activate_qualified_automatic(40)
    # The confirm transaction requeued the prepared work to projection.
    assert root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone() == ("projection", "queued")
    library.now[0] = 41
    library.drain()

    # Projection is content-addressed: the outcome Preview showed decides.
    assert library.decisions() == [("authors", 2)]
    assert root._connection.execute(
        "SELECT count(*) FROM shadow_decision_outcomes"
    ).fetchone() == (1,)
    decided = library.automatic(2)
    PersistedFieldEffectAuthority(root).resolve(decided)
    fields = _read_fields(library)
    assert fields["authors"]["classification"] == "automatic_recorded"
    case_needs, attention = _asked(library)
    assert "authors" not in case_needs
    assert case_needs == attention
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 42)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 43
    scheduler.schedule()
    plan = _planned(library)[decided.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"


def test_a_failed_requeue_leaves_confirm_automatic_undone(library: _Library) -> None:
    library.leave_automatic()
    with (
        patch.object(
            library.root._review_preparation,
            "_requeue_current_case_projections",
            side_effect=sqlite3.OperationalError("injected"),
        ),
        pytest.raises(
            MetadataReportError, match="automatic posture persistence failed"
        ),
    ):
        library.root.activate_qualified_automatic(40)
    assert library.root.shadow_posture() == "preview"
    assert library.root._connection.execute(
        "SELECT stage,state FROM review_preparation_work WHERE item_id='item-1'"
    ).fetchone() == ("complete", "complete")


def test_an_owner_decision_after_confirming_holds_the_current_generation(
    library: _Library, tmp_path: Path
) -> None:
    """The owner overrides and releases the decision that decides now (#1573)."""
    root = library.root
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    library.leave_automatic()
    root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    fresh = library.automatic(3)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 42
    scheduler.schedule()
    plan = _planned(library)[fresh.decision_id].plan
    assert root.field_effect_authority_is_current(plan.authority)

    owner = _accept(library.case(), "authors", "after-confirming-automatic", 43)
    root.record_human_decision(owner)

    # The owner's decision supersedes this generation's plan and holds its
    # decision, not the earlier generation's.
    assert not root.field_effect_authority_is_current(plan.authority)
    (held,) = root._connection.execute(
        "SELECT json_extract(document,'$.decision_id') FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination='authors' AND active=1",
        (fresh.review_case_id,),
    ).fetchone()
    assert held == owner.decision_id

    # Releasing the hold makes this generation's decision current again.
    assert (
        root.clear_field_effect_manual_hold(fresh.review_case_id, "authors", 45)
        is not None
    )
    assert root._connection.execute(
        "SELECT decision_id FROM field_effect_authorities WHERE current=1 "
        "AND decision_id IN (?,?)",
        (library.automatic(2).decision_id, fresh.decision_id),
    ).fetchall() == [(fresh.decision_id,)]


@pytest.mark.parametrize("state", ["claimed", "applying", "reconciling"])
def test_an_earlier_generation_write_in_flight_still_refuses_an_owner_answer(
    library: _Library, tmp_path: Path, state: str
) -> None:
    """An earlier generation decides nothing, but its write is still out (#1573).

    Leaving Automatic readmits the case while the authors write is claimed,
    applying or reconciling. The owner cannot replace the decision until that
    service request is reconciled, as before generations were recorded.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
    if state != "claimed":
        root.transition_field_effect(plan.plan_id, "claimed", "applying", 15)
    if state == "reconciling":
        root.transition_field_effect(plan.plan_id, "applying", "reconciling", 16)

    library.leave_automatic()

    assert library.generation() == 3
    assert library.case_id() == automatic.review_case_id
    assert root.get_field_effect(plan.plan_id).state == state
    owner = _accept(library.case(), "authors", "owner-while-writing", 40)
    with pytest.raises(HumanDecisionError, match="already_decided"):
        root.record_human_decision(owner)
    assert (
        root._connection.execute(
            "SELECT count(*) FROM human_decisions WHERE review_case_id=?",
            (automatic.review_case_id,),
        ).fetchone()[0]
        == 0
    )
    assert root.get_field_effect(plan.plan_id).state == state


def test_an_earlier_generation_write_settled_unwritten_lets_the_owner_answer(
    library: _Library, tmp_path: Path
) -> None:
    """A settled earlier-generation write no longer stands in the owner's way (#1573).

    The write went stale without changing the library, so the report still
    holds and leaving Automatic returns the same case, where the earlier
    generation's decision is replaced by the owner's answer.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, _, _ = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(plan.plan_id, "planned", "c" * 64, 14)
    root.transition_field_effect(plan.plan_id, "claimed", "applying", 15)
    root.transition_field_effect(
        plan.plan_id, "applying", "stale", 16, "target_changed"
    )

    library.leave_automatic()

    assert library.external == []
    assert library.case_id() == automatic.review_case_id
    owner = _accept(library.case(), "authors", "owner-after-writing", 40)
    root.record_human_decision(owner)
    assert root._connection.execute(
        "SELECT decision_id FROM human_decisions WHERE review_case_id=?",
        (automatic.review_case_id,),
    ).fetchall() == [(owner.decision_id,)]


def _automatic_written(
    library: _Library, tmp_path: Path
) -> tuple[AutomaticDecision, ContinuousGuardedApplication]:
    """Automatic's authors decision, written and read back."""
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    plan = _planned(library)[automatic.decision_id].plan
    target.seed(plan)
    assert service.apply(plan.plan_id).state == "verified"
    return automatic, scheduler


def _settled_without_a_write(
    library: _Library, scheduler: ContinuousGuardedApplication, owner: HumanDecision
) -> None:
    """The owner's accept of the written value holds the field, never waits
    for a write, and is marked as needing none (ADR-0105)."""
    root = library.root
    library.now[0] += 1
    scheduler.schedule()
    assert owner.decision_id not in library.pending()
    assert owner.decision_id not in _planned(library)
    assert root.pending_field_effect_decision_counts() == ()
    assert root._connection.execute(
        "SELECT origin,target_version FROM field_effect_no_changes WHERE decision_id=?",
        (owner.decision_id,),
    ).fetchall() == [("human", field_value_version("authors", ["Ada Example"]))]
    (hold,) = root._connection.execute(
        "SELECT json_extract(document,'$.decision_id') FROM "
        "field_effect_manual_holds WHERE destination='authors' AND active=1"
    ).fetchone()
    assert hold == owner.decision_id
    assert _read(library, scheduler)["fields"]["authors"]["no_change_required"] is True


# Leaving Automatic after the write reads the work again into a new case
# (#1986), so the old case is pressed under Automatic, running or paused.
@pytest.mark.parametrize("posture", ["automatic", "automatic_paused"])
def test_choosing_automatics_written_value_needs_no_write(
    library: _Library, tmp_path: Path, posture: str
) -> None:
    """Pressing Automatic's card once its write landed records the owner's
    answer with nothing to write; Activity lists it as kept (ADR-0105)."""
    root = library.root
    automatic, scheduler = _automatic_written(library, tmp_path)
    if posture == "automatic_paused":
        root.pause_automatic_safety(True, 31)

    owner = _accept(library.case(), "authors", "owner-presses-automatics-card", 40)
    root.record_human_decision(owner)
    _settled_without_a_write(library, scheduler, owner)

    activity = cast(
        list[dict[str, Any]], _read(library, scheduler)["activity"]["events"]
    )
    # The owner's own row says the accept kept the written value, as History
    # does, never a change from the value the case read.
    (accepted,) = (event for event in activity if event["state"] == "accept")
    assert accepted["change"] == "Kept Ada Example"
    assert {fact["label"]: fact["value"] for fact in accepted["evidence"]} == {
        "Previous value": "Ada Example",
        "New value": "Ada Example",
        "Chosen by": "You",
    }
    (kept,) = (event for event in activity if event["state"] == "no_change_required")
    assert kept["change"] == "Kept Ada Example"
    # It is the owner's choice: Activity lists it with the owner's, not
    # Automatic's.
    assert (kept["event_type"], kept["category"]) == ("review", "owner")
    for event_type, listed in (("review", True), ("metadata", False)):
        filtered = scheduler.activity(
            search=None, event_type=event_type, range_days=30, page=1, page_size=25
        )
        assert (
            kept["event_id"]
            in {
                event["event_id"]
                for event in cast(list[dict[str, Any]], filtered["events"])
            }
        ) is listed
    assert {fact["label"]: fact["value"] for fact in kept["evidence"]} == {
        "Current value": "Ada Example",
        "Chosen by": "You",
        "Confirmed in Audiobookshelf": "No change was needed",
    }
    (recorded,) = (
        entry
        for entry in root.get_review_case_history(library.case_id(), 50)
        if entry.get("decision_id") == owner.decision_id
    )
    assert recorded["previous_value"] == recorded["selected_value"]

    # A replay records nothing more; a second accept of the same value is
    # refused while the first stands, and records nothing.
    assert json.loads(root.record_human_decision(owner))["decision_id"] == (
        owner.decision_id
    )
    again = _accept(library.case(), "authors", "owner-presses-it-again", 42)
    with pytest.raises(HumanDecisionError, match="owner_value_unchanged"):
        root.record_human_decision(again)
    assert root._connection.execute(
        "SELECT COUNT(*) FROM field_effect_no_changes WHERE origin='human'"
    ).fetchone() == (1,)
    assert root._connection.execute(
        "SELECT decision_id FROM human_decisions"
    ).fetchall() == [(owner.decision_id,)]

    if posture == "automatic":
        # Returning the field to Automatic leaves its written decision
        # settled: nothing waits and nothing is planned.
        assert root.clear_field_effect_manual_hold(
            library.case_id(), "authors", 43, owner.decision_id
        )
        library.now[0] = 44
        scheduler.schedule()
        assert automatic.decision_id not in library.pending()
        assert set(_planned(library)) == {automatic.decision_id}


def test_choosing_the_written_value_after_resume_holds_the_field_again(
    library: _Library, tmp_path: Path
) -> None:
    """Resume automatic handling releases the owner's no-change answer; the
    owner can then choose the same value again, which holds the field anew
    with nothing to write, and pin it."""
    root = library.root
    _, scheduler = _automatic_written(library, tmp_path)
    first = _accept(library.case(), "authors", "owner-presses-before-resume", 40)
    root.record_human_decision(first)
    assert root.clear_field_effect_manual_hold(
        library.case_id(), "authors", 41, first.decision_id
    )
    resumed = _read(library, scheduler)["fields"]["authors"]
    assert resumed["pin_control"] == {"pin": "change_waiting", "unpin": False}

    again = _accept(library.case(), "authors", "owner-presses-after-resume", 42)
    root.record_human_decision(again)
    _settled_without_a_write(library, scheduler, again)
    field = _read(library, scheduler)["fields"]["authors"]
    assert field["pin_control"] == {"pin": "available", "unpin": False}


def test_the_card_pin_on_automatics_written_value_settles(
    library: _Library, tmp_path: Path
) -> None:
    """After an entry of the previous names, the card pin on Automatic's
    written value sends accept then pin: both settle with nothing to write."""
    root = library.root
    _, scheduler = _automatic_written(library, tmp_path)
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    entry = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="owner_entered",
        selected_value={"kind": "list", "value": ["Current Writer"]},
        idempotency_key="owner-enters-the-previous-names",
        occurred_at=40,
        previous=None,
        written=(["Ada Example"],),
    )
    root.record_human_decision(entry)
    accept = _accept(library.case(), "authors", "card-pin-accepts-first", 41)
    root.record_human_decision(accept)
    pin = HumanDecision.create(
        library.case(),
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="pin",
        selected_value=None,
        idempotency_key="card-pin-then-pins",
        occurred_at=42,
    )
    root.record_human_decision(pin)
    library.now[0] = 43
    scheduler.schedule()
    assert entry.decision_id not in library.pending()
    assert entry.decision_id not in _planned(library)
    assert accept.decision_id not in library.pending()
    assert accept.decision_id not in _planned(library)
    assert root.pending_field_effect_decision_counts() == ()
    (hold,) = root._connection.execute(
        "SELECT document FROM field_effect_manual_holds "
        "WHERE destination='authors' AND active=1"
    ).fetchone()
    held = json.loads(hold)
    assert held["decision_id"] == accept.decision_id
    assert held["pin"] is not None


def test_an_earlier_generation_write_once_settled_lets_the_owner_answer(
    tmp_path: Path,
) -> None:
    """A settled earlier-generation write no longer stands in the owner's way.

    Its report predates the write, so leaving Automatic reads the work again
    from the library (ADR-0034): the current case starts from the written
    authors, and the owner's answer on that field is recorded there.
    """
    with _prepared(tmp_path, automatic=True, library_model="publication") as library:
        root = library.root
        automatic = library.automatic()
        root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
        scheduler, service, target = _applier(library, tmp_path)
        library.now[0] = 13
        scheduler.schedule()
        plan = _planned(library)[automatic.decision_id].plan
        target.seed(plan)
        assert service.apply(plan.plan_id).state == "verified"
        assert library.held is not None
        library.held.write(TargetDestination.AUTHORS, ("Ada Example",))

        library.leave_automatic()

        case = library.case()
        assert case.review_case_id != automatic.review_case_id
        authors = _read(library, scheduler)["fields"]["authors"]
        assert authors["owner_control"]["current"] == ["Ada Example"]
        proposal = next(
            value for value in case.proposals if value["destination"] == "authors"
        )
        owner = HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination="authors",
            actor="owner",
            outcome="pin",
            selected_value=None,
            idempotency_key="owner-after-writing",
            occurred_at=40,
        )
        root.record_human_decision(owner)
        assert root._connection.execute(
            "SELECT decision_id FROM human_decisions WHERE review_case_id=?",
            (case.review_case_id,),
        ).fetchall() == [(owner.decision_id,)]


def test_a_new_generation_write_waits_for_an_earlier_write_still_out(
    library: _Library, tmp_path: Path
) -> None:
    """A later generation never writes over an unreconciled request (#1573).

    One automatic decision per field used to make a second write for the
    field impossible; with one per generation, the newer write waits until the
    earlier one is settled.
    """
    root = library.root
    automatic = library.automatic()
    root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    scheduler, service, target = _applier(library, tmp_path)
    library.now[0] = 13
    scheduler.schedule()
    earlier = _planned(library)[automatic.decision_id].plan
    root.claim_field_effect(earlier.plan_id, "planned", "c" * 64, 14)
    root.transition_field_effect(earlier.plan_id, "claimed", "applying", 15)
    library.leave_automatic()
    root.activate_qualified_automatic(40)
    library.now[0] = 41
    library.drain()
    fresh = library.automatic(3)
    library.now[0] = 42
    scheduler.schedule()
    renewed = _planned(library)[fresh.decision_id].plan
    target.seed(renewed)
    before = target.read(renewed).value

    # While the earlier request is out, the new plan is not selected, Activity
    # counts nothing as ready (it names the earlier write still in progress),
    # and applying the new plan defers without consuming it.
    assert root.field_effect_deferred(renewed.plan_id)
    assert root.next_field_effect_plan_id(None) is None
    assert _waiting(scheduler) == (0, "authors")
    with pytest.raises(GuardedApplicationError, match="deferred"):
        service.apply(renewed.plan_id)
    assert root.get_field_effect(renewed.plan_id).state == "planned"
    assert target.read(renewed).value == before

    # Once the earlier request is settled, the new plan writes.
    root.transition_field_effect(earlier.plan_id, "applying", "stale", 43, "authority")
    assert not root.field_effect_deferred(renewed.plan_id)
    assert root.next_field_effect_plan_id(None) == (renewed.plan_id, "ebooks")
    assert service.apply(renewed.plan_id).state == "verified"


def _withdraw(library: _Library, key: str, at: int) -> HumanDecision:
    case = library.case()
    proposal = next(
        value for value in case.proposals if value["destination"] == "authors"
    )
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="authors",
        actor="owner",
        outcome="withdraw",
        selected_value=None,
        idempotency_key=key,
        occurred_at=at,
    )


def _confirm_automatic(library: _Library, at: int) -> None:
    library.root.activate_qualified_automatic(at)
    library.now[0] = at + 1
    library.drain()


@pytest.mark.parametrize(
    "answered", ["before_leaving", "in_preview", "before_leaving_then_confirmed"]
)
def test_an_answer_beside_an_earlier_generation_decision_can_be_withdrawn(
    library: _Library, answered: str
) -> None:
    """The page offers Withdraw where only an earlier generation's automatic
    decision is on the field, so the store accepts it: that decision holds
    nothing and no Resume shows to release it (ADR-0036, ADR-0085)."""
    root = library.root
    earlier = library.automatic()
    if answered != "in_preview":
        root.record_human_decision(
            _accept(library.case(), "authors", "answer-before-leaving", 11)
        )
        # Beside this generation's decision the refusal stands, and says so.
        assert _read_fields(library)["authors"]["owner_control"]["withdraw"] == (
            "automatic_hold"
        )
        with pytest.raises(HumanDecisionError) as refused:
            root.record_human_decision(_withdraw(library, "withdraw-in-automatic", 12))
        assert refused.value.code == "automatic_hold"
    library.leave_automatic()
    if answered == "in_preview":
        root.record_human_decision(
            _accept(library.case(), "authors", "answer-in-preview-mode", 31)
        )
    if answered.endswith("confirmed"):
        # Confirming decides the held field again under its hold, so the
        # field reads as before leaving: the hold, Resume, and the refusal
        # (ADR-0085, Amendment 4).
        _confirm_automatic(library, 40)
        assert library.decisions() == [("authors", 2), ("authors", 3)]
        field = _read_fields(library)["authors"]
        assert (field["manual_hold"], field["owner_control"]["withdraw"]) == (
            True,
            "automatic_hold",
        )
        with pytest.raises(HumanDecisionError) as refused:
            root.record_human_decision(_withdraw(library, "withdraw-confirmed", 41))
        assert refused.value.code == "automatic_hold"
        return
    assert library.decisions() == [("authors", 2)]
    field = _read_fields(library)["authors"]
    assert (field["manual_hold"], field["owner_control"]["withdraw"]) == (
        False,
        "available",
    )

    root.record_human_decision(_withdraw(library, "withdraw-earlier-generation", 50))

    # The earlier decision still writes nothing and nothing holds the field.
    assert earlier.decision_id not in library.pending()
    assert library.pending() == []
    assert root._connection.execute(
        "SELECT count(*) FROM field_effect_manual_holds WHERE active=1"
    ).fetchone() == (0,)
    field = _read_fields(library)["authors"]
    assert field["human_decision"] is None
    assert field["owner_control"]["withdraw"] is None
    assert field["needs_attention"] is False
    # Automatic stays off a field the owner has answered, a withdrawal
    # included, so the page doesn't say turning it on decides it again.
    assert field["earlier_automatic_value"] is None
    if not answered.endswith("confirmed"):
        _confirm_automatic(library, 60)
    assert library.decisions() == [("authors", 2)]
    assert library.pending() == []
    assert _read_fields(library)["authors"]["automatic_decision"] is None
