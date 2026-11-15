"""The settings authority and catalogue entry preparation-pipeline tests share."""

from __future__ import annotations

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.domain.owner_settings import (
    OWNER_SETTINGS_SCHEMA,
    OwnerSettingsDocument,
)

PRODUCER = "f" * 64


def install_settings_authority(repository: object) -> None:
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": OWNER_SETTINGS_SCHEMA,
            "revision": 1,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "libraries": [
                {
                    "name": name,
                    "connection": "primary",
                    "library_id": library_id,
                    "media_kind": "ebook",
                }
                for name, library_id in (
                    ("fiction", "lib-1"),
                    ("history", "lib-2"),
                    ("poetry", "lib-3"),
                )
            ],
        }
    )
    repository._connection.execute(
        "INSERT INTO owner_settings VALUES(1,?,?)",
        (document.revision, _canonical_json(document.model_dump())),
    )
    repository._connection.executemany(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        (
            ("connection.primary.key", "synthetic-target-secret"),
            ("owner.password.verifier", "$argon2id$synthetic"),
        ),
    )
    repository._connection.commit()


def entry(item_id: str, file_identity: str) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        item_id,
        1,
        "Synthetic work",
        ("Example Author",),
        True,
        False,
        "singular-supported",
        file_identity,
        f"/{item_id}.epub",
        "epub",
        "books",
    )
