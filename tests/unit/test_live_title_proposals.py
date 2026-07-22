from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO

from mechanical_librarian.application.live_title_proposals import (
    LiveProviderEvidence,
    LiveProviderState,
    LiveSourceEvidence,
    LiveSourceState,
    ProposeLiveTitle,
)
from mechanical_librarian.application.title_proposals import (
    ProposalPersistence,
    SyntheticTitleProposalReport,
)
from mechanical_librarian.bootstrap.composition import _run_epub_opening_report
from mechanical_librarian.domain.epub_inspection import EpubInspection
from mechanical_librarian.domain.epub_opening import OpeningField
from mechanical_librarian.domain.epub_opening_evidence import EpubEvidenceLocator
from mechanical_librarian.domain.title_proposals import LiveTitleProposalSet
from mechanical_librarian.interfaces.cli import main as cli_main
from tests.support.epub_packages import opening_title_isbn_applicability


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class _Repository:
    def __init__(
        self,
        source: LiveSourceEvidence,
        provider_state: LiveProviderState = LiveProviderState.READY,
        provider_title: str | None = "The Applicable Clock",
    ) -> None:
        self.source = source
        self.source_at_publish = source
        self.provider_state = provider_state
        self.provider_title = provider_title
        self.provider_reads = 0
        self.current: dict[str, LiveTitleProposalSet] = {}

    def inspect_source(self, locator, mount_configuration_digests):
        self.assert_equal(locator, self.source.locator)
        self.assert_equal(
            mount_configuration_digests, (self.source.mount_configuration_digest,)
        )
        return self.source

    def inspect_provider(self, work_key, observed_at):
        self.provider_reads += 1
        return LiveProviderEvidence(
            self.provider_state,
            _digest(f"provider-{self.provider_state.value}-{self.provider_title}"),
            self.provider_title,
            work_key,
        )

    def current_live(self, lineage):
        return self.current.get(lineage)

    def publish_live(self, proposal_set, source, provider, mount_configuration_digests):
        self.assert_equal(
            mount_configuration_digests, (self.source.mount_configuration_digest,)
        )
        if source != self.source_at_publish:
            raise RuntimeError("source changed")
        existing = self.current.get(proposal_set.lineage)
        replayed = existing == proposal_set
        if not replayed:
            self.current[proposal_set.lineage] = proposal_set
        current = self.current[proposal_set.lineage]
        return ProposalPersistence(
            proposal_set,
            replayed=replayed,
            current=current == proposal_set,
            current_set_id=current.set_id,
            superseded_set_id=(
                None if replayed or proposal_set.previous_set_id is None
                else proposal_set.previous_set_id
            ),
            history_count=current.lineage_revision,
        )

    @staticmethod
    def assert_equal(left, right):
        if left != right:
            raise AssertionError((left, right))


class LiveTitleProposalTests(unittest.TestCase):
    def test_live_persistence_cannot_be_rendered_as_a_synthetic_report(self) -> None:
        proposal_set = self._execute(_Repository(self.source)).proposal_set
        persistence = ProposalPersistence(
            proposal_set,
            replayed=False,
            current=True,
            current_set_id=proposal_set.set_id,
            superseded_set_id=None,
            history_count=proposal_set.lineage_revision,
        )

        with self.assertRaisesRegex(ValueError, "synthetic proposal report is malformed"):
            SyntheticTitleProposalReport(persistence)

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        (root / "book.epub").write_bytes(opening_title_isbn_applicability())
        configuration = json.loads(
            Path("docs/examples/synthetic-config.json").read_text(encoding="utf-8")
        )
        configuration["media_mounts"] = [
            {
                "name": "books",
                "library_type": "ebook",
                "service_path": "/books",
                "container_path": str(root),
                "read_only": True,
            }
        ]
        config_path = root / "config.json"
        config_path.write_text(json.dumps(configuration), encoding="utf-8")
        cls.report = _run_epub_opening_report(config_path, "books", "book.epub")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.locator = EpubEvidenceLocator(
            "target_" + "1" * 32,
            "snapshot_" + "2" * 32,
            0,
        )
        self.source = LiveSourceEvidence(
            self.locator,
            LiveSourceState.READY,
            _digest("mount"),
            inventory_fingerprint=_digest("inventory"),
            current_title_loaded=True,
            current_title=None,
            evidence_id="evidence_" + "3" * 32,
            source_fingerprint=self.report.source_fingerprint.digest,
            inspection=EpubInspection.from_opening_report(self.report),
        )
        self.now = datetime(2026, 7, 21, tzinfo=timezone.utc)

    def _execute(self, repository: _Repository, *, enabled: bool = True):
        return ProposeLiveTitle(repository).run(
            self.locator,
            (self.source.mount_configuration_digest,),
            provider_enabled=enabled,
            authority_revision=1,
            observed_at=self.now,
        )

    def test_positive_is_report_only_and_direct_output_redacts_private_inputs(self) -> None:
        repository = _Repository(self.source)
        report = self._execute(repository)
        self.assertEqual(report.proposal_set.result.value, "needs_review")
        self.assertEqual(report.proposal_set.proposals[0].proposed_value, "The Applicable Clock")
        self.assertTrue(report.proposal_set.production_applicable)
        self.assertEqual(report.proposal_set.mode.value, "production_report_only")
        rendered = report.to_json()
        self.assertNotIn("9780306406157", rendered)
        self.assertNotIn(report.proposal_set.manifest["provider_evidence_id"], rendered)
        self.assertNotIn(self.source.inventory_fingerprint, rendered)
        self.assertNotIn(self.source.mount_configuration_digest, rendered)
        self.assertNotIn(self.source.source_fingerprint, rendered)

    def test_four_stable_zero_categories(self) -> None:
        cases = (
            (
                _Repository(
                    LiveSourceEvidence(
                        self.locator,
                        LiveSourceState.NOT_READY,
                        self.source.mount_configuration_digest,
                    )
                ),
                True,
                "source_not_ready",
                0,
            ),
            (_Repository(self.source), False, "provider_not_ready", 0),
            (
                _Repository(self.source, provider_title="A Different Catalogue Title"),
                True,
                "evidence_disagreement",
                1,
            ),
            (
                _Repository(replace(self.source, current_title="the applicable clock")),
                True,
                "no_change",
                1,
            ),
        )
        for repository, enabled, expected, provider_reads in cases:
            with self.subTest(expected=expected):
                self.source = repository.source
                report = self._execute(repository, enabled=enabled)
                self.assertEqual(report.proposal_set.result.value, expected)
                self.assertEqual(report.proposal_set.proposals, ())
                self.assertFalse(report.proposal_set.production_applicable)
                self.assertEqual(repository.provider_reads, provider_reads)

    def test_null_normalized_isbn_publishes_stable_source_not_ready(self) -> None:
        report = replace(
            self.report,
            claims=tuple(
                replace(claim, normalized_value=None)
                if claim.field is OpeningField.IDENTIFIER
                and claim.identifier_type in {"isbn_10", "isbn_13"}
                else claim
                for claim in self.report.claims
            ),
        )
        source = replace(self.source, inspection=EpubInspection.from_opening_report(report))
        repository = _Repository(source)
        self.source = source

        result = self._execute(repository)

        self.assertEqual(result.proposal_set.result.value, "source_not_ready")
        self.assertEqual(result.proposal_set.proposals, ())
        self.assertFalse(result.proposal_set.production_applicable)
        self.assertEqual(repository.provider_reads, 0)

    def test_exact_replay_and_changed_input_successor(self) -> None:
        repository = _Repository(self.source)
        first = self._execute(repository)
        replay = self._execute(repository)
        self.assertEqual(replay.proposal_set, first.proposal_set)
        self.assertTrue(replay.persistence.replayed)
        repository.source = replace(
            self.source, inventory_fingerprint=_digest("changed-inventory")
        )
        repository.source_at_publish = repository.source
        self.source = repository.source
        successor = self._execute(repository)
        self.assertEqual(successor.proposal_set.lineage_revision, 2)
        self.assertEqual(successor.proposal_set.previous_set_id, first.proposal_set.set_id)
        self.assertNotEqual(successor.proposal_set.set_id, first.proposal_set.set_id)
        repository.source = replace(
            repository.source, mount_configuration_digest=_digest("changed-mount")
        )
        repository.source_at_publish = repository.source
        self.source = repository.source
        mount_successor = self._execute(repository)
        self.assertEqual(mount_successor.proposal_set.lineage_revision, 3)
        self.assertEqual(
            mount_successor.proposal_set.previous_set_id,
            successor.proposal_set.set_id,
        )

    def test_stale_final_source_is_rejected_without_advancing_current(self) -> None:
        repository = _Repository(self.source)
        first = self._execute(repository)
        repository.source = replace(
            self.source, inventory_fingerprint=_digest("candidate-change")
        )
        repository.source_at_publish = self.source
        self.source = repository.source
        with self.assertRaisesRegex(RuntimeError, "source changed"):
            self._execute(repository)
        self.assertEqual(repository.current[first.proposal_set.lineage], first.proposal_set)

    def test_noncurrent_positive_replay_is_never_returned_as_applicable(self) -> None:
        repository = _Repository(self.source)
        first = self._execute(repository)
        successor_id = "ps-" + "a" * 60

        def stale_publish(proposal_set, source, provider, mount_configuration_digests):
            return ProposalPersistence(
                proposal_set,
                replayed=True,
                current=False,
                current_set_id=successor_id,
                superseded_set_id=proposal_set.set_id,
                history_count=2,
            )

        repository.publish_live = stale_publish
        with self.assertRaisesRegex(ValueError, "no longer current"):
            self._execute(repository)

    def test_cli_emits_one_canonical_report_and_rejects_bad_selector(self) -> None:
        expected = self._execute(_Repository(self.source))
        output, errors = StringIO(), StringIO()
        code = cli_main(
            [
                "live-title-proposal-report",
                "--config", "config.json",
                "--database", "state.sqlite",
                "--target", self.locator.target_reference,
                "--snapshot", self.locator.snapshot_reference,
                "--file-index", "0",
                "--format", "json",
            ],
            state_check_runner=lambda _path: None,
            live_title_proposal_runner=lambda *_args: expected,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), expected.to_json() + "\n")
        self.assertEqual(errors.getvalue(), "")

        output, errors = StringIO(), StringIO()
        code = cli_main(
            [
                "live-title-proposal-report",
                "--config", "config.json",
                "--database", "state.sqlite",
                "--target", "not-opaque",
                "--snapshot", self.locator.snapshot_reference,
                "--file-index", "0",
            ],
            state_check_runner=lambda _path: None,
            live_title_proposal_runner=lambda *_args: expected,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "Live title proposal report failed safely.\n")


if __name__ == "__main__":
    unittest.main()
