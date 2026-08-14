from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers import hardcover_metadata
from mechanical_librarian.application.configuration import MediaMountConfiguration, ReportLimits
from mechanical_librarian.application.metadata_report import (
    ProviderBinding, ProviderLaneResult, TargetFile, TargetSnapshot, metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry, GenerateProviderRetrySuccessor,
    ProviderRetrySharedFailure, ProviderRetrySuccessorError,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderAttemptRecord
from mechanical_librarian.domain.metadata_report import (
    ClaimKind, ClaimScope, FileFacts, LocalClaim, MetadataReport, ProviderCandidate, ProviderQueryKind, ProviderRequestAccounting, ProviderStatus,
    TargetDestination, TargetFacts, TargetFieldState,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory, FailureComponent, FailureDiagnostic, FailureStage,
)


class _Target:
    def __init__(self, snapshot): self.snapshot = snapshot
    def read(self, _item): return self.snapshot


class _Inspector:
    def __init__(self, current=True): self.current = current
    def unchanged(self, *_args): return self.current


class _Repository:
    def __init__(self, report):
        self.values = {report.report_id: report.to_json()}; self.current_id = report.report_id; self.package = (None, report.report_id, None, None, "report_only")
    def get(self, report_id): return self.values[report_id]
    def current_optional(self, *_args): return self.values[self.current_id]
    def current_review_package(self, *_args): return self.package
    def current_review_package_report(self, *_args): return self.package[1]
    def publish(self, report, currentness, expected_current=None, after_publication=None, *, expected_package=None):
        if expected_current != self.current_id or expected_package != self.package or not currentness(): raise ProviderRetrySuccessorError("stale")
        self.values[report.report_id] = report.to_json(); self.current_id = report.report_id
        if after_publication is not None: after_publication(self, report)
        return report.to_json()
    def close(self): pass


class _Provider:
    def __init__(self, observation, attempt_records=()): self.observation = observation; self.plans = []; self.deadlines = []; self.attempt_records = attempt_records
    def observe(self, plan, *, deadline=None): self.plans.append(plan); self.deadlines.append(deadline); return self.observation


class ProviderRetrySuccessorTests(unittest.TestCase):
    def setUp(self):
        first = LocalClaim.create(kind=ClaimKind.ISBN, observed="9780306406157", canonical="9780306406157", excerpt="9780306406157", applicability="ebook", checksum="valid_isbn_13", applicability_basis="ebook")
        second = LocalClaim.create(kind=ClaimKind.ISBN, observed="9780061120084", canonical="9780061120084", excerpt="9780061120084", applicability="ebook", checksum="valid_isbn_13", applicability_basis="ebook")
        self.claims = (first, second)
        self.snapshot = TargetSnapshot("ebook", None, (TargetFile("b" * 64, "book.epub", "epub"),))
        self.plan = metadata_discovery_plan(self.snapshot, self.claims)
        self.failed, self.success = self.plan.lanes
        original = provider_observation("catalogue", (
            ProviderLaneResult(self.failed, ProviderStatus.UNAVAILABLE),
            ProviderLaneResult(self.success, ProviderStatus.CANDIDATES, (ProviderCandidate("old", "work", ()),)),
        ), authority="open_library")
        self.report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item", self.snapshot.fields), file=FileFacts(0, "b" * 64, "c" * 64), local_claims=self.claims, local_reasons=("opening_matter_isbn",), providers=(original,))
        self.repository = _Repository(self.report)

    def service(self, configured, *, inspector=None, after_publication=lambda *_args: None, resolver=None):
        return GenerateProviderRetrySuccessor(
            target=_Target(self.snapshot), inspector=_Inspector() if inspector is None else inspector,
            repository_factory=lambda: self.repository,
            provider_resolver=(lambda *_args: configured) if resolver is None else resolver,
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(), after_publication=after_publication,
        )

    def test_retries_only_selected_failed_lane_and_preserves_sibling(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),), authority="open_library")
        provider = _Provider(replacement)
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        successor = MetadataReport.from_json(service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2))
        self.assertEqual((self.failed.identity,), tuple(lane.identity for lane in provider.plans[0].lanes))
        before = self.report.providers[0].lanes[1].to_data()
        after = next(lane for lane in successor.providers[0].lanes if lane.identity == self.success.identity).to_data()
        self.assertEqual(before, after)
        self.assertNotEqual(self.report.report_id, successor.report_id)
        self.assertEqual(self.report.report_id, self.repository.package[1])

    def test_none_returning_staging_receipt_succeeds(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),), authority="open_library")
        provider = _Provider(replacement)
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        self.assertEqual(
            MetadataReport.from_json(service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)).report_id,
            self.repository.current_id,
        )

    def test_raw_adapter_authority_is_bound_and_retry_has_deadline(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),))
        provider = _Provider(replacement)
        successor = MetadataReport.from_json(self.service(
            ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2)
        ).run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2))
        self.assertEqual("open_library", successor.providers[0].authority)
        self.assertEqual(1, len(provider.deadlines))
        self.assertGreater(provider.deadlines[0], 0.0)

    def test_final_source_drift_prevents_factory_dispatch(self):
        replacement = provider_observation("catalogue", (), authority="open_library")
        provider = _Provider(replacement)

        class DriftingTarget:
            reads = 0
            def read(_self, _item):
                _self.reads += 1
                return self.snapshot if _self.reads < 2 else TargetSnapshot("ebook", "changed", self.snapshot.files)

        service = GenerateProviderRetrySuccessor(
            target=DriftingTarget(), inspector=_Inspector(),
            repository_factory=lambda: self.repository,
            provider_resolver=lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2
            ),
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(), after_publication=lambda *_args: None,
        )
        with self.assertRaisesRegex(Exception, "target or media"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)

    def test_same_report_package_advancement_prevents_factory_dispatch(self):
        replacement = provider_observation("catalogue", (), authority="open_library")
        provider = _Provider(replacement)

        class AdvancingTarget:
            reads = 0
            def read(_self, _item):
                _self.reads += 1
                if _self.reads == 2:
                    self.repository.package = (None, self.report.report_id, None, None, "no_case")
                return self.snapshot

        service = GenerateProviderRetrySuccessor(
            target=AdvancingTarget(), inspector=_Inspector(),
            repository_factory=lambda: self.repository,
            provider_resolver=lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2
            ),
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(), after_publication=lambda *_args: None,
        )
        with self.assertRaisesRegex(Exception, "target or media"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)

    def test_malformed_package_prevents_factory_dispatch(self):
        provider = _Provider(provider_observation("catalogue", (), authority="open_library"))

        class MalformedPackageRepository(_Repository):
            def current_review_package(_self, *_args):
                raise ProviderRetrySuccessorError("persisted review package is invalid")

        repository = MalformedPackageRepository(self.report)
        service = GenerateProviderRetrySuccessor(
            target=_Target(self.snapshot), inspector=_Inspector(),
            repository_factory=lambda: repository,
            provider_resolver=lambda *_args: ConfiguredProviderRetry(
                ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2
            ),
            mount=MediaMountConfiguration("books", "/container", "/service", True),
            limits=ReportLimits(), after_publication=lambda *_args: None,
        )
        with self.assertRaisesRegex(Exception, "package is invalid"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)

    def test_malformed_replacement_leaves_predecessor_current(self):
        provider = _Provider(provider_observation(
            "catalogue", (ProviderLaneResult(self.failed, ProviderStatus.MALFORMED),), authority="open_library"
        ))
        service = self.service(ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2
        ))
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "result"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual(self.report.report_id, self.repository.current_id)

    def test_malformed_aggregate_with_clean_lane_leaves_predecessor_current(self):
        clean_lane = provider_observation(
            "catalogue", (ProviderLaneResult(self.failed, ProviderStatus.NO_MATCH),), authority="open_library"
        )
        provider = _Provider(replace(clean_lane, status=ProviderStatus.MALFORMED))
        service = self.service(ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2
        ))
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "result"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual(self.report.report_id, self.repository.current_id)
        self.assertEqual(self.report.report_id, self.repository.package[1])

    def test_shared_terminal_matrix_is_typed_before_publication(self):
        for status, failure in (
            (ProviderStatus.AUTHENTICATION, "authentication"),
            (ProviderStatus.AUTHORIZATION, "authorization"),
            (ProviderStatus.RATE_LIMITED, "rate_limited"),
            (ProviderStatus.UNAVAILABLE, "unavailable"),
            (ProviderStatus.INCOMPLETE, "incomplete"),
        ):
            with self.subTest(status=status):
                self.repository = _Repository(self.report)
                provider = _Provider(provider_observation(
                    "catalogue", (ProviderLaneResult(self.failed, status),),
                    authority="open_library",
                ))
                service = self.service(ConfiguredProviderRetry(
                    ProviderBinding("catalogue", True, lambda: provider, "open_library"),
                    "open_library", 2,
                ))
                with self.assertRaises(ProviderRetrySharedFailure) as raised:
                    service.run(
                        self.report.report_id, (self.failed.identity,),
                        provider_name="catalogue", adapter="open_library", generation=2,
                    )
                self.assertEqual(failure, raised.exception.failure)
                self.assertEqual(self.report.report_id, self.repository.current_id)
                self.assertEqual(self.report.report_id, self.repository.package[1])

    def test_validated_final_retry_after_is_carried_by_shared_failure(self):
        record = ProviderAttemptRecord(
            "catalogue", 1, "http_429", 37.0, 0.1, "exhausted"
        )
        provider = _Provider(provider_observation(
            "catalogue", (ProviderLaneResult(self.failed, ProviderStatus.RATE_LIMITED),),
            authority="open_library",
        ), (record,))
        service = self.service(ConfiguredProviderRetry(
            ProviderBinding("catalogue", True, lambda: provider, "open_library"),
            "open_library", 2,
        ))
        with self.assertRaises(ProviderRetrySharedFailure) as raised:
            service.run(
                self.report.report_id, (self.failed.identity,),
                provider_name="catalogue", adapter="open_library", generation=2,
            )
        self.assertEqual(("rate_limited", 37), (
            raised.exception.failure, raised.exception.retry_after,
        ))

    def test_merge_uses_one_timestamp_and_retains_bounded_evidence(self):
        diagnostic = FailureDiagnostic(
            FailureCategory.UNKNOWN, FailureComponent.PROVIDER, FailureStage.RESPONSE
        )
        original = provider_observation("catalogue", (
            ProviderLaneResult(self.failed, ProviderStatus.UNAVAILABLE, accessed_at="2026-08-14T12:00:00.000000Z"),
            ProviderLaneResult(self.success, ProviderStatus.CANDIDATES, (ProviderCandidate("old", "work", ()),), "2026-08-14T12:00:00.000000Z"),
        ), authority="open_library", diagnostics=(diagnostic,), request_accounting=ProviderRequestAccounting(2, 2))
        replacement = provider_observation("catalogue", (
            ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),), "2026-08-14T12:01:00.000000Z"),
        ), authority="open_library", diagnostics=(diagnostic,), request_accounting=ProviderRequestAccounting(1, 1))
        merged = self.service(None)._merged(self.plan, original, (self.failed,), replacement)
        self.assertEqual("2026-08-14T12:01:00.000000Z", merged.accessed_at)
        self.assertEqual(
            next(lane for lane in original.lanes if lane.identity == self.success.identity).to_data(),
            next(lane for lane in merged.lanes if lane.identity == self.success.identity).to_data(),
        )
        self.assertEqual(2, merged.diagnostics[0].count)
        self.assertEqual(ProviderRequestAccounting(3, 3), merged.request_accounting)

    def test_retained_work_overflow_survives_selected_isbn_no_match(self):
        plan, work, isbn, original = self._overflow_source()
        replacement = provider_observation("catalogue", (
            ProviderLaneResult(isbn, ProviderStatus.NO_MATCH),
        ), authority="open_library")
        merged = self.service(None)._merged(plan, original, (isbn,), replacement)
        self.assertEqual(original.edition_enrichment_overflow, merged.edition_enrichment_overflow)

    def test_retry_that_erases_retained_work_overflow_fails_closed(self):
        plan, work, isbn, original = self._overflow_source()
        replacement = provider_observation("catalogue", (
            ProviderLaneResult(isbn, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),
        ), authority="open_library")
        before = original.to_data()
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "overflow"):
            self.service(None)._merged(plan, original, (isbn,), replacement)
        self.assertEqual(before, original.to_data())

    def _overflow_source(self):
        title = LocalClaim.create(
            kind=ClaimKind.TITLE, observed="Current title", canonical="Current title",
            excerpt="Current title", applicability="not_applicable", basis="target_metadata",
            checksum="not_applicable", applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        fields = tuple(
            replace(field, state=TargetFieldState.VALUE, value=("Ada Example",))
            if field.destination is TargetDestination.AUTHORS else field
            for field in TargetSnapshot("ebook", "Current title", ()).fields
        )
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", "Current title", (), fields=fields), (title, self.claims[0])
        )
        work = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.WORK)
        isbn = next(lane for lane in plan.lanes if lane.kind is ProviderQueryKind.ISBN)
        payload = {"data": {"editions": [
            {"id": index, "book_id": 10, "isbn_10": None, "isbn_13": None,
             "asin": None, "title": None, "subtitle": None, "release_date": None,
             "image": None, "publisher": None, "language": None, "contributions": []}
            for index in range(1, 7)
        ], "books": [{"id": 10, "title": "Current title", "description": None,
            "release_date": None, "image": None, "contributions": [{"id": 1,
            "contribution": "Author", "author": {"id": 7, "name": "Ada Example"}}],
            "book_series": [], "taggings": []}], "tags": []}}
        witness = hardcover_metadata._parse_edition_enrichment_overflow(
            json.dumps(payload).encode(), "catalogue"
        )
        assert witness is not None
        return plan, work, isbn, provider_observation("catalogue", (
            ProviderLaneResult(work, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                               edition_enrichment_overflow=witness),
            ProviderLaneResult(isbn, ProviderStatus.UNAVAILABLE),
        ), authority="open_library")

    def test_real_sqlite_successor_staging_retains_the_source_package(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),), authority="open_library")
        provider = _Provider(replacement)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            source_repository = open_repository(path)
            try:
                source_repository.publish(self.report, lambda: True)
            finally:
                source_repository.close()
            service = GenerateProviderRetrySuccessor(
                target=_Target(self.snapshot), inspector=_Inspector(),
                repository_factory=lambda: open_repository(path),
                provider_resolver=lambda *_args: ConfiguredProviderRetry(
                    ProviderBinding("catalogue", True, lambda: provider, "open_library"),
                    "open_library", 2,
                ),
                mount=MediaMountConfiguration("books", "/container", "/service", True),
                limits=ReportLimits(), after_publication=lambda *_args: None,
            )
            successor = MetadataReport.from_json(service.run(
                self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2
            ))
            repository = open_repository(path)
            try:
                self.assertEqual(self.report.report_id, repository.current_review_package_report(
                    self.report.target.connection, self.report.target.library, self.report.target.item
                ))
                self.assertEqual(successor.to_json(), repository.current(
                    successor.target.connection, successor.target.library,
                    successor.target.item, successor.file.identity,
                ))
            finally:
                repository.close()

    def test_recreated_equivalent_resolver_factory_is_current(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),), authority="open_library")
        provider = _Provider(replacement)
        def resolver(*_args):
            return ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2)
        successor = self.service(None, resolver=resolver).run(
            self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2
        )
        self.assertNotEqual(self.report.report_id, MetadataReport.from_json(successor).report_id)

    def test_full_currentness_failure_prevents_provider_factory_dispatch(self):
        provider = _Provider(provider_observation("catalogue", (), authority="open_library"))
        service = self.service(
            ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2),
            inspector=_Inspector(False),
        )
        with self.assertRaisesRegex(Exception, "target or media"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)

    def test_rejects_successful_lane_before_provider_dispatch(self):
        provider = _Provider(provider_observation("catalogue", (), authority="open_library"))
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "lane"):
            service.run(self.report.report_id, (self.success.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)

    def test_rebound_generation_and_provider_failure_leave_predecessor_current(self):
        provider = _Provider(provider_observation("catalogue", (), authority="open_library"))
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "configuration"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=3)
        self.assertEqual(self.report.report_id, self.repository.current_id)

        class FailingProvider:
            def observe(self, *_args, **_kwargs): raise RuntimeError("offline")
        failing = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, FailingProvider, "open_library"), "open_library", 2))
        with self.assertRaisesRegex(ProviderRetrySuccessorError, "dispatch"):
            failing.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual(self.report.report_id, self.repository.current_id)

    def test_rejects_report_current_but_not_current_package_before_dispatch(self):
        provider = _Provider(provider_observation("catalogue", (), authority="open_library"))
        self.repository.package = ("proposal", "not-the-source", "case", 2, "admitted")
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        with self.assertRaisesRegex(Exception, "target or media"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)
