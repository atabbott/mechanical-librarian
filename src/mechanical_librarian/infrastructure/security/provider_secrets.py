"""Provider-scoped private-file credentials and bounded Hardcover client."""

from __future__ import annotations

import httpx
import os
import stat
from pathlib import Path

from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.private_file import (
    BoundaryWhitespacePolicy,
    read_private_secret,
)


class ProviderSecretUnavailable(Exception):
    """Safe fixed failure for unavailable provider credential material."""


class PrivateFileProviderCredentials:
    def hardcover_identity_token(self, handle: SecretHandle) -> bytes:
        """Read one F09f token through a stable owner-only descriptor chain."""

        if (
            type(handle) is not SecretHandle
            or handle.scope is not SecretScope.METADATA_PROVIDER
        ):
            raise ProviderSecretUnavailable() from None
        parent_descriptor: int | None = None
        descriptor: int | None = None
        try:
            path = Path(handle.reference.value)
            if not path.is_absolute() or path.name in {"", ".", ".."}:
                raise ProviderSecretUnavailable()
            parent = path.parent.resolve(strict=True)
            parent_descriptor = os.open(
                parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent_descriptor,
            )
            before = os.fstat(descriptor)
            if not _safe_identity_secret(before):
                raise ProviderSecretUnavailable()
            payload = os.read(descriptor, before.st_size + 1)
            after = os.fstat(descriptor)
            if (
                len(payload) != before.st_size
                or _secret_identity(before) != _secret_identity(after)
                or not all(0x21 <= value <= 0x7E for value in payload)
                or payload.lower().startswith(b"bearer")
            ):
                raise ProviderSecretUnavailable()
            return payload
        except ProviderSecretUnavailable:
            raise
        except (OSError, UnicodeError, ValueError):
            raise ProviderSecretUnavailable() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if parent_descriptor is not None:
                os.close(parent_descriptor)

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
        material = read_private_secret(
            handle.reference,
            boundary_whitespace=BoundaryWhitespacePolicy.REJECT,
        )
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


def _safe_identity_secret(information: os.stat_result) -> bool:
    return (
        stat.S_ISREG(information.st_mode)
        and information.st_uid == os.geteuid()
        and information.st_nlink == 1
        and bool(information.st_mode & stat.S_IRUSR)
        and not information.st_mode & 0o077
        and 1 <= information.st_size <= 2_048
    )


def _secret_identity(information: os.stat_result) -> tuple[int, ...]:
    return (
        information.st_dev,
        information.st_ino,
        information.st_mode,
        information.st_uid,
        information.st_nlink,
        information.st_size,
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
