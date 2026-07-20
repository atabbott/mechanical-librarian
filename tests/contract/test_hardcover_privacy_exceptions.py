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
from tests.support.hardcover_c1_privacy import PrivacyEvidenceError
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


if __name__ == "__main__":
    unittest.main()
