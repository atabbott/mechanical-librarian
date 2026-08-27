from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ProviderLaneObservation,
    ProviderQueryKind,
    ProviderStatus,
    _provider_lane_from_data,
)
from mechanical_librarian.domain.work_identity import (
    WORK_TITLE_TRANSFORMATION_RULES,
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    WorkTitleTransformationLineage,
    WorkTitleTransformationSide,
    admit_provider_work_identity,
    apply_work_title_transformation,
    narrowing_work_query,
    replay_provider_work_identity_admission,
    work_dispatch_lineage_from_data,
    work_query_ladder,
    work_title_has_variant_marker,
    work_title_transformation_lineage_from_data,
)


def test_closed_rule_table_has_one_independent_row_per_family() -> None:
    assert tuple(rule.family for rule in WORK_TITLE_TRANSFORMATION_RULES) == tuple(
        WorkTitleTransformationFamily
    )
    assert all(rule.query_enabled for rule in WORK_TITLE_TRANSFORMATION_RULES)
    assert [WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX] == [
        rule.family
        for rule in WORK_TITLE_TRANSFORMATION_RULES
        if rule.admission_enabled
    ]


@pytest.mark.parametrize(
    ("title", "authors", "family", "expected"),
    (
        (
            "Plain_Work",
            ("Ada Example",),
            WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
            "Plain Work",
        ),
        (
            "Ada_Example_-_Plain_Work",
            ("Ada Example",),
            WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
            "Plain Work",
        ),
        (
            "Plain Work [retail]",
            ("Ada Example",),
            WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,
            "Plain Work",
        ),
        (
            "Plain Work ISBN 9780306406157",
            ("Ada Example",),
            WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
            "Plain Work",
        ),
        (
            "Plain Work: 2nd Edition",
            ("Ada Example",),
            WorkTitleTransformationFamily.EDITION_LABEL,
            "Plain Work",
        ),
        (
            "Plain Work (Series Name)",
            ("Ada Example",),
            WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
            "Plain Work",
        ),
        (
            "Plain Work (12)",
            ("Ada Example",),
            WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
            "Plain Work",
        ),
        (
            "Plain Work (2012)",
            ("Ada Example",),
            WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
            "Plain Work",
        ),
        (
            "Plain Work Book 2",
            ("Ada Example",),
            WorkTitleTransformationFamily.TRAILING_SERIES_SEQUENCE,
            "Plain Work",
        ),
        (
            "Series Book 2 - Plain Work",
            ("Ada Example",),
            WorkTitleTransformationFamily.ORDER_INVERTED_SERIES_PREFIX,
            "Plain Work",
        ),
        (
            "Plain Work: Subtitle",
            ("Ada Example",),
            WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
            "Plain Work",
        ),
        (
            "Plain & Work",
            ("Ada Example",),
            WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
            "Plain and Work",
        ),
    ),
)
def test_each_rule_shapes_one_bounded_query_rung(
    title: str,
    authors: tuple[str, ...],
    family: WorkTitleTransformationFamily,
    expected: str,
) -> None:
    assert apply_work_title_transformation(family, title, authors) == expected
    ladder = work_query_ladder(title, authors)

    matching = [query for query in ladder if family in query.transformations]
    assert matching
    assert expected in {query.title for query in matching}
    assert len(ladder) <= 3


def test_parenthesized_bare_number_excludes_year_family() -> None:
    query = work_query_ladder("Plain Work (2012)", ("Ada Example",))[0]

    assert WorkTitleTransformationFamily.PARENTHESIZED_YEAR in query.transformations
    assert (
        WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER
        not in query.transformations
    )


def test_ladder_deduplicates_and_narrowing_preserves_base_lineage() -> None:
    assert len(work_query_ladder("Plain Work", ("Ada Example",))) == 1
    query = work_query_ladder("Plain Work: Subtitle", ("Ada & Bea",))[1]

    narrowed = narrowing_work_query(query)

    assert (query.title, query.authors, query.rung, query.transformations) == (
        narrowed.title,
        narrowed.authors,
        narrowed.rung,
        narrowed.transformations,
    )
    assert narrowed.candidate_limit_narrowing_retry
    assert WorkDispatchLineage.from_query(narrowed).candidate_limit_narrowing_retry


@pytest.mark.parametrize("separator", (" - ", "–", "—", " – ", " — ", ":"))
def test_exact_primary_author_filename_prefix_admits_local_side(separator: str) -> None:
    decision = admit_provider_work_identity(
        local_title=f"Ada_Example{separator}Plain Work",
        local_authors=("Ada Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    )

    assert decision.admitted
    assert decision.transformation == WorkTitleTransformationLineage(
        WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
        WorkTitleTransformationSide.LOCAL,
    )


def test_exact_primary_author_filename_prefix_admits_provider_side() -> None:
    decision = admit_provider_work_identity(
        local_title="Plain Work",
        local_authors=("Ada Example",),
        provider_title="Ada_Example_-_Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    )

    assert decision.transformation == WorkTitleTransformationLineage(
        WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
        WorkTitleTransformationSide.PROVIDER,
    )


@pytest.mark.parametrize(
    "decorated",
    (
        "Example_Ada_-_Plain_Work",
        "A_Example_-_Plain_Work",
        "Ada_-_Plain_Work",
        "Bea_Example_-_Plain_Work",
        "Ada Example Jr_-_Plain_Work",
    ),
)
def test_author_prefix_same_author_negatives_reject(decorated: str) -> None:
    assert not admit_provider_work_identity(
        local_title=decorated,
        local_authors=("Ada Example", "Bea Example"),
        provider_title="Plain Work",
        provider_authors=("Ada Example", "Bea Example"),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize(
    "decorated",
    (
        "Plain Work [retail]",
        "Plain Work ISBN 9780306406157",
        "Plain Work: 2nd Edition",
        "Plain Work (Series Name)",
        "Plain Work (12)",
        "Plain Work (2012)",
        "Plain Work Book 2",
        "Series Book 2 - Plain Work",
        "Plain Work: Subtitle",
        "Plain & Work",
    ),
)
def test_query_only_families_never_admit(decorated: str) -> None:
    assert not admit_provider_work_identity(
        local_title=decorated,
        local_authors=("Ada Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize(
    "decoration_only",
    (
        "[retail]",
        "(1)",
        "(2010)",
        "ISBN 9780306406157",
        "2nd Edition",
        "Book 2",
    ),
)
def test_decoration_only_work_titles_have_no_dispatches(
    decoration_only: str,
) -> None:
    assert () == work_query_ladder(decoration_only, ("Ada Example",))


def test_complete_ordered_authors_and_variant_veto_remain_identity_floor() -> None:
    assert not admit_provider_work_identity(
        local_title="Ada_Example_-_Plain_Work",
        local_authors=("Ada Example", "Bea Example"),
        provider_title="Plain Work",
        provider_authors=("Bea Example", "Ada Example"),
        provider_work_identity="work-1",
    ).admitted
    assert work_title_has_variant_marker("Plain Work (Variant)")
    assert not admit_provider_work_identity(
        local_title="Plain Work",
        local_authors=("Ada Example",),
        provider_title="Plain Work (Variant)",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize(
    ("local_title", "provider_title"),
    (
        ("Work:", "Work"),
        ("Work;", "Work"),
        ("Work-", "Work"),
        (":Work", "Work"),
        ("-Work", "Work"),
    ),
    ids=(
        "trailing_colon",
        "trailing_semicolon",
        "trailing_hyphen",
        "leading_colon",
        "leading_hyphen",
    ),
)
def test_admission_preserves_title_punctuation_collisions(
    local_title: str, provider_title: str
) -> None:
    assert not admit_provider_work_identity(
        local_title=local_title,
        local_authors=("Ada Example",),
        provider_title=provider_title,
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


def test_admission_preserves_author_and_filename_remainder_punctuation() -> None:
    assert not admit_provider_work_identity(
        local_title="Work",
        local_authors=("Ada Example,",),
        provider_title="Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted
    assert not admit_provider_work_identity(
        local_title="Ada Example - Plain_Work",
        local_authors=("Ada Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted
    assert not admit_provider_work_identity(
        local_title="Ada Example - Work:",
        local_authors=("Ada Example",),
        provider_title="Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize("separator", ("\u00a0", "\u2009", "\u202f", "\u3000"))
def test_admission_retains_established_unicode_whitespace_equality(
    separator: str,
) -> None:
    assert admit_provider_work_identity(
        local_title=f"Plain{separator}Work",
        local_authors=(f"Ada{separator}Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


def test_replay_requires_one_exact_ladder_dispatch_or_its_narrowing_successor() -> None:
    local_title = "Ada_Example_-_Plain Work"
    authors = ("Ada Example",)
    query = work_query_ladder(local_title, authors)[0]
    exact = WorkDispatchLineage.from_query(query)

    accepted = replay_provider_work_identity_admission(
        local_title=local_title,
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=authors,
        provider_work_identity="work-1",
        dispatch_lineage=exact,
    )
    narrowed = replay_provider_work_identity_admission(
        local_title=local_title,
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=authors,
        provider_work_identity="work-1",
        dispatch_lineage=WorkDispatchLineage.from_query(narrowing_work_query(query)),
    )
    absent = replay_provider_work_identity_admission(
        local_title=local_title,
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=authors,
        provider_work_identity="work-1",
        dispatch_lineage=None,
    )
    false = replay_provider_work_identity_admission(
        local_title=local_title,
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=authors,
        provider_work_identity="work-1",
        dispatch_lineage=WorkDispatchLineage(
            WorkDispatchRung.NORMALIZED,
            (WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,),
        ),
    )

    assert accepted.admitted and narrowed.admitted
    assert not absent.admitted and not false.admitted
    assert not admit_provider_work_identity(
        local_title="Ada_Example_-_Plain_Work",
        local_authors=("Ada Example", "Bea Example"),
        provider_title="Plain Work",
        provider_authors=("Ada Example", "Bea Example"),
        provider_work_identity="work-1",
        variant_excluded=True,
    ).admitted


def test_lineage_readback_rejects_unknown_malformed_and_query_only_admission() -> None:
    dispatch = WorkDispatchLineage(
        WorkDispatchRung.NORMALIZED,
        (WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,),
        True,
    )
    assert work_dispatch_lineage_from_data(dispatch.to_data()) == dispatch
    with pytest.raises((TypeError, ValueError)):
        work_dispatch_lineage_from_data({**dispatch.to_data(), "rung": "unknown"})
    with pytest.raises(ValueError):
        work_dispatch_lineage_from_data({"rung": "normalized"})
    with pytest.raises(ValueError):
        work_title_transformation_lineage_from_data(
            {"family": "bracket_source_tag", "side": "local"}
        )


@pytest.mark.parametrize(
    ("rung", "transformations"),
    (
        ("normalized", ["subtitle_segment"]),
        ("subtitle_stripped", []),
        ("subtitle_stripped", ["punctuation_ampersand_fold"]),
        ("punctuation_folded", ["subtitle_segment"]),
        (
            "punctuation_folded",
            ["subtitle_segment", "filename_underscores", "punctuation_ampersand_fold"],
        ),
    ),
)
def test_dispatch_lineage_readback_rejects_impossible_ladder_combinations(
    rung: str,
    transformations: list[str],
) -> None:
    with pytest.raises(ValueError):
        work_dispatch_lineage_from_data(
            {
                "candidate_limit_narrowing_retry": False,
                "rung": rung,
                "transformations": transformations,
            }
        )


def test_persisted_lane_lineages_round_trip_and_reject_tampering() -> None:
    lane = ProviderLaneObservation(
        "lane_" + ("a" * 64),
        ProviderQueryKind.WORK,
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ("local_" + ("b" * 64),),
        dispatch_lineage=WorkDispatchLineage(
            WorkDispatchRung.SUBTITLE_STRIPPED,
            (
                WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
            ),
            True,
        ),
        transformation_lineage=WorkTitleTransformationLineage(
            WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
            WorkTitleTransformationSide.LOCAL,
        ),
    )
    data = lane.to_data()

    assert _provider_lane_from_data(data) == lane
    with pytest.raises(ValueError):
        _provider_lane_from_data(
            {
                **data,
                "transformation_lineage": {
                    "family": "subtitle_segment",
                    "side": "local",
                },
            }
        )
    dispatch_data = data["dispatch_lineage"]
    assert type(dispatch_data) is dict
    with pytest.raises(ValueError):
        _provider_lane_from_data(
            {
                **data,
                "dispatch_lineage": {
                    **dispatch_data,
                    "candidate_limit_narrowing_retry": "true",
                },
            }
        )
