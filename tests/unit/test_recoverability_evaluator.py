import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("recoverability_evaluator", ROOT / "scripts" / "recoverability_evaluator.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RecoverabilityEvaluatorTests(unittest.TestCase):
    def load(self):
        return json.loads((ROOT / "tests/support/recoverability_mini_corpus.json").read_text())

    def test_categories_are_exhaustive_and_arithmetic_is_exact(self):
        result = MODULE.evaluate(self.load())
        view = result["views"]["supported"]
        self.assertEqual(set(view["categories"]), set(MODULE.CATEGORIES))
        self.assertEqual(sum(view["categories"].values()), view["denominator"])
        self.assertEqual(result["isbn"]["counts"], {item: 1 for item in MODULE.ISBN_RELATIONS})
        self.assertEqual(result["views"]["exploratory"]["evaluated_fields"], ["edition", "rights"])

    def test_precedence_and_private_values_never_enter_output(self):
        document = self.load()
        document["rows"][0]["field"] = "Secret Title 9780306406157"
        with self.assertRaises(ValueError):
            MODULE.evaluate(document)
        document = self.load()
        result = json.dumps(MODULE.evaluate(document))
        self.assertNotIn("Secret", result)
        self.assertNotIn("9780306406157", result)
        self.assertEqual(MODULE._category(document["rows"][1]), "inspection_failure")

    def test_rejects_unclosed_or_unsupported_contract_rows(self):
        document = self.load()
        document["rows"][7]["field"] = "secret_field"
        with self.assertRaises(ValueError): MODULE.evaluate(document)

    def test_cli_rejects_private_path_and_malformed_json_without_echoing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            target = Path(directory) / "output.json"
            source.write_text('{"secret":"/private/library/Secret"}', encoding="utf-8")
            result = subprocess.run(["python3", str(ROOT / "scripts/recoverability_evaluator.py"), "--input", str(source), "--output", str(target)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "Recoverability evaluation failed safely.\n")
            self.assertNotIn("Secret", result.stderr)
            self.assertFalse(target.exists())

    def test_replay_is_deterministic_and_profile_spoof_fails(self):
        document = self.load()
        self.assertEqual(MODULE.evaluate(document), MODULE.evaluate(document))
        document["profile"] = "gold-50-v1"
        with self.assertRaises(ValueError): MODULE.evaluate(document)

    def test_rejects_contradictory_cross_stage_failure(self):
        document = self.load()
        document["rows"][1]["projection"] = "projected"
        with self.assertRaises(ValueError):
            MODULE.evaluate(document)

    def test_loader_rejects_oversize_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            source.write_bytes(b"x" * (MODULE.MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                MODULE.load(source)
            source.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.load(source)

    def test_exploratory_changes_do_not_change_supported_matrix(self):
        document = self.load()
        before = MODULE.evaluate(document)["views"]["supported"]
        document["rows"][7]["comparison"] = "semantic"
        after = MODULE.evaluate(document)["views"]["supported"]
        self.assertEqual(before, after)
