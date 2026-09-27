from __future__ import annotations

import pytest

from mechanical_librarian.application.metadata_policy import (
    PolicyPreviewCapture,
    PolicyPreviewLibraryCapture,
    bind_proposal_policy,
    preview_policy,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)


class _PreviewRepository:
    def capture_policy_preview(self, libraries, limit):
        import json

        if hasattr(self, "metadata_policy_preview_baseline"):
            document, resolutions = self.metadata_policy_preview_baseline(libraries)
        else:
            document = (
                self.active_metadata_policy()
                if hasattr(self, "active_metadata_policy")
                else MetadataPolicyProfile.builtin().canonical_json
            )
            resolutions = tuple((library, None) for library in libraries)
        captured = []
        for library, resolution in resolutions:
            total, pairs = (
                self.current_policy_impact_pairs(library, limit)
                if hasattr(self, "current_policy_impact_pairs") and limit
                else (self.current_unapplied_count(library), ())
            )
            captured.append(
                PolicyPreviewLibraryCapture(library, resolution, total, pairs)
            )
            limit -= len(pairs)
        return PolicyPreviewCapture(
            MetadataPolicyProfile.from_data(json.loads(document)), 1, tuple(captured)
        )


def test_builtin_is_canonical_and_replayable() -> None:
    profile = MetadataPolicyProfile.builtin()
    assert profile == MetadataPolicyProfile.from_data(profile.to_data())
    assert (
        profile.policy_id
        == MetadataPolicyProfile.from_data(profile.to_data()).policy_id
    )


def test_every_closed_option_and_manual_interaction_resolves() -> None:
    destinations = {
        field: "ask" if field != "explicit" else "unsupported"
        for field in OPTION_CATALOGUE
    }
    for field, choices in OPTION_CATALOGUE.items():
        for choice in choices:
            options = dict(DEFAULT_OPTIONS)
            options[field] = choice
            profile = MetadataPolicyProfile.create("Draft", options, destinations)
            resolved = resolve_policy(profile, "library")
            assert dict(resolved.options)[field] == choice
            assert (
                "unsupported" if field == "explicit" else "manual_only"
            ) == resolved.qualification(field)


def test_sparse_override_is_read_only_and_identity_bound() -> None:
    profile = MetadataPolicyProfile.builtin()
    first = resolve_policy(
        profile,
        "first",
        {
            "options": {"date_precision": "full_date"},
            "destinations": {"tags": "ignore"},
        },
    )
    second = resolve_policy(profile, "second")
    assert "full_date" == dict(first.options)["date_precision"]
    assert "ignore" == dict(first.destinations)["tags"]
    assert DEFAULT_OPTIONS["date_precision"] == dict(second.options)["date_precision"]
    assert first.resolved_id != second.resolved_id


def test_unknown_removed_and_automatic_options_fail_or_remain_unqualified() -> None:
    options = dict(DEFAULT_OPTIONS)
    options["date_precision"] = "full_date"
    profile = MetadataPolicyProfile.create(
        "Draft",
        options,
        {
            field: "ask" if field != "explicit" else "unsupported"
            for field in OPTION_CATALOGUE
        },
    )
    with pytest.raises(MetadataPolicyError):
        resolve_policy(
            profile, "library", {"options": {"removed": "anything"}, "destinations": {}}
        )
    with pytest.raises(MetadataPolicyError):
        resolve_policy(
            profile, "library", {"options": {}, "destinations": {"explicit": "ask"}}
        )
    unavailable = {"tags", "asin"}
    automatic = resolve_policy(
        profile,
        "library",
        {
            "options": {},
            "destinations": {field: "automatic" for field in unavailable}
            | {"description_fill": "automatic"},
        },
    )
    assert {field: "unavailable" for field in unavailable} == {
        field: automatic.qualification(field) for field in unavailable
    }
    assert "available" == automatic.qualification("description_fill")
    genres = resolve_policy(
        profile,
        "library",
        {"options": {}, "destinations": {"genres": "automatic"}},
    )
    assert "available" == genres.qualification("genres")
    isbn = resolve_policy(
        profile,
        "library",
        {"options": {}, "destinations": {"isbn_form": "automatic"}},
    )
    assert "available" == isbn.qualification("isbn_form")


def test_preview_names_each_library_categories_and_unapplied_count_without_mutation() -> (
    None
):
    class Repository(_PreviewRepository):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def current_unapplied_count(self, library: str) -> int:
            self.calls.append(library)
            return {"first": 2, "second": 0}[library]

    repository = Repository()
    options = dict(DEFAULT_OPTIONS)
    options["date_precision"] = "full_date"
    profile = MetadataPolicyProfile.create(
        "Draft",
        options,
        {
            field: "ask" if field != "explicit" else "unsupported"
            for field in OPTION_CATALOGUE
        },
    )
    preview = preview_policy(
        profile,
        ("first", "second"),
        {"first": {"options": {}, "destinations": {}}},
        repository,
    )
    assert ["first", "second"] == repository.calls
    assert ("first", "second") == tuple(item.library for item in preview.libraries)
    assert (2, 0) == tuple(item.current_unapplied_count for item in preview.libraries)
    assert "date_precision" in preview.libraries[0].affected_categories


def test_preview_categories_are_the_exact_resolved_delta_from_active_policy() -> None:
    class Repository(_PreviewRepository):
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
    assert (
        "title_layout",
        "date_precision",
        "language_rendering",
        "contributor_names",
        "publisher_preference",
        "subtitle",
        "series",
        "genres",
        "tags",
        "description_fill",
        "isbn_form",
        "cover_order",
    ) == preview.libraries[0].affected_categories
    assert (
        "title_layout",
        "date_precision",
        "language_rendering",
        "contributor_names",
        "publisher_preference",
        "subtitle",
        "series",
        "genres",
        "description_fill",
        "isbn_form",
        "cover_order",
    ) == preview.libraries[1].affected_categories


def test_preview_compares_with_generation_bound_resolution_after_override_change() -> (
    None
):
    active = MetadataPolicyProfile.builtin()
    persisted = resolve_policy(
        active,
        "library",
        {"options": {"date_precision": "year"}, "destinations": {}},
    )
    draft = MetadataPolicyProfile.create(
        "Draft",
        {**dict(DEFAULT_OPTIONS), "date_precision": "full_date"},
        dict(active.destinations),
    )

    class Repository(_PreviewRepository):
        def metadata_policy_preview_baseline(self, libraries):
            return active.canonical_json, (("library", persisted),)

        def current_unapplied_count(self, library: str) -> int:
            return 0

    preview = preview_policy(
        draft,
        ("library",),
        {"library": {"options": {"date_precision": "full_date"}, "destinations": {}}},
        Repository(),
    )

    assert "date_precision" in preview.libraries[0].affected_categories


def test_preview_uses_bounded_exact_current_pairs_without_effects() -> None:
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.proposal_evidence import _report

    report = _report()
    proposal = evaluate_report(report)
    options = dict(MetadataPolicyProfile.builtin().options)
    options["tags"] = "fill_empty"
    draft = MetadataPolicyProfile.create(
        "Changed draft",
        options,
        dict(MetadataPolicyProfile.builtin().destinations),
    )

    class Repository(_PreviewRepository):
        def active_metadata_policy(self) -> str:
            self.active_reads = getattr(self, "active_reads", 0) + 1
            return MetadataPolicyProfile.builtin().canonical_json

        def current_unapplied_count(self, library: str) -> int:
            return 1

        def current_policy_impact_pairs(self, library: str, limit: int):
            self.limit = limit
            return (1, ((report, proposal),))

    repository = Repository()
    preview = preview_policy(draft, (report.target.library,), {}, repository)
    assert 20 == repository.limit
    assert 1 == repository.active_reads
    assert MetadataPolicyProfile.builtin().policy_id == preview.active_policy_id
    assert 1 == preview.libraries[0].cases_total
    assert (report.target.current_title or "Untitled",) == preview.libraries[
        0
    ].work_labels
    assert 8 == len(preview.libraries[0].cases[0].fields)
    assert (
        proposal.resolved_policy_id != preview.libraries[0].cases[0].resolved_policy_id
    )


def test_preview_caps_cases_across_libraries_and_uses_pair_snapshot_counts() -> None:
    from mechanical_librarian.domain.metadata_report import (
        MetadataReport,
        TargetFacts,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.proposal_evidence import _report

    source = _report()

    def pair_for(library: str):
        report = MetadataReport.create(
            target=TargetFacts(
                source.target.connection,
                library,
                source.target.item,
                source.target.fields,
            ),
            file=source.file,
            local_claims=source.local_claims,
            local_reasons=source.local_reasons,
            providers=source.providers,
            cover_sources=source.cover_sources,
        )
        return report, evaluate_report(report)

    first = pair_for("first")
    second = pair_for("second")

    class Repository(_PreviewRepository):
        def active_metadata_policy(self) -> str:
            self.active_reads = getattr(self, "active_reads", 0) + 1
            return MetadataPolicyProfile.builtin().canonical_json

        def current_unapplied_count(self, library: str) -> int:
            return 999

        def current_policy_impact_pairs(self, library: str, limit: int):
            total, pair = {"first": (15, first), "second": (15, second)}[library]
            return total, (pair,) * min(limit, total)

    options = dict(MetadataPolicyProfile.builtin().options)
    options["tags"] = "fill_empty"
    draft = MetadataPolicyProfile.create(
        "Global bounded preview",
        options,
        dict(MetadataPolicyProfile.builtin().destinations),
    )
    repository = Repository()
    preview = preview_policy(draft, ("first", "second"), {}, repository)
    assert 1 == repository.active_reads
    first_impact, second_impact = preview.libraries
    assert 20 == sum(len(impact.cases) for impact in preview.libraries)
    assert (15, False, 15) == (
        first_impact.cases_total,
        first_impact.cases_truncated,
        first_impact.current_unapplied_count,
    )
    assert (15, True, 15) == (
        second_impact.cases_total,
        second_impact.cases_truncated,
        second_impact.current_unapplied_count,
    )
    assert (15, 5) == (len(first_impact.cases), len(second_impact.cases))


def test_proposal_binding_is_exact_and_does_not_change_the_profile() -> None:
    profile = MetadataPolicyProfile.builtin()
    resolved = resolve_policy(profile, "library")
    assert resolved == ResolvedMetadataPolicy.from_data(resolved.to_data())
    binding = bind_proposal_policy("proposal_set_" + "a" * 64, resolved)
    assert resolved.resolved_id == binding.resolved_policy_id
    assert profile == MetadataPolicyProfile.from_data(profile.to_data())
