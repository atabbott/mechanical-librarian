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

The planned 0.2 path remains report-only. F06 parses one generated EPUB using
bounded read-only input and has no network client, credential, application
repository, or persistence authority; only its bounded canonical package-title
outcome may later enter the title join. F09a substitutes a deterministic fake
provider. F09b uses the same application-owned provider port and supplies the
Hardcover adapter only the minimum bounded title-query facts, its own
account-capable provider token, and a dedicated outbound client.

Before F09b can be marked Ready or implementation begins, its supported
contract must freeze the exact outbound field allowlist and record the
disclosure purpose for each field. This threat model does not invent that list.
Any undeclared field is denied rather than sent.

That client is fixed to HTTPS POST at
`https://api.hardcover.app/v1/graphql` and one fixed read-only GraphQL query.
Neither endpoint nor query is operator-controlled. It sends no target-service
credential, cookies, raw service rows, external item identifiers, private
paths, media, or mutation capability. TLS validation is mandatory; redirects
are not followed; DNS results resolving to loopback, link-local, multicast,
private, or reserved space are rejected. Request bytes, response bytes, content
type, JSON depth, collection count, retained title length, per-attempt and total
deadline, attempt count, and retry backoff have fixed finite limits; concurrency
is one in-flight provider request. Cancellation stops further attempts. Logs,
diagnostics, exceptions, and test fixtures redact the provider token and
authorization headers.

Provider responses remain untrusted. The adapter validates a closed response
shape and retains only a bounded canonical title candidate with opaque
provider provenance. That canonical candidate/evidence and provenance are
classified as retained evidence. Before F09b can be marked Ready, #223 must
define their exact lifecycle: bind expiry to `retention.evidence_days`; provide
owner inspection and export; purge private retained values and provenance on
owner deletion or expiry; and preserve only a minimal non-private tombstone or
referential-history record needed to explain immutable proposal history. Until
that contract and its tests exist, the project makes no claim that these
behaviors are supported. Raw responses, upstream
provider identifiers, headers, and request bodies are neither persisted nor
cached. Disabled configuration constructs no provider adapter or client,
resolves no provider secret, performs no DNS lookup, and permits no egress.

The current target snapshot, F06 result, provider result, normalization policy,
candidate, and proposal set are joined by explicit versions and scope. Each
evaluated combination has one immutable versioned set bound to the ordered
`proposal-input-slot-v1` identities for current title, F06 title, and provider
title, including typed zero-proposal outcomes. Each slot carries a successful
exact value/evidence identity or its exact typed scoped outcome and contract
version; missing or failed inputs do not manufacture candidate values.
`title-normalization-v1` derives only the conservative
comparison key documented in the compatibility contract using the complete
frozen Unicode 16.0.0 normalization, whitespace, and case-folding baseline
rather than host-runtime Unicode data. Provider data only corroborates; the
retained proposal value is always the bounded original F06 package title, never
the provider spelling or comparison key. F09a accepts it only when the validated
current F06 report has no `ambiguous_title` or
`ambiguous_metadata_refinement` warning and exactly one applicable `TITLE`
observation whose F06 citation selector is
`title-type-main@<source-ordinal>` with only the optional non-selecting
`;display-seq@<source-ordinal>` component. The exact value must fit the
4,096-character proposal bound and must not be an F06 redaction or oversize
marker. Either warning binds `f06_title_ambiguous` before other partitions.
Provider agreement cannot choose among selector-less, multiple,
`short`, `expanded`, redacted, or out-of-bounds local title observations; those
states remain typed zero-proposal outcomes. Only canonical current-title `EMPTY`
can enter fill-empty policy; `PRESENT` is partitioned into equivalent or
different values, while `MISSING`, `NULL`, `MALFORMED`, and `UNSUPPORTED`
remain distinct zero-proposal outcomes. Missing, failed, unsupported, stale,
duplicate, or unjoinable input cannot create a proposal; local/provider
disagreement creates a typed
conflict set. Identical replay resolves to the same
semantic set and case identity without duplication. Current-state drift
supersedes applicability of the set and any derived case while preserving
immutable history. No outcome in this path grants automatic approval, a human
or AI decision, cover or rich-field processing, multi-provider fusion, or
production write authority. Future writes still require every gate below.

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
and XInclude while enforcing XML and package-graph counters. Opaque navigation,
content, cover, and linked-resource declarations are never dereferenced. The
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
