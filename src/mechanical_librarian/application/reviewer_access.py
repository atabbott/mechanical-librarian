"""Owner-managed reviewer authority, status, and exact-one-engine dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from mechanical_librarian.application.reviewer_contracts import (
    CodexDeviceAuthState,
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    ReviewerDispatch,
    ReviewerStatus,
    ReviewerStatusReason,
    openrouter_profile,
)
from mechanical_librarian.application.cover_observation import Reviewer
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.reviewer_access import (
    CodexTaskSelection,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
    ReviewerEngine,
    ReviewerTaskSelection,
)


class ReviewerAccessApplicationError(RuntimeError):
    """Value-free owner-facing reviewer-access refusal."""


class ReviewerAccessRepository(Protocol):
    def read(self) -> tuple[ReviewerAccessDocument, bool]: ...

    def replace(
        self,
        document: ReviewerAccessDocument,
        expected_revision: int,
        openrouter_key: str | None = None,
        clear_openrouter_key: bool = False,
    ) -> tuple[ReviewerAccessDocument, bool]: ...

    def openrouter_key(self) -> str: ...


class CodexAuth(Protocol):
    def start(self) -> CodexDeviceAuthState: ...

    def status(self) -> CodexDeviceAuthState: ...

    def cancel(self) -> CodexDeviceAuthState: ...

    def clear(self) -> CodexDeviceAuthState: ...

    def current_credential_status(self) -> CodexDeviceAuthState: ...


class OpenRouterClient(Protocol):
    def probe(self) -> OpenRouterAccountStatus: ...

    def run(
        self,
        profile: bytes,
        instruction: bytes,
        packet: bytes,
        schema: bytes,
        images: tuple[tuple[str, str, bytes], ...],
    ) -> str: ...

    def close(self) -> None: ...


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReviewerEngineStatus(_Closed):
    engine: ReviewerEngine
    state: ReviewerStatus
    reason: ReviewerStatusReason | None = None
    model: StrictStr | None = None
    tier: StrictStr | None = None
    host_headroom_bytes: StrictInt | None = None


class ReviewerAccessView(_Closed):
    schema_version: Literal["reviewer-access-view-v1"] = "reviewer-access-view-v1"
    revision: StrictInt
    mapping: ReviewerTaskSelection | None
    openrouter_key_configured: StrictBool
    codex: ReviewerEngineStatus
    openrouter: ReviewerEngineStatus
    recommendations: tuple[StrictStr, ...] = ()
    free_tier_caveat: StrictStr = (
        "Free-model request caps depend on the current account tier and may change."
    )
    data_use_caveat: StrictStr = (
        "OpenRouter and the selected endpoint may process submitted cover previews; "
        "review current provider data policies before use."
    )


OpenRouterFactory = Callable[[str, str], OpenRouterClient]
CodexBinding = Callable[[], tuple[str, bytes, Reviewer]]


class ReviewerAccessOperations:
    """The one application authority shared by setup, Settings, and dispatch."""

    def __init__(
        self,
        repository: ReviewerAccessRepository,
        *,
        codex_auth: CodexAuth,
        codex_host: Callable[[], ReviewerHostStatus],
        codex_binding: CodexBinding,
        openrouter_factory: OpenRouterFactory,
    ) -> None:
        self._repository = repository
        self._codex_auth = codex_auth
        self._codex_host = codex_host
        self._codex_binding = codex_binding
        self._openrouter_factory = openrouter_factory

    def read(self) -> ReviewerAccessView:
        return self._view()

    def refresh_openrouter(self) -> ReviewerAccessView:
        document, configured = self._repository.read()
        selection = document.cover_observation
        if not configured or not isinstance(selection, OpenRouterTaskSelection):
            return self._view(document=document, configured=configured)
        try:
            account = self._probe_openrouter(
                self._repository.openrouter_key(), selection.model
            )
            status = ReviewerEngineStatus(
                engine="openrouter",
                state="usable",
                model=account.model,
                tier=account.tier,
            )
        except OpenRouterReviewerError as error:
            status = _openrouter_failure_status(selection.model, error)
        return self._view(document=document, configured=configured, openrouter=status)

    def _view(
        self,
        *,
        document: ReviewerAccessDocument | None = None,
        configured: bool | None = None,
        openrouter: ReviewerEngineStatus | None = None,
    ) -> ReviewerAccessView:
        if document is None or configured is None:
            document, configured = self._repository.read()
        host = self._codex_host()
        credential = self._codex_auth.current_credential_status()
        codex = ReviewerEngineStatus(
            engine="codex",
            state=("host_unsupported" if not host.available else credential.state),
            reason=host.reason if not host.available else credential.failure,
            host_headroom_bytes=host.headroom_bytes,
        )
        openrouter_status = openrouter or ReviewerEngineStatus(
            engine="openrouter", state="unconfigured"
        )
        selection = document.cover_observation
        if (
            openrouter is None
            and configured
            and isinstance(selection, OpenRouterTaskSelection)
        ):
            openrouter_status = ReviewerEngineStatus(
                engine="openrouter", state="configured", model=selection.model
            )
        elif openrouter is None and configured:
            openrouter_status = ReviewerEngineStatus(
                engine="openrouter", state="configured", reason="not_selected"
            )
        return ReviewerAccessView(
            revision=document.revision,
            mapping=selection,
            openrouter_key_configured=configured,
            codex=codex,
            openrouter=openrouter_status,
        )

    def configure_openrouter(
        self, *, expected_revision: int, api_key: str, model: str
    ) -> ReviewerAccessView:
        account = self._probe_openrouter(api_key, model)
        replacement = ReviewerAccessDocument(
            schema_version="reviewer-access-v1",
            revision=expected_revision + 1,
            cover_observation=OpenRouterTaskSelection(engine="openrouter", model=model),
        )
        self._repository.replace(replacement, expected_revision, openrouter_key=api_key)
        return self._view(
            openrouter=ReviewerEngineStatus(
                engine="openrouter",
                state="usable",
                model=account.model,
                tier=account.tier,
            )
        )

    def select_codex(self, *, expected_revision: int) -> ReviewerAccessView:
        host = self._codex_host()
        credential = self._codex_auth.current_credential_status()
        if not host.available or credential.state != "usable":
            raise ReviewerAccessApplicationError("codex is unavailable")
        self._replace_selection(
            expected_revision,
            CodexTaskSelection(engine="codex"),
        )
        return self.read()

    def select_none(self, *, expected_revision: int) -> ReviewerAccessView:
        self._replace_selection(expected_revision, None)
        return self.read()

    def clear_openrouter(self, *, expected_revision: int) -> ReviewerAccessView:
        document, _configured = self._repository.read()
        selection = document.cover_observation
        if isinstance(selection, OpenRouterTaskSelection):
            selection = None
        replacement = ReviewerAccessDocument(
            schema_version="reviewer-access-v1",
            revision=expected_revision + 1,
            cover_observation=selection,
        )
        self._repository.replace(
            replacement, expected_revision, clear_openrouter_key=True
        )
        return self.read()

    def start_codex(self) -> CodexDeviceAuthState:
        if not self._codex_host().available:
            raise ReviewerAccessApplicationError("codex host is unsupported")
        return self._codex_auth.start()

    def codex_status(self) -> CodexDeviceAuthState:
        return self._codex_auth.status()

    def cancel_codex(self) -> CodexDeviceAuthState:
        return self._codex_auth.cancel()

    def clear_codex(self, *, expected_revision: int) -> ReviewerAccessView:
        document, _configured = self._repository.read()
        selection = document.cover_observation
        if isinstance(selection, CodexTaskSelection):
            selection = None
        self._replace_selection(expected_revision, selection)
        self._codex_auth.clear()
        return self.read()

    def dispatch_cover_observation(self) -> ReviewerDispatch:
        document, configured = self._repository.read()
        selection = document.cover_observation
        if selection is None:
            raise ReviewerAccessApplicationError("reviewer is not configured")
        if isinstance(selection, CodexTaskSelection):
            if not self._codex_host().available:
                raise ReviewerAccessApplicationError("codex host is unsupported")
            if self._codex_auth.current_credential_status().state != "usable":
                raise ReviewerAccessApplicationError("codex credential is unavailable")
            model, profile, runner = self._codex_binding()
            return ReviewerDispatch("codex", model, profile, runner)
        if not configured:
            raise ReviewerAccessApplicationError("openrouter key is unavailable")
        return self._openrouter_dispatch(
            self._repository.openrouter_key(), selection.model
        )

    def _replace_selection(
        self, expected_revision: int, selection: ReviewerTaskSelection | None
    ) -> None:
        replacement = ReviewerAccessDocument(
            schema_version="reviewer-access-v1",
            revision=expected_revision + 1,
            cover_observation=selection,
        )
        self._repository.replace(replacement, expected_revision)

    def _probe_openrouter(self, key: str, model: str) -> OpenRouterAccountStatus:
        client = self._openrouter_factory(key, model)
        try:
            return client.probe()
        finally:
            client.close()

    def _openrouter_dispatch(self, key: str, model: str) -> ReviewerDispatch:
        client = self._openrouter_factory(key, model)

        def run(
            profile: bytes,
            instruction: bytes,
            packet: bytes,
            schema: bytes,
            images: tuple[tuple[str, str, bytes], ...],
        ) -> str:
            try:
                return client.run(profile, instruction, packet, schema, images)
            finally:
                client.close()

        return ReviewerDispatch("openrouter", model, openrouter_profile(model), run)


def _openrouter_failure_status(
    model: str, error: OpenRouterReviewerError
) -> ReviewerEngineStatus:
    if error.code == "transport":
        state: ReviewerStatus = "unreachable"
        reason: ReviewerStatusReason = "unreachable"
    elif error.code in {"profile_mismatch", "malformed_request"}:
        state = "malformed_response"
        reason = "malformed_response"
    else:
        # The two adapter-only invocation failures were normalized above; the
        # remaining closed provider grammar is exactly the public status subset.
        state = cast("ReviewerStatus", error.code)
        reason = cast("ReviewerStatusReason", error.code)
    return ReviewerEngineStatus(
        engine="openrouter", state=state, reason=reason, model=model
    )


__all__ = [
    "ReviewerAccessApplicationError",
    "ReviewerAccessOperations",
    "ReviewerAccessView",
    "ReviewerDispatch",
    "ReviewerEngineStatus",
]
