"""Fixed-origin OpenRouter transport for the sealed cover-observation packet."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation

import httpx

from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    admit_cover_review_request,
    canonical_json,
    unique_json_object,
)
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterAccountStatus,
    OpenRouterFailure,
    OpenRouterReviewerError,
    openrouter_cover_profile,
    openrouter_profile,
)
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceInput,
    DescriptionAdviceOutput,
)
from mechanical_librarian.domain.reviewer_access import OpenRouterTaskSelection

_ORIGIN = "https://openrouter.ai"
_KEY_PATH = "/api/v1/key"
_MODELS_PATH = "/api/v1/models"
_CHAT_PATH = "/api/v1/chat/completions"
_MAX_KEY_RESPONSE = 65_536
# The catalogue grows with OpenRouter, so it has no total bound: it is read as
# one stream within the call deadline and only one entry is held at a time.
_MAX_CATALOGUE_ENTRY = 65_536
# A deliberate margin, not a strict derivation: one vision model's answer
# exceeded the former 131,072-byte cap on 2026-10-24, and 1 MiB is eight times
# that cap. The 8,192-token request bounds the content itself (checked at
# 64 KiB below); the margin covers JSON escaping, any returned reasoning and
# the provider envelope.
_MAX_CHAT_RESPONSE = 1_048_576
_MAX_ERROR_RESPONSE = 8_192
_STRUCTURE = re.compile(rb'[\[\]{},:"]')
_STRING_END = re.compile(rb'["\\]')


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
        self._timeout_seconds = timeout_seconds
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

    def _deadline(self) -> float:
        # One budget per public call: the lookups and the completion share it.
        return time.monotonic() + self._timeout_seconds

    def probe(self) -> OpenRouterAccountStatus:
        status, _reasoning_supported = self._probe_capabilities(self._deadline())
        return status

    def probe_description(self) -> OpenRouterAccountStatus:
        return self._probe_capabilities(self._deadline(), description=True)[0]

    def _probe_capabilities(
        self, deadline: float, *, description: bool = False
    ) -> tuple[OpenRouterAccountStatus, bool]:
        key = self._json_request("GET", _KEY_PATH, _MAX_KEY_RESPONSE, deadline)
        data = key.get("data")
        if type(data) is not dict or type(data.get("is_free_tier")) is not bool:
            raise OpenRouterReviewerError("malformed_response")
        model = self._current_model(deadline)
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
        if ("text" if description else "image") not in inputs or "text" not in outputs:
            raise OpenRouterReviewerError("model_text_only")
        if "structured_outputs" not in parameters:
            raise OpenRouterReviewerError("model_unavailable")
        prices: list[str | None] = []
        pricing = model.get("pricing")
        for name in ("prompt", "completion", "request", "image"):
            value = pricing.get(name) if type(pricing) is dict else None
            try:
                if type(value) is not str or not 1 <= len(value) <= 40:
                    raise ValueError
                number = Decimal(value)
                if not number.is_finite() or not 0 <= number <= 1_000_000:
                    raise ValueError
            except (ValueError, InvalidOperation):
                if (
                    description
                    and name != "image"
                    and not (
                        name == "request"
                        and type(pricing) is dict
                        and name not in pricing
                    )
                ):
                    raise OpenRouterReviewerError("model_unavailable") from None
                value = None
            prices.append(value)
        return (
            OpenRouterAccountStatus(
                tier="free" if data["is_free_tier"] else "paid",
                model=self._model,
                input_modalities=inputs,
                output_modalities=outputs,
                structured_outputs=True,
                observed_at=int(time.time()),
                prompt_price=prices[0],
                completion_price=prices[1],
                request_price=prices[2],
                image_price=prices[3],
            ),
            "reasoning" in parameters,
        )

    def run(
        self,
        profile: bytes,
        instruction: bytes,
        packet: bytes,
        schema: bytes,
        images: tuple[tuple[str, str, bytes], ...],
    ) -> str:
        deadline = self._deadline()
        if profile != openrouter_cover_profile(self._model):
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
        _status, reasoning_supported = self._probe_capabilities(deadline)
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
        chat_body: dict[str, object] = {
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": content}],
            "model": self._model,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "cover_observation",
                    "strict": True,
                    "schema": schema_value,
                },
            },
        }
        if reasoning_supported:
            chat_body["reasoning"] = {"effort": "minimal", "exclude": True}
        response = self._json_request(
            "POST",
            _CHAT_PATH,
            _MAX_CHAT_RESPONSE,
            deadline,
            json_body=chat_body,
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

    def run_description(
        self, profile: bytes, instruction: bytes, packet: bytes, schema: bytes
    ) -> str:
        deadline = self._deadline()
        if profile != openrouter_profile(self._model):
            raise OpenRouterReviewerError("profile_mismatch")
        try:
            request = ContainedReviewRequest(
                instruction.decode("utf-8"),
                packet.decode("utf-8"),
                schema.decode("utf-8"),
            )
            if (
                request.instruction != DESCRIPTION_INSTRUCTION
                or request.output_schema != DESCRIPTION_OUTPUT_SCHEMA
            ):
                raise ValueError
            DescriptionAdviceInput.model_validate_json(request.input_document)
        except (ValueError, UnicodeError):
            raise OpenRouterReviewerError("malformed_request") from None
        self._probe_capabilities(deadline, description=True)
        response = self._json_request(
            "POST",
            _CHAT_PATH,
            _MAX_CHAT_RESPONSE,
            deadline,
            json_body={
                "model": self._model,
                "max_tokens": 8192,
                "messages": [
                    {
                        "role": "user",
                        "content": request.instruction
                        + "\nSealed descriptions:\n"
                        + request.input_document,
                    }
                ],
                "provider": {"require_parameters": True, "allow_fallbacks": False},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "description_rewrite",
                        "strict": True,
                        "schema": json.loads(request.output_schema),
                    },
                },
            },
        )
        try:
            choices = response["choices"]
            if (
                response.get("model") != self._model
                or type(choices) is not list
                or len(choices) != 1
            ):
                raise ValueError
            content = choices[0]["message"]["content"]
            if type(content) is not str or len(content.encode("utf-8")) > 65_536:
                raise ValueError
            result = DescriptionAdviceOutput.model_validate(
                json.loads(content, object_pairs_hook=unique_json_object)
            )
        except (ValueError, TypeError, KeyError, IndexError):
            raise OpenRouterReviewerError("malformed_response") from None
        return canonical_json(
            {"request_sha256": request.digest, "result": result.model_dump()}
        )

    def _current_model(self, deadline: float) -> dict[str, object]:
        catalogue = _Catalogue(self._model)
        self._exchange("GET", _MODELS_PATH, None, deadline, catalogue.feed)
        match = catalogue.finish()
        if match is None:
            raise OpenRouterReviewerError("model_unavailable")
        return match

    def _json_request(
        self,
        method: str,
        path: str,
        maximum: int,
        deadline: float,
        *,
        json_body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        body = bytearray()
        self._exchange(method, path, maximum, deadline, body.extend, json_body)
        try:
            value = json.loads(bytes(body).decode("utf-8", "strict"))
            if type(value) is not dict:
                raise ValueError
            return value
        except (UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise OpenRouterReviewerError("malformed_response") from error

    def _exchange(
        self,
        method: str,
        path: str,
        maximum: int | None,
        deadline: float,
        consume: Callable[[bytes], object],
        json_body: dict[str, object] | None = None,
    ) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OpenRouterReviewerError("timed_out")
        try:
            with self._client.stream(
                method,
                path,
                json=json_body,
                # Each phase keeps its own bound, never past the shared deadline.
                timeout=httpx.Timeout(min(self._timeout_seconds, remaining)),
            ) as response:
                if response.is_redirect:
                    raise OpenRouterReviewerError("transport")
                if response.status_code in {401, 403}:
                    raise OpenRouterReviewerError("invalid_key")
                if response.status_code == 429:
                    raise OpenRouterReviewerError("rate_limited")
                if response.status_code in {402}:
                    raise OpenRouterReviewerError("quota_exhausted")
                if response.status_code != 200:
                    try:
                        error_body = b"".join(
                            _chunks(response, _MAX_ERROR_RESPONSE, deadline)
                        )
                    except OpenRouterReviewerError as error:
                        if error.code != "response_too_large":
                            raise
                        error_body = b""
                    raise OpenRouterReviewerError(
                        _refusal(response.status_code, error_body)
                        or (
                            "model_unavailable"
                            if response.status_code == 404
                            else "transport"
                        )
                    )
                for chunk in _chunks(response, maximum, deadline):
                    consume(chunk)
        except OpenRouterReviewerError:
            raise
        except httpx.HTTPError as error:
            raise OpenRouterReviewerError(
                "timed_out" if time.monotonic() >= deadline else "unreachable"
            ) from error


def _chunks(
    response: httpx.Response, maximum: int | None, deadline: float
) -> Iterator[bytes]:
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isascii() or not declared.isdecimal()):
        raise OpenRouterReviewerError("malformed_response")
    if declared is not None and maximum is not None and int(declared) > maximum:
        raise OpenRouterReviewerError("response_too_large")
    total = 0
    for chunk in response.iter_bytes():
        if time.monotonic() >= deadline:
            raise OpenRouterReviewerError("timed_out")
        total += len(chunk)
        if maximum is not None and total > maximum:
            raise OpenRouterReviewerError("response_too_large")
        yield chunk
    # The end of the stream can itself arrive late, after the last bytes.
    if time.monotonic() >= deadline:
        raise OpenRouterReviewerError("timed_out")


def _refusal(status_code: int, body: bytes) -> OpenRouterFailure | None:
    """Recognise two documented refusals; the provider's words never leave here."""
    try:
        error = json.loads(body.decode("utf-8", "strict"))["error"]
        message = error["message"]
        if type(message) is not str:
            return None
    except (UnicodeError, ValueError, KeyError, TypeError):
        return None
    # Metadata is optional context: a missing, null or misshapen one is ignored.
    metadata = error.get("metadata")
    raw = metadata.get("raw") if type(metadata) is dict else None
    text = (message + ("\n" + raw if type(raw) is str else "")).casefold()
    if status_code == 404 and "no endpoints found matching your data policy" in text:
        return "data_policy_refused"
    if status_code == 400 and "too many images" in text:
        return "image_limit_exceeded"
    return None


def _entry(text: bytes) -> object:
    try:
        return json.loads(text.decode("utf-8", "strict"))
    except (UnicodeError, ValueError, RecursionError):
        raise OpenRouterReviewerError("malformed_response") from None


class _Catalogue:
    """Cut the catalogue's top-level ``data`` array into bounded entries.

    Only the outer ``{"key": value, ...}`` and ``"data": [entry, ...]`` skeleton
    is scanned here, carrying string, escape and depth state across chunks.
    Every key, other top-level value and entry is cut out whole, at most
    ``_MAX_CATALOGUE_ENTRY`` bytes, and parsed by ``json``; entries other than
    the selected model are dropped as soon as they parse, and only the first
    match is kept: a repeat merely marks the selection ambiguous.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._state = "start"
        self._capture = bytearray()
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._keys = 0
        self._data_seen = False
        self._entries = 0
        self._match: dict[str, object] | None = None
        self._repeated = False

    def feed(self, chunk: bytes) -> None:
        index = 0
        while index < len(chunk):
            if self._state in {"start", "data", "after_data", "end"}:
                byte = chunk[index : index + 1]
                index += 1
                if byte not in b" \t\r\n":
                    self._skeleton(byte)
            elif self._in_string:
                if self._escaped:
                    self._escaped = False
                    self._append(chunk[index : index + 1])
                    index += 1
                    continue
                found = _STRING_END.search(chunk, index)
                end = len(chunk) if found is None else found.end()
                self._append(chunk[index:end])
                index = end
                if found is not None:
                    self._escaped = found.group() == b"\\"
                    self._in_string = self._escaped
            else:
                found = _STRUCTURE.search(chunk, index)
                if found is None:
                    self._append(chunk[index:])
                    return
                byte = found.group()
                if self._depth == 0 and (
                    byte in b",}]" or (byte == b":" and self._state == "key")
                ):
                    self._append(chunk[index : found.start()])
                    index = found.end()
                    self._close(byte)
                    continue
                self._append(chunk[index : found.end()])
                index = found.end()
                if byte == b'"':
                    self._in_string = True
                elif byte in b"[{":
                    self._depth += 1
                elif byte in b"]}":
                    self._depth -= 1

    def finish(self) -> dict[str, object] | None:
        """Return the one matching entry; ``None`` when none or several match."""
        if self._state != "end" or not self._data_seen:
            raise OpenRouterReviewerError("malformed_response")
        return None if self._repeated else self._match

    def _append(self, part: bytes) -> None:
        self._capture.extend(part)
        if len(self._capture) > _MAX_CATALOGUE_ENTRY:
            raise OpenRouterReviewerError("response_too_large")

    def _skeleton(self, byte: bytes) -> None:
        if self._state == "start" and byte == b"{":
            self._state = "key"
        elif self._state == "data" and byte == b"[":
            self._state = "entry"
        elif self._state == "after_data" and byte in b",}":
            self._state = "key" if byte == b"," else "end"
        else:
            raise OpenRouterReviewerError("malformed_response")

    def _close(self, delimiter: bytes) -> None:
        text = bytes(self._capture)
        self._capture.clear()
        blank = not text.strip()
        state = self._state
        if state == "key" and delimiter == b"}" and blank and not self._keys:
            self._state = "end"
        elif state == "key" and delimiter == b":" and not blank:
            key = _entry(text)
            if type(key) is not str or (key == "data" and self._data_seen):
                raise OpenRouterReviewerError("malformed_response")
            self._keys += 1
            self._data_seen = self._data_seen or key == "data"
            self._state = "data" if key == "data" else "value"
        elif state == "value" and delimiter in b",}" and not blank:
            _entry(text)
            self._state = "key" if delimiter == b"," else "end"
        elif state == "entry" and delimiter == b"]" and blank and not self._entries:
            self._state = "after_data"
        elif state == "entry" and delimiter in b",]" and not blank:
            self._entries += 1
            entry = _entry(text)
            if type(entry) is dict and entry.get("id") == self._model:
                if self._match is None:
                    self._match = entry
                else:
                    self._repeated = True
            if delimiter == b"]":
                self._state = "after_data"
        else:
            raise OpenRouterReviewerError("malformed_response")


__all__ = [
    "OpenRouterAccountStatus",
    "OpenRouterReviewer",
    "OpenRouterReviewerError",
    "openrouter_profile",
]
