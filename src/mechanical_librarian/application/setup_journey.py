"""Application operations for the closed six-step first-run ledger."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal, Protocol

from mechanical_librarian.domain.owner_settings import LibrarySettings, ProviderSettings
from mechanical_librarian.domain.setup_state import (
    SetupLibraryCandidate,
    SetupMountResult,
    SetupStateDocument,
    SetupStateError,
    SetupStep,
)


class SetupJourneyError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SetupJourneyRepository(Protocol):
    def read(self, bootstrap_verifier: str) -> SetupStateDocument: ...

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        bootstrap_verifier: str,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument: ...

    def promote(
        self,
        bootstrap_verifier: str,
        owner_verifier: str,
        expected_revision: int | None = None,
    ) -> None: ...


class SetupLibraryDiscovery(Protocol):
    def __call__(
        self, document: SetupStateDocument
    ) -> tuple[SetupLibraryCandidate, ...]: ...


class SetupJourneyOperation:
    def __init__(
        self,
        repository: SetupJourneyRepository,
        bootstrap_verifier: str,
        discover_libraries: SetupLibraryDiscovery,
        mount_names: dict[str, tuple[str, ...]] | Callable[[str], tuple[str, ...]],
    ) -> None:
        self._repository = repository
        self._bootstrap_verifier = bootstrap_verifier
        self._discover_libraries = discover_libraries
        self._mount_names = (
            (lambda path: mount_names.get(path, ()))
            if isinstance(mount_names, dict)
            else mount_names
        )

    def providers(
        self,
        expected_revision: int,
        providers: Sequence[ProviderSettings],
        secret_delta: dict[str, str],
    ) -> SetupStateDocument:
        current = self._current(expected_revision, "providers")
        canonical = sorted(providers, key=lambda value: value.name)
        successor = SetupStateDocument.model_validate(
            {
                **current.model_dump(),
                "revision": current.revision + 1,
                "completed_steps": ["claim", "connect", "providers"],
                "providers": [value.model_dump() for value in canonical],
            }
        )
        values: dict[str, str | None] = {
            field_id: value for field_id, value in secret_delta.items()
        }
        return self._write(successor, current.revision, values)

    def discover(self, expected_revision: int) -> tuple[SetupLibraryCandidate, ...]:
        current = self._current(expected_revision)
        if current.next_step() not in {"libraries", "mounts"}:
            raise SetupJourneyError("stale")
        try:
            return self._discover_libraries(current)
        except SetupJourneyError:
            raise
        except Exception as error:
            code = getattr(error, "code", "unavailable")
            if code not in {
                "unauthorized",
                "tls",
                "timeout",
                "unavailable",
                "malformed",
                "oversize",
                "redirect",
            }:
                code = "unavailable"
            raise SetupJourneyError(code) from None

    def libraries(
        self, expected_revision: int, libraries: Sequence[LibrarySettings]
    ) -> SetupStateDocument:
        current = self._current(expected_revision, "libraries")
        connection = current.connection
        if connection is None:
            raise SetupJourneyError("stale")
        available = {
            value.library_id: value for value in self.discover(expected_revision)
        }
        if (
            not libraries
            or len({value.name for value in libraries}) != len(libraries)
            or any(
                value.connection != connection.name
                or value.library_id not in available
                or available[value.library_id].media_kind != "book"
                or value.provider_connections != sorted(set(value.provider_connections))
                or not set(value.provider_connections).issubset(
                    {
                        provider.name
                        for provider in current.providers
                        if provider.enabled
                    }
                )
                for value in libraries
            )
        ):
            raise SetupJourneyError("invalid_libraries")
        canonical = sorted(libraries, key=lambda value: value.name)
        successor = SetupStateDocument.model_validate(
            {
                **current.model_dump(),
                "revision": current.revision + 1,
                "completed_steps": [
                    "claim",
                    "connect",
                    "providers",
                    "libraries",
                ],
                "libraries": [value.model_dump() for value in canonical],
            }
        )
        return self._write(successor, current.revision)

    def mounts(
        self, expected_revision: int, service_paths: dict[str, str | None]
    ) -> SetupStateDocument:
        current = self._current(expected_revision, "mounts")
        if set(service_paths) != {value.name for value in current.libraries}:
            raise SetupJourneyError("invalid_mounts")
        libraries: list[LibrarySettings] = []
        results: list[SetupMountResult] = []
        discovered = {
            value.library_id: value for value in self._discover_libraries(current)
        }
        for library in current.libraries:
            supplied = service_paths[library.name]
            evidence = discovered.get(library.library_id)
            paths = (
                (supplied,)
                if supplied is not None
                else ()
                if evidence is None
                else evidence.service_paths
            )
            matches = tuple(
                sorted({name for path in paths for name in self._mount_names(path)})
            )
            state: Literal["matched", "ambiguous", "missing", "unavailable"] = (
                "unavailable"
                if not paths
                else "matched"
                if len(matches) == 1
                else "ambiguous"
                if len(matches) > 1
                else "missing"
            )
            libraries.append(
                library.model_copy(
                    update={"mount": matches[0] if state == "matched" else None}
                )
            )
            results.append(SetupMountResult(library=library.name, state=state))
        successor = SetupStateDocument.model_validate(
            {
                **current.model_dump(),
                "revision": current.revision + 1,
                "completed_steps": [
                    "claim",
                    "connect",
                    "providers",
                    "libraries",
                ]
                + (
                    ["mounts"]
                    if all(
                        library.media_kind != "book" or result.state == "matched"
                        for library, result in zip(libraries, results, strict=True)
                    )
                    else []
                ),
                "libraries": [value.model_dump() for value in libraries],
                "mounts": [value.model_dump() for value in results],
            }
        )
        return self._write(successor, current.revision)

    def change(self, expected_revision: int, step: SetupStep) -> SetupStateDocument:
        if step in {"claim", "kick_off"}:
            raise SetupJourneyError("invalid_change")
        current = self._current(expected_revision)
        changed = current.invalidate_after(step).model_copy(
            update={"revision": current.revision + 1}
        )
        return self._write(changed, current.revision)

    def kick_off(self, expected_revision: int) -> None:
        current = self.validate_kick_off(expected_revision)
        try:
            self._repository.promote(
                self._bootstrap_verifier,
                self._bootstrap_verifier,
                current.revision,
            )
        except SetupStateError:
            raise SetupJourneyError("stale") from None

    def validate_kick_off(self, expected_revision: int) -> SetupStateDocument:
        """Validate the exact candidate without committing setup authority."""
        try:
            current = self._current(expected_revision, "kick_off")
        except SetupJourneyError as error:
            raise error from None
        mount_states = {value.library: value.state for value in current.mounts}
        if any(
            library.media_kind == "book" and mount_states.get(library.name) != "matched"
            for library in current.libraries
        ):
            raise SetupJourneyError("mounts_unready")
        return current

    def _current(
        self, expected_revision: int, expected_step: SetupStep | None = None
    ) -> SetupStateDocument:
        try:
            current = self._repository.read(self._bootstrap_verifier)
        except SetupStateError:
            raise SetupJourneyError("stale") from None
        if current.revision != expected_revision or (
            expected_step is not None and current.next_step() != expected_step
        ):
            raise SetupJourneyError("stale")
        return current

    def _write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        secret_delta: dict[str, str | None] | None = None,
    ) -> SetupStateDocument:
        try:
            return self._repository.write(
                document,
                expected_revision,
                self._bootstrap_verifier,
                secret_delta,
            )
        except SetupStateError:
            raise SetupJourneyError("stale") from None


__all__ = [
    "SetupJourneyError",
    "SetupJourneyOperation",
    "SetupJourneyRepository",
    "SetupLibraryDiscovery",
]
