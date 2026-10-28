from __future__ import annotations

import pytest

from mechanical_librarian.application.setup_connection import (
    SetupConnectionError,
    SetupConnectionOperation,
    SetupConnectionVerification,
)
from mechanical_librarian.domain.setup_state import SetupStateDocument

_BOOTSTRAP = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash"


class _Failure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


class _Repository:
    def __init__(self) -> None:
        self.document = SetupStateDocument(
            schema_version="setup-state-v4",
            revision=2,
            bootstrap_verifier_digest="0" * 64,
            completed_steps=["claim"],
            claimed=True,
        )
        self.writes: list[
            tuple[SetupStateDocument, int, dict[str, str | None] | None]
        ] = []

    def read(self, _bootstrap_verifier: str) -> SetupStateDocument:
        return self.document

    def write(
        self,
        document: SetupStateDocument,
        expected_revision: int,
        _bootstrap_verifier: str,
        secret_values: dict[str, str | None] | None = None,
    ) -> SetupStateDocument:
        self.writes.append((document, expected_revision, secret_values))
        self.document = document
        return document


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("authentication", "unauthorized"),
        ("authorization", "unauthorized"),
        ("name_resolution", "name_resolution"),
        ("refused", "refused"),
        ("tls", "tls"),
        ("timeout", "timeout"),
        ("unavailable", "unavailable"),
        ("malformed", "malformed"),
        ("oversize", "oversize"),
        ("redirect", "redirect"),
        ("secret-value", "unavailable"),
    ),
)
def test_failure_codes_are_closed_and_value_free(source: str, expected: str) -> None:
    verifier = SetupConnectionVerification(
        lambda _endpoint, _key: (_ for _ in ()).throw(_Failure(source))
    )
    with pytest.raises(SetupConnectionError) as error:
        verifier.verify("primary", "https://abs.invalid", "secret-value")
    assert error.value.code == expected
    assert "secret-value" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://127.0.0.1:13378/audiobookshelf",
        "https://owner:synthetic-userinfo@abs.invalid:13378",
        "https://abs.invalid:13378/?library=one",
        "https://abs.invalid:13378/#fragment",
    ),
)
def test_an_address_beyond_scheme_host_and_port_is_refused_before_probing(
    endpoint: str,
) -> None:
    probes: list[str] = []

    def probe(endpoint: str, key: str) -> None:
        probes.append(endpoint)

    with pytest.raises(SetupConnectionError) as error:
        SetupConnectionVerification(probe).verify("primary", endpoint, "secret-value")
    assert error.value.code == "invalid_endpoint"
    assert probes == []
    assert "synthetic-userinfo" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint", ("http://127.0.0.1:13378", "https://abs.invalid:13378/")
)
def test_an_origin_with_or_without_a_trailing_slash_is_probed(endpoint: str) -> None:
    probes: list[str] = []
    SetupConnectionVerification(lambda address, _key: probes.append(address)).verify(
        "primary", endpoint, "secret-value"
    )
    assert probes == [endpoint]


def test_success_returns_connection_and_write_only_delta() -> None:
    connection, secrets = SetupConnectionVerification(
        lambda _endpoint, _key: None
    ).verify("primary", "https://abs.invalid", "secret-value")
    assert connection.endpoint == "https://abs.invalid"
    assert secrets == {"connection.primary.key": "secret-value"}


def test_operation_records_connection_only_after_successful_probe() -> None:
    repository = _Repository()
    operation = SetupConnectionOperation(
        SetupConnectionVerification(lambda _endpoint, _key: None),
        repository,
        _BOOTSTRAP,
    )

    document = operation.connect(2, "primary", "https://abs.invalid", "secret-value")

    assert document.next_step() == "providers"
    assert repository.writes == [
        (document, 2, {"connection.primary.key": "secret-value"})
    ]


def test_operation_probe_failure_records_nothing() -> None:
    repository = _Repository()
    operation = SetupConnectionOperation(
        SetupConnectionVerification(
            lambda _endpoint, _key: (_ for _ in ()).throw(_Failure("timeout"))
        ),
        repository,
        _BOOTSTRAP,
    )

    with pytest.raises(SetupConnectionError) as error:
        operation.connect(2, "primary", "https://abs.invalid", "secret-value")

    assert error.value.code == "timeout"
    assert repository.writes == []
    assert repository.document.next_step() == "connect"


def test_operation_stale_request_does_not_probe_or_write() -> None:
    repository = _Repository()
    probes: list[tuple[str, str]] = []
    operation = SetupConnectionOperation(
        SetupConnectionVerification(
            lambda endpoint, key: probes.append((endpoint, key))
        ),
        repository,
        _BOOTSTRAP,
    )

    with pytest.raises(SetupConnectionError) as error:
        operation.connect(1, "primary", "https://abs.invalid", "secret-value")

    assert error.value.code == "stale"
    assert probes == []
    assert repository.writes == []
