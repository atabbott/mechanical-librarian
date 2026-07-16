from __future__ import annotations

import copy
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_inventory import (
    AudiobookshelfInventoryReader,
    _cover_field,
    _cover_value,
    _file_descriptor,
    _file_identity,
    _file_objects,
    _identifiers,
    _normalize,
    _number,
    _page,
    _scalar,
    _structured,
    _status_failure,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.adapters.persistence.inventory_polling import (
    SQLiteInventoryPollingRepository,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    OperationUsage,
    PortResult,
    PortFailure,
    RetryAdvice,
    SafeSummary,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
    InventoryMountMapping,
    InventoryOutcome,
    ReconcileInventory,
)
from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    ExternalItemId,
    LibraryId,
    ScanId,
    SchemaVersion,
    ServiceVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    ItemLifecycle,
    LibraryConnection,
    MediaType,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.domain.inventory_v2 import (
    FieldObservation,
    InventoryInspection,
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanBounds,
    ScanState,
    ScanUsage,
    opaque_target_reference,
)
from mechanical_librarian.domain.inventory_polling import PollPolicy, PollRequest
from mechanical_librarian.infrastructure.persistence.sqlite import migrate


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LIBRARY = LibraryId("01234567-89ab-4def-8123-456789abcdef")
AUDIO = "11234567-89ab-4def-8123-456789abcdef"
EBOOK = "21234567-89ab-4def-8123-456789abcdef"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "audiobookshelf" / "2.35.1"
ROOT = Path(__file__).parents[2]


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def operation_context() -> OperationContext:
    return OperationContext(
        Deadline(NOW + timedelta(minutes=10)),
        CancellationProbe(CancellationId("inventory-test"), lambda: False),
        ResourceBounds(100, 1_000_000),
        TimeProbe(lambda: NOW),
    )


def capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        True,
        True,
        False,
        False,
        True,
        False,
        True,
        frozenset(),
        False,
        frozenset({MediaType.AUDIOBOOK, MediaType.EBOOK}),
    )


def connection() -> LibraryConnection:
    return LibraryConnection(
        ConnectionId("primary"),
        AdapterId("audiobookshelf"),
        ServiceEndpoint("https://library.example"),
        SecretReference("/run/secrets/library"),
        capabilities(),
    )


def factory(handler):
    return lambda: PortResult.success(
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    )


def json_response(value: object) -> httpx.Response:
    payload = json.dumps(value, separators=(",", ":")).encode()
    return httpx.Response(
        200,
        stream=httpx.ByteStream(payload),
        headers={
            "content-type": "application/json",
            "content-length": str(len(payload)),
        },
    )


class AudiobookshelfInventoryReaderTests(unittest.TestCase):
    def test_epub_identity_and_optional_projection_cross_product(self) -> None:
        mapping = InventoryMountMapping(
            "books", "ebook", "/library/books", "a" * 64
        )
        base = self._detail(
            EBOOK,
            {
                "ebookFile": {
                    "ino": "ebook-inode",
                    "ebookFormat": "epub",
                    "metadata": {
                        "path": "/library/books/book.epub",
                        "relPath": "book.epub",
                        "size": 123,
                    },
                }
            },
        )
        summary = {
            key: base[key]
            for key in (
                "id",
                "libraryId",
                "mediaType",
                "updatedAt",
                "isMissing",
                "isInvalid",
            )
        }
        partitions = (
            ("unconfigured", None, ()),
            ("missing_metadata", None, (mapping,)),
            ("malformed_metadata", [], (mapping,)),
            ("missing_path", {"relPath": "book.epub"}, (mapping,)),
            ("malformed_path", {"path": [], "relPath": "book.epub"}, (mapping,)),
            ("missing_relative", {"path": "/library/books/book.epub"}, (mapping,)),
            (
                "malformed_relative",
                {
                    "path": "/library/books/book.epub",
                    "relPath": ["book.epub"],
                },
                (mapping,),
            ),
            (
                "mismatch",
                {
                    "path": "/library/books/book.epub",
                    "relPath": "other.epub",
                },
                (mapping,),
            ),
            (
                "ambiguous",
                base["media"]["ebookFile"]["metadata"],
                (mapping, mapping),
            ),
        )
        for label, metadata, mappings in partitions:
            for identity, expected_failure in (
                ("ebook-inode", None),
                ([], FailureKind.INVALID_RESPONSE),
            ):
                with self.subTest(identity=identity, partition=label):
                    detail = copy.deepcopy(base)
                    detail["media"]["ebookFile"]["ino"] = identity
                    if label == "missing_metadata":
                        del detail["media"]["ebookFile"]["metadata"]
                    elif label != "unconfigured":
                        detail["media"]["ebookFile"]["metadata"] = metadata
                    normalized = _normalize(
                        detail,
                        summary,
                        connection(),
                        LIBRARY,
                        operation_context(),
                        mappings,
                    )
                    if expected_failure is not None:
                        self.assertEqual(normalized.failure.kind, expected_failure)
                        continue
                    self.assertIsNone(normalized.failure)
                    self.assertEqual(normalized.value[0].file_bindings, ())
                    self.assertEqual(
                        normalized.value[0].observation.lifecycle,
                        ItemLifecycle.ACTIVE,
                    )

    def test_f05b_machine_profile_keeps_unavailable_capabilities_explicit(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "docs"
                / "reference"
                / "audiobookshelf-f05b-compatibility.json"
            ).read_text(encoding="utf-8")
        )
        human = (ROOT / "docs" / "reference" / "compatibility.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(profile["pinned_version"], "2.35.1")
        self.assertEqual(profile["notifications"], "unavailable")
        self.assertEqual(profile["rate_limits"], "unavailable")
        self.assertEqual(profile["concurrency_tokens"], "unavailable")
        self.assertEqual(profile["write_routes"], [])
        self.assertIn("audiobookshelf-f05b-compatibility.json", human)

    def test_mixed_library_traversal_is_deterministic_and_path_free(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/api/libraries":
                value = fixture("libraries.json")
            elif request.url.path.endswith("/items"):
                value = fixture("library-items-page-0.json")
            elif request.url.path == f"/api/items/{AUDIO}":
                value = fixture("item-audiobook-expanded.json")
            else:
                value = fixture("item-ebook-expanded.json")
            return json_response(value)

        reader = AudiobookshelfInventoryReader(client_factory=factory(handler))
        bounds = ScanBounds(2, 4, 10, 1_000_000, 300, 0)
        verified = reader.verify_library(
            connection(), LIBRARY, bounds, operation_context()
        )
        self.assertIsNone(verified.failure)
        result = reader.read_pass(connection(), LIBRARY, bounds, operation_context())

        self.assertIsNone(result.failure)
        self.assertEqual(
            [item.observation.media_type for item in result.value.snapshots],
            [MediaType.AUDIOBOOK, MediaType.EBOOK],
        )
        canonical = "".join(
            item.observation.canonical for item in result.value.snapshots
        )
        self.assertNotIn("private/audio.m4b", canonical)
        self.assertNotIn("private/book.epub", canonical)
        self.assertNotIn("synthetic/audio.m4b", canonical)
        self.assertNotIn("synthetic/book.epub", canonical)
        self.assertTrue(all(method == "GET" for method, _ in requests))
        self.assertEqual(result.value.usage.pages, 1)
        title = next(
            field
            for field in result.value.snapshots[0].observation.fields
            if field.field is ObservationField.TITLE
        )
        self.assertEqual(title.values, ("Synthetic audiobook",))
        self.assertEqual(title.raw_values, ('"Synthetic audiobook"',))
        subtitle = next(
            field
            for field in result.value.snapshots[0].observation.fields
            if field.field is ObservationField.SUBTITLE
        )
        self.assertEqual(subtitle.state, ObservationState.MISSING)

    def test_duplicate_json_keys_fail_closed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                stream=httpx.ByteStream(b'{"libraries":[],"libraries":[]}'),
                headers={"content-type": "application/json"},
            )

        result = AudiobookshelfInventoryReader(
            client_factory=factory(handler)
        ).verify_library(
            connection(),
            LIBRARY,
            ScanBounds(2, 4, 10, 1_000_000, 300, 0),
            operation_context(),
        )
        self.assertEqual(result.failure.kind, FailureKind.INVALID_RESPONSE)

    def test_post_io_validation_failure_keeps_consumed_resource_usage(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return json_response({"unexpected": "page"})

        result = AudiobookshelfInventoryReader(
            client_factory=factory(handler)
        ).read_pass(
            connection(),
            LIBRARY,
            ScanBounds(2, 4, 10, 1_000_000, 300, 0),
            operation_context(),
        )

        self.assertEqual(result.failure.kind, FailureKind.INVALID_RESPONSE)
        self.assertEqual(result.failure.usage.pages, 1)
        self.assertGreater(result.failure.usage.response_bytes, 0)

    def test_nonfinite_json_and_http_failures_are_typed(self) -> None:
        bounds = ScanBounds(2, 4, 10, 1_000_000, 300, 0)
        for status, kind in (
            (401, FailureKind.AUTHENTICATION),
            (403, FailureKind.AUTHORIZATION),
            (408, FailureKind.TIMEOUT),
            (429, FailureKind.RATE_LIMITED),
            (503, FailureKind.UNAVAILABLE),
            (302, FailureKind.INVALID_RESPONSE),
        ):
            with self.subTest(status=status):
                reader = AudiobookshelfInventoryReader(
                    client_factory=factory(
                        lambda _request, selected=status: httpx.Response(selected)
                    )
                )
                result = reader.verify_library(
                    connection(), LIBRARY, bounds, operation_context()
                )
                self.assertEqual(result.failure.kind, kind)

    def test_liveness_takes_precedence_when_http_status_arrives(self) -> None:
        bounds = ScanBounds(2, 4, 10, 1_000_000, 300, 0)
        for kind in (FailureKind.CANCELLED, FailureKind.TIMEOUT):
            with self.subTest(kind=kind):
                observed = NOW + timedelta(hours=1)
                clock = [observed]
                cancelled = [False]
                deadline = observed + timedelta(minutes=5)

                def handler(_request, selected=kind):
                    if selected is FailureKind.CANCELLED:
                        cancelled[0] = True
                    else:
                        clock[0] = deadline
                    return httpx.Response(401)

                context = OperationContext(
                    Deadline(deadline),
                    CancellationProbe(
                        CancellationId(f"inventory-status-{kind.value}"),
                        lambda: cancelled[0],
                    ),
                    ResourceBounds(100, 1_000_000),
                    TimeProbe(lambda: clock[0]),
                )
                result = AudiobookshelfInventoryReader(
                    client_factory=factory(handler)
                ).verify_library(connection(), LIBRARY, bounds, context)

                self.assertEqual(result.failure.kind, kind)

        def nonfinite(_request: httpx.Request) -> httpx.Response:
            payload = b'{"libraries":[NaN]}'
            return httpx.Response(
                200,
                stream=httpx.ByteStream(payload),
                headers={"content-type": "application/json"},
            )

        result = AudiobookshelfInventoryReader(
            client_factory=factory(nonfinite)
        ).verify_library(connection(), LIBRARY, bounds, operation_context())
        self.assertEqual(result.failure.kind, FailureKind.INVALID_RESPONSE)

    def test_retry_after_and_route_sensitive_not_found_are_bounded(self) -> None:
        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        delta = _status_failure(
            429, path="/api/libraries", retry_after="17", now=now
        )
        dated = _status_failure(
            429,
            path="/api/libraries",
            retry_after="Mon, 13 Jul 2026 12:00:23 GMT",
            now=now,
        )
        malformed = _status_failure(
            429, path="/api/libraries", retry_after="private-invalid", now=now
        )
        oversized = _status_failure(
            429, path="/api/libraries", retry_after="9" * 128, now=now
        )
        excessive = _status_failure(
            429, path="/api/libraries", retry_after="9" * 5_000, now=now
        )
        library_missing = _status_failure(404, path="/api/libraries")
        item_race = _status_failure(404, path=f"/api/items/{AUDIO}")

        self.assertEqual(delta.retry.retry_after, timedelta(seconds=17))
        self.assertEqual(dated.retry.retry_after, timedelta(seconds=23))
        self.assertIsNone(malformed.retry.retry_after)
        self.assertIsNone(oversized.retry.retry_after)
        self.assertIsNone(excessive.retry.retry_after)
        self.assertEqual(library_missing.kind, FailureKind.NOT_FOUND)
        self.assertEqual(item_race.kind, FailureKind.CONFLICT)

    def test_source_states_and_file_identity_are_allowlisted(self) -> None:
        self.assertEqual(
            _scalar({"title": 12}, "title", ObservationField.TITLE).state,
            ObservationState.MALFORMED,
        )
        for value, state in (
            ({}, ObservationState.MISSING),
            ({"isbn": None, "asin": None}, ObservationState.NULL),
            ({"isbn": "", "asin": "  "}, ObservationState.EMPTY),
            ({"isbn": 12, "asin": object()}, ObservationState.MALFORMED),
        ):
            with self.subTest(state=state):
                self.assertEqual(_identifiers(value).state, state)
        objects, state = _file_objects(
            {"audioFiles": [{"ino": "a"}, "malformed"], "ebookFile": None}
        )
        self.assertEqual(state, ObservationState.PRESENT)
        self.assertEqual(len(objects), 1)
        for malformed_file in ({}, {"unknown": "value"}):
            objects, state = _file_objects(
                {"audioFiles": [malformed_file], "ebookFile": None}
            )
            self.assertEqual(objects, [])
            self.assertEqual(state, ObservationState.MALFORMED)
        base = {
            "ino": "private-inode",
            "metadata": {"path": "private/file.m4b", "size": 12},
        }
        self.assertEqual(
            _file_identity(base),
            _file_identity(base | {"unknownPrivateKey": "/private/sentinel"}),
        )
        changed = base | {"metadata": base["metadata"] | {"size": 13}}
        self.assertNotEqual(_file_identity(base), _file_identity(changed))
        self.assertNotIn("private/file.m4b", _file_descriptor(base))
        infinite = float("inf")
        self.assertEqual(
            _number(
                {"duration": infinite}, "duration", ObservationField.DURATION
            ).state,
            ObservationState.MALFORMED,
        )
        self.assertEqual(
            _structured(
                {"chapters": [{"start": infinite}]},
                "chapters",
                ObservationField.CHAPTERS,
                ("start",),
            ).state,
            ObservationState.MALFORMED,
        )
        objects, state = _file_objects(
            {"audioFiles": [{"size": infinite}], "ebookFile": None}
        )
        self.assertEqual(objects, [])
        self.assertEqual(state, ObservationState.MALFORMED)
        self.assertEqual(
            _cover_value({}, {"coverPath": "private/cover.jpg"}),
            "private/cover.jpg",
        )
        self.assertIsNone(_cover_value({"coverPath": "unknown"}, {"metadata": {}}))
        for media, state in (
            ({}, ObservationState.MISSING),
            ({"coverPath": None}, ObservationState.NULL),
            ({"coverPath": ""}, ObservationState.EMPTY),
            ({"coverPath": 1}, ObservationState.MALFORMED),
        ):
            self.assertEqual(_cover_field({}, media).state, state)

    def test_identifier_namespaces_are_independent_and_invalid_siblings_are_safe(
        self,
    ) -> None:
        for sibling in (None, "", 12, "x\x00y"):
            with self.subTest(sibling=sibling):
                observed = _identifiers({"isbn": " 9780000000001 ", "asin": sibling})
                self.assertEqual(observed.state, ObservationState.PRESENT)
                self.assertEqual(
                    observed.values,
                    ('{"kind":"isbn","value":"9780000000001"}',),
                )
                self.assertEqual(
                    observed.raw_values,
                    ('{"kind":"isbn","value":" 9780000000001 "}',),
                )
        self.assertEqual(
            _identifiers({"isbn13": "9780000000001"}).state,
            ObservationState.MISSING,
        )
        detail = self._detail(
            AUDIO,
            {
                "audioFiles": [{"ino": "included", "exclude": False}],
            },
        )
        detail["media"]["metadata"].update(
            {"isbn": "9780000000001", "asin": "private-invalid-identifier\x00"}
        )
        summary = {
            key: detail[key]
            for key in (
                "id",
                "libraryId",
                "mediaType",
                "updatedAt",
                "isMissing",
                "isInvalid",
            )
        }
        normalized = _normalize(
            detail, summary, connection(), LIBRARY, operation_context()
        )
        self.assertIsNone(normalized.failure)
        self.assertIn(
            "identifiers_quarantined", normalized.value[0].observation.warnings
        )
        self.assertNotIn(
            "private-invalid-identifier", normalized.value[0].observation.canonical
        )

    def test_excluded_audio_does_not_classify_or_contribute_file_evidence(self) -> None:
        summary_keys = (
            "id",
            "libraryId",
            "mediaType",
            "updatedAt",
            "isMissing",
            "isInvalid",
        )
        ebook = {"ino": "ebook", "ebookFormat": "epub"}
        cases = (
            ([{"ino": "excluded", "exclude": True}], MediaType.EBOOK, 1),
            ([{"ino": "included"}], MediaType.AUDIOBOOK, 2),
            ([{"ino": "included", "exclude": False}], MediaType.AUDIOBOOK, 2),
            ([{"ino": "invalid", "exclude": None}], MediaType.EBOOK, 1),
            ([{"ino": "invalid", "exclude": "false"}], MediaType.EBOOK, 1),
        )
        for audio_files, expected_type, identity_count in cases:
            with self.subTest(audio_files=audio_files):
                detail = self._detail(
                    AUDIO,
                    {"audioFiles": audio_files, "ebookFile": ebook},
                )
                summary = {key: detail[key] for key in summary_keys}
                normalized = _normalize(
                    detail, summary, connection(), LIBRARY, operation_context()
                )
                self.assertIsNone(normalized.failure)
                observation = normalized.value[0].observation
                self.assertEqual(observation.media_type, expected_type)
                self.assertEqual(len(observation.file_identities), identity_count)
                if "exclude" in audio_files[0] and audio_files[0]["exclude"] not in {
                    True,
                    False,
                }:
                    self.assertIn("files_quarantined", observation.warnings)

    def test_nested_file_stat_changes_change_snapshot_without_exposing_paths(
        self,
    ) -> None:
        for fixture_name, file_key in (
            ("item-audiobook-expanded.json", "audioFiles"),
            ("item-ebook-expanded.json", "ebookFile"),
        ):
            with self.subTest(fixture=fixture_name):
                before = fixture(fixture_name)
                after = fixture(fixture_name)
                before_file = before["media"][file_key]
                after_file = after["media"][file_key]
                if isinstance(before_file, list):
                    before_file = before_file[0]
                    after_file = after_file[0]
                after_file["metadata"]["size"] += 1
                after_file["metadata"]["mtimeMs"] += 1
                summary = {
                    key: before[key]
                    for key in (
                        "id",
                        "libraryId",
                        "mediaType",
                        "updatedAt",
                        "isMissing",
                        "isInvalid",
                    )
                }
                first = _normalize(
                    before, summary, connection(), LIBRARY, operation_context()
                ).value[0]
                second = _normalize(
                    after, summary, connection(), LIBRARY, operation_context()
                ).value[0]
                self.assertNotEqual(first.fingerprint, second.fingerprint)
                self.assertNotEqual(
                    first.observation.file_identities,
                    second.observation.file_identities,
                )
                self.assertNotIn(
                    before_file["metadata"]["path"], first.observation.canonical
                )
                self.assertNotIn(
                    before_file["metadata"]["relPath"], first.observation.canonical
                )

    def test_optional_file_facts_are_quarantined_without_discarding_stable_identity(
        self,
    ) -> None:
        shapes = (
            (
                "audioFiles",
                {
                    "ino": "stable-audio-inode",
                    "format": "M4B",
                    "metadata": {"path": "private/audio.m4b", "size": 123},
                },
                {
                    "codec": None,
                    "bitRate": None,
                    "metadata": {"ctimeMs": None, "birthtimeMs": None},
                },
                {"codec": [], "metadata": {"ext": {}}},
            ),
            (
                "ebookFile",
                {
                    "ino": "stable-ebook-inode",
                    "metadata": {"path": "private/book.epub", "size": 456},
                },
                {
                    "ebookFormat": None,
                    "metadata": {
                        "ctimeMs": None,
                        "birthtimeMs": None,
                        "ext": None,
                    },
                },
                {"ebookFormat": [], "metadata": {"mtimeMs": []}},
            ),
        )
        for field_name, base, null_optional, invalid_optional in shapes:
            for label, optional_value in (
                ("absent", {}),
                ("null", null_optional),
                ("wrong_type", invalid_optional),
            ):
                with self.subTest(file=field_name, partition=label):
                    file = copy.deepcopy(base)
                    optional = copy.deepcopy(optional_value)
                    nested = optional.pop("metadata", None)
                    file.update(optional)
                    if nested is not None:
                        file["metadata"].update(nested)
                    self.assertEqual(_file_identity(file), _file_identity(base))
                    media_values = (
                        {"audioFiles": [file]}
                        if field_name == "audioFiles"
                        else {"ebookFile": file}
                    )
                    detail = self._detail(AUDIO, media_values)
                    summary = {
                        key: detail[key]
                        for key in (
                            "id",
                            "libraryId",
                            "mediaType",
                            "updatedAt",
                            "isMissing",
                            "isInvalid",
                        )
                    }
                    first = _normalize(
                        detail, summary, connection(), LIBRARY, operation_context()
                    ).value[0]
                    second = _normalize(
                        detail, summary, connection(), LIBRARY, operation_context()
                    ).value[0]
                    self.assertEqual(first.fingerprint, second.fingerprint)
                    self.assertEqual(len(first.observation.file_identities), 1)
                    self.assertNotIn("private/", first.observation.canonical)
                    if label != "absent":
                        self.assertIn("files_quarantined", first.observation.warnings)

        for insufficient in (
            {"format": "M4B", "codec": "aac"},
            {"ebookFormat": "epub", "metadata": {"size": 456}},
            {"ino": None, "metadata": {"path": None, "size": 1}},
        ):
            objects, state = _file_objects(
                {"audioFiles": [insufficient], "ebookFile": None}
            )
            self.assertEqual(objects, [])
            self.assertEqual(state, ObservationState.MALFORMED)

    def test_page_numbers_reject_boolean_and_float_substitutes(self) -> None:
        for page_value, limit_value in ((False, 1), (0.0, 1), (0, True), (0, 1.0)):
            self.assertIsNone(
                _page(
                    {
                        "results": [],
                        "page": page_value,
                        "limit": limit_value,
                        "total": 0,
                    },
                    0,
                    1,
                )
            )

    def test_repeated_page_and_response_boundary_fail_closed(self) -> None:
        def repeated(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/items"):
                value = fixture("library-items-page-0.json")
                value["limit"] = "1"
                value["page"] = request.url.params["page"]
                value["total"] = 2
                value["results"] = [value["results"][0]]
                return json_response(value)
            return json_response(fixture("item-audiobook-expanded.json"))

        result = AudiobookshelfInventoryReader(
            client_factory=factory(repeated)
        ).read_pass(
            connection(),
            LIBRARY,
            ScanBounds(1, 4, 10, 1_000_000, 300, 0),
            operation_context(),
        )
        self.assertEqual(result.failure.kind, FailureKind.CONFLICT)

        for headers, payload, kind in (
            ({"content-type": "text/plain"}, b"{}", FailureKind.INVALID_RESPONSE),
            (
                {"content-type": "application/json", "content-encoding": "gzip"},
                b"{}",
                FailureKind.INVALID_RESPONSE,
            ),
            (
                {"content-type": "application/json", "content-length": "1000001"},
                b"{}",
                FailureKind.RESOURCE_EXHAUSTED,
            ),
        ):
            with self.subTest(headers=headers):
                reader = AudiobookshelfInventoryReader(
                    client_factory=factory(
                        lambda _request, selected=headers, body=payload: httpx.Response(
                            200,
                            stream=httpx.ByteStream(body),
                            headers=selected,
                        )
                    )
                )
                failed = reader.verify_library(
                    connection(),
                    LIBRARY,
                    ScanBounds(2, 4, 10, 1_000_000, 300, 0),
                    operation_context(),
                )
                self.assertEqual(failed.failure.kind, kind)

    @staticmethod
    def _detail(identity: str, media_values: dict[str, object]) -> dict[str, object]:
        return {
            "id": identity,
            "libraryId": LIBRARY.value,
            "mediaType": "book",
            "updatedAt": int((NOW - timedelta(days=1)).timestamp() * 1000),
            "isMissing": False,
            "isInvalid": False,
            "media": {
                "metadata": {
                    "title": "Synthetic title",
                    "authors": [{"id": "author-1", "name": "Example Author"}],
                    "narrators": ["Example Narrator"],
                    "series": [{"id": "series-1", "name": "Series", "sequence": "1"}],
                    "genres": ["Fiction"],
                    "language": "eng",
                    "coverPath": "private/cover.jpg",
                },
                **media_values,
            },
        }


class SQLiteInventoryV2RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = sqlite3.connect(f"{self.temporary.name}/state.sqlite")
        self.database.execute("PRAGMA foreign_keys=ON")
        migrate(self.database)
        self.repository = SQLiteInventoryV2Repository(
            self.database, now=lambda: NOW
        )
        self.bounds = ScanBounds(2, 4, 10, 1_000_000, 300, 0)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_history_is_immutable_and_a_b_a_reuses_the_first_snapshot(self) -> None:
        first = self._snapshot("Title A")
        second = self._snapshot("Title B")
        self._publish("scan-a", first)
        self._publish("scan-b", second)
        inspection = self._publish("scan-c", first)

        self.assertEqual(inspection.state, ScanState.COMPLETE)
        self.assertEqual(inspection.history_count, 2)
        current = self.database.execute(
            "SELECT snapshot_id FROM current_inventory_v2"
        ).fetchone()[0]
        self.assertEqual(current, first.id.value)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.execute(
                "UPDATE inventory_v2_snapshots SET canonical_observation='{}'"
            )

    def test_rejected_or_expired_publication_is_atomic(self) -> None:
        start = self.repository.begin(
            ScanId("scan-atomic"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
        ).value
        other_library = LibraryId("31234567-89ab-4def-8123-456789abcdef")
        rejected = self.repository.publish_complete(
            start,
            (self._snapshot("A"), self._snapshot("B", library=other_library)),
            ScanUsage(2, 2, 100),
            NOW + timedelta(seconds=1),
            operation_context(),
        )
        self.assertEqual(rejected.outcome.value, "rejected")
        for table in (
            "inventory_v2_snapshots",
            "current_inventory_v2",
            "inventory_scan_items",
        ):
            self.assertEqual(
                self.database.execute(f"SELECT COUNT(*) FROM {table}").fetchone(),
                (0,),
            )

        clock = [NOW]
        expired = OperationContext(
            Deadline(NOW + timedelta(seconds=1)),
            CancellationProbe(CancellationId("expired-publication"), lambda: False),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: clock[0]),
        )
        clock[0] = NOW + timedelta(seconds=2)
        result = self.repository.publish_complete(
            start,
            (self._snapshot("A"),),
            ScanUsage(2, 1, 100),
            clock[0],
            expired,
        )
        self.assertEqual(result.outcome.value, "unavailable")
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots"
            ).fetchone(),
            (0,),
        )

    def test_unavailable_snapshot_and_locator_publish_atomically(self) -> None:
        unavailable = self._snapshot(
            "Unavailable", lifecycle=ItemLifecycle.UNAVAILABLE
        )
        inspection = self._publish("scan-unavailable", unavailable)
        self.assertEqual(inspection.state, ScanState.COMPLETE)
        self.assertEqual(
            self.database.execute(
                "SELECT c.snapshot_id,i.lifecycle FROM current_inventory_v2 c "
                "JOIN inventory_snapshot_locator_index i USING "
                "(adapter_id,connection_id,library_id,external_item,snapshot_id)"
            ).fetchone(),
            (unavailable.id.value, "unavailable"),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT state FROM inventory_scans WHERE scan_id='scan-unavailable'"
            ).fetchone(),
            (ScanState.COMPLETE.value,),
        )

    def test_bad_optional_epub_projection_does_not_abort_multi_item_scan(self) -> None:
        mapping = InventoryMountMapping(
            "books", "ebook", "/library/books", "a" * 64
        )

        def normalized(identity: str, relative: object) -> InventorySnapshotV2:
            detail = AudiobookshelfInventoryReaderTests._detail(
                identity,
                {
                    "ebookFile": {
                        "ino": identity,
                        "ebookFormat": "epub",
                        "metadata": {
                            "path": f"/library/books/{identity}.epub",
                            "relPath": relative,
                            "size": 123,
                        },
                    }
                },
            )
            summary = {
                key: detail[key]
                for key in (
                    "id",
                    "libraryId",
                    "mediaType",
                    "updatedAt",
                    "isMissing",
                    "isInvalid",
                )
            }
            result = _normalize(
                detail,
                summary,
                connection(),
                LIBRARY,
                operation_context(),
                (mapping,),
            )
            self.assertIsNone(result.failure)
            return result.value[0]

        good = normalized(EBOOK, f"{EBOOK}.epub")
        bad = normalized("31234567-89ab-4def-8123-456789abcdef", ["bad.epub"])
        self._publish("scan-good-projection-seed", good)
        inspection = self._publish_many(
            "scan-optional-projection", (good, bad)
        )
        self.assertEqual(inspection.state, ScanState.COMPLETE)
        self.assertEqual(inspection.item_count, 2)
        self.assertEqual(inspection.active_count, 2)
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM current_inventory_v2"
            ).fetchone(),
            (2,),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_snapshot_file_bindings"
            ).fetchone(),
            (1,),
        )
        selectors = SQLiteInventoryV2Repository(
            self.database,
            binding_configuration_digests=frozenset({mapping.configuration_digest}),
            owner_id="projection-selector-test",
        )._selectors("audiobookshelf", "primary", LIBRARY.value)
        self.assertEqual(len(selectors), 1)
        self.assertEqual(selectors[0].target_reference, opaque_target_reference(good.observation.target))

    def test_late_cancellation_rolls_back_every_publication_write(self) -> None:
        start = self.repository.begin(
            ScanId("scan-late-cancel"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
        ).value
        cancellation = {"requested": False}

        context = OperationContext(
            Deadline(NOW + timedelta(minutes=5)),
            CancellationProbe(
                CancellationId("late-cancel"),
                lambda: cancellation["requested"],
            ),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: NOW),
        )
        inspect = self.repository._inspection_for

        def cancel_after_inspection(*args):
            result = inspect(*args)
            cancellation["requested"] = True
            return result

        self.repository._inspection_for = cancel_after_inspection
        try:
            result = self.repository.publish_complete(
                start,
                (self._snapshot("A"),),
                ScanUsage(2, 1, 100),
                NOW + timedelta(seconds=1),
                context,
            )
        finally:
            self.repository._inspection_for = inspect
        self.assertEqual(result.outcome.value, "unavailable")
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT state FROM inventory_scans WHERE scan_id='scan-late-cancel'"
            ).fetchone(),
            ("running",),
        )

    def test_mid_source_liveness_failure_terminalizes_and_immediately_retries(
        self,
    ) -> None:
        current = self._snapshot("Stable")
        self._publish("scan-mid-source-seed", current)
        cases = tuple(
            (kind, stage, mode)
            for kind in (FailureKind.CANCELLED, FailureKind.TIMEOUT)
            for stage in ("verify", "first", "second")
            for mode in ("failure", "exception", "success")
        )
        for index, (kind, stage, mode) in enumerate(cases):
            with self.subTest(kind=kind, stage=stage, mode=mode):
                observed = NOW + timedelta(minutes=20 + index * 20)
                clock = [observed]
                cancellation = {"requested": False}
                deadline = observed + timedelta(minutes=5)

                def trigger(selected=kind) -> None:
                    if selected is FailureKind.CANCELLED:
                        cancellation["requested"] = True
                    else:
                        clock[0] = deadline

                context = OperationContext(
                    Deadline(deadline),
                    CancellationProbe(
                        CancellationId(f"mid-source-{kind.value}-{stage}-{mode}"),
                        lambda: cancellation["requested"],
                    ),
                    ResourceBounds(100, 1_000_000),
                    TimeProbe(lambda: clock[0]),
                )
                before_current = self.database.execute(
                    "SELECT external_item,snapshot_id FROM current_inventory_v2"
                ).fetchall()
                before_history = self.database.execute(
                    "SELECT COUNT(*) FROM inventory_v2_snapshots"
                ).fetchone()[0]
                result = ReconcileInventory(
                    _LivenessFailureSource(trigger, stage, mode, (current,)),
                    self.repository,
                ).run(connection(), (LIBRARY,), self.bounds, context)

                self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
                inspection = result.libraries[0].inspection
                self.assertEqual(inspection.state, ScanState.INCOMPLETE)
                self.assertEqual(inspection.failure_kind, kind.value)
                prior_bytes = {"verify": 0, "first": 1, "second": 11}[stage]
                prior_pages = {"verify": 0, "first": 0, "second": 1}[stage]
                added_bytes = (
                    (13 if stage == "verify" else 17) if mode == "success" else 0
                )
                added_pages = 1 if mode == "success" and stage != "verify" else 0
                self.assertEqual(inspection.response_bytes, prior_bytes + added_bytes)
                self.assertEqual(inspection.page_count, prior_pages + added_pages)
                self._assert_terminal_failure_preserved_state(
                    inspection.scan_id, before_current, before_history
                )

                retry = ReconcileInventory(
                    _PassSource((InventoryPass((current,), ScanUsage(1, 1, 10)),) * 2),
                    self.repository,
                ).run(
                    connection(),
                    (LIBRARY,),
                    self.bounds,
                    self._later_context(30 + index * 20),
                )
                self.assertEqual(retry.outcome, InventoryOutcome.READY)

    def test_connection_verification_follows_all_cohort_begins_and_terminalizes(
        self,
    ) -> None:
        other = self._other_library()

        def verify() -> None:
            rows = self.database.execute(
                "SELECT library_id,state FROM inventory_scans ORDER BY library_id"
            ).fetchall()
            self.assertEqual(
                rows,
                sorted([(LIBRARY.value, "running"), (other.value, "running")]),
            )

        source = _ConnectionVerificationSource(
            verify,
            InventoryConnectionVerification(
                ScanUsage(response_bytes=19),
                PortFailure(
                    FailureKind.AUTHENTICATION,
                    RetryAdvice(False),
                    SafeSummary("inventory authentication failed"),
                ),
            ),
        )
        result = ReconcileInventory(source, self.repository).run(
            connection(), (LIBRARY, other), self.bounds, operation_context()
        )

        self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
        self.assertEqual(source.library_calls, 0)
        self.assertEqual(
            [item.inspection.failure_kind for item in result.libraries],
            [FailureKind.AUTHENTICATION.value] * 2,
        )
        self.assertEqual(
            [item.inspection.response_bytes for item in result.libraries], [19, 19]
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
            ).fetchone(),
            (0,),
        )

    def test_cohort_failure_cannot_publish_removal_until_later_complete_poll(self) -> None:
        other = self._other_library()
        original = self._snapshot("Still authoritative")
        self._publish("scan-cohort-removal-seed", original)
        before = self.database.execute(
            "SELECT external_item,snapshot_id FROM current_inventory_v2 "
            "WHERE library_id=?",
            (LIBRARY.value,),
        ).fetchall()

        partial = ReconcileInventory(
            _LibrarySource({LIBRARY: (), other: None}), self.repository
        ).run(
            connection(),
            (LIBRARY, other),
            self.bounds,
            self._later_context(40),
        )

        self.assertFalse(partial.succeeded)
        self.assertEqual(
            self.database.execute(
                "SELECT external_item,snapshot_id FROM current_inventory_v2 "
                "WHERE library_id=?",
                (LIBRARY.value,),
            ).fetchall(),
            before,
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots"
            ).fetchone(),
            (1,),
        )

        repaired = ReconcileInventory(
            _LibrarySource({LIBRARY: (), other: ()}), self.repository
        ).run(
            connection(),
            (LIBRARY, other),
            self.bounds,
            self._later_context(50),
        )
        self.assertTrue(repaired.succeeded)
        inspected = self.repository.inspect(
            connection(), LIBRARY, self._later_context(51)
        )
        self.assertEqual(inspected.value.removed_count, 1)

    def test_changed_late_cohort_precondition_rolls_back_earlier_members(self) -> None:
        other = self._other_library()
        cohort = "cohort-atomic-preconditions"
        first = self.repository.begin(
            ScanId("scan-cohort-atomic-a"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
            cohort_id=cohort,
            cohort_size=2,
        ).value
        second = self.repository.begin(
            ScanId("scan-cohort-atomic-b"),
            connection(),
            other,
            self.bounds,
            NOW,
            operation_context(),
            cohort_id=cohort,
            cohort_size=2,
        ).value
        self.repository.publish_complete(
            first,
            (self._snapshot("First"),),
            ScanUsage(1, 1, 10),
            NOW + timedelta(seconds=1),
            operation_context(),
        )
        original = self.repository._current_values
        calls = [0]

        def changed_on_final_validation(adapter, target_connection, library):
            if library == other.value:
                calls[0] += 1
                if calls[0] == 2:
                    return (self._snapshot("Concurrent", library=other),)
            return original(adapter, target_connection, library)

        self.repository._current_values = changed_on_final_validation
        try:
            result = self.repository.publish_complete(
                second,
                (self._snapshot("Second", library=other),),
                ScanUsage(1, 1, 10),
                NOW + timedelta(seconds=1),
                operation_context(),
            )
        finally:
            self.repository._current_values = original

        self.assertEqual(result.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM current_inventory_v2"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots"
            ).fetchone(),
            (0,),
        )

    def test_cohort_applies_operation_bounds_per_library_not_to_aggregate(self) -> None:
        other = self._other_library()
        context = OperationContext(
            Deadline(NOW + timedelta(minutes=5)),
            CancellationProbe(CancellationId("cohort-member-bounds"), lambda: False),
            ResourceBounds(1, 1_000_000),
            TimeProbe(lambda: NOW),
        )
        cohort = "cohort-member-bounds"
        first = self.repository.begin(
            ScanId("scan-member-bounds-a"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            context,
            cohort_id=cohort,
            cohort_size=2,
        ).value
        second = self.repository.begin(
            ScanId("scan-member-bounds-b"),
            connection(),
            other,
            self.bounds,
            NOW,
            context,
            cohort_id=cohort,
            cohort_size=2,
        ).value

        held = self.repository.publish_complete(
            first,
            (self._snapshot("First"),),
            ScanUsage(1, 1, 10),
            NOW + timedelta(seconds=1),
            context,
        )
        completed = self.repository.publish_complete(
            second,
            (self._snapshot("Second", library=other),),
            ScanUsage(1, 1, 10),
            NOW + timedelta(seconds=1),
            context,
        )

        self.assertTrue(held.accepted)
        self.assertTrue(completed.accepted)
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM current_inventory_v2"
            ).fetchone(),
            (2,),
        )

    def test_stale_poll_fence_cannot_publish_after_reclaim(self) -> None:
        polling = SQLiteInventoryPollingRepository(
            self.database, now=lambda: NOW
        )
        request = PollRequest(
            "poll_stale",
            "trigger_stale",
            "a" * 64,
            "b" * 64,
            PollPolicy(3, 300, 30, 1, 10, 30, 300, 30_000),
        )
        polling.schedule(request, NOW)
        stale = polling.claim("poll_stale", "worker_old", NOW).value
        started = self.repository.begin(
            ScanId("scan-stale-fence"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
            claim=stale,
        )
        self.assertTrue(started.accepted)
        checkpointed = self.repository.checkpoint(
            started.value.scan_id,
            ScanUsage(2, 3, 400),
            NOW + timedelta(seconds=1),
            operation_context(),
            claim=stale,
        )
        self.assertTrue(checkpointed.accepted)

        replacement = polling.claim(
            "poll_stale", "worker_new", NOW + timedelta(seconds=31)
        )
        self.assertTrue(replacement.accepted)
        self.assertEqual(
            polling.inspect("poll_stale").value.usage,
            ScanUsage(2, 3, 400),
        )
        late_context = self._later_context(1)
        published = self.repository.publish_complete(
            started.value,
            (self._snapshot("Too late"),),
            ScanUsage(1, 1, 10),
            NOW + timedelta(seconds=31),
            late_context,
            claim=stale,
        )

        self.assertFalse(published.accepted)
        self.assertEqual(
            self.database.execute(
                "SELECT state,failure_kind FROM inventory_scans WHERE scan_id=?",
                ("scan-stale-fence",),
            ).fetchone(),
            ("incomplete", "interrupted"),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM current_inventory_v2"
            ).fetchone(),
            (0,),
        )

    def test_poll_failure_terminalizes_linked_scan_and_keeps_checkpoint_usage(
        self,
    ) -> None:
        polling = SQLiteInventoryPollingRepository(
            self.database, now=lambda: NOW
        )
        polling.schedule(
            PollRequest(
                "poll_exception",
                "trigger_exception",
                "c" * 64,
                "d" * 64,
                PollPolicy(3, 300, 30, 1, 10, 30, 300, 30_000),
            ),
            NOW,
        )
        claim = polling.claim("poll_exception", "worker_exception", NOW).value
        started = self.repository.begin(
            ScanId("scan-exception-cleanup"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
            claim=claim,
        ).value
        self.repository.checkpoint(
            started.scan_id,
            ScanUsage(2, 3, 400),
            NOW + timedelta(seconds=1),
            operation_context(),
            claim=claim,
        )

        failed = polling.fail(
            claim,
            PortFailure(
                FailureKind.UNAVAILABLE,
                RetryAdvice(True),
                SafeSummary("inventory operation failed safely"),
            ),
            ScanUsage(),
            NOW + timedelta(seconds=2),
            compatibility="supported",
        )

        self.assertEqual(failed.value.usage, ScanUsage(2, 3, 400))
        self.assertEqual(
            self.database.execute(
                "SELECT state,failure_kind FROM inventory_scans WHERE scan_id=?",
                (started.scan_id.value,),
            ).fetchone(),
            ("incomplete", FailureKind.UNAVAILABLE.value),
        )

    def test_poll_exception_usage_includes_terminal_and_running_siblings(self) -> None:
        polling = SQLiteInventoryPollingRepository(
            self.database, now=lambda: NOW
        )
        polling.schedule(
            PollRequest(
                "poll_mixed_exception",
                "trigger_mixed_exception",
                "e" * 64,
                "f" * 64,
                PollPolicy(3, 300, 30, 1, 10, 30, 300, 30_000),
            ),
            NOW,
        )
        claim = polling.claim(
            "poll_mixed_exception", "worker_mixed_exception", NOW
        ).value
        other = self._other_library()
        first = self.repository.begin(
            ScanId("scan-mixed-terminal"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
            claim=claim,
        ).value
        second = self.repository.begin(
            ScanId("scan-mixed-running"),
            connection(),
            other,
            self.bounds,
            NOW,
            operation_context(),
            claim=claim,
        ).value
        self.repository.finish(
            first.scan_id,
            ScanState.INCOMPLETE,
            FailureKind.UNAVAILABLE.value,
            ScanUsage(2, 3, 400),
            NOW + timedelta(seconds=1),
            operation_context(),
            claim=claim,
        )
        self.repository.checkpoint(
            second.scan_id,
            ScanUsage(1, 2, 300),
            NOW + timedelta(seconds=1),
            operation_context(),
            claim=claim,
        )

        failed = polling.fail(
            claim,
            PortFailure(
                FailureKind.UNAVAILABLE,
                RetryAdvice(True),
                SafeSummary("inventory operation failed safely"),
            ),
            ScanUsage(),
            NOW + timedelta(seconds=2),
            compatibility="supported",
        )

        self.assertEqual(failed.value.usage, ScanUsage(3, 5, 700))

    def test_failed_pass_usage_is_durable_and_not_reset(self) -> None:
        result = ReconcileInventory(
            _FailedUsageSource(), self.repository
        ).run(connection(), (LIBRARY,), self.bounds, operation_context())

        inspection = result.libraries[0].inspection
        usage = result.libraries[0].usage
        self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
        self.assertEqual(usage, ScanUsage(2, 3, 401))
        self.assertEqual(inspection.page_count, 2)
        self.assertEqual(inspection.response_bytes, 401)

    def test_cohort_begin_failure_stops_before_connection_verification(self) -> None:
        other = self._other_library()
        source = _ConnectionVerificationSource(
            lambda: None, InventoryConnectionVerification(ScanUsage())
        )
        begin = self.repository.begin

        def reject_second(scan_id, connection_value, library, *args, **kwargs):
            if library == other:
                return RepositoryResult(RepositoryOutcome.REJECTED)
            return begin(scan_id, connection_value, library, *args, **kwargs)

        self.repository.begin = reject_second
        try:
            result = ReconcileInventory(source, self.repository).run(
                connection(), (LIBRARY, other), self.bounds, operation_context()
            )
        finally:
            self.repository.begin = begin

        self.assertEqual(result.outcome, InventoryOutcome.PERSISTENCE_FAILURE)
        self.assertEqual(source.connection_calls, 0)
        self.assertEqual(source.library_calls, 0)
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
            ).fetchone(),
            (0,),
        )

    def test_cohort_begin_liveness_failure_preserves_exact_terminal_kind(self) -> None:
        other = self._other_library()
        for index, kind in enumerate((FailureKind.CANCELLED, FailureKind.TIMEOUT)):
            with self.subTest(kind=kind):
                source = _ConnectionVerificationSource(
                    lambda: None, InventoryConnectionVerification(ScanUsage())
                )
                observed = NOW + timedelta(hours=8 + index)
                clock = [observed]
                cancelled = [False]
                deadline = observed + timedelta(minutes=5)
                selected_context = OperationContext(
                    Deadline(deadline),
                    CancellationProbe(
                        CancellationId(f"cohort-begin-{kind.value}"),
                        lambda: cancelled[0],
                    ),
                    ResourceBounds(100, 1_000_000),
                    TimeProbe(lambda: clock[0]),
                )
                begin = self.repository.begin

                def fail_second(scan_id, connection_value, library, *args, **kwargs):
                    if library == other:
                        if kind is FailureKind.CANCELLED:
                            cancelled[0] = True
                        else:
                            clock[0] = deadline
                        return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                    return begin(scan_id, connection_value, library, *args, **kwargs)

                self.repository.begin = fail_second
                try:
                    result = ReconcileInventory(source, self.repository).run(
                        connection(),
                        (LIBRARY, other),
                        self.bounds,
                        selected_context,
                    )
                finally:
                    self.repository.begin = begin

                self.assertEqual(result.outcome, InventoryOutcome.PERSISTENCE_FAILURE)
                self.assertEqual(source.connection_calls, 0)
                inspection = result.libraries[0].inspection
                self.assertIsNotNone(inspection)
                self.assertEqual(inspection.failure_kind, kind.value)
                self.assertEqual(
                    self.database.execute(
                        "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
                    ).fetchone(),
                    (0,),
                )

    def test_late_publication_liveness_failure_rolls_back_terminalizes_and_retries(
        self,
    ) -> None:
        current = self._snapshot("Stable")
        self._publish("scan-late-publication-seed", current)
        for index, kind in enumerate((FailureKind.CANCELLED, FailureKind.TIMEOUT)):
            with self.subTest(kind=kind):
                observed = NOW + timedelta(minutes=70 + index * 20)
                clock = [observed]
                cancellation = {"requested": False}
                deadline = observed + timedelta(minutes=5)
                context = OperationContext(
                    Deadline(deadline),
                    CancellationProbe(
                        CancellationId(f"late-publication-{kind.value}"),
                        lambda: cancellation["requested"],
                    ),
                    ResourceBounds(100, 1_000_000),
                    TimeProbe(lambda: clock[0]),
                )
                before_current = self.database.execute(
                    "SELECT external_item,snapshot_id FROM current_inventory_v2"
                ).fetchall()
                before_history = self.database.execute(
                    "SELECT COUNT(*) FROM inventory_v2_snapshots"
                ).fetchone()[0]
                persist = self.repository._persist_or_reuse

                def fail_after_persist(snapshot, selected=kind):
                    value = persist(snapshot)
                    if selected is FailureKind.CANCELLED:
                        cancellation["requested"] = True
                    else:
                        clock[0] = deadline
                    return value

                self.repository._persist_or_reuse = fail_after_persist
                try:
                    result = ReconcileInventory(
                        _PassSource(
                            (InventoryPass((current,), ScanUsage(1, 1, 10)),) * 2
                        ),
                        self.repository,
                    ).run(connection(), (LIBRARY,), self.bounds, context)
                finally:
                    self.repository._persist_or_reuse = persist

                self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
                inspection = result.libraries[0].inspection
                self.assertEqual(inspection.state, ScanState.INCOMPLETE)
                self.assertEqual(inspection.failure_kind, kind.value)
                self._assert_terminal_failure_preserved_state(
                    inspection.scan_id, before_current, before_history
                )

                retry = ReconcileInventory(
                    _PassSource((InventoryPass((current,), ScanUsage(1, 1, 10)),) * 2),
                    self.repository,
                ).run(
                    connection(),
                    (LIBRARY,),
                    self.bounds,
                    self._later_context(80 + index * 20),
                )
                self.assertEqual(retry.outcome, InventoryOutcome.READY)

    def test_interrupted_scan_recovers_without_publishing_removals(self) -> None:
        begun = self.repository.begin(
            ScanId("scan-running"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
        )
        self.assertTrue(begun.accepted)
        restarted = SQLiteInventoryV2Repository(
            self.database,
            owner_id="owner-b",
            owner_alive=lambda _owner: False,
        )
        recovered = restarted.recover_interrupted(
            NOW + timedelta(seconds=1), operation_context()
        )
        self.assertEqual(recovered.value, 1)
        row = self.database.execute(
            "SELECT state,failure_kind FROM inventory_scans WHERE scan_id='scan-running'"
        ).fetchone()
        self.assertEqual(row, ("incomplete", "interrupted"))

    def test_same_pid_with_different_process_start_recovers_immediately(self) -> None:
        prior = SQLiteInventoryV2Repository(
            self.database,
            owner_id=f"pid-{os.getpid()}-start-{'0' * 16}",
        )
        begun = prior.begin(
            ScanId("scan-prior-incarnation"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
        )
        self.assertTrue(begun.accepted)
        restarted = SQLiteInventoryV2Repository(self.database)
        recovered = restarted.recover_interrupted(
            NOW + timedelta(seconds=1), operation_context()
        )
        self.assertEqual(recovered.value, 1)

    def test_connection_scoped_library_movement_is_reported(self) -> None:
        other = LibraryId("31234567-89ab-4def-8123-456789abcdef")
        original = self._snapshot("Title")
        moved = self._snapshot("Title", library=other)
        self._publish("scan-original", original)
        self._publish(
            "scan-moved", moved, library=other, cohort_id="cohort-move", cohort_size=2
        )
        inspection = self._publish(
            "scan-removed",
            original.removed(observed_at=NOW + timedelta(seconds=2)),
            cohort_id="cohort-move",
            cohort_size=2,
        )
        self.assertEqual(inspection.moved_count, 1)

    def test_movement_ignores_stale_or_disabled_library_state(self) -> None:
        other = LibraryId("31234567-89ab-4def-8123-456789abcdef")
        original = self._snapshot("Title")
        self._publish(
            "scan-old-other", self._snapshot("Title", library=other), library=other
        )
        self._publish("scan-current", original)
        inspection = self._publish(
            "scan-current-removed",
            original.removed(observed_at=NOW + timedelta(seconds=2)),
        )
        self.assertEqual(inspection.moved_count, 0)

    def test_exact_scan_inspection_ignores_newer_concurrent_complete_scan(self) -> None:
        completed = self._publish("scan-completed", self._snapshot("Title"))
        newer = self.repository.begin(
            ScanId("scan-newer-running"),
            connection(),
            LIBRARY,
            self.bounds,
            NOW + timedelta(minutes=10),
            self._later_context(),
        )
        self.assertTrue(newer.accepted)
        newer_result = self.repository.publish_complete(
            newer.value,
            (self._snapshot("Title").removed(observed_at=NOW + timedelta(minutes=10)),),
            ScanUsage(2, 1, 100),
            NOW + timedelta(minutes=10, seconds=1),
            self._later_context(),
        )
        self.assertTrue(newer_result.accepted)
        scope = self.repository.inspect(connection(), LIBRARY, self._later_context())
        self.assertEqual(scope.value.state, ScanState.COMPLETE)
        self.assertEqual(scope.value.active_count, 0)
        self.assertEqual(scope.value.removed_count, 1)
        exact = self.repository.inspect_scan(completed.scan_id, self._later_context())
        self.assertEqual(exact.value.scan_id, completed.scan_id)
        self.assertEqual(exact.value.state, ScanState.COMPLETE)
        self.assertEqual(exact.value.active_count, 1)
        self.assertEqual(exact.value.removed_count, 0)

    def test_reconciliation_reports_source_first_movement_consistently(self) -> None:
        self._assert_reconciled_movement((LIBRARY, self._other_library()))

    def test_reconciliation_reports_destination_first_movement_consistently(
        self,
    ) -> None:
        self._assert_reconciled_movement((self._other_library(), LIBRARY))

    def test_partial_cohort_does_not_report_movement(self) -> None:
        other = self._other_library()
        original = self._snapshot("Title")
        self._publish("scan-partial-seed", original)
        source = _LibrarySource(
            {
                LIBRARY: (),
                other: None,
            }
        )
        result = ReconcileInventory(source, self.repository).run(
            connection(), (LIBRARY, other), self.bounds, operation_context()
        )
        source_result = next(
            item for item in result.libraries if item.library == LIBRARY
        )
        self.assertEqual(source_result.inspection.moved_count, 0)
        inspected = self.repository.inspect(connection(), LIBRARY, operation_context())
        self.assertEqual(inspected.value.moved_count, 0)

    def test_disappearance_requires_equal_complete_passes(self) -> None:
        first = self._snapshot("Title A")
        self._publish("scan-seed", first)
        source = _PassSource((InventoryPass((), ScanUsage(1, 0, 10)),) * 2)
        clock = [NOW + timedelta(minutes=10)]
        context = OperationContext(
            Deadline(clock[0] + timedelta(minutes=10)),
            CancellationProbe(CancellationId("reconcile-test"), lambda: False),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: clock[0]),
        )
        result = ReconcileInventory(source, self.repository).run(
            connection(), (LIBRARY,), self.bounds, context
        )
        self.assertEqual(result.outcome, InventoryOutcome.READY)
        self.assertEqual(result.libraries[0].inspection.removed_count, 1)
        removed_history = result.libraries[0].inspection.history_count

        restarted = SQLiteInventoryV2Repository(self.database)
        clock[0] += timedelta(minutes=1)
        retained = ReconcileInventory(
            _PassSource((InventoryPass((), ScanUsage(1, 0, 10)),) * 2), restarted
        ).run(connection(), (LIBRARY,), self.bounds, context)
        self.assertEqual(retained.outcome, InventoryOutcome.READY)
        self.assertEqual(retained.libraries[0].inspection.item_count, 1)
        self.assertEqual(retained.libraries[0].inspection.removed_count, 1)
        self.assertEqual(
            retained.libraries[0].inspection.history_count, removed_history
        )
        membership = self.database.execute(
            "SELECT external_item,snapshot_id FROM inventory_scan_items "
            "WHERE scan_id=?",
            (retained.libraries[0].inspection.scan_id.value,),
        ).fetchall()
        current = self.database.execute(
            "SELECT external_item,snapshot_id FROM current_inventory_v2"
        ).fetchall()
        self.assertEqual(membership, current)

        clock[0] += timedelta(minutes=10)
        context = OperationContext(
            Deadline(clock[0] + timedelta(minutes=10)),
            CancellationProbe(CancellationId("reconcile-test-later"), lambda: False),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: clock[0]),
        )
        unstable = _PassSource(
            (
                InventoryPass((first,), ScanUsage(1, 1, 10)),
                InventoryPass((), ScanUsage(1, 0, 10)),
            )
        )
        result = ReconcileInventory(unstable, self.repository).run(
            connection(), (LIBRARY,), self.bounds, context
        )
        self.assertEqual(result.outcome, InventoryOutcome.UNSTABLE)
        self.assertEqual(result.libraries[0].inspection.removed_count, 1)

    def test_reappearance_and_mixed_retained_removal_publish_complete_membership(
        self,
    ) -> None:
        active = self._snapshot("Active")
        other = self._snapshot_for_item("31234567-89ab-4def-8123-456789abcdef")
        self._publish_many("scan-mixed-seed", (active, other))
        removed_other = other.removed(observed_at=NOW + timedelta(minutes=1))
        self._publish_many("scan-mixed-removed", (active, removed_other))

        source = _PassSource((InventoryPass((active,), ScanUsage(1, 1, 10)),) * 2)
        retained = ReconcileInventory(source, self.repository).run(
            connection(), (LIBRARY,), self.bounds, self._later_context()
        )
        self.assertEqual(retained.libraries[0].inspection.item_count, 2)
        self.assertEqual(retained.libraries[0].inspection.removed_count, 1)

        reappeared = ReconcileInventory(
            _PassSource((InventoryPass((active, other), ScanUsage(1, 2, 10)),) * 2),
            self.repository,
        ).run(connection(), (LIBRARY,), self.bounds, self._later_context(20))
        self.assertEqual(reappeared.outcome, InventoryOutcome.READY)
        self.assertEqual(reappeared.libraries[0].inspection.item_count, 2)
        self.assertEqual(reappeared.libraries[0].inspection.active_count, 2)

    def test_publication_repository_outcomes_keep_their_failure_identity(self) -> None:
        source = _PassSource(
            (InventoryPass((self._snapshot("Title"),), ScanUsage(1, 1, 10)),) * 8
        )
        original = self.repository.publish_complete
        cases = (
            (
                RepositoryResult(RepositoryOutcome.UNAVAILABLE),
                InventoryOutcome.PERSISTENCE_FAILURE,
                ScanState.INCOMPLETE,
                FailureKind.UNAVAILABLE,
            ),
            (
                RepositoryResult(RepositoryOutcome.REJECTED),
                InventoryOutcome.UNSTABLE,
                ScanState.UNSTABLE,
                FailureKind.CONFLICT,
            ),
            (
                RepositoryResult(RepositoryOutcome.NOT_FOUND),
                InventoryOutcome.PERSISTENCE_FAILURE,
                ScanState.INCOMPLETE,
                FailureKind.UNAVAILABLE,
            ),
            (
                RepositoryResult(RepositoryOutcome.ACCEPTED, "malformed"),
                InventoryOutcome.PERSISTENCE_FAILURE,
                ScanState.INCOMPLETE,
                FailureKind.UNAVAILABLE,
            ),
        )
        for index, (repository_result, expected, state, failure_kind) in enumerate(
            cases
        ):
            with self.subTest(outcome=repository_result.outcome):
                self.repository.publish_complete = (
                    lambda *_args, selected=repository_result, **_kwargs: selected
                )
                result = ReconcileInventory(source, self.repository).run(
                    connection(),
                    (LIBRARY,),
                    self.bounds,
                    self._later_context(30 + index * 10),
                )
                self.assertEqual(result.outcome, expected)
                self.assertEqual(result.libraries[0].inspection.state, state)
                self.assertEqual(
                    result.libraries[0].inspection.failure_kind, failure_kind.value
                )
        self.repository.publish_complete = original

    def test_final_membership_resource_overflow_is_typed_and_atomic(self) -> None:
        active = self._snapshot("Title")
        self._publish("scan-overflow-seed", active)
        removed = active.removed(observed_at=NOW + timedelta(minutes=10))
        current_bytes = len(active.observation.canonical.encode("utf-8"))
        final_bytes = len(removed.observation.canonical.encode("utf-8"))
        self.assertGreater(final_bytes, current_bytes)
        observed = NOW + timedelta(minutes=10)
        context = OperationContext(
            Deadline(observed + timedelta(minutes=5)),
            CancellationProbe(CancellationId("membership-overflow"), lambda: False),
            ResourceBounds(10, final_bytes - 1),
            TimeProbe(lambda: observed),
        )
        before = self.database.execute(
            "SELECT external_item,snapshot_id FROM current_inventory_v2"
        ).fetchall()
        result = ReconcileInventory(
            _PassSource((InventoryPass((), ScanUsage(1, 0, 10)),) * 2),
            self.repository,
        ).run(connection(), (LIBRARY,), self.bounds, context)
        self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
        self.assertEqual(
            result.libraries[0].inspection.failure_kind,
            FailureKind.RESOURCE_EXHAUSTED.value,
        )
        self.assertEqual(
            self.database.execute(
                "SELECT external_item,snapshot_id FROM current_inventory_v2"
            ).fetchall(),
            before,
        )

    def test_begin_and_inspect_preflight_current_before_hydration(self) -> None:
        seeded = self._publish("scan-bounded-seed", self._snapshot("T" * 2_048))
        context = OperationContext(
            Deadline(NOW + timedelta(minutes=5)),
            CancellationProbe(CancellationId("bounded-current"), lambda: False),
            ResourceBounds(10, 1),
            TimeProbe(lambda: NOW),
        )
        original = self.repository._current_values
        self.repository._current_values = lambda *_args: (_ for _ in ()).throw(
            AssertionError("current canonical rows were hydrated")
        )
        try:
            begun = self.repository.begin(
                ScanId("scan-bounded-refused"),
                connection(),
                LIBRARY,
                self.bounds,
                NOW,
                context,
            )
            inspected = self.repository.inspect(connection(), LIBRARY, context)
            inspected_scan = self.repository.inspect_scan(seeded.scan_id, context)
            scan_bounded = self.repository.begin(
                ScanId("scan-scan-bounded-refused"),
                connection(),
                LIBRARY,
                ScanBounds(2, 4, 10, 1_024, 300, 0),
                NOW,
                operation_context(),
            )
        finally:
            self.repository._current_values = original
        self.assertEqual(begun.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(inspected.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(inspected_scan.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(scan_bounded.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertIsNone(
            self.database.execute(
                "SELECT 1 FROM inventory_scans WHERE scan_id='scan-bounded-refused'"
            ).fetchone()
        )

    def test_begin_and_inspect_stop_on_cancellation_or_deadline_before_hydration(
        self,
    ) -> None:
        self._publish("scan-preflight-seed", self._snapshot("Title"))
        original_current = self.repository._current_values
        original_inspection = self.repository._inspection_for
        self.repository._current_values = lambda *_args: (_ for _ in ()).throw(
            AssertionError("current canonical rows were hydrated")
        )
        self.repository._inspection_for = lambda *_args: (_ for _ in ()).throw(
            AssertionError("inspection materialization was entered")
        )
        try:
            for label, cancelled, deadline, observed in (
                (
                    "cancelled",
                    True,
                    NOW + timedelta(minutes=5),
                    NOW,
                ),
                (
                    "deadline",
                    False,
                    NOW,
                    NOW,
                ),
            ):
                with self.subTest(label=label):
                    context = OperationContext(
                        Deadline(deadline),
                        CancellationProbe(
                            CancellationId(f"preflight-{label}"),
                            lambda selected=cancelled: selected,
                        ),
                        ResourceBounds(10, 1_000_000),
                        TimeProbe(lambda selected=observed: selected),
                    )
                    begun = self.repository.begin(
                        ScanId(f"scan-preflight-{label}"),
                        connection(),
                        LIBRARY,
                        self.bounds,
                        observed,
                        context,
                    )
                    inspected = self.repository.inspect(connection(), LIBRARY, context)
                    self.assertEqual(begun.outcome, RepositoryOutcome.UNAVAILABLE)
                    self.assertEqual(inspected.outcome, RepositoryOutcome.UNAVAILABLE)
        finally:
            self.repository._current_values = original_current
            self.repository._inspection_for = original_inspection

    def test_residual_byte_floor_terminalizes_resource_exhaustion(self) -> None:
        result = ReconcileInventory(
            _UsageSource(ScanUsage(response_bytes=600)), self.repository
        ).run(
            connection(),
            (LIBRARY,),
            ScanBounds(2, 4, 10, 1_024, 300, 0),
            operation_context(),
        )
        self.assertEqual(result.outcome, InventoryOutcome.INCOMPLETE)
        self.assertEqual(
            result.libraries[0].inspection.failure_kind,
            FailureKind.RESOURCE_EXHAUSTED.value,
        )

    def _publish(
        self,
        scan_id: str,
        snapshot: InventorySnapshotV2,
        *,
        library: LibraryId = LIBRARY,
        cohort_id: str | None = None,
        cohort_size: int = 1,
    ):
        start = self.repository.begin(
            ScanId(scan_id),
            connection(),
            library,
            self.bounds,
            NOW,
            operation_context(),
            cohort_id=cohort_id,
            cohort_size=cohort_size,
        ).value
        result = self.repository.publish_complete(
            start,
            (snapshot,),
            ScanUsage(2, 2, 100),
            NOW + timedelta(seconds=1),
            operation_context(),
        )
        self.assertTrue(result.accepted)
        return result.value

    def _assert_terminal_failure_preserved_state(
        self,
        scan_id: ScanId,
        before_current: list[tuple[object, ...]],
        before_history: int,
    ) -> None:
        state, failure_kind, finished_at = self.database.execute(
            "SELECT state,failure_kind,finished_at FROM inventory_scans WHERE scan_id=?",
            (scan_id.value,),
        ).fetchone()
        self.assertEqual(state, ScanState.INCOMPLETE.value)
        self.assertIn(
            failure_kind, {FailureKind.CANCELLED.value, FailureKind.TIMEOUT.value}
        )
        self.assertIsNotNone(finished_at)
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_scan_items WHERE scan_id=?",
                (scan_id.value,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.database.execute(
                "SELECT external_item,snapshot_id FROM current_inventory_v2"
            ).fetchall(),
            before_current,
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots"
            ).fetchone()[0],
            before_history,
        )
        self.assertEqual(
            self.database.execute(
                "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
            ).fetchone(),
            (0,),
        )

    def _publish_many(
        self, scan_id: str, snapshots: tuple[InventorySnapshotV2, ...]
    ) -> InventoryInspection:
        start = self.repository.begin(
            ScanId(scan_id),
            connection(),
            LIBRARY,
            self.bounds,
            NOW,
            operation_context(),
        ).value
        result = self.repository.publish_complete(
            start,
            snapshots,
            ScanUsage(2, len(snapshots), 100),
            NOW + timedelta(seconds=1),
            operation_context(),
        )
        self.assertTrue(result.accepted)
        return result.value

    def _assert_reconciled_movement(self, order: tuple[LibraryId, ...]) -> None:
        other = self._other_library()
        original = self._snapshot("Title")
        self._publish("scan-movement-seed", original)
        source = _LibrarySource(
            {
                LIBRARY: (),
                other: (self._snapshot("Title", library=other),),
            }
        )
        result = ReconcileInventory(source, self.repository).run(
            connection(), order, self.bounds, self._later_context()
        )
        self.assertEqual(result.outcome, InventoryOutcome.READY)
        source_result = next(
            item for item in result.libraries if item.library == LIBRARY
        )
        self.assertEqual(source_result.inspection.moved_count, 1)
        inspected = self.repository.inspect(
            connection(), LIBRARY, self._later_context()
        )
        self.assertEqual(inspected.value.moved_count, 1)

    @staticmethod
    def _other_library() -> LibraryId:
        return LibraryId("31234567-89ab-4def-8123-456789abcdef")

    @staticmethod
    def _later_context(minutes: int = 10) -> OperationContext:
        observed = NOW + timedelta(minutes=minutes)
        return OperationContext(
            Deadline(observed + timedelta(minutes=5)),
            CancellationProbe(CancellationId("later-context"), lambda: False),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: observed),
        )

    @staticmethod
    def _snapshot(
        title: str,
        *,
        library: LibraryId = LIBRARY,
        lifecycle: ItemLifecycle = ItemLifecycle.ACTIVE,
    ) -> InventorySnapshotV2:
        target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("primary"),
            library,
            ExternalItemId(AUDIO),
        )
        observation = InventoryObservation.create(
            target=target,
            media_type=MediaType.AUDIOBOOK,
            lifecycle=lifecycle,
            fields=(
                FieldObservation(
                    ObservationField.TITLE, ObservationState.PRESENT, (title,)
                ),
            ),
            file_identities=("0" * 64,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=NOW,
        )
        return InventorySnapshotV2.create(observation)

    @staticmethod
    def _snapshot_for_item(external_item: str) -> InventorySnapshotV2:
        snapshot = SQLiteInventoryV2RepositoryTests._snapshot("Other")
        observation = InventoryObservation.create(
            target=TargetId(
                snapshot.observation.target.adapter,
                snapshot.observation.target.connection,
                snapshot.observation.target.library,
                ExternalItemId(external_item),
            ),
            media_type=snapshot.observation.media_type,
            lifecycle=snapshot.observation.lifecycle,
            fields=snapshot.observation.fields,
            file_identities=snapshot.observation.file_identities,
            cover_identity=snapshot.observation.cover_identity,
            adapter_version=snapshot.observation.adapter_version,
            normalizer_version=snapshot.observation.normalizer_version,
            service_version=snapshot.observation.service_version,
            warnings=snapshot.observation.warnings,
            observed_at=snapshot.observation.observed_at,
        )
        return InventorySnapshotV2.create(observation)


class _PassSource:
    def __init__(self, passes: tuple[InventoryPass, ...]) -> None:
        self._passes = list(passes)

    def verify_connection(self, connection, libraries, bounds, context):
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(self, connection, library, bounds, context):
        return PortResult.success(ScanUsage(response_bytes=1))

    def read_pass(self, connection, library, bounds, context):
        if not self._passes:
            return PortResult.failed(
                PortFailure(
                    FailureKind.INVALID_RESPONSE,
                    RetryAdvice(False),
                    SafeSummary("inventory test source exhausted"),
                )
            )
        return PortResult.success(self._passes.pop(0))


class _LibrarySource:
    def __init__(
        self, snapshots: dict[LibraryId, tuple[InventorySnapshotV2, ...] | None]
    ) -> None:
        self._snapshots = snapshots

    def verify_connection(self, connection, libraries, bounds, context):
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(self, connection, library, bounds, context):
        return PortResult.success(ScanUsage(response_bytes=1))

    def read_pass(self, connection, library, bounds, context):
        snapshots = self._snapshots[library]
        if snapshots is None:
            return PortResult.failed(
                PortFailure(
                    FailureKind.UNAVAILABLE,
                    RetryAdvice(True),
                    SafeSummary("inventory test source unavailable"),
                )
            )
        return PortResult.success(
            InventoryPass(snapshots, ScanUsage(1, len(snapshots), 10))
        )


class _UsageSource:
    def __init__(self, usage: ScanUsage) -> None:
        self._usage = usage

    def verify_connection(self, connection, libraries, bounds, context):
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(self, connection, library, bounds, context):
        return PortResult.success(self._usage)

    def read_pass(self, connection, library, bounds, context):
        raise AssertionError("resource exhaustion must stop before inventory I/O")


class _FailedUsageSource:
    def verify_connection(self, connection, libraries, bounds, context):
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(self, connection, library, bounds, context):
        return PortResult.success(ScanUsage(response_bytes=1))

    def read_pass(self, connection, library, bounds, context):
        return PortResult.failed(
            PortFailure(
                FailureKind.UNAVAILABLE,
                RetryAdvice(True),
                SafeSummary("inventory test source unavailable"),
                OperationUsage(2, 3, 400),
            )
        )


class _LivenessFailureSource:
    def __init__(self, trigger, stage, mode, snapshots) -> None:
        self._trigger = trigger
        self._stage = stage
        self._mode = mode
        self._snapshots = snapshots
        self._pass = 0

    def _result(self, success):
        self._trigger()
        if self._mode == "exception":
            raise RuntimeError("synthetic source failure")
        if self._mode == "failure":
            return PortResult.failed(
                PortFailure(
                    FailureKind.AUTHENTICATION,
                    RetryAdvice(False),
                    SafeSummary("inventory test source failure"),
                )
            )
        return PortResult.success(success)

    def verify_connection(self, connection, libraries, bounds, context):
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(self, connection, library, bounds, context):
        if self._stage == "verify":
            return self._result(ScanUsage(response_bytes=13))
        return PortResult.success(ScanUsage(response_bytes=1))

    def read_pass(self, connection, library, bounds, context):
        self._pass += 1
        if self._stage == "first" or (self._stage == "second" and self._pass == 2):
            return self._result(
                InventoryPass(
                    self._snapshots,
                    ScanUsage(1, len(self._snapshots), 17),
                )
            )
        return PortResult.success(
            InventoryPass(
                self._snapshots,
                ScanUsage(1, len(self._snapshots), 10),
            )
        )


class _ConnectionVerificationSource:
    def __init__(self, assertion, verification) -> None:
        self._assertion = assertion
        self._verification = verification
        self.connection_calls = 0
        self.library_calls = 0

    def verify_connection(self, connection, libraries, bounds, context):
        self.connection_calls += 1
        self._assertion()
        return self._verification

    def verify_library(self, connection, library, bounds, context):
        self.library_calls += 1
        raise AssertionError("failed connection verification must stop library I/O")

    def read_pass(self, connection, library, bounds, context):
        raise AssertionError("failed connection verification must stop inventory I/O")


if __name__ == "__main__":
    unittest.main()
