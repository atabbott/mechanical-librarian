"""Every unsafe review route is bounded and gated before it reads a body.

FastAPI reads and validates a route's body before the route's session
dependency runs, so a route left off a list of limits would take a body of
any size from someone who has not signed in. These tests walk the routes the
app actually registers, so a new route is covered the moment it is added.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute

from mechanical_librarian.interfaces.web.app import (
    _BODY_PATH_LIMITS,
    _BODY_PATTERN_LIMITS,
    DEFAULT_BODY_LIMIT_BYTES,
    BodyLimitMiddleware,
)
from tests.support.review_web_doubles import (
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)

_SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
_SIGN_IN = "/api/v1/session/login"


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


def _limits() -> BodyLimitMiddleware:
    async def unused(*_: object) -> None:
        raise AssertionError("the limit table is read, never called")

    return BodyLimitMiddleware(
        unused, path_limits=_BODY_PATH_LIMITS, pattern_limits=_BODY_PATTERN_LIMITS
    )


def _api_routes(web: ReviewWebContext[FakeReviewRepository]) -> list[APIRoute]:
    return [route for route in web.client.app.routes if isinstance(route, APIRoute)]


def _unsafe(
    web: ReviewWebContext[FakeReviewRepository],
) -> list[tuple[str, str, APIRoute]]:
    return sorted(
        (
            (method, route.path, route)
            for route in _api_routes(web)
            for method in route.methods - _SAFE
        ),
        key=lambda row: (row[1], row[0]),
    )


def _concrete(template: str) -> str:
    """A request path for a route template; the limit is read before routing."""
    return re.sub(r"\{[^}]+\}", "x", template)


def test_every_listed_limit_raises_or_keeps_the_default() -> None:
    assert DEFAULT_BODY_LIMIT_BYTES == 2_048
    limits = [*_BODY_PATH_LIMITS.values(), *(n for _, n in _BODY_PATTERN_LIMITS)]
    assert all(limit >= DEFAULT_BODY_LIMIT_BYTES for limit in limits)
    assert _limits().limit_for("/api/v1/a-route-nobody-listed") == 2_048
    assert _limits().limit_for("/api/v1/review-cases/x/decisions") == 8_192


def test_an_unlisted_route_is_bounded_at_the_default(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE", "PROPFIND"):
        response = web.client.request(
            method,
            "/api/v1/a-route-nobody-listed",
            headers={**web.origin, "Content-Type": "application/json"},
            content=b"x" * (DEFAULT_BODY_LIMIT_BYTES + 1),
        )
        assert response.status_code == 413, method
        assert response.json()["error"]["code"] == "body_too_large"


def test_every_unsafe_route_refuses_a_body_over_its_limit_before_sign_in(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    limits = _limits()
    unsafe = _unsafe(web)
    assert len(unsafe) > 40
    for method, template, _route in unsafe:
        path = _concrete(template)
        oversized = b"x" * (limits.limit_for(path) + 1)
        headers = {**web.origin, "Content-Type": "application/json"}
        declared = web.client.request(method, path, headers=headers, content=oversized)
        assert declared.status_code == 413, (method, template, declared.status_code)
        assert declared.json()["error"]["code"] == "body_too_large"
        half = len(oversized) // 2
        streamed = web.client.request(
            method,
            path,
            headers=headers,
            content=(part for part in (oversized[:half], oversized[half:])),
        )
        assert streamed.status_code == 413, (method, template, streamed.status_code)


def test_no_safe_route_reads_a_body(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    # The limit passes GET, HEAD and OPTIONS through unread; this keeps that
    # sound by refusing any route that would parse a body on those methods.
    readers = [
        (sorted(route.methods), route.path)
        for route in _api_routes(web)
        if route.body_field is not None and route.methods & _SAFE
    ]
    assert readers == []


def test_every_route_with_a_body_declares_its_refusal(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    undeclared = [
        route.path
        for route in _api_routes(web)
        if route.body_field is not None and 413 not in route.responses
    ]
    assert undeclared == []


def test_every_unsafe_api_route_checks_session_and_build_before_its_body(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The shared dependency is the one place the build identity is checked."""
    ungated = [
        (method, template)
        for method, template, route in _unsafe(web)
        if template.startswith("/api/v1/")
        and template != _SIGN_IN
        and not any(
            getattr(dependency.call, "__name__", "") == "authenticated_session_cookie"
            for dependency in route.dependant.dependencies
        )
    ]
    assert ungated == []


def test_the_largest_valid_consent_and_suspend_bodies_are_admitted(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The newly bounded routes still take the largest body an owner sends."""
    destinations = ["title", "authors", "narrators", "language", "publisher"]
    consent = json.dumps(
        {
            "grants": [
                {"library": "l" * 128, "destinations": destinations * 4}
                for _ in range(64)
            ]
        }
    ).encode()
    suspend = json.dumps({"category": "authors", "reason": "\u0001" * 256}).encode()
    for path, body in (
        ("/api/v1/activity/consent", consent),
        ("/api/v1/metadata-policy/automatic-safety/suspend", suspend),
    ):
        assert len(body) <= _limits().limit_for(path)
        response = web.client.post(
            path,
            headers={**web.origin, "Content-Type": "application/json"},
            content=body,
        )
        assert response.status_code == 401, (path, response.status_code)
