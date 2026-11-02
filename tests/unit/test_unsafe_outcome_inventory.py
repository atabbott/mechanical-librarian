"""Every unsafe `/api/v1` route: which error answers it can give, and when.

The browser reads a 4xx refusal as "nothing changed" and anything else after
the request was sent as "this may have taken effect" (ADR-0091). That is true
only if no failure after a committed write is ever answered with a 4xx. This
table pins, for each unsafe route, the error status and code its handler can
return and the latest point at which each can happen:

- ``pre-call``: the trust boundary, the session dependency, the body limit or
  request validation answered before the handler body ran;
- ``pre-commit``: the handler refused before its durable write, or the write
  rolled back;
- ``no-commit``: the route writes no owner state at all (a read, a probe, or a
  side effect the client reconciles on any error);
- ``post-commit``: the answer can follow a committed write.

When the same answer can arise at several points, the latest one is recorded.
An unhandled exception is a bare 500 for every route and is not listed. A
code of ``*`` stands for every other code that the route's error mapper
passes through with that status.

The table is read from the handlers and their callees, and each row cites
where. The test checks the route set in both directions, so a new unsafe route
fails until it is classified and a removed one fails until its row is deleted.
It also checks that no 4xx is classified after the commit. The allow-list of
pre-call server codes is proven elsewhere and only run here:
`test_review_web_transport.py::test_pre_call_code_is_raised_only_by_the_session_dependency`
and the `uncertainWriteOutcome` block in `frontend/tests/http.test.ts`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal, cast, get_args

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from mechanical_librarian.application.reviewer_access import ReviewerAccessFailure
from tests.support.review_web_doubles import (
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)

When = Literal["pre-call", "pre-commit", "no-commit", "post-commit"]
Row = dict[tuple[int, str], When]

_UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Answered for every unsafe route before its handler body runs.
COMMON: Row = {
    # `trust_boundary` returns before `call_next` (app.py `_secured_error`).
    (400, "invalid_origin"): "pre-call",
    (403, "origin"): "pre-call",
    # `authenticated_session_cookie` and `_require_session`.
    (401, "unauthorized"): "pre-call",
    (403, "csrf"): "pre-call",
    (409, "browser_build_mismatch"): "pre-call",
    (503, "react_assets_unavailable"): "pre-call",
    # `BodyLimitMiddleware` answers before routing.
    (413, "body_too_large"): "pre-call",
    # The `RequestValidationError` handler (policy routes use their own code).
    (422, "invalid"): "pre-call",
    (422, "policy_validation"): "pre-call",
}

# `reviewer_failure_response`: every `ReviewerAccessApplicationError` code that
# is not 409 or 503 is a 422 refusal. Each is raised before `_replace_document`
# (provisioning, host, credential, probe and request checks in
# `application/reviewer_access.py`), or by a command that commits nothing.
_REVIEWER_422 = frozenset(get_args(ReviewerAccessFailure)) - {
    "access_changed",
    "credential_clear_failed",
    "unreachable",
    "timed_out",
}
_REVIEWER_COMMIT: Row = {
    # `_replace_document` CAS or a pre-write `_require_revision`; every
    # committed write answers `_view(committed=True)`, which skips the check.
    (409, "reviewer_access_stale"): "pre-commit",
    # Admission refusal (`ReviewApplication.serialized`) or a store fault,
    # which can include the commit itself.
    (503, "reviewer_access_unavailable"): "post-commit",
    # The provider probe runs before the write.
    (503, "unreachable"): "pre-commit",
    (503, "timed_out"): "pre-commit",
    **{(422, code): "pre-commit" for code in _REVIEWER_422},
}
_REVIEWER_NO_COMMIT: Row = dict.fromkeys(_REVIEWER_COMMIT, "no-commit")

# `_case_call`: ReviewWebError / ReviewApplicationError only. The committing
# calls raise neither after their commit: a cover retry's post-commit failure
# is a bare 500 (#1572, `test_review_web_read_models.py`), and a description
# retry's post-commit `_preparation.request` raises `ReviewPipelineError`,
# which `_case_call` does not catch.
_CASE_CALL: Row = {
    (400, "request"): "pre-commit",
    (409, "stale"): "pre-commit",
    (409, "already_decided"): "pre-commit",
    (404, "not_found"): "pre-commit",
    (404, "*"): "pre-commit",
}

# `_decision_call`. Every refusal is raised by the decision transaction before
# it commits (`adapters/persistence/decisions.py`); `decision_written` refuses
# a withdrawal of a decision already written to the target (`:740`). A read
# after the commit is absorbed by `_decision_confirmation`; the `run`
# catch-all is `unavailable`.
_DECISION: Row = {
    **{
        (409, code): "pre-commit"
        for code in (
            "stale",
            "already_decided",
            "idempotency_conflict",
            "nothing_to_withdraw",
            "automatic_hold",
            "decision_written",
        )
    },
    **{
        (422, code): "pre-commit"
        for code in (
            "invalid",
            "invalid_selection",
            "invalid_choice",
            "cover_consideration_required",
            "unsupported",
            "owner_value_invalid",
            "owner_value_unchanged",
        )
    },
    (404, "not_found"): "pre-commit",
    (503, "persistence"): "post-commit",
    (503, "*"): "post-commit",
}

# `field_effect_call`: 404 `not_found`, 409 for `_FIELD_EFFECT_STATE_CONFLICT`,
# 503 for everything else. The conflict codes come from guards and from the
# claim transaction before it commits (`adapters/persistence/
# field_effect_activity.py` claim, `application/field_effects.py` apply).
# `transition_conflict` after a claim would need a second writer moving a
# claimed, applying or reconciling plan; supersede and revoke move only
# planned and retryable plans (`_FIELD_EFFECT_READY_STATES_SQL`), and one
# application slot runs apply and reconcile. It is recorded as bug-only here.
_FIELD_EFFECT_CONFLICT: Row = {
    (409, code): "pre-commit"
    for code in (
        "authority",
        "capability",
        "claim_conflict",
        "consent",
        "draining",
        "paused",
        "transition_conflict",
    )
}
_FIELD_EFFECT: Row = {
    (404, "not_found"): "pre-commit",
    **_FIELD_EFFECT_CONFLICT,
    (503, "unavailable"): "post-commit",
    (503, "*"): "post-commit",
}

# `_preparation_http_error`: 404 `library`, 409 `paused`/`stale`/`blocked`
# (and `retry` on retry), 503 `unavailable`, else 503 `persistence`. The 4xx
# codes come from pipeline and presentation refusals before their write
# (`application/review_pipeline.py` request, `review_application.py`
# `dismiss_preparation`); the read-back after the write answers 503.
_PREPARATION: Row = {
    (404, "library"): "pre-commit",
    (409, "paused"): "pre-commit",
    (409, "stale"): "pre-commit",
    (409, "blocked"): "pre-commit",
    (503, "unavailable"): "post-commit",
    (503, "persistence"): "post-commit",
}

# `_settings_write_error` (contract A5: settings 409/422 are refusals of the
# write itself; a fault after it is `settings_unavailable`).
_SETTINGS: Row = {
    (409, "settings_stale"): "pre-commit",
    (409, "settings_managed_by_file"): "pre-commit",
    (422, "settings_invalid"): "pre-commit",
    (503, "settings_unavailable"): "post-commit",
}

# `_policy_problem` codes for a `MetadataPolicyError` from profile validation.
_POLICY_PROBLEM: Row = {
    (422, code): "pre-commit"
    for code in (
        "policy_option_unavailable",
        "policy_destination_unavailable",
        "policy_explicit_unsupported",
    )
}

_AUTOMATIC_SAFETY: Row = {
    # The op's own `except Exception`. After its commit the op only publishes
    # a constant topic, which cannot raise (`live_updates.py` `publish`).
    (422, "request"): "pre-commit",
    # The op's store fault, or `_committed_automatic_safety_response`.
    (503, "persistence"): "post-commit",
    (503, "unavailable"): "post-commit",
}

INVENTORY: dict[tuple[str, str], Row] = {
    # --- session and operations -------------------------------------------
    ("POST", "/api/v1/session/logout"): {
        # `OwnerSessionService.logout` refuses inside its revoke transaction;
        # `unauthorized` is a session another request already revoked.
        (403, "csrf"): "pre-commit",
        (401, "unauthorized"): "pre-commit",
        (503, "persistence"): "post-commit",
    },
    ("POST", "/api/v1/operations/support/export"): {
        (503, "support_unavailable"): "no-commit",
    },
    # --- settings ------------------------------------------------------------
    ("PATCH", "/api/v1/settings"): _SETTINGS,
    ("POST", "/api/v1/provider-recovery/retry"): {
        **_SETTINGS,
        # `retry_provider_recovery` compares the generation before writing.
        (409, "provider_recovery_stale"): "pre-commit",
    },
    # --- reviewer access -----------------------------------------------------
    ("PUT", "/api/v1/reviewer-access/openrouter"): {
        # The credential refusal is typed and raised before the probe; the
        # handler catches no plain `ValueError`, so a fault after the saved
        # key is a 500 (Amendment 1; `test_review_web_transport.py::
        # test_committed_openrouter_key_with_a_failed_view_is_a_server_error`).
        **_REVIEWER_COMMIT,
    },
    ("DELETE", "/api/v1/reviewer-access/openrouter"): _REVIEWER_COMMIT,
    # A probe failure is answered as a status in a 200 view; the status is
    # built outside the post-commit catch (`_refresh_openrouter` `else:`).
    ("POST", "/api/v1/reviewer-access/openrouter/refresh"): _REVIEWER_COMMIT,
    ("PUT", "/api/v1/reviewer-access/selection"): _REVIEWER_COMMIT,
    ("DELETE", "/api/v1/reviewer-access/codex"): {
        **_REVIEWER_COMMIT,
        # `clear_codex` clears the credential before the document write.
        (503, "credential_clear_failed"): "pre-commit",
    },
    ("POST", "/api/v1/reviewer-access/codex/device"): {
        **_REVIEWER_COMMIT,
        # Without a Codex selection to clear, the device flow starts and the
        # view re-checks the revision: no document write, and the client
        # reconciles a start on any error (`reviewerAccess.ts`).
        (409, "reviewer_access_stale"): "no-commit",
    },
    ("POST", "/api/v1/reviewer-access/codex/recheck"): _REVIEWER_NO_COMMIT,
    ("POST", "/api/v1/reviewer-access/codex/device/cancel"): _REVIEWER_NO_COMMIT,
    # Price and disclosure probes never write (`test_description_model`,
    # `describe_contained_description`).
    ("POST", "/api/v1/reviewer-access/description-model/test"): _REVIEWER_NO_COMMIT,
    (
        "POST",
        "/api/v1/reviewer-access/description-model/contained",
    ): _REVIEWER_NO_COMMIT,
    # --- metadata policy -----------------------------------------------------
    ("POST", "/api/v1/metadata-policy/drafts"): {
        **_POLICY_PROBLEM,
        # Raised by `MetadataPolicyNotebook.create` before the draft insert.
        # A scope-release failure after `connection.commit()` (B-78,
        # `adapters/persistence/transactions.py` `commit`) is bug-only.
        (422, "policy_automatic_unqualified"): "pre-commit",
        (503, "persistence"): "post-commit",
    },
    # Validation and preview write nothing (`http.ts` `policyCommand`).
    ("POST", "/api/v1/metadata-policy/validate"): {
        **dict.fromkeys(_POLICY_PROBLEM, "no-commit"),
        (422, "policy_validation_is_invalid"): "no-commit",
        (422, "policy_automatic_unqualified"): "no-commit",
        (503, "active_policy_is_unavailable"): "no-commit",
    },
    ("POST", "/api/v1/metadata-policy/preview"): {
        **dict.fromkeys(_POLICY_PROBLEM, "no-commit"),
        (422, "policy_validation_is_invalid"): "no-commit",
        (422, "policy_preview_is_invalid"): "no-commit",
        (422, "policy_automatic_unqualified"): "no-commit",
        (503, "policy_preview_is_unavailable"): "no-commit",
        (503, "active_policy_is_unavailable"): "no-commit",
    },
    ("POST", "/api/v1/metadata-policy/activate"): {
        # `MetadataPolicyNotebook.activate`: argument checks and the draft
        # read precede the write; every `PolicyContentionError` is raised
        # inside the activation transaction before it commits
        # (`adapters/persistence/metadata_policy.py` `activate_metadata_policy`).
        (422, "policy_activation_is_invalid"): "pre-commit",
        (422, "policy_automatic_unqualified"): "pre-commit",
        (422, "policy_activation_is_unqualified"): "pre-commit",
        (409, "policy_activation_is_stale"): "pre-commit",
        (409, "policy_transition_is_processing"): "pre-commit",
        (503, "policy_draft_is_unavailable"): "pre-commit",
        (503, "policy_activation_is_unavailable"): "post-commit",
    },
    ("POST", "/api/v1/metadata-policy/transitions/{transition_id}/retry"): {
        (422, "policy_transition_is_invalid"): "pre-commit",
        # Contention is raised inside the retry transaction.
        (409, "policy_transition_is_stale"): "pre-commit",
        (503, "policy_transition_is_unavailable"): "post-commit",
    },
    (
        "POST",
        "/api/v1/metadata-policy/transitions/{transition_id}/acknowledge",
    ): {
        (422, "policy_transition_is_invalid"): "pre-commit",
        (503, "policy_transition_is_unavailable"): "post-commit",
    },
    ("POST", "/api/v1/metadata-policy/shadow/enter"): {
        (503, "persistence"): "post-commit",
    },
    ("POST", "/api/v1/metadata-policy/shadow/confirm-automatic"): {
        # "automatic posture is unavailable" rolls back, except a
        # scope-release invariant failure after `connection.commit()` (B-78,
        # `adapters/persistence/transactions.py` `commit`), bug-only.
        (422, "automatic_unavailable"): "pre-commit",
        # "shadow posture is unavailable": activation committed and only the
        # read-back failed (`test_confirm_automatic_read_back_failure_is_
        # uncertain_not_refused`).
        (503, "persistence"): "post-commit",
    },
    ("POST", "/api/v1/metadata-policy/automatic-safety/pause"): _AUTOMATIC_SAFETY,
    ("POST", "/api/v1/metadata-policy/automatic-safety/recover"): _AUTOMATIC_SAFETY,
    ("POST", "/api/v1/metadata-policy/automatic-safety/suspend"): _AUTOMATIC_SAFETY,
    # --- activity and field effects -------------------------------------------
    ("POST", "/api/v1/activity/consent"): {
        # `ContinuousGuardedApplication.enable` checks every grant, backup and
        # readiness before `grant_field_effect_consent_matrix`.
        (409, "consent"): "pre-commit",
        (409, "capability"): "pre-commit",
        (503, "backup"): "pre-commit",
        (503, "not_ready"): "pre-commit",
        (503, "*"): "post-commit",
    },
    ("POST", "/api/v1/activity/{connection}/{library}/revoke"): {
        (409, "consent"): "pre-commit",
        (503, "*"): "post-commit",
    },
    ("POST", "/api/v1/field-effects/pause"): {(503, "*"): "post-commit"},
    ("POST", "/api/v1/field-effects/resume"): {(503, "*"): "post-commit"},
    ("POST", "/api/v1/field-effects/{plan_id}/apply"): {
        **_FIELD_EFFECT,
        (503, "claim_failed"): "pre-commit",
    },
    ("POST", "/api/v1/field-effects/{plan_id}/reconcile"): _FIELD_EFFECT,
    (
        "POST",
        "/api/v1/review-cases/{review_case_id}/fields/{destination}/return-to-automatic",
    ): {
        **_FIELD_EFFECT,
        # Raised before the release when the case shows no hold.
        (503, "manual_hold"): "pre-commit",
    },
    # --- review cases --------------------------------------------------------
    ("POST", "/api/v1/review-cases/{review_case_id}/decisions"): _DECISION,
    (
        "POST",
        "/api/v1/review-cases/{review_case_id}/description-advice/selection",
    ): _DECISION,
    (
        "POST",
        "/api/v1/review-cases/{review_case_id}/cover-considerations/retry",
    ): _CASE_CALL,
    (
        "POST",
        "/api/v1/review-cases/{review_case_id}/description-advice/retry",
    ): _CASE_CALL,
    # --- review preparation -----------------------------------------------------
    ("POST", "/api/v1/review-preparation/dismiss"): _PREPARATION,
    ("POST", "/api/v1/review-preparation/first-boot/acknowledge"): {
        (503, "persistence"): "post-commit",
    },
    ("POST", "/api/v1/review-preparation/pause"): _PREPARATION,
    ("POST", "/api/v1/review-preparation/resume"): _PREPARATION,
    ("POST", "/api/v1/review-preparation/{connection}/check-connection"): {
        **_PREPARATION,
        # The handler's own length check.
        (404, "connection"): "pre-commit",
    },
    ("POST", "/api/v1/review-preparation/{connection}/{library_name}/retry"): {
        **_PREPARATION,
        (409, "retry"): "pre-commit",
    },
    ("POST", "/api/v1/review-preparation/{connection}/{library_name}/scan"): (
        _PREPARATION
    ),
}

# The one refusal the browser has dedicated copy for after a commit: a
# same-key decision retry confirms rather than repeats (ADR-0091).
POST_COMMIT_REFUSAL_EXCEPTIONS = frozenset(
    {
        (
            ("POST", "/api/v1/review-cases/{review_case_id}/decisions"),
            (409, "decision_written"),
        )
    }
)


@pytest.fixture(scope="module")
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


def unsafe_routes(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1/")
        and route.path != "/api/v1/session/login"
        for method in (route.methods or set()) & _UNSAFE
    }


def classified(route: tuple[str, str]) -> Row:
    return {**COMMON, **INVENTORY[route]}


def post_commit_refusals(
    inventory: dict[tuple[str, str], Row],
) -> list[tuple[tuple[str, str], tuple[int, str]]]:
    return [
        (route, answer)
        for route, row in inventory.items()
        for answer, when in {**COMMON, **row}.items()
        if answer[0] < 500
        and when == "post-commit"
        and (route, answer) not in POST_COMMIT_REFUSAL_EXCEPTIONS
    ]


def test_every_unsafe_route_is_classified_and_every_row_is_a_route(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    routes = unsafe_routes(cast("FastAPI", web.client.app))

    assert sorted(routes - set(INVENTORY)) == [], "classify the new unsafe route"
    assert sorted(set(INVENTORY) - routes) == [], "delete the removed route's row"


def test_no_refusal_follows_a_committed_write() -> None:
    assert post_commit_refusals(INVENTORY) == []


def test_allow_listed_server_codes_stay_pre_call_on_every_route() -> None:
    # `PRE_CALL_SERVER_CODES` in `frontend/src/api/http.ts`: the browser reads
    # these as refusals, so no route may answer them after its handler ran.
    for route in INVENTORY:
        assert classified(route)[(503, "react_assets_unavailable")] == "pre-call"
