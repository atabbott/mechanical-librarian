"""Immutable, exact-version human decisions for ordinary review fields."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Final, Literal, Mapping, Self, cast

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    field_value_version,
    project_decision_value,
)
from mechanical_librarian.domain.review_case import (
    LEVEL_COMBINATION_DESTINATIONS,
    ReviewCase,
    ReviewCaseValueError,
    compose_offer,
    proposal_media_kind,
    review_proposals,
)
from mechanical_librarian.domain.validation import owner_text_value
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel

COVER_SELECTION_AUTHORITY_SCHEMA: Final = "cover-selection-authority-v3"

HUMAN_DECISION_SCHEMA: Final = "human-decision-v4"

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
# ``reject`` is readable history only; the last two are F59's owner commands
# (ADR-0085). Exactly the value-bearing outcomes carry a selected value.
_OUTCOMES = {
    "accept",
    "keep_current",
    "reject",
    "owner_entered",
    "withdraw",
}
VALUE_OUTCOMES = frozenset({"accept", "owner_entered"})
# The owner may type a value only for these, and choose a genre subset; every
# other destination keeps the proposal-only choices (ADR-0085).
OWNER_TEXT_DESTINATIONS = ("title", "subtitle", "publisher")
OWNER_SUBSET_DESTINATION = "genres"
OWNER_TEXT_MAXIMUM = 512
OWNER_SUBSET_MAXIMUM = 32
OWNER_SUBSET_OPTIONS_MAXIMUM = 160
# A cover and a description each have their own decision lineage; neither can
# be withdrawn here.
UNREVERSIBLE_DESTINATIONS = frozenset({"cover", "description"})
# What a recorded decision may go on to do. Every decision formed since F47
# carries `apply` on both media; `recorded_only` survives only as the F45
# history of decisions taken before audiobook writes existed (#968).
APPLICATIONS = ("apply", "recorded_only")
RECORDED_ONLY_REASON = (
    "recorded, not applied — decided before audiobook writes were available"
)
HumanDecisionAction = Literal["accept", "keep_current"]
# Why a decision row exists when the owner did not make it: absent for every
# owner choice; `matching_changed` for the reconciliation's valueless row that
# asks a field again after Book matching changed its offer (#1355, S10).
DECISION_CAUSES = frozenset({"matching_changed"})
# Why a choice's offer no longer stands (#1355): the case still waits for its
# Book matching check, or the field's offer is not the one the choice saw.
OFFER_CHANGED_REASONS = frozenset({"unreconciled_case", "digest_mismatch"})
# A cover choice binds its consideration, not this digest (AC16).
_UNBOUND_OFFER_DESTINATIONS = frozenset({"cover"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_WORK_IDENTITY_LEVELS = frozenset(level.value for level in WorkIdentityLevel)


OFFER_DIGEST_SCHEMA = "offered-alternatives-v1"


def offer_digest(
    offer: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    destination: str,
) -> str:
    """The SHA-256 of one field's current offer as the case shows it (#1355).

    ``offer`` is a case's ``current_offer``. Each offered alternative counts by
    its value, its field's proposal id and its lineage rule, so a level
    alternative's digest names ``work_identity:<level>`` and a live one names
    no rule. The title, subtitle and series group has one digest over all its
    members. A field offering nothing still has a digest: the empty offer's.
    """
    members = (
        LEVEL_COMBINATION_DESTINATIONS
        if destination in LEVEL_COMBINATION_DESTINATIONS
        else (destination,)
    )
    by_destination = {proposal.get("destination"): proposal for proposal in offer}
    if destination not in by_destination:
        raise HumanDecisionError("invalid")
    data: list[dict[str, object]] = []
    for member in (value for value in members if value in by_destination):
        proposal = by_destination[member]
        result = proposal.get("result")
        lineage = proposal.get("lineage", {})
        if (
            type(result) is not dict
            or type(result.get("alternatives")) is not list
            or type(proposal.get("proposal_id")) is not str
            or type(lineage) is not dict
        ):
            raise HumanDecisionError("invalid")
        data.append(
            {
                "alternatives": sorted(
                    (
                        {
                            "rule": cast(
                                "dict[str, object]",
                                lineage.get(alternative["alternative_id"], {}),
                            ).get("rule"),
                            "value": alternative["value"],
                        }
                        for alternative in cast(
                            "list[dict[str, object]]", result["alternatives"]
                        )
                    ),
                    key=_canonical,
                ),
                "destination": member,
                "proposal_id": proposal["proposal_id"],
            }
        )
    return hashlib.sha256(
        _canonical({"members": data, "schema": OFFER_DIGEST_SCHEMA}).encode("utf-8")
    ).hexdigest()


def manual_cover_eligible(candidate: Mapping[str, object]) -> bool:
    """Owner choice may accept uncertainty, never positive identity contradiction.

    Callers first validate the complete retained consideration and currentness;
    this predicate neither validates a document nor grants automatic authority.
    A retained original has no identity to contradict: it is eligible while
    its projection still offers it, which requires its binding to be live
    (#1363, D2). A title-and-author match at a chosen Book matching level is
    eligible as any unconfirmed cover is: it carries no creator disagreement
    once the initials fold resolves one (#1355).
    """
    if candidate.get("candidate_kind") == "retained":
        return (
            candidate.get("incumbent") is False and candidate.get("selectable") is True
        )
    identity = candidate.get("identity")
    return (
        candidate.get("incumbent") is False
        and type(identity) is dict
        and identity.get("outcome")
        in {
            "verified_manifestation_match",
            "verified_work_match",
            "title_creator_work_match",
            "plausible_match",
            "unknown",
        }
        and "retained_creator_or_contributor_disagreement"
        not in identity.get("reasons", ())
    )


def owner_cover_selection_kind(
    outcome: str, candidate: Mapping[str, object] | None
) -> tuple[str | None, str | None]:
    """A cover-selection authority choice's candidate kind and retained plan.

    Only an accept names a kind: ``retained`` with its switch's plan for the
    original an automatic switch replaced, else ``candidate``. Keep and reject
    name none (#1363, D3).
    """
    if outcome != "accept" or candidate is None:
        return None, None
    if candidate.get("candidate_kind") == "retained":
        return "retained", cast(str, candidate["plan_id"])
    return "candidate", None


def validate_cover_confirmation(value: object) -> dict[str, object]:
    """Read the closed explicit owner confirmation without coercion."""
    if (
        type(value) is not dict
        or set(value) != {"expected_selection_id", "confirm_unverified_match"}
        or type(value["confirm_unverified_match"]) is not bool
        or (
            value["expected_selection_id"] is not None
            and (
                type(value["expected_selection_id"]) is not str
                or re.fullmatch(
                    r"cover_selection_[0-9a-f]{64}", value["expected_selection_id"]
                )
                is None
            )
        )
    ):
        raise HumanDecisionError("invalid_choice")
    return value


def admitted_applications(media_kind: str) -> frozenset[str]:
    """Say which applications a retained decision on this case may carry.

    One derivation, used wherever a retained document is validated. A
    `recorded_only` document is F45 history that only an audiobook case can
    carry, so the same document is stale against an ebook case; new commands
    are narrower still and must say `apply` (#968).
    """
    return (
        frozenset({"apply", "recorded_only"})
        if media_kind == "audiobook"
        else frozenset({"apply"})
    )


class HumanDecisionError(ValueError):
    """A fixed, value-free decision validation or state failure.

    Only ``offer_changed`` carries a closed ``reason`` (#1355).
    """

    def __init__(self, code: str, reason: str | None = None) -> None:
        if (code == "offer_changed") != (reason in OFFER_CHANGED_REASONS):
            raise ValueError("offer change reason is invalid")
        self.code = code
        self.reason = reason
        super().__init__(code)


def decision_actions(proposal: Mapping[str, object]) -> tuple[HumanDecisionAction, ...]:
    """Return exactly the new owner choices the immutable proposal admits."""
    result = proposal.get("result")
    if not _actionable(proposal) or type(result) is not dict:
        return ()
    alternatives = result.get("alternatives")
    if type(alternatives) is not list:
        return ()
    eligible = [
        alternative
        for alternative in alternatives
        if type(alternative) is dict
        and alternative.get("assessment") != "preserves_target"
    ]
    # ``reject`` remains readable historical state. A retain-current choice is
    # meaningful only beside an eligible alternative.
    return ("keep_current", "accept") if eligible else ()


def validate_decision_action(
    proposal: Mapping[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
) -> None:
    """Apply the selection half of the human-decision contract."""
    if outcome in {"owner_entered", "withdraw"}:
        _validate_owner_command(proposal, outcome, selected_value)
        return
    # Historical ``reject`` facts remain constructible and valid.  It is not
    # returned by ``decision_actions``, so no current projection can offer it.
    if outcome == "reject":
        if not _actionable(proposal):
            raise HumanDecisionError("unsupported")
        if selected_value is not None:
            raise HumanDecisionError("invalid_selection")
        return
    if outcome not in decision_actions(proposal):
        raise HumanDecisionError("unsupported")
    if outcome == "accept":
        result = proposal["result"]
        assert type(result) is dict
        alternatives = result["alternatives"]
        assert type(alternatives) is list
        if type(selected_value) is not dict or selected_value not in [
            alternative.get("value")
            for alternative in alternatives
            if type(alternative) is dict
            and alternative.get("assessment") != "preserves_target"
        ]:
            raise HumanDecisionError("invalid_selection")
    elif selected_value is not None:
        raise HumanDecisionError("invalid_selection")


def _validate_retained_decision_action(
    proposal: Mapping[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
) -> None:
    """Validate an immutable historical decision without reopening its command envelope."""
    if outcome != "accept":
        # Retained non-accept facts retain the established command validator's
        # exact rejection identity.  Only historical accepts need the broader
        # all-alternative membership check below.
        validate_decision_action(proposal, outcome, selected_value)
        return
    if outcome == "accept":
        result = proposal.get("result")
        alternatives = result.get("alternatives") if type(result) is dict else None
        if (
            type(selected_value) is not dict
            or type(alternatives) is not list
            or selected_value
            not in [
                alternative.get("value")
                for alternative in alternatives
                if type(alternative) is dict
            ]
        ):
            raise HumanDecisionError("invalid_selection")


def _actionable(proposal: Mapping[str, object]) -> bool:
    result = proposal.get("result")
    return type(result) is dict and (
        result.get("outcome")
        in {"propose_change", "acceptable_choice", "unresolved_choice"}
        or proposal.get("manual_required") is True
    )


def owner_text(value: object) -> bool:
    """Owner-typed text: canonical, bounded, and safe to project (ADR-0085)."""
    if not owner_text_value(value):
        return False
    try:
        validate_projection_text(value, "owner value", OWNER_TEXT_MAXIMUM)
    except EvidenceValueError:
        return False
    return True


def owner_value_admitted(proposal: Mapping[str, object]) -> bool:
    """Whether this field takes an owner-entered value or genre subset at all."""
    result = proposal.get("result")
    return (
        proposal.get("destination")
        in (*OWNER_TEXT_DESTINATIONS, OWNER_SUBSET_DESTINATION)
        and type(result) is dict
        and result.get("outcome") != "ineligible"
    )


def list_union(
    proposal: Mapping[str, object], current: object, previous: object = None
) -> tuple[tuple[str, bool, bool, bool], ...]:
    """A list field's union in its one order: current, each proposal's, previous.

    Each option says whether it is current, whether an eligible proposal holds
    it, and whether it is offered only because the field held it before its
    latest verified write. Values are compared exactly, never by their display
    text.
    """
    current_items, previous_items = (
        [item for item in value if type(item) is str] if type(value) is list else []
        for value in (current, previous)
    )
    result = proposal.get("result")
    alternatives = result.get("alternatives") if type(result) is dict else None
    proposed: list[str] = []
    for alternative in alternatives if type(alternatives) is list else ():
        value = alternative.get("value") if type(alternative) is dict else None
        if (
            type(alternative) is dict
            and alternative.get("assessment") != "preserves_target"
            and type(value) is dict
            and type(value.get("value")) is list
        ):
            proposed.extend(item for item in value["value"] if type(item) is str)
    listed = {*current_items, *proposed}
    union = list(dict.fromkeys([*current_items, *proposed, *previous_items]))
    held = set(proposed)
    return tuple(
        (item, item in current_items, item in held, item not in listed)
        for item in union
    )


def genre_subset_available(
    proposal: Mapping[str, object], current: object, previous: object = None
) -> bool:
    options = list_union(proposal, current, previous)
    return (
        owner_value_admitted(proposal)
        and proposal.get("destination") == OWNER_SUBSET_DESTINATION
        and (
            2 <= len(options) <= OWNER_SUBSET_OPTIONS_MAXIMUM
            or (len(options) == 1 and not options[0][1])
        )
    )


def _validate_owner_command(
    proposal: Mapping[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
) -> None:
    """The shape half of an owner-entered or withdraw command.

    What depends on the case's current value is checked where that value is
    held: `validate_owner_value` and the store.
    """
    destination = proposal.get("destination")
    if type(destination) is not str:
        raise HumanDecisionError("unsupported")
    if outcome == "withdraw":
        if destination in UNREVERSIBLE_DESTINATIONS:
            raise HumanDecisionError("unsupported")
        if selected_value is not None:
            raise HumanDecisionError("invalid_selection")
        return
    if not owner_value_admitted(proposal):
        raise HumanDecisionError("unsupported")
    value = selected_value.get("value") if type(selected_value) is dict else None
    if destination == OWNER_SUBSET_DESTINATION:
        valid = (
            type(value) is list
            and 1 <= len(value) <= OWNER_SUBSET_MAXIMUM
            and all(type(item) is str for item in value)
            and len(set(value)) == len(value)
        )
    else:
        valid = owner_text(value)
    try:
        project_decision_value(destination, selected_value)
    except FieldEffectError:
        valid = False
    if not valid:
        raise HumanDecisionError("owner_value_invalid")


def target_value(case: ReviewCase, destination: str) -> object:
    """The case's recorded target value for one destination; ``None`` if empty."""
    fields = case.target.get("fields")
    field = next(
        (
            item
            for item in (fields if type(fields) is list else ())
            if type(item) is dict and item.get("destination") == destination
        ),
        None,
    )
    if type(field) is not dict or field.get("state") != "value":
        return None
    return field.get("value")


def validate_owner_value(
    case: ReviewCase,
    proposal: Mapping[str, object],
    selected_value: dict[str, object] | None,
    previous: object = None,
) -> None:
    """The case half of an owner-entered value: in the union and a change.

    It runs only for a new command, where the case's target is the one the
    owner saw. ``previous`` is what the field held before its latest verified
    write. The server compares; it never rewrites what was submitted.
    """
    destination = str(proposal["destination"])
    assert type(selected_value) is dict
    value = selected_value["value"]
    current = target_value(case, destination)
    if destination == OWNER_SUBSET_DESTINATION:
        order = {
            item: index
            for index, (item, *_) in enumerate(list_union(proposal, current, previous))
        }
        assert type(value) is list
        # The shape check refused duplicates, so in union order means sorted.
        positions = [order[item] for item in value if item in order]
        if (
            not genre_subset_available(proposal, current, previous)
            or len(positions) != len(value)
            or positions != sorted(positions)
        ):
            raise HumanDecisionError("owner_value_invalid")
    if field_value_version(destination, value) == field_value_version(
        destination, current
    ):
        raise HumanDecisionError("owner_value_unchanged")


OwnerSuggestionRule = Literal["title_case", "strip_part", "split_author", "combined"]


@dataclass(frozen=True, slots=True)
class OwnerSuggestion:
    """One pure normalisation of the current value; it only fills an input."""

    rule: OwnerSuggestionRule
    value: str


_SUGGESTION_RULES: dict[str, tuple[OwnerSuggestionRule, ...]] = {
    "title": ("title_case", "strip_part", "split_author", "combined"),
    "subtitle": ("title_case", "strip_part"),
    "publisher": ("title_case",),
}
_SMALL_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "for",
        "in",
        "nor",
        "of",
        "on",
        "or",
        "per",
        "the",
        "to",
        "vs",
        "via",
    }
)
_ROMAN = re.compile(r"(?:X{0,3})(?:IX|IV|V?I{0,3})", re.IGNORECASE)
_ROMAN_ONE_TO_TWENTY = "|".join(
    sorted(
        (
            "X" * (number // 10)
            + ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX")[number % 10]
            for number in range(1, 21)
        ),
        key=len,
        reverse=True,
    )
)
_PART_NUMBER = rf"(?:[0-9]{{1,3}}|{_ROMAN_ONE_TO_TWENTY})"
_PART_TOKEN = re.compile(
    r"(?:\s*[-–—:,]\s*|\s+)"
    r"(?P<open>[(\[])?(?:disc|disk|cd|part|pt\.?)\s*"
    + _PART_NUMBER
    + r"(?:\s*(?:of|/)\s*"
    + _PART_NUMBER
    + r")?(?P<close>[)\]])?\Z",
    re.IGNORECASE,
)
_PART_SEPARATORS = " \t-–—:,"
_AUTHOR_DASHES = (" - ", " – ", " — ")


def _letter(text: str) -> bool:
    return any(unicodedata.category(character).startswith("L") for character in text)


def _cased(character: str) -> bool:
    return unicodedata.category(character) in {"Lu", "Ll", "Lt"}


def _capitalized(segment: str) -> str:
    lowered = segment.lower()
    for index, character in enumerate(lowered):
        if _cased(character):
            titled = character.title()
            if len(titled) != 1:
                return lowered
            return lowered[:index] + titled + lowered[index + 1 :]
    return lowered


def _title_case(value: str) -> str | None:
    cased = [character for character in value if _cased(character)]
    if len(cased) < 2 or not (
        all(unicodedata.category(character) == "Lu" for character in cased)
        or all(unicodedata.category(character) == "Ll" for character in cased)
    ):
        return None
    words = value.split(" ")
    if any(len(word.lower()) != len(word) for word in words):
        return None
    result = []
    for index, word in enumerate(words):
        start = 0
        while start < len(word) and unicodedata.category(word[start])[0] not in "LN":
            start += 1
        end = len(word)
        while end > start and unicodedata.category(word[end - 1])[0] not in "LN":
            end -= 1
        core = word[start:end]
        previous = words[index - 1] if index else ""
        if core and _ROMAN.fullmatch(core):
            core = core.upper()
        elif (
            core.lower() in _SMALL_WORDS
            and 0 < index < len(words) - 1
            and not previous.endswith((":", "—", "–"))
            and previous != "-"
        ):
            core = core.lower()
        else:
            core = "".join(
                _capitalized(segment) if position % 2 == 0 else segment
                for position, segment in enumerate(re.split("([-‐])", core))
            )
        result.append(word[:start] + core + word[end:])
    return " ".join(result)


def _strip_part(value: str) -> str | None:
    match = _PART_TOKEN.search(value)
    if match is None or {"(": ")", "[": "]", None: None}[
        match.group("open")
    ] != match.group("close"):
        return None
    remaining = value[: match.start()].rstrip(_PART_SEPARATORS)
    depth = {"(": 0, "[": 0}
    for character in remaining:
        if character in "([":
            depth[character] += 1
        elif character in ")]":
            opener = "(" if character == ")" else "["
            depth[opener] -= 1
            if depth[opener] < 0:
                return None
    if not _letter(remaining) or any(depth.values()):
        return None
    return remaining


def _split_author(value: str, authors: tuple[str, ...]) -> str | None:
    if sum(value.count(dash) for dash in _AUTHOR_DASHES) != 1:
        return None
    dash = next(dash for dash in _AUTHOR_DASHES if dash in value)
    sides = [side.strip() for side in value.split(dash)]
    if not all(_letter(side) for side in sides):
        return None
    known = {unicodedata.normalize("NFKC", author).casefold() for author in authors}
    matches = [
        unicodedata.normalize("NFKC", side).casefold() in known for side in sides
    ]
    if matches.count(True) != 1:
        return None
    return sides[matches.index(False)]


def owner_value_suggestions(
    destination: str, current: str | None, authors: tuple[str, ...]
) -> tuple[OwnerSuggestion, ...]:
    """Suggest at most four spellings of the current value, never applying one.

    ``authors`` is the union of the current and eligible proposed authors. Each
    suggestion is owner-admissible text that differs from the current value.
    """
    rules = _SUGGESTION_RULES.get(destination, ())
    if type(current) is not str or not rules:
        return ()
    base = " ".join(unicodedata.normalize("NFC", current).split())
    single: dict[OwnerSuggestionRule, str | None] = {
        "title_case": _title_case(base),
        "strip_part": _strip_part(base),
        "split_author": _split_author(base, authors),
    }
    applied = 0
    combined = base
    for step in (
        _strip_part,
        lambda text: _split_author(text, authors),
        _title_case,
    ):
        stepped = step(combined)
        if stepped is not None:
            applied += 1
            combined = stepped
    candidates: list[tuple[OwnerSuggestionRule, str | None]] = [
        (rule, single[rule]) for rule in ("title_case", "strip_part", "split_author")
    ]
    if applied >= 2 and combined not in single.values():
        candidates.append(("combined", combined))
    suggestions: list[OwnerSuggestion] = []
    for rule, value in candidates:
        if (
            rule in rules
            and value is not None
            and value != current
            and owner_text(value)
            and all(value != suggestion.value for suggestion in suggestions)
        ):
            suggestions.append(OwnerSuggestion(rule, value))
    return tuple(suggestions[:4])


@dataclass(frozen=True, slots=True)
class DecisionScope:
    """One case's proposal projection, which every retained decision is checked in.

    Projecting a case's proposals re-parses its proposal set, so a reader with
    several decisions on one case builds the scope once and validates each
    decision in it rather than projecting the same case per decision (#1215).
    """

    review_case_id: str
    target_fingerprint: str
    proposals: tuple[dict[str, object], ...]
    applications: frozenset[str]
    # Each stored level offer of the case composed over its proposals, every
    # level, never deleted: a retained choice is checked in the offer its
    # bound digest names (#1355, AC1).
    offers: tuple[tuple[dict[str, object], ...], ...] = ()

    @classmethod
    def of(cls, case: ReviewCase, offer_documents: tuple[str, ...] = ()) -> Self:
        proposals = case.proposals
        return cls(
            case.review_case_id,
            case.target_fingerprint,
            proposals,
            admitted_applications(case.media_kind),
            _composed_offers(case.review_case_id, proposals, offer_documents),
        )

    @classmethod
    def of_browse_case(
        cls,
        review_case_id: str,
        case: dict[str, object],
        offer_documents: tuple[str, ...] = (),
    ) -> Self:
        """Project the bounded case envelope used by browse projections."""
        target_fingerprint = case.get("target_fingerprint")
        proposal_set_id = case.get("proposal_set_id")
        proposal_document = case.get("proposal_document")
        results = case.get("results")
        if (
            type(review_case_id) is not str
            or type(target_fingerprint) is not str
            or type(proposal_set_id) is not str
            or type(proposal_document) is not str
            or type(results) is not list
            or any(type(result) is not dict for result in results)
        ):
            raise HumanDecisionError("stale")
        try:
            proposals = review_proposals(proposal_set_id, proposal_document, results)
            media_kind = proposal_media_kind(proposal_document)
        except ReviewCaseValueError as error:
            raise HumanDecisionError("stale") from error
        return cls(
            review_case_id,
            target_fingerprint,
            proposals,
            admitted_applications(media_kind),
            _composed_offers(review_case_id, proposals, offer_documents),
        )


def _composed_offers(
    review_case_id: str,
    proposals: tuple[dict[str, object], ...],
    offer_documents: tuple[str, ...],
) -> tuple[tuple[dict[str, object], ...], ...]:
    try:
        return tuple(
            compose_offer(review_case_id, proposals, document)
            for document in offer_documents
        )
    except ReviewCaseValueError as error:
        raise HumanDecisionError("invalid") from error


@dataclass(frozen=True, slots=True)
class HumanDecision:
    decision_id: str
    review_case_id: str
    proposal_id: str
    target_fingerprint: str
    destination: str
    actor: str
    outcome: str
    selected_value: dict[str, object] | None
    idempotency_digest: str
    request_digest: str
    occurred_at: int
    application: str
    # The field's current offer as the case showed it at decision time
    # (#1355, S10): a choice stands only in that offer.
    offered_alternatives_digest: str
    # Absent for an owner's choice; `matching_changed` for the valueless row
    # the Book matching reconciliation appends.
    cause: str | None = None

    @staticmethod
    def idempotency_digest_for(idempotency_key: str) -> str:
        if (
            type(idempotency_key) is not str
            or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
        ):
            raise HumanDecisionError("invalid")
        return _sha256(idempotency_key)

    def matches_request(
        self,
        *,
        review_case_id: str,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        offered_alternatives_digest: str | None = None,
    ) -> bool:
        return (
            self.review_case_id == review_case_id
            and self.proposal_id == proposal_id
            and self.target_fingerprint == target_fingerprint
            and self.destination == destination
            and self.actor == actor
            and self.outcome == outcome
            and self.selected_value == selected_value
            and self.idempotency_digest == self.idempotency_digest_for(idempotency_key)
            and offered_alternatives_digest in {None, self.offered_alternatives_digest}
        )

    @classmethod
    def create(
        cls,
        case: ReviewCase,
        *,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        actor: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_key: str,
        occurred_at: int,
        previous: object = None,
        offer: tuple[dict[str, object], ...] | None = None,
        offered_alternatives_digest: str | None = None,
    ) -> Self:
        """Form an owner's choice in the field's current offer.

        ``offer`` is the case's ``current_offer`` (its live proposals when
        omitted). ``offered_alternatives_digest`` is the offer the page showed;
        omitted, it is the current offer's own digest.
        """
        if (
            actor != "owner"
            or outcome not in _OUTCOMES
            or type(occurred_at) is not int
            or occurred_at < 0
            or _IDEMPOTENCY_KEY.fullmatch(idempotency_key) is None
            or target_fingerprint != case.target_fingerprint
        ):
            raise HumanDecisionError("invalid")
        offered = case.proposals if offer is None else offer
        proposal = next(
            (
                value
                for value in offered
                if value["destination"] == destination
                and value["proposal_id"] == proposal_id
            ),
            None,
        )
        if proposal is None:
            raise HumanDecisionError("stale")
        current_digest = offer_digest(offered, destination)
        if offered_alternatives_digest is None:
            offered_alternatives_digest = current_digest
        _require_offer(destination, offered_alternatives_digest, current_digest)
        validate_decision_action(proposal, outcome, selected_value)
        if outcome == "owner_entered":
            validate_owner_value(case, proposal, selected_value, previous)
        value = cls._formed(
            case.review_case_id,
            proposal_id,
            target_fingerprint,
            destination,
            outcome,
            selected_value,
            cls.idempotency_digest_for(idempotency_key),
            occurred_at,
            offered_alternatives_digest,
            None,
        )
        value._validate_for(
            case.review_case_id,
            case.target_fingerprint,
            (offered,),
            applications=admitted_applications(case.media_kind),
        )
        return value

    @classmethod
    def matching_changed(
        cls,
        case: ReviewCase,
        superseded: HumanDecision,
        level: str,
        offer: tuple[dict[str, object], ...],
        occurred_at: int,
    ) -> Self:
        """The valueless row that asks a field again (#1355, amendment 3).

        Book matching's reconciliation appends it when the field's offer at
        ``level`` is no longer the one ``superseded`` was chosen in. It
        carries no value and binds the new offer's digest; one row exists per
        superseded choice and level, so a replayed reconciliation forms the
        same idempotency digest.
        """
        if (
            type(superseded) is not cls
            or superseded.review_case_id != case.review_case_id
            or superseded.cause is not None
            or superseded.destination in _UNBOUND_OFFER_DESTINATIONS
            or level not in _WORK_IDENTITY_LEVELS
            or type(occurred_at) is not int
            or occurred_at < 0
        ):
            raise HumanDecisionError("invalid")
        proposal = next(
            (
                value
                for value in case.proposals
                if value["destination"] == superseded.destination
            ),
            None,
        )
        if proposal is None:
            raise HumanDecisionError("stale")
        value = cls._formed(
            case.review_case_id,
            cast("str", proposal["proposal_id"]),
            case.target_fingerprint,
            superseded.destination,
            "withdraw",
            None,
            _sha256(f"matching-changed-v1:{superseded.decision_id}:{level}"),
            occurred_at,
            offer_digest(offer, superseded.destination),
            "matching_changed",
        )
        value._validate_for(
            case.review_case_id,
            case.target_fingerprint,
            (offer,),
            applications=admitted_applications(case.media_kind),
        )
        return value

    @classmethod
    def _formed(
        cls,
        review_case_id: str,
        proposal_id: str,
        target_fingerprint: str,
        destination: str,
        outcome: str,
        selected_value: dict[str, object] | None,
        idempotency_digest: str,
        occurred_at: int,
        offered_alternatives_digest: str,
        cause: str | None,
    ) -> Self:
        value = cls(
            "",
            review_case_id,
            proposal_id,
            target_fingerprint,
            destination,
            "owner",
            outcome,
            selected_value,
            idempotency_digest,
            "",
            occurred_at,
            "apply",
            offered_alternatives_digest,
            cause,
        )
        request_digest = value._expected_request_digest()
        return replace(
            value,
            decision_id=_decision_id(request_digest, occurred_at),
            request_digest=request_digest,
        )

    def _expected_request_digest(self) -> str:
        return _sha256(
            _canonical(
                {
                    "actor": self.actor,
                    "application": self.application,
                    "cause": self.cause,
                    "destination": self.destination,
                    "idempotency_digest": self.idempotency_digest,
                    "offered_alternatives_digest": self.offered_alternatives_digest,
                    "outcome": self.outcome,
                    "proposal_id": self.proposal_id,
                    "review_case_id": self.review_case_id,
                    "selected_value": self.selected_value,
                    "target_fingerprint": self.target_fingerprint,
                }
            )
        )

    def validate_for(
        self, case: ReviewCase, offer_documents: tuple[str, ...] = ()
    ) -> None:
        """Validate a retained immutable fact against its canonical case.

        ``offer_documents`` are the case's stored level offers, every level.
        """
        self.validate_in(DecisionScope.of(case, offer_documents))

    def validate_in(self, scope: DecisionScope) -> None:
        """Validate a retained immutable fact in its case's projected scope."""
        self._validate_for(
            scope.review_case_id,
            scope.target_fingerprint,
            (scope.proposals, *scope.offers),
            applications=scope.applications,
        )

    def validate_new_for(
        self,
        case: ReviewCase,
        previous: object = None,
        offer: tuple[dict[str, object], ...] | None = None,
    ) -> None:
        """Validate a newly submitted command against the current owner envelope.

        ``offer`` is the case's current offer (its live proposals when
        omitted); ``previous`` is what the field held before its latest
        verified write.
        """
        proposals = case.proposals if offer is None else offer
        self._validate_for(
            case.review_case_id,
            case.target_fingerprint,
            (proposals,),
            new_command=True,
            applications=admitted_applications(case.media_kind),
        )
        if self.outcome == "owner_entered":
            validate_owner_value(
                case,
                next(
                    value
                    for value in proposals
                    if value["proposal_id"] == self.proposal_id
                ),
                self.selected_value,
                previous,
            )

    def _validate_for(
        self,
        review_case_id: str,
        target_fingerprint: str,
        offers: tuple[tuple[dict[str, object], ...] | list[object], ...],
        *,
        new_command: bool = False,
        applications: frozenset[str],
    ) -> None:
        """Check the fact in the offer its digest names.

        ``offers`` starts with the live (or, for a new command, the current)
        offer; a retained fact may also stand in a stored level offer.
        """
        live = offers[0]
        if (
            self.review_case_id != review_case_id
            or self.target_fingerprint != target_fingerprint
            or _proposal(live, self.destination, self.proposal_id) is None
            or self.application not in applications
            or (new_command and (self.application != "apply" or self.cause))
        ):
            raise HumanDecisionError("stale")
        offer = self._bound_offer(offers, new_command)
        proposal = _proposal(offer, self.destination, self.proposal_id)
        assert proposal is not None
        if self.cause is not None:
            # A cause row is valueless and withdraws whatever the field held,
            # description and cover included (#1355, AC2).
            if self.outcome != "withdraw" or self.selected_value is not None:
                raise HumanDecisionError("invalid")
        elif new_command:
            validate_decision_action(proposal, self.outcome, self.selected_value)
        else:
            _validate_retained_decision_action(
                proposal, self.outcome, self.selected_value
            )
        if self.request_digest != self._expected_request_digest() or (
            self.decision_id != _decision_id(self.request_digest, self.occurred_at)
        ):
            raise HumanDecisionError("invalid")

    def _bound_offer(
        self,
        offers: tuple[tuple[dict[str, object], ...] | list[object], ...],
        new_command: bool,
    ) -> tuple[dict[str, object], ...] | list[object]:
        """The offer this fact's digest names (#1355, AC1).

        A new command stands only in the current offer. A retained fact stands
        in the live offer or any stored level offer whose digest it bound. A
        cover choice binds its consideration instead (AC16).
        """
        if self.destination in _UNBOUND_OFFER_DESTINATIONS:
            return offers[0]
        if new_command:
            _require_offer(
                self.destination,
                self.offered_alternatives_digest,
                offer_digest(_mappings(offers[0]), self.destination),
            )
            return offers[0]
        for offer in offers:
            if (
                _proposal(offer, self.destination, self.proposal_id) is not None
                and offer_digest(_mappings(offer), self.destination)
                == self.offered_alternatives_digest
            ):
                return offer
        raise HumanDecisionError("invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "application": self.application,
            "cause": self.cause,
            "decision_id": self.decision_id,
            "destination": self.destination,
            "idempotency_digest": self.idempotency_digest,
            "occurred_at": self.occurred_at,
            "offered_alternatives_digest": self.offered_alternatives_digest,
            "outcome": self.outcome,
            "proposal_id": self.proposal_id,
            "request_digest": self.request_digest,
            "review_case_id": self.review_case_id,
            "schema_version": HUMAN_DECISION_SCHEMA,
            "selected_value": self.selected_value,
            "target_fingerprint": self.target_fingerprint,
        }

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            raw = json.loads(serialized)
            if (
                type(raw) is not dict
                or set(raw) != _DOCUMENT_KEYS
                or raw["schema_version"] != HUMAN_DECISION_SCHEMA
            ):
                raise ValueError
            value = cls(
                raw["decision_id"],
                raw["review_case_id"],
                raw["proposal_id"],
                raw["target_fingerprint"],
                raw["destination"],
                raw["actor"],
                raw["outcome"],
                raw["selected_value"],
                raw["idempotency_digest"],
                raw["request_digest"],
                raw["occurred_at"],
                raw["application"],
                raw["offered_alternatives_digest"],
                raw["cause"],
            )
            if (
                re.fullmatch(r"human_decision_[0-9a-f]{64}", value.decision_id) is None
                or type(value.occurred_at) is not int
                or value.occurred_at < 0
                or any(
                    type(item) is not str or _DIGEST.fullmatch(item) is None
                    for item in (
                        value.idempotency_digest,
                        value.request_digest,
                        value.target_fingerprint,
                        value.offered_alternatives_digest,
                    )
                )
                or value.request_digest != value._expected_request_digest()
                or value.decision_id
                != _decision_id(value.request_digest, value.occurred_at)
                or re.fullmatch(r"review_case_[0-9a-f]{64}", value.review_case_id)
                is None
                or re.fullmatch(r"review_proposal_[0-9a-f]{64}", value.proposal_id)
                is None
                or type(value.destination) is not str
                or not 1 <= len(value.destination) <= 64
                or value.actor != "owner"
                or value.outcome not in _OUTCOMES
                or value.application not in APPLICATIONS
                or (value.outcome in VALUE_OUTCOMES)
                != (type(value.selected_value) is dict)
                or (
                    value.cause is not None
                    and (
                        value.cause not in DECISION_CAUSES
                        or value.outcome != "withdraw"
                        or value.selected_value is not None
                    )
                )
            ):
                raise ValueError
            return value
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise HumanDecisionError("invalid") from error


_DOCUMENT_KEYS = frozenset(
    {
        "actor",
        "application",
        "cause",
        "decision_id",
        "destination",
        "idempotency_digest",
        "occurred_at",
        "offered_alternatives_digest",
        "outcome",
        "proposal_id",
        "request_digest",
        "review_case_id",
        "schema_version",
        "selected_value",
        "target_fingerprint",
    }
)


def _decision_id(request_digest: str, occurred_at: int) -> str:
    return "human_decision_" + _sha256(
        _canonical({"request_digest": request_digest, "occurred_at": occurred_at})
    )


def _require_offer(destination: str, bound: str, current: str) -> None:
    """Refuse a choice formed in an offer the field no longer shows (#1355)."""
    if destination in _UNBOUND_OFFER_DESTINATIONS:
        if type(bound) is not str or _DIGEST.fullmatch(bound) is None:
            raise HumanDecisionError("invalid")
        return
    if bound != current:
        raise HumanDecisionError("offer_changed", "digest_mismatch")


def _proposal(
    offer: tuple[dict[str, object], ...] | list[object],
    destination: str,
    proposal_id: str,
) -> dict[str, object] | None:
    return next(
        (
            value
            for value in offer
            if type(value) is dict
            and value["destination"] == destination
            and value["proposal_id"] == proposal_id
        ),
        None,
    )


def _mappings(
    offer: tuple[dict[str, object], ...] | list[object],
) -> list[Mapping[str, object]]:
    return [value for value in offer if type(value) is dict]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
