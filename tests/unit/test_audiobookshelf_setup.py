from __future__ import annotations

import ssl

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_setup import (
    AudiobookshelfSetupProbe,
    AudiobookshelfSetupProbeError,
)


def _probe(
    handler: httpx.MockTransport, *, maximum_bytes: int = 64
) -> AudiobookshelfSetupProbe:
    return AudiobookshelfSetupProbe(
        lambda endpoint, _key: httpx.Client(
            base_url=endpoint,
            transport=handler,
            follow_redirects=False,
            trust_env=False,
        ),
        maximum_bytes=maximum_bytes,
        timeout_seconds=2,
    )


def test_setup_probe_performs_one_bounded_libraries_read() -> None:
    requests: list[httpx.Request] = []

    def response(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"libraries": []},
        )

    _probe(httpx.MockTransport(response))("https://abs.invalid", "secret-value")

    assert len(requests) == 1
    assert requests[0].url == httpx.URL("https://abs.invalid/api/libraries")
    assert requests[0].headers["accept-encoding"] == "identity"


def test_setup_probe_carries_only_bounded_service_path_evidence() -> None:
    response = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "libraries": [
                    {
                        "id": "books",
                        "name": "Books",
                        "mediaType": "book",
                        "folders": [{"fullPath": "/library/books"}],
                    }
                ]
            },
        )
    )
    candidate = _probe(response, maximum_bytes=512).discover(
        "https://abs.invalid", "secret-value"
    )[0]
    assert candidate.service_paths == ("/library/books",)
    assert "service_paths" not in candidate.model_dump()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(401), "unauthorized"),
        (httpx.Response(403), "unauthorized"),
        (
            httpx.Response(302, headers={"location": "https://other.invalid"}),
            "redirect",
        ),
        (httpx.Response(500), "unavailable"),
        (
            httpx.Response(200, headers={"content-type": "text/html"}, text="no"),
            "malformed",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"{" + b"x" * 80 + b"}",
            ),
            "oversize",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"not-json",
            ),
            "malformed",
        ),
    ],
)
def test_setup_probe_returns_only_closed_failure_codes(
    response: httpx.Response, expected: str
) -> None:
    probe = _probe(httpx.MockTransport(lambda _request: response))

    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        probe("https://abs.invalid", "secret-value")

    assert error.value.code == expected
    assert "secret-value" not in str(error.value)


def test_setup_probe_classifies_timeout_without_exposing_transport_detail() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private transport detail", request=request)

    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        _probe(httpx.MockTransport(timeout))("https://abs.invalid", "secret-value")

    assert error.value.code == "timeout"
    assert "private transport detail" not in str(error.value)


def test_setup_probe_classifies_tls_failure_without_exposing_certificate_detail() -> (
    None
):
    def tls_failure(request: httpx.Request) -> httpx.Response:
        certificate = ssl.SSLCertVerificationError("private certificate detail")
        raise httpx.ConnectError(
            "private transport detail", request=request
        ) from certificate

    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        _probe(httpx.MockTransport(tls_failure))("https://abs.invalid", "secret-value")

    assert error.value.code == "tls"
    assert "private certificate detail" not in str(error.value)
