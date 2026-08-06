from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx
from pydantic import ValidationError

from mechanical_librarian.application.configuration import (
    ConfigurationError,
    ProviderConnectionConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.infrastructure.configuration import ConfigurationInput, configuration_schema_json, load_configuration_file
from mechanical_librarian.infrastructure.security.credentials import (
    hardcover_client,
    read_secret,
    target_client,
)


def _source(root: Path) -> dict[str, object]:
    return {
        "schema_version": "config-v5",
        "connections": [
            {"name": "secondary", "adapter": "audiobookshelf", "endpoint": "https://secondary.invalid", "secret_file": str(root / "secondary.secret")},
            {"name": "primary", "adapter": "audiobookshelf", "endpoint": "https://primary.invalid", "secret_file": str(root / "primary.secret")},
        ],
        "libraries": [
            {"name": "books", "connection": "primary", "library_id": "library-a", "mount": "books", "provider_connections": ["second", "first"]},
            {"name": "other", "connection": "secondary", "library_id": "library-b", "mount": "other", "provider_connections": ["first"]},
        ],
        "mounts": [
            {"name": "other", "container_path": "/media/other", "service_path": "/other", "read_only": True},
            {"name": "books", "container_path": "/media/books", "service_path": "/books", "read_only": True},
        ],
        "providers": [
            {"name": "second", "adapter": "hardcover", "enabled": False, "cover_enabled": False, "secret_file": None},
            {"name": "first", "adapter": "hardcover", "enabled": False, "cover_enabled": False, "secret_file": None},
        ],
    }


class MetadataConfigurationTests(unittest.TestCase):
    def test_review_tls_paths_are_explicit_absolute_and_paired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root)
            source["review"] = {
                "canonical_origin": "https://review.example.invalid",
                "owner_password_hash_file": str(root / "owner.hash"),
                "tls_certificate_file": str(root / "review.crt"),
                "tls_private_key_file": str(root / "review.key"),
            }
            path = root / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            review = load_configuration_file(path).review
            assert review is not None
            self.assertEqual(
                review.tls_certificate_file, str(root / "review.crt")
            )
            self.assertEqual(
                review.tls_private_key_file, str(root / "review.key")
            )
            properties = json.loads(configuration_schema_json())["$defs"][
                "ReviewInput"
            ]["properties"]
            for field in ("tls_certificate_file", "tls_private_key_file"):
                absolute = next(
                    choice
                    for choice in properties[field]["anyOf"]
                    if choice.get("type") == "string"
                )
                self.assertEqual(absolute["pattern"], "^/")

            for index, fields in enumerate(
                (
                    {"tls_private_key_file": None},
                    {"tls_certificate_file": None},
                    {"tls_certificate_file": "relative.crt"},
                    {"tls_private_key_file": "relative.key"},
                )
            ):
                invalid = _source(root)
                invalid["review"] = {**source["review"], **fields}
                candidate = root / f"invalid-review-{index}.json"
                candidate.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(
                    ConfigurationError
                ):
                    load_configuration_file(candidate)

    def test_multiple_named_scopes_and_provider_order_are_explicit_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "config.json"
            path.write_text(json.dumps(_source(root)), encoding="utf-8")
            configuration = load_configuration_file(path)
        connection, library, mount, providers = configuration.select("primary", "books")
        self.assertEqual(connection.name, "primary")
        self.assertEqual(library.library_id, "library-a")
        self.assertEqual(library.provider_connections, ("first", "second"))
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
            previous = _source(root)
            previous["schema_version"] = "config-v3"
            cases.append(previous)
            missing_provider_secret = _source(root)
            missing_provider_secret["providers"][0]["enabled"] = True
            cases.append(missing_provider_secret)
            for index, source in enumerate(cases):
                path = root / f"bad-{index}.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    load_configuration_file(path)

    def test_multiple_referenced_cover_providers_share_the_bounded_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root)
            for provider in source["providers"]:
                provider["enabled"] = True
                provider["cover_enabled"] = True
                provider["secret_file"] = str(
                    root / f"{provider['name']}.secret"
                )
            path = root / "config.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            configuration = load_configuration_file(path)
        self.assertEqual(
            (True, True),
            tuple(value.cover_enabled for value in configuration.providers),
        )

    def test_generated_schema_is_current(self) -> None:
        expected = (Path(__file__).parents[2] / "docs" / "reference" / "config-v5.schema.json").read_text(encoding="utf-8")
        self.assertEqual(configuration_schema_json(), expected)

    def test_generated_schema_enforces_provider_variants(self) -> None:
        schema = json.loads(configuration_schema_json())["$defs"]
        self.assertEqual(
            {
                "DisabledGoogleBooksProviderInput",
                "DisabledHardcoverProviderInput",
                "DisabledOpenLibraryProviderInput",
                "EnabledGoogleBooksProviderInput",
                "EnabledHardcoverProviderInput",
                "EnabledOpenLibraryProviderInput",
            },
            {
                value["$ref"].rsplit("/", 1)[-1]
                for value in schema["ProviderInput"]["anyOf"]
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = (
                {
                    "name": "provider",
                    "adapter": "hardcover",
                    "enabled": True,
                    "cover_enabled": True,
                    "secret_file": str(root / "provider.secret"),
                },
                {
                    "name": "provider",
                    "adapter": "hardcover",
                    "enabled": False,
                    "cover_enabled": False,
                    "secret_file": None,
                },
                {
                    "name": "provider",
                    "adapter": "google_books",
                    "enabled": True,
                    "cover_enabled": False,
                    "secret_file": None,
                },
                {
                    "name": "provider",
                    "adapter": "google_books",
                    "enabled": True,
                    "cover_enabled": False,
                    "secret_file": str(root / "provider.secret"),
                },
                {
                    "name": "provider",
                    "adapter": "google_books",
                    "enabled": False,
                    "cover_enabled": False,
                    "secret_file": None,
                },
                {
                    "name": "provider",
                    "adapter": "open_library",
                    "enabled": True,
                    "cover_enabled": True,
                    "secret_file": None,
                    "contact_email": "operator@example.org",
                },
                {
                    "name": "provider",
                    "adapter": "open_library",
                    "enabled": False,
                    "cover_enabled": False,
                    "secret_file": None,
                },
            )
            for provider in valid:
                source = _source(root)
                source["providers"] = [provider]
                ConfigurationInput.model_validate(source)

            invalid = (
                {
                    "adapter": "google_books",
                    "enabled": False,
                    "secret_file": str(root / "provider.secret"),
                },
                {
                    "adapter": "google_books",
                    "enabled": True,
                    "cover_enabled": True,
                    "secret_file": None,
                },
                {
                    "adapter": "open_library",
                    "enabled": True,
                    "secret_file": None,
                },
                {
                    "adapter": "open_library",
                    "enabled": True,
                    "secret_file": str(root / "provider.secret"),
                    "contact_email": "operator@example.org",
                },
                {
                    "adapter": "hardcover",
                    "enabled": True,
                    "secret_file": None,
                },
                {
                    "adapter": "hardcover",
                    "enabled": False,
                    "cover_enabled": True,
                    "secret_file": None,
                },
            )
            for changes in invalid:
                source = _source(root)
                source["providers"][0].update(changes)
                with self.subTest(changes=changes), self.assertRaises(ValidationError):
                    ConfigurationInput.model_validate(source)

    def test_provider_adapters_have_closed_adapter_specific_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = _source(root)
            valid["providers"] = [
                {
                    "name": "first",
                    "adapter": "hardcover",
                    "enabled": True,
                    "cover_enabled": False,
                    "secret_file": str(root / "hardcover.secret"),
                },
                {
                    "name": "second",
                    "adapter": "google_books",
                    "enabled": True,
                    "cover_enabled": False,
                    "secret_file": None,
                },
                {
                    "name": "third",
                    "adapter": "open_library",
                    "enabled": True,
                    "cover_enabled": True,
                    "secret_file": None,
                    "contact_email": "operator@example.org",
                },
            ]
            path = root / "valid.json"
            path.write_text(json.dumps(valid), encoding="utf-8")
            configuration = load_configuration_file(path)
            self.assertEqual(
                tuple((value.name, value.adapter) for value in configuration.providers),
                (
                    ("first", "hardcover"),
                    ("second", "google_books"),
                    ("third", "open_library"),
                ),
            )
            self.assertNotIn("operator@example.org", repr(configuration))

            invalid = []
            for adapter, secret, contact in (
                ("open_library", str(root / "secret"), "operator@example.org"),
                ("open_library", None, None),
                ("open_library", None, "not-an-email"),
                ("open_library", None, ".operator@example.org"),
                ("open_library", None, "operator.@example.org"),
                ("open_library", None, "op..erator@example.org"),
                ("open_library", None, "a" * 65 + "@example.org"),
                ("hardcover", str(root / "secret"), "operator@example.org"),
                ("unknown", None, "operator@example.org"),
            ):
                source = _source(root)
                source["providers"][0].update(
                    {
                        "adapter": adapter,
                        "enabled": True,
                        "secret_file": secret,
                        "contact_email": contact,
                    }
                )
                invalid.append(source)
            for index, source in enumerate(invalid):
                candidate = root / f"invalid-provider-{index}.json"
                candidate.write_text(json.dumps(source), encoding="utf-8")
                with self.subTest(index=index), self.assertRaises(ConfigurationError):
                    load_configuration_file(candidate)

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
        provider_secret = schema["EnabledHardcoverProviderInput"]["properties"][
            "secret_file"
        ]
        self.assertEqual(provider_secret["pattern"], "^/")

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
            ProviderConnectionConfiguration("catalogue", True, True, "provider.secret")


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

    def test_hardcover_raw_token_is_confined_to_the_exact_graphql_post(self) -> None:
        token = "synthetic-provider-token"
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "secret"
            secret.write_text(token, encoding="ascii")
            secret.chmod(0o600)
            requests: list[httpx.Request] = []
            client = hardcover_client(
                str(secret),
                5,
                transport=httpx.MockTransport(
                    lambda request: requests.append(request)
                    or httpx.Response(200)
                ),
            )
            self.assertNotIn(token, repr(client))
            client.post("https://api.hardcover.app/v1/graphql")
            client.get("https://api.hardcover.app/v1/graphql")
            client.post("https://other.invalid/v1/graphql")
            client.close()
        self.assertEqual(
            "Bearer synthetic-provider-token",
            requests[0].headers.get("authorization"),
        )
        self.assertIsNone(requests[1].headers.get("authorization"))
        self.assertIsNone(requests[2].headers.get("authorization"))


if __name__ == "__main__":
    unittest.main()
