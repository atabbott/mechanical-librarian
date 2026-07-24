from __future__ import annotations

import io
import unittest

from mechanical_librarian.interfaces.cli import main


class BatchCliTests(unittest.TestCase):
    def test_preview_and_inspect_are_deterministic(self) -> None:
        output = io.StringIO()
        code = main(["batch", "preview", "--config", "/private/config", "--catalogue", "/private/catalogue", "--manifest", "/private/manifest", "--select", "primary,books,library,item"], batch_preview_runner=lambda *_: '{"id":"a"}', stdout=output, stderr=io.StringIO())
        self.assertEqual((code, output.getvalue()), (0, '{"id":"a"}\n'))
        output = io.StringIO()
        code = main(["batch", "inspect", "--manifest", "/private/manifest"], batch_inspect_runner=lambda _: '{"entries":1}', stdout=output, stderr=io.StringIO())
        self.assertEqual((code, output.getvalue()), (0, '{"entries":1}\n'))

    def test_invalid_or_missing_batch_authority_fails_safely(self) -> None:
        errors = io.StringIO()
        self.assertEqual(main(["batch", "preview", "--config", "/x", "--catalogue", "/x", "--manifest", "/x", "--all-eligible"], stderr=errors), 1)
        self.assertEqual(errors.getvalue(), "Batch failed safely.\n")
