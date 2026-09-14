"""Bounded resolution eligibility for the audiobook replacement option only."""

from __future__ import annotations

from typing import Any, cast


def material_audiobook_cover_reference(
    candidates: object, incumbent_artifact_id: object
) -> str | None:
    """Return the first owner-ordered candidate materially improving the incumbent.

    Consume exact, already bound consideration facts. Order itself supplies no
    improvement evidence; the incumbent must be the actual target artifact.
    """
    if type(candidates) is not list or type(incumbent_artifact_id) is not str:
        return None
    rows = [row for row in candidates if type(row) is dict]
    incumbents = [row for row in rows if row.get("incumbent") is True]
    if len(incumbents) != 1:
        return None
    incumbent = incumbents[0]
    if incumbent.get("artifact_id") != incumbent_artifact_id:
        return None

    def confirmed_dimensions(row: dict[str, Any]) -> tuple[int, int] | None:
        identity = row.get("identity")
        width, height = row.get("width"), row.get("height")
        if (
            type(identity) is not dict
            or identity.get("outcome")
            not in {"verified_manifestation_match", "verified_work_match"}
            or not identity.get("evidence_references")
            or type(width) is not int
            or type(height) is not int
            or width <= 0
            or height <= 0
        ):
            return None
        return width, height

    dimensions = confirmed_dimensions(incumbent)
    if dimensions is None or min(dimensions) >= 300:
        return None
    incumbent_edge = min(dimensions)
    ordered = sorted(
        (
            row
            for row in rows
            if row.get("incumbent") is False
            and type(row.get("deterministic_rank")) is int
            and row["deterministic_rank"] > 0
        ),
        key=lambda row: row["deterministic_rank"],
    )
    for candidate in ordered:
        dimensions = confirmed_dimensions(candidate)
        if dimensions is None:
            continue
        width, height = dimensions
        if (
            candidate.get("selectable") is True
            and candidate.get("artifact_id") != incumbent_artifact_id
            and candidate.get("deterministic_citations")
            and type(candidate.get("reference")) is str
            and width <= height <= 2 * width
            and min(dimensions) >= 500
            and min(dimensions) >= 2 * incumbent_edge
        ):
            return cast(str, candidate["reference"])
    return None
