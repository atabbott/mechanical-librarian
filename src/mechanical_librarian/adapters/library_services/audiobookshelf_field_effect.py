"""Supported, one-field Audiobookshelf target effect adapter.

Audiobookshelf has no conditional update API.  The application therefore owns
the immediately-before-write pre-read and post-write verification; this adapter
performs exactly one destination-minimal request and never retries it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.field_effects import TargetEffectResult, TargetObservation
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.field_effect import FieldEffectPlan


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
        return TargetObservation(value, _version(detail))

    def apply(self, plan: FieldEffectPlan) -> TargetEffectResult:
        """Issue one request; an uncertain transport result is deliberately ambiguous."""
        try:
            with self._client_factory() as client:
                if plan.destination == "cover":
                    digest = plan.desired_value.removeprefix("cover_")
                    if (not isinstance(plan.desired_value, str)
                            or len(digest) != 64):
                        return TargetEffectResult("conflict")
                    try:
                        body = self._artifacts.read_original_digest(digest)
                    except MetadataReportError:
                        return TargetEffectResult("conflict")
                    if hashlib.sha256(body).hexdigest() != digest:
                        return TargetEffectResult("conflict")
                    response = client.post(
                        f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover",
                        files={"cover": ("cover", body, _image_media_type(body))},
                    )
                else:
                    response = client.patch(
                        f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/media",
                        json=_media_payload(plan.destination, plan.desired_value),
                    )
        except (httpx.HTTPError, OSError, ValueError):
            return TargetEffectResult("ambiguous")
        if response.status_code in {200, 201}:
            return TargetEffectResult("accepted")
        # A refusal is terminal for this bounded attempt.  The application will
        # record field-local stale/reconciliation evidence rather than retrying.
        return TargetEffectResult("conflict")

    def _item(self, client: httpx.Client, work_id: str) -> dict[str, Any]:
        try:
            response = client.get(
                f"{self._endpoint}/api/items/{quote(work_id, safe='')}",
                params={"expanded": "1"}, headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                raise ValueError("Audiobookshelf item unavailable")
            if response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                raise ValueError("Audiobookshelf item invalid")
            if len(response.content) > self._maximum_response_bytes:
                raise ValueError("Audiobookshelf item invalid")
            detail = json.loads(response.content.decode("utf-8", "strict"))
        except (httpx.HTTPError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Audiobookshelf item invalid") from error
        if (type(detail) is not dict or detail.get("id") != work_id
                or detail.get("libraryId") != self._library_id
                or detail.get("mediaType") != "book" or type(detail.get("media")) is not dict):
            raise ValueError("Audiobookshelf item invalid")
        _version(detail)
        return detail

    def _value(self, client: httpx.Client, detail: dict[str, Any], plan: FieldEffectPlan) -> object:
        if plan.destination == "cover":
            response = client.get(f"{self._endpoint}/api/items/{quote(plan.work_id, safe='')}/cover", params={"raw": "1"})
            if response.status_code == 404:
                return None
            if response.status_code != 200 or len(response.content) > 8 * 1024 * 1024:
                raise ValueError("Audiobookshelf cover invalid")
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media_type not in {"image/jpeg", "image/png", "image/webp"} or _image_media_type(response.content) != media_type:
                raise ValueError("Audiobookshelf cover invalid")
            return "cover_" + hashlib.sha256(response.content).hexdigest()
        metadata = detail["media"].get("metadata")
        if type(metadata) is not dict:
            raise ValueError("Audiobookshelf metadata invalid")
        value = metadata.get(plan.destination) if plan.destination != "tags" else detail["media"].get("tags")
        if plan.destination == "authors":
            if not isinstance(value, list) or any(type(item) is not dict or type(item.get("name")) is not str for item in value):
                raise ValueError("Audiobookshelf authors invalid")
            return [item["name"] for item in value]
        if plan.destination in {"genres", "tags"}:
            if type(value) is not list or any(type(item) is not str for item in value):
                raise ValueError("Audiobookshelf list invalid")
        return value


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


def _image_media_type(body: bytes) -> str:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("cover artifact media type is invalid")
