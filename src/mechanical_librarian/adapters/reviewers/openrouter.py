"""Fixed-origin OpenRouter transport for the sealed cover-observation packet."""

from __future__ import annotations

import base64
import hashlib
import json

import httpx

from mechanical_librarian.application.contained_reviewer_contract import (
    admit_cover_review_request,
    canonical_json,
)
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    openrouter_profile,
)
from mechanical_librarian.domain.reviewer_access import OpenRouterTaskSelection

_ORIGIN = "https://openrouter.ai"
_KEY_PATH = "/api/v1/key"
_MODELS_PATH = "/api/v1/models"
_CHAT_PATH = "/api/v1/chat/completions"
_MAX_KEY_RESPONSE = 65_536
_MAX_MODELS_RESPONSE = 1_048_576
_MAX_CHAT_RESPONSE = 131_072


class OpenRouterReviewer:
    """Validate current account/model evidence and make exactly one model call."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if type(api_key) is not str or not api_key or api_key.strip() != api_key:
            raise OpenRouterReviewerError("invalid_key")
        self._api_key = api_key
        self._model = OpenRouterTaskSelection(engine="openrouter", model=model).model
        self._client = httpx.Client(
            base_url=_ORIGIN,
            headers={"Authorization": f"Bearer {api_key}"},
            follow_redirects=False,
            trust_env=False,
            timeout=timeout_seconds,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def probe(self) -> OpenRouterAccountStatus:
        key = self._json_request("GET", _KEY_PATH, _MAX_KEY_RESPONSE)
        data = key.get("data")
        if type(data) is not dict or type(data.get("is_free_tier")) is not bool:
            raise OpenRouterReviewerError("malformed_response")
        model = self._current_model()
        architecture = model.get("architecture")
        parameters = model.get("supported_parameters")
        if (
            type(architecture) is not dict
            or type(architecture.get("input_modalities")) is not list
            or type(architecture.get("output_modalities")) is not list
            or type(parameters) is not list
            or any(type(value) is not str for value in parameters)
            or any(
                type(value) is not str
                for value in (
                    *architecture["input_modalities"],
                    *architecture["output_modalities"],
                )
            )
        ):
            raise OpenRouterReviewerError("malformed_response")
        inputs = tuple(architecture["input_modalities"])
        outputs = tuple(architecture["output_modalities"])
        if "image" not in inputs or "text" not in outputs:
            raise OpenRouterReviewerError("model_text_only")
        if "structured_outputs" not in parameters:
            raise OpenRouterReviewerError("model_unavailable")
        return OpenRouterAccountStatus(
            tier="free" if data["is_free_tier"] else "paid",
            model=self._model,
            input_modalities=inputs,
            output_modalities=outputs,
            structured_outputs=True,
        )

    def run(
        self,
        profile: bytes,
        instruction: bytes,
        packet: bytes,
        schema: bytes,
        images: tuple[tuple[str, str, bytes], ...],
    ) -> str:
        if profile != openrouter_profile(self._model):
            raise OpenRouterReviewerError("profile_mismatch")
        try:
            instruction_text = instruction.decode("utf-8", "strict")
            packet_text = packet.decode("utf-8", "strict")
            schema_text = schema.decode("utf-8", "strict")
            schema_value = json.loads(schema_text)
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise OpenRouterReviewerError("malformed_request") from error
        request = admit_cover_review_request(
            instruction_text, packet_text, schema_text, images
        )
        self.probe()
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": instruction_text + "\nSealed packet:\n" + packet_text,
            }
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(payload).decode("ascii")
                },
            }
            for _reference, _digest, payload in images
        )
        response = self._json_request(
            "POST",
            _CHAT_PATH,
            _MAX_CHAT_RESPONSE,
            json_body={
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": content}],
                "model": self._model,
                "provider": {"require_parameters": True},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "cover_observation",
                        "strict": True,
                        "schema": schema_value,
                    },
                },
            },
        )
        try:
            choices = response["choices"]
            model = response["model"]
            if (
                type(choices) is not list
                or len(choices) != 1
                or type(choices[0]) is not dict
                or type(choices[0].get("message")) is not dict
                or type(model) is not str
                or model != self._model
            ):
                raise ValueError
            content_value = choices[0]["message"].get("content")
            if (
                type(content_value) is not str
                or len(content_value.encode("utf-8")) > 65_536
            ):
                raise ValueError
            result = json.loads(content_value)
            if type(result) is not dict:
                raise ValueError
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise OpenRouterReviewerError("malformed_response") from error
        image_binding = canonical_json(
            [
                {"reference": reference, "sha256": digest}
                for reference, digest, _payload in images
            ]
        )
        execution_identity = canonical_json(
            {
                "profile_sha256": hashlib.sha256(profile).hexdigest(),
                "request_sha256": request.digest,
            }
        )
        envelope = {
            "attempt_evidence": [{"dispatched": True}],
            "execution_sha256": hashlib.sha256(
                execution_identity.encode("utf-8")
            ).hexdigest(),
            "image_binding_sha256": hashlib.sha256(
                image_binding.encode("utf-8")
            ).hexdigest(),
            "image_count": len(images),
            "input_sha256": hashlib.sha256(packet).hexdigest(),
            "request_sha256": request.digest,
            "result": result,
            "result_sha256": hashlib.sha256(
                canonical_json(result).encode("utf-8")
            ).hexdigest(),
            "schema_version": "cover-contained-reviewer-result-v1",
            "status": "completed",
        }
        return canonical_json(envelope)

    def _current_model(self) -> dict[str, object]:
        response = self._json_request("GET", _MODELS_PATH, _MAX_MODELS_RESPONSE)
        models = response.get("data")
        if type(models) is not list:
            raise OpenRouterReviewerError("malformed_response")
        matches = [
            value
            for value in models
            if type(value) is dict and value.get("id") == self._model
        ]
        if len(matches) != 1:
            raise OpenRouterReviewerError("model_unavailable")
        return matches[0]

    def _json_request(
        self,
        method: str,
        path: str,
        maximum: int,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            with self._client.stream(method, path, json=json_body) as response:
                if response.is_redirect:
                    raise OpenRouterReviewerError("transport")
                if response.status_code in {401, 403}:
                    raise OpenRouterReviewerError("invalid_key")
                if response.status_code == 429:
                    raise OpenRouterReviewerError("rate_limited")
                if response.status_code in {402}:
                    raise OpenRouterReviewerError("quota_exhausted")
                if response.status_code in {404}:
                    raise OpenRouterReviewerError("model_unavailable")
                if response.status_code != 200:
                    raise OpenRouterReviewerError("transport")
                declared = response.headers.get("content-length")
                if declared is not None and (
                    not declared.isascii()
                    or not declared.isdecimal()
                    or int(declared) > maximum
                ):
                    raise OpenRouterReviewerError("malformed_response")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=16_384):
                    remaining = maximum + 1 - len(body)
                    if remaining <= 0:
                        raise OpenRouterReviewerError("malformed_response")
                    body.extend(chunk[:remaining])
                    if len(body) > maximum or len(chunk) > remaining:
                        raise OpenRouterReviewerError("malformed_response")
        except OpenRouterReviewerError:
            raise
        except httpx.HTTPError as error:
            raise OpenRouterReviewerError("unreachable") from error
        try:
            value = json.loads(bytes(body).decode("utf-8", "strict"))
            if type(value) is not dict:
                raise ValueError
            return value
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise OpenRouterReviewerError("malformed_response") from error


__all__ = [
    "OpenRouterAccountStatus",
    "OpenRouterReviewer",
    "OpenRouterReviewerError",
    "openrouter_profile",
]
