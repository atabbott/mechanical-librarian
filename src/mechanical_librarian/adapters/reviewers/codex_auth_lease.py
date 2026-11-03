"""Process-lifetime Codex sign-in lease: one owner of the configured auth store."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from threading import Lock
from typing import TYPE_CHECKING, cast

from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthError,
)
from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    ContainedReviewerProcessError,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
)
from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState

if TYPE_CHECKING:
    from mechanical_librarian.adapters.reviewers.codex_device_auth import (
        CodexDeviceAuthManager,
    )


class UnavailableCodexAuth:
    def activate_lease(self) -> None:
        return None

    def _state(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("failed", failure="permission_or_profile")

    start = status = clear = current_credential_status = _state
    recheck_credential_status = _state

    def cancel(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("ready")

    def passive_status(self) -> CodexDeviceAuthState:
        return CodexDeviceAuthState("ready")

    def close(self) -> None:
        return None


class LazyCodexAuth:
    """Do not touch the Codex profile or staging tree until Codex is requested."""

    def __init__(
        self,
        factory: Callable[
            [Callable[[], bool], Callable[[], AbstractContextManager[None]]],
            CodexDeviceAuthManager,
        ],
        coordinator: CodexAuthLeaseCoordinator,
        cleanup_factory: Callable[
            [Callable[[], bool], Callable[[], AbstractContextManager[None]]],
            CodexDeviceAuthManager,
        ]
        | None = None,
    ) -> None:
        self._factory = factory
        self._cleanup_factory = cleanup_factory or factory
        self._coordinator = coordinator
        self._lock = Lock()
        self._manager: CodexDeviceAuthManager | None = None
        self._lease: int | None = None
        self._retired = False

    def activate_lease(self) -> None:
        with self._lock:
            if self._retired:
                raise CodexDeviceAuthError("lease")
        self._lease = self._coordinator.activate(self)

    def _lease_current(self) -> bool:
        lease = self._lease
        return lease is not None and self._coordinator.current(self, lease)

    def bind(self, loader: Callable[[], tuple[str, bytes]]) -> tuple[str, bytes]:
        """Capture immutable review identity under the current short lease."""
        if not self._lease_current():
            self.activate_lease()
        lease = cast("int", self._lease)
        with self._coordinator.guard(self, lease):
            return loader()

    @contextmanager
    def review_admission(self) -> Iterator[None]:
        """Revalidate the captured generation after taking global activity."""
        lease = cast("int", self._lease)
        with self._coordinator.guard(self, lease):
            yield

    def _get(self, *, cleanup: bool = False) -> CodexDeviceAuthManager:
        if not self._lease_current():
            self.activate_lease()
        with self._lock:
            if self._manager is None:
                factory = self._cleanup_factory if cleanup else self._factory
                self._manager = factory(
                    self._lease_current,
                    lambda: self._coordinator.guard(self, cast("int", self._lease)),
                )
            return self._manager

    def _state(
        self, operation: str, manager: CodexDeviceAuthManager | None = None
    ) -> CodexDeviceAuthState:
        try:
            current = self._get() if manager is None else manager
            return cast("CodexDeviceAuthState", getattr(current, operation)())
        except (
            OSError,
            ValueError,
            CodexDeviceAuthError,
            ReviewerAccessApplicationError,
            ContainedReviewerProcessError,
        ):
            return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def start(self) -> CodexDeviceAuthState:
        return self._state("start")

    def status(self) -> CodexDeviceAuthState:
        return self._state("status")

    def passive_status(self) -> CodexDeviceAuthState:
        """Return an existing flow without constructing Codex authority."""
        with self._lock:
            manager = self._manager
        return (
            CodexDeviceAuthState("ready")
            if manager is None
            else self._state("status", manager)
        )

    def cancel(self) -> CodexDeviceAuthState:
        # Cleanup never constructs authority or activates another generation.
        with self._lock:
            manager = self._manager
        if manager is None:
            return CodexDeviceAuthState("ready")
        try:
            return manager.cancel()
        except (OSError, ValueError, CodexDeviceAuthError):
            return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def clear(self) -> CodexDeviceAuthState:
        return self._get(cleanup=True).clear()

    def current_credential_status(self) -> CodexDeviceAuthState:
        return self._state("current_credential_status")

    def recheck_credential_status(self) -> CodexDeviceAuthState:
        return self._state("recheck_credential_status")

    def close(self) -> None:
        self._mark_retired()
        self._drain()
        self._coordinator.release(self)

    def _mark_retired(self) -> None:
        with self._lock:
            self._retired = True

    def _drain(self) -> None:
        with self._lock:
            manager = self._manager
        if manager is not None:
            manager.close()


class CodexAuthLeaseCoordinator:
    """Serialize one process-lifetime owner of a configured auth store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._transition = Lock()
        self._activity = Lock()
        self._owner: LazyCodexAuth | None = None
        self._lease = 0
        self._quarantined = False

    def activate(self, owner: LazyCodexAuth) -> int:
        with self._transition:
            with self._lock:
                if self._quarantined:
                    raise CodexDeviceAuthError("process")
                if self._owner is owner:
                    return self._lease
                predecessor = self._owner
                if predecessor is not None:
                    predecessor._mark_retired()
            if predecessor is not None:
                try:
                    predecessor._drain()
                except Exception:
                    with self._lock:
                        if self._owner is predecessor:
                            self._owner = None
                            self._lease += 1
                    raise
            # A device flow can be pre-cancelled without this gate. A review
            # cannot, so handoff waits for its complete broker tree to reap
            # before changing lease authority.
            with self._activity:
                with self._lock:
                    if self._quarantined:
                        raise CodexDeviceAuthError("process")
                    if self._owner not in {None, predecessor}:
                        raise CodexDeviceAuthError("lease")
                    self._lease += 1
                    self._owner = owner
                    return self._lease

    def current(self, owner: LazyCodexAuth, lease: int) -> bool:
        with self._lock:
            return (
                not self._quarantined and self._owner is owner and self._lease == lease
            )

    @contextmanager
    def activity(self) -> Iterator[None]:
        """Admit exactly one Codex auth, recheck, or review process tree."""
        with self._activity:
            with self._lock:
                if self._quarantined:
                    raise CodexDeviceAuthError("process")
            yield

    @contextmanager
    def guard(self, owner: LazyCodexAuth, lease: int) -> Iterator[None]:
        with self._lock:
            if self._quarantined or self._owner is not owner or self._lease != lease:
                raise CodexDeviceAuthError("lease")
            yield

    def quarantine(self) -> None:
        """Permanently reject Codex admission after an unproved process teardown."""
        with self._lock:
            if not self._quarantined:
                self._quarantined = True
                self._lease += 1

    def release(self, owner: LazyCodexAuth) -> None:
        with self._transition, self._activity, self._lock:
            if self._owner is owner:
                self._owner = None
                self._lease += 1

    def close(self) -> None:
        with self._transition:
            with self._lock:
                owner = self._owner
                if owner is not None:
                    owner._mark_retired()
            if owner is not None:
                owner._drain()
            with self._activity, self._lock:
                if self._owner is owner:
                    self._owner = None
                    self._lease += 1
