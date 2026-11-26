"""One work prepared under a qualified Automatic contributor-names policy.

The harness drives a real SQLite repository and the real
``ReviewPreparationPipeline`` with stages that call the real report, proposal,
admission and projection seams. `prepared` yields a `Library` over it;
`applier` builds the guarded application and its scheduler over the same
store, and `planned` reads the write plans it recorded. `Library.close` and
`Library.reopen` stop and start the store over the same file, as an upgrade
with `regenerate-derived` between them does. Moved here from
`tests/integration/test_leaving_automatic_posture.py` so the record's
integration proofs share it (ADR-0108).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

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
from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.regenerate_derived import regenerate_derived
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPipelineError,
    ReviewPipelineRepository,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.cover_evidence import CoverSource
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
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.support.author_evidence import author_report
from tests.support.ledger_partition import rewrite

PRODUCER = "f" * 64
LINEAGE = "connection_name='primary' AND library_name='ebooks' AND item_id='item-1'"


@dataclass
class Held:
    """What Audiobookshelf holds for the work now: the opt-in library model.

    Discovery lists the work with these authors, extraction reads these
    fields into its report, and proposal and admission refuse a report whose
    target differs from them, as `require_report_current` does in production.
    """

    target: TargetFacts
    # What the providers say at the next reads, where it is no longer what
    # they said when the work was prepared.
    providers: tuple[ProviderObservation, ...] | None = None
    # A change to the item discovery sees beyond its title and authors (its
    # source fingerprint), where a test makes one; 0 is none.
    revision: int = 0
    # Every extraction claimed after preparation, and every report proposed.
    extractions: list[PipelineAction] = field(default_factory=list)
    proposed: list[str] = field(default_factory=list)
    # Failure codes the next extractions raise, in order.
    failures: list[str] = field(default_factory=list)

    def write(
        self, destination: TargetDestination, value: str | tuple[str, ...]
    ) -> None:
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
class Library:
    root: SQLiteMetadataReportRepository
    pipeline: ReviewPreparationPipeline
    report: MetadataReport
    admissions: list[tuple[str, str | None]]
    evaluations: list[str]
    now: list[int]
    # Every call that would read the file, Audiobookshelf or a provider in
    # production, by stage, once the harness has prepared its work.
    external: list[str] = field(default_factory=list)
    held: Held | None = None
    database: Path | None = None
    # The pipeline over a store opened again on the same file.
    rebuild: (
        Callable[[SQLiteMetadataReportRepository], ReviewPreparationPipeline] | None
    ) = None
    closed: bool = False

    def close(self) -> None:
        """Close the store, as stopping the server does."""
        if not self.closed:
            self.closed = True
            self.root.close()

    def reopen(self) -> None:
        """Open the store again on the same file, as the next start does,
        with a pipeline whose stages read the same library."""
        assert self.closed and self.database is not None and self.rebuild is not None
        self.root = open_repository(self.database, cross_thread=True)
        self.closed = False
        self.pipeline = self.rebuild(self.root)

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
            "SELECT review_case_id FROM current_review_case_lineages WHERE " + LINEAGE
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


def current_proposal(
    repository: SQLiteMetadataReportRepository, report: MetadataReport
) -> ProposalSet:
    """The report's proposal set under its library's active policy, over the
    ledger's derived epoch, as `CreateProposalSet` reads them."""
    policy = _resolved(repository, report.target.library)
    return evaluate_report(
        report,
        policy.resolved_id,
        human_review_destinations(policy),
        derived_epoch=repository.derived_epoch(),
    )


def _entry(report: MetadataReport, held: Held | None) -> CatalogueEntry:
    """The work as discovery lists it: as prepared, or as the library holds
    it now."""
    return CatalogueEntry(
        "primary",
        "ebooks",
        "lib-1",
        "item-1",
        1,
        "Synthetic work",
        ("Ada Example",) if held is None else held.authors,
        True,
        False,
        "singular-supported",
        report.file.identity,
        "/book.epub",
        "epub",
        "books",
        source_fingerprint=(
            None if held is None or not held.revision else f"{held.revision:064x}"
        ),
    )


@contextmanager
def prepared(
    tmp_path: Path,
    *,
    automatic: bool,
    covers: tuple[CoverSource, ...] = (),
    providers: tuple[ProviderObservation, ...] | None = None,
    before_projection: Callable[[ReviewPipelineRepository, Any], None] | None = None,
    cover_order: str | None = None,
    library_model: Literal["publication", "advance"] | None = None,
) -> Iterator[Library]:
    """One work prepared under a qualified Automatic contributor-names policy.

    With `automatic` false the owner stays in Preview, so the work is first
    prepared there and nothing is decided automatically. `covers` become the
    report's cover sources and `providers`, when given, its providers.
    `before_projection`, when given, runs before each projection as the cover
    stage does in production. `cover_order`, when given, makes covers
    automatic under that cover-order option.

    `library_model`, when given, turns on the library model (`Held`):
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
    held = None if library_model is None else Held(report.target)

    root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, 4)
    discovery = root.claim_review_pipeline_action(5)
    assert discovery is not None
    root.complete_review_discovery(
        discovery,
        1,
        "a" * 64,
        (_entry(report, held),),
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
        return discoveries[0], f"{discoveries[0]:064x}", (_entry(report, held),)

    def propose(repository: ReviewPipelineRepository, report_id: str) -> str:
        concrete = cast(SQLiteMetadataReportRepository, repository)
        value = MetadataReport.from_json(concrete.get(report_id))
        if held is not None:
            held.proposed.append(report_id)
            held.require_current(value)
        proposal = current_proposal(concrete, value)
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

    def build(store: SQLiteMetadataReportRepository) -> ReviewPreparationPipeline:
        return ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(
                database, authority=store.authority
            ),
            libraries=(("primary", "ebooks", "lib-1", PRODUCER),),
            discover=discover,
            extract=extract,
            providers=lambda *_: (),
            propose=propose,
            admit=admit,
            project=project,
            before_projection=(
                (lambda *_args: None)
                if before_projection is None
                else before_projection
            ),
            clock=lambda: now[0],
        )

    value = Library(
        root,
        build(root),
        report,
        admissions,
        evaluations,
        now,
        external,
        held,
        database,
        build,
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
        value.close()


def _extract_held(
    repository: SQLiteMetadataReportRepository,
    action: PipelineAction,
    claim_next: bool,
    held: Held,
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
    providers = prepared.providers if held.providers is None else held.providers
    report = (
        prepared
        if held.target == prepared.target and providers == prepared.providers
        else MetadataReport.create(
            target=held.target,
            file=prepared.file,
            local_claims=prepared.local_claims,
            local_reasons=prepared.local_reasons,
            providers=providers,
            cover_sources=prepared.cover_sources,
        )
    )
    current = repository._connection.execute(
        "SELECT report_id FROM current_metadata_reports WHERE " + LINEAGE
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


def decided_after_the_run(library: Library, decision_id: str) -> bool:
    """Whether the decision was recorded or minted after the latest
    `regenerate-derived` run: an owner decision above the run's ordinal
    high-water mark, or an automatic one, which is derived and so minted
    after it (design 14.5 case 6)."""
    connection = library.root._connection
    (hwm,) = connection.execute(
        "SELECT decision_ordinal_hwm FROM derived_regenerations "
        "ORDER BY derived_epoch_after DESC LIMIT 1"
    ).fetchone()
    human = connection.execute(
        "SELECT ordinal FROM human_decisions WHERE decision_id=?", (decision_id,)
    ).fetchone()
    if human is not None:
        return int(human[0]) > int(hwm)
    return (
        connection.execute(
            "SELECT 1 FROM automatic_decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        is not None
    )


def _not_configured() -> Any:
    raise FileNotFoundError


def regenerate(
    library: Library,
    at: int,
    *,
    retire_pending: bool = False,
    epoch_never_increments: bool = False,
) -> list[str]:
    """Stop the store, run `regenerate-derived` over it as an upgrade leaves it
    (the ledger names another derived version), start the store again (its
    recovery retries the question check) and read every configured library
    again, as the start's scan does (contract S6). Returns the command's
    output lines; the run must succeed.

    `epoch_never_increments` is the epoch negative control (contract S9): a
    test-side rewrite puts the ledger's epoch back after the run, so every
    real reader addresses derived rows as before it."""
    library.close()
    database = library.database
    assert database is not None
    assert rewrite(database, "UPDATE ledger_meta SET derived_version=2") == 1
    lines: list[str] = []
    status = cli.render(
        regenerate_derived(
            SQLiteRegenerationStore(database, lambda: 1_700_000_000 + at),
            retire_pending=retire_pending,
            configuration=_not_configured,
        ),
        lines.append,
    )
    assert status == 0, lines
    lines = [line.rstrip("\n") for line in lines]
    if epoch_never_increments:
        assert (
            rewrite(database, "UPDATE ledger_meta SET derived_epoch=derived_epoch-1")
            == 1
        )
    library.reopen()
    library.pipeline.recover()
    library.root.request_review_scan("primary", "ebooks", "lib-1", PRODUCER, at)
    library.now[0] = at + 1
    library.drain()
    return lines


@pytest.fixture
def library(tmp_path: Path) -> Iterator[Library]:
    """One work prepared and decided in the Automatic posture."""
    with prepared(tmp_path, automatic=True) as value:
        yield value


@pytest.fixture
def preview_library(tmp_path: Path) -> Iterator[Library]:
    """One work first prepared in Preview, before Automatic is confirmed."""
    with prepared(tmp_path, automatic=False) as value:
        yield value


def accept(case: ReviewCase, destination: str, key: str, at: int) -> HumanDecision:
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


def applier(
    library: Library, tmp_path: Path
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


def planned(library: Library) -> dict[str, FieldEffectOperation]:
    return {
        operation.plan.authority.decision_id: operation
        for operation in (
            FieldEffectOperation.from_json(document)
            for (document,) in library.root._connection.execute(
                "SELECT document FROM field_effects"
            ).fetchall()
        )
    }
