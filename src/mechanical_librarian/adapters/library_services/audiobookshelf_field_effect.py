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

from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
    visible_stored_description,
)
from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    CoverArtifactReader,
    TargetEffectResult,
    TargetObservation,
    TargetServiceUnavailable,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.field_effect import FieldEffectPlan, field_value_version


AudiobookshelfCapabilityResult = CapabilityObservation


class _ResponseStatusError(ValueError):
    def __init__(self, status: int) -> None:
        super().__init__(str(status))
        self.status = status


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
        try:
            client = self._client_factory()
        except Exception:
            return self._result(plan.destination, "connection_unavailable", False, False)
        with client:
            try:
                me = _json_response(client, "GET", self._endpoint + "/api/me", None, self._maximum_response_bytes)
            except (httpx.HTTPError, OSError, ValueError):
                return self._result(plan.destination, "connection_unavailable", False, False)
            user = me.get("user", me) if type(me) is dict else None
            permissions = user.get("permissions") if type(user) is dict else None
            accessible = user.get("librariesAccessible") if type(user) is dict else None
            selected = (type(permissions) is dict and permissions.get("accessAllLibraries") is True) or (type(accessible) is list and all(type(value) is str for value in accessible) and self._library_id in accessible)
            active = type(user) is dict and user.get("isActive") is True
            # The connection is reached only when a usable account's identity
            # check was answered and the item request returned a valid body or
            # a definite refusal: a lost transport, a rejected key, an unusable
            # account, a server error, a throttle, a timeout answer or a body
            # that cannot be read waits for the service to recover (#1402).
            usable = active and selected
            try:
                detail = _json_response(client, "GET", self._endpoint + "/api/items/" + quote(plan.work_id, safe=""), {"expanded": "1"}, self._maximum_response_bytes)
            except _ResponseStatusError as error:
                refused = 400 <= error.status < 500 and error.status not in {408, 429}
                return self._result(plan.destination, "connection_unavailable", False, False, reached=usable and refused)
            except (httpx.HTTPError, OSError, ValueError):
                return self._result(plan.destination, "connection_unavailable", False, False)
        item_matches = type(detail) is dict and detail.get("id") == plan.work_id and detail.get("libraryId") == self._library_id and detail.get("mediaType") == "book"
        update = type(permissions) is dict and permissions.get("update") is True
        upload = type(permissions) is dict and permissions.get("upload") is True
        if not active or not selected or not item_matches:
            return self._result(plan.destination, "connection_unavailable", update, upload, reached=usable)
        if not update or (plan.destination == "cover" and not upload):
            return self._result(plan.destination, "insufficient_permission", update, upload, True, reached=True)
        try:
            _capability_value_shape(detail, plan.destination)
        except (httpx.HTTPError, OSError, ValueError):
            return self._result(plan.destination, "field_unavailable", update, upload, True, reached=True)
        return self._result(plan.destination, "allowed", update, upload, True, reached=True)

    def _result(self, destination: str, category: Literal["allowed", "insufficient_permission", "connection_unavailable", "field_unavailable"], update: bool, upload: bool, selected: bool = False, *, reached: bool = False) -> CapabilityObservation:
        capability = {
            "destination": destination,
            "category": category,
            "update": update,
            "selected": selected,
        }
        if destination == "cover":
            capability["upload"] = upload
        payload = json.dumps(capability, sort_keys=True, separators=(",", ":")).encode()
        # `reached` is not capability evidence and never enters the digest (#1402).
        return CapabilityObservation(category, hashlib.sha256(payload).hexdigest(), reached)


class AudiobookshelfFieldEffectTarget:
    """One API-key-authenticated target for a single configured ABS library."""

    def __init__(self, *, client_factory: Callable[[], httpx.Client], endpoint: str,
                 library_id: str, artifacts: CoverArtifactReader,
                 maximum_response_bytes: int = 1_048_576) -> None:
        if (not callable(client_factory) or type(endpoint) is not str
                or not endpoint.startswith(("http://", "https://"))
                or type(library_id) is not str or not library_id
                or not callable(getattr(artifacts, "read_original_digest", None))
                or type(maximum_response_bytes) is not int
                or not 1024 <= maximum_response_bytes <= 4_194_304):
            raise ValueError("Audiobookshelf field-effect target is invalid")
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._artifacts = artifacts
        self._maximum_response_bytes = maximum_response_bytes

    def read(self, plan: FieldEffectPlan) -> TargetObservation:
        with self._client() as client:
            detail = self._item(client, plan.work_id)
            return self._observation(client, detail, plan)

    def _observation(self, client: httpx.Client, detail: dict[str, Any], plan: FieldEffectPlan) -> TargetObservation:
        value = self._value(client, detail, plan)
        companion = plan.subtitle_plan()
        subtitle = None if companion is None else self._observation(client, detail, companion)
        # A description lands as the HTML Audiobookshelf stores, which this
        # read shows as visible text; the desired one is read the same way
        # (#1424). Markup the renderer refuses, such as a script the service
        # drops, never reads as landed.
        landed = (
            plan.destination == "description"
            and type(plan.desired_value) is str
            and type(value) is str
            and visible_stored_description(plan.desired_value) == value
        )
        return TargetObservation(value, field_value_version(plan.destination, value), subtitle, landed)

    def read_cover_bytes(self, plan: FieldEffectPlan) -> bytes | None:
        """Read the exact pre-write cover for private recovery composition."""
        if plan.destination != "cover":
            raise ValueError("cover recovery destination invalid")
        with self._client() as client:
            status, headers, body = _bytes_response(
                client,
                f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover",
                {"raw": "1"},
                8 * 1024 * 1024,
            )
        if status == 404:
            return None
        if status != 200:
            raise TargetServiceUnavailable(
                "connection" if status == 401 else "destination",
                ("connection_unavailable" if status == 401 else
                 "insufficient_permission" if status == 403 else
                 "unexpected_effect_behavior"),
            )
        media_type = headers.get("content-type", "").split(";", 1)[0].lower()
        if media_type != _image_media_type(body):
            raise TargetServiceUnavailable("destination", "field_unavailable")
        return body

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
                if plan.subtitle_companion is not None:
                    subtitle_payload = _media_payload("subtitle", plan.subtitle_companion.desired_value)
                    media_payload["metadata"].update(subtitle_payload["metadata"])
            with self._client() as client:
                detail = self._item(client, plan.work_id)
                current = self._observation(client, detail, plan)
                if not current.matches(plan, desired=False, check_version=True):
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
        except (httpx.TransportError, OSError):
            # A timeout or dropped connection says nothing about the field: the
            # suspension it records is connection-wide and a later successful
            # observation clears it (#1382). The write outcome stays unknown.
            return TargetEffectResult(
                "ambiguous", "connection", "connection_unavailable"
            )
        except (httpx.HTTPError, ValueError):
            return TargetEffectResult(
                "ambiguous", "destination", "unexpected_effect_behavior"
            )
        if status in {200, 201, 204}:
            return TargetEffectResult("accepted")
        if status == 401:
            return TargetEffectResult(
                "ambiguous", "connection", "connection_unavailable"
            )
        if status == 403:
            return TargetEffectResult(
                "ambiguous", "destination", "insufficient_permission"
            )
        return TargetEffectResult(
            "ambiguous", "destination", "unexpected_effect_behavior"
        )

    def _client(self) -> httpx.Client:
        try:
            return self._client_factory()
        except Exception as error:
            raise TargetServiceUnavailable(
                "connection", "connection_unavailable"
            ) from error

    def _item(self, client: httpx.Client, work_id: str) -> dict[str, Any]:
        try:
            detail = _json_response(client, "GET", f"{self._endpoint}/api/items/{quote(work_id, safe='')}", {"expanded": "1"}, self._maximum_response_bytes)
        except _ResponseStatusError as error:
            if error.status in {401, 404}:
                raise TargetServiceUnavailable("connection") from error
            if error.status == 403:
                raise TargetServiceUnavailable(
                    "destination", "insufficient_permission"
                ) from error
            raise TargetServiceUnavailable(
                "destination", "unexpected_effect_behavior"
            ) from error
        except (httpx.TransportError, OSError) as error:
            raise TargetServiceUnavailable(
                "connection", "connection_unavailable"
            ) from error
        except httpx.HTTPError as error:
            raise TargetServiceUnavailable(
                "destination", "unexpected_effect_behavior"
            ) from error
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise TargetServiceUnavailable("destination", "field_unavailable") from error
        if (type(detail) is not dict or detail.get("id") != work_id
                or detail.get("libraryId") != self._library_id
                or detail.get("mediaType") != "book" or type(detail.get("media")) is not dict):
            raise TargetServiceUnavailable("connection")
        return detail

    def _value(self, client: httpx.Client, detail: dict[str, Any], plan: FieldEffectPlan) -> object:
        if plan.destination == "cover":
            try:
                status, headers, body = _bytes_response(client, f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover", {"raw": "1"}, 8 * 1024 * 1024)
            except (httpx.TransportError, OSError) as error:
                raise TargetServiceUnavailable(
                    "connection", "connection_unavailable"
                ) from error
            except httpx.HTTPError as error:
                raise TargetServiceUnavailable(
                    "destination", "unexpected_effect_behavior"
                ) from error
            except ValueError as error:
                raise TargetServiceUnavailable(
                    "destination", "field_unavailable"
                ) from error
            if status == 404:
                return None
            if status != 200:
                raise TargetServiceUnavailable(
                    "connection" if status == 401 else "destination",
                    ("connection_unavailable" if status == 401 else
                     "insufficient_permission" if status == 403 else
                     "unexpected_effect_behavior"),
                )
            media_type = headers.get("content-type", "").split(";", 1)[0].lower()
            try:
                observed_media_type = _image_media_type(body)
            except ValueError as error:
                raise TargetServiceUnavailable("destination") from error
            if media_type not in {"image/jpeg", "image/png", "image/webp"} or observed_media_type != media_type:
                raise TargetServiceUnavailable("destination")
            return "cover_" + hashlib.sha256(body).hexdigest()
        media = detail["media"]
        if plan.destination == "tags" and "tags" in media:
            value = media["tags"]
        else:
            metadata = media.get("metadata")
            if type(metadata) is not dict:
                raise TargetServiceUnavailable("destination")
            value = metadata.get(plan.destination)
        if plan.destination == "authors":
            if not isinstance(value, list) or any(type(item) is not dict or type(item.get("name")) is not str for item in value):
                raise TargetServiceUnavailable("destination")
            return [item["name"] for item in value] or None
        if plan.destination == "series":
            if type(value) is not list:
                raise TargetServiceUnavailable("destination")
            normalized = []
            for item in value:
                if type(item) is not dict or type(item.get("name")) is not str:
                    raise TargetServiceUnavailable("destination")
                sequence = item.get("sequence")
                if sequence is not None and type(sequence) not in {str, int, float}:
                    raise TargetServiceUnavailable("destination")
                normalized.append({
                    "name": item["name"],
                    "sequence": None if sequence is None else str(sequence),
                })
            return normalized or None
        if plan.destination == "explicit" and value is not None and type(value) is not bool:
            raise TargetServiceUnavailable("destination")
        if plan.destination in {"narrators", "tags"} and value is None:
            # An item that has never carried a narrator cast or a tag omits
            # the key entirely on some responses and answers `null` on others.
            # Capability discovery already reads both as allowed, so the
            # pre-read has to agree: absent is no value, exactly as `[]` is.
            return None
        if plan.destination in {"genres", "narrators", "tags"}:
            if type(value) is not list or any(type(item) is not str for item in value):
                raise TargetServiceUnavailable("destination")
            return value or None
        if plan.destination == "description" and type(value) is str:
            # The same text the catalogue admits, so the pre-read compares like
            # with like (#1424): visible text, or the stored value where the
            # renderer reads none. An oversized value stays as stored; the
            # catalogue never admitted it, so it can match no plan.
            visible = visible_stored_description(value)
            return visible if type(visible) is str else value
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
    if status != 200:
        raise _ResponseStatusError(status)
    if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise ValueError("Audiobookshelf response invalid")
    _json_depth(body)
    try:
        value = json.loads(
            body.decode("utf-8", "strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError) as error:
        raise ValueError("Audiobookshelf response invalid") from error
    if type(value) is not dict:
        raise ValueError("Audiobookshelf response invalid")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Audiobookshelf response duplicate field")
        value[key] = item
    return value


def _reject_json_constant(_: str) -> float:
    raise ValueError("Audiobookshelf response non-finite value")


def _json_depth(payload: bytes) -> None:
    depth = 0
    quoted = False
    escaped = False
    for byte in payload:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 100:
                raise ValueError("Audiobookshelf response depth invalid")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise ValueError("Audiobookshelf response depth invalid")
    if quoted or escaped or depth:
        raise ValueError("Audiobookshelf response depth invalid")


def _write_status(client: httpx.Client, method: str, url: str, **kwargs: Any) -> int:
    """Issue exactly one write and deliberately discard its response body."""
    with client.stream(method, url, headers={"Accept-Encoding": "identity"}, **kwargs) as response:
        return response.status_code


def _media_payload(destination: str, value: object) -> dict[str, object]:
    if destination == "tags":
        return {"tags": value}
    if destination == "authors":
        return {"metadata": {"authors": [{"name": item} for item in value]}}
    return {"metadata": {destination: value}}


def _capability_value_shape(detail: dict[str, Any], destination: str) -> None:
    media = detail.get("media")
    if type(media) is not dict:
        raise ValueError("Audiobookshelf metadata capability invalid")
    if destination == "tags":
        if "tags" in media:
            value = media["tags"]
        else:
            metadata = media.get("metadata")
            if type(metadata) is not dict:
                raise ValueError("Audiobookshelf metadata capability invalid")
            value = metadata.get("tags")
        if value is not None and (
            type(value) is not list
            or any(type(item) is not str for item in value)
        ):
            raise ValueError("Audiobookshelf tag capability invalid")
    else:
        metadata = media.get("metadata")
        if type(metadata) is not dict:
            raise ValueError("Audiobookshelf metadata capability invalid")
        # A narrator cast is a plain string list on both media. A service that
        # answers with any other shape disables only that destination, never
        # the library (ADR-0071).
        if destination == "explicit" and metadata.get("explicit") is not None and type(metadata["explicit"]) is not bool:
            raise ValueError("Audiobookshelf explicit capability invalid")
        if destination == "narrators":
            narrators = metadata.get("narrators")
            if narrators is not None and (
                type(narrators) is not list
                or any(type(item) is not str for item in narrators)
            ):
                raise ValueError("Audiobookshelf narrator capability invalid")


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
