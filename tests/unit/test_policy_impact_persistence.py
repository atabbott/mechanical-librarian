"""SQLite proof for bounded, current-only policy impact pairs."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


def _case_report(index: int) -> MetadataReport:
    source = _bibliographic_report(
        current_title="Current title",
        providers=(_source("catalogue", f"edition-{index}", titles=("Corrected title",)),),
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", f"item-{index:02d}", source.target.fields),
        file=FileFacts(index, f"{index:064x}", f"{index + 100:064x}"),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )


class PolicyImpactPersistenceTests(unittest.TestCase):
    def test_prior_persisted_policy_schema_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            repository = open_repository(database)
            row = repository._connection.execute(
                "SELECT policy_id,document FROM metadata_policy_drafts"
            ).fetchone()
            self.assertIsNotNone(row)
            document = json.loads(row[1])
            document["schema_version"] = "metadata-policy-profile-v1"
            document["options"].pop("subtitle")
            document["options"].pop("series")
            document["options"].update({
                "asin": "canonical_asin",
                "description_fill": "fill_when_empty",
                "genres": "catalogue",
                "isbn_form": "isbn13",
                "tags": "catalogue",
            })
            document["destinations"].pop("subtitle")
            document["destinations"].pop("series")
            repository._connection.execute(
                "DROP TRIGGER metadata_policy_drafts_no_update"
            )
            repository._connection.execute(
                "UPDATE metadata_policy_drafts SET document=? WHERE policy_id=?",
                (json.dumps(document, sort_keys=True, separators=(",", ":")), row[0]),
            )
            repository._connection.execute(
                "CREATE TRIGGER metadata_policy_drafts_no_update BEFORE UPDATE "
                "ON metadata_policy_drafts BEGIN SELECT "
                "RAISE(ABORT,'immutable metadata policy draft'); END"
            )
            repository._connection.commit()
            repository.close()
            before = database.read_bytes()
            with self.assertRaises(MetadataReportError):
                open_repository(database, create=False)
            self.assertEqual(before, database.read_bytes())

    def test_current_pairs_are_bounded_ordered_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            repository = open_repository(database)
            for index in range(21):
                report = _case_report(index)
                repository.publish(report, lambda: True)
                proposal = evaluate_report(report)
                repository.publish_proposal(proposal)
                repository.admit_review_case(proposal)
            before = database.read_bytes()
            total, pairs = repository.current_policy_impact_pairs("ebooks", 20)
            self.assertEqual(21, total)
            self.assertEqual(20, len(pairs))
            self.assertEqual(
                tuple(sorted(report.target.item for report, _ in pairs)),
                tuple(report.target.item for report, _ in pairs),
            )
            self.assertEqual(before, database.read_bytes())
            self.assertEqual((21, 21, 20), (total, total, len(pairs)))
            self.assertEqual((0, ()), repository.current_policy_impact_pairs("missing", 20))
            repository.close()
            reader = open_shadow_preview_repository(database)
            self.assertEqual(21, reader.current_unapplied_count("ebooks"))
            self.assertEqual(
                (21, 20),
                (
                    reader.current_policy_impact_pairs("ebooks", 20)[0],
                    len(reader.current_policy_impact_pairs("ebooks", 20)[1]),
                ),
            )
            reader.close()
            self.assertEqual(before, database.read_bytes())

    def test_limit_and_malformed_rows_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            repository = open_repository(database)
            report = _case_report(1)
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
            self.assertEqual(1, repository.current_policy_impact_pairs("ebooks", 20)[0])
            with self.assertRaises(MetadataReportError):
                repository.current_policy_impact_pairs("ebooks", 0)
            # Persisted proposals are append-only; attempted corruption cannot
            # create a partial preview row.
            with self.assertRaises(sqlite3.IntegrityError):
                repository._connection.execute("UPDATE proposal_sets SET document='{}'")
            self.assertEqual(1, repository.current_policy_impact_pairs("ebooks", 20)[0])
            repository._connection.execute(
                "DROP TRIGGER proposal_sets_no_update"
            )
            repository._connection.execute(
                "UPDATE proposal_sets SET document='{}'"
            )
            repository._connection.execute(
                "CREATE TRIGGER proposal_sets_no_update BEFORE UPDATE ON "
                "proposal_sets BEGIN SELECT RAISE(ABORT,'immutable proposal set'); END"
            )
            repository._connection.commit()
            with self.assertRaises(MetadataReportError):
                repository.current_policy_impact_pairs("ebooks", 20)
            self.assertFalse(repository._connection.in_transaction)
            repository.close()
