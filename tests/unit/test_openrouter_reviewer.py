from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.reviewers import openrouter as module
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
from mechanical_librarian.application.reviewer_contracts import (
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    openrouter_cover_profile,
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
    # Official 2026-08-28 snapshot: 387 models, compact JSON 638,527 bytes; the
    # catalogue has no total bound, so one far past 1 MiB must still validate.
    filler = [
        {"id": f"example/filler-{index}", "description": "f" * 1_000}
        for index in range(2_000)
    ]
    selected = cast("list[object]", _models()["data"])
    catalogue = json.dumps({"data": [*filler, *selected, *filler]}).encode()
    assert len(catalogue) > 4 * 1_048_576
    oversized = json.dumps(
        {"data": [{"id": "example/other", "d": "x" * module._MAX_CATALOGUE_ENTRY}]}
    ).encode()

    def reviewer_for(body: bytes) -> OpenRouterReviewer:
        return OpenRouterReviewer(
            "secret",
            "example/vision",
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(
                        200,
                        json=_key(),
                    )
                    if request.url.path == "/api/v1/key"
                    else httpx.Response(200, content=body)
                )
            ),
        )

    reviewer = reviewer_for(catalogue)
    try:
        assert reviewer.probe().input_modalities == ("text", "image")
    finally:
        reviewer.close()
    reviewer = reviewer_for(oversized)
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.probe()
    finally:
        reviewer.close()
    assert caught.value.code == "response_too_large"


@pytest.mark.parametrize("declared", (False, True))
@pytest.mark.parametrize("target", ("key", "models", "chat"))
def test_chunked_oversize_is_rejected_without_content_length(
    target: str, declared: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (
            (target == "key" and request.url.path == "/api/v1/key")
            or (target == "models" and request.url.path == "/api/v1/models")
            or (target == "chat" and request.url.path == "/api/v1/chat/completions")
        ):
            if target == "models":
                # A single entry over its bound; the catalogue has no total one.
                body = b'{"data":[{"id":"' + b"x" * module._MAX_CATALOGUE_ENTRY
                body += b'"}]}'
            else:
                maximum = {
                    "key": module._MAX_KEY_RESPONSE,
                    "chat": module._MAX_CHAT_RESPONSE,
                }[target]
                body = b"x" * (maximum + 1)
            return httpx.Response(
                200,
                headers=(
                    {"content-length": str(len(body))}
                    if declared
                    else {"transfer-encoding": "chunked"}
                ),
                content=body,
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
                    openrouter_cover_profile("example/vision"),
                    b"instruction",
                    b"{}",
                    b"{}",
                    (),
                )
            else:
                reviewer.probe()
    finally:
        reviewer.close()
    assert caught.value.code == "response_too_large"


def test_chat_bound_keeps_its_documented_margin() -> None:
    # Eight times the former 131,072-byte cap one vision model's answer exceeded.
    assert module._MAX_CHAT_RESPONSE == 8 * 131_072


class _Chunks(httpx.SyncByteStream):
    """Synthetic response body, optionally trickled, that records its closing."""

    def __init__(self, chunks: list[bytes], delay: float = 0.0) -> None:
        self.chunks, self.delay, self.closed = chunks, delay, False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                time.sleep(self.delay)
            yield chunk

    def close(self) -> None:
        self.closed = True


def _probe_catalogue(
    body: bytes, *, one_byte: bool = False
) -> OpenRouterAccountStatus | str:
    stream = _Chunks(
        [body[i : i + 1] for i in range(len(body))] if one_byte else [body]
    )
    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, json=_key())
                if request.url.path == "/api/v1/key"
                else httpx.Response(200, stream=stream)
            )
        ),
    )
    try:
        return reviewer.probe()
    except OpenRouterReviewerError as error:
        return error.code
    finally:
        reviewer.close()


_ENTRY = (
    '{"id":"example/vision","architecture":{"input_modalities":["text","image"],'
    '"output_modalities":["text"]},"supported_parameters":["structured_outputs"]}'
)


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        pytest.param('{"data":[]}', "model_unavailable", id="empty-data"),
        pytest.param(
            '{"data":[{"id":"example/other"},7,null]}',
            "model_unavailable",
            id="other-entries-only",
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "," + _ENTRY + "]}",
            "model_unavailable",
            id="duplicate-model-id",
        ),
        pytest.param("{}", "malformed_response", id="missing-data"),
        pytest.param(
            '{"other":[' + _ENTRY + "]}",
            "malformed_response",
            id="entries-under-other-key",
        ),
        pytest.param(
            '{"data":{"id":"example/vision"}}',
            "malformed_response",
            id="data-is-object",
        ),
        pytest.param('{"data":null}', "malformed_response", id="data-is-null"),
        pytest.param(
            '{"data":[],"data":[' + _ENTRY + "]}",
            "malformed_response",
            id="duplicate-data-key",
        ),
        pytest.param(
            '{"data":[' + _ENTRY + '],"d\\u0061ta":[]}',
            "malformed_response",
            id="duplicate-data-key-escaped",
        ),
        pytest.param("[" + _ENTRY + "]", "malformed_response", id="top-level-array"),
        pytest.param(
            '{"data":[' + _ENTRY + ",]}",
            "malformed_response",
            id="trailing-comma-in-array",
        ),
        pytest.param(
            '{"data":[,' + _ENTRY + "]}",
            "malformed_response",
            id="leading-comma-in-array",
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "}}",
            "malformed_response",
            id="array-closed-by-brace",
        ),
        pytest.param(
            '{"data":[' + _ENTRY + '],"x":tru}',
            "malformed_response",
            id="invalid-other-value",
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "]}}", "malformed_response", id="extra-closer"
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "]} x", "malformed_response", id="trailing-text"
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "],}",
            "malformed_response",
            id="trailing-comma-in-object",
        ),
        pytest.param(
            '{"data":[{"id":"example/vision"]}',
            "malformed_response",
            id="entry-closed-by-bracket",
        ),
        pytest.param(
            '{"data":[' + _ENTRY, "malformed_response", id="truncated-in-array"
        ),
        pytest.param(
            '{"data":[' + _ENTRY + "]", "malformed_response", id="truncated-in-object"
        ),
        pytest.param(
            '{"data":[{"id":"example/vis',
            "malformed_response",
            id="truncated-in-string",
        ),
        pytest.param("", "malformed_response", id="empty-body"),
    ),
)
@pytest.mark.parametrize("one_byte", (False, True), ids=("whole", "one-byte"))
def test_catalogue_structure_and_exactly_one_match(
    body: str, expected: str, one_byte: bool
) -> None:
    assert _probe_catalogue(body.encode(), one_byte=one_byte) == expected


@pytest.mark.parametrize("one_byte", (False, True))
def test_catalogue_strings_carry_escaped_quotes_and_brackets(one_byte: bool) -> None:
    tricky = 'say \\"]},[{:\\\\ and \\u005d then \\\\'
    body = (
        ' { "meta" : {"note": "' + tricky + '", "list": [1, {"a": "]"}]} ,\n'
        ' "d\\u0061ta" : [ {"id": "example/other", "description": "'
        + tricky
        + '"},'
        + _ENTRY[:-1]
        + ', "name": "'
        + tricky
        + '"} , "]}"\t] }\r\n'
    ).encode()
    status = _probe_catalogue(body, one_byte=one_byte)
    assert not isinstance(status, str)
    assert status.input_modalities == ("text", "image")


def test_response_trickling_past_the_deadline_times_out_and_closes() -> None:
    stream = _Chunks([b" "] * 200, delay=0.05)
    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        timeout_seconds=0.5,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, stream=stream)
        ),
    )
    started = time.monotonic()
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.probe()
    finally:
        reviewer.close()
    elapsed = time.monotonic() - started
    # The body would take ten seconds; the half-second deadline stops it.
    assert caught.value.code == "timed_out"
    assert 0.5 <= elapsed < 5.0
    assert stream.closed is True


def test_response_finishing_inside_the_deadline_succeeds_and_closes() -> None:
    streams: list[_Chunks] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.dumps(
            _key() if request.url.path == "/api/v1/key" else _models()
        ).encode()
        streams.append(_Chunks([payload[:10], payload[10:]], delay=0.01))
        return httpx.Response(200, stream=streams[-1])

    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert reviewer.probe().structured_outputs is True
    finally:
        reviewer.close()
    assert [stream.closed for stream in streams] == [True, True]


@pytest.mark.parametrize("call", ("probe", "run"))
def test_lookups_spend_the_same_deadline_as_the_completion(
    call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time)
    )
    monkeypatch.setattr(module, "admit_cover_review_request", lambda *_args: _Request())
    timeouts: dict[str, dict[str, float]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts[request.url.path] = request.extensions["timeout"]
        if request.url.path == "/api/v1/key":
            now[0] += 4.0
            return httpx.Response(200, json=_key())
        if request.url.path == "/api/v1/models":
            now[0] += 3.0
            return httpx.Response(200, json=_models())
        return httpx.Response(
            200,
            json={
                "model": "example/vision",
                "choices": [{"message": {"content": "{}"}}],
            },
        )

    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        timeout_seconds=10.0,
        transport=httpx.MockTransport(handler),
    )
    try:
        if call == "probe":
            reviewer.probe()
        else:
            reviewer.run(
                openrouter_cover_profile("example/vision"), b"i", b"{}", b"{}", ()
            )
        assert timeouts["/api/v1/key"]["read"] == 10.0
        assert timeouts["/api/v1/models"]["read"] == 6.0
        if call == "run":
            assert set(timeouts["/api/v1/chat/completions"].values()) == {3.0}
        timeouts.clear()

        def slow_key(request: httpx.Request) -> httpx.Response:
            timeouts[request.url.path] = request.extensions["timeout"]
            now[0] += 10.0
            return httpx.Response(200, json=_key())

        reviewer._client._transport = httpx.MockTransport(slow_key)
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.probe()
        assert caught.value.code == "timed_out"
        assert list(timeouts) == ["/api/v1/key"]
    finally:
        reviewer.close()


def test_transport_error_after_the_deadline_is_timed_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.05)
        raise httpx.ReadTimeout("synthetic", request=request)

    # The same transport error is unreachable inside the budget, timed out past it.
    for timeout_seconds, expected in ((10.0, "unreachable"), (0.005, "timed_out")):
        reviewer = OpenRouterReviewer(
            "secret",
            "example/vision",
            timeout_seconds=timeout_seconds,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(OpenRouterReviewerError) as caught:
                reviewer.probe()
        finally:
            reviewer.close()
        assert caught.value.code == expected


def test_repeated_matches_keep_one_entry_and_are_unavailable() -> None:
    padded = _ENTRY[:-1] + ',"description":"' + "x" * 60_000 + '"}'
    body = ('{"data":[' + ",".join([padded] * 40) + "]}").encode()
    catalogue = module._Catalogue("example/vision")
    for start in range(0, len(body), 4_096):
        catalogue.feed(body[start : start + 4_096])
        # However many repeats arrive, at most the first match is retained.
        held = [value for value in vars(catalogue).values() if type(value) is dict]
        assert len(held) <= 1
        assert not any(type(value) is list for value in vars(catalogue).values())
    assert catalogue.finish() is None
    assert _probe_catalogue(body) == "model_unavailable"
    # Structure is still checked first: a broken tail outranks the repeat.
    assert _probe_catalogue(body[:-1]) == "malformed_response"


@pytest.mark.parametrize("call", ("probe", "run"))
def test_stream_ending_past_the_deadline_times_out(
    call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(
        module, "time", SimpleNamespace(monotonic=lambda: now[0], time=time.time)
    )
    monkeypatch.setattr(module, "admit_cover_review_request", lambda *_args: _Request())
    late = "/api/v1/models" if call == "probe" else "/api/v1/chat/completions"

    def late_end(body: bytes) -> Iterator[bytes]:
        yield body
        now[0] += 11.0  # Every byte arrived in time; the end of stream did not.

    def handler(request: httpx.Request) -> httpx.Response:
        payload = (
            _key()
            if request.url.path == "/api/v1/key"
            else _models()
            if request.url.path == "/api/v1/models"
            else {
                "model": "example/vision",
                "choices": [{"message": {"content": "{}"}}],
            }
        )
        body = json.dumps(payload).encode()
        if request.url.path != late:
            return httpx.Response(200, content=body)
        return httpx.Response(200, stream=_Lazy(late_end(body)))

    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        timeout_seconds=10.0,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            if call == "probe":
                reviewer.probe()
            else:
                reviewer.run(
                    openrouter_cover_profile("example/vision"), b"i", b"{}", b"{}", ()
                )
    finally:
        reviewer.close()
    assert caught.value.code == "timed_out"


class _Lazy(httpx.SyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self.chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks


_CANARY = "private provider canary"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    (
        (
            404,
            {"error": {"message": "No endpoints found matching your data policy"}},
            "data_policy_refused",
        ),
        (
            404,
            {
                "error": {
                    "message": _CANARY,
                    "metadata": {
                        "raw": "NO ENDPOINTS FOUND MATCHING YOUR DATA POLICY " + _CANARY
                    },
                }
            },
            "data_policy_refused",
        ),
        (404, None, "model_unavailable"),
        (
            404,
            {"error": {"message": "No endpoints found for " + _CANARY}},
            "model_unavailable",
        ),
        (
            400,
            {"error": {"message": "Too many images: " + _CANARY}},
            "image_limit_exceeded",
        ),
        (400, {"error": {"message": _CANARY}}, "transport"),
        (400, {"error": {"message": ["too many images"]}}, "transport"),
        (400, {"error": "too many images"}, "transport"),
        (
            400,
            {"error": {"message": "x", "metadata": {"raw": 1}, "z": "too many images"}},
            "transport",
        ),
        (400, b"too many images", "transport"),
        # Misshapen metadata is ignored; the message alone still matches.
        *(
            (
                400,
                {"error": {"message": "Too many images", "metadata": metadata}},
                "image_limit_exceeded",
            )
            for metadata in (None, "raw", [1], {"raw": 1}, {"raw": None})
        ),
        *(
            (
                404,
                {
                    "error": {
                        "message": "No endpoints found matching your data policy",
                        "metadata": metadata,
                    }
                },
                "data_policy_refused",
            )
            for metadata in (None, 7, {"raw": ["x"]})
        ),
        (400, b'{"error":{"message":"too many images \xff"}}', "transport"),
        (
            404,
            {
                "error": {
                    "message": "No endpoints found matching your data policy",
                    "metadata": {"raw": "x" * module._MAX_ERROR_RESPONSE},
                }
            },
            "model_unavailable",
        ),
        (500, {"error": {"message": "too many images"}}, "transport"),
    ),
)
def test_documented_refusal_bodies_are_recognised_without_echo(
    status: int,
    body: object,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = (
        b""
        if body is None
        else body
        if isinstance(body, bytes)
        else json.dumps(body).encode()
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json=_key())
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=_models())
        return httpx.Response(status, content=content)

    monkeypatch.setattr(module, "admit_cover_review_request", lambda *_args: _Request())
    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(OpenRouterReviewerError) as caught:
            reviewer.run(
                openrouter_cover_profile("example/vision"), b"i", b"{}", b"{}", ()
            )
    finally:
        reviewer.close()
    assert caught.value.code == expected
    assert caught.value.args == (expected,)
    assert caught.value.__cause__ is None
    assert _CANARY not in repr(caught.value)


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
        assert body["provider"] == {
            "require_parameters": True,
            "data_collection": "deny",
        }
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
            openrouter_cover_profile("example/vision"),
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


@pytest.mark.parametrize("pricing_available", ["full", "omitted_request", "absent"])
@pytest.mark.parametrize("response_kind", ["valid", "wrong_model", "duplicate"])
def test_description_text_model_needs_pricing_without_changing_cover_capability(
    pricing_available: str,
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
                                        **(
                                            {"request": "0"}
                                            if pricing_available == "full"
                                            else {}
                                        ),
                                    }
                                }
                                if pricing_available != "absent"
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
        if pricing_available == "absent":
            with pytest.raises(OpenRouterReviewerError, match="model_unavailable"):
                reviewer.probe_description()
            assert posted == []
            return
        status = reviewer.probe_description()
        assert status.prompt_price == "0.000001" and status.observed_at is not None
        assert status.request_price == ("0" if pricing_available == "full" else None)
        assert posted == []
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
        assert len(posted) == 1
        assert posted[0]["provider"] == {
            "require_parameters": True,
            "allow_fallbacks": False,
        }
        assert posted[0]["model"] == "example/text"
        assert "image_url" not in json.dumps(posted)
        assert "synthetic-secret" not in json.dumps(posted)
    finally:
        reviewer.close()


@pytest.mark.parametrize(
    "model",
    (
        "mistralai/ministral-3b-2512",
        "openai/gpt-5.6-luna",
        "mistralai/mistral-small-2603",
    ),
)
@pytest.mark.parametrize("result_kind", ("valid", "invented", "wrong_model", "fenced"))
def test_real_cover_producer_admission_http_and_strict_observer(model, result_kind):
    from mechanical_librarian.application.cover_observation import (
        OUTPUT_SCHEMA_VERSION,
        TAXONOMY,
        observe_cover_packet,
    )
    from tests.unit.test_cover_packets import _packet_case

    packet, images = _packet_case(2)
    posted = []

    def handler(request):
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json=_key())
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=_models(model))
        body = json.loads(request.content)
        posted.append(body)
        assert body["model"] == model
        assert body["max_tokens"] == 8192
        assert body["provider"] == {
            "require_parameters": True,
            "data_collection": "deny",
        }
        assert "reasoning" not in body
        result = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "observations": [
                {
                    "candidate_reference": row["reference"],
                    **{name: values[0][0] for name, values in TAXONOMY},
                }
                for row in json.loads(packet.document)["candidates"]
            ],
        }
        if result_kind == "invented":
            result["observations"][0]["candidate_reference"] = "cover_ref_" + "0" * 64
        content = json.dumps(result)
        if result_kind == "fenced":
            content = "```json\n" + content + "\n```"
        return httpx.Response(
            200,
            json={
                "model": "example/other" if result_kind == "wrong_model" else model,
                "choices": [{"message": {"content": content}}],
            },
        )

    reviewer = OpenRouterReviewer(
        "synthetic-secret", model, transport=httpx.MockTransport(handler)
    )
    try:
        if result_kind == "valid":
            report = observe_cover_packet(
                openrouter_cover_profile(model),
                packet.document.encode(),
                images,
                reviewer.run,
            )
            assert len(json.loads(report.document)["observations"]) == 2
        else:
            with pytest.raises((ValueError, OpenRouterReviewerError)):
                observe_cover_packet(
                    openrouter_cover_profile(model),
                    packet.document.encode(),
                    images,
                    reviewer.run,
                )
    finally:
        reviewer.close()
    assert len(posted) == 1


def test_description_profile_bytes_remain_independent_of_cover_posture():
    assert openrouter_profile("example/text") == (
        b'{"engine":"openrouter","model":"example/text","schema_version":"openrouter-reviewer-profile-v1"}'
    )
    assert openrouter_cover_profile("example/text") != openrouter_profile(
        "example/text"
    )


@pytest.mark.parametrize(
    "value",
    ["0", "0.000002", None, "NaN", "Infinity", "-1", "1000001", {"usd": "1"}, 0],
)
def test_cover_prices_are_individually_observed_without_capability_rejection(value):
    document = _models()
    document["data"][0]["pricing"] = {
        "prompt": "0.000001",
        "completion": value,
        "image": "0.01",
    }
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(
            200, json=_key() if request.url.path == "/api/v1/key" else document
        )

    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(respond)
    )
    try:
        status = reviewer.probe()
    finally:
        reviewer.close()
    assert status.prompt_price == "0.000001"
    assert status.image_price == "0.01"
    assert status.request_price is None
    assert status.completion_price == (value if value in ("0", "0.000002") else None)
    assert status.observed_at is not None
    assert requests == [("GET", "/api/v1/key"), ("GET", "/api/v1/models")]


@pytest.mark.parametrize("field", ["prompt", "completion", "request"])
@pytest.mark.parametrize(
    "value", [None, 0, {}, "", "NaN", "Infinity", "-1", "1000001", "0" * 41]
)
def test_description_rejects_present_invalid_prices(field, value):
    document = _models()
    document["data"][0]["pricing"] = {"prompt": "0", "completion": "0", "request": "0"}
    document["data"][0]["pricing"][field] = value
    requests = []

    def respond(request):
        requests.append(request.method)
        return httpx.Response(
            200, json=_key() if request.url.path == "/api/v1/key" else document
        )

    reviewer = OpenRouterReviewer(
        "secret", "example/vision", transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(OpenRouterReviewerError, match="model_unavailable"):
            reviewer.probe_description()
    finally:
        reviewer.close()
    assert requests == ["GET", "GET"]


@pytest.mark.parametrize("field", ["prompt", "completion"])
def test_description_requires_both_token_prices(field):
    document = _models()
    document["data"][0]["pricing"] = {"prompt": "0", "completion": "0"}
    del document["data"][0]["pricing"][field]
    reviewer = OpenRouterReviewer(
        "secret",
        "example/vision",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=_key() if request.url.path == "/api/v1/key" else document
            )
        ),
    )
    try:
        with pytest.raises(OpenRouterReviewerError, match="model_unavailable"):
            reviewer.probe_description()
    finally:
        reviewer.close()
