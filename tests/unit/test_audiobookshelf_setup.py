from __future__ import annotations

import ssl
from contextlib import closing

import httpx
import pytest

from mechanical_librarian.adapters.library_services.audiobookshelf_setup import (
    AudiobookshelfSetupProbe,
    AudiobookshelfSetupProbeError,
)
from mechanical_librarian.infrastructure.security.credentials import (
    target_client_token,
)
from tests.support.ports import ReservedPort


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


def test_setup_probe_classifies_a_contextual_certificate_failure_as_tls() -> None:
    """The real stack attaches the certificate error as context, not cause.

    `httpcore` re-raises its own `ConnectError` without `from`, so walking only
    `__cause__` stopped one link short of `ssl.SSLCertVerificationError` and a
    genuine self-signed endpoint reached the owner as the generic unavailable
    recovery instead of the certificate one (#1008).
    """

    def tls_failure(request: httpx.Request) -> httpx.Response:
        try:
            raise ssl.SSLCertVerificationError("private certificate detail")
        except ssl.SSLCertVerificationError:
            # No `from`: the certificate error becomes the implicit context.
            raise httpx.ConnectError(  # noqa: B904 - the shape under test
                "private transport detail", request=request
            )

    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        _probe(httpx.MockTransport(tls_failure))("https://abs.invalid", "secret-value")

    assert error.value.code == "tls"
    assert "private certificate detail" not in str(error.value)


def _real_probe() -> AudiobookshelfSetupProbe:
    return AudiobookshelfSetupProbe(
        lambda endpoint, key: target_client_token(endpoint, key, 5), timeout_seconds=5
    )


def test_setup_probe_classifies_a_refused_connection_from_a_real_socket() -> None:
    # A bound socket that never listens refuses every connection to its port.
    with closing(ReservedPort()) as port:
        with pytest.raises(AudiobookshelfSetupProbeError) as error:
            _real_probe()(f"http://127.0.0.1:{port.number}", "secret-value")

    assert error.value.code == "refused"
    assert error.value.__cause__ is None and error.value.__suppress_context__


def test_setup_probe_classifies_an_unresolvable_name_from_a_real_lookup() -> None:
    # `.invalid` is reserved and never resolves (RFC 6761).
    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        _real_probe()("http://audiobookshelf.invalid:13378", "secret-value")

    assert error.value.code == "name_resolution"
    assert "audiobookshelf.invalid" not in str(error.value)


def test_setup_probe_ranks_a_certificate_failure_above_its_connection_error() -> None:
    def tls_failure(request: httpx.Request) -> httpx.Response:
        try:
            try:
                raise ConnectionRefusedError(111, "private refusal")
            except ConnectionRefusedError:
                raise ssl.SSLCertVerificationError("private certificate detail")  # noqa: B904
        except ssl.SSLCertVerificationError:
            raise httpx.ConnectError("private", request=request)  # noqa: B904

    with pytest.raises(AudiobookshelfSetupProbeError) as error:
        _probe(httpx.MockTransport(tls_failure))("https://abs.invalid", "secret-value")

    assert error.value.code == "tls"


def test_setup_probe_reads_only_an_exact_audiobooks_only_switch() -> None:
    def library(identifier: str, settings: object) -> dict[str, object]:
        return {
            "id": identifier,
            "name": identifier,
            "mediaType": "book",
            "settings": settings,
        }

    response = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "libraries": [
                    library("audio", {"audiobooksOnly": True}),
                    library("mixed", {"audiobooksOnly": False}),
                    library("text", {"audiobooksOnly": "true"}),
                    library("bare", None),
                    {"id": "pods", "name": "pods", "mediaType": "podcast"},
                ]
            },
        )
    )
    candidates = _probe(response, maximum_bytes=1024).discover(
        "https://abs.invalid", "secret-value"
    )

    assert [value.audiobooks_only for value in candidates] == [
        True,
        False,
        False,
        False,
        False,
    ]
