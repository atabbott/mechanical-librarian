from __future__ import annotations

import io
import json
import os
import tempfile
from inspect import signature
from pathlib import Path
from typing import get_protocol_members, get_type_hints

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteShadowPreviewRepository,
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.application.metadata_policy import (
    PolicyImpactRepository,
    PolicyPreviewApplication,
)
from mechanical_librarian.bootstrap.composition import run_policy_preview
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.interfaces.cli import main

"""Prove the preview composes only against a contract its store satisfies.

The CLI reaches two operations, so a composition that also named session,
review-case, decision, library, browser-state, or policy-mutation members
stayed green while every one of them was a runtime `AttributeError`. These
two tests bound the composed surface and its store contract instead.
"""


def _unsatisfied(members: frozenset[str]) -> frozenset[str]:
    return frozenset(
        name for name in members if not hasattr(SQLiteShadowPreviewRepository, name)
    )


def test_preview_composition_declares_only_the_preview_store() -> None:
    parameters = signature(PolicyPreviewApplication.__init__).parameters
    assert {"self", "repository", "libraries", "overrides"} == set(parameters)
    declared = get_type_hints(PolicyPreviewApplication.__init__)["repository"]
    assert frozenset() == _unsatisfied(get_protocol_members(declared))
    # The same computation over the contract this composition used to be
    # given reports exactly what the shadow preview repository does not
    # have, so the assertion above is a live check rather than a vacuous
    # one: declaring the wider store here would fail it.
    assert frozenset(
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
    ) == _unsatisfied(get_protocol_members(PolicyImpactRepository))


def test_composed_preview_reaches_every_operation_it_exposes() -> None:
    builtin = MetadataPolicyProfile.builtin()
    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "state.sqlite3"
        open_repository(database).close()
        repository = open_shadow_preview_repository(database)
        try:
            application = PolicyPreviewApplication(repository, ("ebooks",), {})
            assert {"preview_policy", "rich_qualification", "validate_policy"} == {
                name for name in dir(application) if not name.startswith("_")
            }
            assert 19 == len(application.rich_qualification(builtin))
            assert builtin == application.validate_policy(
                builtin.name,
                dict(builtin.options),
                dict(builtin.destinations),
            )
            profile, preview = application.preview_policy(
                builtin.name, dict(builtin.options), dict(builtin.destinations)
            )
            assert builtin == profile
            assert ("ebooks",) == tuple(item.library for item in preview.libraries)
        finally:
            repository.close()


def test_composed_preview_uses_active_profile_for_new_library() -> None:
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

        assert [] == result["libraries"][0]["affected_categories"]


def test_composed_preview_uses_existing_database_without_mutation() -> None:
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
        assert "metadata-policy-preview-v1" == result["schema_version"]
        assert 19 == len(result["rich_qualification"])
        assert {
            "description_fill:safe_plain_text",
            "genres:fill_empty",
            "genres:add",
            "genres:replace",
            "series:primary_narrative",
            "series:source_order",
            "series:all_qualified",
            "isbn_form:preserve_exact",
            "isbn_form:prefer_isbn13",
            "tags:fill_empty",
            "tags:add",
            "tags:replace",
        } == {
            f"{item['field']}:{item['option']}"
            for item in result["rich_qualification"]
            if item["automatic_available"] is True
        }
        assert "ebooks" == result["libraries"][0]["library"]
        assert [] == result["libraries"][0]["cases"]
        assert before_database == database.read_bytes()
        assert before_entries == tuple(sorted(value.name for value in root.iterdir()))


def test_canonical_profile_is_delegated_read_only_and_canonical_output() -> None:
    profile = MetadataPolicyProfile.builtin().to_data()
    result = {
        "libraries": [
            {
                "affected_categories": [],
                "cases": [],
                "cases_total": 0,
                "cases_truncated": False,
                "current_unapplied_count": 0,
                "library": "library",
                "resolved_policy_id": "resolved_policy_" + "a" * 64,
            }
        ],
        "policy_id": "policy_" + "b" * 64,
        "schema_version": "metadata-policy-preview-v1",
    }
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "policy.json"
        path.write_text(
            json.dumps(profile, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        before = path.read_bytes()
        output = io.StringIO()
        calls: list[dict[str, object]] = []
        code = main(
            [
                "policy-preview",
                "--config",
                str(path),
                "--database",
                str(path),
                "--profile",
                str(path),
            ],
            policy_preview_runner=lambda _config, _database, value: (
                calls.append(value) or result  # type: ignore[func-returns-value,return-value]  # reason: the `append(...) or value` recorder idiom is deliberate; the recorded profile is a JSON document
            ),
            stdout=output,
            stderr=io.StringIO(),
        )
        assert 0 == code
        assert [profile] == calls
        assert before == path.read_bytes()
        assert result == json.loads(output.getvalue())


def test_invalid_json_is_safe_and_does_not_invoke_runner() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "policy.json"
        path.write_text("{", encoding="utf-8")
        errors = io.StringIO()
        assert 1 == main(
            [
                "policy-preview",
                "--config",
                str(path),
                "--database",
                str(path),
                "--profile",
                str(path),
            ],
            policy_preview_runner=lambda *_: pytest.fail("called"),
            stdout=io.StringIO(),
            stderr=errors,
        )
        assert "Review server failed safely.\n" == errors.getvalue()


def test_prior_schema_is_rejected_before_the_runner() -> None:
    profile = MetadataPolicyProfile.builtin().to_data()
    profile["schema_version"] = "metadata-policy-profile-v1"
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "policy.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        assert 1 == main(
            [
                "policy-preview",
                "--config",
                str(path),
                "--database",
                str(path),
                "--profile",
                str(path),
            ],
            policy_preview_runner=lambda *_: pytest.fail("called"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )


@pytest.mark.parametrize("special", ("oversized", "fifo"), ids=("oversized", "fifo"))
def test_oversized_and_special_profiles_fail_before_runner(special: str) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        path = root / "profile.json"
        if special == "oversized":
            path.write_bytes(b"x" * 16_385)
        else:
            os.mkfifo(path)
        assert 1 == main(
            [
                "policy-preview",
                "--config",
                str(path),
                "--database",
                str(path),
                "--profile",
                str(path),
            ],
            policy_preview_runner=lambda *_: pytest.fail("called"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
