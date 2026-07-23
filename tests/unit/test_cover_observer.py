import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mechanical_librarian.adapters.cover_artifacts import CoverDecodeTimeout
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.providers.remote_cover import CoverDeadline, RemoteCover
from mechanical_librarian.application.metadata_report import CoverReadError, CoverSourceChangedError, CoverTimeoutError, TargetCover, TargetSnapshot
from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
from mechanical_librarian.domain.cover_evidence import (
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (10, 20), "red").save(output, "JPEG")
    return output.getvalue()


class _Target:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def read(self, item: str) -> TargetSnapshot:
        raise AssertionError("not used")

    def read_cover(self, cover: TargetCover, *, deadline: float | None = None) -> bytes | None:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


class CoverObserverTests(unittest.TestCase):
    def test_abs_result_is_recorded_without_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = _Target(b"unexpected")
            value = _CoverEvidenceObserver(target, CoverArtifactStore(Path(directory))).observe(
                TargetSnapshot("ebook", "Title", (), None), (), ()
            )
        self.assertEqual((CoverSourceKind.ABS, CoverSourceState.ABSENT), (value[0].kind, value[0].state))
        self.assertEqual(0, target.calls)

    def test_retrieves_and_stages_one_declared_abs_cover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CoverArtifactStore(Path(directory))
            target = _Target(_jpeg())
            value = _CoverEvidenceObserver(target, store).observe(
                TargetSnapshot("ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")), (), ()
            )
            self.assertEqual(CoverSourceState.RETRIEVED, value[0].state)
            self.assertEqual(1, target.calls)
            self.assertEqual(b"\x89PNG\r\n\x1a\n", store.preview(value[0].artifact)[:8])

    def test_timeout_is_an_explicit_attempted_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = _CoverEvidenceObserver(_Target(CoverTimeoutError("elapsed")), CoverArtifactStore(Path(directory))).observe(
                TargetSnapshot("ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")), (), ()
            )
        self.assertEqual((CoverSourceState.TIMEOUT, "attempted"), (value[0].state, value[0].timeout_scope))

    def test_changed_abs_source_is_recorded_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            value = _CoverEvidenceObserver(_Target(CoverSourceChangedError("changed")), CoverArtifactStore(Path(directory))).observe(
                TargetSnapshot("ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")), (), ()
            )
        self.assertEqual(CoverSourceState.SOURCE_CHANGED, value[0].state)

    def test_abs_terminal_read_classifications_are_preserved(self) -> None:
        for state, expected in (
            ("oversized", CoverSourceState.OVERSIZED),
            ("unsupported_format", CoverSourceState.UNSUPPORTED_FORMAT),
        ):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                value = _CoverEvidenceObserver(
                    _Target(CoverReadError(state)),
                    CoverArtifactStore(Path(directory)),
                ).observe(
                    TargetSnapshot("ebook", "Title", (), TargetCover("descriptor", "/cover.jpg")),
                    (),
                    (),
                )
            self.assertEqual(expected, value[0].state)

    def test_abs_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout(self) -> None:
        aggregate = time.monotonic() + 10.0
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "mechanical_librarian.bootstrap.composition.decode_cover",
                side_effect=CoverDecodeTimeout("elapsed"),
            ) as decode,
        ):
            value = _CoverEvidenceObserver(
                _Target(_jpeg()),
                CoverArtifactStore(Path(directory)),
            ).observe(
                TargetSnapshot(
                    "ebook",
                    "Title",
                    (),
                    TargetCover("descriptor", "/cover.jpg"),
                ),
                (),
                (),
                aggregate_deadline=aggregate,
            )
        decode.assert_called_once_with(_jpeg(), deadline=aggregate)
        self.assertEqual(
            (CoverSourceState.TIMEOUT, "attempted"),
            (value[0].state, value[0].timeout_scope),
        )

    def test_remote_decoder_uses_aggregate_deadline_and_maps_expiry_to_timeout(self) -> None:
        aggregate = time.monotonic() + 10.0
        source_id = cover_source_id(CoverSourceKind.HARDCOVER, "candidate")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "mechanical_librarian.bootstrap.composition.fetch_remote_cover",
                return_value=RemoteCover(
                    "https://example.com/cover.jpg",
                    "image/jpeg",
                    _jpeg(),
                ),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.decode_cover",
                side_effect=CoverDecodeTimeout("elapsed"),
            ) as decode,
        ):
            value = _CoverEvidenceObserver(
                _Target(None),
                CoverArtifactStore(Path(directory)),
            )._remote(
                source_id,
                "https://example.com/cover.jpg",
                CoverDeadline(aggregate, aggregate),
            )
        decode.assert_called_once_with(_jpeg(), deadline=aggregate)
        self.assertEqual(
            (CoverSourceState.TIMEOUT, "attempted"),
            (value.state, value.timeout_scope),
        )
