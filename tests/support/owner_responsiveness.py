"""Complete synthetic cover evidence and production browse ownership for #1072."""

from __future__ import annotations

import json
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.provider_recovery import (
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.cover_producers import _qualified_cover_report


class OwnerResponsivenessFixture:
    """Own all borrowers until they close before the root database authority."""

    def __init__(self, root: Path, works: int) -> None:
        self.root = root
        self.database = root / "reports.sqlite"
        self.repository = open_repository(self.database, cross_thread=True)
        self.recovery = SQLiteProviderRecoveryStore.open(root / "recovery.sqlite")
        self.sessions = SQLiteReviewSessionRepository.open(root / "sessions.sqlite")
        base, _ = _qualified_cover_report(root)
        review = ReviewWebConfiguration(
            "http://127.0.0.1:18444", "/synthetic/owner.hash"
        )
        self.covers = composition._compose_cover_considerations(
            self.repository,
            root / "artifacts",
            review,
            composition._compose_reviewer_access(self.repository, review),
            self.recovery,
        )
        self.cases: list[ReviewCase] = []
        for index in range(works):
            title = f"Prepared synthetic work {index:04d}"
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
            report = MetadataReport.create(
                target=TargetFacts(
                    "primary",
                    f"books-{index % 5}",
                    f"work-{index:04d}",
                    base.target.fields,
                ),
                file=FileFacts(0, f"{index + 1:064x}", f"{index + works + 1:064x}"),
                local_claims=(*base.local_claims, title_claim),
                local_reasons=(*base.local_reasons, "opening_matter_title"),
                providers=base.providers,
                cover_sources=base.cover_sources,
            )
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
                    value for value in case.proposals if value["destination"] == "cover"
                ),
            )
            self.cases.append(case)
        self.browse = composition.open_review_browse_repository(
            self.database,
            self.repository.authority,
            self.covers,
            self.recovery,
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
            verify_password=lambda password: password == "synthetic-owner",
            session_digest=lambda token: "0" * 64,
            clock=lambda: 10,
            session_lifetime_seconds=3600,
            graceful_shutdown_seconds=2,
            cover_considerations=self.covers,
        )

    def close(self) -> None:
        try:
            self.application.stop()
        finally:
            self.sessions.close()
            self.recovery.close()
