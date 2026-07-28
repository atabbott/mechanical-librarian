from __future__ import annotations

import io
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mechanical_librarian.bootstrap import composition
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.adapters.reviewers import codex_shadow
from mechanical_librarian.adapters.reviewers.codex_shadow import (
    CodexShadowReviewer,
    QualificationProfile,
    ShadowReviewerError,
    load_qualification_profile,
)
from mechanical_librarian.application.shadow_reviews import (
    CitationNormalizationMarker,
    ShadowReviewError,
    ShadowReviewRunResult,
    execute_shadow_review,
    inspect_shadow_review,
    preview_shadow_review,
)
from mechanical_librarian.application.failure_diagnostics import (
    reviewer_failure_diagnostic,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionStaleError,
    ReviewCase,
)
from mechanical_librarian.domain.shadow_review import (
    MAX_LINKAGE_FORMAT_FACTS,
    MAX_LINKAGE_IDENTIFIER_FACTS,
    MAX_PACKET_BYTES,
    MAX_PROFILE_IDENTITY_BYTES,
    MAX_REQUEST_BYTES,
    ReviewerProfileIdentity,
    ShadowReviewDocumentError,
    ShadowReviewIdentityError,
    ShadowReviewPacket,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewSemanticError,
    ShadowReviewValueError,
    canonical_json,
)
from mechanical_librarian.interfaces.cli import main
from tests.unit.test_review_case_persistence import _report


def _identity() -> ReviewerProfileIdentity:
    return ReviewerProfileIdentity(
        "a" * 64,
        "codex-cli 0.146.0-alpha.3.1",
        "b" * 64,
        "bubblewrap-v1",
        "gpt-5.6-sol",
        "medium",
        "shadow-review-prompt-v2",
        "codex-shadow-profile-v1",
        "shadow-review-packet-v2",
        "shadow-review-result-v1",
        "qualification-only-policy-v1",
        120,
        65_536,
        536_870_912,
        16,
        131_072,
    )


def _failure_output(code: str) -> str:
    return canonical_json(
        {
            "diagnostic": reviewer_failure_diagnostic(code).to_data(),
            "mode": "qualification_only",
            "schema_version": "shadow-review-failure-v2",
        }
    ) + "\n"


def _result(request: ShadowReviewRequest) -> ShadowReviewResult:
    recommendations = []
    citation = request.packet.citations[0]["citation_id"]
    for proposal in request.packet.proposals:
        candidate = proposal["candidates"][0]["candidate_id"]
        recommendations.append(
            {
                "candidate_id": candidate,
                "citations": [citation],
                "problems": [],
                "proposal_id": proposal["proposal_id"],
                "reasons": ["The cited evidence supports this exact candidate."],
                "recommendation": "recommend_candidate",
            }
        )
    value = ShadowReviewResult(
        request.review_request_id, tuple(recommendations)
    )
    value.validate_for(request)
    return value


def _request_with_citations(
    count: int = 2,
    *,
    seed: str = "a",
    identity: ReviewerProfileIdentity | None = None,
) -> ShadowReviewRequest:
    profile_identity = _identity() if identity is None else identity
    request = ShadowReviewRequest.synthetic_canary(profile_identity)
    packet = request.packet.to_data()
    fact = packet["citations"][0]["fact"]
    packet["citations"] = [
        {
            "citation_id": f"synthetic_citation_{seed}{index:063x}",
            "fact": fact,
        }
        for index in range(count)
    ]
    return ShadowReviewRequest.create_from_packet(
        profile_identity,
        ShadowReviewPacket(
            packet["review_case_id"],
            tuple(packet["proposals"]),
            tuple(packet["citations"]),
            tuple(packet["correlations"]),
            packet["schema_version"],
        ),
    )


class _Runner:
    def __init__(self, *, fail: str | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def run(self, request, dispatched):
        self.calls += 1
        dispatched(12345, 12345, "777")
        if self.fail is not None:
            raise ShadowReviewerError(self.fail, count=9, digest="c" * 64)
        return ShadowReviewRunResult(
            _result(request), CitationNormalizationMarker(False, 0)
        )


class ShadowReviewTests(unittest.TestCase):
    def test_publication_candidate_projects_typed_source_and_access_context(
        self,
    ) -> None:
        lexical = "2024-03-05T10:30:00-04:00"
        claim = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value=lexical,
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
            raw_order=2,
        )
        identifier = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ISBN,
            value="9780061120084",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        asin = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ASIN,
            value="b000000001",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
            raw_order=1,
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        ProviderCandidate(
                            "edition-1",
                            "work-1",
                            (identifier, asin, claim),
                            (
                                ProviderEvidence(
                                    ProviderEvidenceKind.PHYSICAL_FORMAT,
                                    "ebook",
                                    ClaimScope.EDITION,
                                ),
                            ),
                        ),
                    ),
                    ProviderQueryKind.WORK,
                    "hardcover",
                    accessed_at="2023-11-14T22:13:20.125000Z",
                ),
            ),
        )
        report = MetadataReport.from_json(report.to_json())
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        with self.assertRaisesRegex(
            ShadowReviewValueError, "provider source is unavailable"
        ):
            ShadowReviewPacket.from_json(packet.to_json())
        projected = next(
            value
            for value in packet.proposals
            if value["destination"] == "publishedDate"
        )["candidates"][0]
        self.assertEqual("manifestation_publication", projected["date_event"])
        self.assertEqual("day", projected["date_precision"])
        citation = next(
            value
            for value in packet.citations
            if value["citation_id"] == claim.reference
        )["fact"]
        self.assertEqual("hardcover", citation["access"]["authority"])
        self.assertEqual("work", citation["access"]["query_kind"])
        self.assertEqual(
            "2023-11-14T22:13:20.125000Z",
            citation["access"]["accessed_at"],
        )
        self.assertEqual("edition-1", citation["candidate"])
        self.assertEqual("work-1", citation["work_identity"])
        self.assertEqual("B000000001", asin.value)
        expected_identifier_facts = sorted(
            [
                {
                    "applicability": "ebook",
                    "kind": "isbn",
                    "raw_order": 0,
                    "reference": identifier.reference,
                    "scope": "edition",
                    "value": "9780061120084",
                },
                {
                    "applicability": "ebook",
                    "kind": "asin",
                    "raw_order": 1,
                    "reference": asin.reference,
                    "scope": "edition",
                    "value": "B000000001",
                },
            ],
            key=lambda fact: fact["reference"],
        )
        self.assertEqual(
            [fact["reference"] for fact in expected_identifier_facts],
            citation["linkage"]["identifier_references"],
        )
        self.assertEqual(
            expected_identifier_facts,
            citation["linkage"]["identifier_facts"],
        )
        self.assertEqual(
            "ebook",
            citation["linkage"]["format_facts"][0]["value"],
        )
        self.assertEqual(lexical, citation["fact"]["date_lexical"])
        self.assertEqual("timestamp", citation["fact"]["date_precision"])

        document = json.loads(packet.to_json())
        published = next(
            value
            for value in document["proposals"]
            if value["destination"] == "publishedDate"
        )
        published["candidates"][0]["date_event"] = "work_first_publication"
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        def provider_date(document: dict[str, object]) -> dict[str, object]:
            return next(
                value
                for value in document["citations"]
                if value["citation_id"] == claim.reference
            )["fact"]

        for field, invalid in (
            ("applicability", "print"),
            ("kind", "title"),
            ("raw_order", 99),
            ("reference", "provider_" + "0" * 64),
            ("scope", "work"),
            ("value", "9780061120085"),
            ("value", "sk-proj-credential-shaped"),
        ):
            document = json.loads(packet.to_json())
            linked = provider_date(document)["linkage"]
            fact = next(
                value
                for value in linked["identifier_facts"]
                if value["kind"] == "isbn"
            )
            fact[field] = invalid
            with self.subTest(field=field, invalid=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        linked = provider_date(document)["linkage"]
        next(
            value
            for value in linked["identifier_facts"]
            if value["kind"] == "asin"
        )["value"] = "A000000001"
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        for extra in ("role", "state"):
            document = json.loads(packet.to_json())
            provider_date(document)["linkage"]["identifier_facts"][0][extra] = (
                None if extra == "role" else "malformed"
            )
            with self.subTest(extra=extra):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)

        for field, invalid in (
            ("candidate", "edition-2"),
            ("provider", "other"),
            ("candidate", "/private/library/edition"),
            ("work_identity", "api_key=private"),
        ):
            document = json.loads(packet.to_json())
            provider_date(document)[field] = invalid
            with self.subTest(context=field, invalid=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        provider_date(document)["linkage"]["identifier_facts"].pop()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        provider_date(document)["linkage"]["identifier_references"].pop()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        linked = provider_date(document)["linkage"]
        linked["identifier_facts"].append(linked["identifier_facts"][0].copy())
        linked["identifier_references"].append(
            linked["identifier_references"][0]
        )
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        linked = provider_date(document)["linkage"]
        linked["identifier_facts"].extend(
            linked["identifier_facts"][0].copy()
            for _ in range(MAX_LINKAGE_IDENTIFIER_FACTS - 1)
        )
        linked["identifier_references"].extend(
            linked["identifier_references"][0]
            for _ in range(MAX_LINKAGE_IDENTIFIER_FACTS - 1)
        )
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        linked = provider_date(document)["linkage"]
        linked["identifier_facts"].reverse()
        linked["identifier_references"].reverse()
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        linked = provider_date(document)["linkage"]
        linked["format_facts"][0]["role"] = "narrator"
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        for invalid in (
            "/private/library/format",
            "api_key=private",
        ):
            document = json.loads(packet.to_json())
            provider_date(document)["linkage"]["format_facts"][0][
                "value"
            ] = invalid
            with self.subTest(format_value=invalid):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        del provider_date(document)["linkage"]
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        invented_identifier = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ISBN,
            value="9780306406157",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
            raw_order=7,
        )
        invented_identifier_data = invented_identifier.to_data()
        invented_identifier_fact = {
            key: invented_identifier_data[key]
            for key in (
                "applicability",
                "kind",
                "raw_order",
                "reference",
                "scope",
                "value",
            )
        }
        invented_format = ProviderEvidence(
            ProviderEvidenceKind.PHYSICAL_FORMAT,
            "hardcover",
            ClaimScope.EDITION,
        ).to_data()

        def add_identifier(linkage: dict[str, object]) -> None:
            linkage["identifier_facts"].append(invented_identifier_fact)
            linkage["identifier_facts"].sort(
                key=lambda fact: fact["reference"]
            )
            linkage["identifier_references"][:] = [
                fact["reference"] for fact in linkage["identifier_facts"]
            ]

        def add_format(linkage: dict[str, object]) -> None:
            linkage["format_facts"].append(invented_format)
            linkage["format_facts"].sort(key=canonical_json)

        for name, mutate in (
            ("invented_valid_isbn", add_identifier),
            ("invented_physical_format", add_format),
        ):
            document = json.loads(packet.to_json())
            mutate(provider_date(document)["linkage"])
            forged_json = canonical_json(document)
            with self.subTest(binding=name, document="packet"):
                with self.assertRaisesRegex(
                    ShadowReviewValueError, "source binding is invalid"
                ):
                    ShadowReviewPacket.from_json(forged_json, report)
            forged_packet = ShadowReviewPacket(
                document["review_case_id"],
                tuple(document["proposals"]),
                tuple(document["citations"]),
                tuple(document["correlations"]),
                document["schema_version"],
            )
            forged_request = ShadowReviewRequest.create_from_packet(
                _identity(), forged_packet
            )
            with self.subTest(binding=name, document="request"):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewRequest.from_json(
                        forged_request.to_json(), report
                    )

        request = ShadowReviewRequest.create_from_packet(_identity(), packet)
        with self.assertRaisesRegex(
            ShadowReviewValueError, "request is invalid"
        ):
            ShadowReviewRequest.from_json(request.to_json())
        self.assertEqual(
            request, ShadowReviewRequest.from_json(request.to_json(), report)
        )
        request_document = json.loads(request.to_json())
        provider_date(request_document["packet"])["linkage"][
            "identifier_facts"
        ][0]["raw_order"] = 99
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewRequest.from_json(canonical_json(request_document), report)

        self.assertEqual(16, MAX_LINKAGE_IDENTIFIER_FACTS)
        self.assertEqual(16, MAX_LINKAGE_FORMAT_FACTS)

    def test_provider_rich_date_evidence_requires_content_bound_linkage(
        self,
    ) -> None:
        identifier = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.ISBN,
            value="9780061120084",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        first_publication = ProviderEvidence(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            "2001-02-03",
            ClaimScope.WORK,
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        ProviderCandidate(
                            "edition-1",
                            "work-1",
                            (identifier,),
                            (first_publication,),
                        ),
                    ),
                ),
            ),
        )
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        replayed = ShadowReviewPacket.from_json(packet.to_json(), report)
        rich = next(
            citation["fact"]
            for citation in replayed.citations
            if "evidence" in citation["fact"]
            and citation["fact"]["evidence"]["kind"]
            == "first_publication_date"
        )
        self.assertEqual(
            [identifier.reference],
            rich["linkage"]["identifier_references"],
        )
        self.assertEqual(0, rich["linkage"]["identifier_facts"][0]["raw_order"])

        def rich_date(document: dict[str, object]) -> dict[str, object]:
            return next(
                citation["fact"]
                for citation in document["citations"]
                if "evidence" in citation["fact"]
                and citation["fact"]["evidence"]["kind"]
                == "first_publication_date"
            )

        document = json.loads(packet.to_json())
        del rich_date(document)["linkage"]
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        rich_date(document)["linkage"] = {"identifier_references": []}
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

        for field, invalid in (
            ("candidate", "edition-2"),
            ("provider", "other"),
        ):
            document = json.loads(packet.to_json())
            rich_date(document)[field] = invalid
            with self.subTest(field=field):
                with self.assertRaises(ShadowReviewValueError):
                    ShadowReviewPacket.from_json(canonical_json(document), report)

        document = json.loads(packet.to_json())
        rich_date(document)["evidence"]["kind"] = "copyright_date"
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(canonical_json(document), report)

    def test_publication_linkage_construction_rejects_context_over_limits(
        self,
    ) -> None:
        def packet_for(
            claims: tuple[ProviderClaim, ...],
            evidence: tuple[ProviderEvidence, ...],
        ) -> None:
            report = MetadataReport.create(
                target=TargetFacts(
                    "primary",
                    "ebooks",
                    "item-1",
                    default_target_fields(None),
                ),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(),
                local_reasons=("no_opening_claims",),
                providers=(
                    ProviderObservation(
                        "catalogue",
                        ProviderStatus.CANDIDATES,
                        (),
                        (
                            ProviderCandidate(
                                "edition-1",
                                "work-1",
                                claims,
                                evidence,
                            ),
                        ),
                    ),
                ),
            )
            proposal = evaluate_report(report)
            case = ReviewCase.create(proposal, report)
            ShadowReviewPacket.create(case, proposal, report)

        identifiers = tuple(
            ProviderClaim.create(
                provider="catalogue",
                candidate="edition-1",
                kind=ClaimKind.ASIN,
                value="B000000001",
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability="ebook",
                raw_order=order,
            )
            for order in range(MAX_LINKAGE_IDENTIFIER_FACTS + 1)
        )
        publication = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value="2024-03-05",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
            raw_order=len(identifiers),
        )
        packet_for((*identifiers[:-1], publication), ())
        with self.assertRaises(ShadowReviewValueError):
            packet_for((*identifiers, publication), ())

        formats = tuple(
            ProviderEvidence(
                ProviderEvidenceKind.PHYSICAL_FORMAT,
                f"format-{index:02d}",
                ClaimScope.EDITION,
            )
            for index in range(MAX_LINKAGE_FORMAT_FACTS + 1)
        )
        publication = ProviderClaim.create(
            provider="catalogue",
            candidate="edition-1",
            kind=ClaimKind.PUBLICATION_DATE,
            value="2024-03-05",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
        packet_for((publication,), formats[:-1])
        with self.assertRaises(ShadowReviewValueError):
            packet_for((publication,), formats)

        private_candidate = "/private/library/edition-1"
        private_publication = ProviderClaim.create(
            provider="catalogue",
            candidate=private_candidate,
            kind=ClaimKind.PUBLICATION_DATE,
            value="2024-03-05",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
        )
        report = MetadataReport.create(
            target=TargetFacts(
                "primary",
                "ebooks",
                "item-1",
                default_target_fields(None),
            ),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(
                ProviderObservation(
                    "catalogue",
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        ProviderCandidate(
                            private_candidate,
                            "work-1",
                            (private_publication,),
                        ),
                    ),
                ),
            ),
        )
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.create(case, proposal, report)

    def _repository(self, directory: str):
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        return repository, admitted["review_case_id"]

    def test_preview_is_sanitized_and_has_no_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            preview = json.loads(
                preview_shadow_review(repository, case_id, _identity())
            )
            serialized = canonical_json(preview["request"]["packet"])
            self.assertNotIn("primary", serialized)
            self.assertNotIn("ebooks", serialized)
            self.assertNotIn("item-1", serialized)
            self.assertEqual(
                (0,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_requests"
                ).fetchone(),
            )
            self.assertEqual("preview", preview["status"])
            repository.close()

    def test_request_persists_before_dispatch_and_replay_does_not_egress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            runner = _Runner()
            first = json.loads(
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    runner,
                    claimant=(9001, "11"),
                )
            )
            replay = json.loads(
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    runner,
                    claimant=(9001, "11"),
                )
            )
            self.assertEqual(1, runner.calls)
            self.assertEqual("qualification_only", first["status"])
            self.assertEqual(first, replay)
            self.assertEqual(
                (1, 1, 1, 1),
                (
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_requests"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_attempts"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_dispatches"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_outcomes"
                    ).fetchone()[0],
                ),
            )
            repository.close()

    def test_dispatched_failure_is_unknown_and_explicit_reevaluation_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            failing = _Runner(fail="timeout")
            with self.assertRaises(ShadowReviewerError) as raised:
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    failing,
                    claimant=(9001, "11"),
                )
            self.assertEqual("timeout", raised.exception.code)
            request_id = repository._connection.execute(
                "SELECT review_request_id FROM shadow_review_requests"
            ).fetchone()[0]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("unknown", inspected["status"])
            self.assertEqual(1, inspected["attempt_count"])
            self.assertEqual(
                ["timeout"],
                [
                    value["category"]
                    for value in inspected["diagnostics"]
                ],
            )
            projection = canonical_json(inspected)
            for forbidden in (
                "diagnostic_digest",
                "failure_code",
                "attempt_number",
                "claim_pid",
                "start_token",
                "packet",
                "prompt",
            ):
                self.assertNotIn(forbidden, projection)
            successful = _Runner()
            result = json.loads(
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    successful,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: False,
                )
            )
            self.assertEqual("qualification_only", result["status"])
            self.assertEqual(2, result["attempt_count"])
            repository.close()

    def test_live_schema_rejection_survives_restart_as_bounded_diagnostic(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository, case_id = self._repository(directory)
            with self.assertRaises(ShadowReviewerError) as raised:
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    _Runner(fail="response_format"),
                    claimant=(9001, "11"),
                )
            self.assertEqual("response_format", raised.exception.code)
            request_id = repository._connection.execute(
                "SELECT review_request_id FROM shadow_review_requests"
            ).fetchone()[0]
            repository.close()

            reopened = open_repository(database, create=False)
            inspected = reopened.inspect_shadow_request(
                request_id, _identity().to_json()
            )
            self.assertEqual(
                ["live_schema_rejection"],
                [
                    value["category"]
                    for value in json.loads(inspected)["diagnostics"]
                ],
            )
            for forbidden in (
                "response_format",
                "diagnostic_digest",
                "/tmp/private",
                "raw stderr",
                "raw stdout",
            ):
                self.assertNotIn(forbidden, inspected)
            reopened.close()

    def test_wrong_result_identity_persists_and_restarts_without_payload(
        self,
    ) -> None:
        class WrongIdentityRunner:
            def run(self, request, dispatched):
                dispatched(12345, 12345, "777")
                result = _result(request)
                wrong_id = "review_request_" + (
                    "f" if not request.review_request_id.endswith("f" * 64) else "e"
                ) * 64
                return ShadowReviewRunResult(
                    ShadowReviewResult(wrong_id, result.recommendations),
                    CitationNormalizationMarker(False, 0),
                )

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository, case_id = self._repository(directory)
            with self.assertRaises(ShadowReviewError) as raised:
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    WrongIdentityRunner(),
                    claimant=(9001, "11"),
                )
            self.assertEqual("identity", raised.exception.code)
            self.assertEqual("identity", raised.exception.diagnostic.category.value)
            self.assertEqual("reviewer", raised.exception.diagnostic.component.value)
            self.assertEqual("identity", raised.exception.diagnostic.stage.value)
            request_id = repository._connection.execute(
                "SELECT review_request_id FROM shadow_review_requests"
            ).fetchone()[0]
            repository.close()

            reopened = open_repository(database, create=False)
            inspected = reopened.inspect_shadow_request(
                request_id, _identity().to_json()
            )
            diagnostic = json.loads(inspected)["diagnostics"]
            self.assertEqual(
                [
                    {
                        "category": "identity",
                        "component": "reviewer",
                        "count": 1,
                        "current": None,
                        "retryable": None,
                        "stage": "identity",
                    }
                ],
                diagnostic,
            )
            for forbidden in (
                "review_request_ffffffff",
                "failure_code",
                "diagnostic_digest",
                "payload",
            ):
                self.assertNotIn(forbidden, inspected)
            reopened.close()

    def test_currentness_and_persistence_have_exact_application_producers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            scenarios = (
                (
                    ReviewAdmissionStaleError("/tmp/private currentness"),
                    "currentness",
                    "reviewer",
                    "currentness",
                ),
                (
                    MetadataReportError("/tmp/private persistence"),
                    "persistence",
                    "persistence",
                    "persistence",
                ),
            )
            for cause, category, component, stage in scenarios:
                with self.subTest(category=category), patch.object(
                    repository,
                    "prepare_shadow_request",
                    side_effect=cause,
                ), self.assertRaises(ShadowReviewError) as raised:
                    execute_shadow_review(
                        repository,
                        case_id,
                        _identity(),
                        _Runner(),
                        claimant=(9001, "11"),
                    )
                self.assertEqual(category, raised.exception.code)
                self.assertEqual(
                    (category, component, stage),
                    (
                        raised.exception.diagnostic.category.value,
                        raised.exception.diagnostic.component.value,
                        raised.exception.diagnostic.stage.value,
                    ),
                )
                self.assertNotIn("/tmp/private", str(raised.exception))
            repository.close()

    def test_completed_success_cannot_be_reevaluated_or_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            serialized = execute_shadow_review(
                repository,
                case_id,
                _identity(),
                _Runner(),
                claimant=(9001, "11"),
            )
            request_id = json.loads(serialized)["review_request_id"]
            claim = repository.last_shadow_claim(request_id)
            with self.assertRaises(MetadataReportError):
                repository.prepare_shadow_request(
                    request_id,
                    _identity().to_json(),
                    reevaluate=True,
                    claim_pid=9002,
                    claim_start_token="12",
                    expected_prior_attempt_id=claim[0],
                )
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: False,
                )
            self.assertEqual(0, runner.calls)
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_pre_dispatch_failure_is_terminal_and_cannot_be_reevaluated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_failure(
                prepared["attempt_id"],
                reviewer_failure_diagnostic("setup").to_json(),
            )
            request_id = prepared["request"]["review_request_id"]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("failed", inspected["status"])
            self.assertEqual(
                "setup_configuration",
                inspected["diagnostics"][0]["category"],
            )
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: False,
                )
            self.assertEqual(0, runner.calls)
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_dispatch_without_terminal_outcome_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_dispatch(
                prepared["attempt_id"], 12345, 12345, "777"
            )
            request_id = prepared["request"]["review_request_id"]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("unknown", inspected["status"])
            self.assertEqual(1, inspected["attempt_count"])
            self.assertEqual(
                [
                    {
                        "category": "unknown",
                        "component": "reviewer",
                        "count": 1,
                        "current": None,
                        "retryable": None,
                        "stage": "execution",
                    }
                ],
                inspected["diagnostics"],
            )
            repository.close()

            reopened = open_repository(database, create=False)
            restarted = inspect_shadow_review(
                reopened, request_id, _identity()
            )
            reopened.close()
            output, errors = io.StringIO(), io.StringIO()
            self.assertEqual(
                0,
                main(
                    [
                        "shadow-review-inspect",
                        "--database",
                        "/private/database",
                        "--review-request-id",
                        request_id,
                        "--qualification-profile",
                        "/private/profile",
                    ],
                    shadow_inspect_runner=lambda *_: restarted,
                    stdout=output,
                    stderr=errors,
                ),
            )
            self.assertEqual("", errors.getvalue())
            self.assertEqual(json.loads(restarted), json.loads(output.getvalue()))
            for forbidden in (
                "12345",
                "777",
                "attempt_id",
                "failure_document",
                "result_document",
                "/private",
            ):
                self.assertNotIn(forbidden, restarted)

    def test_pending_pre_dispatch_restart_can_be_explicitly_reevaluated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            request_id = prepared["request"]["review_request_id"]
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: True,
                )
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            result = json.loads(
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: False,
                )
            )
            self.assertEqual("qualification_only", result["status"])
            self.assertEqual(2, result["attempt_count"])
            repository.close()

    def test_concurrent_reevaluation_claim_has_one_cas_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_dispatch(
                prepared["attempt_id"], 12345, 12345, "777"
            )
            request_id = prepared["request"]["review_request_id"]
            claim = repository.last_shadow_claim(request_id)
            repository.close()
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def claim_reevaluation() -> None:
                barrier.wait()
                local = open_repository(database, create=False)
                try:
                    local.prepare_shadow_request(
                        request_id,
                        _identity().to_json(),
                        reevaluate=True,
                        claim_pid=9002,
                        claim_start_token="12",
                        expected_prior_attempt_id=claim[0],
                    )
                    outcomes.append("won")
                except Exception:
                    outcomes.append("lost")
                finally:
                    local.close()

            workers = [
                threading.Thread(target=claim_reevaluation)
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(["lost", "won"], sorted(outcomes))
            repository = open_repository(database, create=False)
            self.assertEqual(
                (2,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_result_rejects_missing_coverage_and_unknown_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            request = repository.validate_shadow_request(
                repository.preview_shadow_request(
                    case_id, _identity().to_json()
                )
            )
            with self.assertRaises(ShadowReviewValueError):
                ShadowReviewResult.from_json(
                    canonical_json(
                        {
                            "recommendations": [],
                            "review_request_id": request.review_request_id,
                            "schema_version": "shadow-review-result-v1",
                        }
                    ),
                    request,
                )
            malformed = _result(request).to_data()
            malformed["recommendations"][0]["citations"] = ["evidence_" + "f" * 64]
            with self.assertRaises(ShadowReviewValueError):
                ShadowReviewResult.from_json(canonical_json(malformed), request)
            repository.close()

    def test_result_identity_mismatch_is_typed_before_other_semantics(
        self,
    ) -> None:
        request = ShadowReviewRequest.synthetic_canary(_identity())
        wrong_identity = _result(request).to_data()
        wrong_id = "review_request_" + (
            "f" if not request.review_request_id.endswith("f" * 64) else "e"
        ) * 64
        wrong_identity["review_request_id"] = wrong_id
        with self.assertRaises(ShadowReviewIdentityError):
            ShadowReviewResult.from_json(
                canonical_json(wrong_identity),
                request,
            )
        wrong_identity["recommendations"][0]["citations"] = [
            "evidence_" + "f" * 64
        ]
        with self.assertRaises(ShadowReviewIdentityError):
            ShadowReviewResult.from_json(
                canonical_json(wrong_identity),
                request,
            )
        for malformed_id in ("review_request_private", None):
            with self.subTest(malformed_id=malformed_id):
                malformed = ShadowReviewResult(
                    malformed_id,  # type: ignore[arg-type]
                    _result(request).recommendations,
                )
                with self.assertRaises(ShadowReviewSemanticError) as raised:
                    malformed.validate_for(request)
                self.assertNotIsInstance(
                    raised.exception, ShadowReviewIdentityError
                )

        missing_coverage = _result(request).to_data()
        missing_coverage["recommendations"] = []
        with self.assertRaises(ShadowReviewSemanticError) as raised:
            ShadowReviewResult.from_json(
                canonical_json(missing_coverage),
                request,
            )
        self.assertNotIsInstance(raised.exception, ShadowReviewIdentityError)

    def test_maximum_packet_request_round_trips_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            base = repository.validate_shadow_request(
                repository.preview_shadow_request(case_id, _identity().to_json())
            ).packet

            def packet_with_padding(size: int) -> ShadowReviewPacket:
                data = json.loads(base.to_json())
                original = data["proposals"][0]["candidates"][0]
                data["proposals"][0]["candidates"] = [
                    {
                        **original,
                        "candidate_id": f"alternative_{index:064x}",
                    }
                    for index in range(512)
                ]
                data["citations"][0]["fact"]["request_bound_padding"] = "x" * size
                return ShadowReviewPacket(
                    data["review_case_id"],
                    tuple(data["proposals"]),
                    tuple(data["citations"]),
                    tuple(data["correlations"]),
                    data["schema_version"],
                )

            lower, upper = 0, MAX_PACKET_BYTES
            while lower < upper:
                candidate = (lower + upper + 1) // 2
                try:
                    packet_with_padding(candidate)
                except ShadowReviewValueError:
                    upper = candidate - 1
                else:
                    lower = candidate
            packet = packet_with_padding(lower)
            self.assertLessEqual(
                MAX_PACKET_BYTES - len(packet.to_json().encode("utf-8")), 1
            )
            request = ShadowReviewRequest.create_from_packet(_identity(), packet)
            self.assertGreater(
                len(request.to_json().encode("utf-8")),
                MAX_PACKET_BYTES + MAX_PROFILE_IDENTITY_BYTES,
            )
            self.assertLessEqual(
                len(request.to_json().encode("utf-8")), MAX_REQUEST_BYTES
            )
            self.assertEqual(
                request, repository.validate_shadow_request(request.to_json())
            )
            repository._connection.execute(
                "INSERT INTO shadow_review_requests("
                "review_request_id,review_case_id,profile_document,"
                "packet_document,request_document) VALUES(?,?,?,?,?)",
                (
                    request.review_request_id,
                    request.review_case_id,
                    request.profile.to_json(),
                    request.packet.to_json(),
                    request.to_json(),
                ),
            )
            repository._connection.commit()
            stored = repository._connection.execute(
                "SELECT request_document FROM shadow_review_requests "
                "WHERE review_request_id=?",
                (request.review_request_id,),
            ).fetchone()
            self.assertIsNotNone(stored)
            self.assertEqual(
                request, repository.validate_shadow_request(stored[0])
            )
            self.assertEqual(
                request,
                repository._validated_shadow_request(
                    request.review_request_id
                ),
            )
            schema = repository._connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='shadow_review_requests'"
            ).fetchone()[0]
            self.assertIn(str(MAX_REQUEST_BYTES), schema)
            repository.close()

    def test_profile_load_does_not_touch_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            raw = {
                "auth_store_path": str(root / "missing-auth"),
                "codex": {
                    "path": str(root / "missing-codex"),
                    "sha256": "a" * 64,
                    "version": "codex-cli 0.146.0-alpha.3.1",
                },
                "job_root_path": str(root / "missing-jobs"),
                "limits": {
                    "file_bytes": 131_072,
                    "memory_bytes": 536_870_912,
                    "output_bytes": 65_536,
                    "processes": 16,
                    "timeout_seconds": 120,
                },
                "mode": "qualification_only",
                "model": "gpt-5.6-sol",
                "output_version": "shadow-review-result-v1",
                "packet_version": "shadow-review-packet-v2",
                "policy_version": "qualification-only-policy-v1",
                "profile_version": "codex-shadow-profile-v1",
                    "prompt_version": "shadow-review-prompt-v2",
                "reasoning": "medium",
                "sandbox": {
                    "path": str(root / "missing-bwrap"),
                    "sha256": "b" * 64,
                    "version": "bubblewrap-v1",
                },
                "schema_version": "reviewer-qualification-profile-v1",
            }
            profile.write_text(canonical_json(raw), encoding="utf-8")
            os.chmod(profile, 0o600)
            loaded = load_qualification_profile(profile)
            self.assertEqual(_identity(), loaded.identity)

    def test_cli_routes_exact_shadow_ids_and_redacts_failures(self) -> None:
        case_id = "review_case_" + "a" * 64
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "reviewer-auth-status",
                    "--qualification-profile",
                    "/profile",
                ],
                reviewer_auth_runner=lambda *args: self.assertEqual(
                    (Path("/profile"), "status"), args
                )
                or canonical_json(
                    {
                        "mode": "qualification_only",
                        "schema_version": "reviewer-auth-v1",
                        "status": "chatgpt",
                    }
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", errors.getvalue())
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-preview",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_preview_runner=lambda *_: (_ for _ in ()).throw(
                    ShadowReviewerError("profile")
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(_failure_output("profile"), errors.getvalue())

        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-preview",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_preview_runner=lambda *_: (_ for _ in ()).throw(
                    ShadowReviewerError(
                        "response_format",
                        count=999,
                        digest=hashlib.sha256(
                            b"/tmp/private raw stderr raw stdout"
                        ).hexdigest(),
                    )
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        cli_failure = json.loads(errors.getvalue())
        self.assertEqual(
            "live_schema_rejection",
            cli_failure["diagnostic"]["category"],
        )
        self.assertEqual("schema", cli_failure["diagnostic"]["stage"])
        self.assertEqual(999, cli_failure["diagnostic"]["count"])
        for forbidden in ("/tmp/private", "raw stderr", "raw stdout"):
            self.assertNotIn(forbidden, errors.getvalue())

        output, errors = io.StringIO(), io.StringIO()
        private_identity = "review_request_" + "f" * 64
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-preview",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_preview_runner=lambda *_: (_ for _ in ()).throw(
                    ShadowReviewerError(
                        "identity",
                        count=1,
                        digest=hashlib.sha256(
                            private_identity.encode("utf-8")
                        ).hexdigest(),
                    )
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        cli_failure = json.loads(errors.getvalue())
        self.assertEqual(
            {
                "category": "identity",
                "component": "reviewer",
                "count": 1,
                "current": None,
                "retryable": None,
                "stage": "identity",
            },
            cli_failure["diagnostic"],
        )
        self.assertNotIn(private_identity, errors.getvalue())

    def test_cli_rejects_wrong_success_schema_and_exact_id(self) -> None:
        case_id = "review_case_" + "a" * 64
        wrong_case_id = "review_case_" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            repository, admitted_case_id = self._repository(directory)
            preview = preview_shadow_review(
                repository, admitted_case_id, _identity()
            )
            repository.close()
        for serialized in (
            preview.replace(admitted_case_id, wrong_case_id),
            canonical_json(
                {
                    "mode": "qualification_only",
                    "schema_version": "wrong-v1",
                    "status": "preview",
                }
            ),
        ):
            with self.subTest(serialized=serialized):
                output, errors = io.StringIO(), io.StringIO()
                self.assertEqual(
                    1,
                    main(
                        [
                            "shadow-review-preview",
                            "--database",
                            "/db",
                            "--review-case-id",
                            case_id,
                            "--qualification-profile",
                            "/profile",
                        ],
                        shadow_preview_runner=lambda *_args, value=serialized: value,
                        stdout=output,
                        stderr=errors,
                    ),
                )
                self.assertEqual("", output.getvalue())
                self.assertEqual(
                    _failure_output("malformed_output"), errors.getvalue()
                )

    def test_cli_validates_request_and_inspect_ids_against_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            serialized = execute_shadow_review(
                repository,
                case_id,
                _identity(),
                _Runner(),
                claimant=(9001, "11"),
            )
            request_id = json.loads(serialized)["review_request_id"]
            repository.close()
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "shadow-review-request",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_request_runner=lambda *_: serialized,
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", errors.getvalue())
        wrong_case = json.loads(serialized)
        wrong_case["review_case_id"] = "review_case_" + "f" * 64
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-request",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_request_runner=lambda *_: canonical_json(wrong_case),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(
            _failure_output("malformed_output"), errors.getvalue()
        )
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-inspect",
                    "--database",
                    "/db",
                    "--review-request-id",
                    "review_request_" + "f" * 64,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_inspect_runner=lambda *_: serialized,
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(
            _failure_output("malformed_output"), errors.getvalue()
        )
        self.assertTrue(request_id.startswith("review_request_"))

    def test_cli_maps_exact_shadow_repository_failures_to_persistence(
        self,
    ) -> None:
        request_id = "review_request_" + "a" * 64
        private = "/private/repository-detail"

        class BrokenRepository:
            def inspect_shadow_request(self, *_args):
                raise MetadataReportError(private)

            def close(self) -> None:
                return None

        for name, repository_effect in (
            ("open", MetadataReportError(private)),
            ("inspect", BrokenRepository()),
        ):
            output, errors = io.StringIO(), io.StringIO()
            open_patch = (
                patch.object(
                    composition,
                    "open_repository",
                    side_effect=repository_effect,
                )
                if isinstance(repository_effect, Exception)
                else patch.object(
                    composition,
                    "open_repository",
                    return_value=repository_effect,
                )
            )
            with self.subTest(boundary=name), patch.object(
                composition,
                "load_qualification_profile",
                return_value=SimpleNamespace(identity=_identity()),
            ), open_patch:
                self.assertEqual(
                    1,
                    main(
                        [
                            "shadow-review-inspect",
                            "--database",
                            "/private/database",
                            "--review-request-id",
                            request_id,
                            "--qualification-profile",
                            "/private/profile",
                        ],
                        shadow_inspect_runner=(
                            composition.inspect_shadow_review_request
                        ),
                        stdout=output,
                        stderr=errors,
                    ),
                )
            self.assertEqual("", output.getvalue())
            self.assertEqual(
                _failure_output("persistence"), errors.getvalue()
            )
            self.assertNotIn(private, errors.getvalue())

        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-inspect",
                    "--database",
                    "/private/database",
                    "--review-request-id",
                    request_id,
                    "--qualification-profile",
                    "/private/profile",
                ],
                shadow_inspect_runner=lambda *_: (_ for _ in ()).throw(
                    RuntimeError(private)
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(_failure_output("unknown"), errors.getvalue())
        self.assertNotIn(private, errors.getvalue())

    def test_cli_rejects_zero_exit_private_and_diagnostic_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            serialized = execute_shadow_review(
                repository,
                case_id,
                _identity(),
                _Runner(),
                claimant=(9001, "11"),
            )
            repository.close()
        invalid_request = json.loads(serialized)
        invalid_request["request"] = {"private": "packet"}
        invalid_result = json.loads(serialized)
        invalid_result["diagnostics"] = [{"category": "unknown"}]
        timeout = reviewer_failure_diagnostic("timeout").to_data()
        unknown = reviewer_failure_diagnostic("unknown").to_data()
        duplicate_diagnostics = json.loads(serialized)
        duplicate_diagnostics["diagnostics"] = [timeout, timeout]
        unaggregated_diagnostics = json.loads(serialized)
        second_timeout = dict(timeout)
        second_timeout["count"] = 2
        unaggregated_diagnostics["diagnostics"] = [timeout, second_timeout]
        out_of_order_diagnostics = json.loads(serialized)
        out_of_order_diagnostics["diagnostics"] = [unknown, timeout]
        for name, document in (
            ("request", invalid_request),
            ("result", invalid_result),
            ("duplicate_diagnostics", duplicate_diagnostics),
            ("unaggregated_diagnostics", unaggregated_diagnostics),
            ("out_of_order_diagnostics", out_of_order_diagnostics),
        ):
            output, errors = io.StringIO(), io.StringIO()
            with self.subTest(document=name):
                self.assertEqual(
                    1,
                    main(
                        [
                            "shadow-review-request",
                            "--database",
                            "/db",
                            "--review-case-id",
                            case_id,
                            "--qualification-profile",
                            "/profile",
                        ],
                        shadow_request_runner=lambda *_args, value=canonical_json(
                            document
                        ): value,
                        stdout=output,
                        stderr=errors,
                    ),
                )
                self.assertEqual("", output.getvalue())
                self.assertEqual(
                    _failure_output("malformed_output"), errors.getvalue()
                )


class CodexShadowAdapterTests(unittest.TestCase):
    def _profile(
        self, root: Path, codex: Path, sandbox: Path
    ) -> QualificationProfile:
        auth, jobs = root / "auth", root / "jobs"
        auth.mkdir()
        jobs.mkdir()
        os.chmod(auth, 0o700)
        os.chmod(jobs, 0o700)
        identity = ReviewerProfileIdentity(
            hashlib.sha256(codex.read_bytes()).hexdigest(),
            "codex-cli 0.146.0-alpha.3.1",
            hashlib.sha256(sandbox.read_bytes()).hexdigest(),
            "bubblewrap-v1",
            "gpt-5.6-sol",
            "medium",
            "shadow-review-prompt-v2",
            "codex-shadow-profile-v1",
            "shadow-review-packet-v2",
            "shadow-review-result-v1",
            "qualification-only-policy-v1",
            120,
            65_536,
            536_870_912,
            16,
            131_072,
        )
        return QualificationProfile(codex, sandbox, auth, jobs, identity)

    def test_bound_artifact_survives_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "codex"
            path.write_bytes(b"original")
            os.chmod(path, 0o700)
            digest = hashlib.sha256(b"original").hexdigest()
            with codex_shadow._bound_artifact(path, digest) as bound:
                replacement = Path(directory) / "replacement"
                replacement.write_bytes(b"replacement")
                os.chmod(replacement, 0o700)
                os.replace(replacement, path)
                os.lseek(bound.descriptor, 0, os.SEEK_SET)
                self.assertEqual(b"original", os.read(bound.descriptor, 64))
            self.assertEqual(b"replacement", path.read_bytes())

    def test_auth_commands_mask_store_config_and_pin_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            version = subprocess.CompletedProcess(
                [], 0, b"codex-cli 0.146.0-alpha.3.1\n", b""
            )
            chatgpt = subprocess.CompletedProcess(
                [], 0, b"Logged in using ChatGPT\n", b""
            )
            success = subprocess.CompletedProcess([], 0, b"", b"")
            scenarios = {
                "start": ([version, success], ("login", "--device-auth")),
                "status": ([version, chatgpt], ("login", "status")),
                "logout": (
                    [version, chatgpt, success],
                    ("logout",),
                ),
            }
            for action, (responses, suffix) in scenarios.items():
                with self.subTest(action=action), patch.object(
                    codex_shadow.subprocess,
                    "run",
                    side_effect=responses,
                ) as run:
                    CodexShadowReviewer(profile).auth(action)
                    invoked = [
                        call.args[0] for call in run.call_args_list[1:]
                    ]
                    self.assertEqual(tuple(invoked[-1][-len(suffix) :]), suffix)
                    self.assertEqual(
                        1,
                        len(
                            {
                                call.kwargs["pass_fds"][2]
                                for call in run.call_args_list[1:]
                            }
                        ),
                    )
                    for index, command in enumerate(invoked, start=1):
                        self.assertNotIn("--ignore-user-config", command)
                        sandbox_descriptor = run.call_args_list[index].kwargs[
                            "pass_fds"
                        ][1]
                        self.assertEqual(
                            str(sandbox_descriptor), command[0].rsplit("/", 1)[1]
                        )
                        self.assertEqual(
                            (
                                "--perms",
                                "0400",
                                "--ro-bind-data",
                                str(run.call_args_list[index].kwargs["pass_fds"][2]),
                                "/auth/config.toml",
                            ),
                            tuple(
                                command[
                                    command.index("--perms", command.index("--bind"))
                                    : command.index("--perms", command.index("--bind")) + 5
                                ]
                            ),
                        )
                        self.assertNotIn("/dev/null", command)
                        self.assertEqual(
                            "/auth",
                            command[command.index("CODEX_HOME") + 1],
                        )
                        overrides = {
                            command[index + 1]
                            for index, value in enumerate(command[:-1])
                            if value == "-c"
                        }
                        self.assertEqual(
                            set(codex_shadow._auth_config()), overrides
                        )
                    self.assertEqual(1, len(run.call_args_list[0].kwargs["pass_fds"]))
                    for call in run.call_args_list[1:]:
                        self.assertEqual(3, len(call.kwargs["pass_fds"]))

    def test_empty_auth_config_descriptor_is_eof_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(self._profile(root, codex, sandbox))
            with reviewer._empty_auth_config() as descriptor:
                self.assertEqual(b"", os.read(descriptor, 1))
                os.fstat(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_chatgpt_auth_status_requires_one_exact_canonical_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(self._profile(root, codex, sandbox))
            canonical = b"Logged in using ChatGPT"
            accepted = [
                subprocess.CompletedProcess([], 0, value, b"")
                for value in (canonical, canonical + b"\n", canonical + b"\r\n")
            ] + [
                subprocess.CompletedProcess([], 0, b"", value)
                for value in (canonical, canonical + b"\n", canonical + b"\r\n")
            ]
            rejected = [
                subprocess.CompletedProcess([], 0, canonical, canonical),
                subprocess.CompletedProcess([], 0, canonical, b"extra"),
                subprocess.CompletedProcess([], 0, canonical + b"\n\n", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess(
                    [], 0, b"Logged in using an API key\n", b""
                ),
                subprocess.CompletedProcess([], 0, b"prefix " + canonical, b""),
                subprocess.CompletedProcess([], 1, canonical, b""),
            ]
            for completed in accepted:
                with self.subTest(
                    accepted=True,
                    stdout=bool(completed.stdout),
                    ending=completed.stdout[-2:] or completed.stderr[-2:],
                ), patch.object(reviewer, "_run_auth", return_value=completed):
                    reviewer._require_chatgpt_auth([], object(), object(), 0)
            for completed in rejected:
                with self.subTest(
                    accepted=False,
                    returncode=completed.returncode,
                    stdout=bool(completed.stdout),
                    stderr=bool(completed.stderr),
                ), patch.object(reviewer, "_run_auth", return_value=completed):
                    with self.assertRaisesRegex(ShadowReviewerError, "auth"):
                        reviewer._require_chatgpt_auth([], object(), object(), 0)

    def test_sandbox_command_has_exact_tool_disable_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            command = CodexShadowReviewer(profile)._sandbox_command(
                root,
                root / "result.json",
                "/proc/self/fd/10",
                "/proc/self/fd/11",
            )
            overrides = {
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "-c"
            }
            self.assertEqual(
                set(codex_shadow._exact_config("medium")), overrides
            )
            for required in (
                "features.plugins=false",
                "features.browser_use=false",
                "features.computer_use=false",
                "features.image_generation=false",
                "features.skill_search=false",
                "features.tool_call_mcp_elicitation=false",
            ):
                self.assertIn(required, overrides)
            self.assertEqual("/proc/self/fd/11", command[0])
            bind = command.index("/runtime/codex")
            self.assertEqual("/proc/self/fd/10", command[bind - 1])
            self.assertIn("--skip-git-repo-check", command)
            self.assertIn("--json", command)

    def test_production_runner_applies_profile_resource_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            with patch.object(codex_shadow.resource, "setrlimit") as setrlimit:
                CodexShadowReviewer(profile)._limit_child()
            self.assertEqual(
                [
                    (
                        codex_shadow.resource.RLIMIT_AS,
                        (
                            profile.identity.memory_bytes,
                            profile.identity.memory_bytes,
                        ),
                    ),
                    (
                        codex_shadow.resource.RLIMIT_FSIZE,
                        (profile.identity.file_bytes, profile.identity.file_bytes),
                    ),
                    (
                        codex_shadow.resource.RLIMIT_NPROC,
                        (
                            profile.identity.process_limit,
                            profile.identity.process_limit,
                        ),
                    ),
                    (codex_shadow.resource.RLIMIT_NOFILE, (64, 64)),
                ],
                [call.args for call in setrlimit.call_args_list],
            )

    def test_production_run_parses_result_and_cleans_its_child_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            reviewer = CodexShadowReviewer(profile)
            request = _request_with_citations(identity=profile.identity)
            citation_ids = [
                citation["citation_id"]
                for citation in request.packet.citations
            ]
            expected_data = _result(request).to_data()
            expected_data["recommendations"][0]["citations"] = citation_ids
            expected = ShadowReviewResult.from_json(
                canonical_json(expected_data), request
            )
            untrusted = json.loads(expected.to_json())
            untrusted["recommendations"][0]["citations"] = [
                citation_ids[1],
                citation_ids[0],
                citation_ids[0],
            ]
            dispatches: list[tuple[int, int, str]] = []

            def run_gated(*args) -> int:
                dispatched = args[-1]
                dispatched(1234, 1234, "5678")
                jobs = list(profile.job_root_path.glob("shadow-review-*"))
                self.assertEqual(1, len(jobs))
                (jobs[0] / "result.json").write_bytes(
                    canonical_json(untrusted).encode("utf-8")
                )
                return 0

            with (
                patch.object(codex_shadow.sys, "platform", "linux"),
                patch.object(reviewer, "_verify_codex"),
                patch.object(reviewer, "_require_chatgpt_auth"),
                patch.object(reviewer, "_run_gated", side_effect=run_gated),
            ):
                result = reviewer.run(
                    request,
                    lambda pid, pgid, token: dispatches.append(
                        (pid, pgid, token)
                    ),
                )
            self.assertEqual(expected, result.result)
            self.assertEqual(
                CitationNormalizationMarker(True, 1),
                result.citation_normalization,
            )
            self.assertEqual([(1234, 1234, "5678")], dispatches)
            self.assertFalse(
                list(profile.job_root_path.glob("shadow-review-*"))
            )

    def test_nonzero_execution_statuses_are_runtime_with_bounded_diagnostics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            request = ShadowReviewRequest.synthetic_canary(profile.identity)
            stdout, stderr = b"synthetic-out", b"synthetic-err"
            diagnostic = stdout + stderr

            for returncode in (126, 128 + signal.SIGKILL, 1):
                reviewer = CodexShadowReviewer(profile)

                def run_gated(*args, code=returncode) -> int:
                    os.write(args[4], stdout)
                    os.write(args[5], stderr)
                    args[6](1234, 1234, "5678")
                    return code

                with (
                    self.subTest(returncode=returncode),
                    patch.object(codex_shadow.sys, "platform", "linux"),
                    patch.object(reviewer, "_verify_codex"),
                    patch.object(reviewer, "_require_chatgpt_auth"),
                    patch.object(
                        reviewer, "_run_gated", side_effect=run_gated
                    ),
                    self.assertRaises(ShadowReviewerError) as raised,
                ):
                    reviewer.run(request, lambda *_: None)
                self.assertEqual("runtime", raised.exception.code)
                self.assertEqual("runtime", str(raised.exception))
                self.assertEqual(len(diagnostic), raised.exception.count)
                self.assertEqual(
                    hashlib.sha256(diagnostic).hexdigest(),
                    raised.exception.digest,
                )
                self.assertNotIn("synthetic", str(raised.exception))
                self.assertFalse(
                    list(profile.job_root_path.glob("shadow-review-*"))
                )

    def test_pinned_jsonl_live_schema_rejection_has_distinct_failure_code(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            request = ShadowReviewRequest.synthetic_canary(profile.identity)
            private_detail = "/tmp/private schema body"
            message = (
                "Invalid schema for response_format 'codex_output_schema': "
                + private_detail
            )
            jsonl = (
                canonical_json({"thread_id": "thread_1", "type": "thread.started"})
                + "\n"
                + canonical_json(
                    {
                        "error": {"message": message},
                        "type": "turn.failed",
                    }
                )
                + "\n"
            ).encode()
            reviewer = CodexShadowReviewer(profile)

            def run_gated(*args) -> int:
                os.write(args[4], jsonl)
                args[6](1234, 1234, "5678")
                return 1

            with (
                patch.object(codex_shadow.sys, "platform", "linux"),
                patch.object(reviewer, "_verify_codex"),
                patch.object(reviewer, "_require_chatgpt_auth"),
                patch.object(reviewer, "_run_gated", side_effect=run_gated),
                self.assertRaises(ShadowReviewerError) as raised,
            ):
                reviewer.run(request, lambda *_: None)
            self.assertEqual("response_format", raised.exception.code)
            self.assertEqual(len(jsonl), raised.exception.count)
            self.assertEqual(
                hashlib.sha256(jsonl).hexdigest(), raised.exception.digest
            )
            self.assertNotIn(private_detail, str(raised.exception))

    def test_schema_rejection_signal_variance_fails_closed_to_runtime(
        self,
    ) -> None:
        prefix = (
            b"Invalid schema for response_format 'codex_output_schema': private"
        )
        variants = (
            canonical_json(
                {
                    "error": {"message": "ordinary runtime failure"},
                    "type": "turn.failed",
                }
            ).encode()
            + b"\n",
            canonical_json(
                {
                    "message": prefix.decode(),
                    "type": "error",
                }
            ).encode()
            + b"\n",
            canonical_json(
                {
                    "error": {"message": prefix.decode(), "raw": "private"},
                    "type": "turn.failed",
                }
            ).encode()
            + b"\n",
            b'{"error":{"message":"' + prefix + b'"},"type":"turn.failed"}',
            b'{"error":{"message":"'
            + prefix
            + b'"},"type":"turn.failed","type":"turn.failed"}\n',
            b"not-json\n"
            + canonical_json(
                {
                    "error": {"message": prefix.decode()},
                    "type": "turn.failed",
                }
            ).encode()
            + b"\n",
            b"\xff\n",
        )
        for payload in variants:
            with self.subTest(payload=payload[:40]), tempfile.TemporaryFile(
                "w+b"
            ) as stdout_file, tempfile.TemporaryFile("w+b") as stderr_file:
                stdout_file.write(payload)
                code, count, digest = codex_shadow._nonzero_failure(
                    stdout_file, stderr_file, returncode=1
                )
                self.assertEqual("runtime", code)
                self.assertEqual(len(payload), count)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        canonical_signal = (
            canonical_json(
                {
                    "error": {"message": prefix.decode()},
                    "type": "turn.failed",
                }
            ).encode()
            + b"\n"
        )
        private_stderr = b"/tmp/private raw stderr"
        with tempfile.TemporaryFile("w+b") as stdout_file, tempfile.TemporaryFile(
            "w+b"
        ) as stderr_file:
            stdout_file.write(canonical_signal)
            stderr_file.write(private_stderr)
            code, count, digest = codex_shadow._nonzero_failure(
                stdout_file, stderr_file, returncode=1
            )
            self.assertEqual("runtime", code)
            self.assertEqual(len(canonical_signal) + len(private_stderr), count)
            self.assertEqual(
                hashlib.sha256(canonical_signal + private_stderr).hexdigest(),
                digest,
            )

    def test_output_schema_is_typed_bound_and_digest_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = ShadowReviewTests()._repository(directory)
            request = repository.validate_shadow_request(
                repository.preview_shadow_request(case_id, _identity().to_json())
            )
            binding = codex_shadow._schema_binding(request)
            schema = json.loads(binding.serialized)
            self.assertEqual(
                hashlib.sha256(binding.serialized).hexdigest(), binding.digest
            )
            properties = schema["properties"]
            self.assertEqual("string", properties["review_request_id"]["type"])
            item = properties["recommendations"]["items"]["properties"]
            self.assertEqual("string", item["proposal_id"]["type"])
            self.assertEqual("string", item["citations"]["items"]["type"])
            self.assertNotIn("uniqueItems", item["citations"])
            repository.close()

    def test_output_schema_rejects_typeless_or_contradictory_bindings(self) -> None:
        with self.assertRaisesRegex(ShadowReviewerError, "schema"):
            codex_shadow._validate_output_schema({"enum": ["bound"]})
        with self.assertRaisesRegex(ShadowReviewerError, "schema"):
            codex_shadow._validate_output_schema(
                {"const": "bound", "enum": ["other"], "type": "string"}
            )
        with self.assertRaisesRegex(ShadowReviewerError, "schema"):
            codex_shadow._validate_output_schema(
                {"const": "bound", "enum": ["bound", 42], "type": "string"}
            )
        invalid = (
            {
                "$schema": "wrong",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "type": "object",
            },
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "items": {"type": "string"},
                "maxLength": 1,
                "type": "array",
            },
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["other"],
                "type": "object",
            },
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "additionalProperties": True,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "type": "object",
            },
        )
        for schema in invalid:
            with self.subTest(schema=schema), self.assertRaisesRegex(
                ShadowReviewerError, "schema"
            ):
                codex_shadow._validate_output_schema(schema)

    def test_output_documents_partition_malformed_and_semantic_failures(self) -> None:
        request = ShadowReviewRequest.synthetic_canary(_identity())
        valid = _result(request).to_json().encode("utf-8")
        malformed = (
            b"\xff",
            b"{",
            valid.replace(
                b'{"recommendations":',
                b'{"recommendations":[],"recommendations":',
                1,
            ),
            b" " + valid,
        )
        for serialized in malformed:
            with self.subTest(serialized=serialized[:20]):
                with self.assertRaises(ShadowReviewerError) as raised:
                    codex_shadow._validated_result(
                        serialized,
                        request,
                        maximum=MAX_REQUEST_BYTES,
                    )
                self.assertEqual("malformed_output", raised.exception.code)
                self.assertEqual(len(serialized), raised.exception.count)
                self.assertEqual(
                    hashlib.sha256(serialized).hexdigest(),
                    raised.exception.digest,
                )
        semantic = json.loads(valid)
        semantic["recommendations"] = []
        semantic_bytes = canonical_json(semantic).encode("utf-8")
        with self.assertRaises(ShadowReviewerError) as raised:
            codex_shadow._validated_result(
                semantic_bytes,
                request,
                maximum=MAX_REQUEST_BYTES,
            )
        self.assertEqual("semantic_validation", raised.exception.code)
        self.assertEqual(len(semantic_bytes), raised.exception.count)
        self.assertEqual(
            hashlib.sha256(semantic_bytes).hexdigest(),
            raised.exception.digest,
        )
        wrong_identity = json.loads(valid)
        wrong_identity["review_request_id"] = "review_request_" + "f" * 64
        identity_bytes = canonical_json(wrong_identity).encode("utf-8")
        with self.assertRaises(ShadowReviewerError) as identity_raised:
            codex_shadow._validated_result(
                identity_bytes,
                request,
                maximum=MAX_REQUEST_BYTES,
            )
        self.assertEqual("identity", identity_raised.exception.code)
        self.assertEqual(len(identity_bytes), identity_raised.exception.count)
        self.assertEqual(
            hashlib.sha256(identity_bytes).hexdigest(),
            identity_raised.exception.digest,
        )
        self.assertNotIn(
            wrong_identity["review_request_id"],
            str(identity_raised.exception),
        )
        with self.assertRaises(ShadowReviewDocumentError):
            ShadowReviewResult.from_json("{", request)
        with self.assertRaises(ShadowReviewSemanticError):
            ShadowReviewResult.from_json(
                canonical_json(semantic), request
            )

    def test_exact_wrong_identity_precedes_citation_normalization_only_after_shape(
        self,
    ) -> None:
        request = _request_with_citations()
        other_request = _request_with_citations(seed="b")
        wrong_id = "review_request_" + (
            "f" if not request.review_request_id.endswith("f" * 64) else "e"
        ) * 64
        foreign_citation = other_request.packet.citations[0]["citation_id"]
        base = _result(request).to_data()
        identity_cases = {
            "valid_citations": base,
            "foreign_citation": {
                **base,
                "recommendations": [
                    {
                        **base["recommendations"][0],
                        "citations": [foreign_citation],
                    }
                ],
            },
            "malformed_recommendations": {
                **base,
                "recommendations": "private malformed recommendations",
            },
        }
        for name, value in identity_cases.items():
            with self.subTest(identity=name):
                raw = json.loads(canonical_json(value))
                raw["review_request_id"] = wrong_id
                serialized = canonical_json(raw).encode("utf-8")
                with self.assertRaises(ShadowReviewerError) as raised:
                    codex_shadow._validated_result(
                        serialized,
                        request,
                        maximum=MAX_REQUEST_BYTES,
                    )
                self.assertEqual("identity", raised.exception.code)
                self.assertEqual(len(serialized), raised.exception.count)
                self.assertEqual(
                    hashlib.sha256(serialized).hexdigest(),
                    raised.exception.digest,
                )
                self.assertNotIn(wrong_id, str(raised.exception))
                self.assertNotIn("private", str(raised.exception))

        semantic_cases = {
            "missing_identity": {
                key: value
                for key, value in base.items()
                if key != "review_request_id"
            },
            "malformed_identity": {
                **base,
                "review_request_id": "review_request_private",
            },
            "matching_identity_foreign_citation": {
                **base,
                "recommendations": [
                    {
                        **base["recommendations"][0],
                        "citations": [foreign_citation],
                    }
                ],
            },
        }
        for name, raw in semantic_cases.items():
            with self.subTest(semantic=name):
                serialized = canonical_json(raw).encode("utf-8")
                with self.assertRaises(ShadowReviewerError) as raised:
                    codex_shadow._validated_result(
                        serialized,
                        request,
                        maximum=MAX_REQUEST_BYTES,
                    )
                self.assertEqual(
                    "semantic_validation",
                    raised.exception.code,
                )
        with self.assertRaises(ShadowReviewerError) as malformed:
            codex_shadow._validated_result(
                b'{"review_request_id":',
                request,
                maximum=MAX_REQUEST_BYTES,
            )
        self.assertEqual("malformed_output", malformed.exception.code)

    def test_untrusted_citations_normalize_before_domain_construction(self) -> None:
        request = _request_with_citations()
        citation_ids = [
            citation["citation_id"] for citation in request.packet.citations
        ]
        ordered = _result(request).to_data()
        ordered["recommendations"][0]["citations"] = citation_ids
        reordered = json.loads(canonical_json(ordered))
        reordered["recommendations"][0]["citations"] = [
            citation_ids[1],
            citation_ids[0],
        ]
        duplicated = json.loads(canonical_json(ordered))
        duplicated["recommendations"][0]["citations"] = [
            citation_ids[1],
            citation_ids[0],
            citation_ids[0],
        ]

        canonical = codex_shadow._validated_result(
            canonical_json(ordered).encode("utf-8"),
            request,
            maximum=MAX_REQUEST_BYTES,
        )
        reordered_result = codex_shadow._validated_result(
            canonical_json(reordered).encode("utf-8"),
            request,
            maximum=MAX_REQUEST_BYTES,
        )
        duplicated_result = codex_shadow._validated_result(
            canonical_json(duplicated).encode("utf-8"),
            request,
            maximum=MAX_REQUEST_BYTES,
        )

        self.assertEqual(
            CitationNormalizationMarker(False, 0),
            canonical.citation_normalization,
        )
        self.assertEqual(
            CitationNormalizationMarker(True, 0),
            reordered_result.citation_normalization,
        )
        self.assertEqual(
            CitationNormalizationMarker(True, 1),
            duplicated_result.citation_normalization,
        )
        self.assertEqual(canonical.result, reordered_result.result)
        self.assertEqual(canonical.result, duplicated_result.result)
        self.assertEqual(
            canonical.result.to_json(), reordered_result.result.to_json()
        )
        self.assertEqual(
            hashlib.sha256(
                canonical.result.to_json().encode("utf-8")
            ).hexdigest(),
            hashlib.sha256(
                duplicated_result.result.to_json().encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            canonical.result,
            ShadowReviewResult.from_json(canonical.result.to_json(), request),
        )
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewResult.from_json(canonical_json(reordered), request)
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewResult.from_json(canonical_json(duplicated), request)

    def test_untrusted_citation_failures_retain_exact_raw_diagnostics(self) -> None:
        request = _request_with_citations()
        other_request = _request_with_citations(seed="b")
        known = request.packet.citations[0]["citation_id"]
        unknown = other_request.packet.citations[0]["citation_id"]
        base = _result(request).to_data()
        cases: dict[str, object] = {
            "unknown": [unknown],
            "mixed": [known, unknown],
            "missing": None,
            "malformed": [known, 1],
            "over_bound": [known] * 257,
        }
        for name, citations in cases.items():
            with self.subTest(name=name):
                raw = json.loads(canonical_json(base))
                if citations is None:
                    del raw["recommendations"][0]["citations"]
                else:
                    raw["recommendations"][0]["citations"] = citations
                serialized = canonical_json(raw).encode("utf-8")
                with self.assertRaises(ShadowReviewerError) as raised:
                    codex_shadow._validated_result(
                        serialized,
                        request,
                        maximum=MAX_REQUEST_BYTES,
                    )
                self.assertEqual("semantic_validation", raised.exception.code)
                self.assertEqual(len(serialized), raised.exception.count)
                self.assertEqual(
                    hashlib.sha256(serialized).hexdigest(),
                    raised.exception.digest,
                )

    def test_start_gate_does_not_exec_when_dispatch_persistence_fails(self) -> None:
        shell_source = Path(os.path.realpath("/bin/sh")).read_bytes()
        digest = hashlib.sha256(shell_source).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shell = root / "shell"
            shell.write_bytes(shell_source)
            os.chmod(shell, 0o700)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(
                self._profile(root, codex, sandbox)
            )
            marker = root / "executed"
            with (
                codex_shadow._bound_artifact(shell, digest) as bound_codex,
                codex_shadow._bound_artifact(shell, digest) as bound_sandbox,
                (root / "stdin").open("w+b") as stdin,
                (root / "stdout").open("w+b") as stdout,
                (root / "stderr").open("w+b") as stderr,
                patch.object(
                    codex_shadow, "_process_start_token", return_value="123"
                ),
                patch.object(
                    codex_shadow, "process_identity_alive", return_value=False
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "dispatch"):
                    reviewer._run_gated(
                        [
                            bound_sandbox.invocation_path,
                            "-c",
                            f"printf ran > '{marker}'",
                        ],
                        bound_codex,
                        bound_sandbox,
                        stdin.fileno(),
                        stdout.fileno(),
                        stderr.fileno(),
                        lambda *_: (_ for _ in ()).throw(
                            RuntimeError("dispatch")
                        ),
                    )
            self.assertFalse(marker.exists())

    def test_proc_stat_parser_uses_final_comm_delimiter_and_starttime(self) -> None:
        tail = [
            "S",
            "1",
            "4321",
            "1",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "987654",
        ]
        raw = "123 (worker name with ) spaces) " + " ".join(tail)
        self.assertEqual(("987654", 4321), codex_shadow._proc_identity(raw))


if __name__ == "__main__":
    unittest.main()
