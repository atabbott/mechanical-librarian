from __future__ import annotations

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
    _file_identity,
    _file_objects,
    _identifiers,
    _number,
    _page,
    _scalar,
    _structured,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    PortResult,
    PortFailure,
    RetryAdvice,
    SafeSummary,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryOutcome,
    ReconcileInventory,
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
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanBounds,
    ScanState,
    ScanUsage,
)
from mechanical_librarian.infrastructure.persistence.sqlite import migrate


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LIBRARY = LibraryId("01234567-89ab-4def-8123-456789abcdef")
AUDIO = "11234567-89ab-4def-8123-456789abcdef"
EBOOK = "21234567-89ab-4def-8123-456789abcdef"
FIXTURES = Path(__file__).parents[1] / "fixtures" / "audiobookshelf" / "2.35.1"


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

    def test_source_states_and_file_identity_are_allowlisted(self) -> None:
        self.assertEqual(
            _scalar({"title": 12}, "title", ObservationField.TITLE).state,
            ObservationState.MALFORMED,
        )
        for value, state in (
            ({}, ObservationState.MISSING),
            ({"isbn": None}, ObservationState.NULL),
            ({"isbn": ""}, ObservationState.EMPTY),
            ({"isbn": 12}, ObservationState.MALFORMED),
        ):
            with self.subTest(state=state):
                self.assertEqual(_identifiers(value).state, state)
        objects, state = _file_objects(
            {"audioFiles": [{"ino": "a"}, "malformed"], "ebookFile": None}
        )
        self.assertEqual(state, ObservationState.MALFORMED)
        self.assertEqual(len(objects), 1)
        for malformed_file in ({}, {"unknown": "value"}):
            objects, state = _file_objects(
                {"audioFiles": [malformed_file], "ebookFile": None}
            )
            self.assertEqual(objects, [])
            self.assertEqual(state, ObservationState.MALFORMED)
        base = {"ino": "private/file.m4b", "size": 12}
        self.assertEqual(
            _file_identity(base),
            _file_identity(base | {"unknownPrivateKey": "/private/sentinel"}),
        )
        self.assertNotEqual(_file_identity(base), _file_identity(base | {"size": 13}))
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
        self.repository = SQLiteInventoryV2Repository(self.database)
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

        clock[0] += timedelta(minutes=10)
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
    def _later_context() -> OperationContext:
        observed = NOW + timedelta(minutes=10)
        return OperationContext(
            Deadline(observed + timedelta(minutes=5)),
            CancellationProbe(CancellationId("later-context"), lambda: False),
            ResourceBounds(100, 1_000_000),
            TimeProbe(lambda: observed),
        )

    @staticmethod
    def _snapshot(title: str, *, library: LibraryId = LIBRARY) -> InventorySnapshotV2:
        target = TargetId(
            AdapterId("audiobookshelf"),
            ConnectionId("primary"),
            library,
            ExternalItemId(AUDIO),
        )
        observation = InventoryObservation.create(
            target=target,
            media_type=MediaType.AUDIOBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
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


class _PassSource:
    def __init__(self, passes: tuple[InventoryPass, ...]) -> None:
        self._passes = list(passes)

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

    def verify_library(self, connection, library, bounds, context):
        return PortResult.success(self._usage)

    def read_pass(self, connection, library, bounds, context):
        raise AssertionError("resource exhaustion must stop before inventory I/O")


if __name__ == "__main__":
    unittest.main()
