from __future__ import annotations

import unittest

from mechanical_librarian.application.configuration import ConfigurationError, load_configuration, plan_import


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = {"schema_version": "config-v1", "instance_name": "test", "connections": [{"adapter": "audiobookshelf", "endpoint": "https://library.example", "secret_reference": "docker-secret/abs", "enabled_libraries": ["books"]}], "media_mounts": [{"name": "books", "library_type": "ebook", "container_path": "/media/books", "service_path": "/library/books"}], "limits": {"parser_concurrency": 1}}

    def test_export_is_secret_and_path_free(self) -> None:
        exported = repr(load_configuration(self.source).export())
        self.assertNotIn("docker-secret", exported)
        self.assertNotIn("library.example", exported)
        self.assertNotIn("/media/books", exported)

    def test_unknown_fields_fail_before_use(self) -> None:
        source = {**self.source, "unexpected": "value"}
        with self.assertRaises(ConfigurationError):
            load_configuration(source)

    def test_unsafe_limits_and_writes_are_rejected(self) -> None:
        for change in ({"limits": {"parser_concurrency": 99}}, {"destructive_actions_enabled": True}):
            with self.subTest(change=change):
                with self.assertRaises(ConfigurationError):
                    load_configuration({**self.source, **change})

    def test_import_plan_is_deterministic_and_secret_free(self) -> None:
        current = load_configuration(self.source)
        self.assertTrue(plan_import(current, self.source).is_noop)
        proposed = {**self.source, "limits": {"parser_concurrency": 2}}
        plan = plan_import(current, proposed)
        self.assertEqual(plan.changed_sections, ("limits",))
        self.assertTrue(plan.requires_restart)
        self.assertTrue(plan.reextract_evidence)
        self.assertNotIn("docker-secret", repr(plan))


if __name__ == "__main__":
    unittest.main()
