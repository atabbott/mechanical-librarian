from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    ProviderLaneCandidate,
    ProviderLaneObservation,
    ProviderQueryKind,
    ProviderStatus,
    _provider_lane_from_data,
)
from mechanical_librarian.domain.work_identity import (
    WORK_TITLE_COMPOUND_PRODUCTIONS,
    WORK_TITLE_TRANSFORMATION_RULES,
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleCompoundProduction,
    WorkTitleTransformationApplication,
    WorkTitleTransformationFamily,
    WorkTitleTransformationField,
    WorkTitleTransformationLineage,
    WorkTitleTransformationSide,
    admit_provider_work_identity,
    apply_work_title_transformation,
    narrowing_work_query,
    replay_provider_work_identity_admission,
    work_dispatch_lineage_from_data,
    work_query_ladder,
    work_title_election_matches,
    work_title_has_variant_marker,
    work_title_transformation_lineage_from_data,
)


@pytest.mark.parametrize(
    ("title", "family", "clean", "component"),
    (
        (
            "Plain Work [retail]",
            WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,
            "Plain Work",
            None,
        ),
        (
            "Plain Work (2)",
            WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
            "Plain Work",
            "2",
        ),
        (
            "Plain Work (The Sequence)",
            WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
            "Plain Work",
            "The Sequence",
        ),
        (
            "Plain Work (2012)",
            WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
            "Plain Work",
            "2012",
        ),
        (
            "Plain Work ISBN 9780306406157",
            WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
            "Plain Work",
            "9780306406157",
        ),
        (
            "Plain Work ISBN: 9780306406157",
            WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
            "Plain Work",
            "9780306406157",
        ),
        (
            "Plain Work: A subtitle",
            WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
            "Plain Work",
            "A subtitle",
        ),
    ),
)
def test_consensus_election_grammar_returns_closed_reversible_matches(
    title: str,
    family: WorkTitleTransformationFamily,
    clean: str,
    component: str | None,
) -> None:
    assert ((family, clean, component),) == tuple(
        (value.family, value.clean_title, value.preserved_component)
        for value in work_title_election_matches(title, ())
    )


@pytest.mark.parametrize(
    "title",
    (
        "Plain Work ASIN B012345678",
        "Plain Work ISBN 9780306406158",
        "Plain Work 9780306406157",
        "Plain Work [Illustrated Edition]",
        "Plain Work [RETAIL]",
        "Plain Work - A subtitle",
        "Plain Work: First: Second",
    ),
)
def test_consensus_election_grammar_abstains_outside_or_stacked_patterns(
    title: str,
) -> None:
    assert () == work_title_election_matches(title, ())


def test_consensus_election_grammar_returns_each_stacked_pattern() -> None:
    assert 2 == len(work_title_election_matches("Plain Work (2) [retail]", ()))


def test_closed_rule_table_has_one_independent_row_per_family() -> None:
    assert tuple(rule.family for rule in WORK_TITLE_TRANSFORMATION_RULES) == tuple(
        WorkTitleTransformationFamily
    )
    assert all(rule.query_enabled for rule in WORK_TITLE_TRANSFORMATION_RULES)
    assert [] == [
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
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            ("Ada Example",),
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            "Cafe",
        ),
        (
            "A\N{COMBINING VERTICAL LINE ABOVE}",
            ("Ada Example",),
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            "A",
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


def test_latin_combining_marks_fold_is_bounded_and_records_exact_applications() -> None:
    decision = admit_provider_work_identity(
        local_title="Cafe\u0301",
        local_authors=("Jose\u0301", "Ana"),
        provider_title="Cafe",
        provider_authors=("Jose", "Ana"),
        provider_work_identity="work-1",
    )

    assert decision.admitted
    assert decision.transformation is not None
    assert decision.transformation.production == "latin_combining_marks"
    assert decision.transformation.applications == (
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.AUTHORS,
            0,
        ),
    )
    dispatches = work_query_ladder("Cafe\u0301", ("Jose\u0301", "Ana"))
    assert len(dispatches) <= 3
    assert (
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
        in dispatches[-1].transformations
    )
    bounded = work_query_ladder(
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
        ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
    )
    assert [(query.title, query.authors) for query in bounded] == [
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
            ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
        ),
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
        ),
        ("Cafe", ("Jose",)),
    ]
    assert [
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS in query.transformations
        for query in bounded
    ] == [False, False, True]


def test_ampersand_then_latin_dispatch_is_canonical_and_replays() -> None:
    title = "Caf\N{LATIN SMALL LETTER E WITH ACUTE} & Tea"
    authors = ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",)
    query = work_query_ladder(title, authors)[-1]

    assert (query.title, query.authors, query.transformations) == (
        "Cafe and Tea",
        ("Jose",),
        (
            WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
        ),
    )
    dispatch = WorkDispatchLineage.from_query(query)
    assert work_dispatch_lineage_from_data(dispatch.to_data()) == dispatch
    replayed = replay_provider_work_identity_admission(
        local_title=title,
        local_authors=authors,
        provider_title="Cafe & Tea",
        provider_authors=("Jose",),
        provider_work_identity="work-1",
        dispatch_lineage=dispatch,
    )
    assert replayed.admitted
    assert replayed.transformation is not None
    assert replayed.transformation.production == "latin_combining_marks"

    with pytest.raises(ValueError):
        WorkDispatchLineage(
            WorkDispatchRung.PUNCTUATION_FOLDED,
            tuple(reversed(query.transformations)),
        )
    with pytest.raises(ValueError):
        WorkDispatchLineage(
            WorkDispatchRung.PUNCTUATION_FOLDED,
            (
                WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
                WorkTitleTransformationFamily.PUNCTUATION_AMPERSAND_FOLD,
                WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            ),
        )


def test_decomposed_mark_without_precomposition_has_exact_latin_dispatch() -> None:
    title = "A\N{COMBINING VERTICAL LINE ABOVE} Tale"
    authors = ("Jose",)
    query = work_query_ladder(title, authors)[-1]

    assert (query.title, query.authors, query.transformations) == (
        "A Tale",
        authors,
        (WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,),
    )
    replayed = replay_provider_work_identity_admission(
        local_title=title,
        local_authors=authors,
        provider_title="A Tale",
        provider_authors=authors,
        provider_work_identity="work-1",
        dispatch_lineage=WorkDispatchLineage.from_query(query),
    )
    assert replayed.admitted
    assert replayed.transformation is not None
    assert replayed.transformation.applications == (
        WorkTitleTransformationApplication(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            WorkTitleTransformationSide.LOCAL,
            WorkTitleTransformationField.TITLE,
            None,
        ),
    )


def test_exported_latin_transformation_leaves_inapplicable_text_unchanged() -> None:
    assert (
        apply_work_title_transformation(
            WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
            "Жозе",
            (),
        )
        == "Жозе"
    )


@pytest.mark.parametrize("value", ("\u0301Cafe", "Жозе", "ø", "ł", "æ", "ß"))
def test_latin_combining_marks_boundary_inputs_stay_unadmitted(value: str) -> None:
    assert not admit_provider_work_identity(
        local_title=value,
        local_authors=("Ada",),
        provider_title="Cafe",
        provider_authors=("Ada",),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize("unchanged", ("Ж", "\u0301Ada"))
def test_latin_family_requires_every_title_and_author_field_to_be_applicable(
    unchanged: str,
) -> None:
    assert not admit_provider_work_identity(
        local_title="Café",
        local_authors=(unchanged, "Ada"),
        provider_title="Cafe",
        provider_authors=(unchanged, "Ada"),
        provider_work_identity="work-1",
    ).admitted
    assert WorkTitleTransformationFamily.LATIN_COMBINING_MARKS not in {
        family
        for query in work_query_ladder("Café", (unchanged, "Ada"))
        for family in query.transformations
    }


@pytest.mark.parametrize("value", ("ø", "ł", "æ", "ß"))
def test_excluded_substitutions_are_applicable_unchanged_noops(value: str) -> None:
    assert WorkTitleTransformationFamily.LATIN_COMBINING_MARKS not in {
        family
        for query in work_query_ladder(value, ("Ada",))
        for family in query.transformations
    }


@pytest.mark.parametrize("separator", (" - ", "–", "—", " – ", " — ", ":"))
def test_author_prefix_without_a_remainder_underscore_stays_query_only(
    separator: str,
) -> None:
    decision = admit_provider_work_identity(
        local_title=f"Ada_Example{separator}Plain Work",
        local_authors=("Ada Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    )

    assert not decision.admitted


@pytest.mark.parametrize(
    "local_title",
    (
        "Ada_Example_-_Plain_Work",
        "Ada Example - Plain_Work",
        "Ada_Example:Plain_Work",
        "Ada_Example–Plain_Work",
    ),
    ids=("underscored", "spaced_prefix", "colon", "en_dash"),
)
def test_author_prefix_then_underscores_admits_as_one_closed_production(
    local_title: str,
) -> None:
    decision = admit_provider_work_identity(
        local_title=local_title,
        local_authors=("Ada Example",),
        provider_title="Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    )

    assert decision.admitted
    assert decision.transformation == WorkTitleTransformationLineage(
        (
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                WorkTitleTransformationSide.LOCAL,
                WorkTitleTransformationField.TITLE,
                None,
            ),
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                WorkTitleTransformationSide.LOCAL,
                WorkTitleTransformationField.TITLE,
                None,
            ),
        )
    )
    assert decision.transformation.production == "author_prefix_then_underscores"


def test_first_author_prefix_admits_only_with_exact_ordered_authors() -> None:
    authors = ("Ada Example", "Bea Example")

    assert admit_provider_work_identity(
        local_title="Ada_Example_-_Plain_Work",
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=authors,
        provider_work_identity="work-1",
    ).admitted
    assert not admit_provider_work_identity(
        local_title="Ada_Example_-_Plain_Work",
        local_authors=authors,
        provider_title="Plain Work",
        provider_authors=("Bea Example", "Ada Example"),
        provider_work_identity="work-1",
    ).admitted


@pytest.mark.parametrize(
    ("local_title", "provider_title"),
    (
        ("Plain_Work", "Plain Work"),
        ("Ada_Example - Plain Work", "Plain Work"),
        ("Ada_Example ~ Plain_Work", "Plain Work"),
        ("Ada_Example_Plain_Work", "Plain Work"),
        ("Ada_Example_-_Plain_Work", "Plain Work Two"),
        ("Ada_Example_-_Translated_Work", "Translated Work"),
    ),
    ids=(
        "underscores_only",
        "prefix_only",
        "separator_outside_the_closed_set",
        "no_separator",
        "remainder_collision",
        "variant_marker",
    ),
)
def test_compound_admission_negatives_reject(
    local_title: str, provider_title: str
) -> None:
    assert not admit_provider_work_identity(
        local_title=local_title,
        local_authors=("Ada Example",),
        provider_title=provider_title,
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    ).admitted


def test_constituent_families_stay_query_only_in_the_rule_table() -> None:
    admitting = {
        family
        for production in WORK_TITLE_COMPOUND_PRODUCTIONS
        if production.admission_enabled
        for family in production.families
    }

    assert admitting == {
        WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
        WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,
    }
    assert all(not rule.admission_enabled for rule in WORK_TITLE_TRANSFORMATION_RULES)
    assert WorkTitleCompoundProduction(
        "single_family", (WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,), True
    ).families == (WorkTitleTransformationFamily.LATIN_COMBINING_MARKS,)
    with pytest.raises(ValueError):
        WorkTitleTransformationLineage(
            (
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                    WorkTitleTransformationSide.LOCAL,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
            )
        )


def test_primary_author_filename_prefix_is_query_only_provider_side() -> None:
    decision = admit_provider_work_identity(
        local_title="Plain Work",
        local_authors=("Ada Example",),
        provider_title="Ada_Example_-_Plain Work",
        provider_authors=("Ada Example",),
        provider_work_identity="work-1",
    )

    assert not decision.admitted


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
        provider_title="Plain_Work",
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
    local_title = "Plain Work"
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
    lineage = WorkTitleTransformationLineage(
        (
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                WorkTitleTransformationSide.LOCAL,
                WorkTitleTransformationField.TITLE,
                None,
            ),
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                WorkTitleTransformationSide.LOCAL,
                WorkTitleTransformationField.TITLE,
                None,
            ),
        )
    )
    assert work_title_transformation_lineage_from_data(lineage.to_data()) == lineage
    with pytest.raises(ValueError):
        work_title_transformation_lineage_from_data(
            {"family": "bracket_source_tag", "side": "local"}
        )
    with pytest.raises(ValueError):
        work_title_transformation_lineage_from_data(
            {**lineage.to_data(), "production": "other_production"}
        )
    with pytest.raises(ValueError):
        work_title_transformation_lineage_from_data(
            {**lineage.to_data(), "side": "provider"}
        )
    with pytest.raises(ValueError):
        work_title_transformation_lineage_from_data(
            {
                **lineage.to_data(),
                "families": ["filename_underscores", "filename_author_title_prefix"],
            }
        )
    with pytest.raises(ValueError):
        WorkTitleTransformationLineage(
            (
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                    WorkTitleTransformationSide.PROVIDER,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                    WorkTitleTransformationSide.PROVIDER,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
            )
        )


@pytest.mark.parametrize("applications", (("malformed",), ({},)))
def test_lineage_members_are_exact_typed_before_hashing_or_sorting(
    applications: tuple[object, ...],
) -> None:
    with pytest.raises(
        ValueError, match="work title transformation lineage is invalid"
    ):
        WorkTitleTransformationLineage(applications)  # type: ignore[arg-type]  # reason: exercise runtime rejection of malformed retained values


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


def test_persisted_dispatch_lineage_round_trips_and_non_null_transform_rejects() -> (
    None
):
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
    )
    data = lane.to_data()

    assert _provider_lane_from_data(data) == lane
    admitted = ProviderLaneObservation(
        "lane_" + ("a" * 64),
        ProviderQueryKind.WORK,
        ProviderStatus.CANDIDATES,
        ("local_" + ("b" * 64),),
        candidates=(
            ProviderLaneCandidate(
                "lane_candidate_" + ("c" * 64),
                "edition-1",
                "work-1",
            ),
        ),
        dispatch_lineage=WorkDispatchLineage(
            WorkDispatchRung.NORMALIZED,
            (
                WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
            ),
        ),
        transformation_lineage=WorkTitleTransformationLineage(
            (
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX,
                    WorkTitleTransformationSide.LOCAL,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
                WorkTitleTransformationApplication(
                    WorkTitleTransformationFamily.FILENAME_UNDERSCORES,
                    WorkTitleTransformationSide.LOCAL,
                    WorkTitleTransformationField.TITLE,
                    None,
                ),
            )
        ),
    )

    assert _provider_lane_from_data(admitted.to_data()) == admitted
    with pytest.raises(ValueError):
        _provider_lane_from_data(
            {
                **data,
                "transformation_lineage": {
                    "family": "filename_author_title_prefix",
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
