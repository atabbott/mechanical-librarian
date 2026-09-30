from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import ValidationError

from mechanical_librarian.domain.reviewer_cost import ReviewerCostEvidence


def test_shipped_evidence_is_empty_identical_and_private_free():
    public = Path("docs/reference/reviewer-cost-evidence-v1.json").read_bytes()
    assert (
        files("mechanical_librarian")
        .joinpath("resources/reviewer-cost-evidence-v1.json")
        .read_bytes()
        == public
    )
    evidence = ReviewerCostEvidence.model_validate_json(public)
    assert evidence.measured_run is None and evidence.models == ()


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_cost_usd", "NaN"),
        ("per_1000_usd", "1"),
        ("requests_attempted", 1),
        ("requests_completed", 2),
        ("private_text", "not allowed"),
    ],
)
def test_public_aggregate_refuses_invalid_or_incomplete_rows(field, value):
    import json

    row = {
        "model": "example/model",
        "task": "cover",
        "requests_selected": 2,
        "requests_attempted": 2,
        "requests_completed": 1,
        "requests_failed": 1,
        "works": 2,
        "distinct_images": 3,
        "total_cost_usd": "0.02",
        "per_1000_usd": "10",
    }
    evidence = {
        "schema_version": "reviewer-cost-evidence-v1",
        "measured_run": {
            "source_head": "a" * 40,
            "cohort_sha256": "b" * 64,
            "observed_at": 1,
        },
        "models": [row],
    }
    assert (
        ReviewerCostEvidence.model_validate_json(json.dumps(evidence))
        .models[0]
        .requests_failed
        == 1
    )
    evidence["models"] = [row, row]
    with pytest.raises(ValidationError):
        ReviewerCostEvidence.model_validate_json(json.dumps(evidence))
    evidence["models"] = [row]
    row[field] = value
    with pytest.raises(ValidationError):
        ReviewerCostEvidence.model_validate_json(json.dumps(evidence))
