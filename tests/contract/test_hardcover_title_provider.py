from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from mechanical_librarian.adapters.providers import hardcover_title
from mechanical_librarian.adapters.providers.hardcover_title import (
    HARDCOVER_QUERY,
    HardcoverTitleProvider,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    PageRequest,
    ProviderFact,
    ProviderFactCode,
    ProviderQuery,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.title_proposals import PROVIDER_SCHEMA
from mechanical_librarian.domain.inventory import (
    FieldName,
    MediaType,
    SecretReference,
)
from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.provider_secrets import (
    PrivateFileProviderCredentials,
)
from tests.support.hardcover_fixtures import (
    OBSERVED_LIVE_SHAPE_STATS,
    hardcover_live_search_payload,
)


PUBLIC_IP = "93.184.216.34"
TITLE = "A Synthetic Book"


class _Peer:
    def __init__(self, address: str = PUBLIC_IP) -> None:
        self.address = address

    def get_extra_info(self, name: str):
        return (self.address, 443) if name == "server_addr" else None


def _response(
    payload: object,
    *,
    status: int = 200,
    peer: str = PUBLIC_IP,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body = (
        payload
        if type(payload) is bytes
        else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    )
    selected_headers = {"content-type": "application/json"}
    if headers:
        selected_headers.update(headers)
    return httpx.Response(
        status,
        headers=selected_headers,
        content=body,
        extensions={"network_stream": _Peer(peer)},
    )


def _success(result_title: str | None = TITLE) -> dict[str, object]:
    return hardcover_live_search_payload(TITLE, result_title)


def _results(payload: dict[str, object]) -> dict[str, object]:
    return payload["data"]["search"]["results"]  # type: ignore[index,return-value]


def _hit(payload: dict[str, object]) -> dict[str, object]:
    return _results(payload)["hits"][0]  # type: ignore[index,return-value]


def _path(value: object, *parts: object) -> object:
    current = value
    for part in parts:
        current = current[part]  # type: ignore[index]
    return current


def _context(
    *,
    cancelled: bool = False,
    deadline_seconds: float = 5,
    cancellation_reader=None,
) -> OperationContext:
    now = datetime.now(timezone.utc)
    return OperationContext(
        Deadline(now + timedelta(seconds=deadline_seconds)),
        CancellationProbe(
            CancellationId("hardcover-contract"),
            (lambda: cancelled) if cancellation_reader is None else cancellation_reader,
        ),
        ResourceBounds(16, 65_536),
        TimeProbe(lambda: now),
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        MediaType.EBOOK,
        FieldName.TITLE,
        (ProviderFact(ProviderFactCode.TITLE, (TITLE,)),),
        PROVIDER_SCHEMA,
    )


class HardcoverTitleProviderContractTests(unittest.TestCase):
    def _provider(
        self,
        handler,
        *,
        resolver=lambda _host, _port, _budget: (PUBLIC_IP,),
    ):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        secret = Path(directory.name) / "provider-token"
        secret.write_text("provider-token-sentinel", encoding="utf-8")
        secret.chmod(0o600)
        client = PrivateFileProviderCredentials().hardcover_client(
            SecretHandle(SecretReference(str(secret)), SecretScope.METADATA_PROVIDER),
            transport=httpx.MockTransport(handler),
        )
        self.addCleanup(lambda: asyncio.run(client.aclose()))
        if resolver is None:
            return HardcoverTitleProvider(client)
        return HardcoverTitleProvider(client, resolver=resolver)

    def _lookup_payload(self, payload: object):
        return self._provider(lambda _request: _response(payload)).lookup(
            _query(), PageRequest(None, 1), _context()
        )

    def test_exact_pinned_request_and_closed_candidate(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _response(_success())

        result = self._provider(handler).lookup(_query(), PageRequest(None, 1), _context())
        self.assertIsNone(result.failure)
        self.assertEqual(len(result.value.items), 1)  # type: ignore[union-attr]
        candidate = result.value.items[0]  # type: ignore[union-attr]
        self.assertEqual(candidate.value.values, (TITLE,))
        self.assertEqual(candidate.provenance.source_id, "hardcover-title-poc")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), f"https://{PUBLIC_IP}/v1/graphql")
        self.assertEqual(request.headers["host"], "api.hardcover.app")
        self.assertEqual(
            request.headers["authorization"], "Bearer provider-token-sentinel"
        )
        self.assertEqual(request.headers["accept-encoding"], "identity")
        self.assertEqual(request.headers["connection"], "close")
        self.assertEqual(request.extensions["sni_hostname"], "api.hardcover.app")
        body = json.loads(request.content)
        self.assertEqual(body["operationName"], "HardcoverTitlePoc")
        self.assertEqual(body["query"], HARDCOVER_QUERY)
        self.assertEqual(body["variables"], {"title": TITLE})

    def test_sanitized_fixture_matches_observed_live_shape_and_rich_values_are_discarded(
        self,
    ) -> None:
        payload = _success("A Different Synthetic Book")
        self.assertEqual(
            hardcover_title._json_shape(payload),
            (
                OBSERVED_LIVE_SHAPE_STATS["nodes"],
                OBSERVED_LIVE_SHAPE_STATS["depth"],
                OBSERVED_LIVE_SHAPE_STATS["max_object_keys"],
                OBSERVED_LIVE_SHAPE_STATS["max_list_items"],
                OBSERVED_LIVE_SHAPE_STATS["max_string_length"],
            ),
        )
        result = self._lookup_payload(payload)
        self.assertIsNone(result.failure)
        candidate = result.value.items[0]  # type: ignore[union-attr]
        self.assertEqual(candidate.value.values, ("A Different Synthetic Book",))
        self.assertNotIn("synthetic-live-book-id", repr(candidate))
        self.assertNotIn("Synthetic Author", repr(candidate))

    def test_every_observed_object_is_closed_to_unknown_fields(self) -> None:
        paths = (
            (),
            ("data",),
            ("data", "search"),
            ("data", "search", "results"),
            ("data", "search", "results", "request_params"),
            ("data", "search", "results", "hits", 0),
            ("data", "search", "results", "hits", 0, "document"),
            (
                "data",
                "search",
                "results",
                "hits",
                0,
                "document",
                "contributions",
                0,
            ),
            (
                "data",
                "search",
                "results",
                "hits",
                0,
                "document",
                "contributions",
                0,
                "author",
            ),
            ("data", "search", "results", "hits", 0, "document", "image"),
            ("data", "search", "results", "hits", 0, "highlight"),
            ("data", "search", "results", "hits", 0, "highlight", "title"),
            ("data", "search", "results", "hits", 0, "highlights", 0),
            ("data", "search", "results", "hits", 0, "text_match_info"),
        )
        for path in paths:
            with self.subTest(path=path):
                payload = _success()
                target = _path(payload, *path)
                target["unknown"] = "rejected"  # type: ignore[index]
                self.assertIsNotNone(self._lookup_payload(payload).failure)

    def test_missing_null_wrong_type_bool_as_int_and_nonfinite_values_fail_closed(
        self,
    ) -> None:
        cases: list[tuple[str, dict[str, object]]] = []

        missing_root = _success()
        del missing_root["data"]
        cases.append(("missing-root", missing_root))

        missing_echo = _success()
        del _results(missing_echo)["request_params"]["q"]  # type: ignore[index]
        cases.append(("missing-echo", missing_echo))

        missing_title = _success()
        del _hit(missing_title)["document"]["title"]  # type: ignore[index]
        cases.append(("missing-title", missing_title))

        null_results = _success()
        null_results["data"]["search"]["results"] = None  # type: ignore[index]
        cases.append(("null-results", null_results))

        null_document = _success()
        _hit(null_document)["document"] = None
        cases.append(("null-document", null_document))

        wrong_image = _success()
        _hit(wrong_image)["document"]["image"]["width"] = "600"  # type: ignore[index]
        cases.append(("wrong-image-type", wrong_image))

        bool_page = _success()
        bool_page["data"]["search"]["page"] = True  # type: ignore[index]
        cases.append(("bool-page", bool_page))

        bool_found = _success()
        _results(bool_found)["found"] = True
        cases.append(("bool-found", bool_found))

        bool_document_count = _success()
        _hit(bool_document_count)["document"]["activities_count"] = True  # type: ignore[index]
        cases.append(("bool-document-count", bool_document_count))

        integer_rating = _success()
        _hit(integer_rating)["document"]["rating"] = 4  # type: ignore[index]
        cases.append(("integer-rating", integer_rating))

        bool_text_match = _success()
        _hit(bool_text_match)["text_match"] = True
        cases.append(("bool-text-match", bool_text_match))

        bool_match_info = _success()
        _hit(bool_match_info)["text_match_info"]["fields_matched"] = True  # type: ignore[index]
        cases.append(("bool-match-info", bool_match_info))

        for name, payload in cases:
            with self.subTest(name=name):
                self.assertIsNotNone(self._lookup_payload(payload).failure)

        encoded = json.dumps(_success(), separators=(",", ":")).encode("utf-8")
        nonfinite = encoded.replace(b'"rating":4.25', b'"rating":1e309')
        result = self._provider(lambda _request: _response(nonfinite)).lookup(
            _query(), PageRequest(None, 1), _context()
        )
        self.assertEqual(result.failure.kind, FailureKind.INVALID_RESPONSE)  # type: ignore[union-attr]

    def test_echo_count_and_result_cardinality_invariants(self) -> None:
        valid_many_matches = self._lookup_payload(_success())
        self.assertIsNone(valid_many_matches.failure)

        cases: list[tuple[str, dict[str, object]]] = []
        for name, path, value in (
            ("outer-query", ("data", "search", "query"), "different"),
            ("outer-type", ("data", "search", "query_type"), "book"),
            ("result-page", ("data", "search", "results", "page"), 2),
            (
                "first-query",
                ("data", "search", "results", "request_params", "first_q"),
                "different",
            ),
            (
                "query",
                ("data", "search", "results", "request_params", "q"),
                "different",
            ),
            (
                "page-size",
                ("data", "search", "results", "request_params", "per_page"),
                2,
            ),
        ):
            payload = _success()
            target = _path(payload, *path[:-1])
            target[path[-1]] = value  # type: ignore[index]
            cases.append((name, payload))

        found_above_total = _success()
        _results(found_above_total)["found"] = 101
        cases.append(("found-above-total", found_above_total))

        hit_with_zero_found = _success()
        _results(hit_with_zero_found)["found"] = 0
        cases.append(("hit-with-zero-found", hit_with_zero_found))

        empty_with_found = _success(None)
        _results(empty_with_found)["found"] = 1
        cases.append(("empty-with-found", empty_with_found))

        negative_time = _success()
        _results(negative_time)["search_time_ms"] = -1
        cases.append(("negative-time", negative_time))

        for name, payload in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    self._lookup_payload(payload).failure.kind,  # type: ignore[union-attr]
                    FailureKind.INCOMPATIBLE,
                )

    def test_string_list_duplicate_and_global_shape_bounds(self) -> None:
        string_boundary = _success()
        _hit(string_boundary)["document"]["description"] = "x" * 512  # type: ignore[index]
        self.assertIsNone(self._lookup_payload(string_boundary).failure)
        string_over = _success()
        _hit(string_over)["document"]["description"] = "x" * 513  # type: ignore[index]
        self.assertEqual(
            self._lookup_payload(string_over).failure.kind,  # type: ignore[union-attr]
            FailureKind.INVALID_RESPONSE,
        )

        list_boundary = _success()
        _hit(list_boundary)["document"]["alternative_titles"] = ["x"] * 16  # type: ignore[index]
        self.assertIsNone(self._lookup_payload(list_boundary).failure)
        list_over = _success()
        _hit(list_over)["document"]["alternative_titles"] = ["x"] * 17  # type: ignore[index]
        self.assertEqual(
            self._lookup_payload(list_over).failure.kind,  # type: ignore[union-attr]
            FailureKind.INVALID_RESPONSE,
        )

        encoded = json.dumps(_success("A Different Synthetic Book"), separators=(",", ":")).encode(
            "utf-8"
        )
        duplicate = encoded.replace(
            b'"title":"A Different Synthetic Book"',
            b'"title":"A Different Synthetic Book","title":"duplicate"',
        )
        self.assertEqual(
            self._provider(lambda _request: _response(duplicate))
            .lookup(_query(), PageRequest(None, 1), _context())
            .failure.kind,  # type: ignore[union-attr]
            FailureKind.INVALID_RESPONSE,
        )

        self.assertEqual(hardcover_title._json_shape([None] * 16)[3], 16)
        self.assertEqual(hardcover_title._json_shape([None] * 17)[3], 17)

    def test_every_global_shape_and_response_boundary_is_inclusive(self) -> None:
        depth_boundary: object = None
        for _ in range(hardcover_title.MAX_JSON_DEPTH - 1):
            depth_boundary = {"nested": depth_boundary}
        depth_over = {"nested": depth_boundary}
        self.assertEqual(
            hardcover_title._json_shape(depth_boundary)[1],
            hardcover_title.MAX_JSON_DEPTH,
        )
        self.assertTrue(hardcover_title._json_within_bounds(depth_boundary))
        self.assertFalse(hardcover_title._json_within_bounds(depth_over))

        node_boundary = {f"outer-{index}": None for index in range(63)}
        node_boundary["nested"] = {
            f"inner-{index}": None for index in range(63)
        }
        node_over = copy.deepcopy(node_boundary)
        node_over["nested"]["inner-63"] = None  # type: ignore[index]
        self.assertEqual(
            hardcover_title._json_shape(node_boundary)[0],
            hardcover_title.MAX_JSON_NODES,
        )
        self.assertTrue(hardcover_title._json_within_bounds(node_boundary))
        self.assertFalse(hardcover_title._json_within_bounds(node_over))

        object_boundary = {
            f"field-{index}": None
            for index in range(hardcover_title.MAX_OBJECT_KEYS)
        }
        object_over = dict(object_boundary)
        object_over["field-over"] = None
        self.assertEqual(
            hardcover_title._json_shape(object_boundary)[2],
            hardcover_title.MAX_OBJECT_KEYS,
        )
        self.assertTrue(hardcover_title._json_within_bounds(object_boundary))
        self.assertFalse(hardcover_title._json_within_bounds(object_over))

        self.assertTrue(
            hardcover_title._response_size_allowed(
                hardcover_title.MAX_RESPONSE_BYTES
            )
        )
        self.assertFalse(
            hardcover_title._response_size_allowed(
                hardcover_title.MAX_RESPONSE_BYTES + 1
            )
        )
        self.assertFalse(hardcover_title._response_size_allowed(-1))

    def test_provider_integer_and_rating_boundaries_are_closed(self) -> None:
        self.assertTrue(
            hardcover_title._non_negative_int(hardcover_title.MAX_INTEGER)
        )
        self.assertTrue(
            hardcover_title._non_negative_int(hardcover_title.MAX_INTEGER - 1)
        )
        self.assertFalse(
            hardcover_title._non_negative_int(hardcover_title.MAX_INTEGER + 1)
        )
        self.assertFalse(hardcover_title._non_negative_int(-1))
        self.assertFalse(hardcover_title._non_negative_int(True))
        self.assertTrue(
            hardcover_title._non_negative_text_match_int(
                hardcover_title.MAX_TEXT_MATCH_INTEGER - 1
            )
        )
        self.assertTrue(
            hardcover_title._non_negative_text_match_int(
                hardcover_title.MAX_TEXT_MATCH_INTEGER
            )
        )
        self.assertFalse(
            hardcover_title._non_negative_text_match_int(
                hardcover_title.MAX_TEXT_MATCH_INTEGER + 1
            )
        )
        self.assertFalse(hardcover_title._non_negative_text_match_int(-1))
        self.assertFalse(hardcover_title._non_negative_text_match_int(True))

        integer_boundary = _success()
        _hit(integer_boundary)["document"]["image"]["id"] = (  # type: ignore[index]
            hardcover_title.MAX_INTEGER
        )
        self.assertIsNone(self._lookup_payload(integer_boundary).failure)
        for value in (-1, hardcover_title.MAX_INTEGER + 1):
            with self.subTest(integer=value):
                payload = _success()
                _hit(payload)["document"]["image"]["id"] = value  # type: ignore[index]
                self.assertEqual(
                    self._lookup_payload(payload).failure.kind,  # type: ignore[union-attr]
                    FailureKind.INVALID_RESPONSE,
                )

        for rating in (hardcover_title.MIN_RATING, hardcover_title.MAX_RATING):
            with self.subTest(rating=rating):
                payload = _success()
                _hit(payload)["document"]["rating"] = rating  # type: ignore[index]
                self.assertIsNone(self._lookup_payload(payload).failure)
        for rating in (-0.01, 5.01):
            with self.subTest(rating=rating):
                payload = _success()
                _hit(payload)["document"]["rating"] = rating  # type: ignore[index]
                self.assertEqual(
                    self._lookup_payload(payload).failure.kind,  # type: ignore[union-attr]
                    FailureKind.INVALID_RESPONSE,
                )

        text_match_boundary = _success()
        _hit(text_match_boundary)["text_match"] = (
            hardcover_title.MAX_TEXT_MATCH_INTEGER
        )
        self.assertIsNone(self._lookup_payload(text_match_boundary).failure)
        for value in (-1, hardcover_title.MAX_TEXT_MATCH_INTEGER + 1, True):
            with self.subTest(text_match=value):
                payload = _success()
                _hit(payload)["text_match"] = value
                self.assertEqual(
                    self._lookup_payload(payload).failure.kind,  # type: ignore[union-attr]
                    FailureKind.INVALID_RESPONSE,
                )

    def test_http_outcomes_are_typed_and_never_redirect(self) -> None:
        expected = {
            301: FailureKind.INVALID_RESPONSE,
            401: FailureKind.AUTHENTICATION,
            403: FailureKind.AUTHORIZATION,
            404: FailureKind.NOT_FOUND,
            408: FailureKind.TIMEOUT,
            429: FailureKind.RATE_LIMITED,
            500: FailureKind.UNAVAILABLE,
        }
        for status, kind in expected.items():
            with self.subTest(status=status):
                calls = []

                def handler(request, status=status):
                    calls.append(request)
                    return _response({}, status=status)

                result = self._provider(handler).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                self.assertEqual(result.failure.kind, kind)  # type: ignore[union-attr]
                self.assertEqual(len(calls), 1)

    def test_empty_and_graphql_or_shape_failures_are_distinct(self) -> None:
        oversized_title = _success()
        _hit(oversized_title)["document"]["title"] = "x" * 513  # type: ignore[index]
        multiple = _success()
        _results(multiple)["hits"].append(copy.deepcopy(_hit(multiple)))  # type: ignore[union-attr]
        _results(multiple)["found"] = 2
        cases = (
            (_success(None), None),
            ({"errors": [{"message": "private upstream detail"}]}, FailureKind.INVALID_RESPONSE),
            ({"data": {"search": {"results": []}}}, FailureKind.INVALID_RESPONSE),
            (oversized_title, FailureKind.INVALID_RESPONSE),
            (multiple, FailureKind.INVALID_RESPONSE),
        )
        for payload, kind in cases:
            with self.subTest(kind=kind):
                result = self._provider(lambda _request, payload=payload: _response(payload)).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                if kind is None:
                    self.assertEqual(result.value.items, ())  # type: ignore[union-attr]
                else:
                    self.assertEqual(result.failure.kind, kind)  # type: ignore[union-attr]

    def test_duplicate_deep_oversized_and_encoded_responses_fail_closed(self) -> None:
        deep: object = None
        for _ in range(10):
            deep = {"nested": deep}
        cases = (
            b'{"data":{"search":{}},"data":{"search":{}}}',
            json.dumps(deep).encode(),
            b"{" + b" " * 65_536 + b"}",
        )
        for payload in cases:
            with self.subTest(size=len(payload)):
                result = self._provider(lambda _request, payload=payload: _response(payload)).lookup(
                    _query(), PageRequest(None, 1), _context()
                )
                self.assertIn(
                    result.failure.kind,  # type: ignore[union-attr]
                    {FailureKind.INVALID_RESPONSE, FailureKind.RESOURCE_EXHAUSTED},
                )
        encoded = self._provider(
            lambda _request: _response(
                _success(None), headers={"content-encoding": "gzip"}
            )
        ).lookup(_query(), PageRequest(None, 1), _context())
        self.assertEqual(encoded.failure.kind, FailureKind.INVALID_RESPONSE)  # type: ignore[union-attr]

    def test_destination_set_and_connected_peer_are_both_enforced(self) -> None:
        calls = []

        def handler(request):
            calls.append(request)
            return _response(_success(None))

        for addresses in (
            (),
            ("127.0.0.1",),
            ("169.254.1.1",),
            ("224.0.0.1",),
            ("0.0.0.0",),
            (PUBLIC_IP, "10.0.0.1"),
            ("::ffff:93.184.216.34",),
            ("fe80::1%en0",),
            ("not-an-address",),
        ):
            with self.subTest(addresses=addresses):
                result = self._provider(
                    handler,
                    resolver=lambda _host, _port, _budget, values=addresses: values,
                ).lookup(_query(), PageRequest(None, 1), _context())
                self.assertIsNotNone(result.failure)
        self.assertEqual(calls, [])

        mismatch = self._provider(
            lambda _request: _response(_success(None), peer="93.184.216.35")
        ).lookup(_query(), PageRequest(None, 1), _context())
        self.assertEqual(mismatch.failure.kind, FailureKind.INTEGRITY_FAILURE)  # type: ignore[union-attr]

    def test_production_default_uses_the_system_resolver(self) -> None:
        with patch.object(
            hardcover_title,
            "_system_resolver",
            return_value=(PUBLIC_IP,),
        ) as resolver:
            result = self._provider(
                lambda _request: _response(_success(None)),
                resolver=None,
            ).lookup(_query(), PageRequest(None, 1), _context())

        self.assertIsNone(result.failure)
        resolver.assert_called_once()
        host, port, budget = resolver.call_args.args
        self.assertEqual((host, port), ("api.hardcover.app", 443))
        self.assertIsInstance(budget, hardcover_title._OperationBudget)

    def test_system_resolver_uses_isolated_bounded_subprocess(self) -> None:
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = (
            json.dumps([PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"]).encode(
                "ascii"
            ),
            b"",
        )
        with patch.object(
            hardcover_title.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            result = hardcover_title._system_resolver(
                "api.hardcover.app",
                443,
                hardcover_title._OperationBudget(_context()),
            )

        self.assertEqual(
            result,
            (PUBLIC_IP, "2606:2800:220:1:248:1893:25c8:1946"),
        )
        command = popen.call_args.args[0]
        self.assertEqual(
            command,
            [
                hardcover_title.sys.executable,
                "-I",
                "-c",
                hardcover_title._RESOLVER_SCRIPT,
                "api.hardcover.app",
                "443",
            ],
        )
        options = popen.call_args.kwargs
        self.assertIs(options["stdin"], hardcover_title.subprocess.DEVNULL)
        self.assertIs(options["stdout"], hardcover_title.subprocess.PIPE)
        self.assertIs(options["stderr"], hardcover_title.subprocess.DEVNULL)
        self.assertTrue(options["close_fds"])
        self.assertEqual(options["cwd"], "/")
        self.assertEqual(options["env"], {})
        process.communicate.assert_called_once_with(timeout=0.1)
        process.kill.assert_not_called()

    def test_system_resolver_rejects_process_and_output_failures(self) -> None:
        cases = (
            (1, json.dumps([PUBLIC_IP]).encode("ascii")),
            (0, b""),
            (0, b"{"),
            (0, json.dumps({"address": PUBLIC_IP}).encode("ascii")),
            (0, json.dumps([PUBLIC_IP, 1]).encode("ascii")),
            (0, b"x" * 8_193),
        )
        for returncode, output in cases:
            process = Mock(returncode=returncode)
            process.poll.return_value = returncode
            process.communicate.return_value = (output, b"")
            with (
                self.subTest(returncode=returncode, output_size=len(output)),
                patch.object(
                    hardcover_title.subprocess,
                    "Popen",
                    return_value=process,
                ),
                self.assertRaises(OSError),
            ):
                hardcover_title._system_resolver(
                    "api.hardcover.app",
                    443,
                    hardcover_title._OperationBudget(_context()),
                )

    def test_system_resolver_kills_and_reaps_on_deadline_or_cancellation(self) -> None:
        for kind, expected in (
            (FailureKind.TIMEOUT, hardcover_title._ResolverTimeout),
            (FailureKind.CANCELLED, hardcover_title._ResolverCancelled),
        ):
            process = Mock(returncode=None)

            def poll(selected=process):
                return selected.returncode

            def kill(selected=process):
                selected.returncode = -9

            process.poll.side_effect = poll
            process.kill.side_effect = kill
            process.wait.return_value = -9
            budget = Mock()
            budget.failure.return_value = hardcover_title._failure(
                kind,
                "synthetic resolver stop",
            )
            budget.remaining.return_value = 0.01
            with (
                self.subTest(kind=kind),
                patch.object(
                    hardcover_title.subprocess,
                    "Popen",
                    return_value=process,
                ),
                self.assertRaises(expected),
            ):
                hardcover_title._system_resolver(
                    "api.hardcover.app",
                    443,
                    budget,
                )
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=0.1)
            process.communicate.assert_not_called()

    def test_cancelled_and_invalid_application_requests_send_nothing(self) -> None:
        calls = []
        provider = self._provider(
            lambda request: calls.append(request) or _response(_success(None))
        )
        cancelled = provider.lookup(_query(), PageRequest(None, 1), _context(cancelled=True))
        self.assertEqual(cancelled.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        invalid = provider.lookup(_query(), PageRequest(None, 2), _context())
        self.assertEqual(invalid.failure.kind, FailureKind.INVALID_REQUEST)  # type: ignore[union-attr]
        self.assertEqual(calls, [])

    def test_one_budget_bounds_resolution_and_clips_transport_timeouts(self) -> None:
        calls = []
        workers_before = {
            worker.ident for worker in threading.enumerate() if worker.is_alive()
        }

        def slow_resolver(_host, _port, budget):
            while budget.failure() is None:
                time.sleep(0.005)
            return (PUBLIC_IP,)

        started = time.monotonic()
        timed_out = self._provider(
            lambda request: calls.append(request) or _response(_success(None)),
            resolver=slow_resolver,
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.05),
        )
        self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(calls, [])
        self.assertEqual(
            {worker.ident for worker in threading.enumerate() if worker.is_alive()},
            workers_before,
        )

        def delayed_resolver(_host, _port, _budget):
            time.sleep(0.05)
            return (PUBLIC_IP,)

        clipped = self._provider(
            lambda request: calls.append(request) or _response(_success(None)),
            resolver=delayed_resolver,
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.5),
        )
        self.assertIsNone(clipped.failure)
        timeouts = calls[0].extensions["timeout"]
        self.assertTrue(all(0 < value < 0.5 for value in timeouts.values()))

    def test_deadline_and_cancellation_interrupt_stream_consumption(self) -> None:
        body = json.dumps(_success(None), separators=(",", ":")).encode("utf-8")

        class SlowStream(httpx.AsyncByteStream):
            def __init__(self, *, cancel_after_first=None) -> None:
                self.consumed = 0
                self.cancel_after_first = cancel_after_first

            async def __aiter__(self):
                for byte in body:
                    self.consumed += 1
                    if self.consumed == 1 and self.cancel_after_first is not None:
                        self.cancel_after_first.set()
                    await asyncio.sleep(0.02)
                    yield bytes((byte,))

        def response_for(stream):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=stream,
                extensions={"network_stream": _Peer()},
            )

        deadline_stream = SlowStream()
        started = time.monotonic()
        deadline = self._provider(
            lambda _request: response_for(deadline_stream)
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=0.08),
        )
        self.assertEqual(deadline.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertLess(deadline_stream.consumed, len(body))
        consumed_at_return = deadline_stream.consumed
        time.sleep(0.08)
        self.assertEqual(deadline_stream.consumed, consumed_at_return)

        cancelled = threading.Event()
        cancelled_stream = SlowStream(cancel_after_first=cancelled)
        result = self._provider(
            lambda _request: response_for(cancelled_stream)
        ).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        self.assertLess(cancelled_stream.consumed, len(body))
        consumed_at_return = cancelled_stream.consumed
        time.sleep(0.08)
        self.assertEqual(cancelled_stream.consumed, consumed_at_return)

    def test_deadline_and_cancellation_interrupt_response_header_wait(self) -> None:
        completed = threading.Event()

        async def delayed_headers(_request):
            try:
                await asyncio.sleep(1)
                return _response(_success(None))
            finally:
                completed.set()

        started = time.monotonic()
        timed_out = self._provider(delayed_headers).lookup(
            _query(), PageRequest(None, 1), _context(deadline_seconds=0.05)
        )
        self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(completed.is_set())

        completed.clear()
        cancelled = threading.Event()
        timer = threading.Timer(0.05, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = self._provider(delayed_headers).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertTrue(completed.is_set())

    def test_deadline_and_cancellation_take_precedence_over_parse_failure(self) -> None:
        original_parse = hardcover_title._parse_response

        def delayed_invalid_parse(payload, requested_title):
            time.sleep(0.08)
            return original_parse(payload, requested_title)

        with patch.object(
            hardcover_title, "_parse_response", delayed_invalid_parse
        ):
            started = time.monotonic()
            timed_out = self._provider(lambda _request: _response(b"{")).lookup(
                _query(), PageRequest(None, 1), _context(deadline_seconds=0.03)
            )
            self.assertEqual(timed_out.failure.kind, FailureKind.TIMEOUT)  # type: ignore[union-attr]
            self.assertLess(time.monotonic() - started, 0.2)

            cancelled = threading.Event()
            timer = threading.Timer(0.03, cancelled.set)
            timer.start()
            try:
                result = self._provider(lambda _request: _response(b"{")).lookup(
                    _query(),
                    PageRequest(None, 1),
                    _context(
                        deadline_seconds=1,
                        cancellation_reader=cancelled.is_set,
                    ),
                )
            finally:
                timer.cancel()
            self.assertEqual(result.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

    def test_cancellation_takes_precedence_over_http_failures(self) -> None:
        cancelled = threading.Event()

        def authentication_failure(_request):
            cancelled.set()
            return _response({}, status=401)

        authentication = self._provider(authentication_failure).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(authentication.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]

        cancelled.clear()

        def connection_failure(request):
            cancelled.set()
            raise httpx.ConnectError("private connection detail", request=request)

        unavailable = self._provider(connection_failure).lookup(
            _query(),
            PageRequest(None, 1),
            _context(deadline_seconds=1, cancellation_reader=cancelled.is_set),
        )
        self.assertEqual(unavailable.failure.kind, FailureKind.CANCELLED)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
