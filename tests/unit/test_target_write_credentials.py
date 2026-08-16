from __future__ import annotations

import httpx
import pytest

from mechanical_librarian.infrastructure.security.credentials import _TargetAuth


@pytest.mark.parametrize(
    ("method", "path", "authorization"),
    (
        ("GET", "/api/me", "Bearer synthetic-token"),
        ("GET", "/api/items/item/cover", "Bearer synthetic-token"),
        ("PATCH", "/api/items/item/media", "Bearer synthetic-token"),
        ("POST", "/api/items/item/cover", "Bearer synthetic-token"),
        ("POST", "/api/items/item/media", None),
    ),
    ids=(
        "get_me",
        "get_cover",
        "patch_media",
        "post_cover",
        "post_media_refused",
    ),
)
def test_authorizes_only_supported_field_effect_paths(
    method: str, path: str, authorization: str | None
) -> None:
    auth = _TargetAuth("http://127.0.0.1:8080", "synthetic-token")
    request = httpx.Request(method, "http://127.0.0.1:8080" + path)
    assert next(auth.auth_flow(request)).headers.get("Authorization") == authorization
