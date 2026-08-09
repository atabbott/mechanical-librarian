from __future__ import annotations

import tempfile
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectCapabilities,
    AudiobookshelfFieldEffectTarget,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.field_effects import (
    GuardedFieldApplication,
    TargetServiceUnavailable,
)
from mechanical_librarian.domain.cover_evidence import CoverArtifact
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
)


_PNG = b"\x89PNG\r\n\x1a\nsynthetic-cover"


class AudiobookshelfFieldEffectTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.artifacts = CoverArtifactStore(Path(self.directory.name) / "artifacts")
        artifact = CoverArtifact.create(_PNG, b"preview", "image/png", 1, 1)
        self.artifacts.stage(artifact, _PNG, b"preview")
        self.cover = artifact.artifact_id
        self.requests: list[httpx.Request] = []
        self.events: list[str] = []
        self.detail = {
            "id": "item-1", "libraryId": "library-1", "mediaType": "book", "updatedAt": 7,
            "media": {"metadata": {"title": "Before", "genres": ["old"]}, "tags": ["old"]},
        }
        self.cover_bytes: bytes | None = None
        self.write_status = 200

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            self.events.append(request.method)
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
                return httpx.Response(self.write_status, json={"updated": True})
            if request.method == "POST":
                self.assertIn(b"name=\"cover\"", request.content)
                self.assertIn(b'filename="cover.png"', request.content)
                self.assertIn(_PNG, request.content)
                self.cover_bytes = _PNG
                self.detail["updatedAt"] = 9
                return httpx.Response(self.write_status, json={"success": True})
            return httpx.Response(500)

        self.target = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer test-key"}),
            endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def plan(self, destination: str, pre_value: object, desired_value: object,
             target_version: str = "7") -> FieldEffectPlan:
        authority = EffectAuthority(
            "human_decision_" + "a" * 64, "review_case_" + "b" * 64,
            "c" * 64, "fake_service", "human", "candidate", None, None,
        )
        consent = EffectConsent.create(
            connection="primary", library="books", destination=destination,
            granted_at=1,
        )
        return FieldEffectPlan.create(
            connection="primary", library="books", work_id="item-1",
            destination=destination, pre_value=pre_value,
            desired_value=desired_value, target_fingerprint="c" * 64,
            target_version=target_version, dependency_digests=("d" * 64,),
            authority=authority, consent=consent, capability_digest="e" * 64,
            created_at=1,
        )

    def test_metadata_effect_is_destination_minimal_and_readable(self) -> None:
        plan = self.plan("title", "Before", "After")
        before = self.target.read(plan)
        self.assertEqual((before.value, before.version), ("Before", "7"))
        self.assertEqual(self.target.apply(plan).outcome, "accepted")
        after = self.target.read(plan)
        self.assertEqual((after.value, after.version), ("After", "8"))
        patch = next(request for request in self.requests if request.method == "PATCH")
        self.assertEqual(patch.headers["authorization"], "Bearer test-key")

    def test_cover_reads_digest_and_uploads_only_verified_original(self) -> None:
        plan = self.plan("cover", None, self.cover)
        self.assertEqual(self.target.read(plan).value, None)
        self.events.clear()
        original = self.artifacts.read_original_digest
        with patch.object(
            self.artifacts, "read_original_digest",
            side_effect=lambda digest: self.events.append("artifact") or original(digest),
        ):
            self.assertEqual(self.target.apply(plan).outcome, "accepted")
        self.assertEqual(self.events[0], "artifact")
        self.assertEqual(self.events[-1], "POST")
        self.assertEqual(self.target.read(plan).value, self.cover)
        self.assertEqual(len([request for request in self.requests if request.method == "POST"]), 1)

    def test_refusal_and_unknown_cover_are_not_retried(self) -> None:
        plan = self.plan("cover", None, "cover_" + "0" * 64)
        self.assertEqual(self.target.apply(plan).outcome, "conflict")
        self.assertFalse(any(request.method == "POST" for request in self.requests))

    def test_tags_are_read_from_current_service_metadata_shape(self) -> None:
        self.detail["media"].pop("tags")
        self.detail["media"]["metadata"]["tags"] = ["old"]
        self.assertEqual(self.target.read(self.plan("tags", ["old"], ["new"])).value, ["old"])

    def test_immediate_prewrite_destination_or_version_drift_refuses_patch(self) -> None:
        plan = self.plan("title", "Before", "After", target_version="6")
        self.assertEqual(self.target.apply(plan).outcome, "conflict")
        self.assertFalse(any(request.method == "PATCH" for request in self.requests))
        self.assertFalse(any(request.method == "POST" for request in self.requests))

    def test_success_without_body_is_accepted_and_write_refusals_are_ambiguous(self) -> None:
        self.write_status = 204
        self.assertEqual(self.target.apply(self.plan("title", "Before", "After")).outcome, "accepted")
        self.detail["media"]["metadata"]["title"] = "Before"
        self.detail["updatedAt"] = 7
        self.write_status = 403
        refused = self.target.apply(self.plan("title", "Before", "After"))
        self.assertEqual(
            (refused.outcome, refused.suspension_scope, refused.suspension_reason),
            ("ambiguous", "destination", "insufficient_permission"),
        )
        self.detail["media"]["metadata"]["title"] = "Before"
        self.detail["updatedAt"] = 7
        self.write_status = 500
        failed = self.target.apply(self.plan("title", "Before", "After"))
        self.assertEqual(
            (failed.outcome, failed.suspension_scope, failed.suspension_reason),
            ("ambiguous", "destination", "unexpected_effect_behavior"),
        )

        self.requests.clear()
        changed = self.plan("title", "Elsewhere", "After")
        self.assertEqual(self.target.apply(changed).outcome, "conflict")
        self.assertFalse(any(request.method == "PATCH" for request in self.requests))

    def test_capability_constructor_rejects_unbounded_or_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            AudiobookshelfFieldEffectCapabilities(client_factory=lambda: httpx.Client(), endpoint="invalid", library_id="", maximum_response_bytes=1)

    def test_capability_is_field_local_and_malformed_or_unavailable_state_fails_closed(self) -> None:
        user = {
            "isActive": True,
            "permissions": {"update": True, "upload": False, "accessAllLibraries": False},
            "librariesAccessible": ["library-1"],
        }

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/me":
                return httpx.Response(200, headers={"content-type": "application/json"}, json=user)
            return httpx.Response(200, headers={"content-type": "application/json"}, json=self.detail)

        capabilities = AudiobookshelfFieldEffectCapabilities(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
            endpoint="https://abs.example.invalid", library_id="library-1",
        )
        title_before = capabilities.result(self.plan("title", "Before", "After"))
        cover_before = capabilities.result(self.plan("cover", None, self.cover))
        self.assertEqual(title_before.category, "allowed")
        self.assertEqual(cover_before.category, "insufficient_permission")
        user["permissions"]["upload"] = True
        title_after = capabilities.result(self.plan("title", "Before", "After"))
        cover_after = capabilities.result(self.plan("cover", None, self.cover))
        self.assertEqual(title_after, title_before)
        self.assertEqual(cover_after.category, "allowed")
        self.assertNotEqual(cover_after.digest, cover_before.digest)
        user["librariesAccessible"] = "library-1"
        self.assertEqual(capabilities.result(self.plan("title", "Before", "After")).category, "connection_unavailable")
        user["permissions"]["accessAllLibraries"] = True
        self.assertEqual(capabilities.result(self.plan("title", "Before", "After")).category, "allowed")
        self.detail["media"]["metadata"] = "invalid"
        self.assertEqual(capabilities.result(self.plan("title", "Before", "After")).category, "field_unavailable")
        self.detail["media"]["metadata"] = {"title": "Before"}
        user["isActive"] = False
        self.assertEqual(capabilities.result(self.plan("title", "Before", "After")).category, "connection_unavailable")

        unavailable = AudiobookshelfFieldEffectCapabilities(
            client_factory=lambda: httpx.Client(
                transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadError("lost", request=request)))
            ),
            endpoint="https://abs.example.invalid", library_id="library-1",
        )
        self.assertEqual(unavailable.result(self.plan("title", "Before", "After")).category, "connection_unavailable")

    def test_streamed_reads_reject_encoded_or_oversized_responses(self) -> None:
        plan = self.plan("title", "Before", "After")
        encoded = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"}, content=b"invalid")
            )),
            endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
        )
        with self.assertRaisesRegex(TargetServiceUnavailable, "unexpected_effect_behavior"):
            encoded.read(plan)

        oversized = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{" + b" " * 1024)
            )),
            endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
            maximum_response_bytes=1024,
        )
        with self.assertRaisesRegex(TargetServiceUnavailable, "field_unavailable"):
            oversized.read(plan)

    def test_target_read_failures_preserve_connection_permission_and_unexpected_scope(self) -> None:
        plan = self.plan("title", "Before", "After")
        expected = {
            401: ("connection", "connection_unavailable"),
            403: ("destination", "insufficient_permission"),
            500: ("destination", "unexpected_effect_behavior"),
        }
        for status, disposition in expected.items():
            with self.subTest(status=status):
                target = AudiobookshelfFieldEffectTarget(
                    client_factory=lambda status=status: httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(status)
                        )
                    ),
                    endpoint="https://abs.example.invalid", library_id="library-1",
                    artifacts=self.artifacts,
                )
                with self.assertRaises(TargetServiceUnavailable) as raised:
                    target.read(plan)
                self.assertEqual(
                    (raised.exception.scope, raised.exception.reason), disposition
                )
        transport = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ReadError("lost", request=request)
                )
            )),
            endpoint="https://abs.example.invalid", library_id="library-1",
            artifacts=self.artifacts,
        )
        with self.assertRaises(TargetServiceUnavailable) as raised:
            transport.read(plan)
        self.assertEqual(
            (raised.exception.scope, raised.exception.reason),
            ("destination", "unexpected_effect_behavior"),
        )

    def test_oversized_cover_reads_suspend_the_destination_before_and_after_write(self) -> None:
        oversized = b"x" * (8 * 1024 * 1024 + 1)
        for phase in ("preclaim", "verification"):
            with self.subTest(phase=phase):
                wrote = False

                def handle(request: httpx.Request) -> httpx.Response:
                    nonlocal wrote
                    if request.method == "POST":
                        wrote = True
                        return httpx.Response(200)
                    if request.url.path.endswith("/cover"):
                        if phase == "preclaim" or wrote:
                            return httpx.Response(
                                200, headers={"content-type": "image/jpeg"},
                                content=oversized,
                            )
                        return httpx.Response(404)
                    return httpx.Response(
                        200, headers={"content-type": "application/json"},
                        json=self.detail,
                    )

                target = AudiobookshelfFieldEffectTarget(
                    client_factory=lambda: httpx.Client(
                        transport=httpx.MockTransport(handle)
                    ),
                    endpoint="https://abs.example.invalid", library_id="library-1",
                    artifacts=self.artifacts,
                )
                plan = self.plan("cover", None, self.cover)
                root = Path(self.directory.name) / ("cover-" + phase)
                root.mkdir()
                os.chmod(root, 0o700)
                repository = open_repository(root / "effects.sqlite")
                repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
                current = type("Current", (), {"current": lambda self, value: True})()
                ticks = iter(range(100, 200))
                service = GuardedFieldApplication(
                    repository, target, FakeBackupReadiness(),
                    FakeCapabilityObserver("e" * 64), current,
                    FakeDependencyCurrentness(), current,
                    PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
                    FakeForwardReevaluation(), lambda: next(ticks),
                )
                result = service.apply(plan.plan_id)
                self.assertEqual(
                    result.state, "stale" if phase == "preclaim" else "reconciling"
                )
                self.assertEqual(
                    repository.field_effect_capability_suspension(
                        plan.connection, "cover"
                    ),
                    "field_unavailable",
                )
                repository.close()

    def test_post_dispatch_status_and_transport_loss_reconcile_through_application(self) -> None:
        for outcome in (401, 403, 500, "transport_loss"):
            with self.subTest(outcome=outcome):
                detail = {
                    "id": "item-1", "libraryId": "library-1", "mediaType": "book", "updatedAt": 7,
                    "media": {"metadata": {"title": "Before"}, "tags": []},
                }
                requests: list[str] = []

                def handle(request: httpx.Request) -> httpx.Response:
                    requests.append(request.method)
                    if request.method == "GET":
                        return httpx.Response(200, headers={"content-type": "application/json"}, json=detail)
                    detail["media"]["metadata"]["title"] = "After"
                    detail["updatedAt"] = 8
                    if outcome == "transport_loss":
                        raise httpx.ReadError("response lost", request=request)
                    return httpx.Response(outcome)

                target = AudiobookshelfFieldEffectTarget(
                    client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
                    endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
                )
                plan = self.plan("title", "Before", "After")
                root = Path(self.directory.name) / ("case-" + str(outcome))
                root.mkdir()
                os.chmod(root, 0o700)
                repository = open_repository(root / "effects.sqlite")
                repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
                current = type("Current", (), {"current": lambda self, value: True})()
                ticks = iter(range(10, 100))
                service = GuardedFieldApplication(
                    repository, target, FakeBackupReadiness(), FakeCapabilityObserver("e" * 64),
                    current, FakeDependencyCurrentness(), current,
                    PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
                    FakeForwardReevaluation(), lambda: next(ticks),
                )
                self.assertEqual(service.apply(plan.plan_id).state, "verified")
                self.assertEqual(requests.count("PATCH"), 1)
                self.assertGreaterEqual(requests.count("GET"), 4)
                self.assertEqual(
                    repository.field_effect_capability_suspension(plan.connection, "title"),
                    ("connection_unavailable" if outcome == 401 else
                     "insufficient_permission" if outcome == 403 else
                     "unexpected_effect_behavior"),
                )
                repository.close()


if __name__ == "__main__":
    unittest.main()
