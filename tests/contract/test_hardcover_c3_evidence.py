from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class HardcoverC3EvidenceTests(unittest.TestCase):
    def test_c3_registry_has_exact_installed_predecessor_and_scope(self) -> None:
        expected = {
            "c3-fixed-adapter-input.json": (
                2230,
                "8944fd8edda4c4e2907ddb845d1ab3ccb833bfe1016d031f0497203ac44f229a",
            ),
            "c3-fixed-adapter-example-output.json": (
                187,
                "09bbf56b45a896c1a4a8ec0ef2ac45a3863d2e55370e901693677de7209baa40",
            ),
            "c3-fixed-adapter-input-schema.json": (
                1766,
                "4c17b1aaab8ec8796c0981c022ef249a780f03a3ee7296480271cc0bcbb9582a",
            ),
            "c3-fixed-adapter-variant-matrix.json": (
                987,
                "207dcef3ceccdace6addda731055b60e40d0688d0c920a703f5f828b30f472b4",
            ),
            "c3-preview-output-schema-v1.json": (
                4617,
                "40980c861683de4f56b8fb7fa60a769a47ea1223c798c858c79fd876d7f694da",
            ),
            "c3-preview-output-privacy-contract-v1.json": (
                2790,
                "32ec6078b981b15d3255296cb0f41346b5840686ae859ea48176cdb047895b37",
            ),
            "c3-preview-output-privacy-scan-v1.json": (
                3243,
                "bedc4f12909da3b255a81e83537bf0019859f393fb9c460085411f9e30df021b",
            ),
            "preview-registry-c3-v6.json": (
                5881,
                "95d971c2d6e689044320512f415caa38b6c944cab2ec91132669523f16ab2fed",
            ),
        }
        for name, (size, digest) in expected.items():
            with self.subTest(name=name):
                payload = (FIXTURES / name).read_bytes()
                self.assertEqual(len(payload), size)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

        predecessor = (FIXTURES / "preview-registry-c2-v6.json").read_bytes()
        registry = json.loads(
            (FIXTURES / "preview-registry-c3-v6.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            registry["predecessor_registry_sha256"],
            hashlib.sha256(predecessor).hexdigest(),
        )
        self.assertEqual(
            [item["scenario"] for item in registry["scenarios"]],
            ["inert-report-v3", "identity-decision-v3", "fixed-adapter-v3"],
        )
        for scenario in registry["scenarios"]:
            for resource in scenario["resources"].values():
                payload = (FIXTURES / resource["path"]).read_bytes()
                self.assertEqual(resource["bytes"], len(payload))
                self.assertEqual(
                    resource["sha256"], hashlib.sha256(payload).hexdigest()
                )

        output_schema = json.loads(
            (FIXTURES / "c3-preview-output-schema-v1.json").read_text(
                encoding="utf-8"
            )
        )
        fixed = output_schema["$defs"]["fixed_adapter_result"]
        self.assertEqual(len(fixed["oneOf"]), 6)
        branches = [item["properties"] for item in fixed["oneOf"]]
        self.assertEqual(branches[0]["address_policy"], {"const": "rejected"})
        self.assertEqual(branches[0]["peer_equal"], {"const": False})
        self.assertEqual(branches[0]["row_count"], {"const": 0})
        self.assertEqual(branches[1]["row_count"]["minimum"], 1)
        self.assertEqual(branches[1]["row_count"]["maximum"], 3)
        self.assertEqual(branches[2]["row_count"], {"const": 0})
        self.assertEqual(branches[3]["outcome"], {"const": "malformed"})
        self.assertEqual(branches[3]["row_count"]["minimum"], 0)
        self.assertEqual(branches[3]["row_count"]["maximum"], 4)
        self.assertEqual(branches[4]["row_count"]["minimum"], 2)
        for key in ("output_schema", "output_privacy_contract", "output_privacy_scan"):
            resource = registry[key]
            payload = (FIXTURES / resource["path"]).read_bytes()
            self.assertEqual(resource["bytes"], len(payload))
            self.assertEqual(resource["sha256"], hashlib.sha256(payload).hexdigest())

    def test_c3_cumulative_artifacts_exclude_future_scenarios(self) -> None:
        artifacts = (
            "c3-preview-output-schema-v1.json",
            "c3-preview-output-privacy-contract-v1.json",
            "c3-preview-output-privacy-scan-v1.json",
            "preview-registry-c3-v6.json",
        )
        forbidden = (
            "durable-work-v3",
            "safe-executor-v3",
            "durable_work_result",
            "safe_executor_result",
            "schema8",
        )
        for name in artifacts:
            payload = (FIXTURES / name).read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(name=name, marker=marker):
                    self.assertNotIn(marker, payload)


if __name__ == "__main__":
    unittest.main()
