"""Deterministic provider fake for the closed F09a technical preview."""

from __future__ import annotations

from dataclasses import dataclass

from mechanical_librarian.application.ports import (
    FailureKind,
    MetadataCandidate,
    OperationContext,
    Page,
    PageRequest,
    PortFailure,
    PortResult,
    ProviderProvenance,
    ProviderQuery,
    RetryAdvice,
    SafeSummary,
    provider_response_preflight,
)
from mechanical_librarian.application.title_proposals import SyntheticScenario
from mechanical_librarian.domain.evidence import FieldValue
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion
from mechanical_librarian.domain.inventory import FieldName


@dataclass(frozen=True)
class _Behavior:
    outcome: str
    candidate_override: str | None = None


_BEHAVIORS = {
    "local-provider-conflict": _Behavior("candidate", "A Different Provider Title"),
    "provider-disabled": _Behavior("disabled"),
    "provider-failed": _Behavior("failed"),
    "provider-empty": _Behavior("empty"),
    "provider-no-match": _Behavior("no_match"),
}


class SyntheticTitleProvider:
    """Implement only ``MetadataProvider``; it has no transport or credentials."""

    def __init__(self, scenario: SyntheticScenario) -> None:
        if type(scenario) is not SyntheticScenario:
            raise ValueError("synthetic title provider requires a closed scenario")
        self._scenario_name = scenario.name

    def lookup(
        self, query: ProviderQuery, page: PageRequest, context: OperationContext
    ) -> PortResult[Page[MetadataCandidate]]:
        if type(query) is not ProviderQuery or type(page) is not PageRequest or type(context) is not OperationContext:
            return PortResult.failed(_failure(FailureKind.INVALID_REQUEST, "synthetic provider request invalid"))
        preflight = query.preflight_result(page, context)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        if query.field is not FieldName.TITLE:
            return PortResult.failed(_failure(FailureKind.INVALID_REQUEST, "synthetic provider field invalid"))
        behavior = _BEHAVIORS.get(self._scenario_name, _Behavior("candidate"))
        if behavior.outcome == "disabled":
            return PortResult.failed(_failure(FailureKind.UNSUPPORTED, "synthetic provider disabled"))
        if behavior.outcome == "failed":
            return PortResult.failed(_failure(FailureKind.UNAVAILABLE, "synthetic provider unavailable", retryable=True))
        if behavior.outcome == "no_match":
            return PortResult.failed(_failure(FailureKind.NOT_FOUND, "synthetic provider found no match"))
        if behavior.outcome == "empty":
            result = Page((), None)
        elif behavior.outcome == "candidate":
            query_titles = tuple(
                value
                for fact in query.facts
                if fact.code.value == "title"
                for value in fact.values
            )
            if len(query_titles) != 1:
                return PortResult.failed(_failure(FailureKind.INVALID_REQUEST, "synthetic provider title query invalid"))
            candidate_title = behavior.candidate_override or query_titles[0]
            fingerprint = Fingerprint.from_canonical_value(
                {
                    "scenario": self._scenario_name,
                    "title": candidate_title,
                    "version": "synthetic-title-provider-v1",
                },
                schema=SchemaVersion("synthetic-provider-candidate-v1"),
            )
            result = Page(
                (
                    MetadataCandidate(
                        FieldName.TITLE,
                        FieldValue((candidate_title,)),
                        ProviderProvenance(
                            "synthetic-title-provider",
                            f"scenario-{self._scenario_name}",
                            fingerprint,
                        ),
                    ),
                ),
                None,
            )
        else:
            return PortResult.failed(_failure(FailureKind.INVALID_RESPONSE, "synthetic provider output invalid"))
        checked = provider_response_preflight(result, page, context, MetadataCandidate)
        if checked.failure is not None:
            return PortResult.failed(checked.failure)
        return PortResult.success(result)


def _failure(kind: FailureKind, summary: str, *, retryable: bool = False) -> PortFailure:
    return PortFailure(kind, RetryAdvice(retryable), SafeSummary(summary))


__all__ = ["SyntheticTitleProvider"]
