"""The draft preview's title, contributor, date, publisher, language and cover rows."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.application import metadata_policy as policy_application
from mechanical_librarian.application.metadata_policy import (
    PolicyFieldComparison,
    PolicyPreviewCapture,
    PolicyPreviewCover,
    PolicyPreviewLibraryCapture,
    preview_policy,
)
from mechanical_librarian.domain.automatic_decision import (
    audiobook_title_subtitle_bundle,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from tests.support.audiobook_reports import (
    corroborated_audiobook_report,
    synthetic_audiobook_bibliographic_report,
    synthetic_audiobook_original_date_report,
)
from tests.support.cover_producers import adapter_cover_value
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.language_reports import language_report
from tests.support.proposal_evidence import _fields, _report

BUILTIN = MetadataPolicyProfile.builtin()


def _draft(
    options: dict[str, str] | None = None,
    destinations: dict[str, str] | None = None,
    active: MetadataPolicyProfile = BUILTIN,
) -> MetadataPolicyProfile:
    return MetadataPolicyProfile.create(
        "Draft",
        dict(active.options) | (options or {}),
        dict(active.destinations) | (destinations or {}),
    )


class _Capture:
    """The capture seam over in-memory reports, bound to the active policy."""

    def __init__(
        self,
        reports: tuple[MetadataReport, ...],
        active: MetadataPolicyProfile = BUILTIN,
        covers: tuple[PolicyPreviewCover | None, ...] = (),
    ) -> None:
        self.reports, self.active, self.covers = reports, active, covers

    def capture_policy_preview(
        self, libraries: tuple[str, ...], limit: int
    ) -> PolicyPreviewCapture:
        captured = []
        for library in libraries:
            resolved = resolve_policy(self.active, library)
            pairs = tuple(
                (
                    report,
                    evaluate_report(
                        report, resolved.resolved_id, dict(resolved.destinations)
                    ),
                )
                for report in self.reports
                if report.target.library == library
            )
            captured.append(
                PolicyPreviewLibraryCapture(
                    library, resolved, len(pairs), pairs, self.covers
                )
            )
        return PolicyPreviewCapture(self.active, 2, tuple(captured))


def _rows(
    draft: MetadataPolicyProfile,
    report: MetadataReport,
    *,
    active: MetadataPolicyProfile = BUILTIN,
    covers: tuple[PolicyPreviewCover | None, ...] = (),
    overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, PolicyFieldComparison]:
    """The one sampled work's rows beyond the rich eight, by library field."""
    library = report.target.library
    preview = preview_policy(
        draft, (library,), overrides or {}, _Capture((report,), active, covers)
    )
    (case,) = preview.libraries[0].comparisons
    return {field.destination: field for field in case.fields[8:]}


def _result(side) -> tuple[object, ...]:
    return (side.outcome, side.current_value, side.desired_value, side.reason)


def _publisher_report() -> MetadataReport:
    """An ebook with no publisher whose own file states one."""
    return _report(
        fields=_fields(),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.PUBLISHER,
                observed="Lantern Row Press",
                canonical="Lantern Row Press",
                excerpt="Lantern Row Press",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
            ),
        ),
    )


def test_a_contributor_names_option_leaves_a_filled_narrator_to_the_owner() -> None:
    rows = _rows(
        _draft({"contributor_names": "preserve"}), corroborated_audiobook_report()
    )

    assert {"authors", "narrators"} == set(rows)
    narrators = rows["narrators"]
    assert narrators.changed
    assert ("contributor_names", "automatic", "automatic") == (
        narrators.draft.field,
        narrators.active_destination,
        narrators.draft_destination,
    )
    assert ("projected", None, ["Ilsa Brandt"], None) == _result(narrators.active)
    # Left to you: Automatic was asked for and is not offered for this change.
    assert ("suggested", None, ["Ilsa Brandt"], "automatic_unqualified") == _result(
        narrators.draft
    )
    # The authors are kept under both options, so their row is not a change.
    assert not rows["authors"].changed
    assert "no_op" == rows["authors"].draft.outcome


def test_a_date_precision_option_fills_an_empty_date() -> None:
    rows = _rows(
        _draft({"date_precision": "full_date"}),
        synthetic_audiobook_original_date_report(),
    )

    assert {"publishedDate"} == set(rows)
    date = rows["publishedDate"]
    assert date.changed
    assert ("suggested", None, "2025-02-03", "automatic_unqualified") == _result(
        date.active
    )
    # Filled: Automatic writes a value where none is set.
    assert ("projected", None, "2025-02-03", None) == _result(date.draft)


def test_a_title_setting_changes_a_current_title_only_under_automatic() -> None:
    active = _draft(destinations={"title_layout": "ask"})
    rows = _rows(BUILTIN, isbn_title_report(), active=active)

    assert {"title"} == set(rows)
    title = rows["title"]
    assert title.changed
    current = "Example Series 03 - Example Title"
    assert ("suggested", current, "Example Title", None) == _result(title.active)
    # Changed: Automatic replaces the current value.
    assert ("projected", current, "Example Title", None) == _result(title.draft)


def test_a_language_setting_of_ignore_leaves_a_filled_language_alone() -> None:
    rows = _rows(
        _draft(destinations={"language_rendering": "ignore"}),
        language_report(("en",)),
    )

    assert {"language"} == set(rows)
    language = rows["language"]
    assert language.changed
    assert ("projected", None, "English", None) == _result(language.active)
    # No change: the draft leaves the field alone where the active policy fills.
    assert ("no_op", None, None, "policy_ignored") == _result(language.draft)


def test_a_publisher_setting_of_ask_leaves_a_filled_publisher_to_the_owner() -> None:
    rows = _rows(
        _draft(destinations={"publisher_preference": "ask"}), _publisher_report()
    )

    assert {"publisher"} == set(rows)
    publisher = rows["publisher"]
    assert publisher.changed
    assert ("projected", None, "Lantern Row Press", None) == _result(publisher.active)
    # Left to you: the draft asks.
    assert ("suggested", None, "Lantern Row Press", None) == _result(publisher.draft)


@pytest.mark.parametrize(
    "report",
    (
        corroborated_audiobook_report,
        synthetic_audiobook_original_date_report,
        isbn_title_report,
        lambda: language_report(("en",)),
    ),
)
def test_an_unchanged_draft_lists_no_field_beyond_the_rich_eight(report) -> None:
    assert {} == _rows(_draft(), report())


def test_an_override_that_pins_the_field_lists_none_for_that_library() -> None:
    report = corroborated_audiobook_report()
    pinned: dict[str, dict[str, object]] = {
        report.target.library: {
            "options": {"contributor_names": "canonical"},
            "destinations": {},
        }
    }

    assert {} == _rows(
        _draft({"contributor_names": "preserve"}), report, overrides=pinned
    )


def test_a_narrators_row_is_compiled_only_for_an_audiobook() -> None:
    rows = _rows(_draft(destinations={"contributor_names": "ask"}), isbn_title_report())

    assert {"authors"} == set(rows)


def test_the_preview_stops_at_the_outcome_set_for_a_title_and_subtitle_pair() -> None:
    """The stated limit of ADR-0048: admission's joint pair is not previewed.

    Under ``title_and_subtitle`` admission writes this recording's title and
    subtitle as one pair; under ``preserve`` no pair binds. The outcome set
    gives the title the same destination, disposition and value under both, so
    the preview lists no row for a draft that changes only that option.
    """
    report = synthetic_audiobook_bibliographic_report()
    active = _draft({"subtitle": "corroborated_split"}, {"subtitle": "automatic"})
    draft = _draft({"title_layout": "preserve"}, active=active)

    def pair(profile: MetadataPolicyProfile) -> str | None:
        policy = resolve_policy(profile, report.target.library)
        proposal = evaluate_report(
            report, policy.resolved_id, dict(policy.destinations)
        )
        case = ReviewCase.create(proposal, report)
        decisions = decide_shadow_packet(
            ShadowReviewPacket.create(
                case, proposal, report, include_policy_ignored=True
            )
        )
        outcomes = ShadowOutcomeSet.create(
            case, decisions, policy, 2, report, proposal, media_kind="audiobook"
        ).outcomes
        return audiobook_title_subtitle_bundle(
            tuple(
                outcome
                for outcome in outcomes
                if outcome.destination in {"title", "subtitle"}
            )
        )

    assert pair(active) is not None
    assert pair(draft) is None
    rows = _rows(draft, report, active=active)
    assert {"title"} == set(rows)
    assert not rows["title"].changed
    assert _result(rows["title"].active) == _result(rows["title"].draft)
    assert "projected" == rows["title"].draft.outcome


def test_outcomes_compile_at_most_twice_a_work_and_never_for_an_equal_policy(
    monkeypatch,
) -> None:
    reports = (
        corroborated_audiobook_report(),
        synthetic_audiobook_original_date_report(),
    )
    library = reports[0].target.library
    assert {library} == {report.target.library for report in reports}
    compiled: list[str] = []
    create = ShadowOutcomeSet.create

    def counting(case, *args, **kwargs):
        compiled.append(case.report_id)
        return create(case, *args, **kwargs)

    monkeypatch.setattr(policy_application.ShadowOutcomeSet, "create", counting)

    def preview(draft: MetadataPolicyProfile, overrides=None) -> list[str]:
        compiled.clear()
        preview_policy(draft, (library,), overrides or {}, _Capture(reports))
        return sorted(compiled)

    twice = sorted(report.report_id for report in reports for _ in range(2))
    assert twice == preview(_draft({"contributor_names": "preserve"}))
    assert twice == preview(
        _draft(
            {"contributor_names": "preserve", "date_precision": "full_date"},
            {"title_layout": "ask", "cover_order": "ask"},
        )
    )
    # The resolved draft equals the resolved active policy: nothing compiles.
    assert [] == preview(_draft())
    assert [] == preview(
        _draft({"contributor_names": "preserve"}),
        {library: {"options": {"contributor_names": "canonical"}, "destinations": {}}},
    )
    # A draft that changes only a rich field or the cover needs no outcome set.
    assert [] == preview(_draft(destinations={"genres": "ask", "cover_order": "ask"}))


_FILLABLE = PolicyPreviewCover(False, "fill", None, True)


@pytest.mark.parametrize(
    "cover,active,draft,changed",
    (
        # Filled: Automatic fills an empty ebook cover; Ask me suggests it.
        (
            _FILLABLE,
            ("suggested", "Suggests a confirmed cover", None),
            ("projected", "Fills the empty cover", None),
            True,
        ),
        # A fill the comparison does not recommend for a known-empty cover.
        (
            PolicyPreviewCover(False, "fill", None, False),
            ("suggested", "Suggests a confirmed cover", None),
            ("suggested", "Suggests a confirmed cover", None),
            False,
        ),
        # No replacement is automatic in this release.
        (
            PolicyPreviewCover(False, "replace", "below_floor", False),
            ("suggested", "Suggests a replacement", "below_floor"),
            ("suggested", "Suggests a replacement", "below_floor"),
            False,
        ),
        (
            PolicyPreviewCover(False, "suggest", "sharper_copy", False),
            ("suggested", "Suggests a replacement", "sharper_copy"),
            ("suggested", "Suggests a replacement", "sharper_copy"),
            False,
        ),
        (
            PolicyPreviewCover(False, "keep", None, False),
            ("no_op", "Current cover kept", "current_cover_kept"),
            ("no_op", "Current cover kept", "current_cover_kept"),
            False,
        ),
        (
            PolicyPreviewCover(False, "hold", None, False),
            ("no_op", "Current cover kept", None),
            ("no_op", "Current cover kept", None),
            False,
        ),
        (
            PolicyPreviewCover(False, "empty", None, False),
            ("no_op", None, "no_confirmed_candidates"),
            ("no_op", None, "no_confirmed_candidates"),
            False,
        ),
        # The three reasons a cover row is not previewed.
        (
            None,
            ("not_previewed", None, "cover_not_compared"),
            ("not_previewed", None, "cover_not_compared"),
            False,
        ),
        (
            PolicyPreviewCover(True, "fill", None, True),
            ("not_previewed", None, "provider_fetching"),
            ("not_previewed", None, "provider_fetching"),
            False,
        ),
        (
            PolicyPreviewCover(False, None, None, False),
            ("not_previewed", None, "cover_compared_next_scan"),
            ("not_previewed", None, "cover_compared_next_scan"),
            False,
        ),
    ),
)
def test_a_cover_row_is_words_from_the_stored_stage(
    cover, active, draft, changed
) -> None:
    """Ask me is active and Automatic is the draft, over one ebook."""
    rows = _rows(
        BUILTIN,
        isbn_title_report(),
        active=_draft(destinations={"cover_order": "ask"}),
        covers=(cover,),
    )

    assert {"cover"} == set(rows)
    row = rows["cover"]
    assert "cover_order" == row.draft.field
    assert active == (row.active.outcome, row.active.desired_value, row.active.reason)
    assert draft == (row.draft.outcome, row.draft.desired_value, row.draft.reason)
    assert changed == row.changed
    assert row.draft.current_value in {None, "Has a cover"}


def test_ignore_leaves_a_suggested_cover_alone_and_an_audiobook_fill_is_suggested() -> (
    None
):
    ignored = _rows(
        _draft(destinations={"cover_order": "ignore"}),
        isbn_title_report(),
        covers=(PolicyPreviewCover(False, "suggest", "sharper_copy", False),),
    )["cover"]
    assert ignored.changed
    assert ("suggested", "Suggests a replacement", "sharper_copy") == (
        ignored.active.outcome,
        ignored.active.desired_value,
        ignored.active.reason,
    )
    # No change: the draft leaves the cover alone where the active policy asks.
    assert ("no_op", None, "policy_ignored") == (
        ignored.draft.outcome,
        ignored.draft.desired_value,
        ignored.draft.reason,
    )

    # An audiobook fill is not qualified, so Automatic suggests it as Ask me does.
    audiobook = _rows(
        BUILTIN,
        corroborated_audiobook_report(),
        active=_draft(destinations={"cover_order": "ask"}),
        covers=(_FILLABLE,),
    )["cover"]
    assert not audiobook.changed
    assert "Suggests a confirmed cover" == audiobook.draft.desired_value


@pytest.mark.parametrize("stage", ("keep", "hold"))
@pytest.mark.parametrize("ignored", ("active", "draft"))
def test_a_kept_cover_is_not_a_change_when_one_policy_ignores_it(
    stage: str, ignored: str
) -> None:
    """Two results that leave the cover alone are equal, whatever their words."""
    ignore = _draft(destinations={"cover_order": "ignore"})
    source = isbn_title_report()
    # A kept cover is one the work has, and "Current" says so in words.
    report = MetadataReport.create(
        target=replace(
            source.target,
            fields=tuple(
                TargetField(
                    field.destination, TargetFieldState.VALUE, adapter_cover_value()
                )
                if field.destination is TargetDestination.COVER
                else field
                for field in source.target.fields
            ),
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )
    row = _rows(
        ignore if ignored == "draft" else BUILTIN,
        report,
        active=ignore if ignored == "active" else BUILTIN,
        covers=(PolicyPreviewCover(False, stage, None, False),),
    )["cover"]

    sides = {"active": row.active, "draft": row.draft}
    kept = sides.pop("draft" if ignored == "active" else "active")
    assert ("no_op", "Has a cover", "Current cover kept") == _result(kept)[:3]
    assert ("no_op", None, "policy_ignored") == _result(sides[ignored])[::2] + (
        sides[ignored].reason,
    )
    assert not row.changed


def test_an_ask_and_an_automatic_that_is_not_offered_read_the_same() -> None:
    """The same value left to the owner under both policies is not a change."""
    report = corroborated_audiobook_report()
    active = _draft({"contributor_names": "preserve"})
    row = _rows(
        _draft(destinations={"contributor_names": "ask"}, active=active),
        report,
        active=active,
    )["narrators"]

    assert ("suggested", ["Ilsa Brandt"], "automatic_unqualified") == (
        row.active.outcome,
        row.active.desired_value,
        row.active.reason,
    )
    assert ("suggested", ["Ilsa Brandt"], None) == (
        row.draft.outcome,
        row.draft.desired_value,
        row.draft.reason,
    )
    assert not row.changed
