"""Owner-managed reviewer authority, status, and exact-one-engine dispatch."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from mechanical_librarian.application.cover_observation import Reviewer
from mechanical_librarian.application.reviewer_contracts import (
    CodexDeviceAuthState,
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    ReviewerDispatch,
    ReviewerStatus,
    ReviewerStatusReason,
    openrouter_profile,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.reviewer_access import (
    CodexTaskSelection,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
    ReviewerEngine,
    ReviewerTaskSelection,
)
from mechanical_librarian.domain.validation import credential

ReviewerAccessFailure = Literal[
    "not_configured",
    "host_unsupported",
    "credential_unavailable",
    "access_changed",
    "invalid_key",
    "quota_exhausted",
    "rate_limited",
    "model_unavailable",
    "model_text_only",
    "malformed_response",
    "unreachable",
    "credential_clear_failed",
]


class ReviewerAccessApplicationError(RuntimeError):
    """Value-free owner-facing reviewer-access refusal."""

    def __init__(self, code: ReviewerAccessFailure) -> None:
        super().__init__(code)
        self.code = code


class ReviewerAccessRepositoryError(RuntimeError):
    """Value-free persistence contract failure."""


class ReviewerAccessRepository(Protocol):
    def read(self) -> tuple[ReviewerAccessDocument, bool]: ...

    def snapshot(self) -> tuple[ReviewerAccessDocument, str | None]: ...

    def revision_is_current(self, revision: int) -> bool: ...

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

    def recheck_credential_status(self) -> CodexDeviceAuthState: ...

    def close(self) -> None: ...


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


class OpenRouterRecommendation(_Closed):
    model: StrictStr
    tier: Literal["free", "paid"]
    qualified: Literal[False] = False
    evidence: Literal["catalogue_capability_only"] = "catalogue_capability_only"
    requests_per_minute: StrictInt | None = None
    requests_per_day: StrictStr | None = None


class ReviewerAccessView(_Closed):
    schema_version: Literal["reviewer-access-view-v1"] = "reviewer-access-view-v1"
    revision: StrictInt
    mapping: ReviewerTaskSelection | None
    openrouter_key_configured: StrictBool
    codex: ReviewerEngineStatus
    openrouter: ReviewerEngineStatus
    recommendations: tuple[StrictStr, ...] = ()
    candidates: tuple[OpenRouterRecommendation, ...] = (
        OpenRouterRecommendation(
            model="dots-studio/dots-3-note-preview:free",
            tier="free",
            requests_per_minute=20,
            requests_per_day="50 before $10 lifetime purchases; 1000 after",
        ),
        OpenRouterRecommendation(model="mistralai/ministral-3b-2512", tier="paid"),
        OpenRouterRecommendation(model="google/gemma-3-12b-it", tier="paid"),
        OpenRouterRecommendation(
            model="mistralai/mistral-small-3.2-24b-instruct", tier="paid"
        ),
    )
    free_tier_caveat: StrictStr = (
        "OpenRouter currently documents free-model caps of 20 requests/minute and "
        "50 requests/day before $10 in lifetime purchases, or 1,000/day after; "
        "provider limits may change."
    )
    data_use_caveat: StrictStr = (
        "OpenRouter and the selected endpoint may process submitted cover previews; "
        "review current provider data policies before use."
    )


OpenRouterFactory = Callable[[str, str], OpenRouterClient]
CodexBinding = Callable[[], tuple[str, bytes, Reviewer]]
CodexIdentity = Callable[[], tuple[str, bytes]]


class ReviewerAccessOperations:
    """The one application authority shared by setup, Settings, and dispatch."""

    def __init__(
        self,
        repository: ReviewerAccessRepository,
        *,
        codex_auth: CodexAuth,
        codex_host: Callable[[], ReviewerHostStatus],
        codex_identity: CodexIdentity | None = None,
        codex_binding: CodexBinding,
        openrouter_factory: OpenRouterFactory,
    ) -> None:
        self._repository = repository
        self._codex_auth = codex_auth
        self._codex_host = codex_host
        self._codex_binding = codex_binding
        self._codex_identity = codex_identity or (lambda: self._codex_binding()[:2])
        self._openrouter_factory = openrouter_factory
        self._gate = RLock()

    def read(self) -> ReviewerAccessView:
        with self._gate:
            return self._view()

    def refresh_openrouter(self) -> ReviewerAccessView:
        with self._gate:
            return self._refresh_openrouter()

    def _refresh_openrouter(self) -> ReviewerAccessView:
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
        selection = document.cover_observation
        if isinstance(selection, CodexTaskSelection):
            host = self._codex_host()
            flow = self._codex_auth.status()
            credential = (
                flow
                if flow.state
                in {
                    "starting",
                    "issued",
                    "waiting",
                    "validating",
                    "usable",
                    "expired",
                    "cancelled",
                    "failed",
                    "interrupted",
                }
                else self._codex_auth.current_credential_status()
            )
            codex = ReviewerEngineStatus(
                engine="codex",
                state=("host_unsupported" if not host.available else credential.state),
                reason=host.reason if not host.available else credential.failure,
                host_headroom_bytes=host.headroom_bytes,
            )
        else:
            # Generic access reads remain engine-neutral and cannot initialize
            # CODEX_HOME, parse the contained profile, or inspect host memory.
            codex = ReviewerEngineStatus(engine="codex", state="ready")
        openrouter_status = openrouter or ReviewerEngineStatus(
            engine="openrouter", state="unconfigured"
        )
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
        with self._gate:
            return self._configure_openrouter(
                expected_revision=expected_revision, api_key=api_key, model=model
            )

    def _configure_openrouter(
        self, *, expected_revision: int, api_key: str, model: str
    ) -> ReviewerAccessView:
        if not credential(api_key):
            raise ValueError("invalid reviewer credential")
        try:
            account = self._probe_openrouter(api_key, model)
        except OpenRouterReviewerError as error:
            raise ReviewerAccessApplicationError(
                _public_openrouter_failure(error)
            ) from None
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
        with self._gate:
            return self._select_codex(expected_revision=expected_revision)

    def _select_codex(self, *, expected_revision: int) -> ReviewerAccessView:
        host = self._codex_host()
        credential = self._codex_auth.recheck_credential_status()
        if not host.available or credential.state != "usable":
            raise ReviewerAccessApplicationError(
                "host_unsupported" if not host.available else "credential_unavailable"
            )
        self._replace_selection(
            expected_revision,
            CodexTaskSelection(engine="codex"),
        )
        return self.read()

    def select_none(self, *, expected_revision: int) -> ReviewerAccessView:
        with self._gate:
            self._replace_selection(expected_revision, None)
            return self._view()

    def clear_openrouter(self, *, expected_revision: int) -> ReviewerAccessView:
        with self._gate:
            return self._clear_openrouter(expected_revision=expected_revision)

    def _clear_openrouter(self, *, expected_revision: int) -> ReviewerAccessView:
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
        return self._view()

    def start_codex(self, *, expected_revision: int) -> CodexDeviceAuthState:
        with self._gate:
            document, _configured = self._repository.read()
            if document.revision != expected_revision:
                raise ReviewerAccessApplicationError("access_changed")
            if isinstance(document.cover_observation, CodexTaskSelection):
                self._replace_selection(expected_revision, None)
            if not self._codex_host().available:
                raise ReviewerAccessApplicationError("host_unsupported")
            return self._codex_auth.start()

    def codex_status(self) -> CodexDeviceAuthState:
        with self._gate:
            flow = self._codex_auth.status()
            if flow.state in {
                "starting",
                "issued",
                "waiting",
                "validating",
                "usable",
                "expired",
                "cancelled",
                "failed",
                "interrupted",
            }:
                return flow
            return self._codex_auth.current_credential_status()

    def cancel_codex(self) -> CodexDeviceAuthState:
        with self._gate:
            return self._codex_auth.cancel()

    def clear_codex(self, *, expected_revision: int) -> ReviewerAccessView:
        with self._gate:
            document, _configured = self._repository.read()
            if document.revision != expected_revision:
                raise ReviewerAccessApplicationError("access_changed")
            try:
                self._codex_auth.clear()
            except Exception:
                raise ReviewerAccessApplicationError(
                    "credential_clear_failed"
                ) from None
            selection = document.cover_observation
            if isinstance(selection, CodexTaskSelection):
                selection = None
            self._replace_selection(expected_revision, selection)
            return self._view()

    def dispatch_cover_observation(self) -> ReviewerDispatch:
        with self._gate:
            return self._dispatch_cover_observation()

    def _dispatch_cover_observation(self) -> ReviewerDispatch:
        document, key = self._repository.snapshot()
        selection = document.cover_observation
        if selection is None:
            raise ReviewerAccessApplicationError("not_configured")
        if isinstance(selection, CodexTaskSelection):
            if not self._codex_host().available:
                raise ReviewerAccessApplicationError("host_unsupported")
            if self._codex_auth.recheck_credential_status().state != "usable":
                raise ReviewerAccessApplicationError("credential_unavailable")
            model, profile, runner = self._codex_binding()
            if not self._repository.revision_is_current(document.revision):
                raise ReviewerAccessApplicationError("access_changed")
            return ReviewerDispatch(document.revision, "codex", model, profile, runner)
        if key is None:
            raise ReviewerAccessApplicationError("credential_unavailable")
        dispatch = self._openrouter_dispatch(document.revision, key, selection.model)
        if not self._repository.revision_is_current(document.revision):
            dispatch.close()
            raise ReviewerAccessApplicationError("access_changed")
        return dispatch

    def current_cover_observation_identity(
        self,
    ) -> tuple[int, str, str, bytes] | None:
        """Return non-secret replay identity without constructing an engine."""
        with self._gate:
            return self._current_cover_observation_identity()

    def _current_cover_observation_identity(
        self,
    ) -> tuple[int, str, str, bytes] | None:
        document, _key = self._repository.snapshot()
        selection = document.cover_observation
        if selection is None:
            return None
        if isinstance(selection, CodexTaskSelection):
            try:
                model, profile = self._codex_identity()
            except RuntimeError:
                return None
            return document.revision, "codex", model, profile
        return (
            document.revision,
            "openrouter",
            selection.model,
            openrouter_profile(selection.model),
        )

    def revision_is_current(self, revision: int) -> bool:
        with self._gate:
            return self._repository.revision_is_current(revision)

    def close(self) -> None:
        with self._gate:
            self._codex_auth.close()

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

    def _openrouter_dispatch(
        self, revision: int, key: str, model: str
    ) -> ReviewerDispatch:
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

        return ReviewerDispatch(
            revision, "openrouter", model, openrouter_profile(model), run, client.close
        )


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


def _public_openrouter_failure(error: OpenRouterReviewerError) -> ReviewerAccessFailure:
    if error.code in {"transport", "unreachable"}:
        return "unreachable"
    if error.code in {"profile_mismatch", "malformed_request"}:
        return "malformed_response"
    return cast("ReviewerAccessFailure", error.code)


__all__ = [
    "ReviewerAccessApplicationError",
    "ReviewerAccessOperations",
    "ReviewerAccessFailure",
    "ReviewerAccessRepositoryError",
    "ReviewerAccessView",
    "ReviewerDispatch",
    "ReviewerEngineStatus",
    "OpenRouterRecommendation",
]
