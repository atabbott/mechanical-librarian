"""Rich, deterministic, entirely synthetic data for interface qualification."""

from __future__ import annotations

import functools
import struct
import time
import zlib
from copy import deepcopy
from typing import cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from mechanical_librarian.adapters.persistence.owner_settings import _settings_view
from mechanical_librarian.application.metadata_policy import rich_qualification
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument

BUILD_IDENTITY = "a" * 64
HOSTILE_TEXT = "Hostile <img src=x onerror=window.__metadataExecuted=true>"
TERMINAL_FAILURE_COPY = (
    "Provider credentials need owner attention. "
    "<img src=x onerror=window.__terminalFailureExecuted=true> "
    + "UnbrokenTerminalPreparationCondition"
    * 12
)
UNBROKEN_TITLE_SEGMENT = "UnbrokenSyntheticCatalogueToken" * 12
LONG_TITLE = (
    "The Clockmaker's Cartography of Quietly Impossible Libraries — "
    f"{UNBROKEN_TITLE_SEGMENT} — an intentionally long synthetic title for "
    "narrow-layout qualification"
)
# A description long enough that the row's change column, and the expanded
# fact beside it, are the only things bounding it.
LONG_DESCRIPTION = (
    "An intentionally unbounded synthetic description that keeps going well "
    "past any width a row column has, so a projection that forgot to bound it "
    f"is a layout fault rather than a subtle one. {UNBROKEN_TITLE_SEGMENT} "
    "And it continues past that too, in ordinary prose with ordinary spaces, "
    "so a wrap that is allowed to happen has somewhere to happen."
)
# Layout data consumes the same complete read-only summary as the API.
_RICH_QUALIFICATION = rich_qualification(MetadataPolicyProfile.builtin())


def _work(
    number: int,
    title: str,
    authors: list[str],
    library: str,
    media_kind: str,
    *,
    case: str | None,
    attention: int,
    dispositions: list[str],
    preparation: dict[str, object] | None = None,
    preparation_outcomes: list[dict[str, object]] | None = None,
    summary: dict[str, object] | None = None,
    cover: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        **({} if cover is None else {"cover": cover}),
        "work_id": f"library_work_{number:064d}",
        "review_case_id": case,
        "title": title,
        "authors": authors,
        "library": library,
        "media_kind": media_kind,
        "item": f"synthetic-item-{number}",
        "attention_count": attention,
        "attention_state": ("needs_attention" if attention else "no_attention_needed"),
        "detail_url": f"/review/{case}" if case else None,
        # The row says what its address opens; every work in this cohort that
        # has one has a review case behind it.
        "detail_kind": "case" if case else None,
        "dispositions": dispositions,
        "preparation": preparation,
        "preparation_outcomes": preparation_outcomes or [],
        "summary": summary,
    }


TERMINAL_LIBRARY_WORK = _work(
    6,
    "Catalogue-only work",
    ["Oren Finch"],
    "Household ebooks",
    "epub",
    case=None,
    attention=0,
    dispositions=[],
    preparation={
        "attempt": 1,
        "diagnostic_reference": "prep-0123456789abcdef",
        # A provider-credential terminal says nothing about the media.
        "media_health": [],
        "provider": "hardcover-main",
        "reason": TERMINAL_FAILURE_COPY,
        "recovery": "Update the configured provider credentials, then scan this library again.",
        "recovery_attempt": 0,
        "stage": "provider",
        "status": "authentication",
    },
    preparation_outcomes=[
        {
            "provider_requests": 1,
            "lane_kind": "work",
            "provider": "hardcover-main",
            "reason": "Provider credentials need owner attention.",
            "status": "authentication",
        }
    ],
    summary={
        "fields": [],
        "reason": "Provider credentials need owner attention.",
        "state": "preparation_failed",
    },
)


# The library cover case-1's row shows: its current ABS cover, at that
# candidate's size (#1290). Every other row keeps its spine mark.
LIBRARY_COVER_PREVIEW = "b" * 64
LIBRARY_WORKS: tuple[dict[str, object], ...] = (
    _work(
        1,
        HOSTILE_TEXT,
        ["Mara Venn", "Ivo North"],
        "Household ebooks",
        "epub",
        case="case-1",
        attention=2,
        dispositions=["unresolved_choice", "automatic", "ineligible"],
        cover={
            "height": 1200,
            "preview_sha256": LIBRARY_COVER_PREVIEW,
            "url": "/api/v1/library/cover?"
            + urlencode(
                {
                    "connection": "synthetic",
                    "library": "Household ebooks",
                    "item": "synthetic-item-1",
                    "preview": LIBRARY_COVER_PREVIEW,
                }
            ),
            "width": 800,
        },
    ),
    _work(
        2,
        LONG_TITLE,
        ["Aster Vale"],
        "Spoken stories",
        "m4b",
        case="case-2",
        attention=2,
        dispositions=["unresolved_choice", "no_safe_proposal"],
    ),
    _work(
        3,
        "Paper Moons over Larkspur Station",
        ["Nia Calder"],
        "Household ebooks",
        "epub",
        case="case-3",
        attention=1,
        dispositions=["unresolved_choice"],
    ),
    _work(
        4,
        "A Field Guide to Borrowed Weather",
        ["Rowan Pike"],
        "Research shelf",
        "epub",
        case="case-4",
        attention=1,
        dispositions=["manual_hold"],
    ),
    _work(
        5,
        "The Glass Orchard",
        ["Tamsin Reed"],
        "Spoken stories",
        "m4b",
        case="case-5",
        attention=0,
        dispositions=["automatic"],
    ),
    TERMINAL_LIBRARY_WORK,
    _work(
        7,
        "Resolved work with retained history",
        ["Juniper Ash"],
        "Research shelf",
        "epub",
        case="case-7",
        attention=0,
        dispositions=["human_decision"],
    ),
    _work(
        8,
        "Automatic-only edition",
        ["Sable Quill"],
        "Household ebooks",
        "epub",
        case="case-8",
        attention=0,
        dispositions=["automatic"],
    ),
    _work(
        9,
        "Pre-admission observation",
        ["Ember Lane"],
        "Incoming shelf",
        "epub",
        case=None,
        attention=0,
        dispositions=["pre_admission"],
        preparation_outcomes=[
            {
                "provider_requests": 2,
                "lane_kind": "work",
                "provider": "hardcover-main",
                "reason": "Returned matching metadata for this work.",
                "status": "candidates",
            },
            {
                "provider_requests": 0,
                "lane_kind": "work",
                "provider": "google-books",
                "reason": "Was not enabled for this lane, so it was never asked.",
                "status": "disabled",
            },
        ],
        summary={
            "fields": [
                {
                    "destination": "title",
                    "state": "value",
                    "value": "Pre-admission observation",
                },
                {"destination": "authors", "state": "value", "value": ["Ember Lane"]},
                {"destination": "series", "state": "missing", "value": None},
            ],
            "reason": (
                "No review decision is recorded for this work. Its current values "
                "are shown exactly as Mechanical Librarian last read them."
            ),
            "state": "no_decision_recorded",
        },
    ),
    _work(
        10,
        "Retired exact-current case",
        ["Cedar Moss"],
        "Spoken stories",
        "m4b",
        case="case-stale",
        attention=0,
        dispositions=["superseded"],
    ),
    _work(
        11,
        "No eligible proposal",
        [],
        "Incoming shelf",
        "epub",
        case="case-11",
        attention=0,
        dispositions=["ineligible"],
    ),
    _work(
        12,
        "Unicode shelves: 雨の地図 · خريطة هادئة · 🧭",
        ["Renée D'Arcy"],
        "Family archive — shared",
        "epub",
        case="case-12",
        attention=0,
        dispositions=["resolved"],
    ),
    # Public-domain catalogue examples keep the installed visual cohort broad
    # without drawing on a contributor's library or a live provider response.
    _work(
        13,
        "Frankenstein; or, The Modern Prometheus",
        ["Mary Shelley"],
        "Local collection",
        "epub",
        case="case-13",
        attention=1,
        dispositions=["unresolved_choice", "duplicate"],
    ),
    _work(
        14,
        "Pride and Prejudice",
        ["Jane Austen"],
        "Project Gutenberg",
        "m4b",
        case="case-14",
        attention=0,
        dispositions=["automatic"],
    ),
    _work(
        15,
        "Moby-Dick; or, The Whale",
        ["Herman Melville"],
        "Google Books catalogue",
        "epub",
        case="case-15",
        attention=0,
        dispositions=["corroborated"],
    ),
    _work(
        16,
        "Dracula",
        ["Bram Stoker"],
        "Open Library catalogue",
        "m4b",
        case="case-16",
        attention=0,
        dispositions=["missing"],
    ),
    _work(
        17,
        "The Time Machine",
        ["H. G. Wells"],
        "Local collection",
        "epub",
        case="case-17",
        attention=0,
        dispositions=["conflict"],
    ),
    _work(
        18,
        "The Adventures of Sherlock Holmes",
        ["Arthur Conan Doyle"],
        "Project Gutenberg",
        "m4b",
        case="case-18",
        attention=0,
        dispositions=["duplicate"],
    ),
    _work(
        19,
        "The Republic",
        ["Plato"],
        "Google Books catalogue",
        "epub",
        case="case-19",
        attention=0,
        dispositions=["unknown"],
    ),
    _work(
        20,
        "The Odyssey",
        ["Homer"],
        "Open Library catalogue",
        "m4b",
        case="case-20",
        attention=0,
        dispositions=["pagination"],
    ),
)


def _field(
    destination: str,
    current: object,
    proposed: list[object],
    *,
    disposition: str = "unresolved_choice",
    needs_attention: bool = True,
    human_decision: dict[str, object] | None = None,
    automatic_decision: dict[str, object] | None = None,
    manual_hold: bool = False,
    notices: list[str] | None = None,
    provenance: list[str] | None = None,
    corroboration_sources: list[str] | None = None,
    classification: str | None = None,
    classification_reason: str | None = None,
    editable: bool | None = None,
    navigation_role: str | None = None,
    inventory_role: str | None = None,
    preservation_only: bool | None = None,
    owner_control: dict[str, object] | None = None,
) -> dict[str, object]:
    # The installed client fails closed to the exact owner-decision vocabulary.
    # Synthetic cases mirror the domain rule: an owner can retain the incumbent
    # only alongside at least one genuinely different eligible alternative.
    decision_actions = (
        ["keep_current", "accept"]
        if any(value != current for value in proposed)
        else []
    )
    inferred_classification = (
        "human_recorded"
        if human_decision is not None
        else "automatic_recorded"
        if automatic_decision is not None
        else "needs_attention"
        if needs_attention
        else "unsupported"
    )
    resolved_classification = classification or inferred_classification
    inferred_reason = (
        "This field needs your review."
        if needs_attention
        else "Automatic handling recorded this field; you may inspect or override it."
        if automatic_decision is not None
        else "There is no owner decision available for this field."
    )
    return {
        "classification": resolved_classification,
        "classification_reason": classification_reason or inferred_reason,
        "destination": destination,
        "decision_actions": decision_actions,
        "deterministic_disposition": disposition,
        # A producer suspension reason: the cohort's fields are guarded from
        # external update (#1286).
        "effect_capability": "insufficient_permission",
        "editable": editable
        if editable is not None
        else (
            (human_decision is None and automatic_decision is not None)
            or needs_attention
        ),
        "needs_attention": needs_attention,
        "manual_hold": manual_hold,
        "manual_required": needs_attention,
        "navigation_role": navigation_role
        or ("attention" if needs_attention else "inspect"),
        "inventory_role": inventory_role
        or (
            "contextual"
            if resolved_classification == "unsupported" and not proposed
            else "primary"
        ),
        "current": {
            "kind": "synthetic",
            "state": "missing" if current is None else "value",
            "value": current,
        },
        "proposed_values": [
            {"kind": "synthetic", "value": value} for value in proposed
        ],
        "choice_contexts": [
            {
                "alternative_id": None,
                "evidence": [
                    {
                        "observation": "This value is currently present in the local library.",
                        "role": "local_presence",
                        "source": "Local library record",
                    }
                ],
                "limitations": notices or [],
                "rationale": "Keep the value currently held by the local library.",
                "recommendation": "no_recommendation",
                "recommendation_reason": "No deterministic recommendation is retained for the current value.",
                "selection": "current",
            },
            *[
                {
                    "alternative_id": f"alternative-{destination}-{index}",
                    "evidence": [
                        {
                            "observation": "A synthetic catalogue observation supports this choice.",
                            "role": "external_corroboration",
                            "source": "Open Library",
                        }
                    ],
                    "limitations": notices or [],
                    "rationale": "This retained alternative is eligible for owner selection.",
                    "recommendation": "no_recommendation",
                    "recommendation_reason": "No deterministic recommendation is retained for this alternative.",
                    "selection": "proposed",
                }
                for index, _ in enumerate(proposed, start=1)
            ],
        ],
        "preservation_only": preservation_only
        if preservation_only is not None
        else False,
        "proposal_id": f"proposal-{destination}",
        "target_fingerprint": "f" * 64,
        "human_decision": human_decision,
        "automatic_decision": automatic_decision,
        "automatic_withheld_reason": None,
        "automatic_selection_sources": [],
        "notices": notices or [],
        "provenance": provenance or [],
        "corroboration_sources": corroboration_sources or [],
        "owner_control": owner_control,
        "uncertainty": [],
    }


def _case(case_id: str) -> dict[str, object]:
    if case_id == "case-stale":
        return {
            "schema_version": "review-case-detail-v6",
            "recording_identity_basis": None,
            "audiobook_review_scope": None,
            "media_health": [],
            "writes_consented": False,
            "review_case_id": case_id,
            "current": False,
            "comparison_notices": [],
            "target": {
                "connection": "primary",
                "library": "Spoken stories",
                "media_kind": "m4b",
                "authors": ["Cedar Moss"],
                "item": "internal-retired-case",
                "title": "Retired exact-current case",
            },
            "fields": [],
        }

    if case_id == "case-owner":
        # Every owner control at once, with the longest words the store admits
        # in a value, so the layout proof meets its worst case (#1285).
        long_title = "The Incomprehensibilities of Counterrevolutionary Cartographers"
        return {
            **_case("case-1"),
            "review_case_id": case_id,
            "fields": [
                _field(
                    "title",
                    long_title,
                    ["Incomprehensibilities"],
                    owner_control={
                        "entry": {
                            "max_characters": 512,
                            "suggestions": [
                                {"rule": "title_case", "value": long_title},
                                {"rule": "strip_part", "value": "Counterrevolutionary"},
                                {
                                    "rule": "previous",
                                    "value": "Uncharacteristically Interdisciplinary",
                                },
                            ],
                        },
                        "subset": None,
                        "withdraw": None,
                    },
                ),
                _field(
                    "genres",
                    ["Historiography"],
                    [["Historiography", "Psychogeography"]],
                    owner_control={
                        "entry": None,
                        "subset": {
                            "max_items": 32,
                            "options": [
                                {
                                    "value": value,
                                    "current": value == "Historiography",
                                    "proposed": value == "Psychogeography",
                                    "previous": value
                                    == "Counterfactual speculative fiction",
                                }
                                for value in (
                                    "Historiography",
                                    "Psychogeography",
                                    "Counterfactual speculative fiction",
                                )
                            ],
                        },
                        "withdraw": None,
                    },
                ),
            ],
        }

    resolved = case_id in {"case-7", "case-12"}
    automatic = case_id in {"case-5", "case-8"}
    current_only = case_id == "case-11"
    equal_preservation = case_id == "case-equal"
    provider_evidence = [
        "Local catalogue record: current field value",
        "Project Gutenberg record: title and contributor corroboration",
        "Google Books catalogue record: conflicting presentation",
        "Open Library record: duplicate edition candidate",
    ]
    fields = [
        _field(
            "title",
            "Current <script> title",
            []
            if current_only
            else [
                "Current <script> title"
                if equal_preservation
                else "The Clockmaker's Cartography",
                *(
                    []
                    if equal_preservation
                    else ["Cartography of Impossible Libraries", LONG_TITLE]
                ),
            ],
            needs_attention=not (resolved or automatic or current_only),
            manual_hold=case_id == "case-4",
            disposition=(
                "propose_change"
                if automatic
                else "ineligible"
                if current_only
                else "unresolved_choice"
            ),
            human_decision=(
                {
                    "outcome": "accept",
                    "actor": "owner",
                    "selected_value": {
                        "kind": "synthetic",
                        "value": "The Clockmaker's Cartography",
                    },
                }
                if resolved
                else None
            ),
            automatic_decision=(
                {
                    "decision_id": "automatic-title",
                    "outcome": "keep_current",
                    "origin": "automatic_policy",
                    "selected_value": None,
                }
                if automatic
                else None
            ),
            notices=[
                "Three independent catalogue observations disagree on presentation.",
                "Punctuation and subtitle boundaries remain an owner choice.",
            ],
            provenance=provider_evidence,
            preservation_only=equal_preservation,
        ),
        _field(
            "authors",
            ["Mara Venn", "Ivo North"],
            (
                []
                if current_only
                else [["Mara Venn"], ["Mara Venn", "Ivo North", "Pax Rill"]]
            ),
            needs_attention=not (resolved or automatic or current_only),
            disposition="ineligible" if current_only else "unresolved_choice",
            human_decision=(
                {
                    "outcome": "keep_current",
                    "actor": "owner",
                    "selected_value": None,
                }
                if resolved
                else None
            ),
            automatic_decision=(
                {
                    "decision_id": "automatic-authors",
                    "outcome": "keep_current",
                    "origin": "automatic_policy",
                    "selected_value": None,
                }
                if automatic
                else None
            ),
            corroboration_sources=(
                ["Hardcover", "Open Library"] if case_id == "case-1" else None
            ),
        ),
        _field(
            "subtitle",
            None,
            [],
            disposition="ineligible",
            needs_attention=False,
            notices=[
                "No eligible proposed value is supported by the retained evidence."
            ],
            classification="unsupported",
            classification_reason=(
                "There is no owner decision available for this field."
                if current_only
                else "This field is unavailable for the current service capability."
            ),
            editable=False,
            navigation_role="inspect",
        ),
        _field(
            "genres",
            [],
            [],
            disposition="ineligible",
            needs_attention=False,
            notices=[
                "No eligible proposed value is supported by the retained evidence."
            ],
            classification="unsupported",
            classification_reason="This field is unavailable for the current service capability.",
            editable=False,
            navigation_role="inspect",
            inventory_role="primary",
        ),
        _field(
            "series",
            {"name": "Atlas Cycle", "sequence": "2.5"},
            [],
            disposition="ineligible" if current_only else "propose_change",
            needs_attention=False,
            automatic_decision=(
                None
                if current_only
                else {
                    "decision_id": "automatic-series",
                    "outcome": "keep_current",
                    "origin": "automatic_policy",
                    "selected_value": None,
                }
            ),
        ),
        _field(
            "publishedDate",
            "2024-08",
            [] if current_only else ["2024-08"],
            disposition="ineligible" if current_only else "propose_change",
            needs_attention=False,
            automatic_decision=(
                {
                    "decision_id": "automatic-publication",
                    "outcome": "candidate",
                    "origin": "automatic_policy",
                    "selected_value": {"kind": "synthetic", "value": "2024-08"},
                }
                if not current_only
                else None
            ),
        ),
    ]
    if automatic:
        fields.append(
            _field(
                "isbn",
                None,
                ["9780306406157"],
                disposition="propose_change",
                needs_attention=False,
                automatic_decision={
                    "decision_id": "automatic-isbn",
                    "outcome": "candidate",
                    "origin": "automatic_policy",
                    "selected_value": {"kind": "synthetic", "value": "9780306406157"},
                },
                notices=["verified_manifestation_isbn"],
            )
        )
    if case_id in {"case-1", "case-2", "case-3"}:
        reviewer_status = {
            "case-1": "completed",
            "case-2": "failed",
            "case-3": "unavailable",
        }[case_id]
        fields.append(
            {
                **_field(
                    "cover",
                    "cover-current-synthetic",
                    ["cover-choice-synthetic"],
                    disposition="unresolved_choice",
                    needs_attention=True,
                    notices=["Cover comparison remains an owner choice."],
                ),
                "cover_consideration": {
                    "candidates": [
                        *(
                            [
                                {
                                    "artifact_id": "cover-current-synthetic",
                                    "deterministic_citations": [
                                        "cover-ref-current",
                                        "cover-packet-synthetic",
                                    ],
                                    "deterministic_rank": None,
                                    "deterministic_rank_key": [
                                        1,
                                        0,
                                        -960000,
                                        -800,
                                        -1200,
                                        0,
                                        "cover-ref-current",
                                    ],
                                    "height": 1200,
                                    "identity": {
                                        "candidate_reference": "cover-ref-current",
                                        "evidence_references": [],
                                        "outcome": "verified_work_match",
                                        "preview_reference": "cover-preview-current",
                                        "reasons": [],
                                    },
                                    "incumbent": True,
                                    "match_state": "same_book",
                                    "observation": None,
                                    "original_sha256": "a" * 64,
                                    "ordinal": 0,
                                    "preview_sha256": "b" * 64,
                                    "reference": "cover-ref-current",
                                    "selectable": False,
                                    "manual_eligible": False,
                                    "source_kinds": ["abs"],
                                    "state": "excluded",
                                    "width": 800,
                                }
                            ]
                            if case_id != "case-3"
                            else []
                        ),
                        {
                            "artifact_id": "cover-choice-synthetic",
                            "deterministic_citations": [
                                "cover-ref-choice",
                                "cover-packet-synthetic",
                            ],
                            "deterministic_rank": 1,
                            "deterministic_rank_key": [
                                1,
                                0,
                                -960000,
                                -800,
                                -1200,
                                0 if case_id == "case-3" else 1,
                                "cover-ref-choice",
                            ],
                            "height": 280,
                            "identity": {
                                "candidate_reference": "cover-ref-choice",
                                "evidence_references": [],
                                "outcome": "verified_work_match",
                                "preview_reference": "cover-preview-choice",
                                "reasons": [],
                            },
                            "incumbent": False,
                            "match_state": "same_book",
                            "observation": None,
                            "original_sha256": "c" * 64,
                            "ordinal": 0 if case_id == "case-3" else 1,
                            "preview_sha256": "d" * 64,
                            "reference": "cover-ref-choice",
                            "selectable": True,
                            "manual_eligible": True,
                            "source_kinds": ["hardcover"],
                            "state": "recommended",
                            "width": 169,
                        },
                        *(
                            [
                                {
                                    "artifact_id": "cover-unconfirmed-synthetic",
                                    "deterministic_citations": [],
                                    "deterministic_rank": None,
                                    "deterministic_rank_key": None,
                                    "height": 480,
                                    "identity": {
                                        "candidate_reference": "cover-ref-unconfirmed",
                                        "evidence_references": [],
                                        "outcome": "unknown",
                                        "preview_reference": "cover-preview-unconfirmed",
                                        "reasons": ["insufficient_identity_evidence"],
                                    },
                                    "incumbent": False,
                                    "match_state": "match_not_confirmed",
                                    "observation": None,
                                    "original_sha256": "1" * 64,
                                    "ordinal": 2,
                                    "preview_sha256": "2" * 64,
                                    "reference": "cover-ref-unconfirmed",
                                    "selectable": False,
                                    "manual_eligible": True,
                                    "source_kinds": ["open_library"],
                                    "state": "excluded",
                                    "width": 305,
                                }
                            ]
                            if case_id == "case-1"
                            else []
                        ),
                    ],
                    "consideration_id": "cover-consideration-" + reviewer_status,
                    "choice_contexts": [
                        {
                            "alternative_id": None,
                            "selection": "current",
                            "evidence": [
                                {
                                    "source": "Local library record",
                                    "role": "local_presence",
                                    "observation": "Local library cover retained.",
                                }
                            ],
                            # The producer's own sentence for an unavailable
                            # incumbent preview (cover_consideration), never a
                            # code the owner would read raw (#1286).
                            "limitations": [
                                "Preview unavailable for this retained cover."
                            ]
                            if case_id == "case-3"
                            else [],
                            "rationale": "Keep the cover currently held by the local library.",
                            "recommendation": "no_recommendation",
                            "recommendation_reason": "No recommendation retained.",
                        },
                        {
                            "alternative_id": "cover-ref-choice",
                            "selection": "proposed",
                            "evidence": [
                                {
                                    "source": "Open Library",
                                    "role": "external_corroboration",
                                    "observation": "Retained catalogue cover supports this choice.",
                                }
                            ],
                            "limitations": [],
                            "rationale": "This retained cover candidate is selectable for owner review.",
                            "recommendation": "recommended",
                            "recommendation_reason": "Deterministic confirmed-cover policy recommends this retained cover.",
                        },
                        *(
                            [
                                {
                                    "alternative_id": "cover-ref-unconfirmed",
                                    "selection": "proposed",
                                    "evidence": [],
                                    "limitations": ["match_not_confirmed"],
                                    "rationale": "Retained cover requires owner confirmation.",
                                    "recommendation": "no_recommendation",
                                    "recommendation_reason": "Match not confirmed.",
                                }
                            ]
                            if case_id == "case-1"
                            else []
                        ),
                    ],
                    "current": True,
                    "incumbent": {
                        "reference": None
                        if case_id == "case-3"
                        else "cover-ref-current",
                        "state": "unavailable" if case_id == "case-3" else "available",
                    },
                    "omissions": [],
                    "policy": {
                        "acceptable_candidate_references": ["cover-ref-choice"],
                        "actual_target_state": (
                            "known_empty" if case_id == "case-3" else "confirmed"
                        ),
                        "cover_packet_reference": "cover-packet-synthetic",
                        "identity_classifier_reference": "cover-identity-synthetic",
                        "identity_report_sha256": "e" * 64,
                        "observation_report_sha256": None,
                        "observation_taxonomy_reference": "cover-taxonomy-synthetic",
                        "owner_ready": True,
                        "policy_reference": "cover-policy-synthetic",
                        "reasons": ["policy_satisfied"],
                        "recommended_candidate_reference": "cover-ref-choice",
                        "schema_version": "cover-policy-report-v2",
                        "status": "recommended",
                    },
                    "policy_preferences": {
                        "avoid_promotional_text": False,
                        "avoid_tie_in_branding": False,
                        "prefer_minimal_composition": False,
                        "schema_version": "cover-policy-v1",
                    },
                    "policy_reference": "cover-policy-synthetic",
                    "provider_fetching": False,
                    "ready": True,
                    "reviewer_access_revision": (None if case_id == "case-3" else 7),
                    "reviewer_engine": None if case_id == "case-3" else "codex",
                    "reviewer_model": (
                        None if case_id == "case-3" else "contained-reviewer"
                    ),
                    "reviewer_status": reviewer_status,
                    "reviewer_unavailable_reason": None,
                    "schema_version": "cover-consideration-v4",
                    "supersedes_id": (
                        "cover-consideration-historical"
                        if reviewer_status == "failed"
                        else None
                    ),
                    "target_fingerprint": "f" * 64,
                },
            }
        )
    work = next(
        (item for item in LIBRARY_WORKS if item["review_case_id"] == case_id),
        LIBRARY_WORKS[0],
    )
    return {
        "schema_version": "review-case-detail-v6",
        "recording_identity_basis": None,
        "audiobook_review_scope": None,
        "media_health": [],
        "writes_consented": False,
        "review_case_id": case_id,
        "current": True,
        "comparison_notices": [],
        "target": {
            "connection": "primary",
            "library": work["library"],
            "media_kind": work["media_kind"],
            "authors": work["authors"],
            "item": f"internal-{case_id}",
            "title": work["title"],
        },
        "fields": fields,
    }


def _clear(item: dict[str, object]) -> bool:
    """Nothing to decide, and a provider matched it (#1407)."""
    return (
        int(cast(int, item["attention_count"])) == 0
        and item["attention_state"] != "not_matched"
    )


def _library(query: dict[str, list[str]]) -> dict[str, object]:
    lens = query.get("lens", ["all"])[0]
    needle = query.get("q", [""])[0].casefold().strip()
    # Like the server, counts cover the search but not the lens or the page.
    matched = [
        item
        for item in LIBRARY_WORKS
        if not needle
        or needle
        in " ".join(
            [
                str(item["title"]),
                *[str(author) for author in item["authors"]],
                str(item["library"]),
                str(item["media_kind"]),
            ]
        ).casefold()
    ]
    works = matched
    if lens == "needs_attention":
        works = [item for item in works if int(cast(int, item["attention_count"])) > 0]
    elif lens == "no_attention_needed":
        works = [item for item in works if _clear(item)]
    elif lens == "not_matched":
        works = [item for item in works if item["attention_state"] == "not_matched"]
    cursor = query.get("cursor", [""])[0]
    page = {"page-2": 2, "page-3": 3, "page-4": 4}.get(cursor, 1)
    start = (page - 1) * 6
    end = start + 6
    return {
        "schema_version": "review-library-v3",
        "lens": lens,
        "query": needle or None,
        "next_cursor": f"page-{page + 1}" if len(works) > end else None,
        "previous_cursor": f"page-{page - 1}" if page > 1 else None,
        "page": page,
        "counts": {
            "needs_attention": sum(
                int(cast(int, item["attention_count"])) > 0 for item in matched
            ),
            "all": len(matched),
            "no_attention_needed": sum(_clear(item) for item in matched),
            "not_matched": sum(
                item["attention_state"] == "not_matched" for item in matched
            ),
        },
        "items": deepcopy(works[start:end]),
    }


def _attention() -> dict[str, object]:
    items = []
    for work in LIBRARY_WORKS[:4]:
        destinations = (
            ("title", "authors")
            if int(cast(int, work["attention_count"])) > 1
            else ("title",)
        )
        for destination in destinations:
            items.append(
                {
                    "review_case_id": work["review_case_id"],
                    "destination": destination,
                    "title": work["title"],
                    "authors": work["authors"],
                    "library": work["library"],
                    "media_kind": work["media_kind"],
                    "item": work["item"],
                    "detail_url": f"/review/{work['review_case_id']}?field={destination}",
                    "classification_reason": "This field needs your review.",
                    "notices": ["Conflicting retained evidence"],
                    "provenance": [
                        "catalogue observation",
                        "local exact-current value",
                    ],
                    "work_attention_count": work["attention_count"],
                }
            )
    return {
        "schema_version": "attention-v1",
        "query": None,
        "next_cursor": None,
        "total": len(items),
        "items": items,
    }


# The value shapes an Activity row has to survive: a scalar, a joined list, a
# series membership with an index, an opaque cover identity, an unbounded
# description, and a policy setting. Each carries the compact form the row
# shows and the whole sentence the row announces, exactly as the projection
# emits them, so the layout is stressed by real widths rather than by labels.
_COHORT_FIELD_LABELS = {
    "authors": "Authors",
    "cover": "Cover",
    "description": "Description",
    "genres": "Genres",
    "isbn": "ISBN",
    "policy": "Policy",
    "series": "Series",
    "title": "Title",
}
_COHORT_CHANGES = {
    "title": (
        "Paper Moons: Larkspur \u2192 Paper Moons over Larkspur Station",
        "Set Title from Paper Moons: Larkspur to Paper Moons over Larkspur Station",
        "Paper Moons over Larkspur Station",
    ),
    "authors": (
        "Not set \u2192 Ursula K. Le Guin, Margaret Atwood, \u96e8\u306e\u5730\u56f3",
        "Set Authors from Not set to Ursula K. Le Guin, Margaret Atwood, \u96e8\u306e\u5730\u56f3",
        "Ursula K. Le Guin, Margaret Atwood, \u96e8\u306e\u5730\u56f3",
    ),
    "series": (
        "Not set \u2192 Earthsea Cycle (1), Hainish Cycle (4)",
        "Set Series from Not set to Earthsea Cycle (1), Hainish Cycle (4)",
        "Earthsea Cycle (1), Hainish Cycle (4)",
    ),
    "cover": (
        "Current cover \u2192 selected cover",
        "Set Cover from the current image to the selected image",
        "A recorded cover image",
    ),
    "description": (
        LONG_DESCRIPTION[:119] + "\u2026",
        "Set Description from Not set to " + LONG_DESCRIPTION[:119] + "\u2026",
        LONG_DESCRIPTION[:511] + "\u2026",
    ),
    "policy": ("3 changes activated", "3 changes activated", "Updated"),
}
# One preparation's automatic checks of a single work, across three fields.
# They share the preparation key the ledger gathers them by into one row (#1281).
CHECKED_WORK = "Synthetic checked work"
CHECK_GROUP = "report_" + "c" * 64
_COHORT_CHECKS = {
    "title": (
        "Kept Paper Moons over Larkspur Station",
        "Kept Title as Paper Moons over Larkspur Station",
        "Paper Moons over Larkspur Station",
    ),
    "isbn": ("Left ISBN unchanged", "Left ISBN unchanged", "9780000000002"),
    "genres": ("Kept Fantasy", "Kept Genres as Fantasy", "Fantasy"),
}


def _cohort_change(work: str, destination: str) -> tuple[str, str, str]:
    return (_COHORT_CHECKS if work == CHECKED_WORK else _COHORT_CHANGES)[destination]


_COHORT_POLICY_CHANGES = [
    {
        "field": "Title layout",
        "setting": "Review mode",
        "previous": "Ask me",
        "next": "Automatic",
    },
    {
        "field": "Description fill",
        "setting": "Representation",
        "previous": "Safe plain text",
        "next": "Visible Google text",
    },
    {
        "field": "Cover",
        "setting": "Review mode",
        "previous": "Ask me",
        "next": "Automatic",
    },
]


def _cohort_event_type(destination: str, origin: str, failure: str | None) -> str:
    if destination == "policy":
        return "policy"
    if failure:
        return "safety"
    return "review" if origin in {"human", "owner_retry"} else "metadata"


def _cohort_category(destination: str, origin: str, failure: str | None) -> str:
    return {
        "policy": "policy",
        "safety": "recovery",
        "review": "owner",
        "metadata": "automatic",
    }[_cohort_event_type(destination, origin, failure)]


def _activity(query: dict[str, list[str]]) -> dict[str, object]:
    events = [
        (
            "Equal automatic title",
            "Household ebooks",
            "title",
            "no_change_required",
            "automatic_policy",
            None,
        ),
        (
            "The Glass Orchard",
            "Spoken stories",
            "title",
            "verified",
            "automatic_policy",
            None,
        ),
        (LONG_TITLE, "Spoken stories", "authors", "applying", "human", None),
        (
            "Paper Moons over Larkspur Station",
            "Household ebooks",
            "title",
            "stale",
            "human",
            "target_changed",
        ),
        (
            "A Field Guide to Borrowed Weather",
            "Research shelf",
            "title",
            "recovered",
            "human",
            "readback",
        ),
        (
            "Unicode shelves: 雨の地図",
            "Family archive — shared",
            "series",
            "paused",
            "human",
            "consent_revoked",
        ),
        (
            HOSTILE_TEXT,
            "Household ebooks",
            "title",
            "ambiguous",
            "future_provider_origin",
            "ambiguous",
        ),
        (
            "Resolved work with retained history",
            "Research shelf",
            "authors",
            "resolved",
            "human",
            None,
        ),
        (
            "Automatic-only edition",
            "Household ebooks",
            "series",
            "running",
            "automatic_policy",
            None,
        ),
        (
            "Synthetic completed cover advice",
            "Household ebooks",
            "cover",
            "completed",
            "automatic_pipeline",
            None,
        ),
        (
            "Synthetic unavailable cover advice",
            "Spoken stories",
            "cover",
            "unavailable",
            "owner_retry",
            None,
        ),
        (
            "Synthetic failed cover advice",
            "Research shelf",
            "cover",
            "failed",
            "owner_retry",
            None,
        ),
        (
            "Synthetic stale cover advice",
            "Household ebooks",
            "cover",
            "stale",
            "human",
            "currentness",
        ),
        (
            "Synthetic superseded cover advice",
            "Spoken stories",
            "cover",
            "superseded",
            "human",
            None,
        ),
        (
            "Work with an unbounded description",
            "Household ebooks",
            "description",
            "verified",
            "automatic_policy",
            None,
        ),
        (
            "Library policy",
            "Library policy",
            "policy",
            "completed",
            "policy",
            None,
        ),
        *(
            (
                CHECKED_WORK,
                "Household ebooks",
                destination,
                state,
                "automatic_policy",
                None,
            )
            for destination, state in (
                ("title", "no_change_required"),
                ("isbn", "recorded"),
                ("genres", "recorded"),
            )
        ),
    ]
    return {
        "schema_version": "continuous-field-activity-v3",
        "current_waiting": None,
        "owner_action_required": 0,
        "backup_ready": True,
        "paused": False,
        "configured_libraries": [
            {
                "connection": "primary",
                "library": "Household ebooks",
                "prepared": {"automatic": 2, "human": 3, "total": 5},
            },
            {
                "connection": "primary",
                "library": "Spoken stories",
                "prepared": {"automatic": 1, "human": 1, "total": 2},
            },
            {
                "connection": "archive",
                "library": "Research shelf",
                "prepared": {"automatic": 0, "human": 2, "total": 2},
            },
        ],
        "consented_libraries": [
            {"connection": "primary", "library": "Household ebooks"}
        ],
        "enabled_libraries": [{"connection": "primary", "library": "Household ebooks"}],
        "capabilities": [
            {
                "connection": "primary",
                "library": "Household ebooks",
                "state": "partially_allowed",
                "destinations": [
                    {
                        "destination": "authors",
                        "state": "allowed",
                        "consented": True,
                        "prepared": 2,
                    },
                    {
                        "destination": "subtitle",
                        "state": "field_unavailable",
                        "consented": False,
                        "prepared": 0,
                    },
                    {
                        "destination": "title",
                        "state": "allowed",
                        "consented": True,
                        "prepared": 4,
                    },
                ],
            },
            {
                "connection": "primary",
                "library": "Spoken stories",
                "state": "allowed",
                "destinations": [
                    {
                        "destination": "authors",
                        "state": "allowed",
                        "consented": False,
                        "prepared": 1,
                    },
                    {
                        "destination": "title",
                        "state": "allowed",
                        "consented": False,
                        "prepared": 3,
                    },
                ],
            },
            {
                "connection": "archive",
                "library": "Research shelf",
                "state": "not_ready",
                "destinations": [
                    {
                        "destination": "title",
                        "state": "unexpected_effect_behavior",
                        "consented": False,
                        "prepared": 2,
                    },
                ],
            },
        ],
        "events": [
            {
                "event_id": f"activity-event-{index}",
                "event_type": _cohort_event_type(destination, origin, failure),
                "category": _cohort_category(destination, origin, failure),
                "change": _cohort_change(work, destination)[0],
                "change_detail": _cohort_change(work, destination)[1],
                "check_group": CHECK_GROUP if work == CHECKED_WORK else None,
                "field_label": _COHORT_FIELD_LABELS[destination],
                "policy_changes": (
                    _COHORT_POLICY_CHANGES if destination == "policy" else []
                ),
                "evidence": [
                    {
                        "label": "Previous value",
                        "value": _cohort_change(work, destination)[2]
                        if work == CHECKED_WORK
                        else "Paper Moons: Larkspur"
                        if destination == "title"
                        else "Not set",
                    },
                    {
                        "label": "New value",
                        "value": _cohort_change(work, destination)[2],
                    },
                    {
                        "label": "Chosen by",
                        "value": "Automatic policy"
                        if origin == "automatic_policy"
                        else "Library owner",
                    },
                ],
                "work_id": f"book-{index}",
                "work_label": work,
                "author_label": "Synthetic Author",
                "library": library,
                "destination": destination,
                "state": state,
                "origin": origin,
                # The last check falls on the next day, inside its group.
                "occurred_at": 1_754_858_100
                + index * 300
                + (86_400 if work == CHECKED_WORK and destination == "genres" else 0),
                **(
                    {"transition_id": "effect_plan_" + "a" * 64}
                    if work == "The Glass Orchard"
                    else {}
                ),
                **({"failure": failure} if failure else {}),
            }
            for index, (
                work,
                library,
                destination,
                state,
                origin,
                failure,
            ) in enumerate(events)
        ],
        "counts": {"verified": 1, "applying": 1, "stale": 1, "recovered": 1},
        "example": None,
        "lapsed_consents": [],
        "last_failure": "readback",
        "ledger": {
            "event_type": query.get("event_type", ["all"])[0],
            "page": int(query.get("page", ["1"])[0]),
            "page_count": 1,
            "page_size": int(query.get("page_size", ["25"])[0]),
            "query": query.get("q", [None])[0],
            "range_days": int(query.get("range_days", ["30"])[0]),
            "total": len(events),
        },
        "prepared": {"automatic": 3, "human": 6, "total": 9},
        # Waiting, split into the four different next steps an owner has plus
        # what is moving now, so this cohort renders #639's summary rather than
        # the single number it replaced.
        "waiting": {
            "applying": 1,
            "awaiting_permission": 4,
            "held_for_recovery": 3,
            "retrying": 2,
            "scheduled": 5,
        },
        "provider_recovery": [
            {
                "state": "waiting",
                "affected_count": 2,
                "next_eligible_at": 1_754_900_000,
                "dispatch_reasons": [
                    "missing_corroboration",
                    "blanket_scan",
                ],
            },
            {"state": "checking", "affected_count": 1, "next_eligible_at": None},
            {"state": "fanout", "affected_count": 3, "next_eligible_at": None},
            {"state": "recovered", "affected_count": 1, "next_eligible_at": None},
            {"state": "dormant", "affected_count": 2, "next_eligible_at": None},
            {"state": "exhausted", "affected_count": 1, "next_eligible_at": None},
        ],
        "suspensions": [
            {
                "reason": "unexpected_effect_behavior",
                "connection": "archive",
                "destination": "authors",
                "scope": "destination",
                "updated_at": 1_754_858_700,
            },
            {
                "reason": "unexpected_effect_behavior",
                "connection": "archive",
                "destination": "title",
                "scope": "destination",
                "updated_at": 1_754_858_700,
            },
            {
                "reason": "insufficient_permission",
                "connection": "primary",
                "destination": None,
                "scope": "connection",
                "updated_at": 1_754_859_000,
            },
        ],
    }


# Three sources in two libraries (#1282): a Google Books burst that resets on
# the next day, a dormant Hardcover key refusal and an exhausted Open Library.
THROTTLE_LONG_LIBRARY = "Synthetic audiobooks with a long library name"


def _throttling_entries() -> list[dict[str, object]]:
    return [
        {
            "library": "Synthetic ebooks",
            "adapter": "google_books",
            "state": "waiting",
            "reasons": ["burst_throttled"],
            "deferred": 214,
            "next_retry_at": int(time.time()) + 26 * 3600,
            "remedy": "add_api_key",
        },
        {
            "library": "Synthetic ebooks",
            "adapter": "hardcover",
            "state": "dormant",
            "reasons": ["authentication"],
            "deferred": 1,
            "next_retry_at": None,
            "remedy": "check_api_key",
        },
        {
            "library": THROTTLE_LONG_LIBRARY,
            "adapter": "open_library",
            "state": "exhausted",
            "reasons": ["transient"],
            "deferred": 10_000,
            "next_retry_at": None,
            "remedy": None,
        },
    ]


def rich_payload(target: str, *, review_case_id: str | None = None) -> object:
    """Return a fresh response for one synthetic API target or absolute URL."""
    parsed = urlsplit(target)
    path = parsed.path
    query = parse_qs(parsed.query)
    if path == "/api/v1/settings":
        document = OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v4",
                "revision": 1,
                "connections": [
                    {"name": "primary", "endpoint": "https://library.invalid"}
                ],
                "libraries": [
                    {
                        "name": "ebooks",
                        "connection": "primary",
                        "library_id": "library",
                        "media_kind": "ebook",
                    }
                ],
            }
        )
        return _settings_view(document, {}).model_dump(mode="json")
    if path == "/api/v1/review-preparation":
        return {
            "schema_version": "review-preparation-v2",
            "recovery_required": False,
            "active": True,
            "actionable": True,
            "paused": False,
            "libraries": [
                {
                    "blocked": 0,
                    "checked": 12,
                    "connection": "primary",
                    "library": "Household ebooks",
                    "library_id": "library-1",
                    "ready": 5,
                    "recorded": 7,
                    "not_matched": 0,
                    "rescan_requested": False,
                    "scan_id": "scan-1",
                    "state": "completed",
                    "total": 12,
                },
                {
                    "blocked": 2,
                    "checked": 8,
                    "connection": "primary",
                    "library": "Spoken stories",
                    "library_id": "library-2",
                    "ready": 2,
                    "recorded": 4,
                    "not_matched": 0,
                    "rescan_requested": False,
                    "scan_id": "scan-2",
                    "state": "processing",
                    "total": 16,
                },
            ],
            "connections": [
                {
                    "connection": "primary",
                    "code": "unavailable",
                    "state": "paused",
                    "affected": 2,
                    "updated_at": 1,
                }
            ],
            "provider_throttling": {
                "generation": 1,
                "retry": "available",
                "entries": _throttling_entries(),
            },
        }
    if path == "/api/v1/library":
        return _library(query)
    if path == "/api/v1/attention":
        return _attention()
    if path.startswith("/api/v1/review-cases/"):
        suffix = path.removeprefix("/api/v1/review-cases/")
        case_id, _, subview = suffix.partition("/")
        cohort_case_id = (
            "case-1"
            if review_case_id is not None and case_id == review_case_id
            else case_id
        )
        if subview == "evidence":
            cursor = query.get("cursor", [""])[0]
            page = 2 if cursor == "evidence-page-2" else 1
            return {
                "schema_version": "review-case-evidence-v4",
                "review_case_id": case_id,
                "next_cursor": "evidence-page-2" if page == 1 else None,
                "missing_lineage_count": 2,
                "items": [
                    {
                        "destinations": [kind],
                        "observation": f"{kind.title()}: {fact}",
                        "role": (
                            "local_presence"
                            if provider == "This library"
                            else "external_corroboration"
                        ),
                        "source": provider,
                    }
                    for index, (kind, provider, fact) in enumerate(
                        [
                            ("title", "Open catalogue", HOSTILE_TEXT),
                            ("authors", "This library", "Mara Venn · Ivo North"),
                            ("series", "Second catalogue", LONG_TITLE),
                        ],
                        start=1,
                    )
                ],
                "source_ledger": [
                    {
                        "provider_requests": 1,
                        "lane_kind": "work",
                        "provider": "Open Library",
                        "reason": "Returned matching metadata for this work.",
                        "status": "candidates",
                    },
                    {
                        "provider_requests": 2,
                        "lane_kind": "isbn",
                        "provider": "Hardcover",
                        "reason": "Returned no matching metadata, so nothing was retained.",
                        "status": "no_match",
                    },
                ],
            }
        if subview == "history":
            case_payload = _case(cohort_case_id)
            cover = next(
                (
                    field.get("cover_consideration")
                    for field in case_payload["fields"]
                    if field["destination"] == "cover"
                ),
                None,
            )
            items = [
                {
                    "checkpoint": 1,
                    "kind": "admitted",
                    "reason": "actionable",
                    "transition_id": "review-transition-internal",
                },
                {
                    "actor": "owner",
                    "decision_id": "decision-owner-internal",
                    "destination": "title",
                    "event_id": "event-owner-internal",
                    "kind": "human_decision_recorded",
                    "occurred_at": 1_754_858_100,
                    "outcome": "accept",
                    "previous_value": {"kind": "text", "value": "Current title"},
                    "review_case_id": case_id,
                    "schema_version": "human-decision-event-v1",
                    "selected_value": {"kind": "text", "value": HOSTILE_TEXT},
                },
                {
                    "decision_id": "decision-automatic-internal",
                    "destination": "authors",
                    "kind": "automatic_decision_recorded",
                    "occurred_at": 1_754_858_200,
                    "origin": "automatic_policy",
                    "outcome": "keep_current",
                    "previous_value": {
                        "kind": "list",
                        "value": ["Mara Venn", "Ivo North"],
                    },
                    "review_case_id": case_id,
                    "schema_version": "automatic-decision-history-event-v1",
                    "selected_value": {
                        "kind": "list",
                        "value": ["Mara Venn", "Ivo North"],
                    },
                },
                {
                    "authority": "none",
                    "dispositions": {"candidate": 2, "ignore": 1},
                    "kind": "shadow_preview_recorded",
                    "occurred_at": 1_754_858_300,
                    "origin": "policy_preview",
                    "outcome_set_id": "outcome-set-internal",
                    "review_case_id": case_id,
                    "schema_version": "shadow-preview-history-event-v1",
                },
            ]
            if cover is not None:
                items.append(
                    {
                        "consideration_id": "cover-consideration-internal",
                        "consideration": cover,
                        "decision": None,
                        "decision_id": None,
                        "kind": "automatic_prepared",
                        "occurred_at": 1_754_858_400,
                        "review_case_id": case_id,
                        "schema_version": "cover-consideration-event-v1",
                    }
                )
            return {
                "schema_version": "review-case-history-v3",
                "review_case_id": case_id,
                "next_cursor": None,
                "items": items,
            }
        payload = _case(cohort_case_id)
        payload["review_case_id"] = case_id
        return payload
    if path == "/api/v1/metadata-policy/state":
        return {
            "schema_version": "metadata-policy-notebook-v1",
            "policy_id": "policy-active-1",
            "audiobook_qualification": [
                dict(value)
                for value in audiobook_decision_qualification.public_summary()
            ],
            "rich_qualification": list(_RICH_QUALIFICATION),
            "profile": {
                "schema_version": "metadata-policy-profile-v3",
                "name": "Household metadata policy",
                "options": {
                    "title_layout": "title_and_subtitle",
                    "date_precision": "year",
                    "language_rendering": "display_name",
                    "contributor_names": "canonical",
                    "publisher_preference": "publisher",
                    "subtitle": "fill_empty",
                    "series": "primary_narrative",
                    "genres": "fill_empty",
                    "tags": "off",
                    "description_fill": "safe_plain_text",
                    "isbn_form": "preserve_exact",
                    "cover_order": "fixed_source_order",
                    "asin": "preserve_exact",
                    "explicit": "unsupported",
                },
                "destinations": {
                    "title_layout": "ask",
                    "date_precision": "ask",
                    "language_rendering": "ask",
                    "contributor_names": "ask",
                    "publisher_preference": "ask",
                    "subtitle": "ask",
                    "series": "ask",
                    "genres": "automatic",
                    "tags": "ask",
                    "description_fill": "ask",
                    "isbn_form": "ask",
                    "cover_order": "ask",
                    "asin": "ask",
                    "explicit": "unsupported",
                },
            },
        }
    if path == "/api/v1/metadata-policy/validate":
        state = rich_payload("/api/v1/metadata-policy/state")
        assert isinstance(state, dict)
        return {
            "schema_version": "metadata-policy-profile-v3",
            "policy_id": "policy_" + "d" * 64,
            "profile": state["profile"],
            "rich_qualification": state["rich_qualification"],
        }
    if path == "/api/v1/metadata-policy/drafts":
        return {
            "policy_id": "policy_" + "d" * 64,
            "schema_version": "metadata-policy-profile-v3",
        }
    if path == "/api/v1/metadata-policy/preview":
        fields = [
            ("subtitle", "missing", None, "A bounded subtitle", "projected", None),
            (
                "series",
                "value",
                [{"name": "Old order", "sequence": "1"}],
                [{"name": "Reading order", "sequence": "2"}],
                "projected",
                None,
            ),
            ("genres", "value", ["Fantasy"], ["Fantasy", "Epic"], "projected", None),
            ("tags", "missing", None, None, "abstain", "policy_off"),
            (
                "description_fill",
                "missing",
                None,
                "Safe visible description <3",
                "projected",
                None,
            ),
            ("isbn_form", "missing", None, "9780306406157", "projected", None),
            ("asin", "missing", None, "B012345678", "projected", None),
            ("explicit", "missing", None, None, "abstain", "explicit_unsupported"),
        ]
        preview_fields = [
            {
                "current_value": current,
                "desired_value": desired,
                "field": field,
                "narrowed_for_target": field == "series",
                "option": {
                    "subtitle": "fill_empty",
                    "series": "all_qualified",
                    "genres": "add",
                    "tags": "off",
                    "description_fill": "google_visible_text",
                    "isbn_form": "prefer_isbn13",
                    "asin": "ebook_manifestation",
                    "explicit": "unsupported",
                }[field],
                "outcome": outcome,
                "qualified_value": (
                    [
                        {"name": "Reading order", "sequence": "2"},
                        {
                            "name": "Publication order",
                            "sequence": None,
                        },
                    ]
                    if field == "series"
                    else None
                ),
                "reason": reason,
                "target_state": state,
            }
            for field, state, current, desired, outcome, reason in fields
        ]
        comparisons = [
            {
                "media_kind": "ebook",
                "fields": [
                    {
                        "active": {
                            **field,
                            "desired_value": field["current_value"],
                            "qualified_value": field["current_value"],
                        },
                        "draft": field,
                        "active_destination": "ask",
                        "draft_destination": "automatic",
                        "changed": field["current_value"] != field["desired_value"],
                    }
                    for field in preview_fields
                ],
            }
        ]
        preview = {
            "active_policy_id": "policy-active-1",
            "schema_version": "metadata-policy-preview-v1",
            "active_generation": 1,
            "policy_id": "policy_" + "d" * 64,
            "rich_qualification": list(_RICH_QUALIFICATION),
            "libraries": [
                {
                    "affected_categories": ["description_fill"],
                    "comparisons": comparisons,
                    "cases": [
                        {
                            "fields": preview_fields,
                            "proposal_set_id": "proposal_set_" + "b" * 64,
                            "report_id": "report_" + "a" * 64,
                            "resolved_policy_id": "resolved_policy_" + "c" * 64,
                            "work_label": "The Clockmaker's Map",
                        }
                    ],
                    "cases_total": 1,
                    "cases_truncated": False,
                    "current_unapplied_count": 1,
                    "library": "Household ebooks",
                    "resolved_policy_id": "resolved_policy_" + "c" * 64,
                }
            ],
        }
        return preview
    if path == "/api/v1/metadata-policy/shadow":
        return {
            "schema_version": "shadow-posture-v1",
            "posture": "preview",
            "authority": "none",
            "can_leave_preview": True,
        }
    if path == "/api/v1/metadata-policy/automatic-safety":
        return {
            "paused": False,
            "global_suspension": None,
            "category_suspensions": {"title": {"reason": "target_changed", "count": 2}},
        }
    if path == "/api/v1/metadata-policy/shadow/outcomes":
        return {
            "schema_version": "shadow-outcome-sample-v1",
            "authority": "none",
            "items": [
                {
                    "outcome_set_id": "preview-1",
                    "outcomes": [{"destination": "title"}, {"destination": "authors"}],
                },
                {
                    "outcome_set_id": "preview-2",
                    "outcomes": [{"destination": "series"}],
                },
            ],
            "next_cursor": None,
        }
    if path == "/api/v1/metadata-policy/transition":
        return {
            "schema_version": "metadata-policy-transition-v1",
            "transition_id": "transition-1",
            "from_policy_id": "policy-active-0",
            "to_policy_id": "policy-active-1",
            "generation": 3,
            "state": "completed",
            "completed": 11,
            "total": 12,
            "failed": 1,
            "acknowledged": False,
            "origin": "activation",
        }
    if path == "/api/v1/activity/permission-readiness":
        value = _activity(query)
        return {
            key: value[key]
            for key in (
                "backup_ready",
                "configured_libraries",
                "capabilities",
                "prepared",
                "example",
            )
        }
    if path == "/api/v1/activity/consent-state":
        value = _activity(query)
        return {
            "backup_ready": None,
            **{
                key: value[key]
                for key in (
                    "capabilities",
                    "configured_libraries",
                    "lapsed_consents",
                    "waiting",
                )
            },
        }
    if path == "/api/v1/activity":
        return {**_activity(query), "backup_ready": None, "example": None}
    if path.startswith("/api/v1/activity/") and path.endswith("/transitions"):
        after_sequence = int(query.get("after_sequence", ["0"])[0])
        transitions = [
            {
                "event_id": f"synthetic-transition-{sequence}",
                "occurred_at": 1_754_858_100 + sequence,
                "sequence": sequence,
                "state": "planned" if sequence < 25 else "verified",
                "failure": None,
            }
            for sequence in range(after_sequence + 1, min(after_sequence + 20, 25) + 1)
        ]
        return {"events": transitions, "has_more": after_sequence + 20 < 25}
    raise AssertionError(f"unexpected API target: {target}")


# Each synthetic preview the cohort names, at the size its payload states, so a
# rendered cover is a real image of a real shape rather than a broken one
# (#1290): the candidates by reference, the library cover by digest.
_SYNTHETIC_PREVIEWS: dict[str, tuple[int, int, tuple[int, int, int]]] = {
    "cover-ref-current": (800, 1200, (59, 85, 80)),
    "cover-ref-choice": (169, 280, (138, 90, 22)),
    "cover-ref-unconfirmed": (305, 480, (60, 90, 140)),
    LIBRARY_COVER_PREVIEW: (800, 1200, (59, 85, 80)),
}


@functools.cache
def synthetic_preview(key: str) -> bytes:
    """A two-tone PNG at the named size: the lower third is lighter."""
    width, height, colour = _SYNTHETIC_PREVIEWS[key]
    light = tuple(min(255, value + 90) for value in colour)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    rows = b"".join(
        b"\x00" + bytes(colour if row < height * 2 // 3 else light) * width
        for row in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def synthetic_preview_key(path: str, query: str) -> str | None:
    if path == "/api/v1/library/cover":
        preview = parse_qs(query).get("preview", [""])[0]
        return preview if preview in _SYNTHETIC_PREVIEWS else None
    if path.startswith("/api/v1/review-cases/") and "/images/" in path:
        reference = path.rpartition("/images/")[2]
        return reference if reference in _SYNTHETIC_PREVIEWS else None
    return None


class RichFidelityApiMiddleware(BaseHTTPMiddleware):
    """Replace successful read responses after the real app enforces authentication."""

    def __init__(self, app: ASGIApp, *, review_case_id: str | None = None) -> None:
        super().__init__(app)
        self._review_case_id = review_case_id
        self._retries = 0

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        target = str(request.url)
        target_url = urlsplit(target)
        original_path = request.scope["path"]
        original_raw_path = request.scope.get("raw_path")
        original_query_string = request.scope.get("query_string", b"")
        alias_prefix = "/api/v1/review-cases/"
        installed_prefix = f"{alias_prefix}{self._review_case_id}"
        requested_case_id, _, suffix = original_path.removeprefix(
            alias_prefix
        ).partition("/")
        rewrite_alias = (
            request.method == "GET"
            and self._review_case_id is not None
            and original_path.startswith(alias_prefix)
            and requested_case_id != self._review_case_id
        )
        rich_cursor = parse_qs(target_url.query).get("cursor")
        rewrite_library_page = (
            request.method == "GET"
            and original_path == "/api/v1/library"
            and rich_cursor is not None
            and len(rich_cursor) == 1
            and rich_cursor[0] in {"page-2", "page-3", "page-4"}
        )
        if rewrite_alias:
            installed_path = installed_prefix + (f"/{suffix}" if suffix else "")
            request.scope["path"] = installed_path
            request.scope["raw_path"] = installed_path.encode("ascii")
        if (
            rewrite_alias
            and suffix == "evidence"
            and rich_cursor == ["evidence-page-2"]
        ) or rewrite_library_page:
            admitted_query = urlencode(
                [
                    (key, value)
                    for key, value in parse_qsl(
                        target_url.query, keep_blank_values=True
                    )
                    if key != "cursor"
                ]
            )
            request.scope["query_string"] = admitted_query.encode("ascii")
        try:
            response = await call_next(request)
        finally:
            request.scope["path"] = original_path
            if original_raw_path is None:
                request.scope.pop("raw_path", None)
            else:
                request.scope["raw_path"] = original_raw_path
            request.scope["query_string"] = original_query_string
        # The real app has refused an unauthenticated, CSRF-less or malformed
        # Retry now by now; only its settings-unavailable answer is scripted:
        # the first press is accepted, every later one is stale (#1282).
        if (
            request.method == "POST"
            and original_path == "/api/v1/provider-recovery/retry"
            and response.status_code == 503
        ):
            self._retries += 1
            if self._retries > 1:
                return JSONResponse(
                    {
                        "error": {"code": "provider_recovery_stale"},
                        "schema_version": "review-error-v1",
                    },
                    status_code=409,
                )
            settings = cast(
                dict[str, dict[str, object]], rich_payload("/api/v1/settings")
            )
            settings["document"] |= {"revision": 2, "provider_recovery_generation": 2}
            return JSONResponse(settings)
        preview = synthetic_preview_key(original_path, target_url.query)
        # The real app has authenticated the read; it refuses the synthetic
        # references themselves, which no retained preview backs.
        if (
            request.method == "GET"
            and preview is not None
            and response.status_code not in {401, 403}
        ):
            return Response(
                synthetic_preview(preview),
                media_type="image/png",
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower()
                    in {"content-security-policy", "x-content-type-options"}
                }
                | {"cache-control": "no-store, private"},
            )
        if request.method != "GET" or response.status_code != 200:
            return response
        try:
            payload = rich_payload(target, review_case_id=self._review_case_id)
        except AssertionError:
            return response
        return JSONResponse(
            payload,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            },
        )


__all__ = [
    "BUILD_IDENTITY",
    "HOSTILE_TEXT",
    "LIBRARY_COVER_PREVIEW",
    "LIBRARY_WORKS",
    "LONG_TITLE",
    "TERMINAL_FAILURE_COPY",
    "TERMINAL_LIBRARY_WORK",
    "THROTTLE_LONG_LIBRARY",
    "UNBROKEN_TITLE_SEGMENT",
    "RichFidelityApiMiddleware",
    "rich_payload",
    "synthetic_preview",
    "synthetic_preview_key",
]
