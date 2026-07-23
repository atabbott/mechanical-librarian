from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from mechanical_librarian.adapters.evidence_extractors.epub_package import _Rejected
from mechanical_librarian.adapters.evidence_extractors.epub_package_model import (
    EpubPackageSourceError,
)
from mechanical_librarian.bootstrap import epub_child
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits


class EpubChildArchiveConstructionTests(unittest.TestCase):
    def test_constructor_timeout_preserves_typed_outcome(self) -> None:
        self.assertEqual(
            self._constructed_outcome(_Rejected(EpubInspectionOutcome.TIMEOUT)),
            EpubInspectionOutcome.TIMEOUT,
        )

    def test_constructor_source_failure_remains_unavailable(self) -> None:
        self.assertEqual(
            self._constructed_outcome(EpubPackageSourceError("read failed")),
            EpubInspectionOutcome.UNAVAILABLE_SOURCE,
        )

    @staticmethod
    def _constructed_outcome(error: Exception) -> EpubInspectionOutcome:
        extractor = Mock()
        extractor.preflight.return_value = None
        with (
            patch.object(epub_child, "EpubPackageExtractor", return_value=extractor),
            patch.object(epub_child, "_SourceIO", return_value=object()),
            patch.object(epub_child.zipfile, "ZipFile", side_effect=error),
        ):
            return epub_child._inspect_opening(object(), object(), EpubOpeningLimits()).outcome
