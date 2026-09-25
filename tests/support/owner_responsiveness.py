"""Complete synthetic cover evidence and production browse ownership for #1072."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path

import uvicorn
from argon2 import PasswordHasher

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.operation_admission import OperationAdmission
from mechanical_librarian.application.owner_settings import (
    FileOwnerSettingsRepository,
    OwnerSettingsOperations,
    file_settings_projection,
)
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPipelineError,
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderClaim,
    TargetDestination,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.interfaces.web import (
    create_review_app,
    owner_password_binding,
)
from tests.support.cover_producers import _qualified_cover_report
from tests.support.ports import ReservedPort


class OwnerResponsivenessFixture:
    """Own all borrowers until they close before the root database authority."""

    def __init__(
        self,
        root: Path,
        works: int,
        *,
        mixed: bool = False,
        prepare: bool = False,
        origin: str = "http://127.0.0.1:18444",
    ) -> None:
        self.root = root
        self.database = root / "reports.sqlite"
        self._close_external_actors: Callable[[], None] | None = None
        with ExitStack() as activation:
            self.repository = open_repository(
                self.database, cross_thread=True, admission=OperationAdmission()
            )
            activation.callback(self.repository.close)
            self.recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
            activation.callback(self.recovery.close)
            self.sessions = SQLiteReviewSessionRepository.open(root / "sessions.sqlite")
            activation.callback(self.sessions.close)
            base, _ = _qualified_cover_report(root)
            review = ReviewWebConfiguration(origin, "/synthetic/owner.hash")
            self.review = review
            self.reviewer_access = composition._compose_reviewer_access(
                self.repository, review
            )
            self.covers = composition._compose_cover_considerations(
                self.repository,
                root / "artifacts",
                review,
                self.reviewer_access,
                self.recovery,
            )
            self.cases: list[ReviewCase] = []
            self.reports: dict[str, MetadataReport] = {}
            for index in range(works):
                title = f"Prepared synthetic work {index:04d}"
                if mixed and index % 10 == 0:
                    title += (
                        " — A deliberately long account of libraries, uncertain evidence and careful decisions"
                        * 3
                    )
                title_claim = LocalClaim.create(
                    kind=ClaimKind.TITLE,
                    observed=title,
                    canonical=title,
                    excerpt=title,
                    applicability="not_applicable",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    scope=ClaimScope.WORK,
                )
                providers = base.providers
                if mixed and index % 5 == 0:
                    provider = providers[0]
                    candidate = provider.candidates[0]
                    rich_claims = tuple(
                        ProviderClaim.create(
                            provider=provider.provider,
                            candidate=candidate.identity,
                            kind=kind,
                            value=value,
                            state=FieldState.VALID,
                            scope=ClaimScope.WORK,
                            applicability="not_applicable",
                            raw_order=order,
                        )
                        for order, (kind, value) in enumerate(
                            (
                                (ClaimKind.TITLE, title),
                                (
                                    ClaimKind.CREATOR,
                                    "Synthetic Author With A Long Name",
                                ),
                                (
                                    ClaimKind.DESCRIPTION,
                                    (
                                        "A synthetic account of shared libraries and careful decisions. "
                                        * 40
                                    ).strip(),
                                ),
                            ),
                            start=1,
                        )
                    )
                    providers = (
                        replace(
                            provider,
                            candidates=(
                                replace(
                                    candidate, claims=(*candidate.claims, *rich_claims)
                                ),
                            ),
                        ),
                    )
                report = MetadataReport.create(
                    target=TargetFacts(
                        "primary",
                        f"books-{index % 5}",
                        f"work-{index:04d}",
                        tuple(
                            replace(field, value=title)
                            if mixed
                            and index % 10 == 0
                            and field.destination is TargetDestination.TITLE
                            else field
                            for field in base.target.fields
                        ),
                    ),
                    file=FileFacts(0, f"{index + 1:064x}", f"{index + works + 1:064x}"),
                    local_claims=(*base.local_claims, title_claim),
                    local_reasons=(*base.local_reasons, "opening_matter_title"),
                    providers=providers,
                    cover_sources=base.cover_sources,
                )
                self.reports[report.target.item] = report
                if mixed and index == works - 1:
                    self.repository.publish(report, lambda: True)
                    continue
                proposal = evaluate_report(report)
                self.repository.publish(report, lambda: True)
                self.repository.publish_proposal(proposal)
                case_id = json.loads(self.repository.admit_review_case(proposal))[
                    "review_case_id"
                ]
                case = ReviewCase.create_from_data(
                    json.loads(self.repository.get_review_case(case_id))["case"]
                )
                self.covers.ensure(
                    case_id,
                    report.to_json(),
                    next(
                        value
                        for value in case.proposals
                        if value["destination"] == "cover"
                    ),
                )
                self.cases.append(case)
            if works == 0:
                self.reports = {
                    report.target.item: report
                    for report in (
                        MetadataReport.from_json(row[0])
                        for row in self.repository._connection.execute(
                            "SELECT document FROM metadata_reports"
                        )
                    )
                }
                self.cases = sorted(
                    (
                        ReviewCase.from_json(row[0])
                        for row in self.repository._connection.execute(
                            "SELECT document FROM review_cases"
                        )
                    ),
                    key=lambda case: str(case.target["item"]),
                )
            self.browse = self.repository
            self.preparation = self._preparation() if prepare else None
            configuration = ApplicationConfiguration(
                (TargetConnectionConfiguration("primary", "http://127.0.0.1:1", None),),
                tuple(
                    LibraryConfiguration(
                        f"books-{index}", "primary", f"books-{index}", "ebook", None, ()
                    )
                    for index in range(5)
                ),
                (),
                (),
                review=review,
            )
            self.configuration = configuration
            activity, close_activity = (
                composition.open_field_effect_activity_projection(
                    configuration,
                    self.database,
                    self.repository.authority,
                    self.recovery,
                    admission=self.repository.operation_admission,
                )
            )
            activation.callback(close_activity)
            settings = file_settings_projection(configuration)
            owner_binding = owner_password_binding(
                PasswordHasher().hash("synthetic-owner")
            )
            self.application = compose_review_application(
                state_owner=self.repository,
                session_store=self.sessions,
                case_store=self.repository,
                decision_store=self.repository,
                library_store=self.browse,
                policy_store=self.repository,
                browse_observation=self.browse,
                admission=self.repository.operation_admission,
                operation_lock=self.repository.operation_lock,
                close_state_owner=True,
                verify_password=owner_binding[0],
                session_digest=owner_binding[1],
                clock=lambda: int(time.time()),
                session_lifetime_seconds=3600,
                graceful_shutdown_seconds=2,
                cover_considerations=self.covers,
                reviewer_access=self.reviewer_access,
                activity_projection=activity,
                close_activity_projection=close_activity,
                preparation=self.preparation,
                settings=OwnerSettingsOperations(
                    FileOwnerSettingsRepository(settings.document, settings.secrets)
                ),
                policy_libraries=tuple((f"books-{index}", None) for index in range(5)),
            )

            if mixed and len(self.cases) > 1:
                case = self.cases[1]
                title_proposal = next(
                    value for value in case.proposals if value["destination"] == "title"
                )
                proposal_id = title_proposal["proposal_id"]
                assert type(proposal_id) is str
                self.application.record_decision(
                    case.review_case_id,
                    proposal_id=proposal_id,
                    target_fingerprint=case.target_fingerprint,
                    destination="title",
                    actor="owner",
                    outcome="keep_current",
                    selected_value=None,
                    idempotency_key="fixture-owner-final-title",
                )
            activation.pop_all()

    def _preparation(self) -> ReviewPreparationSupervisor:
        """Replay synthetic observation through the real persisted preparation actors."""

        def repository():
            return open_repository(
                self.database,
                create=False,
                authority=self.repository.authority,
                admission=self.repository.operation_admission,
            )

        def discover(_connection: str, library: str):
            reports = [
                report
                for report in self.reports.values()
                if report.target.library == library
            ]
            return (
                1,
                "f" * 64,
                tuple(
                    CatalogueEntry(
                        "primary",
                        library,
                        library,
                        report.target.item,
                        1,
                        "Synthetic preparation " + report.target.item,
                        (),
                        True,
                        False,
                        "singular-supported",
                        report.file.identity,
                        "/synthetic.epub",
                        "epub",
                        "books",
                    )
                    for report in reports
                ),
            )

        def extract(bound, action, claim_next):
            report = self.reports[action.item_id]
            if action.item_id == sorted(self.reports)[-1]:
                raise ReviewPipelineError("unsupported_media")
            bound.publish(
                report,
                lambda: True,
                after_publication=lambda current, published: (
                    current.complete_review_extraction_publication(
                        action,
                        published.report_id,
                        int(time.time()),
                        0,
                        None,
                        claim_next,
                        None,
                    )
                ),
            )
            return report.report_id, True

        proposals = {
            report.report_id: evaluate_report(report)
            for report in self.reports.values()
        }
        by_proposal = {
            proposal.proposal_set_id: proposal for proposal in proposals.values()
        }

        def propose(bound, report_id):
            proposal = proposals[report_id]
            bound.publish_proposal(proposal)
            return proposal.proposal_set_id

        def admit(bound, proposal_id):
            result = json.loads(bound.admit_review_case(by_proposal[proposal_id]))
            return result["status"], result.get("review_case_id")

        pipeline = ReviewPreparationPipeline(
            admission=self.repository.operation_admission,
            repository_factory=repository,
            observation_repository_factory=lambda: open_repository(
                self.database,
                create=False,
                authority=self.repository.authority,
                cross_thread=True,
                admission=self.repository.operation_admission,
            ),
            libraries=tuple(
                ("primary", f"books-{index}", f"books-{index}", "f" * 64)
                for index in range(5)
            ),
            discover=discover,
            extract=extract,
            providers=lambda *_: (),
            propose=propose,
            admit=admit,
            project=lambda *_: True,
        )
        return ReviewPreparationSupervisor(pipeline, polling_interval_seconds=None)

    @contextmanager
    def hold_external_actors(self) -> Iterator[OwnerExternalActors]:
        actors = OwnerExternalActors(self)
        self._close_external_actors = actors.close
        try:
            actors.start()
            yield actors
        finally:
            actors.close()

    def close(self) -> None:
        if self._close_external_actors is not None:
            self._close_external_actors()
        self.application.stop()
        self.sessions.close()
        self.recovery.close()


@contextmanager
def running_owner_fixture(root: Path, works: int, *, prepare: bool = False):
    """Serve the same complete synthetic fixture over explicit local HTTP."""
    reservation = ReservedPort()
    origin = f"http://127.0.0.1:{reservation.number}"
    fixture = OwnerResponsivenessFixture(
        root, works, mixed=True, prepare=prepare, origin=origin
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_review_app(fixture.application, fixture.review),
            host="127.0.0.1",
            port=reservation.number,
            log_level="error",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    reservation.release()
    try:
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive():
            if time.monotonic() >= deadline:
                raise AssertionError("synthetic owner server did not start")
            time.sleep(0.01)
        assert server.started
        yield fixture, origin
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()
        fixture.close()
        reservation.close()


def measure_owner_routes(
    fixture: OwnerResponsivenessFixture, origin: str, samples: int
):
    """Retain every real HTTP sample, including failures and budget misses."""
    import math
    import statistics

    import httpx

    routes = {
        "library": "/api/v1/library?lens=all&limit=25",
        "search": "/api/v1/library?lens=all&limit=25&q=synthetic",
        "attention": "/api/v1/attention?limit=25",
        "detail": f"/api/v1/review-cases/{fixture.cases[2].review_case_id}",
        "policy": "/api/v1/metadata-policy/state",
        "settings": "/api/v1/settings",
        "preparation": "/api/v1/review-preparation",
    }
    values: dict[str, list[float]] = {name: [] for name in routes}
    misses: list[dict[str, object]] = []
    preparation_progress: list[dict[str, object]] = []
    with httpx.Client(
        base_url=origin, headers={"Origin": origin}, timeout=30
    ) as client:
        assert (
            client.post(
                "/api/v1/session/login", json={"password": "synthetic-owner"}
            ).status_code
            == 200
        )
        if fixture.preparation is not None:
            for index in range(5):
                fixture.preparation.request("primary", f"books-{index}")
        for sample in range(samples):
            for name, route in routes.items():
                started = time.perf_counter()
                response = client.get(route)
                elapsed = (time.perf_counter() - started) * 1000
                values[name].append(elapsed)
                if name == "preparation" and response.status_code == 200:
                    state = response.json()
                    preparation_progress.append(
                        {
                            "sample": sample,
                            "active": state["active"],
                            "checked": sum(
                                library["checked"] for library in state["libraries"]
                            ),
                            "blocked": sum(
                                library["blocked"] for library in state["libraries"]
                            ),
                        }
                    )
                if response.status_code != 200 or elapsed > 2000:
                    misses.append(
                        {
                            "route": name,
                            "sample": sample,
                            "status": response.status_code,
                            "ms": elapsed,
                        }
                    )
    summary = {
        name: {
            "n": len(rows),
            "p50_ms": statistics.median(rows),
            "p95_ms": sorted(rows)[max(0, math.ceil(len(rows) * 0.95) - 1)],
            "max_ms": max(rows),
            "samples_ms": rows,
        }
        for name, rows in values.items()
    }
    return {
        "routes": summary,
        "misses": misses,
        "preparation_progress": preparation_progress,
    }


class OwnerExternalActors:
    """Hold two real application actors at fake external leaves for one warm pass."""

    def __init__(self, fixture: OwnerResponsivenessFixture) -> None:
        from mechanical_librarian.adapters.field_effect_authority import (
            PersistedFieldEffectAuthority,
        )
        from mechanical_librarian.adapters.field_effect_fakes import (
            FakeBackupReadiness,
            FakeCapabilityObserver,
            FakeForwardReevaluation,
            FakeTargetService,
            PrivateCoverRecoveryStore,
        )
        from mechanical_librarian.adapters.field_effect_runtime import (
            PersistedEffectConsent,
            PersistedEffectDependencies,
        )
        from mechanical_librarian.application.continuous_field_effects import (
            ContinuousGuardedApplication,
        )
        from mechanical_librarian.application.field_effects import (
            GuardedFieldApplication,
            TargetEffectResult,
            TargetObservation,
        )
        from mechanical_librarian.domain.field_effect import FieldEffectPlan
        from mechanical_librarian.domain.human_decision import HumanDecision

        self.fixture = fixture
        self._release = threading.Event()
        self._entered = {name: threading.Event() for name in ("effect", "reviewer")}
        self._threads: list[threading.Thread] = []
        self._errors: list[BaseException] = []
        self._closed = False
        self._target_calls: list[str] = []
        self.receipt: dict[str, object] = {
            "coverage": "first warm interaction pass only",
            "hard_hold_seconds": 60,
            "events": [],
            "confirmed_unrelated_cases": [],
        }
        self._events: list[dict[str, object]] = []
        self._confirmed: list[str] = []
        self.receipt["events"] = self._events
        self.receipt["confirmed_unrelated_cases"] = self._confirmed
        self._event_lock = threading.Lock()
        self._admission = fixture.repository.operation_admission
        assert self._admission is not None
        self._before_reviewer = fixture.reviewer_access._repository.snapshot()[0]
        self._before_identity = (
            fixture.reviewer_access.current_cover_observation_identity()
        )
        self._before_authority = fixture.covers.current_authority()
        self._original_factory = fixture.reviewer_access._openrouter_factory
        self.reserved_case_id = fixture.cases[-1].review_case_id
        case = fixture.cases[-1]
        proposal = next(
            value for value in case.proposals if value["destination"] == "title"
        )
        result = proposal["result"]
        assert type(result) is dict
        alternatives = result["alternatives"]
        assert type(alternatives) is list and alternatives
        alternative = alternatives[0]
        assert type(alternative) is dict
        proposal_id = proposal["proposal_id"]
        assert type(proposal_id) is str
        acknowledgement = fixture.application.record_decision(
            case.review_case_id,
            proposal_id=proposal_id,
            target_fingerprint=case.target_fingerprint,
            destination="title",
            actor="owner",
            outcome="accept",
            selected_value=alternative["value"],
            idempotency_key="owner-overlap-reserved-title",
        )
        confirmation = acknowledgement["confirmation"]
        assert type(confirmation) is dict and confirmation["status"] == "confirmed"
        decision = next(
            HumanDecision.from_json(raw)
            for raw in fixture.repository.get_review_case_decisions(case.review_case_id)
            if json.loads(raw).get("decision_id") == acknowledgement["decision_id"]
        )
        authority = PersistedFieldEffectAuthority(fixture.repository)
        snapshot = authority.resolve(decision)
        self.receipt["reserved_case_id"] = case.review_case_id
        self.receipt["reserved_work"] = snapshot.work_id
        self.receipt["work_count"] = len(fixture.reports)
        self.receipt["library_count"] = len(
            {report.target.library for report in fixture.reports.values()}
        )
        capability = FakeCapabilityObserver("a" * 64)
        actors = self

        class HeldTarget(FakeTargetService):
            def apply(self, plan: FieldEffectPlan) -> TargetEffectResult:
                actors._hold("effect")
                value = super().apply(plan)
                actors._target_calls.append("effect_return")
                actors._event("effect_return")
                return value

            def read(self, plan: FieldEffectPlan) -> TargetObservation:
                actors._assert_external()
                actors._target_calls.append("read")
                return super().read(plan)

        self._target = HeldTarget()

        def now() -> int:
            return int(time.time())

        fixture.repository.grant_field_effect_consents(
            snapshot.connection, (snapshot.library,), ("title",), now()
        )
        guarded = GuardedFieldApplication(
            fixture.repository,
            self._target,
            FakeBackupReadiness(),
            capability,
            authority,
            PersistedEffectDependencies(fixture.repository),
            PersistedEffectConsent(fixture.repository),
            PrivateCoverRecoveryStore(
                fixture.root / "effect-recovery", lambda _: b"old-cover"
            ),
            FakeForwardReevaluation(),
            now,
        )
        operation = guarded.plan(
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
            capability_digest=capability.digest,
        )
        assert operation is not None
        self._plan_id = operation.plan.plan_id
        self._target.seed(operation.plan)
        self._lane = ContinuousGuardedApplication(
            fixture.repository,
            {(snapshot.connection, snapshot.library): guarded},
            {(snapshot.connection, snapshot.library): capability},
            authority,
            ((snapshot.connection, snapshot.library),),
            now,
            lambda *_: None,
            operation_admission=self._admission,
        )
        self._lane.attach_serialization(fixture.application.serialized)

    def _event(self, name: str) -> None:
        with self._event_lock:
            self._events.append(
                {
                    "event": name,
                    "monotonic_ms": time.perf_counter() * 1000,
                    "epoch_ms": time.time() * 1000,
                }
            )

    def _assert_external(self) -> None:
        assert self._admission is not None and not self._admission.held
        owner = self.fixture.repository._serialization_lock
        assert owner is not None and owner.depth() == 0

    def _hold(self, name: str) -> None:
        self._assert_external()
        self._event(name + "_enter")
        self._entered[name].set()
        assert self._release.wait(60), "synthetic external hold exceeded sixty seconds"

    def start(self) -> None:
        from mechanical_librarian.application.reviewer_contracts import (
            OpenRouterAccountStatus,
        )

        actors = self

        class HeldReviewer:
            def probe_description(self) -> OpenRouterAccountStatus:
                actors._hold("reviewer")
                return OpenRouterAccountStatus(
                    tier="free",
                    model="example/model",
                    input_modalities=("text",),
                    output_modalities=("text",),
                    structured_outputs=True,
                    observed_at=1,
                    prompt_price="0.000001",
                    completion_price="0.000002",
                    request_price="0",
                )

            def probe(self) -> OpenRouterAccountStatus:
                raise AssertionError("unexpected cover probe")

            def run(
                self,
                profile: bytes,
                instruction: bytes,
                packet: bytes,
                schema: bytes,
                images: tuple[tuple[str, str, bytes], ...],
            ) -> str:
                raise AssertionError("reviewer execution is outside this fixture")

            def run_description(
                self, profile: bytes, instruction: bytes, packet: bytes, schema: bytes
            ) -> str:
                raise AssertionError("reviewer execution is outside this fixture")

            def close(self) -> None:
                actors._event("reviewer_close")

        self.fixture.reviewer_access._openrouter_factory = lambda _key, _model: (
            HeldReviewer()
        )

        def worker(name: str) -> None:
            try:
                assert self._admission is not None
                with self._admission.classify("background"):
                    if name == "effect":
                        effect_result = self._lane.apply(self._plan_id)
                        assert effect_result.state == "verified"
                        self.receipt["effect_state"] = effect_result.state
                    else:
                        reviewer_result = (
                            self.fixture.reviewer_access.test_description_model(
                                "synthetic-key", "example/model"
                            )
                        )
                        assert reviewer_result.model == "example/model"
                        self.receipt["reviewer_model"] = reviewer_result.model
            except BaseException as error:
                self._errors.append(error)

        for name in self._entered:
            thread = threading.Thread(target=worker, args=(name,))
            self._threads.append(thread)
            thread.start()
        assert all(event.wait(5) for event in self._entered.values()), self._errors
        self.receipt["both_held"] = True

    def confirm_unrelated(self, review_case_id: str) -> None:
        assert review_case_id != self.reserved_case_id
        assert not self._release.is_set()
        detail = self.fixture.application.case_detail(review_case_id)
        assert detail.current
        assert any(
            type(field["human_decision"]) is dict
            and field["human_decision"]["actor"] == "owner"
            for field in detail.fields
        )
        self._confirmed.append(review_case_id)
        self._event("unrelated_durable_confirmation")

    def close(self) -> None:
        if self._closed:
            return
        self._event("release")
        self._release.set()
        for thread in self._threads:
            thread.join(5)
        assert not any(thread.is_alive() for thread in self._threads)
        self.fixture.reviewer_access._openrouter_factory = self._original_factory
        self._closed = True
        self.fixture._close_external_actors = None
        self.receipt["errors"] = [type(error).__name__ for error in self._errors]
        assert not self._errors, self._errors
        assert (
            self.fixture.reviewer_access._repository.snapshot()[0]
            == self._before_reviewer
        )
        assert (
            self.fixture.reviewer_access.current_cover_observation_identity()
            == self._before_identity
        )
        assert self.fixture.covers.current_authority() == self._before_authority
        events = [
            json.loads(raw)
            for raw in self.fixture.repository.field_effect_event_documents(
                self._plan_id
            )
        ]
        assert sum(event["state"] == "verified" for event in events) == 1
        assert len(self._target._effects) == 1
        assert self._target_calls[self._target_calls.index("effect_return") + 1 :] == [
            "read"
        ]
        total, _ = self.fixture.repository.count_current_review_cases(None)
        assert total == len(self.fixture.reports)
        self.receipt.update(
            {
                "verified_events": 1,
                "target_effects": 1,
                "reviewer_unchanged": True,
                "cover_identity_unchanged": True,
                "census_unchanged": True,
                "target_calls": self._target_calls,
            }
        )
