from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
    IsbnIdentityQuery,
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

    def test_transport_metadata_caps_reject_before_address_parsing(self) -> None:
        public = ("1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9")
        for count, accepted in ((3, True), (4, True), (5, False)):
            with self.subTest(resolver_cardinality=count):
                result = adapter._validated_addresses(public[:count])
                self.assertIs(result is not None, accepted)

        for size, bounded in ((38, True), (39, True), (40, False)):
            with self.subTest(ip_scalars=size):
                self.assertIs(adapter._bounded_ip_text("A" * size), bounded)
        self.assertFalse(adapter._bounded_ip_text("é" * 19))

        with patch.object(adapter.ipaddress, "ip_address") as parse:
            self.assertIsNone(adapter._validated_addresses(("A" * 40,)))
            self.assertFalse(adapter._peer_equal("A" * 40, "1.1.1.1"))
        parse.assert_not_called()

        prefix = b"HTTP/1.1 200 "
        for size, accepted in ((127, True), (128, True), (129, False)):
            with self.subTest(status_bytes=size):
                status = prefix + b"A" * (size - len(prefix) - 2) + b"\r\n"
                self.assertIs(adapter._parse_status(status) is not None, accepted)

    def test_private_parse_failures_clear_context_and_traceback_locals(self) -> None:
        forbidden_text = "private-isbn-title-token-path"
        forbidden_bytes = b"\xff" + forbidden_text.encode()

        def assert_clean(error: BaseException, expected: str) -> None:
            self.assertEqual(error.args, (expected,))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            self.assertFalse(getattr(error, "__notes__", ()))
            traceback = error.__traceback__
            while traceback is not None:
                frame = traceback.tb_frame
                if "/src/mechanical_librarian/" in frame.f_code.co_filename:
                    for value in frame.f_locals.values():
                        self.assertIsNot(value, forbidden_bytes)
                        self.assertNotEqual(value, forbidden_text)
                        self.assertNotIn(forbidden_text, repr(value))
                        if isinstance(value, UnicodeDecodeError):
                            self.assertIsNot(value.object, forbidden_bytes)
                traceback = traceback.tb_next

        with self.assertRaises(ValueError) as body_failure:
            adapter.identity_query_from_canonical_body(forbidden_bytes)
        assert_clean(
            body_failure.exception,
            "Hardcover identity request body is invalid",
        )

        with self.assertRaises(ValueError) as query_failure:
            IsbnIdentityQuery(forbidden_text, None)
        assert_clean(query_failure.exception, "ISBN identity query is malformed")

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

    def test_raw_header_field_values_reject_every_control_injection(self) -> None:
        valid = adapter._parse_headers(
            (
                b"Content-Type: application/json\r\n",
                b"X-Synthetic:\tA B\tC\r\n",
                b"X-Extended: \x80\xff\r\n",
            )
        )
        self.assertIs(type(valid), dict)
        hostile = (
            (b"X-Test: A\r\nInjected: B\r\n",),
            (b"X-Test: A\rB\r\n",),
            (b"X-Test: A\nB\r\n",),
            (b"X-Test: A\x00B\r\n",),
            (b"X-Test: A\x01B\r\n",),
            (b"X-Test: A\x0bB\r\n",),
            (b"X-Test: A\x1fB\r\n",),
            (b"X-Test: A\x7fB\r\n",),
            (b"X-Test: A\r\n Content-Type: text/plain\r\n",),
            (
                b"Content-Type: application/json\r\n",
                b"Content-Type: text/plain\r\n",
            ),
        )
        for lines in hostile:
            with self.subTest(lines=lines):
                self.assertIs(
                    adapter._parse_headers(lines),
                    IdentityAttemptFailureKind.MALFORMED,
                )

    def test_body_callable_discovery_is_typed_closed_and_value_free(self) -> None:
        class CloseTracker:
            close_calls = 0

            def close(self) -> None:
                self.close_calls += 1

        class MissingRead(CloseTracker):
            pass

        class NoncallableRead(CloseTracker):
            read_chunk = False

        def hostile_read_property(error: BaseException) -> type[CloseTracker]:
            class HostileRead(CloseTracker):
                @property
                def read_chunk(self) -> object:
                    raise error

            return HostileRead

        cases = (
            (MissingRead(), IdentityAttemptFailureKind.MALFORMED),
            (NoncallableRead(), IdentityAttemptFailureKind.MALFORMED),
            (
                hostile_read_property(RuntimeError("private-read-access"))(),
                IdentityAttemptFailureKind.UNAVAILABLE,
            ),
            (
                hostile_read_property(SystemExit("private-read-base"))(),
                IdentityAttemptFailureKind.UNAVAILABLE,
            ),
            (
                hostile_read_property(KeyboardInterrupt("private-read-cancel"))(),
                IdentityAttemptFailureKind.CANCELLED,
            ),
        )
        for reader, expected in cases:
            with self.subTest(expected=expected):
                result = adapter._bounded_body(
                    (b"Content-Type: application/json\r\n",), reader
                )
                self.assertIs(result, expected)
                self.assertEqual(reader.close_calls, 1)
                self.assertNotIn("private-", repr(result))

    def test_body_read_invocation_maps_every_base_failure_and_closes_once(self) -> None:
        class FailedRead:
            def __init__(self, error: BaseException) -> None:
                self.error = error
                self.close_calls = 0

            def read_chunk(self, max_bytes: int) -> bytes | None:
                raise self.error

            def close(self) -> None:
                self.close_calls += 1

        for error, expected in (
            (RuntimeError("private-read-call"), IdentityAttemptFailureKind.UNAVAILABLE),
            (SystemExit("private-read-base"), IdentityAttemptFailureKind.UNAVAILABLE),
            (
                KeyboardInterrupt("private-read-cancel"),
                IdentityAttemptFailureKind.CANCELLED,
            ),
        ):
            with self.subTest(error=type(error).__name__):
                reader = FailedRead(error)
                result = adapter._bounded_body(
                    (b"Content-Type: application/json\r\n",), reader
                )
                self.assertIs(result, expected)
                self.assertEqual(reader.close_calls, 1)
                self.assertNotIn("private-", repr(result))

    def test_close_discovery_and_invocation_never_override_body_results(self) -> None:
        class MissingClose:
            def read_chunk(self, max_bytes: int) -> None:
                return None

        class NoncallableClose(MissingClose):
            close = False

        def hostile_close_property(error: BaseException) -> type[MissingClose]:
            class HostileClose(MissingClose):
                @property
                def close(self) -> object:
                    raise error

            return HostileClose

        for reader in (
            MissingClose(),
            NoncallableClose(),
            hostile_close_property(RuntimeError("private-close-access"))(),
            hostile_close_property(SystemExit("private-close-base"))(),
            hostile_close_property(KeyboardInterrupt("private-close-cancel"))(),
        ):
            with self.subTest(reader=type(reader).__name__):
                self.assertIs(
                    adapter._bounded_body(
                        (b"Content-Type: application/json\r\n",), reader
                    ),
                    IdentityAttemptFailureKind.MALFORMED,
                )
                adapter._close_body(reader)

        class RaisingClose:
            def __init__(
                self,
                values: list[bytes | None | BaseException],
                close_error: BaseException,
            ) -> None:
                self.values = iter(values)
                self.close_error = close_error
                self.close_calls = 0

            def read_chunk(self, max_bytes: int) -> bytes | None:
                value = next(self.values)
                if isinstance(value, BaseException):
                    raise value
                return value

            def close(self) -> None:
                self.close_calls += 1
                raise self.close_error

        paths = (
            ((), (b"malformed-header",), IdentityAttemptFailureKind.MALFORMED),
            (
                (b"Content-Type: application/json\r\n",),
                (RuntimeError("private-read"),),
                IdentityAttemptFailureKind.UNAVAILABLE,
            ),
            (
                (b"Content-Type: application/json\r\n",),
                (b"A" * 65_537,),
                IdentityAttemptFailureKind.OVERSIZED,
            ),
            (
                (b"Content-Type: application/json\r\n",),
                (b"{}", None),
                b"{}",
            ),
        )
        for close_error in (
            RuntimeError("private-close-call"),
            SystemExit("private-close-base"),
            KeyboardInterrupt("private-close-cancel"),
        ):
            for lines, values, expected in paths:
                with self.subTest(
                    close_error=type(close_error).__name__, expected=expected
                ):
                    reader = RaisingClose(list(values), close_error)
                    result = adapter._bounded_body(lines, reader)
                    self.assertEqual(result, expected)
                    self.assertEqual(reader.close_calls, 1)

        class HostileIterator:
            def __iter__(self) -> HostileIterator:
                return self

            def __next__(self) -> bytes:
                return b"must-not-read"

            @property
            def close(self) -> object:
                raise KeyboardInterrupt("private-producer-close")

        structural = adapter.StructuralIdentityBodyReader(HostileIterator())
        self.assertFalse(structural.close())
        self.assertFalse(structural.closed)
        self.assertTrue(structural.read_disabled)
        self.assertTrue(structural.finalization_attempted)
        self.assertFalse(structural.abort())
        self.assertFalse(structural.closed)

    def test_structural_release_state_requires_confirmed_underlying_finalization(self) -> None:
        finalized = False

        def producer() -> object:
            nonlocal finalized
            try:
                yield b"private-body"
            finally:
                finalized = True

        successful = adapter.StructuralIdentityBodyReader(producer())
        self.assertFalse(successful.read_disabled)
        self.assertFalse(successful.finalization_attempted)
        self.assertFalse(successful.closed)
        self.assertEqual(successful.read_chunk(64), b"private-body")
        self.assertTrue(successful.close())
        self.assertTrue(finalized)
        self.assertTrue(successful.read_disabled)
        self.assertTrue(successful.finalization_attempted)
        self.assertTrue(successful.closed)
        self.assertTrue(successful.close())

        class RetryProducer:
            def __init__(self) -> None:
                self.calls = 0

            def __iter__(self) -> RetryProducer:
                return self

            def __next__(self) -> bytes:
                return b"must-not-read"

            def close(self) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise SystemExit("private-first-close")

        retry_producer = RetryProducer()
        retry = adapter.StructuralIdentityBodyReader(retry_producer)
        self.assertFalse(retry.close())
        self.assertFalse(retry.closed)
        self.assertTrue(retry.read_disabled)
        self.assertTrue(retry.finalization_attempted)
        self.assertTrue(retry.abort())
        self.assertTrue(retry.closed)
        self.assertEqual(retry_producer.calls, 2)

        exhausted = adapter.StructuralIdentityBodyReader(iter(()))
        self.assertIsNone(exhausted.read_chunk(1))
        self.assertTrue(exhausted.closed)
        self.assertTrue(exhausted.read_disabled)
        self.assertTrue(exhausted.finalization_attempted)

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
