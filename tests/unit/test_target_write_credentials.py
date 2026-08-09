from __future__ import annotations

import unittest

import httpx

from mechanical_librarian.infrastructure.security.credentials import _TargetAuth


class TargetWriteCredentialTests(unittest.TestCase):
    def test_authorizes_only_supported_field_effect_paths(self) -> None:
        auth = _TargetAuth("http://127.0.0.1:8080", "synthetic-token")
        permitted = (
            ("GET", "/api/me"),
            ("GET", "/api/items/item/cover"),
            ("PATCH", "/api/items/item/media"),
            ("POST", "/api/items/item/cover"),
        )
        for method, path in permitted:
            request = httpx.Request(method, "http://127.0.0.1:8080" + path)
            self.assertEqual(next(auth.auth_flow(request)).headers["Authorization"], "Bearer synthetic-token")
        refused = httpx.Request("POST", "http://127.0.0.1:8080/api/items/item/media")
        self.assertNotIn("Authorization", next(auth.auth_flow(refused)).headers)
