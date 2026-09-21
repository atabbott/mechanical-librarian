"""Complete synthetic cover evidence and production browse ownership for #1072."""

from __future__ import annotations

import json
import threading
import time
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
        with ExitStack() as activation:
            self.repository = open_repository(self.database, cross_thread=True)
            activation.callback(self.repository.close)
            self.recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
            activation.callback(self.recovery.close)
            self.sessions = SQLiteReviewSessionRepository.open(root / "sessions.sqlite")
            activation.callback(self.sessions.close)
            base, _ = _qualified_cover_report(root)
            review = ReviewWebConfiguration(origin, "/synthetic/owner.hash")
            self.review = review
            self.covers = composition._compose_cover_considerations(
                self.repository,
                root / "artifacts",
                review,
                composition._compose_reviewer_access(self.repository, review),
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
            self.browse = composition.open_review_browse_repository(
                self.database,
                self.repository.authority,
                self.covers,
                self.recovery,
            )
            activation.callback(self.browse.close)
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
                report_store=self.repository,
                policy_store=self.repository,
                browse_observation=self.browse.browse_observation,
                close_browse=self.browse.close,
                close_state_owner=True,
                verify_password=owner_binding[0],
                session_digest=owner_binding[1],
                clock=lambda: int(time.time()),
                session_lifetime_seconds=3600,
                graceful_shutdown_seconds=2,
                cover_considerations=self.covers,
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
                self.database, create=False, authority=self.repository.authority
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
            repository_factory=repository,
            observation_repository_factory=lambda: open_repository(
                self.database,
                create=False,
                authority=self.repository.authority,
                cross_thread=True,
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

    def close(self) -> None:
        try:
            self.application.stop()
        finally:
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
