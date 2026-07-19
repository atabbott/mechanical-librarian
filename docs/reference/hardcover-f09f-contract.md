# Hardcover F09f Identity Contract

This reference defines the provider-neutral, report-only identity decision
installed by F09f increment 2. It does not activate a Hardcover client.

## Application port

The application owns this interface:

```text
BookIdentityProvider.lookup(IsbnIdentityQuery, OperationContext)
  -> IdentityAttemptResult
```

One call is one attempt. The port has no persistence, lock, retry, wait,
credential-discovery, public lifecycle, proposal, review, or write behavior.
`IdentityAttemptResult` contains exactly one typed observation tuple or one
typed failure. Query, result, observation, identity, and decision representations
redact private values from `repr` and diagnostics.

The current executable implementation injects a structural in-memory fake. It
returns only contributor-supplied typed observations and contains no identity
decision logic. No production provider implements or is composed into this
port at this increment.

## ISBN identity

- Input is checksum-valid ASCII ISBN-10 or ISBN-13.
- ISBN-13 is canonical.
- A `978` ISBN-13 carries its exact equivalent ISBN-10.
- A `979` ISBN-13 has no ISBN-10 and any returned ISBN-10 is incompatible.
- Returned ISBN values must be exact valid ASCII values canonicalising to the
  query. Validity flags are `null` exactly when their ISBN is `null`, otherwise
  `true`. At least one ISBN is present.
- When both ISBNs are present, `isbns_match` is `true`; with one it is `null` or
  `true`.

## Compatible observation

An edition observation is compatible only when:

- edition `id` and `book_id` are positive signed 32-bit integers and
  `book_id == book.id`;
- edition `canonical_id` is null or a positive signed 32-bit integer;
- `reading_format_id == 4`, state is `normalized`, and compilation is false;
- its embedded book has a positive signed 32-bit ID, null or positive signed
  32-bit `canonical_id`, `book_status_id == 1`, state `normalized` or
  `processed`, no parent, and both compilation and partial-book flags false;
- the provider title satisfies the same scalar, byte, and forbidden-character
  parser limits as the private local title; and
- its ISBN fields satisfy the query identity above.

Any incompatible row makes the whole one-to-four-row result incompatible.
Selection never skips around one.

## Alias graph and semantic equality

Edition and book aliases are resolved independently. A primary has null
`canonical_id`; an alias directly names a compatible returned primary. Self
references, missing targets, alias-to-alias targets, cycles, conflicting
duplicate identities, or semantically different aliases are malformed.

Alias semantic equality contains canonical ISBN-13, reading format, edition
state, book status and state, edition and book compilation flags, partial-book
and parent-book fields, and exact provider-title bytes. Linkage IDs are graph
links and do not participate in semantic equality. The local title never
participates in provider identity.

## Decision precedence

The pure domain decision applies this order:

1. resource and structural bounds;
2. compatibility of every returned row;
3. alias/reference graph validity;
4. exactly four compatible rows are ambiguous; then
5. one-to-three rows collapse independently by edition and book primary.

Zero rows produce `no_match`. More than one distinct edition or book primary
produces `ambiguous`. One primary plus semantically identical aliases produces
`matched`; `aliases_collapsed` is the number of additional rows.

Only after a match, the local and selected provider titles are compared using
`unicode-title-comparison-v1` (NFC, casefold, NFC, Unicode-space collapse). The
comparison is transient and yields only `equal` or `different`; unmatched
results use `not_compared`.

## Redacted preview result

`identity-decision-v3` emits only:

- `outcome`: `matched`, `no_match`, `ambiguous`, `incompatible`, or `malformed`;
- `rows_validated`: zero through four;
- `aliases_collapsed`: zero through three; and
- `comparison`: `equal`, `different`, or `not_compared`.

ISBNs, titles, query text, book/edition IDs, provider/resource identity, input
digests, fingerprints, stable correlations, raw input/response, paths,
addresses, and secrets are forbidden from stdout, stderr, exceptions, logs,
traces/spans, metrics, persistence, exports, reports, and audit records.

The exact current-head registry is `preview-registry-c2-v6.json`, SHA-256
`557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11`.
It derives from the installed C1 registry `eb3ec8a5454a3e4a21f9bbfe9b8b2308687de00f6a95df7533055a6083009619`
and includes only `inert-report-v3` and `identity-decision-v3`.
