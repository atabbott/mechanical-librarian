import copy
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "recoverability_evaluator", ROOT / "scripts" / "recoverability_evaluator.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def load():
    return json.loads((ROOT / "tests/support/recoverability_mini_corpus.json").read_text())


def test_categories_exclusions_and_arithmetic_are_exhaustive():
    result = MODULE.evaluate(load())
    supported = result["views"]["supported"]
    assert set(supported["categories"]) == set(MODULE.CATEGORIES)
    assert sum(supported["categories"].values()) == supported["readable_denominator"]
    assert result["excluded"] == {state: 1 for state in MODULE.EXCLUDED}
    assert result["isbn"]["counts"] == {"accepted": 1, "associated": 2, "unrelated": 1, "invalid": 1, "unresolved": 1}
    assert result["isbn"]["accepted_recall"] == {"numerator": 1, "denominator": 6}
    assert result["inspection_failure_subtypes"] == {"whole_item": 1, "extraction_stage": 0}


@pytest.mark.parametrize("comparison", ("complete", "semantic", "partial"))
@pytest.mark.parametrize("field", ("edition", "rights"))
def test_exploratory_positive_negative_and_ambiguous_rows_never_change_supported(
    field, comparison
):
    document = load()
    before = MODULE.evaluate(document)["views"]["supported"]
    changed = copy.deepcopy(document)
    for row in changed["rows"]:
        if row.get("field") == field and row.get("state") == "value":
            row["comparison"] = comparison
    assert MODULE.evaluate(changed)["views"]["supported"] == before


def test_whole_item_failure_is_not_exploratory_recoverable_evidence():
    document = load()
    before = MODULE.evaluate(document)["exploratory_recoverable_recall"]["denominator"]
    row = next(row for row in document["rows"] if row.get("field") == "edition" and row.get("state") == "value")
    row.update({"inspection": "failure", "source": "unavailable", "extraction": "not_attempted", "projection": "not_projected", "comparison": "none"})
    assert MODULE.evaluate(document)["exploratory_recoverable_recall"]["denominator"] == before - 1


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("inspection", "failure"),
        ("source", "present"),
        ("extraction", "found"),
        ("projection", "projected"),
        ("comparison", "complete"),
    ),
)
def test_stage_tuples_reject_every_cross_stage_contradiction(key, value):
    changed = load()
    changed["rows"][0][key] = value
    with pytest.raises(ValueError):
        MODULE.evaluate(changed)


def test_extraction_stage_failure_is_categorized_as_an_inspection_failure():
    extraction_failure = load()
    extraction_failure["rows"][2].update({"inspection": "success", "source": "present", "extraction": "failure", "projection": "not_projected", "comparison": "none"})
    assert MODULE._category(extraction_failure["rows"][2]) == "inspection_failure"


@pytest.mark.parametrize(
    ("observation", "update"),
    (
        (1, {"basis": "unavailable"}),
        (1, {"provider_scope": "none"}),
        (1, {"same_work": False}),
        (1, {"target_slot": 0}),
        (5, {"slot": 0}),
    ),
    ids=("basis", "provider_scope", "same_work", "target_slot", "slot"),
)
def test_identifier_evidence_is_exact_and_target_bound(observation, update):
    document = load()
    changed = copy.deepcopy(document)
    changed["identifier_observations"][observation].update(update)
    with pytest.raises(ValueError):
        MODULE.evaluate(changed)


def test_regression_is_derived_not_declared_and_profiles_are_distinct():
    document = load()
    result = MODULE.evaluate(document)
    assert result["regression"]["current"]["matched_rows"] == 2
    changed = copy.deepcopy(document)
    changed["negative_rows"][0]["has_candidate"] = "private value"
    with pytest.raises(ValueError):
        MODULE.evaluate(changed)
    changed = copy.deepcopy(document)
    changed["profile"] = "gold-50-v1"
    with pytest.raises(ValueError):
        MODULE.evaluate(changed)


def test_private_values_paths_and_unicode_identifiers_are_rejected_or_omitted():
    document = load()
    document["rows"][0]["field"] = "Secret /private/library/9780306406157"
    with pytest.raises(ValueError):
        MODULE.evaluate(document)
    document = load()
    document["identifier_observations"][0]["basis"] = "秘密9780306406157"
    with pytest.raises(ValueError):
        MODULE.evaluate(document)
    rendered = json.dumps(MODULE.evaluate(load()))
    assert "Secret" not in rendered
    assert "9780306406157" not in rendered


@pytest.mark.parametrize(
    "payload",
    (
        b"x" * (MODULE.MAX_BYTES + 1),
        b'{"schema":"a","schema":"b"}', b'{"number":1.1}', b'{"number":NaN}',
        b'{"number":1234567890123}', b'\xff',
        (b"[" * 22) + (b"]" * 22),
    ),
    ids=(
        "oversized_file", "duplicate_key", "fractional", "not_a_number",
        "oversized_integer", "invalid_utf8", "deep_nesting",
    ),
)
def test_loader_rejects_size_depth_utf8_duplicate_and_numeric_abuse(payload):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "input.json"
        source.write_bytes(payload)
        with pytest.raises(ValueError):
            MODULE.load(source)


def test_cli_fails_without_echoing_private_path():
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "input.json"
        target = Path(directory) / "output.json"
        source.write_text('{"secret":"/private/library/Secret"}', encoding="utf-8")
        result = subprocess.run(
            ["python3", str(ROOT / "scripts/recoverability_evaluator.py"), "--input", str(source), "--output", str(target)],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode != 0
        assert result.stderr == "Recoverability evaluation failed safely.\n"
        assert "Secret" not in result.stderr
        assert not target.exists()
