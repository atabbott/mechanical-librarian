from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.domain.hardcover_identity import (
    PrivateIdentityQuery,
    PrivateQueryError,
    validate_private_query,
)
from mechanical_librarian.infrastructure.security.private_query import (
    read_private_query,
)


class PrivateIdentityQueryTests(unittest.TestCase):
    def test_isbn_checksums_canonicalization_and_title_boundaries(self) -> None:
        isbn13 = validate_private_query(
            {"isbn": "9780000000019", "title": " Synthetic\u2003Title "}
        )
        isbn10 = validate_private_query(
            {"isbn": "0306406152", "title": "Synthetic Title"}
        )
        isbn10_x = validate_private_query(
            {"isbn": "080442957X", "title": "Synthetic Title"}
        )
        self.assertEqual(isbn13.canonical_isbn, "9780000000019")
        self.assertEqual(isbn10.canonical_isbn, "9780306406157")
        self.assertEqual(isbn10_x.canonical_isbn, "9780804429573")
        self.assertNotIn("Synthetic", repr(isbn13))
        with self.assertRaises(PrivateQueryError):
            PrivateIdentityQuery("9780000000019", "unvalidated")

        valid_titles = ("A" * 512, "é" * 512, "😀" * 512, "A\u200bB")
        for title in valid_titles:
            with self.subTest(title_length=len(title)):
                validate_private_query({"isbn": "9780000000019", "title": title})
        invalid = (
            {"isbn": "9780000000018", "title": "A"},
            {"isbn": "4006381333931", "title": "A"},
            {"isbn": "030640615x", "title": "A"},
            {"isbn": "0306406153", "title": "A"},
            {"isbn": "9780000000019", "title": ""},
            {"isbn": "9780000000019", "title": "A" * 513},
            {"isbn": "9780000000019", "title": "é" * 1_025},
            {"isbn": "9780000000019", "title": "A\u202eB"},
            {"isbn": "9780000000019", "title": "A\u2028B"},
            {"isbn": "9780000000019", "title": "A\x7fB"},
            {"isbn": "9780000000019", "title": "A\tB"},
            {"isbn": "9780000000019", "title": "\ufdd0"},
            {"isbn": "9780000000019", "title": "   "},
            {"isbn": "9780000000019", "title": "A", "extra": True},
        )
        for source in invalid:
            with self.subTest(source_keys=tuple(source)):
                with self.assertRaises(PrivateQueryError):
                    validate_private_query(source)

    def test_owner_only_descriptor_reader_rejects_unsafe_files_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            query = root / "query.json"
            query.write_text(
                json.dumps({"isbn": "9780000000019", "title": "Private Title"}),
                encoding="utf-8",
            )
            query.chmod(0o600)
            self.assertEqual(read_private_query(query).canonical_isbn, "9780000000019")

            base = b'{"isbn":"9780000000019","title":"A"}'
            for size in (4_095, 4_096):
                with self.subTest(exact_file_size=size):
                    query.write_bytes(base + b" " * (size - len(base)))
                    query.chmod(0o600)
                    self.assertEqual(
                        read_private_query(query).canonical_isbn, "9780000000019"
                    )
            query.write_bytes(base + b" " * (4_097 - len(base)))
            query.chmod(0o600)
            with self.assertRaises(PrivateQueryError):
                read_private_query(query)

            cases = (
                b"",
                b"\xff",
                b'{"isbn":"9780000000019"}',
                b'{"title":"A"}',
                b'{"isbn":"9780000000019","isbn":"9780000000019","title":"A"}',
                b'{"isbn":"9780000000019","title":"A","extra":true}',
                b'\xef\xbb\xbf{"isbn":"9780000000019","title":"A"}',
            )
            for payload in cases:
                with self.subTest(size=len(payload)):
                    query.write_bytes(payload)
                    query.chmod(0o600)
                    with self.assertRaises(PrivateQueryError):
                        read_private_query(query)

            query.write_text('{"isbn":"9780000000019","title":"A"}')
            query.chmod(0o640)
            with self.assertRaises(PrivateQueryError):
                read_private_query(query)
            query.chmod(0o600)
            hardlink = root / "hardlink.json"
            os.link(query, hardlink)
            with self.assertRaises(PrivateQueryError):
                read_private_query(query)
            hardlink.unlink()
            symlink = root / "symlink.json"
            symlink.symlink_to(query)
            with self.assertRaises(PrivateQueryError):
                read_private_query(symlink)

            fifo = root / "query.fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(PrivateQueryError):
                read_private_query(fifo)

            with patch(
                "mechanical_librarian.infrastructure.security.private_query.os.geteuid",
                return_value=os.geteuid() + 1,
            ):
                with self.assertRaises(PrivateQueryError):
                    read_private_query(query)

    def test_reader_performs_one_bounded_read_and_detects_every_identity_race(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            query = Path(directory) / "query.json"
            query.write_text('{"isbn":"9780000000019","title":"A"}')
            query.chmod(0o600)
            exact_size = query.stat().st_size
            real_read = os.read
            with patch(
                "mechanical_librarian.infrastructure.security.private_query.os.read",
                wraps=real_read,
            ) as bounded_read:
                read_private_query(query)
            bounded_read.assert_called_once()
            self.assertEqual(bounded_read.call_args.args[1], exact_size + 1)

            real_fstat = os.fstat
            for field, delta in (
                (stat.ST_DEV, 1),
                (stat.ST_INO, 1),
                (stat.ST_MODE, stat.S_IWGRP),
                (stat.ST_UID, 1),
                (stat.ST_NLINK, 1),
                (stat.ST_SIZE, 1),
            ):
                with self.subTest(raced_field=field):
                    calls = 0

                    def changed_identity(descriptor: int) -> os.stat_result:
                        nonlocal calls
                        information = real_fstat(descriptor)
                        calls += 1
                        if calls == 2:
                            values = list(information)
                            values[field] += delta
                            return os.stat_result(values)
                        return information

                    with patch(
                        "mechanical_librarian.infrastructure.security.private_query."
                        "os.fstat",
                        side_effect=changed_identity,
                    ):
                        with self.assertRaises(PrivateQueryError):
                            read_private_query(query)


if __name__ == "__main__":
    unittest.main()
