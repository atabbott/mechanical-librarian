"""Reusable vendor-neutral contract assertions for target inventory readers."""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
)
from mechanical_librarian.application.ports import (
    FailureKind,
    OperationContext,
    PortFailure,
    PortResult,
)
from mechanical_librarian.domain.identifiers import LibraryId
from mechanical_librarian.domain.inventory import LibraryConnection
from mechanical_librarian.domain.inventory_v2 import (
    InventoryPass,
    InventorySnapshotV2,
    ScanBounds,
    ScanUsage,
)


_MUTATION_METHODS = (
    "apply",
    "create",
    "delete",
    "mutate",
    "remove",
    "set_cover",
    "set_metadata",
    "update",
    "upload",
    "write",
    "write_cover",
    "write_metadata",
)
_VENDOR_PAYLOAD_KEYS = frozenset(
    {
        "accessAllLibraries",
        "audioFiles",
        "coverPath",
        "ebookFile",
        "isInvalid",
        "isMissing",
        "librariesAccessible",
        "libraryId",
        "serverVersion",
        "updatedAt",
    }
)
_CANONICAL_OBSERVATION_KEYS = frozenset(
    {
        "adapter_version",
        "cover_identity",
        "fields",
        "file_identities",
        "lifecycle",
        "media_type",
        "normalizer_version",
        "schema",
        "service_version",
        "target",
        "warnings",
    }
)


@dataclass(frozen=True)
class TargetInventoryContractFixture:
    """One fully isolated reader invocation for the shared contract."""

    reader: object
    connection: LibraryConnection
    invalid_connection: LibraryConnection
    library: LibraryId
    bounds: ScanBounds
    context: OperationContext


class TargetInventoryContract:
    """Assert the application-owned inventory contract without vendor payloads."""

    def __init__(self, case: unittest.TestCase) -> None:
        self.case = case

    def require(self, fixture: TargetInventoryContractFixture) -> None:
        reader = fixture.reader
        verification = reader.verify_connection(
            fixture.connection,
            (fixture.library,),
            fixture.bounds,
            fixture.context,
        )
        self.case.assertIs(type(verification), InventoryConnectionVerification)
        self.case.assertIs(type(verification.usage), ScanUsage)
        self.case.assertIsNone(verification.failure)

        library_result = reader.verify_library(
            fixture.connection,
            fixture.library,
            fixture.bounds,
            fixture.context,
        )
        library_usage = self._success(library_result, ScanUsage)
        self._require_usage_within_bounds(library_usage, fixture.bounds)

        read_result = reader.read_pass(
            fixture.connection,
            fixture.library,
            fixture.bounds,
            fixture.context,
        )
        inventory_pass = self._success(read_result, InventoryPass)
        self._require_complete_bounded_pass(inventory_pass, fixture)

        failure_result = reader.verify_library(
            fixture.invalid_connection,
            fixture.library,
            fixture.bounds,
            fixture.context,
        )
        self.case.assertIs(type(failure_result), PortResult)
        self.case.assertIsNone(failure_result.value)
        self.case.assertIs(type(failure_result.failure), PortFailure)
        self.case.assertIs(failure_result.failure.kind, FailureKind.INVALID_REQUEST)

        for name in _MUTATION_METHODS:
            self.case.assertFalse(
                callable(getattr(reader, name, None)),
                f"{type(reader).__name__} must not expose mutation method {name}",
            )

    def _success(self, result: object, expected: type[object]) -> object:
        self.case.assertIs(type(result), PortResult)
        self.case.assertIsNone(result.failure)
        self.case.assertIs(type(result.value), expected)
        return result.value

    def _require_complete_bounded_pass(
        self,
        inventory_pass: InventoryPass,
        fixture: TargetInventoryContractFixture,
    ) -> None:
        self._require_usage_within_bounds(inventory_pass.usage, fixture.bounds)
        self.case.assertEqual(
            inventory_pass.usage.items,
            len(inventory_pass.snapshots),
        )
        self.case.assertLessEqual(len(inventory_pass.snapshots), fixture.bounds.max_items)
        targets = tuple(item.observation.target for item in inventory_pass.snapshots)
        self.case.assertEqual(len(targets), len(set(targets)))

        for snapshot in inventory_pass.snapshots:
            self.case.assertIs(type(snapshot), InventorySnapshotV2)
            observation = snapshot.observation
            self.case.assertEqual(observation.target.adapter, fixture.connection.adapter)
            self.case.assertEqual(observation.target.connection, fixture.connection.id)
            self.case.assertEqual(observation.target.library, fixture.library)
            canonical = json.loads(observation.canonical)
            self.case.assertEqual(
                frozenset(canonical),
                _CANONICAL_OBSERVATION_KEYS,
            )
            self.case.assertTrue(
                _VENDOR_PAYLOAD_KEYS.isdisjoint(_nested_keys(canonical))
            )

    def _require_usage_within_bounds(
        self,
        usage: ScanUsage,
        bounds: ScanBounds,
    ) -> None:
        self.case.assertLessEqual(usage.pages, bounds.max_pages)
        self.case.assertLessEqual(usage.items, bounds.max_items)
        self.case.assertLessEqual(usage.response_bytes, bounds.max_bytes)


def _nested_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_nested_keys(item) for item in value))
    return set()


__all__ = ["TargetInventoryContract", "TargetInventoryContractFixture"]
