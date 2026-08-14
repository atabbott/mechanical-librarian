from __future__ import annotations

import unittest

from mechanical_librarian.application.configuration import MediaMountConfiguration, ReportLimits
from mechanical_librarian.application.metadata_report import (
    ProviderBinding, ProviderLaneResult, TargetFile, TargetSnapshot, metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.application.provider_retry_successor import (
    ConfiguredProviderRetry, GenerateProviderRetrySuccessor, ProviderRetrySuccessorError,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind, FileFacts, LocalClaim, MetadataReport, ProviderCandidate, ProviderStatus,
    TargetFacts,
)


class _Target:
    def __init__(self, snapshot): self.snapshot = snapshot
    def read(self, _item): return self.snapshot


class _Inspector:
    def __init__(self, current=True): self.current = current
    def unchanged(self, *_args): return self.current


class _Repository:
    def __init__(self, report):
        self.values = {report.report_id: report.to_json()}; self.current_id = report.report_id; self.package_id = report.report_id
    def get(self, report_id): return self.values[report_id]
    def current_optional(self, *_args): return self.values[self.current_id]
    def current_review_package_report(self, *_args): return self.package_id
    def publish(self, report, currentness, expected_current=None, after_publication=None, *, expected_package_report=None):
        if expected_current != self.current_id or expected_package_report != self.package_id or not currentness(): raise ProviderRetrySuccessorError("stale")
        self.values[report.report_id] = report.to_json(); self.current_id = report.report_id
        if after_publication is not None: after_publication(self, report)
        return report.to_json()
    def close(self): pass


class _Provider:
    def __init__(self, observation): self.observation = observation; self.plans = []
    def observe(self, plan, *, deadline=None): self.plans.append(plan); return self.observation


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
        self.assertEqual(self.report.report_id, self.repository.package_id)

    def test_none_returning_staging_receipt_succeeds(self):
        replacement = provider_observation("catalogue", (ProviderLaneResult(self.failed, ProviderStatus.CANDIDATES, (ProviderCandidate("new", "work", ()),)),), authority="open_library")
        provider = _Provider(replacement)
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        self.assertEqual(
            MetadataReport.from_json(service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)).report_id,
            self.repository.current_id,
        )

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
        self.repository.package_id = "not-the-source"
        service = self.service(ConfiguredProviderRetry(ProviderBinding("catalogue", True, lambda: provider, "open_library"), "open_library", 2))
        with self.assertRaisesRegex(Exception, "target or media"):
            service.run(self.report.report_id, (self.failed.identity,), provider_name="catalogue", adapter="open_library", generation=2)
        self.assertEqual([], provider.plans)
