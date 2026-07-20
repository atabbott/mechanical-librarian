from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence import hardcover_identity_work
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkObservation,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import (
    DescriptorHardcoverIdentityGate,
)
from mechanical_librarian.infrastructure.persistence.sqlite import migrate
from tests.support.hardcover_c1_privacy import (
    PrivacyEvidenceError,
    assert_projection_channels_private,
)
from tests.support.hardcover_private_persistence import (
    logical_private_persistence_records,
)


class _ObservedDigest:
    def __init__(self, digest: object, updates: list[bytes]) -> None:
        self._digest = digest
        self._updates = updates

    def update(self, value: bytes) -> None:
        self._updates.append(bytes(value))
        self._digest.update(value)  # type: ignore[attr-defined]

    def hexdigest(self) -> str:
        return self._digest.hexdigest()  # type: ignore[no-any-return,attr-defined]


class HardcoverPrivatePersistenceExceptionsTests(unittest.TestCase):
    def test_matched_observation_exception_requires_exact_canonical_shape(
        self,
    ) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.execute(
            "CREATE TABLE hardcover_identity_observations("
            "observation_id TEXT,work_key TEXT,generation INTEGER,"
            "canonical_observation TEXT)"
        )
        connection.execute(
            "INSERT INTO hardcover_identity_observations VALUES(?,?,?,?)",
            (
                "a" * 64,
                "b" * 64,
                1,
                '{"book_id":2001,"edition_id":1001,"outcome":"matched",'
                '"provider_title":"Remote Volume Zeta","row_count":1,'
                '"unexpected":"9780000000019"}',
            ),
        )
        with self.assertRaisesRegex(
            PrivacyEvidenceError, "private matched observation is malformed"
        ):
            logical_private_persistence_records(connection)

    def test_observation_integrity_digest_has_one_closed_private_input_shape(
        self,
    ) -> None:
        work_key = "a" * 64
        observation = IdentityWorkObservation(
            IdentityMatchOutcome.MATCHED,
            2,
            1001,
            2001,
            "Remote Volume Zeta",
        )
        canonical = observation.to_canonical_json()
        updates: list[bytes] = []
        real_sha256 = hashlib.sha256

        def observed_sha256(value: bytes = b"") -> _ObservedDigest:
            return _ObservedDigest(real_sha256(value), updates)

        with patch.object(
            hardcover_identity_work.hashlib,
            "sha256",
            side_effect=observed_sha256,
        ):
            observation_id = hardcover_identity_work._observation_id(
                work_key, 7, canonical
            )

        self.assertEqual(
            updates,
            [
                hardcover_identity_work._OBSERVATION_ID_DOMAIN,
                work_key.encode("ascii"),
                b"\x00",
                b"7",
                b"\x00",
                canonical.encode("ascii"),
            ],
        )
        joined = b"".join(updates)
        self.assertNotIn(b"Synthetic Ebook Alpha", joined)
        self.assertNotIn(b"9780000000019", joined)
        self.assertEqual(len(observation_id), 64)
        self.assertFalse(set(observation_id) - set("0123456789abcdef"))
        self.assertNotEqual(
            observation_id,
            hardcover_identity_work._observation_id(
                work_key,
                7,
                IdentityWorkObservation(
                    IdentityMatchOutcome.MATCHED,
                    2,
                    1002,
                    2001,
                    "Remote Volume Zeta",
                ).to_canonical_json(),
            ),
        )

    def test_inode_recovery_record_has_closed_fields_and_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            database = root / "state.sqlite"
            connection = sqlite3.connect(database, isolation_level=None)
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute("PRAGMA application_id=123456")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            identity = database.stat()
            gate = DescriptorHardcoverIdentityGate(
                database,
                expected_database_identity=(identity.st_dev, identity.st_ino),
            )
            lease = gate.try_acquire()
            self.assertIsNotNone(lease)
            assert lease is not None
            try:
                recovery = gate._publish_binding(
                    connection,
                    lease.database_identity,
                    0x4D4C4201,
                    123456,
                )
                row = connection.execute(
                    "SELECT key,value FROM instance_metadata "
                    "WHERE key='hardcover-identity-binding-recovery-v1'"
                ).fetchone()
                self.assertEqual(
                    row,
                    (
                        "hardcover-identity-binding-recovery-v1",
                        f"v1:{identity.st_dev}:{identity.st_ino}:1296843265:123456",
                    ),
                )
                fields = row[1].split(":")
                self.assertEqual(fields[0], "v1")
                self.assertEqual(len(fields), 5)
                self.assertTrue(all(field.isascii() for field in fields))
                self.assertTrue(all(field.isdecimal() for field in fields[1:]))
                for forbidden in (
                    str(root),
                    database.name,
                    "Remote Volume Zeta",
                    "Synthetic Ebook Alpha",
                    "9780000000019",
                    "provider",
                    "credential",
                    "user",
                ):
                    self.assertNotIn(forbidden, row[1])

                rendered = "\n".join(
                    logical_private_persistence_records(connection)
                )
                self.assertNotIn(row[0], rendered)
                self.assertNotIn(row[1], rendered)

                gate._restore_binding(connection, recovery)
                self.assertIsNone(
                    connection.execute(
                        "SELECT value FROM instance_metadata "
                        "WHERE key='hardcover-identity-binding-recovery-v1'"
                    ).fetchone()
                )
                self.assertEqual(
                    connection.execute("PRAGMA application_id").fetchone(),
                    (123456,),
                )
            finally:
                lease.release()

            gate.bind_connection(connection)
            self.assertIsNone(
                connection.execute(
                    "SELECT value FROM instance_metadata "
                    "WHERE key='hardcover-identity-binding-recovery-v1'"
                ).fetchone()
            )

    def test_recovery_exception_rejects_every_forged_location_and_field(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            database = root / "state.sqlite"
            connection = sqlite3.connect(database, isolation_level=None)
            self.addCleanup(connection.close)
            migrate(connection)
            identity = database.stat()
            marker = 0x4D4C4201
            canonical = (
                f"v1:{identity.st_dev}:{identity.st_ino}:{marker}:123456"
            )

            def install(value: str, *, application_id: int = marker) -> None:
                connection.execute(
                    "INSERT OR REPLACE INTO instance_metadata(key,value) "
                    "VALUES(?,?)",
                    ("hardcover-identity-binding-recovery-v1", value),
                )
                connection.execute(f"PRAGMA application_id={application_id}")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

            field_values = [
                (
                    "device-private-identifier",
                    "v1:9780000000019:1:1296843265:0",
                    marker,
                ),
                (
                    "device-mismatch",
                    f"v1:{identity.st_dev + 1}:{identity.st_ino}:{marker}:123456",
                    marker,
                ),
                (
                    "inode-mismatch",
                    f"v1:{identity.st_dev}:{identity.st_ino + 1}:{marker}:123456",
                    marker,
                ),
                (
                    "marker-invalid",
                    f"v1:{identity.st_dev}:{identity.st_ino}:1:123456",
                    marker,
                ),
                (
                    "original-out-of-range",
                    f"v1:{identity.st_dev}:{identity.st_ino}:{marker}:2147483648",
                    marker,
                ),
                (
                    "marker-current-mismatch",
                    canonical,
                    0x4D4C4202,
                ),
            ]
            canonical_tokens = canonical.split(":")[1:]
            for index in range(4):
                leading_zero = list(canonical_tokens)
                leading_zero[index] = f"0{leading_zero[index]}"
                field_values.append(
                    (
                        f"field-{index}-leading-zero",
                        "v1:" + ":".join(leading_zero),
                        marker,
                    )
                )
                oversized = list(canonical_tokens)
                oversized[index] = "9" * 21
                field_values.append(
                    (
                        f"field-{index}-oversized",
                        "v1:" + ":".join(oversized),
                        marker,
                    )
                )

            for name, value, application_id in field_values:
                with self.subTest(name=name):
                    install(value, application_id=application_id)
                    rendered = "\n".join(
                        logical_private_persistence_records(connection)
                    )
                    self.assertIn(value, rendered)
                    connection.execute(
                        "DELETE FROM instance_metadata WHERE key=?",
                        ("hardcover-identity-binding-recovery-v1",),
                    )

            install("v1:9780000000019:1:1296843265:0")
            records = logical_private_persistence_records(connection)
            with self.assertRaisesRegex(
                PrivacyEvidenceError,
                "isbn_identity projection reached persistence",
            ):
                assert_projection_channels_private(
                    {"persistence": records},
                    {"isbn_identity": frozenset({"9780000000019"})},
                    expected_channels=("persistence",),
                    expected_source_classes=("isbn_identity",),
                )
            connection.execute(
                "DELETE FROM instance_metadata WHERE key=?",
                ("hardcover-identity-binding-recovery-v1",),
            )

            location_cases = (
                (
                    "wrong-key",
                    "INSERT INTO instance_metadata(key,value) VALUES(?,?)",
                    ("not-the-recovery-key", canonical),
                ),
                (
                    "wrong-table",
                    "INSERT INTO binding_recovery_probe(key,value) VALUES(?,?)",
                    ("hardcover-identity-binding-recovery-v1", canonical),
                ),
                (
                    "wrong-column",
                    "INSERT INTO binding_recovery_column_probe(key,recovery_value) "
                    "VALUES(?,?)",
                    ("hardcover-identity-binding-recovery-v1", canonical),
                ),
            )
            connection.execute(
                "CREATE TABLE binding_recovery_probe(key TEXT,value TEXT)"
            )
            connection.execute(
                "CREATE TABLE binding_recovery_column_probe("
                "key TEXT,recovery_value TEXT)"
            )
            connection.execute(f"PRAGMA application_id={marker}")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for name, statement, parameters in location_cases:
                with self.subTest(name=name):
                    connection.execute(statement, parameters)
                    rendered = "\n".join(
                        logical_private_persistence_records(connection)
                    )
                    self.assertIn(canonical, rendered)
                    if name == "wrong-key":
                        connection.execute(
                            "DELETE FROM instance_metadata WHERE key=?",
                            ("not-the-recovery-key",),
                        )
                    elif name == "wrong-table":
                        connection.execute("DELETE FROM binding_recovery_probe")
                    else:
                        connection.execute(
                            "DELETE FROM binding_recovery_column_probe"
                        )


if __name__ == "__main__":
    unittest.main()
