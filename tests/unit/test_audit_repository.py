from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.application.audit import AuditAppendOutcome, AuditEvent, AuditEventKind, AuditEventId, AuditQueryOutcome, SafeAuditSummary
from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import SchemaError, connect, migrate, transaction
from tests.contract.audit_repository import AuditRepositoryContract


def event(identifier: str, timestamp: str = "2026-07-10T10:00:00Z", summary: str = "discovered item") -> AuditEvent:
    return AuditEvent(AuditEventId(identifier), AuditEventKind("discovery"), datetime.fromisoformat(timestamp.replace("Z", "+00:00")), SafeAuditSummary(summary))


class AuditRepositoryTests(unittest.TestCase):
    def test_append_replay_conflict_and_bounded_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            first = event("event-one")
            self.assertEqual(repository.append(first).outcome, AuditAppendOutcome.APPENDED)
            self.assertEqual(repository.append(first).outcome, AuditAppendOutcome.DUPLICATE)
            self.assertEqual(
                repository.append(event("event-one", summary="item changed")).outcome,
                AuditAppendOutcome.CONFLICT,
            )
            repository.append(event("event-two", "2026-07-11T10:00:00Z"))
            result = repository.recent(1)
            self.assertEqual(result.outcome, AuditQueryOutcome.OK)
            self.assertEqual(result.events, (event("event-two", "2026-07-11T10:00:00Z"),))

    def test_recent_orders_fractional_timestamps_after_exact_second(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            exact = event("event-one", "2026-07-10T10:00:00Z")
            fractional = event("event-two", "2026-07-10T10:00:00.500000Z")
            repository.append(exact)
            repository.append(fractional)
            self.assertEqual(repository.recent(2).events, (fractional, exact))

    def test_recent_orders_mixed_legacy_timestamp_spellings_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:1]):
                migrate(connection)
            rows = (
                ("event-exact", "discovery", "2026-07-10T10:00:00Z", "discovered item"),
                ("event-fractional", "discovery", "2026-07-10T10:00:00.500000+00:00", "item changed"),
                ("event-offset", "discovery", "2026-07-10T11:00:00+01:00", "item skipped"),
                ("event-next", "discovery", "2026-07-10T10:00:01+00:00", "job started"),
            )
            connection.executemany(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)", rows
            )
            migrate(connection)
            result = SQLiteAuditRepository(connection).recent(3)
            self.assertEqual(result.outcome, AuditQueryOutcome.OK)
            self.assertEqual(tuple(str(item.id) for item in result.events), ("event-next", "event-fractional", "event-offset"))

    def test_recent_uses_covering_timestamp_index_without_temporary_sort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            plan = tuple(
                row[3]
                for row in connection.execute(
                    "EXPLAIN QUERY PLAN "
                    "SELECT id, kind, created_at, summary FROM audit_events "
                    "ORDER BY created_at DESC, id DESC LIMIT ?",
                    (10,),
                )
            )
            self.assertTrue(
                any("audit_events_by_created_at" in step for step in plan), plan
            )
            self.assertFalse(any("TEMP B-TREE" in step for step in plan), plan)

    def test_recent_uses_id_as_tie_break_for_equivalent_timestamps_at_limit_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:1]):
                migrate(connection)
            connection.executemany(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                (
                    ("event-a", "discovery", "2026-07-10T10:00:00Z", "discovered item"),
                    ("event-b", "discovery", "2026-07-10T11:00:00+01:00", "item changed"),
                ),
            )
            migrate(connection)
            result = SQLiteAuditRepository(connection).recent(1)
            self.assertEqual(tuple(str(item.id) for item in result.events), ("event-b",))

    def test_legacy_timestamp_row_is_an_idempotent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:1]):
                migrate(connection)
            connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                ("event-one", "discovery", "2026-07-10T10:00:00Z", "discovered item"),
            )
            migrate(connection)
            self.assertEqual(SQLiteAuditRepository(connection).append(event("event-one")).outcome, AuditAppendOutcome.DUPLICATE)

    def test_invalid_and_hostile_values_are_rejected_at_boundary(self) -> None:
        class StringSubclass(str):
            pass

        class AuditEventIdSubclass(AuditEventId):
            pass

        class AuditEventKindSubclass(AuditEventKind):
            pass

        class SafeAuditSummarySubclass(SafeAuditSummary):
            def __str__(self) -> str:
                return "Cookie: session=credential"

        class AuditEventSubclass(AuditEvent):
            pass

        class HostileTimezone(tzinfo):
            def __init__(self, error: Exception) -> None:
                self._error = error

            def utcoffset(self, value: datetime | None) -> timedelta:
                raise self._error

            def dst(self, value: datetime | None) -> timedelta:
                return timedelta(0)

        class OffsetlessTimezone(tzinfo):
            def utcoffset(self, value: datetime | None) -> None:
                return None

            def dst(self, value: datetime | None) -> timedelta:
                return timedelta(0)

        self.assertEqual(str(SafeAuditSummary("authorization granted")), "authorization granted")
        hostile_values: tuple[object, ...] = (
            "https://private.example/token",
            "ssh://private-host/resource",
            "private.example",
            "private-host:8080",
            "127.0.0.1",
            "localhost",
            "/private/library",
            r"C:\Users\alice\Audiobooks",
            "~/private/library",
            "Authorization: Bearer example-credential",
            "Proxy-Authorization: Basic example-credential",
            "Cookie: session=example-credential",
            "Set-Cookie: session=example-credential",
            "Bearer example-credential",
            "Basic example-credential",
            "api_key=example-credential",
            "access-token: example-credential",
            "refresh_token=example-credential",
            "client-secret=example-credential",
            "password=example-credential",
            "token example-credential",
            "secret example-credential",
            "credential example-credential",
            "session-token example-credential",
            "api key example-credential",
            "?api_key=example-credential",
            "::1",
            "2001:db8::1",
            "mailto:private@example.test",
            "urn:example:private",
            '{"provider": "payload"}',
            "provider=[payload]",
            "result={status:ok}",
            "field=value",
            "provider: payload",
            "provider,payload,secret",
            "provider: payload\nsecret: value",
            "provider=value&token=secret",
            "safe\nforged",
            "safe\x00forged",
            b"bytes are not text",
            StringSubclass("subclass text"),
        )
        for hostile_value in hostile_values:
            with self.subTest(hostile_value=repr(hostile_value)), self.assertRaises(ValueError):
                SafeAuditSummary(hostile_value)  # type: ignore[arg-type]
        summary = SafeAuditSummary("authorization granted")
        self.assertEqual(repr(summary), "SafeAuditSummary(<redacted>)")
        self.assertNotIn(summary.value, repr(summary))
        with self.assertRaises(ValueError):
            AuditEvent(
                AuditEventId("event-one"),
                AuditEventKind("discovery"),
                datetime.now(),
                SafeAuditSummary("discovered item"),
            )
        with self.assertRaises(ValueError):
            AuditEvent("event-one", "discovery", datetime.now(timezone.utc), "https://private.example/token")  # type: ignore[arg-type]
        typed_timestamp = datetime.now(timezone.utc)
        typed_id = AuditEventId("event-one")
        typed_kind = AuditEventKind("discovery")
        typed_summary = SafeAuditSummary("discovered item")
        for unsafe_field_set in (
            (
                AuditEventIdSubclass("event-one"),
                typed_kind,
                typed_summary,
            ),
            (
                typed_id,
                AuditEventKindSubclass("discovery"),
                typed_summary,
            ),
            (
                typed_id,
                typed_kind,
                SafeAuditSummarySubclass("discovered item"),
            ),
        ):
            with self.subTest(unsafe_field_set=unsafe_field_set), self.assertRaises(ValueError):
                AuditEvent(
                    unsafe_field_set[0],
                    unsafe_field_set[1],
                    typed_timestamp,
                    unsafe_field_set[2],
                )

        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            unsafe_event = AuditEventSubclass(
                typed_id, typed_kind, typed_timestamp, typed_summary
            )
            self.assertEqual(
                repository.append(unsafe_event).outcome,
                AuditAppendOutcome.INVALID,
            )
            for field, unsafe_value in (
                ("id", "invalid identifier"),
                ("kind", "invalid kind"),
                ("summary", "password=private-value"),
            ):
                with self.subTest(forged_field=field):
                    forged_id = AuditEventId("event-one")
                    forged_kind = AuditEventKind("discovery")
                    forged_summary = SafeAuditSummary("discovered item")
                    object.__setattr__(
                        {"id": forged_id, "kind": forged_kind, "summary": forged_summary}[field],
                        "value",
                        unsafe_value,
                    )
                    forged_event = object.__new__(AuditEvent)
                    object.__setattr__(forged_event, "id", forged_id)
                    object.__setattr__(forged_event, "kind", forged_kind)
                    object.__setattr__(forged_event, "created_at", typed_timestamp)
                    object.__setattr__(forged_event, "summary", forged_summary)
                    self.assertEqual(
                        repository.append(forged_event).outcome,
                        AuditAppendOutcome.INVALID,
                    )
            missing_fields = object.__new__(AuditEvent)
            self.assertEqual(
                repository.append(missing_fields).outcome,
                AuditAppendOutcome.INVALID,
            )
            for hostile_error in (
                RuntimeError("password=private-value"),
                ValueError("token=private-value"),
            ):
                with self.subTest(hostile_error_type=type(hostile_error).__name__):
                    hostile_timestamp = datetime(
                        2026,
                        7,
                        10,
                        10,
                        tzinfo=HostileTimezone(hostile_error),
                    )
                    with self.assertRaisesRegex(
                        ValueError, "audit timestamps must be representable in UTC"
                    ) as raised:
                        AuditEvent(
                            AuditEventId("event-hostile"),
                            AuditEventKind("discovery"),
                            hostile_timestamp,
                            SafeAuditSummary("discovered item"),
                        )
                    self.assertIsNone(raised.exception.__cause__)
                    self.assertNotIn("private-value", str(raised.exception))

                    forged_event = object.__new__(AuditEvent)
                    object.__setattr__(forged_event, "id", AuditEventId("event-hostile"))
                    object.__setattr__(
                        forged_event, "kind", AuditEventKind("discovery")
                    )
                    object.__setattr__(forged_event, "created_at", hostile_timestamp)
                    object.__setattr__(
                        forged_event,
                        "summary",
                        SafeAuditSummary("discovered item"),
                    )
                    self.assertEqual(
                        repository.append(forged_event).outcome,
                        AuditAppendOutcome.INVALID,
                    )
            for offsetless_timestamp in (
                datetime(2026, 7, 10, 10),
                datetime(2026, 7, 10, 10, tzinfo=OffsetlessTimezone()),
            ):
                with self.subTest(
                    offsetless_timezone=type(offsetless_timestamp.tzinfo).__name__
                ):
                    forged_event = object.__new__(AuditEvent)
                    object.__setattr__(
                        forged_event, "id", AuditEventId("event-offsetless")
                    )
                    object.__setattr__(
                        forged_event, "kind", AuditEventKind("discovery")
                    )
                    object.__setattr__(
                        forged_event, "created_at", offsetless_timestamp
                    )
                    object.__setattr__(
                        forged_event,
                        "summary",
                        SafeAuditSummary("discovered item"),
                    )
                    self.assertEqual(
                        repository.append(forged_event).outcome,
                        AuditAppendOutcome.INVALID,
                    )
            self.assertEqual(repository.recent(10).events, ())

    def test_audit_event_requires_exact_utc_representable_datetime(self) -> None:
        with self.assertRaises(ValueError):
            AuditEvent(
                AuditEventId("event-min"),
                AuditEventKind("discovery"),
                datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
                SafeAuditSummary("discovered item"),
            )
        with self.assertRaises(ValueError):
            AuditEvent(
                AuditEventId("event-max"),
                AuditEventKind("discovery"),
                datetime.max.replace(tzinfo=timezone(-timedelta(hours=14))),
                SafeAuditSummary("discovered item"),
            )
        self.assertEqual(
            event(
                "event-min-valid",
                "0001-01-01T00:00:00-14:00",
            ).created_at.utcoffset(),
            -timedelta(hours=14),
        )
        self.assertEqual(
            event(
                "event-max-valid",
                "9999-12-31T23:59:59.999999+14:00",
            ).created_at.utcoffset(),
            timedelta(hours=14),
        )

        invalid_event = object.__new__(AuditEvent)
        object.__setattr__(invalid_event, "id", AuditEventId("event-min"))
        object.__setattr__(invalid_event, "kind", AuditEventKind("discovery"))
        object.__setattr__(
            invalid_event,
            "created_at",
            datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
        )
        object.__setattr__(invalid_event, "summary", SafeAuditSummary("discovered item"))
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            self.assertEqual(
                SQLiteAuditRepository(connection).append(invalid_event).outcome,
                AuditAppendOutcome.INVALID,
            )

    def test_recent_orders_adjacent_microseconds_before_id_tie_break(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.executemany(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                (
                    ("event-z", "discovery", "2026-07-10T10:00:00.000001Z", "discovered item"),
                    ("event-a", "discovery", "2026-07-10T10:00:00.000002Z", "item changed"),
                ),
            )
            result = SQLiteAuditRepository(connection).recent(1)
            self.assertEqual(result.outcome, AuditQueryOutcome.OK)
            self.assertEqual(tuple(str(item.id) for item in result.events), ("event-a",))

    def test_migration_rejects_unsupported_or_hidden_corrupt_timestamp(self) -> None:
        corrupt_timestamps = ("2026-07-10X10:00:00Z", "bad-time")
        for corrupt_timestamp in corrupt_timestamps:
            with self.subTest(corrupt_timestamp=corrupt_timestamp), tempfile.TemporaryDirectory() as directory:
                connection = connect(Path(directory) / "state.sqlite")
                with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:1]):
                    migrate(connection)
                connection.executemany(
                    "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                    (
                        ("event-valid", "discovery", "2026-07-11T10:00:00Z", "discovered item"),
                        ("event-corrupt", "discovery", corrupt_timestamp, "item changed"),
                    ),
                )
                original_schema = connection.execute(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'audit_events'"
                ).fetchone()
                original_rows = connection.execute(
                    "SELECT id, kind, created_at, summary FROM audit_events ORDER BY id"
                ).fetchall()
                with self.assertRaisesRegex(SchemaError, "rolled back"):
                    migrate(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    [(1,)],
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'audit_events'"
                    ).fetchone(),
                    original_schema,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT id, kind, created_at, summary "
                        "FROM audit_events ORDER BY id"
                    ).fetchall(),
                    original_rows,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE name IN ('audit_events_v2', 'audit_events_by_created_at')"
                    ).fetchone()
                )
                connection.close()

                reopened = connect(Path(directory) / "state.sqlite")
                with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:1]):
                    self.assertEqual(migrate(reopened), 1)
                self.assertEqual(
                    reopened.execute(
                        "SELECT id, kind, created_at, summary "
                        "FROM audit_events ORDER BY id"
                    ).fetchall(),
                    original_rows,
                )
                reopened.close()

    def test_unavailable_corrupt_and_invalid_query_return_typed_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            self.assertEqual(repository.recent(0).outcome, AuditQueryOutcome.INVALID)
            self.assertEqual(repository.recent(101).outcome, AuditQueryOutcome.INVALID)
            for malformed in (True, 1.0, "1", None):
                with self.subTest(malformed=malformed):
                    self.assertEqual(repository.recent(malformed).outcome, AuditQueryOutcome.INVALID)  # type: ignore[arg-type]
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("INSERT INTO audit_events(id, kind, created_at, summary) VALUES ('event-one', 'discovery', 'bad-time', 'discovered item')")
            connection.execute("PRAGMA ignore_check_constraints = OFF")
            self.assertEqual(repository.recent(1).outcome, AuditQueryOutcome.CORRUPT)
            connection.close()
            self.assertEqual(repository.append(event("event-two")).outcome, AuditAppendOutcome.UNAVAILABLE)
            self.assertEqual(repository.recent(1).outcome, AuditQueryOutcome.UNAVAILABLE)

    def test_corrupt_stored_value_types_and_values_return_corrupt(self) -> None:
        corrupt_rows = (
            (sqlite3.Binary(b"event-one"), "discovery", "2026-07-10T10:00:00.000000Z", "discovered item"),
            ("event-one", "UPPERCASE", "2026-07-10T10:00:00.000000Z", "discovered item"),
            ("event-one", "discovery", "bad-time", "discovered item"),
            ("event-one", "discovery", "2026-07-10T10:00:00.000000Z", "unknown summary"),
        )
        for row in corrupt_rows:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as directory:
                connection = connect(Path(directory) / "state.sqlite")
                migrate(connection)
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute(
                    "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)", row
                )
                connection.execute("PRAGMA ignore_check_constraints = OFF")
                self.assertEqual(SQLiteAuditRepository(connection).recent(1).outcome, AuditQueryOutcome.CORRUPT)
                connection.close()

    def test_locked_database_and_integrity_failures_map_to_typed_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            locker = connect(path)
            self.addCleanup(locker.close)
            migrate(locker)
            contender = connect(path)
            self.addCleanup(contender.close)
            contender.execute("PRAGMA busy_timeout = 0")
            locker.execute("BEGIN EXCLUSIVE")
            self.assertEqual(SQLiteAuditRepository(contender).append(event("event-one")).outcome, AuditAppendOutcome.UNAVAILABLE)

        failing_connection = Mock()
        failing_connection.execute.side_effect = sqlite3.DatabaseError("synthetic integrity failure")
        repository = SQLiteAuditRepository(failing_connection)
        self.assertEqual(repository.append(event("event-one")).outcome, AuditAppendOutcome.INTEGRITY_FAILURE)
        self.assertEqual(repository.recent(1).outcome, AuditQueryOutcome.CORRUPT)

    def test_invalid_append_input_and_benign_legacy_authorization_summary_return_typed_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            self.assertEqual(repository.append(object()).outcome, AuditAppendOutcome.INVALID)  # type: ignore[arg-type]
            connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                ("event-one", "authorization", "2026-07-10T10:00:00.000000Z", "authorization granted"),
            )
            result = repository.recent(1)
            self.assertEqual(result.outcome, AuditQueryOutcome.OK)
            self.assertEqual(str(result.events[0].summary), "authorization granted")

    def test_transaction_rollback_leaves_no_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            with self.assertRaises(RuntimeError):
                with transaction(connection):
                    self.assertEqual(repository.append(event("event-one")).outcome, AuditAppendOutcome.APPENDED)
                    raise RuntimeError("fail")
            self.assertEqual(repository.recent(1).events, ())


class SQLiteAuditRepositoryContractTests(AuditRepositoryContract, unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self._connection = connect(Path(self._temporary_directory.name) / "state.sqlite")
        self.addCleanup(self._connection.close)
        migrate(self._connection)
        self.repository = SQLiteAuditRepository(self._connection)


if __name__ == "__main__":
    unittest.main()
