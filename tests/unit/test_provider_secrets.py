from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

import httpx

from mechanical_librarian.domain.inventory import SecretReference
from mechanical_librarian.domain.security import SecretHandle, SecretScope
from mechanical_librarian.infrastructure.security.provider_secrets import (
    PrivateFileProviderCredentials,
    ProviderSecretUnavailable,
    _HardcoverAuthorizationAuth,
)


class ProviderSecretTests(unittest.TestCase):
    def test_private_file_rules_match_the_existing_bounded_token_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases: list[tuple[str, Path]] = []
            missing = root / "missing"
            cases.append(("missing", missing))
            insecure = root / "insecure"
            insecure.write_text("token", encoding="utf-8")
            insecure.chmod(0o644)
            cases.append(("insecure", insecure))
            unreadable = root / "unreadable"
            unreadable.write_text("token", encoding="utf-8")
            unreadable.chmod(0o000)
            cases.append(("unreadable", unreadable))
            empty = root / "empty"
            empty.write_bytes(b"")
            empty.chmod(0o600)
            cases.append(("empty", empty))
            oversized = root / "oversized"
            oversized.write_bytes(b"x" * 2_049)
            oversized.chmod(0o600)
            cases.append(("oversized", oversized))
            whitespace = root / "whitespace"
            whitespace.write_text("two tokens", encoding="utf-8")
            whitespace.chmod(0o600)
            cases.append(("whitespace", whitespace))
            invalid = root / "invalid"
            invalid.write_bytes(b"\xff")
            invalid.chmod(0o600)
            cases.append(("invalid", invalid))
            directory_entry = root / "directory"
            directory_entry.mkdir()
            cases.append(("directory", directory_entry))
            fifo = root / "fifo"
            os.mkfifo(fifo, 0o600)
            cases.append(("fifo", fifo))
            target = root / "target"
            target.write_text("token", encoding="utf-8")
            target.chmod(0o600)
            symlink = root / "symlink"
            symlink.symlink_to(target)
            cases.append(("symlink", symlink))

            for name, path in cases:
                with self.subTest(name=name):
                    with self.assertRaises(ProviderSecretUnavailable):
                        PrivateFileProviderCredentials().hardcover_client(
                            SecretHandle(
                                SecretReference(str(path)),
                                SecretScope.METADATA_PROVIDER,
                            )
                        )

    def test_only_metadata_provider_scope_can_construct_the_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "token"
            secret.write_text("provider-token", encoding="utf-8")
            secret.chmod(0o600)
            for scope in SecretScope:
                handle = SecretHandle(SecretReference(str(secret)), scope)
                if scope is SecretScope.METADATA_PROVIDER:
                    client = PrivateFileProviderCredentials().hardcover_client(handle)
                    asyncio.run(client.aclose())
                else:
                    with self.assertRaises(ProviderSecretUnavailable):
                        PrivateFileProviderCredentials().hardcover_client(handle)

    def test_hardcover_auth_builds_one_bearer_header_only_for_the_fixed_request(
        self,
    ) -> None:
        auth = _HardcoverAuthorizationAuth("raw-provider-token")

        def authorize(
            *,
            method: str = "POST",
            url: str = "https://93.184.216.34/v1/graphql",
            host: str = "api.hardcover.app",
            sni: str = "api.hardcover.app",
        ) -> httpx.Request:
            request = httpx.Request(
                method,
                url,
                headers={"host": host},
                extensions={"sni_hostname": sni},
            )
            return next(auth.auth_flow(request))

        self.assertEqual(
            authorize().headers["authorization"], "Bearer raw-provider-token"
        )
        near_misses = (
            {"method": "GET"},
            {"url": "http://93.184.216.34/v1/graphql"},
            {"url": "https://93.184.216.34:444/v1/graphql"},
            {"url": "https://93.184.216.34/v1/other"},
            {"url": "https://93.184.216.34/v1/graphql?operation=other"},
            {"url": "https://93.184.216.34/v1/graphql#fragment"},
            {"host": "example.invalid"},
            {"sni": "example.invalid"},
        )
        for values in near_misses:
            with self.subTest(values=values):
                self.assertNotIn("authorization", authorize(**values).headers)

    def test_prefixed_token_is_rejected_by_the_unchanged_shared_reader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "token"
            secret.write_text("Bearer raw-provider-token", encoding="utf-8")
            secret.chmod(0o600)
            with self.assertRaises(ProviderSecretUnavailable):
                PrivateFileProviderCredentials().hardcover_client(
                    SecretHandle(
                        SecretReference(str(secret)), SecretScope.METADATA_PROVIDER
                    )
                )


if __name__ == "__main__":
    unittest.main()
