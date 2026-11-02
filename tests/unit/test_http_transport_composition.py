"""Exercise the selected credential-bearing callbacks, not their equality."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    MediaMountConfiguration,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.infrastructure.security.credentials import target_client_token
from tests.support.review_cases import _report


@pytest.mark.parametrize("authority", ["file", "store"])
def test_report_currentness_effect_and_capability_callbacks_use_http(
    tmp_path: Path, authority: str
) -> None:
    secret = tmp_path / "target.secret"
    secret.write_text("synthetic-token")
    secret.chmod(0o600)
    report = _report()
    connection = TargetConnectionConfiguration(
        report.target.connection, "http://audiobookshelf:13378", str(secret)
    )
    library = LibraryConfiguration(
        report.target.library, connection.name, "books", "book", "media", ()
    )
    mount = MediaMountConfiguration("media", "/media", "/books", True)
    configuration = ApplicationConfiguration(
        (connection,),
        (library,),
        (mount,),
        (),
        review=ReviewWebConfiguration("http://review.test", "/synthetic/owner.hash"),
    )
    credentials = (
        {"target:" + connection.name: "synthetic-token"}
        if authority == "store"
        else None
    )
    requests: list[httpx.Request] = []
    reached: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"libraries": []})

    def client(endpoint: str, token: str, timeout: int) -> httpx.Client:
        value = target_client_token(endpoint, token, timeout)
        value._transport = httpx.MockTransport(respond)
        return value

    def consume(name: str):
        def construct(**kwargs):
            with (
                kwargs["client_factory"](connection.name)
                if name == "catalogue"
                else kwargs["client_factory"]()
            ) as value:
                assert value.get("/api/libraries").status_code == 200
            reached.append(name)
            return Mock()

        return construct

    def recovery(**kwargs):
        target, inspector, audio_inspector = kwargs["observers"](
            connection, library, mount
        )
        assert target is not None
        assert isinstance(inspector, composition.IsolatedEpubInspector)
        assert isinstance(audio_inspector, composition.IsolatedAudioInspector)
        activation = Mock()
        activation.drain_outbox.return_value = ()
        return activation

    with (
        patch.object(composition, "target_client_token", side_effect=client),
        patch.object(composition, "CoverArtifactStore"),
        patch.object(
            composition, "AudiobookshelfItemReader", side_effect=consume("item")
        ),
        patch.object(
            composition,
            "AudiobookshelfFieldEffectTarget",
            side_effect=consume("effect"),
        ),
        patch.object(
            composition,
            "AudiobookshelfFieldEffectCapabilities",
            side_effect=consume("capability"),
        ),
        patch.object(composition, "GenerateMetadataReport") as generator,
        patch.object(
            composition,
            "AudiobookshelfCatalogueReader",
            side_effect=consume("catalogue"),
        ),
        patch.object(composition, "ProviderRecoveryActivation", side_effect=recovery),
        patch.object(composition, "ReviewPipelineStages"),
        patch.object(
            composition, "load_configuration_file", return_value=configuration
        ),
        patch.object(composition, "open_catalogue"),
        patch.object(composition, "RefreshCurrentCatalogue") as catalogue,
        patch.object(composition, "require_report_current", return_value=True),
    ):
        # File construction delegates to credentials.target_client_token, not
        # composition's imported symbol; bind that same real factory too.
        with patch(
            "mechanical_librarian.infrastructure.security.credentials.target_client_token",
            side_effect=client,
        ):
            generator.return_value.run.return_value = "report"
            assert (
                composition.run_metadata_report_loaded(
                    configuration,
                    tmp_path / "db",
                    tmp_path / "artifacts",
                    connection.name,
                    library.name,
                    "item",
                    0,
                    credentials=credentials,
                )
                == "report"
            )
            assert composition._require_report_current_loaded(
                configuration, Mock(), report, credentials
            )
            composition.compose_audiobookshelf_field_effect_target(
                configuration,
                tmp_path / "artifacts",
                connection.name,
                library.name,
                credentials=credentials,
            )
            composition.compose_audiobookshelf_field_effect_capabilities(
                configuration, connection.name, library.name, credentials=credentials
            )
            supervisor = composition.create_review_pipeline(
                configuration,
                tmp_path / "db",
                tmp_path / "catalogue",
                tmp_path / "artifacts",
                Mock(),
                provider_recovery=Mock(),
                credentials=credentials,
            )
            supervisor.stop()
            if authority == "file":
                catalogue.return_value.refresh.return_value = 1
                assert (
                    composition.run_catalogue(
                        tmp_path / "config",
                        tmp_path / "catalogue",
                        "refresh",
                        None,
                        None,
                        None,
                        None,
                    )
                    == '{"generation":1}'
                )
    assert reached == ["item", "item", "effect", "capability", "catalogue", "item"] + (
        ["catalogue"] if authority == "file" else []
    )
    assert len(requests) == len(reached)
    assert all(
        str(request.url) == "http://audiobookshelf:13378/api/libraries"
        for request in requests
    )
    assert all(
        request.headers["authorization"] == "Bearer synthetic-token"
        for request in requests
    )


def test_pipeline_target_clients_read_the_secret_file_only_without_credentials(
    tmp_path: Path,
) -> None:
    """The catalogue reader and every recovery item reader the pipeline builds
    take the injected credential when the runtime supplies one, and the
    operator's secret file only when it does not."""
    connection = TargetConnectionConfiguration(
        "primary", "http://audiobookshelf:13378", "/target"
    )
    library = LibraryConfiguration("books", "primary", "books", "book", "media", ())
    mount = MediaMountConfiguration("media", "/media", "/books", True)
    configuration = ApplicationConfiguration(
        (connection,),
        (library,),
        (mount,),
        (),
        review=ReviewWebConfiguration("http://review.test", "/synthetic/owner.hash"),
    )
    built: dict[str, list[object]] = {"catalogue": [], "item": []}

    def recovery(**kwargs):
        kwargs["observers"](connection, library, mount)
        activation = Mock()
        activation.drain_outbox.return_value = ()
        return activation

    with (
        patch.object(composition, "target_client", return_value="from-file") as file,
        patch.object(
            composition, "target_client_token", return_value="from-token"
        ) as token,
        patch.object(composition, "CoverArtifactStore"),
        patch.object(
            composition,
            "AudiobookshelfCatalogueReader",
            side_effect=lambda **kwargs: (
                built["catalogue"].append(kwargs["client_factory"]("primary")) or Mock()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(
            composition,
            "AudiobookshelfItemReader",
            side_effect=lambda **kwargs: (
                built["item"].append(kwargs["client_factory"]()) or Mock()  # type: ignore[func-returns-value]  # reason: the `append(...) or value` recorder idiom is deliberate
            ),
        ),
        patch.object(composition, "ProviderRecoveryActivation", side_effect=recovery),
        patch.object(composition, "ReviewPipelineStages"),
    ):
        for credentials in (None, {"target:primary": "synthetic-token"}):
            composition.create_review_pipeline(
                configuration,
                tmp_path / "db",
                tmp_path / "catalogue",
                tmp_path / "artifacts",
                Mock(),
                provider_recovery=Mock(),
                credentials=credentials,
            ).stop()

    assert {
        "catalogue": ["from-file", "from-token"],
        "item": ["from-file", "from-token"],
    } == built
    assert 2 == file.call_count
    assert all(
        call.args == ("http://audiobookshelf:13378", "/target", 30)
        for call in file.call_args_list
    )
    assert 2 == token.call_count
    assert all(
        call.args == ("http://audiobookshelf:13378", "synthetic-token", 30)
        for call in token.call_args_list
    )
