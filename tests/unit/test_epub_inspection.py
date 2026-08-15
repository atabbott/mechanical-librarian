"""Characterization of the private, source-attributed EPUB inspector contract."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    DatePrecision,
    EpubInspection,
    EpubInspectionFact,
    EpubInspectionField,
    EpubInspectionOutcome,
    normalize_date_value,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion


class EpubInspectionFactTests(unittest.TestCase):
    def test_rear_limit_disposition_is_boolean_success_only_and_canonical(self) -> None:
        source = Fingerprint("sha256", SchemaVersion("source-v1"), "c" * 64)
        inspection = EpubInspection(
            EpubInspectionOutcome.SUCCESS,
            source,
            rear_resource_limit=True,
        )
        serialized = inspection.to_json()
        self.assertEqual(EpubInspection.from_json(serialized), inspection)
        malformed = json.loads(serialized)
        malformed["rear_resource_limit"] = "rear_resource_limit"
        with self.assertRaises(ValueError):
            EpubInspection.from_json(json.dumps(malformed, separators=(",", ":"), sort_keys=True))
        with self.assertRaises(ValueError):
            EpubInspection(EpubInspectionOutcome.RESOURCE_LIMIT, None, rear_resource_limit=True)

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
            classification_basis="ebook",
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
            classification_basis="unlabelled",
            basis="opening_matter",
        )

        self.assertIsNone(fact.canonical_value)
        self.assertEqual(fact.checksum, "invalid")

    def test_date_event_precision_and_contextual_credit_are_canonical(self) -> None:
        source = Fingerprint("sha256", SchemaVersion("source-v1"), "d" * 64)
        inspection = EpubInspection(
            EpubInspectionOutcome.SUCCESS,
            source,
            (
                EpubInspectionFact(
                    EpubInspectionField.PUBLICATION_DATE,
                    source,
                    "2024-03",
                    "2024-03",
                    "This ebook edition published 2024-03",
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "opening_matter",
                    date_event=DateEvent.MANIFESTATION_PUBLICATION,
                    date_precision=DatePrecision.MONTH,
                ),
                EpubInspectionFact(
                    EpubInspectionField.CONTEXTUAL_CREDIT,
                    source,
                    "Tool House",
                    "Tool House",
                    "EPUB conversion by Tool House",
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "opening_matter",
                    role="conversion",
                ),
            ),
        )
        self.assertEqual(EpubInspection.from_json(inspection.to_json()), inspection)
        self.assertIn('"schema":"epub-inspection-v3"', inspection.to_json())

    def test_valid_date_facts_bind_observed_canonical_and_precision_at_ipc(
        self,
    ) -> None:
        source = Fingerprint(
            "sha256", SchemaVersion("source-v1"), "e" * 64
        )
        valid_cases = (
            (
                EpubInspectionField.PUBLICATION_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                "2001-02-03T04:05:06Z",
            ),
            (
                EpubInspectionField.DATE_EVENT,
                DateEvent.WORK_FIRST_PUBLICATION,
                "2001-02-03T04:05:06+01:00:00",
            ),
            (
                EpubInspectionField.DATE_EVENT,
                DateEvent.COPYRIGHT,
                "2001-02-03",
            ),
        )
        for field, event, observed in valid_cases:
            canonical, precision = normalize_date_value(observed)
            fact = EpubInspectionFact(
                field,
                source,
                observed,
                canonical,
                observed,
                "not_applicable",
                "not_applicable",
                "not_applicable",
                "package_metadata",
                date_event=event,
                date_precision=precision,
            )
            inspection = EpubInspection(
                EpubInspectionOutcome.SUCCESS,
                source,
                (fact,),
            )
            with self.subTest(field=field, event=event, observed=observed):
                self.assertEqual(
                    inspection,
                    EpubInspection.from_json(inspection.to_json()),
                )
                for key, forged in (
                    ("observed", "2002-04-05"),
                    ("observed", "not-a-date"),
                    ("canonical", "2002-04-05"),
                    ("date_precision", DatePrecision.YEAR.value),
                ):
                    document = json.loads(inspection.to_json())
                    document["facts"][0][key] = forged
                    with self.assertRaisesRegex(
                        ValueError, "EPUB inspection protocol is invalid"
                    ):
                        EpubInspection.from_json(
                            json.dumps(
                                document,
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        )

    def test_nonvalid_date_facts_cannot_retain_canonical_values_or_precision(
        self,
    ) -> None:
        source = Fingerprint(
            "sha256", SchemaVersion("source-v1"), "e" * 64
        )
        date_cases = (
            (
                EpubInspectionField.PUBLICATION_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
            ),
            (EpubInspectionField.DATE_EVENT, DateEvent.COPYRIGHT),
        )
        state_observations = (
            ("malformed", "not-a-date"),
            (
                "oversized",
                "oversized-source-text-sha256:" + "a" * 64,
            ),
            (
                "unsupported",
                "unsafe-source-text-sha256:" + "b" * 64,
            ),
            ("untyped", "untyped-date"),
            ("inapplicable", "2001"),
        )
        for field, event in date_cases:
            for state, observed in state_observations:
                fact = EpubInspectionFact(
                    field,
                    source,
                    observed,
                    None,
                    observed,
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "package_metadata",
                    state=state,
                    date_event=event,
                )
                inspection = EpubInspection(
                    EpubInspectionOutcome.SUCCESS,
                    source,
                    (fact,),
                )
                with self.subTest(field=field, event=event, state=state):
                    self.assertEqual(
                        inspection,
                        EpubInspection.from_json(inspection.to_json()),
                    )
                    for field_name, forged in (
                        ("canonical_value", "2001"),
                        ("date_precision", DatePrecision.YEAR),
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "EPUB inspection date precision is inconsistent",
                        ):
                            replace(fact, **{field_name: forged})
                    for key, forged in (
                        ("canonical", "2001"),
                        ("date_precision", DatePrecision.YEAR.value),
                    ):
                        document = json.loads(inspection.to_json())
                        document["facts"][0][key] = forged
                        with self.assertRaisesRegex(
                            ValueError,
                            "EPUB inspection protocol is invalid",
                        ):
                            EpubInspection.from_json(
                                json.dumps(
                                    document,
                                    separators=(",", ":"),
                                    sort_keys=True,
                                )
                            )

        for field, event, observed, canonical, precision in (
            (
                EpubInspectionField.PUBLICATION_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                "2001",
                "2002",
                DatePrecision.YEAR,
            ),
            (
                EpubInspectionField.DATE_EVENT,
                DateEvent.CREATION,
                "not-a-date",
                "2001",
                DatePrecision.YEAR,
            ),
            (
                EpubInspectionField.DATE_EVENT,
                DateEvent.COPYRIGHT,
                "2001-02-03T04:05:06Z",
                "2001-02-03T04:05:06Z",
                DatePrecision.TIMESTAMP,
            ),
        ):
            with (
                self.subTest(
                    rejection=(field, event, observed, canonical, precision)
                ),
                self.assertRaisesRegex(
                    ValueError,
                    "EPUB inspection date precision is inconsistent",
                ),
            ):
                EpubInspectionFact(
                    field,
                    source,
                    observed,
                    canonical,
                    observed,
                    "not_applicable",
                    "not_applicable",
                    "not_applicable",
                    "package_metadata",
                    date_event=event,
                    date_precision=precision,
                )
