"""Synthetic genre reports shared by persisted authority and owner journeys."""

import json
from dataclasses import replace
from typing import Any

from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    ProviderObservation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.proposal_evidence import (
    _claim,
    _target_work_candidate,
    _target_work_report,
)


def genre_report(
    author: str = "Example, Ada",
    *,
    target_author: str = "Ada Example",
    current: tuple[str, ...] | None = None,
    state: TargetFieldState = TargetFieldState.MISSING,
    genres: tuple[str, ...] = ("Fantasy",),
    extra: tuple[ProviderObservation, ...] = (),
) -> MetadataReport:
    candidate = _target_work_candidate("catalogue", "edition-1", "work-1")
    claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            author,
            scope=ClaimScope.WORK,
            role="author",
        )
        if claim.kind is ClaimKind.CREATOR
        else claim
        for claim in candidate.claims
        if claim.kind is not ClaimKind.GENRE
    )
    candidate = replace(
        candidate,
        claims=claims
        + tuple(
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.GENRE,
                value,
                scope=ClaimScope.WORK,
                order=index + 20,
            )
            for index, value in enumerate(genres)
        ),
    )
    base = _target_work_report(
        (("catalogue", "hardcover", (candidate,), True),),
        target_authors=(target_author,),
    )
    fields = tuple(
        TargetField(
            TargetDestination.GENRES,
            TargetFieldState.VALUE if current is not None else state,
            current,
        )
        if field.destination is TargetDestination.GENRES
        else field
        for field in base.target.fields
    )
    return MetadataReport.create(
        target=TargetFacts(
            base.target.connection, base.target.library, base.target.item, fields
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers + extra,
    )


def publish_genre_case(
    repository: Any, report: MetadataReport, destination: str = "automatic"
):
    """Publish an actual policy, proposal, review case and persisted outcome."""
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["genres"] = destination
    profile = MetadataPolicyProfile.create(
        "Genre evidence", dict(DEFAULT_OPTIONS) | {"genres": "fill_empty"}, destinations
    )
    resolved = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
    )
    if destination == "automatic":
        repository.activate_qualified_automatic(3)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.persist_shadow_outcome(case_id, 4)
    return case_id, proposal


def inventaire_genre_report(
    author: str, rows: int, *, target_author: str = "Ada Example"
):
    """Exercise the complete Inventaire producer with bounded synthetic HTTP."""
    import httpx

    from mechanical_librarian.adapters.providers.inventaire_metadata import (
        InventaireMetadataProvider,
        InventairePacer,
    )
    from mechanical_librarian.application.metadata_report import (
        TargetSnapshot,
        metadata_discovery_plan,
    )
    from mechanical_librarian.infrastructure.security.credentials import (
        inventaire_client,
    )
    from tests.support.metadata_report_schemas import synthetic_report

    base = synthetic_report("Example Book")
    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (target_author,))
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in base.target.fields
    )
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": f"Q{index}", "type": "works", "uri": f"wd:Q{index}"}
                        for index in range(rows)
                    ]
                },
            )
        uris = request.url.params.get_list("uris")
        if uris == ["wd:Q42"]:
            entities = {
                "wd:Q42": {"uri": "wd:Q42", "labels": {"en": author}, "claims": {}}
            }
        else:
            assert uris == [f"wd:Q{index}" for index in range(rows)]
            entities = {
                uri: {
                    "uri": uri,
                    "claims": {
                        "wdt:P1476": ["Example Book"],
                        "wdt:P50": ["wd:Q42"],
                    },
                }
                for uri in uris
            }
        return httpx.Response(200, json={"entities": entities, "redirects": {}})

    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Example Book", (), fields=fields), base.local_claims
    )
    assert len(plan.lanes) == 1
    provider = InventaireMetadataProvider(
        "inventaire",
        inventaire_client(5, transport=httpx.MockTransport(respond)),
        65536,
        pacer=InventairePacer(0.001, clock=lambda: 10.0, sleep=lambda _: None),
        clock=lambda: 10.0,
        wall_clock=lambda: 1700000000.0,
        sleep=lambda _: None,
        jitter=lambda: 0.5,
    )
    result = provider.observe(plan)
    report = MetadataReport.create(
        target=TargetFacts(
            base.target.connection, base.target.library, base.target.item, fields
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(result.observation,),
    )
    return report, requests
