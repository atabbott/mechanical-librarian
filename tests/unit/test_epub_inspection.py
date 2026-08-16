"""Characterization of the private, source-attributed EPUB inspector contract."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

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


def test_rear_limit_disposition_is_boolean_success_only_and_canonical() -> None:
    source = Fingerprint("sha256", SchemaVersion("source-v1"), "c" * 64)
    inspection = EpubInspection(
        EpubInspectionOutcome.SUCCESS,
        source,
        rear_resource_limit=True,
    )
    serialized = inspection.to_json()
    assert EpubInspection.from_json(serialized) == inspection
    malformed = json.loads(serialized)
    malformed["rear_resource_limit"] = "rear_resource_limit"
    with pytest.raises(ValueError):
        EpubInspection.from_json(
            json.dumps(malformed, separators=(",", ":"), sort_keys=True)
        )
    with pytest.raises(ValueError):
        EpubInspection(
            EpubInspectionOutcome.RESOURCE_LIMIT, None, rear_resource_limit=True
        )


def test_isbn_support_is_distinct_from_observed_and_canonical_values() -> None:
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

    assert fact.canonical_value == "9780306406157"
    assert fact.supporting_context == "ISBN 978-0-306-40615-7 (eBook)"
    assert fact.outcome is EpubInspectionOutcome.SUCCESS


def test_invalid_checksum_fact_is_representable_for_the_live_filter() -> None:
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

    assert fact.canonical_value is None
    assert fact.checksum == "invalid"


def test_date_event_precision_and_contextual_credit_are_canonical() -> None:
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
    assert EpubInspection.from_json(inspection.to_json()) == inspection
    assert '"schema":"epub-inspection-v3"' in inspection.to_json()


@pytest.mark.parametrize(
    ("field", "event", "observed"),
    (
        pytest.param(
            EpubInspectionField.PUBLICATION_DATE,
            DateEvent.MANIFESTATION_PUBLICATION,
            "2001-02-03T04:05:06Z",
            id="publication_date_manifestation_utc_timestamp",
        ),
        pytest.param(
            EpubInspectionField.DATE_EVENT,
            DateEvent.WORK_FIRST_PUBLICATION,
            "2001-02-03T04:05:06+01:00:00",
            id="date_event_work_first_publication_offset_timestamp",
        ),
        pytest.param(
            EpubInspectionField.DATE_EVENT,
            DateEvent.COPYRIGHT,
            "2001-02-03",
            id="date_event_copyright_day",
        ),
    ),
)
def test_valid_date_facts_bind_observed_canonical_and_precision_at_ipc(
    field: EpubInspectionField, event: DateEvent, observed: str
) -> None:
    source = Fingerprint("sha256", SchemaVersion("source-v1"), "e" * 64)
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
    assert inspection == EpubInspection.from_json(inspection.to_json())
    for key, forged in (
        ("observed", "2002-04-05"),
        ("observed", "not-a-date"),
        ("canonical", "2002-04-05"),
        ("date_precision", DatePrecision.YEAR.value),
    ):
        document = json.loads(inspection.to_json())
        document["facts"][0][key] = forged
        with pytest.raises(ValueError, match="EPUB inspection protocol is invalid"):
            EpubInspection.from_json(
                json.dumps(
                    document,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )


_NONVALID_DATE_FIELDS = (
    (
        EpubInspectionField.PUBLICATION_DATE,
        DateEvent.MANIFESTATION_PUBLICATION,
        "publication_date",
    ),
    (EpubInspectionField.DATE_EVENT, DateEvent.COPYRIGHT, "date_event"),
)
_NONVALID_STATE_OBSERVATIONS = (
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


@pytest.mark.parametrize(
    ("field", "event", "state", "observed"),
    tuple(
        pytest.param(field, event, state, observed, id=f"{label}_{state}")
        for field, event, label in _NONVALID_DATE_FIELDS
        for state, observed in _NONVALID_STATE_OBSERVATIONS
    ),
)
def test_nonvalid_date_facts_cannot_retain_canonical_values_or_precision(
    field: EpubInspectionField, event: DateEvent, state: str, observed: str
) -> None:
    source = Fingerprint("sha256", SchemaVersion("source-v1"), "e" * 64)
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
    assert inspection == EpubInspection.from_json(inspection.to_json())
    for field_name, forged in (
        ("canonical_value", "2001"),
        ("date_precision", DatePrecision.YEAR),
    ):
        with pytest.raises(
            ValueError, match="EPUB inspection date precision is inconsistent"
        ):
            replace(fact, **{field_name: forged})
    for key, forged in (
        ("canonical", "2001"),
        ("date_precision", DatePrecision.YEAR.value),
    ):
        document = json.loads(inspection.to_json())
        document["facts"][0][key] = forged
        with pytest.raises(ValueError, match="EPUB inspection protocol is invalid"):
            EpubInspection.from_json(
                json.dumps(
                    document,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )

    for rejected_field, rejected_event, rejected_observed, canonical, precision in (
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
        with pytest.raises(
            ValueError, match="EPUB inspection date precision is inconsistent"
        ):
            EpubInspectionFact(
                rejected_field,
                source,
                rejected_observed,
                canonical,
                rejected_observed,
                "not_applicable",
                "not_applicable",
                "not_applicable",
                "package_metadata",
                date_event=rejected_event,
                date_precision=precision,
            )
