"""Draft-policy validation, impact projection, and proposal binding seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Protocol

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_cases import ReviewCaseError
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.audiobook_decision_qualification import (
    qualified_policy_bases,
)
from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    F19A_QUALIFIED_POLICY_OPTIONS,
    AutomaticSafetyState,
)
from mechanical_librarian.domain.cover_qualification_policy import (
    COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256,
    cover_aware_qualified_policy_options,
)
from mechanical_librarian.domain.human_decision import decision_actions
from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    audiobook_review_scope,
    evaluate_report,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichFieldPreview,
    RichMetadataPreview,
    compile_rich_metadata_preview,
    select_automatic_taxonomy_candidate,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_RICH_FIELDS,
    public_summary,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

if TYPE_CHECKING:
    from mechanical_librarian.application.configuration import ApplicationConfiguration

MAX_SHADOW_SAMPLE_RESPONSE_BYTES = 1_048_576


class MetadataPolicyApplicationError(RuntimeError):
    """A fixed, value-free draft policy failure."""


POLICY_CONTENTION_CONDITIONS = frozenset({"processing", "stale", "unqualified"})


class PolicyContentionError(MetadataReportError):
    """A policy seam refusing because the state it was given no longer holds.

    The seam below this application declares no error type, so contention used
    to be recognisable only by searching its message for a word. That made every
    rewording a silent change of control flow, and misread any unrelated failure
    whose message happened to contain one. The condition travels on the
    exception instead, drawn from a closed vocabulary, and the message stays
    free to say whatever reads best. It subclasses ``MetadataReportError`` so
    every caller that already matches the repository's error type still does.
    """

    def __init__(self, condition: str, message: str) -> None:
        if condition not in POLICY_CONTENTION_CONDITIONS:
            raise MetadataReportError("metadata policy contention is invalid")
        self.condition = condition
        super().__init__(message)


class AutomaticSafetyRepository(Protocol):
    """The narrow durable seam the owner-only safety controls depend upon."""

    def automatic_safety_state(self) -> AutomaticSafetyState: ...

    def set_automatic_safety_state(
        self, state: AutomaticSafetyState, updated_at: int
    ) -> None: ...

    def pause_automatic_safety(
        self, paused: bool, updated_at: int
    ) -> AutomaticSafetyState: ...

    def recover_automatic_safety(
        self, updated_at: int, category: str | None
    ) -> AutomaticSafetyState: ...


class AutomaticSafetyControls:
    """Owner-only durable pause/suspension controls; recovery is explicit.

    ``AutomaticSafetyRepository`` is a protocol with no declared error type, so
    neither the read nor the write below has an enumerable raisable set. Both
    handlers stay broad over it and report the same thing -- the durable safety
    state could not be established -- with the cause chained. Breadth is what
    keeps the control fail-closed: a caller that cannot read the state is never
    told automatic work is permitted.
    """

    def __init__(self, repository: AutomaticSafetyRepository) -> None:
        self._repository = repository

    def state(self) -> AutomaticSafetyState:
        try:
            return self._repository.automatic_safety_state()
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "automatic safety is unavailable"
            ) from error

    def pause(self, paused: bool, updated_at: int) -> AutomaticSafetyState:
        try:
            return self._repository.pause_automatic_safety(paused, updated_at)
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "automatic safety is unavailable"
            ) from error

    def suspend(
        self, reason: str, updated_at: int, category: str | None = None
    ) -> AutomaticSafetyState:
        if (
            type(reason) is not str
            or not reason
            or (category is not None and (type(category) is not str or not category))
        ):
            raise MetadataPolicyApplicationError("automatic safety is invalid")
        current = self.state()
        categories = dict(current.category_suspensions)
        if category is None:
            result = AutomaticSafetyState(
                current.paused, reason, current.category_suspensions
            )
        else:
            categories[category] = reason
            result = AutomaticSafetyState(
                current.paused,
                current.global_suspension,
                tuple(sorted(categories.items())),
            )
        try:
            self._repository.set_automatic_safety_state(result, updated_at)
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "automatic safety is unavailable"
            ) from error
        return result

    def recover(
        self, updated_at: int, category: str | None = None
    ) -> AutomaticSafetyState:
        try:
            return self._repository.recover_automatic_safety(updated_at, category)
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "automatic safety is unavailable"
            ) from error


class CurrentPolicyResolutionRepository(Protocol):
    """The three durable reads and one bind current-policy resolution needs.

    An annotation only: the composition root passes the concrete repository, so
    nothing this object can also do is withheld from its other collaborators.
    """

    def current_resolved_metadata_policy(
        self, library: str
    ) -> ResolvedMetadataPolicy | None: ...

    def active_metadata_policy(self) -> str: ...

    def bind_initial_metadata_policy_resolution(self, document: str) -> str: ...


def current_resolved_policy(
    configuration: ApplicationConfiguration,
    repository: CurrentPolicyResolutionRepository,
    library: str,
) -> ResolvedMetadataPolicy:
    """Reconcile the configured resolution with the durably bound one.

    Configuration is authoritative before the first bind and must agree with it
    afterwards, so a library whose override has drifted from its persisted
    resolution is a configuration failure rather than a silent re-resolution.
    """
    persisted = repository.current_resolved_metadata_policy(library)
    profile = MetadataPolicyProfile.from_data(
        json.loads(repository.active_metadata_policy())
    )
    configured = next(
        (value for value in configuration.libraries if value.name == library),
        None,
    )
    if configured is None:
        raise ReviewCaseError("configuration")
    configured_resolution = resolve_policy(profile, library, configured.policy_override)
    if persisted is not None:
        if persisted != configured_resolution:
            raise ReviewCaseError("configuration")
        return persisted
    bound_id = repository.bind_initial_metadata_policy_resolution(
        configured_resolution.canonical_json
    )
    if bound_id != configured_resolution.resolved_id:
        raise ReviewCaseError("configuration")
    return configured_resolution


@dataclass(frozen=True, slots=True)
class PolicyPreviewLibraryCapture:
    library: str
    resolution: ResolvedMetadataPolicy | None
    current_unapplied_count: int
    pairs: tuple[tuple[MetadataReport, ProposalSet], ...]


@dataclass(frozen=True, slots=True)
class PolicyPreviewCapture:
    active: MetadataPolicyProfile
    generation: int
    libraries: tuple[PolicyPreviewLibraryCapture, ...]


@dataclass(frozen=True, slots=True)
class PolicyFieldComparison:
    active: RichFieldPreview
    draft: RichFieldPreview
    active_destination: str
    draft_destination: str
    changed: bool

    def to_data(self) -> dict[str, object]:
        return {
            "active": self.active.to_data(),
            "draft": self.draft.to_data(),
            "active_destination": self.active_destination,
            "draft_destination": self.draft_destination,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class PolicyCaseComparison:
    media_kind: str
    fields: tuple[PolicyFieldComparison, ...]

    def to_data(self) -> dict[str, object]:
        return {
            "media_kind": self.media_kind,
            "fields": [field.to_data() for field in self.fields],
        }


@dataclass(frozen=True, slots=True)
class LibraryPolicyImpact:
    library: str
    affected_categories: tuple[str, ...]
    current_unapplied_count: int
    resolved_policy_id: str
    cases: tuple[RichMetadataPreview, ...] = ()
    cases_total: int = 0
    cases_truncated: bool = False
    work_labels: tuple[str, ...] = ()
    comparisons: tuple[PolicyCaseComparison, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyPreview:
    profile_id: str
    active_policy_id: str
    active_generation: int
    validation: tuple[str, ...]
    libraries: tuple[LibraryPolicyImpact, ...]


MAX_RICH_PREVIEW_CASES = 20


@dataclass(frozen=True, slots=True)
class ProposalPolicyBinding:
    """Immutable association for a ProposalSet; it changes no evidence."""

    proposal_set_id: str
    resolved_policy_id: str


@dataclass(frozen=True, slots=True)
class PolicyTransition:
    transition_id: str
    from_policy_id: str
    to_policy_id: str
    generation: int
    state: str
    completed: int
    total: int
    failed: int
    acknowledged: bool
    # ``baseline`` is the generation the container was created with. The
    # transitions table declares ``generation INTEGER NOT NULL UNIQUE``, and
    # ``_create_schema`` seeds generation 1, so an owner activation producing a
    # generation-1 transition is impossible at the schema level rather than
    # merely unlikely. Without this the fresh container's seeded row was
    # indistinguishable from a completed owner activation, and the interface
    # reported one that never happened.
    origin: str = "activation"


@dataclass(frozen=True, slots=True)
class PolicyActivation:
    active: MetadataPolicyProfile
    transition: PolicyTransition | None


class PolicyPreviewRepository(Protocol):
    """Every durable read the draft-policy impact projection performs.

    The read-only preview surface is composed against exactly this seam, so a
    store that implements it can serve a preview without reaching a member it
    does not have.
    """

    def capture_policy_preview(
        self, libraries: tuple[str, ...], limit: int
    ) -> PolicyPreviewCapture: ...


class PolicyImpactRepository(PolicyPreviewRepository, Protocol):
    def current_unapplied_count(self, library: str) -> int: ...

    def active_metadata_policy(self) -> str: ...

    def create_metadata_policy_draft(self, document: str) -> str: ...

    def get_metadata_policy_draft(self, policy_id: str) -> str: ...

    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
        *,
        expected_active_generation: int,
    ) -> PolicyTransition | None: ...

    def metadata_policy_transition(self) -> PolicyTransition | None: ...

    def retry_metadata_policy_transition(
        self, transition_id: str, retried_at: int
    ) -> PolicyTransition: ...

    def acknowledge_metadata_policy_transition(
        self, transition_id: str, acknowledged_at: int
    ) -> PolicyTransition: ...

    def current_resolved_policy_id(self, library: str) -> str: ...

    def current_resolved_metadata_policy(
        self, library: str
    ) -> ResolvedMetadataPolicy | None: ...

    def shadow_posture(self) -> str: ...

    def enter_shadow_preview(
        self, updated_at: int, libraries: tuple[str, ...]
    ) -> str: ...
    def activate_qualified_automatic(self, updated_at: int) -> str: ...

    def list_shadow_outcomes(self, limit: int) -> tuple[tuple[str, int], ...]: ...


def preview_policy(
    profile: MetadataPolicyProfile,
    libraries: tuple[str, ...],
    overrides: dict[str, dict[str, object]],
    repository: PolicyPreviewRepository,
) -> PolicyPreview:
    if (
        type(profile) is not MetadataPolicyProfile
        or type(libraries) is not tuple
        or libraries != tuple(sorted(set(libraries)))
    ):
        raise MetadataPolicyApplicationError("policy preview is invalid")
    impacts: list[LibraryPolicyImpact] = []
    try:
        capture = repository.capture_policy_preview(libraries, MAX_RICH_PREVIEW_CASES)
        active_profile = capture.active
        if (
            type(capture) is not PolicyPreviewCapture
            or type(capture.generation) is not int
            or capture.generation < 1
            or tuple(item.library for item in capture.libraries) != libraries
            or sum(len(item.pairs) for item in capture.libraries)
            > MAX_RICH_PREVIEW_CASES
        ):
            raise MetadataPolicyApplicationError("policy preview is unavailable")
        for captured in capture.libraries:
            library = captured.library
            resolved = resolve_policy(profile, library, overrides.get(library))
            configured_active = resolve_policy(
                active_profile, library, overrides.get(library)
            )
            active = captured.resolution or configured_active
            if (
                active.profile_id != active_profile.policy_id
                or active.library != library
            ):
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            count = captured.current_unapplied_count
            pairs_total, pairs = count, captured.pairs
            resolved_options = dict(resolved.options)
            resolved_destinations = dict(resolved.destinations)
            active_options = dict(active.options)
            active_destinations = dict(active.destinations)
            categories = tuple(
                field
                for field in OPTION_CATALOGUE
                if resolved_options[field] != active_options[field]
                or resolved_destinations[field] != active_destinations[field]
            )
            if type(count) is not int or count < len(pairs):
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            # The stored pair proves exact-current lineage. Re-evaluate its
            # immutable report through the existing pure proposal compiler so
            # a changed draft receives a new exact policy binding without
            # mutating or retargeting the persisted proposal.
            if any(
                proposal.report_id != report.report_id
                or proposal.target_fingerprint != report.target.fingerprint
                for report, proposal in pairs
            ):
                raise MetadataPolicyApplicationError("policy preview is unavailable")
            # Keep both pure proposal results for meaningful candidate routing:
            # a preserved incumbent has no selected source ID, but can still
            # have a supported candidate whose review destination changed.
            compiled_pairs = tuple(
                tuple(
                    evaluate_report(
                        report, policy.resolved_id, dict(policy.destinations)
                    )
                    for policy in (active, resolved)
                )
                for report, _ in pairs
            )
            active_cases = tuple(
                compile_rich_metadata_preview(active, report, compiled[0])
                for (report, _), compiled in zip(pairs, compiled_pairs, strict=True)
            )
            cases = tuple(
                compile_rich_metadata_preview(resolved, report, compiled[1])
                for (report, _), compiled in zip(pairs, compiled_pairs, strict=True)
            )
            comparisons = tuple(
                PolicyCaseComparison(
                    proposal.media_kind,
                    tuple(
                        PolicyFieldComparison(
                            before,
                            after,
                            active_destinations[before.field],
                            resolved_destinations[after.field],
                            (
                                before.outcome,
                                before.desired_value,
                                before.qualified_value,
                            )
                            != (
                                after.outcome,
                                after.desired_value,
                                after.qualified_value,
                            )
                            or (
                                audiobook_review_scope(report) == "work"
                                and before.field
                                in {"description_fill", "genres", "series"}
                                and any(
                                    bool(
                                        decision_actions({"result": previous.to_data()})
                                    )
                                    != bool(
                                        decision_actions(
                                            {"result": following.to_data()}
                                        )
                                    )
                                    for previous, following in zip(
                                        compiled_pair[0].results,
                                        compiled_pair[1].results,
                                        strict=True,
                                    )
                                    if previous.destination
                                    is {
                                        "description_fill": TargetDestination.DESCRIPTION,
                                        "genres": TargetDestination.GENRES,
                                        "series": TargetDestination.SERIES,
                                    }[before.field]
                                )
                            )
                            or (
                                active_destinations[before.field]
                                != resolved_destinations[after.field]
                                and (
                                    before.source_candidate_id is not None
                                    or after.source_candidate_id is not None
                                    or (
                                        {
                                            active_destinations[before.field],
                                            resolved_destinations[after.field],
                                        }
                                        == {"ask", "automatic"}
                                        and before.field in {"genres", "tags"}
                                        and audiobook_review_scope(report) != "work"
                                        and any(
                                            projected.outcome == "no_op"
                                            and projected.reason == "target_preserved"
                                            and projected.option == "fill_empty"
                                            and projected.target_state == "value"
                                            and select_automatic_taxonomy_candidate(
                                                report,
                                                result,
                                                projected.field,
                                                projected.option,
                                            )[0]
                                            is not None
                                            for route, projected, compiled in (
                                                (
                                                    active_destinations[before.field],
                                                    before,
                                                    compiled_pair[0],
                                                ),
                                                (
                                                    resolved_destinations[after.field],
                                                    after,
                                                    compiled_pair[1],
                                                ),
                                            )
                                            if route == "automatic"
                                            for result in compiled.results
                                            if result.destination.value
                                            == projected.field
                                        )
                                    )
                                )
                            ),
                        )
                        for before, after in zip(old.fields, new.fields, strict=True)
                    ),
                )
                for old, new, (report, proposal), compiled_pair in zip(
                    active_cases, cases, pairs, compiled_pairs, strict=True
                )
            )
            work_labels = tuple(
                report.target.current_title or "Untitled" for report, _ in pairs
            )
            impacts.append(
                LibraryPolicyImpact(
                    library=library,
                    affected_categories=categories,
                    current_unapplied_count=count,
                    resolved_policy_id=resolved.resolved_id,
                    cases=cases,
                    cases_total=pairs_total,
                    cases_truncated=pairs_total > len(pairs),
                    work_labels=work_labels,
                    comparisons=comparisons,
                )
            )
    except MetadataPolicyApplicationError:
        raise
    except (MetadataPolicyError, Exception) as error:
        raise MetadataPolicyApplicationError("policy preview is unavailable") from error
    return PolicyPreview(
        profile.policy_id,
        active_profile.policy_id,
        capture.generation,
        (),
        tuple(impacts),
    )


def bind_proposal_policy(
    proposal_set_id: object, policy: ResolvedMetadataPolicy
) -> ProposalPolicyBinding:
    if (
        type(proposal_set_id) is not str
        or not proposal_set_id.startswith("proposal_set_")
        or len(proposal_set_id) != 77
        or type(policy) is not ResolvedMetadataPolicy
    ):
        raise MetadataPolicyApplicationError("proposal policy binding is invalid")
    return ProposalPolicyBinding(proposal_set_id, policy.resolved_id)


def require_named_libraries(libraries: tuple[str, ...]) -> tuple[str, ...]:
    """Accept only a sorted, unique, exact-`str` library tuple."""
    if (
        type(libraries) is not tuple
        or libraries != tuple(sorted(set(libraries)))
        or any(type(value) is not str for value in libraries)
    ):
        raise MetadataPolicyApplicationError("policy notebook is invalid")
    return libraries


def rich_qualification(profile: MetadataPolicyProfile) -> tuple[dict[str, object], ...]:
    """Expose the fixed public rich-field and cover terminal dispositions."""
    if type(profile) is not MetadataPolicyProfile:
        raise MetadataPolicyApplicationError("policy validation is invalid")
    retained = {
        (str(item["field"]), str(item["option"])): item for item in public_summary()
    }
    summaries = []
    for field, options in OPTION_CATALOGUE.items():
        for option in options:
            item = retained.get((field, option))
            if item is None:
                cover = field == "cover_order"
                available = (
                    cover or f"{field}:{option}" in F19A_QUALIFIED_POLICY_OPTIONS
                )
                item = {
                    "automatic_available": available,
                    "candidate_sha256": COVER_ELECTION_QUALIFICATION_MANIFEST_SHA256
                    if cover
                    else F19A_QUALIFICATION_MANIFEST_SHA256,
                    "disposition": "pass" if available else "not_qualified",
                    "field": field,
                    "option": option,
                }
            scope = (
                "none"
                if not item["automatic_available"]
                else "fill"
                if (field == "cover_order" and option == "replace_if_strictly_better")
                or field in {"subtitle", "description_fill", "isbn_form", "series"}
                or option == "fill_empty"
                else "add"
                if option == "add"
                else "replace"
                if option == "replace"
                else "fill_or_replace"
            )
            summaries.append({**item, "operation_scope": scope})
    return tuple(summaries)


def audiobook_qualification(
    profile: MetadataPolicyProfile,
) -> tuple[dict[str, object], ...]:
    """Expose which audiobook tranches a published F46 record qualifies.

    Routing is one owner choice for the whole library, so the surface that
    offers Automatic has to be able to see an audiobook record: while the
    passing set is empty every tranche states itself unavailable, and
    publishing a record makes the same summary state the option routable with
    no further change here or above (#967).
    """
    if type(profile) is not MetadataPolicyProfile:
        raise MetadataPolicyApplicationError("policy validation is invalid")
    return audiobook_decision_qualification.public_summary()


def require_qualified_automatic(
    profile: MetadataPolicyProfile,
) -> MetadataPolicyProfile:
    """Allow Automatic intent only while it is separately qualified.

    Routing is one owner choice for the whole library, so an option an
    audiobook record qualifies becomes routable here. What that routing then
    does to an ebook is unchanged: only a recording acts under the audiobook
    identity, and an ebook under the same routing still needs the ebook
    envelope, which is why `asin` is exempted from the rich check and never
    added to it (#967).
    """
    if type(profile) is not MetadataPolicyProfile:
        raise MetadataPolicyApplicationError("policy validation is invalid")
    requested = {
        f"{field}:{option}"
        for field, option in profile.options
        if dict(profile.destinations)[field] == "automatic"
    }
    rich_requested = {
        f"{field}:{option}"
        for field, option in profile.options
        if field in F20_RICH_FIELDS and dict(profile.destinations)[field] == "automatic"
    } - qualified_policy_bases()
    if (
        requested and not requested.issubset(cover_aware_qualified_policy_options())
    ) or not rich_requested.issubset(F20_PASSING_AUTOMATIC_OPTIONS):
        raise MetadataPolicyApplicationError("policy automatic unqualified")
    return profile


class PolicyPreviewApplication:
    """The read-only draft-validation and impact-preview boundary.

    Composed for the `policy-preview` CLI over `PolicyPreviewRepository`
    alone, so no operation here can reach a member that contract does not
    name.
    """

    def __init__(
        self,
        repository: PolicyPreviewRepository,
        libraries: tuple[str, ...],
        overrides: dict[str, dict[str, object]],
    ) -> None:
        self._repository = repository
        self._libraries = require_named_libraries(libraries)
        self._overrides = overrides
        self._gate = RLock()

    rich_qualification = staticmethod(rich_qualification)

    def validate_policy(
        self, name: object, options: object, destinations: object
    ) -> MetadataPolicyProfile:
        candidate = MetadataPolicyProfile.create(name, options, destinations)
        with self._gate:
            return require_qualified_automatic(candidate)

    def preview_policy(
        self, name: object, options: object, destinations: object
    ) -> tuple[MetadataPolicyProfile, PolicyPreview]:
        candidate = self.validate_policy(name, options, destinations)
        with self._gate:
            return candidate, preview_policy(
                require_qualified_automatic(candidate),
                self._libraries,
                self._overrides,
                self._repository,
            )


class PolicyNotebook:
    """Read-only policy notebook plus immutable draft creation.

    Configuration supplies only named-library sparse overrides and is never
    written here. Activation is exact-draft and exact-resolution bound.

    ``PolicyImpactRepository`` is a protocol with no declared error type and
    several optional members this class probes with ``hasattr``, so no handler
    below can enumerate what its call may raise. Each stays broad and converts
    with the cause chained; where a body can choose a
    ``MetadataPolicyApplicationError`` of its own, that refusal is re-raised
    untouched ahead of the conversion so the code it chose keeps its identity,
    and the handlers without that clause are the ones whose body chooses none.
    Nothing in this class discards a failure or answers from a default after
    one. Where a body raises a bare ``ValueError``, it is that method's own
    guard against a posture or transition the seam is not permitted to return,
    and the broad handler is the guard's intended destination.
    """

    def __init__(
        self,
        repository: PolicyImpactRepository,
        libraries: tuple[str, ...],
        overrides: dict[str, dict[str, object]],
    ) -> None:
        self._repository = repository
        self._libraries = require_named_libraries(libraries)
        self._overrides = overrides

    def active(self) -> MetadataPolicyProfile:
        try:
            if not hasattr(self._repository, "active_metadata_policy"):
                return MetadataPolicyProfile.builtin()
            return MetadataPolicyProfile.from_data(
                json.loads(self._repository.active_metadata_policy())
            )
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "active policy is unavailable"
            ) from error

    rich_qualification = staticmethod(rich_qualification)
    audiobook_qualification = staticmethod(audiobook_qualification)

    def validate(self, profile: MetadataPolicyProfile) -> MetadataPolicyProfile:
        return require_qualified_automatic(profile)

    def create(
        self, name: object, options: object, destinations: object
    ) -> MetadataPolicyProfile:
        try:
            profile = self.validate(
                MetadataPolicyProfile.create(name, options, destinations)
            )
            policy_id = self._repository.create_metadata_policy_draft(
                profile.canonical_json
            )
            if policy_id != profile.policy_id:
                raise MetadataPolicyApplicationError("policy draft is unavailable")
            return profile
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            # The block spans domain validation and an undeclared draft write,
            # and neither names its refusals in a type this layer can match on,
            # so the qualification refusal is recognised by its message and
            # everything else is reported as an unavailable draft. Both keep
            # the cause chained; the message match only ever narrows the code.
            if "automatic intent is unqualified" in str(error):
                raise MetadataPolicyApplicationError(
                    "policy automatic unqualified"
                ) from error
            raise MetadataPolicyApplicationError(
                "policy draft is unavailable"
            ) from error

    def read(self, policy_id: object) -> MetadataPolicyProfile:
        if type(policy_id) is not str or not policy_id.startswith("policy_"):
            raise MetadataPolicyApplicationError("policy draft is unavailable")
        try:
            profile = MetadataPolicyProfile.from_data(
                json.loads(self._repository.get_metadata_policy_draft(policy_id))
            )
            if profile.policy_id != policy_id:
                raise MetadataPolicyApplicationError("policy draft is unavailable")
            return profile
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "policy draft is unavailable"
            ) from error

    def preview(self, profile: MetadataPolicyProfile) -> PolicyPreview:
        return preview_policy(
            self.validate(profile),
            self._libraries,
            self._overrides,
            self._repository,
        )

    def shadow_state(self) -> dict[str, object]:
        try:
            posture = (
                self._repository.shadow_posture()
                if hasattr(self._repository, "shadow_posture")
                else "review"
            )
            if posture not in {"review", "preview", "automatic"}:
                raise ValueError
            return {
                "authority": "decision_only" if posture == "automatic" else "none",
                "can_leave_preview": False,
                "posture": posture,
                "schema_version": "shadow-posture-v1",
            }
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow posture is unavailable"
            ) from error

    def confirm_automatic(self, updated_at: object) -> dict[str, object]:
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataPolicyApplicationError("automatic posture is invalid")
        try:
            if self._repository.activate_qualified_automatic(updated_at) != "automatic":
                raise ValueError
            return self.shadow_state()
        except MetadataPolicyApplicationError:
            # ``shadow_state`` runs after ``activate_qualified_automatic`` has
            # durably succeeded, and it raises ``shadow posture is unavailable``
            # of its own. Absorbing that into ``automatic posture is
            # unavailable`` told the caller the activation failed when it had
            # already happened and only the read-back did not, leaving no way to
            # tell the two apart. The refusal keeps its own identity, as it does
            # in ``enter_preview``.
            raise
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "automatic posture is unavailable"
            ) from error

    def enter_preview(self, updated_at: object) -> dict[str, object]:
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataPolicyApplicationError("shadow posture is invalid")
        try:
            if (
                self._repository.enter_shadow_preview(updated_at, self._libraries)
                != "preview"
            ):
                raise ValueError
            return self.shadow_state()
        except MetadataPolicyApplicationError:
            raise
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow posture is unavailable"
            ) from error

    def preview_leave(self) -> dict[str, object]:
        """Describe leaving Preview; this increment has no commit method."""
        active = self.active()
        impacts = []
        for library in self._libraries:
            resolved = resolve_policy(active, library, self._overrides.get(library))
            impacts.append(
                {
                    "affected_categories": sorted(
                        field
                        for field, destination in resolved.destinations
                        if destination == "automatic"
                    ),
                    "current_unapplied_count": self._repository.current_unapplied_count(
                        library
                    ),
                    "library": library,
                }
            )
        return {
            "authority": "none",
            "can_confirm": False,
            "confirmation_required": True,
            "libraries": impacts,
            "posture": self.shadow_state()["posture"],
            "requested_posture": "qualified_decision_only_unavailable",
            "schema_version": "shadow-posture-leave-preview-v1",
        }

    def shadow_sample(self, limit: object = 20) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataPolicyApplicationError("shadow sample is invalid")
        try:
            items: list[dict[str, object]] = []
            for document, evaluated_at in self._repository.list_shadow_outcomes(limit):
                candidate = {
                    **ShadowOutcomeSet.from_json(document).to_data(),
                    "evaluated_at": evaluated_at,
                }
                bounded = {
                    "authority": "none",
                    "items": [*items, candidate],
                    "next_cursor": None,
                    "schema_version": "shadow-outcome-sample-v1",
                }
                if (
                    len(
                        json.dumps(
                            bounded,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    > MAX_SHADOW_SAMPLE_RESPONSE_BYTES
                ):
                    if not items:
                        raise ValueError
                    break
                items.append(candidate)
            return {
                "authority": "none",
                "items": items,
                "next_cursor": None,
                "schema_version": "shadow-outcome-sample-v1",
            }
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "shadow sample is unavailable"
            ) from error

    def activate(
        self,
        policy_id: object,
        expected_active_policy_id: object,
        activated_at: object,
        *,
        expected_active_generation: object,
    ) -> PolicyActivation:
        if (
            type(policy_id) is not str
            or type(expected_active_policy_id) is not str
            or type(expected_active_generation) is not int
            or expected_active_generation < 1
            or type(activated_at) is not int
            or activated_at < 0
        ):
            raise MetadataPolicyApplicationError("policy activation is invalid")
        profile = self.validate(self.read(policy_id))
        try:
            resolutions = tuple(
                (
                    library,
                    resolved.resolved_id,
                    resolved.canonical_json,
                )
                for library in self._libraries
                for resolved in (
                    resolve_policy(profile, library, self._overrides.get(library)),
                )
            )
            requested = {
                f"{category}:{dict(resolved.options)[category]}"
                for _, _, document in resolutions
                for resolved in (
                    ResolvedMetadataPolicy.from_data(json.loads(document)),
                )
                for category, destination in resolved.destinations
                if destination == "automatic"
            }
            if not requested.issubset(cover_aware_qualified_policy_options()):
                raise MetadataPolicyApplicationError("policy automatic unqualified")
            transition = self._repository.activate_metadata_policy(
                policy_id,
                expected_active_policy_id,
                resolutions,
                activated_at,
                expected_active_generation=expected_active_generation,
            )
            return PolicyActivation(profile, transition)
        except MetadataPolicyApplicationError:
            raise
        except PolicyContentionError as error:
            # The seam's three contention conditions are typed and closed, so
            # the branch a failure takes no longer depends on a word appearing
            # in a message. The wire codes below are unchanged, because the
            # transport reads them off this message.
            raise MetadataPolicyApplicationError(
                {
                    "processing": "policy transition is processing",
                    "stale": "policy activation is stale",
                    "unqualified": "policy activation is unqualified",
                }[error.condition]
            ) from error
        except Exception as error:
            # The block is wider than the activation seam: it resolves the
            # profile against every configured library and reopens each
            # resolution before the seam is reached, and a resolution that fails
            # lands here too. Contention is the only thing the seam names, and
            # it is matched above; everything else, a failed resolution
            # included, is honestly reported as an activation that is
            # unavailable. Both branches chain the cause, so no failure loses
            # its origin.
            raise MetadataPolicyApplicationError(
                "policy activation is unavailable"
            ) from error

    def transition(self) -> PolicyTransition | None:
        try:
            return self._repository.metadata_policy_transition()
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "policy transition is unavailable"
            ) from error

    def retry(self, transition_id: object, retried_at: object) -> PolicyTransition:
        if (
            type(transition_id) is not str
            or type(retried_at) is not int
            or retried_at < 0
        ):
            raise MetadataPolicyApplicationError("policy transition is invalid")
        try:
            return self._repository.retry_metadata_policy_transition(
                transition_id, retried_at
            )
        except PolicyContentionError as error:
            # Same seam as ``activate``, and staleness is the only condition it
            # signals here. Any other condition would be a contention this retry
            # cannot act on, so it stays unavailable rather than borrowing a
            # staleness it cannot demonstrate.
            if error.condition == "stale":
                raise MetadataPolicyApplicationError(
                    "policy transition is stale"
                ) from error
            raise MetadataPolicyApplicationError(
                "policy transition is unavailable"
            ) from error
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "policy transition is unavailable"
            ) from error

    def acknowledge(
        self, transition_id: object, acknowledged_at: object
    ) -> PolicyTransition:
        if (
            type(transition_id) is not str
            or type(acknowledged_at) is not int
            or acknowledged_at < 0
        ):
            raise MetadataPolicyApplicationError("policy transition is invalid")
        try:
            return self._repository.acknowledge_metadata_policy_transition(
                transition_id, acknowledged_at
            )
        except Exception as error:
            raise MetadataPolicyApplicationError(
                "policy transition is unavailable"
            ) from error


__all__ = [
    "LibraryPolicyImpact",
    "MAX_SHADOW_SAMPLE_RESPONSE_BYTES",
    "MetadataPolicyApplicationError",
    "PolicyActivation",
    "PolicyImpactRepository",
    "PolicyNotebook",
    "PolicyPreview",
    "PolicyPreviewApplication",
    "PolicyPreviewRepository",
    "PolicyTransition",
    "ProposalPolicyBinding",
    "bind_proposal_policy",
    "preview_policy",
]
