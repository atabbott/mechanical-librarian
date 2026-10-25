"""Deep validation of the persisted field-effect state.

`schema.py` calls this during open-time total validation. It deliberately
imports only persistence document leaves, so the schema spine stays acyclic
once the field-effect aggregate moves into its own module.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import cast

from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    expand_cover_consideration,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.audiobook_cover_replacement import (
    material_audiobook_cover_reference,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    audiobook_identity,
    identity_for_effect,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    audiobook_title_candidate_bundle,
    audiobook_title_subtitle_bundle,
    title_preservation_bundle,
)
from mechanical_librarian.domain.cover_automatic_decision import (
    CoverAutomaticDecision,
    parse_automatic_authority,
)
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    FIELD_EFFECT_FAILURES,
    TERMINAL_STATES,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    authority_to_data,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    BibliographicSelectionKind,
    ProposalAlternative,
    ProposalAssessment,
    ProposalSet,
    canonical_target_fields,
    narrator_candidate_set_agrees,
    provider_candidate_represents_target_recording,
    provider_candidate_supports_complete_narrators,
    select_language_representation,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.reviewer_decision_summary import creator_role_evidence
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    audiobook_tag_union_candidate,
    automatic_description_candidate,
    project_audiobook_automatic_decision_value,
    project_f20_automatic_decision_value,
    select_isbn_representation,
)
from mechanical_librarian.domain.shadow_outcome import (
    ShadowFieldOutcome,
    ShadowOutcomeSet,
)
from mechanical_librarian.domain.validation import (
    boolean,
    bounded_integer,
    closed_document,
    literal,
    non_negative_integer,
    prefixed_hex_identity,
    text,
)

__all__ = [
    "_automatic_decision_value",
    "_consent_state",
    "_validate_field_effect_state",
]

# A consent records why it exists: the owner granted it, or an owner-performed
# policy activation carried it from the named predecessor (#1370).
_owner_grant = closed_document(kind=literal("owner_grant"))
_policy_activation = closed_document(
    kind=literal("policy_activation"),
    transition_id=prefixed_hex_identity("policy_transition_"),
    carried_from=prefixed_hex_identity("effect_consent_"),
)
_consent_state = closed_document(
    "consent",
    schema_version=literal("field-effect-consent-state-v2"),
    basis=lambda value: _owner_grant(value) or _policy_activation(value),
    current=boolean,
    updated_at=non_negative_integer,
)

# F20 categories whose preview can preserve a filled target without electing an
# alternative, so the outcome it produces cites no evidence and no combination.
# `isbn_form` is deliberately absent: it proves preservation by reprojecting the
# exact identifier representation, and that stricter rule stands on its own.
_RICH_PRESERVATION_CATEGORIES = frozenset(
    {"description_fill", "genres", "series", "subtitle", "tags"}
)


def _validate_automatic_decision_authority(
    decision: AutomaticDecision,
    case: ReviewCase,
    report: MetadataReport,
    proposal_set: ProposalSet,
    source_outcome: ShadowOutcomeSet,
) -> ShadowFieldOutcome:
    """Bind an automatic decision to the exact immutable authorities it names."""
    if report.audio is not None and not admitted_audiobook_identity(report):
        raise FieldEffectError("authority_binding")
    target = case.target
    if (
        decision.review_case_id != case.review_case_id
        or decision.report_id != case.report_id
        or decision.proposal_set_id != case.proposal_set_id
        or decision.target_fingerprint != case.target_fingerprint
        or proposal_set.proposal_set_id != case.proposal_set_id
        or proposal_set.report_id != case.report_id
        or proposal_set.target_fingerprint != case.target_fingerprint
        or report.report_id != case.report_id
        or report.target.fingerprint != case.target_fingerprint
        or source_outcome.review_case_id != case.review_case_id
        or source_outcome.report_id != case.report_id
        or source_outcome.proposal_set_id != case.proposal_set_id
        or source_outcome.target_fingerprint != case.target_fingerprint
        or source_outcome.resolved_policy_id != decision.resolved_policy_id
        or source_outcome.policy_generation != decision.policy_generation
        or (target.get("connection"), target.get("library"), target.get("item"))
        != (report.target.connection, report.target.library, report.target.item)
    ):
        raise FieldEffectError("authority_binding")
    source = next(
        (
            field
            for field in source_outcome.outcomes
            if field.outcome_id == decision.source_outcome_id
        ),
        None,
    )
    if source is None or any(
        (
            source.proposal_id != decision.proposal_id,
            source.destination != decision.destination,
            source.disposition != decision.outcome,
            source.candidate_id != decision.candidate_id,
            source.rule_id != decision.rule_id,
            source.policy_category != decision.policy_category,
            source.policy_option != decision.policy_option,
        )
    ):
        raise FieldEffectError("authority_binding")
    split_members = tuple(
        field
        for field in source_outcome.outcomes
        if field.destination in {"title", "subtitle"}
    )
    split_requested = any(
        field.policy_category == "subtitle"
        and (field.policy_option or "").startswith("corroborated_split:")
        for field in split_members
    )
    if split_requested and decision.destination in {"title", "subtitle"}:
        expected_bundle = audiobook_title_subtitle_bundle(split_members)
        if expected_bundle is None or decision.bundle_id != expected_bundle:
            raise FieldEffectError("authority_binding")
        combination = next(
            (
                item
                for item in proposal_set.bibliographic_combinations
                if item.combination_id == source.combination_ids[0]
            ),
            None,
        )
        if combination is None:
            raise FieldEffectError("authority_binding")
        for member in split_members:
            candidate = next(
                (
                    alternative
                    for result in proposal_set.results
                    if result.destination.value == member.destination
                    for alternative in result.alternatives
                    if alternative.alternative_id == member.candidate_id
                ),
                None,
            )
            if (
                candidate is None
                or tuple(sorted(candidate.evidence_references)) != member.citation_ids
                or not any(
                    selection.destination.value == member.destination
                    and selection.kind is BibliographicSelectionKind.CANDIDATE
                    and selection.alternative_id == member.candidate_id
                    for selection in combination.selections
                )
            ):
                raise FieldEffectError("authority_binding")
    elif decision.policy_category == "title_layout":
        expected_bundle = (
            "automatic_bundle_"
            + hashlib.sha256(
                canonical_effect_json(
                    {
                        "category": "title_layout",
                        "evidence_ids": list(source.combination_ids),
                    }
                ).encode("utf-8")
            ).hexdigest()
            if source.combination_ids
            else (
                title_preservation_bundle(source)
                or audiobook_title_candidate_bundle(source)
            )
        )
        if expected_bundle is None or decision.bundle_id != expected_bundle:
            raise FieldEffectError("authority_binding")
    return source


def _automatic_decision_value(
    decision: AutomaticDecision,
    case: ReviewCase,
    report: MetadataReport,
    proposal_set: ProposalSet,
    source_outcome: ShadowOutcomeSet,
) -> object:
    """Project one persisted automatic decision to its exact target envelope."""
    if (
        audiobook_identity(decision.policy_category, decision.policy_option) is not None
        and report.audio is None
    ):
        raise FieldEffectError("authority_binding")
    if decision.outcome not in {"candidate", "keep_current"}:
        raise FieldEffectError("authority_binding")
    source = _validate_automatic_decision_authority(
        decision, case, report, proposal_set, source_outcome
    )
    target = case.target
    proposal = next(
        (
            item
            for item in case.proposals
            if item["proposal_id"] == decision.proposal_id
            and item["destination"] == decision.destination
        ),
        None,
    )
    # Every ReviewCase proposal carries a "result" object, and TargetFacts
    # always serialises "fields" as a list of field objects.
    target_field = next(
        (
            item
            for item in cast("list[object]", target.get("fields", ()))
            if type(item) is dict and item.get("destination") == decision.destination
        ),
        None,
    )
    if (
        type(proposal) is not dict
        or type(target_field) is not dict
        or target_field.get("state") not in {"value", "null", "empty", "missing"}
    ):
        raise FieldEffectError("authority_binding")
    result = proposal.get("result")
    alternatives = result.get("alternatives") if type(result) is dict else None
    canonical_target = canonical_target_fields(report.target.fields)[
        TargetDestination(decision.destination)
    ]
    pre_value = (
        canonical_target.value
        if canonical_target.state is TargetFieldState.VALUE
        else None
    )
    if decision.outcome == "keep_current":
        identity = audiobook_identity(decision.policy_category, decision.policy_option)
        if identity is not None and identity != identity_for_effect(
            decision.destination,
            decision.policy_category,
            decision.policy_option.rsplit(":", 1)[0],
            pre_value,
            pre_value,
        ):
            raise FieldEffectError("authority_binding")
        preservation = tuple(
            value
            for value in alternatives or ()
            if type(value) is dict and value.get("assessment") == "preserves_target"
        )
        typed_preservation: tuple[ProposalAlternative, ...] = ()
        preservation_combinations: tuple[str, ...] = ()
        combination_citations: set[str] = set()
        identifier_preservation = False
        try:
            proposal_result = next(
                item
                for item in proposal_set.results
                if item.destination.value == decision.destination
            )
        except StopIteration:
            proposal_result = None
        if proposal_result is not None:
            typed_preservation = tuple(
                value
                for value in proposal_result.alternatives
                if value.assessment is ProposalAssessment.PRESERVES_TARGET
            )
            preservation_combinations = tuple(
                combination.combination_id
                for combination in proposal_set.bibliographic_combinations
                if any(
                    selection.destination.value == decision.destination
                    and selection.kind is BibliographicSelectionKind.PRESERVE_TARGET
                    for selection in combination.selections
                )
            )
            alternative_citations = {
                alternative.alternative_id: (
                    set(alternative.evidence_references)
                    | creator_role_evidence(
                        report, result.destination.value, alternative.to_data()
                    )[1]
                )
                for result in proposal_set.results
                for alternative in result.alternatives
            }
            combination_citations = {
                citation
                for combination in proposal_set.bibliographic_combinations
                if combination.combination_id in preservation_combinations
                for selection in combination.selections
                for citation in alternative_citations.get(
                    selection.alternative_id or "", ()
                )
            }
        if decision.policy_category == "isbn_form" and proposal_result is not None:
            try:
                candidate = next(iter(proposal_result.alternatives))
                exact_value = select_isbn_representation(
                    report, candidate, decision.policy_option.split(":", 1)[0]
                )
                identifier_preservation = exact_value is not None and values_match(
                    TargetDestination(decision.destination), pre_value, exact_value
                )
            except (StopIteration, RichMetadataPreviewError):
                identifier_preservation = False
        typed_preservation_matches = False
        if len(preservation) == len(typed_preservation) == 1:
            try:
                preserved_value = project_decision_value(
                    decision.destination, typed_preservation[0].value.to_data()
                )
            except FieldEffectError:
                pass
            else:
                typed_preservation_matches = (
                    preservation[0] == typed_preservation[0].to_data()
                    and values_match(
                        TargetDestination(decision.destination),
                        pre_value,
                        preserved_value,
                    )
                    and source is not None
                    and source.citation_ids
                    == tuple(
                        sorted(
                            set(typed_preservation[0].evidence_references)
                            | creator_role_evidence(
                                report,
                                decision.destination,
                                typed_preservation[0].to_data(),
                            )[1]
                            | combination_citations
                        )
                    )
                    and source.combination_ids == preservation_combinations
                )
        # A rich no-op cites nothing: the preview preserved the target without
        # electing an alternative, so there is no typed preservation to match.
        # The whole added trust is "the target currently holds a value", which
        # is exactly what the preview asserted, and `keep_current` returns None
        # and never writes.
        rich_preservation = (
            decision.policy_category in _RICH_PRESERVATION_CATEGORIES
            and not source.citation_ids
            and not source.combination_ids
            and canonical_target.state is TargetFieldState.VALUE
        )
        # Explicit is positive-only: preserving an absent flag grants no
        # false/clearing value and must still consume its exact no-op outcome.
        explicit_preservation = (
            report.audio is not None
            and decision.destination == "explicit"
            and decision.policy_category == "explicit"
            and decision.policy_option == "adult_product:audiobook_explicit_preserve"
            and decision.bundle_id is None
            and not source.citation_ids
            and not source.combination_ids
            and (
                canonical_target.state
                in {
                    TargetFieldState.MISSING,
                    TargetFieldState.NULL,
                    TargetFieldState.EMPTY,
                }
                or canonical_target.state is TargetFieldState.VALUE
                and type(canonical_target.value) is bool
            )
        )
        if (
            decision.candidate_id is not None
            or (
                not typed_preservation_matches
                and not identifier_preservation
                and not rich_preservation
                and not explicit_preservation
            )
            or any(
                (
                    source.reason_code != "unique_supported_preservation",
                    not typed_preservation_matches
                    and not rich_preservation
                    and not explicit_preservation
                    and not (
                        decision.policy_category == "isbn_form"
                        and not source.citation_ids
                        and not source.combination_ids
                    ),
                )
            )
        ):
            raise FieldEffectError("authority_binding")
        return None
    selected = next(
        (
            item
            for item in cast("list[dict[str, object]] | None", alternatives) or ()
            if item.get("alternative_id") == decision.candidate_id
        ),
        None,
    )
    if selected is None and decision.destination == "tags" and report.audio is not None:
        option = decision.policy_option.rsplit(":", 1)[0]
        result = next(
            item
            for item in proposal_set.results
            if item.destination is TargetDestination.TAGS
        )
        union = audiobook_tag_union_candidate(report, result, option)
        if (
            decision.policy_category == "tags"
            and union is not None
            and union.alternative_id == decision.candidate_id
            and source.citation_ids == union.evidence_references
            and not source.combination_ids
        ):
            selected = union.to_data()
    if selected is None:
        raise FieldEffectError("authority_binding")
    if audiobook_title_candidate_bundle(
        source
    ) is not None and source.citation_ids != tuple(
        sorted(cast("list[str]", selected["evidence_references"]))
    ):
        raise FieldEffectError("authority_binding")
    if decision.destination == "description":
        result = next(
            item
            for item in proposal_set.results
            if item.destination is TargetDestination.DESCRIPTION
        )
        synopsis = automatic_description_candidate(report, result)
        if (
            synopsis is None
            or synopsis[0].alternative_id != decision.candidate_id
            or synopsis[1] != source.citation_ids
            or source.combination_ids
        ):
            raise FieldEffectError("authority_binding")
    if decision.destination == "narrators" and report.audio is not None:
        references = tuple(cast("list[str]", selected["evidence_references"]))
        _, role_references = creator_role_evidence(report, "narrators", selected)
        recording_supported = any(
            provider_candidate_represents_target_recording(report, provider, candidate)
            and provider_candidate_supports_complete_narrators(candidate, references)
            for provider in report.providers
            for candidate in provider.candidates
        )
        if (
            source.citation_ids != tuple(sorted(set(references) | role_references))
            or source.combination_ids
            or not (
                recording_supported or narrator_candidate_set_agrees(report, references)
            )
        ):
            raise FieldEffectError("authority_binding")
    value = selected["value"]
    if decision.policy_category == "language_rendering":
        language_candidate = next(
            (
                item
                for result in proposal_set.results
                if result.destination is TargetDestination.LANGUAGE
                for item in result.alternatives
                if item.alternative_id == decision.candidate_id
            ),
            None,
        )
        exact_value = (
            None
            if language_candidate is None
            else select_language_representation(
                report, language_candidate, decision.policy_option.split(":", 1)[0]
            )
        )
        if exact_value is None:
            raise FieldEffectError("authority_binding")
        value = {"kind": "text", "value": exact_value}
    if decision.policy_category == "isbn_form":
        try:
            result = next(
                item
                for item in proposal_set.results
                if item.destination.value == decision.destination
            )
            candidate = next(
                item
                for item in result.alternatives
                if item.alternative_id == decision.candidate_id
            )
            exact_value = select_isbn_representation(
                report, candidate, decision.policy_option.split(":", 1)[0]
            )
        except (StopIteration, RichMetadataPreviewError) as error:
            raise FieldEffectError("authority_binding") from error
        if exact_value is None:
            raise FieldEffectError("authority_binding")
        value = {"kind": "text", "value": exact_value}
    if (
        decision.policy_category == "subtitle"
        and decision.policy_option == "corroborated_split:audiobook_subtitle_fill"
    ):
        # The paired source validator above closes both scalar candidates and
        # their shared combination. Re-run the original split premise too.
        from mechanical_librarian.domain.rich_metadata_preview import _subtitle_bundle

        result = next(
            item
            for item in proposal_set.results
            if item.destination is TargetDestination.SUBTITLE
        )
        preview = _subtitle_bundle(
            "subtitle",
            "corroborated_split",
            canonical_target,
            result,
            proposal_set,
            report,
        )
        if (
            decision.bundle_id is None
            or preview.outcome != "projected"
            or preview.source_candidate_id != decision.candidate_id
            or preview.source_combination_ids != source.combination_ids
            or identity_for_effect(
                "subtitle",
                "subtitle",
                "corroborated_split",
                pre_value,
                project_decision_value("subtitle", value),
            )
            != audiobook_identity(decision.policy_category, decision.policy_option)
        ):
            raise FieldEffectError("authority_binding")
        return value
    if decision.policy_category in {
        "series",
        "genres",
        "tags",
        "description_fill",
        "isbn_form",
        "subtitle",
        "explicit",
    }:
        try:
            if audiobook_identity(decision.policy_category, decision.policy_option):
                value = project_audiobook_automatic_decision_value(
                    decision.destination,
                    decision.policy_category,
                    decision.policy_option,
                    value,
                    pre_value,
                )
            else:
                value = project_f20_automatic_decision_value(
                    decision.policy_category,
                    decision.policy_option,
                    value,
                    pre_value,
                )
        except RichMetadataPreviewError as error:
            raise FieldEffectError("authority_binding") from error
    identity = audiobook_identity(decision.policy_category, decision.policy_option)
    if (
        identity is not None
        and decision.policy_category
        not in {
            "series",
            "genres",
            "tags",
            "description_fill",
            "isbn_form",
            "subtitle",
        }
        and identity
        != identity_for_effect(
            decision.destination,
            decision.policy_category,
            decision.policy_option.rsplit(":", 1)[0],
            pre_value,
            project_decision_value(decision.destination, value),
        )
    ):
        raise FieldEffectError("authority_binding")
    return value


def _cover_automatic_decision_value(
    connection: sqlite3.Connection,
    decision: CoverAutomaticDecision,
    case: ReviewCase,
    report: MetadataReport,
    proposal_set: ProposalSet,
    consideration_document: str,
) -> object:
    """Bind qualified cover authority to its exact current consideration."""
    resolution = connection.execute(
        "SELECT document FROM metadata_policy_resolutions WHERE resolved_policy_id=? "
        "AND generation=? AND library_name=?",
        (
            decision.resolved_policy_id,
            decision.policy_generation,
            report.target.library,
        ),
    ).fetchone()
    if resolution is None:
        raise FieldEffectError("authority_binding")
    resolved = json.loads(resolution[0])
    if (
        resolved["options"]["cover_order"] != decision.policy_option.split(":", 1)[0]
        or resolved["destinations"]["cover_order"] != "automatic"
    ):
        raise FieldEffectError("authority_binding")
    material_replacement = (
        report.audio is not None
        and decision.policy_option.split(":", 1)[0] == "replace_if_strictly_better"
    )
    if report.audio is not None:
        target = canonical_target_fields(report.target.fields)[TargetDestination.COVER]
        option = decision.policy_option.split(":", 1)[0]
        expected = identity_for_effect(
            "cover", "cover_order", option, target.value, decision.artifact_id
        )
        if (
            decision.qualification_identity != expected
            or expected is None
            or (
                option == "fixed_source_order"
                and target.state
                not in {
                    TargetFieldState.MISSING,
                    TargetFieldState.NULL,
                    TargetFieldState.EMPTY,
                }
            )
        ):
            raise FieldEffectError("authority_binding")
    elif decision.qualification_identity is not None:
        raise FieldEffectError("authority_binding")
    try:
        consideration = json.loads(consideration_document)
        candidate = next(
            row
            for row in consideration["candidates"]
            if row["reference"] == decision.candidate_reference
        )
        proposal = next(
            result
            for result in proposal_set.results
            if result.destination.value == "cover"
        )
        alternative = next(
            row
            for row in proposal.alternatives
            if row.alternative_id == decision.candidate_id
        )
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
        raise FieldEffectError("authority_binding") from error
    policy_report = json.loads(consideration["policy_report_document"])
    if report.audio is None and decision.policy_option == "replace_if_strictly_better":
        target = canonical_target_fields(report.target.fields)[TargetDestination.COVER]
        if (
            target.state
            not in {
                TargetFieldState.MISSING,
                TargetFieldState.NULL,
                TargetFieldState.EMPTY,
            }
            or policy_report.get("actual_target_state") != "known_empty"
        ):
            raise FieldEffectError("authority_binding")
    if material_replacement and (
        policy_report.get("actual_target_state") != "confirmed"
        or policy_report.get("owner_ready") is not True
        or material_audiobook_cover_reference(
            consideration.get("candidates"), target.value
        )
        != decision.candidate_reference
    ):
        raise FieldEffectError("authority_binding")
    if (
        decision.review_case_id != case.review_case_id
        or decision.report_id != case.report_id
        or decision.proposal_set_id != case.proposal_set_id
        or decision.target_fingerprint != case.target_fingerprint
        or report.report_id != case.report_id
        or report.target.fingerprint != case.target_fingerprint
        or hashlib.sha256(consideration_document.encode()).hexdigest()
        != decision.consideration_digest
        or consideration.get("consideration_id") != decision.consideration_id
        or consideration.get("replay_digest") != decision.replay_digest
        or consideration.get("policy_reference") != decision.policy_reference
        or consideration.get("provider_fetching") is not False
        or (not material_replacement and policy_report.get("status") != "recommended")
        or candidate.get("artifact_id") != decision.artifact_id
        or candidate.get("selectable") is not True
        or candidate.get("incumbent") is not False
        or (not material_replacement and candidate.get("deterministic_rank") != 1)
        or tuple(candidate.get("deterministic_rank_key", ())) != decision.rank_key
        or tuple(sorted(set(candidate.get("deterministic_citations", ()))))
        != decision.citation_ids
        or alternative.value.value != decision.artifact_id
    ):
        raise FieldEffectError("authority_binding")
    return {"kind": "cover", "value": decision.artifact_id}


def _validate_field_effect_state(connection: sqlite3.Connection) -> None:
    """Fail closed on any non-canonical field-effect envelope or row mismatch."""
    try:
        for row in connection.execute(
            "SELECT decision_id,review_case_id,report_id,connection_name,library_name,work_id,destination,target_fingerprint,target_version,origin,occurred_at,event_id FROM field_effect_no_changes"
        ).fetchall():
            record = FieldEffectNoChange.create(
                decision_id=row[0],
                review_case_id=row[1],
                report_id=row[2],
                connection=row[3],
                library=row[4],
                work_id=row[5],
                destination=row[6],
                target_fingerprint=row[7],
                target_version=row[8],
                occurred_at=row[10],
                origin=row[9],
            )
            if row[9] != record.origin or row[11] != record.event_id:
                raise FieldEffectError("persistence")
            authored_row = connection.execute(
                "SELECT d.document,c.document,r.document FROM description_decisions d JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id JOIN metadata_reports r ON r.report_id=? WHERE d.decision_id=?",
                (record.report_id, record.decision_id),
            ).fetchone()
            if authored_row is not None:
                authored = AuthoredDescriptionDecision.from_json(authored_row[0])
                candidate = DescriptionAdviceCandidate.from_json(authored_row[1])
                authored_report = MetadataReport.from_json(authored_row[2])
                authored_target = next(
                    field
                    for field in authored_report.target.fields
                    if field.destination is TargetDestination.DESCRIPTION
                )
                before = (
                    authored_target.value
                    if authored_target.state.value == "value"
                    else None
                )
                if (
                    authored.review_case_id != record.review_case_id
                    or authored.report_id != record.report_id
                    or authored.target_fingerprint != record.target_fingerprint
                    or authored.origin != record.origin
                    or authored_report.target.connection != record.connection
                    or authored_report.target.library != record.library
                    or authored_report.target.item != record.work_id
                    or record.destination != "description"
                    or not values_match(
                        TargetDestination.DESCRIPTION,
                        before,
                        candidate.output.description,
                    )
                    or record.target_version
                    != field_value_version("description", before)
                ):
                    raise FieldEffectError("persistence")
                continue
            decision_row = connection.execute(
                "SELECT document FROM automatic_decisions WHERE decision_id=? "
                "UNION ALL SELECT json_extract(document,'$.decision_document') "
                "FROM cover_selection_authorities WHERE kind='automatic' AND decision_id=?",
                (record.decision_id, record.decision_id),
            ).fetchone()
            case_row = connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (record.review_case_id,),
            ).fetchone()
            report_row = connection.execute(
                "SELECT document FROM metadata_reports WHERE report_id=?",
                (record.report_id,),
            ).fetchone()
            if decision_row is None or case_row is None or report_row is None:
                raise FieldEffectError("persistence")
            decision = parse_automatic_authority(decision_row[0])
            case = ReviewCase.from_json(case_row[0])
            report = MetadataReport.from_json(report_row[0])
            proposal_row = connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (decision.proposal_set_id,),
            ).fetchone()
            outcome_row = connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? "
                "AND report_id=? AND proposal_set_id=? AND resolved_policy_id=?",
                (
                    decision.review_case_id,
                    decision.report_id,
                    decision.proposal_set_id,
                    decision.resolved_policy_id,
                ),
            ).fetchone()
            if proposal_row is None or (
                type(decision) is AutomaticDecision and outcome_row is None
            ):
                raise FieldEffectError("persistence")
            proposal_set = ProposalSet.from_json(proposal_row[0])
            source_outcome = (
                None
                if type(decision) is CoverAutomaticDecision
                else ShadowOutcomeSet.from_json(outcome_row[0])
            )
            if (
                decision.destination != record.destination
                or decision.target_fingerprint != record.target_fingerprint
                or report.target.connection != record.connection
                or report.target.library != record.library
                or report.target.item != record.work_id
            ):
                raise FieldEffectError("persistence")
            # TargetFacts always serialises "fields" as a list of field objects.
            target = next(
                (
                    value
                    for value in cast("list[object]", case.target.get("fields", ()))
                    if type(value) is dict
                    and value.get("destination") == record.destination
                ),
                None,
            )
            proposal = next(
                (
                    value
                    for value in case.proposals
                    if value.get("proposal_id") == decision.proposal_id
                    and value.get("destination") == record.destination
                ),
                None,
            )
            result = proposal.get("result") if type(proposal) is dict else None
            alternatives = result.get("alternatives") if type(result) is dict else None
            if type(target) is not dict or target.get("state") not in {
                "value",
                "null",
                "empty",
                "missing",
            }:
                raise FieldEffectError("persistence")
            pre_value = target.get("value") if target["state"] == "value" else None
            if type(decision) is CoverAutomaticDecision:
                consideration_row = connection.execute(
                    "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM current_cover_considerations current "
                    "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                    "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
                    "WHERE current.review_case_id=? AND current.consideration_id=?",
                    (decision.review_case_id, decision.consideration_id),
                ).fetchone()
                if consideration_row is None:
                    raise FieldEffectError("persistence")
                value = _cover_automatic_decision_value(
                    connection,
                    decision,
                    case,
                    report,
                    proposal_set,
                    expand_cover_consideration(*consideration_row),
                )
            else:
                if source_outcome is None:
                    raise FieldEffectError("persistence")
                value = _automatic_decision_value(
                    cast(AutomaticDecision, decision),
                    case,
                    report,
                    proposal_set,
                    source_outcome,
                )
            if decision.outcome == "keep_current":
                if value is not None:
                    raise FieldEffectError("persistence")
                desired = pre_value
            else:
                selected = next(
                    (
                        value
                        for value in alternatives or ()
                        if type(value) is dict
                        and value.get("alternative_id") == decision.candidate_id
                    ),
                    None,
                )
                if type(selected) is not dict:
                    raise FieldEffectError("persistence")
                desired = project_decision_value(record.destination, value)
            explicit_absence_preservation = (
                type(decision) is AutomaticDecision
                and decision.destination == "explicit"
                and decision.policy_category == "explicit"
                and decision.policy_option
                == "adult_product:audiobook_explicit_preserve"
                and decision.outcome == "keep_current"
                and pre_value is None
                and desired is None
            )
            if (
                not explicit_absence_preservation
                and not values_match(
                    TargetDestination(record.destination), pre_value, desired
                )
            ) or record.target_version != field_value_version(
                record.destination, pre_value
            ):
                raise FieldEffectError("persistence")
        control = connection.execute(
            "SELECT singleton,paused,updated_at FROM field_effect_control"
        ).fetchall()
        if (
            len(control) != 1
            or control[0][0] != 1
            or control[0][1] not in (0, 1)
            or not non_negative_integer(control[0][2])
        ):
            raise FieldEffectError("invalid_control")

        suspension = closed_document(
            "connection",
            "destination",
            "reason",
            "scope",
            "updated_at",
            schema_version=literal("field-effect-suspension-v1"),
        )
        for (
            scope,
            connection_name,
            destination,
            reason,
            updated_at,
            document,
        ) in connection.execute(
            "SELECT scope,connection_name,destination,reason,updated_at,document "
            "FROM field_effect_suspensions"
        ):
            raw = json.loads(document)
            if (
                not suspension(raw)
                or (
                    raw["scope"],
                    raw["connection"],
                    raw["destination"],
                    raw["reason"],
                    raw["updated_at"],
                )
                != (scope, connection_name, destination, reason, updated_at)
                or canonical_effect_json(raw) != document
            ):
                raise FieldEffectError("invalid_suspension")

        authorities: dict[str, tuple[EffectAuthority, bool, int]] = {}
        authority_state = closed_document(
            schema_version=literal("field-effect-authority-state-v1"),
            current=boolean,
            updated_at=non_negative_integer,
            authority=closed_document(
                "decision_id",
                "origin",
                "outcome",
                "policy_identity",
                "qualification_identity",
                "review_case_id",
                "source",
                "target_fingerprint",
            ),
        )
        for decision_id, current, updated_at, document in connection.execute(
            "SELECT decision_id,current,updated_at,document FROM field_effect_authorities"
        ):
            raw = json.loads(document)
            if (
                not authority_state(raw)
                or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                or canonical_effect_json(raw) != document
            ):
                raise FieldEffectError("invalid_authority")
            item = raw["authority"]
            authority = EffectAuthority(
                item["decision_id"],
                item["review_case_id"],
                item["target_fingerprint"],
                item["source"],
                item["origin"],
                item["outcome"],
                item["policy_identity"],
                item["qualification_identity"],
            )
            if decision_id != authority.decision_id or item != authority_to_data(
                authority
            ):
                raise FieldEffectError("invalid_authority")
            authorities[decision_id] = (authority, bool(current), updated_at)

        consents: dict[str, tuple[EffectConsent, bool, int]] = {}
        carried: list[tuple[EffectConsent, str, str]] = []
        for consent_id, current, updated_at, document in connection.execute(
            "SELECT consent_id,current,updated_at,document FROM field_effect_consents"
        ):
            raw = json.loads(document)
            if (
                not _consent_state(raw)
                or (raw["current"], raw["updated_at"]) != (bool(current), updated_at)
                or canonical_effect_json(raw) != document
            ):
                raise FieldEffectError("invalid_consent")
            consent = EffectConsent.from_data(raw["consent"])
            if consent_id != consent.consent_id:
                raise FieldEffectError("invalid_consent")
            consents[consent_id] = (consent, bool(current), updated_at)
            if raw["basis"]["kind"] == "policy_activation":
                basis = raw["basis"]
                carried.append((consent, basis["carried_from"], basis["transition_id"]))
        # A carried consent names a recorded activation and a retired
        # predecessor of exactly its scope, so no activation can have widened
        # what the owner granted.
        for consent, carried_from, transition_id in carried:
            predecessor = consents.get(carried_from)
            if (
                predecessor is None
                or connection.execute(
                    "SELECT 1 FROM metadata_policy_transitions WHERE transition_id=?",
                    (transition_id,),
                ).fetchone()
                is None
                or predecessor[1]
                or (
                    predecessor[0].connection,
                    predecessor[0].library,
                    predecessor[0].destination,
                    predecessor[0].action,
                )
                != (
                    consent.connection,
                    consent.library,
                    consent.destination,
                    consent.action,
                )
            ):
                raise FieldEffectError("invalid_consent")

        pending_return = closed_document(
            requested_at=non_negative_integer,
            scan_id=lambda value: type(value) is str and value != "",
        )
        manual_hold = closed_document(
            "destination",
            "pending_return",
            "review_case_id",
            schema_version=literal("field-effect-manual-hold-v2"),
            active=boolean,
            decision_id=lambda value: (
                type(value) is str
                and re.fullmatch(r"human_decision_[0-9a-f]{64}", value) is not None
            ),
            updated_at=non_negative_integer,
        )
        for (
            review_case_id,
            destination,
            active,
            updated_at,
            document,
        ) in connection.execute(
            "SELECT review_case_id,destination,active,updated_at,document "
            "FROM field_effect_manual_holds"
        ):
            raw = json.loads(document)
            if (
                not manual_hold(raw)
                or (
                    raw["review_case_id"],
                    raw["destination"],
                    raw["active"],
                    raw["updated_at"],
                )
                != (review_case_id, destination, bool(active), updated_at)
                or (
                    raw["pending_return"] is not None
                    and (not raw["active"] or not pending_return(raw["pending_return"]))
                )
                or canonical_effect_json(raw) != document
            ):
                raise FieldEffectError("invalid_manual_hold")

        operations: dict[str, FieldEffectOperation] = {}
        # Rebound below both from a parsed document and from a dict lookup.
        operation: FieldEffectOperation | None
        for row in connection.execute(
            "SELECT plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document FROM field_effects"
        ):
            operation = FieldEffectOperation.from_json(row[9])
            plan = operation.plan
            expected = (
                plan.plan_id,
                plan.authority.decision_id,
                plan.connection,
                plan.library,
                plan.work_id,
                plan.destination,
                operation.state,
                operation.attempt,
                operation.updated_at,
            )
            if (
                row[:9] != expected
                or canonical_effect_json(operation.to_data()) != row[9]
                or plan.authority.decision_id not in authorities
                or plan.consent.consent_id not in consents
                or authorities[plan.authority.decision_id][0] != plan.authority
                or consents[plan.consent.consent_id][0] != plan.consent
            ):
                raise FieldEffectError("invalid_operation")
            companion = plan.subtitle_plan()
            if companion is not None and (
                companion.authority.decision_id not in authorities
                or companion.consent.consent_id not in consents
                or authorities[companion.authority.decision_id][0]
                != companion.authority
                or consents[companion.consent.consent_id][0] != companion.consent
            ):
                raise FieldEffectError("invalid_operation")
            if companion is not None and plan.subtitle_companion is not None:
                for member in (plan, companion):
                    decision_row = connection.execute(
                        "SELECT document FROM automatic_decisions WHERE decision_id=?",
                        (member.authority.decision_id,),
                    ).fetchone()
                    if decision_row is None:
                        raise FieldEffectError("invalid_operation")
                    decision = AutomaticDecision.from_json(decision_row[0])
                    case_row = connection.execute(
                        "SELECT document FROM review_cases WHERE review_case_id=?",
                        (decision.review_case_id,),
                    ).fetchone()
                    report_row = connection.execute(
                        "SELECT document FROM metadata_reports WHERE report_id=?",
                        (decision.report_id,),
                    ).fetchone()
                    proposal_row = connection.execute(
                        "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                        (decision.proposal_set_id,),
                    ).fetchone()
                    outcome_row = connection.execute(
                        "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? AND report_id=? AND proposal_set_id=? AND resolved_policy_id=?",
                        (
                            decision.review_case_id,
                            decision.report_id,
                            decision.proposal_set_id,
                            decision.resolved_policy_id,
                        ),
                    ).fetchone()
                    if any(
                        row is None
                        for row in (case_row, report_row, proposal_row, outcome_row)
                    ):
                        raise FieldEffectError("invalid_operation")
                    report = MetadataReport.from_json(report_row[0])
                    value = _automatic_decision_value(
                        decision,
                        ReviewCase.from_json(case_row[0]),
                        report,
                        ProposalSet.from_json(proposal_row[0]),
                        ShadowOutcomeSet.from_json(outcome_row[0]),
                    )
                    target_field = canonical_target_fields(report.target.fields)[
                        TargetDestination(member.destination)
                    ]
                    previous = (
                        target_field.value
                        if target_field.state is TargetFieldState.VALUE
                        else None
                    )
                    if (
                        decision.bundle_id != plan.subtitle_companion.bundle_id
                        or member.pre_value != previous
                        or member.desired_value
                        != project_decision_value(member.destination, value)
                        or member.target_version
                        != field_value_version(member.destination, previous)
                        or member.dependency_digests != (report.target.fingerprint,)
                    ):
                        raise FieldEffectError("invalid_operation")
            operations[plan.plan_id] = operation

        event = closed_document(
            "decision_id",
            "desired_value",
            "destination",
            "event_id",
            "origin",
            "plan_id",
            "pre_value",
            schema_version=literal("field-effect-event-v1"),
            sequence=bounded_integer(1),
            attempt=bounded_integer(0, 3),
            occurred_at=non_negative_integer,
            kind=text,
            state=text,
            failure=FIELD_EFFECT_FAILURES.__contains__,
        )
        next_sequence: dict[str, int] = {}
        last_events: dict[str, dict[str, object]] = {}
        for (
            event_id,
            plan_id,
            sequence,
            kind,
            occurred_at,
            document,
        ) in connection.execute(
            "SELECT event_id,plan_id,sequence,kind,occurred_at,document FROM field_effect_events ORDER BY plan_id,sequence"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            expected_sequence = next_sequence.get(plan_id, 1)
            if (
                operation is None
                or not event(
                    {
                        **{
                            key: value
                            for key, value in raw.items()
                            if key != "subtitle_companion"
                        },
                        "schema_version": "field-effect-event-v1",
                    }
                )
                or raw.get("schema_version")
                != (
                    "field-effect-title-subtitle-event-v1"
                    if operation.plan.subtitle_companion is not None
                    else "field-effect-event-v1"
                )
                or ("subtitle_companion" in raw)
                != (operation.plan.subtitle_companion is not None)
                or raw.get("subtitle_companion")
                != (
                    operation.plan.subtitle_companion.to_data()
                    if operation.plan.subtitle_companion is not None
                    else None
                )
                or (
                    raw["event_id"],
                    raw["plan_id"],
                    raw["sequence"],
                    raw["kind"],
                    raw["occurred_at"],
                )
                != (event_id, plan_id, sequence, kind, occurred_at)
                or sequence != expected_sequence
                or raw["decision_id"] != operation.plan.authority.decision_id
                or raw["origin"] != operation.plan.authority.origin
                or raw["destination"] != operation.plan.destination
                or raw["pre_value"] != operation.plan.pre_value
                or raw["desired_value"] != operation.plan.desired_value
                or raw["state"] != kind
                or canonical_effect_json(raw) != document
                or event_id
                != "effect_event_"
                + hashlib.sha256(
                    canonical_effect_json(
                        {key: value for key, value in raw.items() if key != "event_id"}
                    ).encode()
                ).hexdigest()
            ):
                raise FieldEffectError("invalid_event")
            next_sequence[plan_id] = expected_sequence + 1
            last_events[plan_id] = raw
        if (
            set(next_sequence) != set(operations)
            or set(authorities)
            != {
                member.authority.decision_id
                for value in operations.values()
                for member in (value.plan, value.plan.subtitle_plan())
                if member is not None
            }
            or not {
                member.consent.consent_id
                for value in operations.values()
                for member in (value.plan, value.plan.subtitle_plan())
                if member is not None
            }.issubset(consents)
        ):
            raise FieldEffectError("invalid_event")
        for plan_id, operation in operations.items():
            last = last_events[plan_id]
            if (last["state"], last["attempt"], last["failure"]) != (
                operation.state,
                operation.attempt,
                operation.failure,
            ):
                raise FieldEffectError("invalid_event")

        action = closed_document(
            "action_id",
            "plan_id",
            schema_version=literal("field-effect-action-v1"),
            attempt=bounded_integer(0, 3),
            updated_at=non_negative_integer,
            kind=text,
            state=text,
        )
        actions: dict[tuple[str, str], tuple[str, int]] = {}
        for (
            action_id,
            plan_id,
            kind,
            state,
            attempt,
            updated_at,
            document,
        ) in connection.execute(
            "SELECT action_id,plan_id,kind,state,attempt,updated_at,document FROM field_effect_actions"
        ):
            raw = json.loads(document)
            if (
                plan_id not in operations
                or not action(raw)
                or (
                    raw["action_id"],
                    raw["plan_id"],
                    raw["kind"],
                    raw["state"],
                    raw["attempt"],
                    raw["updated_at"],
                )
                != (action_id, plan_id, kind, state, attempt, updated_at)
                or canonical_effect_json(raw) != document
                or action_id
                != "effect_action_"
                + hashlib.sha256(f"{plan_id}:{kind}".encode()).hexdigest()
            ):
                raise FieldEffectError("invalid_action")
            actions[(plan_id, kind)] = (state, attempt)
        for plan_id, operation in operations.items():
            targeted = actions.get((plan_id, "targeted_read"))
            if (operation.state == "reconciling") != (
                targeted is not None and targeted[0] in {"pending", "claimed"}
            ) or (
                targeted is not None
                and operation.state != "reconciling"
                and targeted[0] != "completed"
            ):
                raise FieldEffectError("invalid_action")
        for (plan_id, kind), (state, _) in actions.items():
            if kind == "forward_reevaluation" and (
                state not in {"pending", "claimed", "completed"}
                or operations[plan_id].failure != "third_value"
            ):
                raise FieldEffectError("invalid_action")

        cleanup = closed_document(
            "plan_id",
            schema_version=literal("field-effect-cleanup-v1"),
            attempt=bounded_integer(0, 3),
            updated_at=non_negative_integer,
            state=text,
        )
        cleanups: set[str] = set()
        for plan_id, state, attempt, updated_at, document in connection.execute(
            "SELECT plan_id,state,attempt,updated_at,document FROM field_effect_cleanups"
        ):
            raw = json.loads(document)
            operation = operations.get(plan_id)
            if (
                operation is None
                or operation.plan.destination != "cover"
                or operation.state not in TERMINAL_STATES
                or not cleanup(raw)
                or (raw["plan_id"], raw["state"], raw["attempt"], raw["updated_at"])
                != (plan_id, state, attempt, updated_at)
                or canonical_effect_json(raw) != document
            ):
                raise FieldEffectError("invalid_cleanup")
            cleanups.add(plan_id)
        expected_cleanups = {
            plan_id
            for plan_id, operation in operations.items()
            if operation.plan.destination == "cover"
            and operation.state in TERMINAL_STATES
        }
        if cleanups != expected_cleanups:
            raise FieldEffectError("invalid_cleanup")
    except (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        sqlite3.Error,
    ) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
