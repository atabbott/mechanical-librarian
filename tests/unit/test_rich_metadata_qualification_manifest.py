"""Compact authority records bind exactly the permitted operations (ADR-0087)."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts import verify_qualification_records as records

from mechanical_librarian.domain import audiobook_decision_qualification as audio
from mechanical_librarian.domain import automatic_decision as automatic
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_OPTION_DISPOSITIONS,
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
    public_summary,
)

ROOT = Path(__file__).parents[2]
F38_MANIFEST = (
    ROOT / "docs/reference/f38-contributor-display-qualification-manifest-v1.json"
)


def test_current_records_match_runtime_permissions():
    records.verify()
    current = {
        "F19A": automatic.F19A_QUALIFIED_POLICY_OPTIONS,
        "F20": F20_PASSING_AUTOMATIC_OPTIONS,
        "F46": audio.F46_PASSING_AUTOMATIC_OPTIONS,
    }
    assert [len(current[f]) for f in ("F19A", "F20", "F46")] == [11, 13, 71]
    for family, (name, _, _) in records.RECORDS.items():
        document = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        assert (
            frozenset(document["authority"]["permitted_operations"]) == current[family]
        )
    document = json.loads(
        (ROOT / records.REFERENCE / records.RECORDS["F46"][0]).read_bytes()
    )
    assert (
        set(document["authority"]["paired_operations"])
        == audio.F46_PASSING_PAIRED_OPTIONS
    )
    assert len(audio.F46_PASSING_PAIRED_OPTIONS) == 1


def test_all_dispositions_and_public_summary_keep_exact_authority():
    document = json.loads(
        (ROOT / records.REFERENCE / records.RECORDS["F20"][0]).read_bytes()
    )
    assert document["authority"]["dispositions"] == dict(F20_OPTION_DISPOSITIONS)
    assert len(F20_OPTION_DISPOSITIONS) == 19
    assert F20_OPTION_DISPOSITIONS["tags:off"] == "policy_only"
    summary = public_summary()
    assert {entry["candidate_sha256"] for entry in summary} == {
        F20_QUALIFICATION_CANDIDATE_SHA256
    }
    assert {
        f"{e['field']}:{e['option']}" for e in summary if e["automatic_available"]
    } == F20_PASSING_AUTOMATIC_OPTIONS
    assert {e["disposition"] for e in summary} == {
        "passing_automatic",
        "policy_only",
        "passing_human_only",
        "not_qualified",
        "insufficient_coverage",
    }
    with pytest.raises(TypeError):
        F20_OPTION_DISPOSITIONS["tags:off"] = "passing_automatic"


def test_evidence_references_are_not_authority():
    for name, _, _ in records.RECORDS.values():
        record = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        changed = copy.deepcopy(record)
        changed["provenance"] = "different-evidence.json"
        assert records.canonical_digest(
            changed["authority"]
        ) == records.canonical_digest(record["authority"])
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    assert provenance["historical_quality_rescored"] is False
    assert provenance["fresh_holdout_claim"] is False
    assert provenance["production_writes"] == 0
    assert len(provenance["measurement_artifacts"]) == 14
    assert provenance["current_isbn_measurement"]["distinct_cases"] == 19
    assert provenance["current_isbn_measurement"]["unavailable_cases"] == 16
    assert provenance["current_isbn_measurement"]["precision_per_option"] == 1.0
    assert provenance["current_measurement"]["production_writes"] == 0


def test_f38_manifest_separately_qualifies_provider_consensus_contributor_display() -> (
    None
):
    manifest_bytes = F38_MANIFEST.read_bytes()
    manifest = json.loads(manifest_bytes)

    assert (
        hashlib.sha256(manifest_bytes).hexdigest() == F38_QUALIFICATION_MANIFEST_SHA256
    )
    assert "f38-contributor-display-qualification-manifest-v1" == manifest["schema"]
    assert (
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
        f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}" == manifest["policy_identity"]
    )
    assert "decision_only" == manifest["automatic_authority"]
    assert "development_exposed" == manifest["corpus_role"]
    assert False is manifest["holdout_claim"]
    assert {
        "automatic_precision": 1.0,
        "automatic_predictions": 3,
        "automatic_recall": 1.0,
        "positive_opportunities": 3,
        "zero_tolerance_failures": 0,
    } == manifest["metrics"]


def _copy_records(root: Path) -> None:
    for path in [
        records.PROVENANCE,
        *[records.REFERENCE / name for name, _, _ in records.RECORDS.values()],
        *[
            Path("src/mechanical_librarian/domain") / module
            for _, module, _ in records.RECORDS.values()
        ],
    ]:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes((ROOT / path).read_bytes())


@pytest.mark.parametrize(
    "mutation", ["family", "media", "schema", "origin_family", "provenance"]
)
def test_record_family_and_references_are_coherent(tmp_path, mutation):
    _copy_records(tmp_path)
    path = tmp_path / records.REFERENCE / records.RECORDS["F19A"][0]
    document = json.loads(path.read_bytes())
    provenance_path = tmp_path / records.PROVENANCE
    provenance = json.loads(provenance_path.read_bytes())
    if mutation in {"family", "media"}:
        document["authority"][mutation] = "F46" if mutation == "family" else "audiobook"
    elif mutation == "schema":
        document["schema"] = "qualification-current-v1"
    elif mutation == "origin_family":
        provenance["historical_measurement_origins"]["F19A"] = provenance[
            "historical_measurement_origins"
        ]["F46"]
    else:
        document["provenance"] = "unbound.json"
    path.write_text(json.dumps(document))
    provenance_path.write_text(json.dumps(provenance))
    with pytest.raises(ValueError, match="qualification"):
        records.verify(tmp_path)


def test_retained_measurement_context_matches_immutable_base():
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    # Independently captured from the base F46-v20 / F20-v51 records; no archive lookup.
    expected = {
        "current_measurement": "5bc75d52d821e9f91607d0a567382162677bee954a8b4185891874260883a760",
        "measurement_artifacts": "131f350ce18f5d4dc1df46f6364278af148027a95c0db63c5b6b8cd51f698151",
        "limitations": "a5ff8bf309e9f8c5881c3804a4018336f23093a90a707942eb1e1d4186d04669",
        "current_ebook_measurement": "68257847af41acebfa3fbc20bbe61bdc4ae9e2a8df4a6f2aeea1cd4214258efc",
    }
    for key, digest in expected.items():
        assert records.canonical_digest(provenance[key]) == digest


@pytest.mark.parametrize("family", list(records.RECORDS))
@pytest.mark.parametrize("scope", ["record", "authority"])
def test_closed_record_keysets_refuse(tmp_path, family, scope):
    _copy_records(tmp_path)
    path = tmp_path / records.REFERENCE / records.RECORDS[family][0]
    original = json.loads(path.read_bytes())
    target = original if scope == "record" else original["authority"]
    for field in [*target, "incidental_evidence"]:
        changed = copy.deepcopy(original)
        destination = changed if scope == "record" else changed["authority"]
        if field in target:
            del destination[field]
        else:
            destination[field] = "not authority"
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match=f"qualification {scope} keys"):
            records.verify(tmp_path)


@pytest.mark.parametrize("family", list(records.RECORDS))
@pytest.mark.parametrize("change", ["remove", "add"])
def test_permitted_operation_change_without_runtime_constant_drifts(
    tmp_path, family, change
):
    _copy_records(tmp_path)
    records.verify(tmp_path)
    path = tmp_path / records.REFERENCE / records.RECORDS[family][0]
    record = json.loads(path.read_bytes())
    operations = record["authority"]["permitted_operations"]
    if change == "remove":
        operations.pop()
    else:
        operations.append("title_layout:unqualified")
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="runtime authority binding drift"):
        records.verify(tmp_path)


@pytest.mark.parametrize(
    "declaration",
    [
        "",
        'BOUND = "a" * 64',
        'BOUND = "' + "0" * 64 + '"\nBOUND = "' + "1" * 64 + '"',
        "BOUND: str",
    ],
)
def test_binding_assignment_requires_one_top_level_literal(declaration):
    with pytest.raises(ValueError, match="runtime binding assignment"):
        records.binding_value('# BOUND = "' + "2" * 64 + '"\n' + declaration, "BOUND")


@pytest.mark.parametrize("family", list(records.RECORDS))
@pytest.mark.parametrize("decoy", ["comment", "string"])
def test_decoy_binding_cannot_conceal_stale_runtime_constant(tmp_path, family, decoy):
    _copy_records(tmp_path)
    _, module, constant = records.RECORDS[family]
    module_path = tmp_path / "src/mechanical_librarian/domain" / module
    source = module_path.read_text()
    current = records.binding_value(source, constant).value
    assert type(current) is str
    example = f'{constant} = "{current}"'
    source = source.replace(f'"{current}"', '"' + "0" * 64 + '"')
    source = (
        "# " + example + "\n" + source
        if decoy == "comment"
        else source.replace('"""', '"""' + example + "\n", 1)
    )
    module_path.write_text(source)
    with pytest.raises(ValueError, match="runtime authority binding drift"):
        records.verify(tmp_path)


@pytest.mark.parametrize("family", ["F19A", "F20", "F46"])
def test_activity_predecessor_producer_decisions_are_rejected(family):
    fixture = json.loads(
        (
            ROOT / "tests/fixtures/activity-qualification-predecessor-decisions.json"
        ).read_bytes()
    )
    assert fixture["source_sha"] == "43359510da9cc04aed874714728d1581801021e9"
    assert len(fixture["documents"][family]) == 1
    for document in fixture["documents"][family]:
        with pytest.raises(automatic.AutomaticDecisionError):
            automatic.AutomaticDecision.from_json(document)
