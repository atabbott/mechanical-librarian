# Hardcover F09f Identity Contract

This reference defines the provider-neutral identity decision, the fixed,
bounded Hardcover ISBN adapter, schema-11 durable lifecycle, and installed
production command boundary. The command is disabled by default and remains
target-independent, report-only, proposal-free, and write-free.

## Application port

The application owns this interface:

```text
BookIdentityProvider.prepare(IsbnIdentityQuery, OperationContext)
  -> PreparedIdentityExchange | IdentityAttemptResult

PreparedIdentityExchange.exchange(
  OperationContext, begin_transport: Callable[[], OperationContext]
) -> IdentityAttemptResult
```

One prepared exchange can be consumed once. Preparation completes credential,
DNS, address, request, and authorization work without consuming a durable
start. The application supplies the value-free `begin_transport` callback;
the production adapter invokes it exactly once at the last boundary before
network exchange, and only that callback claims and durably starts the attempt
under the deployment gate. A preparation failure returns a typed result with
no claim, start, or one-shot consumption. The port has no persistence, lock, retry, wait,
credential-discovery, public lifecycle, proposal, review, or write behavior.
`IdentityAttemptResult` contains exactly one typed observation tuple or one
typed failure. Query, result, observation, identity, and decision representations
redact private values from `repr` and diagnostics.

`identity-decision-v3` retains its structural observation fake. The new
production-capable adapter implements the same port and contains no identity
decision logic. `fixed-adapter-v3` injects a post-policy in-memory byte channel
into that real adapter. The channel cannot resolve, connect, open a socket, or
read a credential. Enabled production composes the same fixed adapter through a
dedicated selected-IP HTTPS channel only after durable start authority.

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

The production network channel uses one dedicated TLS client with environment
trust, redirects, cookies, ambient credentials, HTTP/2, compression, and
keep-alive disabled. Its request boundary suppresses `httpx` and `httpcore`
records so URL, selected address, headers, body, and dependency failure values
cannot enter application logging. Increment 3 intentionally supplies no such
channel.

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

The historical increment-3 registry is `preview-registry-c3-v6.json`, SHA-256
`394ac103c85548ca3a1de44ef236ed59323fada7340a952b66aab986d0621a02`.
It derives from installed C2
`557dfdf0300bf2d59fa5571fd3af2136fd4f6099e14b1f1d1b953b0cb56a9d11`
and includes exactly `inert-report-v3`, `identity-decision-v3`, and
`fixed-adapter-v3`. Its C3-scoped output schema, privacy contract, and privacy
scan intentionally exclude the later durable-work, executor, lifecycle, and
activation scenarios. The exact current F09f evidence root is the activation
registry identified under [Production activation](#production-activation).

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
repository never accepts or stores a raw ISBN, local title, raw provider
payload, credential, query, or path. A successful matched publication stores
only the bounded selected edition ID, book ID, exact validated provider title,
row count, and outcome needed to report and compare the same current identity
after restart. One work key has monotonically increasing
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
publication finalizes the attempt, appends result and a closed private matched
observation whose canonical fields are `outcome:"matched"`, bounded
`row_count`, positive signed-32-bit `edition_id` and `book_id`, and the bounded
exact `provider_title`, updates matched currentness, transitions the generation,
and appends its event in one transaction. Failure before commit leaves all
those records and the previous current pointer unchanged.

Matched currentness is reusable through exactly 24 hours. At greater age, one
transaction marks the old pointer expired and creates one unreserved next
generation. Reuse first reconciles the pointer with the referenced successful
generation, matched terminal result, exact fence/timestamps, observation digest,
and closed canonical observation. Legacy two-field matched observations remain
readable as immutable history but are non-reusable; migration 11 expires their
current pointers so the next invocation refreshes rather than reporting an
identity that cannot be reconstructed. The succeeded generation's terminal
`updated_at`, result publication, observation creation, and current-pointer
timestamp must be exactly equal; inconsistent state fails without mutation.
Immutable candidate, claim, retry, event, result, and observation
records plus finalize-once attempts preserve restart evidence. Mutable
generation and current-pointer rows are constrained to the closed state model.

The private observation digest is SHA-256 over exactly the fixed
`hardcover-matched-observation:v3` domain separator, opaque work key,
generation, and canonical matched observation. It is an integrity binding, not
a runtime input digest, and excludes local title, raw ISBN, and the serialized
private query. The descriptor gate's only other private persistence exception
is a crash-recovery row containing the database device, inode, binding marker,
and original SQLite application ID. That closed row contains no path,
provider/user data, query, or credential and is validated and deleted before
work or provider authority; a normal binding completes with no recovery row.

Migration 9 is additive and forward-only from the genuine schema-8 prefix.
The `durable-work-v3` preview reconstructs migrations 1–8 plus the synthetic
sentinel, compares the canonical logical snapshot digest, verifies the checksum
ledger and database integrity, injects a migration-statement rollback,
applies migration 9 twice, and runs publication rollback, restart, recovery,
stale-fence, retry, and currentness proofs on an initially absent disposable
database beneath an empty owner-only disposable directory. At that historical
increment production returned `provider_not_activated` before this repository,
a private query, credential, adapter, or network was opened.
The preview opens the directory once with no-follow directory semantics,
captures device, inode, owner, and mode, and retains that descriptor through
descriptor-relative serialized staging and no-replace publication without
changing the process working directory. Parent replacement and symlink
swaps are rejected and private staging is removed, including on base exceptions.

The exact increment-4 registry is `preview-registry-c4-v6.json`, SHA-256
`94380cffa51cd0bb843eafa5aa8edb39e14bc7c254c90b9feb8d08b497ac2d22`.
It derives from the installed C3 registry and binds the canonical C4 input,
output, closed input schema, variant matrix, cumulative schema-v5 output,
privacy contract/scan, and contributor-independent schema-8 dump and ledger.

Increment 5 adds one bounded executor invocation without activating production
composition. It accepts or refreshes one work generation, inspects the
persisted aggregate deadline before acquiring the deployment gate, claims and
atomically starts only under that gate, invokes the provider exactly once, and
publishes through the current durable fence. The attempt deadline is the
earlier of the caller deadline and start plus five seconds; results at or after
that boundary are late and publish as timeout. A busy gate, existing claimed or
retry-wait state, and retryable publication with at least the exact six-second
reserve return `in_progress`. A successfully committed retryable terminal with
less reserve returns `retry_exhausted`, not `persistence_failure`. This executor
does not wait, loop, recover, cancel, install signal handlers, or start a later
candidate; those lifecycle responsibilities remain separately tracked.

The exact C5 registry is `preview-registry-c5-v6.json`, SHA-256
`bbaa89174d682be79e4e0b42f1fe0fc8a1643201bbc7b090bf926cba421e5c39`.
It derives from the exact C4 registry and binds a separate cumulative C5 output
schema, privacy contract, privacy scan, and strict semantic fixture set without
modifying predecessor evidence. Contributor labels and process names are
control-only: a fixed 55-character owner outside their complete syntax domains
crosses the persistence seam, and no runtime or persisted value derives from
them. Sentinel-bearing and ordinary-vocabulary collision variants prove that
flow without treating equal incidental text as a leak. The exact serialized
stdin is absent from every SQLite database-prefix channel.
Privacy qualification materializes disposable-state WAL frames only through a
test-only observer transaction whose timestamp value is restored and read back
exactly. It proves nonempty main, WAL, and SHM while present, then enumerates
logical table/column values. Exact private observation/current-pointer/work-key/
recovery cells are validated before exemption. Elsewhere, only eight-hex 32-bit
digest prefixes are excluded as collision-prone substring evidence; exact/raw
values, full encodings, full collision-resistant digests, and causal poison
remain forbidden.

## Installed activation

Enabled production validates the private query before opening state. It derives
the opaque work key from the exact public
[`hardcover-isbn-work-key-v1`](hardcover-work-key-contract-v1.json) fields,
including canonical ISBN-13 and authority revision but excluding title, paths,
credential, traffic, and the qualification flag. It then initializes or opens
schema-11 state, accepts durable work, and acquires the descriptor-bound gate.
Acceptance creates or reconciles the exact per-generation one-or-three egress
policy before provider preparation. A later request must match it and cannot
widen one-shot authority after a zero-start failure, cancellation, or restart.
The runtime connection is challenged against the gate's exact database inode
before work acceptance or provider authority. Provider preparation may then
read the credential and complete isolated bounded resolution, request
construction, and authorization without claiming or starting work. Immediately
before the one selected-public-IP HTTPS exchange, the prepared exchange invokes
the gate-held callback that claims, renews, and durably starts the attempt. The
Hardcover Host and TLS SNI are preserved. The exact database identity published
for qualification is bound into the gate before state is reopened; replacement
fails before start or send.

The opaque work key is the sole persistence-only exception to the general ban
on ISBN-derived stable values. It is required for durable currentness and
idempotency, excludes title and raw/reversible input, and is never emitted,
logged, exported, reported, or audited.

For `matched`, production schema v6 reports the positive edition ID, book ID,
and transient `equal` or `different` comparison. Reuse obtains the IDs and
provider title from the validated private matched observation and recomputes
the comparison against the new transient local title without another provider
request. Every non-matched report contains only `schema` and `outcome`.
Provider title is private durable comparison input only; it is never emitted,
logged, audited, traced, metered, exported, or retained in review evidence.
The content-bound observation digest and gate recovery row remain private under
the exact closed derivation and lifetime above; neither expands a runtime,
evidence, or export surface.

The public outcomes are exactly `matched`, `disabled`, `invalid_input`,
`in_progress`, `credential_unavailable`, `authentication`, `authorization`,
`cancelled`, `malformed`, `oversized`, `empty`, `no_match`, `ambiguous`,
`incompatible`, `stale`, `persistence_failure`, and `retry_exhausted`.
`provider_not_activated` is unreachable. Internal retry causes remain durable
and never become terminal public reports.

The exact activation registry is
[`hardcover-f09f-activation-registry-v1.json`](hardcover-f09f-activation-registry-v1.json),
SHA-256 `552fb260d15f0e81a9ac4f64e926897513d698ecfd3857d303b0328356ea88b0`.
It derives from and binds the merged C6 registry, all six retained contributor
scenarios, the cumulative output schema and exact privacy contract/scan,
the work-key contract, the exact one-shot authority contract, and production
output schema v6
[`hardcover-identity-report-output-schema-v6.json`](hardcover-identity-report-output-schema-v6.json),
SHA-256 `f9ac215d22e2f9e67f9d8d028fde5ba9738933ec74f3a80fc71242c42b97de98`,
and production privacy contract
[`hardcover-identity-report-output-privacy-v1.json`](hardcover-identity-report-output-privacy-v1.json),
SHA-256 `21321ec555526c43b657d9f5a85775cd194f17d123bea426ee9c6d407aac8260`.
Synthetic runtime remains credential-free after activation.
