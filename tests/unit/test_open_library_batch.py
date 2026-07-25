from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.batch import (
    BatchError,
    BatchSelection,
    CatalogueSnapshot,
    batch_fingerprint,
    manifest,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap import composition


def _entry(ordinal: int) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "books",
        "library",
        f"item-{ordinal}",
        1,
        f"Title {ordinal}",
        (),
        True,
        False,
        "singular-supported",
        f"{ordinal:x}".rjust(64, "0"),
        f"item-{ordinal}.epub",
        "epub",
        "books",
    )


def _selection(ordinal: int) -> BatchSelection:
    value = _entry(ordinal)
    return BatchSelection(
        value.connection,
        value.library,
        value.library_id,
        value.item_id,
        value.file_identity,
        value.classification,
        value.eligible,
    )


def _configuration(enabled: bool) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary",
                "https://example.invalid",
                "/private/target",
            ),
        ),
        (
            LibraryConfiguration(
                "books",
                "primary",
                "library",
                "book",
                None,
                ("open",),
            ),
        ),
        (),
        (
            ProviderConnectionConfiguration(
                "open",
                enabled,
                False,
                None,
                "open_library",
                "operator@example.invalid" if enabled else None,
            ),
        ),
    )


class _Catalogue:
    def __init__(self, snapshot: CatalogueSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self, fingerprint: str) -> CatalogueSnapshot:
        return self._snapshot

    def close(self) -> None:
        pass


class OpenLibraryBatchEnvelopeTests(unittest.TestCase):
    def test_preview_rejects_all_eligible_and_six_explicit_before_manifest(self) -> None:
        configuration = _configuration(True)
        snapshot = CatalogueSnapshot(
            1,
            "a" * 64,
            tuple(_entry(ordinal) for ordinal in range(1, 7)),
        )
        requested = tuple(
            ("primary", "books", "library", f"item-{ordinal}")
            for ordinal in range(1, 7)
        )
        cases = (((), True), (requested, False))
        for selected, all_eligible in cases:
            with (
                self.subTest(all_eligible=all_eligible),
                tempfile.TemporaryDirectory() as directory,
                patch.object(
                    composition,
                    "load_configuration_file",
                    return_value=configuration,
                ),
                patch.object(
                    composition,
                    "open_catalogue",
                    return_value=_Catalogue(snapshot),
                ),
                patch.object(composition, "create_batch_manifest") as create,
            ):
                with self.assertRaisesRegex(
                    BatchError,
                    "Open Library batch selection is invalid",
                ):
                    composition.preview_batch(
                        Path(directory) / "config.json",
                        Path(directory) / "catalogue.sqlite",
                        Path(directory) / "batch.json",
                        selected,
                        all_eligible,
                    )
                create.assert_not_called()
                self.assertFalse((Path(directory) / "batch.json").exists())

    def test_disabled_open_library_preserves_larger_foreground_batches(self) -> None:
        configuration = _configuration(False)
        snapshot = CatalogueSnapshot(
            1,
            "a" * 64,
            tuple(_entry(ordinal) for ordinal in range(1, 7)),
        )
        requested = tuple(
            ("primary", "books", "library", f"item-{ordinal}")
            for ordinal in range(1, 7)
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                composition,
                "load_configuration_file",
                return_value=configuration,
            ),
            patch.object(
                composition,
                "open_catalogue",
                return_value=_Catalogue(snapshot),
            ),
        ):
            result = composition.preview_batch(
                Path(directory) / "config.json",
                Path(directory) / "catalogue.sqlite",
                Path(directory) / "batch.json",
                requested,
                False,
            )
            self.assertIn('"entries":6', result)
            self.assertTrue((Path(directory) / "batch.json").is_file())

    def test_run_rejects_oversized_manifest_before_state_or_credentials(self) -> None:
        configuration = _configuration(True)
        selections = tuple(_selection(ordinal) for ordinal in range(1, 7))
        raw = manifest(
            CatalogueSnapshot(1, "a" * 64, ()),
            batch_fingerprint(configuration, selections),
            selections,
            ("open",),
            ("open_library",),
        )
        with (
            patch.object(composition, "read_batch_manifest", return_value=raw),
            patch.object(
                composition,
                "load_configuration_file",
                return_value=configuration,
            ),
            patch.object(composition, "open_catalogue") as open_catalogue,
            patch.object(composition, "_load_batch_credentials") as credentials,
            patch.object(composition, "OpenLibraryPacer") as pacer,
            patch.object(composition, "open_library_client") as client,
        ):
            with self.assertRaisesRegex(
                BatchError,
                "Open Library batch selection is invalid",
            ):
                composition.run_batch(
                    Path("config.json"),
                    Path("catalogue.sqlite"),
                    Path("batch.json"),
                    composition.parse_manifest(raw)["id"],
                    Path("reports.sqlite"),
                    Path("artifacts"),
                    lambda _: None,
                )
            open_catalogue.assert_not_called()
            credentials.assert_not_called()
            pacer.assert_not_called()
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
