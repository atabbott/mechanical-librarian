"""Characterization of the private, source-attributed EPUB inspector contract."""

from __future__ import annotations

import unittest

from mechanical_librarian.domain.epub_inspection import (
    EpubInspectionFact,
    EpubInspectionField,
    EpubInspectionOutcome,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion


class EpubInspectionFactTests(unittest.TestCase):
    def test_isbn_support_is_distinct_from_observed_and_canonical_values(self) -> None:
        source = Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)

        fact = EpubInspectionFact(
            EpubInspectionField.ISBN,
            source,
            observed_value="ISBN 978-0-306-40615-7",
            canonical_value="9780306406157",
            supporting_context="ISBN 978-0-306-40615-7 (eBook)",
            checksum="valid_isbn_13",
            media="ebook",
            basis="opening_matter",
        )

        self.assertEqual(fact.canonical_value, "9780306406157")
        self.assertEqual(fact.supporting_context, "ISBN 978-0-306-40615-7 (eBook)")
        self.assertIs(fact.outcome, EpubInspectionOutcome.SUCCESS)

    def test_invalid_checksum_fact_is_representable_for_the_live_filter(self) -> None:
        source = Fingerprint("sha256", SchemaVersion("source-v1"), "b" * 64)

        fact = EpubInspectionFact(
            EpubInspectionField.ISBN,
            source,
            observed_value="9780306406158",
            canonical_value=None,
            supporting_context="ISBN 9780306406158",
            checksum="invalid",
            media="unknown",
            basis="opening_matter",
        )

        self.assertIsNone(fact.canonical_value)
        self.assertEqual(fact.checksum, "invalid")

