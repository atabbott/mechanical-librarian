from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx

from mechanical_librarian.application.configuration import (
    ConfigurationError,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.infrastructure.configuration import configuration_schema_json, load_configuration_file
from mechanical_librarian.infrastructure.security.credentials import read_secret, target_client


def _source(root: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v2",
        "connections": [
            {"name": "secondary", "adapter": "audiobookshelf", "endpoint": "https://secondary.invalid", "secret_file": str(root / "secondary.secret")},
            {"name": "primary", "adapter": "audiobookshelf", "endpoint": "https://primary.invalid", "secret_file": str(root / "primary.secret")},
        ],
        "libraries": [
            {"name": "books", "connection": "primary", "library_id": "library-a", "media_kind": "ebook", "mount": "books", "provider_connections": ["second", "first"]},
            {"name": "other", "connection": "secondary", "library_id": "library-b", "media_kind": "ebook", "mount": "other", "provider_connections": ["first"]},
        ],
        "mounts": [
            {"name": "other", "container_path": "/media/other", "service_path": "/other", "read_only": True},
            {"name": "books", "container_path": "/media/books", "service_path": "/books", "read_only": True},
        ],
        "providers": [
            {"name": "second", "adapter": "hardcover", "enabled": False, "secret_file": None},
            {"name": "first", "adapter": "hardcover", "enabled": False, "secret_file": None},
        ],
    }


class MetadataConfigurationTests(unittest.TestCase):
    def test_multiple_named_scopes_and_provider_order_are_explicit_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps(_source(root)), encoding="utf-8")
            configuration = load_configuration_file(path)
        connection, library, mount, providers = configuration.select("primary", "books")
        self.assertEqual(connection.name, "primary")
        self.assertEqual(library.library_id, "library-a")
        self.assertEqual(mount.name, "books")
        self.assertEqual([value.name for value in providers], ["first", "second"])

    def test_duplicate_reference_unknown_field_and_implicit_old_schema_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = []
            duplicate = _source(root)
            duplicate["libraries"][0]["provider_connections"] = ["first", "first"]
            cases.append(duplicate)
            unknown = _source(root)
            unknown["default_database"] = "state.sqlite"
            cases.append(unknown)
            old = _source(root)
            old["schema_version"] = "config-v1"
            cases.append(old)
            missing_provider_secret = _source(root)
            missing_provider_secret["providers"][0]["enabled"] = True
            cases.append(missing_provider_secret)
            for index, source in enumerate(cases):
                path = root / f"bad-{index}.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    load_configuration_file(path)

    def test_generated_schema_is_current(self) -> None:
        expected = (Path(__file__).parents[2] / "docs" / "reference" / "config-v2.schema.json").read_text(encoding="utf-8")
        self.assertEqual(configuration_schema_json(), expected)

    def test_mount_paths_are_absolute_in_generated_schema_and_runtime(self) -> None:
        mount_properties = json.loads(configuration_schema_json())["$defs"]["MountInput"][
            "properties"
        ]
        self.assertEqual(mount_properties["container_path"]["pattern"], "^/")
        self.assertEqual(mount_properties["service_path"]["pattern"], "^/")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in ("container_path", "service_path"):
                source = _source(root)
                source["mounts"][0][field] = "relative/path"
                path = root / f"relative-{field}.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ConfigurationError):
                    load_configuration_file(path)

    def test_secret_paths_are_absolute_in_generated_schema_and_runtime(self) -> None:
        schema = json.loads(configuration_schema_json())["$defs"]
        self.assertEqual(
            schema["ConnectionInput"]["properties"]["secret_file"]["pattern"],
            "^/",
        )
        provider_secret = schema["ProviderInput"]["properties"]["secret_file"]
        absolute_variant = next(
            value for value in provider_secret["anyOf"] if value.get("type") == "string"
        )
        self.assertEqual(absolute_variant["pattern"], "^/")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = []
            relative_target = _source(root)
            relative_target["connections"][0]["secret_file"] = "target.secret"
            cases.append(relative_target)
            relative_provider = _source(root)
            relative_provider["providers"][0].update(
                {"enabled": True, "secret_file": "provider.secret"}
            )
            cases.append(relative_provider)
            for index, source in enumerate(cases):
                path = root / f"relative-secret-{index}.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    load_configuration_file(path)

        with self.assertRaises(ConfigurationError):
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "target.secret"
            )
        with self.assertRaises(ConfigurationError):
            ProviderConnectionConfiguration("catalogue", True, "provider.secret")


class CredentialBoundaryTests(unittest.TestCase):
    def test_private_regular_file_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "secret"
            secret.write_text("token-value", encoding="ascii")
            secret.chmod(0o600)
            self.assertEqual(read_secret(str(secret)), "token-value")
            secret.chmod(0o644)
            with self.assertRaises(Exception):
                read_secret(str(secret))
            link = root / "link"
            link.symlink_to(secret)
            with self.assertRaises(Exception):
                read_secret(str(link))

    def test_target_authorization_is_confined_to_current_item_get(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "secret"
            secret.write_text("token-value", encoding="ascii")
            secret.chmod(0o600)
            requests: list[httpx.Request] = []
            transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(200))
            client = target_client("https://library.invalid", str(secret), 5)
            # Replace only the transport for this structural authorization test.
            client._transport = transport
            client.get("https://library.invalid/api/items/item-1")
            client.post("https://library.invalid/api/items/item-1")
            client.get("https://other.invalid/api/items/item-1")
            client.close()
        self.assertEqual(requests[0].headers.get("authorization"), "Bearer token-value")
        self.assertIsNone(requests[1].headers.get("authorization"))
        self.assertIsNone(requests[2].headers.get("authorization"))


if __name__ == "__main__":
    unittest.main()
