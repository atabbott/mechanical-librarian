from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import httpx
import pytest

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
    field_value_version,
)

if TYPE_CHECKING:
    # Imported for the `cast` below only, which is a runtime no-op.
    from collections.abc import Callable

_PNG = b"\x89PNG\r\n\x1a\nsynthetic-cover"


class _Case:
    """The staged artifacts, mock transport and target a `setUp` carried."""

    def __init__(self) -> None:
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
                if "tags" in payload:
                    assert set(payload) == {"tags"}
                    self.detail["media"]["tags"] = payload["tags"]
                else:
                    assert set(payload) == {"metadata"}
                    metadata = payload["metadata"]
                    assert isinstance(metadata, dict)
                    assert 1 == len(metadata)
                    self.detail["media"]["metadata"].update(metadata)
                self.detail["updatedAt"] = 8
                return httpx.Response(self.write_status, json={"updated": True})
            if request.method == "POST":
                assert b"name=\"cover\"" in request.content
                assert b'filename="cover.png"' in request.content
                assert _PNG in request.content
                self.cover_bytes = _PNG
                self.detail["updatedAt"] = 9
                return httpx.Response(self.write_status, json={"success": True})
            return httpx.Response(500)

        self.target = AudiobookshelfFieldEffectTarget(
            client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle), headers={"Authorization": "Bearer test-key"}),
            endpoint="https://abs.example.invalid", library_id="library-1", artifacts=self.artifacts,
        )

    def close(self) -> None:
        self.directory.cleanup()


@pytest.fixture
def case() -> Iterator[_Case]:
    prepared = _Case()
    try:
        yield prepared
    finally:
        prepared.close()

def _plan(destination: str, pre_value: object, desired_value: object,
         target_version: str | None = None,
         envelope_identity: str = "a" * 64) -> FieldEffectPlan:
    authority = EffectAuthority(
        "human_decision_" + "a" * 64, "review_case_" + "b" * 64,
        "c" * 64, "fake_service", "human", "candidate", None, None,
    )
    consent = EffectConsent.create(
        connection="primary", library="books", destination=destination,
        envelope_identity=envelope_identity,
        granted_at=1,
    )
    return FieldEffectPlan.create(
        connection="primary", library="books", work_id="item-1",
        destination=destination, pre_value=pre_value,
        desired_value=desired_value, target_fingerprint="c" * 64,
        target_version=(target_version if target_version is not None else
                        field_value_version(destination, pre_value)),
        dependency_digests=("d" * 64,),
        authority=authority, consent=consent, capability_digest="e" * 64,
        created_at=1,
    )


def test_metadata_effect_is_destination_minimal_and_readable(case: _Case) -> None:
    plan = _plan("title", "Before", "After")
    before = case.target.read(plan)
    assert (before.value, before.version) == ("Before", field_value_version("title", "Before"))
    assert case.target.apply(plan).outcome == "accepted"
    after = case.target.read(plan)
    assert (after.value, after.version) == ("After", field_value_version("title", "After"))
    patch = next(request for request in case.requests if request.method == "PATCH")
    assert patch.headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize(
    "destination",
    ("subtitle", "series", "genres", "tags", "description", "isbn", "asin"),
)
def test_rich_metadata_target_shapes_read_apply_and_read_back(
    case: _Case, destination: str
) -> None:
    metadata = case.detail["media"]["metadata"]
    metadata.update({
        "subtitle": None,
        "series": [],
        "genres": [],
        "description": None,
        "isbn": None,
        "asin": None,
    })
    case.detail["media"]["tags"] = []
    values = {
        "subtitle": "Synthetic subtitle",
        "series": [{"name": "Synthetic series", "sequence": "1"}],
        "genres": ["Synthetic genre"],
        "tags": ["Synthetic tag"],
        "description": "Synthetic description.",
        "isbn": "9780306406157",
        "asin": "B000000001",
    }
    desired = values[destination]
    plan = _plan(destination, None, desired)
    assert case.target.read(plan).value is None
    assert "accepted" == case.target.apply(plan).outcome
    assert desired == case.target.read(plan).value


def test_cover_reads_digest_and_uploads_only_verified_original(case: _Case) -> None:
    plan = _plan("cover", None, case.cover)
    assert case.target.read(plan).value is None
    case.events.clear()
    original = case.artifacts.read_original_digest
    with patch.object(
        case.artifacts, "read_original_digest",
        side_effect=lambda digest: case.events.append("artifact") or original(digest),  # type: ignore[func-returns-value]  # reason: deliberate `append(...) or value` idiom - `append` returning `None` is what lets the real call's result be the side effect's result.
    ):
        assert case.target.apply(plan).outcome == "accepted"
    assert case.events[0] == "artifact"
    assert case.events[-1] == "POST"
    assert case.target.read(plan).value == case.cover
    assert len([request for request in case.requests if request.method == "POST"]) == 1


def test_refusal_and_unknown_cover_are_not_retried(case: _Case) -> None:
    plan = _plan("cover", None, "cover_" + "0" * 64)
    assert case.target.apply(plan).outcome == "conflict"
    assert not any(request.method == "POST" for request in case.requests)


def test_tags_are_read_from_current_service_metadata_shape(case: _Case) -> None:
    case.detail["media"]["tags"] = None
    case.detail["media"]["metadata"]["tags"] = ["old"]
    assert case.target.read(_plan("tags", None, ["new"])).value is None
    case.detail["media"].pop("tags")
    assert case.target.read(_plan("tags", ["old"], ["new"])).value == ["old"]
    case.detail["media"]["tags"] = ["top-level"]
    case.detail["media"].pop("metadata")
    assert case.target.read(
            _plan("tags", ["top-level"], ["new"])
        ).value == ["top-level"]


@pytest.mark.parametrize("destination", ("authors", "genres", "series", "tags"))
def test_empty_collections_and_service_series_shape_are_normalized(
    case: _Case, destination: str
) -> None:
    metadata = case.detail["media"]["metadata"]
    metadata["authors"] = []
    metadata["genres"] = []
    metadata["series"] = []
    case.detail["media"]["tags"] = []
    desired = {
        "authors": ["New"], "genres": ["New"],
        "series": [{"name": "New", "sequence": None}], "tags": ["New"],
    }
    assert case.target.read(
        _plan(destination, None, desired[destination])
    ).value is None
    metadata["series"] = [{"id": "service-id", "name": "Old", "sequence": 2.5}]
    assert case.target.read(_plan("series", None, desired["series"])).value == [{"name": "Old", "sequence": "2.5"}]


def test_immediate_prewrite_destination_or_version_drift_refuses_patch(case: _Case) -> None:
    plan = _plan("title", "Before", "After", target_version="6")
    assert case.target.apply(plan).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)
    assert not any(request.method == "POST" for request in case.requests)


def test_unrelated_item_revision_does_not_block_the_field_local_effect(case: _Case) -> None:
    plan = _plan("title", "Before", "After")
    case.detail["updatedAt"] = 99
    case.detail["media"]["metadata"]["genres"] = ["externally changed"]
    assert case.target.apply(plan).outcome == "accepted"


def test_success_without_body_is_accepted_and_write_refusals_are_ambiguous(case: _Case) -> None:
    case.write_status = 204
    assert case.target.apply(_plan("title", "Before", "After")).outcome == "accepted"
    case.detail["media"]["metadata"]["title"] = "Before"
    case.detail["updatedAt"] = 7
    case.write_status = 403
    refused = case.target.apply(_plan("title", "Before", "After"))
    assert (refused.outcome, refused.suspension_scope, refused.suspension_reason) == ("ambiguous", "destination", "insufficient_permission")
    case.detail["media"]["metadata"]["title"] = "Before"
    case.detail["updatedAt"] = 7
    case.write_status = 500
    failed = case.target.apply(_plan("title", "Before", "After"))
    assert (failed.outcome, failed.suspension_scope, failed.suspension_reason) == ("ambiguous", "destination", "unexpected_effect_behavior")

    case.requests.clear()
    changed = _plan("title", "Elsewhere", "After")
    assert case.target.apply(changed).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)


def test_capability_constructor_rejects_unbounded_or_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        AudiobookshelfFieldEffectCapabilities(client_factory=lambda: httpx.Client(), endpoint="invalid", library_id="", maximum_response_bytes=1)


def test_target_accepts_the_application_owned_artifact_reader_contract() -> None:
    class ArtifactReader:
        def read_original_digest(self, digest: str) -> bytes:
            self.digest = digest
            return _PNG

    reader = ArtifactReader()
    target = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=reader,
    )

    assert isinstance(target, AudiobookshelfFieldEffectTarget)


def test_client_construction_and_deep_json_fail_closed(case: _Case) -> None:
    def unavailable_client() -> httpx.Client:
        raise RuntimeError("synthetic credential unavailable")

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=unavailable_client,
        endpoint="https://abs.example.invalid", library_id="library-1",
    )
    plan = _plan("title", "Before", "After")
    assert capabilities.result(plan).category == "connection_unavailable"
    target = AudiobookshelfFieldEffectTarget(
        client_factory=unavailable_client,
        endpoint="https://abs.example.invalid", library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        target.read(plan)
    assert (raised.value.scope, raised.value.reason) == ("connection", "connection_unavailable")

    nested = b'{"value":' + b"[" * 101 + b"0" + b"]" * 101 + b"}"
    def deep_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, content=nested
            )
        ))

    deep_capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=deep_client,
        endpoint="https://abs.example.invalid", library_id="library-1",
    )
    assert deep_capabilities.result(plan).category == "connection_unavailable"
    deep_target = AudiobookshelfFieldEffectTarget(
        client_factory=deep_client,
        endpoint="https://abs.example.invalid", library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        deep_target.read(plan)
    assert (raised.value.scope, raised.value.reason) == ("destination", "field_unavailable")


def test_capability_is_field_local_and_malformed_or_unavailable_state_fails_closed(case: _Case) -> None:
    user = {
        "isActive": True,
        "permissions": {"update": True, "upload": False, "accessAllLibraries": False},
        "librariesAccessible": ["library-1"],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(200, headers={"content-type": "application/json"}, json=user)
        return httpx.Response(200, headers={"content-type": "application/json"}, json=case.detail)

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid", library_id="library-1",
    )
    title_before = capabilities.result(_plan("title", "Before", "After"))
    cover_before = capabilities.result(_plan("cover", None, case.cover))
    assert title_before.category == "allowed"
    assert cover_before.category == "insufficient_permission"
    user["permissions"]["upload"] = True
    title_after = capabilities.result(_plan("title", "Before", "After"))
    cover_after = capabilities.result(_plan("cover", None, case.cover))
    assert title_after == title_before
    assert cover_after.category == "allowed"
    assert cover_after.digest != cover_before.digest
    user["librariesAccessible"] = "library-1"
    assert capabilities.result(_plan("title", "Before", "After")).category == "connection_unavailable"
    user["permissions"]["accessAllLibraries"] = True
    assert capabilities.result(_plan("title", "Before", "After")).category == "allowed"
    case.detail["media"]["tags"] = ["top-level"]
    case.detail["media"].pop("metadata")
    assert capabilities.result(
            _plan("tags", ["top-level"], ["new"])
        ).category == "allowed"
    case.detail["media"]["metadata"] = "invalid"
    assert capabilities.result(_plan("title", "Before", "After")).category == "field_unavailable"
    case.detail["media"]["metadata"] = {"title": "Before"}
    user["isActive"] = False
    assert capabilities.result(_plan("title", "Before", "After")).category == "connection_unavailable"

    unavailable = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadError("lost", request=request)))
        ),
        endpoint="https://abs.example.invalid", library_id="library-1",
    )
    assert unavailable.result(_plan("title", "Before", "After")).category == "connection_unavailable"


def test_streamed_reads_reject_encoded_or_oversized_responses(case: _Case) -> None:
    plan = _plan("title", "Before", "After")
    encoded = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"}, content=b"invalid")
        )),
        endpoint="https://abs.example.invalid", library_id="library-1", artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable, match="unexpected_effect_behavior"):
        encoded.read(plan)

    oversized = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "application/json"}, content=b"{" + b" " * 1024)
        )),
        endpoint="https://abs.example.invalid", library_id="library-1", artifacts=case.artifacts,
        maximum_response_bytes=1024,
    )
    with pytest.raises(TargetServiceUnavailable, match="field_unavailable"):
        oversized.read(plan)


@pytest.mark.parametrize(
    ("status", "disposition"),
    (
        (401, ("connection", "connection_unavailable")),
        (403, ("destination", "insufficient_permission")),
        (500, ("destination", "unexpected_effect_behavior")),
    ),
)
def test_target_read_failures_preserve_connection_permission_and_unexpected_scope(
    case: _Case, status: int, disposition: tuple[str, str]
) -> None:
    plan = _plan("title", "Before", "After")
    target = AudiobookshelfFieldEffectTarget(
        client_factory=cast("Callable[[], httpx.Client]", lambda status=status: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(status)
            )
        )),
        endpoint="https://abs.example.invalid", library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        target.read(plan)
    assert (raised.value.scope, raised.value.reason) == disposition
    transport = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                httpx.ReadError("lost", request=request)
            )
        )),
        endpoint="https://abs.example.invalid", library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        transport.read(plan)
    assert (raised.value.scope, raised.value.reason) == ("destination", "unexpected_effect_behavior")


@pytest.mark.parametrize("phase", ("preclaim", "verification"))
def test_oversized_cover_reads_suspend_the_destination_before_and_after_write(
    case: _Case, phase: str
) -> None:
    oversized = b"x" * (8 * 1024 * 1024 + 1)
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
            json=case.detail,
        )

    target = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(handle)
        ),
        endpoint="https://abs.example.invalid", library_id="library-1",
        artifacts=case.artifacts,
    )
    root = Path(case.directory.name) / ("cover-" + phase)
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "effects.sqlite")
    plan = _plan(
        "cover", None, case.cover,
        envelope_identity=repository.field_effect_consent_envelope_identity("books"),
    )
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
    assert result.state == ("stale" if phase == "preclaim" else "reconciling")
    assert repository.field_effect_capability_suspension(
            plan.connection, "cover"
        ) == "field_unavailable"
    repository.close()


@pytest.mark.parametrize("outcome", (401, 403, 500, "transport_loss"))
def test_post_dispatch_status_and_transport_loss_reconcile_through_application(
    case: _Case, outcome: object
) -> None:
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
        endpoint="https://abs.example.invalid", library_id="library-1", artifacts=case.artifacts,
    )
    root = Path(case.directory.name) / ("case-" + str(outcome))
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "effects.sqlite")
    plan = _plan(
        "title", "Before", "After",
        envelope_identity=repository.field_effect_consent_envelope_identity("books"),
    )
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
    current = type("Current", (), {"current": lambda self, value: True})()
    ticks = iter(range(10, 100))
    service = GuardedFieldApplication(
        repository, target, FakeBackupReadiness(), FakeCapabilityObserver("e" * 64),
        current, FakeDependencyCurrentness(), current,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
        FakeForwardReevaluation(), lambda: next(ticks),
    )
    assert service.apply(plan.plan_id).state == "verified"
    assert requests.count("PATCH") == 1
    assert requests.count("GET") >= 4
    assert repository.field_effect_capability_suspension(plan.connection, "title") == ("connection_unavailable" if outcome == 401 else
         "insufficient_permission" if outcome == 403 else
         "unexpected_effect_behavior")
    repository.close()
