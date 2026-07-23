import io
import unittest

from mechanical_librarian.interfaces.cli import main


REPORT = "report_" + "a" * 64
ARTIFACT = "cover_" + "b" * 64


class _BinaryOutput:
    def __init__(self, tty: bool = False) -> None:
        self.buffer = io.BytesIO()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty

    def write(self, _: str) -> int:
        raise AssertionError("preview must use raw bytes")


class CoverPreviewCliTests(unittest.TestCase):
    def test_reads_only_exact_report_bound_preview_as_raw_bytes(self) -> None:
        output = _BinaryOutput()
        calls: list[tuple[object, ...]] = []
        result = main(
            ["metadata-report-cover-preview", "--database", "report.sqlite", "--artifact-store", "artifacts", "--report-id", REPORT, "--artifact-id", ARTIFACT, "--preview-variant", "preview-320x480-rgba-png-v1"],
            preview_runner=lambda *args: calls.append(args) or b"\x89PNG\r\n",
            stdout=output,
            stderr=io.StringIO(),
        )
        self.assertEqual(0, result)
        self.assertEqual(b"\x89PNG\r\n", output.buffer.getvalue())
        self.assertEqual(1, len(calls))

    def test_refuses_tty_before_opening_store(self) -> None:
        called = False
        def runner(*_: object) -> bytes:
            nonlocal called
            called = True
            return b"preview"
        result = main(
            ["metadata-report-cover-preview", "--database", "report.sqlite", "--artifact-store", "artifacts", "--report-id", REPORT, "--artifact-id", ARTIFACT, "--preview-variant", "preview-320x480-rgba-png-v1"],
            preview_runner=runner,
            stdout=_BinaryOutput(tty=True),
            stderr=io.StringIO(),
        )
        self.assertEqual(1, result)
        self.assertFalse(called)
