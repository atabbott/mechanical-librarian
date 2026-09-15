"""Bounded Audiobookshelf read probe used only by first-run setup."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx

from mechanical_librarian.domain.setup_state import SetupLibraryCandidate


class AudiobookshelfSetupProbeError(ValueError):
    """Value-free closed setup recovery condition."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AudiobookshelfSetupProbe:
    def __init__(
        self,
        client_factory: Callable[[str, str], httpx.Client],
        *,
        maximum_bytes: int = 1_048_576,
        timeout_seconds: int = 30,
    ) -> None:
        self._client_factory = client_factory
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = timeout_seconds

    def __call__(self, endpoint: str, key: str) -> None:
        self._libraries(endpoint, key)

    def discover(self, endpoint: str, key: str) -> tuple[SetupLibraryCandidate, ...]:
        payload = self._libraries(endpoint, key)
        libraries = cast("list[dict[str, Any]]", payload["libraries"])
        return tuple(
            SetupLibraryCandidate(
                library_id=item["id"],
                name=item["name"],
                media_kind="book" if item.get("mediaType") == "book" else "unsupported",
                service_paths=tuple(
                    folder["fullPath"]
                    for folder in item.get("folders", [])
                    if type(folder) is dict and type(folder.get("fullPath")) is str
                ),
            )
            for item in libraries
        )

    def _libraries(self, endpoint: str, key: str) -> dict[str, object]:
        try:
            with self._client_factory(endpoint, key) as client:
                with client.stream(
                    "GET",
                    "/api/libraries",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    timeout=self._timeout_seconds,
                ) as response:
                    if response.status_code in {401, 403}:
                        raise AudiobookshelfSetupProbeError("unauthorized")
                    if 300 <= response.status_code < 400:
                        raise AudiobookshelfSetupProbeError("redirect")
                    if response.status_code != 200:
                        raise AudiobookshelfSetupProbeError("unavailable")
                    if (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != "application/json"
                    ):
                        raise AudiobookshelfSetupProbeError("malformed")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self._maximum_bytes:
                            raise AudiobookshelfSetupProbeError("oversize")
                        chunks.append(chunk)
            payload = json.loads(b"".join(chunks).decode("utf-8", "strict"))
            if (
                type(payload) is not dict
                or type(payload.get("libraries")) is not list
                or len(payload["libraries"]) > 128
                or any(
                    type(item) is not dict
                    or type(item.get("id")) is not str
                    or not 1 <= len(item["id"]) <= 128
                    or type(item.get("name")) is not str
                    or not 1 <= len(item["name"]) <= 256
                    or "\x00" in item["name"]
                    or type(item.get("mediaType")) is not str
                    or type(item.get("folders", [])) is not list
                    or len(item.get("folders", [])) > 16
                    or any(
                        type(folder) is not dict
                        or type(folder.get("fullPath")) is not str
                        or not 1 <= len(folder["fullPath"]) <= 4096
                        or "\x00" in folder["fullPath"]
                        for folder in item.get("folders", [])
                    )
                    for item in payload["libraries"]
                )
            ):
                raise AudiobookshelfSetupProbeError("malformed")
            return payload
        except AudiobookshelfSetupProbeError:
            raise
        except httpx.TimeoutException:
            raise AudiobookshelfSetupProbeError("timeout") from None
        except httpx.TransportError as error:
            current: BaseException | None = error
            seen: set[int] = set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                if "SSL" in type(current).__name__.upper():
                    raise AudiobookshelfSetupProbeError("tls") from None
                # `httpcore` re-raises without `from`, so the certificate error
                # is the implicit context rather than the declared cause.
                current = current.__cause__ or current.__context__
            raise AudiobookshelfSetupProbeError("unavailable") from None
        except (httpx.HTTPError, OSError):
            raise AudiobookshelfSetupProbeError("unavailable") from None
        except (UnicodeError, ValueError, RecursionError):
            raise AudiobookshelfSetupProbeError("malformed") from None


__all__ = ["AudiobookshelfSetupProbe", "AudiobookshelfSetupProbeError"]
