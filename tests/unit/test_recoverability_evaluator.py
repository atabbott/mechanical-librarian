import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "recoverability_evaluator", ROOT / "scripts" / "recoverability_evaluator.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RecoverabilityEvaluatorTests(unittest.TestCase):
    def load(self):
        return json.loads((ROOT / "tests/support/recoverability_mini_corpus.json").read_text())

    def test_categories_exclusions_and_arithmetic_are_exhaustive(self):
        result = MODULE.evaluate(self.load())
        supported = result["views"]["supported"]
        self.assertEqual(set(supported["categories"]), set(MODULE.CATEGORIES))
        self.assertEqual(sum(supported["categories"].values()), supported["readable_denominator"])
        self.assertEqual(result["excluded"], {state: 1 for state in MODULE.EXCLUDED})
        self.assertEqual(result["isbn"]["counts"], {"accepted": 1, "associated": 2, "unrelated": 1, "invalid": 1, "unresolved": 1})
        self.assertEqual(result["isbn"]["accepted_recall"], {"numerator": 1, "denominator": 6})
        self.assertEqual(result["inspection_failure_subtypes"], {"whole_item": 1, "extraction_stage": 0})

    def test_exploratory_positive_negative_and_ambiguous_rows_never_change_supported(self):
        document = self.load()
        before = MODULE.evaluate(document)["views"]["supported"]
        for field in ("edition", "rights"):
            for comparison in ("complete", "semantic", "partial"):
                changed = copy.deepcopy(document)
                for row in changed["rows"]:
                    if row.get("field") == field and row.get("state") == "value":
                        row["comparison"] = comparison
                self.assertEqual(MODULE.evaluate(changed)["views"]["supported"], before)

    def test_whole_item_failure_is_not_exploratory_recoverable_evidence(self):
        document = self.load()
        before = MODULE.evaluate(document)["exploratory_recoverable_recall"]["denominator"]
        row = next(row for row in document["rows"] if row.get("field") == "edition" and row.get("state") == "value")
        row.update({"inspection": "failure", "source": "unavailable", "extraction": "not_attempted", "projection": "not_projected", "comparison": "none"})
        self.assertEqual(MODULE.evaluate(document)["exploratory_recoverable_recall"]["denominator"], before - 1)

    def test_stage_tuples_reject_every_cross_stage_contradiction(self):
        document = self.load()
        valid = document["rows"][0]
        for key, value in {
            "inspection": "failure", "source": "present", "extraction": "found",
            "projection": "projected", "comparison": "complete",
        }.items():
            changed = copy.deepcopy(document)
            changed["rows"][0][key] = value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    MODULE.evaluate(changed)
        extraction_failure = copy.deepcopy(document)
        extraction_failure["rows"][2].update({"inspection": "success", "source": "present", "extraction": "failure", "projection": "not_projected", "comparison": "none"})
        self.assertEqual(MODULE._category(extraction_failure["rows"][2]), "inspection_failure")

    def test_identifier_evidence_is_exact_and_target_bound(self):
        document = self.load()
        for update in (
            {"basis": "unavailable"}, {"provider_scope": "none"}, {"same_work": False},
        ):
            changed = copy.deepcopy(document)
            changed["identifier_observations"][1].update(update)
            with self.assertRaises(ValueError):
                MODULE.evaluate(changed)
        changed = copy.deepcopy(document)
        changed["identifier_observations"][1]["target_slot"] = 0
        with self.assertRaises(ValueError):
            MODULE.evaluate(changed)
        changed = copy.deepcopy(document)
        changed["identifier_observations"][5]["slot"] = 0
        with self.assertRaises(ValueError):
            MODULE.evaluate(changed)

    def test_regression_is_derived_not_declared_and_profiles_are_distinct(self):
        document = self.load()
        result = MODULE.evaluate(document)
        self.assertEqual(result["regression"]["current"]["matched_rows"], 2)
        changed = copy.deepcopy(document)
        changed["negative_rows"][0]["has_candidate"] = "private value"
        with self.assertRaises(ValueError):
            MODULE.evaluate(changed)
        changed = copy.deepcopy(document)
        changed["profile"] = "gold-50-v1"
        with self.assertRaises(ValueError):
            MODULE.evaluate(changed)

    def test_private_values_paths_and_unicode_identifiers_are_rejected_or_omitted(self):
        document = self.load()
        document["rows"][0]["field"] = "Secret /private/library/9780306406157"
        with self.assertRaises(ValueError):
            MODULE.evaluate(document)
        document = self.load()
        document["identifier_observations"][0]["basis"] = "秘密9780306406157"
        with self.assertRaises(ValueError):
            MODULE.evaluate(document)
        rendered = json.dumps(MODULE.evaluate(self.load()))
        self.assertNotIn("Secret", rendered)
        self.assertNotIn("9780306406157", rendered)

    def test_loader_rejects_size_depth_utf8_duplicate_and_numeric_abuse(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(b"x" * (MODULE.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                MODULE.load(source)
            for payload in (
                b'{"schema":"a","schema":"b"}', b'{"number":1.1}', b'{"number":NaN}',
                b'{"number":1234567890123}', b'\xff',
            ):
                source.write_bytes(payload)
                with self.subTest(payload=payload):
                    with self.assertRaises(ValueError):
                        MODULE.load(source)
            source.write_text("[" * 22 + "]" * 22, encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.load(source)

    def test_cli_fails_without_echoing_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            target = Path(directory) / "output.json"
            source.write_text('{"secret":"/private/library/Secret"}', encoding="utf-8")
            result = subprocess.run(
                ["python3", str(ROOT / "scripts/recoverability_evaluator.py"), "--input", str(source), "--output", str(target)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "Recoverability evaluation failed safely.\n")
            self.assertNotIn("Secret", result.stderr)
            self.assertFalse(target.exists())
