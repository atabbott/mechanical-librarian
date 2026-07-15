"""Provider-scoped private-file credentials and bounded Hardcover client."""

from __future__ import annotations

import httpx

from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.private_file import read_private_secret


class ProviderSecretUnavailable(Exception):
    """Safe fixed failure for unavailable provider credential material."""


class PrivateFileProviderCredentials:
    def hardcover_client(
        self,
        handle: SecretHandle,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> httpx.AsyncClient:
        if (
            type(handle) is not SecretHandle
            or handle.scope is not SecretScope.METADATA_PROVIDER
        ):
            raise ProviderSecretUnavailable()
        material = read_private_secret(handle.reference)
        if material is None:
            raise ProviderSecretUnavailable()
        return httpx.AsyncClient(
            auth=_HardcoverAuthorizationAuth(material.value),
            cookies=None,
            follow_redirects=False,
            http2=False,
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=0,
                keepalive_expiry=0.0,
            ),
            timeout=httpx.Timeout(5.0, connect=5.0, read=5.0, write=5.0, pool=5.0),
            transport=transport,
            trust_env=False,
            verify=True,
        )


class _HardcoverAuthorizationAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request):
        if (
            request.method == "POST"
            and request.url.scheme == "https"
            and request.url.port in {None, 443}
            and request.url.path == "/v1/graphql"
            and not request.url.query
            and not request.url.fragment
            and request.headers.get("host") == "api.hardcover.app"
            and request.extensions.get("sni_hostname") == "api.hardcover.app"
        ):
            request.headers["authorization"] = f"Bearer {self._token}"
        yield request


__all__ = ["PrivateFileProviderCredentials", "ProviderSecretUnavailable"]
