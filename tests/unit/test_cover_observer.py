import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.application.metadata_report import CoverSourceChangedError, CoverTimeoutError, TargetCover, TargetSnapshot
from mechanical_librarian.bootstrap.composition import _CoverEvidenceObserver
from mechanical_librarian.domain.cover_evidence import CoverSourceKind, CoverSourceState


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
