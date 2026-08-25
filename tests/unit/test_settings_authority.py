import json
import os
from pathlib import Path

import pytest

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    SettingsView,
)
from mechanical_librarian.infrastructure.settings_authority import (
    ReviewBootstrap,
    load_review_bootstrap,
    resolve_startup_configuration,
    store_runtime_configuration,
)


def test_absent_file_selects_store(tmp_path: Path) -> None:
    assert (
        resolve_startup_configuration(tmp_path / "config.json", lambda: "store")
        == "store"
    )


def test_invalid_present_file_does_not_select_store(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        resolve_startup_configuration(path, lambda: "store")


def test_bootstrap_is_strict_private_and_closed(tmp_path: Path) -> None:
    path = tmp_path / "review-bootstrap.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "review-bootstrap-v1",
                "canonical_origin": "https://review.example.invalid",
                "tls_certificate_file": "/run/secrets/tls.crt",
                "tls_private_key_file": "/run/secrets/tls.key",
                "unexpected": True,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    with pytest.raises(ConfigurationError, match="bootstrap"):
        load_review_bootstrap(path)


def test_store_projection_combines_only_deploy_bootstrap_and_store_authority() -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
            "revision": 1,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                    "mount": "books",
                }
            ],
        }
    )
    settings = SettingsView(
        document=document,
        fields=(),
        secrets=(),
        pending_restart=(),
        authority="store",
    )
    bootstrap = ReviewBootstrap.model_validate(
        {
            "schema_version": "review-bootstrap-v1",
            "canonical_origin": "https://review.example.invalid",
            "tls_certificate_file": "/run/secrets/tls.crt",
            "tls_private_key_file": "/run/secrets/tls.key",
            "mounts": [
                {
                    "name": "books",
                    "container_path": "/media/books",
                    "service_path": "/library/books",
                    "read_only": True,
                }
            ],
        }
    )

    configuration = store_runtime_configuration(
        settings, {"connection.primary.key": "target-secret"}, bootstrap
    )

    assert configuration.connections[0].endpoint == "https://library.example.invalid"
    assert configuration.mounts[0].container_path == "/media/books"
    assert configuration.review is not None
    assert configuration.review.canonical_origin == "https://review.example.invalid"
