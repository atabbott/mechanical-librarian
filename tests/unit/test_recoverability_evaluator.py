import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
        document = self.load()
        document["identifier_observations"][1]["provider_scope"] = "unknown"
        with self.assertRaises(ValueError): MODULE.evaluate(document)
