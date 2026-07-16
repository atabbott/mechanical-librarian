from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mechanical_librarian.adapters.library_services.audiobookshelf_inventory import (
    _epub_binding,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryMountMapping,
)
from mechanical_librarian.application.epub_opening_evidence import (
    EpubOpeningExecution,
    EpubSourceFingerprintError,
    RunEpubOpeningEvidence,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceFailure,
    EpubEvidenceClaim,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    EpubEvidenceResult,
    EpubSourceResolution,
    ResolvedEpubSource,
    epub_evidence_identity,
    epub_governing_digest,
    epub_work_key,
)
from mechanical_librarian.domain.local_evidence import LocalEvidenceOutcome


class EpubOpeningEvidenceDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.locator = EpubEvidenceLocator(
            "target_" + "1" * 32,
            "snapshot_" + "2" * 32,
            0,
        )
        self.source = ResolvedEpubSource(
            self.locator,
            "3" * 64,
            "4" * 64,
            "books",
            "book.epub",
        )

    def test_work_identity_changes_for_every_private_source_generation(self) -> None:
        governing = epub_governing_digest(EpubOpeningLimits(), {"profile": "test"})
        baseline = epub_work_key(self.source, "5" * 64, governing)
        self.assertNotEqual(
            baseline,
            epub_work_key(self.source, "6" * 64, governing),
        )
        changed_source = ResolvedEpubSource(
            self.locator,
            "7" * 64,
            self.source.mount_configuration_digest,
            "books",
            "book.epub",
        )
        self.assertNotEqual(
            baseline,
            epub_work_key(changed_source, "5" * 64, governing),
        )

    def test_every_named_governing_contract_family_invalidates_identity(self) -> None:
        import mechanical_librarian.domain.epub_opening_evidence as evidence

        baseline = epub_governing_digest(EpubOpeningLimits(), {"profile": "one"})
        names = (
            "EPUB_OPENING_EXTRACTOR_VERSION",
            "EPUB_OPENING_PARSER_VERSION",
            "EPUB_OPENING_RECOVERY_VERSION",
            "EPUB_OPENING_PROJECTION_VERSION",
            "EPUB_OPENING_NORMALIZER_VERSION",
            "EPUB_OPENING_VOCABULARY_VERSION",
            "EPUB_OPENING_SELECTION_VERSION",
            "EPUB_OPENING_LIMIT_PROFILE",
        )
        for name in names:
            with self.subTest(name=name), patch.object(evidence, name, "changed-contract"):
                self.assertNotEqual(
                    epub_governing_digest(EpubOpeningLimits(), {"profile": "one"}),
                    baseline,
                )
        self.assertNotEqual(
            epub_governing_digest(EpubOpeningLimits(), {"profile": "two"}), baseline
        )

    def test_evidence_identity_binds_canonical_report_and_actual_outcome(self) -> None:
        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
            EpubOpeningLimits(),
        )
        values = epub_evidence_identity(
            work_key="1" * 64,
            binding_digest="2" * 64,
            source_fingerprint="3" * 64,
            governing_digest="4" * 64,
            report=report,
        )
        self.assertTrue(values[0].startswith("evidence_"))
        self.assertEqual(values[3], "none")
        self.assertEqual(len(values[1]), 64)

    def test_operational_failure_cannot_masquerade_as_evidence(self) -> None:
        with self.assertRaises(ValueError):
            epub_evidence_identity(
                work_key="1" * 64,
                binding_digest="2" * 64,
                source_fingerprint="3" * 64,
                governing_digest="4" * 64,
                report=EpubOpeningReport(
                    LocalEvidenceOutcome.RESOURCE_LIMIT,
                    EpubOpeningLimits(),
                ),
            )
        result = EpubEvidenceResult(
            EpubEvidenceMode.FAILED,
            failure=EpubEvidenceFailure.RESOURCE_LIMIT,
        )
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.evidence_id)

    def test_abs_path_contract_creates_only_one_private_logical_binding(self) -> None:
        mapping = InventoryMountMapping(
            "books", "ebook", "/library/books", "a" * 64
        )
        item = {
            "ino": "synthetic-inode",
            "ebookFormat": "epub",
            "metadata": {
                "path": "/library/books/series/book.epub",
                "relPath": "series/book.epub",
            },
        }
        binding = _epub_binding(item, (mapping,))
        self.assertIsNotNone(binding)
        self.assertEqual(binding.reference.mount, "books")
        self.assertEqual(binding.reference.relative_path, "series/book.epub")
        self.assertNotIn("/library/books", repr(binding))
        self.assertIsNone(
            _epub_binding(item, (mapping, mapping))
        )
        mismatch = dict(item)
        mismatch["metadata"] = dict(item["metadata"], relPath="other.epub")
        self.assertIsNone(_epub_binding(mismatch, (mapping,)))

    def test_optional_epub_projection_partition_matrix_returns_no_binding(self) -> None:
        mapping = InventoryMountMapping(
            "books", "ebook", "/library/books", "a" * 64
        )
        valid = {
            "ino": "synthetic-inode",
            "ebookFormat": "epub",
            "metadata": {
                "path": "/library/books/series/book.epub",
                "relPath": "series/book.epub",
            },
        }
        partitions = {
            "missing": dict(valid, metadata={}),
            "malformed": dict(
                valid,
                metadata={
                    "path": "/library/books/series/book.epub",
                    "relPath": ["series/book.epub"],
                },
            ),
            "mismatch": dict(
                valid,
                metadata={
                    "path": "/library/books/series/book.epub",
                    "relPath": "other.epub",
                },
            ),
            "ambiguous": valid,
        }
        for label, value in partitions.items():
            with self.subTest(partition=label):
                mappings = (mapping, mapping) if label == "ambiguous" else (mapping,)
                self.assertIsNone(_epub_binding(value, mappings))

    def test_source_change_after_child_result_never_publishes_evidence(self) -> None:
        source = self.source

        class Repository:
            published = False

            def resolve(self, _locator, _configuration):
                return EpubSourceResolution(source=source)

            def accept(self, value, fingerprint, governing, _accepted_at, _authority):
                key = epub_work_key(value, fingerprint, governing)
                return "epub_work_" + key[:32], None

            def claim(self, work_id, _worker, _claimed_at, _duration, _capacity):
                key = epub_work_key(source, "5" * 64, "6" * 64)
                return EpubEvidenceClaim(work_id, key, 1, 1, "worker")

            def publish(self, *_args):
                self.published = True
                raise AssertionError("changed source was published")

            def fail(
                self,
                _claim,
                failure,
                _failed_at,
                *,
                retryable,
                failure_clock=None,
            ):
                self.failure = failure
                self.retryable = retryable
                self.failure_clock = failure_clock
                return EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)

        repository = Repository()
        fingerprints = iter(("5" * 64, "7" * 64))
        result = RunEpubOpeningEvidence(repository).run(
            self.locator,
            self.source.mount_configuration_digest,
            "6" * 64,
            "worker",
            lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            lambda _source: next(fingerprints),
            lambda _source: EpubOpeningExecution(
                EpubOpeningReport(
                    LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
                    EpubOpeningLimits(),
                ),
                "5" * 64,
            ),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.SOURCE_CHANGED)
        self.assertFalse(repository.published)
        self.assertTrue(repository.retryable)

    def test_pre_enqueue_source_limit_is_typed_and_never_accepts_work(self) -> None:
        source = self.source

        class Repository:
            accepted = False

            def resolve(self, *_args):
                return EpubSourceResolution(source=source)

            def accept(self, *_args):
                self.accepted = True
                raise AssertionError("resource-limited source was enqueued")

        repository = Repository()
        result = RunEpubOpeningEvidence(repository).run(
            self.locator,
            source.mount_configuration_digest,
            "6" * 64,
            "worker",
            lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
            lambda _source: (_ for _ in ()).throw(
                EpubSourceFingerprintError(EpubEvidenceFailure.RESOURCE_LIMIT)
            ),
            lambda _source: EpubOpeningReport(
                LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
                EpubOpeningLimits(),
            ),
        )
        self.assertEqual(result.failure, EpubEvidenceFailure.RESOURCE_LIMIT)
        self.assertFalse(repository.accepted)

    def test_publishable_execution_provenance_invariant_matrix(self) -> None:
        source = self.source

        class Repository:
            published = False

            def resolve(self, *_args):
                return EpubSourceResolution(source=source)

            def accept(self, value, fingerprint, governing, _at, _authority):
                key = epub_work_key(value, fingerprint, governing)
                return "epub_work_" + key[:32], None

            def claim(self, work_id, *_args):
                return EpubEvidenceClaim(
                    work_id,
                    epub_work_key(source, "5" * 64, "6" * 64),
                    1,
                    1,
                    "worker",
                )

            def publish(
                self,
                _claim,
                _source,
                accepted_source,
                execution_source,
                _governing,
                report,
                *_args,
            ):
                self.published = True
                self.provenance = (accepted_source, execution_source)
                return EpubEvidenceResult(
                    EpubEvidenceMode.EXECUTED,
                    report.outcome,
                    evidence_id="evidence_" + "7" * 32,
                    current_applicable=True,
                )

            def fail(
                self,
                _claim,
                failure,
                _at,
                *,
                retryable,
                failure_clock=None,
            ):
                self.failed = (failure, retryable, failure_clock)
                return EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)

        report = EpubOpeningReport(
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT, EpubOpeningLimits()
        )
        cases = (
            (
                "missing",
                report,
                EpubEvidenceFailure.INTEGRITY_FAILURE,
                False,
            ),
            (
                "a-to-b-to-a",
                EpubOpeningExecution(report, "8" * 64),
                EpubEvidenceFailure.SOURCE_CHANGED,
                False,
            ),
            (
                "stable-negative",
                EpubOpeningExecution(report, "5" * 64),
                None,
                True,
            ),
        )
        for label, execution, expected_failure, expected_publish in cases:
            with self.subTest(partition=label):
                repository = Repository()
                fingerprints = iter(("5" * 64, "5" * 64))
                result = RunEpubOpeningEvidence(repository).run(
                    self.locator,
                    source.mount_configuration_digest,
                    "6" * 64,
                    "worker",
                    lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
                    lambda _source: next(fingerprints),
                    lambda _source, value=execution: value,
                )
                self.assertEqual(result.failure, expected_failure)
                self.assertEqual(repository.published, expected_publish)
                if expected_publish:
                    self.assertTrue(result.succeeded)
                    self.assertEqual(
                        repository.provenance, ("5" * 64, "5" * 64)
                    )

    def test_closed_operational_failures_are_durable_attempts_not_evidence(self) -> None:
        source = self.source

        class Repository:
            def resolve(self, *_args):
                return EpubSourceResolution(source=source)

            def accept(self, value, fingerprint, governing, _at, _authority):
                key = epub_work_key(value, fingerprint, governing)
                return "epub_work_" + key[:32], None

            def claim(self, work_id, *_args):
                return EpubEvidenceClaim(
                    work_id, epub_work_key(source, "5" * 64, "6" * 64), 1, 1, "worker"
                )

            def publish(self, *_args):
                raise AssertionError("operational failure published")

            def fail(
                self,
                _claim,
                failure,
                _at,
                *,
                retryable,
                failure_clock=None,
            ):
                self.observed = (failure, retryable)
                self.failure_clock = failure_clock
                return EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)

        cases = {
            LocalEvidenceOutcome.TIMEOUT: EpubEvidenceFailure.TIMEOUT,
            LocalEvidenceOutcome.CANCELLATION: EpubEvidenceFailure.CANCELLED,
            LocalEvidenceOutcome.RESOURCE_LIMIT: EpubEvidenceFailure.RESOURCE_LIMIT,
            LocalEvidenceOutcome.CHILD_FAILURE: EpubEvidenceFailure.INTERNAL_FAILURE,
        }
        for outcome, expected in cases.items():
            with self.subTest(outcome=outcome):
                repository = Repository()
                result = RunEpubOpeningEvidence(repository).run(
                    self.locator,
                    source.mount_configuration_digest,
                    "6" * 64,
                    "worker",
                    lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
                    lambda _source: "5" * 64,
                    lambda _source: EpubOpeningReport(outcome, EpubOpeningLimits()),
                )
                self.assertEqual(result.failure, expected)
                self.assertEqual(repository.observed[0], expected)


if __name__ == "__main__":
    unittest.main()
