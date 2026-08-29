from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
import pytest

from mechanical_librarian.adapters.reviewers import openrouter as module
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterReviewerError,
    openrouter_profile,
)


def _key() -> dict[str, object]:
    return {"data": {"is_free_tier": True}}


def _models(
    model: str = "example/vision",
    *,
    supported_parameters: tuple[str, ...] = ("structured_outputs",),
) -> dict[str, object]:
    return {
        "data": [
            {
                "id": model,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "supported_parameters": list(supported_parameters),
            }
        ]
    }


def test_probe_uses_only_fixed_origin_and_current_catalogue_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = _key() if request.url.path == "/api/v1/key" else _models()
        return httpx.Response(200, json=payload)

    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(handler)
    )
    try:
        status = reviewer.probe()
    finally:
        reviewer.close()
    assert status.structured_outputs is True
    assert [request.url.path for request in requests] == [
        "/api/v1/key",
        "/api/v1/models",
    ]
    assert {request.url.host for request in requests} == {"openrouter.ai"}


@pytest.mark.parametrize(
    ("status", "expected"),
    ((401, "invalid_key"), (402, "quota_exhausted"), (429, "rate_limited")),
)
def test_documented_http_statuses_have_value_free_identities(
    status: int, expected: str
) -> None:
    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status)),
    )
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.probe()
    finally:
        reviewer.close()
    assert caught.value.code == expected


def test_catalogue_bound_covers_measured_current_payload_and_rejects_overflow() -> None:
    # Official 2026-08-28 snapshot: 387 models, compact JSON 638,527 bytes.
    assert module._MAX_MODELS_RESPONSE > 638_527
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=_key())
        return httpx.Response(
            200,
            headers={"content-length": str(module._MAX_MODELS_RESPONSE + 1)},
            content=b"{}",
        )

    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.probe()
    finally:
        reviewer.close()
    assert caught.value.code == "malformed_response"


@pytest.mark.parametrize("target", ("key", "models", "chat"))
def test_chunked_oversize_is_rejected_without_content_length(
    target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (
            (target == "key" and request.url.path == "/api/v1/key")
            or (target == "models" and request.url.path == "/api/v1/models")
            or (target == "chat" and request.url.path == "/api/v1/chat/completions")
        ):
            maximum = {
                "key": module._MAX_KEY_RESPONSE,
                "models": module._MAX_MODELS_RESPONSE,
                "chat": module._MAX_CHAT_RESPONSE,
            }[target]
            return httpx.Response(
                200,
                headers={"transfer-encoding": "chunked"},
                content=b"x" * (maximum + 1),
            )
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json=_key())
        return httpx.Response(200, json=_models())

    monkeypatch.setattr(module, "admit_cover_review_request", lambda *_args: _Request())
    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            if target == "chat":
                reviewer.run(
                    openrouter_profile("example/vision"),
                    b"instruction",
                    b"{}",
                    b"{}",
                    (),
                )
            else:
                reviewer.probe()
    finally:
        reviewer.close()
    assert caught.value.code == "malformed_response"


@dataclass(frozen=True)
class _Request:
    digest: str = "a" * 64


@pytest.mark.parametrize("reasoning_supported", (True, False))
def test_one_run_shapes_reasoning_from_one_immediate_probe_then_one_chat_call(
    monkeypatch: pytest.MonkeyPatch, reasoning_supported: bool
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json=_key())
        if request.url.path == "/api/v1/models":
            parameters = (
                ("structured_outputs", "reasoning")
                if reasoning_supported
                else ("structured_outputs",)
            )
            return httpx.Response(200, json=_models(supported_parameters=parameters))
        body = json.loads(request.content)
        assert body["model"] == "example/vision"
        assert body["max_tokens"] == 8192
        assert body["provider"] == {"require_parameters": True}
        assert body["response_format"]["json_schema"]["strict"] is True
        if reasoning_supported:
            assert body["reasoning"] == {"effort": "minimal", "exclude": True}
        else:
            assert "reasoning" not in body
        return httpx.Response(
            200,
            json={
                "model": "example/vision",
                "choices": [{"message": {"content": '{"result":"ok"}'}}],
            },
        )

    monkeypatch.setattr(module, "admit_cover_review_request", lambda *_args: _Request())
    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(handler)
    )
    try:
        reviewer.run(
            openrouter_profile("example/vision"),
            b"instruction",
            b"{}",
            b"{}",
            (),
        )
    finally:
        reviewer.close()
    assert [request.url.path for request in requests] == [
        "/api/v1/key",
        "/api/v1/models",
        "/api/v1/chat/completions",
    ]
