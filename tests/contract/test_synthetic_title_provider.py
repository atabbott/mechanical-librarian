from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from mechanical_librarian.adapters.providers.synthetic_title import SyntheticTitleProvider
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    MetadataCandidate,
    OperationContext,
    PageRequest,
    ProviderFact,
    ProviderFactCode,
    ProviderQuery,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.title_proposals import PROVIDER_SCHEMA, scenario_named
from mechanical_librarian.domain.inventory import FieldName, MediaType


def context() -> OperationContext:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return OperationContext(
        Deadline(now + timedelta(seconds=5)),
        CancellationProbe(CancellationId("synthetic-contract"), lambda: False),
        ResourceBounds(16, 65_536),
        TimeProbe(lambda: now),
    )


def query() -> ProviderQuery:
    return ProviderQuery(
        MediaType.EBOOK,
        FieldName.TITLE,
        (ProviderFact(ProviderFactCode.TITLE, ("A Synthetic Book",)),),
        PROVIDER_SCHEMA,
    )


class SyntheticTitleProviderContractTests(unittest.TestCase):
    def test_candidate_is_typed_deterministic_and_bounded(self) -> None:
        provider = SyntheticTitleProvider(scenario_named("agree-empty"))
        first = provider.lookup(query(), PageRequest(None, 2), context())
        second = provider.lookup(query(), PageRequest(None, 2), context())
        self.assertEqual(first, second)
        self.assertIsNone(first.failure)
        self.assertEqual(len(first.value.items), 1)  # type: ignore[union-attr]
        self.assertIs(type(first.value.items[0]), MetadataCandidate)  # type: ignore[union-attr]

    def test_closed_typed_disabled_failed_empty_and_no_match_outcomes(self) -> None:
        expected = {
            "provider-disabled": FailureKind.UNSUPPORTED,
            "provider-failed": FailureKind.UNAVAILABLE,
            "provider-empty": None,
            "provider-no-match": FailureKind.NOT_FOUND,
        }
        for name, failure_kind in expected.items():
            with self.subTest(name=name):
                result = SyntheticTitleProvider(scenario_named(name)).lookup(
                    query(), PageRequest(None, 2), context()
                )
                if failure_kind is None:
                    self.assertIsNone(result.failure)
                    self.assertEqual(result.value.items, ())  # type: ignore[union-attr]
                else:
                    self.assertEqual(result.failure.kind, failure_kind)  # type: ignore[union-attr]

    def test_wrong_field_is_rejected_without_transport_authority(self) -> None:
        wrong = ProviderQuery(
            MediaType.EBOOK,
            FieldName.AUTHORS,
            (ProviderFact(ProviderFactCode.TITLE, ("A Synthetic Book",)),),
            PROVIDER_SCHEMA,
        )
        result = SyntheticTitleProvider(scenario_named("agree-empty")).lookup(
            wrong, PageRequest(None, 2), context()
        )
        self.assertEqual(result.failure.kind, FailureKind.INVALID_REQUEST)  # type: ignore[union-attr]
        self.assertFalse(hasattr(SyntheticTitleProvider, "apply"))

    def test_provider_behavior_is_not_exposed_as_application_scenario_data(self) -> None:
        scenario = scenario_named("provider-disabled")
        self.assertFalse(hasattr(scenario, "provider_behavior"))
        self.assertFalse(hasattr(scenario, "provider_title"))


if __name__ == "__main__":
    unittest.main()
