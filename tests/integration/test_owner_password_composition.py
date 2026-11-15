"""A password change through real store generations, cutovers and restarts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mechanical_librarian.application.review_web import ReviewWebError
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from tests.support.store_runtime import StoreRuntime, quiet_store_runtime

_CURRENT = "synthetic-owner"
_REPLACEMENT = "replacement-owner-password"


def _refused(call, code: str) -> None:
    with pytest.raises(ReviewWebError) as raised:
        call()
    assert raised.value.code == code


def _next_document(runtime: StoreRuntime) -> tuple[OwnerSettingsDocument, int]:
    view = runtime.control.owner_settings_store().read()
    revision = view.document.revision
    document = OwnerSettingsDocument.model_validate(
        {**view.document.model_dump(), "revision": revision + 1}
    )
    return document, revision


def _assert_only_the_replacement(application, kept: str, ended: tuple[str, ...]):
    assert application.authenticate(kept) == "owner"
    for token in ended:
        _refused(lambda token=token: application.authenticate(token), "unauthorized")
    application.login(_REPLACEMENT, "client:after")
    _refused(lambda: application.login(_CURRENT, "client:after"), "invalid_credentials")


@pytest.mark.parametrize("order", ("change_inside_cutover", "change_then_save"))
def test_settings_cutover_never_restores_a_replaced_password(order: str) -> None:
    with (
        tempfile.TemporaryDirectory() as temporary,
        quiet_store_runtime(Path(temporary), 1) as runtime,
    ):
        first = runtime.start()
        assert runtime.activation is not None and runtime.dispatcher is not None
        changer = first.login(_CURRENT, "client:changer")
        other = first.login(_CURRENT, "client:other")
        document, revision = _next_document(runtime)
        changed: list[object] = []

        def change() -> None:
            changed.append(
                first.change_owner_password(
                    changer.session_token,
                    changer.csrf_token,
                    _CURRENT,
                    _REPLACEMENT,
                    _REPLACEMENT,
                    "client:changer",
                )
            )

        def commit() -> object:
            # The candidate generation was composed from a snapshot that
            # still held the earlier verifier.
            if order == "change_inside_cutover":
                change()
            return runtime.control.owner_settings_store().write(document, revision, ())

        if order == "change_then_save":
            change()
        runtime.activation.activate(document, revision, (), commit)

        latest = runtime.applications[-1]
        assert latest is not first
        replacement = changed[0]
        _assert_only_the_replacement(
            latest,
            replacement.session_token,
            (changer.session_token, other.session_token),
        )


def test_restart_keeps_the_new_password() -> None:
    with (
        tempfile.TemporaryDirectory() as temporary,
        quiet_store_runtime(Path(temporary), 1) as runtime,
    ):
        first = runtime.start()
        changer = first.login(_CURRENT, "client:changer")
        other = first.login(_CURRENT, "client:other")
        replacement = first.change_owner_password(
            changer.session_token,
            changer.csrf_token,
            _CURRENT,
            _REPLACEMENT,
            _REPLACEMENT,
            "client:changer",
        )
        runtime.stop()

        restarted = runtime.start()

        assert restarted is not first
        _assert_only_the_replacement(
            restarted,
            replacement.session_token,
            (changer.session_token, other.session_token),
        )
