"""Rich, deterministic, entirely synthetic data for interface qualification."""

from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


BUILD_IDENTITY = "a" * 64
HOSTILE_TEXT = "Hostile <img src=x onerror=window.__metadataExecuted=true>"
UNBROKEN_TITLE_SEGMENT = "UnbrokenSyntheticCatalogueToken" * 12
LONG_TITLE = (
    "The Clockmaker's Cartography of Quietly Impossible Libraries — "
    f"{UNBROKEN_TITLE_SEGMENT} — an intentionally long synthetic title for "
    "narrow-layout qualification"
)
_F20_CANDIDATE_SHA256 = "aba7b436d88bb4e884cda0c4b247aa871063dce459e0f5fbea298227a1e9b06c"
_RICH_QUALIFICATION: tuple[dict[str, object], ...] = tuple(
    {
        "automatic_available": disposition == "passing_automatic",
        "candidate_sha256": _F20_CANDIDATE_SHA256,
        "disposition": disposition,
        "field": field,
        "option": option,
    }
    for field, option, disposition in (
        ("subtitle", "fill_empty", "not_qualified"),
        ("subtitle", "corroborated_split", "not_qualified"),
        ("series", "primary_narrative", "passing_automatic"),
        ("series", "source_order", "passing_automatic"),
        ("series", "all_qualified", "passing_automatic"),
        ("genres", "fill_empty", "passing_automatic"),
        ("genres", "add", "passing_automatic"),
        ("genres", "replace", "passing_automatic"),
        ("tags", "off", "policy_only"),
        ("tags", "fill_empty", "passing_automatic"),
        ("tags", "add", "passing_automatic"),
        ("tags", "replace", "passing_automatic"),
        ("description_fill", "safe_plain_text", "passing_automatic"),
        ("description_fill", "google_visible_text", "not_qualified"),
        ("isbn_form", "preserve_exact", "passing_automatic"),
        ("isbn_form", "prefer_isbn13", "passing_automatic"),
        ("asin", "preserve_exact", "insufficient_coverage"),
        ("asin", "ebook_manifestation", "insufficient_coverage"),
        ("explicit", "unsupported", "passing_human_only"),
    )
)


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
) -> dict[str, object]:
    return {
        "work_id": f"library_work_{number:064d}",
        "review_case_id": case,
        "title": title,
        "authors": authors,
        "library": library,
        "media_kind": media_kind,
        "item": f"synthetic-item-{number}",
        "attention_count": attention,
        "attention_state": (
            "needs_attention" if attention else "no_attention_needed"
        ),
        "detail_url": f"/review/{case}" if case else None,
        "dispositions": dispositions,
        "preparation": preparation,
        "preparation_outcomes": preparation_outcomes or [],
    }


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
    ),
    _work(
        2,
        LONG_TITLE,
        ["Aster Vale"],
        "Spoken stories",
        "audiobook",
        case="case-2",
        attention=2,
        dispositions=["unresolved_choice", "no_safe_proposal"],
    ),
    _work(
        3,
        "Paper Moons over Larkspur Station",
        ["Nia Calder"],
        "Household ebooks",
        "ebook",
        case="case-3",
        attention=1,
        dispositions=["unresolved_choice"],
    ),
    _work(
        4,
        "A Field Guide to Borrowed Weather",
        ["Rowan Pike"],
        "Research shelf",
        "pdf",
        case="case-4",
        attention=1,
        dispositions=["manual_hold"],
    ),
    _work(
        5,
        "The Glass Orchard",
        ["Tamsin Reed"],
        "Spoken stories",
        "audiobook",
        case="case-5",
        attention=0,
        dispositions=["automatic"],
    ),
    _work(
        6,
        "Catalogue-only work",
        ["Oren Finch"],
        "Household ebooks",
        "epub",
        case=None,
        attention=0,
        dispositions=[],
        preparation={"attempt": 1, "provider": "hardcover-main", "reason": "Provider credentials need owner attention.", "recovery": "Retry review preparation after correcting the reported condition.", "recovery_attempt": 0, "stage": "provider", "status": "authentication"},
        preparation_outcomes=[{"provider_requests": 1, "lane_kind": "work", "provider": "hardcover-main", "reason": "Provider credentials need owner attention.", "status": "authentication"}],
    ),
    _work(
        7,
        "Resolved work with retained history",
        ["Juniper Ash"],
        "Research shelf",
        "ebook",
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
        "ebook",
        case=None,
        attention=0,
        dispositions=["pre_admission"],
    ),
    _work(
        10,
        "Retired exact-current case",
        ["Cedar Moss"],
        "Spoken stories",
        "audiobook",
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
        "ebook",
        case="case-12",
        attention=0,
        dispositions=["resolved"],
    ),
    # Public-domain catalogue examples keep the installed visual cohort broad
    # without drawing on a contributor's library or a live provider response.
    _work(13, "Frankenstein; or, The Modern Prometheus", ["Mary Shelley"], "Local collection", "ebook", case="case-13", attention=1, dispositions=["unresolved_choice", "duplicate"]),
    _work(14, "Pride and Prejudice", ["Jane Austen"], "Project Gutenberg", "audiobook", case="case-14", attention=0, dispositions=["automatic"]),
    _work(15, "Moby-Dick; or, The Whale", ["Herman Melville"], "Google Books catalogue", "ebook", case="case-15", attention=0, dispositions=["corroborated"]),
    _work(16, "Dracula", ["Bram Stoker"], "Open Library catalogue", "audiobook", case="case-16", attention=0, dispositions=["missing"]),
    _work(17, "The Time Machine", ["H. G. Wells"], "Local collection", "ebook", case="case-17", attention=0, dispositions=["conflict"]),
    _work(18, "The Adventures of Sherlock Holmes", ["Arthur Conan Doyle"], "Project Gutenberg", "audiobook", case="case-18", attention=0, dispositions=["duplicate"]),
    _work(19, "The Republic", ["Plato"], "Google Books catalogue", "ebook", case="case-19", attention=0, dispositions=["unknown"]),
    _work(20, "The Odyssey", ["Homer"], "Open Library catalogue", "audiobook", case="case-20", attention=0, dispositions=["pagination"]),
)


def _field(
    destination: str,
    current: object,
    proposed: list[object],
    *,
    disposition: str = "review",
    needs_attention: bool = True,
    human_decision: dict[str, object] | None = None,
    automatic_decision: dict[str, object] | None = None,
    manual_hold: bool = False,
    notices: list[str] | None = None,
    provenance: list[str] | None = None,
    corroboration_sources: list[str] | None = None,
) -> dict[str, object]:
    return {
        "destination": destination,
        "deterministic_disposition": disposition,
        "effect_capability": "manual",
        "needs_attention": needs_attention,
        "manual_hold": manual_hold,
        "manual_required": needs_attention,
        "current": {"kind": "synthetic", "value": current},
        "proposed_values": [
            {"kind": "synthetic", "value": value} for value in proposed
        ],
        "proposal_id": f"proposal-{destination}",
        "target_fingerprint": "f" * 64,
        "human_decision": human_decision,
        "automatic_decision": automatic_decision,
        "notices": notices or [],
        "provenance": provenance or [],
        "corroboration_sources": corroboration_sources or [],
        "uncertainty": [],
    }


def _case(case_id: str) -> dict[str, object]:
    if case_id == "case-stale":
        return {
            "schema_version": "review-case-v1",
            "review_case_id": case_id,
            "current": False,
            "target": {
                "connection": "primary",
                "library": "Spoken stories",
                "media_kind": "audiobook",
                "authors": ["Cedar Moss"],
                "item": "Retired exact-current case",
            },
            "fields": [],
        }

    resolved = case_id in {"case-7", "case-12"}
    automatic = case_id in {"case-5", "case-8"}
    current_only = case_id == "case-11"
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
            [] if current_only else [
                "The Clockmaker's Cartography",
                "Cartography of Impossible Libraries",
                LONG_TITLE,
            ],
            needs_attention=not (resolved or automatic or current_only),
            manual_hold=case_id == "case-4",
            disposition=(
                "automatic" if automatic else "ineligible" if current_only else "review"
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
                }
                if automatic
                else None
            ),
            notices=[
                "Three independent catalogue observations disagree on presentation.",
                "Punctuation and subtitle boundaries remain an owner choice.",
            ],
            provenance=provider_evidence,
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
            disposition="ineligible" if current_only else "review",
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
            notices=["No eligible proposed value is supported by the retained evidence."],
        ),
        _field(
            "series",
            {"name": "Atlas Cycle", "sequence": "2.5"},
            [],
            disposition="ineligible" if current_only else "automatic",
            needs_attention=False,
            automatic_decision=(
                None
                if current_only
                else {
                    "decision_id": "automatic-series",
                    "outcome": "keep_current",
                    "origin": "automatic_policy",
                }
            ),
        ),
    ]
    work = next((item for item in LIBRARY_WORKS if item["review_case_id"] == case_id), LIBRARY_WORKS[0])
    return {
        "schema_version": "review-case-v1",
        "review_case_id": case_id,
        "current": True,
        "target": {
            "connection": "primary",
            "library": work["library"],
            "media_kind": work["media_kind"],
            "authors": work["authors"],
            "item": work["title"],
        },
        "fields": fields,
    }


def _library(query: dict[str, list[str]]) -> dict[str, object]:
    lens = query.get("lens", ["all"])[0]
    needle = query.get("q", [""])[0].casefold().strip()
    works = list(LIBRARY_WORKS)
    if lens == "needs_attention":
        works = [item for item in works if int(item["attention_count"]) > 0]
    elif lens == "no_attention_needed":
        works = [item for item in works if int(item["attention_count"]) == 0]
    if needle:
        works = [
            item
            for item in works
            if needle
            in " ".join(
                [
                    str(item["title"]),
                    *[str(author) for author in item["authors"]],
                    str(item["library"]),
                    str(item["media_kind"]),
                ]
            ).casefold()
        ]
    cursor = query.get("cursor", [""])[0]
    page = {"page-2": 2, "page-3": 3, "page-4": 4}.get(cursor, 1)
    start = (page - 1) * 6
    end = start + 6
    return {
        "schema_version": "review.v1",
        "lens": lens,
        "query": needle or None,
        "next_cursor": f"page-{page + 1}" if len(works) > end else None,
        "counts": {
            "needs_attention": sum(
                int(item["attention_count"]) > 0 for item in LIBRARY_WORKS
            ),
            "all": len(LIBRARY_WORKS),
            "no_attention_needed": sum(
                int(item["attention_count"]) == 0 for item in LIBRARY_WORKS
            ),
        },
        "items": deepcopy(works[start:end]),
    }


def _attention() -> dict[str, object]:
    items = []
    for work in LIBRARY_WORKS[:4]:
        destinations = ("title", "authors") if int(work["attention_count"]) > 1 else ("title",)
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
                    "notices": ["Conflicting retained evidence"],
                    "provenance": ["catalogue observation", "local exact-current value"],
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


def _activity(query: dict[str, list[str]]) -> dict[str, object]:
    events = [
        ("The Glass Orchard", "Spoken stories", "title", "verified", "automatic_policy", None),
        (LONG_TITLE, "Spoken stories", "authors", "applying", "human", None),
        ("Paper Moons over Larkspur Station", "Household ebooks", "title", "stale", "human", "target_changed"),
        ("A Field Guide to Borrowed Weather", "Research shelf", "title", "recovered", "human", "readback"),
        ("Unicode shelves: 雨の地図", "Family archive — shared", "series", "paused", "human", "consent_revoked"),
        (
            HOSTILE_TEXT,
            "Household ebooks",
            "title",
            "ambiguous",
            "future_provider_origin",
            "ambiguous",
        ),
        ("Resolved work with retained history", "Research shelf", "authors", "resolved", "human", None),
        ("Automatic-only edition", "Household ebooks", "series", "running", "automatic_policy", None),
    ]
    return {
        "schema_version": "continuous-field-activity-v2",
        "current_waiting": None,
        "backup_ready": True,
        "paused": False,
        "configured_libraries": [
            {"connection": "primary", "library": "Household ebooks", "prepared": {"automatic": 2, "human": 3, "total": 5}},
            {"connection": "primary", "library": "Spoken stories", "prepared": {"automatic": 1, "human": 1, "total": 2}},
            {"connection": "archive", "library": "Research shelf", "prepared": {"automatic": 0, "human": 2, "total": 2}},
        ],
        "consented_libraries": [
            {"connection": "primary", "library": "Household ebooks"}
        ],
        "enabled_libraries": [
            {"connection": "primary", "library": "Household ebooks"}
        ],
        "capabilities": [
            {
                "connection": "primary",
                "library": "Household ebooks",
                "state": "partially_allowed",
                "destinations": [
                    {"destination": "authors", "state": "allowed", "consented": True},
                    {"destination": "subtitle", "state": "field_unavailable", "consented": False},
                    {"destination": "title", "state": "allowed", "consented": True},
                ],
            },
            {
                "connection": "primary",
                "library": "Spoken stories",
                "state": "allowed",
                "destinations": [
                    {"destination": "authors", "state": "allowed", "consented": False},
                    {"destination": "title", "state": "allowed", "consented": False},
                ],
            },
            {
                "connection": "archive",
                "library": "Research shelf",
                "state": "blocked",
                "destinations": [
                    {"destination": "title", "state": "recovery_required", "consented": False},
                ],
            },
        ],
        "events": [
            {
                "event_id": f"activity-event-{index}",
                "event_type": (
                    "review" if origin == "human" else "metadata"
                ),
                "evidence": [
                    {"label": "Recorded origin", "value": origin},
                    {"label": "Outcome", "value": state},
                ],
                "work_id": f"book-{index}",
                "work_label": work,
                "author_label": "Synthetic Author",
                "library": library,
                "destination": destination,
                "state": state,
                "origin": origin,
                "occurred_at": 1_754_858_100 + index * 300,
                **({"failure": failure} if failure else {}),
            }
            for index, (work, library, destination, state, origin, failure) in enumerate(events)
        ],
        "counts": {"verified": 1, "applying": 1, "stale": 1, "recovered": 1},
        "example": None,
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
        "provider_recovery": [
            {"state": "waiting", "affected_count": 2, "next_eligible_at": 1_754_900_000},
            {"state": "checking", "affected_count": 1, "next_eligible_at": None},
            {"state": "fanout", "affected_count": 3, "next_eligible_at": None},
            {"state": "recovered", "affected_count": 1, "next_eligible_at": None},
            {"state": "dormant", "affected_count": 2, "next_eligible_at": None},
            {"state": "exhausted", "affected_count": 1, "next_eligible_at": None},
        ],
        "suspensions": [
            {"reason": "readback", "connection": "archive", "destination": "authors", "scope": "destination", "updated_at": 1_754_858_700},
            {"reason": "readback", "connection": "archive", "destination": "title", "scope": "destination", "updated_at": 1_754_858_700},
            {"reason": "consent_revoked", "connection": "primary", "destination": None, "scope": "connection", "updated_at": 1_754_859_000},
        ],
    }


def rich_payload(target: str, *, review_case_id: str | None = None) -> object:
    """Return a fresh response for one synthetic API target or absolute URL."""
    parsed = urlsplit(target)
    path = parsed.path
    query = parse_qs(parsed.query)
    if path == "/api/v1/review-preparation":
        return {
            "schema_version": "review-preparation-v1",
            "active": True,
            "actionable": True,
            "paused": False,
            "libraries": [
                {"blocked": 0, "checked": 12, "connection": "primary", "library": "Household ebooks", "library_id": "library-1", "ready": 5, "rescan_requested": False, "scan_id": "scan-1", "state": "ready", "total": 12},
                {"blocked": 2, "checked": 8, "connection": "primary", "library": "Spoken stories", "library_id": "library-2", "ready": 2, "rescan_requested": False, "scan_id": "scan-2", "state": "scanning", "total": 16},
            ],
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
                "schema_version": "review-case-evidence-v2",
                "review_case_id": case_id,
                "next_cursor": "evidence-page-2" if page == 1 else None,
                "items": [
                    {
                        "reference": f"evidence-{page}-{index}",
                        "kind": kind,
                        "source": "provider_claim",
                        "provider": provider,
                        "candidate": f"candidate-{page}-{index}",
                        "fact": fact,
                    }
                    for index, (kind, provider, fact) in enumerate(
                        [
                            ("title", "Open catalogue", HOSTILE_TEXT),
                            ("authors", "Local manifest", "Mara Venn · Ivo North"),
                            ("series", "Second catalogue", LONG_TITLE),
                        ],
                        start=1,
                    )
                ],
            }
        if subview == "history":
            return {
                "schema_version": "review-case-history-v1",
                "review_case_id": case_id,
                "next_cursor": None,
                "items": [
                    {"kind": "case_admitted", "destination": "title", "at": "2026-08-10T20:00:00Z"},
                    {"kind": "evidence_refreshed", "destination": "authors", "at": "2026-08-10T20:05:00Z"},
                    {"kind": "automatic_decision", "destination": "series", "at": "2026-08-10T20:10:00Z"},
                    {"kind": "human_decision", "destination": "title", "at": "2026-08-10T20:15:00Z"},
                ],
            }
        payload = _case(cohort_case_id)
        payload["review_case_id"] = case_id
        return payload
    if path == "/api/v1/metadata-policy/state":
        return {
            "schema_version": "metadata-policy-notebook-v1",
            "policy_id": "policy-active-1",
            "rich_qualification": list(_RICH_QUALIFICATION),
            "profile": {
                "schema_version": "metadata-policy-profile-v2",
                "name": "Household metadata policy",
                "options": {"title_layout": "title_and_subtitle", "date_precision": "year", "language_rendering": "display_name", "contributor_names": "canonical", "publisher_preference": "publisher", "subtitle": "fill_empty", "series": "primary_narrative", "genres": "fill_empty", "tags": "off", "description_fill": "safe_plain_text", "isbn_form": "preserve_exact", "cover_order": "fixed_source_order", "asin": "preserve_exact", "explicit": "unsupported"},
                "destinations": {"title_layout": "ask", "date_precision": "ask", "language_rendering": "ask", "contributor_names": "ask", "publisher_preference": "ask", "subtitle": "ask", "series": "ask", "genres": "automatic", "tags": "ask", "description_fill": "ask", "isbn_form": "ask", "cover_order": "ask", "asin": "ask", "explicit": "unsupported"},
            },
        }
    if path == "/api/v1/metadata-policy/validate":
        state = rich_payload("/api/v1/metadata-policy/state")
        assert isinstance(state, dict)
        return {
            "schema_version": "metadata-policy-profile-v2",
            "policy_id": "policy_" + "d" * 64,
            "profile": state["profile"],
            "rich_qualification": state["rich_qualification"],
        }
    if path == "/api/v1/metadata-policy/drafts":
        return {
            "policy_id": "policy_" + "d" * 64,
            "schema_version": "metadata-policy-profile-v2",
        }
    if path == "/api/v1/metadata-policy/preview":
        fields = [
            ("subtitle", "missing", None, "A bounded subtitle", "projected", None),
            ("series", "value", [{"name": "Old order", "sequence": "1"}], [{"name": "Reading order", "sequence": "2"}], "projected", None),
            ("genres", "value", ["Fantasy"], ["Fantasy", "Epic"], "projected", None),
            ("tags", "missing", None, None, "abstain", "policy_off"),
            ("description_fill", "missing", None, "Safe visible description <3", "projected", None),
            ("isbn_form", "missing", None, "9780306406157", "projected", None),
            ("asin", "missing", None, "B012345678", "projected", None),
            ("explicit", "missing", None, None, "abstain", "explicit_unsupported"),
        ]
        return {
            "active_policy_id": "policy-active-1",
            "schema_version": "metadata-policy-preview-v1",
            "policy_id": "policy_" + "d" * 64,
            "rich_qualification": list(_RICH_QUALIFICATION),
            "libraries": [{
                "affected_categories": ["description_fill"],
                "cases": [{
                    "fields": [{
                        "current_value": current,
                        "desired_value": desired,
                        "field": field,
                        "narrowed_for_target": field == "series",
                        "option": {
                            "subtitle": "fill_empty", "series": "all_qualified",
                            "genres": "add", "tags": "off",
                            "description_fill": "google_visible_text",
                            "isbn_form": "prefer_isbn13",
                            "asin": "ebook_manifestation",
                            "explicit": "unsupported",
                        }[field],
                        "outcome": outcome,
                        "qualified_value": ([
                            {"name": "Reading order", "sequence": "2"},
                            {"name": "Publication order", "sequence": None},
                        ] if field == "series" else None),
                        "reason": reason,
                        "target_state": state,
                    } for field, state, current, desired, outcome, reason in fields],
                    "proposal_set_id": "proposal_set_" + "b" * 64,
                    "report_id": "report_" + "a" * 64,
                    "resolved_policy_id": "resolved_policy_" + "c" * 64,
                    "work_label": "The Clockmaker's Map",
                }],
                "cases_total": 1,
                "cases_truncated": False,
                "current_unapplied_count": 1,
                "library": "Household ebooks",
                "resolved_policy_id": "resolved_policy_" + "c" * 64,
            }],
        }
    if path == "/api/v1/metadata-policy/shadow":
        return {"schema_version": "shadow-posture-v1", "posture": "preview", "authority": "none", "can_leave_preview": True}
    if path == "/api/v1/metadata-policy/automatic-safety":
        return {"paused": False, "global_suspension": None, "category_suspensions": {"title": {"reason": "target_changed", "count": 2}}}
    if path == "/api/v1/metadata-policy/shadow/outcomes":
        return {
            "schema_version": "shadow-outcome-sample-v1",
            "authority": "none",
            "items": [
                {"outcome_set_id": "preview-1", "outcomes": [{"destination": "title"}, {"destination": "authors"}]},
                {"outcome_set_id": "preview-2", "outcomes": [{"destination": "series"}]},
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
        }
    if path == "/api/v1/activity":
        return _activity(query)
    raise AssertionError(f"unexpected API target: {target}")


class RichFidelityApiMiddleware(BaseHTTPMiddleware):
    """Replace successful read responses after the real app enforces authentication."""

    def __init__(self, app: ASGIApp, *, review_case_id: str | None = None) -> None:
        super().__init__(app)
        self._review_case_id = review_case_id

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
            (
                rewrite_alias
                and suffix == "evidence"
                and rich_cursor == ["evidence-page-2"]
            )
            or rewrite_library_page
        ):
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
    "LIBRARY_WORKS",
    "LONG_TITLE",
    "UNBROKEN_TITLE_SEGMENT",
    "RichFidelityApiMiddleware",
    "rich_payload",
]
