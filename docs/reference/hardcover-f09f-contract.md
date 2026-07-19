# Hardcover F09f Identity Contract

This reference defines the provider-neutral identity decision, the fixed,
bounded Hardcover ISBN adapter, and durable work repository installed through
F09f increment 4. The adapter
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
first confirms normal close; a false result or ordinary `Exception` invokes the
byte-channel-owned release fallback. If neither confirms release,
the attempt returns only value-free `unavailable`, replacing success and every
status- or body-derived candidate. Read disablement and a finalization attempt
are distinct from confirmed producer release; repeated close/abort calls must
perform and confirm a real producer operation rather than infer success from
local wrapper state. Only exact boolean `true` confirms either close or the
independently owned fallback.

Caught external-input and dependency `Exception` failures are reduced to closed
typed outcomes before a public exception is raised, with value-free output and
cause/context protection for raw input, external exception text, credentials,
and private titles. Typed channel failure metadata is trusted according to its
application-owned interface. `KeyboardInterrupt`, `SystemExit`, and
`GeneratorExit` preserve interpreter control-flow semantics. Same-process
descriptor, subclass, post-construction mutation, forged-exception, and
traceback-local sabotage are outside the supported threat model.

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
`394ac103c85548ca3a1de44ef236ed59323fada7340a952b66aab986d0621a02`.
It derives from installed C2
`557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11`
and includes exactly `inert-report-v3`, `identity-decision-v3`, and
`fixed-adapter-v3`. The C3-scoped output schema, privacy contract, and privacy
scan exclude every future durable-work and executor scenario.

The fixed-adapter input schema resolves the project meta-schema
`https://mechanical-librarian.github.io/schemas/utf8-byte-length-meta-v1`,
SHA-256 `6e661639a611c463c65303efc2c83f97e9b522d23ee30c2e4e78f0d4c4e703b1`.
That meta-schema requires the project UTF-8 byte-length vocabulary; the
`mlUtf8MaxBytes` keyword is executable and cannot be treated as an annotation.

`fixed-adapter-v3` emits only `outcome`, `row_count`, `peer_equal`, the constant
`actual_network:false`, and `address_policy`. Request/response/header bytes,
addresses, ISBNs, titles, provider IDs, tokens, fixture/resource identities,
digests, and correlations remain forbidden across every runtime channel.

## Durable identity work

The application derives an opaque 64-hex work key before persistence. The
repository never accepts or stores a raw ISBN, local title, provider payload,
credential, query, or path. One work key has monotonically increasing
generations and at most one `pending`, `claimed`, or `retry_wait` generation.
Initial acceptance atomically creates generation, candidate 1, aggregate start,
exact 30-second deadline, and its event. Expiring a greater-than-24-hour match
instead creates one unreserved pending generation; the next invocation reserves
candidate 1 and starts its aggregate atomically.

A gate-held claim reserves a candidate under a monotonically increasing fence
and 15-second lease without consuming an attempt. `renew_and_start` requires
the exact owner and fence and at least six seconds of aggregate time remaining,
regardless of whether the old live lease has less than six seconds, renews the lease
to wall-now plus 15 seconds, and commits the started attempt before a later
transport could be allowed. At most three attempts exist. This increment does
not compose a gate or transport into production.

After an unstarted claim expires, recovery advances the fence and schedules the
same candidate. After a started attempt expires, recovery finalizes it once as
interrupted and schedules the next candidate. A retry claim derives its
candidate and fence from the unique current due schedule; callers cannot select
one. The repository reconciles its exact reason, creation/due chronology,
fence, candidate, prior immutable claim, attempt, terminal-result, and
generation-attempt history before mutation. Missing, duplicate, early, or
logically impossible schedules fail without mutation. Aggregate exhaustion or
a third interrupted attempt commits terminal
`failed` evidence. Earlier fences cannot start, recover, cancel, or publish,
and cancellation is supported only for the exact current claimed work owner,
fence, candidate, and lease identity. Pending and retry-waiting work cannot be
cancelled through this increment. Terminal
publication finalizes the attempt, appends result and a closed structural
observation whose only canonical fields are `outcome:"matched"` and bounded
`row_count`, updates matched currentness, transitions the generation,
and appends its event in one transaction. Failure before commit leaves all
those records and the previous current pointer unchanged.

Matched currentness is reusable through exactly 24 hours. At greater age, one
transaction marks the old pointer expired and creates one unreserved next
generation. Reuse first reconciles the pointer with the referenced successful
generation, matched terminal result, exact fence/timestamps, observation digest,
and closed canonical observation. The succeeded generation's terminal
`updated_at`, result publication, observation creation, and current-pointer
timestamp must be exactly equal; inconsistent state fails without mutation.
Immutable candidate, claim, retry, event, result, and observation
records plus finalize-once attempts preserve restart evidence. Mutable
generation and current-pointer rows are constrained to the closed state model.

Migration 9 is additive and forward-only from the genuine schema-8 prefix.
The `durable-work-v3` preview reconstructs migrations 1–8 plus the synthetic
sentinel, compares the canonical logical snapshot digest, verifies the checksum
ledger and database integrity, injects a migration-statement rollback,
applies migration 9 twice, and runs publication rollback, restart, recovery,
stale-fence, retry, and currentness proofs on an initially absent disposable
database beneath an empty owner-only disposable directory. Production still
returns `provider_not_activated` before this
repository, a private query, credential, adapter, or network is opened.
The preview opens the directory once with no-follow directory semantics,
captures device, inode, owner, and mode, and retains that descriptor through
descriptor-relative serialized staging and no-replace publication without
changing the process working directory. Parent replacement and symlink
swaps are rejected and private staging is removed, including on base exceptions.

The exact current-head registry is `preview-registry-c4-v6.json`, SHA-256
`3e5183fb4ddff6674717f870c99b82ecb24eb2d232d759cf08957d2506d93e81`.
It derives from the installed C3 registry and binds the canonical C4 input,
output, closed input schema, variant matrix, cumulative schema-v5 output,
privacy contract/scan, and contributor-independent schema-8 dump and ledger.
