"""Owner-managed reviewer authority, status, and exact-one-engine dispatch."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from importlib.resources import files
from threading import RLock
from typing import Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from mechanical_librarian.application.cover_observation import Reviewer
from mechanical_librarian.application.reviewer_contracts import (
    CodexDeviceAuthState,
    OpenRouterAccountStatus,
    OpenRouterReviewerError,
    ReviewerDispatch,
    ReviewerProvisioningStatus,
    ReviewerStatus,
    ReviewerStatusReason,
    openrouter_cover_profile,
    openrouter_profile,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.reviewer_access import (
    CodexTaskSelection,
    CoverModelMetadata,
    DescriptionModelMetadata,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
    ReviewerEngine,
    ReviewerTask,
    ReviewerTaskSelection,
)
from mechanical_librarian.domain.reviewer_cost import ReviewerCostEvidence
from mechanical_librarian.domain.validation import credential

ReviewerAccessFailure = Literal[
    "not_provisioned",
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

    def __init__(
        self,
        code: ReviewerAccessFailure,
        identity: tuple[int, str, str, bytes] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.identity = identity


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
    def activate_lease(self) -> None: ...

    def start(self) -> CodexDeviceAuthState: ...

    def passive_status(self) -> CodexDeviceAuthState: ...

    def status(self) -> CodexDeviceAuthState: ...

    def cancel(self) -> CodexDeviceAuthState: ...

    def clear(self) -> CodexDeviceAuthState: ...

    def current_credential_status(self) -> CodexDeviceAuthState: ...

    def recheck_credential_status(self) -> CodexDeviceAuthState: ...

    def close(self) -> None: ...


class OpenRouterClient(Protocol):
    def run_description(
        self, profile: bytes, instruction: bytes, packet: bytes, schema: bytes
    ) -> str: ...

    def probe(self) -> OpenRouterAccountStatus: ...

    def probe_description(self) -> OpenRouterAccountStatus: ...

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


class CodexDescriptionDisclosure(_Closed):
    model: StrictStr
    profile_sha256: StrictStr


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


def _cost_evidence() -> ReviewerCostEvidence:
    payload = (
        files("mechanical_librarian")
        .joinpath("resources/reviewer-cost-evidence-v1.json")
        .read_bytes()
    )
    if len(payload) > 262144:
        raise ValueError("reviewer cost evidence is oversized")
    return ReviewerCostEvidence.model_validate_json(payload)


class ReviewerAccessView(_Closed):
    schema_version: Literal["reviewer-access-view-v2"] = "reviewer-access-view-v2"
    cost_evidence: ReviewerCostEvidence = Field(default_factory=_cost_evidence)
    revision: StrictInt
    mapping: ReviewerTaskSelection | None
    description_mapping: ReviewerTaskSelection | None = None
    description_metadata: DescriptionModelMetadata | None = None
    cover_metadata: CoverModelMetadata | None = None
    description: ReviewerEngineStatus | None = None
    openrouter_key_configured: StrictBool
    codex: ReviewerEngineStatus
    openrouter: ReviewerEngineStatus
    recommendations: tuple[StrictStr, ...] = ("openai/gpt-6-luna",)
    candidates: tuple[OpenRouterRecommendation, ...] = (
        OpenRouterRecommendation(
            model="dots-studio/dots-3-note-preview:free",
            tier="free",
            requests_per_minute=20,
            requests_per_day="50 before $10 lifetime purchases; 1000 after",
        ),
    )
    free_tier_caveat: StrictStr = (
        "OpenRouter currently documents free-model caps of 20 requests/minute and "
        "50 requests/day before $10 in lifetime purchases, or 1,000/day after; "
        "provider limits may change."
    )
    data_use_caveat: StrictStr = (
        "Cover requests deny provider data collection and allow retention; "
        "zero data retention is not required. Review provider data policies before use."
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
        codex_provisioning: Callable[[bool], ReviewerProvisioningStatus],
        codex_identity: CodexIdentity | None = None,
        codex_binding: CodexBinding,
        openrouter_factory: OpenRouterFactory,
        codex_description_binding: CodexBinding | None = None,
    ) -> None:
        self._command_lock = RLock()
        self._changed: Callable[[], None] = lambda: None
        self._repository = repository
        self._codex_auth = codex_auth
        self._codex_host = codex_host
        self._codex_provisioning = codex_provisioning
        self._codex_binding = codex_binding
        self._codex_description_binding = codex_description_binding
        self._codex_identity = codex_identity or (lambda: self._codex_binding()[:2])
        self._openrouter_factory = openrouter_factory
        self._serialized: Callable[[Callable[[], object]], object] = lambda call: call()

    def _admit_codex(self) -> ReviewerHostStatus:
        if not self._codex_provisioning(True).available:
            raise ReviewerAccessApplicationError("not_provisioned")
        host = self._codex_host()
        if not host.available:
            raise ReviewerAccessApplicationError("host_unsupported")
        return host

    def attach_changes(self, changed: Callable[[], None]) -> None:
        self._changed = changed

    def attach_serialization(
        self, serialized: Callable[[Callable[[], object]], object]
    ) -> None:
        self._serialized = serialized

    def activate_codex_lease(self) -> None:
        self._codex_auth.activate_lease()

    def _replace_document(
        self,
        document: ReviewerAccessDocument,
        expected_revision: int,
        openrouter_key: str | None = None,
        clear_openrouter_key: bool = False,
    ) -> tuple[ReviewerAccessDocument, bool]:
        """Serialize mapping publishers with credential-changing commands."""
        with self._command_lock:
            return cast(
                "tuple[ReviewerAccessDocument, bool]",
                self._serialized(
                    lambda: self._repository.replace(
                        document,
                        expected_revision,
                        openrouter_key,
                        clear_openrouter_key,
                    )
                ),
            )

    def _snapshot(self) -> tuple[ReviewerAccessDocument, str | None]:
        return cast(
            "tuple[ReviewerAccessDocument, str | None]",
            self._serialized(self._repository.snapshot),
        )

    def _require_revision(self, revision: int) -> None:
        if not self.revision_is_current(revision):
            raise ReviewerAccessApplicationError("access_changed")

    def read(self) -> ReviewerAccessView:
        return self._view()

    def refresh_openrouter(
        self, task: ReviewerTask = "cover_observation"
    ) -> ReviewerAccessView:
        return self._refresh_openrouter(task)

    def _refresh_openrouter(
        self, task: ReviewerTask = "cover_observation"
    ) -> ReviewerAccessView:
        document, key = self._snapshot()
        configured = key is not None
        selection = getattr(document, task)
        if not configured or not isinstance(selection, OpenRouterTaskSelection):
            return self._view(document=document, configured=configured)
        assert key is not None
        try:
            account = self._probe_openrouter(key, selection.model, task)
            if task == "description_rewrite":
                metadata = DescriptionModelMetadata.model_validate(
                    {
                        "model": selection.model,
                        "observed_at": account.observed_at,
                        "prompt_price": account.prompt_price,
                        "completion_price": account.completion_price,
                        "request_price": account.request_price,
                    }
                )
                document = ReviewerAccessDocument.model_validate(
                    {
                        **document.model_dump(),
                        "revision": document.revision + 1,
                        "description_metadata": metadata,
                    }
                )
                self._replace_document(document, document.revision - 1)
                self._changed()
            else:
                self._require_revision(document.revision)
                document = ReviewerAccessDocument.model_validate(
                    {
                        **document.model_dump(),
                        "revision": document.revision + 1,
                        "cover_metadata": _cover_metadata(account),
                    }
                )
                self._replace_document(document, document.revision - 1)
                self._changed()
            status = ReviewerEngineStatus(
                engine="openrouter",
                state="usable",
                model=account.model,
                tier=account.tier,
            )
        except (OpenRouterReviewerError, ValueError) as error:
            status = (
                _openrouter_failure_status(selection.model, error)
                if isinstance(error, OpenRouterReviewerError)
                else ReviewerEngineStatus(
                    engine="openrouter",
                    state="model_unavailable",
                    reason="model_unavailable",
                    model=selection.model,
                )
            )
            if task == "description_rewrite":
                document = ReviewerAccessDocument.model_validate(
                    {
                        **document.model_dump(),
                        "revision": document.revision + 1,
                        "description_metadata": None,
                    }
                )
                self._replace_document(document, document.revision - 1)
                self._changed()
        return self._view(
            document=document,
            configured=configured,
            openrouter=status if task == "cover_observation" else None,
            description=status if task == "description_rewrite" else None,
        )

    def _view(
        self,
        *,
        document: ReviewerAccessDocument | None = None,
        configured: bool | None = None,
        openrouter: ReviewerEngineStatus | None = None,
        codex: ReviewerEngineStatus | None = None,
        description: ReviewerEngineStatus | None = None,
    ) -> ReviewerAccessView:
        if document is None or configured is None:
            document, key = self._snapshot()
            configured = key is not None
        selection = document.cover_observation
        provisioning = self._codex_provisioning(False)
        if not provisioning.available:
            codex_status = ReviewerEngineStatus(
                engine="codex", state="not_provisioned", reason=provisioning.reason
            )
        elif codex is not None:
            codex_status = codex
        elif isinstance(selection, CodexTaskSelection) or isinstance(
            document.description_rewrite, CodexTaskSelection
        ):
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
            codex_status = ReviewerEngineStatus(
                engine="codex",
                state=("host_unsupported" if not host.available else credential.state),
                reason=host.reason if not host.available else credential.failure,
                host_headroom_bytes=host.headroom_bytes,
            )
        else:
            # Generic access reads remain engine-neutral and cannot initialize
            # CODEX_HOME, parse the contained profile, or inspect credentials.
            host = self._codex_host()
            flow = self._codex_auth.passive_status()
            codex_status = ReviewerEngineStatus(
                engine="codex",
                state=(
                    "host_unsupported"
                    if not host.available
                    else flow.state
                    if flow.state in {"starting", "issued", "waiting", "validating"}
                    else "ready"
                ),
                reason=None if host.available else host.reason,
                host_headroom_bytes=host.headroom_bytes,
            )
        if provisioning.available:
            codex_status = codex_status.model_copy(update={"model": provisioning.model})
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
        self._require_revision(document.revision)
        return ReviewerAccessView(
            revision=document.revision,
            mapping=selection,
            description_mapping=document.description_rewrite,
            description_metadata=document.description_metadata,
            cover_metadata=document.cover_metadata,
            description=(
                description
                or (
                    codex_status
                    if isinstance(document.description_rewrite, CodexTaskSelection)
                    else ReviewerEngineStatus(
                        engine="openrouter",
                        state=(
                            "unconfigured"
                            if not configured
                            else "configured"
                            if document.description_metadata is not None
                            else "model_unavailable"
                        ),
                        reason=(
                            "model_unavailable"
                            if configured and document.description_metadata is None
                            else None
                        ),
                        model=document.description_rewrite.model,
                    )
                    if isinstance(document.description_rewrite, OpenRouterTaskSelection)
                    else None
                )
            ),
            openrouter_key_configured=configured,
            codex=codex_status,
            openrouter=openrouter_status,
        )

    def configure_openrouter(
        self,
        *,
        expected_revision: int,
        api_key: str,
        model: str,
        task: ReviewerTask = "cover_observation",
    ) -> ReviewerAccessView:
        return self._configure_openrouter(
            expected_revision=expected_revision, api_key=api_key, model=model, task=task
        )

    def describe_contained_description(self) -> CodexDescriptionDisclosure:
        """Explicitly inspect the contained task identity without dispatch or selection."""

        def describe() -> CodexDescriptionDisclosure:
            self._admit_codex()
            try:
                model, profile = self._codex_identity()
            except ReviewerAccessApplicationError:
                raise
            except Exception:
                raise ReviewerAccessApplicationError("model_unavailable") from None
            return CodexDescriptionDisclosure(
                model=model, profile_sha256=hashlib.sha256(profile).hexdigest()
            )

        return describe()

    def test_description_model(
        self, api_key: str, model: str
    ) -> DescriptionModelMetadata:
        """Observe price and capability before selecting a description task."""

        def test() -> DescriptionModelMetadata:
            document, saved_key = self._snapshot()
            key = api_key or saved_key or ""
            if not credential(key):
                raise ValueError("invalid reviewer credential")
            try:
                account = self._probe_openrouter(key, model, "description_rewrite")
                self._require_revision(document.revision)
                return DescriptionModelMetadata.model_validate(
                    {
                        "model": model,
                        "observed_at": account.observed_at,
                        "prompt_price": account.prompt_price,
                        "completion_price": account.completion_price,
                        "request_price": account.request_price,
                    }
                )
            except OpenRouterReviewerError as error:
                raise ReviewerAccessApplicationError(
                    _public_openrouter_failure(error)
                ) from None

        return test()

    def _configure_openrouter(
        self,
        *,
        expected_revision: int,
        api_key: str,
        model: str,
        task: ReviewerTask = "cover_observation",
    ) -> ReviewerAccessView:
        document, saved_key = self._snapshot()
        if task == "description_rewrite" and not api_key:
            api_key = saved_key or ""
        if not credential(api_key):
            raise ValueError("invalid reviewer credential")
        try:
            account = self._probe_openrouter(api_key, model, task)
        except OpenRouterReviewerError as error:
            raise ReviewerAccessApplicationError(
                _public_openrouter_failure(error)
            ) from None
        if task == "description_rewrite" and (
            account.observed_at is None
            or account.prompt_price is None
            or account.completion_price is None
            or account.request_price is None
        ):
            raise ReviewerAccessApplicationError("model_unavailable")
        replacement = ReviewerAccessDocument.model_validate(
            {
                **document.model_dump(),
                "revision": expected_revision + 1,
                task: OpenRouterTaskSelection(engine="openrouter", model=model),
                "cover_metadata": (
                    _cover_metadata(account)
                    if task == "cover_observation"
                    else document.cover_metadata
                ),
                "description_metadata": (
                    DescriptionModelMetadata.model_validate(
                        {
                            "model": model,
                            "observed_at": account.observed_at,
                            "prompt_price": account.prompt_price,
                            "completion_price": account.completion_price,
                            "request_price": account.request_price,
                        }
                    )
                    if task == "description_rewrite"
                    else document.description_metadata
                ),
            }
        )
        document, configured = self._replace_document(
            replacement, expected_revision, openrouter_key=api_key
        )
        self._changed()
        status = ReviewerEngineStatus(
            engine="openrouter", state="usable", model=account.model, tier=account.tier
        )
        return self._view(
            document=document,
            configured=configured,
            openrouter=status if task == "cover_observation" else None,
            description=status if task == "description_rewrite" else None,
        )

    def select_codex(
        self, *, expected_revision: int, task: ReviewerTask = "cover_observation"
    ) -> ReviewerAccessView:
        return self._select_codex(expected_revision=expected_revision, task=task)

    def _select_codex(
        self, *, expected_revision: int, task: ReviewerTask = "cover_observation"
    ) -> ReviewerAccessView:
        host = self._admit_codex()
        with self._command_lock:
            credential = self._codex_auth.recheck_credential_status()
            if credential.state != "usable":
                raise ReviewerAccessApplicationError("credential_unavailable")
            self._admit_codex()
            document, configured = self._replace_selection(
                expected_revision,
                CodexTaskSelection(engine="codex"),
                task,
            )
        return self._view(
            document=document,
            configured=configured,
            codex=ReviewerEngineStatus(
                engine="codex",
                state="usable",
                host_headroom_bytes=host.headroom_bytes,
            ),
        )

    def select_none(
        self, *, expected_revision: int, task: ReviewerTask = "cover_observation"
    ) -> ReviewerAccessView:
        document, configured = self._replace_selection(expected_revision, None, task)
        return self._view(document=document, configured=configured)

    def clear_openrouter(self, *, expected_revision: int) -> ReviewerAccessView:
        return self._clear_openrouter(expected_revision=expected_revision)

    def _clear_openrouter(self, *, expected_revision: int) -> ReviewerAccessView:
        document, _key = self._snapshot()
        selection = document.cover_observation
        if isinstance(selection, OpenRouterTaskSelection):
            selection = None
        replacement = ReviewerAccessDocument.model_validate(
            {
                **document.model_dump(),
                "revision": expected_revision + 1,
                "cover_observation": selection,
                "cover_metadata": None,
                "description_metadata": None,
                "description_rewrite": (
                    None
                    if isinstance(document.description_rewrite, OpenRouterTaskSelection)
                    else document.description_rewrite
                ),
            }
        )
        document, configured = self._replace_document(
            replacement, expected_revision, clear_openrouter_key=True
        )
        self._changed()
        return self._view(document=document, configured=configured)

    def start_codex(
        self, *, expected_revision: int
    ) -> tuple[ReviewerAccessView, CodexDeviceAuthState]:
        with self._command_lock:
            document, key = self._snapshot()
            configured = key is not None
            if document.revision != expected_revision:
                raise ReviewerAccessApplicationError("access_changed")
            self._admit_codex()
            if isinstance(document.cover_observation, CodexTaskSelection) or isinstance(
                document.description_rewrite, CodexTaskSelection
            ):
                document, configured = self._clear_codex_selections(
                    document, expected_revision
                )
            device = self._codex_auth.start()
        return self._view(document=document, configured=configured), device

    def codex_status(self) -> CodexDeviceAuthState:
        def status() -> CodexDeviceAuthState:
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

        return status()

    def recheck_codex(self) -> CodexDeviceAuthState:
        """Explicitly validate the installed credential unless a flow owns it."""

        def recheck() -> CodexDeviceAuthState:
            self._admit_codex()
            return self._codex_auth.recheck_credential_status()

        return recheck()

    def cancel_codex(self) -> CodexDeviceAuthState:
        with self._command_lock:
            return self._codex_auth.cancel()

    def clear_codex(self, *, expected_revision: int) -> ReviewerAccessView:
        with self._command_lock:
            document, _key = self._snapshot()
            if document.revision != expected_revision:
                raise ReviewerAccessApplicationError("access_changed")
            try:
                self._codex_auth.clear()
            except Exception:
                raise ReviewerAccessApplicationError(
                    "credential_clear_failed"
                ) from None
            document, configured = self._clear_codex_selections(
                document, expected_revision
            )
        return self._view(document=document, configured=configured)

    def _clear_codex_selections(
        self, document: ReviewerAccessDocument, expected_revision: int
    ) -> tuple[ReviewerAccessDocument, bool]:
        replacement = ReviewerAccessDocument.model_validate(
            {
                **document.model_dump(),
                "revision": expected_revision + 1,
                "cover_observation": None
                if isinstance(document.cover_observation, CodexTaskSelection)
                else document.cover_observation,
                "description_rewrite": None
                if isinstance(document.description_rewrite, CodexTaskSelection)
                else document.description_rewrite,
            }
        )
        result = self._replace_document(replacement, expected_revision)
        self._changed()
        return result

    def dispatch_cover_observation(self) -> ReviewerDispatch:
        return self._dispatch_cover_observation()

    def _dispatch_cover_observation(
        self, task: ReviewerTask = "cover_observation"
    ) -> ReviewerDispatch:
        document, key = self._snapshot()
        selection = getattr(document, task)
        if selection is None:
            raise ReviewerAccessApplicationError("not_configured")
        if isinstance(selection, CodexTaskSelection):
            self._admit_codex()
            try:
                model, profile = self._codex_identity()
            except ReviewerAccessApplicationError:
                raise
            except Exception:
                raise ReviewerAccessApplicationError("credential_unavailable") from None
            identity = (document.revision, "codex", model, profile)
            if not self._codex_host().available:
                raise ReviewerAccessApplicationError("host_unsupported", identity)
            if self._codex_auth.recheck_credential_status().state != "usable":
                raise ReviewerAccessApplicationError("credential_unavailable", identity)
            try:
                binding = (
                    self._codex_binding
                    if task == "cover_observation"
                    else self._codex_description_binding
                )
                if binding is None:
                    raise RuntimeError("description binding unavailable")
                bound_model, bound_profile, runner = binding()
            except ReviewerAccessApplicationError:
                raise
            except Exception:
                raise ReviewerAccessApplicationError(
                    "credential_unavailable", identity
                ) from None
            if (bound_model, bound_profile) != (model, profile):
                raise ReviewerAccessApplicationError("access_changed", identity)
            if not self._repository.revision_is_current(document.revision):
                raise ReviewerAccessApplicationError("access_changed", identity)
            return ReviewerDispatch(
                document.revision, "codex", bound_model, bound_profile, runner
            )
        identity = (
            document.revision,
            "openrouter",
            selection.model,
            (
                openrouter_cover_profile(selection.model)
                if task == "cover_observation"
                else openrouter_profile(selection.model)
            ),
        )
        if key is None:
            raise ReviewerAccessApplicationError("credential_unavailable", identity)
        if task == "description_rewrite" and (
            document.description_metadata is None
            or document.description_metadata.model != selection.model
        ):
            raise ReviewerAccessApplicationError("model_unavailable", identity)
        try:
            dispatch = self._openrouter_dispatch(
                document.revision, key, selection.model, task
            )
        except Exception:
            raise ReviewerAccessApplicationError("unreachable", identity) from None
        if not self._repository.revision_is_current(document.revision):
            dispatch.close()
            raise ReviewerAccessApplicationError("access_changed", identity)
        return dispatch

    def dispatch_description_rewrite(self) -> ReviewerDispatch:
        return self._dispatch_cover_observation("description_rewrite")

    def current_description_rewrite_identity(
        self,
    ) -> tuple[int, str, str, bytes] | None:
        return self._current_cover_observation_identity("description_rewrite")

    def current_cover_observation_identity(
        self,
    ) -> tuple[int, str, str, bytes] | None:
        """Return non-secret replay identity without constructing an engine."""
        return self._current_cover_observation_identity()

    def _current_cover_observation_identity(
        self,
        task: ReviewerTask = "cover_observation",
    ) -> tuple[int, str, str, bytes] | None:
        document, _key = self._snapshot()
        selection = getattr(document, task)
        if selection is None:
            return None
        if isinstance(selection, CodexTaskSelection):
            try:
                model, profile = self._codex_identity()
            except RuntimeError:
                return None
            self._require_revision(document.revision)
            return document.revision, "codex", model, profile
        self._require_revision(document.revision)
        return (
            document.revision,
            "openrouter",
            selection.model,
            (
                openrouter_cover_profile(selection.model)
                if task == "cover_observation"
                else openrouter_profile(selection.model)
            ),
        )

    def revision_is_current(self, revision: int) -> bool:
        return cast(
            "bool",
            self._serialized(lambda: self._repository.revision_is_current(revision)),
        )

    def close(self) -> None:
        with self._command_lock:
            self._codex_auth.close()

    def _replace_selection(
        self,
        expected_revision: int,
        selection: ReviewerTaskSelection | None,
        task: ReviewerTask = "cover_observation",
    ) -> tuple[ReviewerAccessDocument, bool]:
        document, _key = self._snapshot()
        replacement = ReviewerAccessDocument.model_validate(
            {
                **document.model_dump(),
                "revision": expected_revision + 1,
                task: selection,
                "cover_metadata": (
                    None if task == "cover_observation" else document.cover_metadata
                ),
                "description_metadata": (
                    None
                    if task == "description_rewrite"
                    else document.description_metadata
                ),
            }
        )
        result = self._replace_document(replacement, expected_revision)
        self._changed()
        return result

    def _probe_openrouter(
        self, key: str, model: str, task: ReviewerTask = "cover_observation"
    ) -> OpenRouterAccountStatus:
        client = self._openrouter_factory(key, model)
        try:
            return (
                client.probe_description()
                if task == "description_rewrite"
                else client.probe()
            )
        finally:
            client.close()

    def _openrouter_dispatch(
        self,
        revision: int,
        key: str,
        model: str,
        task: ReviewerTask = "cover_observation",
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
                if task == "description_rewrite":
                    return client.run_description(profile, instruction, packet, schema)
                return client.run(profile, instruction, packet, schema, images)
            finally:
                client.close()

        return ReviewerDispatch(
            revision,
            "openrouter",
            model,
            (
                openrouter_cover_profile(model)
                if task == "cover_observation"
                else openrouter_profile(model)
            ),
            run,
            client.close,
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


def _cover_metadata(account: OpenRouterAccountStatus) -> CoverModelMetadata:
    return CoverModelMetadata.model_validate(
        {
            "model": account.model,
            "observed_at": account.observed_at,
            "prompt_price": account.prompt_price,
            "completion_price": account.completion_price,
            "image_price": account.image_price,
            "request_price": account.request_price,
        }
    )
