"""Choosing and sealing the decisions one taste build sends (#1372 A4)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from mechanical_librarian.application.cover_taste_contract import (
    admit_taste_build_request,
    taste_image_request_bytes,
    taste_request_frame_bytes,
)
from mechanical_librarian.application.cover_taste_inputs import (
    TasteCandidateSource,
    TasteDecisionSource,
    assemble_taste_inputs,
    taste_previews,
)
from mechanical_librarian.domain.cover_taste import TasteRule
from tests.support.cover_taste import canonical, stored_preview

NONCE = b"\x00" * 32
_STORE: dict[str, bytes] = {}


def _candidate(
    index: int,
    *,
    art: str | None = None,
    current: bool = False,
    rank: int | None = 0,
    wrong_shape: bool = False,
) -> TasteCandidateSource:
    payload = stored_preview(index)
    preview = canonical(payload)
    digest = hashlib.sha256(payload).hexdigest()
    _STORE[digest] = payload
    return TasteCandidateSource(
        reference=f"cover_ref_{index}",
        preview_sha256=digest,
        transport_sha256=preview.digest,
        # The original's decoded size, larger than any preview.
        width=600 + index,
        height=900 + index,
        current=current,
        rank=rank,
        ordinal=index,
        wrong_shape=wrong_shape,
        art=art if art is not None else f"art-{index}",
    )


def _decision(
    selection: int, candidates: tuple[TasteCandidateSource, ...]
) -> TasteDecisionSource:
    return TasteDecisionSource(
        f"selection-{selection}", "ebook", candidates[0].reference, candidates
    )


def _read(digest: str) -> bytes:
    return _STORE[digest]


def test_previews_are_chosen_current_other_arts_then_copies() -> None:
    chosen = _candidate(1, art="x")
    copy_of_chosen = _candidate(2, art="x", rank=0)
    current = _candidate(3, art="y", current=True, rank=None)
    other_late = _candidate(4, art="z", rank=5)
    other_early = _candidate(5, art="w", rank=1)
    decision = TasteDecisionSource(
        "s",
        "ebook",
        chosen.reference,
        (copy_of_chosen, other_late, current, chosen, other_early),
    )
    assert [candidate.ordinal for candidate in taste_previews(decision)] == [1, 3, 5, 4]


def test_previews_fall_back_to_copies_when_arts_run_out() -> None:
    chosen = _candidate(1, art="x")
    decision = _decision(1, (chosen, _candidate(2, art="x"), _candidate(3, art="y")))
    assert [candidate.ordinal for candidate in taste_previews(decision)] == [1, 3, 2]


def test_inputs_seal_closed_facts_only_and_admit() -> None:
    decisions = [
        _decision(
            index,
            (_candidate(index * 10 + 1), _candidate(index * 10 + 2, current=True)),
        )
        for index in range(3)
    ]
    inputs = assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    assert inputs is not None
    request = admit_taste_build_request(inputs.packet_document, inputs.images)
    assert len(request.images) == 6
    packet = json.loads(inputs.packet_document)
    assert set(packet) == {"budget", "decisions", "kept", "left_out", "schema_version"}
    assert (packet["kept"], packet["left_out"]) == ([], [])
    assert packet["budget"] == {"characters": 8_192, "rules": 40, "words": 1_200}
    for decision in packet["decisions"]:
        assert set(decision) == {"images", "medium"}
        for image in decision["images"]:
            assert set(image) == {
                "current",
                "group",
                "height",
                "reference",
                "role",
                "shape",
                "width",
            }
    # Forbidden-key scan: nothing identifying reaches the packet.
    text = inputs.packet_document
    for forbidden in ("selection-", "cover_ref_", "art-", "title", "path", "sha256"):
        assert forbidden not in text
    assert inputs.record == {
        "corrections": 0,
        "dropped": 0,
        "eligible": 3,
        "images": 6,
        "included": 3,
        "input_sha256": inputs.record["input_sha256"],
        "truncated": False,
    }


def test_same_art_shares_a_group_letter_and_shape_is_stated() -> None:
    decision = _decision(
        1,
        (
            _candidate(1, art="x"),
            _candidate(2, art="x"),
            _candidate(3, art="y", wrong_shape=True),
        ),
    )
    inputs = assemble_taste_inputs([decision], _read, kept=(), left_out=(), nonce=NONCE)
    assert inputs is not None
    images = json.loads(inputs.packet_document)["decisions"][0]["images"]
    assert [
        (image["group"], image["shape"], image["role"], image["width"], image["height"])
        for image in images
    ] == [
        ("A", "fits", "chosen", 601, 901),
        ("B", "wrong_shape", "passed_over", 603, 903),
        ("A", "fits", "passed_over", 602, 902),
    ]


def test_a_decision_whose_preview_is_missing_or_changed_is_dropped_and_counted() -> (
    None
):
    good = _decision(1, (_candidate(1), _candidate(2)))
    missing = _decision(2, (_candidate(3), _candidate(4)))
    del _STORE[missing.candidates[1].preview_sha256]
    changed_candidate = _candidate(5)
    changed = _decision(
        3, (replace(changed_candidate, transport_sha256="0" * 64), _candidate(6))
    )

    def read(digest: str) -> bytes:
        if digest not in _STORE:
            raise OSError("missing")
        return _STORE[digest]

    inputs = assemble_taste_inputs(
        [good, missing, changed], read, kept=(), left_out=(), nonce=NONCE
    )
    assert inputs is not None
    assert inputs.record["included"] == 1
    assert inputs.record["dropped"] == 2
    assert inputs.record["truncated"] is True
    assert (
        assemble_taste_inputs([missing], read, kept=(), left_out=(), nonce=NONCE)
        is None
    )


def test_120_decisions_cap_at_50_in_a_stable_sample() -> None:
    decisions = [_decision(index, (_candidate(index),)) for index in range(120)]
    first = assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    again = assemble_taste_inputs(
        list(reversed(decisions)), _read, kept=(), left_out=(), nonce=NONCE
    )
    assert first is not None and again is not None
    assert first.record["included"] == 50
    assert first.record["eligible"] == 120
    assert first.record["truncated"] is True
    assert first.record["input_sha256"] == again.record["input_sha256"]
    assert first.packet_document == again.packet_document


def test_image_cap_truncates_before_100_images() -> None:
    decisions = [
        _decision(index, tuple(_candidate(index * 10 + part) for part in range(4)))
        for index in range(30)
    ]
    inputs = assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    assert inputs is not None
    assert inputs.record["images"] == 100
    assert inputs.record["included"] == 25
    assert inputs.record["truncated"] is True


def test_references_are_fresh_per_build() -> None:
    decisions = [_decision(1, (_candidate(1),))]
    one = assemble_taste_inputs(decisions, _read, kept=(), left_out=())
    two = assemble_taste_inputs(decisions, _read, kept=(), left_out=())
    assert one is not None and two is not None
    assert one.images[0].reference != two.images[0].reference
    assert one.record["input_sha256"] == two.record["input_sha256"]


KEPT = (
    TasteRule(2, "drawn_to", "Calm type.", "owner"),
    TasteRule(5, "avoid", "Busy type!", "edited"),
)


def test_the_packet_carries_kept_rules_removed_texts_and_their_budget() -> None:
    decisions = [_decision(1, (_candidate(1),))]
    inputs = assemble_taste_inputs(
        decisions, _read, kept=KEPT, left_out=("Glossy finish.",), nonce=NONCE
    )
    assert inputs is not None
    packet = json.loads(inputs.packet_document)
    assert packet["kept"] == [
        {"section": "drawn_to", "text": "Calm type."},
        {"section": "avoid", "text": "Busy type!"},
    ]
    assert packet["left_out"] == ["Glossy finish."]
    assert packet["budget"] == {"characters": 8_172, "rules": 38, "words": 1_196}
    assert admit_taste_build_request(inputs.packet_document, inputs.images)


def test_the_byte_budget_starts_from_the_kept_rules_and_removed_texts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decisions = [_decision(1, (_candidate(1),))]
    alone = assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    assert alone is not None
    removed = tuple(f"Removed rule {index} " + "é" * 300 for index in range(40))
    # Exactly room for the decision with no owner rules, and less with them.
    monkeypatch.setattr(
        "mechanical_librarian.application.cover_taste_inputs.TASTE_MAX_REQUEST_BYTES",
        taste_request_frame_bytes(alone.packet_document)
        + 64
        + 64
        + 192
        + taste_image_request_bytes(alone.images[0].jpeg),
    )
    assert assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    assert (
        assemble_taste_inputs(
            decisions, _read, kept=KEPT, left_out=removed, nonce=NONCE
        )
        is None
    )


def test_corrections_lead_newest_first_up_to_25_then_the_stable_sample() -> None:
    """S4, J9: decisions that corrected a suggestion are sent first, newest
    first, at most 25; the rest, and corrections past 25, follow in the
    stable sample, and the record counts the corrections sent."""
    decisions = [
        replace(_decision(index, (_candidate(index),)), correction=index % 2 == 0)
        for index in range(60)
    ]
    built = assemble_taste_inputs(decisions, _read, kept=(), left_out=(), nonce=NONCE)
    assert built is not None
    corrections = [decision for decision in decisions if decision.correction]
    newest = corrections[::-1][:25]
    rest = sorted(
        (decision for decision in decisions if decision not in newest),
        key=lambda decision: hashlib.sha256(
            decision.selection_id.encode("utf-8")
        ).hexdigest(),
    )
    expected = [
        (decision.selection_id, [decision.candidates[0].transport_sha256])
        for decision in (newest + rest)[:50]
    ]
    assert built.record["corrections"] == 25
    assert built.record["included"] == 50
    assert (
        built.record["input_sha256"]
        == hashlib.sha256(
            json.dumps(expected, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    without = assemble_taste_inputs(
        [replace(decision, correction=False) for decision in decisions],
        _read,
        kept=(),
        left_out=(),
        nonce=NONCE,
    )
    assert without is not None and without.record["corrections"] == 0
    assert without.record["input_sha256"] != built.record["input_sha256"]
