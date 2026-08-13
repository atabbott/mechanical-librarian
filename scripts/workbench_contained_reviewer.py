"""Source-only generic contained-reviewer composition for contributor scenarios."""

from __future__ import annotations

import hashlib
import json

from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    ContainedReviewerAdapter,
    load_contained_reviewer_profile_bytes,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewRequest,
    canonical_json,
)


def run_contained_reviewer(
    profile_payload: bytes,
    action: str,
    instruction_payload: bytes | None,
    input_payload: bytes | None,
    schema_payload: bytes | None,
) -> str:
    """Authenticate or invoke one image-free, semantic-neutral workbench request."""
    reviewer = ContainedReviewerAdapter(
        load_contained_reviewer_profile_bytes(profile_payload)
    )
    if action in {"auth-start", "auth-status", "auth-logout"}:
        return reviewer.auth(action.removeprefix("auth-"))
    if action != "invoke" or None in {
        instruction_payload,
        input_payload,
        schema_payload,
    }:
        raise ValueError("invalid contained-reviewer action")
    assert instruction_payload is not None
    assert input_payload is not None
    assert schema_payload is not None
    request = ContainedReviewRequest(
        instruction_payload.decode("utf-8", errors="strict"),
        input_payload.decode("utf-8", errors="strict"),
        schema_payload.decode("utf-8", errors="strict"),
    )
    dispatch: dict[str, object] = {}

    def record_dispatch(pid: int, pgid: int, start_token: str) -> None:
        dispatch.update({"pgid": pgid, "pid": pid, "start_token": start_token})

    result = reviewer.run(request, record_dispatch)
    if set(dispatch) != {"pgid", "pid", "start_token"}:
        raise RuntimeError("contained reviewer did not record dispatch")
    execution_identity = canonical_json(
        {
            "profile_sha256": hashlib.sha256(profile_payload).hexdigest(),
            "request_sha256": request.digest,
        }
    )
    return json.dumps(
        {
            "execution_sha256": hashlib.sha256(
                execution_identity.encode()
            ).hexdigest(),
            "image_binding_sha256": hashlib.sha256(b"[]").hexdigest(),
            "image_count": 0,
            "input_sha256": hashlib.sha256(
                request.input_document.encode()
            ).hexdigest(),
            "request_sha256": request.digest,
            "result": json.loads(result.document),
            "result_sha256": result.digest,
            "schema_version": "contained-reviewer-workbench-result-v2",
            "status": "completed",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ["run_contained_reviewer"]
