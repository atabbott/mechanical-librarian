from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.metadata_policy import (
    MAX_SHADOW_SAMPLE_RESPONSE_BYTES,
    PolicyNotebook,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    ReviewPreparationPipeline,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
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
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase, ReviewTransition
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
)
from tests.support.proposal_evidence import (
    _report as _proposal_report,
)
from tests.support.review_cases import _report
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source,
)
from tests.unit.test_cover_packets import (
    _observer_result as _cover_observer_result,
)
from tests.unit.test_cover_packets import (
    _preview as _cover_preview,
)
from tests.unit.test_cover_packets import (
    _report as _cover_report,
)
from tests.unit.test_cover_packets import (
    _source as _cover_source,
)


def _case(repository):
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return admitted["review_case_id"]


def _running_projection(repository, case_id: str) -> str:
    case = json.loads(repository.get_review_case(case_id))["case"]
    scan_id = "review_scan_" + ("1" * 64)
    work_id = "review_work_" + ("2" * 64)
    repository._connection.execute(
        "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,"
        "library_id,producer_fingerprint,state,requested_at,updated_at,"
        "catalogue_generation,catalogue_fingerprint,total) "
        "VALUES(?,?,?,?,?,'processing',1,1,1,?,1)",
        (scan_id, "primary", "ebooks", "library-1", "3" * 64, "4" * 64),
    )
    repository._connection.execute(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,"
        "library_name,library_id,item_id,file_identity,source_revision,"
        "source_fingerprint,producer_fingerprint,catalogue_generation,"
        "catalogue_fingerprint,stage,state,attempt,recovery_attempt,report_id,"
        "proposal_set_id,review_case_id,failure_code,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'projection','running',1,0,?,?,?,NULL,1)",
        (
            work_id,
            scan_id,
            0,
            "primary",
            "ebooks",
            "library-1",
            "item-1",
            case["file_identity"],
            1,
            "5" * 64,
            "3" * 64,
            1,
            "4" * 64,
            case["report_id"],
            case["proposal_set_id"],
            case_id,
        ),
    )
    repository._connection.commit()
    return work_id


def _staged_projection_action(repository, work_id: str) -> PipelineAction:
    row = repository._connection.execute(
        "SELECT work_id,scan_id,stage,attempt,connection_name,library_name,library_id,"
        "producer_fingerprint,catalogue_generation,catalogue_fingerprint,item_id,"
        "file_identity,source_revision,report_id,proposal_set_id,review_case_id,"
        "source_fingerprint FROM review_preparation_work WHERE work_id=?",
        (work_id,),
    ).fetchone()
    assert row is not None
    checkpoint = repository._connection.execute(
        "SELECT checkpoint FROM current_review_case_lineages WHERE "
        "connection_name=? AND library_name=? AND item_id=?",
        (row[4], row[5], row[10]),
    ).fetchone()
    assert checkpoint is not None
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state WHERE singleton=1"
    ).fetchone()
    assert generation is not None
    return PipelineAction(
        *cast(
            "tuple[str, str, str, int, str, str, str, str, int | None, str | None, str | None, str | None, int | None, str | None, str | None, str | None, str | None]",
            row,
        ),
        lineage_checkpoint=checkpoint[0],
        policy_generation=generation[0],
    )


def _digest(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _preview_automatic_profile() -> MetadataPolicyProfile:
    destinations = {
        category: "unsupported" if category == "explicit" else "ask"
        for category in OPTION_CATALOGUE
    }
    destinations.update(
        title_layout="automatic",
        publisher_preference="automatic",
        tags="ignore",
    )
    return MetadataPolicyProfile.create(
        "Preview automatic intent", dict(DEFAULT_OPTIONS), destinations
    )


def _pipeline_configuration(
    *, cover_reviewer_profile_file: str | None = None
) -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/run/target-token"
            ),
        ),
        (LibraryConfiguration("ebooks", "primary", "library-1", "book", None, ()),),
        (),
        (),
        review=ReviewWebConfiguration(
            "https://review.example.invalid",
            "/run/owner-password",
            cover_reviewer_profile_file=cover_reviewer_profile_file,
        ),
    )


def _maximum_reference_outcome(repository) -> ShadowOutcomeSet:
    case_id = _case(repository)
    repository.enter_shadow_preview(1)
    base = ShadowOutcomeSet.from_json(repository.persist_shadow_outcome(case_id, 2))
    citations = tuple(f"local_{index:064x}" for index in range(256))
    combinations = tuple(
        f"bibliographic_combination_{index:064x}" for index in range(256)
    )
    fields = []
    for field in base.outcomes:
        identity = field._identity_data()
        identity["citation_ids"] = list(citations)
        identity["combination_ids"] = list(combinations)
        identity["qualification_candidate_id"] = "qualification_candidate_" + _digest(
            {
                "action_id": field.action_id,
                "candidate_id": field.candidate_id,
                "catalogue_sha256": base.catalogue_sha256,
                "citation_ids": list(citations),
                "combination_ids": list(combinations),
                "rule_id": field.rule_id,
            }
        )
        fields.append(
            replace(
                field,
                outcome_id="shadow_outcome_" + _digest(identity),
                citation_ids=citations,
                combination_ids=combinations,
                qualification_candidate_id=identity["qualification_candidate_id"],
            )
        )
    identity = base._identity_data()
    identity["outcomes"] = [field.to_data() for field in fields]
    return replace(
        base,
        outcome_set_id="shadow_outcome_set_" + _digest(identity),
        outcomes=tuple(fields),
    )


def _synthetic_automatic_tuple(repository, case_id: str, category: str):
    """A value-free qualified projection packet for persistence race coverage.

    The production projection is separately tested from real reports above.  This
    small packet isolates the append-only admission boundary without importing a
    corpus value or manufacturing a metadata write.
    """
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    members, option = (
        (("title",), "title_and_subtitle")
        if category == "title_layout"
        else (("publishedDate", "publishedYear"), "full_date")
    )
    shared = (
        ("bibliographic_combination_" + "a" * 64,) if category == "title_layout" else ()
    )
    fields = tuple(
        SimpleNamespace(
            disposition="candidate",
            policy_category=category,
            policy_option=option,
            policy_destination="automatic",
            destination=destination,
            citation_ids=("local_" + "a" * 64,),
            combination_ids=shared,
            proposal_id="review_proposal_" + f"{index:064x}",
            candidate_id="alternative_" + f"{index + 16:064x}",
            rule_id="shadow_rule_" + f"{index + 32:064x}",
            outcome_id="shadow_outcome_" + f"{index + 48:064x}",
        )
        for index, destination in enumerate(members)
    )
    policy_id = repository.current_resolved_policy_id(case.target["library"])
    assert policy_id is not None
    generation = repository._connection.execute(
        "SELECT generation FROM metadata_policy_state WHERE singleton=1"
    ).fetchone()[0]
    return SimpleNamespace(
        outcomes=fields,
        review_case_id=case_id,
        report_id=case.report_id,
        proposal_set_id=case.proposal_set_id,
        target_fingerprint=case.target_fingerprint,
        resolved_policy_id=policy_id,
        policy_generation=generation,
    )


def _human_title_decision(repository, case_id: str, occurred_at: int) -> HumanDecision:
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "title"
    )
    result = proposal["result"]
    assert isinstance(result, dict)
    alternatives = result.get("alternatives")
    assert isinstance(alternatives, list) and alternatives
    selected = alternatives[0]["value"]
    assert isinstance(selected, dict)
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="synthetic-human-title-key-0001",
        occurred_at=occurred_at,
    )


def _qualified_cover_report(root: Path) -> tuple[MetadataReport, object]:
    payloads = (_cover_preview(1), _cover_preview(2))
    sources = (
        _cover_source(1, payloads[0], incumbent=True),
        _cover_source(2, payloads[1], embedded=True),
    )
    base = _cover_report(sources)
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", base.target.fields),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=base.cover_sources,
    )
    artifacts = CoverArtifactStore(root / "artifacts")
    for index, (source, payload) in enumerate(zip(sources, payloads, strict=True), 1):
        assert source.artifact is not None
        artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
    return report, evaluate_report(report)


def _run_qualified_cover_pipeline(
    repository, root: Path, report: MetadataReport, proposal, supervisor
) -> ReviewPreparationPipeline:
    database = root / "reports.sqlite"

    def extract(bound, action, claim_next):
        bound.publish(
            report,
            lambda: True,
            after_publication=lambda current, published: (
                current.complete_review_extraction_publication(
                    action,
                    published.report_id,
                    10,
                    0,
                    None,
                    claim_next,
                    None,
                )
            ),
        )
        return report.report_id, True

    def project(bound, action):
        outcome = bound.persist_shadow_outcome(
            action.review_case_id or "",
            10,
            staged_action=action,
        )
        return "stale" if outcome == "stale" else "complete"

    pipeline = ReviewPreparationPipeline(
        repository_factory=lambda: open_repository(
            database, authority=repository.authority
        ),
        libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
        discover=lambda *_: (
            1,
            report.file.content_digest,
            (
                CatalogueEntry(
                    "primary",
                    "ebooks",
                    "lib-1",
                    "item-1",
                    1,
                    "Synthetic cover",
                    (),
                    True,
                    False,
                    "singular-supported",
                    report.file.identity,
                    "/item-1.epub",
                    "epub",
                    "books",
                ),
            ),
        ),
        extract=extract,
        providers=lambda *_: (),
        propose=lambda bound, _: (
            bound.publish_proposal(proposal),
            proposal.proposal_set_id,
        )[1],
        admit=lambda bound, _: (
            lambda value: (value["status"], value.get("review_case_id"))
        )(json.loads(bound.admit_review_case(proposal))),
        project=project,
        before_projection=supervisor._pipeline._before_projection,
        after_advance=supervisor._pipeline._after_advance,
        clock=lambda: 10,
    )
    pipeline.request("primary", "ebooks")
    return pipeline


def _assert_no_cover_effect_authority(repository) -> None:
    assert (0,) == repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone()


def _assert_manual_cover_surface(
    repository, case_id: str, considerations, reviewer_status: str
) -> None:
    detail = InspectCurrentReviewCase(repository, considerations).run(case_id)
    cover = next(field for field in detail["fields"] if field["destination"] == "cover")
    consideration = cover["cover_consideration"]
    assert consideration is not None
    assert consideration is not None
    assert reviewer_status == consideration["reviewer_status"]
    assert any(candidate["selectable"] for candidate in consideration["candidates"])
    assert cover["editable"]
    assert cover["needs_attention"]
    assert cover["human_decision"] is None


def test_activity_projects_cover_reviewer_terminal_state_without_private_payloads() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def record(status: str, suffix: str, kind: str = "automatic_prepared") -> None:
            consideration_id = "cover_consideration_" + suffix * 64
            consideration = json.dumps(
                {
                    "candidate": "private-candidate",
                    "document": "private-document",
                    "image": "private-image",
                    "path": "/private/profile",
                    "profile": "private-profile",
                    "recommendation": "private-recommendation",
                    "reviewer_status": status,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    suffix * 64,
                    hashlib.sha256(consideration.encode()).hexdigest(),
                    None,
                    10,
                    consideration,
                ),
            )
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": kind,
                    "occurred_at": 10,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            event_id = (
                "cover_consideration_event_"
                + hashlib.sha256(event.encode()).hexdigest()
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (event_id, consideration_id, review_case_id, kind, None, 10, event),
            )

        record("completed", "a", "automatic_prepared")
        record("unavailable", "b", "automatic_prepared")
        record("failed", "c", "automatic_prepared")
        record("completed", "1", "owner_retry_prepared")
        record("unavailable", "2", "owner_retry_prepared")
        record("failed", "3", "owner_retry_prepared")
        record("failed", "d", "stale")
        record("failed", "e", "superseded")
        record("completed", "6", "selected")
        record("completed", "7", "kept_incumbent")
        record("completed", "8", "no_action")
        repository._connection.commit()
        page = repository.field_effect_activity_page(
            search=None,
            event_type="all",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        cover_events = [
            event for event in page["events"] if event["destination"] == "cover"
        ]
        assert {
            "completed",
            "unavailable",
            "failed",
            "stale",
            "superseded",
            "selected",
            "kept_incumbent",
            "no_action",
        } == {event["state"] for event in cover_events}
        assert {"completed", "unavailable", "failed"} == {
            event["state"]
            for event in cover_events
            if event["origin"] == "automatic_pipeline"
        }
        assert {"completed", "unavailable", "failed"} == {
            event["state"] for event in cover_events if event["origin"] == "owner_retry"
        }
        assert {"review"} == {
            event["event_type"]
            for event in cover_events
            if event["origin"] == "owner_retry"
        }
        assert "safety" == next(
            event["event_type"] for event in cover_events if event["state"] == "stale"
        )
        reviews = repository.field_effect_activity_page(
            search=None,
            event_type="review",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        review_cover_events = [
            event for event in reviews["events"] if event["destination"] == "cover"
        ]
        assert {
            "completed",
            "unavailable",
            "failed",
            "selected",
            "kept_incumbent",
            "no_action",
        } == {event["state"] for event in review_cover_events}
        assert {"human"} == {
            event["origin"]
            for event in review_cover_events
            if event["state"] in {"selected", "kept_incumbent", "no_action"}
        }
        serialized = json.dumps(page)
        for value in (
            "private-candidate",
            "private-document",
            "private-image",
            "/private/profile",
            "private-profile",
            "private-recommendation",
        ):
            assert value not in serialized
        repository.close()


def test_activity_fails_closed_for_malformed_cover_consideration_projection() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "f" * 64
        repository._connection.execute(
            "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
            (
                consideration_id,
                review_case_id,
                report_id,
                "f" * 64,
                "0" * 64,
                None,
                10,
                "{malformed",
            ),
        )
        event = json.dumps(
            {
                "consideration_id": consideration_id,
                "decision_id": None,
                "kind": "automatic_prepared",
                "occurred_at": 10,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-event-v1",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
            (
                "cover_consideration_event_"
                + hashlib.sha256(event.encode()).hexdigest(),
                consideration_id,
                review_case_id,
                "automatic_prepared",
                None,
                10,
                event,
            ),
        )
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="^persistence$"):
            repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
        repository.close()


def test_activity_accepts_the_cover_document_cap_and_rejects_larger_or_malformed_values() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def record(document: str, suffix: str) -> None:
            consideration_id = "cover_consideration_" + suffix * 64
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": "automatic_prepared",
                    "occurred_at": 10,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    suffix * 64,
                    hashlib.sha256(document.encode()).hexdigest(),
                    None,
                    10,
                    document,
                ),
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (
                    "cover_consideration_event_"
                    + hashlib.sha256(event.encode()).hexdigest(),
                    consideration_id,
                    review_case_id,
                    "automatic_prepared",
                    None,
                    10,
                    event,
                ),
            )

        for size, suffix in ((2_097_153, "a"), (12_582_912, "b")):
            prefix = '{"padding":"'
            suffix_text = '","reviewer_status":"completed"}'
            document = (
                prefix + "x" * (size - len(prefix) - len(suffix_text)) + suffix_text
            )
            record(document, suffix)
            repository._connection.commit()
            page = repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
            assert (1 if suffix == "a" else 2) == len(
                [item for item in page["events"] if item["destination"] == "cover"]
            )

        repository._connection.execute("PRAGMA ignore_check_constraints=ON")
        record('{"padding":"' + "private" + "x" * 12_582_912 + '"}', "c")
        repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="^persistence$"):
            repository.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )
        repository.close()


def test_activity_page_does_not_materialize_repeated_near_limit_cover_documents() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "a" * 64
        prefix = '{"candidate":"private-candidate","document":"private-document","image":"private-image","padding":"'
        suffix = '","profile":"private-profile","recommendation":"private-recommendation","reviewer_status":"completed"}'
        document = prefix + "x" * (12_582_912 - len(prefix) - len(suffix)) + suffix
        repository._connection.execute(
            "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
            (
                consideration_id,
                review_case_id,
                report_id,
                "a" * 64,
                hashlib.sha256(document.encode()).hexdigest(),
                None,
                10,
                document,
            ),
        )
        for occurred_at in range(100, 200):
            event = json.dumps(
                {
                    "consideration_id": consideration_id,
                    "decision_id": None,
                    "kind": "automatic_prepared",
                    "occurred_at": occurred_at,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-event-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            repository._connection.execute(
                "INSERT INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
                (
                    "cover_consideration_event_"
                    + hashlib.sha256(event.encode()).hexdigest(),
                    consideration_id,
                    review_case_id,
                    "automatic_prepared",
                    None,
                    occurred_at,
                    event,
                ),
            )
        repository._connection.commit()
        original_loads = json.loads

        def reject_large_json(value: object, *args: object, **kwargs: object) -> object:
            if type(value) is str and len(value.encode("utf-8")) > 65_536:
                raise AssertionError("activity materialized a private cover document")
            return original_loads(value, *args, **kwargs)

        with patch(
            "mechanical_librarian.adapters.persistence.metadata_reports.json.loads",
            side_effect=reject_large_json,
        ):
            page = repository.field_effect_activity_page(
                search=None,
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=100,
            )
        cover_events = [
            event for event in page["events"] if event["destination"] == "cover"
        ]
        assert 100 == len(cover_events)
        assert {"completed"} == {event["state"] for event in cover_events}
        serialized = json.dumps(page)
        assert len(serialized) < 100_000
        for value in (
            "private-candidate",
            "private-document",
            "private-image",
            "private-profile",
            "private-recommendation",
        ):
            assert value not in serialized
        repository.close()


def test_atomic_cover_preparation_excludes_sibling_drift_and_rolls_back_every_pointer() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]
        consideration_id = "cover_consideration_" + "d" * 64
        document = json.dumps(
            {
                "consideration_id": consideration_id,
                "replay_digest": "e" * 64,
                "report_id": report_id,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-v1",
                "supersedes_id": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "CREATE TEMP TRIGGER abort_cover_event BEFORE INSERT ON cover_consideration_events BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        package_count = repository._connection.execute(
            "SELECT count(*) FROM current_review_packages"
        ).fetchone()[0]

        def finalize() -> object:
            sibling = sqlite3.connect(path, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError):
                    sibling.execute(
                        "UPDATE current_review_case_lineages SET checkpoint=checkpoint"
                    )
            finally:
                sibling.close()
            return repository.record_cover_consideration(
                document,
                review_case_id=review_case_id,
                report_id=report_id,
                replay_digest="e" * 64,
                supersedes_id=None,
                occurred_at=10,
            )

        with pytest.raises(MetadataReportError, match="unavailable"):
            repository.atomic_cover_preparation(finalize)
        assert (0, 0, 0, package_count) == repository._connection.execute(
            "SELECT (SELECT count(*) FROM cover_considerations), (SELECT count(*) FROM cover_consideration_events), (SELECT count(*) FROM current_cover_considerations), (SELECT count(*) FROM current_review_packages)"
        ).fetchone()
        repository.close()


def test_manual_cover_predecessor_drift_rolls_back_and_releases_the_writer_lock() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        review_case_id = _case(repository)
        report_id = json.loads(repository.get_review_case(review_case_id))["case"][
            "report_id"
        ]

        def document(suffix: str, supersedes_id: str | None) -> str:
            return json.dumps(
                {
                    "consideration_id": "cover_consideration_" + suffix * 64,
                    "replay_digest": suffix * 64,
                    "report_id": report_id,
                    "review_case_id": review_case_id,
                    "schema_version": "cover-consideration-v1",
                    "supersedes_id": supersedes_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            )

        first = document("a", None)
        repository.record_cover_consideration(
            first,
            review_case_id=review_case_id,
            report_id=report_id,
            replay_digest="a" * 64,
            supersedes_id=None,
            occurred_at=10,
        )
        with pytest.raises(MetadataReportError, match="cover consideration changed"):
            repository.record_cover_consideration(
                document("b", None),
                review_case_id=review_case_id,
                report_id=report_id,
                replay_digest="b" * 64,
                supersedes_id=None,
                occurred_at=11,
            )
        assert not repository._connection.in_transaction
        sibling = sqlite3.connect(path, timeout=0)
        try:
            sibling.execute("BEGIN IMMEDIATE")
            sibling.execute("ROLLBACK")
        finally:
            sibling.close()
        assert (1, 1, 1) == repository._connection.execute(
            "SELECT (SELECT count(*) FROM cover_considerations), (SELECT count(*) FROM cover_consideration_events), (SELECT count(*) FROM current_cover_considerations)"
        ).fetchone()
        repository.close()


def _automatic_no_change_inputs(
    scenario: str,
) -> tuple[MetadataReport, str, str, str]:
    """Build one naturally qualified, equal automatic policy outcome."""
    if scenario in {"exact_title", "canonical_title"}:
        current_title = (
            "Secret History" if scenario == "exact_title" else "secret history"
        )
        local_title = LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Secret History",
            canonical="Secret History",
            excerpt="Secret History",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
        )
        return (
            _proposal_report(
                fields=_fields(
                    current_title,
                    subtitle=TargetField(
                        TargetDestination.SUBTITLE, TargetFieldState.NULL
                    ),
                    series=TargetField(
                        TargetDestination.SERIES, TargetFieldState.MISSING
                    ),
                ),
                local_claims=(local_title,),
                providers=(
                    _source(
                        "catalogue",
                        "edition-1",
                        titles=("Secret History",),
                        subtitle="A Mistborn Novella",
                    ),
                ),
            ),
            "title_layout",
            "title_and_subtitle",
            "title",
        )
    if scenario in {"exact_authors", "canonical_authors"}:
        authors = ("Mick Herron",) if scenario == "exact_authors" else ("mick herron",)
        local_creator = LocalClaim.create(
            kind=ClaimKind.CREATOR,
            observed="Mick Herron",
            canonical="Mick Herron",
            excerpt="Mick Herron",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.WORK,
            role="author",
        )
        return (
            _proposal_report(
                fields=_fields(
                    authors=TargetField(
                        TargetDestination.AUTHORS, TargetFieldState.VALUE, authors
                    ),
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.VALUE,
                        "0-306-40615-2",
                    ),
                ),
                local_claims=(
                    local_creator,
                    LocalClaim.create(
                        kind=ClaimKind.ISBN,
                        observed="0-306-40615-2",
                        canonical="0306406152",
                        excerpt="0-306-40615-2",
                        applicability="ebook",
                        checksum="valid_isbn_10",
                        applicability_basis="ebook",
                        scope=ClaimScope.EDITION,
                    ),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "work-1",
                        (
                            _claim(
                                "catalogue",
                                "work-1",
                                ClaimKind.CREATOR,
                                "Mick Herron",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            _claim(
                                "catalogue",
                                "work-1",
                                ClaimKind.ISBN,
                                "9780306406157",
                                scope=ClaimScope.EDITION,
                                applicability="ebook",
                                order=1,
                            ),
                        ),
                    ),
                ),
            ),
            "contributor_names",
            "preserve",
            "authors",
        )
    if scenario == "formatted_isbn":
        isbn10 = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0-306-40615-2",
            canonical="0306406152",
            excerpt="0-306-40615-2",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        return (
            _proposal_report(
                fields=_fields(
                    isbn=TargetField(
                        TargetDestination.ISBN,
                        TargetFieldState.VALUE,
                        "0-306-40615-2",
                    )
                ),
                local_claims=(isbn10,),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.ISBN,
                                "9780306406157",
                                scope=ClaimScope.EDITION,
                                applicability="ebook",
                            ),
                        ),
                    ),
                ),
            ),
            "isbn_form",
            "preserve_exact",
            "isbn",
        )
    raise AssertionError(f"unknown automatic no-change scenario: {scenario}")


@pytest.mark.parametrize(
    "scenario",
    (
        "exact_title",
        "canonical_title",
        "exact_authors",
        "canonical_authors",
        "formatted_isbn",
    ),
)
def test_automatic_keep_current_is_a_single_no_change_fact_without_guards(
    scenario: str,
) -> None:
    report, category, option, destination = _automatic_no_change_inputs(scenario)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        options = dict(DEFAULT_OPTIONS)
        options[category] = option
        destinations = {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        }
        destinations[category] = "automatic"
        if category == "contributor_names":
            destinations["isbn_form"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "No-change policy", options, destinations
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
        repository.activate_qualified_automatic(3)
        proposal_set = evaluate_report(
            report,
            resolved.resolved_id,
            human_review_destinations(resolved),
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal_set)
        case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        assert case_id is not None
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        persisted = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        outcome = next(
            value for value in persisted.outcomes if value.destination == destination
        )
        assert "keep_current" == outcome.disposition
        decisions = tuple(
            AutomaticDecision.from_json(value)
            for value in repository.get_review_case_decisions(case.review_case_id)
        )
        expected_decisions = (
            ("authors", "isbn") if category == "contributor_names" else (destination,)
        )
        assert set(expected_decisions) == {value.destination for value in decisions}
        decision = next(
            value for value in decisions if value.destination == destination
        )
        assert "keep_current" == decision.outcome
        if scenario == "exact_title":
            corrupt_case = SimpleNamespace(
                proposal_set_id=case.proposal_set_id,
                report_id=case.report_id,
                review_case_id=case.review_case_id,
                target=case.target,
                target_fingerprint=case.target_fingerprint,
                proposals=json.loads(json.dumps(case.proposals)),
            )
            proposal = next(
                value
                for value in corrupt_case.proposals
                if value["proposal_id"] == decision.proposal_id
            )
            alternatives = proposal["result"]["alternatives"]
            preservation = [
                value
                for value in alternatives
                if value["assessment"] == "preserves_target"
            ]
            assert 1 == len(preservation)
            preservation[0]["assessment"] = "replaces_target"
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["assessment"] = "preserves_target"
            preservation[0]["value"] = {"kind": "text", "value": "Different Title"}
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["value"] = {"kind": "text", "value": "Secret History"}
            preservation[0]["evidence_references"] = []
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision, corrupt_case, report, proposal_set, persisted
                )
            preservation[0]["evidence_references"] = list(
                next(
                    value
                    for value in proposal_set.results
                    if value.destination.value == destination
                )
                .alternatives[0]
                .evidence_references
            )

            def source_with(**changes: object) -> ShadowOutcomeSet:
                corrupted = persisted.to_data()
                field = next(
                    value
                    for value in corrupted["outcomes"]
                    if value["outcome_id"] == decision.source_outcome_id
                )
                field.update(changes)
                field["qualification_candidate_id"] = (
                    "qualification_candidate_"
                    + _digest(
                        {
                            "action_id": field["action_id"],
                            "candidate_id": field["candidate_id"],
                            "catalogue_sha256": corrupted["catalogue_sha256"],
                            "citation_ids": field["citation_ids"],
                            "combination_ids": field["combination_ids"],
                            "rule_id": field["rule_id"],
                        }
                    )
                )
                identity = dict(field)
                del identity["outcome_id"]
                field["outcome_id"] = "shadow_outcome_" + _digest(identity)
                outcome_identity = dict(corrupted)
                del outcome_identity["outcome_set_id"]
                corrupted["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                    outcome_identity
                )
                return ShadowOutcomeSet.from_json(
                    json.dumps(corrupted, separators=(",", ":"), sort_keys=True)
                )

            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision,
                    case,
                    report,
                    proposal_set,
                    source_with(citation_ids=[]),
                )
            with pytest.raises(FieldEffectError, match="authority_binding"):
                _automatic_decision_value(
                    decision,
                    case,
                    report,
                    proposal_set,
                    source_with(combination_ids=[]),
                )
        assert repository.persist_shadow_outcome(case_id, 5) == persisted.to_json()
        assert repository.count_current_attention_fields(None) == 0

        class UnexpectedAuthority:
            def resolve(self, _decision):
                raise AssertionError("keep-current decision reached authority")

        scheduler = ContinuousGuardedApplication(
            repository,
            {("primary", "ebooks"): cast(GuardedFieldApplication, SimpleNamespace())},
            {("primary", "ebooks"): SimpleNamespace()},
            UnexpectedAuthority(),
            (("primary", "ebooks"),),
            lambda: 10,
            lambda *_: None,
        )
        assert scheduler.schedule() == 0
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (len(expected_decisions),)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_authorities"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_consents"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effect_actions"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone() == (len(expected_decisions),)
        assert repository.pending_field_effect_decision_documents(100) == ()
        assert repository.count_current_attention_fields(None) == 0
        page = repository.field_effect_activity_page(
            search="item-1",
            event_type="metadata",
            occurred_after=0,
            page=1,
            page_size=25,
        )
        no_change = next(
            event for event in page["events"] if event["state"] == "no_change_required"
        )
        assert no_change["report_id"] == report.report_id
        repository.close()
        reopened = open_repository(path, create=False)
        assert reopened.persist_shadow_outcome(case_id, 11) == persisted.to_json()
        assert (
            reopened.field_effect_activity_page(
                search="item-1",
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )["events"][0]["state"]
            == "no_change_required"
        )
        assert reopened.pending_field_effect_decision_documents(100) == ()
        assert reopened._connection.execute(
            "SELECT count(*) FROM field_effect_no_changes"
        ).fetchone() == (len(expected_decisions),)
        if scenario == "exact_title":
            corrupted = persisted.to_data()
            field = next(
                value
                for value in corrupted["outcomes"]
                if value["outcome_id"] == decision.source_outcome_id
            )
            field["disposition"] = "abstention"
            field["reason_code"] = "no_eligible_action"
            identity = dict(field)
            del identity["outcome_id"]
            field["outcome_id"] = "shadow_outcome_" + _digest(identity)
            outcome_identity = dict(corrupted)
            del outcome_identity["outcome_set_id"]
            corrupted["outcome_set_id"] = "shadow_outcome_set_" + _digest(
                outcome_identity
            )
            trigger = reopened._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='shadow_decision_outcomes_no_update'"
            ).fetchone()
            assert trigger is not None and type(trigger[0]) is str
            reopened._connection.execute(
                "DROP TRIGGER shadow_decision_outcomes_no_update"
            )
            reopened._connection.execute(
                "UPDATE shadow_decision_outcomes SET document=?",
                (json.dumps(corrupted, separators=(",", ":"), sort_keys=True),),
            )
            reopened._connection.execute(trigger[0])
            reopened._connection.commit()
        reopened.close()
        if scenario == "exact_title":
            with pytest.raises(
                MetadataReportError, match="database is unavailable or incompatible"
            ) as raised:
                open_repository(path, create=False)
            assert isinstance(raised.value.__cause__, FieldEffectError)
            assert "authority_binding" == raised.value.__cause__.code


def test_every_f20_automatic_option_reaches_persisted_effect_authority() -> None:
    isbn10 = LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="0306406152",
        canonical="0306406152",
        excerpt="0306406152",
        applicability="ebook",
        checksum="valid_isbn_10",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )
    isbn_family_provider = _provider(
        "catalogue",
        "edition-1",
        (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.ISBN,
                "9780306406157",
                scope=ClaimScope.EDITION,
                applicability="ebook",
            ),
        ),
    )
    isbn_family_report = _proposal_report(
        fields=_fields(
            isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING)
        ),
        local_claims=(isbn10,),
        providers=(isbn_family_provider,),
    )
    series_claims = (
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "Book",
            scope=ClaimScope.WORK,
            order=0,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Reading Order",
            scope=ClaimScope.WORK,
            order=1,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "2",
            scope=ClaimScope.WORK,
            order=2,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES,
            "Publication Order",
            scope=ClaimScope.WORK,
            order=3,
        ),
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.SERIES_SEQUENCE,
            "4",
            scope=ClaimScope.WORK,
            order=4,
        ),
    )
    series_report = _bibliographic_report(
        current_title="Book",
        providers=(
            _provider(
                "catalogue",
                "edition-1",
                series_claims,
                authority="hardcover",
            ),
            _provider(
                "search",
                "edition-2",
                (
                    _claim(
                        "search",
                        "edition-2",
                        ClaimKind.TITLE,
                        "Book",
                        scope=ClaimScope.WORK,
                        order=0,
                    ),
                    _claim(
                        "search",
                        "edition-2",
                        ClaimKind.SERIES,
                        "Reading Order",
                        scope=ClaimScope.WORK,
                        order=1,
                    ),
                    _claim(
                        "search",
                        "edition-2",
                        ClaimKind.SERIES_SEQUENCE,
                        "2",
                        scope=ClaimScope.WORK,
                        order=2,
                    ),
                    _claim(
                        "search",
                        "edition-2",
                        ClaimKind.SERIES,
                        "Publication Order",
                        scope=ClaimScope.WORK,
                        order=3,
                    ),
                    _claim(
                        "search",
                        "edition-2",
                        ClaimKind.SERIES_SEQUENCE,
                        "4",
                        scope=ClaimScope.WORK,
                        order=4,
                    ),
                ),
                authority="open_library",
            ),
        ),
    )
    genre_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.GENRE,
            value,
            scope=ClaimScope.WORK,
            order=index,
        )
        for index, value in enumerate(("Fantasy", "Adventure"))
    )
    tag_claims = tuple(
        _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TAG,
            value,
            scope=ClaimScope.WORK,
            order=index,
        )
        for index, value in enumerate(("Quest", "Found family"))
    )
    description_claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "An exact source-bound description.",
        scope=ClaimScope.WORK,
    )
    genre_report = _proposal_report(
        fields=_fields(
            genres=TargetField(
                TargetDestination.GENRES,
                TargetFieldState.VALUE,
                ("Existing",),
            )
        ),
        providers=(_provider("catalogue", "edition-1", genre_claims),),
    )
    genre_empty_report = _proposal_report(
        fields=_fields(
            genres=TargetField(
                TargetDestination.GENRES,
                TargetFieldState.EMPTY,
            )
        ),
        providers=(_provider("catalogue", "edition-1", genre_claims),),
    )
    tag_empty_report = _proposal_report(
        fields=_fields(
            tags=TargetField(
                TargetDestination.TAGS,
                TargetFieldState.MISSING,
            )
        ),
        providers=(_provider("catalogue", "edition-1", tag_claims),),
    )
    tag_value_report = _proposal_report(
        fields=_fields(
            tags=TargetField(
                TargetDestination.TAGS,
                TargetFieldState.VALUE,
                ("Existing",),
            )
        ),
        providers=(_provider("catalogue", "edition-1", tag_claims),),
    )
    description_report = _proposal_report(
        fields=_fields(
            description=TargetField(
                TargetDestination.DESCRIPTION,
                TargetFieldState.MISSING,
            )
        ),
        providers=(_provider("catalogue", "edition-1", (description_claim,)),),
    )
    cases = (
        *(
            (
                "series",
                option,
                series_report,
                "series",
                {
                    "kind": "series",
                    "value": [
                        {"name": "Reading Order", "sequence": "2"},
                    ],
                },
            )
            for option in ("primary_narrative", "source_order", "all_qualified")
        ),
        (
            "genres",
            "fill_empty",
            genre_empty_report,
            "genres",
            {"kind": "list", "value": ["Fantasy", "Adventure"]},
        ),
        (
            "genres",
            "add",
            genre_report,
            "genres",
            {
                "kind": "list",
                "value": [
                    "Existing",
                    "Fantasy",
                    "Adventure",
                ],
            },
        ),
        (
            "genres",
            "replace",
            genre_report,
            "genres",
            {"kind": "list", "value": ["Fantasy", "Adventure"]},
        ),
        (
            "tags",
            "fill_empty",
            tag_empty_report,
            "tags",
            {"kind": "list", "value": ["Quest", "Found family"]},
        ),
        (
            "tags",
            "add",
            tag_value_report,
            "tags",
            {
                "kind": "list",
                "value": [
                    "Existing",
                    "Quest",
                    "Found family",
                ],
            },
        ),
        (
            "tags",
            "replace",
            tag_value_report,
            "tags",
            {"kind": "list", "value": ["Quest", "Found family"]},
        ),
        (
            "description_fill",
            "safe_plain_text",
            description_report,
            "description",
            {"kind": "text", "value": "An exact source-bound description."},
        ),
        (
            "isbn_form",
            "preserve_exact",
            isbn_family_report,
            "isbn",
            {"kind": "text", "value": "0306406152"},
        ),
        (
            "isbn_form",
            "prefer_isbn13",
            isbn_family_report,
            "isbn",
            {"kind": "text", "value": "9780306406157"},
        ),
    )
    for category, option, report, destination, expected in cases:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            options = dict(DEFAULT_OPTIONS)
            options[category] = option
            destinations = {
                name: "unsupported" if name == "explicit" else "ask"
                for name in OPTION_CATALOGUE
            }
            destinations[category] = "automatic"
            profile = MetadataPolicyProfile.create(
                "F20 automatic reachability", options, destinations
            )
            resolved = resolve_policy(profile, report.target.library)
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
            )
            repository.activate_qualified_automatic(3)
            proposal = evaluate_report(
                report,
                resolved.resolved_id,
                human_review_destinations(resolved),
            )
            if category == "series":
                series_result = next(
                    value
                    for value in proposal.results
                    if value.destination is TargetDestination.SERIES
                )
                assert 2 == len(series_result.alternatives[0].value.value)
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))[
                "review_case_id"
            ]
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(case_id, 4)
            )
            field = next(
                value for value in outcome.outcomes if value.destination == destination
            )
            assert (category, option, "automatic", "candidate") == (
                field.policy_category,
                field.policy_option,
                field.policy_destination,
                field.disposition,
            )
            rows = repository._connection.execute(
                "SELECT document FROM automatic_decisions WHERE "
                "review_case_id=? AND destination=?",
                (case_id, destination),
            ).fetchall()
            assert 1 == len(rows)
            decision = AutomaticDecision.from_json(rows[0][0])
            authority = repository.field_effect_authority_source(decision)
            assert expected == authority["decision_value"]
            assert destination == authority["destination"]
            assert case_id == authority["review_case_id"]
            repository.grant_field_effect_consents(
                authority["connection"],
                (authority["library"],),
                (destination,),
                5,
            )
            target = FakeTargetService()
            service = GuardedFieldApplication(
                repository,
                target,
                FakeBackupReadiness(),
                FakeCapabilityObserver("c" * 64),
                PersistedFieldEffectAuthority(repository),
                FakeDependencyCurrentness(),
                PersistedEffectConsent(repository),
                SimpleNamespace(),
                FakeForwardReevaluation(),
                lambda: 5,
            )
            plan = service.plan(
                decision,
                connection=authority["connection"],
                library=authority["library"],
                work_id=authority["work_id"],
                destination=destination,
                pre_value=authority["pre_value"],
                desired_value=expected,
                target_fingerprint=authority["target_fingerprint"],
                target_version=authority["target_version"],
                dependency_digests=authority["dependency_digests"],
                capability_digest="c" * 64,
            )
            assert plan is not None
            assert plan is not None
            assert expected["value"] == plan.plan.desired_value
            target.seed(plan.plan)
            assert "verified" == service.apply(plan.plan.plan_id).state
            assert expected["value"] == target.read(plan.plan).value
            repository.close()


def test_outcome_accepts_packet_valid_configured_provider_citations() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        base = ShadowOutcomeSet.from_json(repository.persist_shadow_outcome(case_id, 2))
        repository.close()
    field = base.outcomes[0]
    citations = ("hardcover_" + "a" * 64,)
    qualification_candidate_id = "qualification_candidate_" + _digest(
        {
            "action_id": field.action_id,
            "candidate_id": field.candidate_id,
            "catalogue_sha256": base.catalogue_sha256,
            "citation_ids": list(citations),
            "combination_ids": list(field.combination_ids),
            "rule_id": field.rule_id,
        }
    )
    identity = field._identity_data()
    identity["citation_ids"] = list(citations)
    identity["qualification_candidate_id"] = qualification_candidate_id
    projected = replace(
        field,
        outcome_id="shadow_outcome_" + _digest(identity),
        citation_ids=citations,
        qualification_candidate_id=qualification_candidate_id,
    )
    assert citations == projected.citation_ids


def test_preview_automatic_matrix_preserves_human_policy_and_records_outcomes() -> None:
    publisher = LocalClaim.create(
        kind=ClaimKind.PUBLISHER,
        observed="Selected EPUB Press",
        canonical="Selected EPUB Press",
        excerpt="Selected EPUB Press",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Secret History",
        canonical="Secret History",
        excerpt="Secret History",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    source = _source(
        "catalogue",
        "edition-1",
        titles=("Secret History",),
        subtitle="A Mistborn Novella",
    )
    reports = (
        _report(),
        _proposal_report(
            title=None,
            fields=_fields(None),
            local_claims=(publisher,),
        ),
        _bibliographic_report(current_title="secret history", providers=(source,)),
        _bibliographic_report(
            current_title="secret history",
            providers=(source,),
            local_claims=(local_title,),
        ),
    )
    recorded: set[str] = set()
    for report in reports:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            profile = _preview_automatic_profile()
            repository.enter_shadow_preview(1)
            repository.create_metadata_policy_draft(profile.canonical_json)
            resolved = resolve_policy(profile, report.target.library)
            repository.activate_metadata_policy(
                profile.policy_id,
                MetadataPolicyProfile.builtin().policy_id,
                (
                    (
                        report.target.library,
                        resolved.resolved_id,
                        resolved.canonical_json,
                    ),
                ),
                2,
            )
            effective = human_review_destinations(resolved)
            assert "ask" == effective["title_layout"]
            assert "ignore" == effective["tags"]
            assert "unsupported" == effective["explicit"]
            manual_profile = MetadataPolicyProfile.create(
                "Equivalent human review",
                dict(profile.options),
                effective,
            )
            equivalent = resolve_policy(manual_profile, report.target.library)
            proposal = evaluate_report(report, resolved.resolved_id, effective)
            manual_proposal = evaluate_report(
                report,
                equivalent.resolved_id,
                dict(equivalent.destinations),
            )
            assert proposal.results == manual_proposal.results
            assert (
                proposal.bibliographic_combinations
                == manual_proposal.bibliographic_combinations
            )
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            assert "admitted" == admitted["status"]
            default_packet = repository.preview_shadow_packet(
                admitted["review_case_id"]
            )
            preview_packet = repository._persisted_shadow_packet_source(
                admitted["review_case_id"], include_policy_ignored=True
            )[1]
            assert "tags" not in {
                value["destination"] for value in default_packet.proposals
            }
            assert "tags" in {
                value["destination"] for value in preview_packet.proposals
            }
            outcome = ShadowOutcomeSet.from_json(
                repository.persist_shadow_outcome(admitted["review_case_id"], 3)
            )
            recorded.update(value.disposition for value in outcome.outcomes)
            assert "none" == outcome.authority
            abstentions = [
                value for value in outcome.outcomes if value.disposition == "abstention"
            ]
            for abstention in abstentions:
                detail = InspectCurrentReviewCase(repository).run(
                    admitted["review_case_id"]
                )
                field = next(
                    value
                    for value in detail["fields"]
                    if value["destination"] == abstention.destination
                )
                assert "manual_fallback" == field["classification"]
                assert field["needs_attention"]
                assert 1 == repository.count_current_attention_fields(None)
                assert (1, 1) == repository.count_current_review_cases(None)
                attention = repository.read_review_attention(None, 50, 0)
                assert 1 == attention.total
                assert [abstention.destination] == [row[2] for row in attention.rows]
            repository.close()
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        report = _report()
        profile = MetadataPolicyProfile.create(
            "Manual review",
            dict(DEFAULT_OPTIONS),
            {
                category: "unsupported" if category == "explicit" else "ask"
                for category in OPTION_CATALOGUE
            },
        )
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.publish(report, lambda: True)
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(admitted["review_case_id"], 3)
        )
        recorded.update(value.disposition for value in outcome.outcomes)
        repository.close()
    assert {
        "candidate",
        "keep_current",
        "conflict",
        "abstention",
        "unsupported",
        "ignore",
        "manual",
    } == recorded


def test_composed_projection_succeeds_replays_and_reports_stale() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        provider_recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=provider_recovery,
        )

        assert "complete" == supervisor._pipeline._project(repository, case_id)
        assert "complete" == supervisor._pipeline._project(repository, case_id)
        current = _report("Current title")
        prior_report_id = json.loads(repository.get_review_case(case_id))["case"][
            "report_id"
        ]
        repository.publish(current, lambda: True, prior_report_id)
        assert "stale" == supervisor._pipeline._project(repository, case_id)
        assert 1 == len(repository.list_shadow_outcomes(50))
        supervisor.stop()
        repository.close()


def test_composed_projection_prepares_one_qualified_cover_advice_before_f22() -> None:
    """Ordinary F22 composition persists advice before its package projection.

    The synthetic runner is the only observer boundary in this test.  The
    pipeline still composes its real consideration service and repository,
    so replay can prove that neither restart nor projection replay dispatches
    a second observer or gains cover-effect authority.
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        payloads = (_cover_preview(1), _cover_preview(2))
        sources = (
            _cover_source(1, payloads[0], incumbent=True),
            _cover_source(2, payloads[1], embedded=True),
        )
        base = _cover_report(sources)
        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", base.target.fields),
            file=base.file,
            local_claims=base.local_claims,
            local_reasons=base.local_reasons,
            providers=base.providers,
            cover_sources=base.cover_sources,
        )
        artifacts = CoverArtifactStore(root / "artifacts")
        for index, (source, payload) in enumerate(
            zip(sources, payloads, strict=True), 1
        ):
            assert source.artifact is not None
            artifacts.stage(source.artifact, f"original-{index}".encode(), payload)
        proposal = evaluate_report(report)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []

        def reviewer(*args):
            calls.append(args)
            return _cover_observer_result(*args)

        with (
            patch.object(
                composition,
                "read_contained_reviewer_profile",
                return_value=b"synthetic-profile",
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=reviewer
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/synthetic-profile"
                ),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                database_authority=repository.authority,
                provider_recovery=recovery,
            )

            def extract(bound, action, claim_next):
                bound.publish(
                    report,
                    lambda: True,
                    after_publication=lambda current, published: (
                        current.complete_review_extraction_publication(
                            action, published.report_id, 10, 0, None, claim_next, None
                        )
                    ),
                )
                return report.report_id, True

            def project(bound, action):
                assert (0,) == bound._connection.execute(
                    "SELECT count(*) FROM current_review_packages"
                ).fetchone()
                document = bound.current_cover_consideration_document(
                    action.review_case_id or ""
                )
                assert document is not None
                assert document is not None
                assert "completed" == json.loads(document)["reviewer_status"]
                outcome = bound.persist_shadow_outcome(
                    action.review_case_id or "", 10, staged_action=action
                )
                return "stale" if outcome == "stale" else "complete"

            pipeline = ReviewPreparationPipeline(
                repository_factory=lambda: open_repository(
                    database, authority=repository.authority
                ),
                libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
                discover=lambda *_: (
                    1,
                    report.file.content_digest,
                    (
                        CatalogueEntry(
                            "primary",
                            "ebooks",
                            "lib-1",
                            "item-1",
                            1,
                            "Synthetic cover",
                            (),
                            True,
                            False,
                            "singular-supported",
                            report.file.identity,
                            "/item-1.epub",
                            "epub",
                            "books",
                        ),
                    ),
                ),
                extract=extract,
                providers=lambda *_: (),
                propose=lambda bound, _: (
                    bound.publish_proposal(proposal),
                    proposal.proposal_set_id,
                )[1],
                admit=lambda bound, _: (
                    lambda value: (value["status"], value.get("review_case_id"))
                )(json.loads(bound.admit_review_case(proposal))),
                project=project,
                before_projection=supervisor._pipeline._before_projection,
                after_advance=supervisor._pipeline._after_advance,
                clock=lambda: 10,
            )
            pipeline.request("primary", "ebooks")
            advanced = [pipeline.run_next(1) for _ in range(6)]
            assert [True, True, True, True, True, False] == advanced, (
                repository._connection.execute(
                    "SELECT stage,state,failure_code FROM review_preparation_work"
                ).fetchone()
            )
            case_id = repository._connection.execute(
                "SELECT review_case_id FROM current_review_packages"
            ).fetchone()[0]
            first = repository.current_cover_consideration_document(case_id)
            assert first is not None
            assert (1,) == repository._connection.execute(
                "SELECT count(*) FROM current_review_packages"
            ).fetchone()
            assert (1,) == repository._connection.execute(
                "SELECT count(*) FROM provider_recovery_outbox"
            ).fetchone()
            assert "completed" == json.loads(first)["reviewer_status"]
            assert 1 == len(calls)
            assert not pipeline.run_next(1)
            assert first == repository.current_cover_consideration_document(case_id)
            assert 1 == len(calls)
            assert (
                0
                == repository._connection.execute(
                    "SELECT count(*) FROM field_effects"
                ).fetchone()[0]
            )
            supervisor.stop()
        repository.close()


def test_composed_projection_without_reviewer_persists_unavailable_before_f22() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        assert "unavailable" == json.loads(document)["reviewer_status"]
        dispatch.assert_not_called()
        considerations = composition._compose_cover_considerations(
            repository, root / "artifacts", _pipeline_configuration().review
        )
        _assert_manual_cover_surface(repository, case_id, considerations, "unavailable")
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


@pytest.mark.parametrize(
    "reviewer",
    (
        pytest.param(lambda *_: '{"status":"refused"}', id="refusal"),
        pytest.param(lambda *_: "not-json", id="malformed"),
        pytest.param(
            lambda *_: (_ for _ in ()).throw(RuntimeError("contained")),
            id="exception",
        ),
    ),
)
def test_composed_projection_contains_reviewer_failures_before_f22(reviewer) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []

        def dispatch(*args):
            calls.append(args)
            return reviewer(*args)

        with (
            patch.object(
                composition, "read_contained_reviewer_profile", return_value=b"profile"
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=dispatch
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages"
        ).fetchone()[0]
        document = repository.current_cover_consideration_document(case_id)
        assert document is not None
        assert "failed" == json.loads(document)["reviewer_status"]
        assert 1 == len(calls)
        with patch.object(
            composition,
            "read_contained_reviewer_profile",
            return_value=b"profile",
        ):
            considerations = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile"
                ).review,
            )
        _assert_manual_cover_surface(repository, case_id, considerations, "failed")
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_stale_after_observation_does_not_publish() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )

        def drift(*_args):
            repository._connection.execute(
                "UPDATE metadata_policy_state SET generation=generation+1 WHERE singleton=1"
            )
            repository._connection.commit()
            return _cover_observer_result(*_args)

        with (
            patch.object(
                composition, "read_contained_reviewer_profile", return_value=b"profile"
            ),
            patch.object(composition, "run_cover_packet_reviewer", side_effect=drift),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
            assert pipeline.run_next(1)
        assert ("queued", "stale") == repository._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_cover_persistence_failure_does_not_publish() -> None:
    """A store fault in the cover stage is durably readable as one (#734)."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        pipeline = _run_qualified_cover_pipeline(
            repository, root, report, proposal, supervisor
        )
        assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
        with patch.object(
            type(repository),
            "record_cover_consideration",
            side_effect=MetadataReportError("persistence"),
        ):
            assert pipeline.run_next(1)
        assert ("failed", "persistence", None) == repository._connection.execute(
            "SELECT state,failure_code,failure_root FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_records_a_broken_factory_without_cover_effects() -> None:
    """A broken consideration factory fails the stage and leaves nothing behind.

    The factory `create_review_pipeline` binds is
    `_compose_cover_considerations`, which writes through the repository
    twice while it builds the service -- `bind_current_cover_policy` before
    construction and `bind_current_cover_replay` after it. A fault it raises
    is therefore not separable from the rest of the cover stage, and the
    stage answers `persistence` for it like the rest of its body. That code
    is now in `review_preparation_work.failure_code`'s allow-list, so the
    durable record names the side that failed instead of the anonymous
    `failed` every unlisted code normalised into, and this read is what
    proves it survives the shipped adapter's normalisation and its CHECK
    (#698, #734). No consideration, package, or outbox row survives the
    failure either way.
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        pipeline = _run_qualified_cover_pipeline(
            repository, root, report, proposal, supervisor
        )
        assert [True, True, True, True] == [pipeline.run_next(1) for _ in range(4)]
        with patch.object(
            composition,
            "_compose_cover_considerations",
            side_effect=TypeError("consideration service is broken"),
        ):
            assert pipeline.run_next(1)
        assert ("failed", "persistence", None) == repository._connection.execute(
            "SELECT state,failure_code,failure_root FROM review_preparation_work "
            "WHERE stage='projection'"
        ).fetchone()
        for table in (
            "cover_considerations",
            "current_cover_considerations",
            "current_review_packages",
            "provider_recovery_outbox",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_cover_replay_reuses_exact_binding_and_profile_change_supersedes_once() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report, proposal = _qualified_cover_report(root)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        calls: list[object] = []

        def reviewer(*args):
            calls.append(args)
            return _cover_observer_result(*args)

        with (
            patch.object(
                composition,
                "read_contained_reviewer_profile",
                side_effect=(b"profile-1", b"profile-1", b"profile-2"),
            ),
            patch.object(
                composition, "run_cover_packet_reviewer", side_effect=reviewer
            ),
        ):
            supervisor = composition.create_review_pipeline(
                _pipeline_configuration(cover_reviewer_profile_file="/run/profile-1"),
                database,
                root / "catalogue.sqlite",
                root / "artifacts",
                database_authority=repository.authority,
                provider_recovery=recovery,
            )
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            assert [True, True, True, True, True, False] == [
                pipeline.run_next(1) for _ in range(6)
            ]
            case_id = repository._connection.execute(
                "SELECT review_case_id FROM current_review_packages"
            ).fetchone()[0]
            first = repository.current_cover_consideration_document(case_id)
            cover_proposal_id = json.loads(first)["proposal_id"]
            replay = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile-1"
                ).review,
            ).ensure(case_id, report.to_json(), {"proposal_id": cover_proposal_id})
            successor = composition._compose_cover_considerations(
                repository,
                root / "artifacts",
                _pipeline_configuration(
                    cover_reviewer_profile_file="/run/profile-2"
                ).review,
            ).ensure(case_id, report.to_json(), {"proposal_id": cover_proposal_id})
        assert 2 == len(calls)
        assert json.loads(first)["consideration_id"] == replay["consideration_id"]
        assert json.loads(first)["consideration_id"] == successor["supersedes_id"]
        assert (2,) == repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone()
        _assert_no_cover_effect_authority(repository)
        supervisor.stop()
        repository.close()


def test_composed_projection_without_cover_skips_advice_and_completes_f22() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        report = _report("No cover proposal")
        proposal = evaluate_report(report)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(cover_reviewer_profile_file="/run/profile"),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=recovery,
        )

        def extract(bound, action, claim_next):
            bound.publish(
                report,
                lambda: True,
                after_publication=lambda current, published: (
                    current.complete_review_extraction_publication(
                        action,
                        published.report_id,
                        10,
                        0,
                        None,
                        claim_next,
                        None,
                    )
                ),
            )
            return report.report_id, True

        def project(bound, action):
            assert (
                bound.current_cover_consideration_document(action.review_case_id or "")
                is None
            )
            outcome = bound.persist_shadow_outcome(
                action.review_case_id or "",
                10,
                staged_action=action,
            )
            return "stale" if outcome == "stale" else "complete"

        pipeline = ReviewPreparationPipeline(
            repository_factory=lambda: open_repository(
                database, authority=repository.authority
            ),
            libraries=(("primary", "ebooks", "lib-1", "f" * 64),),
            discover=lambda *_: (
                1,
                report.file.content_digest,
                (
                    CatalogueEntry(
                        "primary",
                        "ebooks",
                        "lib-1",
                        "item-1",
                        1,
                        "No cover proposal",
                        (),
                        True,
                        False,
                        "singular-supported",
                        report.file.identity,
                        "/item-1.epub",
                        "epub",
                        "books",
                    ),
                ),
            ),
            extract=extract,
            providers=lambda *_: (),
            propose=lambda bound, _: (
                bound.publish_proposal(proposal),
                proposal.proposal_set_id,
            )[1],
            admit=lambda bound, _: (
                lambda value: (value["status"], value.get("review_case_id"))
            )(json.loads(bound.admit_review_case(proposal))),
            project=project,
            before_projection=supervisor._pipeline._before_projection,
            after_advance=supervisor._pipeline._after_advance,
            clock=lambda: 10,
        )
        pipeline.request("primary", "ebooks")
        assert [True, True, True, True, True, False] == [
            pipeline.run_next(1) for _ in range(6)
        ]
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM current_review_packages"
        ).fetchone()
        assert (1,) == repository._connection.execute(
            "SELECT count(*) FROM provider_recovery_outbox"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone()
        assert not pipeline.run_next(1)
        supervisor.stop()
        repository.close()


def test_review_posture_composed_stale_projection_restarts_instead_of_completing() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        provider_recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        current_proposal = evaluate_report(current_report)
        repository.publish_proposal(current_proposal)
        repository.admit_review_case(current_proposal)
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            database_authority=repository.authority,
            provider_recovery=provider_recovery,
        )

        assert "automatic" == repository.shadow_posture()
        assert "stale" == supervisor._pipeline._project(repository, old_case_id)
        assert "stale" == supervisor._pipeline._project(repository, old_case_id)
        assert (
            "proposal",
            "queued",
            current_report.report_id,
            None,
            None,
        ) == repository._connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id "
            "FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert () == repository.list_shadow_outcomes(50)
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work"
        ).fetchone()
        supervisor.stop()
        repository.close()


def test_automatic_bootstrap_and_preview_replay_one_exact_set() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)

        assert "automatic" == repository.shadow_posture()
        bootstrap = repository.metadata_policy_transition()
        assert bootstrap is not None
        assert bootstrap is not None
        assert not bootstrap.acknowledged
        assert not hasattr(repository, "record_automatic_decision")
        automatic = repository.persist_shadow_outcome(case_id, 10)
        assert automatic.startswith('{"authority":"none"')
        assert ((automatic, 10),) == repository.list_shadow_outcomes(50)

        assert "preview" == repository.enter_shadow_preview(11)
        first = repository.persist_shadow_outcome(case_id, 12)
        second = repository.persist_shadow_outcome(case_id, 13)
        assert first == second
        outcome = ShadowOutcomeSet.from_json(first)
        assert "none" == outcome.authority
        assert "preview" == outcome.posture
        assert case_id == outcome.review_case_id
        assert {value.disposition for value in outcome.outcomes}.issubset(
            {
                "candidate",
                "keep_current",
                "abstention",
                "conflict",
                "manual",
                "unsupported",
            }
        )
        assert all(
            value.qualification_candidate_id.startswith("qualification_candidate_")
            for value in outcome.outcomes
        )
        assert len(outcome.outcomes) == len(
            {value.qualification_candidate_id for value in outcome.outcomes}
        )
        assert ((first, 10),) == repository.list_shadow_outcomes(50)
        history = repository.get_review_case_history(case_id, 50)
        assert "shadow_preview_recorded" == history[0]["kind"]
        assert "none" == history[0]["authority"]
        repository.close()

        connection = sqlite3.connect(path)
        assert (1,) == connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE shadow_decision_outcomes SET evaluated_at=99")
        connection.close()


def test_preview_reconciliation_failure_rolls_back_the_posture_and_automatic_rows() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        with patch.object(
            repository,
            "activate_metadata_policy",
            side_effect=MetadataReportError("reconciliation failed"),
        ):
            with pytest.raises(MetadataReportError, match="reconciliation failed"):
                repository.enter_shadow_preview(3)
        assert "automatic" == repository.shadow_posture()
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.close()


def test_automatic_safety_update_preserves_a_caller_transaction_and_restart() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        repository._connection.execute("BEGIN IMMEDIATE")
        repository.set_automatic_safety_state(
            AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
            9,
        )
        assert repository._connection.in_transaction
        repository._connection.commit()
        repository.close()
        reopened = open_repository(path)
        assert (
            AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),))
            == reopened.automatic_safety_state()
        )
        reopened.close()


def test_automatic_authority_binding_failure_is_global_and_keeps_shadow_projection() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)
        # The construction seam reports malformed manifest/source/identity
        # bindings as an authority error. It must fail closed globally,
        # while the report-only observation survives the savepoint rollback.
        with patch.object(
            repository,
            "_record_automatic_outcomes",
            side_effect=AutomaticDecisionError("source_binding_invalid"),
        ):
            persisted = repository.persist_shadow_outcome(case_id, 9)
        assert persisted.startswith('{"authority":"none"')
        assert (
            "automatic_authority_failed"
            == repository.automatic_safety_state().global_suspension
        )
        assert () == repository.automatic_safety_state().category_suspensions
        assert ((persisted, 9),) == repository.list_shadow_outcomes(50)
        repository.close()
        reopened = open_repository(path)
        assert (
            "automatic_authority_failed"
            == reopened.automatic_safety_state().global_suspension
        )
        reopened.close()


def test_explicit_bundle_safety_failure_is_category_scoped() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        # Category is reserved for the bundle-coherence classifier; it
        # must not become the fallback for authority-binding failures.
        with patch.object(
            repository,
            "_record_automatic_outcomes",
            side_effect=AutomaticDecisionError("category:title_layout"),
        ) as admission:
            repository.persist_shadow_outcome(case_id, 10)
        assert 2 == admission.call_count
        safety = repository.automatic_safety_state()
        assert safety.global_suspension is None
        assert (
            ("title_layout", "automatic_admission_failed"),
        ) == safety.category_suspensions
        repository.close()


def test_shadow_history_orders_human_and_preview_events_by_time() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        repository.persist_shadow_outcome(case_id, 5)
        # A second deterministic set is a replay, so it cannot manufacture a
        # newer chronology merely because delivery repeats.
        repository.persist_shadow_outcome(case_id, 50)
        events = repository.get_review_case_history(case_id, 50)
        shadow = [
            value for value in events if value["kind"] == "shadow_preview_recorded"
        ]
        assert [5] == [value["occurred_at"] for value in shadow]
        repository.close()


def test_sample_bounds_limit_fifty_at_maximum_reference_cardinality() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        outcome = _maximum_reference_outcome(repository)
        repository.close()

    class SampleRepository:
        def list_shadow_outcomes(self, limit):
            return tuple((outcome.to_json(), index) for index in range(limit))

    sample = PolicyNotebook(SampleRepository(), (), {}).shadow_sample(50)
    serialized = json.dumps(
        sample, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert len(sample["items"]) > 0
    assert len(sample["items"]) < 50
    assert len(serialized) <= MAX_SHADOW_SAMPLE_RESPONSE_BYTES
    first = sample["items"][0]["outcomes"][0]
    assert 256 == len(first["citation_ids"])
    assert 256 == len(first["combination_ids"])


def test_canonical_hostile_shadow_row_fails_closed_without_exposure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.enter_shadow_preview(1)
        persisted = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 2)
        ).to_data()
        field = dict(persisted["outcomes"][0])
        field.update(
            {
                "destination": "/private/owner/library",
                "policy_category": None,
                "policy_destination": "unsupported",
                "policy_option": None,
                "disposition": "unsupported",
            }
        )
        field["rule_id"] = "shadow_rule_" + _digest(
            {
                "category": None,
                "decision_policy_sha256": persisted["decision_policy_sha256"],
                "decision_policy_version": persisted["decision_policy_version"],
                "destination": "unsupported",
                "option": None,
            }
        )
        field["qualification_candidate_id"] = "qualification_candidate_" + _digest(
            {
                "action_id": field["action_id"],
                "candidate_id": field["candidate_id"],
                "catalogue_sha256": persisted["catalogue_sha256"],
                "citation_ids": field["citation_ids"],
                "combination_ids": field["combination_ids"],
                "rule_id": field["rule_id"],
            }
        )
        field_identity = dict(field)
        del field_identity["outcome_id"]
        field["outcome_id"] = "shadow_outcome_" + _digest(field_identity)
        persisted["outcomes"][0] = field
        outcome_identity = dict(persisted)
        del outcome_identity["outcome_set_id"]
        persisted["outcome_set_id"] = "shadow_outcome_set_" + _digest(outcome_identity)
        hostile = json.dumps(
            persisted,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        repository._connection.execute(
            "DROP TRIGGER shadow_decision_outcomes_no_update"
        )
        repository._connection.execute(
            "UPDATE shadow_decision_outcomes SET document=?",
            (hostile,),
        )
        repository._connection.commit()

        with pytest.raises(
            Exception, match="shadow sample is unavailable"
        ) as sample_error:
            PolicyNotebook(repository, (), {}).shadow_sample(50)
        with pytest.raises(
            Exception, match="review history is unavailable"
        ) as history_error:
            repository.get_review_case_history(case_id, 50)
        assert "/private/owner/library" not in str(sample_error.value)
        assert "/private/owner/library" not in str(history_error.value)
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        repository.close()


def test_report_only_currentness_race_restarts_the_same_projection_row() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        connection = repository._connection
        repository.enter_shadow_preview(2)

        assert (old_case["report_id"], old_case_id) == connection.execute(
            "SELECT report_id,review_case_id FROM current_review_case_lineages"
        ).fetchone()
        assert "stale" == repository.persist_shadow_outcome(old_case_id, 3)
        assert "stale" == repository.persist_shadow_outcome(old_case_id, 4)
        assert () == repository.list_shadow_outcomes(50)
        assert (
            "proposal",
            "queued",
            current_report.report_id,
            None,
            None,
        ) == connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (1,) == connection.execute(
            "SELECT COUNT(*) FROM review_preparation_work"
        ).fetchone()
        repository.close()


def test_old_claim_cannot_project_same_ids_at_newer_lineage_checkpoint() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        work_id = _running_projection(repository, case_id)
        action = _staged_projection_action(repository, work_id)
        pointer = repository._connection.execute(
            "SELECT proposal_set_id,report_id,transition_id,checkpoint FROM "
            "current_review_case_lineages WHERE connection_name='primary' "
            "AND library_name='ebooks' AND item_id='item-1'"
        ).fetchone()
        assert pointer is not None
        successor = ReviewTransition(
            "primary",
            "ebooks",
            "item-1",
            pointer[3] + 1,
            pointer[0],
            pointer[1],
            "superseded",
            case_id,
            case_id,
            "actionable",
        )
        repository._connection.execute(
            "INSERT INTO review_case_transitions(transition_id,connection_name,"
            "library_name,item_id,checkpoint,actor,from_review_case_id,"
            "to_review_case_id,proposal_set_id,report_id,kind,reason) "
            "VALUES(?,?,?,?,?,'review-admission-v1',?,?,?,?,?,'actionable')",
            (
                successor.transition_id,
                "primary",
                "ebooks",
                "item-1",
                pointer[3] + 1,
                case_id,
                case_id,
                pointer[0],
                pointer[1],
                "superseded",
            ),
        )
        repository._connection.execute(
            "UPDATE current_review_case_lineages SET transition_id=?,checkpoint=?,outcome='superseded' "
            "WHERE connection_name='primary' AND library_name='ebooks' AND item_id='item-1'",
            (successor.transition_id, pointer[3] + 1),
        )
        repository._connection.execute(
            "UPDATE current_review_packages SET checkpoint=?,outcome='superseded' "
            "WHERE connection_name='primary' AND library_name='ebooks' AND item_id='item-1'",
            (pointer[3] + 1,),
        )
        repository._connection.execute(
            "DELETE FROM current_review_packages WHERE connection_name='primary' "
            "AND library_name='ebooks' AND item_id='item-1'"
        )
        repository._connection.commit()
        repository.enter_shadow_preview(2)

        # The old claim is tied to generation one.  Reconcile the active
        # policy before the package exists, so an old projection cannot
        # create a generation-two observation or automatic decision.
        profile = _preview_automatic_profile()
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, "ebooks")
        transition = repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            (("ebooks", resolved.resolved_id, resolved.canonical_json),),
            3,
            reconcile_current=True,
        )
        assert transition is not None
        assert 2 == transition.generation

        assert "stale" == repository.persist_shadow_outcome(
            case_id, 4, staged_action=action
        )
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM shadow_decision_outcomes"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        with pytest.raises(
            MetadataReportError, match="review preparation state changed"
        ):
            repository.advance_review_pipeline_action(action, None, 5)
        assert ("projection", "running") == repository._connection.execute(
            "SELECT stage,state FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_packages"
        ).fetchone()
        repository.fail_review_pipeline_action(action, "stale", True, 5)
        assert ("queued", "stale") == repository._connection.execute(
            "SELECT state,failure_code FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_policy_transition_items"
        ).fetchone()
        repository.close()


def test_stale_recovery_currentness_failure_rolls_back_owned_transaction() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        old_case_id = _case(repository)
        old_case = json.loads(repository.get_review_case(old_case_id))["case"]
        work_id = _running_projection(repository, old_case_id)
        current_report = _report("Current title")
        repository.publish(current_report, lambda: True, old_case["report_id"])
        repository._connection.execute(
            "UPDATE current_metadata_reports SET report_id=?",
            (old_case["report_id"],),
        )
        repository._connection.commit()

        with pytest.raises(
            Exception, match="shadow outcome currentness is unavailable"
        ):
            repository.persist_shadow_outcome(old_case_id, 3)
        assert not repository._connection.in_transaction
        assert (
            "projection",
            "running",
            old_case["report_id"],
            old_case["proposal_set_id"],
            old_case_id,
        ) == repository._connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id "
            "FROM review_preparation_work WHERE work_id=?",
            (work_id,),
        ).fetchone()
        repository._connection.execute("BEGIN IMMEDIATE")
        repository._connection.rollback()
        repository.close()


def test_human_authority_supersedes_automatic_without_global_suspension() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository.record_human_decision(_human_title_decision(repository, case_id, 1))
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        assert (1, 0) == tuple(
            repository._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),"
                "(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        )
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 2
        )
        repository.record_human_decision(_human_title_decision(repository, case_id, 3))
        assert (1, 1) == tuple(
            repository._connection.execute(
                "SELECT (SELECT COUNT(*) FROM human_decisions),(SELECT COUNT(*) FROM automatic_decisions)"
            ).fetchone()
        )
        assert ("primary", "ebooks") == repository.field_effect_manual_hold_context(
            case_id, "title"
        )
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()


def test_automatic_tuple_replay_is_exact_and_date_and_title_bundles_are_atomic() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        case_id = _case(repository)
        title = _synthetic_automatic_tuple(repository, case_id, "title_layout")
        dates = _synthetic_automatic_tuple(repository, case_id, "date_precision")
        repository._record_automatic_outcomes(title, 7)
        first = repository.get_review_case_decisions(case_id)
        repository._record_automatic_outcomes(title, 99)
        repository._record_automatic_outcomes(dates, 8)
        documents = tuple(
            json.loads(value) for value in repository.get_review_case_decisions(case_id)
        )
        assert first == repository.get_review_case_decisions(case_id)[:1]
        assert 2 == len(documents)
        assert "publishedYear" not in {value["destination"] for value in documents}
        for destinations in (("title",), ("publishedDate",)):
            bundle = [
                value for value in documents if value["destination"] in destinations
            ]
            assert set(destinations) == {value["destination"] for value in bundle}
            assert 1 == len({value["bundle_id"] for value in bundle})
        repository.close()
        reopened = open_repository(path)
        assert tuple(value["decision_id"] for value in documents) == tuple(
            json.loads(value)["decision_id"]
            for value in reopened.get_review_case_decisions(case_id)
        )
        reopened.close()


def test_incoherent_automatic_tuple_suspends_only_its_category_and_safety_recovery_restores_admission() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        incoherent = _synthetic_automatic_tuple(repository, case_id, "title_layout")
        incoherent.outcomes[0].combination_ids = ()
        with pytest.raises(AutomaticDecisionError, match="category:title_layout"):
            repository._record_automatic_outcomes(incoherent, 3)
        repository.set_automatic_safety_state(
            AutomaticSafetyState(
                False, None, (("title_layout", "automatic_admission_failed"),)
            ),
            4,
        )
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 5
        )
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 6)
        repository._record_automatic_outcomes(
            _synthetic_automatic_tuple(repository, case_id, "title_layout"), 7
        )
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        repository.close()


def test_suspended_bundle_does_not_block_an_unrelated_automatic_category() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        outcome = _synthetic_automatic_tuple(repository, case_id, "date_precision")
        outcome.outcomes += (
            SimpleNamespace(
                disposition="candidate",
                policy_category="language_rendering",
                policy_option="display_name",
                policy_destination="automatic",
                destination="language",
                citation_ids=("local_" + "9" * 64,),
                combination_ids=(),
                proposal_id="review_proposal_" + "9" * 64,
                candidate_id="alternative_" + "8" * 64,
                rule_id="shadow_rule_" + "7" * 64,
                outcome_id="shadow_outcome_" + "6" * 64,
            ),
        )
        repository.set_automatic_safety_state(
            AutomaticSafetyState(
                False,
                None,
                (("date_precision", "automatic_admission_failed"),),
            ),
            3,
        )
        repository._record_automatic_outcomes(outcome, 4)
        assert ("language",) == tuple(
            value[0]
            for value in repository._connection.execute(
                "SELECT destination FROM automatic_decisions"
            ).fetchall()
        )
        repository.close()


def test_nonautomatic_policy_destination_never_constructs_an_automatic_decision() -> (
    None
):
    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        case_id = _case(repository)
        ignored = SimpleNamespace(
            outcomes=(
                SimpleNamespace(
                    disposition="candidate",
                    policy_category="tags",
                    policy_option="catalogue",
                    policy_destination="ignore",
                    destination="tags",
                    combination_ids=(),
                    proposal_id="review_proposal_" + "a" * 64,
                    candidate_id="alternative_" + "b" * 64,
                    rule_id="shadow_rule_" + "c" * 64,
                    outcome_id="shadow_outcome_" + "d" * 64,
                ),
            ),
            review_case_id=case_id,
            report_id=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).report_id,
            proposal_set_id=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).proposal_set_id,
            target_fingerprint=ReviewCase.create_from_data(
                json.loads(repository.get_review_case(case_id))["case"]
            ).target_fingerprint,
            resolved_policy_id=repository.current_resolved_policy_id("ebooks"),
            policy_generation=1,
        )
        repository._record_automatic_outcomes(ignored, 2)
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        assert (
            AutomaticSafetyState(False, None, ()) == repository.automatic_safety_state()
        )
        repository.close()


def test_generic_subtitle_preservation_records_only_the_title_no_op() -> None:
    report = _bibliographic_report(
        current_title="Current title",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Current title",),
                subtitle="A Novel",
            ),
        ),
    )
    profile = _preview_automatic_profile()

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 2)

        persisted = repository.persist_shadow_outcome(case_id, 3)
        outcome = ShadowOutcomeSet.from_json(persisted)

        assert persisted == repository.persist_shadow_outcome(case_id, 4)
        assert [("title", "keep_current")] == [
            (value.destination, value.disposition)
            for value in outcome.outcomes
            if value.destination == "title"
        ]
        assert ("title", "keep_current") == repository._connection.execute(
            "SELECT destination,document ->> '$.outcome' FROM automatic_decisions"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='subtitle'"
        ).fetchone()
        assert 0 == repository.count_current_attention_fields(None)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        title = next(
            value for value in detail["fields"] if value["destination"] == "title"
        )
        subtitle = next(
            value for value in detail["fields"] if value["destination"] == "subtitle"
        )
        assert not title["needs_attention"]
        assert not subtitle["needs_attention"]
        assert 0 == repository.read_review_attention(None, 50, 0).total
        activity = repository.field_effect_activity_page(
            search=None, event_type="metadata", occurred_after=0, page=1, page_size=50
        )
        assert 1 == activity["total"]
        assert "title" == activity["events"][0]["destination"]
        repository.close()

        reopened = open_repository(path, create=False)
        assert persisted == reopened.persist_shadow_outcome(case_id, 5)
        assert (1,) == reopened._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions"
        ).fetchone()
        newer = _bibliographic_report(
            current_title="New current title",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("New current title",),
                    subtitle="A Novel",
                ),
            ),
        )
        reopened.publish(newer, lambda: True, report.report_id)
        assert "stale" == reopened.persist_shadow_outcome(case_id, 6)
        reopened.close()


def test_incomplete_series_memberships_remain_in_attention_after_persistence() -> None:
    report = _bibliographic_report(
        current_title="Current title",
        providers=(
            _source(
                "hardcover",
                "edition-1",
                titles=("Current title",),
                series="Reading Order",
            ),
            _source(
                "open_library",
                "edition-2",
                titles=("Current title",),
                series="Reading Order",
            ),
        ),
    )
    base = _preview_automatic_profile()
    profile = MetadataPolicyProfile.create(
        "Automatic series requires positions",
        {**dict(base.options), "series": "source_order"},
        {**dict(base.destinations), "series": "automatic"},
    )

    with tempfile.TemporaryDirectory() as temporary:
        repository = open_repository(Path(temporary) / "reports.sqlite")
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        proposal = evaluate_report(
            report, resolved.resolved_id, dict(resolved.destinations)
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.set_automatic_safety_state(AutomaticSafetyState(False, None, ()), 2)

        repository.persist_shadow_outcome(case_id, 3)
        assert 1 == repository.count_current_attention_fields(None)
        detail = InspectCurrentReviewCase(repository).run(case_id)
        series = next(
            value for value in detail["fields"] if value["destination"] == "series"
        )
        assert series["needs_attention"]
        assert "propose_change" == series["deterministic_disposition"]
        assert 1 == repository.read_review_attention(None, 50, 0).total
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='series'"
        ).fetchone()
        repository.close()


def test_paused_or_category_suspended_automatic_tuple_remains_in_every_attention_projection() -> (
    None
):
    local_title = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Secret History",
        canonical="Secret History",
        excerpt="Secret History",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    source = _source(
        "catalogue",
        "edition-1",
        titles=("Secret History",),
        subtitle="A Mistborn Novella",
    )
    report = _bibliographic_report(
        current_title="secret history", providers=(source,), local_claims=(local_title,)
    )
    profile = _preview_automatic_profile()

    def admit(repository):
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        resolved = resolve_policy(profile, report.target.library)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        title = next(
            value for value in proposal.results if value.destination.value == "title"
        )
        assert "unresolved_choice" != title.to_data()["outcome"]
        # `to_data()` is a `dict[str, object]`, so the membership test needs the
        # declared shape of `reasons` -- `ProposalResult.to_data` writes
        # `list(self.reasons)`, a list of strings.
        assert "no_safe_bibliographic_combination" not in cast(
            list[str], title.to_data()["reasons"]
        )
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        return json.loads(repository.admit_review_case(proposal))["review_case_id"]

    with tempfile.TemporaryDirectory() as temporary:
        baseline = open_repository(Path(temporary) / "reports.sqlite")
        case_id = admit(baseline)
        title = next(
            field
            for field in InspectCurrentReviewCase(baseline).run(case_id)["fields"]
            if field["destination"] == "title"
        )
        assert not title["needs_attention"]
        baseline.close()
    for safety in (
        AutomaticSafetyState(True, None, ()),
        AutomaticSafetyState(False, None, (("title_layout", "admission_failed"),)),
        AutomaticSafetyState(False, "authority_failed", ()),
    ):
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = admit(repository)
            repository.set_automatic_safety_state(safety, 1)
            repository.persist_shadow_outcome(case_id, 2)
            assert (0,) == repository._connection.execute(
                "SELECT COUNT(*) FROM automatic_decisions"
            ).fetchone()
            detail = InspectCurrentReviewCase(repository).run(case_id)
            title = next(
                field for field in detail["fields"] if field["destination"] == "title"
            )
            assert title["needs_attention"]
            assert (
                "Automatic handling is suspended pending a safety recovery."
                == title["classification_reason"]
            )
            library = repository.read_review_library(None, 50, 0, True)
            assert 1 == library.total
            assert 1 == library.attention_total
            attention = repository.read_review_attention(None, 50, 0)
            assert attention.total >= 1
            assert "title" in tuple(row[2] for row in attention.rows)
            assert "automatic_safety_suspended" == next(
                row[5]
                for row in cast(
                    "tuple[tuple[str, str, str, int, str, str], ...]", attention.rows
                )
                if row[2] == "title"
            )
            projected = BrowseReviewLibrary(repository).attention(
                search=None,
                limit=50,
                cursor=None,
            )
            assert attention.total == projected["total"]
            assert "title" in [item["destination"] for item in projected["items"]]
            assert "Automatic handling is suspended pending a safety recovery." == next(
                item["classification_reason"]
                for item in projected["items"]
                if item["destination"] == "title"
            )
            repository.close()


def test_owner_activation_acknowledgement_is_durable() -> None:
    """Dismissing a completed activation survives a restart.

    This once acknowledged the seeded bootstrap row, because that row was what
    a fresh container reported. The interface now names generation 1 as the
    active baseline and never offers it for dismissal, so the durability this
    proves is bound to the transition an owner can actually dismiss.
    """

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "reports.sqlite"
        repository = open_repository(path)
        report = _report()
        repository.publish(report, lambda: True)
        profile = MetadataPolicyProfile.create(
            "Owner policy",
            dict(DEFAULT_OPTIONS),
            {
                name: "unsupported" if name == "explicit" else "ask"
                for name in OPTION_CATALOGUE
            },
        )
        resolved = resolve_policy(profile, report.target.library)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        transition = repository.metadata_policy_transition()
        assert transition is not None
        assert transition.origin == "activation"
        acknowledged = repository.acknowledge_metadata_policy_transition(
            transition.transition_id, 4
        )
        assert acknowledged.acknowledged
        repository.close()
        reopened = open_repository(path)
        persisted = reopened.metadata_policy_transition()
        assert persisted is not None
        assert (persisted.acknowledged, persisted.origin) == (True, "activation")
        reopened.close()
