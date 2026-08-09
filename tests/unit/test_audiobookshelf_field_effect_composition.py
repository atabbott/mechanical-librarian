from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap import composition


class AudiobookshelfFieldEffectCompositionTests(unittest.TestCase):
    def test_canary_target_uses_selected_validated_connection_and_artifacts(self) -> None:
        configuration = ApplicationConfiguration(
            (TargetConnectionConfiguration("primary", "http://127.0.0.1:8080", "/target"),),
            (LibraryConfiguration("books", "primary", "library", "book", None, ()),),
            (), (),
        )
        artifacts = object()
        constructed = object()
        with patch.object(composition, "CoverArtifactStore", return_value=artifacts) as store, patch.object(
            composition, "AudiobookshelfFieldEffectTarget", return_value=constructed
        ) as target:
            result = composition.compose_audiobookshelf_field_effect_target(
                configuration, Path("/artifacts"), "primary", "books"
            )
        self.assertIs(result, constructed)
        store.assert_called_once_with(Path("/artifacts"), create=False, expected_identity=None)
        self.assertEqual(target.call_args.kwargs["endpoint"], "http://127.0.0.1:8080")
        self.assertEqual(target.call_args.kwargs["library_id"], "library")
        self.assertIs(target.call_args.kwargs["artifacts"], artifacts)
        self.assertNotIn("GuardedFieldApplication", str(target.call_args))
