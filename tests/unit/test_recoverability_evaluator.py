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
        self.assertEqual(set(result["supported"]["categories"]), set(MODULE.CATEGORIES))
        self.assertEqual(sum(result["supported"]["categories"].values()), result["supported"]["value_bearing_rows"])
        self.assertEqual(result["isbn"], {"associated": 1})
        self.assertEqual(result["excluded"], {"accepted_unsupported": 1, "known_empty": 1, "not_applicable": 1, "unexplained_failure": 1, "unknown": 1})

    def test_precedence_and_private_values_never_enter_output(self):
        document = self.load()
        document["rows"][0]["private_title"] = "/private/library/Secret Title 9780306406157"
        result = json.dumps(MODULE.evaluate(document))
        self.assertNotIn("Secret", result)
        self.assertNotIn("9780306406157", result)
        self.assertEqual(MODULE.category(document["rows"][1]), "inspection_failure")

    def test_rejects_unclosed_or_unsupported_contract_rows(self):
        document = self.load()
        document["rows"][7]["supported"] = True
        with self.assertRaises(ValueError): MODULE.evaluate(document)
        document = self.load()
        document["rows"][6]["isbn_relation"] = "associated"
        document["rows"][6].pop("association_basis")
        with self.assertRaises(ValueError): MODULE.evaluate(document)
