from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.infrastructure.persistence.sqlite import (
    MIGRATIONS,
    connect,
    finalize_staged_state,
    migrate_to_version,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class HardcoverC4EvidenceTests(unittest.TestCase):
    def test_c4_registry_binds_exact_installed_resources_and_predecessor(self) -> None:
        expected = {
            "c4-durable-work-input.json": (1844, "983ae3fbd9cb394772389e99dc722c81066e0d1ed268be7adc9623fdaf6c991e"),
            "c4-durable-work-example-output.json": (280, "d4beb29ec1542f093c6054f104ae857a86332c98dc01dc216b9072788547e949"),
            "c4-durable-work-input-schema.json": (1098, "f1d5c475af492c36469bb222b622afe232aebd5d0956d3b2afcd2f54b64c50a2"),
            "c4-durable-work-variant-matrix.json": (966, "5e1c6aedc4f322b7d6d979853a50913e5373cd97e695f4c1dc8a832eaba5a002"),
            "preview-output-schema-v5.json": (3910, "fd0f7d4e0248faa97ceddd6cbe442c23dfae4f48c8a02b47ee3df36bc7eedec7"),
            "preview-output-privacy-contract-v2.json": (3613, "02a897d7f3c997cc1c99b5f3ad808f3e636f704305cefa13ea44c8c5fb0c18e3"),
            "preview-output-privacy-scan-v2.json": (3644, "81aa8c3db583bdd326ccda572dcb510060bb1868e93b449737d71adef7bee7bc"),
            "preview-registry-c4-v6.json": (8327, "3e5183fb4ddff6674717f870c99b82ecb24eb2d232d759cf08957d2506d93e81"),
            "schema8-logical-snapshot.sql": (53233, "1040b8eefc8703503a9f515543c3618908bd7af0d08fae7700806b37a48811ed"),
            "schema8-logical-snapshot-ledger.json": (70843, "140d3d17ad743f8860ff216052976344ff2f7258096f45765d286b9b4887508c"),
        }
        for name, (size, digest) in expected.items():
            with self.subTest(name=name):
                payload = (FIXTURES / name).read_bytes()
                self.assertEqual(
                    (len(payload), hashlib.sha256(payload).hexdigest()),
                    (size, digest),
                )

        registry = json.loads(
            (FIXTURES / "preview-registry-c4-v6.json").read_text(encoding="utf-8")
        )
        predecessor = (FIXTURES / "preview-registry-c3-v6.json").read_bytes()
        self.assertEqual(
            registry["predecessor_registry_sha256"],
            hashlib.sha256(predecessor).hexdigest(),
        )
        self.assertEqual(
            [row["scenario"] for row in registry["scenarios"]],
            [
                "inert-report-v3",
                "identity-decision-v3",
                "fixed-adapter-v3",
                "durable-work-v3",
            ],
        )
        for key in ("output_schema", "output_privacy_contract", "output_privacy_scan"):
            self._assert_resource(registry[key])
        for resource in registry["scenarios"][-1]["resources"].values():
            self._assert_resource(resource)

    def test_schema_eight_dump_and_ledger_reproduce_from_genuine_prefix(self) -> None:
        ledger = json.loads(
            (FIXTURES / "schema8-logical-snapshot-ledger.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(ledger["schema_version"], 8)
        self.assertEqual(
            ledger["schema_migrations"],
            [
                {"version": item.version, "checksum": item.checksum}
                for item in MIGRATIONS[:8]
            ],
        )
        self.assertEqual(ledger["integrity_check"], [["ok"]])
        self.assertEqual(ledger["foreign_key_check"], [])
        self.assertIn("never execute snapshot SQL", ledger["creation_contract"]["runtime_reconstruction"])

        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "schema8.sqlite")
            try:
                self.assertEqual(migrate_to_version(connection, 8), 8)
                connection.execute(
                    "INSERT INTO instance_metadata(key,value) VALUES('synthetic_schema8_sentinel','fixture-v1')"
                )
                finalize_staged_state(connection)
                regenerated = "\n".join(connection.iterdump()) + "\n"
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)]
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()
        self.assertEqual(
            regenerated,
            (FIXTURES / "schema8-logical-snapshot.sql").read_text(encoding="utf-8"),
        )

    def test_closed_schemas_and_privacy_contract_include_cumulative_scope(self) -> None:
        input_schema = json.loads(
            (FIXTURES / "c4-durable-work-input-schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(input_schema["additionalProperties"])
        self.assertEqual(input_schema["properties"]["scenario"], {"const": "durable-work-v3"})
        self.assertFalse(
            input_schema["properties"]["script"]["items"]["additionalProperties"]
        )
        output_schema = json.loads(
            (FIXTURES / "preview-output-schema-v5.json").read_text(encoding="utf-8")
        )
        durable = output_schema["$defs"]["durable_work_result"]
        self.assertEqual(durable["properties"]["schema_from"], {"const": 8})
        self.assertEqual(durable["properties"]["schema_to"], {"const": 9})
        self.assertEqual(
            durable["properties"]["outcome"]["enum"],
            ["matched", "stale", "persistence_failure", "in_progress"],
        )
        privacy = json.loads(
            (FIXTURES / "preview-output-privacy-contract-v2.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(privacy, sort_keys=True)
        for channel in ("persistence", "exports", "audit"):
            self.assertIn(channel, serialized)
        for forbidden in ("raw ISBN", "raw title", "provider payload"):
            self.assertNotIn(forbidden, serialized)

    def _assert_resource(self, resource: dict[str, object]) -> None:
        payload = (FIXTURES / str(resource["path"])).read_bytes()
        self.assertEqual(resource["bytes"], len(payload))
        self.assertEqual(resource["sha256"], hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    unittest.main()
