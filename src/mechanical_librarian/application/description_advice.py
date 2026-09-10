"""Prepare a description adviser outside write transactions, then bind its result."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Protocol

from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    ContainedReviewResult,
)
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
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
    ) -> bool: ...


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
    ) -> DescriptionAdviceCandidate | None:
        identity = self._access.current_description_rewrite_identity()
        if identity is None:
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
            retry=retry,
        )
        if attempt is None:
            return None
        attempt_id, reused = attempt
        if reused is not None:
            return reused
        if not self._repository.start(attempt_id, self._clock(), case_current):
            return None
        candidate = None
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
        except Exception:
            # Failure records retain only the bounded engine provenance captured
            # before dispatch; raw provider/model errors never enter history.
            candidate = None
        admitted = self._repository.finish(
            attempt_id, candidate, self._clock(), case_current
        )
        return candidate if admitted else None
