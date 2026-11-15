"""Prepare a description adviser outside write transactions, then bind its result."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, Protocol, cast

from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    ContainedReviewRequest,
    ContainedReviewResult,
)
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.reviewer_contracts import OpenRouterReviewerError
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceInput,
    DescriptionAdviceOutput,
    DescriptionAdviceSettings,
    description_digest,
    description_document,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_case import ReviewCase

if TYPE_CHECKING:
    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from mechanical_librarian.application.review_pipeline import (
        PipelineAction,
        ReviewPipelineRepository,
    )


class DescriptionAdviceRepository(Protocol):
    """Attempt callbacks read staged case state on this same worker repository.

    The callback must not acquire an owner/access gate or read another store.
    Local request and passive profile checks belong to the repository transaction.
    """

    def begin_attempt(
        self,
        request_identity: str,
        binding: DescriptionAdviceBinding,
        engine: str,
        model: str,
        now: int,
        case_current: Callable[[], bool],
        *,
        retry: bool = False,
        reuse_only: bool = False,
    ) -> tuple[str, DescriptionAdviceCandidate | None] | None: ...

    def start(
        self, attempt_id: str, now: int, case_current: Callable[[], bool]
    ) -> bool: ...

    def finish(
        self,
        attempt_id: str,
        candidate: DescriptionAdviceCandidate | None,
        now: int,
        case_current: Callable[[], bool],
        *,
        failure: Literal["unavailable", "invalid_output"] | None = None,
    ) -> bool: ...

    def consume_pending_if_unconfigured(self, review_case_id: str) -> None: ...


class DescriptionAdviceStore(DescriptionAdviceRepository, Protocol):
    """The advice store as the pipeline's description stages use it."""

    def bind_settings(self, settings: DescriptionAdviceSettings) -> None: ...

    def current_settings(self) -> DescriptionAdviceSettings: ...

    def read(self, review_case_id: str) -> dict[str, object] | None: ...

    def admit(
        self,
        candidate_id: str,
        expected_binding: DescriptionAdviceBinding,
        origin: str,
        idempotency_digest: str,
        now: int,
    ) -> object: ...


class DescriptionPreparationRepository(Protocol):
    """The review pipeline's repository, as its description stages read it.

    The pipeline hands each stage callback back exactly the repository the
    composition root created, so this names capabilities that object carries.
    """

    def get(self, report_id: str) -> str: ...

    def read_staged_description_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str]: ...

    def description_advice_store(self) -> DescriptionAdviceStore: ...


class DescriptionAdviceService:
    def __init__(
        self,
        repository: DescriptionAdviceRepository,
        access: ReviewerAccessOperations,
        settings: Callable[[], DescriptionAdviceSettings],
        clock: Callable[[], int],
    ) -> None:
        self._repository = repository
        self._access = access
        self._settings = settings
        self._clock = clock

    def prepare(
        self,
        report: MetadataReport,
        binding: DescriptionAdviceBinding,
        case_current: Callable[[], bool],
        *,
        retry: bool = False,
        reuse_only: bool = False,
    ) -> DescriptionAdviceCandidate | None:
        """Prepare the case's description advice.

        With `reuse_only`, as a Book matching check projects (#1355), an
        earlier attempt or candidate for the same request is reused and no new
        attempt starts, so no model request is made; a pending request is left
        for the next ordinary preparation.
        """
        identity = self._access.current_description_rewrite_identity()
        if identity is None:
            self._repository.consume_pending_if_unconfigured(binding.review_case_id)
            return None
        settings = self._settings()
        preferences = settings.cleanup()
        try:
            projected = DescriptionAdviceInput.from_report(report, preferences)
            request = ContainedReviewRequest(
                DESCRIPTION_INSTRUCTION,
                description_document(projected.model_dump()),
                DESCRIPTION_OUTPUT_SCHEMA,
            )
        except ValueError:
            return None
        revision, engine, model, profile = identity
        request_identity = description_digest(
            {
                "request": request.digest,
                "access_revision": revision,
                "engine": engine,
                "model": model,
                "profile": hashlib.sha256(profile).hexdigest(),
            }
        )

        attempt = self._repository.begin_attempt(
            request_identity,
            binding,
            engine,
            model,
            self._clock(),
            case_current,
            retry=retry and not reuse_only,
            reuse_only=reuse_only,
        )
        if attempt is None:
            return None
        attempt_id, reused = attempt
        if reused is not None:
            return reused
        if not self._repository.start(attempt_id, self._clock(), case_current):
            return None
        candidate = None
        failure: Literal["unavailable", "invalid_output"] | None = "unavailable"
        try:
            dispatch = self._access.dispatch_description_rewrite()
            try:
                if (
                    dispatch.access_revision,
                    dispatch.engine,
                    dispatch.model,
                    dispatch.profile,
                ) != identity:
                    raise ValueError("description dispatch changed")
                document = dispatch.runner(
                    profile,
                    request.instruction.encode(),
                    request.input_document.encode(),
                    request.output_schema.encode(),
                    (),
                )
            finally:
                dispatch.close()
            failure = "invalid_output"
            if type(document) is not str or len(document.encode("utf-8")) > 65536:
                raise ValueError("description result too large")
            result = ContainedReviewResult(
                document, hashlib.sha256(document.encode("utf-8")).hexdigest()
            )
            envelope = json.loads(result.document)
            if (
                type(envelope) is not dict
                or set(envelope) != {"request_sha256", "result"}
                or envelope["request_sha256"] != request.digest
            ):
                raise ValueError("description result binding mismatch")
            output = DescriptionAdviceOutput.model_validate(envelope["result"])
            candidate = DescriptionAdviceCandidate.create(
                attempt_id=attempt_id,
                request_identity=request_identity,
                request_digest=request.digest,
                instruction_digest=hashlib.sha256(
                    request.instruction.encode()
                ).hexdigest(),
                schema_digest=hashlib.sha256(
                    request.output_schema.encode()
                ).hexdigest(),
                preferences_digest=description_digest(preferences.model_dump()),
                profile_digest=hashlib.sha256(profile).hexdigest(),
                engine=engine,
                model=model,
                access_revision=revision,
                output=output,
                created_at=self._clock(),
            )
            failure = None
        except ContainedReviewerError as error:
            failure = (
                "invalid_output"
                if error.code in {"malformed_output", "response_format"}
                else "unavailable"
            )
        except OpenRouterReviewerError as error:
            # Only an unreadable answer is invalid output. An oversized answer,
            # an exhausted deadline and a data-policy or image-count refusal
            # are the provider being unavailable, within the persisted set.
            failure = (
                "invalid_output"
                if error.code == "malformed_response"
                else "unavailable"
            )
        except Exception:
            # Only the bounded phase/error identity enters history, never raw errors.
            candidate = None
        admitted = self._repository.finish(
            attempt_id, candidate, self._clock(), case_current, failure=failure
        )
        return candidate if admitted else None


def prepare_staged_description(
    pipeline_repository: ReviewPipelineRepository,
    action: PipelineAction,
    *,
    reviewer_access: ReviewerAccessOperations,
    review: ReviewWebConfiguration,
    reuse_only: bool = False,
) -> None:
    """Prepare a staged review case's description advice, if it needs one.

    With `reuse_only` (a Book matching check, #1355) no new attempt starts.
    """
    if action.review_case_id is None:
        return
    repository = cast("DescriptionPreparationRepository", pipeline_repository)
    if action.report_id is not None:
        bound_report = MetadataReport.from_json(repository.get(action.report_id))
        if bound_report.audio is not None and not admitted_audiobook_identity(
            bound_report
        ):
            return
    identity = reviewer_access.current_description_rewrite_identity()
    if identity is None:
        repository.description_advice_store().consume_pending_if_unconfigured(
            action.review_case_id
        )
        return
    snapshot = repository.read_staged_description_preparation(action)
    case = ReviewCase.from_json(snapshot[0])
    report = MetadataReport.from_json(snapshot[1])
    settings = DescriptionAdviceSettings(
        remove_marketing=review.description_remove_marketing,
        remove_sales_boasts=review.description_remove_sales_boasts,
        remove_praise=review.description_remove_praise,
        automatic_application=review.description_automatic_application,
    )
    binding = DescriptionAdviceBinding(
        review_case_id=case.review_case_id,
        report_id=case.report_id,
        proposal_set_id=case.proposal_set_id,
        target_fingerprint=case.target_fingerprint,
        policy_generation=action.policy_generation or 1,
        access_revision=identity[0],
        preferences_digest=description_digest(settings.model_dump()),
    )

    def current() -> bool:
        try:
            return repository.read_staged_description_preparation(action) == snapshot
        except (ValueError, RuntimeError):
            return False

    repository.description_advice_store().bind_settings(settings)
    DescriptionAdviceService(
        repository.description_advice_store(),
        reviewer_access,
        repository.description_advice_store().current_settings,
        lambda: int(time.time()),
    ).prepare(
        report,
        binding,
        current,
        reuse_only=reuse_only,
    )


def admit_staged_description(
    pipeline_repository: ReviewPipelineRepository, action: PipelineAction
) -> None:
    """Admit a staged review case's description candidate by automatic policy."""
    if action.review_case_id is None:
        return
    repository = cast("DescriptionPreparationRepository", pipeline_repository)
    if action.report_id is not None:
        bound_report = MetadataReport.from_json(repository.get(action.report_id))
        if bound_report.audio is not None and not admitted_audiobook_identity(
            bound_report
        ):
            return
    store = repository.description_advice_store()
    advice = store.read(action.review_case_id)
    if advice is None or advice["candidate"] is None:
        return
    candidate = cast("dict[str, object]", advice["candidate"])
    binding = DescriptionAdviceBinding.model_validate(advice["binding"])
    try:
        store.admit(
            str(candidate["candidate_id"]),
            binding,
            "automatic_policy",
            description_digest(
                {
                    "candidate": candidate["candidate_id"],
                    "binding": binding.digest,
                    "origin": "automatic_policy",
                }
            ),
            int(time.time()),
        )
    except ValueError:
        # Parked, owner-final and stale results retain their immutable text.
        return
