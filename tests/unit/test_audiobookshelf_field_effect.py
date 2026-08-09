from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import AudiobookshelfFieldEffectTarget
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.domain.cover_evidence import CoverArtifact


_PNG = b"\x89PNG\r\n\x1a\nsynthetic-cover"


class AudiobookshelfFieldEffectTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.artifacts = CoverArtifactStore(Path(self.directory.name) / "artifacts")
        artifact = CoverArtifact.create(_PNG, b"preview", "image/png", 1, 1)
        self.artifacts.stage(artifact, _PNG, b"preview")
        self.cover = artifact.artifact_id
        self.requests: list[httpx.Request] = []
        self.detail = {
            "id": "item-1", "libraryId": "library-1", "mediaType": "book", "updatedAt": 7,
            "media": {"metadata": {"title": "Before", "genres": ["old"]}, "tags": ["old"]},
        }
        self.cover_bytes: bytes | None = None

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.method == "GET" and request.url.path == "/api/items/item-1":
                return httpx.Response(200, headers={"content-type": "application/json"}, json=self.detail)
            if request.method == "GET" and request.url.path == "/api/items/item-1/cover":
                if self.cover_bytes is None:
                    return httpx.Response(404)
                return httpx.Response(200, headers={"content-type": "image/png"}, content=self.cover_bytes)
            if request.method == "PATCH":
                payload = json.loads(request.content)
                self.assertEqual(payload, {"metadata": {"title": "After"}})
                self.detail["media"]["metadata"]["title"] = "After"
                self.detail["updatedAt"] = 8
                return httpx.Response(200, json={"updated": True})
            if request.method == "POST":
                self.assertIn(b"name=\"cover\"", request.content)
                self.assertIn(_PNG, request.content)
                self.cover_bytes = _PNG
                self.detail["updatedAt"] = 9
                return httpx.Response(200, json={"success": True})
            return httpx.Response(500)

        self.target = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer test-key"}),
            endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_metadata_effect_is_destination_minimal_and_readable(self) -> None:
        plan = SimpleNamespace(work_id="item-1", destination="title", desired_value="After")
        before = self.target.read(plan)
        self.assertEqual((before.value, before.version), ("Before", "7"))
        self.assertEqual(self.target.apply(plan).outcome, "accepted")
        after = self.target.read(plan)
        self.assertEqual((after.value, after.version), ("After", "8"))
        patch = next(request for request in self.requests if request.method == "PATCH")
        self.assertEqual(patch.headers["authorization"], "Bearer test-key")

    def test_cover_reads_digest_and_uploads_only_verified_original(self) -> None:
        plan = SimpleNamespace(work_id="item-1", destination="cover", desired_value=self.cover)
        self.assertEqual(self.target.read(plan).value, None)
        self.assertEqual(self.target.apply(plan).outcome, "accepted")
        self.assertEqual(self.target.read(plan).value, self.cover)
        self.assertEqual(len([request for request in self.requests if request.method == "POST"]), 1)

    def test_refusal_and_unknown_cover_are_not_retried(self) -> None:
        plan = SimpleNamespace(work_id="item-1", destination="cover", desired_value="cover_" + "0" * 64)
        self.assertEqual(self.target.apply(plan).outcome, "conflict")
        self.assertFalse(any(request.method == "POST" for request in self.requests))


if __name__ == "__main__":
    unittest.main()
