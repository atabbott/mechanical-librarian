"""Shared Audiobookshelf target snapshots.

Reads one synthetic item through the real reader so a target
projection assertion sees the same snapshot the adapter builds.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectTarget,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    AudiobookshelfItemReader,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
)
from mechanical_librarian.domain.cover_evidence import CoverArtifact


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


SYNTHETIC_COVER_PNG = b"\x89PNG\r\n\x1a\nsynthetic-cover"


class FieldEffectTargetCase:
    """The staged artifacts, mock transport and target a `setUp` carried."""

    def __init__(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.artifacts = CoverArtifactStore(Path(self.directory.name) / "artifacts")
        artifact = CoverArtifact.create(
            SYNTHETIC_COVER_PNG, b"preview", "image/png", 1, 1
        )
        self.artifacts.stage(artifact, SYNTHETIC_COVER_PNG, b"preview")
        self.cover = artifact.artifact_id
        self.requests: list[httpx.Request] = []
        self.events: list[str] = []
        self.detail = {
            "id": "item-1",
            "libraryId": "library-1",
            "mediaType": "book",
            "updatedAt": 7,
            "media": {
                "metadata": {"title": "Before", "genres": ["old"]},
                "tags": ["old"],
            },
        }
        self.cover_bytes: bytes | None = None
        self.write_status = 200
        self.paired_mode = "normal"
        self.fail_read_once = False

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.events.append(request.method)
            if request.method == "GET" and request.url.path == "/api/items/item-1":
                if self.fail_read_once:
                    self.fail_read_once = False
                    raise httpx.ReadTimeout("synthetic readback unavailable")
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, json=self.detail
                )
            if (
                request.method == "GET"
                and request.url.path == "/api/items/item-1/cover"
            ):
                if self.cover_bytes is None:
                    return httpx.Response(404)
                return httpx.Response(
                    200, headers={"content-type": "image/png"}, content=self.cover_bytes
                )
            if request.method == "PATCH":
                payload = json.loads(request.content)
                if "tags" in payload:
                    assert set(payload) == {"tags"}
                    self.detail["media"]["tags"] = payload["tags"]
                else:
                    assert set(payload) == {"metadata"}
                    metadata = payload["metadata"]
                    assert isinstance(metadata, dict)
                    assert len(metadata) == 1 or set(metadata) == {"title", "subtitle"}
                    if set(metadata) == {"title", "subtitle"}:
                        selected = (
                            {"title": metadata["title"]}
                            if self.paired_mode.startswith("partial")
                            else metadata
                        )
                        self.detail["media"]["metadata"].update(selected)
                        if self.paired_mode in {"timeout", "partial_timeout"}:
                            raise httpx.ReadTimeout("synthetic timeout after PATCH")
                        if self.paired_mode == "crash":
                            raise KeyboardInterrupt("synthetic crash after PATCH")
                        if self.paired_mode == "read_failure":
                            self.fail_read_once = True
                    else:
                        self.detail["media"]["metadata"].update(metadata)
                self.detail["updatedAt"] = 8
                return httpx.Response(self.write_status, json={"updated": True})
            if request.method == "POST":
                assert b'name="cover"' in request.content
                assert b'filename="cover.png"' in request.content
                assert SYNTHETIC_COVER_PNG in request.content
                self.cover_bytes = SYNTHETIC_COVER_PNG
                self.detail["updatedAt"] = 9
                return httpx.Response(self.write_status, json={"success": True})
            return httpx.Response(500)

        self.target = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(handle),
                headers={"Authorization": "Bearer test-key"},
            ),
            endpoint="https://abs.example.invalid",
            library_id="library-1",
            artifacts=self.artifacts,
        )

    def close(self) -> None:
        self.directory.cleanup()


def synthetic_audio_file(
    name: str, extension: str = ".m4b", inode: str = "inode-1"
) -> dict[str, object]:
    return {
        "ino": inode,
        "metadata": {
            "relPath": name,
            "path": f"/library/{name}",
            "size": 1,
            "ext": extension,
        },
    }
