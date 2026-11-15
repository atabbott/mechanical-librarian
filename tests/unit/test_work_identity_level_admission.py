"""ADR-0096's level-admission seam for provider records (#1355, S5 and S6).

`provider_candidate_level_admission` is the one place a provider record is
admitted by title and creator at a configured Book matching level. The
records here are duck-typed: the seam reads only the members they carry.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    IdentifierRelation,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ReportValueError,
    WorkIdentityContradiction,
    creator_list_relation,
    provider_candidate_work_identity_contradiction,
)
from mechanical_librarian.domain.proposal_set import (
    LevelAdmissionRecord,
    level_contradiction,
    provider_candidate_level_admission,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.unit.test_cover_identity_listed_edition import _claim, _target

_EXACT = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
_WIDE = WorkIdentityLevel.TITLE_AND_CREATOR
_WORK = "work-0000000001"
_OTHER_WORK = "work-0000000002"


def _record(
    title: str | None = "Synthetic Title",
    creators: tuple[str, ...] = ("R. F. Example",),
    *,
    identity: str = "record-1",
    work: str = _WORK,
    extra: tuple[SimpleNamespace, ...] = (),
    evidence: tuple[object, ...] = (),
    creator_links: tuple[object, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        identity=identity,
        work_identity=work,
        claims=(
            *(() if title is None else (_claim(ClaimKind.TITLE, title),)),
            *(
                _claim(
                    ClaimKind.CREATOR, value, raw_order=index + 1, links=creator_links
                )
                for index, value in enumerate(creators)
            ),
            *extra,
        ),
        evidence=evidence,
    )


def _complete_record(
    creators: tuple[str, ...],
    *,
    title: str = "Synthetic Title",
    identity: str = "record-1",
    work: str = _WORK,
    roles: tuple[str | None, ...] | None = None,
    translator: bool = False,
) -> ProviderCandidate:
    """A real record, which the classifier alone requires.

    `roles` gives each creator's role; a `None` role is a neutral creator
    with no identity binding, so the record states no classifier-complete
    claim set.
    """

    def claim(kind: ClaimKind, value: str, **values: Any) -> ProviderClaim:
        return ProviderClaim.create(
            provider="hardcover",
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            **values,
        )

    return ProviderCandidate(
        identity,
        work,
        (
            claim(ClaimKind.TITLE, title),
            *(
                claim(ClaimKind.CREATOR, value, role=role, raw_order=index + 1)
                for index, (value, role) in enumerate(
                    zip(
                        creators,
                        ("author",) * len(creators) if roles is None else roles,
                        strict=True,
                    )
                )
            ),
            *(
                (
                    claim(
                        ClaimKind.CONTRIBUTOR,
                        "Tess Translator",
                        role="translator",
                        raw_order=len(creators) + 1,
                    ),
                )
                if translator
                else ()
            ),
        ),
    )


def _lane(*records: SimpleNamespace | ProviderCandidate) -> SimpleNamespace:
    return SimpleNamespace(
        kind=ProviderQueryKind.WORK,
        candidates=tuple(
            SimpleNamespace(
                edition_identity=value.identity, work_identity=value.work_identity
            )
            for value in records
        ),
    )


def _report(
    *records: SimpleNamespace | ProviderCandidate,
    title: str = "Synthetic Title",
    authors: tuple[str, ...] | None = ("R. F. Example",),
    lanes: tuple[SimpleNamespace, ...] = (),
    identifier_evidence: tuple[object, ...] = (),
) -> tuple[MetadataReport, ProviderObservation]:
    provider = SimpleNamespace(
        provider="hardcover",
        candidates=records,
        query_kind=ProviderQueryKind.WORK,
        lanes=lanes,
        identity_refused_candidates=(),
        identity_refused_truncated=0,
    )
    report = SimpleNamespace(
        audio=None,
        target=_target(title, authors),
        providers=(provider,),
        identifier_evidence=identifier_evidence,
        local_claims=(),
    )
    return cast("MetadataReport", report), cast("ProviderObservation", provider)


def _admit(
    report: MetadataReport,
    provider: ProviderObservation,
    record: SimpleNamespace,
    level: WorkIdentityLevel = _WIDE,
) -> LevelAdmissionRecord | None:
    return provider_candidate_level_admission(report, provider, record, level)  # type: ignore[arg-type]


def _level(value: LevelAdmissionRecord | None) -> WorkIdentityLevel | None:
    assert value is not None
    return None if value.admission is None else value.admission.level


# --- 3. The seam -------------------------------------------------------------


def test_the_seam_is_silent_at_identifier() -> None:
    record = _record()
    report, provider = _report(record)
    assert _admit(report, provider, record, WorkIdentityLevel.IDENTIFIER) is None


def test_the_same_title_and_every_author_meets_the_exact_level() -> None:
    record = _record()
    report, provider = _report(record)
    for level in (_EXACT, _WIDE):
        value = _admit(report, provider, record, level)
        assert value == LevelAdmissionRecord(value.admission if value else None, None)
        assert _level(value) is _EXACT
        assert value is not None and value.creator_comparison == "closed"


def test_the_same_title_and_any_author_meets_only_the_wide_level() -> None:
    record = _record(creators=("R. F. Example",))
    report, provider = _report(record, authors=("R. F. Example", "Bea Example"))
    assert _level(_admit(report, provider, record, _WIDE)) is _WIDE
    assert _admit(report, provider, record, _EXACT) == LevelAdmissionRecord(
        None, "refused"
    )


def test_respelled_initials_are_admitted_through_the_fold() -> None:
    record = _record(creators=("R.F. Example",))
    report, provider = _report(record, authors=("R. F. Example",))
    value = _admit(report, provider, record, _EXACT)
    assert _level(value) is _EXACT
    assert value is not None and value.creator_comparison == "initials_fold"


@pytest.mark.parametrize(
    ("record", "title", "authors"),
    (
        (_record(creators=()), "Synthetic Title", ("R. F. Example",)),
        (_record(), "Synthetic Title", None),
        # Only the stored AUTHORS reach the predicate, never a query spelling.
        (_record(creators=("Example, R. F.",)), "Synthetic Title", ("R. F. Example",)),
        (
            _record("Synthetic Title: The Complete Trilogy"),
            "Synthetic Title",
            ("R. F. Example",),
        ),
        (
            _record("Synthetic Title"),
            "Synthetic Title: The Complete Trilogy",
            ("R. F. Example",),
        ),
        (
            _record(creators=("Different Author",)),
            "Synthetic Title",
            ("R. F. Example",),
        ),
    ),
    ids=(
        "no_record_creator",
        "no_target_author",
        "query_only_inverted_form",
        "omnibus_record",
        "omnibus_target",
        "different_author",
    ),
)
def test_each_refusal_admits_nothing_at_any_level(
    record: SimpleNamespace, title: str, authors: tuple[str, ...] | None
) -> None:
    report, provider = _report(record, title=title, authors=authors)
    for level in (_EXACT, _WIDE):
        assert _admit(report, provider, record, level) == LevelAdmissionRecord(
            None, "refused"
        )


def test_the_seam_refuses_a_level_that_is_not_a_member() -> None:
    record = _record()
    report, provider = _report(record)
    with pytest.raises(ValueError, match="work identity level is invalid"):
        provider_candidate_level_admission(
            report,
            provider,
            record,  # type: ignore[arg-type]
            "title_and_creator",  # type: ignore[arg-type]
        )


# --- 4. The set rule ---------------------------------------------------------


def test_two_works_meeting_the_level_in_one_set_are_ambiguous() -> None:
    first = _record(identity="record-1", work=_WORK)
    second = _record(identity="record-2", work=_OTHER_WORK)
    report, provider = _report(first, second, lanes=(_lane(first, second),))
    for record in (first, second):
        assert _admit(report, provider, record) == LevelAdmissionRecord(
            None, "ambiguous"
        )


def test_the_sibling_of_a_vetoed_work_is_refused() -> None:
    translated = _record(
        identity="record-2",
        extra=(
            SimpleNamespace(
                kind=ClaimKind.CONTRIBUTOR,
                value="Tess Translator",
                raw_order=9,
                scope=ClaimScope.WORK,
                state=_claim(ClaimKind.TITLE, "x").state,
                links=(),
                role="translator",
            ),
        ),
    )
    sibling = _record(identity="record-1")
    report, provider = _report(sibling, translated, lanes=(_lane(sibling, translated),))
    assert _admit(report, provider, sibling) == LevelAdmissionRecord(None, "refused")


def test_a_single_work_set_is_admitted() -> None:
    first = _record(identity="record-1")
    edition = _record(identity="record-2")
    unrelated = _record(
        "Another Title", ("Other Author",), identity="record-3", work=_OTHER_WORK
    )
    report, provider = _report(
        first, edition, unrelated, lanes=(_lane(first, edition, unrelated),)
    )
    assert _level(_admit(report, provider, first)) is _EXACT


def _refused_record(
    identity: str, work: str, *, translator: bool = False
) -> ProviderCandidate:
    """A real retained refused record (#1355): the level classifies its own."""

    def claim(kind: ClaimKind, value: str, **values: Any) -> ProviderClaim:
        return ProviderClaim.create(
            provider="hardcover",
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            **values,
        )

    return ProviderCandidate(
        identity,
        work,
        (
            claim(ClaimKind.TITLE, "Synthetic Title"),
            claim(ClaimKind.CREATOR, "R.F. Example", role="author", raw_order=1),
            *(
                (
                    claim(
                        ClaimKind.CONTRIBUTOR,
                        "Tess Translator",
                        role="translator",
                        raw_order=2,
                    ),
                )
                if translator
                else ()
            ),
        ),
    )


def _refused_set(
    *records: ProviderCandidate,
    authors: tuple[str, ...] = ("R. F. Example",),
) -> tuple[MetadataReport, ProviderObservation]:
    """An observation whose lanes refused every record, so it keeps them."""
    provider = SimpleNamespace(
        provider="hardcover",
        authority="hardcover",
        candidates=(),
        query_kind=ProviderQueryKind.WORK,
        lanes=(),
        identity_refused_candidates=records,
        identity_refused_truncated=0,
    )
    report = SimpleNamespace(
        audio=None,
        target=_target("Synthetic Title", authors),
        providers=(provider,),
        identifier_evidence=(),
        local_claims=(),
    )
    return cast("MetadataReport", report), cast("ProviderObservation", provider)


def test_level_refuses_two_works_same_title_same_author_in_refused_set() -> None:
    first = _refused_record("record-1", _WORK)
    second = _refused_record("record-2", _OTHER_WORK)
    report, provider = _refused_set(first, second)
    alone, lone = _refused_set(first)

    assert _level(provider_candidate_level_admission(alone, lone, first, _EXACT)) is (
        _EXACT
    )
    for record in (first, second):
        assert provider_candidate_level_admission(
            report, provider, record, _EXACT
        ) == LevelAdmissionRecord(None, "ambiguous")


def test_level_refuses_sibling_of_vetoed_work_identity() -> None:
    sibling = _refused_record("record-1", _WORK)
    translated = _refused_record("record-2", _WORK, translator=True)
    report, provider = _refused_set(sibling, translated)
    assert provider_candidate_level_admission(
        report, provider, sibling, _WIDE
    ) == LevelAdmissionRecord(None, "refused")


# --- 5. Contradiction --------------------------------------------------------


def _identifier(relation: IdentifierRelation, identity: str = "record-1") -> object:
    return SimpleNamespace(
        provider="hardcover",
        candidate_identity=identity,
        work_identity=_WORK,
        relation=relation,
        reference=f"identifier-{relation.value}",
    )


def _isbn_claim(value: str, *, scope: ClaimScope) -> SimpleNamespace:
    claim = _claim(ClaimKind.ISBN, value, scope=scope)
    claim.applicability = "ebook"
    claim.canonical = value
    claim.checksum = "valid_isbn_13"
    return claim


@pytest.mark.parametrize(
    ("record", "evidence", "contradicted"),
    (
        (_record(), (), False),
        (_record(), (_identifier(IdentifierRelation.UNRELATED),), True),
        (
            _record(
                evidence=(
                    SimpleNamespace(
                        kind=ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                        links=(),
                    ),
                )
            ),
            (),
            True,
        ),
        (
            _record(creator_links=(SimpleNamespace(outcome=Comparison.DISAGREEMENT),)),
            (),
            True,
        ),
        (_complete_record(("Different Author",)), (), True),
        # Another edition states another ISBN; that alone contradicts nothing.
        (
            _record(extra=(_isbn_claim("9780306406157", scope=ClaimScope.EDITION),)),
            (_identifier(IdentifierRelation.ACCEPTED),),
            False,
        ),
        (_record(creators=("R.F. Example",)), (), False),
    ),
    ids=(
        "none",
        "unrelated_identifier",
        "edition_work_contradiction",
        "creator_disagreement",
        "explicit_classifier_contradiction",
        "differing_isbn_alone",
        "folded_initials",
    ),
)
def test_level_contradiction_matrix(
    record: Any, evidence: tuple[object, ...], contradicted: bool
) -> None:
    # Every case is single-author, so both wider levels compose alike.
    report, provider = _report(record, identifier_evidence=evidence)
    for level in (_EXACT, _WIDE):
        assert level_contradiction(report, provider, record, level) is contradicted


def _contributor(role: str, value: str) -> SimpleNamespace:
    contributor = _claim(ClaimKind.CONTRIBUTOR, value, raw_order=9)
    contributor.role = role
    return contributor


def _recording_report(record: SimpleNamespace) -> tuple[MetadataReport, Any]:
    """An audiobook report whose library declares English only."""
    report, provider = _report(record)
    value = cast("SimpleNamespace", report)
    value.audio = SimpleNamespace(sidecars=())
    value.library_languages = ("en",)
    return report, provider


def _manifestation_report(record: SimpleNamespace) -> tuple[MetadataReport, Any]:
    report, provider = _report(
        record, identifier_evidence=(_identifier(IdentifierRelation.ACCEPTED),)
    )
    cast("SimpleNamespace", report).local_claims = (
        _isbn_claim("9780306406157", scope=ClaimScope.EDITION),
    )
    return report, provider


_CONTRADICTIONS: tuple[tuple[str, SimpleNamespace, Any, bool], ...] = (
    (
        "unrelated_identifier",
        _record(),
        lambda record: _report(
            record, identifier_evidence=(_identifier(IdentifierRelation.UNRELATED),)
        ),
        True,
    ),
    (
        "edition_work_contradiction",
        _record(
            evidence=(
                SimpleNamespace(
                    kind=ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
                    links=(),
                ),
            )
        ),
        _report,
        True,
    ),
    (
        "manifestation_publication_incompatible",
        _record(
            extra=(_isbn_claim("9780306406157", scope=ClaimScope.EDITION),),
            evidence=(
                SimpleNamespace(
                    kind=ProviderEvidenceKind.EDITION_WORK_RELATION,
                    scope=ClaimScope.EDITION,
                    value=_OTHER_WORK,
                    links=(),
                ),
            ),
        ),
        _manifestation_report,
        True,
    ),
    (
        "variant_marker",
        _record(extra=(_claim(ClaimKind.SUBTITLE, "A Variant Text"),)),
        _report,
        False,
    ),
    (
        "translation",
        _record(extra=(_contributor("translator", "Tess Translator"),)),
        _report,
        False,
    ),
    (
        "abridgement",
        _record(extra=(_contributor("abridger", "Abe Abridger"),)),
        _report,
        False,
    ),
    (
        "recording_variant_conflict",
        _record(extra=(_claim(ClaimKind.LANGUAGE, "fr"),)),
        _recording_report,
        True,
    ),
    (
        "creator_disagreement",
        _record(creator_links=(SimpleNamespace(outcome=Comparison.DISAGREEMENT),)),
        _report,
        True,
    ),
)


@pytest.mark.parametrize(
    ("record", "build", "composed"),
    [case[1:] for case in _CONTRADICTIONS],
    ids=[case[0] for case in _CONTRADICTIONS],
)
def test_contradiction_precedes_level_admission(
    record: SimpleNamespace, build: Any, composed: bool
) -> None:
    """Each contradiction, and each own-record veto (a variant, translation or
    abridgement), refuses the record at every wider level although its title
    and author meet the predicate (S6)."""
    report, provider = build(record)
    # Without its contradiction the same record meets the exact level
    # (`test_the_same_title_and_every_author_meets_the_exact_level`); the
    # vetoes are the set rule's, the rest `level_contradiction`'s.
    for level in (_EXACT, _WIDE):
        assert level_contradiction(report, provider, record, level) is composed  # type: ignore[arg-type]
        assert _admit(report, provider, record, level) == LevelAdmissionRecord(
            None, "refused"
        )


def test_a_manifestation_sharing_the_isbn_family_that_contradicts_is_refused() -> None:
    local = _isbn_claim("9780306406157", scope=ClaimScope.EDITION)
    record = _record(
        extra=(_isbn_claim("9780306406157", scope=ClaimScope.EDITION),),
        evidence=(
            SimpleNamespace(
                kind=ProviderEvidenceKind.EDITION_WORK_RELATION,
                scope=ClaimScope.EDITION,
                value=_OTHER_WORK,
                links=(),
            ),
        ),
    )
    report, provider = _report(
        record, identifier_evidence=(_identifier(IdentifierRelation.ACCEPTED),)
    )
    cast("SimpleNamespace", report).local_claims = (local,)
    for level in (_EXACT, _WIDE):
        assert level_contradiction(report, provider, record, level) is True  # type: ignore[arg-type]
    assert _admit(report, provider, record) == LevelAdmissionRecord(None, "refused")


def test_the_classifier_reads_the_fold_only_when_asked() -> None:
    record = _complete_record(("R.F. Example",))
    assert (
        provider_candidate_work_identity_contradiction(
            record,
            "Synthetic Title",
            ("R. F. Example",),
        )
        is WorkIdentityContradiction.EXPLICIT
    )
    assert (
        provider_candidate_work_identity_contradiction(
            record,
            "Synthetic Title",
            ("R. F. Example",),
            fold_initials=True,
        )
        is WorkIdentityContradiction.UNAVAILABLE
    )


def _classifier_callers() -> list[tuple[str, int, bool]]:
    root = Path(__file__).parents[2] / "src" / "mechanical_librarian"
    calls = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name | ast.Attribute)
                and (
                    node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                )
                == "provider_candidate_work_identity_contradiction"
            ):
                folds = any(
                    keyword.arg in {"fold_initials", "partial_authors"}
                    for keyword in node.keywords
                )
                calls.append((str(path.relative_to(root)), node.lineno, folds))
    return calls


@pytest.mark.parametrize(
    "caller",
    [
        caller
        for caller in _classifier_callers()
        if caller[0] != "domain/proposal_set.py" or not caller[2]
    ],
    ids=lambda caller: f"{caller[0]}:{caller[1]}",
)
def test_every_live_classifier_caller_keeps_the_explicit_default(
    caller: tuple[str, int, bool],
) -> None:
    """Guard: only the level path reads the fold (#1780 S9) or partial author
    lists (#1355)."""
    assert caller[2] is False


# --- 6. Partial author lists at `title_and_creator` (#1355) -----------------

_FIRST = "R. F. Example"
_SECOND = "Bea Example"


@pytest.mark.parametrize(
    ("target", "record", "fold", "relation"),
    (
        ((_FIRST, _SECOND), (_FIRST, _SECOND), True, "same"),
        ((_FIRST, _SECOND), (_SECOND, _FIRST), True, "same"),
        # A middle initial on one side only: the replay's four-record class.
        (("John Q. Public", _SECOND), ("John Public", _SECOND), True, "same"),
        ((_FIRST, _SECOND), (_FIRST,), True, "nested"),
        ((_FIRST,), (_FIRST, _SECOND), True, "nested"),
        (("J. R. Example",), ("J.R. Example", "Co Author"), True, "nested"),
        (("J. R. Example",), ("J.R. Example", "Co Author"), False, "disjoint"),
        ((_FIRST, _SECOND), (_FIRST, "Cy Example"), True, "overlap"),
        ((_FIRST,), ("Other Author",), True, "disjoint"),
        # The collapse guard: a fold merging two of one side's creators is not
        # tried, so no folded pairing is made.
        (("A. B. Name",), ("A B Name",), True, "same"),
        (("A. B. Name", "A.B. Name"), ("A B Name",), True, "disjoint"),
        # Duplicate keys count once.
        ((_FIRST, "r. f. example", _SECOND), (_FIRST,), True, "nested"),
        ((_FIRST, _FIRST.upper()), (_FIRST,), True, "same"),
    ),
    ids=(
        "equal",
        "reordered",
        "middle_initial_on_one_side",
        "subset",
        "superset",
        "folded_nested",
        "unfolded_disjoint",
        "overlap",
        "disjoint",
        "fold_without_collapse",
        "collapse_guard",
        "duplicate_keys_nested",
        "duplicate_keys_same",
    ),
)
def test_creator_list_relation(
    target: tuple[str, ...], record: tuple[str, ...], fold: bool, relation: str
) -> None:
    assert creator_list_relation(target, record, fold_initials=fold) == relation


@pytest.mark.parametrize(
    ("target", "record", "fold"),
    (
        ([_FIRST], (_FIRST,), True),
        ((_FIRST,), (), True),
        ((), (_FIRST,), True),
        ((_FIRST, ""), (_FIRST,), True),
        ((_FIRST,), (1,), True),
        ((_FIRST,), (_FIRST,), 1),
    ),
    ids=(
        "list_target",
        "empty_record",
        "empty_target",
        "empty_value",
        "non_text_value",
        "non_bool_fold",
    ),
)
def test_creator_list_relation_refuses_bad_input(
    target: Any, record: Any, fold: Any
) -> None:
    with pytest.raises(ReportValueError, match="creator list relation input"):
        creator_list_relation(target, record, fold_initials=fold)


def _classify(
    record: ProviderCandidate, authors: tuple[str, ...], **values: Any
) -> Any:
    return provider_candidate_work_identity_contradiction(
        record, "Synthetic Title", authors, **values
    )


def test_the_classifier_reads_partial_authors_only_when_asked() -> None:
    explicit = WorkIdentityContradiction.EXPLICIT
    unavailable = WorkIdentityContradiction.UNAVAILABLE
    subset = (_complete_record((_FIRST,)), (_FIRST, _SECOND))
    superset = (_complete_record((_FIRST, _SECOND)), (_FIRST,))
    for record, authors in (subset, superset):
        assert _classify(record, authors) is explicit
        assert _classify(record, authors, fold_initials=True) is explicit
        assert _classify(record, authors, partial_authors=True) is unavailable
        assert (
            _classify(record, authors, fold_initials=True, partial_authors=True)
            is unavailable
        )
    # The record's own list still meets every other check.
    retitled = _complete_record((_FIRST,), title="Another Title")
    assert (
        _classify(retitled, (_FIRST, _SECOND), fold_initials=True, partial_authors=True)
        is explicit
    )
    overlapping = _complete_record((_FIRST, "Cy Example"))
    assert (
        _classify(
            overlapping, (_FIRST, _SECOND), fold_initials=True, partial_authors=True
        )
        is explicit
    )
    # Equal lists keep today's verdict.
    equal = _complete_record((_FIRST, _SECOND))
    assert (
        _classify(equal, (_FIRST, _SECOND), partial_authors=True)
        is WorkIdentityContradiction.NOT_CONTRADICTED
    )
    with pytest.raises(ReportValueError, match="contradiction input is invalid"):
        _classify(equal, (_FIRST,), partial_authors=1)


@pytest.mark.parametrize(
    ("target", "creators", "wide", "comparison"),
    (
        ((_FIRST, _SECOND), (_FIRST,), _WIDE, "closed"),
        ((_FIRST,), (_FIRST, _SECOND), _WIDE, "closed"),
        (("J. R. Example",), ("J.R. Example", "Co Author"), _WIDE, "initials_fold"),
        ((_FIRST, _SECOND), (_FIRST, _SECOND), _EXACT, "closed"),
    ),
    ids=("subset", "superset", "folded", "equal"),
)
def test_a_nested_author_list_meets_only_the_wide_level(
    target: tuple[str, ...],
    creators: tuple[str, ...],
    wide: WorkIdentityLevel,
    comparison: str,
) -> None:
    record = _complete_record(creators)
    report, provider = _report(record, authors=target)
    value = provider_candidate_level_admission(report, provider, record, _WIDE)
    assert _level(value) is wide
    assert value is not None and value.creator_comparison == comparison
    exact = provider_candidate_level_admission(report, provider, record, _EXACT)
    if wide is _EXACT:
        assert _level(exact) is _EXACT
    else:
        assert exact == LevelAdmissionRecord(None, "refused")
    assert (
        provider_candidate_level_admission(
            report, provider, record, WorkIdentityLevel.IDENTIFIER
        )
        is None
    )


_OVERLAPPING = (_FIRST, "Cy Example")


@pytest.mark.parametrize(
    ("record", "complete"),
    (
        (_complete_record(_OVERLAPPING), True),
        (_complete_record(_OVERLAPPING, roles=(None, None)), False),
        (_complete_record(_OVERLAPPING, roles=("author", None)), False),
        (_record(creators=_OVERLAPPING), False),
    ),
    ids=("complete", "neutral_unbound", "mixed_typed_and_neutral", "duck"),
)
def test_overlapping_author_lists_are_refused_at_every_level(
    record: Any, complete: bool
) -> None:
    report, provider = _report(record, authors=(_FIRST, _SECOND))
    for level in (_EXACT, _WIDE):
        assert provider_candidate_level_admission(
            report, provider, record, level
        ) == LevelAdmissionRecord(None, "refused")
    assert level_contradiction(report, provider, record, _WIDE) is True
    # The exact composition is today's: only the classifier refuses there.
    assert level_contradiction(report, provider, record, _EXACT) is complete


def test_two_nested_works_in_one_set_are_ambiguous() -> None:
    first = _complete_record((_FIRST,), identity="record-1", work=_WORK)
    second = _complete_record((_FIRST,), identity="record-2", work=_OTHER_WORK)
    report, provider = _report(
        first, second, authors=(_FIRST, _SECOND), lanes=(_lane(first, second),)
    )
    for record in (first, second):
        assert provider_candidate_level_admission(
            report, provider, record, _WIDE
        ) == LevelAdmissionRecord(None, "ambiguous")


def test_a_nested_record_with_a_translator_is_refused() -> None:
    translated = _complete_record((_FIRST,), identity="record-2", translator=True)
    sibling = _complete_record((_FIRST,), identity="record-1")
    authors = (_FIRST, _SECOND)
    alone, lone = _report(translated, authors=authors)
    assert provider_candidate_level_admission(
        alone, lone, translated, _WIDE
    ) == LevelAdmissionRecord(None, "refused")
    report, provider = _report(
        sibling, translated, authors=authors, lanes=(_lane(sibling, translated),)
    )
    for record in (sibling, translated):
        assert provider_candidate_level_admission(
            report, provider, record, _WIDE
        ) == LevelAdmissionRecord(None, "refused")


def test_a_nested_refused_answer_is_admitted_at_the_wide_level() -> None:
    authors = (_FIRST, _SECOND)
    record = _refused_record("record-1", _WORK)
    report, provider = _refused_set(record, authors=authors)
    value = provider_candidate_level_admission(report, provider, record, _WIDE)
    assert _level(value) is _WIDE
    assert value is not None and value.creator_comparison == "initials_fold"
    assert provider_candidate_level_admission(
        report, provider, record, _EXACT
    ) == LevelAdmissionRecord(None, "refused")
    other = _refused_record("record-2", _OTHER_WORK)
    both, observation = _refused_set(record, other, authors=authors)
    for value_record in (record, other):
        assert provider_candidate_level_admission(
            both, observation, value_record, _WIDE
        ) == LevelAdmissionRecord(None, "ambiguous")
    cast("SimpleNamespace", provider).identity_refused_truncated = 1
    assert provider_candidate_level_admission(
        report, provider, record, _WIDE
    ) == LevelAdmissionRecord(None, "truncated")


@pytest.mark.parametrize(
    "level",
    (WorkIdentityLevel.IDENTIFIER, "title_and_creator"),
    ids=("identifier", "non_member"),
)
def test_level_contradiction_refuses_identifier(level: Any) -> None:
    record = _record()
    report, provider = _report(record)
    with pytest.raises(ReportValueError, match="work identity level is invalid"):
        level_contradiction(report, provider, record, level)  # type: ignore[arg-type]
