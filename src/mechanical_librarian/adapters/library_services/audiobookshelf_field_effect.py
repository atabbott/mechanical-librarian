"""Supported, one-field Audiobookshelf target effect adapter.

Audiobookshelf has no conditional update API.  The application therefore owns
the immediately-before-write pre-read and post-write verification; this adapter
performs exactly one destination-minimal request and never retries it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import quote

import httpx

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    TargetEffectResult,
    TargetObservation,
    TargetServiceUnavailable,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.field_effect import FieldEffectPlan


AudiobookshelfCapabilityResult = CapabilityObservation


class AudiobookshelfFieldEffectCapabilities:
    """Read the API-key's real field-local write capability, never a version."""
    def __init__(self, *, client_factory: Callable[[], httpx.Client], endpoint: str,
                 library_id: str, maximum_response_bytes: int = 1_048_576) -> None:
        if (not callable(client_factory) or type(endpoint) is not str or not endpoint.startswith(("http://", "https://"))
                or type(library_id) is not str or not library_id or type(maximum_response_bytes) is not int
                or not 1024 <= maximum_response_bytes <= 4_194_304):
            raise ValueError("Audiobookshelf field-effect capability is invalid")
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._maximum_response_bytes = maximum_response_bytes

    def observe(self, plan: FieldEffectPlan) -> CapabilityObservation:
        return self.result(plan)

    def result(self, plan: FieldEffectPlan) -> AudiobookshelfCapabilityResult:
        with self._client_factory() as client:
            try:
                me = _json_response(client, "GET", self._endpoint + "/api/me", None, self._maximum_response_bytes)
            except (httpx.HTTPError, OSError, ValueError):
                return self._result(plan.destination, "connection_unavailable", False, False)
            try:
                detail = _json_response(client, "GET", self._endpoint + "/api/items/" + quote(plan.work_id, safe=""), {"expanded": "1"}, self._maximum_response_bytes)
            except (httpx.HTTPError, OSError, ValueError):
                return self._result(plan.destination, "connection_unavailable", False, False)
        user = me.get("user", me) if type(me) is dict else None
        permissions = user.get("permissions") if type(user) is dict else None
        accessible = user.get("librariesAccessible") if type(user) is dict else None
        selected = (type(permissions) is dict and permissions.get("accessAllLibraries") is True) or (type(accessible) is list and all(type(value) is str for value in accessible) and self._library_id in accessible)
        item_matches = type(detail) is dict and detail.get("id") == plan.work_id and detail.get("libraryId") == self._library_id and detail.get("mediaType") == "book"
        update = type(permissions) is dict and permissions.get("update") is True
        upload = type(permissions) is dict and permissions.get("upload") is True
        active = type(user) is dict and user.get("isActive") is True
        if not active or not selected or not item_matches:
            return self._result(plan.destination, "connection_unavailable", update, upload)
        if not update or (plan.destination == "cover" and not upload):
            return self._result(plan.destination, "insufficient_permission", update, upload, True)
        try:
            _capability_value_shape(detail, plan.destination)
        except (httpx.HTTPError, OSError, ValueError):
            return self._result(plan.destination, "field_unavailable", update, upload, True)
        return self._result(plan.destination, "allowed", update, upload, True)

    def _result(self, destination: str, category: Literal["allowed", "insufficient_permission", "connection_unavailable", "field_unavailable"], update: bool, upload: bool, selected: bool = False) -> CapabilityObservation:
        capability = {
            "destination": destination,
            "category": category,
            "update": update,
            "selected": selected,
        }
        if destination == "cover":
            capability["upload"] = upload
        payload = json.dumps(capability, sort_keys=True, separators=(",", ":")).encode()
        return CapabilityObservation(category, hashlib.sha256(payload).hexdigest())


class AudiobookshelfFieldEffectTarget:
    """One API-key-authenticated target for a single configured ABS library."""

    def __init__(self, *, client_factory: Callable[[], httpx.Client], endpoint: str,
                 library_id: str, artifacts: CoverArtifactStore,
                 maximum_response_bytes: int = 1_048_576) -> None:
        if (not callable(client_factory) or type(endpoint) is not str
                or not endpoint.startswith(("http://", "https://"))
                or type(library_id) is not str or not library_id
                or type(artifacts) is not CoverArtifactStore
                or type(maximum_response_bytes) is not int
                or not 1024 <= maximum_response_bytes <= 4_194_304):
            raise ValueError("Audiobookshelf field-effect target is invalid")
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._artifacts = artifacts
        self._maximum_response_bytes = maximum_response_bytes

    def read(self, plan: FieldEffectPlan) -> TargetObservation:
        with self._client_factory() as client:
            detail = self._item(client, plan.work_id)
            value = self._value(client, detail, plan)
            # ABS cover retrieval is separate from the item response.  Bind a
            # cover observation to the item version after that authoritative
            # cover read, not the version that preceded it.
            if plan.destination == "cover":
                detail = self._item(client, plan.work_id)
        return TargetObservation(value, _version(detail))

    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult:
        """Check the captured destination then issue one non-retriable request.

        Audiobookshelf cannot make this comparison atomic with the mutation.
        This is deliberately the last supported validation point: a target-side
        actor can still change this one destination after this read.
        """
        try:
            cover_body: bytes | None = None
            media_payload: dict[str, object] | None = None
            if plan.destination == "cover":
                if not isinstance(plan.desired_value, str):
                    return TargetEffectResult("conflict")
                digest = plan.desired_value.removeprefix("cover_")
                if len(digest) != 64:
                    return TargetEffectResult("conflict")
                try:
                    cover_body = self._artifacts.read_original_digest(digest)
                except MetadataReportError:
                    return TargetEffectResult("conflict")
                if hashlib.sha256(cover_body).hexdigest() != digest:
                    return TargetEffectResult("conflict")
            else:
                media_payload = _media_payload(plan.destination, plan.desired_value)
            with self._client_factory() as client:
                detail = self._item(client, plan.work_id)
                current = self._value(client, detail, plan)
                if plan.destination == "cover":
                    detail = self._item(client, plan.work_id)
                if current != plan.pre_value or _version(detail) != plan.target_version:
                    return TargetEffectResult("conflict")
                if plan.destination == "cover":
                    assert cover_body is not None
                    status = _write_status(client, "POST",
                        f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover",
                        files={"cover": ("cover" + _image_suffix(cover_body), cover_body, _image_media_type(cover_body))},
                    )
                else:
                    assert media_payload is not None
                    status = _write_status(client, "PATCH",
                        f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/media",
                        json=media_payload,
                    )
        except (httpx.HTTPError, OSError, ValueError):
            return TargetEffectResult("ambiguous", "destination")
        if status in {200, 201, 204}:
            return TargetEffectResult("accepted")
        scope = "connection" if status in {401, 403} else "destination"
        return TargetEffectResult("ambiguous", scope)

    def _item(self, client: httpx.Client, work_id: str) -> dict[str, Any]:
        try:
            detail = _json_response(client, "GET", f"{self._endpoint}/api/items/{quote(work_id, safe='')}", {"expanded": "1"}, self._maximum_response_bytes)
        except (httpx.HTTPError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise TargetServiceUnavailable("connection") from error
        if (type(detail) is not dict or detail.get("id") != work_id
                or detail.get("libraryId") != self._library_id
                or detail.get("mediaType") != "book" or type(detail.get("media")) is not dict):
            raise TargetServiceUnavailable("connection")
        try:
            _version(detail)
        except ValueError as error:
            raise TargetServiceUnavailable("connection") from error
        return detail

    def _value(self, client: httpx.Client, detail: dict[str, Any], plan: FieldEffectPlan) -> object:
        if plan.destination == "cover":
            status, headers, body = _bytes_response(client, f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover", {"raw": "1"}, 8 * 1024 * 1024)
            if status == 404:
                return None
            if status != 200:
                raise TargetServiceUnavailable(
                    "connection" if status in {401, 403} else "destination"
                )
            media_type = headers.get("content-type", "").split(";", 1)[0].lower()
            try:
                observed_media_type = _image_media_type(body)
            except ValueError as error:
                raise TargetServiceUnavailable("destination") from error
            if media_type not in {"image/jpeg", "image/png", "image/webp"} or observed_media_type != media_type:
                raise TargetServiceUnavailable("destination")
            return "cover_" + hashlib.sha256(body).hexdigest()
        metadata = detail["media"].get("metadata")
        if type(metadata) is not dict:
            raise TargetServiceUnavailable("destination")
        if plan.destination == "tags":
            value = detail["media"].get("tags")
            if value is None:
                value = metadata.get("tags")
        else:
            value = metadata.get(plan.destination)
        if plan.destination == "authors":
            if not isinstance(value, list) or any(type(item) is not dict or type(item.get("name")) is not str for item in value):
                raise TargetServiceUnavailable("destination")
            return [item["name"] for item in value]
        if plan.destination in {"genres", "tags"}:
            if type(value) is not list or any(type(item) is not str for item in value):
                raise TargetServiceUnavailable("destination")
        return value


def _bytes_response(client: httpx.Client, url: str, params: dict[str, str], maximum: int) -> tuple[int, dict[str, str], bytes]:
    """Read one identity-coded response without allocating beyond its bound."""
    with client.stream("GET", url, params=params, headers={"Accept-Encoding": "identity"}) as response:
        encoding = response.headers.get("content-encoding", "").lower()
        if encoding not in {"", "identity"}:
            raise ValueError("Audiobookshelf response encoding invalid")
        body = bytearray()
        for chunk in response.iter_bytes():
            if len(body) + len(chunk) > maximum:
                raise ValueError("Audiobookshelf response too large")
            body.extend(chunk)
        return response.status_code, dict(response.headers), bytes(body)


def _json_response(client: httpx.Client, method: str, url: str, params: dict[str, str] | None, maximum: int) -> dict[str, Any]:
    if method != "GET":
        raise ValueError("Audiobookshelf response invalid")
    status, headers, body = _bytes_response(client, url, params or {}, maximum)
    if status != 200 or headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise ValueError("Audiobookshelf response invalid")
    value = json.loads(body.decode("utf-8", "strict"))
    if type(value) is not dict:
        raise ValueError("Audiobookshelf response invalid")
    return value


def _write_status(client: httpx.Client, method: str, url: str, **kwargs: Any) -> int:
    """Issue exactly one write and deliberately discard its response body."""
    with client.stream(method, url, headers={"Accept-Encoding": "identity"}, **kwargs) as response:
        return response.status_code


def _version(detail: dict[str, Any]) -> str:
    value = detail.get("updatedAt")
    if type(value) not in {int, float, str} or isinstance(value, bool) or not str(value):
        raise ValueError("Audiobookshelf version invalid")
    return str(value)


def _media_payload(destination: str, value: object) -> dict[str, object]:
    if destination == "tags":
        return {"tags": value}
    if destination == "authors":
        return {"metadata": {"authors": [{"name": item} for item in value]}}
    return {"metadata": {destination: value}}


def _capability_value_shape(detail: dict[str, Any], destination: str) -> None:
    media = detail.get("media")
    if type(media) is not dict or type(media.get("metadata")) is not dict:
        raise ValueError("Audiobookshelf metadata capability invalid")
    if destination == "tags":
        value = media.get("tags", media["metadata"].get("tags"))
        if type(value) is not list or any(type(item) is not str for item in value):
            raise ValueError("Audiobookshelf tag capability invalid")


def _image_media_type(body: bytes) -> str:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("cover artifact media type is invalid")


def _image_suffix(body: bytes) -> str:
    return {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[_image_media_type(body)]
