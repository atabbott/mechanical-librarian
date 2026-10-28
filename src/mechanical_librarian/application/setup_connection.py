"""Setup-owned bounded proof that an Audiobookshelf endpoint is readable."""

from __future__ import annotations

from typing import Protocol

from mechanical_librarian.domain.setup_state import (
    SetupConnection,
    SetupStateDocument,
    SetupStateError,
)
from mechanical_librarian.domain.validation import credential, http_origin


class SetupConnectionProbe(Protocol):
    def __call__(self, endpoint: str, key: str) -> None: ...


class SetupConnectionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SetupConnectionVerification:
    def __init__(self, probe: SetupConnectionProbe) -> None:
        self._probe = probe

    def verify(
        self, name: str, endpoint: str, key: str
    ) -> tuple[SetupConnection, dict[str, str]]:
        if (
            type(endpoint) is not str
            or not endpoint.startswith(("https://", "http://"))
            # Only scheme, host and port: a path, userinfo, query or fragment
            # is refused here, before any probe, rather than read as a failure.
            or not http_origin(endpoint)
            or type(key) is not str
            or not credential(key)
        ):
            raise SetupConnectionError("invalid_endpoint")
        try:
            self._probe(endpoint, key)
        except Exception as error:
            code = getattr(error, "code", "unavailable")
            if code in {"authentication", "authorization"}:
                code = "unauthorized"
            if code not in {
                "unauthorized",
                "name_resolution",
                "refused",
                "tls",
                "timeout",
                "unavailable",
                "malformed",
                "oversize",
                "redirect",
            }:
                code = "unavailable"
            raise SetupConnectionError(code) from None
        try:
            connection = SetupConnection(name=name, endpoint=endpoint)
        except ValueError:
            raise SetupConnectionError("invalid_endpoint") from None
        return connection, {f"connection.{name}.key": key}


class SetupStateRepository(Protocol):
    def read(self, bootstrap_verifier: str) -> SetupStateDocument: ...

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        bootstrap_verifier: str,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument: ...


class SetupConnectionOperation:
    """Verify first, then record connection and key in one local transaction."""

    def __init__(
        self,
        verification: SetupConnectionVerification,
        repository: SetupStateRepository,
        bootstrap_verifier: str,
    ) -> None:
        self._verification = verification
        self._repository = repository
        self._bootstrap_verifier = bootstrap_verifier

    def connect(
        self, expected_revision: int, name: str, endpoint: str, key: str
    ) -> SetupStateDocument:
        try:
            current = self._repository.read(self._bootstrap_verifier)
            if (
                current.revision != expected_revision
                or current.next_step() != "connect"
            ):
                raise SetupConnectionError("stale")
            connection, secret_delta = self._verification.verify(name, endpoint, key)
            successor = SetupStateDocument.model_validate(
                {
                    **current.model_dump(),
                    "revision": current.revision + 1,
                    "completed_steps": ["claim", "connect"],
                    "connection": connection.model_dump(),
                }
            )
            values: dict[str, str | None] = {
                field_id: value for field_id, value in secret_delta.items()
            }
            return self._repository.write(
                successor,
                current.revision,
                self._bootstrap_verifier,
                values,
            )
        except SetupConnectionError:
            raise
        except SetupStateError:
            raise SetupConnectionError("stale") from None


__all__ = [
    "SetupConnectionError",
    "SetupConnectionOperation",
    "SetupConnectionVerification",
    "SetupStateRepository",
]
