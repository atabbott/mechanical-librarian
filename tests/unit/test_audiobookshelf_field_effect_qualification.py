from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.qualify_audiobookshelf_field_effect import (
    QualificationError,
    _compose_review_client,
    _epub,
    _installed_consent_grants,
    _installed_mutation_headers,
    _qualification_hardcover_client,
    _read_installed_return_lineage,
    _require_installed_return_automatic_transition,
    _require_installed_return_lineage_progress,
    _operator_epub,
    _require_installed_full_chain_coverage,
    _require_installed_lifecycle_evidence,
)
from mechanical_librarian.interfaces.web.react_assets import ReactAssetRecovery


class AudiobookshelfFieldEffectQualificationTests(unittest.TestCase):
    def test_installed_mutations_bind_the_loaded_browser_identity(self) -> None:
        assets = SimpleNamespace(build_identity="a" * 64)
        with patch(
            "scripts.qualify_audiobookshelf_field_effect.ReactAssetLoader"
        ) as loader:
            loader.return_value.load.return_value = assets
            headers = _installed_mutation_headers(
                {"Origin": "https://review.test"}, "csrf-token"
            )

        self.assertEqual(headers, {
            "Origin": "https://review.test",
            "X-CSRF-Token": "csrf-token",
            "X-ML-Build-Identity": "a" * 64,
        })

    def test_installed_mutations_fail_closed_without_browser_assets(self) -> None:
        with patch(
            "scripts.qualify_audiobookshelf_field_effect.ReactAssetLoader"
        ) as loader:
            loader.return_value.load.return_value = ReactAssetRecovery()
            with self.assertRaisesRegex(
                QualificationError, "installed_browser_assets_unavailable"
            ):
                _installed_mutation_headers(
                    {"Origin": "https://review.test"}, "csrf-token"
                )

    def test_installed_consent_uses_only_capability_allowed_destinations(self) -> None:
        grants = _installed_consent_grants({"capabilities": [
            {"library": "alpha", "destinations": [
                {"destination": "description", "state": "not_ready"},
                {"destination": "title", "state": "allowed"},
            ]},
            {"library": "beta", "destinations": [
                {"destination": "authors", "state": "allowed"},
            ]},
        ]}, (("alpha", "alpha-id", Path("alpha")),
             ("beta", "beta-id", Path("beta"))))

        self.assertEqual(grants, [
            {"library": "alpha", "destinations": ["title"]},
            {"library": "beta", "destinations": ["authors"]},
        ])

    def test_installed_qualification_composes_application_before_fastapi(self) -> None:
        repository = Mock()
        review = SimpleNamespace(
            owner_password_hash_file="/synthetic/owner.hash",
            session_lifetime_seconds=600,
            graceful_shutdown_seconds=5,
        )
        preparation = Mock()
        continuous = Mock()
        cover_considerations = Mock()
        application = object()
        client = object()

        with (
            patch(
                "scripts.qualify_audiobookshelf_field_effect.load_owner_password_verifier",
                return_value=lambda _: False,
            ),
            patch(
                "scripts.qualify_audiobookshelf_field_effect.compose_review_application",
                return_value=application,
            ) as compose,
            patch(
                "scripts.qualify_audiobookshelf_field_effect.composition._compose_cover_considerations",
                return_value=cover_considerations,
            ) as compose_covers,
            patch(
                "scripts.qualify_audiobookshelf_field_effect.create_review_app",
                return_value=object(),
            ) as create,
            patch(
                "scripts.qualify_audiobookshelf_field_effect.TestClient",
                return_value=client,
            ),
        ):
            result = _compose_review_client(
                repository,
                review,
                preparation,
                continuous,
                (("books", None),),
                Path("/synthetic/artifacts"),
            )

        self.assertIs(result, client)
        compose.assert_called_once()
        self.assertIs(
            compose.call_args.kwargs["cover_considerations"], cover_considerations
        )
        compose_covers.assert_called_once_with(
            repository, Path("/synthetic/artifacts"), review
        )
        create.assert_called_once_with(application, review)

    def test_return_to_automatic_retains_manual_hold_until_fresh_lineage(self) -> None:
        before = SimpleNamespace(scan_id="review_scan", updated_at=10)
        after = SimpleNamespace(scan_id="review_scan", updated_at=11)
        _require_installed_return_automatic_transition(204, before, after)

        for status, candidate in (
            (202, after),
            (204, before),
            (204, SimpleNamespace(scan_id="other", updated_at=11)),
        ):
            with self.subTest(status=status, candidate=candidate):
                with self.assertRaisesRegex(
                    QualificationError, "installed_return_automatic_failed"
                ):
                    _require_installed_return_automatic_transition(
                        status, before, candidate
                    )

    def test_return_to_automatic_requires_requested_scan_before_hold_clear(self) -> None:
        requested_scan_id = "scan-library"
        requested_at = 20
        self.assertFalse(_require_installed_return_lineage_progress(
            SimpleNamespace(
                scan_id=requested_scan_id, state="processing",
                rescan_requested=True, updated_at=requested_at,
            ),
            requested_scan_id,
            requested_at,
            object(),
        ))
        self.assertTrue(_require_installed_return_lineage_progress(
            SimpleNamespace(
                scan_id=requested_scan_id, state="completed",
                rescan_requested=False, updated_at=requested_at + 1,
            ),
            requested_scan_id,
            requested_at,
            None,
        ))
        self.assertTrue(_require_installed_return_lineage_progress(
            SimpleNamespace(
                scan_id=requested_scan_id, state="completed",
                rescan_requested=False, updated_at=requested_at + 1,
            ),
            requested_scan_id,
            requested_at,
            object(),
        ))
        for progress, hold in (
            (SimpleNamespace(
                scan_id=requested_scan_id, state="processing",
                rescan_requested=True, updated_at=requested_at,
            ), None),
            (SimpleNamespace(
                scan_id=requested_scan_id, state="completed",
                rescan_requested=False, updated_at=requested_at - 1,
            ), None),
            (SimpleNamespace(
                scan_id=requested_scan_id, state="completed_with_failures",
                rescan_requested=False, updated_at=requested_at + 1,
            ), object()),
        ):
            with self.subTest(progress=progress, hold=hold):
                with self.assertRaisesRegex(
                    QualificationError, "installed_return_fresh_lineage_failed"
                ):
                    _require_installed_return_lineage_progress(
                        progress, requested_scan_id, requested_at, hold
                    )

    def test_return_lineage_reads_hold_before_progress(self) -> None:
        events = []
        repository = SimpleNamespace(
            field_effect_manual_hold_context=(
                lambda *_: events.append("hold") or None
            ),
            review_pipeline_progress=(
                lambda *_: events.append("progress") or SimpleNamespace(
                    state="completed"
                )
            ),
        )

        progress, hold = _read_installed_return_lineage(
            repository, "primary", "books", "review_case", "title"
        )

        self.assertEqual(events, ["hold", "progress"])
        self.assertEqual(progress.state, "completed")
        self.assertIsNone(hold)

    def test_hardcover_fixture_uses_distinct_candidate_identities_per_discovery_lane(self) -> None:
        client = _qualification_hardcover_client(
            {}, "synthetic-provider-token-a", {}, {}, {}
        )
        try:
            isbn_response = client.post(
                "https://qualification.invalid/graphql",
                json={"variables": {"isbns": ["9780306406157"]}},
            )
            work_response = client.post(
                "https://qualification.invalid/graphql",
                json={
                    "variables": {
                        "title": "Guarded qualification title",
                        "authors": ["Synthetic Author"],
                    }
                },
            )
        finally:
            client.close()

        isbn_data = json.loads(isbn_response.content)["data"]
        work_data = json.loads(work_response.content)["data"]
        self.assertNotEqual(
            isbn_data["editions"][0]["id"], work_data["editions"][0]["id"]
        )
        self.assertNotEqual(
            isbn_data["books"][0]["id"], work_data["books"][0]["id"]
        )

    def test_missing_installed_destination_fails_full_chain_qualification(self) -> None:
        expected = {"title": "automatic_policy", "cover": "human"}
        with self.assertRaisesRegex(
            QualificationError, "installed_scheduler_failed"
        ) as captured:
            _require_installed_full_chain_coverage(
                {"title": "automatic_policy"},
                expected,
                activity_present=True,
                diagnostics={"thread_alive": False},
            )
        self.assertIn('"missing": {"cover": "human"}', str(captured.exception))

        _require_installed_full_chain_coverage(
            expected,
            expected,
            activity_present=True,
            diagnostics={},
        )

    def test_lifecycle_evidence_reads_cleanup_from_persistence_not_public_activity(self) -> None:
        _require_installed_lifecycle_evidence(
            activity_status=200,
            suspensions=({"scope": "destination"},),
            pending_cleanup_plan_ids=(),
            event_documents=(("planned",), ("verified",)),
        )
        with self.assertRaisesRegex(
            QualificationError, "installed_lifecycle_evidence_failed"
        ):
            _require_installed_lifecycle_evidence(
                activity_status=200,
                suspensions=({"scope": "destination"},),
                pending_cleanup_plan_ids=("effect_plan_pending",),
                event_documents=(("planned",),),
            )

    def test_operator_overlay_preserves_content_and_binds_valid_isbn_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.epub"
            destination = root / "operator.epub"
            _epub(source)
            isbn, title, author = _operator_epub(source, destination, 3)

            self.assertEqual(13, len(isbn))
            self.assertTrue(isbn.isdigit())
            self.assertEqual(
                0,
                sum(
                    int(value) * (1 if position % 2 == 0 else 3)
                    for position, value in enumerate(isbn)
                ) % 10,
            )
            with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination) as overlaid:
                self.assertEqual(
                    original.read("OEBPS/chapter.xhtml"),
                    overlaid.read("OEBPS/chapter.xhtml"),
                )
                package = ET.fromstring(overlaid.read("OEBPS/content.opf"))
                metadata = package.find("{http://www.idpf.org/2007/opf}metadata")
                assert metadata is not None
                dc = "{http://purl.org/dc/elements/1.1/}"
                self.assertEqual(title, metadata.findtext(dc + "title"))
                self.assertEqual(isbn, metadata.findtext(dc + "identifier"))
            self.assertEqual("Synthetic Author.", author)
            self.assertEqual(1, len(metadata.findall(dc + "creator")))


if __name__ == "__main__":
    unittest.main()
