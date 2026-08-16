from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from inspect import signature
from pathlib import Path
from typing import get_protocol_members, get_type_hints

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteShadowPreviewRepository,
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.application.metadata_policy import PolicyImpactRepository
from mechanical_librarian.application.review_application import (
    compose_policy_preview_application,
)
from mechanical_librarian.bootstrap.composition import run_policy_preview
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.interfaces.cli import main


class PolicyPreviewCompositionTests(unittest.TestCase):
    """Prove the preview composes only against a contract its store satisfies.

    The CLI reaches two operations, so a composition that also named session,
    review-case, decision, library, browser-state, or policy-mutation members
    stayed green while every one of them was a runtime `AttributeError`. These
    two tests bound the composed surface and its store contract instead.
    """

    @staticmethod
    def _unsatisfied(members: frozenset[str]) -> frozenset[str]:
        return frozenset(
            name
            for name in members
            if not hasattr(SQLiteShadowPreviewRepository, name)
        )

    def test_preview_composition_declares_only_the_preview_store(self) -> None:
        parameters = signature(compose_policy_preview_application).parameters
        self.assertEqual({"policy_store", "policy_libraries"}, set(parameters))
        declared = get_type_hints(compose_policy_preview_application)["policy_store"]
        self.assertEqual(frozenset(), self._unsatisfied(get_protocol_members(declared)))
        # The same computation over the contract this composition used to be
        # given reports exactly what the shadow preview repository does not
        # have, so the assertion above is a live check rather than a vacuous
        # one: declaring the wider store here would fail it.
        self.assertEqual(
            frozenset(
                {
                    "acknowledge_metadata_policy_transition",
                    "activate_metadata_policy",
                    "activate_qualified_automatic",
                    "create_metadata_policy_draft",
                    "current_resolved_metadata_policy",
                    "current_resolved_policy_id",
                    "enter_shadow_preview",
                    "get_metadata_policy_draft",
                    "list_shadow_outcomes",
                    "metadata_policy_transition",
                    "retry_metadata_policy_transition",
                    "shadow_posture",
                }
            ),
            self._unsatisfied(get_protocol_members(PolicyImpactRepository)),
        )

    def test_composed_preview_reaches_every_operation_it_exposes(self) -> None:
        builtin = MetadataPolicyProfile.builtin()
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "state.sqlite3"
            open_repository(database).close()
            repository = open_shadow_preview_repository(database)
            try:
                application = compose_policy_preview_application(
                    policy_store=repository,
                    policy_libraries=(("ebooks", None),),
                )
                self.assertEqual(
                    {"policy", "preview_policy", "validate_policy"},
                    {
                        name
                        for name in dir(application)
                        if not name.startswith("_")
                    },
                )
                self.assertEqual(
                    {"active", "preview", "rich_qualification", "validate"},
                    {
                        name
                        for name in dir(application.policy)
                        if not name.startswith("_")
                    },
                )
                self.assertEqual(builtin, application.policy.active())
                self.assertEqual(19, len(application.policy.rich_qualification(builtin)))
                self.assertEqual(builtin, application.policy.validate(builtin))
                self.assertEqual(
                    builtin,
                    application.validate_policy(
                        builtin.name,
                        dict(builtin.options),
                        dict(builtin.destinations),
                    ),
                )
                self.assertEqual(
                    builtin.policy_id, application.policy.preview(builtin).profile_id
                )
                profile, preview = application.preview_policy(
                    builtin.name, dict(builtin.options), dict(builtin.destinations)
                )
                self.assertEqual(builtin, profile)
                self.assertEqual(
                    ("ebooks",), tuple(item.library for item in preview.libraries)
                )
            finally:
                repository.close()


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
            code = main(["policy-preview", "--config", str(path), "--database", str(path), "--profile", str(path)], policy_preview_runner=lambda _config, _database, value: calls.append(value) or result, stdout=output, stderr=io.StringIO())  # type: ignore[func-returns-value,return-value]  # reason: the `append(...) or value` recorder idiom is deliberate; the recorded profile is a JSON document
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
