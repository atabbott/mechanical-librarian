from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.providers.synthetic_hardcover_identity import (
    SyntheticBookIdentityProvider,
)
from mechanical_librarian.application.hardcover_identity import (
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IdentityDecisionPreviewReport,
    IdentityReportError,
    IsbnIdentityQuery,
    RunHardcoverIdentityReport,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.domain.hardcover_identity import (
    IdentityDecisionOutcome,
    PrivateIdentityQuery,
    PrivateQueryError,
    ProviderBookObservation,
    ProviderIdentityObservation,
    TitleComparison,
    decide_book_identity,
    isbn_identity,
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
        for value in (
            isbn_identity("9780000000019"),
            isbn_identity("0306406152"),
            isbn_identity("9790000000001"),
        ):
            rendered = repr(value)
            self.assertEqual(rendered, "IsbnIdentity(<redacted>)")
            self.assertNotIn(value.canonical_isbn_13, rendered)
            if value.equivalent_isbn_10 is not None:
                self.assertNotIn(value.equivalent_isbn_10, rendered)
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


class BookIdentityDecisionTests(unittest.TestCase):
    def query(
        self, isbn: str = "9780000000019", title: str = "Synthetic Ebook Alpha"
    ) -> PrivateIdentityQuery:
        return validate_private_query({"isbn": isbn, "title": title})

    def row(
        self,
        *,
        edition_id: int = 1001,
        book_id: int = 2001,
        edition_canonical_id: int | None = None,
        book_canonical_id: int | None = None,
        isbn_10: str | None = "0000000019",
        isbn_13: str | None = "9780000000019",
        title: str = "Synthetic Ebook Alpha",
        reading_format_id: int = 4,
        edition_state: str = "normalized",
        book_state: str = "processed",
    ) -> ProviderIdentityObservation:
        return ProviderIdentityObservation(
            id=edition_id,
            book_id=book_id,
            canonical_id=edition_canonical_id,
            isbn_10=isbn_10,
            isbn_10_valid=True if isbn_10 is not None else None,
            isbn_13=isbn_13,
            isbn_13_valid=True if isbn_13 is not None else None,
            isbns_match=True,
            reading_format_id=reading_format_id,
            state=edition_state,
            compilation=False,
            book=ProviderBookObservation(
                id=book_id,
                canonical_id=book_canonical_id,
                book_status_id=1,
                state=book_state,
                title=title,
                parent_book_id=None,
                compilation=False,
                is_partial_book=False,
            ),
        )

    def test_isbn_equivalence_covers_isbn10_978_and_979(self) -> None:
        from_10 = isbn_identity("0000000019")
        from_13 = isbn_identity("9780000000019")
        isbn_979 = isbn_identity("9790000000001")
        self.assertEqual(from_10, from_13)
        self.assertEqual(from_13.equivalent_isbn_10, "0000000019")
        self.assertEqual(isbn_979.canonical_isbn_13, "9790000000001")
        self.assertIsNone(isbn_979.equivalent_isbn_10)

    def test_one_primary_and_exact_alias_collapse_to_one_identity(self) -> None:
        primary = self.row()
        alias = self.row(
            edition_id=1002,
            book_id=2002,
            edition_canonical_id=1001,
            book_canonical_id=2001,
        )
        decision = decide_book_identity(self.query(), (alias, primary))
        self.assertEqual(decision.outcome, IdentityDecisionOutcome.MATCHED)
        self.assertEqual(decision.rows_validated, 2)
        self.assertEqual(decision.aliases_collapsed, 1)
        self.assertEqual(decision.comparison, TitleComparison.EQUAL)
        self.assertEqual(decision.identity.edition_id, 1001)
        self.assertEqual(decision.identity.book_id, 2001)
        for value in (repr(primary), repr(alias), repr(decision), repr(decision.identity)):
            self.assertNotIn("Synthetic", value)
            self.assertNotIn("978", value)

    def test_two_primaries_and_four_compatible_rows_are_ambiguous(self) -> None:
        distinct = decide_book_identity(
            self.query(),
            (self.row(), self.row(edition_id=1002, book_id=2002)),
        )
        four = decide_book_identity(
            self.query(),
            (
                self.row(),
                self.row(edition_id=1002, book_id=2002, edition_canonical_id=1001, book_canonical_id=2001),
                self.row(edition_id=1003, book_id=2003, edition_canonical_id=1001, book_canonical_id=2001),
                self.row(edition_id=1004, book_id=2004, edition_canonical_id=1001, book_canonical_id=2001),
            ),
        )
        self.assertEqual(distinct.outcome, IdentityDecisionOutcome.AMBIGUOUS)
        self.assertEqual(four.outcome, IdentityDecisionOutcome.AMBIGUOUS)

    def test_incompatibility_precedes_four_row_ambiguity(self) -> None:
        rows = tuple(
            self.row(
                edition_id=1000 + index,
                book_id=2000 + index,
                edition_canonical_id=None if index == 1 else 1001,
                book_canonical_id=None if index == 1 else 2001,
                reading_format_id=3 if index == 4 else 4,
            )
            for index in range(1, 5)
        )
        decision = decide_book_identity(self.query(), rows)
        self.assertEqual(decision.outcome, IdentityDecisionOutcome.INCOMPATIBLE)
        self.assertEqual(decision.rows_validated, 4)

    def test_alias_cycles_dangling_references_and_semantic_changes_are_malformed(self) -> None:
        cases = (
            (
                self.row(edition_canonical_id=1002, book_canonical_id=2002),
                self.row(edition_id=1002, book_id=2002, edition_canonical_id=1001, book_canonical_id=2001),
            ),
            (self.row(edition_canonical_id=9999, book_canonical_id=9998),),
            (
                self.row(),
                self.row(
                    edition_id=1002,
                    book_id=2002,
                    edition_canonical_id=1001,
                    book_canonical_id=2001,
                    title="Different Provider Bytes",
                ),
            ),
            (self.row(), self.row()),
        )
        for rows in cases:
            with self.subTest(rows=len(rows)):
                self.assertEqual(
                    decide_book_identity(self.query(), rows).outcome,
                    IdentityDecisionOutcome.MALFORMED,
                )

    def test_non_text_book_state_is_incompatible_without_exception(self) -> None:
        row = self.row()
        bad_book = replace(row.book, state=[])
        decision = decide_book_identity(self.query(), (replace(row, book=bad_book),))
        self.assertEqual(decision.outcome, IdentityDecisionOutcome.INCOMPATIBLE)

    def test_979_forbids_isbn10_and_zero_rows_do_not_match(self) -> None:
        valid = self.row(isbn_10=None, isbn_13="9790000000001")
        invalid = self.row(isbn_10="0000000019", isbn_13="9790000000001")
        self.assertEqual(
            decide_book_identity(self.query("9790000000001"), (valid,)).outcome,
            IdentityDecisionOutcome.MATCHED,
        )
        self.assertEqual(
            decide_book_identity(self.query("9790000000001"), (invalid,)).outcome,
            IdentityDecisionOutcome.INCOMPATIBLE,
        )
        self.assertEqual(
            decide_book_identity(self.query(), ()).outcome,
            IdentityDecisionOutcome.NO_MATCH,
        )

    def test_permutation_and_linkage_ids_do_not_change_identity_semantics(self) -> None:
        primary = self.row()
        alias = self.row(
            edition_id=1002,
            book_id=2002,
            edition_canonical_id=1001,
            book_canonical_id=2001,
        )
        forward = decide_book_identity(self.query(), (primary, alias))
        reverse = decide_book_identity(self.query(), (alias, primary))
        self.assertEqual(forward, reverse)

    def test_local_title_changes_only_transient_coarse_comparison(self) -> None:
        equal = decide_book_identity(self.query(), (self.row(),))
        different = decide_book_identity(
            self.query(title="Locally Different Title"), (self.row(),)
        )
        self.assertEqual(equal.outcome, different.outcome)
        self.assertEqual(equal.identity, different.identity)
        self.assertEqual(equal.comparison, TitleComparison.EQUAL)
        self.assertEqual(different.comparison, TitleComparison.DIFFERENT)


class RunHardcoverIdentityReportTests(unittest.TestCase):
    def query(self) -> PrivateIdentityQuery:
        return validate_private_query(
            {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
        )

    def row(self) -> ProviderIdentityObservation:
        return BookIdentityDecisionTests().row()

    def context(self) -> OperationContext:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return OperationContext(
            Deadline(now + timedelta(seconds=5)),
            CancellationProbe(CancellationId("hardcover-preview"), lambda: False),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: now),
        )

    def test_structural_provider_is_called_once_with_typed_query_and_context(
        self,
    ) -> None:
        observation = self.row()

        class Provider:
            def __init__(self) -> None:
                self.calls: list[tuple[IsbnIdentityQuery, OperationContext]] = []

            def lookup(
                self, query: IsbnIdentityQuery, context: OperationContext
            ) -> IdentityAttemptResult:
                self.calls.append((query, context))
                return IdentityAttemptResult.success((observation,))

        provider = Provider()
        context = self.context()
        report = RunHardcoverIdentityReport().run(
            provider, self.query(), context
        )
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(
            provider.calls[0][0], IsbnIdentityQuery("9780000000019", "0000000019")
        )
        self.assertIs(provider.calls[0][1], context)
        self.assertIs(type(report), IdentityDecisionPreviewReport)
        self.assertEqual(report.outcome, IdentityDecisionOutcome.MATCHED)
        rendered = report.to_json()
        self.assertNotIn("978", rendered)
        self.assertNotIn("Synthetic", rendered)
        self.assertEqual(
            json.loads(rendered)["result"],
            {
                "outcome": "matched",
                "rows_validated": 1,
                "aliases_collapsed": 0,
                "comparison": "equal",
            },
        )

    def test_attempt_result_is_a_closed_success_or_failure_union(self) -> None:
        with self.assertRaises(ValueError):
            IdentityAttemptResult()
        with self.assertRaises(ValueError):
            IdentityAttemptResult(
                observations=(),
                failure=IdentityAttemptResult.failed(
                    IdentityAttemptFailureKind.MALFORMED
                ).failure,
            )
        self.assertEqual(IdentityAttemptResult.success(()).observations, ())
        self.assertEqual(
            IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INCOMPATIBLE
            ).failure.kind,
            IdentityAttemptFailureKind.INCOMPATIBLE,
        )

    def test_attempt_success_enforces_the_value_free_observation_limit(self) -> None:
        observation = self.row()
        limit = (observation,) * 4
        self.assertEqual(IdentityAttemptResult.success(limit).observations, limit)

        oversized = (observation,) * 5
        with self.assertRaisesRegex(ValueError, "resource limit") as caught:
            IdentityAttemptResult.success(oversized)
        rendered = str(caught.exception)
        self.assertEqual(
            rendered, "identity attempt observations exceed the resource limit"
        )
        self.assertNotIn("9780000000019", rendered)
        self.assertNotIn("Synthetic Ebook Alpha", rendered)
        with self.assertRaisesRegex(ValueError, "resource limit"):
            IdentityAttemptResult(observations=oversized)

    def test_preview_report_enforces_exact_closed_outcome_invariants(self) -> None:
        valid = (
            (IdentityDecisionOutcome.MATCHED, 1, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.MATCHED, 3, 2, TitleComparison.DIFFERENT),
            (IdentityDecisionOutcome.NO_MATCH, 0, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.AMBIGUOUS, 2, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.AMBIGUOUS, 4, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.INCOMPATIBLE, 0, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.INCOMPATIBLE, 4, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.MALFORMED, 0, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.MALFORMED, 4, 0, TitleComparison.NOT_COMPARED),
        )
        for values in valid:
            with self.subTest(valid=values):
                IdentityDecisionPreviewReport(*values).validate()

        invalid = (
            (IdentityDecisionOutcome.MATCHED, 0, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.MATCHED, 4, 3, TitleComparison.DIFFERENT),
            (IdentityDecisionOutcome.MATCHED, 2, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.MATCHED, 2, 1, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.NO_MATCH, 1, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.NO_MATCH, 0, 1, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.NO_MATCH, 0, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.AMBIGUOUS, 1, 0, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.AMBIGUOUS, 2, 1, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.AMBIGUOUS, 2, 0, TitleComparison.DIFFERENT),
            (IdentityDecisionOutcome.INCOMPATIBLE, 1, 1, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.INCOMPATIBLE, 1, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.MALFORMED, 1, 1, TitleComparison.NOT_COMPARED),
            (IdentityDecisionOutcome.MALFORMED, 1, 0, TitleComparison.DIFFERENT),
            (IdentityDecisionOutcome.MATCHED, True, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.NO_MATCH, 0, False, TitleComparison.NOT_COMPARED),
            ("matched", 1, 0, TitleComparison.EQUAL),
            (IdentityDecisionOutcome.MATCHED, 1, 0, "equal"),
        )
        for values in invalid:
            with self.subTest(invalid=values):
                with self.assertRaises(TypeError):
                    IdentityDecisionPreviewReport(*values).to_json()

    def test_synthetic_provider_adapter_is_single_use_and_owned_by_adapters(
        self,
    ) -> None:
        query = IsbnIdentityQuery("9780000000019", "0000000019")
        adapter = SyntheticBookIdentityProvider(query, (self.row(),))
        self.assertEqual(
            type(adapter).__module__,
            "mechanical_librarian.adapters.providers.synthetic_hardcover_identity",
        )
        result = adapter.lookup(query, self.context())
        self.assertEqual(result.observations, (self.row(),))
        with self.assertRaisesRegex(ValueError, "invocation is invalid"):
            adapter.lookup(query, self.context())
        fresh = SyntheticBookIdentityProvider(query, ())
        with self.assertRaisesRegex(ValueError, "invocation is invalid"):
            fresh.lookup(
                IsbnIdentityQuery("9780306406157", "0306406152"), self.context()
            )

    def test_deterministic_provider_failures_map_without_private_values(self) -> None:
        class Provider:
            def __init__(self, kind: IdentityAttemptFailureKind) -> None:
                self.kind = kind

            def lookup(
                self, query: IsbnIdentityQuery, context: OperationContext
            ) -> IdentityAttemptResult:
                return IdentityAttemptResult.failed(self.kind)

        for kind, outcome in (
            (IdentityAttemptFailureKind.MALFORMED, IdentityDecisionOutcome.MALFORMED),
            (IdentityAttemptFailureKind.OVERSIZED, IdentityDecisionOutcome.MALFORMED),
            (
                IdentityAttemptFailureKind.INCOMPATIBLE,
                IdentityDecisionOutcome.INCOMPATIBLE,
            ),
        ):
            with self.subTest(kind=kind):
                report = RunHardcoverIdentityReport().run(
                    Provider(kind), self.query(), self.context()
                )
                self.assertEqual(report.outcome, outcome)
                self.assertNotIn("978", report.to_json())

    def test_transient_failures_and_malformed_providers_fail_value_free(self) -> None:
        class TransientProvider:
            def lookup(
                self, query: IsbnIdentityQuery, context: OperationContext
            ) -> IdentityAttemptResult:
                return IdentityAttemptResult.failed(IdentityAttemptFailureKind.TIMEOUT)

        with self.assertRaisesRegex(
            IdentityReportError, "attempt did not produce a decision"
        ) as failure:
            RunHardcoverIdentityReport().run(
                TransientProvider(), self.query(), self.context()
            )
        self.assertNotIn("978", str(failure.exception))
        with self.assertRaises(TypeError):
            RunHardcoverIdentityReport().run(
                object(), self.query(), self.context()
            )


if __name__ == "__main__":
    unittest.main()
