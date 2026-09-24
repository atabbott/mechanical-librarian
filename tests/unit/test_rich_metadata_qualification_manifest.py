"""Compact authority records, reviewed source ownership and frozen decoding parity."""

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
FIXTURE = ROOT / "tests/fixtures/qualification-authority-predecessor"
F38_MANIFEST = (
    ROOT / "docs/reference/f38-contributor-display-qualification-manifest-v1.json"
)


def test_current_records_match_runtime_and_frozen_permissions():
    records.verify()
    expected = json.loads((FIXTURE / "historical-permissions.json").read_bytes())
    provenance = json.loads((FIXTURE / "provenance.json").read_bytes())
    for name, digest in provenance["files"].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest
    current = {
        "F19A": automatic.F19A_QUALIFIED_POLICY_OPTIONS,
        "F20": F20_PASSING_AUTOMATIC_OPTIONS,
        "F46": audio.F46_PASSING_AUTOMATIC_OPTIONS,
    }
    assert [len(current[f]) for f in ("F19A", "F20", "F46")] == [12, 13, 76]
    for family, (name, _, _) in records.RECORDS.items():
        document = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        assert (
            frozenset(document["authority"]["permitted_operations"]) == current[family]
        )
        historical = dict(
            getattr(automatic, f"_PRIOR_{family}_QUALIFICATION_MANIFESTS")
        )
        frozen = {
            digest: frozenset(expected["permission_sets"][index])
            for digest, index in expected["families"][family].items()
        }
        assert historical == frozen | {
            document["predecessor"]["sha256"]: current[family]
        }
        assert all(options is not current[family] for options in historical.values())
    assert (
        len(
            {
                id(s)
                for f in current
                for _, s in getattr(automatic, f"_PRIOR_{f}_QUALIFICATION_MANIFESTS")
            }
        )
        == 10
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


@pytest.mark.parametrize(
    "name",
    [
        "src/mechanical_librarian/domain/rich_metadata_preview.py",
        "src/mechanical_librarian/application/review_web.py",
        "src/mechanical_librarian/interfaces/web/app.py",
        "src/mechanical_librarian/adapters/persistence/field_effect_activity.py",
    ],
)
def test_semantic_changes_rotate_authority(name):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    baseline = records.semantic_digest(
        provenance, records.source_bindings(ROOT, provenance)
    )
    modified = records.source_bindings(
        ROOT, provenance, {name: (ROOT / name).read_bytes() + b"\n# changed\n"}
    )
    assert records.semantic_digest(provenance, modified) != baseline


@pytest.mark.parametrize(
    "name",
    [
        "frontend/src/routes/PolicyPage.tsx",
        "src/mechanical_librarian/application/operations.py",
        "tests/unit/test_rich_metadata_qualification_manifest.py",
    ],
)
def test_provenance_changes_do_not_rotate_authority(name):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    bindings = records.source_bindings(ROOT, provenance)
    modified = records.source_bindings(
        ROOT, provenance, {name: (ROOT / name).read_bytes() + b"\n// changed\n"}
    )
    assert modified != bindings
    assert records.semantic_digest(provenance, modified) == records.semantic_digest(
        provenance, bindings
    )


@pytest.mark.parametrize(
    "mutation",
    ["omitted", "unknown", "reclassified", "exclusion", "ast_source", "override"],
)
def test_unreviewed_partition_and_exclusions_refuse(mutation):
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    name = "src/mechanical_librarian/domain/proposal_set.py"
    overrides = None
    if mutation == "omitted":
        del provenance["source_classification"][name]
    elif mutation == "unknown":
        provenance["source_classification"]["unknown.py"] = "semantic"
    elif mutation == "reclassified":
        provenance["source_classification"][name] = "provenance"
    elif mutation == "exclusion":
        provenance["binding_exclusions"].append("QUALIFIED_POLICY_OPTIONS")
    elif mutation == "ast_source":
        provenance["ast_sources"].append(name)
    else:
        overrides = {"unknown.py": b""}
    with pytest.raises(ValueError):
        records.source_bindings(ROOT, provenance, overrides)


def test_evidence_and_predecessor_references_are_not_authority():
    for name, _, _ in records.RECORDS.values():
        record = json.loads((ROOT / records.REFERENCE / name).read_bytes())
        changed = copy.deepcopy(record)
        changed["predecessor"]["source_sha"] = "0" * 40
        changed["provenance"] = "different-evidence.json"
        assert records.canonical_digest(
            changed["authority"]
        ) == records.canonical_digest(record["authority"])
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    assert provenance["historical_quality_rescored"] is False
    assert provenance["fresh_holdout_claim"] is False
    assert provenance["production_writes"] == 0
    assert len(provenance["measurement_artifacts"]) == 14
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


def test_new_product_source_requires_review(tmp_path):
    import shutil

    shutil.copytree(ROOT / "src", tmp_path / "src")
    (tmp_path / "src/mechanical_librarian/new_authority.py").write_text(
        "grant = True\n"
    )
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    with pytest.raises(ValueError, match="product source inventory"):
        records.source_bindings(tmp_path, provenance)


def test_only_named_binding_assignments_break_the_cycle():
    name = "src/mechanical_librarian/domain/automatic_decision.py"
    source = (ROOT / name).read_text()
    bound = source.replace(automatic.F19A_QUALIFICATION_MANIFEST_SHA256, "0" * 64)
    assert records.source_digest(name, source.encode()) == records.source_digest(
        name, bound.encode()
    )
    changed = source.replace('"title_layout:preserve"', '"title_layout:unknown"', 1)
    assert changed != source
    assert records.source_digest(name, source.encode()) != records.source_digest(
        name, changed.encode()
    )
