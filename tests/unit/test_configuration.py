from __future__ import annotations

from copy import deepcopy
import unittest

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ChangeImpact,
    ConnectionConfiguration,
    ConfigurationChange,
    ConfigurationError,
    MediaMountConfiguration,
    PolicyConfiguration,
    ResourceLimits,
)
from mechanical_librarian.infrastructure.configuration import load_configuration, plan_import


R = ChangeImpact.RESTART_REQUIRED
I = ChangeImpact.INVENTORY_RECONCILIATION_REQUIRED
X = ChangeImpact.EVIDENCE_INVALIDATION_REQUIRED
E = ChangeImpact.EVIDENCE_REEVALUATION_REQUIRED
C = ChangeImpact.CANDIDATE_REEVALUATION_REQUIRED
V = ChangeImpact.REVIEW_REEVALUATION_REQUIRED
N = ChangeImpact.NOTIFICATION_RECONFIGURATION_REQUIRED
B = ChangeImpact.BACKUP_READINESS_REVALIDATION_REQUIRED
RX = ChangeImpact.REEXTRACT_EVIDENCE
LIBRARY_ID = "01234567-89ab-4def-8123-456789abcdef"
SECOND_LIBRARY_ID = "11234567-89ab-4def-8123-456789abcdef"
REPLACEMENT_LIBRARY_ID = "21234567-89ab-4def-8123-456789abcdef"
PRIVATE_LIBRARY_ID = "31234567-89ab-4def-8123-456789abcdef"


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {
            "schema_version": "config-v1",
            "instance_name": "test",
            "connections": [
                {
                    "adapter": "audiobookshelf",
                    "endpoint": "https://library.example",
                    "secret_reference": "docker-secret/abs",
                    "enabled_libraries": [LIBRARY_ID],
                    "capability_overrides": ["inventory-read"],
                }
            ],
            "media_mounts": [
                {
                    "name": "books",
                    "library_type": "ebook",
                    "container_path": "/media/books",
                    "service_path": "/library/books",
                }
            ],
            "limits": {"parser_concurrency": 1},
            "providers": ["provider-a"],
            "reviewers": ["reviewer-a"],
            "policy": {"policy_version": "policy-v1"},
            "notifications": ["notification-a"],
            "backups_enabled": True,
        }

    def proposed(self) -> dict[str, object]:
        return deepcopy(self.source)

    def test_export_and_diagnostics_are_secret_and_path_free(self) -> None:
        configuration = load_configuration(self.source)
        exported = repr(configuration.export())
        diagnostics = repr(configuration)
        for private_value in (
            "docker-secret",
            "library.example",
            "/media/books",
            "/library/books",
        ):
            self.assertNotIn(private_value, exported)
            self.assertNotIn(private_value, diagnostics)

    def test_f05b_inventory_controls_are_exported_and_change_impacts_are_complete(
        self,
    ) -> None:
        source = self.proposed()
        source["inventory"] = {
            "poll_generation": 7,
            "max_attempts": 4,
            "max_elapsed_seconds": 901,
            "initial_backoff_seconds": 6,
            "max_backoff_seconds": 301,
            "connect_timeout_seconds": 11,
            "read_timeout_seconds": 31,
            "max_in_flight_requests": 1,
        }
        exported = load_configuration(source).export()["inventory"]
        self.assertEqual(
            {key: exported[key] for key in source["inventory"]},
            source["inventory"],
        )

        changeable = (
            ("poll_generation", 1),
            ("max_attempts", 4),
            ("max_elapsed_seconds", 901),
            ("initial_backoff_seconds", 6),
            ("max_backoff_seconds", 301),
            ("connect_timeout_seconds", 11),
            ("read_timeout_seconds", 31),
        )
        for field, value in changeable:
            with self.subTest(field=field):
                proposed = self.proposed()
                proposed["inventory"] = {field: value}
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(f"inventory.{field}", (R, I)),),
                )

        fixed = self.proposed()
        fixed["inventory"] = {"max_in_flight_requests": 2}
        with self.assertRaises(ConfigurationError):
            plan_import(self.source, fixed)

    def test_noop_and_one_change_per_top_level_group_follow_matrix(self) -> None:
        self.assertTrue(plan_import(self.source, self.source).is_noop)
        cases = (
            ("instance_name", "renamed", "instance.instance_name", (R,)),
            ("providers", ["provider-b"], "providers", (R, C, V)),
            ("reviewers", ["reviewer-b"], "reviewers", (R, V)),
            ("notifications", ["notification-b"], "notifications", (R, N)),
            ("backups_enabled", False, "backups_enabled", (R, B, V)),
        )
        for key, value, path, impacts in cases:
            with self.subTest(path=path):
                proposed = self.proposed()
                proposed[key] = value
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(path, impacts),),
                )

        policy = self.proposed()
        policy["policy"] = {"policy_version": "policy-v2"}
        self.assertEqual(
            plan_import(self.source, policy).changes,
            (ConfigurationChange("policy.policy_version", (R, V)),),
        )

    def test_connection_changes_are_distinct_and_conservative(self) -> None:
        cases = (
            ("id", "connection-one", "connections.id", (R, I, X, C, V)),
            ("adapter", "synthetic", "connections.adapter", (R, I, X, C, V)),
            (
                "endpoint",
                "https://replacement.example",
                "connections.endpoint",
                (R, I, X, C, V),
            ),
            (
                "secret_reference",
                "docker-secret/replacement",
                "connections.secret_reference",
                (R, I, X, C, V),
            ),
            (
                "enabled_libraries",
                [REPLACEMENT_LIBRARY_ID],
                "connections.enabled_libraries",
                (R, I, X, C, V),
            ),
            (
                "capability_overrides",
                ["metadata-read"],
                "connections.capability_overrides",
                (R, I, E, C, V),
            ),
        )
        for key, value, path, impacts in cases:
            with self.subTest(path=path):
                proposed = self.proposed()
                proposed["connections"][0][key] = value
                plan = plan_import(self.source, proposed)
                self.assertEqual(plan.changes, (ConfigurationChange(path, impacts),))
                self.assertTrue(plan.requires_inventory_reconciliation)
                self.assertEqual(plan.reextract_evidence, X in impacts)

    def test_mount_and_limit_changes_have_field_specific_impacts(self) -> None:
        mount_cases = (
            ("name", "renamed"),
            ("library_type", "audiobook"),
            ("container_path", "/replacement/books"),
            ("service_path", "/replacement/library"),
        )
        for key, value in mount_cases:
            with self.subTest(key=key):
                proposed = self.proposed()
                proposed["media_mounts"][0][key] = value
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(f"media_mounts.{key}", (R, I, X, C, V)),),
                )

        limit_cases = (
            ("parser_concurrency", 2, (R,)),
            ("parser_timeout_seconds", 120, (R, RX, E)),
            ("temporary_bytes", 2_147_483_648, (R, E)),
        )
        for key, value, impacts in limit_cases:
            with self.subTest(key=key):
                proposed = self.proposed()
                proposed["limits"][key] = value
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(f"limits.{key}", impacts),),
                )

        evidence_cases = (
            ("enabled_formats", ["epub"], (R, X, E, C, V)),
            ("maximum_document_bytes", 2_097_152, (R, RX, E)),
            ("maximum_text_characters", 2_048, (R, E)),
        )
        for key, value, impacts in evidence_cases:
            with self.subTest(key=key):
                proposed = self.proposed()
                proposed["evidence"] = {key: value}
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(f"evidence.{key}", impacts),),
                )

        retention_cases = (
            ("evidence_days", 91),
            ("job_log_days", 31),
            ("maximum_artifact_bytes", 2_147_483_648),
        )
        for key, value in retention_cases:
            with self.subTest(key=key):
                proposed = self.proposed()
                proposed["retention"] = {key: value}
                self.assertEqual(
                    plan_import(self.source, proposed).changes,
                    (ConfigurationChange(f"retention.{key}", (R,)),),
                )

    def test_epub_package_limits_are_versioned_lowerable_and_require_reextraction(
        self,
    ) -> None:
        configured = self.proposed()
        configured["evidence"] = {
            "epub_package": {
                "profile": "epub-package-limits-v1",
                "archive_entries": 1_024,
                "xml_depth": 32,
            }
        }
        limits = load_configuration(configured).evidence.epub_package
        self.assertEqual(limits.profile, "epub-package-limits-v1")
        self.assertEqual(limits.archive_entries, 1_024)
        self.assertEqual(limits.xml_depth, 32)
        self.assertEqual(limits.opf_package_bytes, 4_194_304)
        self.assertEqual(
            tuple(change.path for change in plan_import(self.source, configured).changes),
            (
                "evidence.epub_package.archive_entries",
                "evidence.epub_package.xml_depth",
            ),
        )
        self.assertEqual(plan_import(self.source, configured).impacts, (R, RX))
        self.assertTrue(plan_import(self.source, configured).reextract_evidence)

        for value in (0, 8_193):
            with self.subTest(archive_entries=value):
                invalid = self.proposed()
                invalid["evidence"] = {"epub_package": {"archive_entries": value}}
                with self.assertRaises(ConfigurationError):
                    load_configuration(invalid)

        invalid_profile = self.proposed()
        invalid_profile["evidence"] = {
            "epub_package": {"profile": "epub-package-limits-v2"}
        }
        with self.assertRaises(ConfigurationError):
            load_configuration(invalid_profile)

    def test_multiple_changes_have_fixed_path_and_impact_order(self) -> None:
        proposed = self.proposed()
        proposed["connections"][0]["endpoint"] = "https://replacement.example"
        proposed["limits"]["parser_timeout_seconds"] = 120
        proposed["notifications"] = ["notification-b"]
        plan = plan_import(self.source, proposed)
        self.assertEqual(
            tuple(change.path for change in plan.changes),
            (
                "connections.endpoint",
                "limits.parser_timeout_seconds",
                "notifications",
            ),
        )
        self.assertEqual(plan.impacts, (R, I, X, RX, E, C, V, N))
        self.assertEqual(plan.changed_sections, ("connections", "limits", "notifications"))

    def test_set_like_connection_values_are_canonical_noops(self) -> None:
        current = self.proposed()
        current["connections"][0]["enabled_libraries"] = [
            LIBRARY_ID,
            SECOND_LIBRARY_ID,
        ]
        current["connections"][0]["capability_overrides"] = [
            "metadata-read",
            "inventory-read",
        ]
        proposed = deepcopy(current)
        proposed["connections"][0]["enabled_libraries"].reverse()
        proposed["connections"][0]["capability_overrides"].reverse()
        self.assertTrue(plan_import(current, proposed).is_noop)

    def test_typed_configuration_constructors_retain_invariants(self) -> None:
        loaded = load_configuration(self.source)
        invalid_constructors = tuple(
            lambda adapter=adapter: ConnectionConfiguration(
                adapter, loaded.connections[0].endpoint, loaded.connections[0].secret, ("books",)
            )
            for adapter in ("", " bad ", "a" * 65, "bad\x00adapter")
        ) + (
            lambda: MediaMountConfiguration("books", "ebook", "relative", "/service", True),
            lambda: MediaMountConfiguration("books", "ebook", "/media", "/service", False),
            lambda: ApplicationConfiguration(
                loaded.schema_version,
                loaded.instance_name,
                (),
                loaded.media_mounts,
                ResourceLimits(),
            ),
            lambda: ApplicationConfiguration(
                loaded.schema_version,
                loaded.instance_name,
                loaded.connections,
                loaded.media_mounts,
                ResourceLimits(),
                destructive_actions_enabled=True,
            ),
        )
        for construct in invalid_constructors:
            with self.subTest(construct=construct):
                with self.assertRaises(ConfigurationError):
                    construct()

    def test_both_sources_use_the_same_strict_validation_path(self) -> None:
        invalid_cases = (
            {**self.source, "unexpected": "private-sentinel"},
            {**self.source, "schema_version": "config-v2"},
            {**self.source, "connections": "not-a-sequence"},
            {**self.source, "backups_enabled": 1},
        )
        for invalid in invalid_cases:
            for current, proposed in ((invalid, self.source), (self.source, invalid)):
                with self.subTest(invalid=invalid, current=current is invalid):
                    with self.assertRaises(ConfigurationError) as raised:
                        plan_import(current, proposed)
                    self.assertNotIn("private-sentinel", repr(raised.exception))

        with self.assertRaises(ConfigurationError):
            plan_import(load_configuration(self.source), self.source)

    def test_policy_versions_use_the_domain_version_contract_everywhere(self) -> None:
        for valid in ("policy-v1", "policy-v2"):
            with self.subTest(valid=valid):
                source = self.proposed()
                source["policy"]["policy_version"] = valid
                self.assertEqual(load_configuration(source).policy.policy_version, valid)

        invalid_versions = ("Policy-v1", "policy v1", "policy/v1")
        for invalid_version in invalid_versions:
            with self.subTest(invalid_version=invalid_version, boundary="constructor"):
                with self.assertRaises(ConfigurationError) as raised:
                    PolicyConfiguration(invalid_version)
                self.assertNotIn(invalid_version, repr(raised.exception))
                self.assertIsNone(raised.exception.__cause__)

            invalid = self.proposed()
            invalid["policy"]["policy_version"] = invalid_version
            with self.subTest(invalid_version=invalid_version, boundary="load"):
                with self.assertRaises(ConfigurationError) as raised:
                    load_configuration(invalid)
                self.assertNotIn(invalid_version, repr(raised.exception))
                self.assertIsNone(raised.exception.__cause__)

            for current, proposed in ((invalid, self.source), (self.source, invalid)):
                boundary = "current" if current is invalid else "proposed"
                with self.subTest(invalid_version=invalid_version, boundary=boundary):
                    with self.assertRaises(ConfigurationError) as raised:
                        plan_import(current, proposed)
                    self.assertNotIn(invalid_version, repr(raised.exception))
                    self.assertIsNone(raised.exception.__cause__)

        proposed = self.proposed()
        proposed["policy"]["policy_version"] = "policy-v2"
        self.assertEqual(
            plan_import(self.source, proposed).changes,
            (ConfigurationChange("policy.policy_version", (R, V)),),
        )

    def test_unknown_nested_fields_duplicates_and_unsafe_values_fail_closed(self) -> None:
        cases = []
        unknown_connection = self.proposed()
        unknown_connection["connections"][0]["unknown"] = "private-sentinel"
        cases.append(unknown_connection)
        unknown_policy = self.proposed()
        unknown_policy["policy"]["unknown"] = "private-sentinel"
        cases.append(unknown_policy)
        duplicate = self.proposed()
        duplicate["providers"] = ["provider-a", "provider-a"]
        cases.append(duplicate)
        writable = self.proposed()
        writable["media_mounts"][0]["read_only"] = False
        cases.append(writable)
        destructive = self.proposed()
        destructive["destructive_actions_enabled"] = True
        cases.append(destructive)
        invalid_limit = self.proposed()
        invalid_limit["limits"] = {"parser_concurrency": True}
        cases.append(invalid_limit)
        for unsafe_override in (
            "cover-write",
            "/private/library/token=secret",
            "metadata-read=private-secret",
        ):
            unsafe_capability = self.proposed()
            unsafe_capability["connections"][0]["capability_overrides"] = [unsafe_override]
            cases.append(unsafe_capability)
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(ConfigurationError) as raised:
                    plan_import(self.source, source)
                self.assertNotIn("private-sentinel", repr(raised.exception))

    def test_plan_and_errors_never_disclose_changed_values(self) -> None:
        proposed = self.proposed()
        private_values = (
            "replacement.example",
            "private-secret-reference",
            PRIVATE_LIBRARY_ID,
            "/private/container/path",
            "/private/service/path",
        )
        proposed["connections"][0].update(
            {
                "endpoint": "https://replacement.example",
                "secret_reference": "private-secret-reference",
                "enabled_libraries": [PRIVATE_LIBRARY_ID],
            }
        )
        proposed["media_mounts"][0].update(
            {
                "container_path": "/private/container/path",
                "service_path": "/private/service/path",
            }
        )
        plan_text = repr(plan_import(self.source, proposed))
        for private_value in private_values:
            self.assertNotIn(private_value, plan_text)

        invalid = self.proposed()
        invalid["connections"][0]["endpoint"] = "private-invalid-endpoint"
        with self.assertRaises(ConfigurationError) as raised:
            plan_import(self.source, invalid)
        self.assertNotIn("private-invalid-endpoint", repr(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
