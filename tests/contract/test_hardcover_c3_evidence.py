from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.providers import hardcover_identity as adapter
from mechanical_librarian.adapters.providers.hardcover_identity import (
    StructuralHardcoverIdentityPreview,
)
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IsbnIdentityQuery,
)
from mechanical_librarian.application.schema_validation import (
    UTF8_BYTE_LENGTH_META_SCHEMA,
    UTF8_BYTE_LENGTH_VOCABULARY,
    SchemaResourceRegistry,
    meta_schema_validates,
    validates_utf8_byte_limits,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/hardcover_identity"


class HardcoverC3EvidenceTests(unittest.TestCase):
    def schema_resources(
        self,
    ) -> tuple[dict[str, object], dict[str, object], SchemaResourceRegistry]:
        schema = json.loads(
            (FIXTURES / "c3-fixed-adapter-input-schema.json").read_text(
                encoding="utf-8"
            )
        )
        meta = json.loads(
            (FIXTURES / "c3-utf8-byte-length-meta-schema-v1.json").read_text(
                encoding="utf-8"
            )
        )
        return schema, meta, SchemaResourceRegistry((meta,))

    def test_custom_meta_schema_validates_and_requires_byte_vocabulary(self) -> None:
        schema, meta, registry = self.schema_resources()
        self.assertEqual(schema["$schema"], UTF8_BYTE_LENGTH_META_SCHEMA)
        self.assertNotIn("$vocabulary", schema)
        self.assertIs(meta["$vocabulary"][UTF8_BYTE_LENGTH_VOCABULARY], True)
        self.assertTrue(meta_schema_validates(schema, registry))

        missing = json.loads(json.dumps(meta))
        del missing["$vocabulary"][UTF8_BYTE_LENGTH_VOCABULARY]
        with self.assertRaisesRegex(ValueError, "vocabulary is absent"):
            meta_schema_validates(schema, SchemaResourceRegistry((missing,)))

    def test_external_registry_resolution_cannot_silently_ignore_vocabulary(
        self,
    ) -> None:
        schema, meta, _ = self.schema_resources()

        class ExternalRegistry:
            def __init__(self) -> None:
                self.resolved: list[str] = []

            def resolve(self, uri: str) -> dict[str, object]:
                self.resolved.append(uri)
                if uri != meta["$id"]:
                    raise ValueError("external schema resource is unavailable")
                return meta

        external = ExternalRegistry()
        self.assertTrue(meta_schema_validates(schema, external))
        self.assertEqual(external.resolved, [UTF8_BYTE_LENGTH_META_SCHEMA])

    def test_c3_byte_vocabulary_matches_runtime_multibyte_boundaries(self) -> None:
        schema, _, registry = self.schema_resources()
        canonical = json.loads(
            (FIXTURES / "c3-fixed-adapter-input.json").read_text(encoding="utf-8")
        )
        locations = (
            (("request", "body_bytes_utf8"), 8_192),
            (("response", "raw_status_line_bytes_utf8"), 128),
            (("response", "raw_header_lines_bytes_utf8", 0), 256),
            (("response", "raw_body_bytes_utf8"), 65_536),
        )
        query = IsbnIdentityQuery("9780000000019", "0000000019")

        for path, limit in locations:
            for delta in (-1, 0, 1):
                byte_count = limit + delta
                value = "é" * (byte_count // 2) + ("A" if byte_count % 2 else "")
                source = json.loads(json.dumps(canonical))
                target = source
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                schema_valid = validates_utf8_byte_limits(schema, source, registry)
                with patch.object(
                    adapter,
                    "identity_query_from_canonical_body",
                    return_value=query,
                ), patch.object(
                    adapter.HardcoverIdentityProvider,
                    "prepare",
                    return_value=IdentityAttemptResult.failed(
                        IdentityAttemptFailureKind.MALFORMED
                    ),
                ) as prepare:
                    try:
                        StructuralHardcoverIdentityPreview().run(source)
                        runtime_valid = True
                    except ValueError:
                        runtime_valid = False
                expected = delta <= 0
                with self.subTest(path=path, delta=delta):
                    self.assertEqual(schema_valid, expected)
                    self.assertEqual(runtime_valid, expected)
                    self.assertEqual(prepare.called, expected)

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
                1880,
                "5487c3373d590ee4ed9bc35e07870f8d87356456c5683704fb6feedfebf7923b",
            ),
            "c3-utf8-byte-length-meta-schema-v1.json": (
                842,
                "6e661639a611c463c65303efc2c83f97e9b522d23ee30c2e4e78f0d4c4e703b1",
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
                6040,
                "394ac103c85548ca3a1de44ef236ed59323fada7340a952b66aab986d0621a02",
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
