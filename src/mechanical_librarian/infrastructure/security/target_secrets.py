"""Scoped file-secret resolution and credential-bearing target HTTP clients."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

import httpx

from mechanical_librarian.domain.inventory import SecretReference, ServiceEndpoint
from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.private_file import (
    PrivateSecretMaterial,
    read_private_secret,
)


_SecretMaterial = PrivateSecretMaterial


class SecretFileUnavailable(Exception):
    """Safe fixed failure for unavailable target credential material."""


class PrivateFileTargetCredentials:
    """Infrastructure-only credential material and target-client construction."""

    def configured(self, reference: SecretReference) -> bool:
        return _read(reference) is not None

    def target_client(
        self,
        handle: SecretHandle,
        endpoint: ServiceEndpoint,
        *,
        connect_timeout_seconds: int = 3,
        read_timeout_seconds: int = 5,
        max_in_flight_requests: int = 2,
    ) -> httpx.Client:
        if (
            type(handle) is not SecretHandle
            or handle.scope is not SecretScope.TARGET_SERVICE
            or type(endpoint) is not ServiceEndpoint
            or type(connect_timeout_seconds) is not int
            or not 1 <= connect_timeout_seconds <= 300
            or type(read_timeout_seconds) is not int
            or not 1 <= read_timeout_seconds <= 3_600
            or type(max_in_flight_requests) is not int
            or not 1 <= max_in_flight_requests <= 2
        ):
            raise SecretFileUnavailable()
        material = _read(handle.reference)
        if material is None:
            raise SecretFileUnavailable()
        return httpx.Client(
            headers={"Accept-Encoding": "identity"},
            auth=_AuthorizeBearerAuth(
                endpoint.value + "/api/authorize", material.value
            ),
            trust_env=False,
            follow_redirects=False,
            verify=True,
            timeout=httpx.Timeout(
                connect=float(connect_timeout_seconds),
                read=float(read_timeout_seconds),
                write=float(connect_timeout_seconds),
                pool=float(min(connect_timeout_seconds, 2)),
            ),
            limits=httpx.Limits(
                max_connections=max_in_flight_requests,
                max_keepalive_connections=0,
                keepalive_expiry=0.0,
            ),
        )

    def inventory_client(
        self,
        handle: SecretHandle,
        endpoint: ServiceEndpoint,
        *,
        connect_timeout_seconds: int = 10,
        read_timeout_seconds: int = 30,
        max_in_flight_requests: int = 1,
    ) -> httpx.Client:
        """Create a bearer client whose credential can reach GET inventory routes only."""

        if (
            type(handle) is not SecretHandle
            or handle.scope is not SecretScope.TARGET_SERVICE
            or type(endpoint) is not ServiceEndpoint
            or not _is_origin(endpoint)
            or not target_endpoint_allowed(endpoint)
            or type(connect_timeout_seconds) is not int
            or not 1 <= connect_timeout_seconds <= 300
            or type(read_timeout_seconds) is not int
            or not 1 <= read_timeout_seconds <= 3_600
            or max_in_flight_requests != 1
        ):
            raise SecretFileUnavailable()
        material = _read(handle.reference)
        if material is None:
            raise SecretFileUnavailable()
        return httpx.Client(
            headers={"Accept-Encoding": "identity"},
            auth=_InventoryBearerAuth(endpoint.value, material.value),
            trust_env=False,
            follow_redirects=False,
            verify=True,
            timeout=httpx.Timeout(
                connect=float(connect_timeout_seconds),
                read=float(read_timeout_seconds),
                write=float(connect_timeout_seconds),
                pool=float(connect_timeout_seconds),
            ),
            limits=httpx.Limits(
                max_connections=max_in_flight_requests,
                max_keepalive_connections=0,
                keepalive_expiry=0.0,
            ),
        )


class _AuthorizeBearerAuth(httpx.Auth):
    def __init__(self, authorize_url: str, token: str) -> None:
        self._authorize_url = httpx.URL(authorize_url)
        self._token = token

    def auth_flow(self, request):
        if request.method == "POST" and request.url == self._authorize_url:
            request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


_UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_INVENTORY_PATH = re.compile(
    rf"^/api/(?:libraries(?:/{_UUID4}/items)?|items/{_UUID4})$"
)


class _InventoryBearerAuth(httpx.Auth):
    def __init__(self, origin: str, token: str) -> None:
        self._origin = httpx.URL(origin)
        self._token = token

    def auth_flow(self, request):
        same_origin = (
            request.url.scheme == self._origin.scheme
            and request.url.host == self._origin.host
            and request.url.port == self._origin.port
        )
        if (
            request.method == "GET"
            and same_origin
            and _INVENTORY_PATH.fullmatch(request.url.path)
        ):
            request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def _is_origin(endpoint: ServiceEndpoint) -> bool:
    parsed = urlsplit(endpoint.value)
    return parsed.path in {"", "/"} and not parsed.query and not parsed.fragment


def target_endpoint_allowed(endpoint: ServiceEndpoint) -> bool:
    """Require HTTPS except literal loopback hosts used for disposable qualification."""

    if type(endpoint) is not ServiceEndpoint:
        return False
    parsed = urlsplit(endpoint.value)
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http" or parsed.hostname is None:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _read(reference: SecretReference) -> _SecretMaterial | None:
    return read_private_secret(reference)


__all__ = [
    "PrivateFileTargetCredentials",
    "SecretFileUnavailable",
    "target_endpoint_allowed",
]
