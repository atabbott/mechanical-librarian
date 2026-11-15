"""The closed taste build request and its image re-admission (#1372 A5, R1)."""

from __future__ import annotations

import hashlib
import json

import pytest

from mechanical_librarian.application.contained_reviewer_contract import (
    MAX_INPUT_BYTES,
    ContainedReviewerError,
    ContainedReviewRequest,
    canonical_json,
)
from mechanical_librarian.application.cover_taste_contract import (
    COVER_TASTE_BUILD_PACKET_SCHEMA,
    TASTE_CODEX_OUTPUT_SCHEMA,
    TASTE_INSTRUCTION,
    TASTE_INSTRUCTION_SHA256,
    TASTE_OUTPUT_SCHEMA,
    AdmittedTasteBuildRequest,
    TasteBuildImage,
    TasteContractError,
    admit_taste_build_request,
    taste_image_request_bytes,
    taste_request_frame_bytes,
    taste_transport_jpeg,
)
from mechanical_librarian.domain.cover_taste import (
    MAX_TASTE_PROFILE_CODE_POINTS,
    MAX_TASTE_REMOVED,
    MAX_TASTE_REMOVED_CODE_POINTS,
    MAX_TASTE_RULES,
    TASTE_MAX_IMAGES,
    TASTE_MAX_REQUEST_BYTES,
    TASTE_SECTIONS,
    taste_budget,
)
from tests.support.cover_taste import canonical, noise_preview, stored_preview

# Pinned: a changed instruction is a reviewed contract change.
PINNED_INSTRUCTION_SHA256 = (
    "22a8b0893fa8d1ad1655a6a0c12ef8b107d0467578d51636ea150c7165e3c713"
)


def _reference(index: int) -> str:
    return "taste_image_" + hashlib.sha256(index.to_bytes(4, "big")).hexdigest()


def _image(index: int, payload: bytes | None = None) -> TasteBuildImage:
    preview = canonical(payload if payload is not None else stored_preview(index))
    return TasteBuildImage(
        _reference(index), preview.payload, taste_transport_jpeg(preview.payload)
    )


_NO_OWNER: dict[str, object] = {
    "budget": taste_budget(()),
    "kept": [],
    "left_out": [],
}


def _packet(
    images: list[TasteBuildImage],
    per_decision: int = 2,
    owner: dict[str, object] = _NO_OWNER,
) -> str:
    decisions = []
    for start in range(0, len(images), per_decision):
        group = images[start : start + per_decision]
        decisions.append(
            {
                "images": [
                    {
                        "current": position == 1,
                        "group": "AB"[position % 2],
                        "height": canonical_size(image)[1],
                        "reference": image.reference,
                        "role": "chosen" if position == 0 else "passed_over",
                        "shape": "fits",
                        "width": canonical_size(image)[0],
                    }
                    for position, image in enumerate(group)
                ],
                "medium": "ebook",
            }
        )
    return canonical_json(
        {
            **owner,
            "decisions": decisions,
            "schema_version": COVER_TASTE_BUILD_PACKET_SCHEMA,
        }
    )


def canonical_size(image: TasteBuildImage) -> tuple[int, int]:
    width = int.from_bytes(image.png[16:20], "big")
    height = int.from_bytes(image.png[20:24], "big")
    return width, height


def test_instruction_digest_and_schema_are_pinned() -> None:
    assert (
        hashlib.sha256(TASTE_INSTRUCTION.encode()).hexdigest()
        == TASTE_INSTRUCTION_SHA256
    )
    assert TASTE_INSTRUCTION_SHA256 == PINNED_INSTRUCTION_SHA256
    schema = json.loads(TASTE_OUTPUT_SCHEMA)
    assert schema["additionalProperties"] is False
    sections = schema["properties"]["sections"]
    assert sections["additionalProperties"] is False
    assert sections["required"] == ["drawn_to", "avoid", "trade_offs", "keep_or_switch"]


def test_admitted_request_carries_exactly_the_packet_and_its_jpegs() -> None:
    images = [_image(index) for index in range(4)]
    packet = _packet(images)
    request = admit_taste_build_request(packet, tuple(images))
    assert request.instruction == TASTE_INSTRUCTION
    assert request.packet_document == packet
    assert request.output_schema == TASTE_OUTPUT_SCHEMA
    assert request.images == tuple((image.reference, image.jpeg) for image in images)
    assert all(url.startswith("data:image/jpeg;base64,") for url in request.data_urls())
    with pytest.raises(TypeError):
        AdmittedTasteBuildRequest()


def test_transport_jpeg_is_deterministic_and_flattened_on_white() -> None:
    preview = canonical(stored_preview(3)).payload
    assert taste_transport_jpeg(preview) == taste_transport_jpeg(preview)
    assert taste_transport_jpeg(preview)[:3] == b"\xff\xd8\xff"
    with pytest.raises(ValueError):
        taste_transport_jpeg(b"not a png")


def test_tampered_preview_is_refused() -> None:
    images = [_image(index) for index in range(2)]
    packet = _packet(images)
    other = _image(9)
    # The PNG is replaced but the JPEG is the original's: re-derivation differs.
    tampered = [
        TasteBuildImage(images[0].reference, other.png, images[0].jpeg),
        images[1],
    ]
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(tampered))
    # A JPEG that is not the re-derivation of its PNG.
    rewritten = [
        TasteBuildImage(images[0].reference, images[0].png, other.jpeg),
        images[1],
    ]
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(rewritten))
    # A preview that is not canonical.
    raw = [
        TasteBuildImage(images[0].reference, stored_preview(0), images[0].jpeg),
        images[1],
    ]
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(raw))


def test_reference_not_in_packet_or_out_of_order_is_refused() -> None:
    images = [_image(index) for index in range(2)]
    packet = _packet(images)
    stray = TasteBuildImage(_reference(7), images[1].png, images[1].jpeg)
    for supplied in ((images[0], stray), (images[1], images[0]), (images[0],)):
        with pytest.raises(TasteContractError):
            admit_taste_build_request(packet, supplied)


@pytest.mark.parametrize(
    "change",
    [
        lambda packet: {**packet, "notes": "x"},
        lambda packet: {**packet, "schema_version": "cover-taste-build-packet-v0"},
        lambda packet: {
            **packet,
            "decisions": [{**packet["decisions"][0], "title": "A Book"}],
        },
        lambda packet: {
            **packet,
            "decisions": [
                {
                    **packet["decisions"][0],
                    "images": [
                        {
                            **packet["decisions"][0]["images"][0],
                            "path": "/books/a.epub",
                        },
                        packet["decisions"][0]["images"][1],
                    ],
                }
            ],
        },
        lambda packet: {
            **packet,
            "decisions": [
                {
                    **packet["decisions"][0],
                    "images": [
                        {**image, "role": "chosen"}
                        for image in packet["decisions"][0]["images"]
                    ],
                }
            ],
        },
        lambda packet: {
            **packet,
            "decisions": [{**packet["decisions"][0], "medium": "comic"}],
        },
    ],
    ids=[
        "packet-member",
        "schema",
        "decision-member",
        "image-member",
        "two-chosen",
        "medium",
    ],
)
def test_extra_member_or_open_value_is_refused(change) -> None:
    images = [_image(index) for index in range(2)]
    packet = json.loads(_packet(images))
    with pytest.raises(TasteContractError):
        admit_taste_build_request(canonical_json(change(packet)), tuple(images))


def test_original_decoded_size_is_stated_within_bounds() -> None:
    images = [_image(index) for index in range(2)]
    packet = json.loads(_packet(images))

    def sized(width: int, height: int) -> str:
        first = {
            **packet["decisions"][0]["images"][0],
            "width": width,
            "height": height,
        }
        decision = {
            **packet["decisions"][0],
            "images": [first, packet["decisions"][0]["images"][1]],
        }
        return canonical_json({**packet, "decisions": [decision]})

    assert admit_taste_build_request(sized(1200, 1800), tuple(images)).images
    for width, height in ((8193, 10), (0, 10), (4097, 4097)):
        with pytest.raises(TasteContractError):
            admit_taste_build_request(sized(width, height), tuple(images))


def test_non_canonical_packet_text_is_refused() -> None:
    images = [_image(index) for index in range(2)]
    packet = json.dumps(json.loads(_packet(images)), indent=1)
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(images))


def test_image_cap_is_enforced() -> None:
    image = _image(0)
    many = tuple(
        TasteBuildImage(_reference(index), image.png, image.jpeg)
        for index in range(TASTE_MAX_IMAGES + 1)
    )
    # 101 references cannot fit 50 decisions of 2; the cap refuses first anyway.
    with pytest.raises(TasteContractError):
        admit_taste_build_request(_packet(list(many), per_decision=3), many)


def test_byte_budget_bounds_the_worst_case_and_refuses_beyond_it() -> None:
    """Dry proof a, retained: 25 decisions × 4 noise previews fit 24 MB."""
    images = [_image(index, noise_preview(index)) for index in range(100)]
    packet = _packet(images, per_decision=4)
    assert all(len(image.png) <= 1_048_576 for image in images)
    request = admit_taste_build_request(packet, tuple(images))
    body = taste_request_frame_bytes(packet) + sum(
        taste_image_request_bytes(jpeg) for _reference, jpeg in request.images
    )
    assert body <= TASTE_MAX_REQUEST_BYTES
    # Negative control: the same previews sent as PNG overflow the budget.
    png_body = taste_request_frame_bytes(packet) + sum(
        taste_image_request_bytes(image.png) for image in images
    )
    assert png_body > TASTE_MAX_REQUEST_BYTES


def test_byte_budget_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    images = [_image(index) for index in range(2)]
    packet = _packet(images)
    monkeypatch.setattr(
        "mechanical_librarian.application.cover_taste_contract.TASTE_MAX_REQUEST_BYTES",
        taste_request_frame_bytes(packet),
    )
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(images))


def _astral(count: int, start: int) -> str:
    """One word of `count` four-byte code points (CJK Extension B, all
    assigned), distinct per `start`."""
    return "".join(
        chr(0x20000 + (start * 401 + index) % 42_720) for index in range(count)
    )


def _worst_owner() -> dict[str, object]:
    """40 kept rules of 8,192 four-byte code points and 100 removed texts of
    16,384, the most a packet v2 can carry beside its decisions (S4, S8)."""
    sizes = [400] * 20 + [10] * 19 + [MAX_TASTE_PROFILE_CODE_POINTS - 8_190]
    kept = [
        {"section": TASTE_SECTIONS[index // 10], "text": _astral(size, index)}
        for index, size in enumerate(sizes)
    ]
    removed_sizes = [163] * 16 + [164] * 84
    left_out = [_astral(size, 100 + index) for index, size in enumerate(removed_sizes)]
    assert sum(len(rule["text"]) for rule in kept) == MAX_TASTE_PROFILE_CODE_POINTS
    assert len(kept) == MAX_TASTE_RULES
    assert sum(map(len, left_out)) == MAX_TASTE_REMOVED_CODE_POINTS
    assert len(left_out) == MAX_TASTE_REMOVED
    return {
        "budget": taste_budget(tuple(rule["text"] for rule in kept)),
        "kept": kept,
        "left_out": left_out,
    }


def test_the_worst_packet_v2_fits_both_engines_and_the_request_budget() -> None:
    """Dry proof a: 50 decisions, the worst kept rules and removed list."""
    images = [_image(index, noise_preview(index)) for index in range(100)]
    packet = _packet(images, per_decision=2, owner=_worst_owner())
    assert len(json.loads(packet)["decisions"]) == 50
    request = admit_taste_build_request(packet, tuple(images))
    assert len(TASTE_INSTRUCTION.encode()) <= 4_096
    assert len(packet.encode()) <= MAX_INPUT_BYTES
    ContainedReviewRequest(TASTE_INSTRUCTION, packet, TASTE_CODEX_OUTPUT_SCHEMA)
    body = taste_request_frame_bytes(packet) + sum(
        taste_image_request_bytes(jpeg) for _reference, jpeg in request.images
    )
    assert body <= TASTE_MAX_REQUEST_BYTES
    # Negative control: an instruction one byte over Codex's limit is refused.
    with pytest.raises(ContainedReviewerError):
        ContainedReviewRequest("x" * 4_097, packet, TASTE_CODEX_OUTPUT_SCHEMA)


def test_kept_rules_removed_texts_and_budget_are_admitted() -> None:
    images = [_image(index) for index in range(2)]
    kept = [
        {"section": "drawn_to", "text": "Bold art."},
        {"section": "avoid", "text": "Busy type."},
    ]
    owner: dict[str, object] = {
        "budget": {"characters": 8_173, "rules": 38, "words": 1_196},
        "kept": kept,
        "left_out": ["Glossy finish."],
    }
    packet = _packet(images, owner=owner)
    assert admit_taste_build_request(packet, tuple(images)).packet_document == packet


def _owner_change(change: str) -> dict[str, object]:
    kept = [{"section": "avoid", "text": "Busy type."}]
    owner: dict[str, object] = {
        "budget": taste_budget(("Busy type.",)),
        "kept": kept,
        "left_out": [],
    }
    if change == "budget":
        owner["budget"] = {**taste_budget(("Busy type.",)), "words": 1_200}
    elif change == "budget-bool":
        owner["budget"] = {**taste_budget(()), "rules": 40, "words": True}
    elif change == "section":
        owner["kept"] = [{"section": "style", "text": "Busy type."}]
    elif change == "grouping":
        owner["kept"] = [*kept, {"section": "drawn_to", "text": "Bold art."}]
        owner["budget"] = taste_budget(("Busy type.", "Bold art."))
    elif change == "text":
        owner["kept"] = [{"section": "avoid", "text": "See https://example.invalid"}]
        owner["budget"] = taste_budget(("See https://example.invalid",))
    elif change == "kept-member":
        owner["kept"] = [{**kept[0], "number": 1}]
    elif change == "left-out-101":
        owner["left_out"] = [f"Rule {index}." for index in range(101)]
    elif change == "left-out-repeat":
        owner["left_out"] = ["Bold art.", "bold art"]
    elif change == "left-out-text":
        owner["left_out"] = ["Two\nlines."]
    elif change == "budget-member":
        owner["budget"] = {**taste_budget(("Busy type.",)), "images": 1}
    return owner


@pytest.mark.parametrize(
    "change",
    [
        "budget",
        "budget-bool",
        "section",
        "grouping",
        "text",
        "kept-member",
        "left-out-101",
        "left-out-repeat",
        "left-out-text",
        "budget-member",
    ],
)
def test_kept_rules_outside_their_contract_are_refused(change: str) -> None:
    images = [_image(index) for index in range(2)]
    packet = _packet(images, owner=_owner_change(change))
    with pytest.raises(TasteContractError):
        admit_taste_build_request(packet, tuple(images))


def test_a_packet_without_the_owner_members_is_refused() -> None:
    images = [_image(index) for index in range(2)]
    packet = json.loads(_packet(images))
    del packet["kept"]
    with pytest.raises(TasteContractError):
        admit_taste_build_request(canonical_json(packet), tuple(images))
