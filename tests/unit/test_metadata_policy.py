from __future__ import annotations

import unittest

from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    MetadataPolicyError,
    MetadataPolicyProfile,
    OPTION_CATALOGUE,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.application.metadata_policy import bind_proposal_policy, preview_policy


class MetadataPolicyTests(unittest.TestCase):
    def test_builtin_is_canonical_and_replayable(self) -> None:
        profile = MetadataPolicyProfile.builtin()
        self.assertEqual(profile, MetadataPolicyProfile.from_data(profile.to_data()))
        self.assertEqual(profile.policy_id, MetadataPolicyProfile.from_data(profile.to_data()).policy_id)

    def test_every_closed_option_and_manual_interaction_resolves(self) -> None:
        destinations = {field: "ask" if field != "explicit" else "unsupported" for field in OPTION_CATALOGUE}
        for field, choices in OPTION_CATALOGUE.items():
            for choice in choices:
                options = dict(DEFAULT_OPTIONS); options[field] = choice
                profile = MetadataPolicyProfile.create("Draft", options, destinations)
                resolved = resolve_policy(profile, "library")
                self.assertEqual(dict(resolved.options)[field], choice)
                self.assertEqual("unsupported" if field == "explicit" else "manual_only", resolved.qualification(field))

    def test_sparse_override_is_read_only_and_identity_bound(self) -> None:
        profile = MetadataPolicyProfile.builtin()
        first = resolve_policy(profile, "first", {"options": {"date_precision": "full_date"}, "destinations": {"tags": "ignore"}})
        second = resolve_policy(profile, "second")
        self.assertEqual("full_date", dict(first.options)["date_precision"])
        self.assertEqual("ignore", dict(first.destinations)["tags"])
        self.assertEqual(DEFAULT_OPTIONS["date_precision"], dict(second.options)["date_precision"])
        self.assertNotEqual(first.resolved_id, second.resolved_id)

    def test_unknown_removed_and_automatic_options_fail_or_remain_unqualified(self) -> None:
        options = dict(DEFAULT_OPTIONS)
        options["date_precision"] = "full_date"
        profile = MetadataPolicyProfile.create(
            "Draft", options,
            {field: "ask" if field != "explicit" else "unsupported" for field in OPTION_CATALOGUE},
        )
        with self.assertRaises(MetadataPolicyError):
            resolve_policy(profile, "library", {"options": {"removed": "anything"}, "destinations": {}})
        with self.assertRaises(MetadataPolicyError):
            resolve_policy(profile, "library", {"options": {}, "destinations": {"explicit": "ask"}})
        automatic = resolve_policy(profile, "library", {"options": {}, "destinations": {"tags": "automatic"}})
        self.assertEqual("unavailable", automatic.qualification("tags"))

    def test_preview_names_each_library_categories_and_unapplied_count_without_mutation(self) -> None:
        class Repository:
            def __init__(self) -> None: self.calls: list[str] = []
            def current_unapplied_count(self, library: str) -> int:
                self.calls.append(library); return {"first": 2, "second": 0}[library]
        repository = Repository()
        options = dict(DEFAULT_OPTIONS)
        options["date_precision"] = "full_date"
        profile = MetadataPolicyProfile.create(
            "Draft",
            options,
            {field: "ask" if field != "explicit" else "unsupported" for field in OPTION_CATALOGUE},
        )
        preview = preview_policy(profile, ("first", "second"), {"first": {"options": {}, "destinations": {}}}, repository)
        self.assertEqual(["first", "second"], repository.calls)
        self.assertEqual(("first", "second"), tuple(item.library for item in preview.libraries))
        self.assertEqual((2, 0), tuple(item.current_unapplied_count for item in preview.libraries))
        self.assertIn("date_precision", preview.libraries[0].affected_categories)

    def test_preview_categories_are_the_exact_resolved_delta_from_active_policy(self) -> None:
        class Repository:
            def current_unapplied_count(self, library: str) -> int:
                return 0

        options = dict(DEFAULT_OPTIONS)
        destinations = {
            field: "ask" if field != "explicit" else "unsupported"
            for field in OPTION_CATALOGUE
        }
        options["date_precision"] = "full_date"
        destinations["tags"] = "ignore"
        draft = MetadataPolicyProfile.create("Draft", options, destinations)
        preview = preview_policy(
            draft,
            ("inherited", "overridden"),
            {
                "overridden": {
                    "options": {"date_precision": "year"},
                    "destinations": {"tags": "ask"},
                }
            },
            Repository(),
        )
        self.assertEqual(
            ("date_precision", "tags"), preview.libraries[0].affected_categories
        )
        self.assertEqual((), preview.libraries[1].affected_categories)

    def test_proposal_binding_is_exact_and_does_not_change_the_profile(self) -> None:
        profile = MetadataPolicyProfile.builtin()
        resolved = resolve_policy(profile, "library")
        self.assertEqual(
            resolved,
            ResolvedMetadataPolicy.from_data(resolved.to_data()),
        )
        binding = bind_proposal_policy("proposal_set_" + "a" * 64, resolved)
        self.assertEqual(resolved.resolved_id, binding.resolved_policy_id)
        self.assertEqual(profile, MetadataPolicyProfile.from_data(profile.to_data()))
