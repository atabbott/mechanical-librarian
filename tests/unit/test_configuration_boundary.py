from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.infrastructure.configuration import (
    configuration_schema_json,
    load_configuration_file,
    plan_import,
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
            "inventory",
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
        definitions = schema["$defs"]
        self.assertEqual(schema["properties"]["schema_version"]["const"], "config-v1")
        self.assertEqual(schema["properties"]["connections"]["minItems"], 1)
        connection_condition = definitions["ConnectionInput"]["allOf"][0]
        self.assertEqual(
            connection_condition["if"]["properties"]["adapter"]["const"],
            "audiobookshelf",
        )
        self.assertIn(
            "-4",
            connection_condition["then"]["properties"]["enabled_libraries"]["items"][
                "pattern"
            ],
        )
        self.assertEqual(
            definitions["ResourceLimitsInput"]["properties"]["parser_concurrency"],
            {
                "default": 1,
                "maximum": 8,
                "minimum": 1,
                "title": "Parser Concurrency",
                "type": "integer",
            },
        )
        formats = definitions["EvidenceInput"]["properties"]["enabled_formats"]
        self.assertEqual(formats["items"]["enum"], ["epub", "m4b", "mp3"])
        self.assertEqual((formats["minItems"], formats["maxItems"]), (1, 3))
        self.assertTrue(
            definitions["ConnectionInput"]["properties"]["enabled_libraries"][
                "uniqueItems"
            ]
        )
        library_pattern = definitions["ConnectionInput"]["properties"][
            "enabled_libraries"
        ]["items"]["pattern"]
        self.assertIn("[0-9][0-9a-f]{7}", library_pattern)
        self.assertEqual(
            definitions["ConnectionInput"]["properties"]["adapter"]["pattern"],
            "^[a-z][a-z0-9_-]{0,63}$",
        )
        epub = definitions["EpubPackageInput"]["properties"]
        self.assertEqual(
            epub["profile"]["const"], "epub-package-limits-v1"
        )
        self.assertEqual(
            epub["source_archive_bytes"],
            {
                "default": 67_108_864,
                "maximum": 536_870_912,
                "minimum": 1,
                "title": "Source Archive Bytes",
                "type": "integer",
            },
        )
        self.assertEqual(epub["xml_depth"]["default"], 64)
        self.assertEqual(epub["xml_depth"]["maximum"], 128)
        self.assertEqual(len(epub), 19)

    def test_checked_in_synthetic_file_maps_to_immutable_secret_free_values(
        self,
    ) -> None:
        configuration = load_configuration_file(FIXTURE)
        exported = repr(configuration.export())

        self.assertEqual(
            configuration.connections[0].connection_id.value, "synthetic-connection"
        )
        self.assertEqual(configuration.evidence.enabled_formats, ("epub",))
        self.assertEqual(configuration.retention.job_log_days, 7)
        for private in (
            "synthetic.invalid",
            "unused-synthetic-reference",
        ):
            self.assertNotIn(private, exported)

    def test_unknown_coerced_duplicate_and_malformed_inputs_fail_without_values(
        self,
    ) -> None:
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

    def test_boundary_partitions_reject_coercion_and_preserve_canonical_sets(
        self,
    ) -> None:
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
        self.assertEqual(
            loaded.connections[0].enabled_libraries, ("a-library", "z-library")
        )
        self.assertEqual(
            loaded.connections[0].capability_overrides,
            ("inventory-read", "metadata-read"),
        )
        self.assertEqual(
            validate_configuration(base), validate_configuration(self.source())
        )

    def test_enabled_libraries_accept_exact_uuidv4_without_widening_identifiers(
        self,
    ) -> None:
        uuid = "01234567-89ab-4def-8123-456789abcdef"
        source = self.source()
        source["connections"][0]["enabled_libraries"] = [uuid, "synthetic-library"]

        loaded = validate_configuration(source)

        self.assertEqual(
            loaded.connections[0].enabled_libraries,
            (uuid, "synthetic-library"),
        )
        for value in (
            "1library",
            "01234567-89ab-3def-8123-456789abcdef",
            "01234567-89ab-4def-7123-456789abcdef",
            "01234567-89AB-4def-8123-456789abcdef",
        ):
            with self.subTest(value=value):
                invalid = self.source()
                invalid["connections"][0]["enabled_libraries"] = [value]
                with self.assertRaises(ConfigurationError):
                    validate_configuration(invalid)

        invalid_connection = self.source()
        invalid_connection["connections"][0]["id"] = uuid
        with self.assertRaises(ConfigurationError):
            validate_configuration(invalid_connection)

        duplicate = self.source()
        duplicate["connections"][0]["enabled_libraries"] = [uuid, uuid]
        with self.assertRaises(ConfigurationError):
            validate_configuration(duplicate)

    def test_audiobookshelf_libraries_require_canonical_uuidv4(self) -> None:
        valid = (
            "01234567-89ab-4def-8123-456789abcdef",
            "a1234567-89ab-4def-8123-456789abcdef",
        )
        source = self.source()
        source["connections"][0]["adapter"] = "audiobookshelf"
        source["connections"][0]["enabled_libraries"] = list(valid)

        loaded = validate_configuration(source)

        self.assertEqual(loaded.connections[0].enabled_libraries, valid)
        for value in (
            "synthetic-library",
            "01234567-89ab-3def-8123-456789abcdef",
            "a1234567-89ab-3def-8123-456789abcdef",
            "01234567-89ab-4def-7123-456789abcdef",
            "a1234567-89ab-4def-7123-456789abcdef",
            "A1234567-89ab-4def-8123-456789abcdef",
        ):
            with self.subTest(value=value):
                invalid = self.source()
                invalid["connections"][0]["adapter"] = "audiobookshelf"
                invalid["connections"][0]["enabled_libraries"] = [value]
                with self.assertRaises(ConfigurationError):
                    validate_configuration(invalid)

    def test_new_field_bounds_unknowns_and_import_differential_are_exhaustive(
        self,
    ) -> None:
        invalid_updates = (
            (
                "connection-id",
                lambda source, value: source["connections"][0].__setitem__("id", value),
                "A",
            ),
            (
                "connection-id-long",
                lambda source, value: source["connections"][0].__setitem__("id", value),
                "a" * 65,
            ),
            (
                "evidence-empty",
                lambda source, value: source.__setitem__(
                    "evidence", {"enabled_formats": value}
                ),
                [],
            ),
            (
                "evidence-unknown",
                lambda source, value: source.__setitem__(
                    "evidence", {"enabled_formats": value}
                ),
                ["pdf"],
            ),
            (
                "document-low",
                lambda source, value: source.__setitem__(
                    "evidence", {"maximum_document_bytes": value}
                ),
                1_048_575,
            ),
            (
                "document-high",
                lambda source, value: source.__setitem__(
                    "evidence", {"maximum_document_bytes": value}
                ),
                536_870_913,
            ),
            (
                "text-low",
                lambda source, value: source.__setitem__(
                    "evidence", {"maximum_text_characters": value}
                ),
                1_023,
            ),
            (
                "text-high",
                lambda source, value: source.__setitem__(
                    "evidence", {"maximum_text_characters": value}
                ),
                4_194_305,
            ),
            (
                "evidence-days",
                lambda source, value: source.__setitem__(
                    "retention", {"evidence_days": value}
                ),
                3_651,
            ),
            (
                "job-log-days",
                lambda source, value: source.__setitem__(
                    "retention", {"job_log_days": value}
                ),
                0,
            ),
            (
                "artifact-low",
                lambda source, value: source.__setitem__(
                    "retention", {"maximum_artifact_bytes": value}
                ),
                1_048_575,
            ),
            (
                "inventory-pages",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_pages": value}
                ),
                1,
            ),
            (
                "inventory-items",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_items": value}
                ),
                10_001,
            ),
            (
                "inventory-bytes",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_response_bytes": value}
                ),
                67_108_865,
            ),
            (
                "inventory-delay",
                lambda source, value: source.__setitem__(
                    "inventory", {"stability_delay_seconds": value}
                ),
                -1,
            ),
            (
                "inventory-poll-generation",
                lambda source, value: source.__setitem__(
                    "inventory", {"poll_generation": value}
                ),
                -1,
            ),
            (
                "inventory-attempts",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_attempts": value}
                ),
                17,
            ),
            (
                "inventory-elapsed",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_elapsed_seconds": value}
                ),
                0,
            ),
            (
                "inventory-connect-timeout",
                lambda source, value: source.__setitem__(
                    "inventory", {"connect_timeout_seconds": value}
                ),
                301,
            ),
            (
                "inventory-read-timeout",
                lambda source, value: source.__setitem__(
                    "inventory", {"read_timeout_seconds": value}
                ),
                0,
            ),
            (
                "inventory-in-flight",
                lambda source, value: source.__setitem__(
                    "inventory", {"max_in_flight_requests": value}
                ),
                2,
            ),
        )
        for label, update, value in invalid_updates:
            with self.subTest(label=label):
                source = self.source()
                update(source, value)
                with self.assertRaises(ConfigurationError):
                    validate_configuration(source)

        invalid_backoff = self.source()
        invalid_backoff["inventory"] = {
            "initial_backoff_seconds": 10,
            "max_backoff_seconds": 9,
        }
        with self.assertRaises(ConfigurationError):
            validate_configuration(invalid_backoff)

        for evidence_days in (1, 3_650):
            source = self.source()
            source["retention"]["evidence_days"] = evidence_days
            self.assertEqual(
                validate_configuration(source).retention.evidence_days, evidence_days
            )

        unknown = self.source()
        unknown["evidence"]["private-sentinel"] = "token=secret"
        with self.assertRaises(ConfigurationError) as raised:
            validate_configuration(unknown)
        self.assertNotIn("private-sentinel", repr(raised.exception))

        tuple_source = self.source()
        tuple_source["connections"] = tuple(tuple_source["connections"])
        with self.assertRaises(ConfigurationError):
            validate_configuration(tuple_source)
        with self.assertRaises(ConfigurationError):
            plan_import(tuple_source, self.source())

    def test_file_errors_and_oversize_documents_are_value_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private-sentinel.json"
            with self.assertRaises(ConfigurationError) as raised:
                load_configuration_file(missing)
            self.assertNotIn("private-sentinel", repr(raised.exception))
            oversized = Path(directory) / "oversized.json"
            oversized.write_bytes(b"x" * 1_048_577)
            with patch(
                "mechanical_librarian.infrastructure.configuration.os.read"
            ) as read:
                with self.assertRaises(ConfigurationError):
                    load_configuration_file(oversized)
                read.assert_not_called()

            fifo = Path(directory) / "config.fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ConfigurationError):
                load_configuration_file(fifo)

            symlink = Path(directory) / "config-link.json"
            symlink.symlink_to(FIXTURE)
            with self.assertRaises(ConfigurationError) as raised:
                load_configuration_file(symlink)
            self.assertIsNone(raised.exception.__cause__)

            huge_integer = Path(directory) / "huge-number.json"
            huge_integer.write_text(
                '{"schema_version":' + "9" * 5_000 + "}", encoding="utf-8"
            )
            with self.assertRaises(ConfigurationError) as raised:
                load_configuration_file(huge_integer)
            self.assertIsNone(raised.exception.__cause__)

    def test_import_plan_projects_a_typed_value_free_audit_fact(self) -> None:
        current = self.source()
        proposed = self.source()
        proposed["connections"][0]["secret_reference"] = "private-secret-sentinel"

        event = plan_import(current, proposed).audit_event()

        self.assertEqual(event.schema_version.value, "config-change-v1")
        self.assertEqual(
            tuple(change.path for change in event.changes),
            ("connections.secret_reference",),
        )
        self.assertNotIn("private-secret-sentinel", repr(event))


if __name__ == "__main__":
    unittest.main()
