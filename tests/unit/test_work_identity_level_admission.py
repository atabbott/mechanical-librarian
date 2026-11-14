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
    WorkIdentityContradiction,
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


def _complete_record(creators: tuple[str, ...]) -> ProviderCandidate:
    """A real record, which the classifier alone requires."""

    def claim(kind: ClaimKind, value: str, **values: Any) -> ProviderClaim:
        return ProviderClaim.create(
            provider="hardcover",
            candidate="record-1",
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            **values,
        )

    return ProviderCandidate(
        "record-1",
        _WORK,
        (
            claim(ClaimKind.TITLE, "Synthetic Title"),
            *(
                claim(ClaimKind.CREATOR, value, role="author", raw_order=index + 1)
                for index, value in enumerate(creators)
            ),
        ),
    )


def _lane(*records: SimpleNamespace) -> SimpleNamespace:
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
    *records: SimpleNamespace,
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
    report, provider = _report(record, identifier_evidence=evidence)
    assert level_contradiction(report, provider, record) is contradicted


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
    assert level_contradiction(report, provider, record) is True  # type: ignore[arg-type]
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
                folds = any(keyword.arg == "fold_initials" for keyword in node.keywords)
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
    """Guard: only the level path reads the fold (#1780 S9)."""
    assert caller[2] is False
