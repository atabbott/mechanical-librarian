from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap import composition


def test_canary_target_uses_selected_validated_connection_and_artifacts() -> None:
    configuration = ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "http://127.0.0.1:8080", "/target"),),
        (LibraryConfiguration("books", "primary", "library", "book", None, ()),),
        (),
        (),
    )
    artifacts = object()
    constructed = object()
    with (
        patch.object(
            composition, "CoverArtifactStore", return_value=artifacts
        ) as store,
        patch.object(
            composition, "AudiobookshelfFieldEffectTarget", return_value=constructed
        ) as target,
    ):
        result = composition.compose_audiobookshelf_field_effect_target(
            configuration, Path("/artifacts"), "primary", "books"
        )
    assert result is constructed
    store.assert_called_once_with(
        Path("/artifacts"), create=False, expected_identity=None
    )
    assert target.call_args.kwargs["endpoint"] == "http://127.0.0.1:8080"
    assert target.call_args.kwargs["library_id"] == "library"
    assert target.call_args.kwargs["artifacts"] is artifacts
    assert "GuardedFieldApplication" not in str(target.call_args)


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (TargetConnectionConfiguration("primary", "http://127.0.0.1:8080", "/target"),),
        (LibraryConfiguration("books", "primary", "library", "book", None, ()),),
        (),
        (),
    )


def test_canary_capabilities_use_the_selected_validated_connection() -> None:
    """The capability observer's twin: same selection, no artifact authority.

    `compose_audiobookshelf_field_effect_target` above is proved and this was
    not, though both are composed from the same selection by the same caller.
    Nothing but a real container run reached it, so the asymmetry was an
    accident of which sibling a test happened to call (#773).
    """

    configuration = _configuration()
    constructed = object()
    with patch.object(
        composition,
        "AudiobookshelfFieldEffectCapabilities",
        return_value=constructed,
    ) as capabilities:
        result = composition.compose_audiobookshelf_field_effect_capabilities(
            configuration, "primary", "books"
        )

    assert result is constructed
    arguments = capabilities.call_args.kwargs
    assert arguments["endpoint"] == "http://127.0.0.1:8080"
    assert arguments["library_id"] == "library"
    assert (
        arguments["maximum_response_bytes"]
        == configuration.limits.target_response_bytes
    )


def test_canary_capabilities_read_the_secret_file_only_without_credentials() -> None:
    """Which credential the client is built from is the whole point of the seam.

    An injected credential map is the qualification path; its absence is the
    installed path, which reads the operator's secret file. A mutation that
    swaps the two would hand a qualification token to an installed run, or read
    an operator secret during qualification, and no test observed either.
    """

    configuration = _configuration()
    with (
        patch.object(composition, "AudiobookshelfFieldEffectCapabilities") as built,
        patch.object(composition, "target_client", return_value="from-file") as file,
        patch.object(
            composition, "target_client_token", return_value="from-token"
        ) as token,
    ):
        composition.compose_audiobookshelf_field_effect_capabilities(
            configuration, "primary", "books"
        )
        assert "from-file" == built.call_args.kwargs["client_factory"]()

        composition.compose_audiobookshelf_field_effect_capabilities(
            configuration,
            "primary",
            "books",
            credentials={"target:primary": "synthetic-token"},
        )
        assert "from-token" == built.call_args.kwargs["client_factory"]()

    file.assert_called_once_with(
        "http://127.0.0.1:8080",
        "/target",
        configuration.limits.timeout_seconds,
        plain_http=False,
    )
    token.assert_called_once_with(
        "http://127.0.0.1:8080",
        "synthetic-token",
        configuration.limits.timeout_seconds,
        plain_http=False,
    )
