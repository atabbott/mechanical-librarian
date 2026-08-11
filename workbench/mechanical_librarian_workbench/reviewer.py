"""Source-only binding for the contained reviewer kernel."""

from __future__ import annotations

import hashlib
import json

from mechanical_librarian_workbench.contained_reviewer import (
    ContainedReviewerAdapter,
    load_contained_reviewer_profile_bytes,
)
from mechanical_librarian_workbench.contained_reviewer_contract import (
    ContainedReviewImage,
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
    """Authenticate or invoke one image-free, semantic-neutral reviewer request."""
    reviewer = ContainedReviewerAdapter(load_contained_reviewer_profile_bytes(profile_payload))
    if action in {"auth-start", "auth-status", "auth-logout"}:
        return reviewer.auth(action.removeprefix("auth-"))
    if action != "invoke" or None in {instruction_payload, input_payload, schema_payload}:
        raise ValueError("invalid contained-reviewer action")
    assert instruction_payload is not None and input_payload is not None and schema_payload is not None
    return _invoke(
        reviewer,
        profile_payload,
        instruction_payload.decode("utf-8", errors="strict"),
        input_payload.decode("utf-8", errors="strict"),
        schema_payload.decode("utf-8", errors="strict"),
    )


def run_cover_packet_reviewer(
    profile_payload: bytes,
    instruction_payload: bytes,
    packet_payload: bytes,
    schema_payload: bytes,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> str:
    """Invoke the sole image-capable reviewer with one exact cover packet."""
    images = _validate_cover_packet_images(packet_payload, image_payloads)
    reviewer = ContainedReviewerAdapter(load_contained_reviewer_profile_bytes(profile_payload))
    return _invoke(
        reviewer,
        profile_payload,
        instruction_payload.decode("utf-8", errors="strict"),
        packet_payload.decode("utf-8", errors="strict"),
        schema_payload.decode("utf-8", errors="strict"),
        images,
    )


def _validate_cover_packet_images(
    packet_payload: bytes,
    image_payloads: tuple[tuple[str, str, bytes], ...],
) -> tuple[ContainedReviewImage, ...]:
    try:
        if type(packet_payload) is not bytes:
            raise ValueError
        serialized = packet_payload.decode("utf-8", errors="strict")
        packet = json.loads(serialized, object_pairs_hook=_unique_object)
        if (
            type(packet) is not dict
            or canonical_json(packet) != serialized
            or set(packet) != {"candidates", "omissions", "packet_reference", "schema_version"}
            or packet["schema_version"] != "cover-review-packet-v1"
            or type(packet["candidates"]) is not list
            or not 1 <= len(packet["candidates"]) <= 12
            or type(image_payloads) is not tuple
            or any(type(value) is not tuple or len(value) != 3 for value in image_payloads)
        ):
            raise ValueError
        bindings: list[tuple[str, str]] = []
        for candidate in packet["candidates"]:
            if (
                type(candidate) is not dict
                or set(candidate) != {
                    "height", "incumbent", "media_type", "preview_sha256",
                    "preview_variant", "provenance", "reference", "width",
                }
                or candidate["media_type"] != "image/png"
                or candidate["preview_variant"] != "preview-320x480-rgba-png-v1"
            ):
                raise ValueError
            bindings.append((candidate["reference"], candidate["preview_sha256"]))
        supplied = tuple((value[0], value[1]) for value in image_payloads)
        if supplied != tuple(bindings):
            raise ValueError
        return tuple(ContainedReviewImage(*value) for value in image_payloads)
    except (
        IndexError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ValueError("cover packet image binding is invalid") from error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invoke(
    reviewer: ContainedReviewerAdapter,
    profile_payload: bytes,
    instruction: str,
    input_document: str,
    output_schema: str,
    images: tuple[ContainedReviewImage, ...] = (),
) -> str:
    request = ContainedReviewRequest(
        instruction,
        input_document,
        output_schema,
        images,
    )
    dispatch: dict[str, object] = {}

    def record_dispatch(pid: int, pgid: int, start_token: str) -> None:
        dispatch.update({"pgid": pgid, "pid": pid, "start_token": start_token})

    result = reviewer.run(request, record_dispatch)
    if set(dispatch) != {"pgid", "pid", "start_token"}:
        raise RuntimeError("contained reviewer did not record dispatch")
    image_binding = canonical_json([
        {"reference": value.reference, "sha256": value.digest}
        for value in request.images
    ])
    execution_identity = canonical_json({
        "profile_sha256": hashlib.sha256(profile_payload).hexdigest(),
        "request_sha256": request.digest,
    })
    return json.dumps(
        {
            "execution_sha256": hashlib.sha256(execution_identity.encode()).hexdigest(),
            "image_binding_sha256": hashlib.sha256(image_binding.encode()).hexdigest(),
            "image_count": len(request.images),
            "input_sha256": hashlib.sha256(request.input_document.encode()).hexdigest(),
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


__all__ = ["run_contained_reviewer", "run_cover_packet_reviewer"]
