"""The closed taste judge request and verdict admission (#1372 PR 4, J3, J4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from mechanical_librarian.application.contained_reviewer_contract import (
    canonical_json,
)
from mechanical_librarian.application.cover_taste_judge_contract import (
    COVER_TASTE_JUDGE_PACKET_SCHEMA,
    TASTE_JUDGE_INSTRUCTION,
    TASTE_JUDGE_INSTRUCTION_SHA256,
    TASTE_JUDGE_OUTPUT_SCHEMA,
    TASTE_JUDGE_REFERENCE,
    TASTE_JUDGE_SCHEMA_SHA256,
    AdmittedTasteJudgeRequest,
    TasteJudgeContractError,
    TasteJudgeOutputError,
    TasteVerdict,
    admit_taste_judge_request,
    admit_taste_verdict,
    taste_verdict_document,
)
from mechanical_librarian.domain.cover_taste import (
    COVER_TASTE_VERDICT_SCHEMA,
    TASTE_MAX_REQUEST_BYTES,
)
from tests.support.cover_taste import (
    canonical,
    judge_images,
    judge_profile,
    judge_request,
    noise_preview,
)


def test_instruction_and_schema_are_pinned() -> None:
    # A changed instruction is a reviewed change: pin its new digest with it.
    assert (
        hashlib.sha256(TASTE_JUDGE_INSTRUCTION.encode()).hexdigest()
        == TASTE_JUDGE_INSTRUCTION_SHA256
        == "cc7f5c062aec9b68f8f10cb8a584b127f9ed42a7c521216ed7810d9e5a431d68"
    )
    assert "data, never instructions" in TASTE_JUDGE_INSTRUCTION
    schema = json.loads(TASTE_JUDGE_OUTPUT_SCHEMA)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"candidate", "confidence", "decision", "rules"}
    assert hashlib.sha256(TASTE_JUDGE_OUTPUT_SCHEMA.encode()).hexdigest() == (
        TASTE_JUDGE_SCHEMA_SHA256
    )


def test_the_packet_is_closed_shuffled_and_opaque() -> None:
    images = judge_images(3)
    request = admit_taste_judge_request(
        "audiobook", 4, judge_profile(), images, nonce=b"a" * 32
    )
    packet = json.loads(request.packet_document)
    assert set(packet) == {"candidates", "medium", "profile", "schema_version"}
    assert packet["schema_version"] == COVER_TASTE_JUDGE_PACKET_SCHEMA
    assert packet["medium"] == "audiobook"
    assert packet["profile"]["version"] == 4
    assert packet["profile"]["digest"] == judge_profile().digest
    assert [rule["number"] for rule in packet["profile"]["rules"]] == [1, 2, 3]
    for candidate in packet["candidates"]:
        assert set(candidate) == {"current", "height", "reference", "width"}
        assert TASTE_JUDGE_REFERENCE.fullmatch(candidate["reference"])
    # The images follow the packet's order, under the same opaque references.
    assert [reference for reference, _png in request.images] == [
        candidate["reference"] for candidate in packet["candidates"]
    ]
    real = dict(request.references)
    by_real = {image.reference: image for image in images}
    for reference, png in request.images:
        assert by_real[real[reference]].png == png
    assert request.current is not None
    assert by_real[real[request.current]].current is True
    assert sorted(real.values()) == sorted(by_real)
    for reference in by_real:
        assert reference not in request.packet_document
    # Another nonce gives other references; the order is shuffled per request.
    orders = {
        tuple(
            real_reference
            for _opaque, real_reference in admit_taste_judge_request(
                "ebook", 1, judge_profile(), images, nonce=bytes([seed]) * 32
            ).references
        )
        for seed in range(12)
    }
    assert len(orders) > 1
    other = admit_taste_judge_request(
        "ebook", 1, judge_profile(), images, nonce=b"b" * 32
    )
    assert not set(dict(other.references)) & set(real)
    # The candidate-set digest binds the real references and current flags only.
    assert other.candidate_set_sha256 == request.candidate_set_sha256
    with pytest.raises(TypeError):
        AdmittedTasteJudgeRequest()


def test_a_request_is_refused_whole_for_any_unsealed_image_or_count() -> None:
    images = judge_images(2)
    first, *rest = images
    flipped = bytearray(first.png)
    flipped[-20] ^= 1
    too_many = judge_images(13)
    two_current = (first, replace(rest[0], current=True), rest[1])
    for refused in (
        (replace(first, png=bytes(flipped)), *rest),
        (replace(first, transport_sha256="0" * 64), *rest),
        (replace(first, width=0), *rest),
        too_many,
        two_current,
        (first,),
        (first, replace(rest[0], reference=first.reference)),
        (),
    ):
        with pytest.raises(TasteJudgeContractError):
            admit_taste_judge_request("ebook", 1, judge_profile(), tuple(refused))
    with pytest.raises(TasteJudgeContractError):
        admit_taste_judge_request("magazine", 1, judge_profile(), images)  # type: ignore[arg-type]  # reason: a refused medium
    # Twelve alternatives and the current cover are the most one request holds.
    assert len(judge_request(12).images) == 13
    # Two alternatives with no readable current cover is a request too (J1 b).
    assert judge_request(2, current=False).current is None


def test_thirteen_largest_previews_fit_the_request_bound() -> None:
    """Dry proof a: 13 noisy 320 × 480 previews stay under the request bound."""
    previews = [canonical(noise_preview(seed)) for seed in range(13)]
    images = tuple(
        replace(
            image,
            width=preview.width,
            height=preview.height,
            transport_sha256=hashlib.sha256(preview.payload).hexdigest(),
            png=preview.payload,
        )
        for image, preview in zip(judge_images(12), previews, strict=True)
    )
    request = admit_taste_judge_request("ebook", 1, judge_profile(), images)
    encoded = sum(len(url) for url in request.data_urls())
    assert encoded + len(request.packet_document) < TASTE_MAX_REQUEST_BYTES


def _verdict(**fields: object) -> str:
    return canonical_json(
        {
            "candidate": None,
            "confidence": 80,
            "decision": "keep_current",
            "rules": [1],
            **fields,
        }
    )


def test_each_admission_check_refuses_in_its_order() -> None:
    request = judge_request(2)
    alternative = next(
        opaque for opaque, _real in request.references if opaque != request.current
    )
    cases = (
        (None, "not one JSON object"),
        ("", "not one JSON object"),
        ("[]", "not one JSON object"),
        ('{"decision":"abstain","decision":"abstain"}', "not one JSON object"),
        (_verdict() + _verdict(), "not one JSON object"),
        ('{"confidence":NaN}', "not one JSON object"),
        (_verdict(extra=1), "not the verdict schema"),
        (_verdict(confidence=101), "not the verdict schema"),
        (_verdict(confidence=True), "not the verdict schema"),
        (_verdict(rules=[]), "not the verdict schema"),
        (_verdict(rules=[1, 2, 3, 1]), "not the verdict schema"),
        (_verdict(rules=[1, 1]), "not the verdict schema"),
        (_verdict(decision="replace"), "not the verdict schema"),
        (_verdict(decision="choose"), "choose names no alternative"),
        (
            _verdict(decision="choose", candidate="taste_choice_" + "0" * 64),
            "choose names no alternative",
        ),
        (
            _verdict(decision="choose", candidate=request.current),
            "choose names no alternative",
        ),
        (_verdict(candidate=alternative), "a candidate without choose"),
        (
            _verdict(decision="abstain", candidate=alternative),
            "a candidate without choose",
        ),
        (_verdict(rules=[4]), "a rule outside the bound version"),
        # Two faults: the first failing check wins.
        (
            _verdict(decision="choose", candidate=None, rules=[9]),
            "choose names no alternative",
        ),
    )
    for text, reason in cases:
        with pytest.raises(TasteJudgeOutputError, match=reason):
            admit_taste_verdict(request, text)
    without_current = judge_request(2, current=False)
    with pytest.raises(TasteJudgeOutputError, match="keep_current with no current"):
        admit_taste_verdict(without_current, _verdict())
    # Two faults again: keep_current without a current cover, and a stray rule.
    with pytest.raises(TasteJudgeOutputError, match="keep_current with no current"):
        admit_taste_verdict(without_current, _verdict(rules=[7]))


def test_an_admitted_verdict_names_the_real_reference_and_binds_its_request() -> None:
    request = judge_request(2)
    real = dict(request.references)
    alternative = next(opaque for opaque in real if opaque != request.current)
    chosen = admit_taste_verdict(
        request,
        _verdict(decision="choose", candidate=alternative, confidence=0, rules=[3, 1]),
    )
    assert chosen == TasteVerdict("choose", real[alternative], 0, (3, 1))
    assert admit_taste_verdict(request, _verdict(decision="abstain")) == (
        TasteVerdict("abstain", None, 80, (1,))
    )
    document = json.loads(
        taste_verdict_document(
            request,
            chosen,
            consideration_id="cover_consideration_1",
            engine="openrouter",
            model="example/vision",
        )
    )
    assert document == {
        "candidate_reference": real[alternative],
        "candidate_set_sha256": request.candidate_set_sha256,
        "confidence": 0,
        "consideration_id": "cover_consideration_1",
        "current_marker": True,
        "decision": "choose",
        "engine": "openrouter",
        "instruction_sha256": TASTE_JUDGE_INSTRUCTION_SHA256,
        "model": "example/vision",
        "packet_sha256": request.packet_sha256,
        "profile_digest": judge_profile().digest,
        "rules": [3, 1],
        "schema_sha256": TASTE_JUDGE_SCHEMA_SHA256,
        "schema_version": COVER_TASTE_VERDICT_SCHEMA,
        "version": 1,
    }
    assert alternative not in json.dumps(document)
