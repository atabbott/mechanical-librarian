from __future__ import annotations

from contextlib import contextmanager
import copy
import os
from pathlib import Path
import shutil
import tempfile
from types import MappingProxyType
import unittest

from mechanical_librarian.domain.evidence import (
    Disposition,
    EvidenceReference,
    EvidenceRelationship,
    FieldProposal,
    FieldValue,
    ProposalKind,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    EvidenceId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    ProposalId,
    ReviewCaseId,
    SchemaVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import FieldName
from mechanical_librarian.domain.lifecycle import ReviewCaseState
from mechanical_librarian.domain.review import ReviewCase, ReviewGroup
from tests.support.dux_corpus import (
    CATEGORIES,
    CORPUS_ROOT,
    SOURCE_COMMIT,
    SOURCE_TAG,
    CorpusValidationError,
    canonical_json,
    load_registry,
    load_scenario,
)


def _mutable(value: object) -> object:
    if isinstance(value, MappingProxyType):
        return {key: _mutable(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_mutable(child) for child in value]
    return copy.deepcopy(value)


@contextmanager
def _corpus_copy():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "dux"
        shutil.copytree(CORPUS_ROOT, root)
        yield root


def _rewrite(root: Path, filename: str, mutate) -> None:
    path = root / filename
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_bytes(canonical_json(value))


def _target(value: dict[str, str]) -> TargetId:
    return TargetId(
        AdapterId(value["adapter"]),
        ConnectionId(value["connection"]),
        LibraryId(value["library"]),
        ExternalItemId(value["external_item"]),
    )


class DuxCorpusSchemaTests(unittest.TestCase):
    def test_registry_has_exact_complete_coverage_and_required_records(self) -> None:
        registry = load_registry()
        self.assertEqual(frozenset(registry.coverage), CATEGORIES)
        self.assertEqual(len(registry.scenarios), len(CATEGORIES))
        self.assertEqual(len({item.scenario_id for item in registry.scenarios}), len(CATEGORIES))
        for scenario in registry.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                provenance = scenario.record["provenance"]
                licensing = scenario.record["licensing"]
                self.assertEqual(provenance["source_commit"], SOURCE_COMMIT)
                self.assertEqual(provenance["source_tag"], SOURCE_TAG)
                self.assertEqual(provenance["behavioral_treatment"], "independently_reimplemented")
                self.assertEqual(provenance["fixture_origin"], "generated")
                self.assertEqual(licensing["spdx"], "AGPL-3.0-or-later")
                self.assertEqual(
                    licensing["authorship"],
                    "Mechanical Librarian contributors and repository history",
                )
                self.assertIsNone(licensing["required_notice"])
                self.assertTrue(scenario.record["dux_behavior"])
                self.assertTrue(scenario.record["preserved_invariant"])
                if scenario.status == "future":
                    self.assertTrue(scenario.record["not_yet_proving_product_behavior"])
                    self.assertEqual(scenario.record["future_owner"]["state"], "open")

    def test_records_and_registry_are_immutable(self) -> None:
        registry = load_registry()
        with self.assertRaises(TypeError):
            registry.coverage["conflicts"] = "dux-synthetic-01"  # type: ignore[index]
        with self.assertRaises(TypeError):
            registry.scenarios[0].record["status"] = "current"  # type: ignore[index]

    def test_committed_and_repeated_canonical_serialization_is_byte_stable(self) -> None:
        first = load_registry()
        second = load_registry()
        self.assertEqual(first.canonical_bytes, second.canonical_bytes)
        for left, right in zip(first.scenarios, second.scenarios, strict=True):
            self.assertEqual(left.canonical_bytes, right.canonical_bytes)
            self.assertEqual(left.canonical_bytes, canonical_json(_mutable(left.record)))
            self.assertTrue(left.canonical_bytes.endswith(b"\n"))
            self.assertFalse(left.canonical_bytes.endswith(b"\n\n"))

    def test_lookup_validates_complete_registry_before_returning_first_scenario(self) -> None:
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-11.json", lambda item: item.pop("provenance"))
            with self.assertRaisesRegex(CorpusValidationError, r"^DUX013_PROVENANCE scenario=dux-synthetic-11$"):
                load_scenario("dux-synthetic-01", root)

    def test_missing_provenance_and_invalid_versions_fail_closed(self) -> None:
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-01.json", lambda item: item.pop("provenance"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX013_PROVENANCE"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-01.json", lambda item: item.__setitem__("schema", "dux-synthetic-invalid"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX010_SCENARIO_SCHEMA"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "registry.json", lambda item: item.__setitem__("schema", "dux-synthetic-invalid"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX006_REGISTRY_SCHEMA"):
                load_registry(root)

    def test_invalid_future_owner_duplicate_identity_and_orphan_fail_closed(self) -> None:
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-01.json", lambda item: item["future_owner"].__setitem__("issue", 13))
            with self.assertRaisesRegex(CorpusValidationError, "DUX015_OWNERSHIP"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-03.json", lambda item: item.__setitem__("scenario_id", "dux-synthetic-01"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX017_DUPLICATE_ID"):
                load_registry(root)
        with _corpus_copy() as root:
            (root / "dux-synthetic-12.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(CorpusValidationError, "DUX009_ORPHAN_FILE"):
                load_registry(root)

    def test_prohibited_source_markers_and_mandated_values_outside_provenance_fail(self) -> None:
        for sentinel in (
            "generated-source-marker",
            f"generated-prefix-{SOURCE_COMMIT}-suffix",
            f"generated-prefix-{SOURCE_TAG}-suffix",
        ):
            with self.subTest(sentinel="generated-source-sentinel"), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-01.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, "DUX027_SOURCE_LITERAL")
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_rejects_unsafe_generated_sentinels_without_echoing(self) -> None:
        sentinels = (
            ("/home/generated/private-item", "DUX022_PROHIBITED_PATH"),
            ("~/generated-private-item", "DUX022_PROHIBITED_PATH"),
            ("generated_root=/var/generated/private-item", "DUX022_PROHIBITED_PATH"),
            ("generated_path:/home/generated/private-item", "DUX022_PROHIBITED_PATH"),
            ("`/home/generated/private-item`", "DUX022_PROHIBITED_PATH"),
            ("`~/generated-private-item`", "DUX022_PROHIBITED_PATH"),
            ("[/home/generated/private-item]", "DUX022_PROHIBITED_PATH"),
            ("(/home/generated/private-item)", "DUX022_PROHIBITED_PATH"),
            ("{/home/generated/private-item}", "DUX022_PROHIBITED_PATH"),
            ("**/home/generated/private-item**", "DUX022_PROHIBITED_PATH"),
            ("> /home/generated/private-item", "DUX022_PROHIBITED_PATH"),
            (r"C:\\generated\\private-item", "DUX022_PROHIBITED_PATH"),
            (r"\\\\generated-host\\private-item", "DUX022_PROHIBITED_PATH"),
            ("https://generated.invalid/item", "DUX023_DOMAIN_OR_URL"),
            ("ssh://generated.invalid/item", "DUX023_DOMAIN_OR_URL"),
            ("generated-person@example.invalid", "DUX023_DOMAIN_OR_URL"),
            ("192.0.2.44", "DUX024_NETWORK_ADDRESS"),
            ("Bearer generated-private", "DUX021_SECRET_SHAPE"),
            ("session_cookie", "DUX021_SECRET_SHAPE"),
            ("access%5Ftoken", "DUX021_SECRET_SHAPE"),
            ("access:token", "DUX021_SECRET_SHAPE"),
            ("refresh/token", "DUX021_SECRET_SHAPE"),
            ("client%3Asecret", "DUX021_SECRET_SHAPE"),
            ("apikey", "DUX021_SECRET_SHAPE"),
            ("privatekey", "DUX021_SECRET_SHAPE"),
            ("accesskey", "DUX021_SECRET_SHAPE"),
            ("accesskeyid", "DUX021_SECRET_SHAPE"),
            ("secretkey", "DUX021_SECRET_SHAPE"),
            ("accesstoken", "DUX021_SECRET_SHAPE"),
            ("refreshtoken", "DUX021_SECRET_SHAPE"),
            ("clientsecret", "DUX021_SECRET_SHAPE"),
            ("generated.production-report", "DUX025_PRODUCTION_STATE"),
            ("saved_browser_state", "DUX025_PRODUCTION_STATE"),
            ("saved:browser:state", "DUX025_PRODUCTION_STATE"),
            ("support/bundle", "DUX025_PRODUCTION_STATE"),
            ("support%2Fbundle", "DUX025_PRODUCTION_STATE"),
            ("123e4567-e89b-42d3-a456-426614174000", "DUX026_PRODUCTION_ID"),
            ("123E4567-E89B-42D3-A456-426614174000", "DUX026_PRODUCTION_ID"),
            ("generated-source-marker", "DUX027_SOURCE_LITERAL"),
            ("browser_local", "DUX027_SOURCE_LITERAL"),
            ("browser-local", "DUX027_SOURCE_LITERAL"),
            ("browser:local", "DUX027_SOURCE_LITERAL"),
            ("Copyright (c) generated", "DUX028_COPYRIGHT"),
            ("SPDX-FileCopyrightText generated", "DUX028_COPYRIGHT"),
            ("© generated", "DUX028_COPYRIGHT"),
            ("generated\u202evalue", "DUX020_UNSAFE_TEXT"),
            ("generated\x01value", "DUX020_UNSAFE_TEXT"),
            ("z" * 513, "DUX020_UNSAFE_TEXT"),
        )
        for sentinel, rule in sentinels:
            with self.subTest(rule=rule), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-01.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, rule)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_allows_safe_near_misses(self) -> None:
        for sentinel in (
            "Generated credentialing behavior.",
            "Generated apikeyed behavior.",
            "Generated privatekeys behavior.",
            "Generated clientsecretary behavior.",
            "Generated productionist behavior.",
            "Generated supportive bundle behavior.",
            "Generated copy right-sized behavior.",
            "Generated source marked behavior.",
            "Generated browser locality behavior.",
            "Generated scheme//without-address behavior.",
            "Generated prose/fragment remains synthetic.",
        ):
            with self.subTest(sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-01.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                self.assertEqual(len(load_registry(root).scenarios), len(CATEGORIES))

    def test_noncanonical_and_unowned_registry_files_fail_closed(self) -> None:
        with _corpus_copy() as root:
            path = root / "dux-synthetic-01.json"
            path.write_bytes(path.read_bytes().replace(b'"category"', b' "category"', 1))
            with self.assertRaisesRegex(CorpusValidationError, "DUX005_NONCANONICAL"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "registry.json", lambda item: item["scenario_files"].append("dux-synthetic-01.json"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX007_REGISTRY_FILES"):
                load_registry(root)
        with _corpus_copy() as root:
            nested = root / "generated-unowned"
            nested.mkdir()
            (nested / "generated.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(CorpusValidationError, "DUX009_ORPHAN_FILE"):
                load_registry(root)

    def test_each_category_rejects_a_type_correct_semantic_mismatch(self) -> None:
        invalid_changes = {
            "dux-synthetic-01.json": lambda item: item["contract"]["expected"].__setitem__("agreement_families", 2),
            "dux-synthetic-02.json": lambda item: item["contract"]["input"]["proposals"].append("dux-synthetic-proposal-x"),
            "dux-synthetic-03.json": lambda item: item["contract"]["input"].__setitem__("evidence_families", ["dux-synthetic-family-b", "dux-synthetic-family-b"]),
            "dux-synthetic-04.json": lambda item: item["contract"]["input"].__setitem__("candidate_identity", item["contract"]["input"]["current_identity"]),
            "dux-synthetic-05.json": lambda item: item["contract"]["input"].__setitem__("proposed", ["synthetic-title-delta"]),
            "dux-synthetic-06.json": lambda item: item["contract"]["input"].__setitem__("current_state", "empty"),
            "dux-synthetic-07.json": lambda item: item["contract"]["input"].__setitem__("media_type", "audiobook"),
            "dux-synthetic-08.json": lambda item: item["contract"]["input"].__setitem__("candidate", item["contract"]["input"]["current"]),
            "dux-synthetic-09.json": lambda item: item["contract"]["expected"].__setitem__("sequence", ["8.625"]),
            "dux-synthetic-10.json": lambda item: item["contract"]["expected"].__setitem__("siblings", list(reversed(item["contract"]["expected"]["siblings"]))),
            "dux-synthetic-11.json": lambda item: item["contract"]["input"].__setitem__("destructive", False),
        }
        for filename, mutate in invalid_changes.items():
            with self.subTest(filename=filename), _corpus_copy() as root:
                _rewrite(root, filename, mutate)
                with self.assertRaisesRegex(CorpusValidationError, "DUX016_CONTRACT"):
                    load_registry(root)

    def test_current_internal_identifiers_use_exact_domain_bounds(self) -> None:
        overlength = "dux-synthetic-" + ("x" * 52)
        invalid_changes = {
            "dux-synthetic-02.json": lambda item: item["contract"]["input"]["target"].__setitem__("adapter", overlength),
            "dux-synthetic-05.json": lambda item: item["contract"]["input"].__setitem__("proposal_id", overlength),
            "dux-synthetic-10.json": lambda item: item["contract"]["input"]["siblings"].__setitem__(0, overlength),
        }
        self.assertGreater(len(overlength), 64)
        for filename, mutate in invalid_changes.items():
            with self.subTest(filename=filename), _corpus_copy() as root:
                _rewrite(root, filename, mutate)
                with self.assertRaisesRegex(CorpusValidationError, "DUX016_CONTRACT"):
                    load_registry(root)

    def test_loader_is_independent_of_cwd_home_locale_and_timezone(self) -> None:
        original = Path.cwd()
        preserved = {key: os.environ.get(key) for key in ("HOME", "LANG", "LC_ALL", "TZ")}
        try:
            with tempfile.TemporaryDirectory() as directory:
                os.chdir(directory)
                os.environ.update(
                    {"HOME": directory, "LANG": "C", "LC_ALL": "C", "TZ": "Pacific/Kiritimati"}
                )
                self.assertEqual(len(load_registry().scenarios), len(CATEGORIES))
        finally:
            os.chdir(original)
            for key, value in preserved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class DuxCurrentContractTests(unittest.TestCase):
    def test_evidence_only_input_hits_exact_review_case_empty_rejection(self) -> None:
        data = _mutable(load_scenario("dux-synthetic-02").contract)
        inputs = data["input"]
        target = _target(inputs["target"])
        observed_outcome = None
        with self.assertRaises(ValueError) as raised:
            ReviewCase(
                ReviewCaseId(inputs["case_id"]),
                target,
                SnapshotId(inputs["snapshot_id"]),
                Fingerprint.from_canonical_value({"scenario": "dux-synthetic-02"}, schema=SchemaVersion("snapshot-v1")),
                "dux-synthetic-set-v1",
                tuple(inputs["proposals"]),
                ReviewCaseState.OPEN,
                Disposition.NEEDS_REVIEW,
            )
        observed_outcome = "reject_empty_case"
        self.assertEqual(type(raised.exception).__name__, data["expected"]["exception"])
        self.assertEqual(observed_outcome, data["expected"]["outcome"])

    def test_no_op_hits_exact_field_proposal_shape(self) -> None:
        data = _mutable(load_scenario("dux-synthetic-05").contract)
        inputs = data["input"]
        target = _target(inputs["target"])
        reference = EvidenceReference(
            EvidenceId(inputs["evidence_id"]), target, target, EvidenceRelationship.SAME_TARGET
        )
        proposal = FieldProposal(
            ProposalId(inputs["proposal_id"]),
            target,
            SnapshotId(inputs["snapshot_id"]),
            FieldName(inputs["field"]),
            FieldValue(tuple(inputs["current"])),
            FieldValue(tuple(inputs["proposed"])),
            ProposalKind(data["expected"]["kind"]),
            (reference,),
        )
        self.assertIs(proposal.kind, ProposalKind.NO_CHANGE)
        self.assertEqual(proposal.current_value, proposal.proposed_value)
        self.assertEqual(proposal.kind.value, data["expected"]["kind"])
        self.assertEqual(
            proposal.current_value == proposal.proposed_value,
            data["expected"]["current_equals_proposed"],
        )

    def test_independent_siblings_hit_exact_display_only_review_group(self) -> None:
        data = _mutable(load_scenario("dux-synthetic-10").contract)
        inputs = data["input"]
        group = ReviewGroup(
            inputs["label"],
            inputs["link_method"],
            inputs["confidence"],
            tuple(ReviewCaseId(item) for item in inputs["siblings"]),
        )
        self.assertEqual(
            tuple(str(item) for item in group.sibling_case_ids),
            tuple(data["expected"]["siblings"]),
        )
        observed_decision_state = "decision" in vars(group)
        self.assertEqual(observed_decision_state, data["expected"]["decision_state_present"])

    def test_destructive_case_hits_exact_ordinary_review_exclusion(self) -> None:
        data = _mutable(load_scenario("dux-synthetic-11").contract)
        inputs = data["input"]
        target = _target(inputs["target"])
        observed_outcome = None
        with self.assertRaises(ValueError) as raised:
            ReviewCase(
                ReviewCaseId(inputs["case_id"]),
                target,
                SnapshotId(inputs["snapshot_id"]),
                Fingerprint.from_canonical_value({"scenario": "dux-synthetic-11"}, schema=SchemaVersion("snapshot-v1")),
                "dux-synthetic-set-v1",
                (ProposalId(inputs["proposal_id"]),),
                ReviewCaseState.OPEN,
                Disposition.NEEDS_REVIEW,
                destructive=inputs["destructive"],
            )
        observed_outcome = "exclude_from_ordinary_review"
        self.assertEqual(type(raised.exception).__name__, data["expected"]["exception"])
        self.assertEqual(observed_outcome, data["expected"]["outcome"])


if __name__ == "__main__":
    unittest.main()
