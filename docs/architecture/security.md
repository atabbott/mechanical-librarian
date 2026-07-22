# Security and Threat Model

## Security objectives

1. Protect service credentials, AI credentials, private media, and personal
   library data.
2. Prevent untrusted inputs or AI output from gaining production authority.
3. Make every production change bounded, attributable, reversible where
   possible, and independently verified.
4. Fail closed for writes while continuing safe inventory and diagnosis.
5. Ship secure defaults that do not require expert container hardening.

## Trust boundaries

### Trusted with production authority

- Curator application process.
- Validated service adapter write methods.
- Migration and backup code running within its declared scope.

### Trusted for reads but not writes

- Bounded media extractors.
- Provider adapters after transport and schema validation.
- Inventory and reconciliation jobs.

### Untrusted

- Ebook and audiobook file contents.
- Filenames, folder names, embedded tags, HTML, and images.
- Provider responses and cover URLs.
- Library-service event payloads until re-read through the API.
- AI prompts, reasoning, tool requests, and output.
- Browser input and uploaded files.
- Dependencies, container base images, and update artifacts until verified.

## Principal separation

The single container must preserve least privilege internally:

- `curator` can read configured media and service secrets.
- `reviewer` can access only staged evidence, explicitly attached images, its
  own authentication state, and its output path.
- The reviewer has no service secret, main database, backup, production media,
  container-control, or apply access.
- The supervisor starts principals but does not expose a general privileged
  control socket.

This boundary must be tested from inside the published image.

Within the curator principal, planned composition still follows least authority.
Target-service adapters receive only their scoped connection secret and
supported read/write client; local evidence extractors receive bounded read-only
media handles and no network client; metadata-provider adapters receive only
an application-built minimal bounded allowlisted `ProviderQuery`, their own
provider secret, and an allowlisted outbound client; deterministic policy
receives canonical facts and no credentials or network access; reviewer
backends receive staged canonical packets and their own authentication state.
Provider adapters never receive target-service credentials, main-database
repositories, production write interfaces, or raw media access, and target
adapters never receive provider or reviewer credentials.

A provider adapter never receives a raw `ItemSnapshot` or service row, target
endpoint or credential, unrestricted external ID or path, media handle,
main-database repository, or mutation port. When providers are disabled,
composition constructs no provider adapter or network client, resolves no
provider secret, and permits no provider egress.

### 0.2 title technical-preview boundary

The 0.2 path remains report-only. F09a supplies a deterministic fake candidate
through an application-owned provider-neutral port and obtains its other inputs
only from a licence-safe generated EPUB passed by descriptor to the real F06
child and an in-memory Audiobookshelf-shaped synthetic snapshot. Its CLI parser
accepts only a database path, a closed scenario, and an output format before
state is opened. It accepts no configuration, target/media identifiers, title
values, paths, provider payloads, endpoints, or credentials. F09b preserves that
mode and adds `hardcover-title-proposal-report` for only the generated
`agree-empty` case. Omitting its optional secret-file reference creates no
adapter or network client, resolves no secret, performs no DNS lookup, and
permits no provider egress. It adds no `config-v1` field. A malformed reference
fails before state access. A valid reference whose file is missing, insecure,
unreadable, or invalid makes no request and records `provider_failed` while
preserving local F06 evidence.

The enabled adapter owns one fixed read-only GraphQL query sent only by HTTPS
POST to `https://api.hardcover.app/v1/graphql`. The origin, path, method, and
query are not operator-controlled. TLS validation is mandatory, redirects are
not followed, and loopback, link-local, private, multicast, reserved,
unspecified, or unexpected destinations are rejected. The client connects to
the validated IP while retaining the Hardcover hostname for TLS SNI and
certificate verification, then rejects a peer-address mismatch. It trusts no
environment proxy, sends no cookies, requests no compression, and has the exact
finite bounds in the [F09b contract](../reference/hardcover-f09b-contract.md):
one attempt, no retry or backoff, five seconds total, and concurrency one.
The owner-only secret file contains only the bounded raw token. The provider-
specific authentication adapter constructs `Bearer <raw-token>` only after the
method, HTTPS port, path, empty query and fragment, host header, and TLS SNI
match the fixed request; the shared secret reader and destination checks are
not relaxed.

The application sends only an allowlisted minimum title lookup projection, with
a documented disclosure purpose for each field. It never sends target-service
credentials, cookies, raw target rows, private paths, media, or write
capability. The account-capable provider token is isolated from target and
reviewer credentials and is redacted from logs, diagnostics, exceptions, and
test output.

Provider responses are untrusted and must match the closed versioned live
Typesense wrapper, hit, document, highlight, count, echo, and rich-field shape.
Every field is validated within byte, depth, node, object, list, string, numeric,
and result bounds; only a bounded canonical title value and the minimum
provenance needed to explain its source may be retained. Validated rich fields
and upstream IDs are discarded. Raw requests, responses, headers, tokens,
request bodies, upstream identifiers, and caches are never persisted. Provider
failure or disagreement cannot erase valid F06 evidence, become negative
bibliographic evidence, or grant proposal, decision, review, or write
authority.

The live-compatible provider contract is persisted as
`hardcover-title-poc-v2` for every provider outcome. Version 1 observations
remain readable, while their version-bearing lineage cannot be replayed as a
version 2 observation; no persisted schema migration is required.

`synthetic_preview` is a closed identity-bearing execution mode, not an
operator flag. Persisted proposal sets and their current pointers remain in
dedicated synthetic tables; production applicability is always derived false
and has no independently writable column. Unknown modes, altered canonical
sets, dangling pointers, non-exact predecessors, cross-lineage supersession,
and changed identity headers fail closed without repair. Generated EPUB bytes,
paths, and descriptors are never persisted, and temporary files plus child
process pipes are released on every success, failure, cancellation, and
exception path. The live F06/current-snapshot join and configured-media
resolution remain deferred and cannot be inferred from this preview.

The current target snapshot, current title, successful F06 report, provider
outcome, comparison policy, and proposal set are bound explicitly. An eligible
F06 `TITLE` has a non-empty bounded usable `normalized_value`, not an unsafe,
oversized, or redaction marker. A proposal value is that exact F06
`normalized_value`; F06 `raw_value` remains evidence/provenance and provider
evidence only corroborates.

Comparison applies, in order, supported-runtime NFC normalization, Unicode
default case folding, final NFC normalization, then trims leading/trailing
Unicode whitespace and collapses each internal Unicode-whitespace run to
U+0020. Both the comparison-policy version and runtime Unicode-data version are
bound. Comparison never alters proposal or evidence values. A current title
field that is present and loaded with an empty value remains a valid fill-empty
case when usable F06 and provider titles corroborate; a materially different
current title also remains eligible. A missing, unloaded, or unavailable
current title, current equivalence, disagreement, an empty or unusable
F06/provider title, or another failed, unsupported, disabled, no-match, stale,
duplicate, or unjoinable input creates its exact typed no-proposal result.
Identical replay does not duplicate state, while changed inputs supersede
current applicability without rewriting history.

Ordinary CI and automated tests use an irreversibly synthetic structural fake
through the same adapter seam and no provider credential. F09b historically
completed one exact-installed-artifact qualification against a bounded real
endpoint with a dedicated non-production account and disposable state; its
credentialed command is retired and that request must not be repeated. Only a
redacted aggregate result was retained. This proves no production configuration,
provider-work persistence, freshness/reuse, restart, identity, or live-target
behavior. F09f owns the configured target-independent provider lane;
F09g owns the live join. Nothing in the preview invokes AI,
records a human decision, handles covers or rich
fields, fuses multiple providers, or authorizes a production write.

### F09f configured identity boundary

The first F09f increment added strict configuration, private-query parsing, and
an inert report. After activation a missing or disabled `hardcover_provider`
block still returns `disabled` before query-file, database, credential,
provider/client, DNS, proxy, socket, or network access. Enabled configuration
uses only that optional block; the legacy `providers` list remains inert and no
ambient or inline token is accepted.

The private-query reader uses a canonical parent directory descriptor and a
no-follow final-component open, verifies effective-user ownership, regular
type, owner-only mode, link count one, bounded size, and stable identity around
one exact read. The local title is transient and never hashed, persisted, logged,
exported, reported, audited, traced, or emitted as a metric. The contributor
preview uses the same validation seams but has no production composition and
emits only its closed typed schema-v5 zero-access result.

Increment 3 installed the fixed `hardcover-isbn-identity-v1` adapter behind the
application-owned identity port without composing it into enabled production
mode. F09f activation now composes provider preparation and exchange through
the durable lifecycle at the installed production boundary. The retained
increment-3 demonstration injects a
`validated-public-address` capability and a post-policy in-memory byte channel;
the channel is structurally unable to resolve, connect, open a socket, inherit
a proxy, or read a credential, and the report always records
`actual_network:false`. The synthetic policy corpus separates acceptance of a
public address from rejection of private, loopback, documentation, link-local,
reserved, multicast, unspecified, scoped, and IPv4-mapped addresses before the
channel is opened.

The adapter constructs only the frozen canonical ISBN GraphQL body. It resolves
once under a four-answer cap, bounds each resolved and peer address to the
39-byte/scalar IPv6 textual maximum before parsing or sorting, selects one
validated public address deterministically, preserves the
Hardcover hostname for Host and TLS SNI/certificate verification, forbids
re-resolution, and requires the connected peer to equal the selected address.
Bearer authentication is attached only after exact method, HTTPS origin/path,
empty query/fragment, Host, SNI, operation, query bytes, variables, canonical
body, contract identity, selected address, and peer plan are revalidated. Any
near miss receives neither authorization nor send authority. The production network
channel retains dedicated TLS-client, no-redirect, no-environment-proxy,
no-cookie, no-ambient-credential, no-compression, no-HTTP/2, and connection-
close behavior.

HTTP response bytes remain hostile. Status lines are capped at 128 bytes before
parsing. Non-200 bodies are released without parsing. Every post-exchange path
requires either positively confirmed reader close or a positively confirmed
transport-owned release fallback; otherwise only value-free `unavailable` may
escape, including when the candidate was success or a status-derived result.
Reader-local disablement and attempted finalization never imply release, and
truthy/non-boolean close or fallback returns are rejected. Contributor input,
Hardcover/network bytes and metadata, credentials, DNS/address/peer/TLS data,
real external dependency failures, and resource correctness are the hostile
boundary. Application-owned typed collaborators, documented dependency
objects, and structural fakes honouring their interfaces are trusted. Ordinary
product boundaries translate `Exception` failures to value-free typed results;
interpreter control flow is not caught. External-input translation breaks
cause/context chains where raw input, exception text, credentials, or private
titles could otherwise escape. Same-process descriptor, subclass, mutation,
forged-exception, and traceback-local sabotage are not supported threat actors.
HTTP 200 enforces exact header cardinality and grammar, 65,536 streamed bytes,
strict UTF-8 once without BOM, string-aware depth 8, 128 value nodes, 96
members, four cumulative list elements, per-string and cumulative Unicode/byte
bounds, signed-int32 tokens only, duplicate/unknown-key rejection, and exact
GraphQL, edition, and book shapes. All rows, including the four-row ambiguity
sentinel, are validated before selection. Raw request, response, headers,
addresses, token, ISBN, title, provider identity, exception text, and derived
digests or correlations are forbidden from every runtime channel. Only the
coarse fixed-adapter result fields are exportable. Activated production stores
only the validated bounded provider title and positive edition/book IDs in its
private matched observation so restart reuse can reconstruct the identity and
recompute title comparison. The provider title is never a runtime output, log,
audit, trace, metric, export, support report, or review-evidence value; the IDs
are permitted only in that private observation and the direct matched report.
The observation's private integrity digest binds a fixed domain, opaque work
key, generation, and exact canonical observation; it excludes local title, raw
ISBN, and serialized query. Database binding may transiently commit one closed
crash-recovery row containing only device, inode, binding marker, and original
SQLite application ID. Recovery validates and deletes that row before provider
or work authority; normal binding leaves no row. Paths, provider/user data, and
credentials are forbidden from it.
Schema 11 expires legacy current pointers that cannot supply those fields.

F10a consumes only an already-persisted current F09a lineage selected by the
closed scenario vocabulary. `synthetic-title-review-binding-v1` binds closed
synthetic target constants to the validated manifest-derived item, snapshot,
fingerprint, set, proposal, policy, and bounded provenance. Schema 7 revalidates
the exact current set inside one write transaction, then commits the immutable
case when applicable, scoped current or retired state, lifecycle link, actor,
and reason atomically. Exact replay adds no transition; stale, reverse,
cross-lineage, arbitrary, malformed, destructive, or AI-shaped input cannot
rewind or gain authority. `production_applicable=false` remains derived and no
case row can carry reviewer, decision, approval, apply, target-write, or
destructive capability.

The 0.3 path remains report-only and least-authority. Before scheduling or
binding new F09f provider work, F09g must validate that the selected target,
snapshot, source, and bounded F07b evidence are current and that exactly one
distinct canonical checksum-valid edition-applicable local ISBN may form the
bounded provider query. Repeated equivalent claims of that same canonical ISBN
remain one input; conflicting, ambiguously applicable, or multiple distinct
ISBNs fail closed. This preflight does not make target state an authorization
input to F09f's target-independent durable retry/restart. F09f resolves
credentials only when its
versioned configuration enables the provider; disabled composition constructs
no provider client, resolves no secret, and performs no DNS or egress. The
application, not target or provider data, fixes the allowed request shape and
bounds. Provider responses are untrusted, bounded, and fail closed on malformed,
oversized, ambiguous, multiple, incompatible, stale, or failed results.

F09f persists only the minimum provider-scoped title, book/ebook-edition
identity, query/configuration identity, and outcome provenance needed for
restart and explanation. It retains no raw request or response, header, token,
secret reference value, path, unrestricted upstream identifier, or cache. The
Hardcover title, book, and edition remain one trust lineage. Only F09g may join
that external lineage with independently derived local EPUB evidence, and it
must revalidate every current input atomically before deriving the sole
positive output: one `needs_review`, `production_report_only` proposal whose
value is the exact current F07 normalized title. Every other condition fails
closed as `source_not_ready`, `provider_not_ready`, `evidence_disagreement`,
or `no_change` without erasing usable evidence. Direct command output omits
ISBNs, provider identifiers, work keys, source fingerprints, and private
configuration digests.

The 0.3 checkpoint constructs no reviewer, decision, approval, AI, apply,
target-write, media-write, destructive, tagging, or release-publication
authority. F09c remains a separate 1.0 fallback report/provider lane; F10b owns
later production review-case admission. F11 remains independently synthetic,
and the paths converge only at F17.

## Primary threats and mitigations

### Prompt injection in books or provider metadata

Risk: embedded text instructs the AI to read secrets, execute tools, ignore
policy, or invent approval.

Mitigations:

- Treat extracted text as quoted evidence, never instructions.
- Use an evidence schema that separates facts from system policy.
- Disable or deny reviewer command execution where possible.
- Remove production secrets and paths from the reviewer namespace.
- Validate output against a closed JSON schema and supplied proposal IDs.
- Reject decisions citing unknown evidence or proposing undeclared fields.

### Credential disclosure

Risk: service or AI credentials enter logs, diagnostics, reports, subprocess
environments, crash dumps, or the dashboard.

Mitigations:

- Load credentials through a secret abstraction with file-secret support.
- Pass a minimal environment to subprocesses.
- Use separate Unix principals and deny cross-principal reads.
- Centralize redaction and test known token, URL, header, and query patterns.
- Never include raw request headers or environment dumps in support bundles.
- Resolve target, provider, and reviewer secret references independently and
  inject only the role-specific credential into the owning adapter or runner.

### Server-side request forgery

Risk: malicious cover or provider URLs target localhost, private networks,
cloud metadata services, or unsupported protocols.

Mitigations:

- Permit only HTTPS by default.
- Resolve and reject loopback, link-local, multicast, private, and reserved
  destinations unless an adapter explicitly owns the local service.
- Re-check every redirect destination.
- Bound redirects, response size, content type, and timeout.
- Keep provider hosts in adapter-specific allow rules where practical.
- Do not reuse a target-service HTTP client, redirect policy, cookie jar, or
  authorization header for provider traffic; provider egress uses an
  independently allowlisted client with no access to target write endpoints.

### Archive, image, and parser attacks

Risk: zip bombs, path traversal, decompression bombs, malformed tags, parser
vulnerabilities, excessive memory, or long-running extraction.

Mitigations:

- Never extract archives by trusting stored paths.
- Bound entry count, compressed and expanded bytes, nesting, text, and time.
- Disable external entities, DTD and network retrieval, script execution, and
  remote resources while parsing EPUB package and XHTML content.
- Allowlist local resource types and reject archive traversal, absolute paths,
  compression bombs, oversized XML/XHTML trees, and unsupported encryption.
- Decode images with pixel and byte limits.
- Run external parsers without shell interpolation, with time and resource
  limits, and with read-only input.
- Bound audio decode, sample duration, temporary output, transcription time,
  model resources, and concurrency; delete transient audio on every terminal
  and recovery path.
- Keep parser dependencies updated and covered by malformed-input tests.

F06 EPUB package extraction applies those controls through one transient child.
The parent performs a component-by-component no-follow walk within one
configured read-only mount, hashes the complete descriptor before and after
the child, and repeats the walk after extraction. The child inherits only that
descriptor and a parent-liveness control descriptor; its context contains the
effective narrowest limits, a derived deadline and an opaque fingerprint, not a
host path or product handle. The child entry point sits below an inert package
initializer and therefore does not import product network, database, persistence
or secret implementations. On Linux the child verifies its spawn-time parent
PID before and after installing `PR_SET_PDEATHSIG`, then closes the control
descriptor and lowers `RLIMIT_NPROC` to one; Darwin retains the control-pipe watcher. Raw ZIP
preflight rejects
NUL names, Unicode/case aliases, traversal, split archives, local/central
conflicts, unsupported compression/encryption, overlaps, declaration overruns,
and unsafe expansion before XML interpretation. Required members are streamed
with CRC checks and never extracted to disk.

Compatibility warnings do not bypass those gates. Noncanonical marker order,
legacy package versions, duplicate or missing optional declarations, invalid
refinements, and deprecated package elements may be ignored or downgraded to a
warning only after ZIP/path/XML preflight succeeds. They grant no new codec,
path, network, entity, encryption, extraction, or write capability. Larger
archive defaults apply to ranged structural reads and opaque declarations;
required XML members retain their separate small byte/tree limits, and the
child address-space and public-report caps are unchanged.

Fresh Expat parsers reject DOCTYPE/entity declarations, external resolution,
and XInclude while enforcing XML and package-graph counters. The isolated EPUB
child keeps opaque navigation, content, cover, and linked-resource declarations
private, then opens only bounded package-local navigation plus selected declared
XHTML/HTML resources through the same archive session.
The hostile-parser and process extension is governed by
[ADR-0008](decisions/0008-transient-epub-opening-parser.md); ADR-0007 remains
the accepted package-only isolation decision.
Strict XHTML uses a fresh Expat parser; recovery uses a fresh standard-library
`HTMLParser` over only those bounded bytes. Neither parser has a renderer,
network client, URL/base resolver, stylesheet engine, script runtime, XSLT,
XInclude, filesystem extraction, temporary-file, or source-repair seam.
An ordinary HTML/XHTML doctype, including an inert external identifier, may be
treated as recovery syntax; it is never resolved. Any entity declaration,
internal subset, non-HTML declaration, or actual resolution attempt remains
rejected and gains no DTD or network authority.
Namespace declarations are resolved before the recovery allowlist is applied,
so default or aliased XInclude/XSLT namespaces and aliased XHTML active
elements cannot gain authority through tolerant parsing. F06 and F07 same-file
lineage is independently derived from the source fingerprint and validated in
the canonical report; coordinated child-supplied lineage fields are
insufficient.
Recovered closing and self-closing tags use the same resolved namespace model
as start tags; implicit SVG/MathML subtrees and other foreign-namespace active
elements are inert too, including beneath an inherited XHTML default.
Only unprefixed controlled Structural Semantics Vocabulary tokens have F07a
authority, so extension-prefix lookalikes cannot fabricate claims or selection
boundaries.
HTML/XHTML namespace capability also gates navigation, claim, body-transition,
and exclusion consumers; foreign local-name collisions remain bounded text but
cannot acquire those semantics.
Missing or empty named-prefix bindings resolve to a closed unknown foreign
identity.
Fragment roles carry only hashed decoded targets internally and require one
unique HTML/XHTML `id` or `xml:id`; raw identifiers are never published. Only the
highest-precedence tier with a surviving valid opening role can authorize
recognition under its canonical path; a valid but boundary-suppressed higher
tier prevents lower-tier fallback, while invalid-only optional roles suppress
claims without a valid lower association. Active recovery encodings ignore
RCDATA, templates, foreign meta, and other inert syntax; XML declarations must
follow the exact version/encoding/standalone grammar. Duplicate namespace
declarations are malformed; duplicate controlled authority attributes cannot
select by order. Implied closes stop at foreign boundaries; explicit end-tag
searches stay within the current contiguous namespace region, may close a
matching foreign boundary, and cannot reach an HTML/XHTML ancestor beneath it.
Qualified ends retain their binding; unqualified ends resolve through the
current default namespace rather than acting as wildcards. Missing, empty, and
duplicated qualified prefixes use opaque prefix-specific identities, and
self-closing syntax cannot close an older element when its own start was not
pushed. A unique empty default explicitly re-enters the HTML-equivalent
no-namespace model beneath foreign content; empty named prefixes remain opaque.
Namespace declarations are resolved per prefix, so a unique foreign default
remains authoritative when an unrelated prefix is duplicated.
Duplicate-namespace malformedness is independent of supported active charset
discovery, while foreign meta remains inert.
Malformed declarations, any NUL in the bounded
payload, and BOM-less wide-character signatures retain no codec-detection
authority beyond the closed allowlist. Invalid and duplicate href anchors are
charged before target validation so they cannot bypass navigation-link limits.
Active and structurally hidden elements are excluded from the deterministic
projection. Navigation bytes/nodes/text/links and selected content share the
closed aggregate profile; aggregate exhaustion discards partial claims. The
parent concurrently caps child stdout/stderr, validates one canonical closed
report, and terminates/reaps on cancellation, deadline, overflow, crash, or
parent loss. These composition controls deny delegated application authority;
they are not described as a kernel syscall sandbox. Linux production also
enforces the fixed 256 MiB child address-space limit. Darwin framework Python
cannot lower its address-space limit beneath its preloaded reservation, so
macOS qualification is development-only for this command until an enforceable
memory boundary is available. The Linux F06 cohort is qualified non-root with
networking disabled and the container root, repository, and installed Python
environment read-only.

Opening parsing does not treat tolerant parsing as additional authority and
does not weaken archive, path, codec, encryption, CRC, source-stability, output,
process, cancellation, or reaping controls. Complete decoded resources,
navigation trees, recovered trees, and diagnostic markup are never retained.
The exact local command response is private owner output; CI and private-shadow
qualification reduce it in memory to controlled aggregate categories before
printing anything.

### Stale or conflicting writes

Risk: a user, scan, import, or another curator job changes an item after review.

Mitigations:

- Guard every apply with the expected fingerprint.
- Serialize writes per service and item.
- Suppress or reconcile self-generated update events.
- Supersede stale plans instead of force-applying them.

### Supply-chain compromise

Risk: malicious dependency, base image, build action, or release artifact.

Mitigations:

- Pin dependencies and base images by reviewed versions or digests.
- Generate an SBOM for releases.
- Scan dependencies, code, images, and secrets in CI.
- Sign release images and publish checksums and provenance.
- Minimize runtime packages and remove build tools from the final image.

### Dashboard compromise

Risk: unauthorized user approves work, reads private metadata, obtains tokens,
or invokes administrative functions.

Mitigations:

- Require authentication on non-health routes.
- Use secure session cookies, CSRF defenses, rate limits, and origin checks.
- Never return credentials to the browser after initial submission.
- Separate approval, configuration, backup, and write-enablement permissions
  even if the initial product has one owner.
- Render metadata as text or sanitized HTML under a restrictive CSP.

## Write-safety gates

Production writes require all of:

- compatible adapter capability;
- healthy service connection;
- stable current fingerprint;
- complete decision provenance;
- strict plan validation;
- verified backup readiness;
- service and item locks;
- active circuit breaker;
- allowed policy disposition;
- post-write read-back verification.

Missing any gate produces a visible blocked state. Emergency overrides are out
of scope for unattended operation and must never be hidden configuration.

## Security testing requirements

F09f durable Hardcover identity work treats contributor preview JSON,
disposable database identity and parent ownership/mode/emptiness, persisted
rows and retry schedules, process interruption, wall-clock chronology, and
competing lease owners as hostile boundaries. Atomic
acceptance prevents partial generations. A claim reserves without consuming;
gate-held start records consumption before later transport could be permitted.
Monotonic fences plus exact owner checks prevent stale or ABA cancellation or
publication. The immutable persisted claim's candidate, owner, fence, and lease
identity are reconciled before start, recovery, cancellation, or publication;
only current claimed work is cancellable. Retry claims derive their
candidate/fence from one current due
schedule and reconcile it against immutable claim, attempt, candidate, result,
reason, and timestamp history before mutation. Matched-pointer reuse likewise
reconciles the successful generation, terminal result, terminal generation
timestamp, exact fence/timestamps,
observation digest, and closed structural observation. Aggregate/attempt
exhaustion commits terminal evidence, while
currentness expiry and unreserved next-generation creation share one
transaction; the first reader reports `stale` without egress and a later
invocation may reserve the refresh. Append-only records and
finalize-once attempts make restart and corruption visible.

The installed durable-work preview injects gate ownership structurally and has no provider
secret, query-file, DNS, socket, HTTP, process loop, signal, proposal, review,
or production-write authority. It persists no ISBN, title, provider payload,
path, credential, or contributor script. Activated production reuses this
repository before credential or network authority. Tests trap all egress and secret seams,
compare the reconstructed schema-8 sentinel/logical snapshot, inject migration
and publication failures, reopen before and after starts, and prove stale
fences leave state unchanged. Disposable preview publication retains one
no-follow directory descriptor from validation through descriptor-relative
serialized staging, synchronization, and no-replace linking without a
process-global working-directory change; device/inode/owner/mode
revalidation rejects parent swaps without publishing into the replacement.

F09f increment 5 replaces structural gate assertions only in the new
`safe-executor-v3` synthetic lane. `RunSafeHardcoverIdentity` owns acceptance,
inspection of the persisted aggregate deadline, nonblocking gate acquisition,
claim/fence, atomic start accounting, one typed provider attempt, and durable
publication. The provider port owns exactly one attempt and cannot claim,
retry, wait, recover, or publish. Durable retry state remains available for a
later orchestrator, but this executor never waits, retries, recovers, installs
signal handlers, or loops in this synthetic scenario.

The deployment gate canonicalizes the state parent and binds its directory,
database, and exact `.mechanical-librarian-hardcover-identity.lock` entries to
open descriptors. The root is effective-user-owned and private; database and
lock are regular, effective-user-owned, link-count-one entries opened with
no-follow and close-on-exec flags. The lock is mode `0600`, acquired with
nonblocking `flock`, and its entry/descriptor identity is checked after open,
after acquisition, and immediately before send. Canonical parent aliases
converge on the same device/inode and lock. Dot/dotdot, nonconverging or changed
identity, final symlink, hardlink, nonregular database, permissive root, and
lock/database replacement fail closed. The lock is stable and never unlinked
during operation.

Qualification publication retains the exact descriptor-observed database inode
and binds it into the production gate before reopening state. Replacement after
publication therefore fails before credential or send. After durable start,
the held lease is carried through the application-owned provider port and
revalidated at the exact exchange boundary after token read, DNS, address
selection, request construction, and authorization. The HTTP dependency logging
boundary suppresses `httpx`/`httpcore` records for that exchange and restores
prior logger configuration afterward; adversarial URL, address, response-header,
body, and failure values never reach application log capture.

The persisted 30-second aggregate window is inspected before gate acquisition,
and the caller deadline is checked at the same boundary. A busy gate returns
`in_progress` without claim or start. Under the gate, atomic renew-and-start
requires six seconds remaining, extends the current fence lease to 15 seconds,
records consumption, and is the sole send authority. The attempt context ends
at the earlier of the caller deadline and start plus five seconds. A result at
or after that exact boundary is converted to timeout before publication.
`rate_limited`, `timeout`, and `unavailable` publish retryable durable state and
return `in_progress` when at least the exact six-second retry reserve remains.
A committed retryable terminal caused by insufficient reserve returns
`retry_exhausted`, not `persistence_failure`; a future invocation is not started
by this executor.

The synthetic C5 contributor label and process fields never supply durable
identity. The executor uses one fixed 55-character internal owner outside the
complete label and process syntax domains. Qualification uses sentinel-bearing
controls, owner readback, and an internal-vocabulary collision variant, so an
equal word such as `pending` is not confused with contributor-derived flow.
The persistence scanner validates exact private table/column/canonical shapes,
then ignores only eight-hex 32-bit digest prefixes in every other logical value
because unrelated durable identities can collide. It still rejects exact/raw
contributor, ISBN, and provider values, full encodings and collision-resistant
digests, and causal poison. A synthetic-only observer
materializes and exactly restores one timestamp transaction so qualification
captures nonempty main, WAL, and SHM while present without changing durable
domain state or production authority.
Ordinary gate, provider, and persistence failures map to closed public outcomes,
while interpreter control flow is re-raised after gate cleanup.

F09f increment 6 composes that one-attempt authority behind a synchronous
lifecycle orchestrator. Retry due time, attempt consumption, aggregate deadline,
and one-shot authority remain repository-owned; the orchestrator cannot send
without the existing atomic start. It releases transport and gate before its
injected wait, returns before a future due time after restart, and recovers only
an expired claim after acquiring the deployment gate. Pending/retry-wait
cancellation is durable; provider-time cancellation publishes only through the
current fence after provider return. Main-thread-only temporary SIGINT/SIGTERM
handlers set a local Event and restore prior handlers in `finally`; they add no
cross-process cancellation channel and do not translate interpreter control
flow. C6 remains credential-free after activation. Production composition wraps
the same lifecycle in main-thread cancellation handling. The repository creates
or reconciles the exact per-generation egress policy during acceptance before
provider preparation; preparation may resolve the credential, but no network
transport begins until the durable gate-held start and a later mode switch
cannot widen that generation's authority.

F07b treats the service path projection, private logical binding, EPUB archive,
and persisted canonical report as hostile inputs. Callers can supply only
strict opaque references and a bounded file index. Resolution requires one
current binding under the material configuration digest; media access uses
no-follow descriptor traversal, and publication revalidates the current
binding, source generation, lease owner, attempt, fence, and cancellation in
one transaction. The extractor retains ADR-0007/0008 process isolation. No
credential, network, service-write, media-write, renderer, script, or browser
authority is added.

- Secret-redaction golden tests.
- Prompt-injection and hostile-metadata tests.
- SSRF and redirect tests.
- Zip, image, path traversal, and parser resource-bound tests.
- Container tests proving reviewer isolation.
- Contract and container tests proving provider adapters cannot read target
  credentials, production media, or mutation interfaces and target adapters
  cannot read provider or reviewer credentials.
- Provider-disabled container tests proving no provider adapter/client is
  constructed, no provider secret is resolved, and no provider network request
  can occur.
- Authorization and CSRF integration tests.
- Apply concurrency, stale fingerprint, replay, and rollback tests.
- Dependency, image, static-analysis, and secret scans on every release.

## Open issues before public release

- Select the dashboard authentication mechanism.
- Select and prove the internal reviewer sandbox implementation.
- Define signed-update and rollback behavior.
- Define private vulnerability reporting contact and response targets.
- Complete a third-party-oriented container hardening review.
