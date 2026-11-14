"""Deterministic policy adjudication over exact report-bound cover evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverContextField,
    CoverDesignation,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.cover_rules import (
    EDITION_PREFERENCE,
    STAGE_REASONS,
    CoverExclusion,
    CoverIssue,
    CoverOutcome,
    CoverProtection,
    CoverRuleCandidate,
    CurrentCover,
    MeasuredCoverElection,
    elect_measured_covers,
    same_art,
)
from mechanical_librarian.domain.deterministic_cover_election import (
    CoverActualTargetState,
    CoverElectionCandidate,
    elect_confirmed_covers,
)
from mechanical_librarian.domain.field_outcomes import (
    language_canonical_name,
    language_outcomes_compatible,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderCandidate,
    TargetDestination,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

if TYPE_CHECKING:
    from .configuration import ReviewWebConfiguration

from .cover_identity import (
    CLASSIFIER_REFERENCE,
    _preview_reference,
    classify_cover_identity,
)
from .cover_identity import (
    SCHEMA_VERSION as IDENTITY_SCHEMA_VERSION,
)
from .cover_observation import (
    INSTRUCTION,
    LEGACY_INSTRUCTION_SHA256,
    LEGACY_OUTPUT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    LEGACY_TAXONOMY,
    LEGACY_TAXONOMY_REFERENCE,
    OUTPUT_SCHEMA_VERSION,
    PREVIOUS_INSTRUCTION_SHA256,
    PREVIOUS_SCHEMA_VERSION,
    RETAINED_V3_INSTRUCTION_SHA256,
    TAXONOMY,
    TAXONOMY_REFERENCE,
    _observer_reference,
    _output_schema,
)
from .cover_observation import (
    SCHEMA_VERSION as OBSERVATION_SCHEMA_VERSION,
)
from .cover_packet_semantics import bind_cover_packet, canonical_packet_json
from .cover_packets import cover_source_reference

SCHEMA_VERSION = "cover-policy-report-v4"
POLICY_SCHEMA_VERSION = "cover-policy-v2"
_IDENTITY_OUTCOMES = frozenset(
    {
        "conflict",
        "plausible_match",
        "title_creator_work_match",
        "unknown",
        "verified_manifestation_match",
        "verified_work_match",
    }
)
_REFERENCE = re.compile(r"[a-z][a-z0-9_]{0,63}_[0-9a-f]{64}")
# A current cover the target states but no retrieved image shows has no
# packet reference, so the stage names it with this one.
_UNREAD_CURRENT = "current_cover"
_REPORT_REASONS = frozenset(
    {
        "current_cover_kept",
        "deterministic_confirmed_order",
        "no_confirmed_candidates",
        "provider_fetching",
        *STAGE_REASONS,
    }
)
_CHOOSING_OUTCOMES = frozenset(
    {CoverOutcome.REPLACE.value, CoverOutcome.SUGGEST.value, CoverOutcome.FILL.value}
)
# A title result this unsettled may name another book (#1363 Q3).
_TITLE_CONFLICT_REASONS = frozenset(
    {"competing_evidence", "isbn_title_material_conflict"}
)


@dataclass(frozen=True, slots=True)
class CoverPolicy:
    """One closed owner policy; no prompt text or free-form preference."""

    avoid_promotional_text: bool = False
    avoid_tie_in_branding: bool = False
    prefer_minimal_composition: bool = False
    current_protection: str = CoverProtection.PROTECTED.value
    prefer_edition: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not bool
            for value in (
                self.avoid_promotional_text,
                self.avoid_tie_in_branding,
                self.prefer_minimal_composition,
                self.prefer_edition,
            )
        ) or (
            type(self.current_protection) is not str
            or self.current_protection not in {item.value for item in CoverProtection}
        ):
            raise ValueError("cover policy is invalid")

    @property
    def protection(self) -> CoverProtection:
        return CoverProtection(self.current_protection)

    @property
    def document(self) -> str:
        return canonical_packet_json(
            {
                "avoid_promotional_text": self.avoid_promotional_text,
                "avoid_tie_in_branding": self.avoid_tie_in_branding,
                "current_protection": self.current_protection,
                "prefer_edition": self.prefer_edition,
                "prefer_minimal_composition": self.prefer_minimal_composition,
                "schema_version": POLICY_SCHEMA_VERSION,
            }
        )

    @property
    def reference(self) -> str:
        return (
            "cover_policy_" + hashlib.sha256(self.document.encode("utf-8")).hexdigest()
        )


def cover_policy_of(review: ReviewWebConfiguration) -> CoverPolicy:
    """Read the owner's Current covers settings as the policy they choose."""
    return CoverPolicy(
        avoid_promotional_text=review.cover_avoid_promotional_text,
        avoid_tie_in_branding=review.cover_avoid_tie_in_branding,
        prefer_minimal_composition=review.cover_prefer_minimal_composition,
        current_protection=review.cover_current_protection,
        prefer_edition=review.cover_prefer_edition,
    )


@dataclass(frozen=True, slots=True)
class CoverPolicyReport:
    document: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.document.encode("utf-8")).hexdigest()


def adjudicate_cover_policy(
    report_document: str,
    packet_document: str,
    image_payloads: tuple[tuple[str, str, bytes], ...],
    observation_document: str | None,
    policy: CoverPolicy,
    *,
    profile_payload: bytes | None = None,
    read_preview: Callable[[str, str], bytes],
    provider_fetching: bool = False,
    work_identity_level: WorkIdentityLevel,
) -> CoverPolicyReport:
    """Return an identity-confirmed technical recommendation.

    Observations remain bound diagnostic evidence, but cannot change the
    confirmation, readiness, or order that this policy reports. The configured
    work-identity level reaches only identity classification: a
    ``title_creator_work_match`` is admitted as a row and never elected (#1355).
    """
    if type(policy) is not CoverPolicy:
        raise ValueError("cover policy is invalid")
    identity_document = classify_cover_identity(
        report_document,
        packet_document,
        image_payloads,
        read_preview=read_preview,
        work_identity_level=work_identity_level,
    ).document
    bind_cover_packet(packet_document, image_payloads)
    packet = _load_object(packet_document, "cover packet")
    candidates = packet["candidates"]
    if type(candidates) is not list:
        raise ValueError("cover packet is invalid")
    references = tuple(_required_reference(value["reference"]) for value in candidates)

    identity = _identity_rows(
        identity_document,
        packet["packet_reference"],
        candidates,
        references,
    )
    classifier_reference = identity["classifier_reference"]
    identity_by_reference = {
        value["candidate_reference"]: value
        for value in cast("list[dict[str, object]]", identity["candidates"])
    }
    observation_taxonomy_reference = TAXONOMY_REFERENCE
    if observation_document is not None:
        # Reviewer observations remain advisory, but an admitted document must
        # still satisfy the exact packet/profile binding before it is retained.
        observation = _observation_rows(
            observation_document,
            packet["packet_reference"],
            candidates,
            references,
            profile_payload,
        )
        observation_taxonomy_reference = cast("str", observation["taxonomy_reference"])
    election_candidates = tuple(
        CoverElectionCandidate(
            reference=reference,
            identity=cast("str", identity_by_reference[reference]["outcome"]),
            identity_citations=tuple(
                cast(
                    "list[str]",
                    identity_by_reference[reference]["evidence_references"],
                )
            ),
            source_citations=tuple(
                cast("str", binding["reference"])
                for binding in candidates[index]["source_bindings"]
            ),
            incumbent=cast("bool", candidates[index]["incumbent"]),
            width=cast("int", candidates[index]["width"]),
            height=cast("int", candidates[index]["height"]),
            packet_ordinal=index,
        )
        for index, reference in enumerate(references)
        if identity_by_reference[reference]["outcome"]
        in {"verified_manifestation_match", "verified_work_match"}
    )
    report = MetadataReport.from_json(report_document)
    target_cover = next(
        value
        for value in report.target.fields
        if value.destination is TargetDestination.COVER
    )
    has_confirmed_incumbent = any(
        candidate.incumbent for candidate in election_candidates
    )
    has_retained_incumbent = any(
        cast("bool", candidate["incumbent"]) for candidate in candidates
    )
    actual_target_state = (
        CoverActualTargetState.CONFIRMED
        if target_cover.state is TargetFieldState.VALUE and has_confirmed_incumbent
        else CoverActualTargetState.KNOWN_EMPTY
        if target_cover.state
        in {
            TargetFieldState.MISSING,
            TargetFieldState.NULL,
            TargetFieldState.EMPTY,
        }
        and not has_retained_incumbent
        else CoverActualTargetState.UNKNOWN
    )
    # The election keeps owner readiness and its own order; the measured
    # stage decides what replaces, suggests or fills (ADR-0093).
    election = elect_confirmed_covers(
        election_candidates,
        provider_fetching=provider_fetching,
        actual_target_state=actual_target_state,
    )
    acceptable = tuple(item.candidate.reference for item in election.ranked)
    owner_ready = election.ready
    rule_candidates, medium, current, current_language = _measured_inputs(
        report, candidates, election_candidates
    )

    def stage_over(
        offered: tuple[CoverRuleCandidate, ...],
    ) -> MeasuredCoverElection:
        return elect_measured_covers(
            offered,
            medium=medium,
            current=current,
            protection=policy.protection,
            prefer_edition=policy.prefer_edition,
            title_conflict=_title_conflict(report),
            # EPUB opening evidence of omnibus membership never reaches the
            # report, so the omnibus rule has no input yet (#1363 Q6).
            omnibus=False,
        )

    larger = _larger_copy(report, candidates, rule_candidates, current)
    stage = None
    if larger is not None:
        # A larger original of the current cover's own picture is a stage
        # candidate outside the reviewed packet, kept only when it cures an
        # undersized or soft current cover (#1363, Codex P2).
        stage = stage_over((*rule_candidates, larger))
        if stage.choice == larger.reference and stage.reason in {
            CoverIssue.BELOW_FLOOR,
            CoverIssue.SHARPER_COPY,
        }:
            acceptable = (*acceptable, larger.reference)
            owner_ready = not provider_fetching
        else:
            stage = None
    if stage is None:
        stage = stage_over(rule_candidates)
    fetching = bool(acceptable) and provider_fetching
    return _report(
        packet["packet_reference"],
        classifier_reference,
        policy,
        identity_document,
        observation_document,
        observation_taxonomy_reference,
        acceptable,
        stage.choice
        if not fetching and stage.outcome.value in _CHOOSING_OUTCOMES
        else None,
        owner_ready,
        actual_target_state.value,
        (_stage_report_reason(stage, fetching),),
        _stage_document(stage, current_language),
    )


def _stage_report_reason(stage: MeasuredCoverElection, fetching: bool) -> str:
    if fetching:
        return "provider_fetching"
    if stage.outcome is CoverOutcome.EMPTY:
        return "no_confirmed_candidates"
    if stage.outcome is CoverOutcome.FILL:
        return "deterministic_confirmed_order"
    reason = _stage_reason(stage)
    return "current_cover_kept" if reason is None else reason


def _stage_reason(stage: MeasuredCoverElection) -> str | None:
    if stage.reason is not None:
        return stage.reason.value
    return EDITION_PREFERENCE if stage.edition_preferred else None


def _stage_document(
    stage: MeasuredCoverElection, current_language: str | None
) -> dict[str, object]:
    return {
        "choice": stage.choice,
        "edition_preferred": stage.edition_preferred,
        "exclusions": [
            {"exclusion": exclusion.value, "reference": reference}
            for reference, exclusion in stage.excluded
        ],
        "groups": [
            {"members": list(group.members), "representative": group.representative}
            for group in stage.groups
        ],
        "issues": [issue.value for issue in stage.issues],
        # The edition language a reason names, only when that is an issue.
        "language": current_language
        if CoverIssue.EDITION_LANGUAGE in stage.issues
        else None,
        "outcome": stage.outcome.value,
        "reason": _stage_reason(stage),
    }


def _valid_stage(value: object, references: tuple[str, ...]) -> bool:
    """Whether a stored stage has the closed shape ``_stage_document`` writes."""
    if type(value) is not dict or set(value) != {
        "choice",
        "edition_preferred",
        "exclusions",
        "groups",
        "issues",
        "language",
        "outcome",
        "reason",
    }:
        return False
    known = {*references, _UNREAD_CURRENT}
    issues, exclusions, groups = value["issues"], value["exclusions"], value["groups"]
    order = [issue.value for issue in CoverIssue]
    return (
        value["outcome"] in {outcome.value for outcome in CoverOutcome}
        and (value["choice"] is None or value["choice"] in references)
        and type(value["edition_preferred"]) is bool
        and (
            value["language"] is None
            if CoverIssue.EDITION_LANGUAGE.value not in issues
            else type(value["language"]) is str and 1 <= len(value["language"]) <= 280
        )
        and (value["reason"] is None or value["reason"] in STAGE_REASONS)
        and type(issues) is list
        and all(item in order for item in issues)
        and issues == sorted(set(issues), key=order.index)
        and type(exclusions) is list
        and all(
            type(item) is dict
            and set(item) == {"exclusion", "reference"}
            and item["reference"] in references
            and item["exclusion"] in {exclusion.value for exclusion in CoverExclusion}
            for item in exclusions
        )
        and type(groups) is list
        and all(
            type(item) is dict
            and set(item) == {"members", "representative"}
            and type(item["members"]) is list
            and len(item["members"]) >= 2
            and all(member in known for member in item["members"])
            and item["representative"] in item["members"]
            for item in groups
        )
    )


def _record_title(candidate: ProviderCandidate) -> str | None:
    """The one work title a provider record states, if it states exactly one."""
    titles = [
        claim.value
        for claim in sorted(candidate.claims, key=lambda claim: claim.raw_order)
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and type(claim.value) is str
    ]
    return titles[0] if len(titles) == 1 else None


def _title_conflict(report: MetadataReport) -> bool:
    """Whether the case's title result is an unresolved choice that may name another book."""
    title = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )
    return title.outcome is ProposalOutcome.UNRESOLVED_CHOICE and bool(
        _TITLE_CONFLICT_REASONS & set(title.reasons)
    )


def _measured_inputs(
    report: MetadataReport,
    candidates: list[dict[str, object]],
    election_candidates: tuple[CoverElectionCandidate, ...],
) -> tuple[tuple[CoverRuleCandidate, ...], str, CurrentCover | None, str | None]:
    """Build the stage's candidates, medium and current cover from the report.

    The last value names the current cover's edition language when it is
    outside the library's, for the reason the owner reads.

    Each candidate's measures and size come from the artifact the packet's
    dimensions name; its edition and title facts come from the provider
    sources it is bound to.
    """
    sources = {
        cover_source_reference(report.report_id, source.source_id): source
        for source in report.cover_sources
        if source.state is CoverSourceState.RETRIEVED and source.artifact is not None
    }
    target_title = next(
        (
            field.value
            for field in report.target.fields
            if field.destination is TargetDestination.TITLE
            and field.state is TargetFieldState.VALUE
            and type(field.value) is str
        ),
        None,
    )
    eligible = {candidate.reference: candidate for candidate in election_candidates}
    rule_candidates: list[CoverRuleCandidate] = []
    current: CurrentCover | None = None
    current_language: str | None = None
    for candidate in candidates:
        bound = [
            sources[cast("str", binding["reference"])]
            for binding in cast("list[dict[str, object]]", candidate["source_bindings"])
        ]
        reference = cast("str", candidate["reference"])
        if candidate["incumbent"] is True:
            abs_source = next(s for s in bound if s.kind is CoverSourceKind.ABS)
            artifact = cast("CoverArtifact", abs_source.artifact)
            # The current cover takes the edition flag from any provider
            # source showing the same original.
            same_original = [
                source
                for source in sources.values()
                if source.provenance is not None
                and cast("CoverArtifact", source.artifact).original_digest
                == artifact.original_digest
            ]
            election = eligible.get(reference)
            current_language = _edition_language(report, same_original)
            current = CurrentCover(
                reference=reference,
                identity=None if election is None else election.identity,
                readable=True,
                width=artifact.width,
                height=artifact.height,
                measures=artifact.measures,
                edition_outside_library_languages=current_language is not None,
            )
            continue
        election = eligible.get(reference)
        if election is None:
            continue
        artifact = cast(
            "CoverArtifact",
            max(
                (
                    source
                    for source in bound
                    if cast("CoverArtifact", source.artifact).width == election.width
                    and cast("CoverArtifact", source.artifact).height == election.height
                ),
                key=lambda source: cast("CoverArtifact", source.artifact).artifact_id,
            ).artifact,
        )
        rule_candidates.append(
            CoverRuleCandidate(
                reference=reference,
                identity=election.identity,
                width=election.width,
                height=election.height,
                packet_ordinal=election.packet_ordinal,
                measures=artifact.measures,
                edition_outside_library_languages=_edition_language(report, bound)
                is not None,
                title_differs=_title_differs(report, bound, target_title),
            )
        )
    target = next(
        field
        for field in report.target.fields
        if field.destination is TargetDestination.COVER
    )
    if current is None and target.state not in {
        TargetFieldState.MISSING,
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
    }:
        # The target states a cover that no retrieved image shows.
        current = CurrentCover(reference=_UNREAD_CURRENT, identity=None, readable=False)
    medium = "audiobook" if report.file.format in AUDIO_FILE_FORMATS else "ebook"
    return tuple(rule_candidates), medium, current, current_language


def larger_copy_source(
    report: MetadataReport, candidates: list[dict[str, object]]
) -> CoverSource | None:
    """The largest other original of the current cover's own preview, if larger.

    The packet keeps one candidate per preview, so a provider or EPUB copy
    showing the current cover's picture at a larger size is bound to the
    current cover's candidate rather than reviewed on its own. It is offered
    only when its short edge is longer than the current cover's.
    """
    sources = {
        cover_source_reference(report.report_id, source.source_id): source
        for source in report.cover_sources
        if source.state is CoverSourceState.RETRIEVED and source.artifact is not None
    }
    incumbent = next((c for c in candidates if c["incumbent"] is True), None)
    if incumbent is None:
        return None
    bound = [
        sources[cast("str", binding["reference"])]
        for binding in cast("list[dict[str, object]]", incumbent["source_bindings"])
    ]
    current = next(s for s in bound if s.kind is CoverSourceKind.ABS)
    current_artifact = cast("CoverArtifact", current.artifact)
    others = [
        source
        for source in bound
        if source.kind not in {CoverSourceKind.ABS, CoverSourceKind.RETAINED}
        and cast("CoverArtifact", source.artifact).original_digest
        != current_artifact.original_digest
    ]
    if not others:
        return None

    def short_edge(source: CoverSource) -> int:
        artifact = cast("CoverArtifact", source.artifact)
        return min(artifact.width, artifact.height)

    largest = max(
        others,
        key=lambda source: (
            short_edge(source),
            cast("CoverArtifact", source.artifact).width
            * cast("CoverArtifact", source.artifact).height,
            cast("CoverArtifact", source.artifact).artifact_id,
        ),
    )
    if short_edge(largest) <= short_edge(current):
        return None
    return largest


def _larger_copy(
    report: MetadataReport,
    candidates: list[dict[str, object]],
    rule_candidates: tuple[CoverRuleCandidate, ...],
    current: CurrentCover | None,
) -> CoverRuleCandidate | None:
    """The larger copy as a stage candidate, when one is worth offering.

    It needs a confirmed current cover, whose identity it shares, and no
    reviewed candidate of the same picture already at least as large.
    """
    source = larger_copy_source(report, candidates)
    if source is None or current is None or current.identity is None:
        return None
    artifact = cast("CoverArtifact", source.artifact)
    size = (artifact.width, artifact.height)
    current_size = current.size
    if current_size is not None and current.measures is not None:
        for candidate in rule_candidates:
            if (
                candidate.measures is not None
                and candidate.width * candidate.height >= size[0] * size[1]
                and same_art(
                    current.measures,
                    current_size,
                    candidate.measures,
                    (candidate.width, candidate.height),
                )
            ):
                return None
    return CoverRuleCandidate(
        reference=cover_source_reference(report.report_id, source.source_id),
        identity=current.identity,
        width=artifact.width,
        height=artifact.height,
        packet_ordinal=len(candidates),
        measures=artifact.measures,
        edition_outside_library_languages=current.edition_outside_library_languages,
    )


def _edition_language(report: MetadataReport, sources: list[CoverSource]) -> str | None:
    """Name the edition language the sources label when none is the library's.

    The name is the one the Language field writes, or the label itself when
    no name is known. With no library languages the rule does nothing
    (#1363 Q4).
    """
    languages = [
        context.value
        for source in sources
        if source.provenance is not None
        for context in source.provenance.context
        if context.field is CoverContextField.LANGUAGE
    ]
    if (
        not report.library_languages
        or not languages
        or any(
            language_outcomes_compatible(language, library)
            for language in languages
            for library in report.library_languages
        )
    ):
        return None
    return language_canonical_name(languages[0]) or languages[0]


def _title_differs(
    report: MetadataReport, sources: list[CoverSource], target_title: object
) -> bool:
    """Whether the provider records the sources bind to all title another book.

    A source binds to a provider candidate as identity classification binds it;
    a candidate with no bound titled record does not differ (#1363 Q5).
    """
    if type(target_title) is not str:
        return False
    titles: list[str] = []
    for source in sources:
        provenance = source.provenance
        if provenance is None or provenance.designation is CoverDesignation.TRUNCATION:
            continue
        listed = provenance.designation is CoverDesignation.LISTED_EDITION
        for observation in report.providers:
            if observation.provider != provenance.provider:
                continue
            for record in observation.candidates:
                if record.work_identity != provenance.work_identity or (
                    not listed
                    and provenance.edition_identity is not None
                    and record.identity != provenance.edition_identity
                ):
                    continue
                title = _record_title(record)
                if title is not None:
                    titles.append(title)
    return bool(titles) and not any(
        values_match(TargetDestination.TITLE, target_title, title) for title in titles
    )


def _identity_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
) -> dict[str, object]:
    value = _load_object(document, "cover identity")
    if (
        set(value)
        != {
            "candidates",
            "classifier_reference",
            "cover_packet_reference",
            "report_reference",
            "schema_version",
            "work_identity_level",
            "work_target_reference",
        }
        or value["schema_version"] != IDENTITY_SCHEMA_VERSION
        or value["work_identity_level"]
        not in {level.value for level in WorkIdentityLevel}
        or value["cover_packet_reference"] != packet_reference
        or value["classifier_reference"] != CLASSIFIER_REFERENCE
        or _REFERENCE.fullmatch(str(value["report_reference"])) is None
        or _REFERENCE.fullmatch(str(value["work_target_reference"])) is None
        or type(value["candidates"]) is not list
    ):
        raise ValueError("cover identity report is invalid")
    rows = value["candidates"]
    if len(rows) != len(references):
        raise ValueError("cover identity report is invalid")
    admitted = []
    expected = {candidate["reference"]: candidate for candidate in candidates}
    for row in rows:
        if (
            type(row) is not dict
            or set(row)
            != {
                "candidate_reference",
                "evidence_references",
                "outcome",
                "preview_reference",
                "reasons",
            }
            or _required_reference(row["candidate_reference"]) not in references
            or row["outcome"] not in _IDENTITY_OUTCOMES
            or type(row["evidence_references"]) is not list
            or type(row["reasons"]) is not list
            or row["preview_reference"]
            != _preview_reference(
                # ``bind_cover_packet`` already admitted the packet, so its
                # ``packet_reference`` is the validated reference string.
                cast("str", packet_reference),
                expected[row["candidate_reference"]],
            )
            or any(
                _REFERENCE.fullmatch(str(item)) is None
                for item in row["evidence_references"]
            )
            or any(type(item) is not str for item in row["reasons"])
        ):
            raise ValueError("cover identity report is invalid")
        admitted.append(row)
    if tuple(sorted(row["candidate_reference"] for row in admitted)) != tuple(
        sorted(references)
    ):
        raise ValueError("cover identity report is invalid")
    return value


def _observation_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
    profile_payload: bytes | None,
) -> dict[str, object]:
    value, taxonomy, taxonomy_reference, expected_instruction, expected_schema = (
        _observation_header(document, references)
    )
    if expected_instruction == RETAINED_V3_INSTRUCTION_SHA256:
        raise ValueError("cover observation report is not current")
    if type(profile_payload) is not bytes:
        raise ValueError("cover observation profile is invalid")
    expected_observer = _observer_reference(
        profile_payload,
        expected_instruction,
        expected_schema,
        taxonomy_reference,
    )
    value = _validated_observation_rows(
        value,
        packet_reference,
        candidates,
        references,
        taxonomy,
        taxonomy_reference,
        expected_instruction,
        expected_schema,
    )
    if value["observer_reference"] != expected_observer:
        raise ValueError("cover observation report is invalid")
    return value


def _observation_document_rows(
    document: str,
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
) -> tuple[dict[str, object], str, str, str]:
    """Validate retained observation semantics without claiming profile identity."""
    value, taxonomy, taxonomy_reference, expected_instruction, expected_schema = (
        _observation_header(document, references)
    )
    return (
        _validated_observation_rows(
            value,
            packet_reference,
            candidates,
            references,
            taxonomy,
            taxonomy_reference,
            expected_instruction,
            expected_schema,
        ),
        expected_instruction,
        expected_schema,
        taxonomy_reference,
    )


def _observation_header(
    document: str,
    references: tuple[str, ...],
) -> tuple[
    dict[str, object],
    tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    str,
    str,
    str,
]:
    value = _load_object(document, "cover observation")
    schema_version = value.get("schema_version")
    if type(schema_version) is not str:
        raise ValueError("cover observation report is invalid")
    legacy = schema_version in {LEGACY_SCHEMA_VERSION, PREVIOUS_SCHEMA_VERSION}
    taxonomy = LEGACY_TAXONOMY if legacy else TAXONOMY
    taxonomy_reference = LEGACY_TAXONOMY_REFERENCE if legacy else TAXONOMY_REFERENCE
    expected_instruction = {
        LEGACY_SCHEMA_VERSION: LEGACY_INSTRUCTION_SHA256,
        PREVIOUS_SCHEMA_VERSION: PREVIOUS_INSTRUCTION_SHA256,
        OBSERVATION_SCHEMA_VERSION: hashlib.sha256(
            INSTRUCTION.encode("utf-8")
        ).hexdigest(),
    }.get(schema_version)
    if (
        schema_version == OBSERVATION_SCHEMA_VERSION
        and value.get("instruction_sha256") == RETAINED_V3_INSTRUCTION_SHA256
    ):
        expected_instruction = RETAINED_V3_INSTRUCTION_SHA256
    expected_schema = hashlib.sha256(
        _output_schema(
            references,
            taxonomy=taxonomy,
            schema_version=(
                LEGACY_OUTPUT_SCHEMA_VERSION if legacy else OUTPUT_SCHEMA_VERSION
            ),
        ).encode("utf-8")
    ).hexdigest()
    if expected_instruction is None:
        raise ValueError("cover observation report is invalid")
    return (
        value,
        taxonomy,
        taxonomy_reference,
        expected_instruction,
        expected_schema,
    )


def _validated_observation_rows(
    value: dict[str, object],
    packet_reference: object,
    candidates: list[dict[str, object]],
    references: tuple[str, ...],
    taxonomy: tuple[tuple[str, tuple[tuple[str, str], ...]], ...],
    taxonomy_reference: str,
    expected_instruction: str,
    expected_schema: str,
) -> dict[str, object]:
    taxonomy_names = tuple(name for name, _values in taxonomy)
    taxonomy_values = {name: frozenset(dict(values)) for name, values in taxonomy}
    schema_version = value["schema_version"]
    valid_shape = (
        schema_version == LEGACY_SCHEMA_VERSION
        and set(value)
        == {
            "cover_packet_reference",
            "instruction_sha256",
            "observations",
            "observer_reference",
            "output_schema_sha256",
            "schema_version",
            "taxonomy_reference",
        }
    ) or (
        schema_version in {PREVIOUS_SCHEMA_VERSION, OBSERVATION_SCHEMA_VERSION}
        and set(value)
        == {
            "attempt_evidence",
            "cover_packet_reference",
            "instruction_sha256",
            "observations",
            "observer_reference",
            "output_schema_sha256",
            "schema_version",
            "taxonomy_reference",
        }
        and _attempt_evidence(value["attempt_evidence"])
    )
    if (
        not valid_shape
        or value["schema_version"]
        not in {
            LEGACY_SCHEMA_VERSION,
            PREVIOUS_SCHEMA_VERSION,
            OBSERVATION_SCHEMA_VERSION,
        }
        or value["cover_packet_reference"] != packet_reference
        or value["taxonomy_reference"] != taxonomy_reference
        or type(value["observer_reference"]) is not str
        or _REFERENCE.fullmatch(value["observer_reference"]) is None
        or value["instruction_sha256"] != expected_instruction
        or value["output_schema_sha256"] != expected_schema
        or type(value["observations"]) is not list
    ):
        raise ValueError("cover observation report is invalid")
    expected = {candidate["reference"]: candidate for candidate in candidates}
    admitted = []
    for row in value["observations"]:
        if (
            type(row) is not dict
            or set(row)
            != {
                "candidate_reference",
                "preview_sha256",
                "preview_variant",
                *taxonomy_names,
            }
            or _required_reference(row["candidate_reference"]) not in references
        ):
            raise ValueError("cover observation report is invalid")
        candidate = expected[row["candidate_reference"]]
        if (
            row["preview_sha256"] != candidate["preview_sha256"]
            or row["preview_variant"] != candidate["preview_variant"]
            or any(row[name] not in taxonomy_values[name] for name in taxonomy_names)
        ):
            raise ValueError("cover observation report is invalid")
        admitted.append(row)
    if tuple(sorted(row["candidate_reference"] for row in admitted)) != tuple(
        sorted(references)
    ):
        raise ValueError("cover observation report is invalid")
    return value


def _attempt_evidence(value: object) -> bool:
    if type(value) is not list or not 1 <= len(value) <= 2:
        return False
    for index, attempt in enumerate(value):
        if type(attempt) is not dict or attempt.get("dispatched") is not True:
            return False
        if index == 0 and len(value) == 2:
            if (
                set(attempt)
                != {
                    "diagnostic_bytes",
                    "diagnostic_sha256",
                    "dispatched",
                    "failure_code",
                }
                or attempt["failure_code"] != "local_cli_abort"
                or type(attempt["diagnostic_bytes"]) is not int
                or not 1 <= attempt["diagnostic_bytes"] <= 2_147_483_647
                or type(attempt["diagnostic_sha256"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", attempt["diagnostic_sha256"]) is None
            ):
                return False
        elif set(attempt) != {"dispatched"}:
            return False
    return True


def _report(
    packet_reference: object,
    classifier_reference: object,
    policy: CoverPolicy,
    identity_document: str,
    observation_document: str | None,
    observation_taxonomy_reference: str,
    acceptable: tuple[str, ...],
    recommendation: str | None,
    owner_ready: bool,
    actual_target_state: str,
    reasons: tuple[str, ...],
    stage: dict[str, object],
) -> CoverPolicyReport:
    if recommendation is not None and recommendation not in acceptable:
        raise ValueError("cover recommendation is invalid")
    status = "recommended" if recommendation is not None else "abstained"
    body = {
        "acceptable_candidate_references": list(acceptable),
        "actual_target_state": actual_target_state,
        "cover_packet_reference": packet_reference,
        "identity_classifier_reference": classifier_reference,
        "identity_report_sha256": hashlib.sha256(
            identity_document.encode("utf-8")
        ).hexdigest(),
        "observation_report_sha256": (
            hashlib.sha256(observation_document.encode("utf-8")).hexdigest()
            if observation_document is not None
            else None
        ),
        "observation_taxonomy_reference": observation_taxonomy_reference,
        "owner_ready": owner_ready,
        "policy_reference": policy.reference,
        "reasons": list(reasons),
        "recommended_candidate_reference": recommendation,
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "status": status,
    }
    return CoverPolicyReport(canonical_packet_json(body))


def read_cover_policy_report(
    document: str,
    *,
    packet_reference: str,
    classifier_reference: str,
    policy: CoverPolicy,
    identity_document: str,
    observation_document: str | None,
    observation_taxonomy_reference: str,
    candidate_references: tuple[str, ...],
) -> dict[str, object]:
    """Validate one canonical policy report against its retained inputs."""
    value = _load_object(document, "cover policy report")
    fields = {
        "acceptable_candidate_references",
        "actual_target_state",
        "cover_packet_reference",
        "identity_classifier_reference",
        "identity_report_sha256",
        "observation_report_sha256",
        "observation_taxonomy_reference",
        "owner_ready",
        "policy_reference",
        "reasons",
        "recommended_candidate_reference",
        "schema_version",
        "stage",
        "status",
    }
    acceptable_value = value.get("acceptable_candidate_references")
    reasons_value = value.get("reasons")
    recommendation = value.get("recommended_candidate_reference")
    if (
        set(value) != fields
        or value.get("schema_version") != SCHEMA_VERSION
        or type(acceptable_value) is not list
        or any(type(item) is not str for item in acceptable_value)
        or len(set(acceptable_value)) != len(acceptable_value)
        or any(item not in candidate_references for item in acceptable_value)
        or type(value.get("actual_target_state")) is not str
        or value.get("actual_target_state")
        not in {item.value for item in CoverActualTargetState}
        or value.get("cover_packet_reference") != packet_reference
        or value.get("identity_classifier_reference") != classifier_reference
        or value.get("identity_report_sha256")
        != hashlib.sha256(identity_document.encode("utf-8")).hexdigest()
        or value.get("observation_report_sha256")
        != (
            None
            if observation_document is None
            else hashlib.sha256(observation_document.encode("utf-8")).hexdigest()
        )
        or value.get("observation_taxonomy_reference") != observation_taxonomy_reference
        or type(value.get("owner_ready")) is not bool
        or value.get("policy_reference") != policy.reference
        or type(reasons_value) is not list
        or len(reasons_value) != 1
        or any(
            type(item) is not str or item not in _REPORT_REASONS
            for item in reasons_value
        )
        or (recommendation is not None and type(recommendation) is not str)
        or not _valid_stage(value.get("stage"), candidate_references)
        or recommendation
        != (
            cast("dict[str, object]", value["stage"])["choice"]
            if reasons_value != ["provider_fetching"]
            and cast("dict[str, object]", value["stage"])["outcome"]
            in _CHOOSING_OUTCOMES
            else None
        )
    ):
        raise ValueError("cover policy report is invalid")
    acceptable = tuple(cast("list[str]", acceptable_value))
    reasons = tuple(cast("list[str]", reasons_value))
    if (
        _report(
            packet_reference,
            classifier_reference,
            policy,
            identity_document,
            observation_document,
            observation_taxonomy_reference,
            acceptable,
            recommendation,
            cast("bool", value["owner_ready"]),
            cast("str", value["actual_target_state"]),
            reasons,
            cast("dict[str, object]", value["stage"]),
        ).document
        != document
    ):
        raise ValueError("cover policy report is invalid")
    return value


def _required_reference(value: object) -> str:
    if type(value) is not str or _REFERENCE.fullmatch(value) is None:
        raise ValueError("cover reference is invalid")
    return value


def _load_object(document: str, label: str) -> dict[str, object]:
    try:
        if (
            type(document) is not str
            or not 2 <= len(document.encode("utf-8")) <= 1_048_576
        ):
            raise ValueError
        value = json.loads(
            document,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if type(value) is not dict or canonical_packet_json(value) != document:
            raise ValueError
        return value
    except (
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ValueError(f"{label} document is invalid") from error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid number: {value}")


__all__ = [
    "CoverPolicy",
    "CoverPolicyReport",
    "POLICY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "adjudicate_cover_policy",
    "larger_copy_source",
]
