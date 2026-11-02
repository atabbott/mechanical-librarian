"""A subtitle that only restates the series is never written automatically (#1616).

The predicate's closed grammar is pinned value by value, then the preview is
driven through a real adapter-produced audiobook report: the restating value
abstains with its own reason and stays a choice, while a real subtitle on the
same report still projects.
"""

import pytest

from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimScope,
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    evaluate_report,
    subtitle_restates_series,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichFieldPreview,
    compile_rich_metadata_preview,
)
from tests.support.audiobook_reports import synthetic_audiobook_bibliographic_report
from tests.support.proposal_evidence import _local_subtitle
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source

_SERIES = ("Saltmarsh Chronicles",)


@pytest.mark.parametrize(
    ("value", "series_names"),
    (
        # The whole value is the series grammar's label: no name is needed.
        ("Saltmarsh Chronicles, Book 3", ()),
        ("Saltmarsh Chronicles Book 3", ()),
        ("Saltmarsh Chronicles #3", ()),
        ("Saltmarsh Chronicles Series, Book 3", ()),
        ("A Tidewater Novel Saltmarsh Chronicles, Book 3", ()),
        # Position grammar alone also reads a real subtitle ending in a part.
        ("Lessons, Part 2", ()),
        # A case series name, whole or inside the closed frame.
        ("Saltmarsh Chronicles", _SERIES),
        ("The Saltmarsh Chronicles", _SERIES),
        ("Saltmarsh Chronicles 3", _SERIES),
        ("Saltmarsh Chronicles Series 3", _SERIES),
        ("A Saltmarsh Chronicles Novel", _SERIES),
        ("A Saltmarsh Chronicles Novella 2", _SERIES),
        ("An Saltmarsh Chronicles Story", _SERIES),
        ("The Third Saltmarsh Chronicles Novel", _SERIES),
        ("Book 3 of Saltmarsh Chronicles", _SERIES),
        ("Book One of the Saltmarsh Chronicles", _SERIES),
        ("Volume IV in the Saltmarsh Chronicles", _SERIES),
        ("  a   saltmarsh chronicles novel  ", _SERIES),
    ),
)
def test_a_series_restating_subtitle_is_read(value, series_names):
    assert subtitle_restates_series(value, series_names) is True


@pytest.mark.parametrize(
    ("value", "series_names"),
    (
        ("A Pandemic Story", ()),
        ("A Saltmarsh Chronicles Novel", ()),
        ("Saltmarsh Chronicles 3", ()),
        ("The Complete Saltmarsh Chronicles Story Collection", _SERIES),
        ("A Tale of Saltmarsh Chronicles", _SERIES),
        ("Military Science Fiction Adventure", _SERIES),
        ("A Tidewater Hollow Novel", _SERIES),
        ("A Novel", _SERIES),
        ("Saltmarsh Chronicles Rising", _SERIES),
        # An article without a trailing noun is not part of the frame.
        ("The First Saltmarsh Chronicles", _SERIES),
        ("", _SERIES),
        ("   ", _SERIES),
        (None, _SERIES),
        (["Saltmarsh Chronicles"], _SERIES),
        # An unkeyable name or value never raises.
        ("Saltmarsh Chronicles", ("\\",)),
        ("\\", _SERIES),
    ),
)
def test_other_subtitles_are_not_read_as_a_restatement(value, series_names):
    assert subtitle_restates_series(value, series_names) is False


def _preview(
    report: MetadataReport, option: str = "fill_empty"
) -> tuple[RichFieldPreview, ProposalSet]:
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["subtitle"] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create(
            "Series restatement", {**DEFAULT_OPTIONS, "subtitle": option}, destinations
        ),
        report.target.library,
    )
    proposal = evaluate_report(
        report, policy.resolved_id, human_review_destinations(policy)
    )
    preview = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    return preview, proposal


def _offered(proposal: ProposalSet) -> list[object]:
    return [
        alternative.value.value
        for result in proposal.results
        if result.destination is TargetDestination.SUBTITLE
        for alternative in result.alternatives
    ]


@pytest.mark.parametrize(
    ("subtitle", "source_series"),
    (
        ("Saltmarsh Chronicles, Book 3", None),
        ("Book 3 of the Saltmarsh Chronicles", ("Saltmarsh Chronicles", "3")),
        ("A Saltmarsh Chronicles Novel", ("Saltmarsh Chronicles", "3")),
    ),
)
def test_a_series_restating_fill_abstains_and_stays_choosable(subtitle, source_series):
    report = synthetic_audiobook_bibliographic_report(
        title="Saltmarsh",
        source_title="Saltmarsh",
        source_subtitle=subtitle,
        source_series=source_series,
    )
    preview, proposal = _preview(report)
    assert (preview.outcome, preview.reason) == ("abstain", "subtitle_restates_series")
    assert _offered(proposal) == [subtitle]


def test_a_real_subtitle_on_the_same_report_still_projects():
    report = synthetic_audiobook_bibliographic_report(
        title="Saltmarsh",
        source_title="Saltmarsh",
        source_subtitle="A Tale of Courage",
        source_series=("Saltmarsh Chronicles", "3"),
    )
    preview, _proposal = _preview(report)
    assert (preview.outcome, preview.desired_value) == (
        "projected",
        "A Tale of Courage",
    )


@pytest.mark.parametrize(
    ("subtitle", "outcome"),
    (
        ("Saltmarsh Chronicles, Book 3", "abstain"),
        ("A Tale of Courage", "projected"),
    ),
)
def test_a_series_restating_replacement_of_an_announcement_abstains(subtitle, outcome):
    report = synthetic_audiobook_bibliographic_report(
        title="Saltmarsh",
        source_title="Saltmarsh",
        source_subtitle=subtitle,
        target_subtitle="Now a Major Motion Picture",
    )
    preview, proposal = _preview(report, "replace_descriptive")
    assert preview.outcome == outcome
    if outcome == "abstain":
        assert preview.reason == "subtitle_restates_series"
    assert subtitle in _offered(proposal)


@pytest.mark.parametrize(
    ("subtitle", "series", "outcome"),
    (
        ("Mistborn, Book 3", None, "abstain"),
        ("A Mistborn Novella", "Mistborn", "abstain"),
        ("A Mistborn Novella", None, "projected"),
    ),
)
def test_an_ebook_subtitle_fill_is_guarded_alike(subtitle, series, outcome):
    report = _bibliographic_report(
        current_title="Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle=subtitle,
                series=series,
                sequence=None if series is None else "3",
            ),
        ),
        local_claims=(_local_subtitle(subtitle, ClaimScope.WORK),),
    )
    policy = resolve_policy(MetadataPolicyProfile.builtin(), report.target.library)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    preview = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "subtitle"
    )
    assert preview.outcome == outcome
    if outcome == "abstain":
        assert preview.reason == "subtitle_restates_series"
    assert _offered(proposal) == [subtitle]
