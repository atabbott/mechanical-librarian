"""Shared Audiobookshelf target snapshots.

Reads one synthetic item through the real reader so a target
projection assertion sees the same snapshot the adapter builds.
"""

from __future__ import annotations

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
)


def target_snapshot(metadata: dict[str, object]) -> TargetSnapshot:
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": {
            "metadata": metadata,
            "ebookFile": {
                "ebookFormat": "epub",
                "ino": "inode-1",
                "metadata": {
                    "relPath": "book.epub",
                    "path": "/library/book.epub",
                    "size": 1,
                },
            },
        },
    }
    return AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65_536,
    ).read("item-1")
