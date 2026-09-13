"""Synthetic packet fixtures for the retained deterministic decision engine."""

from mechanical_librarian.domain.shadow_review import ShadowReviewPacket


def synthetic_packet() -> ShadowReviewPacket:
    case_id = "review_case_" + "0" * 64
    proposal_id = "review_proposal_" + "1" * 64
    candidate_id = "alternative_" + "2" * 64
    citation_id = "synthetic_citation_" + "3" * 64
    correlation_id = "correlation_" + "4" * 64
    return ShadowReviewPacket(
        case_id,
        (
            {
                "candidates": [
                    {
                        "assessment": "replaces_target",
                        "candidate_id": candidate_id,
                        "correlations": [correlation_id],
                        "date_event": None,
                        "date_precision": None,
                        "decision_summary": {
                            "bibliographic_compatibility_ids": [],
                            "independent_title": False,
                            "recording_support": False,
                            "creator_roles": {"excluded": [], "included": []},
                            "evidence_scope": "work",
                            "publication": None,
                            "selected_manifestation_binding": "associated_work_only",
                            "support": {
                                "independent_provider_correlations": 0,
                                "local_evidence": 1,
                                "target_only_preservation": 0,
                            },
                        },
                        "evidence_references": [citation_id],
                        "scope": "work",
                        "value": "Synthetic title",
                    }
                ],
                "current": {
                    "confidence": "unknown",
                    "source": "synthetic",
                    "value": "Current synthetic title",
                },
                "decision_summary": {
                    "evidence_sufficiency": "manual",
                    "target_only_preservation": 0,
                },
                "destination": "title",
                "outcome": "unresolved_choice",
                "proposal_id": proposal_id,
                "reasons": ["Synthetic compatibility canary."],
            },
        ),
        (
            {
                "citation_id": citation_id,
                "fact": {
                    "reference": citation_id,
                    "state": "synthetic",
                    "value": "Synthetic title",
                },
            },
        ),
        (
            {
                "candidate_ids": [candidate_id],
                "correlation_id": correlation_id,
                "proposal_ids": [proposal_id],
            },
        ),
    )


__all__ = ["synthetic_packet"]
