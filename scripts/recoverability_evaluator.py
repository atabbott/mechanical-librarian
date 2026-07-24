#!/usr/bin/env python3
"""Bounded, aggregate-only recoverability qualification evaluator."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

INPUT_SCHEMA = "recoverability-evaluator-v3"
OUTPUT_SCHEMA = "recoverability-aggregate-v3"
SUPPORTED = ("title", "subtitle", "creator", "contributor", "publisher", "language", "publication_date", "identifier", "collection", "collection_position")
EXPLORATORY = ("edition", "rights")
CATEGORIES = ("absent_in_source", "inspection_failure", "extracted_not_projected", "normalization_mismatch", "semantic_mismatch", "matched_partial", "matched_complete")
ISBN_RELATIONS = ("accepted", "associated", "unrelated", "invalid", "unresolved")
EXCLUDED = ("unknown", "known_empty", "not_applicable", "accepted_unsupported", "unexplained_failure")
PROFILES = {
    "synthetic-v1": {
        "population": {"supported": 7, "exploratory": 1},
        "readable": {"supported": 7, "exploratory": 1},
    },
    "gold-50-v1": {
        "population": {"supported": 405, "exploratory": 94},
        "readable": {"supported": 397, "exploratory": 92},
        "fields": {
            "title": (50, 49), "subtitle": (10, 10), "creator": (50, 49),
            "contributor": (36, 36), "publisher": (47, 46),
            "language": (50, 49), "publication_date": (50, 49),
            "identifier": (47, 46), "collection": (36, 35),
            "collection_position": (29, 28), "edition": (47, 46),
            "rights": (47, 46),
        },
    },
}
FLOORS = {"title": (48, 49), "creator": (48, 49), "language": (49, 49), "matched_rows": (253, 489), "precision": (804734, 1_000_000), "false_positive": (7, 82)}
MAX_BYTES = 1_000_000
MAX_ROWS = 2_000
MAX_DEPTH = 20
MAX_INTEGER_DIGITS = 12


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _integer(value: str) -> int:
    if len(value.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError("integer too large")
    return int(value)


def load(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("input too large")
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_int=_integer, parse_float=lambda _: (_ for _ in ()).throw(ValueError("floats forbidden")), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError("invalid input") from error
    def depth(value: Any, level: int = 0) -> None:
        if level > MAX_DEPTH:
            raise ValueError("input too deep")
        if isinstance(value, dict):
            for item in value.values(): depth(item, level + 1)
        elif isinstance(value, list):
            for item in value: depth(item, level + 1)
    depth(parsed)
    if not isinstance(parsed, dict): raise ValueError("invalid document")
    return parsed


def _category(row: dict[str, Any]) -> str:
    if row["inspection"] == "failure":
        if row["extraction"] != "not_found" or row["projection"] != "not_projected" or row["comparison"] != "none":
            raise ValueError("invalid whole inspection failure")
        return "inspection_failure"
    if row["inspection"] != "success":
        raise ValueError("invalid inspection stage")
    if row["extraction"] == "failure":
        if row["projection"] != "not_projected" or row["comparison"] != "none":
            raise ValueError("invalid extraction failure")
        return "inspection_failure"
    if row["extraction"] not in {"found", "not_found"}:
        raise ValueError("invalid extraction stage")
    if row["source"] == "absent": return "absent_in_source"
    if row["source"] != "present" or row["extraction"] != "found":
        raise ValueError("invalid evidence stages")
    if row["projection"] == "not_projected": return "extracted_not_projected"
    if row["projection"] != "projected": raise ValueError("invalid projection")
    values = {"normalization": "normalization_mismatch", "semantic": "semantic_mismatch", "partial": "matched_partial", "complete": "matched_complete"}
    if row["comparison"] not in values: raise ValueError("invalid comparison")
    return values[row["comparison"]]


def _matrix(fields: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {field: {category: 0 for category in CATEGORIES} for field in fields}


def _rate(numerator: int, denominator: int) -> dict[str, int]:
    return {"numerator": numerator, "denominator": denominator}


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    if set(document) != {"schema", "profile", "rows", "identifier_observations", "regression"} or document.get("schema") != INPUT_SCHEMA or document.get("profile") not in PROFILES:
        raise ValueError("invalid document")
    rows = document["rows"]
    observations = document["identifier_observations"]
    if not isinstance(rows, list) or not isinstance(observations, list) or len(rows) > MAX_ROWS or len(observations) > MAX_ROWS: raise ValueError("invalid row collection")
    matrices = {"supported": _matrix(SUPPORTED), "exploratory": _matrix(EXPLORATORY)}
    excluded = Counter({state: 0 for state in EXCLUDED})
    failure_subtypes = Counter({"whole_item": 0, "extraction_stage": 0})
    for row in rows:
        if not isinstance(row, dict) or row.get("field") not in SUPPORTED + EXPLORATORY or row.get("state") not in ("value",) + EXCLUDED: raise ValueError("invalid row")
        if row["state"] != "value":
            if set(row) != {"field", "state"}: raise ValueError("excluded row carries evidence")
            if row["state"] == "accepted_unsupported" and row["field"] not in EXPLORATORY: raise ValueError("unsupported state applies only to exploratory fields")
            excluded[row["state"]] += 1; continue
        required = {"field", "state", "readable", "inspection", "source", "extraction", "projection", "comparison"}
        if set(row) != required or row["readable"] is not True or any(type(row[key]) is not str for key in required - {"readable"}): raise ValueError("invalid value row")
        target = "supported" if row["field"] in SUPPORTED else "exploratory"
        classified = _category(row)
        matrices[target][row["field"]][classified] += 1
        if classified == "inspection_failure":
            failure_subtypes["whole_item" if row["inspection"] == "failure" else "extraction_stage"] += 1
    views = {}
    for name, fields in (("supported", SUPPORTED), ("exploratory", EXPLORATORY)):
        denominator = sum(sum(matrices[name][field].values()) for field in fields)
        if denominator != PROFILES[document["profile"]]["readable"][name]: raise ValueError("profile denominator drift")
        expected_fields = PROFILES[document["profile"]].get("fields")
        if expected_fields is not None:
            for field in fields:
                if sum(matrices[name][field].values()) != expected_fields[field][1]:
                    raise ValueError("profile field denominator drift")
        views[name] = {"name": name, "evaluated_fields": list(fields), "denominator": denominator, "by_field": matrices[name], "categories": {category: sum(matrices[name][field][category] for field in fields) for category in CATEGORIES}}
    isbn = Counter({relation: 0 for relation in ISBN_RELATIONS})
    seen_slots: set[int] = set()
    for observation in observations:
        if not isinstance(observation, dict) or set(observation) != {"slot", "relation", "provider_scope", "same_work", "non_corroborating", "source_present"}: raise ValueError("invalid identifier observation")
        if type(observation["slot"]) is not int or observation["slot"] < 0 or observation["slot"] in seen_slots: raise ValueError("invalid identifier slot")
        seen_slots.add(observation["slot"])
        relation = observation["relation"]
        if relation not in ISBN_RELATIONS or observation["provider_scope"] not in {"none", "hardcover"} or any(type(observation[key]) is not bool for key in ("same_work", "non_corroborating", "source_present")): raise ValueError("invalid identifier observation")
        if relation == "associated" and not (observation["provider_scope"] == "hardcover" and observation["same_work"] and observation["non_corroborating"] and observation["source_present"]): raise ValueError("invalid associated ISBN")
        if relation != "associated" and observation["same_work"]: raise ValueError("same-work evidence requires associated relation")
        isbn[relation] += 1
    if document["profile"] == "gold-50-v1" and len(observations) > views["supported"]["by_field"]["identifier"]["matched_complete"] + views["supported"]["by_field"]["identifier"]["matched_partial"]:
        raise ValueError("identifier observations exceed supported identifier matches")
    regression = document["regression"]
    if not isinstance(regression, dict) or set(regression) != {"title_matches", "creator_matches", "language_matches", "matched_rows", "precision_matches", "precision_candidates", "false_positives", "negative_rows"} or any(type(value) is not int or value < 0 for value in regression.values()): raise ValueError("invalid regression controls")
    gates = {
        "supported-denominator": views["supported"]["denominator"] == 405,
        "title": regression["title_matches"] >= FLOORS["title"][0],
        "creator": regression["creator_matches"] >= FLOORS["creator"][0],
        "language": regression["language_matches"] >= FLOORS["language"][0],
        "matched-rows": regression["matched_rows"] >= FLOORS["matched_rows"][0],
        "candidate-precision": regression["precision_matches"] * FLOORS["precision"][1] >= FLOORS["precision"][0] * regression["precision_candidates"],
        "explicit-negative-fp": regression["false_positives"] * FLOORS["false_positive"][1] <= FLOORS["false_positive"][0] * regression["negative_rows"],
    }
    if sum(failure_subtypes.values()) != views["supported"]["categories"]["inspection_failure"] + views["exploratory"]["categories"]["inspection_failure"]:
        raise ValueError("inspection failure arithmetic")
    return {"schema": OUTPUT_SCHEMA, "profile": document["profile"], "views": views, "excluded": dict(excluded), "inspection_failure_subtypes": dict(failure_subtypes), "isbn": {"counts": dict(isbn), "denominator": sum(isbn.values()), "accepted_recall": _rate(isbn["accepted"], sum(isbn.values())), "associated_yield": _rate(isbn["associated"], sum(isbn.values())), "unrelated_rate": _rate(isbn["unrelated"], sum(isbn.values())), "invalid_rate": _rate(isbn["invalid"], sum(isbn.values())), "unresolved_rate": _rate(isbn["unresolved"], sum(isbn.values()))}, "regression": {"controls": regression, "gates": gates, "passed": all(gates.values()), "note": "overall matches are rows; precision matches are candidate atoms"}, "recommendations": [{"category": category, "count": views["supported"]["categories"][category]} for category in ("extracted_not_projected", "normalization_mismatch", "semantic_mismatch") if views["supported"]["categories"][category]]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path); parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try: args.output.write_text(json.dumps(evaluate(load(args.input)), sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, RecursionError, ValueError): raise SystemExit("Recoverability evaluation failed safely.")


if __name__ == "__main__": main()
