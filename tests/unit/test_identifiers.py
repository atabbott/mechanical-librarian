from __future__ import annotations

import unittest

from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    DomainValueError,
    ExternalItemId,
    Fingerprint,
    IdempotencyKey,
    LibraryId,
    SchemaVersion,
    TargetId,
)


class IdentifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetId(
            adapter=AdapterId("audiobookshelf"),
            connection=ConnectionId("primary"),
            library=LibraryId("books"),
            external_item=ExternalItemId("private/item-123"),
        )

    def test_internal_identifiers_reject_unsafe_or_ambiguous_values(self) -> None:
        for value in (
            "",
            "1starts-with-digit",
            "UPPER",
            "has space",
            "-starts-with-dash",
            "a" * 65,
        ):
            with self.subTest(value=value):
                with self.assertRaises(DomainValueError):
                    ConnectionId(value)

    def test_library_identifiers_accept_scoped_names_and_canonical_uuidv4(self) -> None:
        uuid = "01234567-89ab-4def-8123-456789abcdef"
        letter_leading_uuid = "a1234567-89ab-4def-8123-456789abcdef"

        self.assertEqual(LibraryId("synthetic-library").value, "synthetic-library")
        self.assertEqual(LibraryId(uuid).value, uuid)
        self.assertEqual(LibraryId(letter_leading_uuid).value, letter_leading_uuid)
        with self.assertRaises(DomainValueError):
            ConnectionId(uuid)

        for value in (
            "1library",
            "01234567-89ab-3def-8123-456789abcdef",
            "01234567-89ab-4def-7123-456789abcdef",
            "01234567-89AB-4def-8123-456789abcdef",
            "0123456789ab4def8123456789abcdef",
            "01234567-89ab-4def-8123-456789abcde/",
            "01234567-89ab-4def-8123-456789abcde\n",
            "0" * 65,
        ):
            with self.subTest(value=value):
                with self.assertRaises(DomainValueError):
                    LibraryId(value)

    def test_external_identifiers_are_scoped_and_redacted(self) -> None:
        other_target = TargetId(
            adapter=AdapterId("audiobookshelf"),
            connection=ConnectionId("secondary"),
            library=LibraryId("books"),
            external_item=ExternalItemId("private/item-123"),
        )

        self.assertNotEqual(self.target, other_target)
        self.assertNotIn("private/item-123", repr(self.target))

    def test_fingerprints_are_deterministic_and_key_order_independent(self) -> None:
        schema = SchemaVersion("snapshot-v1")

        first = Fingerprint.from_canonical_value({"b": 2, "a": [True, None]}, schema=schema)
        second = Fingerprint.from_canonical_value({"a": [True, None], "b": 2}, schema=schema)

        self.assertEqual(first, second)
        self.assertNotIn(first.digest, repr(first))

    def test_fingerprints_reject_non_finite_or_non_json_values(self) -> None:
        schema = SchemaVersion("snapshot-v1")
        for value in ({"value": float("nan")}, {"value": {1, 2}}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(DomainValueError):
                    Fingerprint.from_canonical_value(value, schema=schema)

    def test_versions_are_opaque_exact_compatibility_markers(self) -> None:
        current = SchemaVersion("snapshot-v1")

        self.assertTrue(current.is_compatible_with(SchemaVersion("snapshot-v1")))
        self.assertFalse(current.is_compatible_with(SchemaVersion("snapshot-v2")))
        self.assertFalse(current.is_compatible_with("snapshot-v1"))

    def test_idempotency_keys_include_target_scope_and_canonical_input(self) -> None:
        first = IdempotencyKey.from_canonical_input(
            operation="apply-metadata", target=self.target, value={"b": 2, "a": 1}
        )
        same = IdempotencyKey.from_canonical_input(
            operation="apply-metadata", target=self.target, value={"a": 1, "b": 2}
        )
        other_target = TargetId(
            adapter=AdapterId("audiobookshelf"),
            connection=ConnectionId("secondary"),
            library=LibraryId("books"),
            external_item=ExternalItemId("private/item-123"),
        )
        other = IdempotencyKey.from_canonical_input(
            operation="apply-metadata", target=other_target, value={"a": 1, "b": 2}
        )

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertNotIn("private/item-123", repr(first))


if __name__ == "__main__":
    unittest.main()
