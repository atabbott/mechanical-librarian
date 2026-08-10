from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from types import SimpleNamespace

from scripts.qualify_audiobookshelf_field_effect import (
    QualificationError,
    _epub,
    _qualification_hardcover_client,
    _require_installed_return_automatic_transition,
    _require_installed_return_lineage_progress,
    _operator_epub,
    _require_installed_full_chain_coverage,
)


class AudiobookshelfFieldEffectQualificationTests(unittest.TestCase):
    def test_return_to_automatic_retains_manual_hold_until_fresh_lineage(self) -> None:
        _require_installed_return_automatic_transition(204, object())

        with self.assertRaisesRegex(
            QualificationError, "installed_return_automatic_failed"
        ):
            _require_installed_return_automatic_transition(202, object())
        with self.assertRaisesRegex(
            QualificationError, "installed_return_automatic_failed"
        ):
            _require_installed_return_automatic_transition(204, None)

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
