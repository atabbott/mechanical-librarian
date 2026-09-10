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


@pytest.mark.parametrize("pricing_available", [True, False])
@pytest.mark.parametrize("response_kind", ["valid", "wrong_model", "duplicate"])
def test_description_text_model_needs_pricing_without_changing_cover_capability(
    pricing_available: bool,
    response_kind: str,
) -> None:
    from mechanical_librarian.application.contained_reviewer_contract import (
        ContainedReviewRequest,
    )
    from mechanical_librarian.domain.description_advice import (
        DESCRIPTION_INSTRUCTION,
        DESCRIPTION_OUTPUT_SCHEMA,
        DescriptionAdviceInput,
        DescriptionPreferences,
        description_document,
    )

    packet = description_document(
        DescriptionAdviceInput(
            descriptions=("One synthetic provider description.",),
            preferences=DescriptionPreferences(),
        ).model_dump()
    )
    request_contract = ContainedReviewRequest(
        DESCRIPTION_INSTRUCTION, packet, DESCRIPTION_OUTPUT_SCHEMA
    )
    posted: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json=_key())
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "example/text",
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["text"],
                            },
                            "supported_parameters": ["structured_outputs"],
                            **(
                                {
                                    "pricing": {
                                        "prompt": "0.000001",
                                        "completion": "0.000002",
                                        "request": "0",
                                    }
                                }
                                if pricing_available
                                else {}
                            ),
                        }
                    ]
                },
            )
        posted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "other/model"
                if response_kind == "wrong_model"
                else "example/text",
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"description":"First","description":"Rewritten text.","disposition":"ready"}'
                                if response_kind == "duplicate"
                                else '{"description":"Rewritten text.","disposition":"ready"}'
                            )
                        }
                    }
                ],
            },
        )

    reviewer = OpenRouterReviewer(
        "synthetic-secret", "example/text", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(OpenRouterReviewerError, match="model_text_only"):
            reviewer.probe()
        if not pricing_available:
            with pytest.raises(OpenRouterReviewerError, match="model_unavailable"):
                reviewer.probe_description()
            assert posted == []
            return
        status = reviewer.probe_description()
        assert status.prompt_price == "0.000001" and status.observed_at is not None
        if response_kind != "valid":
            with pytest.raises(OpenRouterReviewerError, match="malformed_response"):
                reviewer.run_description(
                    openrouter_profile("example/text"),
                    DESCRIPTION_INSTRUCTION.encode(),
                    packet.encode(),
                    DESCRIPTION_OUTPUT_SCHEMA.encode(),
                )
            assert len(posted) == 1
            return
        result = json.loads(
            reviewer.run_description(
                openrouter_profile("example/text"),
                DESCRIPTION_INSTRUCTION.encode(),
                packet.encode(),
                DESCRIPTION_OUTPUT_SCHEMA.encode(),
            )
        )
        assert result["request_sha256"] == request_contract.digest
        assert result["result"] == {
            "description": "Rewritten text.",
            "disposition": "ready",
        }
        assert posted[0]["provider"] == {
            "require_parameters": True,
            "allow_fallbacks": False,
        }
        assert posted[0]["model"] == "example/text"
        assert "image_url" not in json.dumps(posted)
        assert "synthetic-secret" not in json.dumps(posted)
    finally:
        reviewer.close()
