# Hardcover F09f Identity Contract

This reference defines the provider-neutral identity decision and the fixed,
bounded Hardcover ISBN adapter installed through F09f increment 3. The adapter
is proved only through a credential-free structural byte channel and is not
composed into production.

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

`identity-decision-v3` retains its structural observation fake. The new
production-capable adapter implements the same port and contains no identity
decision logic. `fixed-adapter-v3` injects a post-policy in-memory byte channel
into that real adapter. The channel cannot resolve, connect, open a socket, or
read a credential. Production composition remains absent, so valid enabled
configuration continues to return `provider_not_activated`.

## Fixed operation and authentication

The sole request contract is `hardcover-isbn-identity-v1`:

- `POST https://api.hardcover.app/v1/graphql` only;
- operation `HardcoverEditionByIsbn` and the frozen 353-byte query whose
  SHA-256 is
  `309af66157939f2d817680cd8f10b58d27c58f3b3a4f3da7b6b0ee66c61d706b`;
- canonical UTF-8 JSON with sorted keys, comma/colon separators,
  `ensure_ascii=true`, no final newline, and at most 8,192 bytes;
- variables `[ISBN-13, ISBN-10]` for 978 and `[ISBN-13]` for 979, preserving
  that order and removing no other values; and
- HTTPS port 443, empty URL query and fragment, exact Host and TLS SNI, and a
  connected peer equal to the deterministically selected validated public IP.

The adapter resolves once per attempt, rejects an empty answer or any private,
loopback, link-local, multicast, reserved, unspecified, scoped, or IPv4-mapped
IPv6 address before opening the byte channel, and never re-resolves after
selection. Resolution is bounded before address parsing or sorting to four
answers; each answer and the connected-peer text is bounded to the 39 ASCII
bytes/scalars required by an IPv6 address. The authenticator attaches
`Authorization: Bearer <raw-token>` only
after revalidating the complete method, destination, query bytes, operation,
variables, canonical body, contract identity, selected public address, Host,
SNI, and peer plan. A near miss receives no authorization and no send.

The future network channel must use one dedicated TLS client with environment
trust, redirects, cookies, ambient credentials, HTTP/2, compression, and
keep-alive disabled. Increment 3 intentionally supplies no such channel.

## Hostile response boundary

Only HTTP 200 is parsed. HTTP 401 and 403 become `authentication` and
`authorization`; 408, 429, and 500–599 remain internal timeout, rate-limit, and
unavailable failures; redirects and other statuses are `incompatible`. A
status line is rejected above 128 bytes before parsing. A non-200 body is
released without JSON parsing or retention.

Every post-exchange exit uses one conclusive release operation. The body reader
first confirms normal close; a failed, absent, non-callable, or raising close
invokes the byte-channel-owned release fallback. If neither confirms release,
the attempt returns only value-free `unavailable`, replacing success and every
status- or body-derived candidate.

A 200 response must have exactly one unpadded `Content-Type` matching
`application/json` with an optional unquoted UTF-8 charset, at most one absent
or exact `identity` Content-Encoding, and at most one canonical decimal
Content-Length. A length above 65,536 is rejected before streaming; an in-range
length must equal the bytes received. The body is streamed to 65,536 bytes,
strict-decoded as UTF-8 once, and rejects a BOM or whitespace-only content.

Before JSON construction, a string/escape-aware scan caps structural depth at
8 with root depth 1. The decoded document permits at most 128 value nodes, 96
object members, and four cumulative list elements. Keys count as members, not
strings. Each string value permits 512 scalars and 2,048 UTF-8 bytes; cumulative
string values permit 4,096 scalars and 16,384 bytes. JSON numbers are signed
32-bit integer tokens only: floats, constants, and booleans used as integers
fail closed. Duplicate and unknown keys fail closed.

The exact root is `{data:{editions:[...]}}`. GraphQL errors, null or partial
data, and extra members are malformed. Every one of up to four rows is fully
parsed before domain selection. Provider titles additionally require 1–512
scalars, at most 2,048 bytes, exact NFC, Unicode trimming, and the established
control, bidi, surrogate, noncharacter, and line-separator exclusions.

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

The exact current-head registry is `preview-registry-c3-v6.json`, SHA-256
`95d971c2d6e689044320512f415caa38b6c944cab2ec91132669523f16ab2fed`.
It derives from installed C2
`557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11`
and includes exactly `inert-report-v3`, `identity-decision-v3`, and
`fixed-adapter-v3`. The C3-scoped output schema, privacy contract, and privacy
scan exclude every future durable-work and executor scenario.

`fixed-adapter-v3` emits only `outcome`, `row_count`, `peer_equal`, the constant
`actual_network:false`, and `address_policy`. Request/response/header bytes,
addresses, ISBNs, titles, provider IDs, tokens, fixture/resource identities,
digests, and correlations remain forbidden across every runtime channel.
