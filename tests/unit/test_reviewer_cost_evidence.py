from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import ValidationError

from mechanical_librarian.domain.reviewer_cost import ReviewerCostEvidence


def test_shipped_evidence_keeps_each_row_with_its_own_complete_run():
    public = Path("docs/reference/reviewer-cost-evidence-v2.json").read_bytes()
    assert (
        files("mechanical_librarian")
        .joinpath("resources/reviewer-cost-evidence-v2.json")
        .read_bytes()
        == public
    )
    evidence = ReviewerCostEvidence.model_validate_json(public)
    assert len(evidence.models) == 2
    description, cover = evidence.models
    assert (description.model, description.task) == ("openai/gpt-6-luna", "description")
    assert description.measured_run.source_head == (
        "4f9264a1e8759d55a40bb58129ab0820d9bfe142"
    )
    assert description.measured_run.cohort_sha256 == (
        "90dc7d10d2e174a5c69dd925100fc6632b2c3cf49316f419822cbb4cc1d81911"
    )
    assert description.measured_run.observed_at == 1790929488
    assert (
        description.requests_selected
        == description.requests_attempted
        == description.requests_completed
        == description.works
        == 50
    )
    assert description.requests_failed == description.distinct_images == 0
    assert description.total_cost_usd == "0.008565275"
    assert description.per_1000_usd == "0.1713055000"
    assert (cover.model, cover.task) == ("openai/gpt-6-luna", "cover")
    assert cover.measured_run.source_head == (
        "3ac47aea8cd97364b1d2ebda935d455209debebb"
    )
    assert cover.measured_run.cohort_sha256 == (
        "36e3ed083b420fb6d45406734227f6434b0b4d69a78390bb00a5a8b8ce49774d"
    )
    assert cover.measured_run.observed_at == 1791924850
    assert (
        cover.requests_selected
        == cover.requests_attempted
        == cover.requests_completed
        == cover.works
        == 100
    )
    assert cover.requests_failed == 0
    assert cover.distinct_images == 270
    assert cover.total_cost_usd == "0.063641500"
    assert cover.per_1000_usd == "0.636415000"


def test_documented_cover_continuation_keeps_its_own_native_provenance():
    evidence = ReviewerCostEvidence.model_validate_json(
        Path("docs/reference/reviewer-cost-cover-continuation-v2.json").read_bytes()
    )
    assert len(evidence.models) == 1
    row = evidence.models[0]
    assert row.measured_run.source_head == "4f9264a1e8759d55a40bb58129ab0820d9bfe142"
    assert row.measured_run.cohort_sha256 == (
        "7eb0f02e4f8e04c35fb52cf1f27ac808b6b1aee3302e63a212d02a4b0c683891"
    )
    assert row.measured_run.observed_at == 1790929028
    assert (row.model, row.task) == ("xiaomi/mimo-v2.6-flash", "cover")
    assert (
        row.requests_selected == row.requests_attempted == row.requests_completed == 71
    )
    assert row.requests_failed == 0
    assert row.distinct_images == 213
    assert row.total_cost_usd == "0.057402618"
    assert row.per_1000_usd == "0.8084875774647887323943661972"


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_cost_usd", "NaN"),
        ("per_1000_usd", "1"),
        ("requests_attempted", 1),
        ("requests_completed", 2),
        ("private_text", "not allowed"),
        ("measured_run", None),
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
        "measured_run": {
            "source_head": "a" * 40,
            "cohort_sha256": "b" * 64,
            "observed_at": 1,
        },
    }
    evidence = {"schema_version": "reviewer-cost-evidence-v2", "models": [row]}
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


def test_evidence_refuses_a_row_without_its_run_or_a_top_level_run():
    import json

    run = {"source_head": "a" * 40, "cohort_sha256": "b" * 64, "observed_at": 1}
    row = {
        "model": "example/model",
        "task": "description",
        "requests_selected": 1,
        "requests_attempted": 1,
        "requests_completed": 1,
        "requests_failed": 0,
        "works": 1,
        "distinct_images": 0,
        "total_cost_usd": "0.01",
        "per_1000_usd": "10",
    }
    evidence: dict[str, object] = {
        "schema_version": "reviewer-cost-evidence-v2",
        "models": [],
    }
    assert ReviewerCostEvidence.model_validate_json(json.dumps(evidence)).models == ()
    evidence["models"] = [row]
    with pytest.raises(ValidationError, match="measured_run"):
        ReviewerCostEvidence.model_validate_json(json.dumps(evidence))
    evidence["models"] = [{**row, "measured_run": run}]
    assert ReviewerCostEvidence.model_validate_json(json.dumps(evidence)).models
    with pytest.raises(ValidationError, match="measured_run"):
        ReviewerCostEvidence.model_validate_json(
            json.dumps({**evidence, "measured_run": run})
        )
    with pytest.raises(ValidationError):
        ReviewerCostEvidence.model_validate_json(
            json.dumps({**evidence, "schema_version": "reviewer-cost-evidence-v1"})
        )
