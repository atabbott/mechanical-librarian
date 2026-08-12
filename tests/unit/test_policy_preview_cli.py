from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile, resolve_policy
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.bootstrap.composition import run_policy_preview
from mechanical_librarian.interfaces.cli import main


class PolicyPreviewCliTests(unittest.TestCase):
    def test_composed_preview_uses_active_profile_for_new_library(self) -> None:
        builtin = MetadataPolicyProfile.builtin()
        profile = MetadataPolicyProfile.create(
            "Activated policy",
            {**dict(builtin.options), "language_rendering": "preserve"},
            dict(builtin.destinations),
        )
        source = Path("docs/examples/synthetic-config.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            database = root / "state.sqlite3"
            config.write_bytes(source.read_bytes())
            repository = open_repository(database)
            repository.create_metadata_policy_draft(profile.canonical_json)
            resolved = resolve_policy(profile, "first")
            repository.activate_metadata_policy(
                profile.policy_id,
                builtin.policy_id,
                (("first", resolved.resolved_id, resolved.canonical_json),),
                1,
            )
            repository.close()

            result = run_policy_preview(config, database, profile.to_data())

            self.assertEqual([], result["libraries"][0]["affected_categories"])

    def test_composed_preview_uses_existing_database_without_mutation(self) -> None:
        profile = MetadataPolicyProfile.builtin().to_data()
        source = Path("docs/examples/synthetic-config.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            database = root / "state.sqlite3"
            config.write_bytes(source.read_bytes())
            repository = open_repository(database)
            repository.close()
            before_database = database.read_bytes()
            before_entries = tuple(sorted(value.name for value in root.iterdir()))
            result = run_policy_preview(config, database, profile)
            self.assertEqual("metadata-policy-preview-v1", result["schema_version"])
            self.assertEqual(19, len(result["rich_qualification"]))
            self.assertEqual(
                {
                    "description_fill:safe_plain_text",
                    "genres:fill_empty", "genres:add", "genres:replace",
                    "series:primary_narrative", "series:source_order",
                    "series:all_qualified", "isbn_form:preserve_exact",
                    "isbn_form:prefer_isbn13",
                    "tags:fill_empty", "tags:add", "tags:replace",
                },
                {
                    f"{item['field']}:{item['option']}"
                    for item in result["rich_qualification"]
                    if item["automatic_available"] is True
                },
            )
            self.assertEqual("ebooks", result["libraries"][0]["library"])
            self.assertEqual([], result["libraries"][0]["cases"])
            self.assertEqual(before_database, database.read_bytes())
            self.assertEqual(
                before_entries,
                tuple(sorted(value.name for value in root.iterdir())),
            )

    def test_canonical_profile_is_delegated_read_only_and_canonical_output(self) -> None:
        profile = MetadataPolicyProfile.builtin().to_data()
        result = {"libraries": [{"affected_categories": [], "cases": [], "cases_total": 0, "cases_truncated": False, "current_unapplied_count": 0, "library": "library", "resolved_policy_id": "resolved_policy_" + "a" * 64}], "policy_id": "policy_" + "b" * 64, "schema_version": "metadata-policy-preview-v1"}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            path.write_text(json.dumps(profile, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            before = path.read_bytes()
            output = io.StringIO()
            calls: list[dict[str, object]] = []
            code = main(["policy-preview", "--config", str(path), "--database", str(path), "--profile", str(path)], policy_preview_runner=lambda _config, _database, value: calls.append(value) or result, stdout=output, stderr=io.StringIO())
            self.assertEqual(0, code)
            self.assertEqual([profile], calls)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(result, json.loads(output.getvalue()))

    def test_invalid_json_is_safe_and_does_not_invoke_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            path.write_text("{", encoding="utf-8")
            errors = io.StringIO()
            self.assertEqual(1, main(["policy-preview", "--config", str(path), "--database", str(path), "--profile", str(path)], policy_preview_runner=lambda *_: self.fail("called"), stdout=io.StringIO(), stderr=errors))
            self.assertEqual("Review server failed safely.\n", errors.getvalue())

    def test_prior_schema_is_rejected_before_the_runner(self) -> None:
        profile = MetadataPolicyProfile.builtin().to_data()
        profile["schema_version"] = "metadata-policy-profile-v1"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            path.write_text(json.dumps(profile), encoding="utf-8")
            self.assertEqual(1, main(["policy-preview", "--config", str(path), "--database", str(path), "--profile", str(path)], policy_preview_runner=lambda *_: self.fail("called"), stdout=io.StringIO(), stderr=io.StringIO()))

    def test_oversized_and_special_profiles_fail_before_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "oversized.json"
            oversized.write_bytes(b"x" * 16_385)
            fifo = root / "profile.fifo"
            os.mkfifo(fifo)
            for path in (oversized, fifo):
                with self.subTest(path=path.name):
                    self.assertEqual(1, main(["policy-preview", "--config", str(path), "--database", str(path), "--profile", str(path)], policy_preview_runner=lambda *_: self.fail("called"), stdout=io.StringIO(), stderr=io.StringIO()))
