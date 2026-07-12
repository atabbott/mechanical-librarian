from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.configuration import plan_import
from mechanical_librarian.interfaces.configuration import (
    configuration_schema_json,
    load_configuration_file,
    validate_configuration,
)


ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "docs" / "examples" / "synthetic-config.json"


class ConfigurationBoundaryTests(unittest.TestCase):
    def source(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_generated_schema_is_deterministic_complete_and_documented(self) -> None:
        first = configuration_schema_json()
        second = configuration_schema_json()
        schema = json.loads(first)
        documentation = (ROOT / "docs" / "reference" / "configuration.md").read_text(
            encoding="utf-8"
        )
        artifact = (ROOT / "docs" / "reference" / "config-v1.schema.json").read_text(
            encoding="utf-8"
        )

        self.assertEqual(first, second)
        self.assertEqual(first, artifact)
        for field in (
            "connections",
            "media_mounts",
            "limits",
            "evidence",
            "providers",
            "reviewers",
            "policy",
            "notifications",
            "backups_enabled",
            "retention",
        ):
            self.assertIn(field, schema["properties"])
            self.assertIn(field.replace("_", " "), documentation.casefold())

    def test_checked_in_synthetic_file_maps_to_immutable_secret_free_values(self) -> None:
        configuration = load_configuration_file(FIXTURE)
        exported = repr(configuration.export())

        self.assertEqual(configuration.connections[0].connection_id.value, "synthetic-connection")
        self.assertEqual(configuration.evidence.enabled_formats, ("epub",))
        self.assertEqual(configuration.retention.job_log_days, 7)
        for private in (
            "synthetic.invalid",
            "unused-synthetic-reference",
        ):
            self.assertNotIn(private, exported)

    def test_unknown_coerced_duplicate_and_malformed_inputs_fail_without_values(self) -> None:
        sentinel = "/private/config token=secret"
        cases: list[dict[str, object]] = []
        unknown = self.source()
        unknown["unknown"] = sentinel
        cases.append(unknown)
        coerced = self.source()
        coerced["backups_enabled"] = 1
        cases.append(coerced)
        nested = self.source()
        nested["limits"]["parser_concurrency"] = "1"
        cases.append(nested)
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(ConfigurationError) as raised:
                    validate_configuration(source)
                self.assertNotIn(sentinel, repr(raised.exception))
                self.assertNotIn("token=", repr(raised.exception))

        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "config.json"
            duplicate.write_text(
                '{"schema_version":"config-v1","schema_version":"private-sentinel"}',
                encoding="utf-8",
            )
            with self.assertRaises(ConfigurationError) as raised:
                load_configuration_file(duplicate)
            self.assertNotIn("private-sentinel", repr(raised.exception))

    def test_boundary_partitions_reject_coercion_and_preserve_canonical_sets(self) -> None:
        base = self.source()
        for value in (True, False, 1.0, "1", None, [], {}):
            with self.subTest(value=value):
                source = self.source()
                source["limits"]["parser_concurrency"] = value
                with self.assertRaises(ConfigurationError):
                    validate_configuration(source)

        reordered = self.source()
        reordered["connections"][0]["enabled_libraries"] = ["z-library", "a-library"]
        reordered["connections"][0]["capability_overrides"] = [
            "metadata-read",
            "inventory-read",
        ]
        loaded = validate_configuration(reordered)
        self.assertEqual(loaded.connections[0].enabled_libraries, ("a-library", "z-library"))
        self.assertEqual(
            loaded.connections[0].capability_overrides,
            ("inventory-read", "metadata-read"),
        )
        self.assertEqual(validate_configuration(base), validate_configuration(self.source()))

    def test_file_errors_and_oversize_documents_are_value_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private-sentinel.json"
            with self.assertRaises(ConfigurationError) as raised:
                load_configuration_file(missing)
            self.assertNotIn("private-sentinel", repr(raised.exception))
            oversized = Path(directory) / "oversized.json"
            oversized.write_bytes(b"x" * 1_048_577)
            with self.assertRaises(ConfigurationError):
                load_configuration_file(oversized)

    def test_import_plan_projects_a_typed_value_free_audit_fact(self) -> None:
        current = self.source()
        proposed = self.source()
        proposed["connections"][0]["secret_reference"] = "private-secret-sentinel"

        event = plan_import(current, proposed).audit_event()

        self.assertEqual(event.schema_version.value, "config-change-v1")
        self.assertEqual(tuple(change.path for change in event.changes), ("connections.secret_reference",))
        self.assertNotIn("private-secret-sentinel", repr(event))


if __name__ == "__main__":
    unittest.main()
