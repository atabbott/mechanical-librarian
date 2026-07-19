from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
)


class HardcoverIdentityBoundaryTests(unittest.TestCase):
    def bounded_body(
        self, lines: tuple[bytes, ...], chunks: tuple[bytes, ...]
    ) -> bytes | IdentityAttemptFailureKind:
        return adapter._bounded_body(
            lines, adapter.StructuralIdentityBodyReader(iter(chunks))
        )

    def row(self, *, title: str = "Synthetic Provider Title") -> dict[str, object]:
        return {
            "id": 1001,
            "book_id": 2001,
            "canonical_id": None,
            "isbn_10": "0000000019",
            "isbn_10_valid": True,
            "isbn_13": "9780000000019",
            "isbn_13_valid": True,
            "isbns_match": True,
            "reading_format_id": 4,
            "state": "normalized",
            "compilation": False,
            "book": {
                "id": 2001,
                "canonical_id": None,
                "book_status_id": 1,
                "state": "processed",
                "title": title,
                "parent_book_id": None,
                "compilation": False,
                "is_partial_book": False,
            },
        }

    def payload(self, *, title: str = "Synthetic Provider Title") -> bytes:
        return json.dumps(
            {"data": {"editions": [self.row(title=title)]}},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def test_response_byte_limit_minus_exact_plus_and_content_length(self) -> None:
        content_type = (b"Content-Type: application/json\r\n",)
        for size in (65_535, 65_536):
            with self.subTest(size=size):
                payload = b" " * size
                self.assertEqual(
                    self.bounded_body(content_type, (payload[:1], payload[1:])),
                    payload,
                )
        self.assertIs(
            self.bounded_body(content_type, (b" " * 65_537,)),
            IdentityAttemptFailureKind.OVERSIZED,
        )

        for declared, expected in (
            (b"65535", IdentityAttemptFailureKind.MALFORMED),
            (b"65536", IdentityAttemptFailureKind.MALFORMED),
            (b"65537", IdentityAttemptFailureKind.OVERSIZED),
            (b"100000", IdentityAttemptFailureKind.OVERSIZED),
            (b"065536", IdentityAttemptFailureKind.MALFORMED),
        ):
            with self.subTest(declared=declared):
                result = self.bounded_body(
                    content_type + (b"Content-Length: " + declared + b"\r\n",),
                    (b"{}",),
                )
                self.assertIs(result, expected)

    def test_declared_oversize_closes_without_reading_body(self) -> None:
        reader = adapter.StructuralIdentityBodyReader(iter((b"must-not-read",)))
        result = adapter._bounded_body(
            (
                b"Content-Type: application/json\r\n",
                b"Content-Length: 65537\r\n",
            ),
            reader,
        )
        self.assertIs(result, IdentityAttemptFailureKind.OVERSIZED)
        self.assertEqual(reader.read_count, 0)
        self.assertTrue(reader.closed)

    def test_streaming_limit_stops_before_advancing_past_cap(self) -> None:
        produced: list[int] = []

        def chunks() -> object:
            for chunk in (b"A" * 65_536, b"B", b"must-not-advance"):
                produced.append(len(chunk))
                yield chunk

        reader = adapter.StructuralIdentityBodyReader(chunks())
        result = adapter._bounded_body(
            (b"Content-Type: application/json\r\n",), reader
        )
        self.assertIs(result, IdentityAttemptFailureKind.OVERSIZED)
        self.assertEqual(produced, [65_536, 1])
        self.assertEqual(reader.read_count, 2)
        self.assertTrue(reader.closed)

    def test_stream_reader_failures_are_closed_and_value_free(self) -> None:
        class FailedReader:
            closed = False

            def read_chunk(self, max_bytes: int) -> bytes | None:
                raise RuntimeError("private-reader-detail")

            def close(self) -> None:
                self.closed = True

        reader = FailedReader()
        result = adapter._bounded_body(
            (b"Content-Type: application/json\r\n",), reader
        )
        self.assertIs(result, IdentityAttemptFailureKind.UNAVAILABLE)
        self.assertTrue(reader.closed)
        self.assertNotIn("private-reader-detail", repr(result))

    def test_reader_over_return_is_rejected_before_payload_copy(self) -> None:
        class ViolatingReader:
            closed = False
            requests: list[int] = []

            def read_chunk(self, max_bytes: int) -> bytes | None:
                self.requests.append(max_bytes)
                return b"A" * (max_bytes + 1)

            def close(self) -> None:
                self.closed = True

        reader = ViolatingReader()
        payload = bytearray()
        with patch.object(
            adapter, "bytearray", return_value=payload, create=True
        ):
            result = adapter._bounded_body(
                (b"Content-Type: application/json\r\n",), reader
            )
        self.assertIs(result, IdentityAttemptFailureKind.OVERSIZED)
        self.assertEqual(reader.requests, [65_537])
        self.assertTrue(reader.closed)
        self.assertEqual(payload, b"")

    def test_compliant_reader_receives_only_remaining_plus_one(self) -> None:
        class CappedReader:
            def __init__(self) -> None:
                self.remaining = 65_537
                self.requests: list[int] = []
                self.closed = False

            def read_chunk(self, max_bytes: int) -> bytes | None:
                self.requests.append(max_bytes)
                size = min(max_bytes, self.remaining)
                self.remaining -= size
                return b"A" * size if size else None

            def close(self) -> None:
                self.closed = True

        reader = CappedReader()
        result = adapter._bounded_body(
            (b"Content-Type: application/json\r\n",), reader
        )
        self.assertIs(result, IdentityAttemptFailureKind.OVERSIZED)
        self.assertEqual(reader.requests, [65_537])
        self.assertTrue(reader.closed)

    def test_structural_reader_close_finalizes_underlying_generator(self) -> None:
        finalized = False

        def chunks() -> object:
            nonlocal finalized
            try:
                yield b"A" * 65_537
                raise AssertionError("producer advanced after oversize")
            finally:
                finalized = True

        reader = adapter.StructuralIdentityBodyReader(chunks())
        result = adapter._bounded_body(
            (b"Content-Type: application/json\r\n",), reader
        )
        self.assertIs(result, IdentityAttemptFailureKind.OVERSIZED)
        self.assertTrue(reader.closed)
        self.assertTrue(finalized)

    def test_depth_limit_minus_exact_plus_and_string_aware_scan(self) -> None:
        def nested(depth: int) -> str:
            return '{"x":' * depth + "0" + "}" * depth

        self.assertTrue(adapter._depth_allowed(nested(7)))
        self.assertTrue(adapter._depth_allowed(nested(8)))
        self.assertFalse(adapter._depth_allowed(nested(9)))
        self.assertTrue(
            adapter._depth_allowed(
                json.dumps({"x": "quoted { [ ] } and escaped \\\" {"})
            )
        )

    def test_node_member_and_list_limits_minus_exact_plus(self) -> None:
        for size, expected in ((95, True), (96, True), (97, False)):
            with self.subTest(members=size):
                value = {f"k{index}": None for index in range(size)}
                self.assertIs(adapter._structure_allowed(value), expected)
        for size, expected in ((3, True), (4, True), (5, False)):
            with self.subTest(elements=size):
                self.assertIs(adapter._structure_allowed([None] * size), expected)
        with patch.object(adapter, "MAX_JSON_MEMBERS", 256), patch.object(
            adapter, "MAX_JSON_LIST_ELEMENTS", 256
        ):
            for size, expected in ((126, True), (127, True), (128, False)):
                with self.subTest(child_nodes=size):
                    # Root plus one node for each member value.
                    value = {f"k{index}": None for index in range(size)}
                    self.assertIs(adapter._structure_allowed(value), expected)

    def test_string_scalar_and_utf8_limits_minus_exact_plus(self) -> None:
        for size, expected in ((511, True), (512, True), (513, False)):
            with self.subTest(scalars=size):
                self.assertIs(adapter._structure_allowed("A" * size), expected)
        with patch.object(adapter, "MAX_STRING_SCALARS", 4_096):
            for size, expected in ((2_047, True), (2_048, True), (2_049, False)):
                with self.subTest(utf8_bytes=size):
                    self.assertIs(adapter._structure_allowed("A" * size), expected)

        values = {f"k{index}": "A" * 512 for index in range(7)}
        for tail, expected in ((511, True), (512, True), (513, False)):
            with self.subTest(total_scalars=3_584 + tail):
                value = dict(values)
                value["tail"] = "A" * tail
                self.assertIs(
                    adapter._structure_allowed(value),
                    expected,
                )
        with patch.object(adapter, "MAX_TOTAL_STRING_SCALARS", 20_000), patch.object(
            adapter, "MAX_STRING_SCALARS", 20_000
        ), patch.object(adapter, "MAX_STRING_BYTES", 20_000):
            for size, expected in ((16_383, True), (16_384, True), (16_385, False)):
                with self.subTest(total_utf8_bytes=size):
                    self.assertIs(adapter._structure_allowed("A" * size), expected)

    def test_integer_token_limits_and_number_kinds(self) -> None:
        for token, expected in (
            ("-2147483649", False),
            ("-2147483648", True),
            ("2147483647", True),
            ("2147483648", False),
        ):
            with self.subTest(token=token):
                try:
                    value = adapter._parse_int32(token)
                except ValueError:
                    self.assertFalse(expected)
                else:
                    self.assertTrue(expected)
                    self.assertEqual(str(value), token)
        for token in ("1.0", "1e0", "NaN", "Infinity"):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    adapter._reject_number(token)

    def test_title_and_escape_boundaries_are_enforced_after_one_decode(self) -> None:
        for title, expected in (
            ("A" * 511, True),
            ("A" * 512, True),
            ("A" * 513, False),
            ("😀" * 512, True),
            ("😀" * 513, False),
            ("é", True),
            ("e\u0301", False),
            (" surrounded ", False),
            ("A\u202eB", False),
        ):
            with self.subTest(scalars=len(title)):
                result = adapter._parse_observations(self.payload(title=title))
                self.assertIs(type(result) is tuple, expected)
        escaped = self.payload(title="A" * 512).replace(
            b'"' + b"A" * 512 + b'"',
            b'"' + b"\\u0041" * 512 + b'"',
        )
        self.assertIs(type(adapter._parse_observations(escaped)), tuple)
        escaped_plus = escaped.replace(
            b'"' + b"\\u0041" * 512 + b'"',
            b'"' + b"\\u0041" * 513 + b'"',
        )
        self.assertIs(
            adapter._parse_observations(escaped_plus),
            IdentityAttemptFailureKind.MALFORMED,
        )


if __name__ == "__main__":
    unittest.main()
