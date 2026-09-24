"""Synthetic ebook WORK evidence produced by the real Hardcover adapter."""

import httpx

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    TargetSnapshot,
    metadata_discovery_plan,
    observe_provider,
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
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.hardcover_evidence import hardcover_envelope
from tests.support.proposal_evidence import _fields, _report


def synopsis_report(
    current: str | None = None,
    summaries: tuple[str, ...] = (
        "A voyage reveals a hidden city.",
        "An explorer discovers an ancient city.",
    ),
    *,
    reverse: bool = False,
) -> MetadataReport:
    title = "Synthetic Voyage"
    fields = _fields(
        title,
        authors=TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada Example",)
        ),
        description=TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.MISSING if current is None else TargetFieldState.VALUE,
            current,
        ),
        tags=TargetField(TargetDestination.TAGS, TargetFieldState.MISSING),
    )
    claims = (
        LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed=title,
            canonical=title,
            excerpt=title,
            basis="target_metadata",
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
        ),
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", title, (), fields=fields), claims
    )
    observations = []
    for index, summary in sorted(enumerate(summaries), reverse=reverse):
        payload = hardcover_envelope(
            [{"id": index + 1, "book_id": 10}],
            [
                {
                    "id": 10,
                    "title": title,
                    "description": summary,
                    "contributions": [
                        {
                            "id": 1,
                            "contribution": None,
                            "author": {"id": 1, "name": "Ada Example"},
                        }
                    ],
                    "taggings": [{"id": 1, "tag": {"id": 1, "tag": "Exploration"}}],
                }
            ],
        )
        with httpx.Client(
            transport=httpx.MockTransport(
                lambda request, payload=payload: httpx.Response(200, json=payload)
            )
        ) as client:
            adapter = HardcoverMetadataProvider(
                f"catalogue-{index}",
                client,
                65536,
                clock=lambda: 0.0,
                wall_clock=lambda: 1700000000.0,
            )
            observations.append(
                observe_provider(
                    f"catalogue-{index}", "hardcover", adapter, plan
                ).observation
            )
    return _report(
        title=title, fields=fields, local_claims=claims, providers=tuple(observations)
    )


def publish_synopsis_preview(repository, report, mode="automatic"):
    return publish_audiobook_preview(
        repository,
        report,
        ("tags",),
        selected_options={"description_fill": "safe_plain_text", "tags": "fill_empty"},
        selected_destinations={"description_fill": mode},
    )


def ambiguous_synopsis_report(*, same_record: bool) -> MetadataReport:
    """Retain same-record or cross-source ambiguity with proved WORK lineage."""
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope
    from tests.support.proposal_evidence import _claim

    report = synopsis_report()
    first, second = report.providers
    providers = report.providers
    if same_record:
        candidate = first.candidates[0]
        competing = _claim(
            first.provider,
            candidate.identity,
            ClaimKind.DESCRIPTION,
            "A conflicting account of the voyage.",
            scope=ClaimScope.WORK,
            order=99,
        )
        providers = (
            replace(
                first,
                candidates=(replace(candidate, claims=(*candidate.claims, competing)),),
            ),
        )
    else:
        providers = (
            replace(first, authority="open_library"),
            replace(second, authority="audnexus"),
        )
    return MetadataReport.create(
        target=report.target,
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=providers,
    )
