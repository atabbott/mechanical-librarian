from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import httpx
import pytest
from scripts.qualify_audiobookshelf_field_effect import DESCRIPTION_LANDINGS

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
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.private_recovery import PrivateCoverRecoveryStore
from mechanical_librarian.application.field_effects import (
    GuardedFieldApplication,
    TargetServiceUnavailable,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectOperation,
    FieldEffectPlan,
    field_value_version,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    TargetDestination,
)

if TYPE_CHECKING:
    # Imported for the `cast` below only, which is a runtime no-op.
    from collections.abc import Callable

from tests.support.audiobookshelf_targets import SYNTHETIC_COVER_PNG as _PNG
from tests.support.audiobookshelf_targets import FieldEffectTargetCase as _Case
from tests.support.audiobookshelf_targets import target_snapshot


@pytest.fixture
def case() -> Iterator[_Case]:
    prepared = _Case()
    try:
        yield prepared
    finally:
        prepared.close()


def _plan(
    destination: str,
    pre_value: object,
    desired_value: object,
    target_version: str | None = None,
    envelope_identity: str = "a" * 64,
) -> FieldEffectPlan:
    authority = EffectAuthority(
        "human_decision_" + "a" * 64,
        "review_case_" + "b" * 64,
        "c" * 64,
        "fake_service",
        "human",
        "candidate",
        None,
        None,
    )
    consent = EffectConsent.create(
        connection="primary",
        library="books",
        destination=destination,
        envelope_identity=envelope_identity,
        granted_at=1,
    )
    return FieldEffectPlan.create(
        connection="primary",
        library="books",
        work_id="item-1",
        destination=destination,
        pre_value=pre_value,
        desired_value=desired_value,
        target_fingerprint="c" * 64,
        target_version=(
            target_version
            if target_version is not None
            else field_value_version(destination, pre_value)
        ),
        dependency_digests=("d" * 64,),
        authority=authority,
        consent=consent,
        capability_digest="e" * 64,
        created_at=1,
    )


def test_metadata_effect_is_destination_minimal_and_readable(case: _Case) -> None:
    plan = _plan("title", "Before", "After")
    before = case.target.read(plan)
    assert (before.value, before.version) == (
        "Before",
        field_value_version("title", "Before"),
    )
    assert case.target.apply(plan).outcome == "accepted"
    after = case.target.read(plan)
    assert (after.value, after.version) == (
        "After",
        field_value_version("title", "After"),
    )
    patch = next(request for request in case.requests if request.method == "PATCH")
    assert patch.headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize(
    "destination",
    (
        "subtitle",
        "series",
        "genres",
        "narrators",
        "tags",
        "description",
        "isbn",
        "asin",
        "explicit",
    ),
)
def test_rich_metadata_target_shapes_read_apply_and_read_back(
    case: _Case, destination: str
) -> None:
    metadata = case.detail["media"]["metadata"]
    metadata.update(
        {
            "subtitle": None,
            "series": [],
            "genres": [],
            "narrators": [],
            "description": None,
            "isbn": None,
            "asin": None,
            "explicit": None,
        }
    )
    case.detail["media"]["tags"] = []
    values = {
        "subtitle": "Synthetic subtitle",
        "series": [{"name": "Synthetic series", "sequence": "1"}],
        "genres": ["Synthetic genre"],
        "narrators": ["Synthetic narrator"],
        "tags": ["Synthetic tag"],
        "description": "Synthetic description.",
        "isbn": "9780306406157",
        "asin": "B000000001",
        "explicit": True,
    }
    desired = values[destination]
    plan = _plan(destination, None, desired)
    assert case.target.read(plan).value is None
    assert "accepted" == case.target.apply(plan).outcome
    assert desired == case.target.read(plan).value


def test_series_fill_preserves_null_sequence_through_supported_adapter(
    case: _Case,
) -> None:
    case.detail["media"]["metadata"]["series"] = []
    desired = [{"name": "Synthetic series", "sequence": None}]
    plan = _plan("series", None, desired)
    assert case.target.read(plan).value is None
    assert case.target.apply(plan).outcome == "accepted"
    assert case.target.read(plan).value == desired


def test_an_unsupported_narrator_shape_disables_only_that_destination(
    case: _Case,
) -> None:
    """The expansion boundary: a rejected shape costs one field, not the library.

    A narrator cast is a plain string list on both media. When the service
    answers with anything else the read refuses and the capability reads
    `field_unavailable`, while every other destination on the same item stays
    allowed (ADR-0071, #968).
    """
    user = {
        "isActive": True,
        "permissions": {"update": True, "upload": True, "accessAllLibraries": False},
        "librariesAccessible": ["library-1"],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=case.detail
        )

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    case.detail["media"]["metadata"]["narrators"] = [{"name": "Synthetic narrator"}]
    plan = _plan("narrators", None, ["Synthetic narrator"])

    with pytest.raises(TargetServiceUnavailable) as raised:
        case.target.read(plan)

    assert ("destination", "field_unavailable") == (
        raised.value.scope,
        raised.value.reason,
    )
    assert capabilities.result(plan).category == "field_unavailable"
    assert capabilities.result(_plan("title", "Before", "After")).category == "allowed"


@pytest.mark.parametrize("answer", ("absent", "null"))
def test_an_item_with_no_narrator_cast_reads_as_no_value(
    case: _Case, answer: str
) -> None:
    """An unnarrated item is an empty field, not an unsupported one.

    Audiobookshelf omits `metadata.narrators` on some responses and answers
    `null` on others, and capability discovery reads both as allowed. The
    pre-read has to agree, or the very case the fifteenth destination exists
    for -- filling an empty cast -- refuses before it plans (#968).
    """
    metadata = case.detail["media"]["metadata"]
    if answer == "absent":
        metadata.pop("narrators", None)
    else:
        metadata["narrators"] = None
    user = {
        "isActive": True,
        "permissions": {"update": True, "upload": True, "accessAllLibraries": False},
        "librariesAccessible": ["library-1"],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=case.detail
        )

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    desired = ["Synthetic narrator"]
    plan = _plan("narrators", None, desired)

    assert case.target.read(plan).value is None
    assert capabilities.result(plan).category == "allowed"
    assert "accepted" == case.target.apply(plan).outcome
    assert desired == case.target.read(plan).value


def test_cover_reads_digest_and_uploads_only_verified_original(case: _Case) -> None:
    plan = _plan("cover", None, case.cover)
    assert case.target.read(plan).value is None
    case.events.clear()
    original = case.artifacts.read_original_digest
    with patch.object(
        case.artifacts,
        "read_original_digest",
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
    assert case.target.read(_plan("tags", ["top-level"], ["new"])).value == [
        "top-level"
    ]


@pytest.mark.parametrize(
    "destination", ("authors", "genres", "narrators", "series", "tags")
)
def test_empty_collections_and_service_series_shape_are_normalized(
    case: _Case, destination: str
) -> None:
    metadata = case.detail["media"]["metadata"]
    metadata["authors"] = []
    metadata["genres"] = []
    metadata["narrators"] = []
    metadata["series"] = []
    case.detail["media"]["tags"] = []
    desired = {
        "authors": ["New"],
        "genres": ["New"],
        "narrators": ["New"],
        "series": [{"name": "New", "sequence": None}],
        "tags": ["New"],
    }
    assert (
        case.target.read(_plan(destination, None, desired[destination])).value is None
    )
    metadata["series"] = [{"id": "service-id", "name": "Old", "sequence": 2.5}]
    assert case.target.read(_plan("series", None, desired["series"])).value == [
        {"name": "Old", "sequence": "2.5"}
    ]


def test_immediate_prewrite_destination_or_version_drift_refuses_patch(
    case: _Case,
) -> None:
    plan = _plan("title", "Before", "After", target_version="6")
    assert case.target.apply(plan).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)
    assert not any(request.method == "POST" for request in case.requests)


def test_unrelated_item_revision_does_not_block_the_field_local_effect(
    case: _Case,
) -> None:
    plan = _plan("title", "Before", "After")
    case.detail["updatedAt"] = 99
    case.detail["media"]["metadata"]["genres"] = ["externally changed"]
    assert case.target.apply(plan).outcome == "accepted"


def test_success_without_body_is_accepted_and_write_refusals_are_ambiguous(
    case: _Case,
) -> None:
    case.write_status = 204
    assert case.target.apply(_plan("title", "Before", "After")).outcome == "accepted"
    case.detail["media"]["metadata"]["title"] = "Before"
    case.detail["updatedAt"] = 7
    case.write_status = 403
    refused = case.target.apply(_plan("title", "Before", "After"))
    assert (refused.outcome, refused.suspension_scope, refused.suspension_reason) == (
        "ambiguous",
        "destination",
        "insufficient_permission",
    )
    case.detail["media"]["metadata"]["title"] = "Before"
    case.detail["updatedAt"] = 7
    case.write_status = 500
    failed = case.target.apply(_plan("title", "Before", "After"))
    assert (failed.outcome, failed.suspension_scope, failed.suspension_reason) == (
        "ambiguous",
        "destination",
        "unexpected_effect_behavior",
    )

    case.requests.clear()
    changed = _plan("title", "Elsewhere", "After")
    assert case.target.apply(changed).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)


def test_capability_constructor_rejects_unbounded_or_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        AudiobookshelfFieldEffectCapabilities(
            client_factory=lambda: httpx.Client(),
            endpoint="invalid",
            library_id="",
            maximum_response_bytes=1,
        )


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
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    plan = _plan("title", "Before", "After")
    assert capabilities.result(plan).category == "connection_unavailable"
    target = AudiobookshelfFieldEffectTarget(
        client_factory=unavailable_client,
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        target.read(plan)
    assert (raised.value.scope, raised.value.reason) == (
        "connection",
        "connection_unavailable",
    )

    nested = b'{"value":' + b"[" * 101 + b"0" + b"]" * 101 + b"}"

    def deep_client() -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "application/json"}, content=nested
                )
            )
        )

    deep_capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=deep_client,
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    assert deep_capabilities.result(plan).category == "connection_unavailable"
    deep_target = AudiobookshelfFieldEffectTarget(
        client_factory=deep_client,
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        deep_target.read(plan)
    assert (raised.value.scope, raised.value.reason) == (
        "destination",
        "field_unavailable",
    )


def test_capability_is_field_local_and_malformed_or_unavailable_state_fails_closed(
    case: _Case,
) -> None:
    user = {
        "isActive": True,
        "permissions": {"update": True, "upload": False, "accessAllLibraries": False},
        "librariesAccessible": ["library-1"],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=case.detail
        )

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
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
    assert (
        capabilities.result(_plan("title", "Before", "After")).category
        == "connection_unavailable"
    )
    user["permissions"]["accessAllLibraries"] = True
    assert capabilities.result(_plan("title", "Before", "After")).category == "allowed"
    case.detail["media"]["tags"] = ["top-level"]
    case.detail["media"].pop("metadata")
    assert (
        capabilities.result(_plan("tags", ["top-level"], ["new"])).category == "allowed"
    )
    case.detail["media"]["metadata"] = "invalid"
    assert (
        capabilities.result(_plan("title", "Before", "After")).category
        == "field_unavailable"
    )
    case.detail["media"]["metadata"] = {"title": "Before"}
    user["isActive"] = False
    assert (
        capabilities.result(_plan("title", "Before", "After")).category
        == "connection_unavailable"
    )

    unavailable = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ReadError("lost", request=request)
                )
            )
        ),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    assert (
        unavailable.result(_plan("title", "Before", "After")).category
        == "connection_unavailable"
    )


def test_streamed_reads_reject_encoded_or_oversized_responses(case: _Case) -> None:
    plan = _plan("title", "Before", "After")
    encoded = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json",
                        "content-encoding": "gzip",
                    },
                    content=b"invalid",
                )
            )
        ),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable, match="unexpected_effect_behavior"):
        encoded.read(plan)

    oversized = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=b"{" + b" " * 1024,
                )
            )
        ),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
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
        client_factory=cast(
            "Callable[[], httpx.Client]",
            lambda status=status: httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(status))
            ),
        ),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        target.read(plan)
    assert (raised.value.scope, raised.value.reason) == disposition
    transport = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(
                    httpx.ReadError("lost", request=request)
                )
            )
        ),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    with pytest.raises(TargetServiceUnavailable) as raised:
        transport.read(plan)
    # A lost connection is clearable connection unavailability, not a protocol
    # violation that would hold the destination until an owner repairs it.
    assert (raised.value.scope, raised.value.reason) == (
        "connection",
        "connection_unavailable",
    )


@pytest.mark.parametrize(
    ("failure", "disposition"),
    (
        (httpx.WriteTimeout, ("connection", "connection_unavailable")),
        (httpx.ConnectError, ("connection", "connection_unavailable")),
        (httpx.DecodingError, ("destination", "unexpected_effect_behavior")),
        (httpx.TooManyRedirects, ("destination", "unexpected_effect_behavior")),
    ),
)
def test_only_transport_loss_is_connection_unavailability_on_every_target_request(
    case: _Case,
    failure: type[httpx.RequestError],
    disposition: tuple[str, str],
) -> None:
    """Each request a guarded write makes maps a lost connection alike (#1382)."""
    detail = {
        "id": "item-1",
        "libraryId": "library-1",
        "mediaType": "book",
        "updatedAt": 7,
        "media": {"metadata": {"title": "Before"}, "tags": []},
    }
    failing: set[tuple[str, str]] = set()

    def handle(request: httpx.Request) -> httpx.Response:
        if (request.method, request.url.path) in failing:
            raise failure("synthetic failure", request=request)
        if request.url.path.endswith("/cover"):
            return httpx.Response(404)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=detail
        )

    target = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    failing.add(("PATCH", "/api/items/item-1/media"))
    written = target.apply(_plan("title", "Before", "After"))
    assert (written.outcome, written.suspension_scope, written.suspension_reason) == (
        "ambiguous",
        *disposition,
    )
    for request, plan in (
        (("GET", "/api/items/item-1"), _plan("title", "Before", "After")),
        (("GET", "/api/items/item-1/cover"), _plan("cover", None, case.cover)),
    ):
        failing.clear()
        failing.add(request)
        with pytest.raises(TargetServiceUnavailable) as raised:
            target.read(plan)
        assert (raised.value.scope, raised.value.reason) == disposition


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
                    200,
                    headers={"content-type": "image/jpeg"},
                    content=oversized,
                )
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=case.detail,
        )

    target = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    root = Path(case.directory.name) / ("cover-" + phase)
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "effects.sqlite")
    plan = _plan(
        "cover",
        None,
        case.cover,
        envelope_identity=repository.field_effect_consent_envelope_identity("books"),
    )
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
    current = type("Current", (), {"current": lambda self, value: True})()
    ticks = iter(range(100, 200))
    service = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver("e" * 64),
        current,
        FakeDependencyCurrentness(),
        current,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
        FakeForwardReevaluation(),
        lambda: next(ticks),
    )
    result = service.apply(plan.plan_id)
    assert result.state == ("stale" if phase == "preclaim" else "reconciling")
    assert (
        repository.field_effect_capability_suspension(plan.connection, "cover")
        == "field_unavailable"
    )
    repository.close()


@pytest.mark.parametrize("outcome", (401, 403, 500, "transport_loss"))
def test_post_dispatch_status_and_transport_loss_reconcile_through_application(
    case: _Case, outcome: object
) -> None:
    detail = {
        "id": "item-1",
        "libraryId": "library-1",
        "mediaType": "book",
        "updatedAt": 7,
        "media": {"metadata": {"title": "Before"}, "tags": []},
    }
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=detail
            )
        detail["media"]["metadata"]["title"] = "After"
        detail["updatedAt"] = 8
        if outcome == "transport_loss":
            raise httpx.ReadError("response lost", request=request)
        return httpx.Response(outcome)

    target = AudiobookshelfFieldEffectTarget(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
        artifacts=case.artifacts,
    )
    root = Path(case.directory.name) / ("case-" + str(outcome))
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "effects.sqlite")
    plan = _plan(
        "title",
        "Before",
        "After",
        envelope_identity=repository.field_effect_consent_envelope_identity("books"),
    )
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
    current = type("Current", (), {"current": lambda self, value: True})()
    ticks = iter(range(10, 100))
    service = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver("e" * 64),
        current,
        FakeDependencyCurrentness(),
        current,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
        FakeForwardReevaluation(),
        lambda: next(ticks),
    )
    assert service.apply(plan.plan_id).state == "verified"
    assert requests.count("PATCH") == 1
    assert requests.count("GET") >= 4
    assert repository.field_effect_capability_suspension(plan.connection, "title") == (
        "connection_unavailable"
        if outcome in {401, "transport_loss"}
        else "insufficient_permission"
        if outcome == 403
        else "unexpected_effect_behavior"
    )
    repository.close()


@pytest.mark.parametrize(
    "value,readable", (("2025-02-03", True), ("2025", True), ("2025-02", True))
)
def test_publication_date_transport_and_report_reader_define_the_supported_shape(
    case, value, readable
):
    from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
        _target_field,
    )
    from mechanical_librarian.domain.metadata_report import (
        TargetDestination,
        TargetFieldState,
    )

    plan = _plan("publishedDate", None, value)
    assert case.target.read(plan).value is None
    assert case.target.apply(plan).outcome == "accepted"
    assert case.target.read(plan).value == value
    request = next(item for item in case.requests if item.method == "PATCH")
    assert json.loads(request.content) == {"metadata": {"publishedDate": value}}
    observed = _target_field(
        case.detail["media"]["metadata"], TargetDestination.PUBLISHED_DATE
    )
    assert observed.state is (
        TargetFieldState.VALUE if readable else TargetFieldState.MALFORMED
    )
    assert observed.value == (value if readable else None)


@pytest.mark.parametrize("current", ("First\n\nSecond", " \tFirst\r\n\r\nSecond\t "))
def test_multiline_description_effect_keeps_exact_currentness_and_readback(
    case: _Case, current: str
) -> None:
    case.detail["media"]["metadata"]["description"] = current
    desired = "Selected description."
    plan = _plan(
        "description", current, desired, field_value_version("description", current)
    )
    observed = case.target.read(plan)
    assert observed.value == current
    assert observed.version == field_value_version("description", current)
    assert case.target.apply(plan).outcome == "accepted"
    assert case.target.read(plan).value == desired
    patches = [request for request in case.requests if request.method == "PATCH"]
    assert len(patches) == 1
    assert json.loads(patches[0].content) == {"metadata": {"description": desired}}


def test_description_formatting_drift_is_not_an_effect_precondition_match(
    case: _Case,
) -> None:
    current = "First\r\nSecond"
    case.detail["media"]["metadata"]["description"] = current.replace("\r\n", "\n")
    plan = _plan(
        "description",
        current,
        "Selected description.",
        field_value_version("description", current),
    )
    observed = case.target.read(plan)
    assert observed.version != plan.target_version
    assert observed.value != plan.pre_value
    assert case.target.apply(plan).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)


def test_qualification_pre_plan_reads_reach_the_real_target(case: _Case) -> None:
    # The canary waits for a stable cover through the adapter's own
    # observation, which also asks the plan for a subtitle companion (#1183).
    from scripts.qualify_audiobookshelf_field_effect import _stable_cover_observation

    _stable_cover_observation(case.target, "item-1")
    assert sum(request.method == "GET" for request in case.requests) >= 2


@pytest.mark.parametrize(
    ("answer", "category", "reached"),
    (
        ("transport", "connection_unavailable", False),
        ("key_rejected", "connection_unavailable", False),
        ("item_transport", "connection_unavailable", False),
        ("inactive", "connection_unavailable", False),
        ("unselected", "connection_unavailable", False),
        ("item_500", "connection_unavailable", False),
        ("item_503", "connection_unavailable", False),
        ("item_429", "connection_unavailable", False),
        ("item_408", "connection_unavailable", False),
        ("item_malformed", "connection_unavailable", False),
        ("item_gone", "connection_unavailable", True),
        ("item_403", "connection_unavailable", True),
        ("allowed", "allowed", True),
    ),
)
def test_only_an_answered_identity_check_reaches_the_connection(
    case: _Case, answer: str, category: str, reached: bool
) -> None:
    """A refused item is an answer; a lost transport on either request, a
    refused key, an account that cannot write this library, a server error,
    a throttle, a timeout answer, or an unreadable body is not.

    Category and digest stay what they were: the fact rides beside the
    capability evidence and never changes it (#1402).
    """
    user = {
        "isActive": answer != "inactive",
        "permissions": {
            "update": True,
            "upload": True,
            "accessAllLibraries": answer != "unselected",
        },
        "librariesAccessible": [],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if answer == "transport" or (
            answer == "item_transport" and request.url.path != "/api/me"
        ):
            raise httpx.ConnectError("synthetic outage", request=request)
        if request.url.path == "/api/me":
            if answer == "key_rejected":
                return httpx.Response(401)
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        if answer == "item_gone":
            return httpx.Response(404)
        if answer.removeprefix("item_").isdigit():
            return httpx.Response(int(answer.removeprefix("item_")))
        if answer == "item_malformed":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"{"
            )
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json=case.detail
        )

    capabilities = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )
    observed = capabilities.result(_plan("title", "Before", "After"))
    assert (observed.category, observed.connection_reached) == (category, reached)
    if answer != "allowed":
        # Every unanswered or refused observation keeps the digest it had.
        update = answer in {"inactive", "unselected"}
        assert (
            observed.digest
            == capabilities._result(
                "title", "connection_unavailable", update, update
            ).digest
        )


# Each value sent, and the form every pinned Audiobookshelf stores it in (#1424).
_STORED = {sent: stored for _, sent, stored, _ in DESCRIPTION_LANDINGS}


def _guarded(case: _Case, name: str):
    root = Path(case.directory.name) / name
    root.mkdir()
    os.chmod(root, 0o700)
    repository = open_repository(root / "effects.sqlite")
    current = type("Current", (), {"current": lambda self, value: True})()
    ticks = iter(range(10, 100))
    service = GuardedFieldApplication(
        repository,
        case.target,
        FakeBackupReadiness(),
        FakeCapabilityObserver("e" * 64),
        current,
        FakeDependencyCurrentness(),
        current,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b""),
        FakeForwardReevaluation(),
        lambda: next(ticks),
    )
    return service, repository


def _description_plan(case: _Case, repository, desired: str) -> FieldEffectPlan:
    case.detail["media"]["metadata"]["description"] = "Earlier description."
    plan = _plan(
        "description",
        "Earlier description.",
        desired,
        envelope_identity=repository.field_effect_consent_envelope_identity("books"),
    )
    repository.record_field_effect(FieldEffectOperation(plan, updated_at=1))
    return plan


@pytest.mark.parametrize("readback", ("verification", "reconciliation"))
@pytest.mark.parametrize(
    ("name", "sent", "stored", "outcome"),
    DESCRIPTION_LANDINGS,
    ids=[row[0] for row in DESCRIPTION_LANDINGS],
)
def test_a_description_resolves_as_the_service_stored_it(
    case: _Case, name: str, sent: str, stored: str, outcome: str, readback: str
) -> None:
    def keep(value: str) -> str:
        # A lost read-back sends the landing through reconciliation instead.
        case.fail_read_once = readback == "reconciliation"
        return _STORED[value]

    case.stored_description = keep
    service, repository = _guarded(case, name + "-" + readback)
    plan = _description_plan(case, repository, sent)
    applied = service.apply(plan.plan_id)
    if outcome == "verified" and readback == "verification":
        # The landing is confirmed by its own read-back, not by reconciling it.
        assert applied.state == "verified"
    else:
        assert applied.state == "reconciling"
        applied = service.reconcile(plan.plan_id)
    assert case.detail["media"]["metadata"]["description"] == stored
    assert (applied.state, applied.failure) == (
        ("verified", None) if outcome == "verified" else ("stale", "third_value")
    )
    assert [request.method for request in case.requests].count("PATCH") == 1
    repository.close()


def test_another_description_the_service_holds_is_still_a_third_value(
    case: _Case,
) -> None:
    case.stored_description = lambda _value: "Fish &amp; chips, and peas."
    service, repository = _guarded(case, "drift")
    plan = _description_plan(case, repository, "Fish & chips.")
    assert service.apply(plan.plan_id).state == "reconciling"
    result = service.reconcile(plan.plan_id)
    assert (result.state, result.failure) == ("stale", "third_value")
    repository.close()


@pytest.mark.parametrize(
    ("name", "sent"),
    [
        (name, sent)
        for name, sent, stored, outcome in DESCRIPTION_LANDINGS
        if outcome == "verified" and stored != sent
    ],
)
def test_an_escaped_landing_whose_confirmation_was_lost_is_verified_unsent(
    case: _Case, name: str, sent: str
) -> None:
    def keep(value: str) -> str:
        case.fail_read_once = True
        return _STORED[value]

    case.stored_description = keep
    service, repository = _guarded(case, name)
    plan = _description_plan(case, repository, sent)
    assert service.apply(plan.plan_id).state == "reconciling"
    case.fail_read_once = True
    unknown = service.reconcile(plan.plan_id)
    assert (unknown.state, unknown.failure) == ("retryable", "target_failure")
    # The next pre-read shows the stored form of the desired value, so the
    # retry records the landing instead of a change it did not make (#1402).
    verified = service.apply(plan.plan_id)
    assert (verified.state, verified.failure) == ("verified", None)
    assert [request.method for request in case.requests].count("PATCH") == 1
    repository.close()


def test_an_escaped_incumbent_pre_read_matches_its_admitted_text_exactly(
    case: _Case,
) -> None:
    stored = "Fish &amp; chips for the qualification."
    admitted = next(
        field
        for field in target_snapshot(
            {"title": "Current title", "description": stored}
        ).fields
        if field.destination is TargetDestination.DESCRIPTION
    ).value
    assert admitted == "Fish & chips for the qualification."
    case.detail["media"]["metadata"]["description"] = stored
    plan = _plan("description", admitted, "Selected description.")
    assert plan.target_version == field_value_version("description", admitted)
    assert case.target.apply(plan).outcome == "accepted"
    # Another actor's edit between the catalogue read and the claim is still a
    # conflict once both sides are read as text: no request is sent.
    case.requests.clear()
    case.detail["media"]["metadata"]["description"] = stored.replace(".", "!")
    assert case.target.apply(plan).outcome == "conflict"
    assert not any(request.method == "PATCH" for request in case.requests)


@pytest.mark.parametrize(
    ("stored", "read"),
    (
        ("<b>" + "x" * (MAX_DESCRIPTION_CHARACTERS - 7) + "</b>", None),
        ("<b>" + "x" * (MAX_DESCRIPTION_CHARACTERS - 6) + "</b>", "stored"),
        ("<p>Unclosed", "stored"),
    ),
    ids=("bounded", "oversized", "refused"),
)
def test_a_description_read_is_bounded_before_it_is_rendered(
    case: _Case, stored: str, read: str | None
) -> None:
    # A bounded value reads as its text; one the catalogue would call
    # oversized or malformed stays as stored, so it can match no plan.
    case.detail["media"]["metadata"]["description"] = stored
    observed = case.target.read(_plan("description", None, "Selected."))
    assert observed.value == (
        stored if read == "stored" else "x" * (MAX_DESCRIPTION_CHARACTERS - 7)
    )
    assert observed.version == field_value_version("description", observed.value)
