"""Run one requested taste profile build outside every write transaction.

The preparation runner calls `wake` after each wake. A build is persisted as
`requested` first, its request is counted in the transaction that starts it,
and the model is called with no transaction open (#1372 S9, Q7).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.application.cover_taste_contract import (
    TasteChargeUnknownError,
    TasteContractError,
    admit_taste_build_request,
)
from mechanical_librarian.application.cover_taste_inputs import (
    TasteDecisionSource,
    assemble_taste_inputs,
)
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.domain.cover_taste import (
    CoverTasteError,
    TasteBuildFailure,
    TasteProfile,
)

# The cadence reads the eligible count, so it runs at most once a minute even
# though the runner wakes every second.
_SCHEDULE_SECONDS = 60


class CoverTasteRepository(Protocol):
    def schedule(self, now: int, configured: bool, daily_limit: int) -> None: ...

    def request_retry(
        self, build_id: str, now: int, daily_limit: int
    ) -> str | None: ...

    def pending(self) -> str | None: ...

    def claim(
        self, build_id: str, now: int, model: str, daily_limit: int
    ) -> TasteBuildFailure | None: ...

    def fail(
        self,
        build_id: str,
        now: int,
        failure: TasteBuildFailure,
        *,
        stop_day: bool = False,
    ) -> None: ...

    def succeed(
        self,
        build_id: str,
        now: int,
        profile: TasteProfile,
        record: dict[str, object],
        *,
        stop_day: bool = False,
    ) -> int | None: ...

    def decisions(self) -> tuple[TasteDecisionSource, ...]: ...

    def interrupt_recovered(self, now: int) -> None: ...

    def read(self) -> dict[str, object]: ...


class CoverTasteRetryError(RuntimeError):
    """A retry names a build that is not the latest failed one."""


class CoverTasteBuildService:
    def __init__(
        self,
        repository: Callable[[], CoverTasteRepository],
        access: ReviewerAccessOperations,
        read_preview: Callable[[str], bytes],
        *,
        daily_limit: int,
        clock: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self._repository = repository
        self._access = access
        self._read_preview = read_preview
        self._daily_limit = daily_limit
        self._clock = clock
        self._scheduled_at: int | None = None
        self._recovered = False

    def read(self) -> dict[str, object]:
        """The stored build and version facts, whether the task is configured,
        and the daily limit the owner reads."""
        return {
            **self._repository().read(),
            "configured": self._access.current_taste_build_model() is not None,
            "daily_limit": self._daily_limit,
        }

    def retry(self, build_id: str) -> None:
        """Request the owner's Try again; it counts against the ceiling when sent."""
        if (
            self._repository().request_retry(build_id, self._clock(), self._daily_limit)
            is None
        ):
            raise CoverTasteRetryError("taste build is not retryable")

    def wake(self, now: int) -> None:
        """Schedule and run due builds; only the preparation runner calls it."""
        repository = self._repository()
        if not self._recovered:
            # Builds run only inside `wake`, so a build still running before
            # this process's first wake was left by an earlier process.
            repository.interrupt_recovered(now)
            self._recovered = True
        if self._scheduled_at is None or now - self._scheduled_at >= _SCHEDULE_SECONDS:
            self._scheduled_at = now
            repository.schedule(
                now,
                self._access.current_taste_build_model() is not None,
                self._daily_limit,
            )
        build_id = repository.pending()
        if build_id is not None:
            self._run(repository, build_id)

    def _run(self, repository: CoverTasteRepository, build_id: str) -> None:
        try:
            _revision, model, profile, client = self._access.open_taste_build()
        except ReviewerAccessApplicationError as error:
            repository.fail(
                build_id,
                self._clock(),
                "unconfigured"
                if error.code in {"not_configured", "credential_unavailable"}
                else "unavailable",
            )
            return
        try:
            inputs = assemble_taste_inputs(repository.decisions(), self._read_preview)
            request = (
                None
                if inputs is None
                else admit_taste_build_request(inputs.packet_document, inputs.images)
            )
            if inputs is None or request is None:
                repository.fail(build_id, self._clock(), "unavailable")
                return
            if repository.claim(build_id, self._clock(), model, self._daily_limit):
                return
            try:
                reply = client.run_taste_build(profile, request)
            except TasteChargeUnknownError:
                repository.fail(
                    build_id, self._clock(), "unknown_charge", stop_day=True
                )
                return
            except Exception:  # noqa: BLE001 - every other outcome sent no profile
                repository.fail(build_id, self._clock(), "unavailable")
                return
            try:
                admitted = TasteProfile.from_output(reply.result)
            except CoverTasteError:
                repository.fail(
                    build_id,
                    self._clock(),
                    "invalid_output",
                    stop_day=not reply.usage_reported,
                )
                return
            repository.succeed(
                build_id,
                self._clock(),
                admitted,
                inputs.record,
                stop_day=not reply.usage_reported,
            )
        except TasteContractError:
            repository.fail(build_id, self._clock(), "unavailable")
        finally:
            client.close()


__all__ = ["CoverTasteBuildService", "CoverTasteRepository", "CoverTasteRetryError"]
