"""Compose the installed contained reviewer behind application-owned contracts."""

from __future__ import annotations

import hashlib
import json

from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    ContainedReviewerAdapter,
    load_contained_reviewer_profile_bytes,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    _CoverReviewRequest,
    admit_cover_review_request,
    canonical_json,
)


def run_cover_packet_reviewer(
    profile_payload: bytes,
    instruction_payload: bytes,
    packet_payload: bytes,
    schema_payload: bytes,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> str:
    """Invoke the sole image-capable reviewer with one exact cover packet."""
    request = admit_cover_review_request(
        instruction_payload.decode("utf-8", errors="strict"),
        packet_payload.decode("utf-8", errors="strict"),
        schema_payload.decode("utf-8", errors="strict"),
        image_payloads,
    )
    reviewer = ContainedReviewerAdapter(
        load_contained_reviewer_profile_bytes(profile_payload)
    )
    return _invoke_cover(reviewer, profile_payload, request)


def _invoke_cover(
    reviewer: ContainedReviewerAdapter,
    profile_payload: bytes,
    request: _CoverReviewRequest,
) -> str:
    dispatch: dict[str, object] = {}

    def record_dispatch(pid: int, pgid: int, start_token: str) -> None:
        dispatch.update({"pgid": pgid, "pid": pid, "start_token": start_token})

    result = reviewer.run_cover(request, record_dispatch)
    if set(dispatch) != {"pgid", "pid", "start_token"}:
        raise RuntimeError("contained reviewer did not record dispatch")
    image_binding = canonical_json(
        [
            {"reference": value.reference, "sha256": value.digest}
            for value in request.images
        ]
    )
    execution_identity = canonical_json(
        {
            "profile_sha256": hashlib.sha256(profile_payload).hexdigest(),
            "request_sha256": request.digest,
        }
    )
    envelope = {
        "execution_sha256": hashlib.sha256(execution_identity.encode()).hexdigest(),
        "image_binding_sha256": hashlib.sha256(image_binding.encode()).hexdigest(),
        "image_count": len(request.images),
        "input_sha256": hashlib.sha256(request.input_document.encode()).hexdigest(),
        "request_sha256": request.digest,
        "result": json.loads(result.document),
        "result_sha256": result.digest,
        "schema_version": "cover-contained-reviewer-result-v1",
        "status": "completed",
        "attempt_evidence": [
            {
                "diagnostic_bytes": attempt.diagnostic_bytes,
                "diagnostic_sha256": attempt.diagnostic_sha256,
                "dispatched": True,
                "failure_code": attempt.failure_code,
            }
            if attempt.failure_code is not None
            else {"dispatched": True}
            for attempt in result.attempts
        ],
    }
    return json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ["run_cover_packet_reviewer"]


def run_description_reviewer(
    profile_payload: bytes,
    instruction_payload: bytes,
    packet_payload: bytes,
    schema_payload: bytes,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> str:
    """Use the contained engine's existing image-free port for the closed task."""
    from mechanical_librarian.application.contained_reviewer_contract import (
        ContainedReviewRequest,
    )
    from mechanical_librarian.domain.description_advice import (
        DESCRIPTION_INSTRUCTION,
        DESCRIPTION_OUTPUT_SCHEMA,
        DescriptionAdviceInput,
    )

    request = ContainedReviewRequest(
        instruction_payload.decode("utf-8"),
        packet_payload.decode("utf-8"),
        schema_payload.decode("utf-8"),
    )
    if (
        image_payloads
        or request.instruction != DESCRIPTION_INSTRUCTION
        or request.output_schema != DESCRIPTION_OUTPUT_SCHEMA
    ):
        raise ValueError("invalid description request")
    DescriptionAdviceInput.model_validate_json(request.input_document)
    reviewer = ContainedReviewerAdapter(
        load_contained_reviewer_profile_bytes(profile_payload)
    )
    dispatched = False

    def record_dispatch(_pid: int, _pgid: int, _start_token: str) -> None:
        nonlocal dispatched
        dispatched = True

    result = reviewer.run(request, record_dispatch)
    if not dispatched:
        raise RuntimeError("contained reviewer did not record dispatch")
    return canonical_json(
        {"request_sha256": request.digest, "result": json.loads(result.document)}
    )
