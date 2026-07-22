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
    def test_loaded_corpus_is_exactly_the_current_delivered_boundary(self) -> None:
        registry = load_registry()

        self.assertEqual(
            dict(registry.coverage),
            {
                "destructive_cases": "dux-synthetic-11",
                "evidence_only_input": "dux-synthetic-02",
                "independent_siblings": "dux-synthetic-10",
                "no_ops": "dux-synthetic-05",
            },
        )
        self.assertEqual(
            tuple(scenario.scenario_id for scenario in registry.scenarios),
            (
                "dux-synthetic-02",
                "dux-synthetic-05",
                "dux-synthetic-10",
                "dux-synthetic-11",
            ),
        )
        for scenario in registry.scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertNotIn("future_owner", scenario.record)
                self.assertNotIn("not_yet_proving_product_behavior", scenario.record)

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

    def test_records_and_registry_are_immutable(self) -> None:
        registry = load_registry()
        with self.assertRaises(TypeError):
            registry.coverage["no_ops"] = "dux-synthetic-02"  # type: ignore[index]
        with self.assertRaises(TypeError):
            registry.scenarios[0].record["category"] = "no_ops"  # type: ignore[index]

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
                load_scenario("dux-synthetic-02", root)

    def test_missing_provenance_and_invalid_versions_fail_closed(self) -> None:
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-02.json", lambda item: item.pop("provenance"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX013_PROVENANCE"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-02.json", lambda item: item.__setitem__("schema", "dux-synthetic-invalid"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX010_SCENARIO_SCHEMA"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "registry.json", lambda item: item.__setitem__("schema", "dux-synthetic-invalid"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX006_REGISTRY_SCHEMA"):
                load_registry(root)

    def test_incompatible_v1_corpus_is_rejected_by_the_current_schema(self) -> None:
        registry = load_registry()
        self.assertEqual(registry.canonical_bytes, canonical_json({
            "coverage": {
                "destructive_cases": "dux-synthetic-11",
                "evidence_only_input": "dux-synthetic-02",
                "independent_siblings": "dux-synthetic-10",
                "no_ops": "dux-synthetic-05",
            },
            "scenario_files": [
                "dux-synthetic-02.json",
                "dux-synthetic-05.json",
                "dux-synthetic-10.json",
                "dux-synthetic-11.json",
            ],
            "schema": "dux-corpus-v2",
        }))
        with _corpus_copy() as root:
            _rewrite(root, "registry.json", lambda item: item.__setitem__("schema", "dux-corpus-v1"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX006_REGISTRY_SCHEMA"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-02.json", lambda item: item.__setitem__("schema", "dux-corpus-v1"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX010_SCENARIO_SCHEMA"):
                load_registry(root)

    def test_duplicate_identity_and_orphan_fail_closed(self) -> None:
        with _corpus_copy() as root:
            _rewrite(root, "dux-synthetic-05.json", lambda item: item.__setitem__("scenario_id", "dux-synthetic-02"))
            with self.assertRaisesRegex(CorpusValidationError, "DUX017_DUPLICATE_ID"):
                load_registry(root)
        with _corpus_copy() as root:
            (root / "dux-synthetic-12.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(CorpusValidationError, "DUX009_ORPHAN_FILE"):
                load_registry(root)

    def test_membership_fields_reject_json_containers_with_stable_diagnostics(self) -> None:
        cases = (
            ("category", ["sensitive-category-list"], "DUX012_CATEGORY"),
            ("category", {"sensitive-category-object": True}, "DUX012_CATEGORY"),
        )
        for field, rejected, rule in cases:
            with self.subTest(field=field, rejected_type=type(rejected).__name__), _corpus_copy() as root:
                if field == "category":
                    mutate = lambda item, value=rejected: item.__setitem__(field, value)
                _rewrite(root, "dux-synthetic-02.json", mutate)
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, rule)
                for sensitive_value in rejected:
                    self.assertNotIn(str(sensitive_value), str(raised.exception))

    def test_prohibited_source_markers_and_mandated_values_outside_provenance_fail(self) -> None:
        for sentinel in (
            "generated-source-marker",
            f"generated-prefix-{SOURCE_COMMIT}-suffix",
            f"generated-prefix-{SOURCE_TAG}-suffix",
        ):
            with self.subTest(sentinel="generated-source-sentinel"), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
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
            ("access%09token", "DUX021_SECRET_SHAPE"),
            ("access%0Atoken", "DUX021_SECRET_SHAPE"),
            ("access%0Btoken", "DUX021_SECRET_SHAPE"),
            ("access%0Ctoken", "DUX021_SECRET_SHAPE"),
            ("access%0Dtoken", "DUX021_SECRET_SHAPE"),
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
            ("saved%09browser%09state", "DUX025_PRODUCTION_STATE"),
            ("saved%0Abrowser%0Astate", "DUX025_PRODUCTION_STATE"),
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
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, rule)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_rejects_finite_credential_label_matrix(self) -> None:
        nouns = (
            "authorization",
            "authorizations",
            "bearer",
            "bearers",
            "cookie",
            "cookies",
            "credential",
            "credentials",
            "header",
            "headers",
            "key",
            "keys",
            "password",
            "passwords",
            "secret",
            "secrets",
            "token",
            "--token--",
        )
        compounds = (
            ("api", "key"),
            ("private", "key"),
            ("access", "key"),
            ("secret", "key"),
            ("access", "token"),
            ("refresh", "token"),
            ("session", "token"),
            ("id", "token"),
            ("auth", "token"),
            ("session", "cookie"),
            ("client", "secret"),
        )
        delimiters = ("", ".", "_", "-", " ", ":", "/", "%2E")
        rejected_values = (
            *nouns,
            *(
                f"{prefix}{delimiter}{noun}{suffix}"
                for prefix, noun in compounds
                for suffix in ("", "s")
                for delimiter in delimiters
            ),
            "accesskeyid",
            "accesskeyids",
            "access key id",
            "access key ids",
            "access%2Ekey%2Eids",
        )
        for sentinel in rejected_values:
            with self.subTest(location="value", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                expected_rule = (
                    "DUX022_PROHIBITED_PATH"
                    if sentinel in {"private/key", "private/keys"}
                    else "DUX021_SECRET_SHAPE"
                )
                self.assertEqual(raised.exception.rule, expected_rule)
                self.assertNotIn(sentinel, str(raised.exception))

        for sentinel in ("credentials", "api_keys", "session%2Ecookies", "authtokens"):
            with self.subTest(location="nested-key", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item["contract"]["input"].__setitem__(
                        value, "synthetic-value"
                    ),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, "DUX021_SECRET_SHAPE")
                self.assertNotIn(sentinel, str(raised.exception))

        for sentinel in (
            "token bucket behavior",
            "purpose tokens remain synthetic",
            "tokenized value remains synthetic",
            "tokenization remains synthetic",
            "credentials policy remains synthetic",
            "keys remain synthetic labels in this sentence",
        ):
            with self.subTest(location="safe-value", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                self.assertEqual(len(load_registry(root).scenarios), len(CATEGORIES))

    def test_recursive_audit_rejects_finite_state_marker_matrix(self) -> None:
        nouns = ("database", "report", "runtime")
        compounds = (
            ("production", "state"),
            ("browser", "state"),
            ("saved", "browser", "state"),
            ("support", "bundle"),
        )
        delimiters = ("", ".", "_", "-", " ", ":", "/")
        encoded_delimiters = ("%2E", "%5F", "%2D", "%20", "%3A", "%2F", "%09")
        rejected = (
            "production",
            *(noun + suffix for noun in nouns for suffix in ("", "s")),
            *(
                delimiter.join((*prefix, noun + suffix))
                for *prefix, noun in compounds
                for suffix in ("", "s")
                for delimiter in (*delimiters, *encoded_delimiters)
            ),
        )
        embedded = (
            "prefix_browserstate_suffix",
            "fixture/supportbundle/value",
            "fixture%5Fproductionstate%5Fvalue",
            "prefix_savedbrowserstates_suffix",
            "prefix_browser_states_suffix",
            "prefix_reports_suffix",
            "prefix__reports__suffix",
            "contains `saved_browser_states`, marker",
            "contains (prefix_reports_suffix) marker",
            'contains "browser-state" marker',
            "contains 'saved%5Fbrowser%5Fstates' marker",
            "contains BROWSERSTATE marker",
            "contains savedbrowserstates marker",
            "contains productionstates marker",
            "contains supportbundles marker",
            "contains α_savedbrowserstates_β marker",
            "contains α_saved_browser_states_ś marker",
            "contains α_saved_browser_states_s\u0301 marker",
            "contains α_reports_ś marker",
            "contains α_reports_s\u0301 marker",
            "contains α%5Fsaved_browser_states%5Fś marker",
            "contains α%5Fsaved_browser_states%5Fs\u0301 marker",
            "contains ↚savedbrowserstates marker",
            "contains ←\u0338savedbrowserstates marker",
            "contains ∄saved_browser_states marker",
            "contains ∃\u0338saved_browser_states marker",
            "contains !ाsavedbrowserstates marker",
            "contains !\u20ddsaved_browser_states marker",
            "contains αsaved_browser_state marker",
            "contains αsaved_browser_states marker",
            "contains αsaved-browser-state-s\u0301 marker",
            "contains αsaved%5Fbrowser%5Fstates%5Fs\u0301 marker",
        )
        edge_delimited = tuple(
            (
                f"contains {delimiter}reports{delimiter} marker",
                "DUX022_PROHIBITED_PATH"
                if delimiter == "/"
                else "DUX025_PRODUCTION_STATE",
            )
            for delimiter in (
                "_",
                "-",
                ".",
                "/",
                ":",
                "%09",
                "%0A",
                "%0B",
                "%0C",
                "%0D",
                "%20",
                "%2D",
                "%2E",
                "%3A",
                "%5F",
                "%2F",
            )
        )
        legacy_overlaps = (
            ("/home/generated/reports", "DUX022_PROHIBITED_PATH"),
            ("secret.report", "DUX021_SECRET_SHAPE"),
            ("generated.production-report", "DUX025_PRODUCTION_STATE"),
            ("report:copyright", "DUX025_PRODUCTION_STATE"),
            ("report:123e4567-e89b-42d3-a456-426614174000", "DUX025_PRODUCTION_STATE"),
            ("report:192.0.2.44", "DUX025_PRODUCTION_STATE"),
            ("report.example.invalid", "DUX025_PRODUCTION_STATE"),
            ("report:prototype", "DUX025_PRODUCTION_STATE"),
            ("custom:report", "DUX025_PRODUCTION_STATE"),
            ("custom:saved_browser_state", "DUX025_PRODUCTION_STATE"),
        )
        specific_overlaps = (
            ("/home/generated/browser_states", "DUX022_PROHIBITED_PATH"),
            ("secret.browser_states", "DUX021_SECRET_SHAPE"),
            ("databases:copyright", "DUX028_COPYRIGHT"),
            ("reports.example.invalid", "DUX023_DOMAIN_OR_URL"),
            ("runtimes:192.0.2.44", "DUX024_NETWORK_ADDRESS"),
            ("reports:123e4567-e89b-42d3-a456-426614174000", "DUX026_PRODUCTION_ID"),
            ("prototype.browser_state", "DUX027_SOURCE_LITERAL"),
            ("custom:browser_states", "DUX023_DOMAIN_OR_URL"),
            ("prefix.savedbrowserstates.suffix", "DUX023_DOMAIN_OR_URL"),
        )
        embedded_specific_overlaps = (
            ("See /home/generated/prefix_browser_states_suffix now", "DUX022_PROHIBITED_PATH"),
            ("access_token and prefix_reports_suffix", "DUX021_SECRET_SHAPE"),
            ("Copyright prefix_databases_suffix", "DUX028_COPYRIGHT"),
            ("See reports.example.invalid and prefix_browser_states_suffix", "DUX023_DOMAIN_OR_URL"),
            ("Address 192.0.2.44 and prefix_runtimes_suffix", "DUX024_NETWORK_ADDRESS"),
            (
                "ID 123e4567-e89b-42d3-a456-426614174000 and prefix_reports_suffix",
                "DUX026_PRODUCTION_ID",
            ),
            ("prototype prefix_browser_states_suffix", "DUX027_SOURCE_LITERAL"),
            ("custom:prefix_browser_states_suffix", "DUX023_DOMAIN_OR_URL"),
            ("See /home/generated/%5Freports%5F now", "DUX022_PROHIBITED_PATH"),
            ("access_token and %5Freports%5F", "DUX021_SECRET_SHAPE"),
        )
        unicode_morphological_near_misses = (
            "contains ésavedbrowserstates marker",
            "contains e\u0301savedbrowserstates marker",
            "contains savedbrowserstateś marker",
            "contains savedbrowserstates\u0301 marker",
            "contains ésupport_bundles marker",
            "contains e\u0301support_bundles marker",
            "contains saved_browser_stateś marker",
            "contains saved_browser_states\u0301 marker",
            "contains काsavedbrowserstates marker",
            "contains savedbrowserstatesा marker",
            "contains A\u20ddsupport_bundles marker",
            "contains saved_browser_states\u20dd marker",
        )
        benign = (
            "databases remain synthetic prose",
            "reports remain synthetic prose",
            "reporting remains synthetic",
            "runtimes remain synthetic prose",
            "runtimeish-neutrality remains synthetic",
            "productionized states remain synthetic prose",
            "browser state remains synthetic prose",
            "browser states remain synthetic prose",
            "saved browser states remain synthetic prose",
            "support bundles remain synthetic prose",
            "supportive bundles remain synthetic",
            "databaseish label remains synthetic",
            "contains reports marker",
            "contains databases marker",
            "contains saved browser states marker",
            "contains support bundles marker",
            "contains supportive_bundles marker",
            "contains browser_stateful marker",
            "contains databaseish_label marker",
            "contains αsavedbrowserstatesβ marker",
            "contains αsavedbrowserstates marker",
            "contains savedbrowserstatesß marker",
            "contains １savedbrowserstates２ marker",
            "contains αsaved_browser_statesβ marker",
            "contains saved_browser_statesß marker",
            "contains xsavedbrowserstatesy marker",
            "contains xsavedbrowserstates marker",
            "contains savedbrowserstatesy marker",
            *unicode_morphological_near_misses,
        )
        benign_keys = (
            "database migration remains synthetic",
            "report remains synthetic prose",
            "runtime neutrality remains synthetic",
            "saved browser state remains synthetic prose",
            "support bundle remains synthetic prose",
            "contains reports marker",
            "contains databases marker",
            "contains saved browser states marker",
            "contains support bundles marker",
            "contains supportive_bundles marker",
            "contains browser_stateful marker",
            "contains databaseish_label marker",
            "contains αsavedbrowserstatesβ marker",
            "contains αsavedbrowserstates marker",
            "contains savedbrowserstatesß marker",
            "contains １savedbrowserstates２ marker",
            "contains αsaved_browser_statesβ marker",
            "contains saved_browser_statesß marker",
            "contains xsavedbrowserstatesy marker",
            "contains xsavedbrowserstates marker",
            "contains savedbrowserstatesy marker",
            *unicode_morphological_near_misses,
        )
        cases = (
            *(("value", marker, "DUX025_PRODUCTION_STATE") for marker in rejected),
            *(("nested-key", marker, "DUX025_PRODUCTION_STATE") for marker in rejected),
            *(("value", marker, "DUX025_PRODUCTION_STATE") for marker in embedded),
            *(("nested-key", marker, "DUX025_PRODUCTION_STATE") for marker in embedded),
            *(("value", marker, rule) for marker, rule in edge_delimited),
            *(("nested-key", marker, rule) for marker, rule in edge_delimited),
            *(("value", marker, rule) for marker, rule in legacy_overlaps),
            *(("value", marker, rule) for marker, rule in specific_overlaps),
            *(("nested-key", marker, rule) for marker, rule in specific_overlaps),
            *(("value", marker, rule) for marker, rule in embedded_specific_overlaps),
            *(("nested-key", marker, rule) for marker, rule in embedded_specific_overlaps),
            *(("safe-value", marker, None) for marker in benign),
            *(("benign-nested-key", marker, "DUX016_CONTRACT") for marker in benign_keys),
        )
        for location, sentinel, expected_rule in cases:
            with self.subTest(location=location, sentinel=sentinel), _corpus_copy() as root:
                if location in {"nested-key", "benign-nested-key"}:
                    _rewrite(
                        root,
                        "dux-synthetic-02.json",
                        lambda item, value=sentinel: item["contract"]["input"].__setitem__(
                            value, "synthetic-value"
                        ),
                    )
                else:
                    _rewrite(
                        root,
                        "dux-synthetic-02.json",
                        lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                    )
                if expected_rule is None:
                    self.assertEqual(len(load_registry(root).scenarios), len(CATEGORIES))
                    continue
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, expected_rule)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_finds_structured_state_tokens_at_recursive_depths(self) -> None:
        for sentinel in (
            "contains (`prefix_saved_browser_states_suffix`), marker",
            "contains `%5Freports%5F`, marker",
        ):
            mutations = (
                ("root-value", lambda item: item.__setitem__("dux_behavior", sentinel)),
                (
                    "list-value",
                    lambda item: item["contract"]["input"]["proposals"].append(sentinel),
                ),
                (
                    "deep-mapping-list-value",
                    lambda item: item["contract"]["input"].__setitem__(
                        "synthetic_container", [{"synthetic_child": [sentinel]}]
                    ),
                ),
                (
                    "deep-nested-key",
                    lambda item: item["contract"]["input"].__setitem__(
                        "synthetic_container",
                        [{"synthetic_child": {sentinel: "synthetic-value"}}],
                    ),
                ),
            )
            for location, mutate in mutations:
                with self.subTest(location=location, sentinel=sentinel), _corpus_copy() as root:
                    _rewrite(root, "dux-synthetic-02.json", mutate)
                    with self.assertRaises(CorpusValidationError) as raised:
                        load_registry(root)
                    self.assertEqual(raised.exception.rule, "DUX025_PRODUCTION_STATE")
                    self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_rejects_browser_storage_label_matrix(self) -> None:
        delimiters = ("", ".", "_", "-", " ", ":", "/", "%2E")
        rejected_values = tuple(
            f"{scope}{delimiter}storage"
            for scope in ("local", "session")
            for delimiter in delimiters
        )
        for sentinel in rejected_values:
            with self.subTest(location="value", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                expected_rule = (
                    "DUX023_DOMAIN_OR_URL"
                    if sentinel in {"local.storage", "session.storage"}
                    else "DUX027_SOURCE_LITERAL"
                )
                self.assertEqual(raised.exception.rule, expected_rule)
                self.assertNotIn(sentinel, str(raised.exception))

        for sentinel in ("local_storage", "session%2Estorage"):
            with self.subTest(location="nested-key", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item["contract"]["input"].__setitem__(
                        value, "synthetic-value"
                    ),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, "DUX027_SOURCE_LITERAL")
                self.assertNotIn(sentinel, str(raised.exception))

        for sentinel in (
            "local generated storage remains synthetic",
            "storage local remains synthetic",
            "locality storage remains synthetic",
            "sessionized storage remains synthetic",
        ):
            with self.subTest(location="safe-value", sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                self.assertEqual(len(load_registry(root).scenarios), len(CATEGORIES))

    def test_recursive_audit_rejects_generic_uri_schemes_without_echoing(self) -> None:
        sentinels = (
            "urn:generated:item",
            "data:text/plain,generated",
            "javascript:generated",
            "mailto:generated-local",
            "file:relative/generated-item",
            "about:generated",
            "blob:generated-item",
            "tel:generated-number",
            "custom:generated-value",
            "URN:generated:item",
            "git+ssh:generated-item",
            "custom+v1:generated-item",
            "custom:",
            "`urn:generated:item`",
            "(data:text/plain,generated)",
            "generated_value=javascript:generated",
            "Generated—mailto:generated-local",
        )
        for sentinel in sentinels:
            with self.subTest(sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, "DUX023_DOMAIN_OR_URL")
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_preserves_specific_uri_overlap_diagnostics(self) -> None:
        sentinels = (
            ("file:/home/generated/private-item", "DUX022_PROHIBITED_PATH"),
            ("urn:access_token", "DUX021_SECRET_SHAPE"),
            ("custom:saved_browser_state", "DUX025_PRODUCTION_STATE"),
            ("about:browser_local", "DUX027_SOURCE_LITERAL"),
            ("2001:db8::8a2e:370:7334", "DUX024_NETWORK_ADDRESS"),
            (
                "urn:123e4567-e89b-42d3-a456-426614174000",
                "DUX026_PRODUCTION_ID",
            ),
        )
        for sentinel, rule in sentinels:
            with self.subTest(rule=rule), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                with self.assertRaises(CorpusValidationError) as raised:
                    load_registry(root)
                self.assertEqual(raised.exception.rule, rule)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_recursive_audit_allows_safe_near_misses(self) -> None:
        for sentinel in (
            "Generated credentialing behavior.",
            "Generated%09credentialing behavior.",
            "Generated apikeyed behavior.",
            "Generated privatekeyed behavior.",
            "Generated clientsecretary behavior.",
            "Generated productionist behavior.",
            "Generated supportive bundle behavior.",
            "Generated copy right-sized behavior.",
            "Generated source marked behavior.",
            "Generated browser locality behavior.",
            "Generated note: synthetic behavior.",
            "Generated label: value remains synthetic.",
            "Generated time 12:30 remains synthetic.",
            "Generated ratio 2:1 remains synthetic.",
            "Generated 1custom:value remains synthetic.",
            "Generated +custom:value remains synthetic.",
            "Generated .custom:value remains synthetic.",
            "Generated -custom:value remains synthetic.",
            "Generated custom :value remains synthetic.",
            "Generated custom: value remains synthetic.",
            "Generated scheme//without-address behavior.",
            "Generated prose/fragment remains synthetic.",
        ):
            with self.subTest(sentinel=sentinel), _corpus_copy() as root:
                _rewrite(
                    root,
                    "dux-synthetic-02.json",
                    lambda item, value=sentinel: item.__setitem__("dux_behavior", value),
                )
                self.assertEqual(len(load_registry(root).scenarios), len(CATEGORIES))

    def test_noncanonical_and_unowned_registry_files_fail_closed(self) -> None:
        with _corpus_copy() as root:
            path = root / "dux-synthetic-02.json"
            path.write_bytes(path.read_bytes().replace(b'"category"', b' "category"', 1))
            with self.assertRaisesRegex(CorpusValidationError, "DUX005_NONCANONICAL"):
                load_registry(root)
        with _corpus_copy() as root:
            _rewrite(root, "registry.json", lambda item: item["scenario_files"].append("dux-synthetic-02.json"))
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
            "dux-synthetic-02.json": lambda item: item["contract"]["input"]["proposals"].append("dux-synthetic-proposal-x"),
            "dux-synthetic-05.json": lambda item: item["contract"]["input"].__setitem__("proposed", ["synthetic-title-delta"]),
            "dux-synthetic-10.json": lambda item: item["contract"]["expected"].__setitem__("siblings", list(reversed(item["contract"]["expected"]["siblings"]))),
            "dux-synthetic-11.json": lambda item: item["contract"]["input"].__setitem__("destructive", False),
        }
        for filename, mutate in invalid_changes.items():
            with self.subTest(filename=filename), _corpus_copy() as root:
                _rewrite(root, filename, mutate)
                with self.assertRaisesRegex(CorpusValidationError, "DUX016_CONTRACT"):
                    load_registry(root)

    def test_evidence_only_contract_requires_a_typed_evidence_reference_id(self) -> None:
        with _corpus_copy() as root:
            _rewrite(
                root,
                "dux-synthetic-02.json",
                lambda item: item["contract"]["input"].pop("evidence_id"),
            )
            with self.assertRaisesRegex(CorpusValidationError, "DUX016_CONTRACT"):
                load_registry(root)

        overlength = "dux-synthetic-" + ("e" * 52)
        self.assertGreater(len(overlength), 64)
        with _corpus_copy() as root:
            _rewrite(
                root,
                "dux-synthetic-02.json",
                lambda item: item["contract"]["input"].__setitem__(
                    "evidence_id", overlength
                ),
            )
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
        reference = EvidenceReference(
            EvidenceId(inputs["evidence_id"]),
            target,
            target,
            EvidenceRelationship.SAME_TARGET,
        )
        evidence = (reference,)
        self.assertEqual(reference.evidence, EvidenceId(inputs["evidence_id"]))
        self.assertEqual(inputs["proposals"], [])
        self.assertEqual(len(evidence), 1)
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
                evidence=evidence,
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
