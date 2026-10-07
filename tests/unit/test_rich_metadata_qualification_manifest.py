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
    displaced = {
        "F19A": {
            "b236b2612772f298c44e196a82e053a6b63e8eee05ea4c105bc262bdce0489ab": 2,
            "b0987ca2be0c2171545da8942b1646a99197e1315714e985b07049b637ca63b3": 2,
            "8a443c1c879ec69b64f8eef5ce4905042b570f265669411eda9ee8cddd404187": 2,
            "437a7549d2007132ba228bd828fba0356fd89f46df41a2afbf38eed8a340a052": 2,
            "1469272be676f1c42591d87cf76033b25559082da7411cac0738b716b9c0fcb2": 2,
            "8e075ba6671ac1dee8ed6c5dfc9f7285380397ba47d292d3e174742a9f962147": 2,
            "d48649f16a28bd7cc083cd2c41ef3d5372a5ee050c9e763719fafd86178dcfb2": 2,
            "a46d75a576f05ed37c3d9b23b994d349968ab1e6bab52df27aa60480462df1ae": 2,
            "6c45a65639e89e9ae1f0bec1af042fbe95e4dd33ee6021aed296b66340b6c430": 2,
            "47a01405e62e9a583438026760aa2f98665bb1b018bca1a1c6514b7102452717": 2,
            "bd0c20ddfdb7909915ce3ad307b8853381eb8ac254b7cc821864467ef24aa180": 2,
            "d7013f0cc88f2fcd476de73c0f9185c265f30ba8360f2daee08266200b57eff3": 2,
            "f7eabaf07b3893babd2bbf4dd45d299b58a37b1d2f427ad663e31fca548474b7": 2,
            "8af85b77852fa7dcb7dcc84458809aec20fc7b8b5e3dfcf95577535eeedf2038": 2,
            "6151378c5ec4c044731eef559736ca9a09144c271644f7bfd8970c7d2fa98337": 2,
            "d378d33df53a5fae94860c05a9aabf01307d2ba87c2390a8aa3d3e498fda3cad": 2,
            "61fe819cafee34ef0ec743c08632449235d819798c33ab2b58aeb5db61dd513c": 2,
            "16d37103732ce6572c043e66f2984a2b17ef932aaf23f2eea36d0647f9d834c4": 2,
            "888f2144f5edc5c8c2fc6d279882fa6d88c1569b706f3d2d48644decc8e1d625": 2,
            "73cd3e9923600d1b85b5c82155f677485d0e51eda11b5c4920bfdfcd23e80ca9": 2,
            "c23c7cdf1bdfc47e5c225b6d13299c47a7163cba6c33796f61f89410f0ee3ef3": 2,
            "cfee88ac6aa6f30a5fe918d1bdbaa0e8b005c5df901fae9f8c2eaf9197562051": 2,
            "4ce7351925e110a5808cfdd778192bc2e8a840dcb57ea73add6172c1e663dd0d": 2,
            "e959dde63913605db2d0dc5ed1a4a3c98c1e3f70a36791e6fd593bf831d0bf2d": 2,
            "28489fb10f20a47c036ebfba11584829a6598a3ec1dfae52ce993c6be10bf935": 2,
            "3606173cd73ed348a151ae34794048e217f46e6e4bae83f5c080fdfe27dd0022": 2,
            "b71766e606ac6affb2251fb68f57c9640ba5455266a592f065916b549f3fcbfc": 2,
            "796b2a4d55851a997dae96f0863a527c8820a841e1a90c9712f4cf9fc50a4c29": 2,
            "d65cdbe68edeb9b93bda94d013b1c14fb9fea40e152506d9f1a0032cf6d54db2": 2,
            "0f59b668db9c95502cc3bd02d7eb3d75e5abdae3ae45e15ec927f7cdd7160aa6": 2,
            "7b5cfb2662246bc74f4ad7745f1edfe0656fdbe0116a712fc3af93673945f9a2": 2,
            "d5903b889fe5b457c7303e5b27d51677bcf798a11ed25bb637a4a7ae4eb5cd77": 2,
        },
        "F20": {
            "5da9c325b2cacd183ceb83d342d07f22e835aeda435583716e14d13080533b52": 7,
            "013fd7173f7b748d900e9088a0955c857f1aeb3543fc630d1f2f792d6d8e1a59": 7,
            "814ad344e1579bf81e295a8c46b07000086bda409182747f43b232c5d8b476c1": 7,
            "943996cf5f21f79ab5cb2a9991a4412f2e39c1643a0187b049844159182cb58e": 7,
            "62da3c4c37ce61692120adc4c578386acd058082d081c02870c2e6799e738a9a": 7,
            "7ca996f6f834c70addd870182e3911b783597e7487f7089e20d54750bebb036c": 7,
            "c8d7621d9f532daeb8a37a88627dfd128528705988be06c67dee9d286752a0a9": 7,
            "20260cf99162a6b452f74aa02afff42efce26d590cf5e792c01f9ce72b697092": 7,
            "08b13c4390c6abe494b0af882f4632b1c6ad74618f308e88df4ba7a216cce7ab": 7,
            "efed61bd11e4db288c881d88239a11e24fbcef25b4a8cdcdf2832929599f2ec8": 7,
            "46b78078736e845aabf123f6594ef0ab1f3d48a1e73b6cce0612173afd85d038": 7,
            "0085c6617dc55aaffa65f90931c9055cfd19a2663fcbb3555a11d343b4eeafef": 7,
            "622be5be6e6ac7b8a8506bb0045ef1c0a2ee8aec4958f2550e72e92078a8c805": 7,
            "b2d2ac644ab9bf7f811583c3c63aa5a0ca483ef6f566be2216c1d3dd23f6f656": 7,
            "fb1c3754ef9167a37fe49e27421688632e8ed8871e539289be5385ce483c8520": 7,
            "9d92f8d21be1e1ce1c8d1cf8e025c7feeb6c32143ff4cb0561e484afc7be5e9c": 7,
            "5ead1bb255187d079eaa712e2c15bfeae9ed75fbd32e463c748ac13515d7ab5c": 7,
            "5226ae2d56a23b1350da62b48819c5fd2277cda51e49706919abcfc0dd901985": 7,
            "41b78db864563f25246fe0d601d85470946c01e3c553dd4402da46a54b04d537": 7,
            "8feec1320711ca12a725a17f2edccb55a173bd0ba1da178d21b2e69754555bc8": 7,
            "14e8b99fe109f080f3c3eb861f1f0c03ec5d3bb682c53cd0c41bb9a0b95a2211": 7,
            "d87ef2b962bf83b2a8efb5cb5e56de6f1d5dd9d2093778e57f4cc915d0f79daa": 7,
            "f3743d065f3bdf0df4ad9a902c7f235949f7a0aade296f9744958c5b502f2dbd": 7,
            "bc5990df48e5ada2a7ffbbc1ed92d5290600dd667dfe9d9fc99ac678f4157a85": 7,
            "8ac05c1ef678cc7fca3d98186fd75ea53a2a91f402dd11d53ee406d47aa97465": 7,
            "b529c94a7ad2099c150d743cc31959bd97430d13c2474e46f1579cbfb8011a7b": 7,
            "d01c036ee3ff4072a08a83d3f2fb55305b3fe1b0f60acf525305c337e31fef47": 7,
            "19939b85acb471fb17c076a44c6a0dd173f6c32e5a951986b9e65060458cfd8a": 7,
            "05349d253bf340c2b1a7635fdae210bb53a640f10ca4b960305969801d3bd7ac": 7,
            "d1bc961a69d7cd37ca8763b297c68666f3156fbdcc8961ceced68f3ffaecddfb": 7,
            "5f943991748c5a613ba1d8f8451040a9224e9bd028941d1e63816879b5d1edfe": 7,
            "28b95c60856b1c6a5f88b6e67f7cba9c55b215c10567eeb3b29d2a7e98233175": 7,
        },
        "F46": {
            "73ffca05139a5c108a812f0d77692c5e8742f134d74ef3591d3d0f27d618adfa": 0,
            "c242735d3aa59e823ed9db00968828acd6a3a6a536c5b72d2ca4fbe644ddfd06": 0,
            "964c3311475bbd6f623656cc3385591fef2c8c9ca97d90f3cd7a6ac020c623b7": 0,
            "6342bb7c483657aa9ea5c28af08be9c5bb34a79e773b1ad1d4f65ed759a79f67": 0,
            "54c79836aa7697fbcd0b90f23ac50ca7f17dc3d76ca8de4c133a7c1516042cb9": 0,
            "994dc921adc536bb7c22d1ed48e3b33defcf1e9a0a30b1393d508ce5af85a0b9": 0,
            "1ece3263e7f89f584bbe7a6d0d569ccc1afa6efd44570ae4bb87b4059bb7fc68": 0,
            "c23baea19f9bf94d515bc9672893aee73f2a0d27b399247ee4c1f7c53205f046": 0,
            "941c907ce734ca42bd59424e2e8e6950932e2baf00b4624fe19976718ad7943c": 0,
            "5754c99c3dd2f3ba7a50d9e424dc3aa53012ed1b5d1a2bf8a43afc1bd5bf7f79": 0,
            "524ad7469a09527d61ccd6a5f2daa1972e253aa5e98a835fc1abeb58d7461ed0": 0,
            "30765f5822c80b55b4c354578671cf16335f64a3b7d6548a2f7fa84294cf2f33": 0,
            "0fb2395d521fa24ff95ee716c64ff8ee6aa40e95085ef938de50d716e221caac": 0,
            "7674acb44b4f53dc0c91732d67e1d680e51819ac2bcdac7400821a74ef207173": 0,
            "96a1a33494ebf7af4c4cf2146b124fc75f6f489567f8b2281adce25787f5bc59": 0,
            "b8f52a5af2c1e35e21255eecf4cd23471a4950660b0288a8c85a6aa4f5f8a289": 0,
            "56358083ffe71b6213ad7c5001b2bdbf5f836701e9a76a80fa19a87c15da2cd6": 0,
            "48ed4e2e8a666eb96e5b8898285a4a6915253f3d9704cf8512da519202ebb01e": 0,
            "27d26381421a582b657c98ce7eab0502564feea6d58e242de9449fe39d6dcc02": 0,
            "a527f0292b14d944bab5258a428427836024ac8fc01a9289e623b9817d918df0": 0,
            "e37c4246cdbe4b3ad59a9d8ebe92460ed116d0dc54688615b0ea866072072571": 0,
            "ffecf9fa22a8b2991a42274bd803fad88ad43d11c882a2f8a1a36464537f9110": 0,
            "b817180340f3b6200ae45ecd6789af20d2dbc6f66d16ac0ef0979762d8f10384": 0,
            "84dce7450bcd96e0b0608172652e070bf41c511f08554addf32e43df60fc98f7": 0,
            "567197f08dbc77ff3d965d64a9e175a382815ee1476095620eb7a355ba348dc2": 0,
            "1fc2958cef7ad8cccb87f20fcc239a815552f3994fabcb70f951270471b6aeb6": 0,
            "5ead3d4b797994d57c062e09aefba6a6ee88bcbf63d68453d04e1df242ce692f": 0,
            "1380202f048a6f7dbd006041c18ce6eded8d7c1ecd5522f330af6f2023b0a12c": 0,
            "81b5bb21dad42ef84eda6b72b7ff44c693cc8125a6c699f4c86852fdcccb6f64": 0,
            "11917ae9a497ff2cecd420e45bf770b29c2d0e4b0f3700a5c2a3f9e69cdaa8f0": 0,
            "92802201955b3133c9ffb458b99570492a96fe28c7134e0a4eed2724da3223e7": 0,
            "124e4695fc4a4511086b06c8734c9adbd3c7da014f5813540b1e2a81a71edeb3": 0,
        },
    }
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
            digest: frozenset(expected["permission_sets"][index])
            for digest, index in displaced[family].items()
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
        "src/mechanical_librarian/adapters/providers/hardcover_metadata.py",
        "src/mechanical_librarian/adapters/persistence/field_effect_validation.py",
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
        "src/mechanical_librarian/interfaces/web/settings_vocabulary.json",
        "docs/product/rich-metadata-qualification.md",
        "tests/fixtures/qualification-authority-predecessor/provenance.json",
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


@pytest.mark.parametrize(
    "name",
    [
        "new_authority.py",
        "rules.json",
        "rules.txt",
        "interfaces/web/react_assets/rules.json",
        "__pycache__/rules.json",
    ],
)
def test_new_product_source_requires_review(tmp_path, name):
    import shutil

    shutil.copytree(ROOT / "src", tmp_path / "src")
    resource = tmp_path / "src/mechanical_librarian" / name
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text("grant = True\n")
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


@pytest.mark.parametrize(
    "mutation",
    [
        "family",
        "media",
        "predecessor_digest",
        "predecessor_family",
        "origin_digest",
        "origin_family",
        "provenance",
    ],
)
def test_immutable_references_and_family_are_coherent(tmp_path, mutation):
    import shutil

    reference = tmp_path / records.REFERENCE
    reference.mkdir(parents=True)
    for name, _, _ in records.RECORDS.values():
        shutil.copyfile(ROOT / records.REFERENCE / name, reference / name)
    shutil.copyfile(
        ROOT / records.REFERENCE / "qualification-archive-index.json",
        reference / "qualification-archive-index.json",
    )
    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    path = reference / records.RECORDS["F19A"][0]
    document = json.loads(path.read_bytes())
    if mutation in {"family", "media"}:
        document["authority"][mutation] = "F46" if mutation == "family" else "audiobook"
    elif mutation == "predecessor_digest":
        document["predecessor"]["sha256"] = "0" * 64
    elif mutation == "predecessor_family":
        document["predecessor"] = json.loads(
            (reference / records.RECORDS["F46"][0]).read_bytes()
        )["predecessor"]
    elif mutation == "origin_digest":
        provenance["historical_measurement_origins"]["F19A"]["sha256"] = "0" * 64
    elif mutation == "origin_family":
        provenance["historical_measurement_origins"]["F19A"] = provenance[
            "historical_measurement_origins"
        ]["F46"]
    else:
        document["provenance"] = "unbound.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        records.verify_references(tmp_path, provenance)


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
def test_closed_record_keysets_refuse_before_verify_or_refresh(tmp_path, family, scope):
    import shutil

    reference = tmp_path / records.REFERENCE
    reference.mkdir(parents=True)
    for path in [
        ROOT / records.PROVENANCE,
        ROOT / records.REFERENCE / "qualification-archive-index.json",
        *[ROOT / records.REFERENCE / name for name, _, _ in records.RECORDS.values()],
    ]:
        shutil.copyfile(path, reference / path.name)
    path = reference / records.RECORDS[family][0]
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
        before = {p.name: p.read_bytes() for p in reference.iterdir()}
        for operation in (records.verify, records.refresh):
            with pytest.raises(ValueError, match=f"qualification {scope} keys"):
                operation(tmp_path)
            assert {p.name: p.read_bytes() for p in reference.iterdir()} == before


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
def test_refresh_changes_actual_imported_binding_not_decoy(tmp_path, family, decoy):
    import runpy
    import shutil

    provenance = json.loads((ROOT / records.PROVENANCE).read_bytes())
    for name in provenance["source_classification"]:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    name, module, constant = records.RECORDS[family]
    module_path = tmp_path / "src/mechanical_librarian/domain" / module
    source = module_path.read_text()
    old = records.binding_value(source, constant).value
    assert type(old) is str
    example = f'{constant} = "{old}"'
    source = (
        "# " + example + "\n" + source
        if decoy == "comment"
        else source.replace('"""', '"""' + example + "\n", 1)
    )
    module_path.write_text(source)
    record_path = tmp_path / records.REFERENCE / name
    record = json.loads(record_path.read_bytes())
    record["authority"]["semantic_revision"] += 1
    record_path.write_text(json.dumps(record))
    records.refresh(tmp_path)
    records.verify(tmp_path)
    expected = records.canonical_digest(
        json.loads(record_path.read_bytes())["authority"]
    )
    assert expected != old
    assert example in module_path.read_text()
    assert runpy.run_path(str(module_path))[constant] == expected
    # A matching comment must not conceal a stale actual value from verification.
    source = module_path.read_text().replace(f'"{expected}"', '"' + "0" * 64 + '"')
    module_path.write_text(f'# {constant} = "{expected}"\n' + source)
    with pytest.raises(ValueError, match="runtime authority binding drift"):
        records.verify(tmp_path)
