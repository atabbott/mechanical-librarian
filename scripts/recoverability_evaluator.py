#!/usr/bin/env python3
"""Bounded, aggregate-only recoverability qualification evaluator."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

INPUT_SCHEMA = "recoverability-evaluator-v4"
OUTPUT_SCHEMA = "recoverability-aggregate-v4"
SUPPORTED = (
    "title", "subtitle", "creator", "contributor", "publisher", "language",
    "publication_date", "identifier", "collection", "collection_position",
)
EXPLORATORY = ("edition", "rights")
CATEGORIES = (
    "absent_in_source", "inspection_failure", "extracted_not_projected",
    "normalization_mismatch", "semantic_mismatch", "matched_partial",
    "matched_complete",
)
EXCLUDED = (
    "unknown", "known_empty", "not_applicable", "accepted_unsupported",
    "unexplained_failure",
)
ISBN_RELATIONS = ("accepted", "associated", "unrelated", "invalid", "unresolved")
OBSERVATION_TUPLES = {
    "accepted": ("accepted_target", "none", False, True, True),
    "associated": ("provider_work_identity", "hardcover", True, True, True),
    "unrelated": ("provider_different_work", "hardcover", False, True, True),
    "invalid": ("format_invalid", "none", False, True, True),
    "unresolved": ("unavailable", "none", False, True, True),
}
GOLD_FIELDS = {
    "title": (50, 49), "subtitle": (10, 10), "creator": (50, 49),
    "contributor": (36, 36), "publisher": (47, 46), "language": (50, 49),
    "publication_date": (50, 49), "identifier": (47, 46),
    "collection": (36, 35), "collection_position": (29, 28),
    "edition": (47, 46), "rights": (47, 46),
}
SYNTHETIC_FIELDS = {
    "title": (2, 1), "creator": (2, 1), "publisher": (2, 1),
    "subtitle": (1, 1), "language": (1, 1), "collection": (1, 1),
    "identifier": (2, 1), "edition": (3, 3), "rights": (4, 3),
    "contributor": (0, 0), "publication_date": (0, 0),
    "collection_position": (0, 0),
}
PROFILES = {
    "synthetic-v1": {"fields": SYNTHETIC_FIELDS, "identifier_targets": 6,
                     "identifier_observations": 6, "candidate_rows": 5,
                     "negative_rows": 2},
    "gold-50-v1": {"fields": GOLD_FIELDS, "identifier_targets": 47,
                    "identifier_observations": 68, "observed_identifier_targets": 44, "candidate_rows": 344,
                    "negative_rows": 82},
}
# These are frozen lower/upper bounds, while current metrics are derived from
# the matrices and candidate evidence supplied to this run.
FROZEN_BASELINE = {
    "title_matches": 47, "creator_matches": 47, "language_matches": 48,
    "identifier_matches": 41, "matched_rows": 257,
    "precision": (801775, 1_000_000), "false_positive": (85366, 1_000_000),
}
MAX_BYTES = 1_000_000
MAX_ROWS = 2_000
MAX_DEPTH = 20
MAX_INTEGER_DIGITS = 12


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError("integer too large")
    return int(value)


def _raise_number(_: str) -> Any:
    raise ValueError("floating point values are forbidden")


def _check_depth(value: Any, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise ValueError("input too deep")
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, level + 1)


def load(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("input too large")
    try:
        parsed = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_raise_number,
            parse_constant=_raise_number,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("invalid input") from error
    _check_depth(parsed)
    if not isinstance(parsed, dict):
        raise ValueError("invalid document")
    return parsed


def _category(row: dict[str, Any]) -> str:
    inspection = row["inspection"]
    source = row["source"]
    extraction = row["extraction"]
    projection = row["projection"]
    comparison = row["comparison"]
    if inspection == "failure":
        if (source, extraction, projection, comparison) != (
            "unavailable", "not_attempted", "not_projected", "none"
        ):
            raise ValueError("invalid whole-item inspection failure")
        return "inspection_failure"
    if inspection != "success":
        raise ValueError("invalid inspection stage")
    if extraction == "failure":
        if (source, projection, comparison) != ("present", "not_projected", "none"):
            raise ValueError("invalid extraction-stage failure")
        return "inspection_failure"
    if extraction == "not_found":
        if (source, projection, comparison) != ("absent", "not_projected", "none"):
            raise ValueError("invalid absent-source stages")
        return "absent_in_source"
    if extraction != "found" or source != "present":
        raise ValueError("invalid extraction stages")
    if projection == "not_projected":
        if comparison != "none":
            raise ValueError("invalid projection-loss stages")
        return "extracted_not_projected"
    if projection != "projected":
        raise ValueError("invalid projection stage")
    comparisons = {
        "normalization": "normalization_mismatch",
        "semantic": "semantic_mismatch",
        "partial": "matched_partial",
        "complete": "matched_complete",
    }
    if comparison not in comparisons:
        raise ValueError("invalid comparison stage")
    return comparisons[comparison]


def _matrix(fields: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {field: {category: 0 for category in CATEGORIES} for field in fields}


def _rate(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def _view(
    name: str, fields: tuple[str, ...], matrix: dict[str, dict[str, int]],
    field_totals: Counter[str], expected: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    readable = 0
    population = 0
    for field in fields:
        total, expected_readable = expected[field]
        field_readable = sum(matrix[field].values())
        if field_totals[field] != total or field_readable != expected_readable:
            raise ValueError("profile field cardinality drift")
        readable += field_readable
        population += total
    categories = {
        category: sum(matrix[field][category] for field in fields)
        for category in CATEGORIES
    }
    return {
        "name": name,
        "evaluated_fields": list(fields),
        "population": population,
        "readable_denominator": readable,
        "by_field": matrix,
        "categories": categories,
    }


def _identifier_metrics(
    targets: Any, observations: Any, profile: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(targets, list) or not isinstance(observations, list):
        raise ValueError("invalid identifier evidence")
    if len(targets) != profile["identifier_targets"]:
        raise ValueError("identifier target denominator drift")
    if len(observations) != profile["identifier_observations"]:
        raise ValueError("identifier observation denominator drift")
    target_slots: set[int] = set()
    observed_targets: set[int] = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"slot", "observed"}:
            raise ValueError("invalid identifier target")
        if type(target["slot"]) is not int or target["slot"] < 0:
            raise ValueError("invalid identifier target slot")
        if type(target["observed"]) is not bool or target["slot"] in target_slots:
            raise ValueError("invalid identifier target")
        target_slots.add(target["slot"])
        if target["observed"]:
            observed_targets.add(target["slot"])
    if target_slots != set(range(len(targets))):
        raise ValueError("identifier target slots are detached")
    counts = Counter({relation: 0 for relation in ISBN_RELATIONS})
    observation_slots: set[int] = set()
    observed_by_evidence: set[int] = set()
    required = {
        "slot", "target_slot", "relation", "basis", "provider_scope",
        "same_work", "non_corroborating", "source_present",
    }
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != required:
            raise ValueError("invalid identifier observation")
        slot = observation["slot"]
        target_slot = observation["target_slot"]
        if (type(slot) is not int or type(target_slot) is not int or slot < 0
                or target_slot not in observed_targets or slot in observation_slots):
            raise ValueError("detached identifier observation")
        relation = observation["relation"]
        evidence = (
            observation["basis"], observation["provider_scope"],
            observation["same_work"], observation["non_corroborating"],
            observation["source_present"],
        )
        if relation not in OBSERVATION_TUPLES or evidence != OBSERVATION_TUPLES[relation]:
            raise ValueError("invalid identifier relation evidence")
        if any(type(observation[key]) is not bool for key in (
            "same_work", "non_corroborating", "source_present"
        )):
            raise ValueError("invalid identifier observation")
        observation_slots.add(slot)
        observed_by_evidence.add(target_slot)
        counts[relation] += 1
    if (observation_slots != set(range(len(observations)))
            or observed_by_evidence != observed_targets
            or len(observed_targets) != profile.get("observed_identifier_targets", len(observed_targets))):
        raise ValueError("identifier observations are detached")
    observation_denominator = len(observations)
    target_denominator = len(targets)
    return {
        "counts": dict(counts),
        "observation_denominator": observation_denominator,
        "target_denominator": target_denominator,
        "accepted_recall": _rate(
            len({item["target_slot"] for item in observations if item["relation"] == "accepted"}),
            target_denominator,
        ),
        "associated_yield": _rate(counts["associated"], observation_denominator),
        "unrelated_rate": _rate(counts["unrelated"], observation_denominator),
        "invalid_rate": _rate(counts["invalid"], observation_denominator),
        "unresolved_rate": _rate(counts["unresolved"], observation_denominator),
    }


def _regression(
    supported: dict[str, Any], candidate_rows: Any, negative_rows: Any,
    profile: dict[str, Any],
) -> dict[str, Any]:
    if (not isinstance(candidate_rows, list)
            or len(candidate_rows) != profile["candidate_rows"]):
        raise ValueError("candidate evidence denominator drift")
    if not isinstance(negative_rows, list) or len(negative_rows) != profile["negative_rows"]:
        raise ValueError("negative evidence denominator drift")
    matched_candidates = 0
    false_positives = 0
    for row in candidate_rows:
        if not isinstance(row, dict) or set(row) != {"outcome"}:
            raise ValueError("invalid candidate evidence")
        if row["outcome"] not in {"matched", "nonmatching"}:
            raise ValueError("invalid candidate outcome")
        matched_candidates += row["outcome"] == "matched"
    for row in negative_rows:
        if not isinstance(row, dict) or set(row) != {"has_candidate"}:
            raise ValueError("invalid negative evidence")
        if type(row["has_candidate"]) is not bool:
            raise ValueError("invalid negative evidence")
        false_positives += row["has_candidate"]
    by_field = supported["by_field"]
    controls = {
        "title_matches": by_field["title"]["matched_complete"] + by_field["title"]["matched_partial"],
        "creator_matches": by_field["creator"]["matched_complete"] + by_field["creator"]["matched_partial"],
        "language_matches": by_field["language"]["matched_complete"] + by_field["language"]["matched_partial"],
        "identifier_matches": by_field["identifier"]["matched_complete"] + by_field["identifier"]["matched_partial"],
        "matched_rows": supported["categories"]["matched_complete"] + supported["categories"]["matched_partial"],
        "candidate_matches": matched_candidates,
        "candidate_denominator": len(candidate_rows),
        "false_positives": false_positives,
        "explicit_negative_denominator": len(negative_rows),
    }
    gates = {
        "supported-population": supported["population"] == 405,
        "title": controls["title_matches"] >= FROZEN_BASELINE["title_matches"],
        "creator": controls["creator_matches"] >= FROZEN_BASELINE["creator_matches"],
        "language": controls["language_matches"] >= FROZEN_BASELINE["language_matches"],
        "identifier": controls["identifier_matches"] >= FROZEN_BASELINE["identifier_matches"],
        "matched-rows": controls["matched_rows"] >= FROZEN_BASELINE["matched_rows"],
        "candidate-precision": (
            controls["candidate_matches"] * FROZEN_BASELINE["precision"][1]
            >= FROZEN_BASELINE["precision"][0] * controls["candidate_denominator"]
        ),
        "explicit-negative-fp": (
            controls["false_positives"] * FROZEN_BASELINE["false_positive"][1]
            <= FROZEN_BASELINE["false_positive"][0]
            * controls["explicit_negative_denominator"]
        ),
    }
    return {
        "frozen_baseline": FROZEN_BASELINE,
        "current": controls,
        "gates": gates,
        "passed": all(gates.values()),
    }


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema", "profile", "rows", "identifier_targets",
        "identifier_observations", "candidate_rows", "negative_rows",
    }
    if (not isinstance(document, dict) or set(document) != required
            or document.get("schema") != INPUT_SCHEMA
            or document.get("profile") not in PROFILES):
        raise ValueError("invalid document")
    rows = document["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("invalid row collection")
    profile = PROFILES[document["profile"]]
    matrices = {"supported": _matrix(SUPPORTED), "exploratory": _matrix(EXPLORATORY)}
    excluded = Counter({state: 0 for state in EXCLUDED})
    source_present = Counter({"supported": 0, "exploratory": 0})
    field_totals: Counter[str] = Counter()
    failure_subtypes = Counter({"whole_item": 0, "extraction_stage": 0})
    value_fields = SUPPORTED + EXPLORATORY
    for row in rows:
        if not isinstance(row, dict) or row.get("field") not in value_fields:
            raise ValueError("invalid row")
        field = row["field"]
        field_totals[field] += 1
        state = row.get("state")
        if state in EXCLUDED:
            if set(row) != {"field", "state"}:
                raise ValueError("excluded row carries evidence")
            if state == "accepted_unsupported" and field not in EXPLORATORY:
                raise ValueError("unsupported state applies only to exploratory fields")
            excluded[state] += 1
            continue
        expected_keys = {
            "field", "state", "readable", "inspection", "source", "extraction",
            "projection", "comparison",
        }
        if (state != "value" or set(row) != expected_keys or row["readable"] is not True
                or any(type(row[key]) is not str for key in expected_keys - {"readable"})):
            raise ValueError("invalid value row")
        target = "supported" if field in SUPPORTED else "exploratory"
        if row["source"] == "present":
            source_present[target] += 1
        category = _category(row)
        matrices[target][field][category] += 1
        if category == "inspection_failure":
            failure_subtypes["whole_item" if row["inspection"] == "failure" else "extraction_stage"] += 1
    # PROFILES entries are heterogeneous literals, so mypy widens each value
    # to object; "fields" is the field-bound mapping _view declares.
    expected_fields = cast("dict[str, tuple[int, int]]", profile["fields"])
    if not set(field_totals) <= set(expected_fields):
        raise ValueError("unexpected profile field")
    supported = _view("supported", SUPPORTED, matrices["supported"], field_totals, expected_fields)
    exploratory = _view("exploratory", EXPLORATORY, matrices["exploratory"], field_totals, expected_fields)
    if sum(failure_subtypes.values()) != (
        supported["categories"]["inspection_failure"]
        + exploratory["categories"]["inspection_failure"]
    ):
        raise ValueError("inspection failure arithmetic")
    isbn = _identifier_metrics(
        document["identifier_targets"], document["identifier_observations"], profile
    )
    regression = _regression(
        supported,
        document["candidate_rows"],
        document["negative_rows"],
        profile,
    )
    recovered = (
        exploratory["categories"]["matched_complete"]
        + exploratory["categories"]["matched_partial"]
    )
    recommendations = []
    for field, values in supported["by_field"].items():
        for category in (
            "extracted_not_projected", "normalization_mismatch", "semantic_mismatch"
        ):
            if values[category]:
                recommendations.append({
                    "field": field,
                    "category": category,
                    "scope": "bounded-diagnosis",
                    "count": values[category],
                })
    return {
        "schema": OUTPUT_SCHEMA,
        "profile": document["profile"],
        "views": {"supported": supported, "exploratory": exploratory},
        "excluded": dict(excluded),
        "inspection_failure_subtypes": dict(failure_subtypes),
        "exploratory_recoverable_recall": _rate(
            recovered,
            source_present["exploratory"],
        ),
        "isbn": isbn,
        "regression": regression,
        "recommendations": recommendations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        args.output.write_text(
            json.dumps(evaluate(load(args.input)), sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RecursionError, ValueError):
        raise SystemExit("Recoverability evaluation failed safely.") from None


if __name__ == "__main__":
    main()
