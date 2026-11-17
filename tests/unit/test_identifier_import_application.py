"""The identifiers file preview held in session memory, and its refusals (#1478).

A preview is held for exactly fifteen minutes and one per session, bound to
the library's catalogue digest; it keeps no row text. The store here is an
in-memory double: the real store is proved in
`test_identifier_import_persistence.py` and the routes in
`test_identifier_import_web.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from mechanical_librarian.application.identifier_import import (
    MAXIMUM_PREVIEW_SESSIONS,
    PREVIEW_TTL_SECONDS,
    IdentifierImportRecord,
    IdentifierImportRefused,
    IdentifierImportService,
    LibraryCandidates,
    preview_digest,
)
from mechanical_librarian.application.review_application import ReviewApplication
from mechanical_librarian.domain.identifier_file import (
    IdentifierCandidate,
    WillLink,
)

CONNECTION, LIBRARY = "primary", "fiction"
TOKEN, OTHER = "session-token-one", "session-token-two"
ISBN13 = "9780306406157"
READ = LibraryCandidates(
    (
        IdentifierCandidate(
            "sentinel-item-1",
            "Sentinel Shelf/Sentinel Book",
            "ebook",
            "Title One",
            ("Author One",),
        ),
        IdentifierCandidate("item-2", "Shelf/Two", "audiobook", "Title Two", ()),
    ),
    "d" * 64,
)
FILE = (
    "item\tidentifier\n"
    f"sentinel-item-1\t{ISBN13}\n"
    "item-2\tB00TEST001\n"
    "sentinel-missing-item\t9780804429573\n"
).encode()


@dataclass
class _Clock:
    now: float = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class _Store:
    """Records an import per request and per preview digest."""

    by_request: dict[str, IdentifierImportRecord] = field(default_factory=dict)
    by_preview: dict[str, IdentifierImportRecord] = field(default_factory=dict)
    status: dict[str, dict[str, object]] = field(default_factory=dict)

    def identifier_candidates(self, connection: str, library: str) -> LibraryCandidates:
        return READ

    def identifier_import_replay(
        self, request_id: str
    ) -> IdentifierImportRecord | None:
        return self.by_request.get(request_id)

    def identifier_import_for_preview(
        self, preview_digest: str
    ) -> IdentifierImportRecord | None:
        return self.by_preview.get(preview_digest)

    def import_identifiers(
        self,
        connection: str,
        library: str,
        *,
        preview_digest: str,
        catalogue_digest: str,
        rows: int,
        counts: Mapping[str, int],
        links: tuple[WillLink, ...],
        actor: str,
        occurred_at: int,
        request_id: str,
        request_sha256: str,
    ) -> tuple[IdentifierImportRecord, bool]:
        record = IdentifierImportRecord(
            "identifier_import_" + "e" * 64,
            connection,
            library,
            len(links),
            request_sha256,
        )
        self.by_request[request_id] = record
        self.by_preview[preview_digest] = record
        return record, False

    def withdraw_identifier_import(
        self, import_id: str, **kwargs: object
    ) -> tuple[IdentifierImportRecord, int, bool]:
        raise IdentifierImportRefused("import_not_found")

    def identifier_import_status(self, import_id: str) -> dict[str, object] | None:
        return self.status.get(import_id)


def _service(
    clock: _Clock | None = None, store: _Store | None = None
) -> tuple[IdentifierImportService, _Store, _Clock]:
    clock = clock or _Clock()
    store = store or _Store()
    service = IdentifierImportService(
        store=store,
        library_configured=lambda connection, library: (
            (connection, library) == (CONNECTION, LIBRARY)
        ),
        clock=clock,
    )
    return service, store, clock


def _preview(service: IdentifierImportService, token: str = TOKEN) -> dict[str, object]:
    return service.preview(token, CONNECTION, LIBRARY, FILE, READ)


def _refused(call) -> str:
    with pytest.raises(IdentifierImportRefused) as refused:
        call()
    return refused.value.code


def test_a_preview_answers_counts_and_rows_and_echoes_no_row_text() -> None:
    service, _, clock = _service()
    answer = _preview(service)
    assert answer["schema_version"] == "identifier-preview-v1"
    assert answer["expires_at"] == int(clock.now) + PREVIEW_TTL_SECONDS
    assert answer["rows"] == 3
    assert answer["counts"] == {
        "will_link": 2,
        "already_linked": 0,
        "no_item": 1,
        "ambiguous": 0,
        "invalid_identifier": 0,
        "wrong_medium": 0,
        "repeated": 0,
        "conflicting": 0,
        "unreadable": 0,
    }
    assert answer["will_link"] == [
        {
            "row": 2,
            "title": "Title One",
            "authors": ["Author One"],
            "identifier_kind": "isbn",
            "identifier": ISBN13,
        },
        {
            "row": 3,
            "title": "Title Two",
            "authors": [],
            "identifier_kind": "asin",
            "identifier": "B00TEST001",
        },
    ]
    # A row is numbered by its line in the file, the header being line 1.
    assert answer["refused"] == [{"row": 4, "reason": "no_item"}]
    preview_id = answer["preview_id"]
    assert isinstance(preview_id, str) and len(preview_id) == 43
    text = json.dumps(answer)
    assert "sentinel" not in text.lower()
    held = service.held(TOKEN, preview_id)
    assert held is not None and preview_id not in repr(held)
    # It keeps each will-link work's item, never a path or a refused row's text.
    assert "Sentinel Shelf" not in repr(held)
    assert "sentinel-missing-item" not in repr(held)


def test_a_preview_is_held_at_fifteen_minutes_and_gone_just_after() -> None:
    service, _, clock = _service()
    preview_id = str(_preview(service)["preview_id"])
    clock.now += PREVIEW_TTL_SECONDS
    assert service.held(TOKEN, preview_id) is not None
    clock.now += 0.001
    assert service.held(TOKEN, preview_id) is None
    assert _refused(lambda: service.open_preview(TOKEN, preview_id)) == (
        "preview_not_found"
    )


def test_a_second_preview_replaces_the_first() -> None:
    service, _, _ = _service()
    first = str(_preview(service)["preview_id"])
    second = str(_preview(service)["preview_id"])
    assert first != second
    assert service.held(TOKEN, first) is None
    assert service.held(TOKEN, second) is not None


def test_another_sessions_preview_is_not_found() -> None:
    service, _, _ = _service()
    preview_id = str(_preview(service)["preview_id"])
    assert service.held(OTHER, preview_id) is None
    assert _refused(lambda: service.open_preview(OTHER, preview_id)) == (
        "preview_not_found"
    )
    assert service.preview_status(OTHER, preview_id)["state"] == "closed"


def test_logout_forgets_the_sessions_preview() -> None:
    service, _, _ = _service()
    preview_id = str(_preview(service)["preview_id"])
    service.forget(TOKEN)
    assert service.held(TOKEN, preview_id) is None


def test_review_logout_forgets_the_sessions_preview() -> None:
    service, _, _ = _service()
    preview_id = str(_preview(service)["preview_id"])
    application = ReviewApplication.__new__(ReviewApplication)
    application._sessions = SimpleNamespace(  # type: ignore[assignment]
        logout=lambda token, csrf: None
    )
    application._owner_search = None
    application._identifier_import = service
    application.logout(TOKEN, "csrf")
    assert service.held(TOKEN, preview_id) is None


def test_few_previews_are_held_at_once() -> None:
    service, _, clock = _service()
    previews = []
    for index in range(MAXIMUM_PREVIEW_SESSIONS + 1):
        clock.now += 1
        previews.append(str(_preview(service, f"session-{index}")["preview_id"]))
    assert service.held("session-0", previews[0]) is None
    assert all(
        service.held(f"session-{index}", previews[index]) is not None
        for index in range(1, MAXIMUM_PREVIEW_SESSIONS + 1)
    )


def test_an_unconfigured_library_is_not_found() -> None:
    service, _, _ = _service()
    service.require_library(CONNECTION, LIBRARY)
    assert _refused(lambda: service.require_library(CONNECTION, "other")) == (
        "library_not_found"
    )


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "file_header"),
        (b"item\tidentifier\n", "file_no_rows"),
        (b"\xff\xfe", "file_encoding"),
        (b"item\tidentifier\nitem-1\x07\tB00TEST001\n", "file_control_character"),
    ],
)
def test_a_file_refusal_holds_nothing(data: bytes, code: str) -> None:
    service, _, _ = _service()
    assert _refused(
        lambda: service.preview(TOKEN, CONNECTION, LIBRARY, data, READ)
    ) == (code)


def test_status_reads_the_store_first_then_the_session() -> None:
    service, store, _ = _service()
    preview_id = str(_preview(service)["preview_id"])
    assert service.preview_status(TOKEN, preview_id) == {
        "state": "open",
        "import_id": None,
        "linked": None,
        "schema_version": "identifier-preview-status-v1",
    }
    held = service.open_preview(TOKEN, preview_id)
    store.import_identifiers(
        CONNECTION,
        LIBRARY,
        preview_digest=held.digest,
        catalogue_digest=held.catalogue_digest,
        rows=held.result.rows,
        counts=held.result.counts,
        links=held.result.will_link,
        actor="owner",
        occurred_at=1,
        request_id="import-request-0001",
        request_sha256="c" * 64,
    )
    # As after a restart: the session no longer holds the preview.
    service.forget(TOKEN)
    assert service.preview_status(TOKEN, preview_id) == {
        "state": "imported",
        "import_id": "identifier_import_" + "e" * 64,
        "linked": 2,
        "schema_version": "identifier-preview-status-v1",
    }
    assert _refused(lambda: service.open_preview(TOKEN, preview_id)) == (
        "preview_imported"
    )


def test_a_replay_with_another_request_is_a_conflict() -> None:
    service, store, _ = _service()
    record = IdentifierImportRecord(
        "identifier_import_" + "e" * 64, "c", "l", 1, "a" * 64
    )
    store.by_request["import-request-0001"] = record
    assert service.replayed("import-request-0001", "a" * 64) == record
    assert service.replayed("import-request-0002", "a" * 64) is None
    assert _refused(lambda: service.replayed("import-request-0001", "b" * 64)) == (
        "idempotency_conflict"
    )


def test_an_imports_status_is_read_live() -> None:
    service, store, _ = _service()
    import_id = "identifier_import_" + "e" * 64
    assert _refused(lambda: service.import_status(import_id)) == "import_not_found"
    store.status[import_id] = {
        "import_id": import_id,
        "linked": 2,
        "still_linked": 1,
        "withdrawn": False,
    }
    assert service.import_status(import_id) == {
        "import_id": import_id,
        "linked": 2,
        "still_linked": 1,
        "withdrawn": False,
        "schema_version": "identifier-import-v1",
    }


def test_the_preview_digest_is_never_the_id() -> None:
    assert preview_digest("abc") != "abc"
    assert len(preview_digest("abc")) == 64


def test_preview_events_carry_counts_and_no_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, _, _ = _service()
    _preview(service)
    _refused(lambda: service.preview(TOKEN, CONNECTION, LIBRARY, b"", READ))
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert lines == [
        {
            "event": "identifier_import",
            "action": "preview",
            "outcome": "previewed",
            "rows": 3,
            "linked": 2,
        },
        {
            "event": "identifier_import",
            "action": "preview",
            "outcome": "file_header",
            "rows": 0,
            "linked": 0,
        },
    ]
